"""Prove completion when CLI retains a historical or recovered API error.

Never reinterpret a genuine current-turn error from a response alone. This
fallback requires a fully finished stream and matching, read-only official
steps; an unknown schema or unavailable evidence leaves ERROR unchanged.
"""
from pathlib import Path
import hashlib
import json
import math
import re
import sqlite3

from activity_monitor import read_rows, proto_fields, visible_step


def artifact_write_proof(payload, tool, cli_root, sid, response, time_window):
    """Known write_file schema + exact on-disk content, never re-execute a tool.

    Only a completed Markdown artifact inside this official session is supported.
    Unknown tool results, arbitrary project writes and changed files stay unknown.
    """
    try:
        if time_window is None or len(time_window) != 2:
            return None
        start, end = time_window
        if not all(type(t) in (int, float) and math.isfinite(t) for t in (start, end)) or end < start:
            return None
        fields = proto_fields(payload)
        if fields.get(1) != 132 or fields.get(4) != 3:
            return None
        call = proto_fields(proto_fields(fields[5])[4])
        parameters = json.loads(call[3])
        if not isinstance(parameters, dict) or call[2] != b'write_file' or parameters.get('Path') != tool.get('path'):
            return None
        content = parameters.get('Content')
        if not isinstance(content, str) or not 0 < len(content.encode('utf-8')) <= 1000000:
            return None
        target = Path(parameters['Path'])
        base = cli_root / 'brain' / sid
        # No globbing, arbitrary paths, symlink traversals or hidden CLI metadata.
        relative = target.relative_to(base)
        if (not target.is_absolute() or target.suffix.lower() != '.md' or not relative.parts
                or any(part.startswith('.') for part in relative.parts)
                or str(target) not in response):
            return None
        if not target.resolve().is_relative_to(base.resolve()):
            return None
        parents = [cli_root / 'brain', base, target]
        path = target.parent
        while path != base:
            parents.append(path)
            path = path.parent
        for path in parents:
            if path.is_symlink():
                return None
        if not target.is_file():
            return None
        info = target.stat()
        if not 0 < info.st_size <= 1000000 or not start - 1 <= info.st_mtime <= end + 3:
            return None
        raw = target.read_bytes()
        if raw != content.encode('utf-8'):
            return None
        return {'tool': 'write_file', 'artifact': relative.as_posix(),
                'sha256': hashlib.sha256(raw).hexdigest(), 'bytes': len(raw)}
    except (OSError, ValueError, KeyError, TypeError, UnicodeError):
        return None


