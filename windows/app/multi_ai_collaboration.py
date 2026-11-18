"""Strict per-chat collaboration preferences and a metadata-only offline mirror."""
import copy
import json
import os
from pathlib import Path
import re
import secrets
import sys
from datetime import datetime
from urllib.parse import urlsplit

PROVIDERS = ('agy', 'muse', 'claude')
UUID = re.compile(r'[a-f0-9]{8}(?:-[a-f0-9]{4}){3}-[a-f0-9]{12}')


def validate(value, thread_id=None, portable=False):
    allowed = {'schema_version', 'enabled', 'providers'} | (set() if portable else {'codex_thread_id'})
    if (not isinstance(value, dict) or set(value) != allowed
            or type(value.get('schema_version')) is not int or value['schema_version'] != 1
            or type(value.get('enabled')) is not bool):
        raise ValueError('多 AI 协作状态无效，请检查本聊天设置。')
    if not portable:
        identity = value.get('codex_thread_id')
        if (not isinstance(identity, str) or not UUID.fullmatch(identity)
                or thread_id is not None and identity != thread_id):
            raise ValueError('多 AI 协作状态与当前聊天不一致。')
    providers = value.get('providers')
    if not isinstance(providers, dict) or set(providers) != set(PROVIDERS):
        raise ValueError('多 AI 协作提供方不完整。')
    for provider in PROVIDERS:
        item = providers[provider]
        if not isinstance(item, dict) or set(item) != {'enabled'} or type(item.get('enabled')) is not bool:
            raise ValueError('提供方开关必须为开启或关闭。')
    return copy.deepcopy(value)


def legacy(thread_id, enabled=True):
    if not isinstance(thread_id, str) or not UUID.fullmatch(thread_id):
        raise ValueError('请先进入这条 Codex 聊天的固定入口。')
    if type(enabled) is not bool:
        raise ValueError('旧协作开关损坏，不能自动恢复协作。')
    return {'schema_version': 1, 'codex_thread_id': thread_id,
            'enabled': enabled is not False,
            'providers': {p: {'enabled': p == 'agy'} for p in PROVIDERS}}


def canonical(conv):
    """Absence migrates; an existing but malformed state never restores defaults."""
    if 'multi_ai_collaboration' in conv:
        thread = conv.get('codex_thread_id')
        if not isinstance(thread, str) or not UUID.fullmatch(thread):
            raise ValueError('多 AI 协作必须属于固定 Codex 聊天。')
        return validate(conv['multi_ai_collaboration'], thread)
    return legacy(conv.get('codex_thread_id'), conv.get('gpt_collaboration', True))


def allowed(conv, provider='agy'):
    try:
        value = canonical(conv)
        return provider in PROVIDERS and value['enabled'] and value['providers'][provider]['enabled']
    except ValueError:
        return False


def migrate(conv):
    if not conv.get('codex_thread_id'):
        return
    try:
        value = canonical(conv)
    except ValueError:
        # Preserve the corrupt original for diagnostics; block legacy consumers.
        conv['gpt_collaboration'] = False
        return
    conv['multi_ai_collaboration'] = value
    conv['gpt_collaboration'] = value['enabled'] and value['providers']['agy']['enabled']
    conv.setdefault('auto_start_console', False)


def patch(current, body, thread_id):
    value = validate(current, thread_id)
    if (not isinstance(body, dict) or set(body) - {'schema_version', 'codex_thread_id', 'enabled', 'providers'}
            or body.get('codex_thread_id') != thread_id
            or 'schema_version' in body and (type(body['schema_version']) is not int or body['schema_version'] != 1)
            or not ({'enabled', 'providers'} & set(body))):
        raise ValueError('多 AI 协作更新格式或聊天标识无效。')
    if 'enabled' in body:
        if type(body['enabled']) is not bool:
            raise ValueError('协作总开关必须为开启或关闭。')
        value['enabled'] = body['enabled']
    if 'providers' in body:
        providers = body['providers']
        if not isinstance(providers, dict) or not providers or set(providers) - set(PROVIDERS):
            raise ValueError('请选择已支持的协作提供方。')
        for name, item in providers.items():
            if not isinstance(item, dict) or set(item) != {'enabled'} or type(item.get('enabled')) is not bool:
                raise ValueError('提供方开关必须为开启或关闭。')
            value['providers'][name]['enabled'] = item['enabled']
    return value


def portable(conv):
    value = canonical(conv)
    value.pop('codex_thread_id')
    return value



HOSTS = ('codex',) + PROVIDERS
ROUNDTABLE_STEPS = ('independent_proposals', 'peer_review', 'host_summary')


