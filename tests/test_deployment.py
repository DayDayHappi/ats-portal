import hashlib
import json
import os
import socket
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path

import pytest
import httpx
from fastapi.testclient import TestClient

from ats_portal import config
from ats_portal.models import DomainError
from ats_portal.repository import Repository
from ats_portal.storage import LocalStorage
from ats_portal.web import create_app


def deployment(tmp_path):
    from ats_portal.launcher import initialize, load_config
    path = tmp_path / '中文 配置' / 'server.json'
    data = tmp_path / '中文 日志'
    initialize(path, data)
    return path, load_config(path, environ={})


def test_platform_defaults(monkeypatch, tmp_path):
    monkeypatch.setenv('LOCALAPPDATA', str(tmp_path / 'Local Data'))
    assert config.default_data_dir(platform='nt') == tmp_path / 'Local Data/ATS-Portal/data'
    assert config.default_config_dir(platform='nt') == tmp_path / 'Local Data/ATS-Portal/config'
    assert config.default_data_dir(platform='posix') == Path.home() / '.local/share/ats-portal'


def test_repeat_install_keeps_config_token_and_existing_data(tmp_path):
    from ats_portal.launcher import initialize, load_config
    path, runtime = deployment(tmp_path)
    token = runtime.settings.upload_token
    assert len(token) >= 24
    document = json.loads(path.read_text(encoding='utf-8'))
    document['port'] = 8099
    path.write_text(json.dumps(document, ensure_ascii=False), encoding='utf-8')
    marker = runtime.settings.data_dir / 'existing.log'
    marker.write_bytes(b'\x00existing\xff\r\n')
    before = path.read_bytes()
    initialize(path)
    loaded = load_config(path, environ={})
    assert loaded.port == 8099
    assert loaded.settings.upload_token == token
    assert path.read_bytes() == before
    assert marker.read_bytes() == b'\x00existing\xff\r\n'
    with pytest.raises(ValueError, match='data_dir'):
        initialize(path, tmp_path / 'different')


def test_env_overrides_do_not_modify_saved_settings(tmp_path):
    from ats_portal.launcher import load_config
    path, original = deployment(tmp_path)
    runtime = load_config(path, environ={
        'ATS_PORTAL_HOST': '0.0.0.0', 'ATS_PORTAL_PORT': '8123',
        'ATS_PORTAL_ALLOWED_HOSTS': 'localhost,192.168.1.20',
        'ATS_PORTAL_READ_AUTH': 'true', 'ATS_PORTAL_TIMEZONE': 'UTC',
    })
    assert runtime.host == '0.0.0.0' and runtime.port == 8123
    assert runtime.settings.require_read_auth is True
    assert runtime.settings.allowed_hosts == ('localhost', '192.168.1.20')
    assert runtime.settings.timezone == 'UTC'
    assert load_config(path, environ={}).port == original.port == 8080


@pytest.mark.parametrize('change', [
    {'port': 0}, {'port': 65536}, {'port': True}, {'host': 'http://localhost'},
    {'timezone': 'No/SuchZone'}, {'unknown_option': 1}, {'allowed_hosts': []},
    {'max_file_mb': 0}, {'ssl_certfile': 'missing.pem'},
    {'allowed_hosts': ['*example.com']}, {'allowed_hosts': ['example.*']},
])
def test_bad_configuration_is_rejected(tmp_path, change):
    from ats_portal.launcher import load_config
    path, _ = deployment(tmp_path)
    doc = json.loads(path.read_text(encoding='utf-8'))
    doc.update(change)
    path.write_text(json.dumps(doc), encoding='utf-8')
    with pytest.raises((ValueError, KeyError)):
        load_config(path, environ={})


def test_missing_token_is_not_silently_regenerated(tmp_path):
    from ats_portal.launcher import initialize, load_config
    path, _ = deployment(tmp_path)
    doc = json.loads(path.read_text(encoding='utf-8'))
    Path(doc['token_file']).unlink()
    with pytest.raises(RuntimeError):
        initialize(path)
    with pytest.raises(RuntimeError):
        load_config(path, environ={})
    assert not Path(doc['token_file']).exists()


def test_cli_check_and_corrupt_config_error(tmp_path):
    path, _ = deployment(tmp_path)
    env = {k: v for k, v in os.environ.items() if not k.startswith('ATS_PORTAL_')}
    args = [sys.executable, '-m', 'ats_portal.launcher', 'check', '--config', str(path)]
    result = subprocess.run(args, capture_output=True, text=True, encoding='utf-8', env=env)
    assert result.returncode == 0, result.stderr
    assert 'http://127.0.0.1:8080' in result.stdout
    path.write_text('{broken', encoding='utf-8')
    result = subprocess.run(args, capture_output=True, text=True, encoding='utf-8', env=env)
    assert result.returncode == 1
    assert 'server.json' in result.stderr


def test_timezone_works_without_system_iana_database():
    env = {**os.environ, 'PYTHONTZPATH': ''}
    code = "from datetime import datetime; from zoneinfo import ZoneInfo; assert datetime(2026, 9, 29, tzinfo=ZoneInfo('Asia/Shanghai')).utcoffset().total_seconds() == 28800"
    result = subprocess.run([sys.executable, '-c', code], env=env, capture_output=True, text=True)
    assert result.returncode == 0, result.stderr


