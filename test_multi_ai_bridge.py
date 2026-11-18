"""Multi-provider bridge contract; isolated doubles, never a live console/model."""
import contextlib
import copy
import io
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import threading
import unittest
from unittest import mock

import bridge


THREAD = '11111111-2222-3333-4444-555555555555'
OTHER = '66666666-7777-8888-9999-aaaaaaaaaaaa'
CID = 'b' * 32
TURN = 'c' * 32


def policy(enabled=True, agy=True, muse=False, claude=False):
    return {'schema_version': 1, 'codex_thread_id': THREAD, 'enabled': enabled,
            'providers': {'agy': {'enabled': agy}, 'muse': {'enabled': muse},
                          'claude': {'enabled': claude}}}


class FakeClient:
    """Only the API surface used by bridge.main; records every side effect."""
    def __init__(self, *, modern=True, value=None):
        self.url = 'http://127.0.0.1:54321'
        self.features = {'codex_binding': True, 'gpt_collaboration': True,
                         'panel_auto_open': True, 'window_models': True,
                         'session_groups': True}
        if modern:
            self.features.update(multi_ai_collaboration=True, console_startup=True)
        self.conv = {'id': CID, 'codex_thread_id': THREAD, 'agy_id': None,
                     'primary_branch': None, 'branches': {},
                     'gpt_collaboration': True, 'auto_open_activity': False,
                     'auto_start_console': False,
                     'console_startup': {'schema_version': 1,
                                         'policy_path': '/isolated/coordination.json',
                                         'installation_root': '/isolated/installation',
                                         'launcher': {'path': ''}}}
        if modern:
            self.conv['multi_ai_collaboration'] = policy() if value is None else value
        self.calls = []
        self.bindings = []
        self.fail_path = None

    def bind(self, thread_id, title=None, **seed):
        self.bindings.append((thread_id, title, copy.deepcopy(seed)))
        return copy.deepcopy(self.conv)

    def request(self, path, body=None):
        self.calls.append((path, copy.deepcopy(body)))
        if path == self.fail_path:
            raise ValueError('isolated save failed')
        if path.endswith('/multi-ai-collaboration'):
            for name, value in body.get('providers', {}).items():
                self.conv['multi_ai_collaboration']['providers'][name] = value
            return copy.deepcopy(self.conv['multi_ai_collaboration'])
        if path.endswith('/collaboration'):
            enabled = body['enabled']
            self.conv['gpt_collaboration'] = enabled
            if 'multi_ai_collaboration' in self.conv:
                self.conv['multi_ai_collaboration']['enabled'] = enabled
                if enabled:
                    self.conv['multi_ai_collaboration']['providers']['agy']['enabled'] = True
            return {'enabled': enabled}
        if path.endswith('/console-startup'):
            self.conv['auto_start_console'] = body['auto_start_console']
            return {'auto_start_console': body['auto_start_console'],
                    'console_startup': copy.deepcopy(self.conv['console_startup'])}
        if path.endswith('/auto-open'):
            self.conv['auto_open_activity'] = body['enabled']
            return {'enabled': body['enabled']}
        if path.endswith('/turns'):
            return {'id': TURN, 'model': body['model']}
        raise AssertionError('Unexpected API path: ' + path)


class MetadataTests(unittest.TestCase):
    def test_missing_feature_uses_legacy_without_interpreting_unadvertised_data(self):
        self.assertIsNone(bridge.multi_ai_metadata({'multi_ai_collaboration': {'broken': True}}, THREAD, {}))

    def test_valid_copy_has_exact_identity_and_no_shared_mutability(self):
        value = policy(muse=True)
        result = bridge.multi_ai_metadata({'multi_ai_collaboration': value}, THREAD,
                                          {'multi_ai_collaboration': True})
        self.assertEqual(result, value)
        result['providers']['agy']['enabled'] = False
        self.assertTrue(value['providers']['agy']['enabled'])

    def test_wrong_schema_identity_unknown_keys_and_non_bool_fail_closed(self):
        invalid = [None, {}, [], 'enabled']
        for key, value in [('schema_version', True), ('schema_version', 2),
                           ('schema_version', '1'), ('codex_thread_id', OTHER),
                           ('enabled', 1), ('enabled', 'false')]:
            item = policy(); item[key] = value; invalid.append(item)
        item = policy(); item['extra'] = True; invalid.append(item)
        item = policy(); del item['providers']['muse']; invalid.append(item)
        item = policy(); item['providers']['unknown'] = {'enabled': True}; invalid.append(item)
        item = policy(); item['providers']['agy']['enabled'] = 0; invalid.append(item)
        item = policy(); item['providers']['muse'] = False; invalid.append(item)
        item = policy(); item['providers']['claude']['extra'] = False; invalid.append(item)
        for value in invalid:
            with self.subTest(value=value), self.assertRaises(ValueError):
                bridge.multi_ai_metadata({'multi_ai_collaboration': value}, THREAD,
                                         {'multi_ai_collaboration': True})


class BridgePolicyTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.task = Path(self.tmp.name) / 'task.txt'
        self.task.write_text('A bounded synthetic test task.', encoding='utf-8')

    def tearDown(self):
        self.tmp.cleanup()

    def invoke(self, client, *args):
        stdout = io.StringIO(); stderr = io.StringIO()
        argv = ['bridge.py', '--thread-id', THREAD, '--url', client.url, *args]
        with mock.patch.object(sys, 'argv', argv), mock.patch.object(bridge, 'Client', return_value=client), \
                contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
            status = bridge.main()
        return status, [json.loads(line) for line in stdout.getvalue().splitlines()], stderr.getvalue()

    def task_args(self):
        return ['--task-file', str(self.task), '--model', 'synthetic-model', '--no-wait']

    def test_ensure_reads_metadata_and_pause_without_any_submission(self):
        client = FakeClient(value=policy(enabled=False, muse=True))
        client.conv['gpt_collaboration'] = False
        status, values, _ = self.invoke(client, '--ensure')
        self.assertEqual(status, 0)
        result = values[0]
        self.assertFalse(result['multi_ai_collaboration']['enabled'])
        self.assertTrue(result['multi_ai_collaboration']['providers']['muse']['enabled'])
        self.assertFalse(result['model_started'])
        self.assertFalse(result['auto_start_console'])
        self.assertEqual(result['panel_action']['type'], 'none')
        self.assertEqual(client.calls, [])
        self.assertEqual(client.bindings, [(THREAD, None, {})])

    def test_legacy_alias_is_derived_from_canonical_agy_permission(self):
        for enabled, agy in [(True, False), (False, True), (False, False), (True, True)]:
            with self.subTest(enabled=enabled, agy=agy):
                client = FakeClient(value=policy(enabled=enabled, agy=agy, muse=True))
                client.conv['gpt_collaboration'] = not (enabled and agy)
                _, values, _ = self.invoke(client, '--ensure')
                self.assertIs(values[0]['collaboration_enabled'], enabled and agy)
                self.assertEqual(client.calls, [])

    def test_provider_toggle_only_patches_that_provider_never_resumes_master(self):
        client = FakeClient(value=policy(enabled=False, agy=False, claude=True))
        _, values, _ = self.invoke(client, '--ensure', '--provider', 'muse', '--provider-collaboration', 'on')
        self.assertEqual(client.calls, [('/api/conversations/'+CID+'/multi-ai-collaboration',
                                         {'codex_thread_id': THREAD, 'providers': {'muse': {'enabled': True}}})])
        result = values[0]['multi_ai_collaboration']
        self.assertFalse(result['enabled']); self.assertFalse(result['providers']['agy']['enabled'])
        self.assertTrue(result['providers']['muse']['enabled'])
        self.assertTrue(result['providers']['claude']['enabled'])
        self.assertEqual(len(client.bindings), 2)
        self.assertEqual({item[0] for item in client.bindings}, {THREAD})

    def test_explicit_master_then_provider_patch_order_and_authoritative_reread(self):
        client = FakeClient(value=policy(enabled=False, agy=False, muse=True))
        _, values, _ = self.invoke(client, '--ensure', '--collaboration', 'on',
                                   '--provider', 'agy', '--provider-collaboration', 'off')
        self.assertEqual([path.rsplit('/', 1)[-1] for path, _ in client.calls],
                         ['collaboration', 'multi-ai-collaboration'])
        result = values[0]
        self.assertTrue(result['multi_ai_collaboration']['enabled'])
        self.assertFalse(result['multi_ai_collaboration']['providers']['agy']['enabled'])
        self.assertTrue(result['multi_ai_collaboration']['providers']['muse']['enabled'])
        self.assertFalse(result['collaboration_enabled'])

    def test_auto_start_saves_exact_chat_and_reports_launcher_without_executing(self):
        client = FakeClient()
        with mock.patch('subprocess.Popen', side_effect=AssertionError('must not launch')):
            _, values, _ = self.invoke(client, '--ensure', '--auto-start', 'on')
        self.assertEqual(client.calls, [('/api/conversations/'+CID+'/console-startup',
                                         {'codex_thread_id': THREAD, 'auto_start_console': True})])
        self.assertTrue(values[0]['auto_start_console'])
        self.assertTrue(values[0]['console_startup_supported'])
        self.assertEqual(values[0]['console_startup']['launcher']['path'], '')
        self.assertFalse(values[0]['model_started'])
        self.assertFalse(values[0]['auto_open_activity'])

    def test_canonical_master_or_agy_pause_blocks_before_submission_even_if_alias_on(self):
        for value in [policy(enabled=False), policy(agy=False, muse=True)]:
            with self.subTest(value=value):
                client = FakeClient(value=value)
                with self.assertRaisesRegex(ValueError, '暂停'):
                    self.invoke(client, *self.task_args())
                self.assertEqual(client.calls, [])

    def test_valid_canonical_controls_dispatch_even_if_legacy_alias_stale_off(self):
        client = FakeClient(value=policy())
        client.conv['gpt_collaboration'] = False
        _, values, _ = self.invoke(client, *self.task_args())
        self.assertTrue(values[0]['submitted'])
        self.assertEqual(client.calls, [('/api/conversations/'+CID+'/turns',
                                         {'prompt': self.task.read_text(), 'model': 'synthetic-model',
                                          'kind': 'text', 'codex_dispatch': True})])

    def test_corrupt_advertised_metadata_never_falls_back_to_legacy_allow(self):
        wrong_id = policy(); wrong_id['codex_thread_id'] = OTHER
        wrong_bool = policy(); wrong_bool['providers']['agy']['enabled'] = 'true'
        for value in [{}, wrong_id, wrong_bool]:
            client = FakeClient(value=value)
            with self.subTest(value=value), self.assertRaises(ValueError):
                self.invoke(client, *self.task_args())
            self.assertEqual(client.calls, [])

    def test_legacy_ensure_and_pause_compatibility(self):
        client = FakeClient(modern=False)
        client.conv['gpt_collaboration'] = False
        _, values, _ = self.invoke(client, '--ensure')
        self.assertFalse(values[0]['multi_ai_collaboration_supported'])
        self.assertNotIn('multi_ai_collaboration', values[0])
        self.assertFalse(values[0]['collaboration_enabled'])
        with self.assertRaisesRegex(ValueError, '暂停'):
            self.invoke(client, *self.task_args())
        self.assertEqual(client.calls, [])

    def test_provider_and_startup_on_old_server_fail_without_task_or_launch(self):
        for flags in [('--provider', 'muse', '--provider-collaboration', 'on'), ('--auto-start', 'on')]:
            client = FakeClient(modern=False)
            with self.subTest(flags=flags), self.assertRaisesRegex(ValueError, '新版'):
                self.invoke(client, '--ensure', *flags)
            self.assertEqual(client.calls, [])

    def test_failed_save_does_not_report_success_or_fall_through_to_dispatch(self):
        client = FakeClient()
        client.fail_path = '/api/conversations/'+CID+'/multi-ai-collaboration'
        with self.assertRaisesRegex(ValueError, 'isolated save failed'):
            self.invoke(client, '--ensure', '--provider', 'muse', '--provider-collaboration', 'on')
        self.assertEqual(len(client.calls), 1)
        self.assertFalse(client.conv['multi_ai_collaboration']['providers']['muse']['enabled'])

    def test_invalid_flag_combinations_reject_before_any_client_mutation(self):
        combinations = [
            ['--provider', 'muse'], ['--provider-collaboration', 'on'],
            ['--provider', 'muse', '--provider-collaboration', 'on'],
            ['--auto-start', 'on'], ['--ensure', '--task-file', str(self.task)],
            ['--ensure', '--import-result', str(self.task)], ['--ensure', '--stage-import'],
            ['--ensure', '--provider', 'unknown', '--provider-collaboration', 'on'],
        ]
        for flags in combinations:
            client = FakeClient()
            with self.subTest(flags=flags), self.assertRaises(SystemExit) as caught:
                self.invoke(client, *flags)
            self.assertEqual(caught.exception.code, 2)
            self.assertEqual(client.bindings, [])
            self.assertEqual(client.calls, [])


