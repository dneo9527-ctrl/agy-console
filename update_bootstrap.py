"""Stable v1 update bootstrap. Never updates itself, runtimes or user data."""
import hashlib
import json
import os
from pathlib import Path
import re
import runpy
import shutil
import sys
import tempfile

FILES = frozenset(('VERSION index.html app.js style.css panel_actions.js icon.svg server.py '
 'account_status.py activity_monitor.py bridge.py collaboration_review.py conversation_sessions.py '
 'foreground_reload.py headless_compat.py image_files.py model_policy.py multi_ai_collaboration.py panel_actions.py '
 'panel_backups.py transcript_export.py proxy_env.py quota_failover.py resources.py settings_insights.py task_progress.py '
 'task_queue.py windows_support.py software_updates.py').split())
MAX_BYTES = 32 * 1024 * 1024


def version_key(value):
    match=re.fullmatch(r'(0|[1-9]\d*)\.(0|[1-9]\d*)\.(0|[1-9]\d*)(?:-([0-9A-Za-z.-]+))?',value) if isinstance(value,str) else None
    if not match:raise ValueError('版本号格式无效。')
    base=tuple(int(x) for x in match.groups()[:3]);pre=match.group(4)
    return base+(1 if pre is None else 0,tuple((0,int(x)) if x.isdigit() else (1,x) for x in (pre or '').split('.')))


def replace_copy(source,destination):
    with tempfile.NamedTemporaryFile(dir=destination.parent,prefix='.agy-update-',delete=False) as stream:
        temporary=Path(stream.name)
        try:
            with source.open('rb') as incoming:shutil.copyfileobj(incoming,stream)
            stream.flush();os.fsync(stream.fileno())
        except BaseException:temporary.unlink(missing_ok=True);raise
    try:os.replace(temporary,destination)
    finally:temporary.unlink(missing_ok=True)


def atomic_json(path, value):
    path = Path(path);path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + '.tmp')
    with temporary.open('w', encoding='utf-8') as stream:
        json.dump(value, stream, ensure_ascii=False);stream.flush();os.fsync(stream.fileno())
    os.replace(temporary, path)


def verify_stage(stage, expected_target=None, expected_version=None):
    stage = Path(stage)
    if stage.is_symlink() or not stage.is_dir():raise ValueError('更新暂存目录无效。')
    path = stage/'payload.json'
    if path.is_symlink() or not path.is_file() or path.stat().st_size > 32768:raise ValueError('更新清单无效。')
    manifest = json.loads(path.read_text(encoding='utf-8'))
    files = manifest.get('files')
    if (manifest.get('schema') != 1 or not isinstance(files, dict) or not files
            or not set(files).issubset(FILES) or 'VERSION' not in files
            or expected_target is not None and manifest.get('target') != expected_target
            or expected_version is not None and manifest.get('version') != expected_version):
        raise ValueError('更新版本、平台或文件范围不匹配。')
    if {p.name for p in stage.iterdir()} != set(files)|{'payload.json'}:raise ValueError('更新包含额外文件。')
    total = 0
    for name, digest in files.items():
        p=stage/name
        if (not isinstance(digest,str) or not re.fullmatch('[a-f0-9]{64}',digest)
                or p.is_symlink() or not p.is_file() or p.stat().st_size > MAX_BYTES):raise ValueError('更新文件无效。')
        raw=p.read_bytes();total+=len(raw)
        if total > MAX_BYTES or hashlib.sha256(raw).hexdigest()!=digest:raise ValueError('更新文件校验失败。')
        if name.endswith('.py'):compile(raw,str(p),'exec')
    if (stage/'VERSION').read_text(encoding='utf-8').strip()!=manifest.get('version'):raise ValueError('更新版本号不一致。')
    return manifest


def location(app_root, data):
    app_root=Path(app_root).resolve();cache=Path(data).resolve()/'software-updates'
    if cache.is_symlink():raise ValueError('更新目录不能是符号链接。')
    cache.mkdir(parents=True,exist_ok=True)
    return app_root,cache


