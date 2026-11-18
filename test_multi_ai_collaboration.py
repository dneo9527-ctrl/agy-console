"""Per-chat multi-provider policy, no browser, sign-in or real model calls."""
import copy
import json
import threading
import unittest
from unittest.mock import patch
from urllib.error import HTTPError
from urllib.request import urlopen

from bridge import Client
from multi_ai_collaboration import canonical, launcher_path, validate, Coordination
from panel_backups import validate_configuration, validate_state
from server import Console, DEFAULT_MODEL, Handler, ThreadingHTTPServer
import test_codex_bridge as fixtures


class MultiAIStateTests(unittest.TestCase):
    setUp = fixtures.BindingTests.setUp
    tearDown = fixtures.BindingTests.tearDown
    wait = fixtures.BindingTests.wait

    def bind(self, thread=fixtures.THREAD):
        return self.app.bind_codex(thread)

    def update(self, conv, **body):
        return self.app.set_multi_ai_collaboration(conv['id'], dict(codex_thread_id=conv['codex_thread_id'], **body))

    def test_old_pause_and_new_provider_defaults_survive_bind_and_restart(self):
        conv = self.app.create(); conv.update(codex_thread_id=fixtures.THREAD, gpt_collaboration=False)
        bound = self.bind()
        self.assertFalse(bound['multi_ai_collaboration']['enabled'])
        self.assertTrue(bound['multi_ai_collaboration']['providers']['agy']['enabled'])
        self.assertFalse(bound['multi_ai_collaboration']['providers']['muse']['enabled'])
        self.assertFalse(bound['multi_ai_collaboration']['providers']['claude']['enabled'])
        self.app.close()
        self.app = Console(self.root/'data', self.binary, import_history=False)
        self.assertEqual(self.bind()['multi_ai_collaboration'], bound['multi_ai_collaboration'])
        self.assertFalse(self.bind()['gpt_collaboration'])

    def test_invalid_legacy_value_fails_closed_instead_of_becoming_enabled(self):
        for value in ('false',0,1,None):
            with self.assertRaises(ValueError): canonical({'codex_thread_id':fixtures.THREAD,'gpt_collaboration':value})

    def test_canonical_policy_cannot_supply_identity_to_an_unbound_chat(self):
        policy = self.bind()['multi_ai_collaboration']
        for identity in (None, '', 1, 'invalid'):
            with self.assertRaises(ValueError):
                canonical({'codex_thread_id':identity,'multi_ai_collaboration':policy})

    def test_provider_only_disable_does_not_turn_global_pause_into_false_migration(self):
        conv = self.bind()
        self.update(conv, providers={'agy': {'enabled': False}, 'muse': {'enabled': True}})
        state = self.bind()
        self.assertTrue(state['multi_ai_collaboration']['enabled'])
        self.assertFalse(state['gpt_collaboration'])
        self.assertTrue(state['multi_ai_collaboration']['providers']['muse']['enabled'])
        self.update(conv, enabled=False)
        self.update(conv, providers={'claude': {'enabled': True}})
        self.assertFalse(self.bind()['multi_ai_collaboration']['enabled'])
        self.update(conv, enabled=True)
        state = self.bind()
        self.assertFalse(state['multi_ai_collaboration']['providers']['agy']['enabled'])
        self.app.set_collaboration(conv['id'], True)
        self.assertTrue(self.bind()['multi_ai_collaboration']['providers']['agy']['enabled'])

    def test_exact_chat_isolation_and_metadata_only_no_call(self):
        first = self.bind(); second = self.bind(fixtures.OTHER)
        before = copy.deepcopy(self.app.conversation(second['id']))
        raw = copy.deepcopy(self.app.conversation(first['id']))
        raw.update(auto_open_activity=False, model_preferences={'text':'', 'image':''})
        self.app.conversation(first['id']).update(raw)
        self.update(first, providers={'muse': {'enabled': True}})
        policy = self.app.multi_ai_policy(fixtures.THREAD)
        self.assertEqual(set(policy), {'schema_version','codex_thread_id','enabled','providers'})
        self.assertEqual(self.app.conversation(second['id']), before)
        stored = self.app.conversation(first['id'])
        for key in ('auto_open_activity','model_preferences','agy_id','branches','turns'):
            self.assertEqual(stored[key], raw[key])
        self.assertFalse(self.app.processes); self.assertFalse(stored['turns'])
        with self.assertRaises(KeyError): self.app.multi_ai_policy(fixtures.SESSION)
        self.assertEqual(len(self.app.state['conversations']), 2)

    def test_bad_updates_never_write_and_wrong_thread_cannot_modify(self):
        conv = self.bind()
        requests = [
            {'enabled': 'false'}, {'enabled': 0}, {'enabled': None},
            {'providers': {'muse': {'enabled': 1}}}, {'providers': {'other': {'enabled': True}}},
            {'providers': []}, {'providers': {}}, {'providers': {'muse': {'enabled': True, 'url': 'x'}}},
            {'schema_version': True, 'enabled': True}, {'schema_version': 2, 'enabled': False},
            {'enabled': False, 'codex_thread_id': fixtures.OTHER}, {'enabled': False, 'token': 'x'}]
        before = self.app.file.read_bytes()
        for body in requests:
            body = dict({'codex_thread_id': fixtures.THREAD}, **body)
            with self.subTest(body=body):
                with self.assertRaises(ValueError): self.app.set_multi_ai_collaboration(conv['id'], body)
                self.assertEqual(self.app.file.read_bytes(), before)
        with self.assertRaises(ValueError): self.app.set_multi_ai_collaboration(self.app.create()['id'], {'codex_thread_id':fixtures.THREAD,'enabled':True})

    def test_corrupt_canonical_never_falls_back_to_legacy_true(self):
        conv = self.bind(); stored = self.app.conversation(conv['id'])
        stored['multi_ai_collaboration']['codex_thread_id'] = fixtures.OTHER
        stored['gpt_collaboration'] = True
        with self.assertRaises(ValueError): self.app.multi_ai_policy(fixtures.THREAD)
        with self.assertRaises(ValueError): self.update(conv, enabled=True)
        with self.assertRaises(ValueError): self.app.submit(conv['id'], 'first', DEFAULT_MODEL, codex_dispatch=True)
        self.assertFalse(stored['turns'])

    def test_disabled_ag_dispatch_rejected_manual_and_already_submitted_continue(self):
        conv = self.bind(); turn = self.app.submit(conv['id'], 'slow', DEFAULT_MODEL, codex_dispatch=True)
        self.update(conv, providers={'agy': {'enabled': False}})
        self.assertIn(turn['status'], {'queued','running'})
        with self.assertRaises(ValueError): self.app.submit(conv['id'], 'blocked', DEFAULT_MODEL, codex_dispatch=True)
        self.app.stop(turn['id']); self.wait(turn)
        manual = self.app.submit(conv['id'], 'first', DEFAULT_MODEL)
        self.wait(manual); self.assertEqual(manual['status'], 'completed')

    def test_offline_publication_and_failures_keep_snapshot_invalid(self):
        conv = self.bind(); file = self.app.coordination.path
        value = json.loads(file.read_text()); self.assertTrue(value['ready'])
        self.assertEqual(value['threads'][fixtures.THREAD]['multi_ai_collaboration'], conv['multi_ai_collaboration'])
        self.assertEqual(file.stat().st_mode & 0o777, 0o600)
        with patch('server.write_json', side_effect=OSError('state disk full')):
            with self.assertRaises(OSError): self.update(conv, enabled=False)
        self.assertFalse(json.loads(file.read_text())['ready'])
        self.app.save()
        with patch.object(self.app.coordination, 'publish', side_effect=OSError('mirror disk full')):
            with self.assertRaises(OSError): self.update(conv, enabled=True)
        self.assertFalse(json.loads(file.read_text())['ready'])
        self.app.save()
        before = self.app.multi_ai_policy(fixtures.THREAD)
        with patch.object(self.app.coordination, 'invalidate', side_effect=OSError('cannot invalidate')):
            with self.assertRaises(OSError): self.update(conv, enabled=False)
        self.assertEqual(self.app.multi_ai_policy(fixtures.THREAD), before)
        invalidate = self.app.coordination.invalidate
        calls = []
        def fail_second_invalidation():
            calls.append(True)
            if len(calls) == 2:
                raise OSError('second invalidation failed')
            invalidate()
        with patch.object(self.app.coordination, 'invalidate', side_effect=fail_second_invalidation):
            with self.assertRaises(OSError): self.update(conv, enabled=False)
        self.assertEqual(self.app.multi_ai_policy(fixtures.THREAD), before)
        self.assertFalse(json.loads(file.read_text())['ready'])
        self.assertFalse(self.app.service_info()['coordination_ready'])
        self.assertTrue(self.app.service_info()['coordination_error'])
        self.app.save()
        self.assertTrue(self.app.service_info()['coordination_ready'])

    def test_failed_policy_and_startup_writes_restore_live_and_saved_preferences(self):
        conv = self.bind()
        self.update(conv, enabled=False)
        before = copy.deepcopy(self.app.conversation(conv['id']))
        turns = self.app.conversation(conv['id'])['turns']
        operations = (
            lambda: self.update(conv, enabled=True),
            lambda: self.app.set_collaboration(conv['id'], True),
            lambda: self.app.set_auto_start_console(conv['id'],
                {'codex_thread_id':fixtures.THREAD,'auto_start_console':True}),
        )
        for operation in operations:
            with self.subTest(operation=operation):
                with patch.object(self.app.coordination, 'publish', side_effect=OSError('mirror disk full')):
                    with self.assertRaises(OSError): operation()
                self.assertEqual(self.app.conversation(conv['id']), before)
                self.assertIs(self.app.conversation(conv['id'])['turns'], turns)
                saved = json.loads(self.app.file.read_text())['conversations'][0]
                self.assertEqual(saved, before)
                self.assertFalse(json.loads(self.app.coordination.path.read_text())['ready'])
                self.app.save()
        file = self.root/'Launcher.command'; file.write_text('#!/bin/sh\nexit 99\n')
        with patch.object(self.app.coordination, 'publish', side_effect=OSError('mirror disk full')):
            with self.assertRaises(OSError): self.app.set_console_startup({'launcher_path':str(file)})
        self.assertEqual(self.app.console_startup_info()['launcher']['path'], '')
        offline = json.loads(self.app.coordination.path.read_text())
        self.assertFalse(offline['ready'])
        self.assertEqual(offline['launcher']['path'], '')

    def test_failed_switch_retains_running_turn_objects(self):
        conv = self.bind()
        turn = self.app.submit(conv['id'], 'slow', DEFAULT_MODEL, codex_dispatch=True)
        turns = self.app.conversation(conv['id'])['turns']
        with patch('server.write_json', side_effect=OSError('state disk full')):
            with self.assertRaises(OSError): self.update(conv, enabled=False)
        self.assertIs(self.app.conversation(conv['id'])['turns'], turns)
        self.assertIs(turns[0], turn)
        self.assertTrue(self.app.multi_ai_policy(fixtures.THREAD)['enabled'])
        self.app.stop(turn['id']); self.wait(turn)

    def test_corrupt_chat_keeps_foreground_service_available_and_all_offline_starts_blocked(self):
        conv = self.bind(); other = self.bind(fixtures.OTHER)
        self.app.conversation(conv['id'])['multi_ai_collaboration']['enabled'] = 'false'
        self.app.save()
        self.assertFalse(json.loads(self.app.coordination.path.read_text())['ready'])
        self.app.close()
        self.app = Console(self.root/'data', self.binary, import_history=False)
        info = self.app.service_info()
        self.assertFalse(info['coordination_ready'])
        self.assertTrue(info['coordination_error'])
        with self.assertRaises(ValueError): self.app.multi_ai_policy(fixtures.THREAD)
        with self.assertRaises(ValueError): self.app.submit(conv['id'], 'blocked', DEFAULT_MODEL, codex_dispatch=True)
        self.assertTrue(self.app.multi_ai_policy(fixtures.OTHER)['enabled'])
        turn = self.app.submit(other['id'], 'first', DEFAULT_MODEL, codex_dispatch=True)
        self.wait(turn)
        self.assertEqual(turn['status'], 'completed')
        self.assertFalse(json.loads(self.app.coordination.path.read_text())['ready'])
        before = self.app.multi_ai_policy(fixtures.OTHER)
        with self.assertRaises(ValueError):
            self.app.set_multi_ai_collaboration(other['id'], {'codex_thread_id':fixtures.OTHER,'enabled':False})
        self.assertEqual(self.app.multi_ai_policy(fixtures.OTHER), before)
        self.assertFalse(json.loads(self.app.coordination.path.read_text())['ready'])

    def test_startup_launcher_exact_policy_default_and_no_execution(self):
        conv = self.bind(); other = self.bind(fixtures.OTHER)
        self.assertFalse(conv['auto_start_console'])
        self.assertEqual(conv['console_startup']['launcher']['path'], '')
        file = self.root/'Launcher.command'; file.write_text('#!/bin/sh\nexit 99\n')
        with patch('subprocess.Popen', side_effect=AssertionError('must not execute')):
            saved = self.app.set_console_startup({'launcher_path': str(file)})
            self.app.set_auto_start_console(conv['id'], {'codex_thread_id':fixtures.THREAD,'auto_start_console':True})
        self.assertEqual(saved, self.app.console_startup_info())
        self.assertEqual(saved['launcher']['path'], str(file))
        self.assertFalse(self.app.conversation(other['id'])['auto_start_console'])
        policy = json.loads(self.app.coordination.path.read_text())
        self.assertTrue(policy['threads'][fixtures.THREAD]['auto_start_console'])
        for invalid in ({'codex_thread_id':fixtures.OTHER,'auto_start_console':True}, {'codex_thread_id':fixtures.THREAD,'auto_start_console':1}):
            with self.assertRaises(ValueError): self.app.set_auto_start_console(conv['id'], invalid)
        self.app.set_console_startup({'launcher_path':''})
        self.assertEqual(self.app.console_startup_info()['launcher']['path'],'')

    def test_portable_configuration_strips_identity_and_preserves_provider_pause(self):
        conv = self.bind()
        self.update(conv, enabled=False, providers={'muse':{'enabled':True},'agy':{'enabled':False}})
        self.app.set_auto_start_console(conv['id'], {'codex_thread_id':fixtures.THREAD,'auto_start_console':True})
        doc = self.app.export_configuration({}, conv['id'])
        raw = json.dumps(doc)
        for value in (fixtures.THREAD, str(self.app.coordination.path), 'auto_start_console', 'launcher'):
            self.assertNotIn(value, raw)
        value = doc['settings']['window']['multi_ai_collaboration']
        self.assertFalse(value['enabled']); self.assertTrue(value['providers']['muse']['enabled'])
        validate_configuration(doc)
        validate_state(self.app.state)
        damaged = copy.deepcopy(doc); damaged['settings']['window']['multi_ai_collaboration']['providers']['muse']['enabled']=1
        with self.assertRaises(ValueError): validate_configuration(damaged)

    def test_metadata_http_is_readonly_and_post_has_csrf_and_exact_thread(self):
        conv = self.bind()
        httpd = ThreadingHTTPServer(('127.0.0.1',0), Handler); httpd.app = self.app
        worker = threading.Thread(target=httpd.serve_forever,daemon=True); worker.start()
        try:
            client = Client('http://127.0.0.1:'+str(httpd.server_port))
            expected = self.app.multi_ai_policy(fixtures.THREAD)
            self.assertEqual(client.request('/api/codex/'+fixtures.THREAD+'/collaboration'), expected)
            with self.assertRaises(ValueError): client.request('/api/codex/'+fixtures.OTHER+'/collaboration')
            self.assertEqual(len(self.app.state['conversations']), 1)
            changed = client.request('/api/conversations/'+conv['id']+'/multi-ai-collaboration',{'codex_thread_id':fixtures.THREAD,'providers':{'claude':{'enabled':True}}})
            self.assertTrue(changed['providers']['claude']['enabled'])
            startup = client.request('/api/console-startup')
            self.assertEqual(startup, self.app.console_startup_info())
            self.assertTrue(client.request('/api/service')['features']['multi_ai_collaboration'])
            self.assertFalse(self.app.processes)
        finally:
            httpd.shutdown();httpd.server_close();worker.join(3)


class LauncherValidationTests(unittest.TestCase):
    def test_explicit_file_or_unique_directory_and_os_extensions(self):
        import tempfile
        from pathlib import Path
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder); mac = root/'Open.command'; mac.write_text('test')
            cmd = root/'Open.cmd'; cmd.write_text('test')
            linux = root/'Start-Agy.sh'; linux.write_text('test')
            self.assertEqual(launcher_path(str(root),'darwin'),str(mac))
            self.assertEqual(launcher_path(str(root),'win32'),str(cmd))
            self.assertEqual(launcher_path(str(root),'linux'),str(linux))
            (root/'Other.command').write_text('test')
            for bad in (str(root), 'Open.command', str(mac)+'; echo no', str(root/'missing.command'), str(cmd)):
                with self.assertRaises(ValueError): launcher_path(bad,'darwin')


if __name__ == '__main__':
    unittest.main()
