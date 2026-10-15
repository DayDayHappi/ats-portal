import hashlib
import json
import sqlite3
from concurrent.futures import ThreadPoolExecutor

import pytest
from fastapi.testclient import TestClient

from ats_portal.config import Settings
from ats_portal.repository import Repository
from ats_portal.web import create_app

TOKEN = 'test-token-that-is-only-used-in-tests'
HEADERS = {'Authorization': f'Bearer {TOKEN}'}


@pytest.fixture
def settings(tmp_path):
    # Empty labels path: annotation tests start from a blank label list, so they
    # can create names that also happen to appear in config/labels.json.
    return Settings(data_dir=tmp_path / 'data', upload_token=TOKEN, allowed_hosts=('testserver', '127.0.0.1', 'localhost'), labels_path=tmp_path / 'labels.json')


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


def test_search_finds_match_far_past_scan_budget(client):
    # A match near the end of a large file must surface in one request: search
    # scans to EOF, not to a fixed byte budget that would silently hide it.
    m, f = bundle()
    f['serial.log'] = (b'filler\n' * 100000) + b'[03:55:05.942] RX< bytes_written = 0, JX_IMU_METADATA_TIME = 448\n'
    m['artifacts'][1].update(size=len(f['serial.log']), sha256=hashlib.sha256(f['serial.log']).hexdigest())
    upload(client, m, f)
    page = client.get('/api/v1/runs/run-1/artifacts/serial.log/search', params={'q': 'bytes_written = 0, JX_IMU_METADATA_TIME = 448'}).json()
    assert len(page['matches']) == 1
    assert page['matches'][0]['number'] == 100001
    assert page['has_more'] is False


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


# ---- human root-cause annotation ----

def fail_run(client, run_id='run-1'):
    """Upload a run whose result needs attention (status FAIL, linked issue)."""
    m, files = bundle(run_id=run_id, status='FAIL')
    upload(client, m, files)
    return m


def test_label_crud(client):
    r = client.post('/api/v1/labels', json={'name': '固件缺陷'})
    assert r.status_code == 201
    label_id = r.json()['id']
    assert client.get('/api/v1/labels').json()[0]['name'] == '固件缺陷'
    # empty / control characters rejected
    assert client.post('/api/v1/labels', json={'name': '  '}).status_code == 422
    assert client.post('/api/v1/labels', json={'name': 'a\nb'}).status_code == 422
    # duplicate name rejected (case-insensitive on all platforms)
    assert client.post('/api/v1/labels', json={'name': '固件缺陷'}).status_code == 409
    # rename
    assert client.patch(f'/api/v1/labels/{label_id}', json={'name': '环境问题'}).status_code == 200
    assert client.patch('/api/v1/labels/missing', json={'name': 'x'}).status_code == 404
    # rename to an existing name rejected
    client.post('/api/v1/labels', json={'name': '用例问题'})
    assert client.patch(f'/api/v1/labels/{label_id}', json={'name': '用例问题'}).status_code == 409
    # delete
    assert client.delete(f'/api/v1/labels/{label_id}').status_code == 204
    assert client.delete('/api/v1/labels/missing').status_code == 404


def test_annotation_crud_and_snapshot(client):
    fail_run(client)
    l1 = client.post('/api/v1/labels', json={'name': '固件缺陷'}).json()
    l2 = client.post('/api/v1/labels', json={'name': '环境问题'}).json()
    base = '/api/v1/runs/run-1/results/0/annotation'
    body = {'label_ids': [l1['id'], l2['id']], 'note': '视频模块偶发丢帧', 'author': '张三'}
    r = client.put(base, json=body)
    assert r.status_code in (200, 201)
    ann = r.json()
    assert {x['name'] for x in ann['labels']} == {'固件缺陷', '环境问题'}
    assert ann['note'] == '视频模块偶发丢帧'
    assert ann['author'] == '张三'
    # read back
    anns = client.get('/api/v1/runs/run-1/annotations').json()
    assert len(anns) == 1 and anns[0]['result_index'] == 0
    # idempotent replace
    body2 = {'label_ids': [l1['id']], 'note': '改判：仅固件', 'author': '张三'}
    ann = client.put(base, json=body2).json()
    assert [x['name'] for x in ann['labels']] == ['固件缺陷']
    assert ann['note'] == '改判：仅固件'
    assert client.get('/api/v1/runs/run-1/annotations').json()[0]['labels'] == [{'id': l1['id'], 'name': '固件缺陷'}]
    # rename label: snapshot keeps old name, new annotations use new name
    client.patch(f"/api/v1/labels/{l1['id']}", json={'name': '固件问题'})
    assert client.get('/api/v1/runs/run-1/annotations').json()[0]['labels'][0]['name'] == '固件缺陷'
    # delete
    assert client.delete(base).status_code == 204
    assert client.get('/api/v1/runs/run-1/annotations').json() == []
    assert client.delete(base).status_code == 404


