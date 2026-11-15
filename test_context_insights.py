"""Session-scoped facts, no transcript exposure or inferred compression rates."""
import copy
import http.client
import json
from pathlib import Path
import tempfile
import threading
import unittest

from settings_insights import context_insights
from server import Console, Handler, ThreadingHTTPServer
from test_server import FAKE


class ContextTests(unittest.TestCase):
    def setUp(self):
        self.conv = {'id': 'a' * 32, 'primary_branch': 'main',
                     'branches': {'main': {'title': '文字'}, 'image': {'title': '图片'}},
                     'turns': []}

    def add(self, prompt='task', branch='main', status='completed', **extra):
        turn = {'id': format(len(self.conv['turns']), '032x'), 'prompt': prompt,
                'response': 'PRIVATE RESPONSE', 'branch': branch, 'status': status,
                'created_at': '2026-11-14T00:00:00Z', **extra}
        self.conv['turns'].append(turn)
        return turn

    def test_only_completed_explicit_command_in_selected_session_counts(self):
        self.add('请说明 /compact；我已压缩上下文')
        self.add('/compact', status='failed')
        self.add('/compact', error='not completed')
        compact = self.add(' /compact focus on decisions')
        self.add('/compact', branch='image')
        self.add('/compactly')
        self.add('/compact', status='running')
        result = context_insights(self.conv, 'main')
        self.assertEqual(result['turn_count'], 6)
        self.assertEqual(result['manual_compaction']['turn_id'], compact['id'])
        self.assertEqual(result['manual_compaction']['turns_after'], 2)
        self.assertIsNone(result['context_percent'])
        self.assertIsNone(result['automatic_compactions'])
        raw = json.dumps(result)
        self.assertNotIn('PRIVATE', raw)
        self.assertNotIn('focus on decisions', raw)

    def test_latest_usage_retains_scope_including_failed_round_and_sanitizes_counts(self):
        self.add(usage={'input_tokens': 100}, usage_scope='conversation')
        latest = self.add(status='failed', usage={'input_tokens': 3, 'output_tokens': 0,
                           'cache_read_tokens': 9, 'total_tokens': float('nan'),
                           'extra': 'SECRET', 'bad': True}, usage_scope='turn')
        self.add(branch='image', usage={'input_tokens': 99999})
        self.add(status='running', usage={'input_tokens': 999})
        before = copy.deepcopy(self.conv)
        result = context_insights(self.conv, 'main')['latest_usage']
        self.assertEqual(result['turn_id'], latest['id'])
        self.assertEqual(result['status'], 'failed')
        self.assertEqual(result['usage_scope'], 'turn')
        self.assertEqual(result['usage'], {'input_tokens': 3, 'output_tokens': 0, 'cache_read_tokens': 9})
        self.assertEqual(self.conv, before)

    def test_empty_or_unreported_fields_remain_unknown(self):
        self.add(usage={'input_tokens': True, 'output_tokens': -1, 'total_tokens': float('inf')})
        value = context_insights(self.conv, 'main')
        self.assertIsNone(value['manual_compaction'])
        self.assertIsNone(value['latest_usage'])


class ContextHttpTests(unittest.TestCase):
    def test_scoped_endpoint_defaults_to_primary_and_does_not_touch_history(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder); binary = root / 'agy'
            binary.write_text(FAKE); binary.chmod(0o700)
            app = Console(root / 'data', binary, import_history=False,
                          account_reader=lambda *_a, **_k: {'state': 'unknown', 'email': None})
            conv = app.bind_codex('11111111-2222-3333-4444-555555555555', 'test')
            empty = app.context_snapshot(conv['id'])
            self.assertEqual(empty['turn_count'], 0)
            self.assertEqual(empty['branch'], 'main')
            app.create_branch(conv['id'], 'Primary', key='main')
            app.create_branch(conv['id'], 'Separate', key='separate')
            stored = app.conversation(conv['id'])
            stored['turns'] = [{'id': '1' * 32, 'branch': stored['primary_branch'], 'prompt': '/compact', 'status': 'completed'},
                               {'id': '2' * 32, 'branch': 'separate', 'prompt': 'PRIVATE', 'status': 'completed'}]
            before = copy.deepcopy(stored)
            httpd = ThreadingHTTPServer(('127.0.0.1', 0), Handler); httpd.app = app
            worker = threading.Thread(target=httpd.serve_forever, daemon=True); worker.start()
            def get(suffix):
                client = http.client.HTTPConnection('127.0.0.1', httpd.server_port, timeout=3)
                try:
                    client.request('GET', '/api/conversations/' + conv['id'] + '/context' + suffix)
                    response = client.getresponse(); return response.status, json.loads(response.read())
                finally:
                    client.close()
            try:
                status, data = get('')
                self.assertEqual(status, 200); self.assertEqual(data['branch'], stored['primary_branch'])
                self.assertIsNotNone(data['manual_compaction'])
                status, data = get('?branch=separate')
                self.assertEqual(status, 200); self.assertEqual(data['turn_count'], 1)
                self.assertIsNone(data['manual_compaction'])
                self.assertEqual(get('?branch=missing')[0], 404)
                self.assertEqual(get('?branch=INVALID')[0], 400)
                self.assertEqual(stored, before)
                self.assertFalse(app.processes)
            finally:
                httpd.shutdown(); httpd.server_close(); worker.join(3); app.close()


if __name__ == '__main__':
    unittest.main()
