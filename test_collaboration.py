"""Per-fixed-chat collaboration switch, real local HTTP/CLI, fake Ag only."""
import json
import os
from pathlib import Path
import subprocess
import sys
import threading
import unittest
from urllib.error import HTTPError
from urllib.request import Request, build_opener, ProxyHandler

from bridge import Client
from server import Console, DEFAULT_MODEL, Handler, ThreadingHTTPServer
import test_codex_bridge as fixtures


class CollaborationTests(unittest.TestCase):
    setUp = fixtures.BindingTests.setUp
    tearDown = fixtures.BindingTests.tearDown
    wait = fixtures.BindingTests.wait

    def test_defaults_on_and_pause_survives_bind_restart_without_changing_identity(self):
        first = self.app.bind_codex(fixtures.THREAD, agy_id=fixtures.SESSION)
        second = self.app.bind_codex(fixtures.OTHER)
        self.assertTrue(first['gpt_collaboration'])
        self.app.set_collaboration(first['id'], False)
        self.assertFalse(self.app.bind_codex(fixtures.THREAD)['gpt_collaboration'])
        self.assertTrue(self.app.conversation(second['id'])['gpt_collaboration'])
        self.app.close()
        self.app = Console(self.root/'data', self.binary, import_history=False,
                           account_reader=lambda *_a, **_k: {'state': 'unknown'})
        restored = self.app.bind_codex(fixtures.THREAD)
        self.assertEqual(restored['id'], first['id'])
        self.assertEqual(restored['agy_id'], fixtures.SESSION)
        self.assertFalse(restored['gpt_collaboration'])
        self.assertFalse(restored['turns'])

    def test_no_model_started_or_empty_work_sessions_created_by_switch(self):
        conv = self.app.bind_codex(fixtures.THREAD)
        for enabled in (False, True, False):
            self.assertEqual(self.app.set_collaboration(conv['id'], enabled)['enabled'], enabled)
        stored = self.app.conversation(conv['id'])
        self.assertFalse(stored['turns']); self.assertFalse(stored['branches'])
        self.assertIsNone(stored['agy_id']); self.assertFalse(self.app.processes)

    def test_strict_values_and_bound_chat_required(self):
        conv = self.app.bind_codex(fixtures.THREAD)
        for value in (None, 0, 1, 'false', {}, []):
            with self.assertRaises(ValueError): self.app.set_collaboration(conv['id'], value)
        standalone = self.app.create()
        with self.assertRaises(ValueError): self.app.set_collaboration(standalone['id'], True)
        with self.assertRaises(ValueError): self.app.set_collaboration('0'*32, True)

    def test_paused_rejects_codex_but_web_manual_still_continues_same_session(self):
        conv = self.app.bind_codex(fixtures.THREAD)
        initial = self.app.submit(conv['id'], 'first', DEFAULT_MODEL, codex_dispatch=True); self.wait(initial)
        sid = initial['agy_id']; self.assertEqual(initial['source'], 'codex')
        self.app.set_collaboration(conv['id'], False)
        with self.assertRaises(ValueError): self.app.submit(conv['id'], 'blocked', DEFAULT_MODEL, codex_dispatch=True)
        self.assertEqual(len(self.app.conversation(conv['id'])['turns']), 1)
        manual = self.app.submit(conv['id'], 'followup', DEFAULT_MODEL); self.wait(manual)
        self.assertEqual(manual['status'], 'completed'); self.assertEqual(manual['agy_id'], sid)
        self.assertEqual(manual['source'], 'console')

    def test_switch_does_not_cancel_existing_task_or_change_another_chat_revision(self):
        conv = self.app.bind_codex(fixtures.THREAD); other = self.app.bind_codex(fixtures.OTHER)
        before = next(c['revision'] for c in self.app.snapshot(True)['conversations'] if c['id'] == other['id'])
        turn = self.app.submit(conv['id'], 'slow', DEFAULT_MODEL, codex_dispatch=True)
        self.app.set_collaboration(conv['id'], False)
        self.assertIn(turn['status'], {'queued', 'running'})
        after = next(c['revision'] for c in self.app.snapshot(True)['conversations'] if c['id'] == other['id'])
        self.assertEqual(before, after)
        self.app.stop(turn['id']); self.wait(turn)

    def test_http_csrf_and_server_recheck_prevent_dispatch_after_switch(self):
        httpd = ThreadingHTTPServer(('127.0.0.1', 0), Handler); httpd.app = self.app
        worker = threading.Thread(target=httpd.serve_forever, daemon=True); worker.start()
        try:
            client = Client('http://127.0.0.1:'+str(httpd.server_port)); conv = client.bind(fixtures.THREAD)
            self.assertTrue(client.features['gpt_collaboration'])
            path = '/api/conversations/'+conv['id']+'/collaboration'
            opener = build_opener(ProxyHandler({}))
            for headers in ({'Content-Type': 'application/json'}, {'Content-Type': 'application/json', 'X-Console-Token': self.app.token, 'Origin': 'https://example.com'}):
                with self.assertRaises(HTTPError) as caught:
                    opener.open(Request(client.url+path, data=b'{"enabled":false}', headers=headers), timeout=2)
                self.assertEqual(caught.exception.code, 403)
            value = client.request(path, {'enabled': False}); self.assertFalse(value['enabled'])
            with self.assertRaises(ValueError): client.request('/api/conversations/'+conv['id']+'/turns', {'prompt': 'race', 'model': DEFAULT_MODEL, 'codex_dispatch': True})
            self.assertFalse(self.app.conversation(conv['id'])['turns'])
        finally:
            httpd.shutdown(); httpd.server_close(); worker.join(3)

    def test_bridge_ensure_inspects_and_toggles_and_paused_dispatch_starts_no_model(self):
        httpd = ThreadingHTTPServer(('127.0.0.1', 0), Handler); httpd.app = self.app
        worker = threading.Thread(target=httpd.serve_forever, daemon=True); worker.start()
        try:
            command = [sys.executable, str(Path(__file__).with_name('bridge.py')), '--url', 'http://127.0.0.1:'+str(httpd.server_port)]
            def run(*args):
                return subprocess.run(command+list(args), env=dict(os.environ, CODEX_THREAD_ID=fixtures.THREAD), capture_output=True, text=True, timeout=8)
            pause = run('--ensure', '--collaboration', 'off'); self.assertEqual(pause.returncode, 0, pause.stderr)
            paused = json.loads(pause.stdout); self.assertFalse(paused['collaboration_enabled']); self.assertFalse(paused['model_started'])
            inspect = json.loads(run('--ensure').stdout); self.assertFalse(inspect['collaboration_enabled'])
            task = self.root/'prompt.txt'; task.write_text('first')
            denied = run('--task-file', str(task)); self.assertEqual(denied.returncode, 1); self.assertIn('暂停', denied.stderr)
            self.assertFalse(self.app.conversation(paused['console_conversation_id'])['turns'])
            resumed = json.loads(run('--ensure', '--collaboration', 'on').stdout)
            self.assertTrue(resumed['collaboration_enabled']); self.assertEqual(resumed['console_conversation_id'], paused['console_conversation_id'])
            done = run('--task-file', str(task), '--timeout', '5')
            self.assertEqual(done.returncode, 0, done.stderr)
            self.assertTrue(json.loads(done.stdout.splitlines()[-1])['completed'])
            self.assertEqual(len(self.app.state['conversations']), 1)
        finally:
            httpd.shutdown(); httpd.server_close(); worker.join(3)


if __name__ == '__main__': unittest.main()
