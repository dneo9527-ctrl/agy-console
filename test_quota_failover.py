"""Fake CLI + disposable official-layout DB: no paid calls or user records."""
import copy
import json
from pathlib import Path
import sqlite3
import time
import unittest

from model_policy import candidate_models, default_model
from quota_failover import AttemptGuard, quota_rejection
from server import Console, ConflictError
import test_codex_bridge as binding
THREAD, SESSION = binding.THREAD, binding.SESSION
from test_panel_backups import fixture_state
from panel_backups import validate_state

ARGON = 'gemini-4-argon-high'
FLASH = 'gemini-3.8-flash-high'
SONNET = 'claude-sonnet-5-5-medium'


class QuotaFailoverTests(unittest.TestCase):
    setUp = binding.BindingTests.setUp
    tearDown = binding.BindingTests.tearDown
    def wait(self, turn):
        deadline=time.monotonic()+8
        while time.monotonic()<deadline:
            with self.app.lock:
                if turn['status'] not in ('queued','running','cancelling'):
                    return
            time.sleep(.02)
        self.fail('worker did not finish')

    def prepare(self, mode='normal', missing_db=False):
        self.app.health['models'] = [{'id':m, 'label':m} for m in (ARGON, FLASH, SONNET)]
        account = {'state':'known', 'email':'fixture@example.test', 'checked_at':'2026-01-01T00:00:00Z', 'quota':{}}
        self.app.account_reader = lambda *_a, **_kw: copy.deepcopy(account)
        self.app.health['account'] = copy.deepcopy(account)
        self.app.set_preferred_model(ARGON)
        self.app.activity.root = self.root / 'official'
        conv = self.app.bind_codex(THREAD, agy_id=SESSION)
        self.app.set_fallback(conv['id'], 'text', {'enabled':True})
        db = self.app.activity.root / 'conversations' / (SESSION+'.db')
        db.parent.mkdir(parents=True,exist_ok=True)
        script = '''#!/usr/bin/python3
import json,sys,sqlite3
from pathlib import Path
args=sys.argv[1:];prompt=args[args.index('-p')+1];model=args[args.index('--model')+1]
session=args[args.index('--conversation')+1]
def emit(e): print(json.dumps(e),flush=True)
with Path('calls.jsonl').open('a') as log: log.write(json.dumps({'model':model,'prompt':prompt,'session':session})+'\\n')
emit({'event':'init','conversation_id':session})
if model==ARGON or MODE=='twice':
 error='Individual quota reached. Resets in 1h.' if MODE!='timeout' else 'API error: connection timeout'
 emit({'event':'step_update','step_update':{'step_index':4,'step_type':'user_input','state':'DONE'}})
 if MODE=='tool':emit({'event':'step_update','step_update':{'step_index':5,'step_type':'tool','tool_name':'write_file','state':'DONE'}})
 if MODE=='partial':emit({'event':'step_update','step_update':{'step_index':5,'step_type':'agent_response','text_delta':'partial','state':'DONE'}})
 if MODE=='malformed':print('{bad-json',flush=True)
 emit({'event':'step_update','step_update':{'step_index':6,'step_type':'error_message','state':'DONE'}})
 if not MISSING:
  db=sqlite3.connect(DB);db.execute('CREATE TABLE IF NOT EXISTS steps (idx INTEGER,step_type INTEGER,status INTEGER,step_payload BLOB)');db.execute('DELETE FROM steps')
  db.executemany('INSERT INTO steps VALUES(?,?,?,?)',[(4,14,3,prompt.encode()),(6,17,3,error.encode())])
  if MODE=='hidden-tool':db.execute('INSERT INTO steps VALUES(?,?,?,?)',(5,132,3,b'tool'))
  db.commit();db.close()
 emit({'event':'result','result':{'status':'ERROR','error':error,'response':'','conversation_id':session,'usage':{'total_tokens':0}}})
 sys.exit(3)
emit({'event':'step_update','step_update':{'step_index':8,'step_type':'agent_response','state':'DONE','text_delta':'done','usage':{'total_tokens':7}}})
emit({'event':'result','result':{'status':'SUCCESS','response':'done','conversation_id':session,'usage':{'total_tokens':7}}})
'''
        script = script.replace('ARGON', repr(ARGON)).replace('MODE', repr(mode)).replace('MISSING', repr(missing_db)).replace('DB', repr(str(db)))
        self.binary.write_text(script)
        return conv, account

    def calls(self, conv):
        return [json.loads(l) for l in (self.app.data/'workspaces'/conv['id']/'calls.jsonl').read_text().splitlines()]

    def test_recovery_keeps_id_and_queue_order_and_archives_first_attempt(self):
        conv, _ = self.prepare()
        self.app.set_model_order([ARGON, FLASH, SONNET], 0)
        with self.app.lock:
            one = self.app.submit(conv['id'], 'do work', '')
            two = self.app.submit(conv['id'], 'next work', '')
        self.wait(one); self.wait(two)
        self.assertEqual(one['status'], 'completed', one.get('error'))
        self.assertEqual(two['status'], 'completed', two.get('error'))
        calls = self.calls(conv)
        self.assertEqual([c['model'] for c in calls], [ARGON, FLASH, FLASH])
        self.assertTrue(all(c['session']==SESSION for c in calls))
        self.assertNotEqual(calls[0]['prompt'], calls[1]['prompt'])
        self.assertEqual(calls[2]['prompt'], 'next work')
        self.assertEqual(len(one['attempts']), 2)
        self.assertEqual(len(self.app.conversation(conv['id'])['turns']), 2)
        self.assertEqual(two['model_changes'][0]['reason'], '执行前额度检查')
        folder = self.app.data/'runs'/one['id']
        self.assertIn('quota reached', (folder/'attempt-1-result.json').read_text())
        self.assertTrue((folder/'attempt-1-stdout.ndjson').is_file())
        self.assertEqual(one['requested_model'], ARGON)
        archive=self.app.export_backup({})
        self.assertTrue(archive)
        import zipfile
        files=list(self.app.backups.folder.glob('*.zip')) if hasattr(self.app.backups,'folder') else list(self.app.data.parent.glob('**/*.zip'))
        self.assertTrue(any(any('attempt-1-stdout.ndjson' in n for n in zipfile.ZipFile(f).namelist()) for f in files))

    def test_safe_gate_and_single_retry_limit(self):
        for mode in ('tool','hidden-tool','partial','timeout','malformed','twice'):
            with self.subTest(mode=mode):
                self.app.close()
                # Isolated data and scripts per case; same official ID is local test data.
                self.app=Console(self.root/('data-'+mode),self.binary,import_history=False)
                conv,_=self.prepare(mode)
                turn=self.app.submit(conv['id'],'work','');self.wait(turn)
                self.assertEqual(turn['status'],'failed')
                self.assertEqual(len(self.calls(conv)),2 if mode=='twice' else 1)
                self.assertTrue(turn.get('fallback_notice') or mode=='timeout')

    def test_missing_db_and_explicit_override_never_auto_replay(self):
        conv,_=self.prepare(missing_db=True)
        turn=self.app.submit(conv['id'],'work','');self.wait(turn)
        self.assertEqual(len(self.calls(conv)),1)
        explicit=self.app.submit(conv['id'],'explicit',ARGON);self.wait(explicit)
        self.assertEqual(len(self.calls(conv)),2)
        self.assertFalse(explicit['fallback_enabled'])

    def test_execution_preflight_uses_fresh_account_and_retains_fixed_choice(self):
        conv,account=self.prepare()
        account['quota']={'gemini-argon-wtu-weekly':{'remaining_fraction':0}}
        turn=self.app.submit(conv['id'],'work','');self.wait(turn)
        self.assertEqual(self.calls(conv)[0]['model'],FLASH)
        self.assertEqual(self.app.state['preferred_model'],ARGON)
        self.app.set_fallback(conv['id'],'text',{'enabled':False})
        fixed=self.app.submit(conv['id'],'work','');self.wait(fixed)
        self.assertEqual(fixed['model'],ARGON)

    def test_snapshot_forecast_matches_worker_without_rewriting_preferences(self):
        conv,account=self.prepare()
        account['quota']={'gemini-argon-wtu-weekly':{'remaining_fraction':0}}
        self.app.health['account']=copy.deepcopy(account)
        self.app.set_model_order([SONNET,FLASH,ARGON],0)
        before=copy.deepcopy(self.app.state)
        snapshot=self.app.snapshot(summaries=True)
        view=next(c for c in snapshot['conversations'] if c['id']==conv['id'])['model_resolution']['text']
        self.assertEqual(view['effective_model'],SONNET)
        self.assertEqual(view['requested_model'],ARGON)
        self.assertTrue(view['automatic'])
        self.assertEqual(self.app.state,before,'read-only forecast cannot change saved settings or turns')
        turn=self.app.submit(conv['id'],'ordered work','');self.wait(turn)
        self.assertEqual(self.calls(conv)[0]['model'],view['effective_model'])
        self.assertEqual(turn['requested_model'],ARGON)
        self.assertEqual(self.app.state['preferred_model'],ARGON)

    def test_preview_respects_kind_allowlist_and_disabled_switch(self):
        conv,account=self.prepare()
        account['quota']={'gemini-argon-wtu-weekly':{'remaining_fraction':0}}
        self.app.health['account']=copy.deepcopy(account)
        self.app.set_model_order([SONNET,FLASH,ARGON],0)
        saved=self.app.set_fallback(conv['id'],'image',{'enabled':True,'models':[FLASH]})
        self.assertEqual(saved['model_resolution']['text']['effective_model'],SONNET)
        self.assertEqual(saved['model_resolution']['image']['effective_model'],FLASH)
        saved=self.app.set_fallback(conv['id'],'text',{'enabled':False})
        self.assertEqual(saved['model_resolution']['text']['effective_model'],ARGON)
        self.assertFalse(saved['model_resolution']['text']['automatic'])
        self.app.set_fallback(conv['id'],'image',{'enabled':True,'models':[]})
        self.assertTrue(self.app.model_resolution(self.app.conversation(conv['id']))['image']['error'])

    def test_smart_selection_uses_candidates_and_does_not_guess_image_support(self):
        conv,_=self.prepare()
        self.app.set_window_model(conv['id'],'text','')
        self.app.set_fallback(conv['id'],'text',{'enabled':True,'models':[SONNET]})
        turn=self.app.submit(conv['id'],'smart work','');self.wait(turn)
        self.assertEqual(turn['model'],SONNET)
        self.app.set_window_model(conv['id'],'image','')
        with self.assertRaises(ValueError):self.app.submit(conv['id'],'image work','','image')
        self.app.set_fallback(conv['id'],'image',{'enabled':True,'models':[FLASH]})
        image=self.app.submit(conv['id'],'image work','','image');self.wait(image)
        self.assertEqual(image['model'],FLASH)

    def test_policy_settings_isolation_cas_and_restart(self):
        conv,_=self.prepare()
        self.app.set_model_order([SONNET,FLASH,ARGON],0)
        with self.assertRaises(ConflictError):self.app.set_model_order([ARGON],0)
        unchanged=self.app.set_model_order([ARGON],if_unset=True)
        self.assertEqual(unchanged['model_order'][0],SONNET)
        self.app.set_fallback(conv['id'],'image',{'enabled':True,'models':[FLASH]})
        self.assertNotIn('models',self.app.conversation(conv['id'])['fallback_preferences']['text'])
        self.app.close();self.app=Console(self.app.data,self.binary,import_history=False)
        self.assertEqual(self.app.state['model_order'][0],SONNET)
        self.assertEqual(self.app.conversation(conv['id'])['fallback_preferences']['image']['models'],[FLASH])
        state=fixture_state();state['model_order']=[FLASH];state['conversations'][0]['fallback_preferences']={'image':{'enabled':True,'models':[FLASH]}}
        validate_state(state)
        state['conversations'][0]['fallback_preferences']['image']['enabled']='yes'
        with self.assertRaises(ValueError):validate_state(state)

    def test_image_candidates_never_use_unselected_text_models(self):
        conv,_=self.prepare()
        self.app.set_fallback(conv['id'],'image',{'enabled':True,'models':[FLASH]})
        turn={'model':ARGON,'kind':'image','fallback_models':[FLASH]}
        self.assertEqual(self.app.choose_turn_model(turn),ARGON, 'healthy current model stays selected')
        self.app.health['account']={'state':'known','quota':{'gemini-argon-wtu-weekly':{'remaining_fraction':0}}}
        self.assertEqual(self.app.choose_turn_model(turn),FLASH)
        turn['fallback_models']=[]
        with self.assertRaises(ValueError):self.app.choose_turn_model(turn)
        self.app.health['account']={'state':'known','quota':{'gemini-5h':{'remaining_fraction':0}}}
        self.assertEqual(self.app.choose_turn_model({'model':ARGON,'kind':'text','attempts':[{'model':ARGON}]},retry=True),SONNET)

    def test_default_on_and_off_survive_queued_preferences(self):
        conv,_=self.prepare()
        live=self.app.conversation(conv['id'])
        live.pop('fallback_preferences')
        with self.app.lock:
            turn=self.app.submit(conv['id'],'default on','')
            self.assertTrue(turn['fallback_enabled'])
            self.app.set_fallback(conv['id'],'text',{'enabled':False})
            self.assertTrue(turn['fallback_enabled'],'queued policy is not rewritten')
            self.app.stop(turn['id'])
        self.app.set_window_model(conv['id'],'text','')
        with self.app.lock:
            off=self.app.submit(conv['id'],'smart without retry','')
            self.assertFalse(off['fallback_enabled'])
            self.assertTrue(off['preflight_enabled'],'smart still avoids known exhausted groups')
            self.app.stop(off['id'])

    def test_runtime_block_expires_and_is_account_scoped(self):
        from unittest.mock import patch
        conv,account=self.prepare()
        with patch('server.time.time',return_value=1000):
            self.app.mark_quota_rejection(ARGON,'Individual quota reached.',account)
            self.assertIn(ARGON,self.app.blocked_models())
        with patch('server.time.time',return_value=1301):
            self.assertNotIn(ARGON,self.app.blocked_models())
        self.app.mark_quota_rejection(ARGON,'quota exhausted gemini-5h',account)
        self.assertIn(FLASH,self.app.blocked_models())
        self.app.health['account']=dict(account,email='other@example.test')
        self.assertEqual(self.app.blocked_models(),[])

    def test_current_quota_proof_rejects_stale_error_after_new_tool_reply(self):
        conv,_=self.prepare()
        error='Individual quota reached. Resets in 1h.'
        db=self.app.activity.root/'conversations'/(SESSION+'.db')
        with sqlite3.connect(db) as connection:
            connection.execute('CREATE TABLE steps (idx INTEGER,step_type INTEGER,status INTEGER,step_payload BLOB)')
            connection.executemany('INSERT INTO steps VALUES (?,?,?,?)',[
                (1,17,3,error.encode()),(4,14,3,b'work'),(5,132,3,b'tool'),(6,15,3,b'complete reply')])
        guard=AttemptGuard()
        for e in [{'event':'init','conversation_id':SESSION},
                  {'event':'step_update','step_update':{'step_index':4,'step_type':'user_input'}},
                  {'event':'step_update','step_update':{'step_index':5,'step_type':'tool'}},
                  {'event':'result','result':{'status':'ERROR','conversation_id':SESSION,'error':error,'response':'complete reply'}}]:guard.observe(e)
        turn={'expected_agy_id':SESSION,'prompt':'work'}
        self.assertFalse(guard.current_quota_rejection(turn,self.app.activity.root),'old error cannot block this model')
        with sqlite3.connect(db) as connection:connection.execute('INSERT INTO steps VALUES (7,17,3,?)',(error.encode(),))
        self.assertTrue(guard.current_quota_rejection(turn,self.app.activity.root),'actual current quota failure remains a block even after work')
        self.assertFalse(guard.permits_retry(turn,0,False),'quota evidence never authorizes replay after a tool')
        turn['expected_agy_id']='99999999-2222-3333-4444-555555555555'
        self.assertFalse(guard.current_quota_rejection(turn,self.app.activity.root))

    def test_official_rejection_ignores_later_task_in_same_persistent_session(self):
        conv,_=self.prepare()
        error='Individual quota reached. Resets in 1h.'
        db=self.app.activity.root/'conversations'/(SESSION+'.db')
        with sqlite3.connect(db) as connection:
            connection.execute('CREATE TABLE steps (idx INTEGER,step_type INTEGER,status INTEGER,step_payload BLOB)')
            connection.executemany('INSERT INTO steps VALUES (?,?,?,?)',[
                (10,14,3,b'work'), (11,101,3,b'system'), (12,23,3,b'checkpoint'),
                (13,132,7,b'read-only denial'), (14,17,3,error.encode()),
                # A later task is already present in the same persistent Agy
                # conversation. It must not contaminate this attempt's proof.
                (15,14,3,b'later work'), (16,101,3,b'later system')])
        guard=AttemptGuard()
        for event in [
            {'event':'init','conversation_id':SESSION},
            {'event':'step_update','step_update':{'step_index':10,'step_type':'user_input'}},
            {'event':'step_update','step_update':{'step_index':11,'step_type':'system_message'}},
            {'event':'step_update','step_update':{'step_index':12,'step_type':'checkpoint'}},
            {'event':'step_update','step_update':{'step_index':13,'step_type':'tool','tool_name':'read_file'}},
            {'event':'result','result':{'status':'ERROR','conversation_id':SESSION,'error':error,'response':'',
                                        'denied_actions':[{'action':'read_file'}]}}]:
            guard.observe(event)
        turn={'expected_agy_id':SESSION,'prompt':'work'}
        self.assertTrue(guard.official_rejection(turn,self.app.activity.root))

    def test_read_denial_does_not_turn_historical_quota_into_current_rejection(self):
        conv,_=self.prepare()
        error='Individual quota reached. Resets in 1h.'
        db=self.app.activity.root/'conversations'/(SESSION+'.db')
        with sqlite3.connect(db) as connection:
            connection.execute('CREATE TABLE steps (idx INTEGER,step_type INTEGER,status INTEGER,step_payload BLOB)')
            connection.executemany('INSERT INTO steps VALUES (?,?,?,?)',[
                (1,17,3,error.encode()),(10,14,3,b'/plan work'),
                (11,101,3,b'system'),(12,23,3,b'checkpoint'),(13,132,7,b'read_file denial')])
        guard=AttemptGuard()
        for event in [
            {'event':'init','conversation_id':SESSION},
            {'event':'step_update','step_update':{'step_index':10,'step_type':'user_input'}},
            {'event':'step_update','step_update':{'step_index':11,'step_type':'system_message'}},
            {'event':'step_update','step_update':{'step_index':12,'step_type':'checkpoint'}},
            {'event':'step_update','step_update':{'step_index':13,'step_type':'tool','tool_name':'read_file','state':'ERROR'}},
            {'event':'result','result':{'status':'ERROR','conversation_id':SESSION,'error':error,'response':'',
                                        'denied_actions':[{'action':'read_file'}]}}]:
            guard.observe(event)
        turn={'expected_agy_id':SESSION,'prompt':'work'}
        self.assertFalse(guard.current_quota_rejection(turn,self.app.activity.root))
        self.assertFalse(guard.official_rejection(turn,self.app.activity.root))

    def test_structured_write_denial_blocks_retry_without_stderr_marker(self):
        guard=AttemptGuard()
        error='Individual quota reached. Resets in 1h.'
        for event in [
            {'event':'init','conversation_id':SESSION},
            {'event':'step_update','step_update':{'step_index':10,'step_type':'user_input'}},
            {'event':'result','result':{'status':'ERROR','conversation_id':SESSION,'error':error,
                                        'denied_actions':[{'action':'write_file'}]}}]:
            guard.observe(event)
        turn={'expected_agy_id':SESSION,'prompt':'work'}
        self.assertFalse(guard.permits_retry(turn,3,False))

    def test_candidate_order_is_per_kind_and_persists(self):
        conv,account=self.prepare()
        self.app.set_model_order([ARGON,FLASH,SONNET],0)
        self.app.set_fallback(conv['id'],'text',{'enabled':True,'models':[SONNET,FLASH]})
        self.app.set_fallback(conv['id'],'image',{'enabled':True,'models':[FLASH,SONNET]})
        account['quota']={'gemini-argon-wtu-weekly':{'remaining_fraction':0}}
        self.app.health['account']=copy.deepcopy(account)
        self.assertEqual(self.app.model_resolution(self.app.conversation(conv['id']))['text']['effective_model'],SONNET)
        self.assertEqual(self.app.model_resolution(self.app.conversation(conv['id']))['image']['effective_model'],FLASH)
        self.app.close();self.app=Console(self.app.data,self.binary,import_history=False)
        self.assertEqual(self.app.conversation(conv['id'])['fallback_preferences']['text']['models'],[SONNET,FLASH])

    def test_endpoints_use_existing_token_and_independent_kind(self):
        import threading
        from bridge import Client
        from server import ThreadingHTTPServer, Handler
        conv,_=self.prepare()
        httpd=ThreadingHTTPServer(('127.0.0.1',0),Handler);httpd.app=self.app
        thread=threading.Thread(target=httpd.serve_forever,daemon=True);thread.start()
        try:
            client=Client('http://127.0.0.1:'+str(httpd.server_port))
            saved=client.request('/api/model-order',{'order':[FLASH,ARGON],'version':0})
            self.assertEqual(saved['model_order_version'],1)
            result=client.request('/api/conversations/'+conv['id']+'/fallback',{'kind':'image','settings':{'enabled':False,'models':[FLASH]}})
            self.assertFalse(result['fallback_preferences']['image']['enabled'])
            self.assertTrue(result['fallback_preferences']['text']['enabled'])
            self.assertEqual(client.request('/api/state?summary=1')['model_order'][0],FLASH)
        finally:
            httpd.shutdown();httpd.server_close();thread.join(3)


class PolicyUnitTests(unittest.TestCase):
    def test_quota_classification_and_tier_shared_pools(self):
        self.assertTrue(quota_rejection('Individual quota reached.'))
        for error in ['HTTP 429 rate limit','RESOURCE_EXHAUSTED','503 capacity','quota exceeded: timeout']:
            self.assertFalse(quota_rejection(error))
        s={'model_order':[SONNET,FLASH,ARGON], 'health':{'models':[{'id':x} for x in [ARGON,FLASH,SONNET]],'account':{}}}
        self.assertEqual(default_model(s),SONNET)
        s['health']['account']={'state':'known','quota':{'3p-weekly':{'remaining_fraction':0}}}
        self.assertEqual(default_model(s),FLASH)
        s['health']['account']['quota']['3p-weekly']['reset_time']='2020-01-01T00:00:00Z'
        self.assertEqual(default_model(s),SONNET)
