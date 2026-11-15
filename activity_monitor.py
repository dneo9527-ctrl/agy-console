"""Read-only discovery of other official Ag CLI sessions and their visible messages.

No agents are started and no source database is opened for writing. Polling happens
only while the web page asks for it. Model reasoning/credentials are never returned.
"""
import copy
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import re
import shutil
import sqlite3
import subprocess
import tempfile
import threading
import time
from urllib.parse import unquote, urlsplit

UUID = re.compile(r'[a-f0-9]{8}(?:-[a-f0-9]{4}){3}-[a-f0-9]{12}')
MAX_MESSAGES = 160
MAX_TEXT = 100000
MAX_DETAIL_CHARS = 1000000


def request_title(text):
    """A short excerpt of the request, without CLI or delegation scaffolding."""
    text = user_text(text)
    text = re.sub(r'^\s*/(?:plan|agent|chat|fast)\b\s*', '', text, flags=re.I)
    text = re.split(r'(?im)^\s*(?:<reference_material\b|交付要求\s*[:：]|established evidence\s*:)', text, maxsplit=1)[0]
    # Explicit task/goal headings are more useful than generic reviewer roles.
    goal = re.search(r'(?im)^\s*(?:#{1,6}\s*)?(?:任务标题|标题|主题|目标|任务|goal|objective|task|topic)\s*[:：]\s*([^\n]+)', text)
    if goal:
        text = goal[1]
    else:
        lines = [line.strip() for line in text.splitlines() if line.strip()]
        text = lines[0] if lines else ''
    text = re.sub(r'^\s*(?:#{1,6}\s*|[-*]\s+)', '', text)
    text = re.sub(r'^用户希望\s*(?:Codex\s*(?:和|与)\s*Antigravity\s*一起\s*)?', '', text, flags=re.I)
    text = re.sub(r'\s+', ' ', text).strip()
    return text[:58] + ('…' if len(text) > 58 else '')


def first_transcript_request(path):
    """Read only the beginning of a log; never load whole histories for titles."""
    with path.open('r', encoding='utf-8') as stream:
        for _ in range(256):
            line = stream.readline(262144)
            if not line:
                break
            if len(line) == 262144 and not line.endswith('\n'):
                break  # No guessing from a fragment or a later request.
            try:
                event = json.loads(line)
            except ValueError:
                continue
            if event.get('type') == 'USER_INPUT' and isinstance(event.get('content'), str):
                title = request_title(event['content'])
                if title:
                    return title
    return ''


def stamp(value=None):
    return datetime.fromtimestamp(value or time.time(), timezone.utc).isoformat()


def clean_time(value):
    try:
        parsed = datetime.fromisoformat(str(value).replace('Z', '+00:00'))
        if parsed.year < 2000:
            return None
        return parsed.replace(tzinfo=parsed.tzinfo or timezone.utc).isoformat()
    except (ValueError, TypeError):
        return None


def read_rows(path, query, params=()):
    """Read live WAL via a short-lived private copy; leave CLI files untouched."""
    path = Path(path)
    if path.is_symlink() or not path.is_file():
        raise sqlite3.OperationalError('session database unavailable')
    wal = Path(str(path) + '-wal')
    if wal.is_file() and not wal.is_symlink() and wal.stat().st_size:
        with tempfile.TemporaryDirectory(prefix='agy-monitor-') as scratch:
            copied = Path(scratch) / 'snapshot.db'
            shutil.copyfile(path, copied)
            shutil.copyfile(wal, str(copied) + '-wal')
            connection = sqlite3.connect(copied.as_uri() + '?mode=ro', uri=True, timeout=.3)
            try:
                connection.row_factory = sqlite3.Row
                return [dict(row) for row in connection.execute(query, params)]
            finally:
                connection.close()
    connection = sqlite3.connect(path.resolve().as_uri() + '?mode=ro&immutable=1', uri=True, timeout=.3)
    try:
        connection.row_factory = sqlite3.Row
        return [dict(row) for row in connection.execute(query, params)]
    finally:
        connection.close()


def varint(data, cursor):
    value = shift = 0
    while cursor < len(data) and shift < 70:
        byte = data[cursor]
        cursor += 1
        value |= (byte & 127) << shift
        if byte < 128:
            return value, cursor
        shift += 7
    raise ValueError('invalid field')


