"""Rebuild platform previews from reviewed candidate plus verified vendor runtime."""
import argparse
import hashlib
import json
from pathlib import Path, PurePosixPath
import stat
import sys
import zipfile
from common import ROOT, source_files, manifest, sha256
from audit import run as audit
sys.path.insert(0, str(ROOT))
from update_bootstrap import FILES, verify_stage

VERSION = json.loads((ROOT/'release.json').read_text(encoding='utf-8'))['version']


def write_zip(path, prefix, files, modes=None):
    modes = modes or {}
    with zipfile.ZipFile(path, 'x', zipfile.ZIP_DEFLATED, compresslevel=6) as archive:
        for name, data in sorted(files.items()):
            entry = zipfile.ZipInfo(prefix + name, (2020, 1, 1, 0, 0, 0))
            entry.compress_type = zipfile.ZIP_DEFLATED
            entry.create_system = 3
            entry.external_attr = modes.get(name, 0o100644) << 16
            archive.writestr(entry, data)
    with zipfile.ZipFile(path) as archive:
        if archive.testzip() is not None: raise ValueError('Archive CRC error')
        if len(archive.namelist()) != len(files): raise ValueError('Archive membership changed')
        for name, data in files.items():
            if archive.read(prefix + name) != data: raise ValueError('Archive byte mismatch')


def build(out, vendor_archive, expected_vendor_digest, windows_only=False, include_program_updates=True):
    if audit()['status'] != 'passed': raise ValueError('Source audit failed')
    if sha256(vendor_archive.read_bytes()) != expected_vendor_digest:
        raise ValueError('Windows handoff runtime carrier digest mismatch')
    # Only vendor runtime files enter the build. Never use a live installation.
    runtime = {}
    with zipfile.ZipFile(vendor_archive) as archive:
        for info in archive.infolist():
            path = PurePosixPath(info.filename)
            if len(path.parts) < 3 or path.parts[1] != 'runtime': continue
            if path.is_absolute() or '..' in path.parts or stat.S_ISLNK(info.external_attr >> 16):
                raise ValueError('Unsafe vendor runtime entry')
            if not info.is_dir(): runtime['/'.join(path.parts[1:])] = archive.read(info)
    if not runtime or 'runtime/LICENSE.txt' not in runtime:
        raise ValueError('Windows runtime or upstream license missing')
    out.mkdir(parents=True, exist_ok=False)
    rows = list(source_files())
    source = {n: p.read_bytes() for n,p in rows}
    # Platform source bundles retain tests/docs and full updater bootstrap, not Windows files.
    posix = {n:data for n,data in source.items() if not n.startswith('windows/')}
    modes = {n: 0o100755 if p.stat().st_mode & 0o111 else 0o100644 for n,p in rows}
    for platform in (() if windows_only else ('macos','linux')):
        write_zip(out/('AgyConsole-'+VERSION+'-'+platform+'-source-preview.zip'),
                  'AgyConsole-'+VERSION+'/', posix, modes)
    windows = {n[len('windows/'):]:data for n,data in source.items()
               if n.startswith('windows/') and not n.startswith('windows/installer/')}
    windows.update(runtime)
    for name in ('LICENSE','docs/PRIVACY.md','docs/VERIFICATION.md','docs/THIRD-PARTY.md'):
        if name in source: windows[name] = source[name]
    windows['SHA256SUMS.txt'] = ''.join(sha256(data)+'  '+n+'\n' for n,data in sorted(windows.items())).encode()
    write_zip(out/('AgyConsole-'+VERSION+'-windows-x64-preview.zip'), 'AgyConsole-Windows-x64/', windows)
    update_manifest = {'schema':1,'version':VERSION,'platforms':{}}
    targets = ('windows-x64',) if windows_only else ('macos-arm64','macos-x64','linux-arm64','linux-x64','windows-x64')
    for target in (targets if include_program_updates else ()):
        app = ROOT/'windows/app' if target == 'windows-x64' else ROOT
        payload = {n:(app/n).read_bytes() for n in sorted(FILES) if (app/n).is_file()}
        expected = set(manifest()['program_files'][target])
        if set(payload) != expected: raise ValueError('Program payload incomplete: '+target)
        payload['payload.json'] = json.dumps({'schema':1,'version':VERSION,'target':target,
            'files':{n:sha256(data) for n,data in payload.items()}}, ensure_ascii=False).encode()
        name = 'AgyConsole-'+VERSION+'-'+target+'-program.zip'
        write_zip(out/name, '', payload)
        path = out/name
        update_manifest['platforms'][target] = {'file':name,'sha256':sha256(path.read_bytes()),'bytes':path.stat().st_size}
    if include_program_updates:
        (out/'updates.json').write_text(json.dumps(update_manifest,indent=2)+'\n', encoding='utf-8')
    assets = [{'name':p.name,'bytes':p.stat().st_size,'sha256':sha256(p.read_bytes())} for p in sorted(out.iterdir())]
    (out/'SHA256SUMS').write_text(''.join(a['sha256']+'  '+a['name']+'\n' for a in assets), encoding='utf-8')
    return {'version':VERSION,'assets':assets,'runtime_carrier_sha256':expected_vendor_digest,
            'source_config':json.loads((ROOT/'release-source.json').read_text()),'published':False}


if __name__ == '__main__':
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--out',type=Path,required=True)
    p.add_argument('--windows-runtime-carrier',type=Path,required=True)
    p.add_argument('--carrier-sha256',required=True)
    p.add_argument('--report',type=Path,required=True)
    p.add_argument('--windows-only',action='store_true',help='Publish only Windows; leave Mac/Linux releases unchanged')
    a=p.parse_args()
    result=build(a.out,a.windows_runtime_carrier,a.carrier_sha256,a.windows_only)
    a.report.parent.mkdir(parents=True,exist_ok=True)
    a.report.write_text(json.dumps(result,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
    print(json.dumps({'version':VERSION,'assets':len(result['assets']),'source_config':result['source_config'],'published':False}))
