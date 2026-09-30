"""Translate current ATS JSON without importing ATS or rewriting its verdicts."""
from collections import Counter
from .models import DomainError


def normalize_report(report: dict | None) -> tuple[list, list]:
    if report is None:
        return [], []
    if not isinstance(report, dict) or not isinstance(report.get('results'), list):
        raise DomainError(422, '报告必须含 results 数组；原始文件已保留，可以检查后重新建一次上报')
    results = []
    for i, raw in enumerate(report['results']):
        if not isinstance(raw, dict):
            raise DomainError(422, f'results[{i}] 必须是对象')
        result = {'index': i}
        for key in ('name', 'module', 'status', 'message', 'detail', 'timestamp', 'scenario'):
            value = raw.get(key, '')
            result[key] = '' if value is None else str(value)
        if not result['status']:
            result['status'] = 'UNKNOWN'
        for key in ('cycle', 'rep', 'elapsed_ms'):
            value = raw.get(key, 0)
            try:
                result[key] = max(0, int(value or 0))
            except (ValueError, TypeError, OverflowError):
                raise DomainError(422, f'results[{i}].{key} 需要非负整数')
        results.append(result)
    recovery = report.get('recovery_events') or []
    if not isinstance(recovery, list) or not all(isinstance(e, dict) for e in recovery):
        raise DomainError(422, 'recovery_events 必须是对象数组')
    return results, recovery


def summarize(results: list, execution_state: str) -> dict:
    counts = Counter(r['status'] for r in results)
    if execution_state == 'aborted':
        status = 'ABORTED'
    elif execution_state != 'completed' or not results or any(s not in ('PASS', 'FAIL', 'ERROR', 'SKIP') for s in counts):
        status = 'UNKNOWN'
    elif counts['ERROR']:
        status = 'ERROR'
    elif counts['FAIL']:
        status = 'FAIL'
    elif counts['PASS']:
        status = 'PASS'
    else:
        status = 'SKIP'
    return {'status': status, 'summary': {'total': len(results), 'passed': counts['PASS'], 'failed': counts['FAIL'], 'errored': counts['ERROR'], 'skipped': counts['SKIP'], 'unknown': sum(v for k, v in counts.items() if k not in ('PASS', 'FAIL', 'ERROR', 'SKIP')), 'case_elapsed_ms': sum(r['elapsed_ms'] for r in results)}}
