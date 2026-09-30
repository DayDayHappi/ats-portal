#!/usr/bin/env bash
set -euo pipefail
umask 077
ATS_APP_ROOT="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
ATS_CONFIG_DIR="${ATS_PORTAL_CONFIG_DIR:-${HOME}/.config/ats-portal}"
if [[ -f "${ATS_CONFIG_DIR}/portal.env" ]]; then
  source "${ATS_CONFIG_DIR}/portal.env"
fi
if [[ ! -x "${ATS_APP_ROOT}/.venv/bin/python" ]]; then
  printf '%s\n' '请先执行 bash scripts/install.sh' >&2
  exit 1
fi
cd -- "${ATS_APP_ROOT}"
exec "${ATS_APP_ROOT}/.venv/bin/python" -m uvicorn ats_portal.web:create_app --factory \
  --host 127.0.0.1 --port "${ATS_PORTAL_PORT:-8080}" --workers 1 --no-access-log
