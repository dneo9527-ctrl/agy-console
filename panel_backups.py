"""Local panel archives, bounded validation, and rollback-safe directory restore."""
from datetime import datetime, timezone
import hashlib
import json
import math
import os
from pathlib import Path, PurePosixPath
import re
import secrets
import shutil
import stat
import time
import zipfile
from collaboration_review import validate_review
from quota_failover import clean_order, clean_fallback
from software_updates import SoftwareUpdates
from multi_ai_collaboration import canonical as collaboration_policy, portable as portable_collaboration, validate as validate_collaboration, portable_roundtable, validate_roundtable, validate_roundtable_messages, validate_meetings

MAX_UPLOAD = 64 * 1024 * 1024
MAX_EXPANDED = 512 * 1024 * 1024
MAX_FILE = 64 * 1024 * 1024
HEX = re.compile(r'[a-f0-9]{32}')
UUID = re.compile(r'[a-f0-9]{8}(?:-[a-f0-9]{4}){3}-[a-f0-9]{12}')
BRANCH = re.compile(r'[a-z][a-z0-9_-]{0,39}')
RUN_FILE = re.compile(r'runs/([a-f0-9]{32})/(?:request\.txt|response\.md|result\.json|stdout\.ndjson|stderr\.log|attempt-1-(?:result\.json|stdout\.ndjson|stderr\.log|response\.md)|images/[a-f0-9]{64}\.(?:png|jpg|gif|webp))')


def configuration_document(state, updates, ui, window=None, app_version=''):
    """Portable preferences only: no identities, histories, paths, or credentials."""
    window_settings = None
    if window is not None:
        window_settings = {key: window[key] for key in
                           ('model_preferences', 'fallback_preferences', 'gpt_collaboration', 'auto_open_activity') if key in window}
        if window.get('codex_thread_id'):
            window_settings['multi_ai_collaboration'] = portable_collaboration(window)
            window_settings['roundtable'] = portable_roundtable(window)
            value = collaboration_policy(window)
            window_settings['gpt_collaboration'] = value['enabled'] and value['providers']['agy']['enabled']
    return validate_configuration({'format': 'agy-console-settings', 'schema': 1,
        'app_version': app_version, 'exported_at': datetime.now(timezone.utc).isoformat(),
        'settings': {'global': {'preferred_model': state.get('preferred_model', ''), 'model_order': state.get('model_order', [])},
                     'window': window_settings, 'updates': updates,
                     'ui': {key: ui.get(key, default) for key, default in (('font',14), ('idleMs',12000))}}})


