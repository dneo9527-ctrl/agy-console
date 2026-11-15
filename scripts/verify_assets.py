"""Check final developer-provided binaries against a reviewed exact manifest."""
import argparse
import json
import re
from pathlib import Path
from common import sha256


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--dir', type=Path, required=True)
    p.add_argument('--manifest', type=Path, required=True)
    args = p.parse_args()
    data = json.loads(args.manifest.read_text(encoding='utf-8'))
    if not data.get('version') or not re.fullmatch(r'[0-9a-f]{40}', data.get('source_commit', '')):
        raise ValueError('Version and exact source commit required')
    names = []
    for row in data['assets']:
        name = row['name']
        if not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9._-]+', name) or name in names:
            raise ValueError('Unsafe or duplicate asset name')
        file = args.dir / name
        if file.is_symlink() or not file.is_file() or file.stat().st_size != row['bytes']:
            raise ValueError('Missing asset or size mismatch: ' + name)
        if sha256(file.read_bytes()) != row['sha256']:
            raise ValueError('Asset checksum mismatch: ' + name)
        names.append(name)
    if not names:
        raise ValueError('No assets')
    print(json.dumps({'status': 'passed', 'assets': names, 'source_commit': data['source_commit'],
                      'scope': 'Size and byte integrity only; platform installation and package privacy are separate gates'}))


if __name__ == '__main__':
    main()
