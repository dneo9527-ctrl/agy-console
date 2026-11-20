#!/usr/bin/env python3
"""Loopback-only Antigravity web console. Python standard library, official CLI."""
import argparse
import copy
from contextlib import contextmanager
from datetime import datetime, timezone
import hmac
import hashlib
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
from pathlib import Path
import re
import secrets
import selectors
import signal
import socketserver
import stat
import subprocess
import sys
import termios
import threading
import tempfile
import time
from urllib.parse import parse_qs, quote, urlsplit
import uuid
import unicodedata
from image_files import collect_images, image_snapshot, manifest_file
from account_status import probe_account
from activity_monitor import ActivityMonitor
from resources import ResourceMonitor
from settings_insights import context_insights
from panel_actions import search_history, recovery_prompt
from panel_backups import PanelBackups, MAX_UPLOAD, configuration_document, validate_configuration
from proxy_env import configure_proxy
from headless_compat import TurnEvidence
from model_policy import default_model, candidate_models, model_family, quota_groups, quota_empty, effective_order, select_turn_model, window_model_resolution, model_selection_settings, clean_global_model_policy
from quota_failover import clean_order, clean_fallback, quota_rejection, AttemptGuard
from task_queue import TaskQueue
from task_progress import record as record_progress, step_progress, finish_progress
from collaboration_review import validate_review
from software_updates import SoftwareUpdates
from update_bootstrap import confirm as confirm_software_update
from foreground_reload import PROXY_KEYS, reload_environment, validate_sources
from conversation_sessions import normalize_sessions, branch_for, official_ids, session_owner, assign_session, session_summaries
from transcript_export import export_markdown, attachment as transcript_attachment
from multi_ai_collaboration import (Coordination, default_policy_path, launcher_path,
                                    canonical as collaboration_policy, migrate as migrate_collaboration,
                                    allowed as collaboration_allowed, patch as patch_collaboration,
                                    validate as validate_collaboration, roundtable as roundtable_policy,
                                    validate_roundtable, patch_roundtable, roundtable_plan, roundtable_prompt,
                                    roundtable_message_request, validate_roundtable_messages,
                                    new_meeting, validate_meetings, record_meeting_event,
                                    link_meeting_turn, meeting_view, meeting_summary, meeting_context,
                                    roundtable_workspace_preferences, patch_roundtable_workspace_preferences,
                                    roundtable_presets, meeting_workspace)

ROOT = Path(__file__).resolve().parent
APP_VERSION = (ROOT / 'VERSION').read_text(encoding='utf-8').strip()
BUSY = {'queued', 'running', 'cancelling'}
DEFAULT_MODEL = 'gemini-4-argon-high'
BARK_SCRIPT = Path.home() / 'Library/Application Support/CodexBark/bark.py'


def bark_notification_payload(status, conversation_id, turn_id, chat_title):
    """Only final outcome and short window identity may leave the console."""
    if status not in ('completed', 'failed'):
        return None
    if any(not isinstance(value, str) or not re.fullmatch(r'[a-f0-9]{32}', value)
           for value in (conversation_id, turn_id)):
        return None
    title = chat_title if isinstance(chat_title, str) else ''
    title = ''.join(' ' if unicodedata.category(char).startswith('C') else char for char in title[:256])
    title = ' '.join(title.split())[:80] or 'Agy'
    return {'status': status, 'conversation_id': conversation_id, 'turn_id': turn_id,
            'chat_title': title}


def notify_bark(payload):
    """Best effort local event handoff; the receiver owns network and policies."""
    try:
        if sys.platform != 'darwin' or not isinstance(payload, dict) or set(payload) != {
                'status', 'conversation_id', 'turn_id', 'chat_title'}:
            return
        clean = bark_notification_payload(**payload)
        if clean is None:
            return
        info = BARK_SCRIPT.lstat()
        if (not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid()
                or stat.S_IMODE(info.st_mode) & 0o022):
            return
        subprocess.run(['/usr/bin/python3', '-I', '-B', str(BARK_SCRIPT), 'agy-event'],
                       input=json.dumps(clean, ensure_ascii=False).encode('utf-8'),
                       stdin=None, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                       timeout=1, check=False)
    except Exception:
        # Notification availability must never change an Agy task's result.
        return


class ConflictError(ValueError):
    pass


class ForegroundHTTPServer(ThreadingHTTPServer):
    def server_bind(self):
        # A numeric loopback listener needs no reverse DNS. HTTPServer's default
        # getfqdn() can block startup on machines without a responsive resolver.
        socketserver.TCPServer.server_bind(self)
        self.server_name = 'localhost'
        self.server_port = self.server_address[1]

    def __init__(self, *args, **kwargs):
        self.requests_changed = threading.Condition()
        self.active_requests = 0
        super().__init__(*args, **kwargs)

    def process_request(self, request, address):
        request.settimeout(5)
        with self.requests_changed:
            self.active_requests += 1
        try:
            super().process_request(request, address)
        except BaseException:
            with self.requests_changed:
                self.active_requests -= 1
                self.requests_changed.notify_all()
            raise

    def process_request_thread(self, request, address):
        try:
            super().process_request_thread(request, address)
        finally:
            with self.requests_changed:
                self.active_requests -= 1
                self.requests_changed.notify_all()

    def drain_requests(self, timeout=6):
        deadline = time.monotonic() + timeout
        with self.requests_changed:
            while self.active_requests:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    return False
                self.requests_changed.wait(remaining)
        return True


def now():
    return datetime.now(timezone.utc).isoformat()


def announce(message):
    try:
        print(message, flush=True)
    except OSError:
        # Terminal may already be closed when its SIGHUP arrives.
        pass


def terminal_is_foreground():
    """Require a live controlling terminal, with this process in its foreground."""
    try:
        input_fd, output_fd = sys.stdin.fileno(), sys.stdout.fileno()
        if not os.isatty(input_fd) or not os.isatty(output_fd):
            return False
        termios.tcgetattr(input_fd)
        return os.tcgetpgrp(input_fd) == os.getpgrp()
    except (OSError, termios.error, ValueError, AttributeError):
        return False


def write_json(path, value):
    temp = path.with_suffix('.tmp')
    temp.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding='utf-8')
    temp.replace(path)


