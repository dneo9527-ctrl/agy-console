"""Dynamic sessions, migrations and update isolation; no paid model calls."""
import copy
import json
import os
from pathlib import Path
import tempfile
import subprocess
import sys
import threading
import time
import unittest
from unittest.mock import patch

from bridge import Client
from conversation_sessions import branch_for, normalize_sessions
from server import BUSY, Console, DEFAULT_MODEL, Handler, ThreadingHTTPServer
from test_images import PNG_B64

THREAD='11111111-2222-3333-4444-555555555555'
OTHER='22222222-2222-3333-4444-555555555555'
EXTERNAL='33333333-2222-3333-4444-555555555555'
FAKE='''#!/usr/bin/python3
import base64,json,sys,uuid
from pathlib import Path
args=sys.argv[1:];prompt=args[args.index('-p')+1]
sid=args[args.index('--conversation')+1] if '--conversation' in args else str(uuid.uuid5(uuid.NAMESPACE_URL,str(Path.cwd())))
if prompt.startswith('wrong-session'):sid='99999999-2222-3333-4444-555555555555'
with Path('calls.jsonl').open('a') as f:f.write(json.dumps({'args':args,'session':sid,'cwd':str(Path.cwd())})+'\\n')
print(json.dumps({'event':'init','conversation_id':sid}),flush=True)
if args[args.index('--mode')+1]=='accept-edits':
 Path('result.png').write_bytes(base64.b64decode('''+repr(PNG_B64)+'''))
 reply='![image](result.png)'
else:reply='done '+prompt
print(json.dumps({'event':'result','result':{'status':'SUCCESS','response':reply,'conversation_id':sid}}),flush=True)
'''