def test_annotation_validation(client):
    fail_run(client)
    l = client.post('/api/v1/labels', json={'name': '固件缺陷'}).json()
    base = '/api/v1/runs/run-1/results/0/annotation'
    # out of range result_index
    assert client.put('/api/v1/runs/run-1/results/9/annotation', json={'label_ids': [l['id']]}).status_code == 422
    # unknown label id
    assert client.put(base, json={'label_ids': ['nope']}).status_code == 422
    # empty label list
    assert client.put(base, json={'label_ids': []}).status_code == 422
    # PASS with no issue is not annotatable
    m, files = bundle(run_id='pass-run', status='PASS', detail='')
    upload(client, m, files)
    assert client.put('/api/v1/runs/pass-run/results/0/annotation', json={'label_ids': [l['id']]}).status_code == 422


def test_annotation_requires_complete(client):
    m, _ = bundle()
    client.post('/api/v1/runs', json=m, headers=HEADERS)
    l = client.post('/api/v1/labels', json={'name': '固件缺陷'}).json()
    assert client.put('/api/v1/runs/run-1/results/0/annotation', json={'label_ids': [l['id']]}).status_code == 409


def test_pass_with_keyword_rule_is_annotatable(client):
    # PASS but detail hits a keyword rule without statuses restriction → attention
    m, files = bundle(run_id='pass-tt', status='PASS', detail='TT ERROR camera')
    upload(client, m, files)
    run = client.get('/api/v1/runs/pass-tt').json()
    assert any(i['category'] == 'firmware_keyword' for i in run['issues'])
    l = client.post('/api/v1/labels', json={'name': '固件缺陷'}).json()
    assert client.put('/api/v1/runs/pass-tt/results/0/annotation', json={'label_ids': [l['id']]}).status_code == 200


def test_unannotated_filter_and_label_filter(client):
    fail_run(client, 'run-a')
    fail_run(client, 'run-b')
    l = client.post('/api/v1/labels', json={'name': '固件缺陷'}).json()
    l2 = client.post('/api/v1/labels', json={'name': '环境问题'}).json()
    # both needs attention and are unannotated
    assert client.get('/api/v1/runs', params={'unannotated': 'true'}).json()['total'] == 2
    assert client.get('/api/v1/runs').json()['stats']['needs_annotation'] == 2
    # annotate one with two labels
    client.put('/api/v1/runs/run-a/results/0/annotation', json={'label_ids': [l['id'], l2['id']]})
    assert client.get('/api/v1/runs', params={'unannotated': 'true'}).json()['total'] == 1
    assert client.get('/api/v1/runs', params={'label': l['id']}).json()['total'] == 1
    assert client.get('/api/v1/runs', params={'label': l2['id']}).json()['total'] == 1
    # label filter composes with other filters (mode + label)
    assert client.get('/api/v1/runs', params={'label': l['id'], 'mode': 'no-such'}).json()['total'] == 0
    # label usage count
    labels = {x['id']: x['count'] for x in client.get('/api/v1/labels').json()}
    assert labels[l['id']] == 1 and labels[l2['id']] == 1


def test_annotation_auth_and_csrf(settings):
    settings.require_read_auth = True
    with TestClient(create_app(settings)) as c:
        assert c.get('/api/v1/labels').status_code == 401
        assert c.post('/api/v1/labels', json={'name': 'x'}).status_code == 401
        # cross-site write rejected by boundaries middleware even when authorized
        assert c.post('/api/v1/labels', json={'name': 'x'}, headers={**HEADERS, 'Origin': 'https://evil.example'}).status_code == 403