def validate_roundtable(value, thread_id=None, portable=False):
    keys = {'schema_version', 'mode', 'host', 'rounds'} | (set() if portable else {'codex_thread_id'})
    if (not isinstance(value, dict) or set(value) != keys
            or type(value.get('schema_version')) is not int or value['schema_version'] != 1
            or value.get('mode') not in ('assist', 'roundtable')
            or value.get('host') not in HOSTS
            or type(value.get('rounds')) is not int or value['rounds'] != 2):
        raise ValueError('圆桌主持设置无效，请检查本聊天设置。')
    if not portable:
        identity = value.get('codex_thread_id')
        if (not isinstance(identity, str) or not UUID.fullmatch(identity)
                or thread_id is not None and identity != thread_id):
            raise ValueError('圆桌主持设置与当前聊天不一致。')
    return copy.deepcopy(value)


def roundtable(conv):
    thread = conv.get('codex_thread_id')
    if not isinstance(thread, str) or not UUID.fullmatch(thread):
        raise ValueError('圆桌协作必须属于固定 Codex 聊天。')
    if 'roundtable' in conv:
        return validate_roundtable(conv['roundtable'], thread)
    return {'schema_version': 1, 'codex_thread_id': thread,
            'mode': 'assist', 'host': 'codex', 'rounds': 2}


def patch_roundtable(current, body, thread_id):
    value = validate_roundtable(current, thread_id)
    if (not isinstance(body, dict) or set(body) - {'schema_version', 'codex_thread_id', 'mode', 'host', 'rounds'}
            or body.get('codex_thread_id') != thread_id
            or not ({'mode', 'host', 'rounds'} & set(body))):
        raise ValueError('圆桌更新格式或聊天标识无效。')
    for key in ('schema_version', 'mode', 'host', 'rounds'):
        if key in body:
            value[key] = body[key]
    return validate_roundtable(value, thread_id)


def portable_roundtable(conv):
    value = roundtable(conv)
    value.pop('codex_thread_id')
    return value


def roundtable_plan(conv):
    thread = conv.get('codex_thread_id')
    plan = {'schema_version': 1, 'codex_thread_id': thread, 'mode': None, 'host': None,
            'rounds': 2, 'participants': [], 'ready': False, 'reason': 'invalid_state',
            'execution': 'active_codex_skill', 'steps': []}
    try:
        settings = roundtable(conv)
        policy = canonical(conv)
    except (ValueError, TypeError):
        return plan
    plan.update(mode=settings['mode'], host=settings['host'],
                participants=['codex'] + [p for p in PROVIDERS if policy['providers'][p]['enabled']])
    if settings['mode'] != 'roundtable':
        plan['reason'] = 'assist_mode'
        return plan
    plan['steps'] = list(ROUNDTABLE_STEPS)
    if not policy['enabled']:
        plan['reason'] = 'master_paused'
    elif settings['host'] != 'codex' and not policy['providers'][settings['host']]['enabled']:
        plan['reason'] = 'host_disabled'
    elif len(plan['participants']) == 1:
        plan['reason'] = 'no_external_participant'
    else:
        plan.update(ready=True, reason='ready')
    return plan


def roundtable_prompt(prompt, settings, thread_id):
    """Server-owned role envelope; only the actual bounded assignment is sent."""
    value = validate_roundtable(settings, thread_id)
    if value['mode'] != 'roundtable':
        return prompt
    names = {'codex': 'Codex', 'agy': 'Agy', 'muse': 'Muse', 'claude': 'Claude'}
    host = names[value['host']]
    role = ('你是本次圆桌讨论的主持，负责组织问题、归纳分歧和总结。'
            if value['host'] == 'agy' else '你是本次圆桌讨论的参与者，主持为 ' + host + '。')
    return ('[圆桌协作 · 主持：' + host + ' · 最多 2 轮]\n' + role +
            '只完成下面实际交给你的有界任务。未提供其他 AI 的真实回复时，不编造发言、共识或会议已完成；'
            '不要自行增加参与方或追加轮次。Codex 负责实际文件操作、执行和独立核验。\n'
            '[本次任务]\n' + prompt)


# Transcript receipts are observed contributions, not official CLI turns. They
# never become a model prompt or an assertion that another provider was called.
ROUNDTABLE_MESSAGE_FIELDS = frozenset(('codex_thread_id', 'anchor_turn_id', 'provider',
                                      'stage', 'text', 'source_url', 'source_id'))
ROUNDTABLE_MESSAGE_LIMIT = 1000
ROUNDTABLE_MESSAGE_BYTES = 2 * 1024 * 1024


