"""Bounded settings and conservative evidence for quota-only recovery."""
import re
import sqlite3
from pathlib import Path
from activity_monitor import read_rows, visible_step


def clean_order(value):
    if (not isinstance(value, list) or len(value) > 128
            or any(not isinstance(x, str) or not re.fullmatch(r'[A-Za-z0-9_.-]{1,100}', x) for x in value)):
        raise ValueError('模型顺序需为最多128个有效模型。')
    return list(dict.fromkeys(value))


def clean_fallback(value):
    if not isinstance(value, dict) or type(value.get('enabled')) is not bool:
        raise ValueError('请设置自动候补开关。')
    result = {'enabled': value['enabled']}
    if 'models' in value:
        result['models'] = clean_order(value['models'])
    return result


def quota_rejection(error):
    # A generic 429/RESOURCE_EXHAUSTED can mean request rate or concurrency.
    # Only explicit quota depletion is eligible; transport failures never are.
    return isinstance(error, str) and bool(re.search(
        r'\bIndividual quota reached\b|\bquota (?:has been )?(?:exhausted|exceeded|depleted)\b|'
        r'\b(?:insufficient|exhausted) (?:model )?quota\b', error, re.I)) and not bool(re.search(
        r'timeout|timed out|503|connection|context deadline|historical', error, re.I))


class AttemptGuard:
    """Fail closed on missing, malformed, unknown or work-bearing stream data."""
    def __init__(self):
        self.init = False
        self.result = None
        self.unsafe = False
        self.user_input = False
        self.session = None
        self.user_index = None

    def observe(self, event):
        if not isinstance(event, dict):
            self.unsafe = True
            return
        kind = event.get('event')
        if self.result is not None:
            self.unsafe = True
        if kind == 'init':
            if self.init:
                self.unsafe = True
            self.init = True
            self.session = event.get('conversation_id') or (event.get('init') or {}).get('conversation_id')
        elif kind == 'step_update':
            step = event.get('step_update') or {}
            typ = step.get('step_type')
            if type(step.get('step_index')) is not int or step['step_index'] < 0:
                self.unsafe = True
            if typ == 'user_input':
                self.user_input = True
                if self.user_index is not None and self.user_index != step.get('step_index'):
                    self.unsafe = True
                self.user_index = step.get('step_index')
            elif typ not in ('agent_response', 'system_message', 'error_message'):
                self.unsafe = True
            if step.get('tool_name') or step.get('tool_info') or step.get('text_delta'):
                self.unsafe = True
        elif kind == 'result':
            self.result = event.get('result') or {}
        else:
            self.unsafe = True

    def permits_retry(self, turn, code, denied):
        result = self.result or {}
        expected = turn.get('expected_agy_id')
        return bool(self.init and self.user_input and not self.unsafe and expected
                    and self.session == expected and result.get('conversation_id') == expected
                    and code in (0, 1, 3) and result.get('status') == 'ERROR'
                    and quota_rejection(result.get('error'))
                    and not (turn.get('response') or result.get('response') or turn.get('images')
                             or denied or result.get('denied_actions') or turn.get('session_mismatch'))
                    and not turn.get('prompt', '').lstrip().startswith('/'))

    def current_quota_rejection(self, turn, root):
        """Prove this error belongs to the submitted user step, never an older turn.

        Unlike replay eligibility, this permits earlier work in the current turn:
        a genuine later quota failure still matters, but never authorizes replay.
        """
        result = self.result or {}
        sid = self.session
        if (not self.init or not self.user_input or type(self.user_index) is not int
                or not isinstance(sid, str) or not re.fullmatch(r'[a-f0-9]{8}(?:-[a-f0-9]{4}){3}-[a-f0-9]{12}', sid)
                or result.get('conversation_id') != sid or result.get('status') != 'ERROR'
                or not quota_rejection(result.get('error')) or turn.get('session_mismatch')
                or turn.get('expected_agy_id') not in (None, sid)):
            return False
        path = Path(root) / 'conversations' / (sid + '.db')
        try:
            if sum(p.stat().st_size for p in (path, Path(str(path) + '-wal')) if p.exists()) > 64 * 1024 * 1024:
                return False
            rows = read_rows(path, 'SELECT idx,step_type,status,step_payload FROM steps WHERE idx>=? AND idx<COALESCE('
                             '(SELECT MIN(idx) FROM steps WHERE idx>? AND step_type=14),9223372036854775807) ORDER BY idx LIMIT 129',
                             (self.user_index, self.user_index))
            if not rows or len(rows) >= 129:
                return False
            first = rows[0]
            if (first['idx'] != self.user_index or first['step_type'] != 14 or first['status'] != 3
                    or turn['prompt'].encode() not in bytes(first['step_payload'])):
                return False
            error = result['error'].encode()
            return any(r['step_type'] == 17 and r['status'] == 3 and error in bytes(r['step_payload']) for r in rows[1:])
        except (OSError, sqlite3.Error, ValueError, KeyError, TypeError):
            return False

    def official_rejection(self, turn, root):
        """Verify the submitted user step exists and no subsequent work occurred."""
        sid = turn.get('expected_agy_id') or ''
        if not re.fullmatch(r'[a-f0-9]{8}(?:-[a-f0-9]{4}){3}-[a-f0-9]{12}', sid):
            return False
        path = Path(root) / 'conversations' / (sid + '.db')
        try:
            if sum(p.stat().st_size for p in (path, Path(str(path) + '-wal')) if p.exists()) > 64 * 1024 * 1024:
                return False
            rows = read_rows(path, 'SELECT idx,step_type,status,step_payload FROM steps WHERE idx>=? ORDER BY idx LIMIT 33', (self.user_index,))
            if not rows or len(rows) >= 33:
                return False
            first = rows[0]
            if (first['idx'] != self.user_index or first['step_type'] != 14 or first['status'] != 3
                    or turn['prompt'].encode() not in bytes(first['step_payload'])):
                return False
            error = (self.result or {}).get('error', '').encode()
            if not any(r['step_type'] == 17 and error and error in bytes(r['step_payload']) for r in rows[1:]):
                return False
            for row in rows[1:]:
                if row['step_type'] not in (101, 17, 15) or row['status'] != 3:
                    return False
                if row['step_type'] == 15:
                    message = visible_step(row)
                    if not message or message['text'] or message['truncated']:
                        return False
            return True
        except (OSError, sqlite3.Error, ValueError, KeyError, TypeError):
            return False
