"""Small shared helpers; never read a live installation's private state."""
import hashlib
import json
from pathlib import Path, PurePosixPath

ROOT = Path(__file__).resolve().parents[1]


def sha256(data):
    return hashlib.sha256(data).hexdigest()


def manifest():
    return json.loads((ROOT / 'release-files.json').read_text(encoding='utf-8'))


def safe_path(name):
    p = PurePosixPath(name)
    if (not name or '\\' in name or p.is_absolute() or '..' in p.parts
            or any(part.startswith('.') for part in p.parts)
            and name not in ('.gitignore', '.gitattributes', '.github/workflows/checks.yml', '.github/workflows/package.yml')):
        raise ValueError('Unsafe or hidden release path')
    return p


def source_files():
    rows = manifest()['source_files']
    if len(rows) != len(set(rows)):
        raise ValueError('Duplicate manifest paths')
    for name in rows:
        safe_path(name)
        p = ROOT / name
        if p.is_symlink() or not p.is_file() or any(parent.is_symlink() for parent in p.parents if parent != ROOT.parent):
            raise ValueError('Missing file or symlink: ' + name)
        yield name, p


def config():
    return json.loads((ROOT / 'release.json').read_text(encoding='utf-8'))