def proto_fields(data):
    """Bounded wire decoder for two observed visible-message fields in CLI 1.5.x."""
    if not isinstance(data, bytes) or len(data) > 4 * 1024 * 1024:
        raise ValueError('unsupported step')
    cursor, result = 0, {}
    while cursor < len(data):
        tag, cursor = varint(data, cursor)
        field, wire = tag >> 3, tag & 7
        if field < 1:
            raise ValueError('invalid field')
        if wire == 0:
            value, cursor = varint(data, cursor)
        elif wire == 2:
            size, cursor = varint(data, cursor)
            value = data[cursor:cursor + size]
            cursor += size
        elif wire in (1, 5):
            size = 8 if wire == 1 else 4
            value, cursor = data[cursor:cursor + size], cursor + size
        else:
            raise ValueError('unsupported wire')
        if cursor > len(data):
            raise ValueError('partial field')
        result[field] = value
    return result


def visible_step(row):
    """Only USER_INPUT and PLANNER_RESPONSE text; skip thought/tool/private fields."""
    kind = row.get('step_type')
    if kind not in (14, 15):
        return None
    fields = proto_fields(row.get('step_payload'))
    if fields.get(1) != kind:
        return None
    payload = proto_fields(fields[19 if kind == 14 else 20])
    text = payload.get(2 if kind == 14 else 1)
    if kind == 15 and not isinstance(text, bytes):
        text = payload.get(8)
    if not isinstance(text, bytes):
        text = b''
    text = text.decode('utf-8', errors='strict')
    if kind == 14:
        text = user_text(text)
    created = None
    try:
        metadata = proto_fields(row.get('metadata') or fields.get(5))
        timestamp = proto_fields(metadata[1])
        seconds = timestamp[1]
        if 946684800 < seconds < 4102444800:
            created = stamp(seconds + timestamp.get(2, 0) / 1e9)
    except (ValueError, KeyError, TypeError):
        pass
    return {'id': 'step-' + str(row['idx']), 'role': 'user' if kind == 14 else 'assistant',
            'text': text[:MAX_TEXT], 'truncated': len(text) > MAX_TEXT,
            'status': 'done' if row.get('status') == 3 else 'streaming', 'created_at': created}


def user_text(text):
    request = re.search(r'<USER_REQUEST>\s*([\s\S]*?)\s*</USER_REQUEST>', text)
    return request[1] if request else text


def transcript_messages(path):
    messages = {}
    # Full transcript preserves long prompts that the short log deliberately cuts.
    with path.open('r', encoding='utf-8') as stream:
        for number, line in enumerate(stream):
            if number > 20000:
                break
            try:
                event = json.loads(line)
            except ValueError:
                continue  # Writer may be appending the last record now.
            kind = event.get('type')
            if kind not in ('USER_INPUT', 'PLANNER_RESPONSE', 'AGENT_RESPONSE'):
                continue
            content = event.get('content')
            index = event.get('step_index')
            if not isinstance(content, str) or not isinstance(index, int):
                continue
            content = user_text(content) if kind == 'USER_INPUT' else content
            messages[index] = {'id': 'step-' + str(index), 'role': 'user' if kind == 'USER_INPUT' else 'assistant',
                               'text': content[:MAX_TEXT], 'truncated': len(content) > MAX_TEXT,
                               'status': 'done' if event.get('status') == 'DONE' else 'streaming',
                               'created_at': clean_time(event.get('created_at'))}
            if len(messages) > MAX_MESSAGES:
                del messages[min(messages)]
    return [messages[k] for k in sorted(messages)]


def processes(binary, excluded):
    result = subprocess.run(['ps', '-axo', 'pid=,ppid=,tty=,etime=,comm='], capture_output=True,
                            text=True, timeout=3)
    rows, all_rows = [], {}
    for line in result.stdout.splitlines():
        parts = line.strip().split(None, 4)
        if len(parts) != 5 or not parts[0].isdigit():
            continue
        pid, parent, tty, elapsed, command = parts
        row = {'pid': int(pid), 'parent_pid': int(parent), 'terminal': None if tty == '??' else tty,
               'elapsed': elapsed, 'program': Path(command).name}
        all_rows[int(pid)] = row
        if (command == str(binary) or Path(command).name == 'agy') and int(pid) not in excluded:
            rows.append(row)
    for row in rows:
        parent = all_rows.get(row['parent_pid'], {})
        row['origin'] = parent.get('program') or '未知启动程序'
        row['window_label'] = ('终端 ' + row['terminal']) if row['terminal'] else ('CLI 进程 ' + str(row['pid']))
    return rows


def lock_owners(directory, valid_pids):
    files = [p for p in directory.glob('*.lock') if UUID.fullmatch(p.stem) and not p.is_symlink()]
    if not files or not valid_pids:
        return {}
    # -a intersects the PID selection with files, so unrelated descriptors stay private.
    result = subprocess.run(['lsof', '-nP', '-w', '-a', '-p', ','.join(map(str, valid_pids)),
                             '-F', 'pn'] + [str(p) for p in files[:512]],
                            capture_output=True, text=True, timeout=4)
    current, owners = None, {}
    for line in result.stdout.splitlines():
        if line.startswith('p') and line[1:].isdigit():
            current = int(line[1:])
        elif line.startswith('n') and current in valid_pids:
            path = Path(line[1:])
            if path.parent == directory and UUID.fullmatch(path.stem):
                owners.setdefault(path.stem, []).append(current)
    return owners


