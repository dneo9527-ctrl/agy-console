"""Companion install/refresh boundaries in disposable Codex homes only."""
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parent
spec = importlib.util.spec_from_file_location('companion_installer', ROOT / 'scripts/install_codex_skill.py')
installer = importlib.util.module_from_spec(spec)
spec.loader.exec_module(installer)


class CodexSkillTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='agy skill path with spaces ')
        self.addCleanup(self.temp.cleanup)
        self.base = Path(self.temp.name).resolve()
        self.console = self.base / 'Console Folder'
        self.console.mkdir()
        self.trace = self.base / 'bridge-call.json'
        (self.console / 'bridge.py').write_text('import json,sys,os\nfrom pathlib import Path\n'
            'Path(os.environ["AGY_SKILL_TEST_TRACE"]).write_text(json.dumps({"args":sys.argv[1:],"thread":os.environ.get("CODEX_THREAD_ID")}))\n')
        self.home = self.base / 'Codex Home'

    def install(self, enable=False):
        return installer.install(self.console, self.home, enable)

    def test_install_dispatch_and_managed_refresh_preserve_unrelated_guidance(self):
        self.home.mkdir()
        agents = self.home / 'AGENTS.md'
        agents.write_text('Keep my project rules.\n')
        self.install(True)
        target = self.home / 'skills/agy-console'
        original = agents.read_bytes()
        self.assertIn(b'Keep my project rules.', original)
        self.assertEqual(original.count(installer.START.encode()), 1)
        connection = json.loads((target / 'connection.json').read_text())
        self.assertEqual(Path(connection['bridge']), self.console / 'bridge.py')
        env = dict(os.environ, CODEX_THREAD_ID='11111111-1111-4111-8111-111111111111', AGY_SKILL_TEST_TRACE=str(self.trace))
        command = [sys.executable, str(target / 'scripts/dispatch.py')]
        proc = subprocess.run(command + ['--ensure', '--title', 'A title with spaces'], env=env, capture_output=True)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertEqual(json.loads(self.trace.read_text())['args'], ['--ensure', '--title', 'A title with spaces'])
        self.trace.unlink()
        proc = subprocess.run(command + ['--thread-id=22222222-2222-4222-8222-222222222222'], env=env, capture_output=True)
        self.assertNotEqual(proc.returncode, 0)
        self.assertFalse(self.trace.exists())
        result = self.install(True)
        self.assertEqual(agents.read_bytes(), original)
        self.assertTrue(Path(result['previous_skill_backup']).is_dir())

    def test_default_is_opt_in_and_unknown_skill_is_not_overwritten(self):
        self.install()
        self.assertFalse((self.home / 'AGENTS.md').exists())
        marker = self.home / 'skills/agy-console/agy-console-install.json'
        marker.unlink()
        before = (marker.parent / 'SKILL.md').read_bytes()
        with self.assertRaisesRegex(ValueError, 'unrelated'):
            self.install(True)
        self.assertEqual((marker.parent / 'SKILL.md').read_bytes(), before)
        self.assertFalse((self.home / 'AGENTS.md').exists())

    def test_malformed_guidance_and_concurrent_install_fail_before_changes(self):
        self.home.mkdir()
        agents = self.home / 'AGENTS.md'
        agents.write_text('Project rules\n' + installer.START)
        with self.assertRaisesRegex(ValueError, 'malformed'):
            self.install(True)
        self.assertFalse((self.home / 'skills').exists())
        agents.write_text('Project rules\n')
        (self.home / '.agy-skill-install.lock').mkdir()
        with self.assertRaisesRegex(ValueError, 'active'):
            self.install(True)
        self.assertEqual(agents.read_text(), 'Project rules\n')
        self.assertFalse((self.home / 'skills/agy-console').exists())

    def test_write_failure_restores_managed_skill_and_guidance(self):
        self.home.mkdir()
        agents = self.home / 'AGENTS.md'
        agents.write_text('Original instructions\n')
        self.install()
        old_skill = (self.home / 'skills/agy-console/SKILL.md').read_bytes()
        with patch.object(installer.os, 'replace', side_effect=OSError('synthetic write failure')):
            with self.assertRaisesRegex(OSError, 'synthetic'):
                self.install(True)
        self.assertEqual((self.home / 'skills/agy-console/SKILL.md').read_bytes(), old_skill)
        self.assertEqual(agents.read_text(), 'Original instructions\n')
        self.assertFalse((self.home / '.agy-skill-install.lock').exists())


if __name__ == '__main__':
    unittest.main(verbosity=2)
