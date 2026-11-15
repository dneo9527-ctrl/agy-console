"""Behavior checks using a fake executable; no model requests or credentials."""
import http.client
import json
from pathlib import Path
import tempfile
import threading
import time
import unittest

from server import BUSY, Console, DEFAULT_MODEL, Handler, ThreadingHTTPServer


FAKE = '''#!/usr/bin/python3
import json, os, sys, time
from pathlib import Path
args = sys.argv[1:]
prompt = args[args.index('-p') + 1]
def send(event):
    raw = (json.dumps(event, ensure_ascii=False) + '\\n').encode()
    for start in range(0, len(raw), 7):
        os.write(1, raw[start:start+7])
session = args[args.index('--conversation')+1] if '--conversation' in args else 'session-test-42'
send({'event':'init', 'conversation_id':session})
if prompt == 'followup':
    assert '--conversation' in args and session == 'session-test-42'
if prompt == 'slow':
    send({'event':'step_update', 'step_update':{'text_delta':'保留部分输出'}})
    time.sleep(30)
if prompt == 'fail':
    print('upstream unavailable', file=sys.stderr)
    sys.exit(2)
if prompt == 'denied':
    print('tool execution denied', file=sys.stderr)
response = '' if prompt == 'empty' else '中文回复：' + prompt
send({'event':'step_update', 'step_update':{'text_delta':response}})
send({'event':'result', 'result':{'status':'SUCCESS', 'response':response,
    'conversation_id':session, 'usage':{'total_tokens':7}}})
'''


class BehaviorTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        root = Path(self.temp.name)
        self.binary = root / 'fake-agy'
        self.binary.write_text(FAKE)
        self.binary.chmod(0o700)
        self.app = Console(root / 'data', self.binary, import_history=False)
        self.conv = self.app.create()

    def tearDown(self):
        self.app.close()
        deadline = time.monotonic() + 5
        while self.app.processes and time.monotonic() < deadline:
            time.sleep(.02)
        self.temp.cleanup()

    def wait_turn(self, turn):
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline:
            # Match API readers: completed state is observed after its locked
            # persistence finishes, not between in-memory update and save.
            with self.app.lock:
                if turn['status'] not in BUSY:
                    return turn
            time.sleep(.02)
        with self.app.lock:
            self.assertNotIn(turn['status'], BUSY)
        return turn

    def submit(self, prompt):
        return self.app.submit(self.conv['id'], prompt, DEFAULT_MODEL)

    def test_unicode_stream_and_exact_followup_session(self):
        first = self.wait_turn(self.submit('first'))
        self.assertEqual(first['status'], 'completed')
        self.assertEqual(first['response'], '中文回复：first')
        self.assertEqual(first['progress'][-1]['label'], '任务完成')
        self.assertFalse(any(r['state'] == 'active' for r in first['progress']))
        self.assertEqual(self.app.snapshot(summaries=True)['conversations'][0]['turns'][-1]['progress'], first['progress'])
        self.assertEqual(self.conv['agy_id'], 'session-test-42')
        second = self.wait_turn(self.submit('followup'))
        self.assertEqual(second['status'], 'completed')
        self.assertEqual(second['usage']['total_tokens'], 7)
        persisted = json.loads(self.app.file.read_text())
        self.assertEqual(persisted['conversations'][0]['turns'][-1]['status'], 'completed')

    def test_nonzero_empty_and_denied_are_not_completed(self):
        for prompt in ('fail', 'empty', 'denied'):
            with self.subTest(prompt=prompt):
                turn = self.wait_turn(self.submit(prompt))
                self.assertEqual(turn['status'], 'failed')
                self.assertTrue(turn['error'])

    def test_stop_preserves_partial_output_and_allows_next_turn(self):
        turn = self.submit('slow')
        deadline = time.monotonic() + 5
        while not turn['response'] and time.monotonic() < deadline:
            time.sleep(.02)
        self.assertIn('保留', turn['response'])
        with self.assertRaises(ValueError):
            self.submit('duplicate')
        self.app.stop(turn['id'])
        self.wait_turn(turn)
        self.assertEqual(turn['status'], 'cancelled')
        self.assertFalse(any(r['state'] == 'active' for r in turn['progress']))
        self.assertIn('保留部分输出', turn['response'])
        self.assertEqual(self.wait_turn(self.submit('followup'))['status'], 'completed')

    def test_queued_cancel_never_runs(self):
        slow = self.submit('slow')
        other = self.app.create()
        queued = self.app.submit(other['id'], 'never', DEFAULT_MODEL)
        self.app.stop(queued['id'])
        self.assertEqual(queued['status'], 'cancelled')
        self.assertEqual(queued['progress'][-1]['state'], 'cancelled')
        self.app.stop(slow['id'])
        self.wait_turn(slow)
        self.assertFalse((self.app.data / 'runs' / queued['id']).exists())

    def test_restart_marks_unconfirmed_work_interrupted(self):
        saved = json.loads(self.app.file.read_text())
        saved['conversations'][0]['turns'] = [
            {'id':'pending', 'status':s, 'response':'partial'} for s in ('queued','running','cancelling')]
        self.app.file.write_text(json.dumps(saved))
        restored = Console(self.app.data, self.binary, import_history=False)
        try:
            self.assertTrue(all(t['status'] == 'interrupted' for t in restored.state['conversations'][0]['turns']))
        finally:
            restored.close()

    def test_http_origin_token_and_export_status(self):
        httpd = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
        httpd.app = self.app
        threading.Thread(target=httpd.serve_forever, daemon=True).start()
        port = httpd.server_port
        def request(method, path, body=None, headers=None):
            client = http.client.HTTPConnection('127.0.0.1', port, timeout=3)
            client.request(method, path, body, headers or {})
            response = client.getresponse()
            status, data = response.status, response.read()
            client.close()
            return status, data
        try:
            headers = {'Content-Type':'application/json', 'X-Console-Token':self.app.token}
            self.assertEqual(request('POST', '/api/conversations', '{}')[0], 403)
            self.assertEqual(request('POST', '/api/conversations', '{}', dict(headers, Origin='https://other.example'))[0], 403)
            self.assertEqual(request('GET', '/api/state', headers={'Host':'other.example'})[0], 403)
            self.assertEqual(request('POST', '/api/conversations', '{}', headers)[0], 201)
            self.assertEqual(request('GET', '/data/state.json')[0], 404)
            turn = self.wait_turn(self.submit('fail'))
            status, exported = request('GET', '/api/conversations/' + self.conv['id'] + '/export')
            self.assertEqual(status, 200)
            self.assertIn(b'failed', exported)
            self.assertNotIn(b'completed', exported)
        finally:
            httpd.shutdown()
            httpd.server_close()


if __name__ == '__main__':
    unittest.main(verbosity=2)
