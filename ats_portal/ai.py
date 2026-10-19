"""OpenAI-compatible AI client and log-context extraction for analysis.

No HTTP routes or database logic live here. The client is a thin wrapper over
``POST {base_url}/chat/completions`` so any provider exposing that surface
(DeepSeek, Qwen, GLM, Kimi, OpenAI, …) works unchanged. Log excerpts are built
only from the existing :class:`~ats_portal.storage.LocalStorage` primitives so
the original-byte and bounded-view guarantees are preserved.
"""
import json
import re
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

import httpx

from .models import DomainError

# Bounded input: keep model prompts well under a few tens of thousands of chars.
EXCERPT_BUDGET = 20000
EXCERPT_LINES = 20
SEARCH_LIMIT = 5

ROOT_CAUSE_SYSTEM = (
    '你是 ATS 硬件压测日志分析专家。下面会提供一次压测运行中「需要关注的测试项」的日志片段，'
    '以及同一测试项在其他成功运行中的日志片段作为对照。请对比异常片段与成功片段，推断最可能的根因。\n'
    '规则：\n'
    '1. 只依据提供的日志片段与结构化信息推断，不要编造日志中不存在的现象；证据要引用片段原文。\n'
    '2. 判断时区分固件缺陷、环境/硬件问题、用例自身问题、偶发抖动等可能。\n'
    '3. confidence 只能是 高 / 中 / 低。\n'
    '4. 只输出一个 JSON 对象，不要输出任何其它文本、不要用 Markdown 代码围栏。结构如下：\n'
    '{"summary": "一句话总体结论", "findings": [{"test_item": "测试项名", "cycle": 2, "rep": 3, "status": "FAIL", '
    '"likely_root_cause": "可能根因", "evidence": "引用的日志片段", "confidence": "中", "suggested_action": "建议"}]}'
)

TRENDS_SYSTEM = (
    '你是 ATS 硬件压测数据分析专家。下面会提供多次压测运行聚合后的失败统计、命中问题类别与代表性证据片段。'
    '请找出反复出现的失败模式与趋势。\n'
    '规则：\n'
    '1. 只依据提供的数据推断，不要编造；假设要注明是假设。\n'
    '2. 识别跨运行反复出现的同一测试项/同一类错误的规律。\n'
    '3. 只输出一个 JSON 对象，不要输出任何其它文本、不要用 Markdown 代码围栏。结构如下：\n'
    '{"summary": "总体趋势结论", "recurring": [{"pattern": "反复出现的失败模式", "test_item": "测试项", '
    '"occurrence_count": 3, "affected_runs": 3, "hypothesis": "成因假设", "suggested_action": "建议"}]}'
)


def parse_json(text):
    """Extract the first JSON object from model output, or return None."""
    if not text or not text.strip():
        return None
    text = text.strip()
    try:
        data = json.loads(text)
        return data if isinstance(data, dict) else None
    except ValueError:
        pass
    fence = re.search(r'```(?:json)?\s*(.*?)```', text, re.DOTALL)
    if fence:
        text = fence.group(1).strip()
    start = text.find('{')
    if start == -1:
        return None
    depth, in_str, escape = 0, False, False
    for i in range(start, len(text)):
        c = text[i]
        if in_str:
            if escape:
                escape = False
            elif c == '\\':
                escape = True
            elif c == '"':
                in_str = False
            continue
        if c == '"':
            in_str = True
        elif c == '{':
            depth += 1
        elif c == '}':
            depth -= 1
            if depth == 0:
                try:
                    data = json.loads(text[start:i + 1])
                    return data if isinstance(data, dict) else None
                except ValueError:
                    return None
    return None


