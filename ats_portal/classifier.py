"""Explainable, mode-independent classification of report evidence."""
import json
from pathlib import Path

RESERVED = {'test_failure', 'execution_error', 'recovery', 'manual', 'incomplete', 'unknown_status'}


def attention_indices(results, issues):
    """Result indices that need human attention.

    Single source of truth for what the UI marks as "needs attention": a
    non-PASS/SKIP status, or any linked issue (this includes PASS/SKIP records
    hit by a keyword rule that has no ``statuses`` restriction). Recovery /
    manual / incomplete issues carry ``result_index=None`` and thus never make
    a record annotatable on their own.
    """
    indices = {r['index'] for r in results}
    attention = {r['index'] for r in results if r['status'] not in ('PASS', 'SKIP')}
    for issue in issues:
        ri = issue.get('result_index')
        if ri is not None and ri in indices:
            attention.add(ri)
    return attention


class Classifier:
    def __init__(self, rules_path: Path):
        config = json.loads(rules_path.read_text(encoding='utf-8'))
        self.version = str(config['version'])
        self.rules = config['rules']
        ids = set()
        for rule in self.rules:
            if not isinstance(rule.get('id'), str) or not rule['id'] or rule['id'] in ids | RESERVED:
                raise ValueError('分类规则 id 必须唯一且不能使用系统保留名称')
            if not rule.get('name') or not isinstance(rule.get('keywords'), list) or not rule['keywords']:
                raise ValueError('分类规则需要 name 和非空 keywords')
            if not all(isinstance(k, str) and k for k in rule['keywords']):
                raise ValueError('keywords 必须包含非空字符串')
            ids.add(rule['id'])

    def classify(self, results, recovery, notes, has_report, execution_state):
        issues = []

        def add(category, label, title, evidence='', result=None, **extra):
            issues.append({'id': len(issues) + 1, 'category': category, 'category_name': label, 'title': title, 'evidence': str(evidence), 'result_index': result['index'] if result else None, 'module': result['module'] if result else '', 'cycle': result['cycle'] if result else None, 'rep': result['rep'] if result else None, **extra})

        for result in results:
            status = result['status']
            if status in ('FAIL', 'ERROR'):
                add('test_failure' if status == 'FAIL' else 'execution_error', '用例失败' if status == 'FAIL' else '执行错误', result['name'], result['message'] or result['detail'], result)
            elif status not in ('PASS', 'SKIP'):
                add('unknown_status', '未知判定', f"未知结果状态：{status}", result['message'], result)
            text = result['message'] + '\n' + result['detail']
            for rule in self.rules:
                if rule.get('statuses') and status not in rule['statuses']:
                    continue
                hits = [k for k in rule['keywords'] if k.casefold() in text.casefold()]
                if hits:
                    add(rule['id'], rule['name'], result['name'], text, result, keywords=hits, search=hits[0])
        for event in recovery:
            add('recovery', '恢复事件', f"{event.get('task', '板卡')} · {event.get('recovery_result', '恢复记录')}", json.dumps(event, ensure_ascii=False, indent=2), cycle=event.get('cycle'), rep=event.get('rep'), search=str(event.get('reason', ''))[:160])
        for name, text in notes:
            if text.strip():
                add('manual', '人工问题记录', name, text, artifact=name)
        if not has_report or not results or execution_state != 'completed':
            reasons = []
            if not has_report:
                reasons.append('未提供结构化报告')
            elif not results:
                reasons.append('报告没有用例结果')
            if execution_state != 'completed':
                reasons.append('运行已中止' if execution_state == 'aborted' else '运行结束状态未确认')
            add('incomplete', '结果不完整', '；'.join(reasons))
        return issues
