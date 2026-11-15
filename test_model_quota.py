"""Only pick advertised models, preserve explicit choice and session routing."""
import json
import os
from pathlib import Path
import subprocess
import sys
import threading
import unittest

from bridge import DEFAULT_MODEL, default_model
from server import Handler, ThreadingHTTPServer
import test_codex_bridge as binding_fixtures

FLASH = 'gemini-3.8-flash-high'
SONNET = 'claude-sonnet-5-5-medium'


def snapshot(quota=None, state='known', models=None):
    return {'health': {'models': [{'id': m} for m in (models if models is not None else [DEFAULT_MODEL, FLASH, SONNET])],
                       'account': {'state': state, 'quota': quota or {}}}}


class ModelQuotaTests(unittest.TestCase):
    def test_default_preserved_until_known_exhausted(self):
        self.assertEqual(default_model(snapshot()), DEFAULT_MODEL)
        self.assertEqual(default_model(snapshot({'gemini-argon-wtu-weekly': {'remaining_fraction': .01}})), DEFAULT_MODEL)

    def test_exhausted_argon_uses_separate_available_group(self):
        q = {'gemini-argon-wtu-weekly': {'remaining_fraction': 0}}
        self.assertEqual(default_model(snapshot(q)), FLASH)
        self.assertEqual(default_model(snapshot(q, state='checking')), FLASH)
        self.assertEqual(default_model(snapshot(q, models=[DEFAULT_MODEL, 'gemini-4-argon-low', SONNET])), SONNET)

    def test_general_gemini_or_third_party_limits_respected(self):
        for group in ('gemini-5h', 'gemini-weekly'):
            self.assertEqual(default_model(snapshot({group: {'remaining_fraction': 0}})), SONNET)
        q = {'gemini-argon-wtu-weekly': {'remaining_fraction': 0}, '3p-weekly': {'remaining_fraction': 0}}
        self.assertEqual(default_model(snapshot(q)), FLASH)

    def test_all_known_quotas_zero_does_not_retry_same_group(self):
        q = {key: {'remaining_fraction': 0} for key in ('gemini-weekly', '3p-5h')}
        with self.assertRaises(ValueError): default_model(snapshot(q))

    def test_failed_account_check_does_not_apply_previous_account_zero(self):
        q = {'gemini-argon-wtu-weekly': {'remaining_fraction': 0}}
        self.assertEqual(default_model(snapshot(q, state='unknown')), DEFAULT_MODEL)

    def test_only_currently_advertised_models_can_be_selected(self):
        self.assertEqual(default_model(snapshot(models=[SONNET])), SONNET)
        with self.assertRaises(ValueError): default_model(snapshot(models=[]))


class ModelDispatchTests(unittest.TestCase):
    setUp = binding_fixtures.BindingTests.setUp
    tearDown = binding_fixtures.BindingTests.tearDown
    def test_quota_aware_default_and_explicit_choice_share_one_binding(self):
        self.app.health['models'] = [{'id': m, 'label': m} for m in (DEFAULT_MODEL, FLASH)]
        self.app.health['account'] = {'state': 'known', 'quota': {'gemini-argon-wtu-weekly': {'remaining_fraction': 0}}}
        httpd = ThreadingHTTPServer(('127.0.0.1', 0), Handler); httpd.app = self.app
        worker = threading.Thread(target=httpd.serve_forever, daemon=True); worker.start()
        task = self.root/'task.txt';task.write_text('first')
        try:
            command = [sys.executable, str(Path(__file__).with_name('bridge.py')), '--url',
                       'http://127.0.0.1:'+str(httpd.server_port), '--task-file', str(task), '--timeout', '5']
            results = []
            for suffix in ([], ['--model', DEFAULT_MODEL]):
                done = subprocess.run(command+suffix, env=dict(os.environ, CODEX_THREAD_ID=binding_fixtures.THREAD),
                                      capture_output=True, text=True, timeout=8)
                self.assertEqual(done.returncode, 0, done.stderr)
                rows = [json.loads(x) for x in done.stdout.splitlines()]
                results.append(rows)
            self.assertEqual(results[0][0]['model'], FLASH)
            self.assertEqual(results[1][0]['model'], DEFAULT_MODEL)
            self.assertEqual(results[0][-1]['conversation_id'], results[1][-1]['conversation_id'])
            self.assertEqual(len(self.app.state['conversations']), 1)
        finally:
            httpd.shutdown(); httpd.server_close(); worker.join(3)


if __name__ == '__main__': unittest.main()
