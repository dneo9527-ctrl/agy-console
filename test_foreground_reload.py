from pathlib import Path
import tempfile
import unittest
from foreground_reload import PROXY_KEYS, REQUIRED_FILES, reload_environment, validate_sources


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


if __name__=='__main__':unittest.main(verbosity=2)
