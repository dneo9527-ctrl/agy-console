"""Persistent, purpose-neutral sessions inside a fixed Codex entry."""
from datetime import datetime, timezone
import re

BRANCH = re.compile(r'[a-z][a-z0-9_-]{0,39}')


def normalize_sessions(conv):
    branches = conv.setdefault('branches', {})
    if not branches and (conv.get('turns') or conv.get('agy_id')):
        branches['main'] = {'id': 'main', 'title': '原会话', 'agy_id': conv.get('agy_id'),
                            'created_at': conv.get('created_at')}
        conv['primary_branch'] = 'main'
    if branches and conv.get('primary_branch') not in branches:
        conv['primary_branch'] = next(iter(branches))
    conv.setdefault('primary_branch', None)
    for turn in conv.get('turns', []):
        turn.setdefault('branch', conv.get('primary_branch') or 'main')
    return conv


def branch_for(conv, key=None, create=False, title=None):
    normalize_sessions(conv)
    key = key or conv.get('primary_branch') or 'main'
    if not isinstance(key, str) or not BRANCH.fullmatch(key):
        raise ValueError('会话标识需为小写字母、数字、横线或下划线，最多40字符。')
    branch = conv['branches'].get(key)
    if branch is None and create:
        if len(conv['branches']) >= 16:
            raise ValueError('一个固定入口最多保留16个工作会话。')
        if title is not None and (not isinstance(title, str) or not title.strip() or len(title.strip()) > 40):
            raise ValueError('会话名称需为1–40个字符。')
        branch = {'id': key, 'title': (title or ('当前会话' if key == 'main' else key)).strip(),
                  'agy_id': None, 'created_at': datetime.now(timezone.utc).isoformat()}
        conv['branches'][key] = branch
        if not conv.get('primary_branch'):
            conv['primary_branch'] = key
    return branch


def official_ids(conv):
    normalize_sessions(conv)
    return {branch['agy_id'] for branch in conv['branches'].values() if branch.get('agy_id')}


def session_owner(conversations, session):
    for conv in conversations:
        for key, branch in normalize_sessions(conv)['branches'].items():
            if branch.get('agy_id') == session:
                return conv, key
    return None, None


def assign_session(conversations, conv, turn, session):
    if not isinstance(session, str) or not session.strip() or len(session) > 128:
        turn['session_mismatch'] = True
        return False
    branch = branch_for(conv, turn.get('branch'), create=True)
    expected = turn.get('expected_agy_id') or branch.get('agy_id')
    owner, key = session_owner(conversations, session)
    if (expected and session != expected) or (owner is not None and (owner is not conv or key != branch['id'])):
        turn['session_mismatch'] = True
        return False
    branch['agy_id'] = session
    turn['agy_id'] = session
    if branch['id'] == conv['primary_branch']:
        conv['agy_id'] = session
    return True


def session_summaries(conv):
    normalize_sessions(conv)
    counts = {key: {'turn_count': 0, 'busy_count': 0, 'last_kind': None, 'last_turn_id': None} for key in conv['branches']}
    for turn in conv['turns']:
        value = counts.get(turn['branch'])
        if value is not None:
            value['turn_count'] += 1
            value['busy_count'] += turn.get('status') in {'queued', 'running', 'cancelling'}
            value['last_kind'] = turn.get('kind', 'text')
            value['last_turn_id'] = turn.get('id')
    return {key: dict(branch, **counts[key]) for key, branch in conv['branches'].items()}
