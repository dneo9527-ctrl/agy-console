"""Build a program-only v1 bundle; never uploads or includes installation data."""
import argparse
import hashlib
import json
from pathlib import Path
import zipfile
from update_bootstrap import FILES, version_key


def build(app_dir,out,target):
    release = Path(__file__).with_name('release.json')
    if release.is_file() and json.loads(release.read_text()).get('program_updates_allowed') is False:
        raise ValueError('Program-only updates are disabled for this complete-package upgrade')
    app_dir=Path(app_dir).resolve();out=Path(out).resolve();out.mkdir(parents=True,exist_ok=True)
    if target not in ('macos-arm64','macos-x64','windows-x64','linux-arm64','linux-x64'):raise ValueError('Unsupported platform target')
    version=(app_dir/'VERSION').read_text(encoding='utf-8').strip();version_key(version)
    files={name:app_dir/name for name in sorted(FILES) if (app_dir/name).is_file()}
    for name,path in files.items():
        if path.is_symlink():raise ValueError('Symlink excluded: '+name)
    manifest={'schema':1,'version':version,'target':target,'files':{name:hashlib.sha256(path.read_bytes()).hexdigest() for name,path in files.items()}}
    path=out/('AgyConsole-'+version+'-'+target+'-program.zip')
    with zipfile.ZipFile(path,'w',zipfile.ZIP_DEFLATED,compresslevel=6) as z:
        for name,file in files.items():z.writestr(name,file.read_bytes())
        z.writestr('payload.json',json.dumps(manifest,ensure_ascii=False))
    descriptor={'file':path.name,'sha256':hashlib.sha256(path.read_bytes()).hexdigest(),'bytes':path.stat().st_size}
    output=out/'updates.json';release=json.loads(output.read_text(encoding='utf-8')) if output.exists() else {'schema':1,'version':version,'platforms':{}}
    if release.get('version')!=version:raise ValueError('Output directory contains another release version')
    release['platforms'][target]=descriptor;output.write_text(json.dumps(release,indent=2)+'\n')
    return {'version':version,'target':target,**descriptor}


if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--app-dir',required=True);p.add_argument('--out',required=True);p.add_argument('--target',required=True);a=p.parse_args()
    print(json.dumps(build(a.app_dir,a.out,a.target)))
