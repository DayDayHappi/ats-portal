import hashlib
import json
from concurrent.futures import ThreadPoolExecutor

import pytest
from fastapi.testclient import TestClient

from ats_portal.config import Settings
from ats_portal.web import create_app

TOKEN = 'test-token-that-is-only-used-in-tests'
HEADERS = {'Authorization': f'Bearer {TOKEN}'}


@pytest.fixture
def settings(tmp_path):
    return Settings(data_dir=tmp_path / 'data', upload_token=TOKEN, allowed_hosts=('testserver', '127.0.0.1', 'localhost'))


@pytest.fixture
def client(settings):
    with TestClient(create_app(settings)) as c:
        yield c


def bundle(run_id='run-1', mode='新模式/任意参数', detail='TT ERROR 命中 1 次：\n第1次: TT ERROR camera', status='PASS', state='completed'):
    report = {'results': [{'name': 'video[3k]', 'module': 'video', 'scenario': mode, 'cycle': 2, 'rep': 3, 'status': status, 'message': 'finished', 'detail': detail, 'elapsed_ms': 1000}], 'recovery_events': []}
    files = {'result.json': json.dumps(report, ensure_ascii=False).encode(), 'serial.log': b'first\n\x1b[31mTT ERROR camera\x1b[0m\n<script>alert(1)</script>\nlast\n'}
    manifest = {'schema_version': 1, 'run_id': run_id, 'mode': {'id': mode, 'name': mode}, 'started_at': '2026-09-27T23:50:00+08:00', 'ended_at': '2026-09-28T03:00:00+08:00', 'execution_state': state, 'source': {'host': 'ubuntu'}, 'artifacts': [{'name': k, 'kind': 'report' if k.endswith('.json') else 'log', 'size': len(v), 'sha256': hashlib.sha256(v).hexdigest()} for k, v in files.items()]}
    return manifest, files


def upload(client, manifest=None, files=None):
    if manifest is None:
        manifest, files = bundle()
    r = client.post('/api/v1/runs', json=manifest, headers=HEADERS)
    assert r.status_code in (200, 201), r.text
    for name, data in files.items():
        r = client.put(f'/api/v1/runs/{manifest["run_id"]}/artifacts/{name}', content=data, headers=HEADERS)
        assert r.status_code == 200, r.text
    return client.post(f'/api/v1/runs/{manifest["run_id"]}/finalize', headers=HEADERS)


def test_new_mode_and_pass_anomaly_are_independent(client):
    assert upload(client).status_code == 200
    run = client.get('/api/v1/runs/run-1').json()
    assert run['status'] == 'PASS'
    assert run['results'][0]['status'] == 'PASS'
    assert any(i['category'] == 'firmware_keyword' for i in run['issues'])
    assert run['results'][0]['cycle'] == 2
    assert run['results'][0]['rep'] == 3
    assert client.get('/api/v1/options').json()['modes'][0]['id'] == '新模式/任意参数'


def test_machine_upload_receipts_do_not_repeat_large_report(client):
    m, files = bundle(detail='TT ERROR ' + 'x' * 100000)
    response = upload(client, m, files)
    assert len(response.content) < 4096
    repeated = client.post('/api/v1/runs', json=m, headers=HEADERS)
    assert len(repeated.content) < 4096
    assert repeated.json()['run_id'] == m['run_id']
    assert repeated.json()['upload_state'] == 'complete'
    assert client.get('/api/v1/runs/run-1').json()['results'][0]['detail'].endswith('x' * 100000)


def test_recovery_manual_notes_and_malformed_reports(client):
    m, files = bundle()
    report=json.loads(files['result.json'])
    report['recovery_events']=[{'reason':'uart timeout','recovery_result':'success','cycle':1}]
    files['result.json']=json.dumps(report).encode()
    files['problem.log']='画面闪烁'.encode()
    m['artifacts']=[{'name':k,'kind':'report' if k.endswith('.json') else 'note' if k=='problem.log' else 'log','size':len(v),'sha256':hashlib.sha256(v).hexdigest()} for k,v in files.items()]
    assert upload(client,m,files).status_code==200
    detail=client.get('/api/v1/runs/run-1').json()
    assert detail['status']=='PASS'
    assert {'recovery','manual'} <= {i['category'] for i in detail['issues']}
    m,files=bundle(run_id='malformed')
    files['result.json']=b'{broken'
    m['artifacts'][0].update(size=len(files['result.json']),sha256=hashlib.sha256(files['result.json']).hexdigest())
    assert upload(client,m,files).status_code==422
    assert client.get('/api/v1/runs/malformed').json()['upload_state']=='receiving'


