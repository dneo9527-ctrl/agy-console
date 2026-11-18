from pathlib import Path
import json
import tempfile
import unittest
from unittest import mock
from foreground_reload import PROXY_KEYS, REQUIRED_FILES, reload_environment, validate_sources
import server


class PreflightTests(unittest.TestCase):
    def test_validation_never_executes_modules_or_writes_bytecode(self):
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder)
            for name in REQUIRED_FILES:(root/name).write_text('')
            (root/'server.py').write_text("raise RuntimeError('must not execute')")
            validate_sources(root);self.assertFalse((root/'__pycache__').exists())
            (root/'bridge.py').write_text('def broken(')
            with self.assertRaises(ValueError):validate_sources(root)
            (root/'bridge.py').unlink()
            with self.assertRaises(ValueError):validate_sources(root)

    def test_only_startup_proxy_overrides_are_reapplied_before_exec(self):
        current={'UNCHANGED':'kept','HTTP_PROXY':'system-value','NO_PROXY':'system-bypass'}
        original={key:None for key in PROXY_KEYS};original['HTTPS_PROXY']='shell-override'
        result=reload_environment(current,original)
        self.assertNotIn('HTTP_PROXY',result);self.assertNotIn('NO_PROXY',result)
        self.assertEqual(result['HTTPS_PROXY'],'shell-override');self.assertEqual(result['UNCHANGED'],'kept')
        self.assertEqual(current['HTTP_PROXY'],'system-value')

    def test_normal_portable_and_installed_data_keep_standard_policy(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            portable = root / 'portable'
            shared = root / 'standard-app-data' / 'coordination.json'
            with mock.patch.object(server, 'ROOT', portable), mock.patch.object(server, 'default_policy_path', return_value=shared):
                self.assertEqual(server.foreground_coordination_path(portable / 'data'), shared)
                # Windows installed launches pass %LOCALAPPDATA%/AgyConsole/data
                # explicitly; it is still the normal per-user installation.
                self.assertEqual(server.foreground_coordination_path(shared.parent / 'data'), shared)
                custom = root / 'custom-profile'
                self.assertEqual(server.foreground_coordination_path(custom), custom.resolve() / 'coordination.json')

    def test_main_with_custom_data_never_modifies_standard_policy_sentinel(self):
        class StopAfterConstruction(Exception):
            pass

        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            shared = root / 'standard-app-data' / 'coordination.json'
            shared.parent.mkdir()
            sentinel = b'{"fixture":"preserve existing installed policies"}\n'
            shared.write_bytes(sentinel)
            custom = root / 'custom-profile'
            binary = root / 'must-not-run'
            real_console = server.Console
            created = []

            def construct(data, binary, **kwargs):
                app = real_console(data, binary, account_reader=lambda *_a, **_k: {'state': 'unknown'}, **kwargs)
                created.append(app)
                raise StopAfterConstruction()

            listener = mock.Mock()
            try:
                with mock.patch.object(server.sys, 'argv', ['server.py', '--data', str(custom), '--port', '0', '--agy-bin', str(binary)]), \
                     mock.patch.object(server, 'terminal_is_foreground', return_value=True), \
                     mock.patch.object(server, 'ForegroundHTTPServer', return_value=listener), \
                     mock.patch.object(server, 'configure_proxy', return_value={}), \
                     mock.patch.object(server.os, 'umask'), \
                     mock.patch.object(server, 'default_policy_path', return_value=shared), \
                     mock.patch.object(server.SoftwareUpdates, 'start'), \
                     mock.patch.object(server, 'Console', side_effect=construct):
                    with self.assertRaises(StopAfterConstruction):
                        server.main()
                self.assertEqual(shared.read_bytes(), sentinel)
                self.assertEqual(len(created), 1)
                self.assertEqual(created[0].coordination.path, custom.resolve() / 'coordination.json')
                isolated = json.loads((custom / 'coordination.json').read_text())
                self.assertTrue(isolated['ready'])
                self.assertEqual(isolated['threads'], {})
                listener.server_close.assert_called_once()
            finally:
                for app in created:
                    app.close()
            self.assertEqual(shared.read_bytes(), sentinel, 'shutdown must also retain the installed mirror')


if __name__=='__main__':unittest.main(verbosity=2)