def validate_configuration(value):
    def obj(item, allowed):
        if not isinstance(item, dict) or set(item) - set(allowed):
            raise ValueError('配置包含未知字段或格式错误；请选择本面板导出的配置 JSON。')
    obj(value, ('format','schema','app_version','exported_at','settings'))
    if value.get('format') != 'agy-console-settings' or type(value.get('schema')) is not int or value['schema'] != 1:
        raise ValueError('不支持此配置版本；请选择本面板导出的配置 JSON。')
    if (not isinstance(value.get('app_version'),str) or len(value['app_version'])>80
            or not isinstance(value.get('exported_at'),str) or len(value['exported_at'])>80):
        raise ValueError('配置版本或时间无效。')
    settings=value.get('settings');obj(settings, ('global','window','updates','ui'))
    if set(settings) != {'global','window','updates','ui'}:raise ValueError('配置缺少必要设置。')
    global_settings=settings['global'];obj(global_settings, ('preferred_model','model_order'))
    model=global_settings.get('preferred_model')
    if not isinstance(model,str) or model and not re.fullmatch(r'[A-Za-z0-9_.-]{1,100}',model):raise ValueError('配置中的模型无效。')
    global_settings={'preferred_model':model,'model_order':clean_order(global_settings.get('model_order'))}
    ui=settings['ui'];obj(ui, ('font','idleMs'))
    if type(ui.get('font')) is not int or ui['font'] not in (14,16,18) or type(ui.get('idleMs')) is not int or ui['idleMs'] not in (4000,12000):
        raise ValueError('配置中的字号或同步频率无效。')
    SoftwareUpdates.validate_preferences(settings['updates'])
    window=settings['window']
    if window is not None:
        obj(window, ('model_preferences','fallback_preferences','gpt_collaboration','auto_open_activity','multi_ai_collaboration','roundtable'))
        if 'multi_ai_collaboration' in window:
            validate_collaboration(window['multi_ai_collaboration'], portable=True)
        if 'roundtable' in window:
            validate_roundtable(window['roundtable'], portable=True)
        for key in ('model_preferences','fallback_preferences'):
            obj(window.get(key,{}), ('text','image'))
        for selected in window.get('model_preferences',{}).values():
            if not isinstance(selected,str) or selected and not re.fullmatch(r'[A-Za-z0-9_.-]{1,100}',selected):raise ValueError('窗口模型无效。')
        for candidate in window.get('fallback_preferences',{}).values():
            obj(candidate, ('enabled','models'));clean_fallback(candidate)
        for key in ('gpt_collaboration','auto_open_activity'):
            if key in window and type(window[key]) is not bool:raise ValueError('窗口开关必须为开或关。')
    clean={'format':value['format'],'schema':1,'app_version':value['app_version'],'exported_at':value['exported_at'],
           'settings':{'global':global_settings,'window':window,'updates':settings['updates'],'ui':ui}}
    raw=json.dumps(clean,ensure_ascii=False).encode('utf-8')
    if len(raw)>65536:raise ValueError('配置需小于64 KB。')
    return json.loads(raw)


def ui_preferences(value):
    value = value if isinstance(value, dict) else {}
    ui = value.get('settings') if isinstance(value.get('settings'), dict) else {}
    order = ui.get('modelOrder') if isinstance(ui.get('modelOrder'), list) else []
    pins = value.get('pins') if isinstance(value.get('pins'), list) else []
    return {'settings': {'font': ui.get('font') if ui.get('font') in (14, 16, 18) else 14,
                         'idleMs': ui.get('idleMs') if ui.get('idleMs') in (4000, 12000) else 12000,
                         'modelOrder': list(dict.fromkeys(x for x in order if isinstance(x, str) and re.fullmatch(r'[a-z0-9][a-z0-9_.-]{0,99}', x)))[:128]},
            'pins': [x for x in pins if isinstance(x, str) and re.fullmatch(r'(?:panel:[a-f0-9]{32}|external:[a-f0-9-]{36})', x)][:512]}