def roundtable_message_request(body, conv):
    if not isinstance(body, dict) or set(body) != ROUNDTABLE_MESSAGE_FIELDS:
        raise ValueError('圆桌发言记录字段无效。')
    thread = conv.get('codex_thread_id')
    if (not isinstance(thread, str) or not UUID.fullmatch(thread)
            or body.get('codex_thread_id') != thread):
        raise ValueError('圆桌发言与当前固定聊天不一致。')
    if (not isinstance(body.get('anchor_turn_id'), str)
            or not re.fullmatch(r'[a-f0-9]{32}', body['anchor_turn_id'])):
        raise ValueError('请选择本聊天已完成的圆桌轮次。')
    anchor = next((turn for turn in conv.get('turns', [])
                   if isinstance(turn, dict) and turn.get('id') == body['anchor_turn_id']), None)
    if (not anchor or anchor.get('source') != 'codex' or anchor.get('status') != 'completed'
            or not isinstance(anchor.get('response'), str) or not anchor['response'].strip()
            or not isinstance(anchor.get('agy_id'), str) or not UUID.fullmatch(anchor['agy_id'])):
        raise ValueError('发言只能关联本聊天已完成且有真实回复的 Codex 圆桌轮次。')
    settings = validate_roundtable(anchor.get('roundtable'), thread)
    if (settings['mode'] != 'roundtable'
            or anchor.get('roundtable_role') != ('host' if settings['host'] == 'agy' else 'participant')):
        raise ValueError('关联轮次缺少有效的圆桌角色快照。')
    provider, stage = body.get('provider'), body.get('stage')
    if provider not in ('codex', 'muse', 'claude') or stage not in ('proposal', 'review', 'summary'):
        raise ValueError('圆桌发言提供方或阶段无效。')
    if stage == 'summary' and settings['host'] != provider:
        raise ValueError('总结必须由该轮冻结的主持人记录。')
    text = body.get('text')
    if (not isinstance(text, str) or not text.strip() or len(text) > 12000
            or len(text.encode('utf-8')) > 48000 or '\x00' in text):
        raise ValueError('圆桌发言须为非空正文，且不超过 12000 字符。')
    if (not isinstance(body.get('source_id'), str)
            or not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_.:-]{0,119}', body['source_id'])):
        raise ValueError('圆桌发言来源标识无效。')
    url = body.get('source_url')
    if not isinstance(url, str) or len(url) > 500:
        raise ValueError('圆桌发言来源地址无效。')
    if provider == 'codex':
        if url:
            raise ValueError('Codex 本地发言不应包含外部来源地址。')
    else:
        try:
            parsed = urlsplit(url)
            hosts = {'muse': ('muse.ai', 'www.muse.ai'), 'claude': ('claude.ai', 'www.claude.ai')}
            prefix = '/thread/' if provider == 'muse' else '/chat/'
            valid = (parsed.scheme == 'https' and parsed.hostname in hosts[provider]
                     and not parsed.username and not parsed.password and parsed.port is None
                     and not parsed.query and not parsed.fragment and '?' not in url and '#' not in url
                     and parsed.path.startswith(prefix)
                     and UUID.fullmatch(parsed.path[len(prefix):]) is not None)
        except ValueError:
            valid = False
        if not valid:
            raise ValueError('请使用该提供方的具体 HTTPS 会话地址，不含查询参数或凭据。')
    if anchor.get('branch') is not None and not isinstance(anchor['branch'], str):
        raise ValueError('圆桌轮次的工作会话无效。')
    branch = anchor.get('branch') or conv.get('primary_branch') or 'main'
    if not isinstance(branch, str) or branch not in conv.get('branches', {}):
        raise ValueError('圆桌轮次的工作会话无效。')
    return copy.deepcopy(body), settings, branch


def validate_roundtable_messages(conv):
    """Validate persistent transcript receipts, including their actual anchors."""
    messages = conv.get('roundtable_messages', [])
    if not isinstance(messages, list) or len(messages) > ROUNDTABLE_MESSAGE_LIMIT:
        raise ValueError('圆桌发言记录数量无效或超过限制。')
    seen_ids, seen_sources, total = set(), set(), 0
    fields = {'id', 'source_id', 'anchor_turn_id', 'provider', 'stage', 'role', 'text',
              'source_url', 'created_at', 'roundtable', 'branch', 'provenance'}
    for message in messages:
        if not isinstance(message, dict) or set(message) != fields:
            raise ValueError('圆桌发言记录结构无效。')
        body = {key: message.get(key) for key in ROUNDTABLE_MESSAGE_FIELDS if key != 'codex_thread_id'}
        body['codex_thread_id'] = conv.get('codex_thread_id')
        _, settings, branch = roundtable_message_request(body, conv)
        expected_role = 'host' if settings['host'] == message['provider'] else 'participant'
        if (not isinstance(message.get('id'), str) or not re.fullmatch(r'[a-f0-9]{32}', message['id'])
                or message['id'] in seen_ids or message['source_id'] in seen_sources
                or message['roundtable'] != settings or message['branch'] != branch
                or message['role'] != expected_role or message['provenance'] != 'codex_observed'):
            raise ValueError('圆桌发言身份、角色或来源快照无效。')
        stamp = message.get('created_at')
        try:
            parsed_stamp = datetime.fromisoformat(stamp) if isinstance(stamp, str) and len(stamp) <= 100 else None
        except ValueError:
            parsed_stamp = None
        if parsed_stamp is None or parsed_stamp.tzinfo is None:
            raise ValueError('圆桌发言记录时间无效。')
        seen_ids.add(message['id']); seen_sources.add(message['source_id'])
        total += len(json.dumps(message, ensure_ascii=False).encode('utf-8'))
    if total > ROUNDTABLE_MESSAGE_BYTES:
        raise ValueError('本聊天圆桌发言记录已达大小限制，请保留现有记录。')
    return copy.deepcopy(messages)

