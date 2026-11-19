"""Reproduce a historical Claude quota error after a completed Flash artifact."""
import copy
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import time
import unittest

from headless_compat import TurnEvidence
import test_headless_compat as history_fixtures
SID = history_fixtures.SID

ERROR = 'Individual quota reached. Resets in 69h48m37s.'


def varint(value):
    result = bytearray()
    while value > 127:
        result.append((value & 127) | 128)
        value >>= 7
    result.append(value)
    return bytes(result)


def proto(*fields):
    return b''.join(varint(key << 3 | (0 if type(value) is int else 2)) +
                    (varint(value) if type(value) is int else varint(len(value)) + value)
                    for key, value in fields)


class HistoricalQuotaArtifactTests(unittest.TestCase):
    setUp = history_fixtures.CurrentTurnTests.setUp
    tearDown = history_fixtures.CurrentTurnTests.tearDown
    def artifact_case(self):
        self.target = self.root / 'brain' / SID / 'plan.md'
        self.target.parent.mkdir(parents=True, exist_ok=True)
        self.content = '# Verified plan\nAlready written; do not replay.\n'
        self.target.write_text(self.content, encoding='utf-8')
        self.response = 'Plan saved: ' + str(self.target)
        self.params = {'Path': str(self.target), 'Content': self.content, 'Overwrite': True}
        self.wire = []
        for idx, kind in ((41, 'user_input'), (42, 'system_message'), (43, 'tool'), (44, 'agent_response')):
            step = dict(conversation_id=SID, step_index=idx, step_type=kind, state='DONE')
            if kind == 'tool':
                step.update(tool_name='write_file', tool_info={'name': 'write_file', 'parameters': {'Path': str(self.target)}})
            if kind == 'agent_response':
                step['text_delta'] = self.response
            self.wire.append({'event': 'step_update', 'step_update': step})
        self.result.update(error=ERROR, response=self.response)
        self.connection.execute('DELETE FROM steps')
        self.connection.executemany('INSERT INTO steps VALUES (?,?,?,?)', [
            (40, 17, 3, ERROR.encode()), (41, 14, 3, b'work'), (42, 101, 3, b''),
            (43, 132, 3, self.tool_payload()), (44, 15, 3, self.reply_payload(self.response))])
        self.connection.commit()
        self.window = (time.time() - 5, time.time() + 5)

    def tool_payload(self):
        call = proto((2, b'write_file'), (3, json.dumps(self.params).encode()))
        return proto((1, 132), (4, 3), (5, proto((4, call))))

    @staticmethod
    def reply_payload(text):
        return proto((1, 15), (20, proto((1, text.encode()))))

    def artifact_proof(self, wire=None):
        evidence = TurnEvidence()
        for event in self.wire if wire is None else wire:
            evidence.observe(event)
        return evidence.prove_historical_error(self.result, self.root, time_window=self.window)

    def test_artifact_content_and_final_reply_prove_only_old_quota_error(self):
        self.artifact_case()
        proof = self.artifact_proof()
        self.assertEqual(proof['source'], 'official_current_turn')
        self.assertEqual(proof['historical_error_step'], 40)
        self.assertEqual(proof['artifacts'][0]['artifact'], 'plan.md')
        self.assertEqual(proof['artifacts'][0]['bytes'], len(self.content.encode()))
        # Merely a stale diagnostic: it does not authorize a new model call.
        self.assertEqual(self.target.read_text(), self.content)

    def test_missing_changed_old_symlink_or_unreferenced_artifact_stays_unknown(self):
        self.artifact_case()
        self.target.unlink(); self.assertIsNone(self.artifact_proof())
        self.target.write_text('changed'); self.assertIsNone(self.artifact_proof())
        self.target.write_text(self.content)
        os.utime(self.target, (1, 1)); self.assertIsNone(self.artifact_proof())
        self.target.unlink()
        other = self.root / 'other.md'; other.write_text(self.content)
        self.target.symlink_to(other); self.assertIsNone(self.artifact_proof())
        self.target.unlink(); self.target.write_text(self.content)
        self.result['response'] = 'Artifact not referenced'
        self.wire[-1]['step_update']['text_delta'] = self.result['response']
        self.connection.execute('UPDATE steps SET step_payload=? WHERE idx=44', (self.reply_payload(self.result['response']),))
        self.connection.commit(); self.assertIsNone(self.artifact_proof())

    def test_real_current_quota_error_and_failed_or_unfinished_tools_remain_failed(self):
        self.artifact_case()
        self.connection.execute('INSERT INTO steps VALUES (45,17,3,?)', (ERROR.encode(),))
        self.connection.commit(); self.assertIsNone(self.artifact_proof())
        self.connection.execute('DELETE FROM steps WHERE idx=45'); self.connection.commit()
        for change in ({'state': 'ACTIVE'}, {'tool_name': 'run_command'}, {'tool_info': {'name': 'write_file', 'error': 'denied'}}):
            wire = copy.deepcopy(self.wire); wire[2]['step_update'].update(change)
            self.assertIsNone(self.artifact_proof(wire))
        wire = copy.deepcopy(self.wire)
        bad = copy.deepcopy(wire[2]); bad['step_update']['tool_info']['success'] = False
        wire.insert(2, bad)  # A later DONE update cannot erase an earlier failure.
        self.assertIsNone(self.artifact_proof(wire))

    def test_unknown_tool_schema_or_mismatched_db_reply_never_clears_error(self):
        self.artifact_case()
        for payload in (b'', proto((1, 132), (4, 3)), self.tool_payload().replace(b'write_file', b'read_file!')):
            self.connection.execute('UPDATE steps SET step_payload=? WHERE idx=43', (payload,)); self.connection.commit()
            self.assertIsNone(self.artifact_proof())
        self.connection.execute('UPDATE steps SET step_payload=? WHERE idx=43', (self.tool_payload(),))
        self.connection.execute('UPDATE steps SET step_payload=? WHERE idx=44', (self.reply_payload('different'),))
        self.connection.commit(); self.assertIsNone(self.artifact_proof())

    def test_quota_backfill_keeps_raw_logs_and_repairs_attempt_without_calling_model(self):
        self.artifact_case()
        from server import Console
        from types import SimpleNamespace
        data = self.root / 'backfill'; folder = data / 'runs' / ('b' * 32); folder.mkdir(parents=True)
        start, end = self.window
        stamp = lambda value: datetime.fromtimestamp(value, timezone.utc).isoformat()
        turn = dict(id='b' * 32, status='failed', exit_code=0, kind='text', response=self.response, error=ERROR,
                    agy_id=SID, started_at=stamp(start), ended_at=stamp(end), created_at=stamp(start),
                    fallback_notice='not confirmed', attempts=[dict(status='failed', error=ERROR)])
        raw = '\n'.join(json.dumps(x) for x in self.wire + [{'event': 'result', 'result': self.result}])
        (folder / 'stdout.ndjson').write_text(raw); (folder / 'stderr.log').write_text('')
        (folder / 'result.json').write_text(json.dumps(turn))
        original_result = (folder / 'result.json').read_bytes()
        app = Console.__new__(Console); app.data = data; app.file = data / 'state.json'
        app.activity = SimpleNamespace(root=self.root)
        app.state = {'conversations': [{'id': 'c' * 32, 'agy_id': SID, 'turns': [turn]}]}
        app.file.write_text(json.dumps(app.state))
        self.assertEqual(app.reconcile_recent_results(), 1)
        self.assertEqual(turn['status'], 'completed'); self.assertEqual(turn['error'], '')
        self.assertEqual(turn['attempts'][0]['status'], 'completed')
        self.assertEqual(turn['attempts'][0]['error_before_recheck'], ERROR)
        self.assertNotIn('fallback_notice', turn)
        self.assertEqual((folder / 'result.json').read_bytes(), original_result)
        self.assertEqual((folder / 'stdout.ndjson').read_text(), raw)
        self.assertEqual(app.reconcile_recent_results(), 0)

    def denial_case(self):
        self.result.update(error=ERROR,response='',denied_actions=[{'action':'read_file'}])
        self.wire=[]
        for idx,kind in ((41,'user_input'),(42,'system_message'),(43,'checkpoint'),(44,'agent_response'),(45,'tool')):
            step=dict(conversation_id=SID,step_index=idx,step_type=kind,state='ERROR' if kind=='tool' else 'DONE')
            if kind=='tool':
                step.update(tool_name='read_file',tool_info={'name':'read_file','error':{'message':'permission denied'}})
            self.wire.append({'event':'step_update','step_update':step})
        tool=proto((1,132),(4,7),(5,proto((4,proto((2,b'read_file'))))))
        self.connection.execute('DELETE FROM steps')
        self.connection.executemany('INSERT INTO steps VALUES (?,?,?,?)',[
            (40,17,3,ERROR.encode()),(41,14,3,b'/plan work'),(42,101,3,b'system'),
            (43,23,3,b'checkpoint'),(44,15,3,self.reply_payload('')),(45,132,7,tool)])
        self.connection.commit()

    def denied_proof(self):
        evidence=TurnEvidence()
        for event in self.wire:evidence.observe(event)
        return evidence.prove_denied_historical_error(self.result,self.root)

    def test_permission_denial_explains_old_quota_without_success_or_replay(self):
        self.denial_case()
        proof=self.denied_proof()
        self.assertEqual(proof['source'],'official_tool_denial')
        self.assertEqual(proof['historical_error_step'],40)
        self.assertEqual(proof['denied_tools'],['read_file'])
        evidence=TurnEvidence()
        for event in self.wire:evidence.observe(event)
        self.assertIsNone(evidence.prove_historical_error(self.result,self.root))
        self.connection.execute('INSERT INTO steps VALUES (46,17,3,?)',(ERROR.encode(),))
        self.connection.commit()
        self.assertIsNone(self.denied_proof(),'a genuine current quota error is never cleared')

    def test_denial_explanation_requires_matching_old_error_and_full_current_steps(self):
        self.denial_case()
        self.connection.execute('DELETE FROM steps WHERE idx=40');self.connection.commit()
        self.assertIsNone(self.denied_proof())
        self.denial_case()
        self.connection.execute('UPDATE steps SET step_payload=? WHERE idx=45',(b'unknown',));self.connection.commit()
        self.assertIsNone(self.denied_proof())
        self.denial_case()
        self.wire[3]['step_update']['text_delta']='partial output'
        self.assertIsNone(self.denied_proof())

    def test_command_action_alias_explains_denial_without_authorizing_replay(self):
        from quota_failover import AttemptGuard
        self.denial_case()
        self.result['denied_actions']=[{'action':'command','display_name':'Bash'}]
        self.wire[-1]['step_update'].update(tool_name='run_command',
            tool_info={'name':'run_command','error':{'message':'permission denied'}})
        tool=proto((1,132),(4,7),(5,proto((4,proto((2,b'run_command'))))))
        self.connection.execute('UPDATE steps SET step_payload=? WHERE idx=45',(tool,))
        self.connection.commit()
        proof=self.denied_proof()
        self.assertEqual(proof['source'],'official_tool_denial')
        self.assertEqual(proof['denied_tools'],['run_command'])
        guard=AttemptGuard()
        for event in [{'event':'init','conversation_id':SID}]+self.wire+[
                {'event':'result','result':self.result}]:guard.observe(event)
        turn={'expected_agy_id':SID,'prompt':'work'}
        self.assertFalse(guard.current_quota_rejection(turn,self.root))
        self.assertFalse(guard.official_rejection(turn,self.root))
        self.assertFalse(guard.permits_retry(turn,3,False))

    def test_denial_backfill_preserves_failure_and_raw_files(self):
        self.denial_case()
        from server import Console
        from types import SimpleNamespace
        data=self.root/'backfill-denial';folder=data/'runs'/('d'*32);folder.mkdir(parents=True)
        turn=dict(id='d'*32,status='failed',exit_code=3,kind='text',response='',error=ERROR,agy_id=SID,
                  created_at='2026-11-16T08:00:00Z',fallback_notice='not confirmed',attempts=[dict(status='failed',error=ERROR)])
        raw='\n'.join(json.dumps(e) for e in self.wire+[{'event':'result','result':self.result}])
        (folder/'stdout.ndjson').write_text(raw);(folder/'stderr.log').write_text(ERROR)
        app=Console.__new__(Console);app.data=data;app.file=data/'state.json';app.activity=SimpleNamespace(root=self.root)
        app.state={'conversations':[{'id':'c'*32,'agy_id':SID,'turns':[turn]}]};app.file.write_text(json.dumps(app.state))
        self.assertEqual(app.reconcile_recent_results(),1)
        self.assertEqual(turn['status'],'failed')
        self.assertIn('工具权限被拒绝',turn['error'])
        self.assertEqual(turn['failure_evidence']['raw_cli_error'],ERROR)
        self.assertEqual(turn['attempts'][0]['status'],'failed')
        self.assertEqual((folder/'stdout.ndjson').read_text(),raw)
        self.assertEqual(app.reconcile_recent_results(),0)

    def test_worker_finishes_artifact_once_without_blocking_or_switching_healthy_model(self):
        self.artifact_case()
        from server import Console, BUSY, DEFAULT_MODEL
        binary = self.root / 'fake-agy'
        wire = [{'event': 'init', 'conversation_id': SID}] + self.wire + [{'event': 'result', 'result': self.result}]
        binary.write_text('#!/usr/bin/python3\nimport json\nfrom pathlib import Path\n'
                         'Path(' + repr(str(self.target)) + ').write_text(' + repr(self.content) + ')\n'
                         'for event in ' + repr(wire) + ': print(json.dumps(event),flush=True)\n')
        binary.chmod(0o700)
        account = {'state': 'known', 'email': 'test@example.test', 'quota': {}}
        app = Console(self.root / 'worker', binary, import_history=False,
                      account_reader=lambda *a, **kw: copy.deepcopy(account))
        try:
            app.activity.root = self.root
            app.health['models'] = [{'id': DEFAULT_MODEL, 'label': DEFAULT_MODEL}]
            conv = app.create()
            turn = app.submit(conv['id'], 'work', '')
            deadline = time.monotonic() + 5
            while time.monotonic() < deadline:
                # Like API readers, observe terminal state only after the worker
                # releases its lock with attempts and persistence finalized.
                with app.lock:
                    if turn['status'] not in BUSY:
                        break
                time.sleep(.01)
            with app.lock:
                self.assertEqual(turn['status'], 'completed', turn.get('error'))
                self.assertTrue(turn['fallback_enabled'])
                self.assertEqual(len(turn['attempts']), 1)
                self.assertEqual(turn.get('model_changes', []), [])
                self.assertEqual(app.blocked_models(), [])
                self.assertEqual(turn['completion_evidence']['artifacts'][0]['artifact'], 'plan.md')
        finally:
            app.close()


if __name__ == '__main__':
    unittest.main()