class TurnEvidence:
    def __init__(self):
        self.steps = {}
        self.usage_steps = {}
        self.invalid = False
        self.text_chars = 0
        self.event_number = 0

    def observe(self, event):
        if self.invalid or event.get('event') != 'step_update':
            return
        step = event.get('step_update') or {}
        self.event_number += 1
        index = step.get('step_index')
        if type(index) is not int or index < 0:
            self.invalid = True
            return
        if index not in self.steps and len(self.steps) >= 256:
            self.invalid = True
            return
        previous = self.steps.setdefault(index, {'text': ''})
        for key in ('step_type', 'state', 'conversation_id'):
            if key in step:
                if key != 'state' and key in previous and previous[key] != step[key]:
                    self.invalid = True
                previous[key] = step[key]
        if step.get('state') == 'DONE':
            previous['done_event'] = self.event_number
        if step.get('step_type') == 'tool':
            info = step.get('tool_info') or {}
            params = info.get('parameters') or {}
            previous['tool'] = {'name': step.get('tool_name'), 'reported_name': info.get('name'),
                                'path': params.get('Path'), 'output': str(info.get('output') or '')[:200],
                                'failed': bool((previous.get('tool') or {}).get('failed') or info.get('error')
                                               or info.get('denied') or info.get('success') is False)}
        if isinstance(step.get('text_delta'), str):
            self.text_chars += len(step['text_delta'])
            if self.text_chars > 1000000:
                self.invalid = True
                for saved in self.steps.values():
                    saved['text'] = ''
            else:
                previous['text'] += step['text_delta']
        usage = step.get('usage')
        if step.get('state') == 'DONE' and isinstance(usage, dict):
            counts = {key: value for key, value in usage.items()
                      if key in {'input_tokens', 'output_tokens', 'thinking_tokens', 'cache_read_tokens', 'total_tokens'}
                      and type(value) in (int, float) and math.isfinite(value) and value >= 0}
            if 'total_tokens' in counts:
                self.usage_steps[index] = counts

    def current_usage(self):
        if self.invalid or not self.usage_steps:
            return None
        totals = {}
        for usage in self.usage_steps.values():
            for key, value in usage.items():
                totals[key] = totals.get(key, 0) + value
        return totals

    def compaction_checkpoint(self, sid):
        if self.invalid or not sid:
            return None
        users = [i for i, step in self.steps.items() if step.get('step_type') == 'user_input']
        checkpoints = [i for i, step in self.steps.items() if step.get('step_type') == 'checkpoint'
                       and step.get('state') == 'DONE' and step.get('conversation_id') == sid]
        if (len(users) != 1 or not checkpoints or max(checkpoints) <= users[0]
                or any(step.get('state') != 'DONE' or step.get('conversation_id') != sid for step in self.steps.values())):
            return None
        return {'source': 'cli_checkpoint', 'conversation_id': sid, 'step_index': max(checkpoints)}

    def prove_historical_error(self, result, root=None, reader=None, time_window=None):
        sid, error = result.get('conversation_id'), result.get('error')
        if (self.invalid or result.get('status') != 'ERROR'
                or result.get('finished_reason') != 'error' or result.get('denied_actions')
                or not isinstance(sid, str)
                or not re.fullmatch(r'[a-f0-9]{8}(?:-[a-f0-9]{4}){3}-[a-f0-9]{12}', sid)
                or not isinstance(error, str) or not 0 < len(error) <= 4096
                or not isinstance(result.get('response'), str) or not result['response'].strip()):
            return None
        users = [i for i, s in self.steps.items() if s.get('step_type') == 'user_input']
        if len(users) != 1:
            return None
        first = users[0]
        allowed = {'user_input', 'system_message', 'agent_response', 'checkpoint', 'error_message', 'tool'}
        if any(i < first or s.get('state') != 'DONE' or s.get('step_type') not in allowed
               or s.get('conversation_id') != sid for i, s in self.steps.items()):
            return None
        responses = [i for i, s in self.steps.items() if s.get('step_type') == 'agent_response']
        if not responses:
            return None
        last_response = max(responses)
        if self.steps[last_response]['text'].strip() != result['response'].strip():
            return None
        cli_root = Path(root) if root is not None else Path.home() / '.gemini/antigravity-cli'
        transcript = str(cli_root / 'brain' / sid / '.system_generated/logs/transcript.jsonl')
        writes = []
        for index, step in self.steps.items():
            if step.get('step_type') != 'tool':
                continue
            tool = step.get('tool') or {}
            if (index > last_response or step.get('done_event', 0) >= self.steps[last_response].get('done_event', 0)
                    or tool.get('failed') or tool.get('name') != tool.get('reported_name')):
                return None
            if tool.get('name') == 'write_file':
                writes.append(index)
            elif (tool.get('name') != 'read_file' or tool.get('path') != transcript
                    or not re.fullmatch(r'\d+ lines,\s*\d+ bytes', tool.get('output', ''))):
                return None
        database = cli_root / 'conversations' / (sid + '.db')
        reader = reader or read_rows
        query = ('SELECT idx,step_type,status, '
                 'CASE WHEN step_type IN (132,15) THEN step_payload ELSE NULL END AS proof_payload, '
                 'CASE WHEN step_type=17 AND instr(step_payload,CAST(? AS BLOB))>0 THEN 1 ELSE 0 END AS error_matches '
                 'FROM steps WHERE (idx>=? AND idx<COALESCE('
                 '(SELECT MIN(idx) FROM steps WHERE idx>? AND step_type=14),9223372036854775807)) OR '
                 '(idx<? AND step_type=17 AND instr(step_payload,CAST(? AS BLOB))>0) '
                 'ORDER BY idx LIMIT 257')
        try:
            files = (database, Path(str(database) + '-wal'))
            if sum(path.stat().st_size for path in files if path.exists()) > 64 * 1024 * 1024:
                return None
            rows = reader(database, query, (error, first, first, first, error))
            if not rows or len(rows) >= 257:
                return None
            current = [r for r in rows if r['idx'] >= first]
            older = [r for r in rows if r['idx'] < first and r['step_type'] == 17 and r['status'] == 3]
            expected = {14:'user_input',15:'agent_response',101:'system_message',23:'checkpoint',17:'error_message',132:'tool'}
            if ({r['idx'] for r in current} != set(self.steps)
                    or any(r['status'] != 3 or expected.get(r['step_type']) != self.steps[r['idx']].get('step_type') for r in current)
                    or [r['idx'] for r in current if r['step_type'] == 14] != [first]
                    or not any(r['idx'] == last_response and r['step_type'] == 15 for r in current)):
                return None
            recovered = [r for r in current if r['step_type'] == 17]
            if recovered:
                transient = bool(re.search(r'API error.*(?:UNAVAILABLE|\b50[234]\b|No capacity|i/o timeout|connection (?:reset|timeout)|deadline exceeded)', error, re.I))
                if not transient or any(not r['error_matches'] or r['idx'] > last_response
                        or self.steps[r['idx']].get('done_event', 0) >= self.steps[last_response].get('done_event', 0)
                        for r in recovered):
                    return None
            elif not older:
                return None
            artifact_proofs = []
            if writes:
                # This extension handles only historical pollution. A real error
                # inside a work-bearing turn is never silently cleared or replayed.
                if recovered or not current or current[-1]['idx'] != last_response:
                    return None
                final = visible_step(dict(idx=last_response, step_type=15, status=3,
                                          step_payload=current[-1]['proof_payload']))
                if not final or final['truncated'] or final['text'].strip() != result['response'].strip():
                    return None
                by_index = {row['idx']: row for row in current}
                for index in writes:
                    proof = artifact_write_proof(by_index[index]['proof_payload'], self.steps[index]['tool'],
                                                 cli_root, sid, result['response'], time_window)
                    if proof is None:
                        return None
                    artifact_proofs.append(dict(proof, step_index=index))
        except (OSError, sqlite3.Error, ValueError, KeyError, TypeError):
            return None
        confirmation = {'source': 'official_recovered_api_error' if recovered else 'official_current_turn',
                        'user_step': first, 'response_step': last_response,
                        'raw_cli_status': result['status'], 'raw_cli_error': error}
        if older:
            confirmation['historical_error_step'] = max(r['idx'] for r in older)
        if recovered:
            confirmation['recovered_error_steps'] = [r['idx'] for r in recovered]
        if artifact_proofs:
            confirmation['artifacts'] = artifact_proofs
        return confirmation
