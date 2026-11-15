"""Persistent per-chat presentation preferences; no real model or UI calls."""
import json
import os
from pathlib import Path
import subprocess
import sys
import threading
import unittest

from bridge import Client
from server import Console, Handler, ThreadingHTTPServer, ConflictError
import test_codex_bridge as fixtures


class AutoOpenTests(unittest.TestCase):
    setUp = fixtures.BindingTests.setUp
    tearDown = fixtures.BindingTests.tearDown

    def test_defaults_isolation_rebind_restart_and_collaboration_independence(self):
        a = self.app.bind_codex(fixtures.THREAD, agy_id=fixtures.SESSION)
        b = self.app.bind_codex(fixtures.OTHER)
        self.assertTrue(a['auto_open_activity'])
        self.app.set_auto_open_activity(a['id'], False)
        self.assertFalse(self.app.bind_codex(fixtures.THREAD)['auto_open_activity'])
        self.assertTrue(self.app.conversation(b['id'])['auto_open_activity'])
        self.assertTrue(self.app.conversation(a['id'])['gpt_collaboration'])
        self.assertEqual(self.app.conversation(a['id'])['agy_id'], fixtures.SESSION)
        self.assertFalse(self.app.processes)
        self.assertEqual(self.app.conversation(a['id'])['turns'], [])
        self.app.close()
        self.app = Console(self.root/'data', self.binary, import_history=False)
        self.assertFalse(self.app.bind_codex(fixtures.THREAD)['auto_open_activity'])
        self.app.set_auto_open_activity(a['id'], True)
        self.assertTrue(self.app.conversation(a['id'])['auto_open_activity'])

    def test_invalid_inputs_unbound_chats_and_maintenance_rejected(self):
        a = self.app.bind_codex(fixtures.THREAD)
        for value in (None, 'false', 0, 1, {}, []):
            with self.assertRaises(ValueError):self.app.set_auto_open_activity(a['id'], value)
        with self.assertRaises(ValueError):self.app.set_auto_open_activity(self.app.create()['id'], False)
        self.app.maintenance = True
        with self.assertRaises(ConflictError):self.app.set_auto_open_activity(a['id'], False)
        self.app.maintenance = False
        self.assertTrue(self.app.conversation(a['id'])['auto_open_activity'])

    def test_http_bridge_off_is_noop_presentation_but_dispatch_still_runs_same_id(self):
        httpd = ThreadingHTTPServer(('127.0.0.1', 0), Handler);httpd.app = self.app
        worker = threading.Thread(target=httpd.serve_forever, daemon=True);worker.start()
        base = [sys.executable, str(Path(__file__).with_name('bridge.py')), '--url', 'http://127.0.0.1:'+str(httpd.server_port)]
        env = dict(os.environ, CODEX_THREAD_ID=fixtures.THREAD)
        try:
            self.app.bind_codex(fixtures.THREAD, agy_id=fixtures.SESSION)
            off = json.loads(subprocess.check_output(base+['--ensure','--auto-open','off'], env=env, text=True, timeout=5))
            self.assertFalse(off['auto_open_activity']);self.assertTrue(off['auto_open_supported'])
            self.assertEqual(off['panel_action']['type'], 'none');self.assertFalse(off['panel_action']['enabled'])
            self.assertFalse(off['model_started']);self.assertFalse(off['panel_action']['verified_visible'])
            repeat = json.loads(subprocess.check_output(base+['--ensure'], env=env, text=True, timeout=5))
            self.assertFalse(repeat['panel_action']['enabled'])
            task = self.root/'task.txt';task.write_text('first')
            output = subprocess.check_output(base+['--task-file',str(task),'--timeout','8'], env=env, text=True, timeout=12)
            sent, result = map(json.loads, output.splitlines())
            self.assertEqual(sent['panel_action']['type'], 'none')
            self.assertFalse(sent['panel_action']['enabled']);self.assertTrue(result['completed'])
            self.assertEqual(result['conversation_id'], fixtures.SESSION)
            on = json.loads(subprocess.check_output(base+['--ensure','--auto-open','on'], env=env, text=True, timeout=5))
            self.assertEqual(on['panel_action']['type'], 'show_existing_browser');self.assertTrue(on['panel_action']['enabled'])
            self.assertEqual(on['panel_action']['url'], off['panel_action']['url'])
            self.assertEqual(len(self.app.state['conversations']), 1)
            self.assertEqual(len(self.app.state['conversations'][0]['turns']), 1)
        finally:httpd.shutdown();httpd.server_close();worker.join(3)

    def test_endpoint_requires_existing_csrf_protection(self):
        import http.client
        a = self.app.bind_codex(fixtures.THREAD)
        httpd = ThreadingHTTPServer(('127.0.0.1', 0), Handler);httpd.app = self.app
        worker = threading.Thread(target=httpd.serve_forever, daemon=True);worker.start()
        conn = http.client.HTTPConnection('127.0.0.1', httpd.server_port)
        try:
            path = '/api/conversations/'+a['id']+'/auto-open'
            conn.request('POST', path, json.dumps({'enabled':False}), {'Content-Type':'application/json'})
            reply = conn.getresponse();reply.read();self.assertEqual(reply.status, 403)
            self.assertTrue(self.app.conversation(a['id'])['auto_open_activity'])
            client = Client('http://127.0.0.1:'+str(httpd.server_port))
            self.assertFalse(client.request(path, {'enabled':False})['enabled'])
        finally:conn.close();httpd.shutdown();httpd.server_close();worker.join(3)


if __name__ == '__main__':unittest.main()
