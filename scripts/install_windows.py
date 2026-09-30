"""Windows installation orchestration; only Python's standard library is needed."""
import os
from pathlib import Path
import subprocess
import sys


def install():
    if os.name != 'nt':
        raise RuntimeError('This installer is for Windows. On Ubuntu use bash scripts/install.sh.')
    if sys.version_info < (3, 10):
        raise RuntimeError('Python 3.10 or newer is required; install Python 3.12 or 3.13, then retry.')
    root = Path(__file__).resolve().parent.parent
    environment = root / '.venv'
    python = environment / 'Scripts/python.exe'
    if environment.exists() and not python.is_file():
        raise RuntimeError('The .venv folder is incomplete or belongs to another OS. '
                           'Move/delete ONLY this project\'s .venv folder, then run install.cmd again. '
                           'Do not delete your ATS-Portal data directory.')
    if not python.is_file():
        print('Creating Windows Python environment...', flush=True)
        subprocess.run([sys.executable, '-m', 'venv', str(environment)], cwd=root, check=True)
    commands = [
        [str(python), '-m', 'pip', 'install', '--disable-pip-version-check', '-r', str(root / 'requirements.txt')],
        [str(python), '-m', 'ats_portal.launcher', 'init'],
        [str(python), '-m', 'ats_portal.launcher', 'check'],
    ]
    for command in commands:
        subprocess.run(command, cwd=root, check=True)
    print('\nInstallation complete. Double-click start.cmd, then open http://127.0.0.1:8080 '
          '(or the configured port shown above).', flush=True)


if __name__ == '__main__':
    try:
        install()
    except (OSError, RuntimeError, subprocess.CalledProcessError) as exc:
        print(f'Installation failed: {exc}\nCheck Python, network access and directory permissions. '
              'Existing archives and credentials have not been reset.', file=sys.stderr)
        raise SystemExit(1)
