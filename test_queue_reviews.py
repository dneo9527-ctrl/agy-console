"""Observable queue order and explicit collaboration results; isolated fake CLI."""
import copy
import json
from pathlib import Path
import tempfile
import subprocess
import sys
import threading
import time
import unittest
from unittest.mock import patch

from server import BUSY, ConflictError, Console, DEFAULT_MODEL, Handler, ThreadingHTTPServer

THREAD='11111111-2222-4333-8444-555555555555'
FAKE='''#!/usr/bin/python3
import json,sys,time,uuid
from pathlib import Path
args=sys.argv;prompt=args[args.index('-p')+1]
root=Path(__file__).parent
with (root/'executed.jsonl').open('a') as out:out.write(json.dumps({'prompt':prompt,'continuation':'--conversation' in args})+'\\n')
sid=args[args.index('--conversation')+1] if '--conversation' in args else str(uuid.uuid4())
print(json.dumps({'event':'init','conversation_id':sid}),flush=True)
if prompt=='hold':
 print(json.dumps({'event':'step_update','step_update':{'text_delta':'holding'}}),flush=True)
 time.sleep(30)
print(json.dumps({'event':'result','result':{'status':'SUCCESS','conversation_id':sid,'response':'done '+prompt}}),flush=True)
'''


def review(status='passed'):
    return {'summary':'Implemented the bounded review.',
            'items':[{'suggestion':'Make task order explicit','decision':'accepted','reason':'Verified execution order.'},
                     {'suggestion':'Increase concurrency','decision':'deferred','reason':'Keep one worker for this Mac.'}],
            'verification':{'status':status,'details':'Fake CLI execution log and saved conversation ID checked.'}}


class QueueReviewTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.root=Path(self.temp.name)
        self.binary=self.root/'fake-agy';self.binary.write_text(FAKE);self.binary.chmod(0o700)
        self.app=Console(self.root/'data',self.binary,import_history=False,account_reader=lambda *_a,**_k:{'state':'unknown'})
        self.a=self.app.bind_codex(THREAD,'A')
        self.b=self.app.bind_codex('aaaaaaaa-bbbb-4ccc-8ddd-eeeeeeeeeeee','B')

    def tearDown(self):
        self.app.close();self.temp.cleanup()

    def wait(self,check):
        deadline=time.monotonic()+7
        while time.monotonic()<deadline:
            with self.app.lock:
                if check():return
            time.sleep(.01)
        self.fail('condition did not settle')

    def submit(self,c,prompt,**kwargs):return self.app.submit(c['id'],prompt,DEFAULT_MODEL,codex_dispatch=True,**kwargs)

    def holding(self):
        c=self.app.create('Running');t=self.app.submit(c['id'],'hold',DEFAULT_MODEL)
        self.wait(lambda:t['response']=='holding')
        return t

    def test_reorder_changes_actual_worker_order_but_preserves_session_sequence(self):
        hold=self.holding();a1=self.submit(self.a,'a1');a2=self.submit(self.a,'a2');b1=self.submit(self.b,'b1')
        original=self.app.queue_snapshot();q=self.app.reorder_queue(b1['id'],'up',original['version'])
        with self.assertRaises(ConflictError):self.app.reorder_queue(b1['id'],'up',original['version'])
        q=self.app.reorder_queue(b1['id'],'up',q['version'])
        self.assertEqual([t['id'] for t in q['queued']],[b1['id'],a1['id'],a2['id']])
        self.assertFalse(q['queued'][2]['can_up'])
        with self.assertRaises(ValueError):self.app.reorder_queue(a2['id'],'up',q['version'])
        with self.assertRaises(ConflictError):self.app.reorder_queue(hold['id'],'down',q['version'])
        self.app.stop(hold['id']);self.wait(lambda:all(t['status'] not in BUSY for t in (hold,a1,a2,b1)))
        rows=[json.loads(line) for line in (self.root/'executed.jsonl').read_text().splitlines()]
        self.assertEqual([r['prompt'] for r in rows],['hold','b1','a1','a2'])
        self.assertTrue(rows[-1]['continuation']);self.assertEqual(a1['agy_id'],a2['agy_id'])
        self.assertTrue(all(t['status']=='completed' for t in (a1,a2,b1)))

    def test_cancel_removes_queued_item_without_execution(self):
        hold=self.holding();a=self.submit(self.a,'never');b=self.submit(self.b,'later')
        self.app.stop(a['id']);q=self.app.queue_snapshot()
        self.assertEqual([t['id'] for t in q['queued']],[b['id']]);self.assertEqual(q['queued'][0]['position'],1)
        self.app.stop(hold['id']);self.wait(lambda:b['status']=='completed')
        self.assertNotIn('never',(self.root/'executed.jsonl').read_text())

    def test_review_requires_real_turn_owner_and_reply_and_persists_without_model_call(self):
        t=self.submit(self.a,'review');self.wait(lambda:t['status']=='completed')
        log=(self.root/'executed.jsonl').read_bytes()
        with self.assertRaises(ValueError):self.app.record_review(self.a['id'],t['id'],'other-thread',review())
        with self.assertRaises(ValueError):self.app.record_review(self.b['id'],t['id'],'aaaaaaaa-bbbb-4ccc-8ddd-eeeeeeeeeeee',review())
        result=self.app.record_review(self.a['id'],t['id'],THREAD,review())
        self.assertFalse(result['model_called']);self.assertEqual(self.app.collaboration_state(self.app.conversation(self.a['id']))['stage'],'verified')
        self.assertEqual((self.root/'executed.jsonl').read_bytes(),log)
        self.app.record_review(self.a['id'],t['id'],THREAD,review('partial'))
        self.assertEqual(len(t['review_history']),1);self.assertEqual(self.app.collaboration_state(self.app.conversation(self.a['id']))['stage'],'reviewed')
        saved=json.loads(self.app.file.read_text());stored=next(c for c in saved['conversations'] if c['id']==self.a['id'])['turns'][0]
        self.assertEqual(stored['review']['verification']['status'],'partial')
        t['status']='failed'
        with self.assertRaises(ValueError):self.app.record_review(self.a['id'],t['id'],THREAD,review())
        t['status']='completed'
        t['response']=''
        with self.assertRaises(ValueError):self.app.record_review(self.a['id'],t['id'],THREAD,review())
        t['status']='running'
        with self.assertRaises(ConflictError):self.app.record_review(self.a['id'],t['id'],THREAD,review('not_run'))

    def test_review_validation_and_reload_preflight_leave_service_running_on_failure(self):
        t=self.submit(self.a,'review');self.wait(lambda:t['status']=='completed')
        for changed in ({'items':[]},{'verification':{'status':'invented','details':'x'}},{'summary':'x'*1001}):
            value=copy.deepcopy(review());value.update(changed)
            with self.assertRaises(ValueError):self.app.record_review(self.a['id'],t['id'],THREAD,value)
        self.app.reload_callback=lambda:None
        with patch('server.validate_sources',side_effect=ValueError('invalid update')):
            with self.assertRaises(ValueError):self.app.prepare_reload()
        self.assertFalse(self.app.stopping);self.assertFalse(self.app.reloading)
        hold=self.holding()
        with self.assertRaises(ConflictError):self.app.prepare_reload()
        self.assertEqual(hold['status'],'running')
        self.app.stop(hold['id'])

    def test_bridge_review_records_results_with_pause_preserved_and_no_new_model_request(self):
        t=self.submit(self.a,'review');self.wait(lambda:t['status']=='completed')
        self.app.set_collaboration(self.a['id'],False)
        path=self.root/'review.json';path.write_text(json.dumps(review()))
        before=(self.root/'executed.jsonl').read_bytes()
        httpd=ThreadingHTTPServer(('127.0.0.1',0),Handler);httpd.app=self.app
        thread=threading.Thread(target=httpd.serve_forever,daemon=True);thread.start()
        try:
            run=subprocess.run([sys.executable,str(Path(__file__).parent/'bridge.py'),'--url','http://127.0.0.1:'+str(httpd.server_port),
                                '--thread-id',THREAD,'--review-turn',t['id'],'--review-file',str(path)],capture_output=True,text=True,timeout=8)
            self.assertEqual(run.returncode,0,run.stderr);result=json.loads(run.stdout)
            self.assertTrue(result['recorded']);self.assertFalse(result['model_called'])
            self.assertFalse(self.app.conversation(self.a['id'])['gpt_collaboration'])
            self.assertEqual((self.root/'executed.jsonl').read_bytes(),before)
            self.assertEqual(t['review']['verification']['status'],'passed')
        finally:httpd.shutdown();httpd.server_close();thread.join(3)

    def test_stale_internal_queue_entry_is_rejected_without_reordering(self):
        hold=self.holding();a=self.submit(self.a,'a');b=self.submit(self.b,'b')
        before=self.app.jobs.snapshot()
        with self.app.lock:
            with self.assertRaises(ValueError):self.app.jobs.move(b['id'],'up',{b['id']:(self.b['id'],'main')})
        self.assertEqual(self.app.jobs.snapshot(),before)
        self.app.stop(a['id']);self.app.stop(b['id']);self.app.stop(hold['id'])

    def test_identity_thread_exit_invalidates_cached_reload_availability(self):
        alive=[True]
        class Check:
            def is_alive(self):return alive[0]
        self.app.health_thread=Check();self.app.reload_callback=lambda:None
        try:
            tag=self.app.state_etag();self.assertFalse(self.app.service_info()['can_reload'])
            alive[0]=False
            self.assertNotEqual(tag,self.app.state_etag());self.assertTrue(self.app.service_info()['can_reload'])
        finally:self.app.health_thread=None


if __name__=='__main__':unittest.main(verbosity=2)