class Console:
    def __init__(self, data, binary, timeout=600, import_history=False, account_reader=None, coordination_path=None,
                 notification_sender=None):
        self.data, self.binary, self.timeout = Path(data), str(binary), timeout
        PanelBackups.recover_interrupted(self.data)
        self.data.mkdir(parents=True, exist_ok=True)
        self.coordination = Coordination(coordination_path or self.data / 'coordination.json', ROOT)
        self.coordination_error = ''
        self.lock = threading.RLock()
        self.close_lock = threading.Lock()
        self.closed = False
        self.jobs = TaskQueue(self.lock)
        self.reloading = False
        self.maintenance = False
        self.backups = PanelBackups(self.data)
        self.reload_callback = None
        self.processes = {}
        self.health_processes = {}
        self.health_thread = None
        self.account_thread = None
        self.account_switch_origin = None
        self.account_lock = threading.Lock()
        self.account_reader = account_reader or probe_account
        self.notification_sender = notification_sender
        self.activity = ActivityMonitor(Path.home() / '.gemini/antigravity-cli', self.binary)
        self.resources = ResourceMonitor()
        self.started = time.monotonic()
        self.revision = 0
        self.conversation_revisions = {}
        self.instance_id = secrets.token_hex(8)
        self.stopping = False
        self.last_stream_save = 0
        self.fallback_blocks = {}
        self.token = secrets.token_urlsafe(32)
        self.health = {'connected': False, 'checking': True, 'account_syncing': False, 'version': '', 'error': '',
                       'models': [{'id': DEFAULT_MODEL, 'label': 'Gemini 4 Argon (High)'}],
                       'account': {'state': 'unknown', 'email': None, 'name': None,
                                   'source': '官方 CLI statusLine', 'checked_at': None,
                                   'detail': '等待检查 CLI 当前登录账号。'}}
        self.file = self.data / 'state.json'
        self.state = json.loads(self.file.read_text()) if self.file.exists() else {'conversations': []}
        if self.file.exists() and any('branches' not in conv for conv in self.state['conversations']):
            backups = self.data / 'backups'
            backups.mkdir(exist_ok=True)
            backup = backups / ('state-before-sessions-' + secrets.token_hex(6) + '.json')
            fd = os.open(str(backup), os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            with os.fdopen(fd, 'wb') as stream:
                stream.write(self.file.read_bytes())
        self.state.setdefault('history_overrides', {})
        self.state.setdefault('preferred_model', '')
        self.state.setdefault('preferred_model_updated_at', None)
        self.state.setdefault('model_order', [])
        self.state.setdefault('model_order_version', 0)
        self.state['global_model_policy'] = clean_global_model_policy(self.state.get('global_model_policy', {}))
        self.state.setdefault('global_model_policy_version', 0)
        for conv in self.state['conversations']:
            normalize_sessions(conv)
            migrate_collaboration(conv)
            for turn in conv['turns']:
                if turn['status'] in BUSY:
                    turn.update(status='interrupted', error='服务曾重启，本次任务未确认完成。', ended_at=now())
                    finish_progress(turn)
        if import_history and not self.state.get('panel_restored_at'):
            self.import_history()
        self.reconcile_recent_results()
        self.save()
        self.import_codex_bindings()
        self.software_updates = SoftwareUpdates(ROOT,self.data,self.update_idle,self.update_changed,start=False)
        self.worker_thread = threading.Thread(target=self.worker, daemon=True)
        self.worker_thread.start()
        self.software_updates.start()

    def update_idle(self):
        with self.lock:
            try:
                self.require_idle()
                return not self.maintenance
            except ValueError:
                return False

    def update_changed(self):
        with self.lock:self._touch()

    def save(self, conv=None, require_coordination=False):
        with self.lock:
            # Offline consumers must not observe old permissions after a failed
            # write. Publish only after the canonical state is durably replaced.
            self.coordination.invalidate()
            self.coordination_error = '离线协作策略尚未同步。'
            write_json(self.file, self.state)
            self._touch(conv)
            try:
                self.coordination.publish(self.state['conversations'])
            except ValueError as error:
                # A corrupt chat must block all offline auto-starts without
                # preventing the foreground panel and manual work from opening.
                # Preference writes require a complete synchronized snapshot.
                self.coordination_error = str(error)
                if require_coordination:
                    raise
            else:
                self.coordination_error = ''

    def reconcile_recent_results(self):
        """Repair only proven API recovery; retain raw files and a state backup."""
        candidates=[(c,t) for c in self.state['conversations'] for t in c['turns']
                    if t.get('status')=='failed' and t.get('exit_code') in (0,1,3)
                    and t.get('kind','text')=='text'
                    and (t.get('response','').strip() or quota_rejection(t.get('error')))
                    and (str(t.get('error','')).startswith('API error') or quota_rejection(t.get('error')))
                    and not t.get('session_mismatch')]
        candidates.sort(key=lambda pair:pair[1].get('created_at',''),reverse=True)
        repaired=[]
        for conv,turn in candidates[:12]:
            if not re.fullmatch(r'[a-f0-9]{32}',str(turn.get('id',''))):
                continue
            folder=self.data/'runs'/turn['id'];raw=folder/'stdout.ndjson';err=folder/'stderr.log'
            try:
                if not raw.is_file() or raw.is_symlink() or raw.stat().st_size>2*1024*1024:
                    continue
                if not err.is_file() or err.is_symlink() or err.stat().st_size>1024*1024:
                    continue
                diagnostic=err.read_text(encoding='utf-8',errors='replace')
                denial = bool(re.search(r'soft.denied|auto.denied|permission denied|tool.{0,80}\bdenied\b',diagnostic,re.I))
                evidence=TurnEvidence();result={};valid=True
                with raw.open(encoding='utf-8') as stream:
                    for index,line in enumerate(stream):
                        if index>=20000:
                            valid=False;break
                        if not line.strip():continue
                        event=json.loads(line);evidence.observe(event)
                        if event.get('event')=='result':result=event.get('result') or {}
                lane=branch_for(conv,turn.get('branch'));expected=turn.get('agy_id') or (lane or {}).get('agy_id')
                if not valid or not expected or result.get('conversation_id')!=expected or result.get('response')!=turn['response']:
                    continue
                window = None
                if turn.get('started_at') and turn.get('ended_at'):
                    window = tuple(datetime.fromisoformat(turn[key].replace('Z', '+00:00')).timestamp()
                                   for key in ('started_at', 'ended_at'))
                proof=(evidence.prove_historical_error(result,root=self.activity.root,time_window=window)
                       if turn.get('exit_code')==0 and not denial else None)
                if proof is None and quota_rejection(result.get('error')):
                    proof=evidence.prove_denied_historical_error(result,root=self.activity.root)
                if proof:repaired.append((turn,proof,evidence.current_usage()))
            except (OSError,ValueError,TypeError,AttributeError):
                continue
        if repaired:
            backups=self.data/'backups';backups.mkdir(exist_ok=True)
            if self.file.exists():
                target=backups/('state-before-result-recheck-'+secrets.token_hex(6)+'.json')
                fd=os.open(str(target),os.O_WRONLY|os.O_CREAT|os.O_EXCL,0o600)
                with os.fdopen(fd,'wb') as stream:stream.write(self.file.read_bytes())
            for turn,proof,usage in repaired:
                if proof['source']=='official_tool_denial':
                    message='工具权限被拒绝：'+', '.join(proof['denied_tools'])+'。本轮未完成；CLI 附带的旧额度错误已隔离。'
                    turn.update(status_before_recheck=turn['status'],error_before_recheck=turn['error'],
                                error=message,failure_evidence=proof,rechecked_at=now(),
                                fallback_notice='本轮工具被拒绝，未自动切换或重跑；请先检查所需权限。')
                    if turn.get('attempts'):
                        attempt=turn['attempts'][-1]
                        attempt.update(error_before_recheck=attempt.get('error'),error=message)
                    continue
                turn.update(status_before_recheck=turn['status'],error_before_recheck=turn['error'],
                            status='completed',error='',completion_evidence=proof,rechecked_at=now())
                turn.pop('fallback_notice', None)
                if turn.get('attempts'):
                    attempt = turn['attempts'][-1]
                    attempt.update(status_before_recheck=attempt.get('status'), error_before_recheck=attempt.get('error'),
                                   status='completed', error='')
                if turn.get('progress'):
                    finish_progress(turn)
                if usage is not None:
                    turn.setdefault('cli_usage',copy.deepcopy(turn.get('usage') or {}));turn.update(usage=usage,usage_scope='turn')
        return len(repaired)

    def _touch(self, conv=None):
        """Invalidate visible data while the caller holds self.lock; no disk work."""
        self.revision += 1
        for item in ([conv] if conv is not None else self.state['conversations']):
            self.conversation_revisions[item['id']] = self.conversation_revisions.get(item['id'], 0) + 1

    def state_etag(self):
        with self.lock:
            stamp = [os.getpid(), self.started, self.revision, self.health, len(self.processes),
                     len(self.health_processes), self.stopping, self.reloading,
                     [bool(t and t.is_alive()) for t in (self.health_thread, self.account_thread)]]
            raw = json.dumps(stamp, sort_keys=True, ensure_ascii=True).encode()
            return '"' + hashlib.blake2s(raw, digest_size=12).hexdigest() + '"'

    def import_history(self):
        base = ROOT.parent / 'ai-assistants' / 'runs'
        seen = {t.get('source') for c in self.state['conversations'] for t in c['turns']}
        names = ['antigravity-smoke', 'antigravity-recall', 'antigravity-benchmark',
                 'antigravity-cross-review', 'antigravity-revision', 'antigravity-image']
        titles = {'antigravity-smoke': '连接与续聊测试', 'antigravity-benchmark': '论文方法与统计 · 能力测试',
                  'antigravity-cross-review': 'Muse 回答 · 交叉审稿', 'antigravity-image': '图片生成 · 真实测试'}
        for name in names:
            folder = base / name
            if name in seen or not (folder / 'result.json').exists():
                continue
            result = json.loads((folder / 'result.json').read_text())
            if not result.get('completed'):
                continue
            session = result.get('conversation_id')
            conv = next((c for c in self.state['conversations'] if c.get('agy_id') == session), None)
            if conv is None:
                conv = self.create(titles.get(name, '历史任务'), save=False)
                conv['agy_id'] = session
            stamp = datetime.fromtimestamp((folder / 'result.json').stat().st_mtime, timezone.utc).isoformat()
            conv['turns'].append({'id': uuid.uuid4().hex, 'prompt': (folder / 'request.txt').read_text(),
                                  'response': result['response'], 'status': 'completed', 'events': [],
                                  'model': result.get('requested_model') or DEFAULT_MODEL, 'created_at': stamp,
                                  'started_at': stamp, 'ended_at': stamp, 'elapsed': result.get('elapsed_seconds', 0),
                                  'usage': result.get('usage', {}), 'source': name, 'error': ''})
            conv['updated_at'] = stamp
            if result.get('kind') == 'image':
                turn = conv['turns'][-1]
                turn.update(kind='image', images=[])
                folder = self.data / 'runs' / turn['id']
                self.gather_images(conv, turn, Path(result.get('artifact_workspace') or result['workspace']), folder, 0, None)
                if not turn['images']:
                    turn.update(status='failed', error='历史图片文件不可用。')

    def create(self, title='新任务', save=True):
        with self.lock:
            if self.stopping:
                raise ValueError('服务正在关闭，请重新启动后再操作。')
            conv = {'id': uuid.uuid4().hex, 'title': str(title)[:60] or '新任务', 'agy_id': None,
                    'created_at': now(), 'updated_at': now(), 'turns': [], 'branches': {}, 'primary_branch': None}
            self.state['conversations'].append(conv)
            if save:
                self.save(conv)
            return conv

    def conversation(self, cid, include_deleted=False):
        return next((c for c in self.state['conversations'] if c['id'] == cid
                     and (include_deleted or not c.get('deleted_at'))), None)

    def bind_codex(self, thread_id, title=None, agy_id=None, initial_turn=None, initial_turns=None):
        """Persist one Codex thread -> one console chat -> one official CLI session."""
        uuid_pattern = r'[a-f0-9]{8}(?:-[a-f0-9]{4}){3}-[a-f0-9]{12}'
        if not isinstance(thread_id, str) or not re.fullmatch(uuid_pattern, thread_id):
            raise ValueError('缺少有效的 Codex 聊天 ID。')
        if agy_id is not None and (not isinstance(agy_id, str) or not re.fullmatch(uuid_pattern, agy_id)):
            raise ValueError('Agy 会话 ID 无效。')
        if initial_turn is not None and initial_turns is not None:
            raise ValueError('导入内容重复。')
        seeds = [initial_turn] if initial_turn is not None else initial_turns or []
        if not isinstance(seeds, list) or len(seeds) > 32:
            raise ValueError('导入记录格式不正确。')
        for seed in seeds:
            if not isinstance(seed, dict) or not agy_id:
                raise ValueError('导入审查必须提供原 Agy 会话。')
            if any(not isinstance(seed.get(k), str) or not seed[k].strip()
                   for k in ('prompt', 'response')):
                raise ValueError('导入内容必须包含原始任务和真实回复。')
            if len(seed['prompt'].encode()) > 100000 or len(seed['response'].encode()) > 1000000:
                raise ValueError('导入内容过长。')
        with self.lock:
            if self.stopping:
                raise ValueError('控制台正在关闭。')
            conv = next((c for c in self.state['conversations'] if c.get('codex_thread_id') == thread_id), None)
            owner, owner_branch = session_owner(self.state['conversations'], agy_id) if agy_id else (None, None)
            if owner and owner.get('codex_thread_id') not in (None, thread_id):
                raise ValueError('该 Agy 会话已经绑定另一个 Codex 聊天。')
            if owner and owner_branch != owner.get('primary_branch'):
                raise ValueError('该会话属于已有入口的支线，不能替换默认绑定。')
            if conv and agy_id and conv.get('agy_id') not in (None, agy_id):
                raise ValueError('这条 Codex 聊天已有固定 Agy 会话，不能替换。')
            if conv is not None and owner is not None and owner is not conv:
                raise ValueError('已存在另一条面板记录，不能合并覆盖。')
            conv = conv or owner
            if conv is None:
                conv = self.create(title or 'Codex · ' + thread_id[:8], save=False)
            conv['codex_thread_id'] = thread_id
            migrate_collaboration(conv)
            conv.setdefault('auto_open_activity', True)
            conv['custom_title'] = True
            if agy_id:
                conv['agy_id'] = agy_id
                branch_for(conv, create=True)['agy_id'] = agy_id
            conv.pop('deleted_at', None)
            self.state['history_overrides'].get('panel:' + conv['id'], {}).pop('hidden', None)
            if seeds and not conv['turns']:
                for seed in seeds:
                    conv['turns'].append({'id': uuid.uuid4().hex, 'prompt': seed['prompt'],
                                     'response': seed['response'], 'status': 'completed',
                                     'model': str(seed.get('model') or DEFAULT_MODEL)[:100],
                                     'kind': 'text', 'images': [], 'events': [], 'error': '',
                                     'usage': {}, 'elapsed': seed.get('elapsed', 0),
                                     'created_at': now(), 'ended_at': now(), 'source': 'codex-import'})
            conv['updated_at'] = now()
            normalize_sessions(conv)
            self.save(conv)
            return self.conversation_metadata(conv)

    def set_collaboration(self, cid, enabled):
        if type(enabled) is not bool:
            raise ValueError('协作开关必须为开启或关闭。')
        with self.lock:
            if self.stopping:
                raise ValueError('服务正在关闭。')
            conv = self.conversation(cid)
            if conv is None or not conv.get('codex_thread_id'):
                raise ValueError('请先进入这条 Codex 聊天的固定入口。')
            current = collaboration_policy(conv)
            current['enabled'] = enabled
            if enabled:
                current['providers']['agy']['enabled'] = True
            with self.collaboration_transaction(conv):
                conv['multi_ai_collaboration'] = current
                conv['gpt_collaboration'] = collaboration_allowed(conv)
                conv['gpt_collaboration_updated_at'] = now()
            return {'id': cid, 'codex_thread_id': conv['codex_thread_id'], 'enabled': enabled,
                    'updated_at': conv['gpt_collaboration_updated_at']}

    @contextmanager
    def collaboration_transaction(self, conv=None):
        """Do not expose a failed preference write as a live permission change."""
        fields = ('multi_ai_collaboration', 'gpt_collaboration',
                  'gpt_collaboration_updated_at', 'multi_ai_collaboration_updated_at',
                  'auto_start_console', 'roundtable', 'roundtable_updated_at')
        previous = ({key: copy.deepcopy(conv[key]) for key in fields if key in conv}
                    if conv is not None else None)
        previous_launcher = self.coordination.launcher
        self.coordination.invalidate()
        self.coordination_error = '离线协作策略尚未同步。'
        try:
            yield
            self.save(conv, require_coordination=True)
        except Exception:
            if conv is not None:
                # Workers retain the original turn objects; restoring settings
                # must not replace history, session objects, or active work.
                for key in fields:
                    if key in previous:
                        conv[key] = previous[key]
                    else:
                        conv.pop(key, None)
            self.coordination.launcher = previous_launcher
            # A failed publish may follow a successful state replace. Restore
            # that file too, but keep the offline snapshot invalid until a
            # subsequent successful save; never report a partial rollback as
            # synchronized. A persistent disk error still propagates.
            try:
                self.coordination.invalidate()
                write_json(self.file, self.state)
            except Exception:
                pass
            self._touch(conv)
            raise

    def conversation_metadata(self, conv, include_meetings=True, compact=False):
        omitted = {'roundtable_meetings'} | ({'turns', 'roundtable_messages'} if compact else set())
        value = copy.deepcopy({k: v for k, v in conv.items() if k not in omitted})
        if include_meetings:
            try:
                records = validate_meetings(conv)
                if compact:
                    value['meeting_summaries'] = [meeting_summary(meeting_view(conv, item)) for item in records]
                else:
                    views = [meeting_view(conv, item) for item in records]
                    value['roundtable_meetings'] = views
                    value['meeting_summaries'] = [meeting_summary(item) for item in views]
            except ValueError:
                value.update(roundtable_meetings=[], meeting_summaries=[], meeting_error='圆桌记录待核对；原数据已保留。')
        if value.get('codex_thread_id'):
            migrate_collaboration(value)
            value.setdefault('auto_start_console', False)
            value['console_startup'] = self.console_startup_info()
            try:
                value['roundtable'] = roundtable_policy(value)
            except ValueError:
                pass  # Preserve malformed state for diagnostics; its plan fails closed.
            value['roundtable_plan'] = roundtable_plan(value)
            value['roundtable_presets'] = roundtable_presets()
            try:
                value['roundtable_workspace_preferences'] = roundtable_workspace_preferences(conv)
            except ValueError:
                value['roundtable_workspace_error'] = '圆桌预设待核对；原设置已保留。'
        return value

    def multi_ai_policy(self, thread_id):
        with self.lock:
            conv = next((c for c in self.state['conversations']
                         if c.get('codex_thread_id') == thread_id and not c.get('deleted_at')), None)
            if conv is None:
                raise KeyError('本聊天尚未绑定。')
            return collaboration_policy(conv)

    def set_multi_ai_collaboration(self, cid, body):
        with self.lock:
            if self.stopping or self.maintenance:
                raise ConflictError('服务暂时不能保存设置。')
            conv = self.conversation(cid)
            if conv is None or not conv.get('codex_thread_id'):
                raise ValueError('请先进入这条 Codex 聊天的固定入口。')
            updated = patch_collaboration(collaboration_policy(conv), body, conv['codex_thread_id'])
            # Fail before changing permissions if the offline mirror cannot be invalidated.
            with self.collaboration_transaction(conv):
                conv['multi_ai_collaboration'] = updated
                conv['gpt_collaboration'] = collaboration_allowed(conv)
                conv['multi_ai_collaboration_updated_at'] = now()
            return copy.deepcopy(updated)

    def roundtable_policy(self, thread_id):
        with self.lock:
            conv = next((c for c in self.state['conversations']
                         if c.get('codex_thread_id') == thread_id and not c.get('deleted_at')), None)
            if conv is None:
                raise KeyError('本聊天尚未绑定。')
            return roundtable_policy(conv)

    def set_roundtable(self, cid, body):
        with self.lock:
            if self.stopping or self.maintenance:
                raise ConflictError('服务暂时不能保存设置。')
            conv = self.conversation(cid)
            if not conv or not conv.get('codex_thread_id'):
                raise ValueError('请先进入这条 Codex 聊天的固定入口。')
            updated = patch_roundtable(roundtable_policy(conv), body, conv['codex_thread_id'])
            with self.collaboration_transaction(conv):
                conv['roundtable'] = updated
                conv['roundtable_updated_at'] = now()
            return copy.deepcopy(updated)

    def record_roundtable_message(self, cid, body):
        """Store a Codex-observed contribution without adding a CLI task."""
        with self.lock:
            if self.stopping or self.maintenance:
                raise ConflictError('服务暂时不能保存圆桌发言。')
            conv = self.conversation(cid)
            if not conv or not conv.get('codex_thread_id'):
                raise ValueError('请先进入这条 Codex 聊天的固定入口。')
            clean, settings, branch = roundtable_message_request(body, conv)
            existing = validate_roundtable_messages(conv)
            same_source = next((message for message in existing
                                if message['source_id'] == clean['source_id']), None)
            if same_source is not None:
                if any(same_source[key] != clean[key] for key in clean if key != 'codex_thread_id'):
                    raise ConflictError('同一来源标识已有不同发言，原记录已保留。')
                return {'recorded': True, 'message': same_source, 'duplicate': True}
            policy = collaboration_policy(conv)
            if (not policy['enabled'] or clean['provider'] != 'codex'
                    and not policy['providers'][clean['provider']]['enabled']):
                raise ValueError('本聊天已暂停该提供方协作，未记录新的发言。')
            record = {key: clean[key] for key in clean if key != 'codex_thread_id'}
            record.update(id=uuid.uuid4().hex, created_at=now(), roundtable=settings, branch=branch,
                          role='host' if settings['host'] == clean['provider'] else 'participant',
                          provenance='codex_observed')
            candidate = existing + [record]
            validate_roundtable_messages(dict(conv, roundtable_messages=candidate))
            had_messages = 'roundtable_messages' in conv
            original_messages, original_updated = conv.get('roundtable_messages'), conv.get('updated_at')
            conv['roundtable_messages'] = candidate
            conv['updated_at'] = record['created_at']
            try:
                # This content is intentionally absent from the offline policy
                # mirror. Persist it without rewriting policy readiness/choices.
                write_json(self.file, self.state)
                self._touch(conv)
            except Exception:
                if had_messages:
                    conv['roundtable_messages'] = original_messages
                else:
                    conv.pop('roundtable_messages', None)
                conv['updated_at'] = original_updated
                try:
                    write_json(self.file, self.state)
                except Exception:
                    pass
                self._touch(conv)
                raise
            return {'recorded': True, 'message': copy.deepcopy(record), 'duplicate': False}

    def meeting_records(self, cid, mid=None, branch=None, before=None, limit=10, ids=None):
        """Independent transcript reads; no provider call or implicit binding."""
        with self.lock:
            conv = self.conversation(cid)
            if not conv or not conv.get('codex_thread_id'):
                raise KeyError('固定聊天不存在。')
            records = validate_meetings(conv)
            if type(limit) is not int or not 1 <= limit <= 20:
                raise ValueError('圆桌分页数量需为 1–20。')
            if branch is not None and branch not in conv.get('branches', {}):
                raise ValueError('工作会话不存在。')
            if ids is not None:
                if (mid is not None or before is not None or not isinstance(ids, list) or not 1 <= len(ids) <= 20
                        or any(not isinstance(ident, str) or not re.fullmatch(r'[a-f0-9]{32}', ident) for ident in ids)
                        or len(set(ids)) != len(ids)):
                    raise ValueError('请选择当前工作会话中最多 20 场不同圆桌。')
                selected = {item['id']: item for item in records if branch is None or item['branch'] == branch}
                if any(ident not in selected for ident in ids):
                    raise KeyError('圆桌记录不属于当前工作会话。')
                return {'meetings': [meeting_view(conv, selected[ident]) for ident in ids]}
            if mid is not None:
                record = next((item for item in records if item['id'] == mid), None)
                if record is None or branch is not None and record['branch'] != branch:
                    raise KeyError('本聊天没有这场圆桌。')
                return meeting_view(conv, record)
            chosen = [item for item in records if branch is None or item['branch'] == branch]
            end = len(chosen)
            if before is not None:
                end = next((i for i, item in enumerate(chosen) if item['id'] == before), None)
                if end is None:
                    raise KeyError('圆桌分页位置不属于当前工作会话。')
            start = max(0, end - limit)
            page = chosen[start:end]
            return {'meetings': [meeting_view(conv, item) for item in page],
                    'total': len(chosen), 'has_older': start > 0,
                    'first_id': page[0]['id'] if page else None,
                    'last_id': page[-1]['id'] if page else None}

    def meeting_context(self, cid, mid, stage, provider):
        """On-demand scoped evidence; no dispatch, policy write or history reset."""
        with self.lock:
            conv = self.conversation(cid)
            if not conv or not conv.get('codex_thread_id'):
                raise KeyError('固定聊天不存在。')
            meeting = next((m for m in validate_meetings(conv) if m['id'] == mid), None)
            if meeting is None:
                raise KeyError('本聊天没有这场圆桌。')
            return meeting_context(conv, meeting, stage, provider)

    def meeting_workspace(self, cid, mid):
        """Read actual saved meeting materials only when the user asks to see them."""
        with self.lock:
            conv = self.conversation(cid)
            if not conv or not conv.get('codex_thread_id'):
                raise KeyError('固定聊天不存在。')
            record = next((item for item in validate_meetings(conv) if item['id'] == mid), None)
            if record is None:
                raise KeyError('本聊天没有这场圆桌。')
            return meeting_workspace(conv, record)

    def set_roundtable_workspace_preferences(self, cid, body):
        """Save next-meeting presentation guidance without changing permissions."""
        with self.lock:
            if self.stopping or self.maintenance:
                raise ConflictError('服务暂时不能保存圆桌预设。')
            conv = self.conversation(cid)
            if not conv or not conv.get('codex_thread_id'):
                raise ValueError('请进入当前 Codex 固定聊天。')
            updated = patch_roundtable_workspace_preferences(conv, body)
            if conv.get('roundtable_workspace_preferences') == updated:
                return copy.deepcopy(updated)
            existed = 'roundtable_workspace_preferences' in conv
            previous = copy.deepcopy(conv.get('roundtable_workspace_preferences'))
            old_stamp = conv.get('updated_at')
            conv.update(roundtable_workspace_preferences=updated, updated_at=now())
            try:
                write_json(self.file, self.state)
                self._touch(conv)
            except Exception:
                if existed:
                    conv['roundtable_workspace_preferences'] = previous
                else:
                    conv.pop('roundtable_workspace_preferences', None)
                conv['updated_at'] = old_stamp
                try:
                    write_json(self.file, self.state)
                except Exception:
                    pass
                self._touch(conv)
                raise
            return copy.deepcopy(updated)

    def persist_meetings(self, conv, records, stamp):
        """Content-only transaction; policy mirror and credentials are untouched."""
        candidate = dict(conv, roundtable_meetings=records)
        validate_meetings(candidate)
        existed, previous = 'roundtable_meetings' in conv, conv.get('roundtable_meetings')
        old_updated = conv.get('updated_at')
        conv.update(roundtable_meetings=records, updated_at=stamp)
        try:
            write_json(self.file, self.state)
            self._touch(conv)
        except Exception:
            if existed:
                conv['roundtable_meetings'] = previous
            else:
                conv.pop('roundtable_meetings', None)
            conv['updated_at'] = old_updated
            try:
                write_json(self.file, self.state)
            except Exception:
                pass
            self._touch(conv)
            raise

    def create_meeting(self, cid, body):
        with self.lock:
            if self.stopping or self.maintenance:
                raise ConflictError('服务暂时不能创建圆桌。')
            conv = self.conversation(cid)
            if not conv or not conv.get('codex_thread_id'):
                raise ValueError('请进入当前 Codex 固定聊天。')
            records = validate_meetings(conv)
            candidate = conv
            if not conv.get('branches') and isinstance(body, dict) and body.get('branch', 'main') == 'main':
                # A Muse-only first meeting needs a display lane, not a dummy
                # CLI request or an invented official conversation.
                candidate = dict(conv, branches={})
                branch_for(candidate, 'main', create=True)
            # Validate input even on idempotent delivery; never accept another
            # thread's source ID or silently replace a topic or participant set.
            record = new_meeting(candidate, body, uuid.uuid4().hex, now())
            existing = next((m for m in records if m['source_id'] == record['source_id']), None)
            if existing:
                if any(existing[k] != record[k] for k in ('topic', 'branch', 'codex_thread_id')):
                    raise ConflictError('同一来源已有不同圆桌，原会议已保留。')
                return {'meeting': meeting_view(conv, existing), 'duplicate': True, 'model_started': False}
            old_lane = {key: conv.get(key) for key in ('branches', 'primary_branch')}
            if candidate is not conv:
                conv.update(branches=candidate['branches'], primary_branch=candidate['primary_branch'])
            try:
                self.persist_meetings(conv, records + [record], record['created_at'])
            except Exception:
                if candidate is not conv:
                    conv.update(old_lane)
                    try:
                        write_json(self.file, self.state)
                    except Exception:
                        pass
                raise
            return {'meeting': meeting_view(conv, record), 'duplicate': False, 'model_started': False}

    def record_meeting_event(self, cid, mid, body):
        with self.lock:
            if self.stopping or self.maintenance:
                raise ConflictError('服务暂时不能保存圆桌发言。')
            conv = self.conversation(cid)
            if not conv or not conv.get('codex_thread_id'):
                raise ValueError('请进入当前 Codex 固定聊天。')
            records = validate_meetings(conv)
            updated, duplicate = record_meeting_event(conv, mid, body, now())
            if not duplicate:
                self.persist_meetings(conv, [updated if m['id'] == mid else m for m in records], updated['updated_at'])
            return {'recorded': True, 'duplicate': duplicate, 'meeting': meeting_view(conv, updated), 'model_started': False}

    def meeting_action(self, cid, mid, body):
        """Explicit local housekeeping only; never dispatch, retry or stop a model."""
        common = {'codex_thread_id', 'action', 'provider', 'stage'}
        if not isinstance(body, dict) or not common <= set(body):
            raise ValueError('会议操作字段无效。')
        action = body.get('action')
        extra = {'source_id', 'reason', 'acknowledge'} if action == 'mark_unavailable' else set()
        if action not in ('mark_unavailable', 'inspect_result') or set(body) != common | extra:
            raise ValueError('请选择受支持的会议操作及明确参数。')
        if (not isinstance(body.get('provider'), str) or body['provider'] not in ('codex', 'agy', 'muse', 'claude')
                or not isinstance(body.get('stage'), str) or body['stage'] not in ('proposal', 'review', 'summary')):
            raise ValueError('会议操作提供方或阶段无效。')
        with self.lock:
            if self.stopping or self.maintenance:
                raise ConflictError('服务正在重载或维护，未处理会议操作。')
            conv = self.conversation(cid)
            if not conv or not conv.get('codex_thread_id') or body['codex_thread_id'] != conv['codex_thread_id']:
                raise ValueError('会议操作与当前固定聊天不一致。')
            records = validate_meetings(conv)
            record = next((item for item in records if item['id'] == mid), None)
            if record is None:
                raise ValueError('本聊天没有这场圆桌。')
            view = meeting_view(conv, record)
            slot = next((item for item in view['slots'] if item['provider'] == body['provider'] and item['stage'] == body['stage']), None)
            if slot is None:
                raise ValueError('操作不属于本会议冻结的参与方或主持阶段。')
            if action == 'inspect_result':
                native = bool(slot['turn_id'])
                inspection = {'source_kind': 'native_cli' if native else 'not_sent' if slot['status'] in ('pending', 'unavailable') else 'codex_observed',
                              'source_url': slot['source_url'], 'turn_id': slot['turn_id'], 'status': slot['status'],
                              'detail': ('仅核对本地保存的原生 CLI 任务；未重跑或再次向服务器验证。' if native else
                                         '这是 Codex 已同步的网页观察记录；请打开原会话核对实际发送与回复，未重发。' if slot['provider'] in ('muse', 'claude') and slot['status'] not in ('pending', 'unavailable') else
                                         '此阶段尚无已发送任务，未调用模型。' if slot['status'] in ('pending', 'unavailable') else '仅核对当前 Codex 已记录的发言，未调用模型。')}
                return copy.deepcopy({'meeting': view, 'slot': slot, 'inspection': inspection, 'model_started': False})
            if (body.get('acknowledge') is not True or not isinstance(body.get('reason'), str)
                    or not body['reason'].strip() or len(body['reason']) > 1000 or '\x00' in body['reason']):
                raise ValueError('请填写缺席理由，并明确确认此阶段从未发送。')
            event = {'codex_thread_id': body['codex_thread_id'], 'source_id': body['source_id'],
                     'provider': body['provider'], 'stage': body['stage'], 'status': 'unavailable',
                     'text': '', 'source_url': '', 'detail': body['reason'].strip()}
            existing = next((item for item in record['events'] if item['source_id'] == body['source_id']), None)
            if existing:
                if any(existing[key] != value for key, value in event.items() if key != 'codex_thread_id'):
                    raise ConflictError('该操作标识已用于其他记录，原记录保留。')
            elif slot['status'] != 'pending' or slot['turn_id']:
                raise ConflictError('此阶段已经发送、关联任务或结束；请核对原始结果，不能标为未发送缺席。')
            elif not slot['can_mark_unavailable']:
                raise ConflictError('请先核对并结束前一阶段；不能跳过前轮直接收尾。')
            updated, duplicate = record_meeting_event(conv, mid, event, now())
            if not duplicate:
                self.persist_meetings(conv, [updated if item['id'] == mid else item for item in records], updated['updated_at'])
            result = meeting_view(conv, updated)
            return {'recorded': True, 'duplicate': duplicate, 'meeting': result,
                    'summary_ready': result['phase'] == 'summary', 'model_started': False}

    def console_startup_info(self):
        return self.coordination.metadata()

    def set_console_startup(self, body):
        if not isinstance(body, dict) or set(body) != {'launcher_path'}:
            raise ValueError('请选择本机启动入口文件。')
        selected = launcher_path(body['launcher_path'])
        with self.lock:
            if self.stopping or self.maintenance:
                raise ConflictError('服务暂时不能保存设置。')
            with self.collaboration_transaction():
                self.coordination.launcher = selected
            return self.console_startup_info()

    def set_auto_start_console(self, cid, body):
        if (not isinstance(body, dict) or set(body) != {'codex_thread_id', 'auto_start_console'}
                or type(body.get('auto_start_console')) is not bool):
            raise ValueError('自动启动开关必须为开启或关闭。')
        with self.lock:
            if self.stopping or self.maintenance:
                raise ConflictError('服务暂时不能保存设置。')
            conv = self.conversation(cid)
            if not conv or not conv.get('codex_thread_id') or conv['codex_thread_id'] != body['codex_thread_id']:
                raise ValueError('自动启动设置与当前聊天不一致。')
            with self.collaboration_transaction(conv):
                conv['auto_start_console'] = body['auto_start_console']
            return {'codex_thread_id': conv['codex_thread_id'], 'auto_start_console': conv['auto_start_console'],
                    'console_startup': self.console_startup_info()}

    def set_preferred_model(self, model):
        with self.lock:
            if self.stopping:
                raise ValueError('服务正在关闭。')
            if not isinstance(model, str) or (model and model not in {m['id'] for m in self.health['models']}):
                raise ValueError('请选择当前列表中的模型，或智能选择。')
            self.state['preferred_model'] = model
            self.state['preferred_model_updated_at'] = now()
            self.save()
            return {'preferred_model': model, 'preferred_model_updated_at': self.state['preferred_model_updated_at']}

    def set_model_order(self, order, version=None, if_unset=False):
        order = clean_order(order)
        with self.lock:
            if self.stopping or self.maintenance:
                raise ConflictError('服务暂时不能保存设置。')
            current = self.state.get('model_order_version', 0)
            if if_unset is True and current:
                return {'model_order': self.state['model_order'], 'model_order_version': current, 'effective_model_order': effective_order(self.policy_snapshot())}
            if if_unset is not True and (type(version) is not int or version != current):
                raise ConflictError('排序已被其他窗口更新，请刷新后调整。')
            self.state.update(model_order=order, model_order_version=current + 1)
            self.save()
            return {'model_order': order, 'model_order_version': current + 1, 'effective_model_order': effective_order(self.policy_snapshot())}

    def set_fallback(self, cid, kind, value):
        if kind not in ('text', 'image'):
            raise ValueError('请选择文字或生图。')
        value = clean_fallback(value)
        with self.lock:
            if self.stopping or self.maintenance:
                raise ConflictError('服务暂时不能保存设置。')
            conv = self.conversation(cid)
            if not conv:
                raise ValueError('窗口不存在。')
            if any(m not in {x['id'] for x in self.health['models']} for m in value.get('models', [])):
                raise ValueError('候补列表含当前不可用模型，请重新选择。')
            conv.setdefault('fallback_preferences', {})[kind] = value
            self.save(conv)
            return {'id': cid, 'fallback_preferences': copy.deepcopy(conv['fallback_preferences']),
                    'model_resolution': self.model_resolution(conv)}

    def set_global_model_policy(self, kind, settings, version):
        if kind not in ('text', 'image'):
            raise ValueError('请选择文字或生图。')
        value = clean_global_model_policy({kind: settings})
        with self.lock:
            if self.stopping or self.maintenance:
                raise ConflictError('服务暂时不能保存设置。')
            current = self.state.get('global_model_policy_version', 0)
            if type(version) is not int or version != current:
                raise ConflictError('全局智能选择已被其他窗口更新，请刷新后调整。')
            if any(m not in {x['id'] for x in self.health['models']} for m in value[kind].get('models', [])):
                raise ValueError('模型列表含当前不可用模型，请重新选择。')
            self.state.setdefault('global_model_policy', {})[kind] = value[kind]
            self.state['global_model_policy_version'] = current + 1
            self.save()
            return {'global_model_policy': copy.deepcopy(self.state['global_model_policy']),
                    'global_model_policy_version': current + 1, 'model_resolution': self.model_resolution({})}

    def blocked_models(self):
        account = self.health.get('account') or {}
        if account.get('state') != 'known' or not account.get('email'):
            return []
        owner = hashlib.sha256(account['email'].encode()).hexdigest()
        stamp = time.time()
        self.fallback_blocks = {k:v for k,v in self.fallback_blocks.items()
                                if k[0] == owner and v['until'] > stamp}
        blocked = []
        for model in self.health['models']:
            for (_, scope), info in self.fallback_blocks.items():
                if scope.startswith('pool:'):
                    pool = scope[5:]
                    bucket = (account.get('quota') or {}).get(pool) or {}
                    if (str(account.get('checked_at') or '') > info['checked_at']
                            and type(bucket.get('remaining_fraction')) in (int, float)
                            and bucket['remaining_fraction'] > 0):
                        info['until'] = 0
                        continue
                    match = pool in quota_groups(model['id'])
                else:
                    match = model_family(model['id']) == scope[7:]
                if match:
                    blocked.append(model['id']); break
        return blocked

    def policy_snapshot(self):
        return {'health': self.health, 'preferred_model': self.state.get('preferred_model', ''),
                'global_model_policy': self.state.get('global_model_policy', {}),
                'model_order': self.state.get('model_order', []), 'blocked_models': self.blocked_models()}

    def verified_image_models(self):
        return list(dict.fromkeys(t['model'] for c in self.state['conversations'] for t in c['turns']
                                  if t.get('kind') == 'image' and t.get('status') == 'completed' and t.get('images')))

    def choose_turn_model(self, turn, retry=False):
        return select_turn_model(self.policy_snapshot(), turn, self.verified_image_models(), retry)

    def model_resolution(self, conv, policy=None, images=None):
        policy = self.policy_snapshot() if policy is None else policy
        images = self.verified_image_models() if images is None else images
        return {kind: window_model_resolution(policy, conv, kind, images) for kind in ('text', 'image')}

    def mark_quota_rejection(self, model, error, account=None):
        account = account or self.health.get('account') or {}
        if not quota_rejection(error) or account.get('state') != 'known' or not account.get('email'):
            return
        owner = hashlib.sha256(account['email'].encode()).hexdigest()
        pools = [p for p in quota_groups(model) if p in error]
        scopes = ['pool:' + p for p in pools] or ['family:' + model_family(model)]
        for scope in scopes:
            self.fallback_blocks[(owner, scope)] = {'until': time.time() + 300,
                                                   'checked_at': account.get('checked_at') or ''}
        while len(self.fallback_blocks) > 32:
            self.fallback_blocks.pop(next(iter(self.fallback_blocks)))

    def record_model_change(self, turn, model, reason):
        old = turn['model']
        if old != model:
            turn.setdefault('model_changes', []).append({'from': old, 'to': model, 'reason': reason, 'at': now()})
            turn['model_changes'] = turn['model_changes'][-3:]
            turn['model'] = model
            record_progress(turn, 'model:' + str(len(turn['model_changes'])), (old + ' → ' + model + ' · ' + reason)[:160], 'done')

    def set_window_model(self, cid, kind, model):
        if kind not in ('text', 'image'):
            raise ValueError('请选择文字或图片模式。')
        with self.lock:
            if self.stopping or self.maintenance:
                raise ConflictError('服务暂时不能保存设置，请稍后重试。')
            conv = self.conversation(cid)
            if conv is None:
                raise ValueError('固定窗口不存在。')
            if model is not None and (not isinstance(model, str) or model and model not in {m['id'] for m in self.health['models']}):
                raise ValueError('请选择可用模型、智能选择或沿用全局。')
            preferences = conv.setdefault('model_preferences', {})
            if model is None:
                preferences.pop(kind, None)
            else:
                preferences[kind] = model
            conv['model_preferences_updated_at'] = now()
            self.save(conv)
            return {'id': cid, 'model_preferences': copy.deepcopy(preferences), 'model_resolution': self.model_resolution(conv),
                    'model_preferences_updated_at': conv['model_preferences_updated_at']}

    def set_auto_open_activity(self, cid, enabled):
        if type(enabled) is not bool:
            raise ValueError('自动打开活动窗口必须为开启或关闭。')
        with self.lock:
            if self.stopping or self.maintenance:
                raise ConflictError('服务暂时不能保存设置，请稍后重试。')
            conv = self.conversation(cid)
            if conv is None or not conv.get('codex_thread_id'):
                raise ValueError('请先进入这条 Codex 聊天的固定入口。')
            conv['auto_open_activity'] = enabled
            conv['auto_open_activity_updated_at'] = now()
            self.save(conv)
            return {'id': cid, 'enabled': enabled, 'updated_at': conv['auto_open_activity_updated_at']}

    def record_review(self, cid, tid, thread_id, value):
        review = validate_review(value)
        with self.lock:
            conv = self.conversation(cid)
            if not conv or not thread_id or conv.get('codex_thread_id') != thread_id:
                raise ValueError('复核必须来自这条固定入口对应的 Codex 聊天。')
            turn = next((t for t in conv['turns'] if t['id'] == tid), None)
            if turn is None or turn.get('source') != 'codex':
                raise ValueError('只可记录本聊天实际 Codex 派发任务的复核。')
            if turn['status'] in BUSY:
                raise ConflictError('Agy 任务尚未结束，不能提前标记整合完成。')
            if review['verification']['status'] == 'passed' and (turn['status'] != 'completed'
                    or not (turn.get('response', '').strip() or turn.get('images'))):
                raise ValueError('任务未成功完成，不能标记协作验证通过。')
            if turn.get('review'):
                turn['review_history'] = (turn.get('review_history', []) + [turn['review']])[-4:]
            review.update(source='codex', thread_id=thread_id, updated_at=now())
            turn['review'] = review
            self.save(conv)
            return {'recorded': True, 'turn_id': tid, 'review': copy.deepcopy(review), 'model_called': False}

    @staticmethod
    def collaboration_state(conv):
        turn = next((t for t in reversed(conv['turns']) if t.get('source') == 'codex'), None)
        if not turn:
            return {'stage': 'idle', 'turn_id': None}
        status = turn['status']; review = turn.get('review')
        stage = ('queued' if status == 'queued' else 'working' if status in BUSY else
                 'failed' if status != 'completed' else 'awaiting_codex' if not review else
                 'verified' if review['verification']['status'] == 'passed' else 'reviewed')
        return {'stage': stage, 'turn_id': turn['id'],
                'updated_at': (review or {}).get('updated_at') or turn.get('ended_at') or turn.get('created_at')}

    def queue_snapshot(self):
        with self.lock:
            lookup = {t['id']: (c, t) for c in self.state['conversations'] for t in c['turns'] if t['status'] in BUSY}
            def item(cid, tid):
                conv, turn = lookup[tid]; lane = branch_for(conv, turn.get('branch'))
                return {'id': tid, 'conversation_id': cid, 'title': conv['title'],
                        'branch': turn.get('branch'), 'session_title': (lane or {}).get('title', '原会话'),
                        'prompt': (turn.get('prompt', '').splitlines() or [''])[0][:120], 'model': turn.get('model', ''),
                        'status': turn['status'], 'created_at': turn.get('created_at'), 'started_at': turn.get('started_at')}
            queued = [item(cid, tid) for cid, tid in self.jobs.snapshot()
                      if tid in lookup and lookup[tid][1]['status'] == 'queued']
            running = [item(c['id'], t['id']) for c, t in lookup.values() if t['status'] != 'queued']
            for index, row in enumerate(queued):
                group = (row['conversation_id'], row['branch'])
                row.update(position=index + 1,
                           can_up=index > 0 and group != (queued[index-1]['conversation_id'], queued[index-1]['branch']),
                           can_down=index+1 < len(queued) and group != (queued[index+1]['conversation_id'], queued[index+1]['branch']))
            raw = json.dumps([[t['id'], t['status']] for t in running + queued]).encode()
            return {'version': hashlib.blake2s(raw, digest_size=12).hexdigest(), 'running': running, 'queued': queued}

    def reorder_queue(self, tid, direction, version):
        with self.lock:
            current = self.queue_snapshot()
            if not isinstance(version, str) or version != current['version']:
                raise ConflictError('队列已变化，请刷新后再调整。')
            groups = {t['id']: (t['conversation_id'], t['branch']) for t in current['queued']}
            if tid not in groups:
                raise ConflictError('任务已开始或已经结束，仅可调整排队任务。')
            self.jobs.move(tid, direction, groups)
            self.save()
            return self.queue_snapshot()

    def prepare_reload(self):
        with self.lock:
            if not self.reload_callback:
                raise ValueError('请从前台 Antigravity.command 启动后使用重载。')
            if (self.stopping or self.maintenance or self.processes or self.health_processes or self.jobs.snapshot()
                    or any(t['status'] in BUSY for c in self.state['conversations'] for t in c['turns'])
                    or any(thread and thread.is_alive() for thread in (self.health_thread, self.account_thread))):
                raise ConflictError('请等待任务与账号检查结束后再重载。')
            validate_sources(ROOT)
            self.save()
            self.reloading = True
            self.stopping = True
            return {'accepted': True, 'previous_instance': self.instance_id}

    def require_idle(self):
        if (self.stopping or self.processes or self.health_processes or self.jobs.snapshot()
                or any(t['status'] in BUSY for c in self.state['conversations'] for t in c['turns'])
                or any(t and t.is_alive() for t in (self.health_thread, self.account_thread))):
            raise ConflictError('请等待所有任务与账号检查结束后再操作。')

    @contextmanager
    def panel_maintenance(self):
        with self.lock:
            if self.maintenance:
                raise ConflictError('正在处理备份，请稍后再操作。')
            self.require_idle()
            self.maintenance = True
            self._touch()
        try:
            yield
        finally:
            with self.lock:
                self.maintenance = False
                self._touch()

    def export_backup(self, preferences):
        with self.panel_maintenance():
            return self.backups.create(self.state, preferences)

    def preview_backup(self, incoming):
        with self.panel_maintenance():
            return self.backups.preview(incoming)

    def export_configuration(self, ui, cid=None):
        with self.lock:
            conv = self.conversation(cid) if cid else None
            if cid and not conv:raise ValueError('当前窗口不存在。')
            return configuration_document(self.state,self.software_updates.snapshot()['preferences'],
                                          ui if isinstance(ui,dict) else {},conv,APP_VERSION)

    def configuration_revision(self, cid=None):
        document=self.export_configuration({},cid)['settings']
        document.pop('ui')
        return hashlib.sha256(json.dumps(document,sort_keys=True).encode()).hexdigest()

    def preview_configuration(self, document, cid=None, apply_window=False):
        if type(apply_window) is not bool:raise ValueError('请选择是否导入窗口设置。')
        value=validate_configuration(document);settings=value['settings'];window=settings['window']
        if apply_window and (window is None or not cid or not self.conversation(cid)):
            raise ValueError('请先进入目标聊天，或取消导入窗口设置。')
        if apply_window and any(key in window for key in ('roundtable', 'roundtable_workspace_preferences')) and not self.conversation(cid).get('codex_thread_id'):
            raise ValueError('圆桌主持配置只能导入已绑定 Codex 的固定聊天。')
        available={m['id'] for m in self.health['models']}
        selected=[settings['global']['preferred_model']]
        for shared in settings['global'].get('global_model_policy', {}).values():
            selected.extend(shared.get('models', []))
        if apply_window:
            selected.extend(window.get('model_preferences',{}).values())
            for fallback in window.get('fallback_preferences',{}).values():selected.extend(fallback.get('models',[]))
        missing=sorted(set(selected)-available-{''})
        if missing:raise ValueError('当前 CLI 不支持配置中的模型：'+', '.join(missing[:8])+'。请更新 CLI 或调整配置后重试。')
        return {'document':value,'revision':self.configuration_revision(cid),'apply_window':apply_window}

    def import_configuration(self, document, ui, cid=None, apply_window=False, revision=None, acknowledge=False):
        if acknowledge is not True:raise ValueError('请先预览并确认导入配置。')
        with self.lock,self.software_updates.lock:
            self.require_idle()
            if self.maintenance:raise ConflictError('正在维护，请稍后导入。')
            manager=self.software_updates
            if manager.requested or manager.state['status'] in ('checking','downloading'):
                raise ConflictError('请等待更新检查或下载结束后导入。')
            preview=self.preview_configuration(document,cid,apply_window)
            if not isinstance(revision,str) or not hmac.compare_digest(revision,preview['revision']):
                raise ConflictError('配置已被其他窗口修改，请重新预览后导入。')
            previous=self.export_configuration(ui,cid)
            folder=self.data/'configuration-backups';folder.mkdir(exist_ok=True)
            ident=uuid.uuid4().hex;write_json(folder/(ident+'.json'),previous)
            original=self.state;old_preferences=copy.deepcopy(manager.preferences);old_update_state=copy.deepcopy(manager.state)
            pending=manager.cache/'pending.json';pending_bytes=pending.read_bytes() if pending.exists() else None
            prefs_file=manager.cache/'preferences.json';prefs_bytes=prefs_file.read_bytes() if prefs_file.exists() else None
            settings=preview['document']['settings'];candidate=dict(original)
            candidate.update(preferred_model=settings['global']['preferred_model'],preferred_model_updated_at=now(),
                             model_order=settings['global']['model_order'],model_order_version=original.get('model_order_version',0)+1)
            if 'global_model_policy' in settings['global']:
                candidate.update(global_model_policy=copy.deepcopy(settings['global']['global_model_policy']),
                                 global_model_policy_version=original.get('global_model_policy_version', 0)+1)
            if apply_window:
                window=dict(self.conversation(cid))
                for key in ('model_preferences','fallback_preferences','gpt_collaboration','auto_open_activity'):
                    if key in settings['window']:window[key]=copy.deepcopy(settings['window'][key])
                    else:window.pop(key,None)
                for key in ('gpt_collaboration','auto_open_activity'):window[key+'_updated_at']=now()
                if window.get('codex_thread_id'):
                    portable = settings['window'].get('multi_ai_collaboration')
                    if portable is not None:
                        window['multi_ai_collaboration'] = dict(copy.deepcopy(portable), codex_thread_id=window['codex_thread_id'])
                    elif 'gpt_collaboration' in settings['window']:
                        # Explicit old-format window imports retain their pause;
                        # they do not re-enable new providers.
                        window.pop('multi_ai_collaboration', None)
                    migrate_collaboration(window)
                    if 'roundtable' in settings['window']:
                        window['roundtable'] = dict(copy.deepcopy(settings['window']['roundtable']), codex_thread_id=window['codex_thread_id'])
                    if 'roundtable_workspace_preferences' in settings['window']:
                        window['roundtable_workspace_preferences'] = dict(copy.deepcopy(settings['window']['roundtable_workspace_preferences']), codex_thread_id=window['codex_thread_id'])
                candidate['conversations']=[window if row['id']==cid else row for row in original['conversations']]
            try:
                # The updater and task workers cannot observe a half-applied import.
                self.state=candidate;self.save(require_coordination=True)
                manager.set_preferences(settings['updates'])
            except Exception:
                self.state=original;manager.preferences=old_preferences;manager.state=old_update_state
                self.coordination.invalidate()
                write_json(self.file,original)
                self.coordination.publish(original['conversations'])
                self.coordination_error = ''
                for file,raw in ((prefs_file,prefs_bytes),(pending,pending_bytes)):
                    if raw is None:file.unlink(missing_ok=True)
                    else:file.write_bytes(raw)
                self._touch()
                raise
            return {'imported':True,'ui':settings['ui'],'rollback_url':'/api/configuration/backups/'+ident,
                    'window_applied':apply_window,'notice':'配置已导入，聊天记录和登录保持原样。'}

    def restore_backup(self, ident, preferences, acknowledge=False):
        if acknowledge is not True:
            raise ValueError('请确认以备份替换面板数据；当前数据会另存回滚备份。')
        with self.panel_maintenance():
            with self.lock:
                self.require_idle()
                missing = []
                def prepare(state):
                    state.setdefault('history_overrides', {})
                    state.setdefault('preferred_model', '')
                    state.setdefault('preferred_model_updated_at', None)
                    state.setdefault('global_model_policy', {})
                    state.setdefault('global_model_policy_version', 0)
                    for conv in state['conversations']:
                        migrate_collaboration(conv)
                        for lane in normalize_sessions(conv)['branches'].values():
                            sid = lane.get('agy_id')
                            absent = bool(sid and not (self.activity.root / 'conversations' / (sid + '.db')).is_file())
                            lane['restored_context_missing'] = absent
                            if absent:
                                missing.append({'conversation_id': conv['id'], 'branch': lane['id'], 'title': lane['title']})
                self.coordination.invalidate()
                state, result = self.backups.restore(ident, self.state, preferences, prepare=prepare)
                self.state = state
                self.coordination.publish(self.state['conversations'])
                self.coordination_error = ''
                self._touch()
                result['missing_contexts'] = missing
                return result

    def open_account_switcher(self):
        self.require_idle()
        if not Path('/Applications/CC Switch.app').is_dir():
            raise ValueError('未找到 CC Switch；请手动打开官方 CLI 登录。')
        result = subprocess.run(['/usr/bin/open', '-b', 'com.ccswitch.desktop'],
                                capture_output=True, timeout=5, check=False)
        if result.returncode:
            raise ValueError('未能打开 CC Switch，请手动启动。')
        self.account_switch_origin = copy.deepcopy(self.health['account'])
        return {'opened': True, 'app': 'CC Switch', 'account_changed': False}

    def import_codex_bindings(self):
        """Apply staged, user-authorized imports after the foreground launcher restarts."""
        folder = self.data / 'pending-codex'
        if not folder.is_dir():
            return
        for path in list(folder.glob('*.json'))[:512]:
            if path.is_symlink() or path.stat().st_size > 150000:
                continue
            try:
                self.bind_codex(**json.loads(path.read_text(encoding='utf-8')))
                path.unlink()
            except (OSError, ValueError, TypeError):
                announce('一项 Codex 会话导入尚未完成；原文件已保留。')

    def manage_history(self, source, cid, action, title=None):
        """Change this console's history only; official CLI files remain read-only."""
        pattern = r'[a-f0-9]{32}' if source == 'panel' else r'[a-f0-9]{8}(?:-[a-f0-9]{4}){3}-[a-f0-9]{12}'
        if source not in ('panel', 'external') or not isinstance(cid, str) or not re.fullmatch(pattern, cid):
            raise ValueError('对话标识无效。')
        if action not in ('rename', 'hide', 'unhide', 'delete', 'restore'):
            raise ValueError('不支持此操作。')
        if action == 'rename' and (not isinstance(title, str) or not title.strip() or len(title.strip()) > 60):
            raise ValueError('名称需为 1–60 个字符。')
        with self.lock:
            if self.stopping:
                raise ValueError('服务正在关闭。')
            conv = self.conversation(cid, include_deleted=True) if source == 'panel' else None
            if source == 'panel' and conv is None:
                raise ValueError('对话不存在。')
            if source == 'external' and action in ('delete', 'restore'):
                raise ValueError('其他窗口的会话只能移出本面板列表。')
            if conv and action in ('hide', 'delete') and any(t['status'] in BUSY for t in conv['turns']):
                raise ValueError('任务正在进行，请先停止或等待结束后再移除。')
            if conv and conv.get('deleted_at') and action != 'restore':
                raise ValueError('该对话在回收站，请先恢复。')
            key = source + ':' + cid
            settings = self.state['history_overrides'].setdefault(key, {})
            if action == 'rename':
                if conv is not None:
                    conv.update(title=title.strip(), custom_title=True)
                else:
                    settings['title'] = title.strip()
            elif action == 'hide':
                settings['hidden'] = True
            elif action == 'unhide':
                settings.pop('hidden', None)
            elif action == 'delete':
                conv['deleted_at'] = now()
                settings.pop('hidden', None)
            elif action == 'restore':
                conv.pop('deleted_at', None)
                settings.pop('hidden', None)
            if not settings:
                self.state['history_overrides'].pop(key, None)
            self.save(conv) if conv is not None else self.save()
            return {'ok': True}

    def snapshot(self, summaries=False):
        with self.lock:
            policy, images = self.policy_snapshot(), self.verified_image_models()
            conversations = [dict(self.conversation_metadata(c, compact=summaries), model_resolution=self.model_resolution(c, policy, images))
                             for c in self.state['conversations'] if not c.get('deleted_at')]
            if summaries:
                compact = []
                fields = ('model_changes', 'fallback_notice', 'requested_model', 'fallback_enabled', 'id', 'status', 'kind', 'branch', 'model', 'created_at', 'started_at', 'elapsed', 'events',
                          'progress', 'progress_updated_at', 'progress_omitted', 'roundtable', 'roundtable_role', 'meeting_id', 'meeting_stage')
                for conv in conversations:
                    raw = self.conversation(conv['id'])
                    item = {k: v for k, v in conv.items() if k not in ('turns', 'roundtable_messages', 'roundtable_meetings')}
                    turns = raw['turns']
                    item.update(summary=True, roundtable_message_count=len(raw.get('roundtable_messages', [])), collaboration_state=self.collaboration_state(raw), turn_count=len(turns), branches=session_summaries(raw), revision=self.instance_id + ':' + str(self.conversation_revisions.get(conv['id'], 0)),
                                turns=[{k: t.get(k) for k in fields} for i, t in enumerate(turns)
                                       if t['status'] in BUSY or i == len(turns) - 1])
                    compact.append(item)
                conversations = compact
            trash = [dict({k: c.get(k) for k in ('id', 'title', 'agy_id', 'updated_at', 'deleted_at')}, session_ids=sorted(official_ids(c)))
                     for c in self.state['conversations'] if c.get('deleted_at')]
            return copy.deepcopy({'conversations': conversations, 'trash': trash,
                                  'model_resolution': self.model_resolution({}, policy, images),
                                  'preferred_model': self.state['preferred_model'],
                                  'preferred_model_updated_at': self.state['preferred_model_updated_at'],
                                  'model_order': self.state.get('model_order', []), 'model_order_version': self.state.get('model_order_version', 0),
                                  'global_model_policy': self.state.get('global_model_policy', {}),
                                  'global_model_policy_version': self.state.get('global_model_policy_version', 0),
                                  'effective_model_order': effective_order(self.policy_snapshot()),
                                  'blocked_models': self.blocked_models(), 'verified_image_models': self.verified_image_models(),
                                  'history_overrides': self.state['history_overrides'], 'health': self.health, 'queue': self.queue_snapshot(),
                                  'service': self.service_info(), 'summary_mode': summaries})

    def conversation_page(self, cid, limit=20, before=None, after=None, branch=None, source=None, around=None):
        if not isinstance(limit, int) or not 1 <= limit <= 40 or sum(bool(x) for x in (before, after, around)) > 1:
            raise ValueError('分页参数无效。')
        if source not in (None,'codex'):
            raise ValueError('记录来源无效。')
        with self.lock:
            conv = self.conversation(cid)
            if conv is None:
                raise KeyError('对话不存在。')
            if branch is not None and not branch_for(conv, branch):
                raise KeyError('会话不存在。')
            turns = [t for t in conv['turns'] if (branch is None or t.get('branch') == branch) and (source is None or t.get('source')==source)]; total = len(turns)
            anchor = before or after or around
            if anchor:
                index = next((i for i, turn in enumerate(turns) if turn['id'] == anchor), None)
                if index is None:
                    raise KeyError('该页位置已失效，请返回最新对话。')
            if around:
                start = max(0, index - limit // 2); end = min(total, start + limit)
            elif before:
                end = index; start = max(0, end - limit)
            elif after:
                start = index + 1; end = min(total, start + limit)
            else:
                end = total; start = max(0, end - limit)
            value = {k: v for k, v in conv.items() if k not in ('turns', 'roundtable_messages', 'roundtable_meetings')}
            value = self.conversation_metadata(value, include_meetings=False)
            try:
                records = validate_meetings(conv)
                scoped_meetings = [m for m in records if branch is None or m['branch'] == branch]
                views = [meeting_view(conv, m) for m in scoped_meetings[-10:]]
                value['roundtable_meetings'] = views if not (before or after or around) else []
                value['meeting_summaries'] = [meeting_summary(view) for view in views]
                value['meeting_count'] = len(scoped_meetings)
            except ValueError:
                value.update(roundtable_meetings=[], meeting_summaries=[], meeting_count=0,
                             meeting_error='圆桌记录待核对；原数据已保留。')
            value['model_resolution'] = self.model_resolution(conv)
            chosen = turns[start:end]
            chosen_ids = {turn['id'] for turn in chosen}
            messages = [message for message in conv.get('roundtable_messages', [])
                        if message.get('anchor_turn_id') in chosen_ids]
            value.update(roundtable_messages=messages, roundtable_message_count=len(conv.get('roundtable_messages', [])))
            value.update(turns=chosen, turn_count=total, branches=session_summaries(conv), summary=False, revision=self.instance_id + ':' + str(self.conversation_revisions.get(cid, 0)),
                         page={'start': start + 1 if chosen else 0, 'end': end, 'total': total,
                               'branch': branch, 'source': source,
                               'has_older': start > 0, 'has_newer': end < total,
                               'first_id': chosen[0]['id'] if chosen else None,
                               'last_id': chosen[-1]['id'] if chosen else None})
            return copy.deepcopy(value)

    def context_snapshot(self, cid, branch=None):
        with self.lock:
            conv = self.conversation(cid)
            if conv is None:
                raise KeyError('对话不存在。')
            branch = branch or conv.get('primary_branch') or 'main'
            if not isinstance(branch, str) or not re.fullmatch(r'[a-z][a-z0-9_-]{0,39}', branch):
                raise ValueError('会话标识无效。')
            if branch not in conv.get('branches', {}) and (conv.get('branches') or branch != 'main'):
                raise KeyError('会话不存在。')
            return context_insights(conv, branch)

    def resource_snapshot(self, force=False, summary=False):
        with self.lock:
            roots = {os.getpid(): '控制台'}
            roots.update({p.pid: 'Agy 任务' for p in self.processes.values() if p.poll() is None})
            roots.update({p.pid: '账号与连接检查' for p in self.health_processes.values() if p.poll() is None})
        return self.resources.summary(roots, force=force) if summary else self.resources.sample(roots, force=force)

    def create_branch(self, cid, title, key=None, agy_id=None):
        if not isinstance(title, str) or not title.strip() or len(title.strip()) > 40:
            raise ValueError('会话名称需为1–40个字符。')
        if agy_id is not None:
            if not isinstance(agy_id, str) or not re.fullmatch(r'[a-f0-9]{8}(?:-[a-f0-9]{4}){3}-[a-f0-9]{12}', agy_id):
                raise ValueError('Agy 会话 ID 无效。')
            inventory = self.activity_snapshot(force=True)
            source = next((s for s in inventory['sessions'] if s['id'] == agy_id), None)
            if source is None or source.get('active') or source.get('status') == 'unknown':
                raise ValueError('仅可关联已退出且状态明确的已有会话。')
            if source.get('parent_known') is False:
                raise ValueError('该会话的归属关系尚未同步，请稍后再关联。')
            if source.get('parent_id'):
                raise ValueError('工具子会话保留只读关系，请在所属入口中查看。')
        with self.lock:
            if self.stopping:
                raise ValueError('服务正在关闭。')
            conv = self.conversation(cid)
            if conv is None:
                raise ValueError('固定入口不存在。')
            key = key or 'work-' + uuid.uuid4().hex
            if agy_id:
                if any(t.get('branch') == key and t['status'] in BUSY for t in conv['turns']):
                    raise ValueError('该工作会话正在执行或排队，不能更换关联。')
                owner, owner_key = session_owner(self.state['conversations'], agy_id)
                if owner is not None and (owner is not conv or owner_key != key):
                    raise ValueError('该 Agy 会话已属于另一条工作会话，不能重复关联。')
            existing = branch_for(conv, key)
            if existing and agy_id and existing.get('agy_id') not in (None, agy_id):
                raise ValueError('不能覆盖已有会话绑定。')
            branch = branch_for(conv, key, create=True, title=title)
            branch['title'] = title.strip()
            if agy_id:
                branch.update(agy_id=agy_id, adopted=True)
                if key == conv['primary_branch']:
                    conv['agy_id'] = agy_id
            self.save(conv)
            return copy.deepcopy(branch)

    def service_info(self):
        with self.lock:
            active = sum(t['status'] in BUSY for c in self.state['conversations'] for t in c['turns'])
            return {'app': 'antigravity-console', 'version': APP_VERSION, 'updates': self.software_updates.snapshot(), 'root': str(ROOT), 'pid': os.getpid(),
                    'features': {'quota': True, 'codex_binding': True, 'light_history': True, 'resources': True, 'resource_summary': True, 'context_insights': True, 'window_models': True,
                                 'task_recovery': True, 'context_compaction': True, 'history_search': True, 'panel_backups': True, 'account_switch': True, 'account_sync': True, 'panel_auto_open': True,
                                 'session_groups': True, 'conversation_versions': True, 'proxy_environment': True,
                                  'gpt_collaboration': True, 'multi_ai_collaboration': True, 'console_startup': True, 'roundtable_host': True, 'roundtable_messages': True, 'roundtable_meetings': True, 'roundtable_context': True, 'roundtable_workspace': True, 'roundtable_presets': True, 'roundtable_decisions': True, 'roundtable_meeting_actions': True, 'roundtable_actions': True, 'current_turn_results': True, 'global_model': True,
                                  'collaboration_history': True, 'verified_api_recovery': True,
                                  'quota_failover': True, 'global_model_policy': True, 'model_resolution': True, 'configuration_transfer': True, 'software_updates': True, 'collaboration_results': True, 'queue_priority': True, 'foreground_reload': bool(self.reload_callback)},
                    'console_startup': self.console_startup_info(),
                    'coordination_ready': not bool(self.coordination_error),
                    'coordination_error': self.coordination_error,
                    'instance_id': self.instance_id, 'reloading': self.reloading, 'maintenance': self.maintenance,
                    'network': getattr(self, 'network', {'source': 'inherited'}),
                    'uptime': round(time.monotonic() - self.started), 'active_tasks': active,
                    'task_processes': len(self.processes), 'checking_processes': len(self.health_processes),
                    'stopping': self.stopping, 'can_shutdown': not active and not self.processes and not self.stopping,
                    'can_reload': bool(self.reload_callback) and not self.maintenance and not active and not self.processes and not self.health_processes and not self.stopping
                        and not any(t and t.is_alive() for t in (self.health_thread, self.account_thread))}

    def activity_snapshot(self, force=False):
        with self.lock:
            excluded = {proc.pid for proc in self.health_processes.values()}
            owners = {session: (c['id'], c.get('codex_thread_id'), key, bool(c.get('deleted_at')))
                      for c in self.state['conversations']
                      for key, branch in normalize_sessions(c)['branches'].items()
                      for session in [branch.get('agy_id')] if session}
        value = self.activity.scan(excluded, set(owners), force=force)
        sessions = {s['id']: s for s in value['sessions']}
        for session in value['sessions']:
            target, seen = session['id'], set()
            for _ in range(32):
                if target in owners:
                    cid, thread, branch, deleted = owners[target]
                    session.update(owner_conversation_id=cid, owner_codex_thread_id=thread,
                                   owner_branch=branch, owner_deleted=deleted, related=session['id'] != target, origin='本面板')
                    break
                if target in seen or target not in sessions:
                    break
                seen.add(target)
                target = sessions[target].get('parent_id')
                if not target:
                    break
        return value

    def health_command(self, args, timeout):
        with self.lock:
            if self.stopping:
                raise ValueError('服务正在关闭。')
            proc = subprocess.Popen([self.binary] + args, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                    text=True, start_new_session=True)
            self.health_processes[proc.pid] = proc
        try:
            stdout, stderr = proc.communicate(timeout=timeout)
            return subprocess.CompletedProcess(proc.args, proc.returncode, stdout, stderr)
        finally:
            if proc.poll() is None:
                self.terminate(proc)
                try:
                    proc.communicate(timeout=4)
                except subprocess.TimeoutExpired:
                    proc.kill()
                    proc.communicate()
            with self.lock:
                self.health_processes.pop(proc.pid, None)

    def check_account(self):
        with self.account_lock:
            with self.lock:
                if self.stopping:
                    return copy.deepcopy(self.health['account'])
                self.health['account'] = dict(self.health['account'], state='checking')
            folder = self.data / 'account-check'
            folder.mkdir(exist_ok=True)
            def register(proc):
                with self.lock:
                    self.health_processes[proc.pid] = proc
                    if self.stopping:
                        self.terminate(proc)
            def unregister(proc):
                with self.lock:
                    self.health_processes.pop(proc.pid, None)
            try:
                account = self.account_reader(self.binary, folder, register=register,
                                              unregister=unregister, stopping=lambda: self.stopping)
            except Exception:
                account = {'state': 'unknown', 'email': None, 'name': None,
                           'checked_at': now(), 'source': '官方 CLI statusLine',
                           'detail': '本次账号检查失败，请在终端确认后重试。'}
            with self.lock:
                self.health['account'] = account
                if not self.health.get('account_syncing'):
                    self.health.pop('account_sync_result', None)
                return copy.deepcopy(account)

    def sync_account(self):
        """Refresh shared official identity and models once, without changing login."""
        def sync():
            try:
                before = self.check_account()
                if before.get('state') not in ('known', 'api_key'):
                    raise ValueError('CLI 登录尚未核实，请在 CC Switch 完成选择后重试同步。')
                result = self.health_command(['models'], 35)
                models = []
                for line in result.stdout.splitlines():
                    parts = line.split('\t', 1)
                    if len(parts) == 2 and re.fullmatch(r'[a-zA-Z0-9_.-]+', parts[0]):
                        models.append({'id': parts[0], 'label': parts[1]})
                account = self.check_account()
                if (before.get('state'), before.get('email')) != (account.get('state'), account.get('email')):
                    raise ValueError('同步期间 CLI 账号发生变化，请等待切换完成后重试同步。')
                if result.returncode or not models:
                    raise ValueError('账号已读取，但模型列表未同步；请检查 CLI 登录与网络后重试。')
                origin_key = (origin.get('state'), origin.get('email'))
                changed = origin_key != (account.get('state'), account.get('email')) if origin.get('state') in ('known', 'api_key') else None
                detail = '账号已变，已同步' if changed is True else '账号未变，已同步' if changed is False else '当前账号已核实并同步'
                with self.lock:
                    if self.stopping:
                        raise ValueError('服务正在关闭。')
                    self.health.update(connected=True, models=models, error='',
                                       account_sync_result={'status': 'completed', 'changed': changed, 'detail': detail, 'checked_at': now()})
                announce('CLI ' + detail + '。原服务和聊天保持不变。')
            except Exception as error:
                with self.lock:
                    self.health.update(connected=False, models=[], error=str(error)[:600],
                                       account_sync_result={'status': 'failed', 'changed': None,
                                                            'detail': '同步未完成，可点“同步”重试', 'checked_at': now()})
                if not self.stopping:
                    announce('CLI 账号同步待检查：' + str(error)[:600])
            finally:
                with self.lock:
                    self.health.update(checking=False, account_syncing=False)
                    self._touch()
        with self.lock:
            if self.health.get('account_syncing'):
                return {'accepted': True, 'already_syncing': True}
            self.require_idle()
            if self.maintenance:
                raise ConflictError('服务正在维护，请稍后同步。')
            origin = copy.deepcopy(self.account_switch_origin or self.health['account'])
            self.account_switch_origin = None
            self.health.update(connected=False, checking=True, account_syncing=True, models=[], error='',
                               account_sync_result={'status': 'syncing', 'changed': None, 'detail': '正在同步当前账号与模型'},
                               account={'state': 'checking', 'email': None, 'name': None,
                                        'source': '官方 CLI statusLine', 'checked_at': None,
                                        'detail': '正在同步当前 CLI 账号与可用模型。'})
            self._touch()
            self.health_thread = threading.Thread(target=sync, daemon=True)
            self.health_thread.start()
            return {'accepted': True, 'already_syncing': False}

    def refresh_account(self):
        with self.lock:
            if self.stopping:
                raise ValueError('服务正在关闭。')
            if self.health.get('account_syncing'):
                return
            if (self.account_thread and self.account_thread.is_alive()) or self.health['account']['state'] == 'checking':
                return
            self.health['account'] = dict(self.health['account'], state='checking')
            self.account_thread = threading.Thread(target=self.check_account, daemon=True)
            self.account_thread.start()

    def refresh_health(self):
        def check():
            try:
                version = self.health_command(['--version'], 10)
                result = self.health_command(['models'], 35)
                models = []
                for line in result.stdout.splitlines():
                    parts = line.split('\t', 1)
                    if len(parts) == 2 and re.fullmatch(r'[a-zA-Z0-9_.-]+', parts[0]):
                        models.append({'id': parts[0], 'label': parts[1]})
                if result.returncode or not models:
                    raise RuntimeError(result.stderr.strip()[:500] or '未取得模型列表，请检查 CLI 登录与网络。')
                with self.lock:
                    self.health.update(connected=True, checking=False, version=version.stdout.strip(),
                                       models=models, error='')
                announce('Agy CLI 已就绪 · 版本 ' + version.stdout.strip() + '。等待网页任务。')
                account = self.check_account()
                if account['state'] == 'known':
                    announce('CLI 当前账号：' + account['email'])
            except Exception as exc:
                with self.lock:
                    self.health.update(connected=False, checking=False, error=str(exc)[:600])
                if not self.stopping:
                    announce('Agy CLI 连接待检查：' + str(exc)[:600])
        with self.lock:
            if self.stopping or self.maintenance:
                raise ValueError('服务正在关闭，请重新启动后再操作。')
            if self.health_thread and self.health_thread.is_alive():
                return
            self.health['checking'] = True
            self.health_thread = threading.Thread(target=check, daemon=True)
            self.health_thread.start()

    def submit(self, cid, prompt, model, kind='text', branch=None, branch_title=None, codex_dispatch=False, meeting_id=None, meeting_stage=None):
        if not isinstance(prompt, str) or not prompt.strip() or len(prompt.encode()) > 100000:
            raise ValueError('请填写任务；单次内容最多约 3 万汉字。')
        with self.lock:
            if self.health.get('account_syncing'):
                raise ConflictError('正在同步 CLI 账号与模型，请稍后提交任务。')
            if self.maintenance:
                raise ConflictError('正在处理备份，请稍后再提交任务。')
            if kind not in ('text', 'image'):
                raise ValueError('请选择文字任务或图片生成。')
            if self.stopping:
                raise ValueError('服务正在关闭，请重新启动后再操作。')
            conv = self.conversation(cid)
            if conv is None:
                raise ValueError('对话不存在。')
            linked_meeting = None
            if meeting_id is not None or meeting_stage is not None:
                if (not codex_dispatch or kind != 'text' or not isinstance(meeting_id, str)
                        or not re.fullmatch(r'[a-f0-9]{32}', meeting_id)
                        or meeting_stage not in ('proposal', 'review', 'summary')):
                    raise ValueError('圆桌任务需为明确阶段的 Codex 文字派发。')
                records = validate_meetings(conv)
                linked_meeting = next((m for m in records if m['id'] == meeting_id), None)
                if not linked_meeting or branch is not None and branch != linked_meeting['branch']:
                    raise ValueError('圆桌与当前工作会话不一致。')
                branch = linked_meeting['branch']
                current_view = meeting_view(conv, linked_meeting)
                slot = next((v for v in current_view['slots'] if v['provider'] == 'agy' and v['stage'] == meeting_stage), None)
                if not slot:
                    raise ValueError('该会议没有此 Agy 阶段。')
                if slot.get('turn_id'):
                    prior = next((t for t in conv['turns'] if t['id'] == slot['turn_id']), None)
                    if prior and prior['prompt'] == prompt.strip() and (not model or model == prior.get('requested_model')):
                        return copy.deepcopy(dict(prior, duplicate_submission=True))
                    raise ConflictError('此会议阶段已有任务，未重复派发。')
                if slot['status'] != 'pending':
                    raise ConflictError('此会议阶段已有发送记录，请先核对原结果。')
                if roundtable_policy(conv)['mode'] != 'roundtable':
                    raise ValueError('圆桌模式已关闭，未派发新阶段。')
            explicit_model = bool(model)
            preferred, config = model_selection_settings(self.policy_snapshot(), conv, kind)
            if model == '':
                if preferred and preferred in {m['id'] for m in self.health['models']}:
                    model = preferred  # Keep the saved preference; refresh quota again when dequeued.
                else:
                    resolution = self.model_resolution(conv)[kind]
                    if resolution['error']:
                        raise ValueError(resolution['error'])
                    model = resolution['effective_model']
            if not isinstance(model, str) or model not in {m['id'] for m in self.health['models']}:
                raise ValueError('请选择列表中的可用模型。')
            if type(codex_dispatch) is not bool:
                raise ValueError('派发来源无效。')
            if codex_dispatch and not collaboration_allowed(conv, 'agy'):
                raise ValueError('这条聊天已暂停多 AI 协作或关闭 Agy。请在固定页面确认；仍可在网页手动给 Agy 发任务。')
            meeting = None
            if codex_dispatch and linked_meeting is None:
                setting = roundtable_policy(conv)
                if setting['mode'] == 'roundtable':
                    plan = roundtable_plan(conv)
                    if not plan['ready']:
                        raise ValueError('圆桌当前不可派发：' + plan['reason'] + '。请检查主持和参与方设置。')
                    meeting = setting
            if linked_meeting is not None:
                frozen = linked_meeting['roundtable']
                if frozen['host'] != 'codex' and not collaboration_allowed(conv, frozen['host']):
                    raise ValueError('该会议主持已暂停，未派发新阶段。')
                meeting = copy.deepcopy(frozen)
            lane = branch_for(conv, branch, create=True, title=branch_title)
            if lane.get('restored_context_missing'):
                sid = lane.get('agy_id')
                if not sid or not (self.activity.root / 'conversations' / (sid + '.db')).is_file():
                    raise ValueError('已恢复面板记录，但本机缺少此官方会话上下文，暂不能续聊。请回原机器使用，或手动新建独立工作会话。')
                lane.pop('restored_context_missing', None)
            if not conv.get('codex_thread_id') and any(t['status'] in BUSY and t.get('branch') == lane['id'] for t in conv['turns']):
                raise ValueError('请等待当前任务结束，或先停止任务。')
            turn = {'id': uuid.uuid4().hex, 'prompt': prompt.strip(), 'model': model, 'kind': kind, 'branch': lane['id'], 'images': [], 'status': 'queued',
                    'response': '', 'events': [], 'created_at': now(), 'started_at': None, 'ended_at': None,
                    'elapsed': 0, 'error': '', 'usage': {}, 'source': 'codex' if codex_dispatch else 'console'}
            if meeting is not None:
                turn['roundtable'] = copy.deepcopy(meeting)
                turn['roundtable_role'] = 'host' if meeting['host'] == 'agy' else 'participant'
            elif codex_dispatch and any(t.get('branch') == lane['id']
                    and t.get('roundtable_role') in ('host', 'participant')
                    and isinstance(t.get('roundtable'), dict)
                    and t['roundtable'].get('mode') == 'roundtable' for t in conv['turns']):
                # Freeze the exit from a persistent meeting persona at submit.
                # Manual tasks and lanes that never used a meeting stay untouched.
                turn['roundtable_reset'] = True
            smart = not preferred
            turn.update(requested_model=model if explicit_model else preferred or model, model_selection='explicit' if explicit_model else 'window_or_global',
                        fallback_enabled=not explicit_model and config.get('enabled', True),
                        preflight_enabled=not explicit_model and (config.get('enabled', True) or smart))
            if (config.get('enabled', True) or kind in self.state.get('global_model_policy', {})) and 'models' in config:
                turn['fallback_models'] = list(config['models'])
            record_progress(turn, 'queued', '进入任务队列', 'done', turn['created_at'])
            if conv.get('codex_thread_id'):
                turn['expected_agy_id'] = lane.get('agy_id')
            if not conv['turns'] and not conv.get('custom_title'):
                conv['title'] = prompt.strip().splitlines()[0][:30]
            if linked_meeting is not None:
                # Freeze only completed prerequisite evidence once, under the
                # same queue lock. Quota fallback retains this exact snapshot.
                turn['meeting_context'] = meeting_context(conv, linked_meeting, meeting_stage, 'agy')
                turn.update(meeting_id=meeting_id, meeting_stage=meeting_stage)
                candidate_conv = dict(conv, turns=conv['turns'] + [turn])
                updated_meeting = link_meeting_turn(candidate_conv, meeting_id, meeting_stage, turn, turn['created_at'])
                previous_meetings = conv.get('roundtable_meetings')
                previous_updated = conv.get('updated_at')
                conv['roundtable_meetings'] = [updated_meeting if m['id'] == meeting_id else m for m in records]
            conv['turns'].append(turn)
            conv['updated_at'] = now()
            try:
                self.save(conv)
            except Exception:
                if linked_meeting is not None:
                    conv['turns'].remove(turn)
                    conv['roundtable_meetings'] = previous_meetings
                    conv['updated_at'] = previous_updated
                    try:
                        write_json(self.file, self.state)
                    except Exception:
                        pass
                    self._touch(conv)
                raise
            self.jobs.put((cid, turn['id']))
            return turn

    def recover_turn(self, cid, tid, strategy, model='', acknowledge=False):
        with self.lock:
            conv = self.conversation(cid)
            old = next((t for t in (conv or {}).get('turns', []) if t['id'] == tid), None)
            if not old or old['status'] not in ('failed', 'cancelled', 'interrupted'):
                raise ValueError('只能恢复失败、停止或中断的任务。')
            if acknowledge is not True:
                raise ValueError('请先核对上一轮结果，再确认恢复。')
            latest = next(t for t in reversed(conv['turns']) if t.get('branch') == old.get('branch'))
            if latest['id'] != tid:
                raise ConflictError('该工作会话已有后续任务。请查看最新结果，再在输入框继续，避免重复执行旧任务。')
            if any(t['status'] in BUSY and t.get('branch') == old.get('branch') for t in conv['turns']):
                raise ConflictError('该会话有任务未结束，请先等待或停止，避免重复执行。')
            turn = self.submit(cid, recovery_prompt(old, strategy), model, old.get('kind', 'text'), old.get('branch'))
            turn.update(action='recovery', recovery_of=tid, recovery_strategy=strategy)
            self.save(conv)
            return copy.deepcopy(turn)

    def compact_context(self, cid, branch, acknowledge=False):
        with self.lock:
            conv = self.conversation(cid)
            lane = branch_for(conv, branch) if conv else None
            if not lane or not lane.get('agy_id'):
                raise ValueError('当前工作会话尚无可整理的官方上下文。')
            if acknowledge is not True:
                raise ValueError('请确认整理当前会话；本操作会使用模型额度。')
            if any(t['status'] in BUSY and t.get('branch') == lane['id'] for t in conv['turns']):
                raise ConflictError('请等当前工作会话的任务结束后再整理上下文。')
            turn = self.submit(cid, '/compact 请保留当前目标、用户约束、关键结论、文件路径、已完成与未完成事项；不要编造遗漏信息。整理后简要列出保留的要点。', '', branch=lane['id'])
            turn['action'] = 'compact'
            self.save(conv)
            return copy.deepcopy(turn)

    def stop(self, tid):
        with self.lock:
            turn = next((t for c in self.state['conversations'] for t in c['turns'] if t['id'] == tid), None)
            if turn is None:
                raise ValueError('任务不存在。')
            if turn['status'] not in BUSY:
                return
            proc = self.processes.get(tid)
            if proc:
                turn.update(status='cancelling', error='正在停止任务，已收到的内容会保留。')
                self.terminate(proc)
            else:
                turn.update(status='cancelled', ended_at=now(), error='任务已由你停止。')
                finish_progress(turn)
                self.jobs.discard(tid)
            self.save(next(c for c in self.state['conversations'] if turn in c['turns']))

    @staticmethod
    def terminate(proc):
        if proc.poll() is not None:
            return
        try:
            os.killpg(proc.pid, signal.SIGTERM)
        except PermissionError:
            proc.terminate()
        except ProcessLookupError:
            return
        def force():
            try:
                proc.wait(timeout=3)
            except subprocess.TimeoutExpired:
                try:
                    os.killpg(proc.pid, signal.SIGKILL)
                except PermissionError:
                    proc.kill()
                except ProcessLookupError:
                    pass
        threading.Thread(target=force, daemon=True).start()

    def event(self, conv, turn, event, evidence=None):
        if evidence is not None:
            evidence.observe(event)
        with self.lock:
            changed = False
            if event.get('event') == 'init':
                record_progress(turn, 'cli', 'CLI 已接收调用', 'done')
                changed = True
                session = event.get('conversation_id') or event.get('init', {}).get('conversation_id')
                if session:
                    assign_session(self.state['conversations'], conv, turn, session)
                    changed = True
            elif event.get('event') == 'step_update':
                step = event.get('step_update', {})
                if step.get('step_type'):
                    step_progress(turn, step)
                    changed = True
                delta = step.get('text_delta')
                if isinstance(delta, str) and delta:
                    turn['response'] += delta
                    changed = True
                kind = step.get('step_type')
                if kind and not delta:
                    label = {'user_input': '已接收任务', 'agent_response': '正在组织回复',
                             'checkpoint': '正在保存会话', 'tool': '正在使用工具'}.get(kind, '正在处理')
                    if step.get('tool_name'):
                        label += ' · ' + str(step['tool_name'])
                    if not turn['events'] or turn['events'][-1] != label:
                        turn['events'] = (turn['events'] + [label])[-12:]
                        changed = True
            elif event.get('event') == 'result':
                changed = True
                record_progress(turn, 'result', 'CLI 已返回结果，核验中', 'done')
                result = event.get('result', {})
                turn['cli_result'] = result
                if isinstance(result.get('response'), str):
                    turn['response'] = result['response']
                turn['usage'] = result.get('usage') or {}
                if result.get('conversation_id'):
                    assign_session(self.state['conversations'], conv, turn, result['conversation_id'])
            if changed:
                if event.get('event') in {'init', 'result'} or time.monotonic() - self.last_stream_save >= 1:
                    self.save(conv)
                    self.last_stream_save = time.monotonic()
                else:
                    self._touch(conv)

    def worker(self):
        retry_item = None
        while not self.stopping:
            with self.lock:
                item = retry_item if retry_item is not None else self.jobs.get()
                retry_item = None
                if item is None or self.stopping:
                    return
                cid, tid = item
                conv = self.conversation(cid)
                if conv is None:
                    continue
                turn = next(t for t in conv['turns'] if t['id'] == tid)
                if turn['status'] != 'queued':
                    continue
                turn.update(status='running')
                if not turn.get('started_at'):
                    turn['started_at'] = now()
                record_progress(turn, 'start', '开始执行', 'done', turn['started_at'])
                folder = self.data / 'runs' / tid
                folder.mkdir(parents=True, exist_ok=True)
                lane = branch_for(conv, turn.get('branch'), create=True)
                workspace = self.data / 'workspaces' / cid
                if lane['id'] != 'main':
                    workspace = self.data / 'session-workspaces' / cid / lane['id']
                workspace.mkdir(parents=True, exist_ok=True)
                previous_images = image_snapshot([workspace, self.artifact_directory({'agy_id': lane.get('agy_id')})])
                (folder / 'request.txt').write_text(turn['prompt'], encoding='utf-8')
                cli_prompt = turn['prompt'] if not turn.get('attempts') else (
                    '上一轮因模型额度拒绝而未取得执行结果。请继续本会话上一条用户任务尚未完成的部分。'
                    '先核对已有记录，不重复已完成的操作；保留原任务所有约束。')
                if turn.get('roundtable') is not None:
                    cli_prompt = roundtable_prompt(cli_prompt, turn['roundtable'], conv.get('codex_thread_id'))
                    if turn.get('meeting_context'):
                        cli_prompt += '\n\n' + turn['meeting_context']['text']
                elif turn.get('roundtable_reset') is True:
                    cli_prompt = ('[日常协助 · 非圆桌] 本次为普通有界协助，不担任主持，也不继续此前的圆桌角色。'
                                  '只完成本次实际收到的任务；不组织额外讨论、不编造其他 AI 发言或共识。'
                                  'Codex 负责实际执行和独立核验。\n[本次任务]\n' + cli_prompt)
                image_task = turn.get('kind') == 'image'
                if image_task:
                    cli_prompt += ('\n\n[用户已选择图片生成] 请实际使用内置 image-generator 子代理或 generate_image '
                                   '工具生成图片，允许为本次出图调用该内置助手。不要用代码绘图替代。'
                                   '仅进行图片任务，不浏览网页、不读取凭据、不修改其他用户项目。'
                                   '生成文件保存到当前工作目录，或保留在本次官方会话的 brain 产物目录。'
                                   '最终用 Markdown 图片引用实际绝对路径。若生成失败，明确说明，切勿假装成功。')
                if turn.get('roundtable') is not None or turn.get('roundtable_reset') is True:
                    turn['worker_prompt'] = cli_prompt
                    (folder / 'request.txt').write_text(cli_prompt, encoding='utf-8')
                command = [self.binary, '-p', cli_prompt, '--output-format', 'stream-json',
                           '--model', turn['model'], '--mode', 'accept-edits' if image_task else 'plan', '--sandbox',
                           '--print-timeout', str(self.timeout) + 's']
                turn['expected_agy_id'] = lane.get('agy_id')
                if lane.get('agy_id'):
                    command += ['--conversation', lane['agy_id']]
                self.save(conv)
                announce('\n▶ 正在调用 Agy CLI · ' + turn['model'] + '\n  任务：' + conv['title'])
            started, started_wall, proc, stderr, selector = time.monotonic(), time.time(), None, bytearray(), None
            stderr_denied = False
            evidence = TurnEvidence()
            guard = AttemptGuard()
            attempt_started_at = now()
            terminal_status, terminal_error = None, None
            try:
                if lane.get('adopted'):
                    source = next((s for s in self.activity_snapshot(force=True)['sessions'] if s['id'] == lane['agy_id']), None)
                    if source is None or source.get('active') or source.get('status') == 'unknown':
                        raise ValueError('原会话可能仍在其他窗口运行，本次未并发写入，请等待原窗口退出。')
                with self.lock:
                    turn['events'].append('正在核对 CLI 当前账号')
                    record_progress(turn, 'account', '核对 CLI 当前账号')
                account = self.check_account()
                with self.lock:
                    turn['account_check'] = copy.deepcopy(account)
                    record_progress(turn, 'account', 'CLI 账号检查已结束', 'done')
                    if account['state'] == 'known':
                        turn['account'] = {k: account.get(k) for k in ('email', 'name', 'source', 'checked_at')}
                    if turn.get('preflight_enabled') and not turn.get('attempts'):
                        self.record_model_change(turn, self.choose_turn_model(turn), '执行前额度检查')
                    if turn.get('attempts') and turn.get('retry_account') != account.get('email'):
                        raise ValueError('自动恢复前账号已改变，未再次调用模型；请核对账号后手动继续。')
                    command[command.index('--model') + 1] = turn['model']
                    self.save(conv)
                with self.lock:
                    if turn['status'] != 'running':
                        continue
                    proc = subprocess.Popen(command, cwd=workspace, stdout=subprocess.PIPE,
                                            stderr=subprocess.PIPE, start_new_session=True)
                    self.processes[tid] = proc
                selector, pending = selectors.DefaultSelector(), bytearray()
                selector.register(proc.stdout, selectors.EVENT_READ, 'out')
                selector.register(proc.stderr, selectors.EVENT_READ, 'err')
                with (folder / 'stdout.ndjson').open('wb') as raw, (folder / 'stderr.log').open('wb') as err_raw:
                    while selector.get_map():
                        if time.monotonic() - started > self.timeout + 10:
                            self.terminate(proc)
                            raise TimeoutError('等待回复超时，本次任务未确认完成。')
                        for key, _ in selector.select(timeout=.3):
                            chunk = os.read(key.fileobj.fileno(), 65536)
                            if not chunk:
                                selector.unregister(key.fileobj)
                                continue
                            if key.data == 'err':
                                err_raw.write(chunk)
                                err_raw.flush()
                                diagnostic_chunk = (stderr[-128:] + chunk).decode('utf-8', errors='replace')
                                stderr_denied = stderr_denied or bool(re.search(r'soft.denied|permission denied|tool.{0,80}\bdenied\b', diagnostic_chunk, re.I))
                                stderr.extend(chunk)
                                if len(stderr) > 65536:
                                    del stderr[:-65536]
                                continue
                            raw.write(chunk)
                            raw.flush()
                            pending.extend(chunk)
                            while b'\n' in pending:
                                line, _, remaining = pending.partition(b'\n')
                                pending = bytearray(remaining)
                                try:
                                    event = json.loads(line)
                                    guard.observe(event)
                                    self.event(conv, turn, event, evidence)
                                except (ValueError, TypeError, AttributeError):
                                    guard.unsafe = True
                    if pending.strip():
                        try:
                            event = json.loads(pending)
                            guard.observe(event)
                            self.event(conv, turn, event, evidence)
                        except (ValueError, TypeError, AttributeError):
                            guard.unsafe = True
                selector.close()
                code = proc.wait(timeout=5)
                with self.lock:
                    result = turn.pop('cli_result', {})
                    current_usage = evidence.current_usage()
                    if current_usage is not None:
                        turn['cli_usage'] = copy.deepcopy(result.get('usage') or {})
                        turn['usage'] = current_usage
                        turn['usage_scope'] = 'turn'
                    elif type(result.get('num_turns')) is int and result['num_turns'] > 1:
                        turn['usage_scope'] = 'conversation'
                    self.gather_images(conv, turn, workspace, folder, started_wall, previous_images)
                    diagnostic = stderr.decode('utf-8', errors='replace')
                    denial = stderr_denied
                    if turn['status'] not in {'cancelled', 'cancelling'}:
                        confirmation = None
                        denied_proof = (evidence.prove_denied_historical_error(result, root=self.activity.root)
                                        if code in (0,1,3) and quota_rejection(result.get('error'))
                                        and not turn.get('session_mismatch') else None)
                        if denied_proof:
                            turn['failure_evidence'] = denied_proof
                        if code == 0 and not denial and not turn.get('session_mismatch'):
                            confirmation = evidence.prove_historical_error(result, root=self.activity.root,
                                                                            time_window=(started_wall, time.time()))
                        if confirmation:
                            turn['completion_evidence'] = confirmation
                        success = (code == 0 and (result.get('status') == 'SUCCESS' or confirmation is not None)
                                   and bool(turn['response'].strip() or turn.get('images'))
                                   and (turn.get('kind') != 'image' or bool(turn.get('images')))
                                   and not result.get('denied_actions') and not denial and not turn.get('session_mismatch'))
                        terminal_status = 'completed' if success else 'failed'
                        if re.match(r'^\s*/compact(?:\s|$)', turn['prompt']):
                            turn['compaction_evidence'] = evidence.compaction_checkpoint(result.get('conversation_id')) if success else None
                        terminal_error = '' if success else str(('Agy 返回了不同的会话，本次未确认续接；原绑定已保留。' if turn.get('session_mismatch') else '') or result.get('error') or diagnostic[-1200:] or
                                                              ('未取得实际图片文件，请查看回复中的生成失败原因。' if image_task else '未取得完整的成功回复。'))
                        if denied_proof:
                            terminal_error='工具权限被拒绝：'+', '.join(denied_proof['denied_tools'])+'。本轮未完成；CLI 附带的旧额度错误已隔离。'
                            turn['fallback_notice']='本轮工具被拒绝，未自动切换或重跑；请先检查所需权限。'
                    turn['exit_code'] = code
            except Exception as exc:
                if proc and proc.poll() is None:
                    self.terminate(proc)
                with self.lock:
                    if turn['status'] not in {'cancelled', 'cancelling'}:
                        terminal_status, terminal_error = 'failed', str(exc)[:1200]
            finally:
                if selector:
                    selector.close()
                if proc:
                    if proc.poll() is None:
                        self.terminate(proc)
                    try:
                        proc.wait(timeout=4)
                    except subprocess.TimeoutExpired:
                        pass
                    proc.stdout.close()
                    proc.stderr.close()
                bark_payload = None
                with self.lock:
                    self.processes.pop(tid, None)
                    if not turn.get('images'):
                        self.gather_images(conv, turn, workspace, folder, started_wall, previous_images)
                    if turn['status'] == 'cancelling':
                        turn.update(status='cancelled', error='任务已由你停止。')
                    elif terminal_status is not None and turn['status'] != 'cancelled':
                        turn.update(status=terminal_status, error=terminal_error)
                    turn.pop('cli_result', None)
                    attempt_elapsed = round(time.monotonic() - started, 2)
                    turn.update(ended_at=now(), elapsed=attempt_elapsed)
                    if not (folder / 'stderr.log').exists():
                        (folder / 'stderr.log').write_bytes(stderr)
                    (folder / 'response.md').write_text(turn['response'], encoding='utf-8')
                    if proc is not None:
                        turn.setdefault('attempts', []).append({
                            'model': turn['model'], 'status': turn['status'], 'error': turn.get('error', '')[:1200],
                            'started_at': attempt_started_at, 'ended_at': turn['ended_at'], 'elapsed': attempt_elapsed,
                            'usage': copy.deepcopy(turn.get('usage') or {}), 'usage_scope': turn.get('usage_scope', 'unknown')})
                    turn['elapsed'] = round(sum(a['elapsed'] for a in turn.get('attempts', [])), 2) if turn.get('attempts') else attempt_elapsed
                    quota_turn = dict(turn, prompt=turn.get('worker_prompt', turn['prompt']))
                    current_quota = (turn['status'] == 'failed' and quota_rejection(turn.get('error'))
                                     and guard.current_quota_rejection(quota_turn, self.activity.root))
                    if current_quota:
                        self.mark_quota_rejection(turn['model'], turn['error'], turn.get('account_check'))
                    account_now = self.health.get('account') or {}
                    same_account = (account_now.get('state') == 'known' and account_now.get('email')
                                    and account_now.get('email') == (turn.get('account_check') or {}).get('email'))
                    eligible = (not self.stopping and current_quota and turn['status'] == 'failed' and turn.get('fallback_enabled')
                                and len(turn.get('attempts', [])) == 1 and same_account
                                and guard.permits_retry(turn, turn.get('exit_code'), stderr_denied)
                                and guard.official_rejection(quota_turn, self.activity.root))
                    if eligible:
                        try:
                            next_model = self.choose_turn_model(turn, retry=True)
                        except ValueError as error:
                            turn['fallback_notice'] = str(error)
                        else:
                            write_json(folder / 'attempt-1-result.json', turn)
                            for name in ('stdout.ndjson', 'stderr.log', 'response.md'):
                                path = folder / name
                                if path.exists():
                                    path.replace(folder / ('attempt-1-' + name))
                            self.record_model_change(turn, next_model, '额度拒绝，安全恢复一次')
                            record_progress(turn, 'retry', '原会话继续 · 最多自动恢复一次', 'done')
                            turn.update(status='queued', response='', error='', ended_at=None, usage={}, retry_account=account_now['email'])
                            turn.pop('usage_scope', None)
                            turn.pop('exit_code', None)
                            retry_item = (cid, tid)
                    elif turn['status'] == 'failed' and quota_rejection(turn.get('error')):
                        if not current_quota:
                            notice = '未确认是本轮额度拒绝，未将当前模型标为耗尽。已保留结果，请核对本轮回复与工具状态。'
                        elif not turn.get('fallback_enabled'):
                            notice = ('本次请求明确指定模型，未启用自动候补。' if turn.get('model_selection') == 'explicit' else
                                      '本窗口已关闭额度用完自动候补。')
                        elif len(turn.get('attempts', [])) >= 2:
                            notice = '已自动候补一次，仍未完成；达到本轮自动恢复上限，结果已保留。'
                        elif not same_account:
                            notice = '无法确认仍是原 CLI 账号，未自动续接；请核对账号后恢复任务。'
                        elif turn.get('response') or turn.get('images') or guard.unsafe:
                            notice = '本轮已有内容或工具操作，未自动重跑以免重复执行。请核对结果后使用“恢复任务”继续。'
                        else:
                            notice = '未取得原会话安全续接的完整证据，未自动重跑；请核对结果后恢复任务。'
                        turn['fallback_notice'] = notice
                    if retry_item is None:
                        finish_progress(turn)
                        attempts = turn.get('attempts', [])
                        if len(attempts) > 1 and all(a['usage_scope'] == 'turn' for a in attempts):
                            turn['usage'] = {k: sum(a['usage'].get(k, 0) for a in attempts) for k in
                                             ('input_tokens', 'output_tokens', 'thinking_tokens', 'cache_read_tokens', 'total_tokens')}
                            turn['usage_scope'] = 'turn'
                    conv['updated_at'] = now()
                    write_json(folder / 'result.json', turn)
                    self.save(conv)
                    if retry_item is None:
                        bark_payload = bark_notification_payload(turn['status'], conv['id'], turn['id'], conv['title'])
                    label = {'completed': '任务完成', 'cancelled': '任务已停止', 'failed': '任务未完成'}.get(turn['status'], turn['status'])
                    announce('■ ' + label + ' · ' + str(turn['elapsed']) + ' 秒')
                    if not self.stopping:
                        pending_count = sum(t['status'] in BUSY for c in self.state['conversations'] for t in c['turns'])
                        announce('等待处理剩余 ' + str(pending_count) + ' 项任务。' if pending_count else '待命中 · 没有模型任务在运行。')
                if bark_payload is not None and self.notification_sender is not None:
                    try:
                        self.notification_sender(bark_payload)
                    except Exception:
                        # Also isolate future adapters from the already-saved result.
                        pass

    @staticmethod
    def artifact_directory(conv):
        session = conv.get('agy_id') or ''
        if re.fullmatch(r'[a-f0-9]{8}(?:-[a-f0-9]{4}){3}-[a-f0-9]{12}', session):
            return Path.home() / '.gemini/antigravity-cli/brain' / session
        return None

    def gather_images(self, conv, turn, workspace, folder, started_at, previous_files):
        lane = branch_for(conv, turn.get('branch'))
        context = {'agy_id': lane.get('agy_id') if lane is not None else conv.get('agy_id')}
        turn['images'] = collect_images(workspace, self.artifact_directory(context), folder / 'images', turn['id'],
                                        turn['response'], started_at, previous_files)

    def close(self):
        with self.close_lock:
            if self.closed:
                return
            self._close()
            self.closed = True

    def _close(self):
        if hasattr(self,'software_updates'):self.software_updates.close()
        with self.lock:
            self.stopping = True
            for conv in self.state['conversations']:
                for turn in conv['turns']:
                    if turn['status'] == 'queued':
                        turn.update(status='cancelled', error='服务已关闭，此任务未执行。', ended_at=now())
                        finish_progress(turn)
                    elif turn['status'] == 'running':
                        turn.update(status='cancelling', error='服务正在关闭。')
            for proc in list(self.processes.values()) + list(self.health_processes.values()):
                self.terminate(proc)
            try:
                self.save()
            except (OSError, ValueError) as error:
                # Broken metadata or disk failures must not prevent foreground
                # teardown or leave the worker thread waiting forever.
                announce('关闭时保存未完成：' + str(error)[:160])
        self.jobs.put(None)
        if threading.current_thread() is not self.worker_thread:
            self.worker_thread.join(timeout=5)
        if self.health_thread and threading.current_thread() is not self.health_thread:
            self.health_thread.join(timeout=5)
        if self.account_thread and threading.current_thread() is not self.account_thread:
            self.account_thread.join(timeout=5)


class Handler(BaseHTTPRequestHandler):
    timeout = 10
    def respond(self, status, body, kind='application/json; charset=utf-8', extra=None):
        if isinstance(body, (dict, list)):
            body = json.dumps(body, ensure_ascii=False).encode()
        elif isinstance(body, str):
            body = body.encode()
        self.send_response(status)
        self.send_header('Content-Type', kind)
        self.send_header('Content-Length', str(len(body)))
        self.send_header('Cache-Control', 'no-store')
        self.send_header('X-Content-Type-Options', 'nosniff')
        self.send_header('Content-Security-Policy', "default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' data:; connect-src 'self'; object-src 'none'; base-uri 'none'; frame-ancestors 'none'")
        for key, value in (extra or {}).items():
            self.send_header(key, value)
        self.end_headers()
        try:
            self.wfile.write(body)
        except (BrokenPipeError, ConnectionResetError):
            pass

    def trusted_host(self):
        port = self.server.server_port
        return self.headers.get('Host') in {f'127.0.0.1:{port}', f'localhost:{port}'}

    def do_GET(self):
        if not self.trusted_host():
            return self.respond(403, {'error': 'Host not allowed'})
        app, path = self.server.app, urlsplit(self.path).path
        query = parse_qs(urlsplit(self.path).query)
        if path == '/api/state':
            summaries = query.get('summary') == ['1']
            tag = app.state_etag()
            if summaries:
                tag = tag[:-1] + '-summary"'
            if self.headers.get('If-None-Match') == tag:
                return self.respond(304, b'', extra={'ETag': tag})
            return self.respond(200, app.snapshot(summaries=summaries), extra={'ETag': tag})
        if path == '/api/service':
            return self.respond(200, app.service_info())
        if path == '/api/console-startup':
            return self.respond(200, app.console_startup_info())
        match = re.fullmatch(r'/api/codex/([a-f0-9]{8}(?:-[a-f0-9]{4}){3}-[a-f0-9]{12})/collaboration', path)
        if match:
            try:
                return self.respond(200, app.multi_ai_policy(match[1]))
            except KeyError:
                return self.respond(404, {'error': '本聊天尚未绑定。'})
            except ValueError as error:
                return self.respond(409, {'error': str(error)})
        match = re.fullmatch(r'/api/codex/([a-f0-9]{8}(?:-[a-f0-9]{4}){3}-[a-f0-9]{12})/roundtable', path)
        if match:
            try:
                return self.respond(200, app.roundtable_policy(match[1]))
            except KeyError:
                return self.respond(404, {'error': '本聊天尚未绑定。'})
            except ValueError as error:
                return self.respond(409, {'error': str(error)})
        if path == '/api/updates':
            return self.respond(200, app.software_updates.snapshot())
        match=re.fullmatch(r'/api/configuration/backups/([a-f0-9]{32})',path)
        if match:
            file=app.data/'configuration-backups'/(match[1]+'.json')
            try:
                if file.is_symlink() or not file.is_file() or file.stat().st_size>65536:raise ValueError('配置备份不可用。')
                value=validate_configuration(json.loads(file.read_text(encoding='utf-8')))
                return self.respond(200,value,extra={'Content-Disposition':'attachment; filename="agy-settings-before-import.json"'})
            except (OSError,ValueError):return self.respond(404,{'error':'配置备份不可用。'})
        if path == '/api/queue':
            return self.respond(200, app.queue_snapshot())
        if path == '/api/resources':
            return self.respond(200, app.resource_snapshot(force=query.get('refresh') == ['1'], summary=query.get('summary') == ['1']))
        if path == '/api/search':
            try:
                cid = query.get('conversation', [None])[0]
                if cid is not None and not re.fullmatch(r'[a-f0-9]{32}', cid):
                    raise ValueError('聊天标识无效。')
                with app.lock:
                    result = search_history(app.state, query.get('q', [''])[0], cid, int(query.get('limit', ['30'])[0]))
                return self.respond(200, result)
            except ValueError as error:
                return self.respond(400, {'error': str(error)})
        match = re.fullmatch(r'/api/backups/([a-f0-9]{32})/download', path)
        if match:
            file = app.data / 'backups' / (match[1] + '.zip')
            try:
                if file.is_symlink() or not file.is_file() or file.stat().st_size > MAX_UPLOAD:
                    raise ValueError('备份不可用。')
                with file.open('rb') as stream:
                    self.send_response(200)
                    self.send_header('Content-Type', 'application/zip')
                    self.send_header('Content-Length', str(os.fstat(stream.fileno()).st_size))
                    self.send_header('Content-Disposition', 'attachment; filename="agy-panel-backup-' + match[1][:8] + '.zip"')
                    self.send_header('Cache-Control', 'no-store')
                    self.send_header('X-Content-Type-Options', 'nosniff')
                    self.end_headers()
                    while True:
                        chunk = stream.read(128 * 1024)
                        if not chunk:
                            break
                        self.wfile.write(chunk)
                return
            except (BrokenPipeError, ConnectionResetError):
                return
            except (OSError, ValueError):
                return self.respond(404, {'error': '备份文件不可用。'})
        match = re.fullmatch(r'/api/conversations/([a-f0-9]{32})/meetings/([a-f0-9]{32})/workspace', path)
        if match:
            try:
                if query:
                    raise ValueError('材料查看无需附加参数。')
                return self.respond(200, app.meeting_workspace(match[1], match[2]))
            except ValueError as exc:
                return self.respond(400, {'error': str(exc)})
            except KeyError as exc:
                return self.respond(404, {'error': str(exc)})
        match = re.fullmatch(r'/api/conversations/([a-f0-9]{32})/meetings/([a-f0-9]{32})/context', path)
        if match:
            try:
                if set(query) != {'stage', 'provider'} or any(len(value) != 1 for value in query.values()):
                    raise ValueError('请选择一个会议阶段和一个冻结参与方。')
                return self.respond(200, app.meeting_context(match[1], match[2], query['stage'][0], query['provider'][0]))
            except ValueError as exc:
                return self.respond(400, {'error': str(exc)})
            except KeyError as exc:
                return self.respond(404, {'error': str(exc)})
        match = re.fullmatch(r'/api/conversations/([a-f0-9]{32})/meetings(?:/([a-f0-9]{32}))?', path)
        if match:
            try:
                return self.respond(200, app.meeting_records(match[1], match[2], query.get('branch', [None])[0],
                                                           query.get('before', [None])[0], int(query.get('limit', ['10'])[0]),
                                                           query['ids'][0].split(',') if 'ids' in query else None))
            except ValueError as exc:
                return self.respond(400, {'error': str(exc)})
            except KeyError as exc:
                return self.respond(404, {'error': str(exc)})
        match = re.fullmatch(r'/api/conversations/([a-f0-9]{32})/context', path)
        if match:
            try:
                return self.respond(200, app.context_snapshot(match[1], query.get('branch', [None])[0]))
            except ValueError as exc:
                return self.respond(400, {'error': str(exc)})
            except KeyError as exc:
                return self.respond(404, {'error': str(exc)})
        match = re.fullmatch(r'/api/conversations/([a-f0-9]{32})', path)
        if match:
            if 'limit' in query:
                try:
                    return self.respond(200, app.conversation_page(match[1], int(query['limit'][0]),
                                                                 query.get('before', [None])[0], query.get('after', [None])[0], query.get('branch', [None])[0], query.get('source', [None])[0], query.get('around', [None])[0]))
                except ValueError as exc:
                    return self.respond(400, {'error': str(exc)})
                except KeyError as exc:
                    return self.respond(404, {'error': str(exc)})
            with app.lock:
                conv = app.conversation(match[1])
                return self.respond(200, app.conversation_metadata(conv)) if conv else self.respond(404, {'error': '对话不存在。'})
        match = re.fullmatch(r'/api/conversations/([a-f0-9]{32})/turns/([a-f0-9]{32})', path)
        if match:
            with app.lock:
                conv = app.conversation(match[1])
                turn = next((t for t in (conv or {}).get('turns', []) if t['id'] == match[2]), None)
                if not turn:
                    return self.respond(404, {'error': '任务不存在。'})
                lane = branch_for(conv, turn.get('branch'))
                return self.respond(200, copy.deepcopy({'id': conv['id'], 'agy_id': turn.get('agy_id') or (lane or {}).get('agy_id'),
                                                       'codex_thread_id': conv.get('codex_thread_id'), 'turn': turn}))
        if path == '/api/activity':
            return self.respond(200, app.activity_snapshot())
        match = re.fullmatch(r'/api/activity/sessions/([a-f0-9-]{36})', path)
        if match:
            app.activity_snapshot()
            try:
                return self.respond(200, app.activity.detail(match[1]))
            except (KeyError, ValueError):
                return self.respond(404, {'error': '未找到该 CLI 会话。'})
        match = re.fullmatch(r'/api/turns/([a-f0-9]{32})/images/([a-f0-9]{64})', path)
        if match:
            with app.lock:
                turn = next((t for c in app.state['conversations'] for t in c['turns'] if t['id'] == match[1]), None)
                image = next((i for i in (turn or {}).get('images', []) if i['id'] == match[2]), None)
            if image is None:
                return self.respond(404, {'error': '图片不存在'})
            try:
                file = manifest_file(app.data / 'runs' / match[1] / 'images', image)
                with file.open('rb') as stream:
                    size = os.fstat(stream.fileno()).st_size
                    self.send_response(200)
                    self.send_header('Content-Type', image['mime'])
                    self.send_header('Content-Length', str(size))
                    self.send_header('X-Content-Type-Options', 'nosniff')
                    self.send_header('Cache-Control', 'private, max-age=3600, immutable')
                    self.send_header('Content-Security-Policy', "default-src 'none'; frame-ancestors 'none'")
                    download = urlsplit(self.path).query == 'download=1'
                    disposition = ('attachment' if download else 'inline') + '; filename="' + image['file'] + '"'
                    disposition += "; filename*=UTF-8''" + quote(image['name'], safe='')
                    self.send_header('Content-Disposition', disposition)
                    self.end_headers()
                    while True:
                        chunk = stream.read(128 * 1024)
                        if not chunk:
                            break
                        self.wfile.write(chunk)
                return
            except (BrokenPipeError, ConnectionResetError):
                return
            except (OSError, ValueError):
                return self.respond(404, {'error': '图片文件不可用'})
        match = re.fullmatch(r'/api/conversations/([a-f0-9]{32})/export', path)
        if match:
            with app.lock:
                conv = copy.deepcopy(app.conversation(match[1]))
            if conv is None:
                return self.respond(404, {'error': '对话不存在'})
            try:
                # Scope is explicit and checked against this detached snapshot;
                # GET never binds sessions, changes preferences or calls a model.
                export_query = parse_qs(urlsplit(self.path).query, keep_blank_values=True)
                if any(key not in ('branch', 'meeting', 'codex') or len(value) != 1 or not value[0]
                       for key, value in export_query.items()):
                    raise ValueError('导出范围参数无效。')
                body, filename = export_markdown(conv, branch=export_query.get('branch', [None])[0],
                    meeting_id=export_query.get('meeting', [None])[0], codex_thread_id=export_query.get('codex', [None])[0],
                    origin='http://127.0.0.1:' + str(self.server.server_port))
            except KeyError as error:
                return self.respond(404, {'error': str(error).strip("'")})
            except ValueError as error:
                return self.respond(400, {'error': str(error)})
            return self.respond(200, body, 'text/markdown; charset=utf-8',
                                {'Content-Disposition': transcript_attachment(filename, conv['id'])})
        assets = {'/': ('index.html', 'text/html; charset=utf-8'),
                  '/app.js': ('app.js', 'text/javascript; charset=utf-8'),
                  '/panel_actions.js': ('panel_actions.js', 'text/javascript; charset=utf-8'),
                  '/style.css': ('style.css', 'text/css; charset=utf-8'),
                  '/icon.svg': ('icon.svg', 'image/svg+xml')}
        if path not in assets:
            return self.respond(404, {'error': 'Not found'})
        name, kind = assets[path]
        body = (ROOT / name).read_text(encoding='utf-8')
        if name == 'index.html':
            body = body.replace('__CONSOLE_TOKEN__', app.token)
        self.respond(200, body, kind)

    def do_POST(self):
        app = self.server.app
        origin = self.headers.get('Origin')
        expected = {f'http://127.0.0.1:{self.server.server_port}', f'http://localhost:{self.server.server_port}'}
        if not self.trusted_host() or (origin is not None and origin not in expected):
            return self.respond(403, {'error': '来源不允许'})
        if not hmac.compare_digest(self.headers.get('X-Console-Token', ''), app.token):
            return self.respond(403, {'error': '页面已失效，请刷新后重试。'})
        try:
            path = urlsplit(self.path).path
            length = int(self.headers.get('Content-Length', '0'))
            if path == '/api/backups/preview':
                if not 1 <= length <= MAX_UPLOAD or self.headers.get('Content-Type') != 'application/zip':
                    raise ValueError('请选择64 MB以内的面板ZIP备份。')
                with app.panel_maintenance():
                    with tempfile.NamedTemporaryFile(prefix='.agy-upload-', dir=app.data.parent) as incoming:
                        remaining = length; deadline = time.monotonic() + 60
                        while remaining:
                            if time.monotonic() > deadline:
                                raise ValueError('上传超时，请重新选择文件。')
                            chunk = self.rfile.read(min(128 * 1024, remaining))
                            if not chunk:
                                raise ValueError('上传文件不完整。')
                            incoming.write(chunk); remaining -= len(chunk)
                        incoming.flush()
                        result = app.backups.preview(incoming.name)
                return self.respond(200, result)
            if length < 0 or length > 150000 or not self.headers.get('Content-Type', '').startswith('application/json'):
                raise ValueError('请求格式不正确或内容过长。')
            body = json.loads(self.rfile.read(length) or '{}')
            if not isinstance(body, dict):
                raise ValueError('请求必须是一个对象。')
            if path == '/api/backups/export':
                return self.respond(201, app.export_backup(body.get('ui')))
            if path == '/api/backups/restore':
                return self.respond(200, app.restore_backup(body.get('id'), body.get('ui'), body.get('acknowledge')))
            with app.lock:
                if app.stopping or app.maintenance:
                    raise ConflictError('服务正在重载或关闭，请稍后刷新。')
                status, payload = self.post_action(path, body)
            try:
                self.respond(status, payload)
                self.wfile.flush()
            finally:
                if path == '/api/service/reload' and status == 202:
                    app.reload_callback()
        except ConflictError as exc:
            return self.respond(409, {'error': str(exc)})
        except (ValueError, KeyError, OSError, subprocess.SubprocessError) as exc:
            return self.respond(400, {'error': str(exc)})

    def post_action(self, path, body):
        app = self.server.app
        if path == '/api/configuration/export':
            return 200, app.export_configuration(body.get('ui'),body.get('conversation_id'))
        if path == '/api/configuration/preview':
            return 200, app.preview_configuration(body.get('document'),body.get('conversation_id'),body.get('apply_window',False))
        if path == '/api/configuration/import':
            return 200, app.import_configuration(body.get('document'),body.get('ui'),body.get('conversation_id'),
                body.get('apply_window',False),body.get('revision'),body.get('acknowledge'))
        if path == '/api/updates/preferences':
            return 200, app.software_updates.set_preferences(body)
        if path == '/api/console-startup':
            return 200, app.set_console_startup(body)
        if path == '/api/updates/action':
            return 202, app.software_updates.request(body.get('action'))
        if path == '/api/conversations':
            return 201, copy.deepcopy(app.create(body.get('title', '新任务')))
        if path == '/api/codex/bind':
            return 200, app.bind_codex(body.get('thread_id'), body.get('title'), body.get('agy_id'), body.get('initial_turn'), body.get('initial_turns'))
        if path == '/api/history':
            return 200, app.manage_history(body.get('source'), body.get('id'), body.get('action'), body.get('title'))
        if path == '/api/model-order':
            return 200, app.set_model_order(body.get('order'), body.get('version'), body.get('if_unset', False))
        if path == '/api/model':
            return 200, app.set_preferred_model(body.get('model'))
        if path == '/api/global-model-policy':
            return 200, app.set_global_model_policy(body.get('kind'), body.get('settings'), body.get('version'))
        if path == '/api/queue/reorder':
            return 200, app.reorder_queue(body.get('turn_id'), body.get('direction'), body.get('version'))
        if path == '/api/service/reload':
            return 202, app.prepare_reload()
        if path == '/api/account/switch':
            return 200, app.open_account_switcher()
        if path == '/api/account/sync':
            if body:
                raise ValueError('账号同步无需账号、凭据或其他参数。')
            return 202, app.sync_account()
        match = re.fullmatch(r'/api/conversations/([a-f0-9]{32})/fallback', path)
        if match:
            return 200, app.set_fallback(match[1], body.get('kind'), body.get('settings'))
        match = re.fullmatch(r'/api/conversations/([a-f0-9]{32})/model', path)
        if match:
            return 200, app.set_window_model(match[1], body.get('kind'), body.get('model'))
        match = re.fullmatch(r'/api/conversations/([a-f0-9]{32})/turns/([a-f0-9]{32})/recover', path)
        if match:
            return 202, app.recover_turn(match[1], match[2], body.get('strategy'), body.get('model', ''), body.get('acknowledge'))
        match = re.fullmatch(r'/api/conversations/([a-f0-9]{32})/compact', path)
        if match:
            return 202, app.compact_context(match[1], body.get('branch'), body.get('acknowledge'))
        match = re.fullmatch(r'/api/conversations/([a-f0-9]{32})/turns/([a-f0-9]{32})/review', path)
        if match:
            return 200, app.record_review(match[1], match[2], body.get('thread_id'), body.get('review'))
        match = re.fullmatch(r'/api/conversations/([a-f0-9]{32})/branches', path)
        if match:
            return 200, app.create_branch(match[1], body.get('title'), body.get('id'), body.get('agy_id'))
        match = re.fullmatch(r'/api/conversations/([a-f0-9]{32})/collaboration', path)
        if match:
            return 200, app.set_collaboration(match[1], body.get('enabled'))
        match = re.fullmatch(r'/api/conversations/([a-f0-9]{32})/multi-ai-collaboration', path)
        if match:
            return 200, app.set_multi_ai_collaboration(match[1], body)
        match = re.fullmatch(r'/api/conversations/([a-f0-9]{32})/roundtable-workspace-preferences', path)
        if match:
            return 200, app.set_roundtable_workspace_preferences(match[1], body)
        match = re.fullmatch(r'/api/conversations/([a-f0-9]{32})/roundtable', path)
        if match:
            return 200, app.set_roundtable(match[1], body)
        match = re.fullmatch(r'/api/conversations/([a-f0-9]{32})/meetings', path)
        if match:
            return 200, app.create_meeting(match[1], body)
        match = re.fullmatch(r'/api/conversations/([a-f0-9]{32})/meetings/([a-f0-9]{32})/events', path)
        if match:
            return 200, app.record_meeting_event(match[1], match[2], body)
        match = re.fullmatch(r'/api/conversations/([a-f0-9]{32})/meetings/([a-f0-9]{32})/actions', path)
        if match:
            return 200, app.meeting_action(match[1], match[2], body)
        match = re.fullmatch(r'/api/conversations/([a-f0-9]{32})/roundtable/messages', path)
        if match:
            return 200, app.record_roundtable_message(match[1], body)
        match = re.fullmatch(r'/api/conversations/([a-f0-9]{32})/console-startup', path)
        if match:
            return 200, app.set_auto_start_console(match[1], body)
        match = re.fullmatch(r'/api/conversations/([a-f0-9]{32})/auto-open', path)
        if match:
            return 200, app.set_auto_open_activity(match[1], body.get('enabled'))
        if path == '/api/reconnect':
            app.refresh_health()
            return 202, {'ok': True}
        if path == '/api/account/refresh':
            app.refresh_account()
            return 202, {'ok': True}
        match = re.fullmatch(r'/api/conversations/([a-f0-9]{32})/turns', path)
        if match:
            return 202, copy.deepcopy(app.submit(match[1], body.get('prompt'), body.get('model', DEFAULT_MODEL), body.get('kind', 'text'), body.get('branch'), body.get('branch_title'), body.get('codex_dispatch', False), body.get('meeting_id'), body.get('meeting_stage')))
        match = re.fullmatch(r'/api/turns/([a-f0-9]{32})/cancel', path)
        if match:
            app.stop(match[1])
            return 200, {'ok': True}
        return 404, {'error': '接口不存在'}

    def log_message(self, fmt, *args):
        if len(args) > 1 and str(args[1]).startswith(('2', '3')):
            return
        super().log_message(fmt, *args)


def foreground_coordination_path(data):
    """Keep isolated profiles from publishing over the installed policy mirror."""
    data = Path(data).expanduser().resolve()
    shared = default_policy_path()
    # Portable POSIX installs use ROOT/data. Per-user installed layouts (notably
    # Windows) use the standard policy directory's data child. Other --data
    # profiles own their mirror, which service metadata reports to consumers.
    normal_data = {(ROOT / 'data').resolve(), (shared.parent / 'data').resolve()}
    return shared if data in normal_data else data / 'coordination.json'


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--port', type=int, default=8765)
    parser.add_argument('--data', default=str(ROOT / 'data'))
    parser.add_argument('--agy-bin', default=str(Path.home() / '.local/bin/agy'))
    parser.add_argument('--import-legacy-history', action='store_true', help='Explicit compatibility import from adjacent ai-assistants/runs; off by default')
    args = parser.parse_args()
    if not terminal_is_foreground():
        announce('未启动：必须在前台终端窗口运行此控制台。\n'
                 '请双击桌面的 Antigravity.command；不允许隐藏后台启动。')
        raise SystemExit(2)
    terminal_parent = os.getppid()
    os.umask(0o077)
    try:
        server = ForegroundHTTPServer(('127.0.0.1', args.port), Handler)
    except OSError as exc:
        announce('未启动：地址或端口不可用。若控制台已经打开，请在原终端窗口中管理它。\n' + str(exc))
        raise SystemExit(1)
    try:
        original_proxy = {key: os.environ.get(key) for key in PROXY_KEYS}
        network = configure_proxy()
        app = Console(args.data, args.agy_bin, import_history=args.import_legacy_history,
                      coordination_path=foreground_coordination_path(args.data), notification_sender=notify_bark)
    except Exception:
        server.server_close()
        raise
    server.app = app
    confirm_software_update(ROOT,args.data)
    app.network = network
    announce('\nAg CLI 控制台正在运行，端口：' + str(server.server_port) + '\n'
             '网页：http://127.0.0.1:' + str(server.server_port) + '\n\n'
             '怎么使用：\n'
             '  1. 保持这个终端窗口打开。\n'
             '  2. 在 Codex 右侧浏览器打开上面的网页；已打开就刷新。\n'
             '  3. 等待“Agy CLI 已就绪”，在网页新建任务、选模型并发送。\n'
             '  4. 进度和回复在网页查看，同一任务内可以继续追问。\n\n'
             '怎么关闭：\n'
             '  关闭此终端窗口或按 Control+C，服务和当前任务一起停止。\n'
             '  历史保留。只关网页不会退出；下次双击启动文件即可。\n\n'
             '加载更新：待命后点击网页「重载更新」，当前终端会原地加载。\n\n'
             '状态：仅本机访问；待命不调用模型，发任务后显示调用状态。\n'
             + network['detail'] + '\n'
             '正在检查 Agy CLI 登录与可用模型…')
    shutdown_started = threading.Event()
    reload_requested = threading.Event()
    quit_requested = threading.Event()
    def stop_service():
        if shutdown_started.is_set():
            return
        shutdown_started.set()
        def finish():
            app.close()
            server.shutdown()
        threading.Thread(target=finish, daemon=True).start()
    def shutdown(signum, frame):
        quit_requested.set()
        reload_requested.clear()
        announce('\n正在关闭：停止 Agy CLI，并保存历史…')
        stop_service()
    def reload_service():
        if quit_requested.is_set():
            return
        reload_requested.set()
        announce('\n空闲重载：保存记录，更新当前前台进程…')
        stop_service()
    app.reload_callback = reload_service
    signal.signal(signal.SIGTERM, shutdown)
    signal.signal(signal.SIGINT, shutdown)
    signal.signal(signal.SIGHUP, shutdown)
    signal.signal(signal.SIGTSTP, shutdown)
    signal.signal(signal.SIGTTIN, shutdown)
    signal.signal(signal.SIGTTOU, shutdown)
    def watch_terminal():
        while not shutdown_started.wait(.4):
            if os.getppid() != terminal_parent or not terminal_is_foreground():
                shutdown(signal.SIGHUP, None)
                return
    threading.Thread(target=watch_terminal, daemon=True).start()
    try:
        if not shutdown_started.is_set():
            app.refresh_health()
        server.serve_forever()
    finally:
        app.close()
        server.server_close()
        if reload_requested.is_set() and not server.drain_requests():
            reload_requested.clear()
            announce('重载未执行：在途连接尚未退出。历史已保存，请从原启动文件重开。')
        if not reload_requested.is_set():
            announce('已退出。本控制台启动的 Agy CLI 已停止，历史已保存。')
    if (reload_requested.is_set() and not quit_requested.is_set()
            and os.getppid() == terminal_parent and terminal_is_foreground()):
        os.execve(sys.executable, [sys.executable, '-u', str(ROOT / 'update_bootstrap.py'), '--app-root', str(ROOT),
                                  '--port', str(args.port), '--data', str(args.data), '--agy-bin', str(args.agy_bin)],
                  reload_environment(os.environ, original_proxy))


if __name__ == '__main__':
    main()
