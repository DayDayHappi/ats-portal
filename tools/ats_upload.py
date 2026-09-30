#!/usr/bin/env python3
"""ATS archive sender. Python standard library only; no ATS imports or changes."""
import argparse
import contextlib
import datetime as dt
import fcntl
import hashlib
import http.client
import json
import os
from pathlib import Path
import re
import shutil
import socket
import subprocess
import sys
import tempfile
import time
from urllib.parse import quote, urlsplit
import uuid
from zoneinfo import ZoneInfo


CONFIG = Path.home() / '.config/ats-portal'
OUTBOX = Path.home() / '.local/share/ats-portal/outbox'


def digest(path):
    h = hashlib.sha256()
    with path.open('rb') as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b''):
            h.update(chunk)
    return h.hexdigest()


def write_json(path, value):
    temporary = path.with_suffix('.tmp')
    with temporary.open('w', encoding='utf-8') as stream:
        json.dump(value, stream, ensure_ascii=False, indent=2)
        stream.flush()
        os.fsync(stream.fileno())
    temporary.replace(path)


def aware_time(value):
    parsed = dt.datetime.fromisoformat(value)
    if parsed.utcoffset() is None:
        raise ValueError('时间必须包含时区，例如 2026-09-28T10:30:00+08:00')
    return parsed.isoformat()


def collect_files(log_dir, report=None, *, recursive=False):
    """Current ATS layout is isolated here; modes themselves are arbitrary data."""
    day, stamp = log_dir.parent.name, log_dir.name
    scenario_root = log_dir.parent.parent
    if report is None:
        candidates = [scenario_root/'report'/day/stamp/'result.json',
                      scenario_root.parent.parent/'reports'/day/stamp/'result.json']
        report = next((p for p in candidates if p.is_file()), None)
    elif not Path(report).is_file():
        raise ValueError(f'指定的报告不存在：{report}')
    files = []
    candidates = log_dir.rglob('*') if recursive else log_dir.iterdir()
    for p in sorted(candidates):
        if p.is_file() and not p.is_symlink() and p.suffix.lower() in ('.log', '.txt'):
            if not p.resolve().is_relative_to(log_dir.resolve()):
                raise ValueError('日志文件不能指向目录外')
            files.append((p, '__'.join(p.relative_to(log_dir).parts), 'log'))
    if report:
        files.append((Path(report), 'result.json', 'report'))
    note = scenario_root/'problem'/f'{stamp}.log'
    if note.is_file():
        files.append((note, 'problem.log', 'note'))
    if not files:
        raise ValueError('没有可上传的日志或报告')
    names = [name for _, name, _ in files]
    if len(names) != len(set(names)):
        duplicates = sorted({name for name in names if names.count(name)>1})
        raise ValueError('文件名冲突：' + '、'.join(duplicates[:10]) + '；请调整上传范围或冲突文件名')
    if len(names) > 100:
        raise ValueError(f'本次收集到 {len(names)} 个文件，超过单次上限 100；请缩小上传范围，默认仅收集顶层日志')
    if any(len(name) > 200 or '/' in name or '\\' in name or any(ord(c)<32 or ord(c)==127 for c in name) for name in names):
        raise ValueError('日志文件名过长或包含不支持的字符')
    return files


