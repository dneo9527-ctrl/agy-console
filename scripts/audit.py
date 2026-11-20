"""Allowlist and limited secret-pattern audit. Findings never include values."""
import argparse
import json
import re
import subprocess
import sys
from pathlib import Path
from common import ROOT, source_files

RULES = {
    'private_key': re.compile(rb'-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----'),
    'github_token': re.compile(rb'(?:gh[pousr]_[A-Za-z0-9]{30,}|github_pat_[A-Za-z0-9_]{40,})'),
    'google_api_key': re.compile(rb'AIza[0-9A-Za-z_-]{30,}'),
    'personal_home_path': re.compile(rb'/(?:Users|home)/[A-Za-z0-9_.-]+/'),
    'credential_url': re.compile(rb'https?://[^\s/<>:@]+:[^\s/<>@]+@'),
}
FORBIDDEN = {'data', 'reviews', 'backups', '.gemini', '.codex', '__pycache__', 'node_modules'}


def findings(data, label):
    # These exact synthetic rejection fixtures are not credentials. No general suppression.
    if label.endswith('test_proxy_env.py'):
        data = data.replace(b'http://' + b'example-user:example-password@127.0.0.1:9000', b'EXPLICIT_SYNTHETIC_FIXTURE')
    if label.endswith('test_software_updates.py'):
        data = data.replace(b'https://' + b'u:p@github.com/x', b'EXPLICIT_SYNTHETIC_REJECTION_FIXTURE')
    if label.endswith('test_markdown_fluency.js'):
        # Exact fabricated basename-rendering fixtures; arbitrary home paths still fail.
        data = data.replace(b'/Users/' + b'local/output.png', b'EXPLICIT_SYNTHETIC_IMAGE_FIXTURE')
        data = data.replace(b'file:///Users/' + b'local/hello%20world.PNG?download=1#view', b'EXPLICIT_SYNTHETIC_IMAGE_FIXTURE')
    return [{'file': label, 'rule': name} for name, rule in RULES.items() if rule.search(data)]


def history(root):
    probe = subprocess.run(['git', 'rev-parse', '--show-toplevel'], cwd=root, capture_output=True, text=True)
    if probe.returncode or Path(probe.stdout.strip()).resolve() != root.resolve():
        return {'status': 'not_run', 'reason': 'No repository rooted at candidate'}, []
    rows = subprocess.check_output(['git', 'rev-list', '--objects', '--all'], cwd=root).splitlines()
    issues, blobs = [], 0
    names = {row.partition(b' ')[0]: row.partition(b' ')[2].decode('utf-8', errors='replace') for row in rows}
    if not names:
        return {'status': 'passed', 'blobs': 0}, []
    metadata = subprocess.check_output(['git', 'cat-file', '--batch-check'], cwd=root,
                                       input=b'\n'.join(names) + b'\n').splitlines()
    requested = []
    for row in metadata:
        oid, kind, raw_size = row.split()
        if kind != b'blob':
            continue
        if int(raw_size) > 32 * 1024 * 1024:
            issues.append({'file': names[oid], 'rule': 'history_blob_too_large_for_audit'})
        else:
            requested.append(oid)
    # Length-framed Git output preserves arbitrary file bytes and avoids spawning
    # several interpreter processes per blob on CI. Every reachable blob is covered.
    raw = subprocess.check_output(['git', 'cat-file', '--batch'], cwd=root,
                                  input=b'\n'.join(requested) + b'\n') if requested else b''
    cursor = 0
    for expected_oid in requested:
        end = raw.index(b'\n', cursor)
        oid, kind, raw_size = raw[cursor:end].split()
        size = int(raw_size)
        if oid != expected_oid or kind != b'blob':
            raise ValueError('Unexpected Git object frame')
        data = raw[end + 1:end + 1 + size]
        if len(data) != size or raw[end + 1 + size:end + 2 + size] != b'\n':
            raise ValueError('Truncated Git blob')
        cursor = end + size + 2
        name = names[oid]
        issues += findings(data, name)
        if FORBIDDEN.intersection(Path(name).parts):
            issues.append({'file': name, 'rule': 'private_state_in_history'})
        blobs += 1
    return {'status': 'passed' if not issues else 'failed', 'blobs': blobs}, issues


def run(include_history=False):
    issues, count = [], 0
    for name, p in source_files():
        if FORBIDDEN.intersection(Path(name).parts) or p.suffix.lower() in {'.png', '.jpg', '.sqlite', '.db', '.log', '.zip'}:
            issues.append({'file': name, 'rule': 'disallowed_release_file'})
        issues += findings(p.read_bytes(), name)
        count += 1
    hist, extra = history(ROOT) if include_history else ({'status': 'not_run'}, [])
    issues += extra
    return {'status': 'passed' if not issues else 'failed', 'files': count, 'history': hist,
            'issues': issues, 'scope': 'Allowlisted source and optional all reachable Git blobs; limited patterns only'}


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--history', action='store_true')
    parser.add_argument('--report', type=Path)
    args = parser.parse_args()
    report = run(args.history)
    if args.report:
        args.report.parent.mkdir(parents=True, exist_ok=True)
        args.report.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
    print(json.dumps(report, ensure_ascii=False))
    sys.exit(report['status'] != 'passed')