def test_date_is_test_start_not_upload_or_end(client):
    upload(client)
    assert client.get('/api/v1/runs', params={'date_from': '2026-09-27', 'date_to': '2026-09-27'}).json()['total'] == 1
    assert client.get('/api/v1/runs', params={'date_from': '2026-09-28'}).json()['total'] == 0


def test_idempotency_and_conflicting_identity(client):
    upload(client)
    assert upload(client).status_code == 200
    manifest, _ = bundle()
    manifest['mode']['name'] = 'conflicting'
    assert client.post('/api/v1/runs', json=manifest, headers=HEADERS).status_code == 409
    assert client.get('/api/v1/runs').json()['total'] == 1


def test_incomplete_and_wrong_checksum_are_not_committed(client):
    m, files = bundle()
    client.post('/api/v1/runs', json=m, headers=HEADERS)
    assert client.post('/api/v1/runs/run-1/finalize', headers=HEADERS).status_code == 409
    assert client.put('/api/v1/runs/run-1/artifacts/serial.log', content=b'wrong', headers=HEADERS).status_code == 422
    assert client.get('/api/v1/runs/run-1').json()['upload_state'] == 'receiving'
    assert upload(client, m, files).status_code == 200


def test_original_bytes_and_bounded_log_context(client):
    upload(client)
    expected = bundle()[1]['serial.log']
    r = client.get('/api/v1/runs/run-1/artifacts/serial.log/download')
    assert r.content == expected
    r = client.get('/api/v1/runs/run-1/artifacts/serial.log/lines', params={'start': 2, 'limit': 2})
    assert [x['number'] for x in r.json()['lines']] == [2, 3]
    assert r.json()['lines'][0]['text'] == 'TT ERROR camera'
    assert r.json()['has_more'] is True
    assert client.get('/api/v1/runs/run-1/artifacts/serial.log/search', params={'q': 'TT ERROR'}).json()['matches'][0]['number'] == 2


def test_locate_send_command_by_time(client):
    m, files = bundle()
    files['serial.log'] = b'[10:00:00.100] RX< boot\n[10:00:01.000] TX> echo "init"\n[10:00:02.000] RX< init\n[10:00:03.500] TX> photo[auto]\n[10:00:04.000] RX< ok\n[10:00:05.000] TX> photo[single]\n'
    m['artifacts'][1].update(size=len(files['serial.log']), sha256=hashlib.sha256(files['serial.log']).hexdigest())
    upload(client, m, files)
    base = '/api/v1/runs/run-1/artifacts/serial.log/locate'
    # First TX line at/after the target wall-clock time.
    assert client.get(base, params={'at': '10:00:02.500'}).json()['number'] == 4
    # No TX at/after a late time falls back to the nearest earlier TX line.
    assert client.get(base, params={'at': '10:00:10.000'}).json()['number'] == 6
    assert client.get(base, params={'at': 'not-a-time'}).status_code == 422


def test_locate_across_midnight_wrap(client):
    # Log spans a midnight: first day runs late, second day runs early. Without a
    # day offset the naive wall-clock compare would mistake 18:14 for "past" 09:19.
    m, files = bundle()
    files['serial.log'] = b'[18:14:30.926] TX> boot\n[18:14:32.927] RX< ok\n[00:00:01.000] TX> midnight\n[09:19:00.054] TX> dfs_capture_start\n[09:19:04.170] TX> cam_set photo mfnr\n'
    m['artifacts'][1].update(size=len(files['serial.log']), sha256=hashlib.sha256(files['serial.log']).hexdigest())
    upload(client, m, files)
    base = '/api/v1/runs/run-1/artifacts/serial.log/locate'
    # Target is the second day 09:19:03 → day offset 1 → first TX at/after is line 5.
    assert client.get(base, params={'at': '09:19:03.000', 'day': 1}).json()['number'] == 5
    # Same wall-clock without the day offset would wrongly match the first-day TX.
    assert client.get(base, params={'at': '09:19:03.000'}).json()['number'] == 1
    # Negative/invalid day offset is rejected.
    assert client.get(base, params={'at': '09:19:03.000', 'day': -1}).status_code == 422


def test_restart_preserves_data_and_modes(client, settings):
    upload(client)
    with TestClient(create_app(settings)) as restarted:
        assert restarted.get('/api/v1/runs').json()['total'] == 1
        assert restarted.get('/api/v1/runs/run-1/artifacts/serial.log/download').content == bundle()[1]['serial.log']