def queue_run(log_dir, outbox=OUTBOX, *, state='unknown', report=None, mode=None,
              mode_name=None, started_at=None, ended_at=None, source=None, new_run=False, recursive=False):
    log_dir, outbox = Path(log_dir).resolve(), Path(outbox).expanduser().resolve()
    if not log_dir.is_dir():
        raise ValueError(f'日志目录不存在：{log_dir}')
    if state not in ('completed', 'aborted', 'unknown'):
        raise ValueError('无效的执行状态')
    mode = mode or log_dir.parent.parent.name
    if not mode.strip() or len(mode)>160 or any(ord(c)<32 for c in mode):
        raise ValueError('请使用 --mode 提供有效的模式标识')
    label = mode_name or mode
    if not label.strip() or len(label)>200 or any(ord(c)<32 for c in label):
        raise ValueError('无效的模式显示名称')
    if started_at is None:
        try:
            started_at = dt.datetime.strptime(log_dir.name, '%Y%m%d_%H%M%S').replace(tzinfo=ZoneInfo('Asia/Shanghai')).isoformat()
        except ValueError as exc:
            raise ValueError('无法从目录识别开始时间，请提供 --started-at 和 --mode') from exc
    started_at = aware_time(started_at)
    ended_at = aware_time(ended_at) if ended_at else None
    if ended_at and dt.datetime.fromisoformat(ended_at)<dt.datetime.fromisoformat(started_at):
        raise ValueError('结束时间不能早于开始时间')
    files = collect_files(log_dir, report, recursive=recursive)
    fingerprint = [{'path': str(p), 'name': name, 'kind': kind, 'size': p.stat().st_size, 'sha256': digest(p)} for p, name, kind in files]
    identity = {'files': fingerprint, 'mode': {'id': mode, 'name': label}, 'started_at': started_at, 'ended_at': ended_at,
                'execution_state': state, 'source': source or {}}
    outbox.mkdir(parents=True, exist_ok=True, mode=0o700)
    key = hashlib.sha256(str(log_dir).encode()).hexdigest()
    with (outbox/'.lock').open('a') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        index_path = outbox/'index.json'
        index = json.loads(index_path.read_text()) if index_path.exists() else {}
        if key in index and not new_run:
            existing = outbox/index[key]
            previous = json.loads((existing/'source.json').read_text())
            if previous != identity:
                raise ValueError('同一日志目录的文件或元数据发生变更；重试请用 retry，确需另存请用 --new-run')
            return existing
        run_id = 'ats-' + uuid.uuid4().hex
        staging = Path(tempfile.mkdtemp(prefix='.pending-', dir=outbox))
        try:
            (staging/'files').mkdir()
            artifacts = []
            for (p, name, kind), expected in zip(files, fingerprint):
                dest = staging/'files'/name
                with p.open('rb') as src, dest.open('wb') as dst:
                    shutil.copyfileobj(src, dst, 1024*1024)
                    dst.flush()
                    os.fsync(dst.fileno())
                if dest.stat().st_size != expected['size'] or digest(dest) != expected['sha256']:
                    raise ValueError('复制期间日志发生变更；请等待 ATS 完全结束再上传')
                artifacts.append({k: expected[k] for k in ('name','kind','size','sha256')})
            manifest = {'schema_version': 1, 'run_id': run_id, 'mode': identity['mode'],
                        'started_at': started_at, 'ended_at': ended_at, 'execution_state': state,
                        'source': {'host':socket.gethostname(), **(source or {})}, 'artifacts': artifacts}
            write_json(staging/'manifest.json', manifest)
            write_json(staging/'source.json', identity)
            queued = outbox/run_id
            staging.replace(queued)
            index[key] = run_id
            write_json(index_path, index)
            return queued
        finally:
            if staging.exists():
                shutil.rmtree(staging)


class UploadError(Exception):
    def __init__(self, status, message):
        self.status = status
        super().__init__(f'HTTP {status}: {message}')


