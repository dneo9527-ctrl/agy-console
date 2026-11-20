"""Behavior tests for release boundaries; no account, network, or live install."""
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
import zipfile
from common import ROOT, config, sha256, safe_path, manifest
from release_plan import blockers
from verify_archive import verify
from audit import findings, history


class ReleaseFrameworkTests(unittest.TestCase):
    def test_disabled_program_updates_fail_before_creating_any_output(self):
        if config().get('program_updates_allowed') is not False:
            self.skipTest('This release has not disabled program-only updates')
        from package_release import build
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            output = root / 'program'
            proc = subprocess.run([sys.executable, '-B', str(ROOT/'build_update.py'),
                '--app-dir', str(ROOT), '--out', str(output), '--target', 'macos-arm64'],
                capture_output=True, text=True)
            self.assertNotEqual(proc.returncode, 0)
            self.assertIn('Program-only updates are disabled', proc.stderr)
            self.assertFalse(output.exists())
            output = root / 'platform'
            with self.assertRaisesRegex(ValueError, 'Program-only updates are disabled'):
                build(output, root/'never-read-runtime.zip', '0'*64, include_program_updates=True)
            self.assertFalse(output.exists())

    def test_unversioned_framework_cannot_be_released(self):
        c = dict(config(), status='framework_only', version=None, publish_authorized=False)
        issues = blockers(c)
        self.assertIn('final version missing or invalid', issues)
        self.assertIn('publication not authorized for this release', issues)

    def test_paths_and_secret_patterns_fail_without_showing_values(self):
        for name in ('../private', '/private', 'a/../../private', 'a\\private', '.env'):
            with self.assertRaises(ValueError): safe_path(name)
        data = b'-----BEGIN ' + b'PRIVATE KEY-----\nSYNTHETIC-NOT-A-KEY'
        rows = findings(data, 'fake-fixture')
        self.assertEqual(rows, [{'file': 'fake-fixture', 'rule': 'private_key'}])
        self.assertNotIn('SYNTHETIC', json.dumps(rows))

    def test_rehearsal_archive_reproducible_and_full_byte_verified(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            archives = []
            for label in ('one', 'two'):
                proc = subprocess.run([sys.executable, '-B', str(ROOT/'scripts/package_source.py'),
                    '--rehearsal', '--out', str(root/label)], capture_output=True, text=True)
                self.assertEqual(proc.returncode, 0, proc.stderr + proc.stdout)
                file = next((root/label).glob('*.zip')); archives.append(file.read_bytes())
                report = verify(file, sha256(file.read_bytes()))
                self.assertTrue(report['rehearsal'])
                self.assertGreater(report['files'], 50)
                with self.assertRaises(ValueError): verify(file, '0'*64)
            self.assertEqual(archives[0], archives[1])

    def test_zip_traversal_and_tampering_rejected(self):
        with tempfile.TemporaryDirectory() as folder:
            file = Path(folder)/'fake.zip'
            with zipfile.ZipFile(file,'w') as z:
                z.writestr('../outside', 'synthetic')
            with self.assertRaises(ValueError): verify(file, sha256(file.read_bytes()))
            with zipfile.ZipFile(file,'w') as z:
                z.writestr('test/PACKAGE-MANIFEST.json', json.dumps({'kind':'source','version':'test',
                    'rehearsal':True,'files':{'code.py':sha256(b'original')}}))
                z.writestr('test/code.py', b'tampered')
            with self.assertRaises(ValueError): verify(file, sha256(file.read_bytes()))

    def test_export_rejects_unreviewed_files_without_creating_output(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder); source = root/'input'; source.mkdir()
            expected = root/'expected.json'
            expected.write_text(json.dumps({'../private': '0'*64}))
            output = root/'output'
            proc = subprocess.run([sys.executable, '-B', str(ROOT/'scripts/export_source.py'),
                '--from-dir', str(source), '--expected', str(expected), '--out', str(output)], capture_output=True)
            self.assertNotEqual(proc.returncode,0)
            self.assertFalse(output.exists())

    def test_frozen_export_preserves_crlf_and_mode_and_rejects_changed_source(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder); source = root/'input'; source.mkdir()
            rows = {}
            for name in manifest()['runtime_files']:
                data = b'# synthetic fixture\r\n'
                (source/name).write_bytes(data); rows[name] = sha256(data)
            (source/'server.py').chmod(0o755)
            expected = root/'expected.json'; expected.write_text(json.dumps(rows))
            command = [sys.executable, '-B', str(ROOT/'scripts/export_source.py'),
                '--from-dir', str(source), '--expected', str(expected)]
            output = root/'good'
            proc = subprocess.run(command+['--out',str(output)],capture_output=True)
            self.assertEqual(proc.returncode,0,proc.stderr)
            self.assertEqual((output/'server.py').read_bytes(), b'# synthetic fixture\r\n')
            self.assertEqual((output/'server.py').stat().st_mode & 0o777, 0o755)
            (source/'server.py').write_bytes(b'# changed after freeze\n')
            bad = root/'bad'
            proc = subprocess.run(command+['--out',str(bad)],capture_output=True)
            self.assertNotEqual(proc.returncode,0)
            self.assertFalse(bad.exists(),'no partial export on a hash mismatch')

    def test_removed_historical_secret_is_still_found(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            def git(*args):
                subprocess.run(['git','-c','user.name=ReleaseFixture','-c','user.email=fixture@example.test',*args],
                               cwd=root,capture_output=True,check=True)
            git('init')
            file = root/'retired.py'
            file.write_bytes(b'-----BEGIN ' + b'PRIVATE KEY-----\nSYNTHETIC-NOT-A-KEY')
            git('add','retired.py');git('commit','-m','Synthetic historical fixture')
            git('rm','retired.py');git('commit','-m','Remove fixture from current tree')
            report, rows = history(root)
            self.assertEqual(report['status'],'failed')
            self.assertIn({'file':'retired.py','rule':'private_key'},rows)


if __name__ == '__main__':
    unittest.main(verbosity=2)
