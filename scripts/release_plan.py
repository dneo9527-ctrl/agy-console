"""Read-only release readiness report. No GitHub mutations, tags or uploads."""
import argparse
import json
import re
import subprocess
from pathlib import Path
from common import ROOT, config, sha256, safe_path, manifest


def blockers(c):
    issues = []
    if c.get('status') != 'ready': issues.append('status is not ready')
    if not re.fullmatch(r'\d+\.\d+\.\d+(?:-[0-9A-Za-z]+(?:[.-][0-9A-Za-z]+)*)?', str(c.get('version') or '')):
        issues.append('final version missing or invalid')
    if not re.fullmatch(r'[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+', str(c.get('repository') or '')):
        issues.append('repository not agreed')
    if c.get('visibility') not in ('public', 'private'): issues.append('visibility not authorized')
    if not c.get('license') or not (ROOT / 'LICENSE').is_file(): issues.append('license/attribution not approved')
    if not c.get('authorization_reference'): issues.append('project authorization reference missing')
    if not c.get('source_frozen'): issues.append('final source not frozen')
    frozen = ROOT / 'source-freeze.json'
    if not frozen.is_file() or c.get('source_manifest_sha256') != sha256(frozen.read_bytes()):
        issues.append('source freeze evidence missing or changed')
    else:
        rows = json.loads(frozen.read_text(encoding='utf-8'))
        if not rows or not set(manifest()['runtime_files']).issubset(rows):
            issues.append('frozen manifest is incomplete')
        for name, digest in rows.items():
            safe_path(name)
            if name not in manifest()['source_files']:
                issues.append('unreviewed file in freeze manifest: ' + name)
                continue
            file = ROOT / name
            if not file.is_file() or file.is_symlink() or sha256(file.read_bytes()) != digest:
                issues.append('frozen source changed: ' + name)
    if c.get('updater_contract') != 'agreed': issues.append('updater contract not agreed')
    if c.get('publish_authorized') is not True: issues.append('publication not authorized for this release')
    return issues


if __name__ == '__main__':
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--report', type=Path)
    args = p.parse_args()
    c = config()
    issues = blockers(c)
    proc = subprocess.run(['git', 'rev-parse', '--show-toplevel'], cwd=ROOT, capture_output=True, text=True)
    head = None
    if proc.returncode or Path(proc.stdout.strip()).resolve() != ROOT:
        issues.append('candidate has no own Git repository / exact commit')
    else:
        head = subprocess.check_output(['git', 'rev-parse', 'HEAD'], cwd=ROOT, text=True).strip()
        if subprocess.check_output(['git', 'status', '--porcelain'], cwd=ROOT): issues.append('candidate has uncommitted changes')
    value = {'ready_for_release_review': not issues, 'version': c.get('version'), 'repository': c.get('repository'),
             'head_sha': head, 'blockers': issues, 'external_actions_performed': False,
             'next_gate': 'Exact-SHA remote CI, artifact review and authorized draft/publish sequence; see docs/RELEASING.md'}
    if args.report:
        args.report.parent.mkdir(parents=True, exist_ok=True)
        args.report.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding='utf-8')
    print(json.dumps(value, ensure_ascii=False, indent=2))
    raise SystemExit(2 if issues else 0)