def upload_queued(queue, server, token, timeout=60):
    target = urlsplit(server)
    if target.scheme not in ('http','https') or not target.hostname or target.username or target.password or target.query or target.fragment:
        raise ValueError('服务器地址必须是 http(s) 地址，且不能包含凭据或查询参数')
    if target.scheme == 'http' and target.hostname not in ('127.0.0.1','localhost','::1'):
        raise ValueError('非本机地址必须使用 HTTPS，避免明文发送上传凭据')
    if not token or '\n' in token or '\r' in token:
        raise ValueError('缺少有效上传凭据')
    queue = Path(queue)
    manifest = json.loads((queue/'manifest.json').read_text())

    def request(method, suffix, *, data=None, file=None):
        connection_type = http.client.HTTPSConnection if target.scheme=='https' else http.client.HTTPConnection
        conn = connection_type(target.hostname, target.port, timeout=timeout)
        body = json.dumps(data, ensure_ascii=False).encode() if data is not None else b''
        size = file.stat().st_size if file else len(body)
        try:
            conn.putrequest(method, target.path.rstrip('/')+'/api/v1'+suffix)
            conn.putheader('Authorization','Bearer '+token)
            conn.putheader('Content-Type','application/octet-stream' if file else 'application/json')
            conn.putheader('Content-Length',str(size))
            conn.endheaders()
            if file:
                with file.open('rb') as stream:
                    for chunk in iter(lambda:stream.read(1024*1024),b''):
                        conn.send(chunk)
            elif body:
                conn.send(body)
            response = conn.getresponse()
            raw = response.read(32*1024*1024+1)
            if not 200<=response.status<300:
                raise UploadError(response.status, raw[:2000].decode('utf-8','replace'))
            if len(raw)>32*1024*1024:
                raise ValueError('服务器响应过大')
            return json.loads(raw)
        finally:
            conn.close()

    request('POST','/runs',data=manifest)
    prefix = '/runs/'+quote(manifest['run_id'],safe='')
    for artifact in manifest['artifacts']:
        path = queue/'files'/artifact['name']
        if path.stat().st_size!=artifact['size'] or digest(path)!=artifact['sha256']:
            raise ValueError('待发送队列文件已损坏，请保留原始日志并重新归档')
        request('PUT',prefix+'/artifacts/'+quote(artifact['name'],safe=''),file=path)
    result = request('POST',prefix+'/finalize')
    write_json(queue/'receipt.json', {'run_id':manifest['run_id'],'server':server,
                                     'uploaded_at':dt.datetime.now(dt.timezone.utc).isoformat()})
    return result


