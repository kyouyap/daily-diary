"""Day identity, byte-bound approvals and private local state."""
from contextlib import contextmanager
from dataclasses import asdict, dataclass, field
from datetime import date, datetime, timedelta, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import tempfile
from zoneinfo import ZoneInfo

JST = ZoneInfo('Asia/Tokyo')
STATUSES = {'下書き', '対話中', '確認待ち', '保存待ち', '保存済み', '要対応'}


class DiaryError(Exception):
    """Only a fixed reason code; never embed untrusted data or stderr."""


@dataclass
class Config:
    runtime_root: Path
    checkout: Path | None = None
    project_id: str = ''
    user_id: str = ''
    base_url: str = ''
    repo_owner: str = ''
    repo_name: str = ''
    branch: str = 'main'
    session_ids: tuple[str, ...] = ()


@dataclass
class Source:
    project_id: str
    trace_id: str
    observation_id: str
    turn_id: str
    started_at: datetime | None
    ended_at: datetime | None
    kind: str
    text: str


@dataclass
class Collection:
    sources: list[Source]
    window_start: datetime
    window_end: datetime
    collected_at: datetime
    status: str
    warnings: list[str]
    observation_count: int = 0
    complete: bool = False


@dataclass
class Page:
    records: list[dict]
    next_cursor: str | None = None


@dataclass
class Approval:
    day: date
    revision: int
    body_sha256: str
    reply_id: str
    approved_at: datetime


@dataclass
class DayState:
    day: date
    revision: int = 1
    status: str = '下書き'
    body_sha256: str = ''
    source_ids: list[str] = field(default_factory=list)
    question_count: int = 0
    pending_question_id: str | None = None
    presentation_id: str | None = None
    approval: Approval | None = None
    base_blob: str | None = None
    commit_id: str | None = None
    push_status: str = 'not_started'
    delivery: str = 'none'
    presentation_kind: str | None = None
    used_ids: list[str] = field(default_factory=list)
    answers: list[dict] = field(default_factory=list)
    questions: list[dict] = field(default_factory=list)
    collection_info: dict = field(default_factory=dict)
    late_source_ids: list[str] = field(default_factory=list)


@dataclass
class SaveResult:
    status: str
    commit_id: str | None = None
    reason_code: str | None = None


def jst_window(day: date) -> tuple[datetime, datetime]:
    start = datetime.combine(day, datetime.min.time(), JST)
    return start.astimezone(timezone.utc), (start + timedelta(days=1)).astimezone(timezone.utc)


def canonical_body(text: str) -> bytes:
    return (text.replace('\r\n', '\n').replace('\r', '\n').rstrip('\n') + '\n').encode('utf-8')


def body_hash(body: bytes) -> str:
    return hashlib.sha256(body).hexdigest()


def safe_id(value: str) -> str:
    if not isinstance(value, str) or not re.fullmatch(r'[a-zA-Z0-9_.:-]{1,180}', value):
        raise DiaryError('invalid_id')
    return value


def safe_path(root: Path, relative: str) -> Path:
    root = Path(root)
    if not root.is_absolute() or root.name != '.daily-diary-runtime' or root.resolve() != root:
        raise DiaryError('invalid_runtime_root')
    rel = Path(relative)
    if rel.is_absolute() or '..' in rel.parts:
        raise DiaryError('path_outside_runtime')
    root.mkdir(mode=0o700, parents=True, exist_ok=True)
    if root.stat().st_uid != os.getuid():
        raise DiaryError('runtime_owner')
    os.chmod(root, 0o700)
    p = root
    for part in rel.parts:
        p = p / part
        if p.is_symlink():
            raise DiaryError('symlink_path')
    return p


def atomic_write(root: Path, relative: str, data: bytes) -> None:
    p = safe_path(root, relative)
    p.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    fd, name = tempfile.mkstemp(prefix='.write-', dir=p.parent)
    try:
        with os.fdopen(fd, 'wb') as f:
            f.write(data)
            f.flush()
            os.fsync(f.fileno())
        safe_path(root, relative)
        os.replace(name, p)
        dirfd = os.open(p.parent, os.O_RDONLY)
        try:
            os.fsync(dirfd)
        finally:
            os.close(dirfd)
    finally:
        if os.path.exists(name):
            os.unlink(name)