class DynamicSessionTests(unittest.TestCase):
    def setUp(self):
        # Session fixtures must not launch the installed public updater. Its
        # asynchronous cache writes can outlive temporary-directory cleanup.
        updater = patch('server.SoftwareUpdates.start')
        updater.start()
        self.addCleanup(updater.stop)
        self.temp=tempfile.TemporaryDirectory();self.root=Path(self.temp.name)
        self.binary=self.root/'fake-agy';self.binary.write_text(FAKE);self.binary.chmod(0o700)
        self.reader=lambda *_a,**_k:{'state':'unknown','email':None}
        self.app=Console(self.root/'data',self.binary,import_history=False,account_reader=self.reader)
        self.conv=self.app.bind_codex(THREAD,'Dynamic')

    def tearDown(self):self.app.close();self.temp.cleanup()

    def wait(self,turn):
        end=time.monotonic()+5
        while turn['status'] in BUSY and time.monotonic()<end:time.sleep(.01)
        self.assertNotIn(turn['status'],BUSY)
        return turn

    def submit(self,prompt='first',kind='text',branch=None):
        return self.app.submit(self.conv['id'],prompt,DEFAULT_MODEL,kind,branch)

    def stored(self):return self.app.conversation(self.conv['id'])

    def revision(self,cid):return next(c['revision'] for c in self.app.snapshot(True)['conversations'] if c['id']==cid)

    def inventory(self,*rows):
        self.app.activity.scan=lambda *_a,**_k:{'sessions':copy.deepcopy(list(rows)),'unmatched_processes':[]}

    def test_first_image_only_and_mixed_modes_do_not_precreate_text_sessions(self):
        self.assertEqual(self.stored()['branches'],{})
        first=self.wait(self.submit('image','image'));self.assertEqual(first['status'],'completed',first['error'])
        self.assertEqual(list(self.stored()['branches']),['main']);sid=self.stored()['agy_id']
        second=self.wait(self.submit('talk about the image'));self.assertEqual(second['status'],'completed')
        self.assertEqual(second['agy_id'],sid);self.assertEqual(len(self.stored()['branches']),1)

    def test_first_explicit_session_becomes_primary_without_an_empty_main(self):
        first=self.wait(self.submit('image','image','artwork'));sid=first['agy_id']
        self.assertEqual(self.stored()['primary_branch'],'artwork');self.assertEqual(list(self.stored()['branches']),['artwork'])
        follow=self.wait(self.submit('followup'));self.assertEqual(follow['branch'],'artwork');self.assertEqual(follow['agy_id'],sid)

    def test_named_sessions_have_distinct_ids_and_workspaces_and_resume_independently(self):
        main=self.wait(self.submit());image=self.wait(self.submit('image','image','art'))
        again=self.wait(self.submit('continue art','text','art'));primary=self.wait(self.submit('continue primary'))
        self.assertNotEqual(main['agy_id'],image['agy_id']);self.assertEqual(image['agy_id'],again['agy_id'])
        self.assertEqual(primary['agy_id'],main['agy_id']);self.assertEqual(self.stored()['agy_id'],main['agy_id'])
        base=self.root/'data/workspaces'/self.conv['id']
        calls=[json.loads(x) for x in (self.root/'data/session-workspaces'/self.conv['id']/'art/calls.jsonl').read_text().splitlines()]
        self.assertIn('--conversation',calls[-1]['args']);self.assertNotEqual(calls[-1]['cwd'],str(base))

    def test_queued_followups_resolve_the_session_id_at_execution(self):
        first=self.submit();other=self.submit('second',branch='review');follow=self.submit('followup')
        for turn in (first,other,follow):self.wait(turn);self.assertEqual(turn['status'],'completed',turn['error'])
        self.assertEqual(first['agy_id'],follow['agy_id']);self.assertNotEqual(other['agy_id'],first['agy_id'])

    def test_other_chat_and_health_changes_do_not_invalidate_current_page(self):
        self.wait(self.submit());before=self.revision(self.conv['id'])
        other=self.app.bind_codex(OTHER);self.wait(self.app.submit(other['id'],'other',DEFAULT_MODEL))
        self.assertEqual(self.revision(self.conv['id']),before)
        self.app.health['version']='changed';self.assertEqual(self.revision(self.conv['id']),before)
        self.wait(self.submit('next'));self.assertNotEqual(self.revision(self.conv['id']),before)

    def test_fast_visible_events_invalidate_pages_without_extra_disk_writes(self):
        turn=self.wait(self.submit());conv=self.stored();self.app.last_stream_save=time.monotonic()
        before=self.revision(conv['id']);tag=self.app.state_etag();raw=self.app.file.read_bytes()
        self.app.event(conv,turn,{'event':'step_update','step_update':{'step_type':'tool','tool_name':'search_web'}})
        self.assertNotEqual(self.revision(conv['id']),before);self.assertNotEqual(self.app.state_etag(),tag)
        self.assertEqual(self.app.file.read_bytes(),raw)
        before=self.revision(conv['id']);self.app.event(conv,turn,{'event':'step_update','step_update':{'text_delta':' new chunk'}})
        self.assertNotEqual(self.revision(conv['id']),before);self.assertEqual(self.app.file.read_bytes(),raw)
        before=self.revision(conv['id']);self.app.event(conv,turn,{'event':'not-visible'})
        self.assertEqual(self.revision(conv['id']),before)

    def test_restart_preserves_all_ids_and_has_a_new_revision_namespace(self):
        first=self.wait(self.submit());second=self.wait(self.submit('second',branch='draft'));old=self.revision(self.conv['id'])
        self.app.close();self.app=Console(self.root/'data',self.binary,import_history=False,account_reader=self.reader)
        self.assertNotEqual(self.revision(self.conv['id']),old)
        self.assertEqual(self.stored()['branches']['draft']['agy_id'],second['agy_id'])
        follow=self.wait(self.submit('after restart'));self.assertEqual(follow['agy_id'],first['agy_id'])

    def test_legacy_image_history_migrates_without_changing_identity(self):
        value={'id':'a'*32,'agy_id':EXTERNAL,'turns':[{'id':'b'*32,'kind':'image','status':'completed'}]}
        normalize_sessions(value)
        self.assertEqual(value['primary_branch'],'main');self.assertEqual(value['branches']['main']['agy_id'],EXTERNAL)
        self.assertEqual(value['turns'][0]['branch'],'main');self.assertEqual(len(value['branches']),1)

    def test_first_upgrade_keeps_a_private_exact_backup_and_legacy_workspace(self):
        first=self.wait(self.submit());self.app.close()
        path=self.root/'data/state.json';legacy=json.loads(path.read_text())
        for conv in legacy['conversations']:
            conv.pop('branches',None);conv.pop('primary_branch',None)
            for turn in conv['turns']:turn.pop('branch',None)
        raw=json.dumps(legacy).encode();path.write_bytes(raw)
        self.app=Console(self.root/'data',self.binary,import_history=False,account_reader=self.reader)
        backups=list((self.root/'data/backups').glob('*.json'));self.assertEqual(len(backups),1);self.assertEqual(backups[0].read_bytes(),raw)
        self.assertEqual(backups[0].stat().st_mode&0o777,0o600)
        follow=self.wait(self.submit('after migration'));self.assertEqual(follow['agy_id'],first['agy_id'])
        self.assertTrue((self.root/'data/workspaces'/self.conv['id']/'calls.jsonl').is_file())
        self.app.close();self.app=Console(self.root/'data',self.binary,import_history=False,account_reader=self.reader)
        self.assertEqual(len(list((self.root/'data/backups').glob('*.json'))),1)

    def test_secondary_id_cannot_replace_a_primary_through_legacy_bind(self):
        self.wait(self.submit());secondary=self.wait(self.submit('secondary',branch='review'))
        with self.assertRaises(ValueError):self.app.bind_codex(OTHER,agy_id=secondary['agy_id'])

    def test_returned_wrong_id_does_not_overwrite_primary_or_branch(self):
        self.wait(self.submit());lane=self.wait(self.submit('normal',branch='draft'));primary=self.stored()['agy_id']
        wrong=self.wait(self.submit('wrong-session',branch='draft'));self.assertEqual(wrong['status'],'failed')
        self.assertEqual(self.stored()['branches']['draft']['agy_id'],lane['agy_id']);self.assertEqual(self.stored()['agy_id'],primary)

    def test_explicit_inactive_association_preserves_original_id_and_can_continue(self):
        self.inventory({'id':EXTERNAL,'active':False,'status':'ended','parent_id':None})
        lane=self.app.create_branch(self.conv['id'],'Existing','existing',EXTERNAL)
        self.assertEqual(lane['agy_id'],EXTERNAL);self.assertEqual(self.stored()['turns'],[])
        follow=self.wait(self.submit('continue',branch='existing'));self.assertEqual(follow['agy_id'],EXTERNAL)

    def test_active_or_tool_child_association_is_rejected(self):
        for metadata in ({'active':True,'status':'running','parent_id':None},{'active':False,'status':'ended','parent_id':'parent'},
                         {'active':False,'status':'unknown','parent_id':None},{'active':False,'status':'ended','parent_id':None,'parent_known':False}):
            self.inventory(dict(metadata,id=EXTERNAL))
            with self.assertRaises(ValueError):self.app.create_branch(self.conv['id'],'No','work',EXTERNAL)
        self.assertEqual(self.stored()['branches'],{})

    def test_pending_first_turn_cannot_have_its_session_binding_changed(self):
        self.app.create_branch(self.conv['id'],'Pending','pending')
        self.stored()['turns'].append({'id':'d'*32,'branch':'pending','status':'queued'})
        self.inventory({'id':EXTERNAL,'active':False,'status':'ended','parent_id':None})
        with self.assertRaises(ValueError):self.app.create_branch(self.conv['id'],'No','pending',EXTERNAL)
        self.assertIsNone(self.stored()['branches']['pending']['agy_id'])

    def test_cross_entry_and_overwrite_association_is_rejected(self):
        first=self.wait(self.submit());self.inventory({'id':first['agy_id'],'active':False,'status':'ended','parent_id':None})
        other=self.app.bind_codex(OTHER)
        with self.assertRaises(ValueError):self.app.create_branch(other['id'],'No','work',first['agy_id'])
        self.inventory({'id':EXTERNAL,'active':False,'status':'ended','parent_id':None})
        with self.assertRaises(ValueError):self.app.create_branch(self.conv['id'],'No','main',EXTERNAL)

    def test_associated_session_reactivated_elsewhere_is_not_concurrently_written(self):
        self.inventory({'id':EXTERNAL,'active':False,'status':'ended','parent_id':None})
        self.app.create_branch(self.conv['id'],'Existing','old',EXTERNAL)
        self.inventory({'id':EXTERNAL,'active':True,'status':'running','parent_id':None})
        turn=self.wait(self.submit('do not start',branch='old'));self.assertEqual(turn['status'],'failed')
        self.assertFalse((self.root/'data/session-workspaces'/self.conv['id']/'old/calls.jsonl').exists())

    def test_official_parent_chain_groups_under_exact_owner_without_guessing_titles(self):
        parent=self.wait(self.submit())['agy_id'];child='child';grandchild='grandchild'
        self.inventory({'id':parent,'parent_id':None},{'id':child,'parent_id':parent},{'id':grandchild,'parent_id':child},
                       {'id':'unrelated','title':'Dynamic','parent_id':None},{'id':'cycle-a','parent_id':'cycle-b'},{'id':'cycle-b','parent_id':'cycle-a'})
        values={s['id']:s for s in self.app.activity_snapshot()['sessions']}
        self.assertEqual(values[grandchild]['owner_conversation_id'],self.conv['id']);self.assertTrue(values[child]['related'])
        self.assertNotIn('owner_conversation_id',values['unrelated']);self.assertNotIn('owner_conversation_id',values['cycle-a'])

    def test_session_cap_and_invalid_names_do_not_start_models(self):
        for key in ('../bad','Bad','',42):
            if key=='':continue
            with self.assertRaises(ValueError):branch_for(self.stored(),key,create=True)
        for i in range(16):self.app.create_branch(self.conv['id'],'Session '+str(i),'s'+str(i))
        with self.assertRaises(ValueError):self.app.create_branch(self.conv['id'],'Too many','overflow')
        self.assertEqual(self.app.processes,{});self.assertEqual(self.stored()['turns'],[])

    def test_filtered_pages_target_turn_id_and_export_cover_all_sessions(self):
        primary=self.wait(self.submit('primary request'));branch=self.wait(self.submit('other request',branch='research'))
        page=self.app.conversation_page(self.conv['id'],branch='research')
        self.assertEqual([t['id'] for t in page['turns']],[branch['id']]);self.assertEqual(page['page']['total'],1)
        httpd=ThreadingHTTPServer(('127.0.0.1',0),Handler);httpd.app=self.app
        thread=threading.Thread(target=httpd.serve_forever,daemon=True);thread.start()
        try:
            client=Client('http://127.0.0.1:'+str(httpd.server_port));client.features={'light_history':True}
            conv,turn=client.wait(self.conv['id'],branch['id'],3);self.assertEqual(conv['agy_id'],branch['agy_id'])
            import urllib.request
            with urllib.request.urlopen(client.url+'/api/conversations/'+self.conv['id']+'/export') as response:text=response.read().decode()
            for part in ('primary request','other request','research'):self.assertIn(part,text)
            self.assertNotEqual(primary['agy_id'],conv['agy_id'])
        finally:httpd.shutdown();httpd.server_close();thread.join(3)

    def test_cli_dispatch_and_ensure_report_the_requested_session_truthfully(self):
        httpd=ThreadingHTTPServer(('127.0.0.1',0),Handler);httpd.app=self.app
        thread=threading.Thread(target=httpd.serve_forever,daemon=True);thread.start()
        bridge=Path(__file__).with_name('bridge.py');url='http://127.0.0.1:'+str(httpd.server_port)
        task=self.root/'task.txt';task.write_text('image')
        env=dict(os.environ,CODEX_THREAD_ID=OTHER)
        def run(*args):return subprocess.run([sys.executable,str(bridge),'--url',url,*args],env=env,capture_output=True,text=True,timeout=12)
        try:
            ensure=run('--ensure','--branch','art');self.assertEqual(ensure.returncode,0,ensure.stderr)
            status=json.loads(ensure.stdout);self.assertIsNone(status['agy_id']);self.assertFalse(status['model_started']);self.assertFalse(status['branch_exists'])
            self.assertNotIn('session=',status['url']);self.assertEqual(self.app.conversation(status['console_conversation_id'])['branches'],{})
            # This fixture explicitly supports images; smart mode must not guess that capability.
            self.app.set_window_model(status['console_conversation_id'],'image',DEFAULT_MODEL)
            first=run('--task-file',str(task),'--branch','art','--branch-title','Artwork','--kind','image','--out',str(self.root/'first'))
            self.assertEqual(first.returncode,0,first.stderr);a=json.loads((self.root/'first/result.json').read_text())
            task.write_text('continue image discussion')
            again=run('--task-file',str(task),'--out',str(self.root/'again'))
            self.assertEqual(again.returncode,0,again.stderr);b=json.loads((self.root/'again/result.json').read_text())
            self.assertEqual(a['conversation_id'],b['conversation_id']);self.assertEqual(b['branch'],'art')
            separate=run('--task-file',str(task),'--branch','review','--out',str(self.root/'separate'))
            self.assertEqual(separate.returncode,0,separate.stderr);c=json.loads((self.root/'separate/result.json').read_text())
            self.assertNotEqual(c['conversation_id'],a['conversation_id']);self.assertIn('session=review',c['url'])
            resolved=run('--ensure','--branch','art');value=json.loads(resolved.stdout);self.assertEqual(value['agy_id'],a['conversation_id']);self.assertTrue(value['branch_exists'])
            original=self.app.service_info
            def legacy_reader():
                value=original();value['features']['light_history']=False;return value
            self.app.service_info=legacy_reader
            fallback=run('--task-file',str(task),'--branch','review','--out',str(self.root/'fallback'))
            self.assertEqual(fallback.returncode,0,fallback.stderr);fallback_result=json.loads((self.root/'fallback/result.json').read_text())
            self.assertEqual(fallback_result['conversation_id'],c['conversation_id']);self.assertNotEqual(fallback_result['conversation_id'],a['conversation_id'])
        finally:httpd.shutdown();httpd.server_close();thread.join(3)


if __name__=='__main__':unittest.main(verbosity=2)
