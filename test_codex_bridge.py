"""Persistent binding and visible dispatch; fake CLI only, no model requests."""
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
from server import BUSY, Console, DEFAULT_MODEL, Handler, ThreadingHTTPServer
from test_server import FAKE

THREAD = '11111111-2222-3333-4444-555555555555'
OTHER = '66666666-7777-8888-9999-aaaaaaaaaaaa'
SESSION = 'bbbbbbbb-cccc-dddd-eeee-ffffffffffff'


class BindingTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(); self.root = Path(self.temp.name)
        self.binary = self.root/'fake-agy'; self.binary.write_text(FAKE); self.binary.chmod(0o700)
        # These contracts exercise bindings and the worker, not public update
        # discovery. Keep unrelated network workers out of the temporary profile.
        updates = patch('server.SoftwareUpdates.start')
        updates.start()
        self.addCleanup(updates.stop)
        if os.name == 'nt':
            # Windows cannot execute a shebang fixture. Use the real interpreter
            # for this exact fake file while retaining native pipes and cleanup.
            original_popen = subprocess.Popen
            def launch_fixture(command, *args, **kwargs):
                if isinstance(command, (list, tuple)) and command and str(command[0]) == str(self.binary):
                    command = [sys.executable, str(self.binary), *command[1:]]
                return original_popen(command, *args, **kwargs)
            fake_cli = patch('subprocess.Popen', side_effect=launch_fixture)
            fake_cli.start()
            self.addCleanup(fake_cli.stop)
        self.app = Console(self.root/'data', self.binary, import_history=False,
                           account_reader=lambda *_a,**_kw:{'state':'unknown','email':None})

    def tearDown(self):
        self.app.close(); self.temp.cleanup()

    def wait(self, turn):
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline:
            # Observe terminal state after the worker's locked persistence and
            # guard handling, just as API readers do.
            with self.app.lock:
                if turn['status'] not in BUSY:
                    return turn
            time.sleep(.02)
        with self.app.lock:
            self.assertNotIn(turn['status'], BUSY)
        return turn

    def test_repeated_binding_persists_and_different_threads_stay_separate(self):
        first = self.app.bind_codex(THREAD, '固定聊天')
        self.assertEqual(first['id'], self.app.bind_codex(THREAD)['id'])
        second = self.app.bind_codex(OTHER)
        self.assertNotEqual(first['id'], second['id'])
        self.app.close()
        self.app = Console(self.root/'data', self.binary, import_history=False)
        self.assertEqual(self.app.bind_codex(THREAD)['id'], first['id'])
        self.assertEqual(len(self.app.state['conversations']), 2)

    def test_queued_calls_reuse_actual_previous_session(self):
        conv = self.app.bind_codex(THREAD, '固定聊天')
        first = self.app.submit(conv['id'], 'first', DEFAULT_MODEL)
        second = self.app.submit(conv['id'], 'followup', DEFAULT_MODEL)
        self.wait(first); self.wait(second)
        self.assertEqual(first['status'], 'completed')
        self.assertEqual(second['status'], 'completed', second.get('error'))
        self.assertEqual(second['expected_agy_id'], 'session-test-42')
        self.assertEqual(self.app.conversation(conv['id'])['title'], '固定聊天')
        self.assertEqual(len(self.app.state['conversations']), 1)

    def test_mismatched_upstream_session_does_not_replace_binding(self):
        conv = self.app.bind_codex(THREAD, agy_id=SESSION)
        code = FAKE.replace("session = args[args.index('--conversation')+1] if '--conversation' in args else 'session-test-42'", "session = 'unexpected-session'")
        self.binary.write_text(code)
        turn = self.app.submit(conv['id'], 'first', DEFAULT_MODEL); self.wait(turn)
        self.assertEqual(turn['status'], 'failed')
        self.assertEqual(self.app.conversation(conv['id'])['agy_id'], SESSION)
        self.assertIn('原绑定已保留', turn['error'])

    def test_import_is_idempotent_and_cannot_reassign_another_threads_session(self):
        seed = {'prompt':'原始任务', 'response':'原始真实回复'}
        first = self.app.bind_codex(THREAD, agy_id=SESSION, initial_turn=seed)
        again = self.app.bind_codex(THREAD, agy_id=SESSION, initial_turn=seed)
        self.assertEqual(len(again['turns']), 1)
        with self.assertRaises(ValueError):self.app.bind_codex(OTHER, agy_id=SESSION)
        with self.assertRaises(ValueError):self.app.bind_codex(THREAD, agy_id=OTHER)
        self.app.manage_history('panel',first['id'],'delete')
        self.assertEqual(self.app.bind_codex(THREAD)['id'], first['id'])

    def test_staged_import_survives_restart_without_duplicate_chat(self):
        self.app.close()
        folder=self.root/'data/pending-codex';folder.mkdir()
        pending=folder/(THREAD+'.json')
        payload={'thread_id':THREAD,'title':'导入的真实审查','agy_id':SESSION,
                 'initial_turn':{'prompt':'任务','response':'回复'}}
        pending.write_text(json.dumps(payload))
        self.app=Console(self.root/'data',self.binary,import_history=False)
        self.assertFalse(pending.exists())
        self.assertEqual(self.app.bind_codex(THREAD)['agy_id'],SESSION)

    def test_http_client_uses_fixed_binding_and_targeted_detail_endpoint(self):
        httpd=ThreadingHTTPServer(('127.0.0.1',0),Handler);httpd.app=self.app
        worker=threading.Thread(target=httpd.serve_forever,daemon=True);worker.start()
        try:
            client=Client('http://127.0.0.1:'+str(httpd.server_port))
            first=client.bind(THREAD);second=client.bind(THREAD)
            self.assertEqual(first['id'],second['id'])
            turn=client.request('/api/conversations/'+first['id']+'/turns',{'prompt':'hello','model':DEFAULT_MODEL})
            conv,result=client.wait(first['id'],turn['id'],5)
            self.assertEqual(result['status'],'completed')
            self.assertEqual(conv['codex_thread_id'],THREAD)
        finally:
            httpd.shutdown();httpd.server_close();worker.join(3)

    def test_long_stderr_remains_on_disk_and_early_denial_is_not_forgotten(self):
        self.binary.write_text(FAKE.replace("if prompt == 'fail':", "if prompt == 'long-log':\n    os.write(2,b'tool execution denied\\n'+b'x'*200000)\nif prompt == 'fail':"))
        conv=self.app.create();turn=self.app.submit(conv['id'],'long-log',DEFAULT_MODEL);self.wait(turn)
        self.assertEqual(turn['status'],'failed')
        logs=list((self.root/'data/runs').glob('*/stderr.log'))
        self.assertEqual(len(logs),1)
        self.assertGreater(logs[0].stat().st_size,200000)
        self.assertTrue(logs[0].read_bytes().startswith(b'tool execution denied'))

    def test_new_chat_ensure_uses_environment_id_and_does_not_call_model(self):
        httpd=ThreadingHTTPServer(('127.0.0.1',0),Handler);httpd.app=self.app
        worker=threading.Thread(target=httpd.serve_forever,daemon=True);worker.start()
        try:
            results=[]
            for thread_id in (THREAD,THREAD,OTHER):
                command=[sys.executable,str(Path(__file__).with_name('bridge.py')),'--ensure','--url','http://127.0.0.1:'+str(httpd.server_port)]
                output=subprocess.check_output(command,env=dict(os.environ,CODEX_THREAD_ID=thread_id),text=True,timeout=5)
                results.append(json.loads(output))
            self.assertEqual(results[0]['console_conversation_id'],results[1]['console_conversation_id'])
            self.assertNotEqual(results[0]['console_conversation_id'],results[2]['console_conversation_id'])
            self.assertTrue(all(r['model_started'] is False for r in results))
            self.assertEqual(len(self.app.state['conversations']),2)
            self.assertTrue(all(not c['turns'] for c in self.app.state['conversations']))
            self.assertFalse(self.app.processes)
        finally:
            httpd.shutdown();httpd.server_close();worker.join(3)

    def test_unchanged_state_returns_no_body_and_changes_invalidate_etag(self):
        import http.client
        httpd=ThreadingHTTPServer(('127.0.0.1',0),Handler);httpd.app=self.app
        worker=threading.Thread(target=httpd.serve_forever,daemon=True);worker.start()
        def get(tag=None):
            client=http.client.HTTPConnection('127.0.0.1',httpd.server_port,timeout=3)
            try:
                client.request('GET','/api/state',headers={'If-None-Match':tag} if tag else {})
                response=client.getresponse();return response.status,response.getheader('ETag'),response.read()
            finally:client.close()
        try:
            status,tag,body=get();self.assertEqual(status,200);self.assertTrue(body)
            self.assertEqual(get(tag),(304,tag,b''))
            self.app.bind_codex(THREAD)
            changed,new_tag,_=get(tag);self.assertEqual(changed,200);self.assertNotEqual(new_tag,tag)
            self.assertEqual(get(new_tag)[0],304)
            self.app.health['account']={'state':'checking'}
            self.assertEqual(get(new_tag)[0],200)
        finally:
            httpd.shutdown();httpd.server_close();worker.join(3)


if __name__=='__main__':unittest.main(verbosity=2)
