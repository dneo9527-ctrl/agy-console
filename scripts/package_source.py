"""Deterministic allowlisted source archive; rehearsal is visibly non-release."""
import argparse
import json
from pathlib import Path
import zipfile
from common import ROOT, config, source_files, sha256
from audit import run as audit
from release_plan import blockers


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--out', type=Path, required=True)
    p.add_argument('--rehearsal', action='store_true')
    args = p.parse_args()
    c = config()
    if not args.rehearsal and blockers(c):
        raise ValueError('Release is not ready. Use --rehearsal only for framework checks.')
    report = audit()
    if report['status'] != 'passed':
        raise ValueError('Source audit failed; no archive was written')
    version = 'framework-check' if args.rehearsal else c['version']
    prefix = 'AgyConsole-' + version + '-source'
    inputs = list(source_files())
    files = {name: path.read_bytes() for name, path in inputs}
    modes = {name: 0o100755 if path.stat().st_mode & 0o111 else 0o100644 for name, path in inputs}
    sums = {name: sha256(data) for name, data in files.items()}
    files['PACKAGE-MANIFEST.json'] = json.dumps({'version': version, 'kind': 'source',
        'rehearsal': args.rehearsal, 'files': sums}, sort_keys=True, indent=2).encode('utf-8')
    args.out.mkdir(parents=True, exist_ok=True)
    output = args.out / (prefix + '.zip')
    with zipfile.ZipFile(output, 'x', compression=zipfile.ZIP_DEFLATED, compresslevel=6) as archive:
        for name, data in sorted(files.items()):
            info = zipfile.ZipInfo(prefix + '/' + name, (2020, 1, 1, 0, 0, 0))
            info.compress_type = zipfile.ZIP_DEFLATED
            info.create_system = 3
            mode = modes.get(name, 0o100644)
            info.external_attr = mode << 16
            archive.writestr(info, data)
    digest = sha256(output.read_bytes())
    (args.out / (output.name + '.sha256')).write_text(digest + '  ' + output.name + '\n', encoding='utf-8')
    print(json.dumps({'file': output.name, 'sha256': digest, 'bytes': output.stat().st_size,
                      'rehearsal': args.rehearsal, 'published': False}))


if __name__ == '__main__':
    main()
