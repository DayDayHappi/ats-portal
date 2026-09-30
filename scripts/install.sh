#!/usr/bin/env bash
set -euo pipefail
umask 077
ATS_APP_ROOT="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
ATS_CONFIG_DIR="${ATS_PORTAL_CONFIG_DIR:-${HOME}/.config/ats-portal}"
ATS_DATA_DIR="${ATS_PORTAL_DATA_DIR:-${HOME}/.local/share/ats-portal}"
python3 -c 'import sys; assert sys.version_info >= (3,10), "需要 Python 3.10 或更新版本"'
for ATS_CHECK_DIR in "${ATS_APP_ROOT}" "${ATS_APP_ROOT}/.venv"; do
  if [[ -d "${ATS_CHECK_DIR}" && ( ! -w "${ATS_CHECK_DIR}" || ! -x "${ATS_CHECK_DIR}" ) ]]; then
    printf '权限不足：当前用户无法写入或访问 %s\n' "${ATS_CHECK_DIR}" >&2
    printf '%s\n' '请检查该项目目录的属主和写权限，修复后以普通用户重新安装。' >&2
    exit 1
  fi
done
if ! python3 -m venv "${ATS_APP_ROOT}/.venv"; then
  printf '%s\n' '虚拟环境创建失败，请根据上方原始错误处理。' \
    'Permission denied 表示目录权限不足；只有提示缺少 venv/ensurepip 时才需安装 python3-venv。' >&2
  exit 1
fi
"${ATS_APP_ROOT}/.venv/bin/python" -m pip install --disable-pip-version-check -r "${ATS_APP_ROOT}/requirements.txt"
mkdir -p -- "${ATS_CONFIG_DIR}" "${ATS_DATA_DIR}"
ATS_CONFIG_DIR="${ATS_CONFIG_DIR}" ATS_DATA_DIR="${ATS_DATA_DIR}" "${ATS_APP_ROOT}/.venv/bin/python" - <<'PY'
import os, secrets, shlex
from pathlib import Path
config=Path(os.environ['ATS_CONFIG_DIR']).resolve()
token=config/'token'
if not token.exists():
    token.write_text(secrets.token_urlsafe(36)+'\n')
    token.chmod(0o600)
elif len(token.read_text().strip())<24:
    raise SystemExit('已有 token 文件无效，请检查配置；安装不会覆盖已有凭据。')
env=config/'portal.env'
if not env.exists():
    values={'ATS_PORTAL_DATA_DIR':str(Path(os.environ['ATS_DATA_DIR']).resolve()),
            'ATS_PORTAL_TOKEN_FILE':str(token),'ATS_PORTAL_PORT':'8080',
            'ATS_PORTAL_TIMEZONE':'Asia/Shanghai','ATS_PORTAL_READ_AUTH':'false'}
    env.write_text(''.join(f'export {k}={shlex.quote(v)}\n' for k,v in values.items()))
    env.chmod(0o600)
print('安装完成。配置：'+str(env))
print('数据目录：'+os.environ['ATS_DATA_DIR'])
print('启动：bash scripts/start.sh')
PY
