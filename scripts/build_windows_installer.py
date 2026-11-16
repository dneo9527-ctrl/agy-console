"""Build a real Windows installer using Inno Setup and the verified .7 runtime."""
import argparse
import hashlib
import json
import os
import re
from pathlib import Path
import shutil
import subprocess
import sys
import time
import urllib.request
import zipfile
from common import ROOT, config, sha256
from package_release import build

CARRIER_URL = 'https://github.com/dneo9527-ctrl/agy-console/releases/download/v0.1.0-preview.7/AgyConsole-0.1.0-preview.7-windows-x64-preview.zip'
CARRIER_SHA256 = 'a8574f901b58bc6574b51b1787381213410749569da801dd7d1eb163340a4d5b'


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--out', type=Path, required=True)
    parser.add_argument('--iscc', type=Path, required=True)
    args = parser.parse_args()
    if os.name != 'nt':
        raise RuntimeError('Build this EXE on Windows; no placeholder binaries are produced.')
    compiler = args.iscc.resolve()
    info = subprocess.run([str(compiler), '/?'], capture_output=True, text=True, errors='replace')
    compiler_text = info.stdout + info.stderr
    if not re.search(r'^Inno Setup\s+6\b', compiler_text, re.MULTILINE):
        raise ValueError('This installer is reviewed for Inno Setup 6 (32-bit setup ABI). Review the ABI before changing compiler major version.')
    output = args.out.resolve()
    build_root = output.parent / 'installer-build'
    build_root.mkdir(parents=True, exist_ok=False)
    carrier = build_root / 'verified-runtime-carrier.zip'
    for attempt in range(3):
        try:
            request = urllib.request.Request(CARRIER_URL, headers={'User-Agent': 'AgyConsole-release-build'})
            with urllib.request.urlopen(request, timeout=60) as response, carrier.open('wb') as stream:
                shutil.copyfileobj(response, stream)
            if sha256(carrier.read_bytes()) != CARRIER_SHA256:
                raise ValueError('Runtime carrier digest mismatch')
            break
        except (OSError, ValueError):
            if attempt == 2:
                raise
            time.sleep(2)
    # This version changes the stable launcher/installation layout. Do not
    # advertise a program-only update that cannot install those components.
    packaged = build(output, carrier, CARRIER_SHA256, windows_only=True, include_program_updates=False)
    subprocess.run([sys.executable, '-B', str(ROOT / 'scripts/package_source.py'), '--out', str(output)], check=True, timeout=60)
    portable = next(output.glob('*-windows-x64-preview.zip'))
    payload = build_root / 'payload'
    payload.mkdir()
    runtime = {}
    with zipfile.ZipFile(portable) as archive:
        assert archive.testzip() is None
        for item in archive.infolist():
            parts = Path(item.filename).parts
            if '..' in parts or len(parts) < 2:
                raise ValueError('Unsafe portable member')
            relative = Path(*parts[1:])
            target = payload / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(archive.read(item))
            if parts[1] == 'runtime':
                runtime[relative.as_posix()] = sha256(target.read_bytes())
    licenses = [p for p in compiler.parent.iterdir() if p.name.lower() in ('license.txt', 'license.rtf')]
    if not licenses:
        raise ValueError('Inno Setup compiler license was not found')
    for license_file in licenses:
        destination = payload / 'licenses' / ('Inno-Setup-' + license_file.name)
        destination.parent.mkdir(exist_ok=True)
        shutil.copyfile(license_file, destination)
    version = config()['version']
    command = [str(compiler), '/DAppVersion=' + version, '/DPayloadDir=' + str(payload),
               '/DOutputDir=' + str(output), str(ROOT / 'windows/installer/AgyConsole.iss')]
    subprocess.run(command, check=True, timeout=300)
    setup = output / ('AgyConsole-' + version + '-windows-x64-setup.exe')
    if not setup.is_file() or setup.read_bytes()[:2] != b'MZ':
        raise ValueError('The native compiler did not produce a Windows PE installer')
    report = {'schema': 1, 'version': version, 'native_platform': 'Windows x64',
              'source_commit': subprocess.check_output(['git', 'rev-parse', 'HEAD'], cwd=ROOT, text=True).strip(),
              'compiler': {'name': 'Inno Setup', 'sha256': sha256(compiler.read_bytes()),
                           'version_banner': compiler_text.splitlines()[:3]},
              'runtime_carrier': {'url': CARRIER_URL, 'sha256': CARRIER_SHA256},
              'runtime_files': runtime, 'installer': {'name': setup.name, 'bytes': setup.stat().st_size, 'sha256': sha256(setup.read_bytes())},
              'portable_packaging': packaged, 'signed_by_publisher': False,
              'program_only_update_published': False,
              'scope': 'Complete Windows installer/portable release only; native lifecycle tests are separate; no official login/model call'}
    (output / 'windows-build.json').write_text(json.dumps(report, indent=2) + '\n', encoding='utf-8')
    rows = [(p.name, sha256(p.read_bytes())) for p in sorted(output.iterdir()) if p.name != 'SHA256SUMS']
    (output / 'SHA256SUMS').write_text(''.join(digest + '  ' + name + '\n' for name, digest in rows), encoding='utf-8')
    print(json.dumps({'native_installer': setup.name, 'sha256': report['installer']['sha256'], 'runtime_files': len(runtime)}))


if __name__ == '__main__':
    main()
