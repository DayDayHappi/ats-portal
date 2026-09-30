"""Native deployment entry point. JSON configuration stays outside the program tree."""
import argparse
import ipaddress
import os
import secrets
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, StrictBool, field_validator, model_validator

from .config import Settings, default_config_dir, default_data_dir


class ServerConfig(BaseModel):
    model_config = ConfigDict(extra='forbid')
    schema_version: Literal[1] = 1
    host: str = '127.0.0.1'
    port: int = Field(default=8080, ge=1, le=65535, strict=True)
    data_dir: Path
    token_file: Path
    timezone: str = 'Asia/Shanghai'
    allowed_hosts: list[str] = Field(default_factory=lambda: ['localhost', '127.0.0.1', '[::1]'], min_length=1)
    read_auth: StrictBool = False
    max_file_mb: int = Field(default=512, ge=1, strict=True)
    max_run_mb: int = Field(default=2048, ge=1, strict=True)
    rules_path: Path | None = None
    ssl_certfile: Path | None = None
    ssl_keyfile: Path | None = None

    @field_validator('host')
    @classmethod
    def bind_address(cls, value):
        ipaddress.ip_address(value)
        return value

    @field_validator('allowed_hosts')
    @classmethod
    def meaningful_hosts(cls, values):
        if any(not host or any(c.isspace() for c in host) or '/' in host for host in values):
            raise ValueError('allowed_hosts 必须填写主机名或 IP，不含协议、路径和空格')
        if any('*' in host and host != '*' and (not host.startswith('*.') or '*' in host[1:]) for host in values):
            raise ValueError('allowed_hosts 通配符只能使用 * 或 *.example.com 格式')
        return values

    @model_validator(mode='after')
    def tls_pair(self):
        if bool(self.ssl_certfile) != bool(self.ssl_keyfile):
            raise ValueError('ssl_certfile 和 ssl_keyfile 必须同时配置')
        return self


@dataclass
class Runtime:
    settings: Settings
    host: str
    port: int
    ssl_certfile: str | None
    ssl_keyfile: str | None


def config_path():
    directory = Path(os.environ.get('ATS_PORTAL_CONFIG_DIR', str(default_config_dir()))).expanduser()
    return directory / 'server.json'


def _document(path):
    return ServerConfig.model_validate_json(Path(path).read_text(encoding='utf-8-sig'))


def _absolute(value, parent):
    path = Path(value).expanduser()
    return (path if path.is_absolute() else parent / path).resolve()


def load_config(config_file, environ=None):
    path = Path(config_file).expanduser().resolve()
    doc = _document(path)
    env = os.environ if environ is None else environ
    # Only the explicitly supported bind overrides enter the server config.
    bind = doc.model_dump()
    if 'ATS_PORTAL_HOST' in env:
        bind['host'] = env['ATS_PORTAL_HOST']
    if 'ATS_PORTAL_PORT' in env:
        bind['port'] = int(env['ATS_PORTAL_PORT'])
    doc = ServerConfig.model_validate(bind)
    application = {
        'ATS_PORTAL_DATA_DIR': str(_absolute(doc.data_dir, path.parent)),
        'ATS_PORTAL_TOKEN_FILE': str(_absolute(doc.token_file, path.parent)),
        'ATS_PORTAL_TIMEZONE': doc.timezone,
        'ATS_PORTAL_ALLOWED_HOSTS': ','.join(doc.allowed_hosts),
        'ATS_PORTAL_READ_AUTH': str(doc.read_auth).lower(),
        'ATS_PORTAL_MAX_FILE_MB': str(doc.max_file_mb),
        'ATS_PORTAL_MAX_RUN_MB': str(doc.max_run_mb),
    }
    if doc.rules_path is not None:
        application['ATS_PORTAL_RULES'] = str(_absolute(doc.rules_path, path.parent))
    application.update(env)
    if application['ATS_PORTAL_READ_AUTH'].lower() not in ('true', 'false'):
        raise ValueError('ATS_PORTAL_READ_AUTH 必须是 true 或 false')
    settings = Settings.from_env(application)
    ServerConfig.meaningful_hosts(list(settings.allowed_hosts))
    if not settings.allowed_hosts:
        raise ValueError('allowed_hosts 不能为空')
    tls = []
    for value in (doc.ssl_certfile, doc.ssl_keyfile):
        target = _absolute(value, path.parent) if value is not None else None
        if target is not None and not target.is_file():
            raise ValueError(f'TLS 文件不存在：{target}')
        tls.append(str(target) if target else None)
    return Runtime(settings, doc.host, doc.port, *tls)


def initialize(config_file, data_dir=None):
    path = Path(config_file).expanduser().resolve()
    if path.exists():
        runtime = load_config(path, environ={})
        if data_dir is not None and Path(data_dir).expanduser().resolve() != runtime.settings.data_dir:
            raise ValueError(f'已有配置的 data_dir 不同，请手工修改并迁移数据：{path}')
        runtime.settings.data_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
        return path
    data = Path(data_dir if data_dir is not None else default_data_dir()).expanduser().resolve()
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    data.mkdir(parents=True, exist_ok=True, mode=0o700)
    token_file = path.parent / 'token'
    if not token_file.exists():
        # Exclusive creation: a concurrent install must never replace a credential.
        try:
            fd = os.open(token_file, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        except FileExistsError:
            pass
        else:
            with os.fdopen(fd, 'w', encoding='utf-8') as stream:
                stream.write(secrets.token_urlsafe(36) + '\n')
    if len(token_file.read_text(encoding='utf-8-sig').strip()) < 24:
        raise RuntimeError(f'已有 token 无效，请恢复正确凭据：{token_file}')
    doc = ServerConfig(data_dir=data, token_file=token_file)
    with path.open('x', encoding='utf-8') as stream:
        stream.write(doc.model_dump_json(indent=2) + '\n')
    load_config(path, environ={})
    return path


def _describe(runtime, path):
    scheme = 'https' if runtime.ssl_certfile else 'http'
    host = runtime.host
    if host == '0.0.0.0':
        host = '127.0.0.1'
    elif host == '::':
        host = '::1'
    if ':' in host:
        host = f'[{host}]'
    print(f'Config: {path}\nData: {runtime.settings.data_dir}\nOpen: {scheme}://{host}:{runtime.port}', flush=True)


def main(argv=None):
    parser = argparse.ArgumentParser(description='ATS Portal website server')
    parser.add_argument('command', choices=('init', 'check', 'start'))
    parser.add_argument('--config', type=Path, default=config_path())
    parser.add_argument('--data-dir', type=Path, help='init only; existing data is never moved automatically')
    args = parser.parse_args(argv)
    try:
        if args.data_dir is not None and args.command != 'init':
            raise ValueError('--data-dir 只适用于 init；请在 server.json 中修改 data_dir')
        if args.command == 'init':
            initialize(args.config, args.data_dir)
        runtime = load_config(args.config)
        _describe(runtime, args.config.resolve())
        if args.command == 'init':
            return 0
        from .web import create_app
        app = create_app(runtime.settings)
        if args.command == 'check':
            print('Configuration, timezone, rules and database: OK', flush=True)
            return 0
        import uvicorn
        print('Keep this window open. Press Ctrl+C to stop.', flush=True)
        uvicorn.run(app, host=runtime.host, port=runtime.port, workers=1, access_log=False,
                    ssl_certfile=runtime.ssl_certfile, ssl_keyfile=runtime.ssl_keyfile)
        return 0
    except (OSError, ValueError, RuntimeError, KeyError) as exc:
        print(f'ATS Portal error ({args.config}): {exc}', file=sys.stderr)
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
