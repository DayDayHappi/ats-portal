"""Exercise real filesystem permissions as an unprivileged Ubuntu user."""
import os
from pathlib import Path
import shutil
import subprocess
import tempfile

import pytest


@pytest.mark.parametrize('blocked', ['project', 'venv'])
def test_install_identifies_permission_failure_without_dependency_advice(blocked):
    with tempfile.TemporaryDirectory(prefix='ats-install-permission-') as directory:
        base=Path(directory)
        base.chmod(0o755)
        project=base/'project'
        (project/'scripts').mkdir(parents=True)
        script=project/'scripts/install.sh'
        shutil.copyfile(Path(__file__).parents[1]/'scripts/install.sh',script)
        script.chmod(0o644)
        (project/'scripts').chmod(0o755)
        if blocked=='venv':
            project.chmod(0o777)
            (project/'.venv').mkdir(mode=0o555)
        else:
            project.chmod(0o555)
        target=project if blocked=='project' else project/'.venv'
        # Some containers map uid 0 to an unprivileged host user already.
        identity={'user':65534,'group':65534,'extra_groups':[]} if os.geteuid()==0 and os.access(target,os.W_OK) else {}
        try:
            result=subprocess.run(['/bin/bash',str(script)],capture_output=True,text=True,timeout=10,**identity)
            assert result.returncode!=0
            assert '权限不足' in result.stderr
            assert '请先安装 Ubuntu 的 python3-venv' not in result.stderr
            assert str(project) in result.stderr
        finally:
            project.chmod(0o755)
            if (project/'.venv').exists(): (project/'.venv').chmod(0o755)
