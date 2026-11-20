"""Bounded account/model resync using fake readers; no login or model calls."""
import copy
import http.client
from pathlib import Path
import subprocess
import tempfile
import threading
import unittest

from server import Console, ConflictError, Handler, ThreadingHTTPServer


def identity(email='new@example.test'):
    return {'state': 'known', 'email': email, 'name': None,
            'source': 'fake official statusLine', 'checked_at': '2026-11-19T00:00:00Z',
            'quota': {'gemini-weekly': {'remaining_fraction': .8}}}


class AccountSyncTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.reads = []
        self.account = identity()
        def read(*args, **kwargs):
            self.reads.append(True)
            return copy.deepcopy(self.account)
        self.app = Console(Path(self.temp.name) / 'data', '/fake/agy', account_reader=read)
        self.conv = self.app.create()
        self.app.health.update(connected=True, checking=False, account=identity('old@example.test'))
        self.commands = []
        def command(args, timeout):
            self.commands.append((args, timeout))
            return subprocess.CompletedProcess(args, 0, 'flash\tFlash\nimage\tImage\n', '')
        self.app.health_command = command

    def tearDown(self):
        self.app.close()
        self.temp.cleanup()

    def finish(self):
        self.app.health_thread.join(timeout=3)
        self.assertFalse(self.app.health_thread.is_alive())
        self.assertFalse(self.app.health['account_syncing'])
        self.assertFalse(self.app.health['checking'])

    def test_sync_rechecks_identity_preserves_sessions_and_preferences(self):
        self.app.state['preferred_model'] = 'saved-global'
        self.conv['window_models'] = {'text': 'saved-text', 'image': 'saved-image'}
        before = copy.deepcopy(self.app.state)
        self.app.sync_account()
        self.finish()
        self.assertEqual(self.app.state, before)
        self.assertEqual(self.commands, [(['models'], 35)])
        self.assertEqual(len(self.reads), 2)
        self.assertEqual(self.app.health['account']['email'], 'new@example.test')
        self.assertEqual(self.app.health['models'][0]['id'], 'flash')
        self.assertTrue(self.app.health['connected'])
        self.assertTrue(self.app.health['account_sync_result']['changed'])
        self.assertTrue(self.app.service_info()['features']['account_sync'])

    def test_unchanged_account_is_ready_without_claiming_a_switch(self):
        self.app.health['account'] = identity()
        self.app.sync_account()
        self.finish()
        self.assertTrue(self.app.health['connected'])
        self.assertFalse(self.app.health['account_sync_result']['changed'])
        self.assertEqual(self.app.health['account_sync_result']['detail'], '账号未变，已同步')

    def test_busy_rejects_without_reading_or_starting_a_process(self):
        self.app.processes['busy'] = object()
        try:
            with self.assertRaises(ConflictError):
                self.app.sync_account()
            self.assertFalse(self.reads)
            self.assertFalse(self.commands)
        finally:
            self.app.processes.clear()

    def test_duplicate_sync_and_new_tasks_are_guarded(self):
        entered, release = threading.Event(), threading.Event()
        def command(args, timeout):
            self.commands.append(args)
            entered.set()
            release.wait(timeout=3)
            return subprocess.CompletedProcess(args, 0, 'flash\tFlash\n', '')
        self.app.health_command = command
        self.app.sync_account()
        try:
            self.assertTrue(entered.wait(timeout=2))
            self.assertTrue(self.app.sync_account()['already_syncing'])
            self.assertEqual(self.app.health['models'], [])
            self.assertFalse(self.app.health['connected'])
            with self.assertRaises(ConflictError):
                self.app.submit(self.conv['id'], 'new task', '')
            self.app.refresh_account()
            self.assertIsNone(self.app.account_thread)
        finally:
            release.set()
        self.finish()
        self.assertEqual(len(self.commands), 1)

    def test_identity_change_does_not_commit_other_accounts_model_list(self):
        def command(args, timeout):
            self.account = identity('changed-again@example.test')
            return subprocess.CompletedProcess(args, 0, 'flash\tFlash\n', '')
        self.app.health_command = command
        self.app.sync_account()
        self.finish()
        self.assertFalse(self.app.health['connected'])
        self.assertEqual(self.app.health['models'], [])
        self.assertEqual(self.app.health['account']['email'], 'changed-again@example.test')
        self.assertIn('账号发生变化', self.app.health['error'])

    def test_model_timeout_clears_stale_models_and_never_retries(self):
        def command(args, timeout):
            self.commands.append(args)
            raise subprocess.TimeoutExpired(args, timeout)
        self.app.health_command = command
        self.app.sync_account()
        self.finish()
        self.assertEqual(self.commands, [['models']])
        self.assertFalse(self.app.health['connected'])
        self.assertEqual(self.app.health['models'], [])
        self.assertNotEqual(self.app.health['account']['email'], 'old@example.test')
        self.assertTrue(self.app.health['error'])

    def test_signed_out_stops_before_model_list(self):
        self.account = {'state': 'signed_out', 'email': None, 'checked_at': None}
        self.app.sync_account()
        self.finish()
        self.assertFalse(self.commands)
        self.assertEqual(self.app.health['models'], [])
        self.assertEqual(self.app.health['account']['state'], 'signed_out')

    def test_sync_http_requires_origin_token_and_no_account_parameters(self):
        httpd = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
        httpd.app = self.app
        thread = threading.Thread(target=httpd.serve_forever, daemon=True)
        thread.start()
        def request(body='{}', headers=None):
            client = http.client.HTTPConnection('127.0.0.1', httpd.server_port, timeout=3)
            try:
                client.request('POST', '/api/account/sync', body, headers or {'Content-Type': 'application/json'})
                response = client.getresponse()
                response.read()
                return response.status
            finally:
                client.close()
        try:
            self.assertEqual(request(), 403)
            headers = {'Content-Type': 'application/json', 'X-Console-Token': self.app.token}
            self.assertEqual(request(headers=dict(headers, Origin='https://untrusted.example')), 403)
            self.assertEqual(request('{"email":"not-a-switch-request@example.test"}', headers), 400)
            self.assertFalse(self.reads)
            self.assertEqual(request(headers=headers), 202)
            self.finish()
        finally:
            httpd.shutdown()
            httpd.server_close()
            thread.join(timeout=3)


if __name__ == '__main__':
    unittest.main()
