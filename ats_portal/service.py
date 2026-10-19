"""Coordinate an upload lifecycle through repository, storage and classifier."""
import json
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

from . import ai as ai_module
from .adapter import normalize_report, summarize
from .classifier import attention_indices
from .models import AnnotationPut, DomainError, Manifest, RunMetaPut


class IngestionService:
    def __init__(self, settings, repository, storage, classifier, ai=None):
        self.settings, self.repository, self.storage, self.classifier = settings, repository, storage, classifier
        self.ai = ai

    def register(self, manifest: Manifest):
        self.storage.validate_manifest(manifest.run_id, [a.model_dump() for a in manifest.artifacts])
        if any(a.size > self.settings.max_file_bytes for a in manifest.artifacts) or sum(a.size for a in manifest.artifacts) > self.settings.max_run_bytes:
            raise DomainError(413, '上传大小超过服务配置限制')
        if any(a.kind == 'report' and a.size > self.settings.max_report_bytes for a in manifest.artifacts):
            raise DomainError(413, '结构化报告过大')
        day = manifest.started_at.astimezone(ZoneInfo(self.settings.timezone)).date().isoformat()
        return self.repository.register(manifest.model_dump(mode='json'), day)

    def artifact(self, run_id, name, require_file=False):
        run = self.repository.get(run_id)
        artifact = next((a for a in run['artifacts'] if a['name'] == name), None)
        if artifact is None:
            raise DomainError(404, '文件不在本次运行的清单中')
        if require_file and not self.storage.available(run_id, artifact):
            raise DomainError(404, '文件尚未接收完成')
        return artifact

    def detail(self, run_id):
        run = self.repository.get(run_id)
        for artifact in run['artifacts']:
            artifact['received'] = self.storage.available(run_id, artifact)
        run['annotations'] = self.repository.list_annotations(run_id)
        run['attention_count'] = len(attention_indices(run.get('results', []), run.get('issues', [])))
        run['meta'] = self.repository.get_run_meta(run_id)
        return run

    def upsert_run_meta(self, run_id, data: RunMetaPut):
        run = self.repository.get(run_id)
        if run['upload_state'] != 'complete':
            raise DomainError(409, '运行尚未归档完成，暂不能编辑')
        now = datetime.now(timezone.utc).isoformat()
        return self.repository.upsert_run_meta(run_id, data.device, data.firmware, data.author, now)

    def finalize(self, run_id):
        run = self.repository.get(run_id)
        if run['upload_state'] == 'complete':
            return run
        missing = [a['name'] for a in run['artifacts'] if not self.storage.available(run_id, a)]
        if missing:
            raise DomainError(409, '尚缺文件：' + '、'.join(missing))
        report, notes = None, []
        for artifact in run['artifacts']:
            if artifact['kind'] == 'report':
                try:
                    report = json.loads(self.storage.read_small(run_id, artifact, self.settings.max_report_bytes))
                except json.JSONDecodeError as exc:
                    raise DomainError(422, f'报告 JSON 格式错误：第 {exc.lineno} 行') from exc
            elif artifact['kind'] == 'note':
                notes.append((artifact['name'], self.storage.read_small(run_id, artifact, 1024*1024)))
        results, recovery = normalize_report(report)
        issues = self.classifier.classify(results, recovery, notes, report is not None, run['execution_state'])
        normalized = {**summarize(results, run['execution_state']), 'results': results, 'recovery_events': recovery, 'issues': issues, 'issue_count': len(issues), 'rules_version': self.classifier.version, 'attention_count': len(attention_indices(results, issues))}
        return self.repository.complete(run_id, normalized)

    def _attention_for(self, run):
        return attention_indices(run.get('results', []), run.get('issues', []))

    def upsert_annotation(self, run_id, result_index, data: AnnotationPut):
        run = self.repository.get(run_id)
        if run['upload_state'] != 'complete':
            raise DomainError(409, '运行尚未归档完成，暂不能标注')
        results = run.get('results', [])
        if not isinstance(result_index, int) or result_index < 0 or result_index >= len(results):
            raise DomainError(422, '指定的测试记录不存在')
        if result_index not in self._attention_for(run):
            raise DomainError(422, '仅可标注需关注的记录（失败、错误、未知状态或已关联问题）')
        labels = self.repository.resolve_labels(data.label_ids)
        now = datetime.now(timezone.utc).isoformat()
        return self.repository.upsert_annotation(run_id, result_index, labels, data.note, data.author, now)

    def delete_annotation(self, run_id, result_index):
        run = self.repository.get(run_id)
        if run['upload_state'] != 'complete':
            raise DomainError(409, '运行尚未归档完成，暂不能标注')
        self.repository.delete_annotation(run_id, result_index)

    # ---- AI analysis ----

    def _require_ai(self):
        if not self.settings.ai_enabled or self.ai is None:
            raise DomainError(503, '未配置 AI 服务，请设置 ATS_PORTAL_AI_BASE_URL / ATS_PORTAL_AI_API_KEY / ATS_PORTAL_AI_MODEL 后重启服务')

    @staticmethod
    def _attention_items(run):
        """(result, issue|None) pairs needing attention, plus run-level issues."""
        results = run.get('results', [])
        issues = run.get('issues', [])
        indices = attention_indices(results, issues)
        by_index = {}
        for issue in issues:
            ri = issue.get('result_index')
            if ri is not None and ri in indices:
                by_index.setdefault(ri, issue)
        items = [(r, by_index.get(r['index'])) for r in results if r['index'] in indices]
        run_level = [i for i in issues if i.get('result_index') is None]
        return items, run_level

    def analyze_run(self, run_id, refresh=False):
        self._require_ai()
        run = self.repository.get(run_id)
        if run['upload_state'] != 'complete':
            raise DomainError(409, '运行尚未归档完成，暂不能分析')
        scope, kind = f'run:{run_id}', 'root_cause'
        if not refresh:
            cached = self.repository.get_analysis(scope, kind)
            if cached:
                return {**cached, 'cached': True}
        items, run_level = self._attention_items(run)
        contexts = ai_module.extract_log_context(run, self.storage, items, self.settings.timezone)
        # Cross-run PASS contrast, bounded.
        contrasts = []
        seen = set()
        for result, _issue in items[:6]:
            key = (result.get('scenario', ''), result.get('module', ''), result.get('name', ''))
            if key in seen:
                continue
            seen.add(key)
            hits = self.repository.find_similar_pass_results(
                run['mode']['id'], {'scenario': key[0], 'module': key[1], 'name': key[2]}, run_id, limit=2)
            for hit in hits[:2]:
                other = self.repository.get(hit['run_id'])
                if not hit.get('timestamp'):
                    continue
                loc = ai_module.locate_time(hit['timestamp'], hit.get('elapsed_ms', 0), other.get('started_at', ''), self.settings.timezone)
                if not loc:
                    continue
                artifact = ai_module._choose_log_file(other, self.storage)
                if artifact is None:
                    continue
                path = self.storage.path(hit['run_id'], artifact['name'])
                n = self.storage.locate(path, loc['at'], day_offset=loc.get('day', 0))
                if not n:
                    continue
                lines = [l['text'] for l in self.storage.lines(path, max(1, n - 10), ai_module.EXCERPT_LINES)['lines']]
                contrasts.append({'run_id': hit['run_id'], 'started_at': other.get('started_at', ''), 'test_item': result.get('name', ''), 'status': hit['status'], 'lines': lines})
        user = ai_module.build_root_cause_user(run, contexts, contrasts, run_level)
        raw = self.ai.chat(ai_module.ROOT_CAUSE_SYSTEM, user)
        payload = ai_module.normalize_root_cause(ai_module.parse_json(raw) or raw)
        now = datetime.now(timezone.utc).isoformat()
        self.repository.upsert_analysis(scope, kind, payload, self.settings.ai_model, now)
        return {'cached': False, 'analysis': payload, 'model': self.settings.ai_model, 'created_at': now}

    def analyze_trends(self, mode='', date_from='', date_to='', refresh=False):
        self._require_ai()
        scope = f"trend:{mode or 'all'}:{date_from or ''}:{date_to or ''}"
        kind = 'trends'
        if not refresh:
            cached = self.repository.get_analysis(scope, kind)
            if cached:
                return {**cached, 'cached': True}
        snapshot = self.repository.trend_snapshot(mode, date_from, date_to)
        user = ai_module.build_trends_user(snapshot, mode, date_from, date_to)
        raw = self.ai.chat(ai_module.TRENDS_SYSTEM, user)
        payload = ai_module.normalize_trends(ai_module.parse_json(raw) or raw)
        now = datetime.now(timezone.utc).isoformat()
        self.repository.upsert_analysis(scope, kind, payload, self.settings.ai_model, now)
        return {'cached': False, 'analysis': payload, 'model': self.settings.ai_model, 'created_at': now}
