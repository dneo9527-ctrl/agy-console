#!/usr/bin/env python3
"""Dispatch Codex work through its persistent, visible local Agy conversation."""
import argparse
from html.parser import HTMLParser
import json
import os
from pathlib import Path
import re
import sys
import time
from urllib.error import HTTPError, URLError
from urllib.parse import urlsplit
from urllib.request import ProxyHandler, Request, build_opener

ROOT = Path(__file__).resolve().parent
UUID = re.compile(r'[a-f0-9]{8}(?:-[a-f0-9]{4}){3}-[a-f0-9]{12}')
BUSY = {'queued', 'running', 'cancelling'}
from model_policy import DEFAULT_MODEL, default_model
from multi_ai_collaboration import validate_roundtable, roundtable_plan


def multi_ai_metadata(conversation, thread_id, features):
    """Validate advertised metadata rather than turning malformed pauses on."""
    if not features.get('multi_ai_collaboration'):
        return None
    value = conversation.get('multi_ai_collaboration')
    if (not isinstance(value, dict) or set(value) != {'schema_version', 'codex_thread_id', 'enabled', 'providers'}
            or type(value.get('schema_version')) is not int or value['schema_version'] != 1
            or value.get('codex_thread_id') != thread_id or type(value.get('enabled')) is not bool):
        raise ValueError('多 AI 协作状态无法核实，未派发；请在当前固定聊天检查设置。')
    providers = value.get('providers')
    if (not isinstance(providers, dict) or set(providers) != {'agy', 'muse', 'claude'}
            or any(not isinstance(item, dict) or set(item) != {'enabled'} or type(item.get('enabled')) is not bool
                   for item in providers.values())):
        raise ValueError('多 AI 提供方状态无效，未派发。')
    return json.loads(json.dumps(value))


def panel_action(conversation, url):
    enabled = conversation.get('auto_open_activity') is not False
    return {'type': 'show_existing_browser' if enabled else 'none', 'enabled': enabled,
            'url': url, 'placement': 'right', 'verified_visible': False}


def roundtable_metadata(conversation, thread_id, features):
    """A host preference is never proof that the proposed meeting happened."""
    if not features.get('roundtable_host'):
        return None, None
    policy = multi_ai_metadata(conversation, thread_id, features)
    if policy is None:
        raise ValueError('圆桌协作需要可核实的本聊天多 AI 设置。')
    value = validate_roundtable(conversation.get('roundtable'), thread_id)
    plan = conversation.get('roundtable_plan')
    expected = roundtable_plan({'codex_thread_id': thread_id,
                               'multi_ai_collaboration': policy, 'roundtable': value})
    if (not isinstance(plan, dict) or set(plan) != set(expected)
            or type(plan.get('schema_version')) is not int
            or type(plan.get('rounds')) is not int
            or type(plan.get('ready')) is not bool or plan != expected):
        raise ValueError('圆桌主持或参与计划无法核实，未派发。')
    return value, json.loads(json.dumps(plan))


class TokenParser(HTMLParser):
    token = None
    def handle_starttag(self, tag, attributes):
        attrs = dict(attributes)
        if tag == 'meta' and attrs.get('name') == 'console-token':
            self.token = attrs.get('content')


