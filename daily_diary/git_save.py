"""Only local bare repositories are enabled in this implementation stage."""
import os
from pathlib import Path
import re
import subprocess

from .collect import sanitize_text
from .core import (Config, DayState, DiaryError, SaveResult, body_hash, day_lock,
                   load_state, safe_path, write_state)


def _git(cwd: Path, *args: str, optional: bool = False) -> bytes | None:
    # Ignore global configuration and inherited Git overrides. Never permit network protocols.
    env = {k:v for k,v in os.environ.items() if not k.startswith('GIT_')}
    env.update(GIT_CONFIG_GLOBAL=os.devnull, GIT_CONFIG_NOSYSTEM='1',
               GIT_ALLOW_PROTOCOL='file', GIT_TERMINAL_PROMPT='0', GIT_NO_REPLACE_OBJECTS='1')
    command = ['git', '-c', 'core.hooksPath=/dev/null', '-c', 'core.fsmonitor=false',
               '-c', 'core.attributesFile=/dev/null', '-c', 'commit.gpgsign=false',
               '-c', 'user.name=Diary helper', '-c', 'user.email=diary@local.invalid', *args]
    try:
        result = subprocess.run(command,cwd=cwd,env=env,capture_output=True,timeout=30,check=False)
    except (OSError, subprocess.TimeoutExpired):
        raise DiaryError('git_failed') from None
    if result.returncode:
        if optional:
            return None
        raise DiaryError('git_failed')
    return result.stdout


def _paths(config: Config, path: str) -> tuple[Path, Path, Path]:
    checkout = safe_path(config.runtime_root, 'diary-checkout')
    if config.checkout != checkout or not checkout.is_dir():
        raise DiaryError('dedicated_checkout_required')
    if not config.repo_name or not Path(config.repo_name).is_absolute():
        raise DiaryError('local_remote_required')
    remote = Path(config.repo_name)
    try:
        relative = remote.relative_to(config.runtime_root)
    except ValueError:
        raise DiaryError('local_remote_required') from None
    if remote == checkout or remote == config.runtime_root or len(relative.parts) != 1:
        raise DiaryError('local_remote_required')
    remote = safe_path(config.runtime_root, str(relative))
    target = safe_path(config.runtime_root, f'diary-checkout/{path}')
    if safe_path(config.runtime_root, 'diary-checkout/.git').is_file():
        raise DiaryError('dedicated_checkout_required')
    if not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_/-]*', config.branch) or '..' in config.branch:
        raise DiaryError('invalid_branch')
    # Inspect all metadata before invoking Git: refs/config/objects may redirect writes.
    for metadata in (checkout / '.git', remote):
        if not metadata.is_dir():
            raise DiaryError('dedicated_checkout_required')
        if metadata.is_symlink() or any(p.is_symlink() for p in metadata.rglob('*')):
            raise DiaryError('symlink_path')
    for repo, prefix in ((checkout,'.git/'), (remote,'')):
        raw = _git(repo,'config','--local','--no-includes','--list').decode()
        for line in raw.splitlines():
            key = line.partition('=')[0].lower()
            if key.startswith(('filter.', 'url.', 'include.', 'includeif.')) or key == 'core.worktree':
                raise DiaryError('unsafe_git_config')
        for filename in ('info/attributes','objects/info/alternates'):
            if safe_path(config.runtime_root, str(repo.relative_to(config.runtime_root) / (prefix+filename))).exists():
                raise DiaryError('unsafe_git_config')
    if any(checkout.rglob('.gitattributes')):
        raise DiaryError('unsafe_git_config')
    if _git(remote,'rev-parse','--is-bare-repository').strip() != b'true':
        raise DiaryError('local_bare_required')
    for repo, metadata in ((checkout, checkout / '.git'), (remote, remote)):
        for flag in ('--absolute-git-dir', '--git-common-dir'):
            effective = Path(_git(repo, 'rev-parse', flag).decode().strip())
            if not effective.is_absolute():
                effective = repo / effective
            if effective.resolve() != metadata:
                raise DiaryError('unsafe_git_config')
    if Path(_git(checkout,'rev-parse','--show-toplevel').decode().strip()).resolve() != checkout:
        raise DiaryError('unsafe_git_config')
    if _git(checkout,'branch','--show-current').decode().strip() != config.branch:
        raise DiaryError('checkout_branch_mismatch')
    return checkout, remote, target


def _blob(repo: Path, ref: str, path: str) -> bytes | None:
    return _git(repo,'show',f'{ref}:{path}',optional=True)


def _blob_id(repo: Path, ref: str, path: str) -> str | None:
    value = _git(repo,'rev-parse','--verify',f'{ref}:{path}',optional=True)
    return value.decode().strip() if value else None


