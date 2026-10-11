import os
from pathlib import Path
import subprocess


def git(cwd, *args):
    env = {k:v for k,v in os.environ.items() if not k.startswith('GIT_')}
    env.update(GIT_CONFIG_GLOBAL=os.devnull, GIT_CONFIG_NOSYSTEM='1', GIT_ALLOW_PROTOCOL='file')
    return subprocess.run(['git','-c','user.name=Synthetic','-c','user.email=synthetic@local.invalid',
                           '-c','commit.gpgsign=false','-c','core.hooksPath=/dev/null',*args],
                          cwd=cwd,env=env,check=True,capture_output=True).stdout


def make_repos(root):
    root.mkdir(parents=True,exist_ok=True)
    remote=root/'local-remote.git'
    checkout=root/'diary-checkout'
    git(root,'init','--bare','-b','main',str(remote))
    git(root,'init','-b','main',str(checkout))
    (checkout/'.gitignore').write_text('*.tmp\n')
    git(checkout,'add','.gitignore')
    git(checkout,'commit','-m','synthetic baseline')
    git(checkout,'remote','add','origin',str(remote))
    git(checkout,'push','origin','main')
    return checkout,remote