def recover(app_root, data):
    root,cache=location(app_root,data);journal=cache/'transaction.json'
    if not journal.exists():return False
    value=json.loads(journal.read_text(encoding='utf-8'));files=value.get('files');backup=cache/'backup'
    if value.get('root')!=str(root) or not isinstance(files,dict) or not set(files).issubset(FILES):raise ValueError('更新回滚日志无效。')
    for name,existed in files.items():
        destination=root/name;saved=backup/name
        if destination.is_symlink() or saved.is_symlink():raise ValueError('更新回滚路径异常。')
        if existed is True:
            if not saved.is_file():raise ValueError('更新回滚备份缺失，未启动。')
            replace_copy(saved,destination)
        elif existed is False:destination.unlink(missing_ok=True)
        else:raise ValueError('回滚文件状态无效。')
    journal.unlink();(cache/'pending.json').unlink(missing_ok=True)
    atomic_json(cache/'last-result.json',{'status':'rolled_back','version':value.get('version'),'detail':'上次更新未完成启动，已恢复原程序。'})
    return True


def apply_pending(app_root, data, target):
    root,cache=location(app_root,data)
    if recover(root,data):return False
    pending=cache/'pending.json'
    if not pending.exists():return False
    value=json.loads(pending.read_text(encoding='utf-8'));stage=cache/'stage'
    manifest=verify_stage(stage,target,value.get('version'))
    if version_key(manifest['version'])<=version_key((root/'VERSION').read_text(encoding='utf-8').strip()):raise ValueError('更新版本不能低于或等于当前版本。')
    backup=cache/'backup'
    if backup.exists():shutil.rmtree(backup)
    backup.mkdir()
    existed={}
    for name in manifest['files']:
        destination=root/name
        if destination.is_symlink() or destination.exists() and not destination.is_file():raise ValueError('安装目录含异常文件。')
        existed[name]=destination.is_file()
        if existed[name]:shutil.copyfile(destination,backup/name)
    atomic_json(cache/'transaction.json',{'root':str(root),'files':existed,'version':manifest['version']})
    try:
        for name in manifest['files']:
            replace_copy(stage/name,root/name)
    except BaseException:
        recover(root,data);raise
    return True  # The new service must confirm successful initialization.


def confirm(app_root, data):
    root,cache=location(app_root,data);journal=cache/'transaction.json'
    if not journal.exists():return
    value=json.loads(journal.read_text(encoding='utf-8'))
    if value.get('root')!=str(root):raise ValueError('更新启动确认路径不匹配。')
    atomic_json(cache/'last-result.json',{'status':'installed','version':value['version']})
    # Removing the journal is the commit point; a crash before it rolls back.
    journal.unlink();(cache/'pending.json').unlink(missing_ok=True)


def platform_target():
    import platform
    system={'Darwin':'macos','Windows':'windows','Linux':'linux'}.get(platform.system(),'unknown')
    arch={'arm64':'arm64','aarch64':'arm64','x86_64':'x64','AMD64':'x64'}.get(platform.machine(),'unknown')
    return system+'-'+arch


def main():
    import argparse
    parser=argparse.ArgumentParser(add_help=False)
    parser.add_argument('--app-root',default=str(Path(__file__).parent))
    parser.add_argument('--data');args,remaining=parser.parse_known_args()
    root=Path(args.app_root).resolve();data=Path(args.data).resolve() if args.data else root/'data'
    try:apply_pending(root,data,platform_target())
    except Exception as error:
        print('更新未应用：'+str(error),flush=True)
        recover(root,data)
        # Invalid staged updates never prevent the already installed version from starting.
        (data/'software-updates/pending.json').unlink(missing_ok=True)
    sys.path.insert(0,str(root));sys.argv=[str(root/'server.py'),'--data',str(data)]+remaining
    try:runpy.run_path(str(root/'server.py'),run_name='__main__')
    except (Exception,SystemExit) as error:
        if isinstance(error,SystemExit) and error.code in (None,0):raise
        if (data/'software-updates/transaction.json').exists():
            recover(root,data)
            # Keep Windows' original Job-owned process alive; never restart on Ctrl+C.
            for name in FILES:
                if name.endswith('.py'):sys.modules.pop(name[:-3],None)
            runpy.run_path(str(root/'server.py'),run_name='__main__')
            return
        raise


if __name__=='__main__':main()