class ActivityMonitor:
    def __init__(self, root, binary, process_reader=None, owner_reader=None):
        self.root, self.binary = Path(root), str(binary)
        self.process_reader, self.owner_reader = process_reader or processes, owner_reader or lock_owners
        self.lock = threading.RLock()
        self.last_scan = float('-inf')
        self.inventory = {'sessions': [], 'unmatched_processes': [], 'checked_at': None, 'warning': ''}
        self.details = {}
        self.titles = {}

    def session_request_title(self, sid):
        cached = self.titles.get(sid)
        if cached and (cached['title'] or time.monotonic() < cached['retry_at']):
            return cached['title']
        title = ''
        try:
            rows = read_rows(self.root / 'conversations' / (sid + '.db'),
                             'SELECT idx,step_type,status,metadata,substr(step_payload,1,262144) AS step_payload '
                             'FROM steps WHERE step_type=14 ORDER BY idx ASC LIMIT 4')
            for row in rows:
                message = visible_step(row)
                if message and message['text'].strip():
                    title = request_title(message['text'])
                    break
        except (OSError, sqlite3.Error, ValueError, KeyError, UnicodeError, TypeError):
            pass
        if not title:
            logs = self.root / 'brain' / sid / '.system_generated/logs'
            for name in ('transcript_full.jsonl', 'transcript.jsonl'):
                path = logs / name
                if path.is_file() and not path.is_symlink():
                    try:
                        title = first_transcript_request(path)
                    except (OSError, ValueError, UnicodeError):
                        pass
                    if title:
                        break
        self.titles[sid] = {'title': title, 'retry_at': time.monotonic() + 10}
        while len(self.titles) > 256:
            self.titles.pop(next(iter(self.titles)))
        return title

    def scan(self, excluded=(), panel_ids=(), force=False):
        with self.lock:
            if not force and time.monotonic() - self.last_scan < 2:
                return copy.deepcopy(self.inventory)
            errors = []
            presence_known = True
            try:
                live = self.process_reader(self.binary, set(excluded))
                by_pid = {row['pid']: row for row in live}
                owners = self.owner_reader(self.root / 'presence', set(by_pid))
            except (OSError, subprocess.SubprocessError):
                live, by_pid, owners = [], {}, {}
                presence_known = False
                errors.append('活动进程检查暂不可用。')
            rows = []
            db = self.root / 'conversation_summaries.db'
            if db.exists():
                try:
                    rows = read_rows(db, 'SELECT conversation_id,title,workspace_uris,status,not_fully_idle,'
                                         'killed,last_modified_time,step_count,parent_conversation_id '
                                         'FROM conversation_summaries ORDER BY last_modified_time DESC LIMIT 200')
                except (sqlite3.Error, OSError):
                    errors.append('会话索引正在写入或格式变化，稍后自动重试。')
            indexed = {row['conversation_id'] for row in rows}
            missing_active = list(set(owners) - indexed)
            if missing_active and db.is_file():
                try:
                    found = read_rows(db, 'SELECT conversation_id,title,workspace_uris,status,not_fully_idle,'
                                         'killed,last_modified_time,step_count,parent_conversation_id '
                                         'FROM conversation_summaries WHERE conversation_id IN (' +
                                         ','.join('?' for _ in missing_active) + ')', missing_active)
                    rows = found + rows
                    indexed.update(row['conversation_id'] for row in found)
                except (sqlite3.Error, OSError):
                    pass
            for sid in set(owners) - indexed:
                rows.insert(0, {'conversation_id': sid})
            # Some sessions appear in the brain log before the summary index is saved.
            brain = self.root / 'brain'
            if brain.is_dir():
                for folder in brain.iterdir():
                    if UUID.fullmatch(folder.name) and folder.name not in indexed and not folder.is_symlink():
                        if not any(row.get('conversation_id') == folder.name for row in rows):
                            rows.append({'conversation_id': folder.name})
            sessions, mapped = [], set()
            for row in rows:
                sid = row.get('conversation_id', '')
                if not UUID.fullmatch(sid):
                    continue
                pids = owners.get(sid, [])
                mapped.update(pids)
                active = bool(row.get('not_fully_idle')) or 'RUNNING' in str(row.get('status', ''))
                waiting = any(x in str(row.get('status', '')) for x in ('WAITING', 'BLOCKED'))
                status = ('waiting' if waiting else 'running' if active else 'idle') if pids else ('unknown' if active or not presence_known else 'ended')
                workspace = ''
                try:
                    uris = json.loads(row.get('workspace_uris') or '[]')
                    if uris and isinstance(uris[0], str):
                        parsed = urlsplit(uris[0])
                        if parsed.scheme == 'file':
                            workspace = unquote(parsed.path)
                except (ValueError, TypeError):
                    pass
                changed = clean_time(row.get('last_modified_time'))
                if not changed:
                    log = brain / sid / '.system_generated/logs/transcript_full.jsonl'
                    changed = stamp(log.stat().st_mtime) if log.is_file() else None
                preview = self.session_request_title(sid)
                sessions.append({'id': sid, 'title': preview or row.get('title') or '未命名聊天 · ' + sid[:6],
                                 'request_title': preview,
                                 'workspace': workspace, 'status': status, 'active': bool(pids),
                                 'processes': [by_pid[pid] for pid in pids if pid in by_pid],
                                 'updated_at': changed, 'step_count': row.get('step_count', 0),
                                 'origin': '本面板' if sid in panel_ids else '其他窗口',
                                 'parent_id': row.get('parent_conversation_id') or None,
                                 'parent_known': 'parent_conversation_id' in row})
            sessions.sort(key=lambda item: (item['active'], item['updated_at'] or ''), reverse=True)
            self.inventory = {'sessions': sessions, 'unmatched_processes': [row for row in live if row['pid'] not in mapped],
                              'checked_at': stamp(), 'warning': ' '.join(errors), 'source': '本机官方 CLI 会话与活动锁',
                              'detail': '约每 2 秒同步。进程、会话和工作目录用于区分来源；不读取其他窗口屏幕。'}
            self.last_scan = time.monotonic()
            return copy.deepcopy(self.inventory)

    def detail(self, sid):
        if not UUID.fullmatch(sid):
            raise ValueError('无效会话')
        with self.lock:
            session = next((item for item in self.inventory['sessions'] if item['id'] == sid), None)
            if session is None:
                raise KeyError('未找到会话')
            database = self.root / 'conversations' / (sid + '.db')
            logs = self.root / 'brain' / sid / '.system_generated/logs'
            transcript = logs / 'transcript_full.jsonl'
            if not transcript.is_file():
                transcript = logs / 'transcript.jsonl'
            signature = []
            for path in (database, Path(str(database) + '-wal'), transcript):
                try:
                    info = path.stat()
                    signature.append((info.st_size, info.st_mtime_ns))
                except OSError:
                    signature.append(None)
            cached = self.details.get(sid)
            if cached and cached['signature'] == signature:
                return dict(copy.deepcopy(cached['value']), session=copy.deepcopy(session), checked_at=stamp())
            messages, note = {}, ''
            if transcript.is_file() and not transcript.is_symlink():
                try:
                    for message in transcript_messages(transcript):
                        messages[message['id']] = message
                except (OSError, ValueError):
                    note = '对话日志暂不可读，稍后自动重试。'
            if database.is_file():
                try:
                    rows = read_rows(database, 'SELECT idx,step_type,status,metadata,substr(step_payload,1,262144) AS step_payload FROM steps '
                                               'WHERE step_type IN (14,15) ORDER BY idx DESC LIMIT ?', (MAX_MESSAGES,))
                    for row in rows:
                        try:
                            message = visible_step(row)
                            if message and (message['text'] or message['id'] not in messages):
                                messages[message['id']] = message
                        except (ValueError, KeyError, UnicodeError, TypeError):
                            existing = messages.get('step-' + str(row.get('idx')))
                            if not existing or not existing.get('text'):
                                note = '部分消息尚未同步，稍后自动重试。'
                except (OSError, sqlite3.Error):
                    note = '正在同步最新内容…' if messages else '暂时无法读取聊天内容，稍后自动重试。'
            ordered = sorted(messages.values(), key=lambda item: int(item['id'].split('-')[-1]))[-MAX_MESSAGES:]
            total = 0
            bounded = []
            for message in reversed(ordered):
                available = MAX_DETAIL_CHARS - total
                if available <= 0:
                    break
                if len(message['text']) > available:
                    message = dict(message, text=message['text'][:available], truncated=True)
                bounded.append(message)
                total += len(message['text'])
            ordered = list(reversed(bounded))
            value = {'session': copy.deepcopy(session), 'messages': ordered, 'checked_at': stamp(),
                     'note': note, 'read_only': True, 'history_limit': MAX_MESSAGES}
            self.details[sid] = {'signature': signature, 'value': copy.deepcopy(value)}
            if len(self.details) > 4:
                self.details.pop(next(iter(self.details)))
            return value
