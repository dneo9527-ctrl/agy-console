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


def panel_action(conversation, url):
    enabled = conversation.get('auto_open_activity') is not False
    return {'type': 'show_existing_browser' if enabled else 'none', 'enabled': enabled,
            'url': url, 'placement': 'right', 'verified_visible': False}


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
    parser.add_argument('--auto-open', choices=('on', 'off'), help='With --ensure, save whether calls should reveal this chat activity page; calls no model')
    parser.add_argument('--review-turn', help='Record explicit Codex decisions for this existing turn; calls no model')
    parser.add_argument('--review-file', help='JSON review with summary, items and verification')
    args = parser.parse_args()
    if not args.thread_id or not UUID.fullmatch(args.thread_id):
        parser.error('需要当前 Codex 聊天 ID；请使用 CODEX_THREAD_ID 或 --thread-id。')
    if args.branch and not re.fullmatch(r'[a-z][a-z0-9_-]{0,39}', args.branch):
        parser.error('--branch 需为最多40位的小写字母、数字、横线或下划线。')
    if args.collaboration and not args.ensure:
        parser.error('--collaboration 仅用于 --ensure，不会启动模型。')
    if args.auto_open and not args.ensure:
        parser.error('--auto-open 仅用于 --ensure，不会启动模型。')
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
        if args.auto_open:
            if not client.features.get('panel_auto_open'):
                raise ValueError('自动打开开关需先载入新版后台。')
            result = client.request('/api/conversations/' + conv['id'] + '/auto-open', {'enabled': args.auto_open == 'on'})
            conv['auto_open_activity'] = result['enabled']
        lane = (conv.get('branches') or {}).get(args.branch) if args.branch else None
        print(json.dumps({'bound':True, 'codex_thread_id':args.thread_id, 'console_conversation_id':conv['id'],
                          'agy_id':(lane or {}).get('agy_id') if args.branch else conv['agy_id'],
                          'branch_id':args.branch or conv.get('primary_branch'), 'branch_exists':bool(lane) if args.branch else bool(conv.get('primary_branch')),
                          'url':client.url+'/?codex='+args.thread_id+('&session='+args.branch if lane else ''),
                          'collaboration_enabled':conv.get('gpt_collaboration') is not False,
                          'collaboration_supported':bool(client.features.get('gpt_collaboration')),
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
    if conv.get('gpt_collaboration') is False:
        raise ValueError('这条聊天已暂停 GPT 协作。请在固定页面重新开启；仍可在网页手动给 Agy 发任务。')
    selection_state = client.request('/api/state?summary=1') if not args.model and not client.features.get('window_models') else {}
    model = args.model or ('' if client.features.get('window_models') else default_model(selection_state))
    turn = client.request('/api/conversations/' + conv['id'] + '/turns', {'prompt':prompt, 'model':model, 'kind':args.kind, 'codex_dispatch':True,
                          **({'branch':args.branch, 'branch_title':args.branch_title} if args.branch else {})})
    print(json.dumps({'submitted': True, 'url': fixed_url, 'turn_id': turn['id'], 'model': turn.get('model', model),
                      'model_selection': 'explicit' if args.model else 'window_or_global' if client.features.get('window_models') else 'global_default' if selection_state.get('preferred_model') else 'available_quota_default',
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
