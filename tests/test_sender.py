import importlib.util
import json
import sys
from pathlib import Path

import pytest

SCRIPT = Path(__file__).parents[1] / 'tools/ats_upload.py'


@pytest.fixture
def sender():
    spec = importlib.util.spec_from_file_location('ats_upload', SCRIPT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def source(tmp_path):
    log = tmp_path / 'project/logs/future_mode/20260928/20260928_103000'
    log.mkdir(parents=True)
    (log/'serial.log').write_text('TT ERROR camera\n',encoding='utf-8')
    (log/'run.log').write_text('[PASS] video\n',encoding='utf-8')
    report=log.parents[1]/'report/20260928/20260928_103000/result.json'
    report.parent.mkdir(parents=True)
    report.write_text(json.dumps({'results':[{'name':'video','module':'video','status':'PASS','detail':'TT ERROR'}]}))
    problem=log.parents[1]/'problem/20260928_103000.log'
    problem.parent.mkdir()
    problem.write_text('现场观察：画面闪烁\n',encoding='utf-8')
    return log


def test_sender_queues_current_layout_and_immutable_retry(sender,source,tmp_path):
    queue=sender.queue_run(source,tmp_path/'outbox',state='completed')
    manifest=json.loads((queue/'manifest.json').read_text())
    assert manifest['mode']['id']=='future_mode'
    assert manifest['started_at']=='2026-09-28T10:30:00+08:00'
    assert any(a['kind']=='note' for a in manifest['artifacts'])
    assert any(a['kind']=='report' for a in manifest['artifacts'])
    assert sender.queue_run(source,tmp_path/'outbox',state='completed')==queue
    with pytest.raises(Exception):
        sender.upload_queued(queue,'http://127.0.0.1:1','test-token',timeout=.1)
    assert (queue/'manifest.json').exists()
    assert not (queue/'receipt.json').exists()
    (source/'serial.log').write_text('changed')
    assert (queue/'files/serial.log').read_text()=='TT ERROR camera\n'
    with pytest.raises(ValueError,match='变更'):
        sender.queue_run(source,tmp_path/'outbox',state='completed')


def test_sender_missing_report_is_supported_and_explicit(sender,tmp_path):
    log=tmp_path/'logs/custom/20260928/20260928_090000'
    log.mkdir(parents=True)
    (log/'run.log').write_text('interrupted')
    q=sender.queue_run(log,tmp_path/'outbox',state='aborted')
    m=json.loads((q/'manifest.json').read_text())
    assert m['execution_state']=='aborted'
    assert not any(a['kind']=='report' for a in m['artifacts'])


def test_default_collection_skips_large_nested_outputs_and_keeps_report(sender,source,tmp_path):
    (source/'recovery.log').write_text('recovered')
    (source/'power_switch.log').write_text('power on')
    for directory in ('photos','videos','video_integrity','future_output'):
        folder=source/directory
        folder.mkdir()
        for index in range(110):
            (folder/f'{index}.txt').write_text('derived output')
    queue=sender.queue_run(source,tmp_path/'outbox',state='completed')
    manifest=json.loads((queue/'manifest.json').read_text())
    assert {a['name'] for a in manifest['artifacts']}=={
        'run.log','serial.log','recovery.log','power_switch.log','result.json','problem.log'}
    assert (source/'video_integrity/109.txt').read_text()=='derived output'


def test_nested_logs_are_explicitly_opt_in(sender,source,tmp_path):
    nested=source/'custom'
    nested.mkdir()
    (nested/'detail.log').write_text('nested log')
    queue=sender.queue_run(source,tmp_path/'outbox',recursive=True)
    assert (queue/'files/custom__detail.log').read_text()=='nested log'


def test_collection_errors_distinguish_count_and_collision(sender,source):
    for i in range(101):
        (source/f'case-{i}.log').write_text('case')
    with pytest.raises(ValueError,match='105 个文件'):
        sender.collect_files(source)
    for p in source.glob('case-*.log'): p.unlink()
    (source/'problem.log').write_text('same destination name as manual note')
    with pytest.raises(ValueError,match='文件名冲突.*problem.log'):
        sender.collect_files(source)


def test_sender_wrapper_preserves_exit_and_captures_actual_path(sender,tmp_path):
    log=tmp_path/'project/logs/fixture/20260928/20260928_103000'
    report=tmp_path/'project/reports/20260928'
    code=f'''import pathlib,sys
p=pathlib.Path({str(log)!r});p.mkdir(parents=True)
(p/'run.log').write_text('failed')
print('VX100 EVB 自动化测试启动，运行时间戳: 20260928_103000',flush=True)
print('日志目录: '+str(p.parent)+'  报告目录: '+{str(report)!r},flush=True)
sys.exit(7)
'''
    result=sender.run_and_collect([sys.executable,'-c',code],tmp_path)
    assert result['returncode']==7
    assert result['log_dir']==log
    assert result['report']==report/'20260928_103000/result.json'


def test_sender_refuses_nonlocal_plaintext_token(sender,source,tmp_path):
    q=sender.queue_run(source,tmp_path/'outbox')
    with pytest.raises(ValueError,match='HTTPS'):
        sender.upload_queued(q,'http://example.com','token')


def test_sender_rejects_changed_end_time(sender,source,tmp_path):
    sender.queue_run(source,tmp_path/'outbox',state='completed',ended_at='2026-09-28T11:00:00+08:00')
    with pytest.raises(ValueError,match='变更'):
        sender.queue_run(source,tmp_path/'outbox',state='completed',ended_at='2026-09-28T12:00:00+08:00')


def test_sender_live_http_roundtrip_and_retry(sender,source,tmp_path):
    import socket
    import threading
    import time
    import urllib.request
    import uvicorn
    from ats_portal.config import Settings
    from ats_portal.web import create_app
    token='live-test-disposable-upload-token'
    app=create_app(Settings(data_dir=tmp_path/'server',upload_token=token))
    sock=socket.socket()
    sock.bind(('127.0.0.1',0))
    url='http://127.0.0.1:'+str(sock.getsockname()[1])
    server=uvicorn.Server(uvicorn.Config(app,log_level='error'))
    thread=threading.Thread(target=server.run,kwargs={'sockets':[sock]},daemon=True)
    thread.start()
    try:
        deadline=time.monotonic()+5
        while not server.started and time.monotonic()<deadline: time.sleep(.01)
        assert server.started
        queue=sender.queue_run(source,tmp_path/'outbox',state='completed')
        assert sender.upload_queued(queue,url,token)['status']=='PASS'
        assert sender.upload_queued(queue,url,token)['status']=='PASS'
        assert (queue/'receipt.json').is_file()
        with urllib.request.urlopen(url+'/api/v1/runs') as response:
            data=json.load(response)
        assert data['total']==1
        run_id=json.loads((queue/'manifest.json').read_text())['run_id']
        with urllib.request.urlopen(url+'/api/v1/runs/'+run_id+'/artifacts/serial.log/download') as response:
            assert response.read()==(source/'serial.log').read_bytes()
    finally:
        server.should_exit=True
        thread.join(timeout=5)
        sock.close()