def test_missing_report_and_aborted_run_never_pass(client):
    m, files = bundle(state='aborted')
    files.pop('result.json')
    m['artifacts'] = [a for a in m['artifacts'] if a['kind'] != 'report']
    assert upload(client, m, files).status_code == 200
    run = client.get('/api/v1/runs/run-1').json()
    assert run['status'] == 'ABORTED'
    assert any(i['category'] == 'incomplete' for i in run['issues'])


def test_unrecognized_status_keeps_original_and_flags_unknown(client):
    m, files = bundle(status='NEW_STATUS', detail='')
    upload(client, m, files)
    run = client.get('/api/v1/runs/run-1').json()
    assert run['status'] == 'UNKNOWN'
    assert run['results'][0]['status'] == 'NEW_STATUS'
    assert run['issues']


def test_filters_pagination_and_literal_search(client):
    upload(client)
    m, f = bundle('run-2', 'another', detail='')
    upload(client, m, f)
    assert client.get('/api/v1/runs', params={'issues_only': 'true'}).json()['total'] == 1
    assert client.get('/api/v1/runs', params={'mode': 'another'}).json()['total'] == 1
    assert len(client.get('/api/v1/runs', params={'page_size': 1}).json()['items']) == 1
    assert client.get('/api/v1/runs', params={'q': '%'}).json()['total'] == 0


def test_upload_auth_and_host_boundary(client):
    m, _ = bundle()
    assert client.post('/api/v1/runs', json=m).status_code == 401
    assert client.get('/api/v1/runs', headers={'host': 'evil.example'}).status_code == 400
    assert client.post('/api/v1/runs', json=m, headers={**HEADERS, 'Origin': 'https://evil.example'}).status_code == 403


def test_read_auth_when_enabled(settings):
    settings.require_read_auth = True
    with TestClient(create_app(settings)) as c:
        assert c.get('/api/v1/runs').status_code == 401
        assert c.post('/api/v1/session', json={'token': 'wrong'}).status_code == 401
        assert c.post('/api/v1/session', json={'token': TOKEN}).status_code == 200
        assert c.get('/api/v1/runs').status_code == 200


@pytest.mark.parametrize('name', ['../serial.log', '/etc/passwd', 'a\\b.log', '.', '\x00.log'])
def test_reject_unsafe_file_paths(client, name):
    m, _ = bundle()
    m['artifacts'][0]['name'] = name
    assert client.post('/api/v1/runs', json=m, headers=HEADERS).status_code == 422


def test_configured_size_limit_and_future_schema(client, settings):
    m, _ = bundle()
    m['schema_version'] = 99
    assert client.post('/api/v1/runs', json=m, headers=HEADERS).status_code == 422
    m['schema_version'] = 1
    m['artifacts'][0]['size'] = settings.max_file_bytes + 1
    assert client.post('/api/v1/runs', json=m, headers=HEADERS).status_code == 413


def test_configurable_classification_without_scenario_branches(tmp_path):
    rules = tmp_path / 'rules.json'
    rules.write_text(json.dumps({'version': 'custom-1', 'rules': [{'id': 'temp', 'name': '温度问题', 'keywords': ['OVERHEAT']}]}))
    cfg = Settings(data_dir=tmp_path / 'store', upload_token=TOKEN, rules_path=rules, allowed_hosts=('testserver',))
    with TestClient(create_app(cfg)) as c:
        m, f = bundle(detail='OVERHEAT', mode='never-seen-before')
        upload(c, m, f)
        run = c.get('/api/v1/runs/run-1').json()
        assert run['rules_version'] == 'custom-1'
        assert run['issues'][0]['category'] == 'temp'


def test_simultaneous_duplicate_create(client):
    m, _ = bundle()
    with ThreadPoolExecutor(max_workers=4) as pool:
        statuses = list(pool.map(lambda _: client.post('/api/v1/runs', json=m, headers=HEADERS).status_code, range(4)))
    assert all(s in (200, 201) for s in statuses)
    assert client.get('/api/v1/runs').json()['total'] == 1


def test_huge_single_line_is_truncated_only_in_view(client):
    m, f = bundle()
    f['serial.log'] = b'X' * 30000 + b'\nlast\n'
    a = m['artifacts'][1]
    a.update(size=len(f['serial.log']), sha256=hashlib.sha256(f['serial.log']).hexdigest())
    upload(client, m, f)
    page = client.get('/api/v1/runs/run-1/artifacts/serial.log/lines').json()
    assert page['lines'][0]['truncated'] is True
    assert len(page['lines'][0]['text']) < 30000
    assert page['lines'][1]['text'] == 'last'
    assert client.get('/api/v1/runs/run-1/artifacts/serial.log/download').content == f['serial.log']