class Client:
    def __init__(self, url='http://127.0.0.1:8765', timeout=15):
        parsed = urlsplit(url)
        if (parsed.scheme != 'http' or parsed.hostname not in ('localhost', '127.0.0.1')
                or parsed.username or parsed.password or parsed.path not in ('', '/') or parsed.query or parsed.fragment):
            raise ValueError('控制台地址必须是本机 HTTP 地址。')
        self.url, self.timeout, self.token = url.rstrip('/'), timeout, None
        # The address is validated loopback-only above; never send local control
        # requests or the console CSRF token through a network proxy.
        self.opener = build_opener(ProxyHandler({}))
        self.features = {}

    def request(self, path, body=None):
        headers = {}
        if body is not None:
            if self.token is None:
                with self.opener.open(self.url + '/', timeout=self.timeout) as response:
                    page = response.read(256000).decode('utf-8')
                parser = TokenParser(); parser.feed(page); self.token = parser.token
                if not self.token:
                    raise ValueError('控制台页面尚未准备好，请刷新或重新启动。')
            headers = {'Content-Type': 'application/json', 'X-Console-Token': self.token}
        raw = json.dumps(body, ensure_ascii=False).encode() if body is not None else None
        try:
            with self.opener.open(Request(self.url + path, data=raw, headers=headers), timeout=self.timeout) as response:
                return json.load(response)
        except HTTPError as exc:
            try:
                detail = json.loads(exc.read()).get('error', str(exc))
            except (ValueError, AttributeError):
                detail = str(exc)
            raise ValueError(detail) from None

    def bind(self, thread_id, title=None, **seed):
        self.features = self.request('/api/service').get('features', {})
        if not self.features.get('codex_binding'):
            raise ValueError('固定会话功能已更新，请关闭控制台终端，再双击桌面的 Antigravity.command。')
        return self.request('/api/codex/bind', dict(thread_id=thread_id, title=title, **seed))

    def wait(self, conversation_id, turn_id, timeout):
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if self.features.get('light_history'):
                conv = self.request('/api/conversations/' + conversation_id + '/turns/' + turn_id)
                turn = conv.get('turn')
            else:
                conv = self.request('/api/conversations/' + conversation_id)
                turn = next((item for item in conv['turns'] if item['id'] == turn_id), None)
            if turn is None:
                raise ValueError('任务记录暂不可读。')
            if turn['status'] not in BUSY:
                return conv, turn
            time.sleep(.8)
        raise ValueError('等待超时；任务继续由控制台管理，请在固定链接查看或停止。')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--thread-id', default=os.environ.get('CODEX_THREAD_ID') or os.environ.get('CODEX_SESSION_ID'))
    parser.add_argument('--title', help='Title used on first binding only')
    parser.add_argument('--url', default='http://127.0.0.1:8765')
    parser.add_argument('--task-file')
    parser.add_argument('--context', action='append', default=[])
    parser.add_argument('--model', help='Exact model slug; otherwise choose from available models and known remaining quota')
    parser.add_argument('--kind', choices=('text', 'image'), default='text')
    parser.add_argument('--branch', help='Explicit stable work-session key; omit to continue the primary session')
    parser.add_argument('--branch-title', help='Human-readable title used only when this session is created')
    parser.add_argument('--timeout', type=int, default=600)
    parser.add_argument('--out', help='New result directory')
    parser.add_argument('--import-result', help='Adopt a completed verified CLI result as this chat\'s first turn')
    parser.add_argument('--stage-import', action='store_true', help='Stage the import for the next foreground server start')
    parser.add_argument('--no-wait', action='store_true')
    parser.add_argument('--ensure', action='store_true', help='Ensure this Codex chat has a fixed entry without starting a model task')
    parser.add_argument('--collaboration', choices=('on', 'off'), help='With --ensure, save this fixed chat collaboration preference without calling a model')
    parser.add_argument('--provider', choices=('agy', 'muse', 'claude'), help='Provider preference to change with --ensure')
    parser.add_argument('--provider-collaboration', choices=('on', 'off'), help='With --ensure --provider, change only that provider; never resumes the master switch')
    parser.add_argument('--auto-start', choices=('on', 'off'), help='With --ensure, save this chat foreground-launch preference; does not execute the launcher')
    parser.add_argument('--host', choices=('codex', 'agy', 'muse', 'claude'), help='With --ensure, save this chat moderator and select roundtable mode; starts no meeting')
    parser.add_argument('--collaboration-mode', choices=('assist', 'roundtable'), help='With --ensure, save bounded assistance or roundtable mode without dispatch')
    parser.add_argument('--auto-open', choices=('on', 'off'), help='With --ensure, save whether calls should reveal this chat activity page; calls no model')
    parser.add_argument('--review-turn', help='Record explicit Codex decisions for this existing turn; calls no model')
    parser.add_argument('--review-file', help='JSON review with summary, items and verification')
    parser.add_argument('--record-message', help='Record one genuinely observed roundtable contribution from a JSON file; calls no model')
    parser.add_argument('--meeting-create', help='Create one independent meeting from exact-thread JSON; starts no model')
    parser.add_argument('--meeting-event', help='Record one actual meeting event from JSON; starts no model')
    parser.add_argument('--meeting-view', help='Read one exact meeting ID, or list recent meetings; starts no model')
    parser.add_argument('--meeting', help='Link this Agy dispatch to an existing independent meeting ID')
    parser.add_argument('--meeting-stage', choices=('proposal', 'review', 'summary'), help='Frozen stage for --meeting task dispatch')
    args = parser.parse_args()
    if not args.thread_id or not UUID.fullmatch(args.thread_id):
        parser.error('需要当前 Codex 聊天 ID；请使用 CODEX_THREAD_ID 或 --thread-id。')
    if args.branch and not re.fullmatch(r'[a-z][a-z0-9_-]{0,39}', args.branch):
        parser.error('--branch 需为最多40位的小写字母、数字、横线或下划线。')
    if args.collaboration and not args.ensure:
        parser.error('--collaboration 仅用于 --ensure，不会启动模型。')
    if bool(args.provider) != bool(args.provider_collaboration) or (args.provider and not args.ensure):
        parser.error('提供方开关需同时使用 --ensure --provider 和 --provider-collaboration。')
    if args.auto_start and not args.ensure:
        parser.error('--auto-start 仅用于 --ensure，不会执行启动入口。')
    if (args.host or args.collaboration_mode) and not args.ensure:
        parser.error('--host 和 --collaboration-mode 仅用于 --ensure，不会开始会议。')
    if args.ensure and (args.task_file or args.import_result or args.stage_import):
        parser.error('--ensure 只读或保存偏好，不能混入任务派发/导入。')
    if args.auto_open and not args.ensure:
        parser.error('--auto-open 仅用于 --ensure，不会启动模型。')
    controls = [args.meeting_create, args.meeting_event, args.meeting_view]
    if sum(bool(value) for value in controls) > 1:
        parser.error('会议创建、记录和读取请分别执行。')
    if bool(args.meeting) != bool(args.meeting_stage):
        parser.error('--meeting 和 --meeting-stage 需一起用于任务派发。')
    if args.meeting and (not re.fullmatch(r'[a-f0-9]{32}', args.meeting)
            or not args.task_file or args.ensure or args.import_result or args.stage_import
            or args.record_message or args.review_turn or args.review_file or any(controls) or args.kind != 'text'):
        parser.error('会议任务只可关联明确会议ID与阶段，不与设置/导入/记录混用。')
    if any(controls):
        if (args.task_file or args.ensure or args.import_result or args.stage_import
                or args.review_turn or args.review_file or args.record_message or args.context
                or args.model or args.out or args.branch or args.branch_title or args.no_wait
                or args.meeting or args.meeting_stage or args.kind != 'text'):
            parser.error('会议记录操作不启动模型，不能与任务或偏好操作混用。')
        body, mid = None, None
        if args.meeting_view:
            if args.meeting_view != 'list' and not re.fullmatch(r'[a-f0-9]{32}', args.meeting_view):
                parser.error('--meeting-view 需为本聊天的会议ID或list。')
        else:
            path = Path(args.meeting_create or args.meeting_event)
            if not path.is_file() or path.stat().st_size > 65536:
                parser.error('会议文件需为64KB以内的 JSON。')
            try:
                body = json.loads(path.read_text(encoding='utf-8'))
            except (ValueError, UnicodeError):
                parser.error('请选择有效的 UTF-8 会议 JSON。')
            if not isinstance(body, dict) or body.get('codex_thread_id') != args.thread_id:
                parser.error('会议操作必须属于当前确切Codex聊天。')
            if args.meeting_event:
                mid = body.pop('meeting_id', None)
                if not isinstance(mid, str) or not re.fullmatch(r'[a-f0-9]{32}', mid):
                    parser.error('会议事件文件缺少明确meeting_id。')
        client = Client(args.url)
        conv = client.bind(args.thread_id)
        if not client.features.get('roundtable_meetings'):
            raise ValueError('独立圆桌记录需从原前台控制台加载新版后台。')
        base = '/api/conversations/' + conv['id'] + '/meetings'
        if args.meeting_view:
            result = client.request(base + ('' if args.meeting_view == 'list' else '/' + args.meeting_view))
        else:
            if args.meeting_create:
                result = client.request(base, body)
            else:
                result = client.request(base + '/' + mid + '/events', body)
        print(json.dumps(result, ensure_ascii=False))
        return 0
    if args.record_message:
        if (args.task_file or args.ensure or args.import_result or args.stage_import
                or args.review_turn or args.review_file or args.context or args.model
                or args.out or args.branch or args.branch_title or args.no_wait):
            parser.error('--record-message 只记录已收到的真实发言，不能与任务、设置或导入混用。')
        path = Path(args.record_message)
        if not path.is_file() or path.stat().st_size > 65536:
            parser.error('圆桌发言文件需为64KB以内的 JSON。')
        body = json.loads(path.read_text(encoding='utf-8'))
        fields = {'codex_thread_id', 'anchor_turn_id', 'provider', 'stage', 'text', 'source_url', 'source_id'}
        if not isinstance(body, dict) or set(body) != fields or body.get('codex_thread_id') != args.thread_id:
            parser.error('圆桌发言必须完整并属于当前 Codex 聊天。')
        client = Client(args.url)
        conv = client.bind(args.thread_id)
        if not client.features.get('roundtable_messages'):
            raise ValueError('统一圆桌聊天需先从原前台控制台加载新版后台。')
        recorded = client.request('/api/conversations/' + conv['id'] + '/roundtable/messages', body)
        print(json.dumps(recorded, ensure_ascii=False))
        return 0
    if args.review_turn or args.review_file:
        if (not args.review_turn or not args.review_file or not re.fullmatch(r'[a-f0-9]{32}', args.review_turn)
                or args.task_file or args.ensure or args.import_result):
            parser.error('复核回填需同时使用 --review-turn 和 --review-file，不启动新任务。')
        path = Path(args.review_file)
        if path.stat().st_size > 50000:
            parser.error('复核文件最多50KB。')
        review = json.loads(path.read_text(encoding='utf-8'))
        client = Client(args.url); conv = client.bind(args.thread_id)
        if not client.features.get('collaboration_results'):
            raise ValueError('复核回填需先从原前台启动文件载入新版后台。')
        recorded = client.request('/api/conversations/' + conv['id'] + '/turns/' + args.review_turn + '/review',
                                  {'thread_id': args.thread_id, 'review': review})
        print(json.dumps(recorded, ensure_ascii=False))
        return 0
    if args.ensure:
        client = Client(args.url)
        conv = client.bind(args.thread_id, args.title)
        if args.branch and not client.features.get('session_groups'):
            raise ValueError('多会话功能需要载入新版后台，请正常重开桌面 Antigravity.command。')
        if args.collaboration:
            if not client.features.get('gpt_collaboration'):
                raise ValueError('协作开关需载入新版后台，请正常重开桌面 Antigravity.command。')
            result = client.request('/api/conversations/' + conv['id'] + '/collaboration', {'enabled': args.collaboration == 'on'})
            conv['gpt_collaboration'] = result['enabled']
        if args.provider:
            if not client.features.get('multi_ai_collaboration'):
                raise ValueError('提供方开关需先从原前台入口载入新版后台。')
            client.request('/api/conversations/' + conv['id'] + '/multi-ai-collaboration',
                           {'codex_thread_id': args.thread_id,
                            'providers': {args.provider: {'enabled': args.provider_collaboration == 'on'}}})
        if args.auto_start:
            if not client.features.get('console_startup'):
                raise ValueError('离线启动设置需先从原前台入口载入新版后台。')
            client.request('/api/conversations/' + conv['id'] + '/console-startup',
                           {'codex_thread_id': args.thread_id, 'auto_start_console': args.auto_start == 'on'})
        if args.host or args.collaboration_mode:
            if not client.features.get('roundtable_host'):
                raise ValueError('主持设置需先从原前台入口载入新版后台。')
            changes = {'codex_thread_id': args.thread_id}
            if args.host:
                changes.update(host=args.host, mode=args.collaboration_mode or 'roundtable')
            elif args.collaboration_mode:
                changes['mode'] = args.collaboration_mode
            client.request('/api/conversations/' + conv['id'] + '/roundtable', changes)
        if args.auto_open:
            if not client.features.get('panel_auto_open'):
                raise ValueError('自动打开开关需先载入新版后台。')
            result = client.request('/api/conversations/' + conv['id'] + '/auto-open', {'enabled': args.auto_open == 'on'})
            conv['auto_open_activity'] = result['enabled']
        if args.collaboration or args.provider or args.auto_start or args.host or args.collaboration_mode:
            conv = client.bind(args.thread_id, args.title)
        multi = multi_ai_metadata(conv, args.thread_id, client.features)
        meeting, plan = roundtable_metadata(conv, args.thread_id, client.features)
        lane = (conv.get('branches') or {}).get(args.branch) if args.branch else None
        print(json.dumps({'bound':True, 'codex_thread_id':args.thread_id, 'console_conversation_id':conv['id'],
                          'agy_id':(lane or {}).get('agy_id') if args.branch else conv['agy_id'],
                          'branch_id':args.branch or conv.get('primary_branch'), 'branch_exists':bool(lane) if args.branch else bool(conv.get('primary_branch')),
                          'url':client.url+'/?codex='+args.thread_id+('&session='+args.branch if lane else ''),
                          'collaboration_enabled':(multi['enabled'] and multi['providers']['agy']['enabled']) if multi is not None else conv.get('gpt_collaboration') is not False,
                          'collaboration_supported':bool(client.features.get('gpt_collaboration')),
                          'multi_ai_collaboration_supported':bool(client.features.get('multi_ai_collaboration')),
                          **({'multi_ai_collaboration':multi} if multi is not None else {}),
                          'roundtable_host_supported':bool(client.features.get('roundtable_host')),
                          'roundtable_meetings_supported':bool(client.features.get('roundtable_meetings')),
                          'meeting_summaries':conv.get('meeting_summaries', []),
                          **({'roundtable':meeting, 'roundtable_plan':plan} if meeting is not None else {}),
                          'console_startup_supported':bool(client.features.get('console_startup')),
                          'auto_start_console':conv.get('auto_start_console') is True,
                          **({'console_startup':conv['console_startup']} if client.features.get('console_startup') and isinstance(conv.get('console_startup'),dict) else {}),
                          'auto_open_activity':conv.get('auto_open_activity') is not False,
                          'auto_open_supported':bool(client.features.get('panel_auto_open')),
                          'panel_action': panel_action(conv, client.url+'/?codex='+args.thread_id+('&session='+args.branch if lane else '')),
                          'model_started':False},ensure_ascii=False))
        return 0
    if not args.task_file or args.timeout <= 0:
        parser.error('需要 --task-file 和正数超时。')
    os.umask(0o077)
    prompt = Path(args.task_file).read_text(encoding='utf-8')
    for value in args.context:
        path = Path(value)
        prompt += '\n<reference_material filename=' + json.dumps(path.name) + '>\n' + path.read_text(encoding='utf-8') + '\n</reference_material>'
    if not prompt.strip() or len(prompt.encode()) > 100000:
        parser.error('任务加资料需在 100KB 内；请提取与本次任务直接相关的部分。')
    seed = {}
    if args.import_result:
        original = json.loads(Path(args.import_result).read_text(encoding='utf-8'))
        if (not original.get('completed') or original.get('status') != 'SUCCESS' or original.get('denied_actions')
                or not UUID.fullmatch(str(original.get('conversation_id', ''))) or not original.get('response', '').strip()):
            parser.error('只可导入已完成、有真实回复和明确会话 ID 的 CLI 结果。')
        seed = {'agy_id': original['conversation_id'], 'initial_turn': {
            'prompt': prompt, 'response': original['response'],
            'model': original.get('requested_model') or args.model or DEFAULT_MODEL, 'elapsed': original.get('elapsed_seconds', 0)}}
    elif args.stage_import:
        parser.error('--stage-import 必须配合 --import-result。')
    fixed_url = args.url.rstrip('/') + '/?codex=' + args.thread_id
    if args.branch:
        fixed_url += '&session=' + args.branch
    if args.stage_import:
        folder = ROOT / 'data' / 'pending-codex'
        folder.mkdir(parents=True, exist_ok=True)
        target = folder / (args.thread_id + '.json')
        payload = dict(thread_id=args.thread_id, title=args.title, **seed)
        encoded = json.dumps(payload, ensure_ascii=False)
        if len(encoded.encode()) > 150000:
            parser.error('导入内容过长。')
        if target.exists() and target.read_text(encoding='utf-8') != encoded:
            parser.error('已有另一项待导入记录，未覆盖。')
        temporary = target.with_suffix('.tmp')
        temporary.write_text(encoded, encoding='utf-8')
        temporary.replace(target)
        print(json.dumps({'staged': True, 'url': fixed_url, 'restart_required': True}, ensure_ascii=False))
        return 0
    client = Client(args.url)
    out = Path(args.out).resolve() if args.out else None
    if out:
        out.mkdir(parents=True, exist_ok=False)
        (out / 'request.txt').write_text(prompt, encoding='utf-8')
    conv = client.bind(args.thread_id, args.title, **seed)
    if args.branch and not client.features.get('session_groups'):
        raise ValueError('多会话功能需要载入新版后台，请正常重开桌面 Antigravity.command。')
    if args.import_result:
        print(json.dumps({'imported': True, 'conversation_id': conv['id'], 'agy_id': conv['agy_id'], 'url': fixed_url}, ensure_ascii=False))
        return 0
    multi = multi_ai_metadata(conv, args.thread_id, client.features)
    if multi is not None and (not multi['enabled'] or not multi['providers']['agy']['enabled']):
        raise ValueError('这条聊天已暂停多 AI 协作或 Agy；未派发新的任务，已有任务不受影响。')
    if multi is None and conv.get('gpt_collaboration') is False:
        raise ValueError('这条聊天已暂停协作。请在固定页面重新开启；仍可在网页手动给 Agy 发任务。')
    meeting, plan = roundtable_metadata(conv, args.thread_id, client.features)
    if not args.meeting and meeting is not None and meeting['mode'] == 'roundtable' and not plan['ready']:
        raise ValueError('圆桌主持或参与方已暂停，未派发；请检查当前聊天设置。')
    if args.meeting and not client.features.get('roundtable_meetings'):
        raise ValueError('独立会议任务需先加载新版后台。')
    selection_state = client.request('/api/state?summary=1') if not args.model and not client.features.get('window_models') else {}
    model = args.model or ('' if client.features.get('window_models') else default_model(selection_state))
    turn = client.request('/api/conversations/' + conv['id'] + '/turns', {'prompt':prompt, 'model':model, 'kind':args.kind, 'codex_dispatch':True,
                          **({'branch':args.branch, 'branch_title':args.branch_title} if args.branch else {}),
                          **({'meeting_id':args.meeting, 'meeting_stage':args.meeting_stage} if args.meeting else {})})
    print(json.dumps({'submitted': True, 'url': fixed_url, 'turn_id': turn['id'], 'model': turn.get('model', model),
                      'model_selection': 'explicit' if args.model else 'window_or_global' if client.features.get('window_models') else 'global_default' if selection_state.get('preferred_model') else 'available_quota_default',
                      **({'roundtable':turn['roundtable'], 'roundtable_role':turn.get('roundtable_role')} if 'roundtable' in turn else {}),
                      'duplicate_submission':turn.get('duplicate_submission', False),
                      **({'meeting_id':turn['meeting_id'], 'meeting_stage':turn['meeting_stage']} if 'meeting_id' in turn else {}),
                      'panel_action': panel_action(conv, fixed_url)}, ensure_ascii=False), flush=True)
    if args.no_wait:
        return 0
    conv, turn = client.wait(conv['id'], turn['id'], args.timeout)
    result = dict(turn, completed=turn['status']=='completed', conversation_id=turn.get('agy_id') or conv.get('agy_id'),
                  console_conversation_id=conv['id'], codex_thread_id=args.thread_id, url=fixed_url)
    if out:
        (out / 'result.json').write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding='utf-8')
        (out / 'response.md').write_text(turn['response'], encoding='utf-8')
    print(json.dumps(result if not out else {k:result[k] for k in ('completed','conversation_id','url')}, ensure_ascii=False))
    return 0 if result['completed'] else 1


if __name__ == '__main__':
    try:
        sys.exit(main())
    except (ValueError, OSError, URLError) as error:
        print(json.dumps({'completed':False, 'error':str(error)}, ensure_ascii=False), file=sys.stderr)
        sys.exit(1)
