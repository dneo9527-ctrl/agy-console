"""Recoverable history management; local fixtures only, no real model calls."""
import http.client
import json
from pathlib import Path
import tempfile
import threading
import time
import unittest

from server import Console, DEFAULT_MODEL, Handler, ThreadingHTTPServer
from test_server import FAKE

SID = '11111111-2222-3333-4444-555555555555'


class HistoryTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        root = Path(self.temp.name)
        binary = root / 'fake-agy'
        binary.write_text(FAKE)
        binary.chmod(0o700)
        self.app = Console(root / 'data', binary, import_history=False,
                           account_reader=lambda *_a, **_k: {'state': 'unknown'})
        self.conv = self.app.create('论文草稿')

    def tearDown(self):
        self.app.close()
        self.temp.cleanup()

    def test_rename_survives_first_message_and_restart(self):
        self.app.manage_history('panel', self.conv['id'], 'rename', '研究计划')
        turn = self.app.submit(self.conv['id'], 'first', DEFAULT_MODEL)
        deadline = time.monotonic() + 4
        while turn['status'] in ('queued', 'running') and time.monotonic() < deadline:
            time.sleep(.02)
        self.assertEqual(turn['status'], 'completed')
        self.assertEqual(self.conv['title'], '研究计划')
        self.app.close()
        restored = Console(self.app.data, self.app.binary, import_history=False)
        try:
            self.assertEqual(restored.conversation(self.conv['id'])['title'], '研究计划')
        finally:
            restored.close()

    def test_recycle_restores_messages_images_and_does_not_delete_artifacts(self):
        self.conv['agy_id'] = SID
        turn = {'id': 'a' * 32, 'status': 'completed', 'prompt': '问题',
                'response': '保留的答复', 'images': [{'id': 'b' * 64}], 'source': 'fixture'}
        self.conv['turns'].append(turn)
        artifact = self.app.data / 'retained-image.png'
        artifact.write_bytes(b'LOCAL-IMAGE-FIXTURE')
        self.app.manage_history('panel', self.conv['id'], 'delete')
        snap = self.app.snapshot()
        self.assertEqual(snap['conversations'], [])
        self.assertEqual(snap['trash'][0]['agy_id'], SID)
        self.assertNotIn('turns', snap['trash'][0])
        self.assertIsNone(self.app.conversation(self.conv['id']))
        with self.assertRaises(ValueError):
            self.app.submit(self.conv['id'], 'cannot resume deleted', DEFAULT_MODEL)
        with self.assertRaises(ValueError):
            self.app.manage_history('panel', self.conv['id'], 'rename', 'cannot rename deleted')
        self.app.manage_history('panel', self.conv['id'], 'restore')
        self.assertEqual(self.app.snapshot()['trash'], [])
        self.assertEqual(self.app.conversation(self.conv['id'])['turns'], [turn])
        self.assertEqual(artifact.read_bytes(), b'LOCAL-IMAGE-FIXTURE')

    def test_external_settings_are_local_only_and_do_not_allow_deletion(self):
        official = Path(self.temp.name) / 'official-fixture.db'
        official.write_bytes(b'OFFICIAL-READ-ONLY-FIXTURE')
        self.app.manage_history('external', SID, 'rename', '另一个窗口')
        self.app.manage_history('external', SID, 'hide')
        settings = self.app.snapshot()['history_overrides']['external:' + SID]
        self.assertEqual(settings, {'title': '另一个窗口', 'hidden': True})
        self.app.manage_history('external', SID, 'unhide')
        self.assertNotIn('hidden', self.app.snapshot()['history_overrides']['external:' + SID])
        for action in ('delete', 'restore'):
            with self.assertRaises(ValueError):
                self.app.manage_history('external', SID, action)
        self.assertEqual(official.read_bytes(), b'OFFICIAL-READ-ONLY-FIXTURE')

    def test_active_panel_tasks_cannot_be_hidden_or_deleted(self):
        for status in ('queued', 'running', 'cancelling'):
            self.conv['turns'] = [{'id': status, 'status': status}]
            for action in ('delete', 'hide'):
                with self.assertRaises(ValueError):
                    self.app.manage_history('panel', self.conv['id'], action)
            self.assertIsNotNone(self.app.conversation(self.conv['id']))
        self.conv['turns'] = []

    def test_cancelled_queued_entry_can_be_deleted_without_killing_worker(self):
        with self.app.lock:
            self.conv['turns'] = [{'id': 'cancelled', 'status': 'cancelled'}]
            self.app.jobs.put((self.conv['id'], 'cancelled'))
            self.app.manage_history('panel', self.conv['id'], 'delete')
        other = self.app.create()
        turn = self.app.submit(other['id'], 'first', DEFAULT_MODEL)
        deadline = time.monotonic() + 4
        while turn['status'] in ('queued', 'running') and time.monotonic() < deadline:
            time.sleep(.02)
        self.assertEqual(turn['status'], 'completed')
        self.assertTrue(self.app.worker_thread.is_alive())

    def test_invalid_actions_and_titles_leave_state_unchanged(self):
        before = self.app.snapshot()
        for source, cid, action, title in [
            ('other', SID, 'hide', None), ('external', '../../secrets', 'hide', None),
            ('panel', self.conv['id'], 'erase', None),
            ('panel', self.conv['id'], 'rename', ''),
            ('panel', self.conv['id'], 'rename', 'a' * 61),
            ('panel', self.conv['id'], 'rename', {'bad': 'type'}),
        ]:
            with self.assertRaises(ValueError):
                self.app.manage_history(source, cid, action, title)
        self.assertEqual(before, self.app.snapshot())

    def test_management_http_requires_same_origin_and_token(self):
        httpd = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
        httpd.app = self.app
        threading.Thread(target=httpd.serve_forever, daemon=True).start()
        def request(headers):
            client = http.client.HTTPConnection('127.0.0.1', httpd.server_port, timeout=3)
            try:
                client.request('POST', '/api/history', json.dumps({'source': 'panel',
                               'id': self.conv['id'], 'action': 'delete'}), headers)
                response = client.getresponse()
                response.read()
                return response.status
            finally:
                client.close()
        try:
            headers = {'Content-Type': 'application/json', 'X-Console-Token': self.app.token}
            self.assertEqual(request({'Content-Type': 'application/json'}), 403)
            self.assertEqual(request(dict(headers, Origin='https://untrusted.example')), 403)
            self.assertIsNotNone(self.app.conversation(self.conv['id']))
            self.assertEqual(request(headers), 200)
            self.assertIsNone(self.app.conversation(self.conv['id']))
        finally:
            httpd.shutdown()
            httpd.server_close()


if __name__ == '__main__':
    unittest.main(verbosity=2)
