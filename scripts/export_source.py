"""Import an explicit frozen file list; never recurse through an installation."""
import argparse
import json
from pathlib import Path
from common import ROOT, manifest, sha256


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--from-dir', type=Path, required=True)
    p.add_argument('--expected', type=Path, required=True, help='JSON mapping relative file name to SHA-256, from developer')
    p.add_argument('--out', type=Path, required=True, help='New empty directory outside live installation and this framework')
    args = p.parse_args()
    source = args.from_dir.resolve()
    target = args.out.resolve()
    if target == ROOT or target == source or source in target.parents or target in source.parents:
        raise ValueError('Output must be a separate new directory')
    expected = json.loads(args.expected.read_text(encoding='utf-8'))
    allowed = set(manifest().get('developer_files', manifest()['runtime_files'])) | {n for n in manifest()['source_files'] if n.startswith('test_')}
    if not expected or set(expected) - allowed:
        raise ValueError('Developer source contains unreviewed file names; update allowlist explicitly first')
    missing = set(manifest()['runtime_files']) - set(expected)
    # Framework SVG is owned here; developer may keep their own unpublished icon.
    missing.discard('icon.svg')
    if missing:
        raise ValueError('Missing required source files: ' + ', '.join(sorted(missing)))
    content = {}
    for name, digest in expected.items():
        file = source / name
        if file.is_symlink() or not file.is_file():
            raise ValueError('Missing source or symlink: ' + name)
        data = file.read_bytes()
        if sha256(data) != digest:
            raise ValueError('Source changed after freeze: ' + name)
        content[name] = (data, 0o755 if file.stat().st_mode & 0o111 else 0o644)
    # Nothing is written until every source digest has been checked.
    target.mkdir(parents=True, exist_ok=False)
    for name, (data, mode) in content.items():
        (target / name).parent.mkdir(parents=True, exist_ok=True)
        (target / name).write_bytes(data)
        (target / name).chmod(mode)
    print(json.dumps({'files': len(content), 'manifest_sha256': sha256(args.expected.read_bytes()), 'status': 'exported_not_published'}))


if __name__ == '__main__':
    main()