def validate_state(value):
    if not isinstance(value, dict) or not isinstance(value.get('conversations'), list) or len(value['conversations']) > 5000:
        raise ValueError('备份中的面板数据格式不正确。')
    clean_order(value.get('model_order', []))
    if type(value.get('model_order_version', 0)) is not int or value.get('model_order_version', 0) < 0:
        raise ValueError('备份中的模型排序版本无效。')
    ids, turn_ids, threads, official, images = set(), set(), set(), set(), 0
    for conv in value['conversations']:
        if (not isinstance(conv, dict) or not isinstance(conv.get('id'), str) or not HEX.fullmatch(conv['id'])
                or conv['id'] in ids or not isinstance(conv.get('title'), str) or len(conv['title']) > 200):
            raise ValueError('备份中的聊天标识或名称无效。')
        ids.add(conv['id'])
        preferences = conv.get('model_preferences', {})
        if (not isinstance(preferences, dict) or set(preferences) - {'text', 'image'}
                or any(not isinstance(v, str) or len(v) > 100 or v and not re.fullmatch(r'[A-Za-z0-9_.-]+', v) for v in preferences.values())):
            raise ValueError('备份中的窗口模型设置无效。')
        fallback = conv.get('fallback_preferences', {})
        if not isinstance(fallback, dict) or set(fallback) - {'text', 'image'}:
            raise ValueError('备份中的候补设置无效。')
        for settings in fallback.values():
            clean_fallback(settings)
        for key in ('created_at', 'updated_at', 'deleted_at', 'gpt_collaboration_updated_at', 'auto_open_activity_updated_at', 'multi_ai_collaboration_updated_at', 'roundtable_updated_at'):
            if conv.get(key) is not None and (not isinstance(conv[key], str) or len(conv[key]) > 100):
                raise ValueError('备份中的聊天日期无效。')
        if 'gpt_collaboration' in conv and type(conv['gpt_collaboration']) is not bool:
            raise ValueError('备份中的协作设置无效。')
        if 'auto_open_activity' in conv and type(conv['auto_open_activity']) is not bool:
            raise ValueError('备份中的自动打开设置无效。')
        if 'auto_start_console' in conv and type(conv['auto_start_console']) is not bool:
            raise ValueError('备份中的自动启动设置无效。')
        thread = conv.get('codex_thread_id')
        if thread is not None:
            if not isinstance(thread, str) or not UUID.fullmatch(thread) or thread in threads:
                raise ValueError('备份中的 Codex 绑定重复或无效。')
            threads.add(thread)
        if 'multi_ai_collaboration' in conv:
            if not thread:
                raise ValueError('多 AI 协作必须属于固定 Codex 聊天。')
            validate_collaboration(conv['multi_ai_collaboration'], thread)
        if 'roundtable' in conv:
            if not thread:
                raise ValueError('圆桌主持必须属于固定 Codex 聊天。')
            validate_roundtable(conv['roundtable'], thread)
        branches = conv.get('branches')
        if not isinstance(branches, dict) or len(branches) > 16:
            raise ValueError('备份中的工作会话无效。')
        if branches and conv.get('primary_branch') not in branches:
            raise ValueError('备份中的默认工作会话缺失。')
        for key, lane in branches.items():
            if (not isinstance(key, str) or not BRANCH.fullmatch(key) or not isinstance(lane, dict) or lane.get('id') != key
                    or not isinstance(lane.get('title'), str) or len(lane['title']) > 100):
                raise ValueError('备份中的工作会话引用无效。')
            sid = lane.get('agy_id')
            if sid is not None:
                if not isinstance(sid, str) or not UUID.fullmatch(sid) or sid in official:
                    raise ValueError('备份中的 Agy 会话重复或无效。')
                official.add(sid)
        if branches and conv.get('agy_id') != branches[conv['primary_branch']].get('agy_id'):
            raise ValueError('备份中的主会话绑定不一致。')
        if not branches and conv.get('agy_id') is not None:
            raise ValueError('备份中的主会话缺少工作会话。')
        if not isinstance(conv.get('turns'), list) or len(conv['turns']) > 20000:
            raise ValueError('备份中的聊天记录无效。')
        validate_roundtable_messages(conv)
        validate_meetings(conv)
        for turn in conv['turns']:
            if (not isinstance(turn, dict) or not isinstance(turn.get('id'), str) or not HEX.fullmatch(turn['id'])
                    or turn['id'] in turn_ids or turn.get('branch') not in branches
                    or turn.get('status') not in ('completed', 'failed', 'cancelled', 'interrupted')
                    or not isinstance(turn.get('prompt'), str) or not isinstance(turn.get('response'), str)):
                raise ValueError('备份中存在重复、未结束或无效的任务记录。')
            if 'roundtable_reset' in turn and (type(turn['roundtable_reset']) is not bool
                    or not thread or turn.get('source') != 'codex' or 'roundtable' in turn):
                raise ValueError('备份中的日常协助角色重置无效。')
            if 'roundtable' in turn:
                if not thread:
                    raise ValueError('圆桌任务必须属于固定 Codex 聊天。')
                validate_roundtable(turn['roundtable'], thread)
                expected_role = 'host' if turn['roundtable']['host'] == 'agy' else 'participant'
                if turn.get('roundtable_role') != expected_role:
                    raise ValueError('备份中的圆桌任务角色无效。')
            elif 'roundtable_role' in turn:
                raise ValueError('备份中的圆桌任务角色缺少设置快照。')
            turn_ids.add(turn['id'])
            if len(turn_ids) > 25000:
                raise ValueError('备份任务数超过限制。')
            if not isinstance(turn.get('model'), str) or len(turn['model']) > 100 or turn.get('kind', 'text') not in ('text', 'image'):
                raise ValueError('备份中的任务模型或类型无效。')
            for key in ('created_at', 'started_at', 'ended_at'):
                if turn.get(key) is not None and (not isinstance(turn[key], str) or len(turn[key]) > 100):
                    raise ValueError('备份中的任务日期无效。')
            if ('error' in turn and not isinstance(turn['error'], str)
                    or not isinstance(turn.get('usage', {}), dict)
                    or not isinstance(turn.get('images', []), list) or len(turn.get('images', [])) > 100
                    or type(turn.get('elapsed', 0)) not in (int, float) or not math.isfinite(turn.get('elapsed', 0))):
                raise ValueError('备份中的任务统计无效。')
            if not isinstance(turn.get('events', []), list) or any(not isinstance(x, str) for x in turn.get('events', [])):
                raise ValueError('备份中的任务进度无效。')
            progress = turn.get('progress', [])
            if (not isinstance(progress, list) or len(progress) > 32
                    or any(not isinstance(row, dict)
                           or any(not isinstance(row.get(key), str) or not 0 < len(row[key]) <= maximum
                                  for key, maximum in (('key', 180), ('label', 160), ('started_at', 100), ('updated_at', 100)))
                           or row.get('state') not in {'done', 'ended', 'stopped', 'failed', 'cancelled', 'interrupted', 'skipped'}
                           for row in progress)
                    or type(turn.get('progress_omitted', 0)) is not int or turn.get('progress_omitted', 0) < 0
                    or turn.get('progress_updated_at') is not None and (not isinstance(turn['progress_updated_at'], str) or len(turn['progress_updated_at']) > 100)):
                raise ValueError('备份中的步骤详情无效。')
            for key in ('review',):
                if turn.get(key) is not None:
                    validate_review(turn[key])
            for key in ('completion_evidence', 'compaction_evidence', 'account'):
                if turn.get(key) is not None and not isinstance(turn[key], dict):
                    raise ValueError('备份中的核验记录无效。')
            for image in turn.get('images', []):
                if (not isinstance(image, dict) or not re.fullmatch(r'[a-f0-9]{64}', str(image.get('id', '')))
                        or not re.fullmatch(r'[a-f0-9]{64}\.(?:png|jpg|gif|webp)', str(image.get('file', '')))
                        or image['file'].split('.')[0] != image['id']
                        or image.get('url') != '/api/turns/' + turn['id'] + '/images/' + image['id']
                        or image.get('download_url') != image.get('url') + '?download=1'
                        or image.get('mime') not in ('image/png', 'image/jpeg', 'image/gif', 'image/webp')
                        or not isinstance(image.get('name'), str) or len(image['name']) > 500
                        or type(image.get('bytes')) is not int or not 0 < image['bytes'] <= MAX_FILE
                        or any(image.get(key) is not None and (type(image[key]) is not int or not 0 < image[key] <= 100000) for key in ('width', 'height'))):
                    raise ValueError('备份中的图片记录无效。')
                images += 1
    if not isinstance(value.get('history_overrides', {}), dict) or not isinstance(value.get('preferred_model', ''), str):
        raise ValueError('备份中的设置无效。')
    for key, settings in value.get('history_overrides', {}).items():
        if (not isinstance(key, str) or not re.fullmatch(r'(?:panel:[a-f0-9]{32}|external:[a-f0-9-]{36})', key)
                or not isinstance(settings, dict) or 'title' in settings and not isinstance(settings['title'], str)
                or 'hidden' in settings and type(settings['hidden']) is not bool):
            raise ValueError('备份中的列表设置无效。')
    return {'conversations': len(ids), 'turns': len(turn_ids), 'images': images,
            'bindings': len(threads), 'sessions': len(official)}, turn_ids