class AIClient:
    """Minimal OpenAI-compatible chat client."""

    def __init__(self, base_url, api_key, model, timeout):
        self.base_url = base_url.rstrip('/')
        self.api_key = api_key
        self.model = model
        self.timeout = timeout
        self._client = httpx.Client(timeout=timeout)

    @property
    def _endpoint(self):
        # Providers may hand either a base (e.g. https://api.example.com/v1) or a
        # full URL (…/chat/completions). Do not inject `/v1` — callers must
        # already include it in the base.
        if self.base_url.endswith('/chat/completions'):
            return self.base_url
        return self.base_url + '/chat/completions'

    def chat(self, system, user):
        """Return the assistant text for one chat completion."""
        payload = {
            'model': self.model,
            'messages': [
                {'role': 'system', 'content': system},
                {'role': 'user', 'content': user},
            ],
            'temperature': 0.2,
        }
        headers = {'Authorization': 'Bearer ' + self.api_key, 'Content-Type': 'application/json'}
        try:
            resp = self._client.post(self._endpoint, json=payload, headers=headers)
        except (httpx.ConnectError, httpx.ConnectTimeout, httpx.ReadTimeout) as exc:
            raise DomainError(504, '无法连接 AI 服务，请检查 ATS_PORTAL_AI_BASE_URL 与网络后重试') from exc
        except httpx.RequestError as exc:
            raise DomainError(504, 'AI 服务响应超时，请稍后重试') from exc
        if resp.status_code in (401, 403):
            raise DomainError(502, 'AI 服务凭据无效，请检查 ATS_PORTAL_AI_API_KEY')
        if resp.status_code >= 400:
            detail = ''
            try:
                body = resp.json()
                if isinstance(body, dict):
                    if isinstance(body.get('error'), dict):
                        detail = body['error'].get('message', '')
                    else:
                        detail = body.get('detail', '')
            except ValueError:
                pass
            raise DomainError(502, f'AI 服务返回错误（{resp.status_code}）：{detail or resp.text[:200]}')
        try:
            data = resp.json()
            content = data['choices'][0]['message']['content']
        except (ValueError, KeyError, IndexError, TypeError) as exc:
            raise DomainError(502, 'AI 服务返回了无法识别的数据') from exc
        return content


def locate_time(timestamp, elapsed_ms, started_at, timezone_name):
    """Server-side twin of the frontend ``locateTime``/``dayOffset`` helpers.

    Returns ``{'at': 'HH:MM:SS.mmm', 'day': n}`` for a wall-clock log lookup, or
    ``None`` when the result carries no parseable timestamp.
    """
    if not timestamp:
        return None
    match = re.match(r'^(\d{4})-(\d{2})-(\d{2})[ T](\d{2}):(\d{2}):(\d{2})(?:\.(\d{1,3}))?$', str(timestamp))
    if not match:
        return None
    y, mo, d, h, mi, s = (int(match.group(i)) for i in range(1, 7))
    frac = (match.group(7) or '').ljust(3, '0')[:3]
    start_utc = datetime(y, mo, d, h, mi, s, tzinfo=timezone.utc) + timedelta(milliseconds=int(frac))
    start_utc = start_utc - timedelta(milliseconds=int(elapsed_ms or 0))
    tz = ZoneInfo(timezone_name)
    try:
        started = datetime.fromisoformat(str(started_at))
    except ValueError:
        started = None
    day = 0
    if started is not None and started.tzinfo is not None:
        day = (start_utc.astimezone(tz).date() - started.astimezone(tz).date()).days
    return {'at': start_utc.strftime('%H:%M:%S.%f')[:12], 'day': day}


def _choose_log_file(run, storage):
    for a in run['artifacts']:
        if a['kind'] == 'log' and a['name'] == 'serial.log' and storage.available(run['id'], a):
            return a
    for a in run['artifacts']:
        if a['kind'] == 'log' and storage.available(run['id'], a):
            return a
    return None


def extract_log_context(run, storage, focus_items, timezone_name):
    """Return per-item log excerpts: ``[{test_item, cycle, rep, status, log_file, lines}]``.

    ``focus_items`` is a list of ``(result, issue|None)``. Reuses the frontend's
    lookup priority: issue search keyword → wall-clock locate → name search.
    """
    artifact = _choose_log_file(run, storage)
    if artifact is None:
        return []
    path = storage.path(run['id'], artifact['name'])
    budget = EXCERPT_BUDGET
    contexts = []
    for result, issue in focus_items:
        if budget <= 0:
            break
        lines = []
        if issue and issue.get('search'):
            found = storage.search(path, issue['search'], 1, SEARCH_LIMIT)
            if found['matches']:
                n = found['matches'][0]['number']
                lines = [l['text'] for l in storage.lines(path, max(1, n - 10), EXCERPT_LINES)['lines']]
        elif result.get('timestamp'):
            loc = locate_time(result.get('timestamp'), result.get('elapsed_ms'), run['started_at'], timezone_name)
            if loc:
                n = storage.locate(path, loc['at'], day_offset=loc.get('day', 0))
                if n:
                    lines = [l['text'] for l in storage.lines(path, max(1, n - 10), EXCERPT_LINES)['lines']]
        if not lines and result.get('name'):
            found = storage.search(path, result['name'], 1, SEARCH_LIMIT)
            lines = [m['text'] for m in found['matches']]
        if not lines:
            continue
        joined = '\n'.join(lines)
        if len(joined) > budget:
            joined = joined[:budget] + '\n…（已截断）'
        budget -= len(joined)
        contexts.append({
            'test_item': result.get('name', ''),
            'module': result.get('module', ''),
            'cycle': result.get('cycle'),
            'rep': result.get('rep'),
            'status': result.get('status', ''),
            'log_file': artifact['name'],
            'lines': lines,
        })
    return contexts


