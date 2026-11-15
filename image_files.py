"""Collect raster artifacts from this task only; never fetch remote image URLs."""
import hashlib
import os
from pathlib import Path
import re
import stat
import struct
from urllib.parse import unquote, urlsplit

MAX_IMAGE_BYTES = 25 * 1024 * 1024
MAX_TURN_BYTES = 80 * 1024 * 1024
MAX_IMAGES = 8
EXTENSIONS = {'.png', '.jpg', '.jpeg', '.webp', '.gif'}


def image_type(head):
    if head.startswith(b'\x89PNG\r\n\x1a\n') and head[12:16] == b'IHDR':
        width, height = struct.unpack('>II', head[16:24])
        return 'image/png', '.png', width, height
    if head.startswith(b'\xff\xd8\xff'):
        return 'image/jpeg', '.jpg', None, None
    if head[:6] in (b'GIF87a', b'GIF89a'):
        width, height = struct.unpack('<HH', head[6:10])
        return 'image/gif', '.gif', width, height
    if head[:4] == b'RIFF' and head[8:12] == b'WEBP':
        return 'image/webp', '.webp', None, None
    return None


def references(text):
    # Markdown image/link destinations and code-quoted absolute/relative paths.
    tokens = re.findall(r'!?\[[^\]]*\]\(\s*(<[^>]+>|[^\n)]+)\s*\)', text or '')
    tokens += re.findall(r'`([^`\n]+)`', text or '')
    paths = []
    for token in tokens:
        token = token.strip().strip('<>')
        if token.startswith('file://'):
            parsed = urlsplit(token)
            if parsed.netloc not in ('', 'localhost'):
                continue
            token = unquote(parsed.path)
        elif '://' in token or token.startswith('data:'):
            continue
        else:
            token = unquote(token)
        if Path(token).suffix.lower() in EXTENSIONS:
            paths.append(token)
    return paths


def image_snapshot(roots):
    versions = set()
    for root in roots:
        if root is None or not Path(root).is_dir():
            continue
        for current, dirs, files in os.walk(root, followlinks=False):
            dirs[:] = [d for d in dirs if not d.startswith('.') and not (Path(current) / d).is_symlink()]
            for name in files:
                path = Path(current) / name
                if path.suffix.lower() not in EXTENSIONS or path.is_symlink():
                    continue
                try:
                    info = path.stat()
                    versions.add((str(path.resolve()), info.st_size, info.st_mtime_ns))
                except OSError:
                    pass
    return versions


def collect_images(workspace, artifact_dir, destination, turn_id, response, started_at, previous_files=None):
    """Copy newly produced/referenced images into a bounded, immutable turn folder."""
    workspace, destination = Path(workspace).resolve(), Path(destination)
    roots = [workspace]
    if artifact_dir is not None:
        roots.append(Path(artifact_dir).resolve())
    candidates = [Path(p) if Path(p).is_absolute() else workspace / p for p in references(response)]
    for root in roots:
        if not root.is_dir():
            continue
        for current, dirs, files in os.walk(root, followlinks=False):
            dirs[:] = [d for d in dirs if not d.startswith('.') and not (Path(current) / d).is_symlink()]
            for name in files:
                path = Path(current) / name
                if path.suffix.lower() in EXTENSIONS:
                    candidates.append(path)
            if len(candidates) > 512:
                break
    manifest, seen, total = [], set(), 0
    for source in candidates:
        if len(manifest) >= MAX_IMAGES:
            break
        try:
            if source.is_symlink():
                continue
            resolved = source.resolve(strict=True)
            if resolved in seen or not any(root in resolved.parents for root in roots):
                continue
            seen.add(resolved)
            descriptor = os.open(str(resolved), os.O_RDONLY | getattr(os, 'O_NOFOLLOW', 0))
            with os.fdopen(descriptor, 'rb') as stream:
                info = os.fstat(stream.fileno())
                if previous_files is not None and (str(resolved), info.st_size, info.st_mtime_ns) in previous_files:
                    continue
                if not stat.S_ISREG(info.st_mode) or info.st_size > MAX_IMAGE_BYTES or info.st_size < 16:
                    continue
                if info.st_mtime < started_at - 2 or total + info.st_size > MAX_TURN_BYTES:
                    continue
                kind = image_type(stream.read(64))
                if not kind:
                    continue
                mime, extension, width, height = kind
                if width is not None and (width < 1 or height < 1 or width * height > 50_000_000):
                    continue
                stream.seek(0)
                digest = hashlib.sha256()
                destination.mkdir(parents=True, exist_ok=True)
                temporary = destination / '.incoming'
                copied = 0
                try:
                    with temporary.open('wb') as target:
                        while True:
                            chunk = stream.read(256 * 1024)
                            if not chunk:
                                break
                            copied += len(chunk)
                            if copied > MAX_IMAGE_BYTES or total + copied > MAX_TURN_BYTES:
                                raise ValueError('image exceeds limit')
                            target.write(chunk)
                            digest.update(chunk)
                    image_id = digest.hexdigest()
                    stored = destination / (image_id + extension)
                    temporary.replace(stored)
                finally:
                    if temporary.exists():
                        temporary.unlink()
            if any(item['id'] == image_id for item in manifest):
                continue
            url = '/api/turns/' + turn_id + '/images/' + image_id
            manifest.append({'id': image_id, 'name': resolved.name[:160], 'mime': mime, 'bytes': copied,
                             'width': width, 'height': height, 'url': url, 'download_url': url + '?download=1',
                             'file': stored.name})
            total += copied
        except (OSError, ValueError, struct.error):
            continue
    return manifest


def manifest_file(directory, image):
    """Resolve only an already collected immutable file, with no symlink escape."""
    directory = Path(directory).resolve()
    name = image.get('file', '')
    if not re.fullmatch(r'[a-f0-9]{64}\.(?:png|jpg|gif|webp)', name):
        raise FileNotFoundError('invalid image')
    path = directory / name
    if path.is_symlink() or path.resolve().parent != directory:
        raise FileNotFoundError('invalid image')
    return path