def test_migration_v1_to_v2(tmp_path):
    db_path = tmp_path / 'portal.sqlite3'
    with sqlite3.connect(db_path) as db:
        db.executescript('''
        CREATE TABLE runs (
            id TEXT PRIMARY KEY, manifest TEXT NOT NULL,
            mode_id TEXT NOT NULL, mode_name TEXT NOT NULL,
            started_at TEXT NOT NULL, started_epoch REAL NOT NULL,
            run_date TEXT NOT NULL, received_at TEXT NOT NULL,
            upload_state TEXT NOT NULL, status TEXT NOT NULL,
            issue_count INTEGER NOT NULL DEFAULT 0,
            payload TEXT NOT NULL
        );
        CREATE INDEX runs_date ON runs(run_date, started_epoch DESC);
        CREATE INDEX runs_mode ON runs(mode_id, started_epoch DESC);
        CREATE TABLE issues (run_id TEXT NOT NULL REFERENCES runs(id), category TEXT NOT NULL, category_name TEXT NOT NULL);
        CREATE INDEX issues_category ON issues(category,run_id);
        PRAGMA user_version=1;
        ''')
        payload = {'id': 'old-1', 'status': 'FAIL', 'issues': [{'category': 'test_failure', 'category_name': '用例失败', 'result_index': 0}], 'results': [{'index': 0, 'status': 'FAIL'}]}
        db.execute('INSERT INTO runs VALUES(?,?,?,?,?,?,?,?,?,?,?,?)', ('old-1', '{}', 'm', 'm', '2026-09-28T10:00:00+08:00', 0.0, '2026-09-28', 'now', 'complete', 'FAIL', 1, json.dumps(payload)))
    repo = Repository(db_path, labels=[{'id': 'seed', 'name': '播种标签'}])
    with repo.connect() as db:
        assert db.execute('PRAGMA user_version').fetchone()[0] == 3
        assert db.execute('SELECT attention_count FROM runs WHERE id=?', ('old-1',)).fetchone()[0] == 1
        assert db.execute('SELECT name FROM labels WHERE id=?', ('seed',)).fetchone()[0] == '播种标签'
        # run_meta table exists from the v2→v3 step
        db.execute('SELECT run_id FROM run_meta LIMIT 0').fetchall()


def test_run_meta_edit_and_fallback(client):
    # bundle() carries no device/firmware, so the detail falls back to 未记录.
    fail_run(client, 'run-a')
    detail = client.get('/api/v1/runs/run-a').json()
    assert detail['meta'] is None
    # edit device/firmware with an author
    r = client.put('/api/v1/runs/run-a/meta', json={'device': 'EVB-01', 'firmware': 'build-123', 'author': '张三'})
    assert r.status_code == 200
    meta = r.json()
    assert meta['device'] == 'EVB-01' and meta['firmware'] == 'build-123' and meta['author'] == '张三'
    assert client.get('/api/v1/runs/run-a').json()['meta'] == meta
    # clearing both fields stores an empty override (meta no longer None)
    meta2 = client.put('/api/v1/runs/run-a/meta', json={'device': '', 'firmware': '', 'author': '张三'}).json()
    assert meta2['device'] == '' and meta2['firmware'] == ''
    assert client.get('/api/v1/runs/run-a/meta').json()['device'] == ''


def test_run_meta_keeps_original_source(client):
    # source.device/firmware stay intact in the manifest; meta is a separate overlay.
    m, files = bundle(run_id='with-device')
    m['source'] = {'host': 'ubuntu', 'device': 'ORIG-EVB', 'firmware': 'ORIG-FW'}
    upload(client, m, files)
    detail = client.get('/api/v1/runs/with-device').json()
    assert detail['source']['device'] == 'ORIG-EVB'
    client.put('/api/v1/runs/with-device/meta', json={'device': 'NEW-EVB', 'firmware': '', 'author': '李四'})
    detail = client.get('/api/v1/runs/with-device').json()
    assert detail['source']['device'] == 'ORIG-EVB'  # untouched
    assert detail['meta']['device'] == 'NEW-EVB'  # overlay


def test_run_meta_requires_complete(client):
    m, _ = bundle()
    client.post('/api/v1/runs', json=m, headers=HEADERS)
    assert client.put('/api/v1/runs/run-1/meta', json={'device': 'x'}).status_code == 409
    assert client.get('/api/v1/runs/run-1/meta').status_code == 200


def test_run_meta_auth(settings):
    settings.require_read_auth = True
    with TestClient(create_app(settings)) as c:
        assert c.get('/api/v1/runs/any/meta').status_code == 401
        assert c.put('/api/v1/runs/any/meta', json={'device': 'x'}).status_code == 401


def test_future_schema_rejected(tmp_path):
    db_path = tmp_path / 'portal.sqlite3'
    with sqlite3.connect(db_path) as db:
        db.execute('PRAGMA user_version=99')
    with pytest.raises(RuntimeError):
        Repository(db_path)
