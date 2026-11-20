"""Shared smart lists, isolated kind policies and frozen tasks; fake CLI only."""
import copy
import http.client
import json
from pathlib import Path
import subprocess
import sys
import threading
import unittest

from model_policy import default_model, model_selection_settings, window_model_resolution
from panel_backups import validate_state
from server import Console, ConflictError, Handler, ThreadingHTTPServer
import test_codex_bridge as binding
from test_panel_backups import fixture_state

ARGON = 'gemini-4-argon-high'
FLASH = 'gemini-3.8-flash-high'
SONNET = 'claude-sonnet-5-5-medium'


class GlobalModelPolicyTests(unittest.TestCase):
    setUp = binding.BindingTests.setUp
    tearDown = binding.BindingTests.tearDown
    wait = binding.BindingTests.wait

    def prepare(self):
        self.app.health['models'] = [{'id': m, 'label': m} for m in (ARGON, FLASH, SONNET)]
        self.app.set_preferred_model(ARGON)
        a = self.app.bind_codex(binding.THREAD)
        b = self.app.bind_codex(binding.OTHER)
        return a, b

    def save(self, kind='text', models=None, enabled=True):
        return self.app.set_global_model_policy(kind, {'enabled': enabled, 'models': [FLASH, SONNET] if models is None else models},
                                                self.app.state['global_model_policy_version'])

    def test_shared_order_ignores_legacy_default_and_per_chat_fallback_without_deleting(self):
        a, b = self.prepare()
        self.app.set_fallback(a['id'], 'text', {'enabled': False, 'models': [ARGON]})
        old = copy.deepcopy(self.app.conversation(a['id'])['fallback_preferences'])
        result = self.save()
        self.assertEqual(result['model_resolution']['text']['effective_model'], FLASH)
        self.assertEqual(result['model_resolution']['text']['requested_model'], '')
        for cid in (a['id'], b['id']):
            conv = self.app.conversation(cid)
            self.assertEqual(default_model(self.app.snapshot(), conv), FLASH)
            self.assertEqual(self.app.model_resolution(conv)['text']['effective_model'], FLASH)
        self.assertEqual(self.app.state['preferred_model'], ARGON)
        self.assertEqual(self.app.conversation(a['id'])['fallback_preferences'], old)

    def test_window_override_is_retained_but_uses_shared_fallback_on_exhaustion(self):
        a, _ = self.prepare()
        self.save()
        self.app.set_window_model(a['id'], 'text', ARGON)
        conv = self.app.conversation(a['id'])
        self.assertEqual(self.app.model_resolution(conv)['text']['effective_model'], ARGON)
        self.app.health['account'] = {'state': 'known', 'quota': {'gemini-argon-wtu-weekly': {'remaining_fraction': 0}}}
        resolution = self.app.model_resolution(conv)['text']
        self.assertEqual(resolution['requested_model'], ARGON)
        self.assertEqual(resolution['effective_model'], FLASH)
        self.assertEqual(conv['model_preferences']['text'], ARGON)

    def test_image_text_and_disabled_retry_keep_exact_lists_and_tiers(self):
        a, _ = self.prepare()
        self.save(models=[FLASH, ARGON], enabled=False)
        self.save('image', [SONNET])
        conv = self.app.conversation(a['id'])
        resolution = self.app.model_resolution(conv)
        self.assertEqual(resolution['text']['effective_model'], FLASH)
        self.assertFalse(resolution['text']['fallback_enabled'])
        self.assertEqual(resolution['image']['effective_model'], SONNET)
        with self.app.lock:
            text = self.app.submit(a['id'], 'text', '')
            image = self.app.submit(a['id'], 'image', '', 'image')
            self.assertEqual(text['model'], FLASH)
            self.assertEqual(text['fallback_models'], [FLASH, ARGON])
            self.assertFalse(text['fallback_enabled'])
            self.assertTrue(text['preflight_enabled'])
            self.assertEqual(image['fallback_models'], [SONNET])
            for turn in (text, image):
                self.app.stop(turn['id'])

    def test_global_edits_never_rewrite_frozen_queued_tasks_or_explicit_override(self):
        a, _ = self.prepare()
        self.save()
        with self.app.lock:
            queued = self.app.submit(a['id'], 'first', '')
            exact = self.app.submit(a['id'], 'first', ARGON)
            self.save(models=[SONNET])
            self.assertEqual(queued['model'], FLASH)
            self.assertEqual(queued['fallback_models'], [FLASH, SONNET])
            self.assertEqual(self.app.choose_turn_model(queued), FLASH)
            self.assertEqual(exact['model'], ARGON)
            self.assertFalse(exact['fallback_enabled'])
            self.assertFalse(exact['preflight_enabled'])
            for turn in (queued, exact):
                self.app.stop(turn['id'])

    def test_known_quota_and_empty_list_do_not_escape_the_selected_models(self):
        self.prepare()
        self.save(models=[ARGON, FLASH])
        self.app.health['account'] = {'state': 'known', 'quota': {'gemini-argon-wtu-weekly': {'remaining_fraction': 0}}}
        self.assertEqual(self.app.model_resolution({})['text']['effective_model'], FLASH)
        self.save(models=[])
        resolution = self.app.model_resolution({})['text']
        self.assertEqual(resolution['effective_model'], '')
        self.assertTrue(resolution['error'])
        self.app.state['global_model_policy'].pop('text')
        self.assertEqual(self.app.model_resolution({})['text']['requested_model'], ARGON)

    def test_compare_and_swap_validation_and_restart_persistence(self):
        self.prepare()
        result = self.save()
        before = self.app.file.read_bytes()
        with self.assertRaises(ConflictError):
            self.app.set_global_model_policy('image', {'enabled': True, 'models': [ARGON]}, 0)
        for kind, value, version in (
            ('audio', {'enabled': True}, 1), ('text', {'enabled': 1}, 1),
            ('text', {'enabled': True, 'models': ['unknown-model']}, 1),
            ('text', {'enabled': True, 'models': [False]}, 1),
            ('text', {'enabled': True, 'secret': 'invalid'}, 1),
            ('text', {'enabled': True}, True),
        ):
            with self.assertRaises((ValueError, ConflictError)):
                self.app.set_global_model_policy(kind, value, version)
        self.assertEqual(before, self.app.file.read_bytes())
        self.app.close()
        self.app = Console(self.root/'data', self.binary, import_history=False)
        self.assertEqual(self.app.state['global_model_policy'], result['global_model_policy'])
        self.assertEqual(self.app.state['global_model_policy_version'], 1)
        for summary in (False, True):
            self.assertEqual(self.app.snapshot(summary)['global_model_policy'], result['global_model_policy'])

    def test_http_gate_and_model_less_bridge_use_the_shared_policy(self):
        self.prepare()
        httpd = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
        httpd.app = self.app
        worker = threading.Thread(target=httpd.serve_forever, daemon=True)
        worker.start()
        def post(authorized=True, origin=None, version=0):
            connection = http.client.HTTPConnection('127.0.0.1', httpd.server_port, timeout=3)
            headers = {'Content-Type': 'application/json'}
            if authorized:
                headers['X-Console-Token'] = self.app.token
            if origin:
                headers['Origin'] = origin
            connection.request('POST', '/api/global-model-policy', json.dumps({'kind': 'text', 'settings': {'enabled': True, 'models': [FLASH]}, 'version': version}), headers)
            response = connection.getresponse()
            code = response.status
            response.read()
            connection.close()
            return code
        try:
            self.assertEqual(post(False), 403)
            self.assertEqual(post(origin='https://elsewhere.example'), 403)
            self.assertEqual(post(), 200)
            self.assertEqual(post(), 409)
            task = self.root/'task.txt'
            task.write_text('first')
            result = subprocess.run([sys.executable, str(Path(__file__).with_name('bridge.py')), '--url',
                'http://127.0.0.1:' + str(httpd.server_port), '--thread-id', binding.THREAD,
                '--task-file', str(task), '--timeout', '8', '--out', str(self.root/'result')], capture_output=True, text=True, timeout=12)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(json.loads(result.stdout.splitlines()[0])['model'], FLASH)
            self.assertEqual(json.loads((self.root/'result/result.json').read_text())['model'], FLASH)
        finally:
            httpd.shutdown()
            httpd.server_close()
            worker.join(3)

    def test_backups_reject_malformed_shared_policy(self):
        state = fixture_state()
        state['global_model_policy'] = {'text': {'enabled': True, 'models': [FLASH, ARGON]}}
        state['global_model_policy_version'] = 4
        validate_state(state)
        for value in ({'text': {'enabled': True, 'models': [None]}}, {'audio': {'enabled': True}}, {'text': {'enabled': True, 'unknown': 1}}):
            state['global_model_policy'] = value
            with self.assertRaises(ValueError):
                validate_state(state)
        state['global_model_policy'] = {}
        state['global_model_policy_version'] = True
        with self.assertRaises(ValueError):
            validate_state(state)


if __name__ == '__main__':
    unittest.main()
