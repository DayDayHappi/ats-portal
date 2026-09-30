"""Synthetic repeat/cycle edge cases for the browser result overview test."""
import hashlib
import json
from fastapi.testclient import TestClient
from ats_portal.config import Settings
from ats_portal.web import create_app


def seed(settings):
    results = []

    def add(cycle, module, status='PASS', rep=1, detail='', scenario='qa-lap'):
        results.append(dict(name=module, module=module, status=status, cycle=cycle,
                            rep=rep, scenario=scenario, elapsed_ms=1000,
                            message='合成测试记录', detail=detail))

    add(1, 'photo', rep=1)
    add(1, 'photo', rep=2)
    add(1, 'video')
    add(1, 'rtmp', 'SKIP')
    add(2, 'photo', rep=1)
    add(2, 'photo', rep=2, detail='TT ERROR camera')
    add(2, 'video', 'FAIL', detail='<img src=x onerror="window.__xss=true">')
    add(2, 'video')  # Same repeat number after recovery must remain a separate record.
    add(2, 'rtmp')
    add(3, 'photo')
    add(3, 'video', 'UNKNOWN')
    add(0, 'prepare', 'UNKNOWN', rep=0)
    add(2, 'photo', 'FAIL', scenario='other-lap')
    report = {'results': results, 'recovery_events': [
        {'cycle': 2, 'task': 'video', 'recovery_result': 'success', 'reason': 'UART timeout'}
    ]}
    files = {'result.json': json.dumps(report).encode(), 'serial.log': b'first\nTT ERROR camera\nlast\n'}
    manifest = dict(schema_version=1, run_id='qa-result-overview',
                    mode={'id': 'custom-overview', 'name': '轮次总览 · 合成验收数据'},
                    started_at='2026-09-29T10:00:00+08:00', execution_state='completed',
                    source={'host': 'qa-only'}, artifacts=[
                        dict(name=name, kind='report' if name.endswith('.json') else 'log',
                             size=len(data), sha256=hashlib.sha256(data).hexdigest())
                        for name, data in files.items()])
    with TestClient(create_app(settings)) as client:
        headers = {'Authorization': 'Bearer ' + settings.upload_token}
        assert client.post('/api/v1/runs', json=manifest, headers=headers).status_code in (200, 201)
        for name, data in files.items():
            assert client.put(f'/api/v1/runs/qa-result-overview/artifacts/{name}', content=data, headers=headers).status_code == 200
        assert client.post('/api/v1/runs/qa-result-overview/finalize', headers=headers).status_code == 200

        many_results = [dict(name='photo', module='photo', scenario='many-rounds',
                             cycle=cycle, rep=rep, elapsed_ms=1,
                             status='FAIL' if cycle == 27 and rep == 30 else 'PASS')
                        for cycle in range(1, 28) for rep in range(1, 31)]
        large_report = json.dumps({'results': many_results}).encode()
        many_manifest = dict(manifest, run_id='qa-many-rounds', artifacts=[
            dict(name='result.json', kind='report', size=len(large_report),
                 sha256=hashlib.sha256(large_report).hexdigest())])
        assert client.post('/api/v1/runs', json=many_manifest, headers=headers).status_code in (200, 201)
        assert client.put('/api/v1/runs/qa-many-rounds/artifacts/result.json', content=large_report, headers=headers).status_code == 200
        assert client.post('/api/v1/runs/qa-many-rounds/finalize', headers=headers).status_code == 200


if __name__ == '__main__':
    config = Settings.from_env()
    config.allowed_hosts = (*config.allowed_hosts, 'testserver')
    seed(config)
    print('Inserted synthetic cycle/repeat results into', config.data_dir)
