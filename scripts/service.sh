#!/usr/bin/env bash
set -euo pipefail
ATS_APP_ROOT="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
case "${1:-status}" in
  install)
    if [[ -n "${ATS_PORTAL_CONFIG_DIR:-}" ]]; then
      printf '%s\n' '用户服务使用默认 ~/.config/ats-portal 配置；自定义配置请使用 start.sh。' >&2
      exit 1
    fi
    ATS_APP_ROOT="${ATS_APP_ROOT}" python3 - <<'PY'
import os
from pathlib import Path
root=Path(os.environ['ATS_APP_ROOT'])
if any(ord(c)<32 for c in str(root)):
    raise SystemExit('服务目录路径不能含控制字符')
def quote(v): return '"'+str(v).replace('\\','\\\\').replace('"','\\"').replace('%','%%')+'"'
unit=Path.home()/'.config/systemd/user/ats-portal.service'
unit.parent.mkdir(parents=True,exist_ok=True)
unit.write_text('[Unit]\nDescription=ATS local test archive\nAfter=network.target\n\n[Service]\nType=simple\n'
                'WorkingDirectory='+quote(root)+'\nExecStart=/bin/bash '+quote(root/'scripts/start.sh').replace('$','$$')+'\n'
                'Restart=on-failure\nRestartSec=5\nUMask=0077\n\n[Install]\nWantedBy=default.target\n')
PY
    systemctl --user daemon-reload
    systemctl --user enable --now ats-portal.service
    ;;
  stop) systemctl --user stop ats-portal.service ;;
  start) systemctl --user start ats-portal.service ;;
  restart) systemctl --user restart ats-portal.service ;;
  status) systemctl --user status ats-portal.service ;;
  logs) journalctl --user -u ats-portal.service -n 100 --no-pager ;;
  remove)
    systemctl --user disable --now ats-portal.service
    rm -f -- "${HOME}/.config/systemd/user/ats-portal.service"
    systemctl --user daemon-reload
    ;;
  *) printf '%s\n' '用法：bash scripts/service.sh install|start|stop|restart|status|logs|remove' >&2; exit 2 ;;
esac
