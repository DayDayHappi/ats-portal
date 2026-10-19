"""Process configuration, independent of web routes and test modes."""
import os
from dataclasses import dataclass, field
from pathlib import Path
from zoneinfo import ZoneInfo


def default_data_dir(platform=None):
    if (platform or os.name) == 'nt':
        return Path(os.environ.get('LOCALAPPDATA') or Path.home() / 'AppData/Local') / 'ATS-Portal/data'
    return Path.home() / '.local/share/ats-portal'


def default_config_dir(platform=None):
    if (platform or os.name) == 'nt':
        return Path(os.environ.get('LOCALAPPDATA') or Path.home() / 'AppData/Local') / 'ATS-Portal/config'
    return Path.home() / '.config/ats-portal'


@dataclass
class Settings:
    data_dir: Path = field(default_factory=default_data_dir)
    upload_token: str = ''
    timezone: str = 'Asia/Shanghai'
    rules_path: Path = field(default_factory=lambda: Path(__file__).parent.parent / 'config/rules.json')
    labels_path: Path = field(default_factory=lambda: Path(__file__).parent.parent / 'config/labels.json')
    max_file_bytes: int = 512 * 1024 * 1024
    max_run_bytes: int = 2 * 1024 * 1024 * 1024
    max_report_bytes: int = 16 * 1024 * 1024
    require_read_auth: bool = False
    allowed_hosts: tuple = ('localhost', '127.0.0.1', '[::1]')
    ai_base_url: str = ''
    ai_api_key: str = ''
    ai_model: str = ''
    ai_timeout_seconds: int = 60

    @property
    def ai_enabled(self) -> bool:
        return bool(self.ai_base_url and self.ai_api_key and self.ai_model)

    def __post_init__(self):
        self.data_dir = Path(self.data_dir).expanduser().resolve()
        self.rules_path = Path(self.rules_path).expanduser().resolve()
        self.labels_path = Path(self.labels_path).expanduser().resolve()
        self.ai_base_url = self.ai_base_url.rstrip('/')
        ZoneInfo(self.timezone)
        if min(self.max_file_bytes, self.max_run_bytes, self.max_report_bytes) < 1:
            raise ValueError('文件大小限制必须大于 0')
        if self.ai_timeout_seconds < 1:
            raise ValueError('AI 超时时间必须大于 0')

    @classmethod
    def from_env(cls, environ=None):
        env = os.environ if environ is None else environ
        token_file = Path(env.get('ATS_PORTAL_TOKEN_FILE', str(default_config_dir() / 'token'))).expanduser()
        token = env.get('ATS_PORTAL_TOKEN', '') or (token_file.read_text(encoding='utf-8-sig').strip() if token_file.exists() else '')
        if len(token) < 24:
            raise RuntimeError('未配置有效上传凭据，请检查 token 文件。首次安装：Windows 运行 install.cmd；Ubuntu 执行 bash scripts/install.sh。')
        return cls(
            data_dir=Path(env.get('ATS_PORTAL_DATA_DIR', str(default_data_dir()))),
            upload_token=token,
            timezone=env.get('ATS_PORTAL_TIMEZONE', 'Asia/Shanghai'),
            rules_path=Path(env.get('ATS_PORTAL_RULES', str(Path(__file__).parent.parent / 'config/rules.json'))),
            labels_path=Path(env.get('ATS_PORTAL_LABELS', str(Path(__file__).parent.parent / 'config/labels.json'))),
            max_file_bytes=int(env.get('ATS_PORTAL_MAX_FILE_MB', '512')) * 1024**2,
            max_run_bytes=int(env.get('ATS_PORTAL_MAX_RUN_MB', '2048')) * 1024**2,
            require_read_auth=env.get('ATS_PORTAL_READ_AUTH', 'false').lower() == 'true',
            allowed_hosts=tuple(x.strip() for x in env.get('ATS_PORTAL_ALLOWED_HOSTS', 'localhost,127.0.0.1,[::1]').split(',') if x.strip()),
            ai_base_url=env.get('ATS_PORTAL_AI_BASE_URL', ''),
            ai_api_key=env.get('ATS_PORTAL_AI_API_KEY', ''),
            ai_model=env.get('ATS_PORTAL_AI_MODEL', ''),
            ai_timeout_seconds=int(env.get('ATS_PORTAL_AI_TIMEOUT', '60')),
        )