def linked_inside(root, file):
    relative = file.relative_to(root)
    return root.is_symlink() or any((root.joinpath(*relative.parts[:i])).is_symlink() for i in range(1, len(relative.parts) + 1))


def verify_images(root, state):
    for conv in state['conversations']:
        for turn in conv['turns']:
            for image in turn.get('images', []):
                file = root / 'runs' / turn['id'] / 'images' / image['file']
                if linked_inside(root, file) or not file.is_file() or file.stat().st_size > MAX_FILE:
                    raise ValueError('缺少已收录图片，或图片文件超过限制。')
                digest = hashlib.sha256()
                with file.open('rb') as stream:
                    while True:
                        chunk = stream.read(128 * 1024)
                        if not chunk:
                            break
                        digest.update(chunk)
                if digest.hexdigest() != image['id']:
                    raise ValueError('图片校验失败。')


class PanelBackups:
    def __init__(self, root):
        self.root = Path(root)
        self.previews = {}

    @staticmethod
    def recover_interrupted(root):
        root = Path(root)
        journal = root.parent / ('.' + root.name + '-restore-journal.json')
        if not journal.exists():
            return
        if journal.is_symlink() or journal.stat().st_size > 4096:
            raise ValueError('恢复日志异常，未覆盖面板数据。')
        value = json.loads(journal.read_text())
        rollback = root.parent / str(value.get('rollback', ''))
        stage = root.parent / str(value.get('stage', ''))
        if (value.get('root') != root.name or not re.fullmatch(r'data-before-restore-[a-f0-9]{16}', rollback.name)
                or not re.fullmatch(r'\.agy-restore-stage-[a-f0-9]{32}', stage.name)
                or rollback.parent != root.parent or stage.parent != root.parent
                or root.is_symlink() or rollback.is_symlink() or stage.is_symlink()):
            raise ValueError('恢复日志路径无效，未覆盖面板数据。')
        if not root.exists():
            if not rollback.is_dir():
                raise ValueError('恢复尚未完成，未找到原数据；请保留现有文件检查。')
            os.replace(rollback, root)
        # If the new root exists, it contains either the original data or the
        # fully staged replacement, never a partially extracted archive.
        journal.unlink()

    def create(self, state, preferences=None):
        counts, turns = validate_state(state)
        verify_images(self.root, state)
        folder = self.root / 'backups'; folder.mkdir(exist_ok=True)
        ident = secrets.token_hex(16); path = folder / (ident + '.zip'); partial = folder / (ident + '.partial')
        manifest = {'format': 'agy-panel-backup', 'version': 1,
                    'created_at': datetime.now(timezone.utc).isoformat(), 'counts': counts,
                    'scope': '面板记录、已收录图片与绑定；不含官方登录、CLI内部上下文和外部项目。',
                    'ui': ui_preferences(preferences), 'files': {}}
        total = 0
        try:
            with zipfile.ZipFile(partial, 'w', compression=zipfile.ZIP_DEFLATED, compresslevel=3) as archive:
                raw = json.dumps(state, ensure_ascii=False).encode('utf-8')
                if len(raw) > MAX_FILE:
                    raise ValueError('聊天数据过大，暂不能生成此大小的备份。')
                archive.writestr('state.json', raw)
                manifest['files']['state.json'] = hashlib.sha256(raw).hexdigest(); total += len(raw)
                for tid in sorted(turns):
                    directory = self.root / 'runs' / tid
                    if not directory.is_dir() or directory.is_symlink():
                        continue
                    candidates = [directory / name for name in ('request.txt', 'response.md', 'result.json', 'stdout.ndjson', 'stderr.log',
                                                                   'attempt-1-result.json', 'attempt-1-stdout.ndjson', 'attempt-1-stderr.log', 'attempt-1-response.md')]
                    image_dir = directory / 'images'
                    if image_dir.is_dir() and not image_dir.is_symlink():
                        candidates.extend(image_dir.iterdir())
                    for file in candidates:
                        name = file.relative_to(self.root).as_posix()
                        if not file.is_file() or not RUN_FILE.fullmatch(name):
                            continue
                        if len(manifest['files']) >= 24998:
                            raise ValueError('备份文件数超过限制。')
                        if linked_inside(self.root, file):
                            raise ValueError('运行记录含符号链接，未生成备份。')
                        descriptor = os.open(file, os.O_RDONLY | getattr(os, 'O_NOFOLLOW', 0))
                        with os.fdopen(descriptor, 'rb') as source:
                            info = os.fstat(source.fileno())
                            if not stat.S_ISREG(info.st_mode) or info.st_size > MAX_FILE or total + info.st_size > MAX_EXPANDED:
                                raise ValueError('备份内容超过512 MB或单文件超过64 MB。')
                            digest = hashlib.sha256()
                            with archive.open(name, 'w') as target:
                                copied = 0
                                while True:
                                    chunk = source.read(128 * 1024)
                                    if not chunk:
                                        break
                                    copied += len(chunk)
                                    if copied > MAX_FILE or total + copied > MAX_EXPANDED:
                                        raise ValueError('备份文件在读取时超出限制。')
                                    target.write(chunk); digest.update(chunk)
                            total += copied; manifest['files'][name] = digest.hexdigest()
                archive.writestr('manifest.json', json.dumps(manifest, ensure_ascii=False))
            if partial.stat().st_size > MAX_UPLOAD:
                raise ValueError('压缩包超过64 MB，暂不支持此大小的备份。')
            partial.replace(path)
        finally:
            partial.unlink(missing_ok=True)
        return {'id': ident, 'url': '/api/backups/' + ident + '/download', 'bytes': path.stat().st_size,
                'counts': counts, 'created_at': manifest['created_at']}

    def preview(self, incoming):
        self.expire()
        if len(self.previews) >= 2:
            oldest = min(self.previews, key=lambda key: self.previews[key][2])
            shutil.rmtree(self.previews.pop(oldest)[0], ignore_errors=True)
        if Path(incoming).stat().st_size > MAX_UPLOAD:
            raise ValueError('备份压缩包最多64 MB。')
        ident = secrets.token_hex(16)
        stage = self.root.parent / ('.agy-restore-stage-' + ident)
        stage.mkdir(mode=0o700)
        try:
            with zipfile.ZipFile(incoming) as archive:
                entries = archive.infolist()
                names = [x.filename for x in entries]
                if len(entries) > 25000 or len(set(names)) != len(names) or sum(x.file_size for x in entries) > MAX_EXPANDED:
                    raise ValueError('备份包含重复文件或内容过大。')
                for item in entries:
                    name = item.filename; parts = PurePosixPath(name).parts
                    if (name not in ('state.json', 'manifest.json') and not RUN_FILE.fullmatch(name)
                            or '..' in parts or '\\' in name or name.startswith('/') or item.flag_bits & 1
                            or stat.S_ISLNK(item.external_attr >> 16) or item.file_size > MAX_FILE):
                        raise ValueError('备份包含不允许的路径或文件。')
                if 'manifest.json' not in names or 'state.json' not in names:
                    raise ValueError('这不是完整的面板备份。')
                if archive.getinfo('manifest.json').file_size > 4 * 1024 * 1024:
                    raise ValueError('备份清单过大。')
                manifest = json.loads(archive.read('manifest.json'))
                if manifest.get('format') != 'agy-panel-backup' or manifest.get('version') != 1 or not isinstance(manifest.get('files'), dict):
                    raise ValueError('备份版本或格式不支持。')
                if set(manifest['files']) != set(names) - {'manifest.json'}:
                    raise ValueError('备份清单与文件不一致。')
                for item in entries:
                    if item.filename == 'manifest.json':
                        continue
                    target = stage / item.filename; target.parent.mkdir(parents=True, exist_ok=True)
                    digest = hashlib.sha256()
                    with archive.open(item) as source, target.open('xb') as output:
                        copied = 0
                        while True:
                            chunk = source.read(128 * 1024)
                            if not chunk:
                                break
                            copied += len(chunk)
                            if copied > MAX_FILE:
                                raise ValueError('解压后文件超过限制。')
                            output.write(chunk); digest.update(chunk)
                    if digest.hexdigest() != manifest['files'][item.filename]:
                        raise ValueError('备份校验未通过，文件可能损坏。')
                state = json.loads((stage / 'state.json').read_text(encoding='utf-8'), parse_constant=lambda _x: (_ for _ in ()).throw(ValueError('数字无效。')))
                counts, turns = validate_state(state)
                if any(RUN_FILE.fullmatch(name) and RUN_FILE.fullmatch(name)[1] not in turns for name in names):
                    raise ValueError('备份包含不属于聊天记录的运行文件。')
                verify_images(stage, state)
            data = {'id': ident, 'counts': counts, 'created_at': manifest.get('created_at'),
                    'scope': '面板记录、已收录图片、固定绑定及设置；不含官方登录和CLI内部上下文。',
                    'ui': ui_preferences(manifest.get('ui'))}
            self.previews[ident] = (stage, data, time.monotonic())
            return data
        except (OSError, ValueError, KeyError, TypeError, AttributeError, RecursionError, zipfile.BadZipFile, RuntimeError) as error:
            shutil.rmtree(stage, ignore_errors=True)
            raise ValueError('未导入：' + str(error)[:200]) from None

    def restore(self, ident, current_state, current_ui, prepare=None):
        self.expire()
        if not isinstance(ident, str) or not HEX.fullmatch(ident):
            raise ValueError('预览标识无效。')
        entry = self.previews.get(ident)
        if entry is None:
            raise ValueError('预览已失效，请重新选择备份。')
        stage, data, _ = entry
        # A downloadable rollback copy accompanies the directory kept below.
        previous = self.create(current_state, current_ui)
        state = json.loads((stage / 'state.json').read_text(encoding='utf-8'))
        validate_state(state)
        if prepare is not None:
            prepare(state)
        state['panel_restored_at'] = datetime.now(timezone.utc).isoformat()
        validate_state(state)
        with (stage / 'state.json').open('w', encoding='utf-8') as stream:
            json.dump(state, stream, ensure_ascii=False)
            stream.flush(); os.fsync(stream.fileno())
        # Finish every fallible preparation before exchanging directories.
        target = stage / 'backups'; target.mkdir(exist_ok=True)
        shutil.copy2(self.root / 'backups' / (previous['id'] + '.zip'), target / (previous['id'] + '.zip'))
        rollback = self.root.parent / ('data-before-restore-' + secrets.token_hex(8))
        journal = self.root.parent / ('.' + self.root.name + '-restore-journal.json')
        partial = journal.with_name(journal.name + '.' + secrets.token_hex(6) + '.tmp')
        with partial.open('x') as stream:
            json.dump({'root': self.root.name, 'stage': stage.name, 'rollback': rollback.name}, stream)
            stream.flush(); os.fsync(stream.fileno())
        partial.replace(journal)
        try:
            os.replace(self.root, rollback)
            os.replace(stage, self.root)
        except BaseException:
            if not self.root.exists() and rollback.is_dir():
                os.replace(rollback, self.root)
            journal.unlink(missing_ok=True)
            raise
        try:
            journal.unlink(missing_ok=True)
        except OSError:
            pass  # Startup recognizes the already committed new directory.
        self.previews.pop(ident, None)
        return state, {'restored': True, 'counts': data['counts'], 'ui': data['ui'],
                       'rollback': previous, 'rollback_directory': str(rollback)}

    def expire(self):
        for ident, (stage, _data, created) in list(self.previews.items()):
            if time.monotonic() - created > 1800:
                shutil.rmtree(stage, ignore_errors=True); self.previews.pop(ident, None)