def default_policy_path():
    if sys.platform == 'darwin':
        return Path.home() / 'Library/Application Support/AgyConsole/coordination.json'
    if sys.platform == 'win32':
        local = os.environ.get('LOCALAPPDATA')
        if not local:
            raise ValueError('LOCALAPPDATA 未配置，不能保存本机启动策略。')
        return Path(local) / 'AgyConsole/coordination.json'
    return Path(os.environ.get('XDG_CONFIG_HOME') or Path.home() / '.config') / 'agy-console/coordination.json'


def launcher_path(value, platform=None):
    platform = platform or sys.platform
    if not isinstance(value, str) or len(value) > 4096 or '\x00' in value or '\n' in value or '\r' in value:
        raise ValueError('请选择本机启动入口文件，不能填写命令。')
    if value == '':
        return ''
    path = Path(value)
    if not path.is_absolute():
        raise ValueError('启动入口必须为绝对文件路径。')
    def supported(p):
        return (p.suffix.lower() == '.command' if platform == 'darwin' else
                p.suffix.lower() == '.cmd' if platform == 'win32' else p.name == 'Start-Agy.sh')
    if path.is_dir():
        candidates = [p for p in path.iterdir() if p.is_file() and supported(p)]
        if len(candidates) != 1:
            raise ValueError('目录中需有且仅有一个受支持的启动入口，请直接选择文件。')
        path = candidates[0]
    if not path.is_file() or not supported(path):
        raise ValueError('启动入口不存在，或不适用于当前系统。')
    return str(path.absolute())


class Coordination:
    """No subprocesses; fail-closed atomic publication before/after state writes."""
    def __init__(self, path, installation_root):
        self.path = Path(path)
        self.root = str(Path(installation_root).resolve())
        self.launcher = ''
        if self.path.is_file() and not self.path.is_symlink():
            try:
                value = json.loads(self.path.read_text(encoding='utf-8'))
                if (type(value.get('schema_version')) is int and value['schema_version'] == 1
                        and value.get('installation_root') == self.root):
                    self.launcher = launcher_path(value.get('launcher', {}).get('path', ''))
            except (OSError, ValueError, TypeError, AttributeError):
                pass

    def metadata(self):
        return {'schema_version': 1, 'policy_path': str(self.path),
                'installation_root': self.root, 'launcher': {'path': self.launcher}}

    def write(self, value):
        self.path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        if self.path.is_symlink():
            raise ValueError('离线协作策略不能写入链接文件。')
        tmp = self.path.with_name('.' + self.path.name + '.' + secrets.token_hex(6) + '.tmp')
        try:
            fd = os.open(str(tmp), os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            with os.fdopen(fd, 'w', encoding='utf-8') as stream:
                json.dump(value, stream, ensure_ascii=False)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(tmp, self.path)
        finally:
            tmp.unlink(missing_ok=True)

    def base(self, ready):
        return {'schema_version': 1, 'ready': ready, 'installation_root': self.root,
                'launcher': {'path': self.launcher}, 'threads': {}}

    def invalidate(self):
        self.write(self.base(False))

    def publish(self, conversations):
        value = self.base(True)
        for conv in conversations:
            thread = conv.get('codex_thread_id')
            if not thread or conv.get('deleted_at'):
                continue
            # Invalid state invalidates the whole mirror, never an allowed subset.
            policy = canonical(conv)
            if type(conv.get('auto_start_console', False)) is not bool:
                raise ValueError('本聊天自动启动设置无效。')
            if thread in value['threads']:
                raise ValueError('存在重复 Codex 聊天绑定，离线策略未同步。')
            value['threads'][thread] = {'multi_ai_collaboration': policy,
                                        'auto_start_console': conv.get('auto_start_console', False),
                                        'roundtable': roundtable(conv)}
        self.write(value)