def build_root_cause_user(run, contexts, contrasts, run_level_issues):
    """Assemble the user prompt for per-run root-cause analysis."""
    parts = [
        '【运行信息】',
        f"运行编号：{run['id']}",
        f"模式：{run['mode']['name']}（{run['mode']['id']}）",
        f"运行状态：{run.get('status', '')}",
        f"用例汇总：{json.dumps(run.get('summary', {}), ensure_ascii=False)}",
        f"设备/固件：{json.dumps(run.get('meta') or run.get('source', {}), ensure_ascii=False)}",
    ]
    if run_level_issues:
        parts.append('【运行级问题（无具体测试项归属）】')
        for issue in run_level_issues:
            parts.append(f"- [{issue.get('category_name','')}] {issue.get('title','')}" + (f"：{issue.get('evidence','')[:300]}" if issue.get('evidence') else ''))
    if contexts:
        parts.append('【异常/需关注测试项的日志片段】')
        for ctx in contexts:
            parts.append(
                f"\n### 测试项 {ctx['test_item']}（模块 {ctx['module'] or '未标注'} · 状态 {ctx['status']}"
                f" · 第{ctx['cycle']}轮 第{ctx['rep']}次 · 文件 {ctx['log_file']}）\n" + '\n'.join(ctx['lines'])
            )
    if contrasts:
        parts.append('【同一测试项在成功运行中的日志片段（对照）】')
        for c in contrasts:
            parts.append(f"\n### 对照：{c['test_item']}（运行 {c['run_id']}，{c['started_at']}，状态 {c['status']}）\n" + '\n'.join(c['lines']))
    if not contexts and not run_level_issues:
        parts.append('【说明】本次运行没有需要关注的测试项；请就整体运行状态给出简要结论。')
    return '\n'.join(parts)


def build_trends_user(rows, mode, date_from, date_to):
    """Assemble the user prompt for cross-run trend analysis."""
    parts = [
        '【趋势分析范围】',
        f"模式：{mode or '全部模式'}",
        f"日期：{date_from or '不限'} 至 {date_to or '不限'}",
        '【各运行概要（时间倒序）】',
    ]
    for row in rows:
        parts.append(
            f"- {row['run_id']} · {row['mode_name']} · {row['started_at']} · 状态 {row['status']} · "
            f"汇总 {json.dumps(row['summary'], ensure_ascii=False)} · 问题 {row['issue_count']} 条"
        )
    return '\n'.join(parts)


def normalize_root_cause(raw):
    """Tolerantly coerce model output into a stable root-cause shape."""
    if isinstance(raw, str):
        return {'summary': raw, 'findings': []}
    if not isinstance(raw, dict):
        return {'summary': '', 'findings': []}
    findings = []
    for f in _as_list(raw.get('findings')):
        if not isinstance(f, dict):
            continue
        findings.append({
            'test_item': _text(f.get('test_item') or f.get('name')),
            'cycle': _text(f.get('cycle')),
            'rep': _text(f.get('rep')),
            'status': _text(f.get('status')),
            'likely_root_cause': _text(f.get('likely_root_cause') or f.get('root_cause')),
            'evidence': _text(f.get('evidence')),
            'confidence': _text(f.get('confidence')),
            'suggested_action': _text(f.get('suggested_action')),
        })
    return {'summary': _text(raw.get('summary')), 'findings': findings}


def normalize_trends(raw):
    """Tolerantly coerce model output into a stable trends shape."""
    if isinstance(raw, str):
        return {'summary': raw, 'recurring': []}
    if not isinstance(raw, dict):
        return {'summary': '', 'recurring': []}
    recurring = []
    for f in _as_list(raw.get('recurring')):
        if not isinstance(f, dict):
            continue
        recurring.append({
            'pattern': _text(f.get('pattern')),
            'test_item': _text(f.get('test_item')),
            'occurrence_count': _text(f.get('occurrence_count')),
            'affected_runs': _text(f.get('affected_runs')),
            'hypothesis': _text(f.get('hypothesis')),
            'suggested_action': _text(f.get('suggested_action')),
        })
    return {'summary': _text(raw.get('summary')), 'recurring': recurring}


def _as_list(value):
    return value if isinstance(value, list) else []


def _text(value, default=''):
    if value is None:
        return default
    if isinstance(value, (dict, list)):
        return json.dumps(value, ensure_ascii=False)
    return str(value)