def run_and_collect(command, project):
    """Tee bytes immediately so ATS's interactive input prompt is not hidden."""
    project = Path(project).resolve()
    env = {**os.environ, 'PYTHONUNBUFFERED':'1'}
    process = subprocess.Popen(command, cwd=project, env=env, stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
    tail = b''
    stamp = log_root = report_root = None
    def examine(raw):
        nonlocal stamp, log_root, report_root
        line = re.sub(r'\x1b\[[0-?]*[ -/]*[@-~]', '', raw.decode('utf-8','replace'))
        match = re.search(r'运行时间戳[:：]\s*(\d{8}_\d{6})',line)
        if match: stamp = match.group(1)
        match = re.search(r'日志目录[:：]\s*(.*?)\s+报告目录[:：]\s*(.*?)\s*$',line)
        if match: log_root, report_root = map(Path,match.groups())
    try:
        while True:
            chunk = os.read(process.stdout.fileno(),8192)
            if not chunk: break
            sys.stdout.write(chunk.decode('utf-8','replace'))
            sys.stdout.flush()
            tail += chunk
            while b'\n' in tail:
                line, tail = tail.split(b'\n',1)
                examine(line)
            if len(tail)>65536: tail=tail[-65536:]
    except KeyboardInterrupt:
        import signal
        process.send_signal(signal.SIGINT)
        try: process.wait(timeout=15)
        except subprocess.TimeoutExpired: process.terminate()
    finally:
        process.stdout.close()
    examine(tail)
    code = process.wait()
    def absolute(path): return path if path.is_absolute() else project/path
    return {'returncode': code if code>=0 else 128-code,
            'log_dir': absolute(log_root)/stamp if log_root and stamp else None,
            'report': absolute(report_root)/stamp/'result.json' if report_root and stamp else None,
            'ended_at':dt.datetime.now(dt.timezone.utc).isoformat()}


def deliver(queue, args, token):
    for attempt in range(3):
        try:
            result=upload_queued(queue,args.server,token)
            print(f'归档成功：{queue.name}；测试状态：{result.get("status","已接收")}')
            return True
        except (OSError, http.client.HTTPException, UploadError, ValueError) as exc:
            retryable = isinstance(exc,(OSError,http.client.HTTPException)) or isinstance(exc,UploadError) and (exc.status in (408,429) or exc.status>=500)
            if attempt<2 and retryable:
                time.sleep(2**attempt)
            else:
                print(f'上传未完成：{exc}\n日志副本已保留：{queue}\n服务恢复后运行本工具的 retry 命令。',file=sys.stderr)
                return False


def main(argv=None):
    os.umask(0o077)
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--server',default=os.getenv('ATS_PORTAL_SERVER','http://127.0.0.1:8080'))
    parser.add_argument('--token-file',type=Path,default=CONFIG/'token')
    parser.add_argument('--outbox',type=Path,default=OUTBOX)
    sub=parser.add_subparsers(dest='action',required=True)
    send=sub.add_parser('send',help='上传已经结束的一次运行')
    send.add_argument('--log-dir',type=Path,required=True)
    send.add_argument('--report',type=Path)
    send.add_argument('--mode')
    send.add_argument('--mode-name')
    send.add_argument('--started-at')
    send.add_argument('--ended-at')
    send.add_argument('--state',choices=['completed','aborted','unknown'],default='unknown')
    send.add_argument('--device')
    send.add_argument('--firmware')
    send.add_argument('--new-run',action='store_true')
    send.add_argument('--recursive',action='store_true',help='额外收集子目录中的 .log/.txt；默认仅顶层日志')
    sub.add_parser('retry',help='重发尚未成功归档的队列')
    run=sub.add_parser('run',help='运行原 ATS 命令，结束后收集并上传')
    run.add_argument('--project',type=Path,required=True)
    run.add_argument('--mode-name')
    run.add_argument('--recursive',action='store_true',help='额外收集子目录中的 .log/.txt；默认仅顶层日志')
    run.add_argument('command',nargs=argparse.REMAINDER)
    args=parser.parse_args(argv)
    token=os.getenv('ATS_PORTAL_TOKEN','')
    if not token and args.token_file.is_file(): token=args.token_file.read_text().strip()
    if args.action=='run':
        command=args.command[1:] if args.command[:1]==['--'] else args.command
        if not command: parser.error('run 需要 -- 后的原 ATS 命令')
        result=run_and_collect(command,args.project)
        try:
            if not result['log_dir']: raise ValueError('未从 ATS 输出识别到日志目录；请使用 send 手动指定 --log-dir')
            report=result['report']
            queue=queue_run(result['log_dir'],args.outbox,state='completed' if result['returncode'] in (0,1) else 'aborted',
                            report=report if report and report.is_file() else None,
                            ended_at=result['ended_at'],mode_name=args.mode_name,recursive=args.recursive)
            deliver(queue,args,token)
        except (OSError,ValueError) as exc:
            print(f'自动归档未完成：{exc}；原 ATS 日志仍保留。',file=sys.stderr)
        return result['returncode']
    try:
        if args.action=='send':
            queue=queue_run(args.log_dir,args.outbox,state=args.state,report=args.report,mode=args.mode,mode_name=args.mode_name,
                            started_at=args.started_at,ended_at=args.ended_at,new_run=args.new_run,recursive=args.recursive,
                            source={k:getattr(args,k) for k in ('device','firmware') if getattr(args,k)})
            return 0 if deliver(queue,args,token) else 1
        success=True
        for queue in sorted(args.outbox.glob('ats-*')):
            if queue.is_dir() and (queue/'manifest.json').is_file() and not (queue/'receipt.json').exists():
                success=deliver(queue,args,token) and success
        return 0 if success else 1
    except (OSError,ValueError) as exc:
        print(f'归档失败：{exc}',file=sys.stderr)
        return 1


if __name__=='__main__':
    raise SystemExit(main())