def validate_state(state: DayState) -> None:
    if (type(state.day) is not date or type(state.revision) is not int or
            type(state.question_count) is not int or not isinstance(state.status, str) or
            not isinstance(state.body_sha256, str) or
            state.delivery not in ('none', 'intent', 'unknown', 'confirmed') or
            state.push_status not in ('not_started', 'unknown', 'confirmed') or
            state.presentation_kind not in (None, 'question', 'review')):
        raise DiaryError('invalid_state')
    for name in ('source_ids', 'late_source_ids', 'used_ids', 'questions', 'answers'):
        if not isinstance(getattr(state, name), list):
            raise DiaryError('invalid_state')
    if not isinstance(state.collection_info, dict):
        raise DiaryError('invalid_state')
    for name in ('pending_question_id', 'presentation_id'):
        if getattr(state, name) is not None:
            safe_id(getattr(state, name))
    for name in ('base_blob', 'commit_id'):
        value = getattr(state, name)
        if value is not None and (not isinstance(value, str) or not re.fullmatch('[0-9a-f]{40}|[0-9a-f]{64}', value)):
            raise DiaryError('invalid_state')
    for entries, keys in ((state.questions, {'id', 'text'}),
                          (state.answers, {'id', 'text', 'question_id'})):
        for entry in entries:
            if not isinstance(entry, dict) or set(entry) != keys or not isinstance(entry['text'], str):
                raise DiaryError('invalid_state')
            safe_id(entry['id'])
            if entry.get('question_id') is not None:
                safe_id(entry['question_id'])
    if len(state.questions) > state.question_count or len(set(state.used_ids)) != len(state.used_ids):
        raise DiaryError('invalid_state')
    if state.status in ('保存待ち', '保存済み') and state.approval is None:
        raise DiaryError('invalid_state')
    if state.status not in STATUSES or not 0 <= state.question_count <= 2 or state.revision < 1:
        raise DiaryError('invalid_state')
    if state.body_sha256 and not re.fullmatch('[0-9a-f]{64}', state.body_sha256):
        raise DiaryError('invalid_state')
    for value in state.used_ids + state.source_ids + state.late_source_ids:
        safe_id(value)
    if state.approval:
        a = state.approval
        if (not isinstance(a, Approval) or type(a.day) is not date or type(a.revision) is not int or
                not isinstance(a.approved_at, datetime) or a.approved_at.tzinfo is None):
            raise DiaryError('invalid_state')
        safe_id(a.reply_id)
        if (a.day, a.revision, a.body_sha256) != (state.day, state.revision, state.body_sha256):
            raise DiaryError('stale_approval')


def write_state(root: Path, state: DayState) -> None:
    validate_state(state)
    data = json.dumps(asdict(state), ensure_ascii=False, default=lambda x: x.isoformat()).encode()
    atomic_write(root, f'state/{state.day.isoformat()}.json', data)


def load_state(root: Path, day: date) -> DayState | None:
    p = safe_path(root, f'state/{day.isoformat()}.json')
    if not p.exists():
        return None
    try:
        raw = json.loads(p.read_bytes())
        if not isinstance(raw, dict) or set(raw) != set(DayState.__dataclass_fields__):
            raise DiaryError('corrupt_state')
        raw['day'] = date.fromisoformat(raw['day'])
        if raw['approval'] is not None:
            a = raw['approval']
            if not isinstance(a, dict) or set(a) != set(Approval.__dataclass_fields__):
                raise DiaryError('corrupt_state')
            a['day'] = date.fromisoformat(a['day'])
            a['approved_at'] = datetime.fromisoformat(a['approved_at'])
            raw['approval'] = Approval(**a)
        state = DayState(**raw)
        if state.day != day:
            raise DiaryError('invalid_state')
        validate_state(state)
        return state
    except (ValueError, TypeError, KeyError, AttributeError):
        raise DiaryError('corrupt_state') from None


@contextmanager
def day_lock(root: Path, day: date):
    p = safe_path(root, f'locks/{day.isoformat()}.lock')
    p.parent.mkdir(mode=0o700, exist_ok=True)
    try:
        fd = os.open(p, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    except FileExistsError:
        raise DiaryError('day_locked') from None
    try:
        with os.fdopen(fd, 'w') as f:
            f.write(str(os.getpid()))
        yield
    finally:
        p.unlink()


def approve(state: DayState, body: bytes, reply_id: str, now: datetime) -> DayState:
    safe_id(reply_id)
    if (state.status != '確認待ち' or not state.presentation_id or
            state.delivery != 'confirmed' or state.body_sha256 != body_hash(body)):
        raise DiaryError('review_not_confirmed')
    if now.tzinfo is None:
        raise DiaryError('naive_time')
    state.approval = Approval(state.day, state.revision, state.body_sha256, reply_id, now)
    state.status = '保存待ち'
    return state
