"""Run fake-CLI regressions with an isolated HOME and no account environment."""
import argparse
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
from common import ROOT, manifest
from audit import run as audit


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--report', type=Path, required=True)
    args = parser.parse_args()
    result = {'audit': audit(), 'python': None, 'javascript': [], 'scope': 'Reference source, fake CLI; no model call'}
    failed = result['audit']['status'] != 'passed'
    with tempfile.TemporaryDirectory(prefix='agy-release-tests-') as folder:
        env = {'PATH': os.environ.get('PATH', ''), 'HOME': folder, 'USERPROFILE': folder,
               'PYTHONDONTWRITEBYTECODE': '1', 'PYTHONUTF8': '1', 'LANG': 'en_US.UTF-8',
               'TERM': 'xterm-256color', 'TMPDIR': folder, 'TEMP': folder, 'TMP': folder}
        # Exercise reviewed program bytes in isolation. A distribution repository
        # is deployment configuration: blank it here so fake-CLI tests never make
        # automatic public update requests. Actual updater networking is a separate check.
        test_root = Path(folder)/'source'; test_root.mkdir()
        for name in manifest()['source_files']:
            if '/' not in name:
                shutil.copy2(ROOT/name,test_root/name)
        (test_root/'release-source.json').write_text('{"repository":"","channel":"preview"}\n',encoding='utf-8')
        result['test_configuration'] = 'Deployment repository blanked in disposable copy; program bytes unchanged'
        suites = sorted(p.name for p in ROOT.glob('test_*.py'))
        command = [sys.executable, '-B', '-m', 'unittest', '-v'] + suites
        proc = subprocess.run(command, cwd=test_root, env=env, capture_output=True, text=True, timeout=600)
        result['python'] = {'exit_code': proc.returncode, 'suites': len(suites), 'output': proc.stdout, 'error': proc.stderr}
        failed |= bool(proc.returncode)
        node = shutil.which('node')
        if not node:
            result['javascript'] = [{'exit_code': 1, 'error': 'Node.js is required; not skipped silently'}]
            failed = True
        else:
            for p in sorted(ROOT.glob('test_*.js')):
                proc = subprocess.run([node, str(test_root/p.name)], cwd=test_root, env=env, capture_output=True, text=True, timeout=30)
                result['javascript'].append({'suite': p.name, 'exit_code': proc.returncode, 'output': proc.stdout, 'error': proc.stderr})
                failed |= bool(proc.returncode)
    result['status'] = 'failed' if failed else 'passed'
    args.report.parent.mkdir(parents=True, exist_ok=True)
    args.report.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding='utf-8')
    print(json.dumps({'status': result['status'], 'python_exit': result['python']['exit_code'],
                      'js_suites': len(result['javascript']), 'failed_js': [r.get('suite') for r in result['javascript'] if r['exit_code']]}))
    return int(failed)


if __name__ == '__main__':
    raise SystemExit(main())