class BridgeHTTPTests(unittest.TestCase):
    """The actual bridge process against an ephemeral local test server."""
    def test_real_http_preferences_remain_per_chat_and_no_cli_process_starts(self):
        from server import Console, Handler, ThreadingHTTPServer
        from test_server import FAKE
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            binary = root / 'fake-agy'; binary.write_text(FAKE); binary.chmod(0o700)
            app = Console(root/'data', binary, import_history=False,
                          account_reader=lambda *_a, **_k: {'state': 'unknown'})
            httpd = ThreadingHTTPServer(('127.0.0.1', 0), Handler); httpd.app = app
            worker = threading.Thread(target=httpd.serve_forever, daemon=True); worker.start()
            command = [sys.executable, str(Path(bridge.__file__)), '--url',
                       'http://127.0.0.1:'+str(httpd.server_port)]

            def run(*args, thread=THREAD):
                return subprocess.run(command+list(args), env=dict(os.environ, CODEX_THREAD_ID=thread),
                                      capture_output=True, text=True, timeout=8)

            def ensured(*args, thread=THREAD):
                result = run('--ensure', *args, thread=thread)
                self.assertEqual(result.returncode, 0, result.stderr)
                return json.loads(result.stdout)

            try:
                first = ensured('--provider', 'agy', '--provider-collaboration', 'off')
                cid = first['console_conversation_id']
                self.assertTrue(first['multi_ai_collaboration']['enabled'])
                self.assertFalse(first['collaboration_enabled'])
                enabled = ensured('--provider', 'muse', '--provider-collaboration', 'on', '--auto-start', 'on')
                self.assertEqual(enabled['console_conversation_id'], cid)
                self.assertTrue(enabled['multi_ai_collaboration']['providers']['muse']['enabled'])
                self.assertFalse(enabled['multi_ai_collaboration']['providers']['agy']['enabled'])
                self.assertTrue(enabled['auto_start_console'])
                self.assertEqual(enabled['console_startup']['policy_path'], str(root/'data/coordination.json'))
                other = ensured(thread=OTHER)
                self.assertNotEqual(other['console_conversation_id'], cid)
                self.assertTrue(other['collaboration_enabled'])
                self.assertFalse(other['auto_start_console'])
                self.assertFalse(other['multi_ai_collaboration']['providers']['muse']['enabled'])
                task = root/'task.txt'; task.write_text('must remain blocked')
                blocked = run('--task-file', str(task), '--no-wait')
                self.assertEqual(blocked.returncode, 1)
                self.assertIn('暂停', blocked.stderr)
                again = ensured()
                self.assertEqual(again['multi_ai_collaboration'], enabled['multi_ai_collaboration'])
                self.assertFalse(app.processes)
                self.assertTrue(all(not conv['turns'] for conv in app.state['conversations']))
                offline = json.loads((root/'data/coordination.json').read_text())
                self.assertTrue(offline['ready'])
                self.assertTrue(offline['threads'][THREAD]['auto_start_console'])
                self.assertFalse(offline['threads'][OTHER]['auto_start_console'])
                self.assertFalse((root/'data/runs').exists())
            finally:
                httpd.shutdown(); httpd.server_close(); worker.join(3); app.close()


if __name__ == '__main__':
    unittest.main(verbosity=2)