def test_configured_server_upload_frontend_and_restart(tmp_path):
    from ats_portal.launcher import load_config
    path, runtime = deployment(tmp_path)
    data = '[2026-09-29 12:00:00] TX> reboot\r\n原始日志\r\n'.encode()
    manifest = {
        'run_id': 'windows-run-1', 'mode': {'id': 'future-mode', 'name': '可扩展模式'},
        'started_at': '2026-09-29T12:00:00+08:00', 'execution_state': 'completed',
        'artifacts': [{'name': 'serial.log', 'size': len(data), 'sha256': hashlib.sha256(data).hexdigest()}],
    }
    headers = {'Authorization': f'Bearer {runtime.settings.upload_token}'}
    with TestClient(create_app(runtime.settings), base_url='http://localhost') as client:
        assert client.get('/').status_code == 200
        for asset in ('app.js', 'results.js', 'style.css'):
            assert client.get('/' + asset).status_code == 200
        assert client.post('/api/v1/runs', json=manifest).status_code == 401
        assert client.post('/api/v1/runs', json=manifest, headers=headers).status_code == 201
        assert client.put('/api/v1/runs/windows-run-1/artifacts/serial.log', content=data, headers=headers).status_code == 200
        assert client.post('/api/v1/runs/windows-run-1/finalize', headers=headers).json()['upload_state'] == 'complete'
    with TestClient(create_app(load_config(path, environ={}).settings), base_url='http://localhost') as client:
        assert client.get('/api/v1/runs').json()['total'] == 1
        assert client.get('/api/v1/options').json()['modes'][0]['id'] == 'future-mode'
        assert client.get('/api/v1/runs/windows-run-1/artifacts/serial.log/download').content == data


def test_http_launcher_restart_keeps_raw_logs(tmp_path):
    path, runtime = deployment(tmp_path)
    with socket.socket() as sock:
        sock.bind(('127.0.0.1', 0))
        port = sock.getsockname()[1]
    doc = json.loads(path.read_text(encoding='utf-8'))
    doc['port'] = port
    path.write_text(json.dumps(doc), encoding='utf-8')
    env = {k: v for k, v in os.environ.items() if not k.startswith('ATS_PORTAL_')}
    env['PYTHONUTF8'] = '1'
    raw = b'TX> reboot\r\noriginal\x00\xff\n'
    manifest = {'run_id': 'http-run', 'mode': {'id': 'any-new-mode', 'name': 'New mode'},
                'started_at': '2026-09-29T12:00:00+08:00',
                'artifacts': [{'name': 'serial.log', 'size': len(raw), 'sha256': hashlib.sha256(raw).hexdigest()}]}
    headers = {'Authorization': f'Bearer {runtime.settings.upload_token}'}
    for phase in ('upload', 'restart'):
        with (tmp_path / f'{phase}.txt').open('wb') as output:
            process = subprocess.Popen([sys.executable, '-m', 'ats_portal.launcher', 'start', '--config', str(path)],
                                       stdout=output, stderr=subprocess.STDOUT, env=env)
            try:
                with httpx.Client(base_url=f'http://127.0.0.1:{port}', trust_env=False, timeout=2) as client:
                    deadline = time.monotonic() + 15
                    while True:
                        assert process.poll() is None, (tmp_path / f'{phase}.txt').read_text(encoding='utf-8')
                        try:
                            if client.get('/api/health').status_code == 200:
                                break
                        except httpx.TransportError:
                            pass
                        assert time.monotonic() < deadline, 'Server startup timed out'
                        time.sleep(0.05)
                    if phase == 'upload':
                        assert client.get('/api/v1/runs').json()['total'] == 0
                        assert client.get('/').status_code == 200
                        assert client.post('/api/v1/runs', json=manifest, headers=headers).status_code == 201
                        assert client.put('/api/v1/runs/http-run/artifacts/serial.log', headers=headers, content=raw).status_code == 200
                        assert client.post('/api/v1/runs/http-run/finalize', headers=headers).json()['upload_state'] == 'complete'
                    else:
                        assert client.get('/api/v1/runs').json()['total'] == 1
                        assert client.get('/api/v1/runs/http-run/artifacts/serial.log/download').content == raw
            finally:
                process.terminate()
                try:
                    process.wait(timeout=10)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait(timeout=5)


@pytest.mark.parametrize('name', ['NUL.log', 'CON', 'aux.txt', 'COM1.log', 'LPT9', 'x:y.log', 'x?.log', 'x.log ', 'x.', 'LONGFI~1.LOG'])
def test_windows_reserved_names_rejected_before_writing(tmp_path, name):
    storage = LocalStorage(tmp_path, windows_names=True)
    with pytest.raises(DomainError) as exc:
        storage.path('run-1', name)
    assert exc.value.status == 422


def test_windows_case_collisions_do_not_overwrite_artifacts(tmp_path):
    storage = LocalStorage(tmp_path / 'artifacts', windows_names=True)
    with pytest.raises(DomainError):
        storage.validate_manifest('run-1', [{'name': 'serial.log'}, {'name': 'SERIAL.log'}])
    with pytest.raises(DomainError):
        storage.validate_manifest('CON', [{'name': 'serial.log'}])
    repo = Repository(tmp_path / 'db.sqlite3', case_sensitive_ids=False)
    manifest = {'run_id': 'Run-1', 'mode': {'id': 'arbitrary', 'name': '任意'}, 'started_at': datetime.now().astimezone().isoformat(), 'artifacts': []}
    repo.register(manifest, '2026-09-29')
    with pytest.raises(DomainError) as exc:
        repo.register({**manifest, 'run_id': 'run-1'}, '2026-09-29')
    assert exc.value.status == 409
