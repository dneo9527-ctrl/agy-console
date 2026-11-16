#!/usr/bin/env python3
"""Continue this Codex chat through the explicitly installed local bridge."""
import json
import os
from pathlib import Path
import re
import subprocess
import sys


def main():
    thread = os.environ.get('CODEX_THREAD_ID', '')
    if not re.fullmatch(r'[a-f0-9]{8}(?:-[a-f0-9]{4}){3}-[a-f0-9]{12}', thread):
        raise ValueError('Run from the current Codex chat with CODEX_THREAD_ID set; no chat was inferred.')
    if any(arg == '--thread-id' or arg.startswith('--thread-id=') for arg in sys.argv[1:]):
        raise ValueError('The companion dispatcher always uses the current CODEX_THREAD_ID.')
    file = Path(__file__).resolve().parents[1] / 'connection.json'
    if file.is_symlink() or not file.is_file() or file.stat().st_size > 16384:
        raise ValueError('Install or refresh the companion skill from the console package first.')
    config = json.loads(file.read_text(encoding='utf-8'))
    if config.get('schema') != 1:
        raise ValueError('Unsupported companion connection format.')
    python, bridge = Path(config['python']), Path(config['bridge'])
    if not python.is_absolute() or not python.is_file() or not bridge.is_absolute() or not bridge.is_file() or bridge.name != 'bridge.py':
        raise ValueError('The configured console moved or is unavailable; reinstall the companion skill.')
    return subprocess.call([str(python), str(bridge), *sys.argv[1:]])


if __name__ == '__main__':
    try:
        raise SystemExit(main())
    except (OSError, ValueError, KeyError, TypeError) as error:
        print(str(error), file=sys.stderr)
        raise SystemExit(1)
