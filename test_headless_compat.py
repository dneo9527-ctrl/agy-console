"""A stale historical error must never mask a real current-turn failure."""
import copy
import json
from pathlib import Path
import sqlite3
import tempfile
import time
import unittest
from types import SimpleNamespace

from headless_compat import TurnEvidence
from server import BUSY, Console, DEFAULT_MODEL


SID = '11111111-2222-4333-8444-555555555555'
ERROR = 'API error: historical connection timeout'
RESPONSE = '本轮中文回复已完成。\n'


def events():
    return [
        {'event': 'step_update', 'step_update': dict(conversation_id=SID, step_index=41,
            state='DONE', step_type='user_input')},
        {'event': 'step_update', 'step_update': dict(conversation_id=SID, step_index=42,
            state='DONE', step_type='system_message')},
        {'event': 'step_update', 'step_update': dict(conversation_id=SID, step_index=43,
            state='ACTIVE', step_type='agent_response', text_delta=RESPONSE[:-1])},
        {'event': 'step_update', 'step_update': dict(conversation_id=SID, step_index=43,
            state='DONE', step_type='agent_response', text_delta='\n')},
    ]


class CurrentTurnTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        (self.root / 'conversations').mkdir()
        self.database = self.root / 'conversations' / (SID + '.db')
        self.connection = sqlite3.connect(self.database)
        self.connection.execute('CREATE TABLE steps (idx INTEGER,step_type INTEGER,status INTEGER,step_payload BLOB)')
        self.connection.executemany('INSERT INTO steps VALUES (?,?,?,?)', [
            (40, 17, 3, b'\x01' + ERROR.encode()), (41, 14, 3, b''),
            (42, 101, 3, b''), (43, 15, 3, b'')])
        self.connection.commit()
        self.result = dict(conversation_id=SID, status='ERROR', error=ERROR,
                           finished_reason='error', response=RESPONSE)

    def tearDown(self):
        self.connection.close()
        self.temp.cleanup()

    def stream(self, source=None):
        evidence = TurnEvidence()
        for event in events() if source is None else source:
            evidence.observe(event)
        return evidence

    def proof(self, result=None, source=None):
        return self.stream(source).prove_historical_error(self.result if result is None else result, self.root)

    def test_completed_current_reply_proven_without_changing_official_database(self):
        before = self.database.read_bytes()
        proof = self.proof()
        self.assertEqual(proof['historical_error_step'], 40)
        self.assertEqual(proof['user_step'], 41)
        self.assertEqual(proof['response_step'], 43)
        self.assertEqual(proof['raw_cli_status'], 'ERROR')
        self.assertEqual(before, self.database.read_bytes())

    def test_current_error_even_with_same_message_is_not_completed(self):
        self.connection.execute('INSERT INTO steps VALUES (44,17,3,?)', (ERROR.encode(),))
        self.connection.commit()
        self.assertIsNone(self.proof())

    def test_unknown_unfinished_and_other_users_are_not_completed(self):
        for kind, status in ((101, 1), (999, 3), (14, 3)):
            with self.subTest(kind=kind, status=status):
                self.connection.execute('UPDATE steps SET step_type=?,status=? WHERE idx=42', (kind, status))
                self.connection.commit()
                self.assertIsNone(self.proof())

    def test_exact_prior_error_required(self):
        self.assertIsNone(self.proof(dict(self.result, error='a different new error')))
        self.connection.execute('DELETE FROM steps WHERE idx=40')
        self.connection.commit()
        self.assertIsNone(self.proof())

    def test_partial_replayed_or_mismatched_stream_remains_failed(self):
        cases = [events()[:-1], events()[1:], []]
        wrong = copy.deepcopy(events()); wrong[2]['step_update']['conversation_id'] = 'different'
        # A final step with the right ID cannot disguise an earlier wrong ID.
        cases.append(wrong)
        for source in cases:
            with self.subTest(source=source):
                self.assertIsNone(self.proof(source=source))
        self.assertIsNone(self.proof(dict(self.result, response='unstreamed reply')))

    def test_denied_nonerror_empty_invalid_session_and_database_fail_closed(self):
        for change in ({'denied_actions': ['tool']}, {'status': 'TIMEOUT'},
                       {'response': ''}, {'finished_reason': 'timeout'}, {'conversation_id': '../bad'}):
            self.assertIsNone(self.proof(dict(self.result, **change)))
        self.connection.execute('DROP TABLE steps'); self.connection.commit()
        self.assertIsNone(self.proof())

    def test_error_or_tool_stream_and_bounded_overflow_are_not_reinterpreted(self):
        for kind in ('error', 'tool', 'checkpoint'):
            source = copy.deepcopy(events()); source[1]['step_update']['step_type'] = kind
            self.assertIsNone(self.proof(source=source))
        stream = self.stream()
        for index in range(100, 400):
            stream.observe({'event': 'step_update', 'step_update': dict(step_index=index,
                state='DONE', step_type='system_message', conversation_id=SID)})
        self.assertLessEqual(len(stream.steps), 256)
        self.assertIsNone(stream.prove_historical_error(self.result, self.root))
        stream = self.stream()
        stream.observe({'event':'step_update','step_update':dict(step_index=43,
            state='ACTIVE',step_type='agent_response',conversation_id=SID,text_delta='x'*1000001)})
        self.assertTrue(stream.invalid)
        self.assertLessEqual(sum(len(s['text']) for s in stream.steps.values()),1000000)

    def test_worker_uses_proof_and_preserves_nonzero_denial_and_current_error(self):
        binary = self.root / 'fake-agy'
        wire = [{'event':'init', 'conversation_id':SID}] + events() + [{'event':'result','result':self.result}]
        binary.write_text('#!/usr/bin/python3\nimport json,sys\nwire='+repr(wire)+'\n'
            'prompt=sys.argv[sys.argv.index("-p")+1]\n'
            'for event in wire: print(json.dumps(event),flush=True)\n'
            'if prompt=="denied": print("tool execution denied",file=sys.stderr)\n'
            'sys.exit(2 if prompt=="nonzero" else 0)\n')
        binary.chmod(0o700)
        app = Console(self.root / 'console', binary, import_history=False,
                      account_reader=lambda *args, **kwargs: {'state':'unknown'})
        app.activity.root = self.root
        conv = app.create()
        def run(prompt):
            turn = app.submit(conv['id'], prompt, DEFAULT_MODEL)
            deadline = time.monotonic()+5
            while time.monotonic() < deadline:
                with app.lock:
                    if turn['status'] not in BUSY:
                        return turn
                time.sleep(.01)
            self.fail('fake task did not finish')
        try:
            first = run('ok')
            self.assertEqual(first['status'], 'completed')
            self.assertEqual(first['completion_evidence']['historical_error_step'], 40)
            self.assertEqual(first['response'], RESPONSE)
            self.assertEqual(first['agy_id'], SID)
            self.assertEqual(json.loads((app.data/'runs'/first['id']/'result.json').read_text())['status'], 'completed')
            for prompt in ('nonzero', 'denied'):
                self.assertEqual(run(prompt)['status'], 'failed')
            self.connection.execute('INSERT INTO steps VALUES (44,17,3,?)', (ERROR.encode(),))
            self.connection.commit()
            self.assertEqual(run('current-error')['status'], 'failed')
        finally:
            app.close()

    def test_current_usage_deduplicates_finished_steps_and_ignores_session_aggregate(self):
        stream = self.stream()
        update = {'event':'step_update','step_update': dict(conversation_id=SID,step_index=43,
            state='DONE',step_type='agent_response',usage={'input_tokens':80,'output_tokens':20,'total_tokens':100})}
        stream.observe(update); stream.observe(update)
        self.assertEqual(stream.current_usage()['total_tokens'],100)
        stream.observe({'event':'result','result':dict(self.result,usage={'total_tokens':2000000})})
        self.assertEqual(stream.current_usage()['total_tokens'],100)
        stream.observe({'event':'step_update','step_update':dict(conversation_id=SID,step_index=44,
            state='DONE',step_type='checkpoint',usage={'total_tokens':4})})
        self.assertEqual(stream.current_usage()['total_tokens'],104)

    def test_invalid_or_unfinished_usage_is_not_presented_as_current_total(self):
        stream = self.stream()
        for usage in ({'total_tokens':float('nan')},{'total_tokens':-1},{'total_tokens':True}):
            stream.observe({'event':'step_update','step_update':dict(step_index=43,state='DONE',usage=usage)})
        self.assertIsNone(stream.current_usage())

        stream.observe({'event':'step_update','step_update':dict(step_index=43,state='ACTIVE',usage={'total_tokens':50})})
        self.assertIsNone(stream.current_usage())

    def recovered_stream(self, *, tool_path=None, error_after_response=False, error_text=None):
        error_text = error_text or 'API error (attempt 1): UNAVAILABLE (code 503): No capacity available for model'
        self.connection.execute('DELETE FROM steps')
        rows=[(41,14,3,b''),(42,101,3,b''),(43,23,3,b''),(44,15,3,b''),
              (45,132,3,b''),(46,17,3,error_text.encode()),(47,15,3,b'')]
        self.connection.executemany('INSERT INTO steps VALUES (?,?,?,?)',rows);self.connection.commit()
        stream=TurnEvidence()
        for idx,kind in [(41,'user_input'),(42,'system_message'),(43,'checkpoint'),(44,'agent_response'),(45,'tool'),(46,'error_message'),(47,'agent_response')]:
            step=dict(conversation_id=SID,step_index=idx,state='DONE',step_type=kind)
            if kind=='agent_response':step['text_delta']=RESPONSE if idx==47 else 'Preparing response'
            if kind=='tool':step.update(tool_name='read_file',tool_info={'name':'read_file','parameters':{'Path':tool_path or str(self.root/'brain'/SID/'.system_generated/logs/transcript.jsonl')},'output':'19 lines, 38348 bytes'})
            stream.observe({'event':'step_update','step_update':step})
        if error_after_response:stream.observe({'event':'step_update','step_update':dict(conversation_id=SID,step_index=46,state='DONE',step_type='error_message')})
        return stream,dict(self.result,error=error_text)

    def test_transient_api_error_before_final_reply_is_verified_after_recovery(self):
        stream,result=self.recovered_stream();proof=stream.prove_historical_error(result,self.root)
        self.assertEqual(proof['source'],'official_recovered_api_error')
        self.assertEqual(proof['recovered_error_steps'],[46])
        self.assertEqual(proof['response_step'],47)

    def test_post_reply_error_or_nontransient_error_remains_failed(self):
        stream,result=self.recovered_stream(error_after_response=True)
        self.assertIsNone(stream.prove_historical_error(result,self.root))
        stream,result=self.recovered_stream(error_text='Individual quota reached')
        self.assertIsNone(stream.prove_historical_error(result,self.root))

    def test_arbitrary_tools_or_unfinished_steps_never_use_recovery_override(self):
        stream,result=self.recovered_stream(tool_path='/some/project/credentials.json')
        self.assertIsNone(stream.prove_historical_error(result,self.root))
        stream,result=self.recovered_stream();stream.steps[45]['tool']['name']='run_command'
        self.assertIsNone(stream.prove_historical_error(result,self.root))
        stream,result=self.recovered_stream();self.connection.execute('UPDATE steps SET status=1 WHERE idx=47');self.connection.commit()
        self.assertIsNone(stream.prove_historical_error(result,self.root))

    def test_later_users_do_not_change_old_turn_proof_or_supply_its_response(self):
        self.connection.executemany('INSERT INTO steps VALUES (?,?,?,?)',[(44,14,3,b''),(45,15,3,b'')]);self.connection.commit()
        self.assertIsNotNone(self.proof())
        source=events()[:2]+[{'event':'step_update','step_update':dict(conversation_id=SID,step_index=45,state='DONE',step_type='agent_response',text_delta=RESPONSE)}]
        self.assertIsNone(self.proof(source=source))

    def saved_recovery(self, **changes):
        stream,result=self.recovered_stream()
        del stream
        turn=dict(id='a'*32,status='failed',exit_code=0,kind='text',response=RESPONSE,
                  error=result['error'],agy_id=SID,created_at='2026-11-13T00:00:00Z',
                  usage={'total_tokens':2000000})
        turn.update(changes)
        data=self.root/'console';folder=data/'runs'/turn['id'];folder.mkdir(parents=True)
        wire=[{'event':'step_update','step_update':dict(conversation_id=SID,step_index=i,
                  state='DONE',step_type=kind,**extra)} for i,kind,extra in [
            (41,'user_input',{}),(42,'system_message',{}),(43,'checkpoint',{}),
            (44,'agent_response',{'text_delta':'Preparing response'}),
            (45,'tool',{'tool_name':'read_file','tool_info':{'name':'read_file',
                 'parameters':{'Path':str(self.root/'brain'/SID/'.system_generated/logs/transcript.jsonl')},
                 'output':'19 lines, 38348 bytes'}}),
            (46,'error_message',{}),(47,'agent_response',{'text_delta':RESPONSE,
                 'usage':{'input_tokens':80,'output_tokens':20,'total_tokens':100}})]]
        (folder/'stdout.ndjson').write_text('\n'.join(json.dumps(x) for x in wire+[{'event':'result','result':result}]))
        (folder/'stderr.log').write_text('')
        (folder/'result.json').write_text(json.dumps(turn))
        app=Console.__new__(Console);app.data=data;app.file=data/'state.json'
        app.activity=SimpleNamespace(root=self.root)
        app.state={'conversations':[{'id':'c'*32,'agy_id':SID,'turns':[turn]}]}
        app.file.write_text(json.dumps(app.state))
        return app,turn,folder

    def test_saved_result_recheck_backs_up_state_and_preserves_original_run(self):
        app,turn,folder=self.saved_recovery()
        from task_progress import finish_progress
        finish_progress(turn)
        self.assertEqual(turn['progress'][-1]['state'], 'failed')
        before=app.file.read_bytes();raw=(folder/'stdout.ndjson').read_bytes()
        raw_result=(folder/'result.json').read_bytes()
        self.assertEqual(app.reconcile_recent_results(),1)
        self.assertEqual(turn['status'],'completed');self.assertEqual(turn['error'],'')
        self.assertEqual(turn['progress'][-1]['label'], '任务完成')
        self.assertEqual(turn['progress'][-1]['state'], 'done')
        self.assertEqual(turn['status_before_recheck'],'failed')
        self.assertEqual(turn['completion_evidence']['source'],'official_recovered_api_error')
        self.assertEqual(turn['usage']['total_tokens'],100)
        self.assertEqual(turn['cli_usage']['total_tokens'],2000000)
        backups=list((app.data/'backups').glob('state-before-result-recheck-*.json'))
        self.assertEqual(len(backups),1);self.assertEqual(backups[0].read_bytes(),before)
        self.assertEqual(backups[0].stat().st_mode&0o777,0o600)
        self.assertEqual((folder/'stdout.ndjson').read_bytes(),raw)
        self.assertEqual((folder/'result.json').read_bytes(),raw_result)
        self.assertEqual(app.reconcile_recent_results(),0)

    def test_saved_recheck_requires_complete_evidence_and_no_permission_denial(self):
        app,turn,folder=self.saved_recovery()
        for diagnostic in ('auto-denied','permission denied','tool command denied'):
            (folder/'stderr.log').write_text(diagnostic)
            self.assertEqual(app.reconcile_recent_results(),0)
        (folder/'stderr.log').unlink()
        self.assertEqual(app.reconcile_recent_results(),0)
        (folder/'stderr.log').write_text('')
        for key,value in [('exit_code',2),('session_mismatch',True),('kind','image'),('status','running')]:
            original=turn.get(key);turn[key]=value
            self.assertEqual(app.reconcile_recent_results(),0)
            if original is None:turn.pop(key,None)
            else:turn[key]=original
        self.connection.execute('INSERT INTO steps VALUES (48,17,3,?)',(turn['error'].encode(),));self.connection.commit()
        self.assertEqual(app.reconcile_recent_results(),0)
        self.assertEqual(turn['status'],'failed');self.assertFalse((app.data/'backups').exists())


if __name__ == '__main__':
    unittest.main(verbosity=2)
