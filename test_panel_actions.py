"""Manual panel actions stay bounded and continue the exact official session."""
import copy
import http.client
import json
from pathlib import Path
import tempfile
import threading
import time
import unittest
from unittest.mock import patch
from urllib.parse import urlencode

from headless_compat import TurnEvidence
from panel_actions import search_history, recovery_prompt
from server import BUSY, Console, ConflictError, DEFAULT_MODEL, Handler, ThreadingHTTPServer

THREAD = '11111111-2222-3333-4444-555555555555'
SID = 'aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee'
FLASH = 'gemini-3.8-flash-high'
FAKE_SCOPED = '''#!/usr/bin/python3
import json,sys
from pathlib import Path
args=sys.argv[1:];prompt=args[args.index('-p')+1]
sid=args[args.index('--conversation')+1]
model=args[args.index('--model')+1]
def send(v):print(json.dumps(v),flush=True)
send({'event':'init','conversation_id':sid})
send({'event':'step_update','step_update':{'step_index':1,'step_type':'user_input','state':'DONE','conversation_id':sid}})
if prompt.startswith('/compact') and not Path(__file__).with_name('no-checkpoint').exists():
 send({'event':'step_update','step_update':{'step_index':2,'step_type':'checkpoint','state':'DONE','conversation_id':sid}})
send({'event':'result','result':{'status':'SUCCESS','response':'continued '+sid+' '+model,'conversation_id':sid}})
'''


def archived_turn(index, branch='main', **extra):
    return {'id': format(index, '032x'), 'prompt': 'Task '+str(index), 'response': 'Result '+str(index),
            'status': 'failed', 'error': 'upstream unavailable', 'branch': branch, 'model': DEFAULT_MODEL,
            'kind': 'text', 'events': [], 'images': [], 'usage': {}, 'elapsed': 0,
            'created_at': '2026-11-14T00:00:00Z', **extra}


class SearchTests(unittest.TestCase):
    def test_literal_scope_deleted_and_branch_results(self):
        state={'conversations':[
            {'id':'a'*32,'title':'Alpha','updated_at':'2','codex_thread_id':THREAD,
             'branches':{'main':{'title':'Main'},'art':{'title':'Art'}},
             'turns':[archived_turn(1,prompt='literal [x].*',response='Reply [X].*',status='completed'),
                      archived_turn(2,branch='art',prompt='[x].* more')]},
            {'id':'b'*32,'title':'Beta','updated_at':'3','turns':[archived_turn(3,prompt='[x].*')]},
            {'id':'c'*32,'title':'Trash','deleted_at':'now','turns':[archived_turn(4,prompt='[x].*')]}]}
        before=copy.deepcopy(state)
        all_results=search_history(state,'[x].*')['results']
        self.assertEqual(len(all_results),4);self.assertEqual(all_results[0]['title'],'Beta')
        scoped=search_history(state,'[x].*','a'*32)['results']
        self.assertEqual([r['branch'] for r in scoped],['art','main','main'])
        self.assertEqual(scoped[1]['turn_id'],format(1,'032x'))
        self.assertEqual(scoped[0]['codex_thread_id'],THREAD)
        self.assertEqual(state,before)

    def test_query_result_snippet_and_scan_limits(self):
        state={'conversations':[{'id':'a'*32,'title':'Long','turns':[
            archived_turn(i,response='x'*10000+'needle'+'y'*10000) for i in range(60)]}]}
        result=search_history(state,'NEEDLE',limit=3)
        self.assertEqual(len(result['results']),3);self.assertTrue(result['truncated'])
        self.assertLessEqual(max(len(r['snippet']) for r in result['results']),260)
        for query,limit in [('',3),('a'*101,3),('x',0),('x',51),('x',True)]:
            with self.assertRaises(ValueError):search_history(state,query,limit=limit)
        many={'conversations':[{'id':'a'*32,'title':'Many','turns':[archived_turn(i) for i in range(20001)]}]}
        self.assertTrue(search_history(many,'absent')['truncated'])

    def test_recovery_unicode_material_is_bounded_by_bytes(self):
        prompt=recovery_prompt({'prompt':'😀'*30000,'response':'文'*50000,'error':'错'*10000},'continue')
        self.assertLess(len(prompt.encode('utf-8')),100000)
        self.assertIn('过长部分已省略',prompt)


class ActionTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.root=Path(self.temp.name)
        binary=self.root/'fake-agy';binary.write_text(FAKE_SCOPED);binary.chmod(0o700)
        self.app=Console(self.root/'data',binary,import_history=False,account_reader=lambda *_a,**_k:{'state':'unknown','email':None})
        self.app.activity.root=self.root/'official'
        self.app.health['models']=[{'id':DEFAULT_MODEL,'label':'Argon'},{'id':FLASH,'label':'Flash'}]
        self.cid=self.app.bind_codex(THREAD,'Test')['id']
        self.app.create_branch(self.cid,'Main',key='main')
        self.conv=self.app.conversation(self.cid)
        self.conv['agy_id']=SID;self.conv['branches']['main']['agy_id']=SID
        self.old=archived_turn(1,response='already wrote a partial result')
        self.conv['turns'].append(self.old);self.app.save()

    def tearDown(self):self.app.close();self.temp.cleanup()

    def wait_turn(self,tid):
        deadline=time.monotonic()+5
        while time.monotonic()<deadline:
            with self.app.lock:
                turn=next(t for t in self.conv['turns'] if t['id']==tid)
                if turn['status'] not in BUSY:return copy.deepcopy(turn)
            time.sleep(.02)
        self.fail('fake task did not finish')

    def test_recovery_keeps_old_record_branch_id_and_global_model(self):
        before=copy.deepcopy(self.old);self.app.state['preferred_model']=FLASH
        turn=self.app.recover_turn(self.cid,self.old['id'],'continue',acknowledge=True)
        done=self.wait_turn(turn['id'])
        self.assertEqual(done['status'],'completed');self.assertEqual(done['agy_id'],SID)
        self.assertEqual(done['branch'],'main');self.assertEqual(done['model'],FLASH)
        self.assertEqual(done['recovery_of'],self.old['id']);self.assertEqual(self.old,before)
        self.assertIn('already wrote',done['prompt']);self.assertIn(SID,done['response'])
        self.assertEqual(len(self.app.state['conversations']),1)

    def test_recovery_explicit_override_is_task_only_and_confirmation_required(self):
        self.app.state['preferred_model']=FLASH
        for strategy,ack in [('continue',False),('invalid',True)]:
            with self.assertRaises(ValueError):self.app.recover_turn(self.cid,self.old['id'],strategy,acknowledge=ack)
        turn=self.app.recover_turn(self.cid,self.old['id'],'retry',DEFAULT_MODEL,True)
        self.assertEqual(self.wait_turn(turn['id'])['model'],DEFAULT_MODEL)
        self.assertEqual(self.app.state['preferred_model'],FLASH)
        self.old['status']='completed'
        with self.assertRaises(ValueError):self.app.recover_turn(self.cid,self.old['id'],'retry',acknowledge=True)

    def test_old_failure_cannot_be_replayed_after_any_newer_same_branch_turn(self):
        turn=self.app.recover_turn(self.cid,self.old['id'],'continue',acknowledge=True)
        self.wait_turn(turn['id'])
        with self.assertRaisesRegex(ConflictError,'后续任务'):
            self.app.recover_turn(self.cid,self.old['id'],'continue',acknowledge=True)
        self.assertEqual(len(self.conv['turns']),2)

    def test_unbound_panel_compact_rejects_wrong_official_id(self):
        self.conv.pop('codex_thread_id')
        binary=Path(self.app.binary)
        binary.write_text(FAKE_SCOPED.replace("sid=args[args.index('--conversation')+1]", "sid='bbbbbbbb-cccc-dddd-eeee-ffffffffffff'"))
        turn=self.app.compact_context(self.cid,'main',True);done=self.wait_turn(turn['id'])
        self.assertEqual(done['status'],'failed');self.assertTrue(done['session_mismatch'])
        self.assertEqual(done['expected_agy_id'],SID);self.assertEqual(self.conv['agy_id'],SID)
        self.assertIsNone(done['compaction_evidence'])

    def test_duplicate_and_compact_branch_busy_guards(self):
        with self.app.lock:
            self.conv['turns'].append(archived_turn(2,status='queued'))
            with self.assertRaises(ConflictError):self.app.recover_turn(self.cid,self.old['id'],'continue',acknowledge=True)
            with self.assertRaises(ConflictError):self.app.compact_context(self.cid,'main',True)
            self.conv['turns'].pop()
        self.assertEqual(len(self.conv['turns']),1)

    def test_compact_exact_session_and_checkpoint_not_model_prose(self):
        with self.assertRaises(ValueError):self.app.compact_context(self.cid,'main',False)
        turn=self.app.compact_context(self.cid,'main',True);done=self.wait_turn(turn['id'])
        self.assertEqual(done['agy_id'],SID);self.assertEqual(done['action'],'compact')
        self.assertEqual(done['compaction_evidence']['source'],'cli_checkpoint')
        (self.root/'no-checkpoint').touch()
        second=self.app.compact_context(self.cid,'main',True);without=self.wait_turn(second['id'])
        self.assertEqual(without['status'],'completed');self.assertIsNone(without['compaction_evidence'])
        self.conv['branches']['main']['agy_id']=None
        with self.assertRaises(ValueError):self.app.compact_context(self.cid,'main',True)

    def test_checkpoint_ignores_wrong_session_incomplete_and_old_steps(self):
        for sid,status,index in [('other','DONE',2),(SID,'RUNNING',2),(SID,'DONE',0)]:
            ev=TurnEvidence()
            ev.observe({'event':'step_update','step_update':{'step_index':1,'step_type':'user_input','state':'DONE','conversation_id':SID}})
            ev.observe({'event':'step_update','step_update':{'step_index':index,'step_type':'checkpoint','state':status,'conversation_id':sid}})
            self.assertIsNone(ev.compaction_checkpoint(SID))

    def test_around_page_contains_old_match_without_loading_whole_history(self):
        self.conv['turns']=[archived_turn(i,status='completed') for i in range(70)]
        page=self.app.conversation_page(self.cid,around=format(4,'032x'),branch='main')
        self.assertEqual(len(page['turns']),20);self.assertEqual(page['page']['total'],70)
        self.assertIn(format(4,'032x'),[t['id'] for t in page['turns']])
        with self.assertRaises(ValueError):self.app.conversation_page(self.cid,before='x',around='x')
        with self.assertRaises(KeyError):self.app.conversation_page(self.cid,around='gone')

    def test_maintenance_freezes_submit_and_account_launch_is_fixed(self):
        with self.app.panel_maintenance():
            with self.assertRaises(ConflictError):self.app.submit(self.cid,'task','')
        self.assertFalse(self.app.maintenance)
        with patch('server.Path.is_dir',return_value=True),patch('server.subprocess.run') as run:
            run.return_value.returncode=0
            value=self.app.open_account_switcher()
            self.assertFalse(value['account_changed'])
            self.assertEqual(run.call_args.args[0],['/usr/bin/open','-b','com.ccswitch.desktop'])
        self.conv['turns'].append(archived_turn(8,status='running'))
        with patch('server.subprocess.run') as run:
            with self.assertRaises(ConflictError):self.app.open_account_switcher()
            run.assert_not_called()
        self.conv['turns'].pop()

    def test_http_auth_search_and_around(self):
        httpd=ThreadingHTTPServer(('127.0.0.1',0),Handler);httpd.app=self.app
        worker=threading.Thread(target=httpd.serve_forever,daemon=True);worker.start()
        def request(method,path,body=None,headers=None):
            client=http.client.HTTPConnection('127.0.0.1',httpd.server_port,timeout=5)
            try:
                client.request(method,path,body,headers or {});res=client.getresponse()
                return res.status,json.loads(res.read())
            finally:client.close()
        try:
            target='/api/conversations/'+self.cid
            self.assertEqual(request('POST',target+'/compact','{}')[0],403)
            headers={'Content-Type':'application/json','X-Console-Token':self.app.token}
            self.assertEqual(request('POST',target+'/compact','{}',headers)[0],400)
            status,result=request('GET','/api/search?'+urlencode({'q':'partial','conversation':self.cid}))
            self.assertEqual(status,200);self.assertEqual(result['results'][0]['turn_id'],self.old['id'])
            self.assertEqual(request('GET','/api/search?q=x&conversation=invalid')[0],400)
            status,page=request('GET',target+'?limit=20&around='+self.old['id'])
            self.assertEqual(status,200);self.assertEqual(len(page['turns']),1)
        finally:httpd.shutdown();httpd.server_close();worker.join(3)


if __name__=='__main__':unittest.main()
