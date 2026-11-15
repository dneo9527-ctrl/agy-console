"""Verify source archive bytes without extracting or executing any content."""
import argparse
import json
from pathlib import Path, PurePosixPath
import stat
import zipfile
from common import sha256
from audit import findings, FORBIDDEN


def verify(file, expected):
    if sha256(file.read_bytes()) != expected:
        raise ValueError('Archive SHA-256 mismatch')
    with zipfile.ZipFile(file) as archive:
        names = archive.namelist()
        if len(set(names)) != len(names) or len(names) > 2000:
            raise ValueError('Duplicate entries or too many files')
        prefixes = set()
        for entry in archive.infolist():
            path = PurePosixPath(entry.filename)
            if (path.is_absolute() or '..' in path.parts or '\\' in entry.filename
                    or not path.parts or stat.S_ISLNK(entry.external_attr >> 16)
                    or FORBIDDEN.intersection(path.parts) or entry.file_size > 32 * 1024 * 1024):
                raise ValueError('Unsafe archive member')
            prefixes.add(path.parts[0])
        if len(prefixes) != 1 or sum(e.file_size for e in archive.infolist()) > 64 * 1024 * 1024:
            raise ValueError('Invalid archive layout or size')
        prefix = next(iter(prefixes)) + '/'
        meta = json.loads(archive.read(prefix + 'PACKAGE-MANIFEST.json'))
        if set(names) != {prefix + n for n in meta['files']} | {prefix + 'PACKAGE-MANIFEST.json'}:
            raise ValueError('Manifest membership mismatch')
        for name, expected_hash in meta['files'].items():
            data = archive.read(prefix + name)
            if sha256(data) != expected_hash or findings(data, name):
                raise ValueError('Content mismatch or privacy rule: ' + name)
    return {'status': 'passed', 'kind': meta['kind'], 'version': meta['version'],
            'rehearsal': meta['rehearsal'], 'files': len(meta['files']), 'sha256': expected}


if __name__ == '__main__':
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('archive', type=Path)
    p.add_argument('--sha256', required=True)
    args = p.parse_args()
    print(json.dumps(verify(args.archive, args.sha256), indent=2))