def _approved(state: DayState, body: bytes) -> bool:
    a = state.approval
    return bool(a and (a.day,a.revision,a.body_sha256) ==
                (state.day,state.revision,body_hash(body)) and state.body_sha256 == body_hash(body))


def _saved(config: Config, state: DayState, checkout: Path, path: str) -> SaveResult:
    state.commit_id = _git(checkout,'rev-parse','refs/diary/remote').decode().strip()
    state.base_blob = _blob_id(checkout,'refs/diary/remote',path)
    state.status, state.push_status = '保存済み', 'confirmed'
    write_state(config.runtime_root,state)
    return SaveResult('保存済み',state.commit_id)


def save_day(config: Config, state: DayState, body: bytes) -> SaveResult:
    with day_lock(config.runtime_root,state.day):
        current = load_state(config.runtime_root,state.day)
        if current is None or not _approved(current,body):
            return SaveResult('要対応',reason_code='approval_required')
        state = current
        path = f'diary/{state.day.year}/{state.day}.md'
        try:
            # A second secret filter must not silently change already-approved bytes.
            if sanitize_text(body.decode()) != body.decode().rstrip('\n'):
                raise DiaryError('unsafe_approved_body')
            checkout, remote, target = _paths(config,path)
            if _git(checkout,'status','--porcelain','--untracked-files=all').strip():
                raise DiaryError('checkout_dirty')
            if not _git(remote,'rev-parse','--verify',f'refs/heads/{config.branch}',optional=True):
                raise DiaryError('remote_branch_missing')
            _git(checkout,'fetch','--no-tags',str(remote),f'refs/heads/{config.branch}:refs/diary/remote')
            remote_body = _blob(checkout,'refs/diary/remote',path)
            if remote_body == body:
                return _saved(config,state,checkout,path)
            remote_blob = _blob_id(checkout,'refs/diary/remote',path)
            if remote_blob != state.base_blob:
                raise DiaryError('target_changed')
            local_head = _git(checkout,'rev-parse','HEAD').decode().strip()
            remote_head = _git(checkout,'rev-parse','refs/diary/remote').decode().strip()
            recovery = (state.push_status == 'unknown' and state.commit_id == local_head and
                        _blob(checkout,'HEAD',path) == body)
            if recovery:
                # Reuse exactly the recorded one-file commit, never unrelated local changes.
                if _git(checkout,'merge-base','--is-ancestor',remote_head,local_head,optional=True) is None:
                    raise DiaryError('git_conflict')
                changed = _git(checkout,'diff','--name-only',remote_head,local_head).decode().splitlines()
                if changed != [path]:
                    raise DiaryError('unexpected_outgoing_changes')
            else:
                if local_head != remote_head:
                    if _git(checkout,'merge-base','--is-ancestor',local_head,remote_head,optional=True) is None:
                        raise DiaryError('git_conflict')
                    _git(checkout,'merge','--ff-only','refs/diary/remote')
                if _blob_id(checkout,'HEAD',path) != state.base_blob:
                    raise DiaryError('target_changed')
                if target.exists() and target.read_bytes() != _blob(checkout,'HEAD',path):
                    raise DiaryError('target_changed')
                target.parent.mkdir(mode=0o700,parents=True,exist_ok=True)
                safe_path(config.runtime_root,f'diary-checkout/{path}')
                # File is synthetic in this stage; no real diary is ever passed by the test suite.
                with open(target,'wb') as f:
                    f.write(body)
                    f.flush()
                    os.fsync(f.fileno())
                _git(checkout,'add','--',path)
                if _git(checkout,'show',f':{path}') != body:
                    raise DiaryError('staged_body_changed')
                _git(checkout,'commit','-m',f'diary: {state.day} revision {state.revision}','--',path)
                if _blob(checkout,'HEAD',path) != body:
                    raise DiaryError('committed_body_changed')
                changed = _git(checkout,'diff-tree','--no-commit-id','--name-only','-r','HEAD').decode().splitlines()
                if changed != [path]:
                    raise DiaryError('unexpected_outgoing_changes')
                state.commit_id = _git(checkout,'rev-parse','HEAD').decode().strip()
            state.status, state.push_status = '保存待ち', 'unknown'
            write_state(config.runtime_root,state)
            try:
                _git(checkout,'push',str(remote),f'HEAD:refs/heads/{config.branch}')
                _git(checkout,'fetch','--no-tags',str(remote),f'refs/heads/{config.branch}:refs/diary/remote')
                if _blob(checkout,'refs/diary/remote',path) != body:
                    raise DiaryError('git_failed')
                return _saved(config,state,checkout,path)
            except DiaryError:
                return SaveResult('保存待ち',state.commit_id,'push_result_unknown')
        except DiaryError as error:
            state.status = '要対応'
            write_state(config.runtime_root,state)
            return SaveResult('要対応',state.commit_id,str(error))
