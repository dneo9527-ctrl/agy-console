"""Read-only Markdown export of stored work sessions and actual meeting receipts."""
from datetime import datetime, timezone
import re
import unicodedata
from urllib.parse import quote

from conversation_sessions import branch_for, normalize_sessions
from multi_ai_collaboration import validate_meetings, validate_roundtable_messages, meeting_view


PROVIDERS = {'codex': 'Codex', 'agy': 'Agy', 'muse': 'Muse', 'claude': 'Claude'}
STAGES = {'proposal': '独立提案', 'review': '互评', 'summary': '主持总结'}
STATUSES = {'completed': '已完成', 'failed': '未完成', 'cancelled': '已停止',
            'interrupted': '已中断', 'queued': '排队中', 'running': '进行中',
            'cancelling': '正在停止', 'pending': '尚未开始', 'sending': '发送待确认',
            'waiting': '等待中', 'unavailable': '本场未参与', 'active': '进行中', 'partial': '部分完成'}
ID = re.compile(r'[a-f0-9]{32}')
IMAGE_ID = re.compile(r'[a-f0-9]{64}')


def inline(value):
    """Metadata is one line; speech bodies below remain verbatim Markdown."""
    value = value if isinstance(value, str) else ''
    clean = ''.join(' ' if unicodedata.category(c).startswith('C') else c for c in value)
    return ' '.join(clean.split())


def _stamp(value):
    try:
        stamp = datetime.fromisoformat(value) if isinstance(value, str) else None
        if stamp is not None and stamp.tzinfo is not None:
            return stamp.astimezone(timezone.utc)
    except ValueError:
        pass
    return datetime.min.replace(tzinfo=timezone.utc)


def _text(value):
    return value if isinstance(value, str) else ''


def _literal(value):
    """A prompt cannot close its own wrapper and masquerade as a new speaker."""
    text = _text(value)
    runs = [len(run) for run in re.findall(r'`+', text)]
    fence = '`' * max(3, max(runs, default=0) + 1)
    return fence + 'text\n' + text + '\n' + fence


def _markdown(value):
    """Preserve reply Markdown; isolate an unfinished fence from later speakers."""
    text = _text(value)
    opened = None
    for line in text.splitlines():
        match = re.match(r'^ {0,3}(`{3,}|~{3,})(.*)$', line)
        if not match:
            continue
        marker, rest = match.groups()
        if opened is None:
            if marker[0] != '`' or '`' not in rest:
                opened = marker
        elif marker[0] == opened[0] and len(marker) >= len(opened) and not rest.strip():
            opened = None
    return text + ('\n' + opened if opened else '')


def _status(value):
    value = inline(value)
    return STATUSES.get(value, '未知') + '（' + value + '）' if value else '未知'


def _name(value):
    value = unicodedata.normalize('NFC', inline(value))
    value = re.sub(r'[\\/:*?"<>|]', '-', value).strip(' .')[:80].rstrip(' .')
    return value or '会话'


def attachment(filename, cid):
    fallback = 'agy-conversation-' + (cid[:8] if ID.fullmatch(cid) else 'export') + '.md'
    return 'attachment; filename="' + fallback + '"; filename*=UTF-8\'\'' + quote(filename, safe='')


def export_markdown(conv, branch=None, meeting_id=None, codex_thread_id=None, origin=''):
    """Render a detached conversation snapshot, without reading files or calling AI."""
    if codex_thread_id is not None and codex_thread_id != conv.get('codex_thread_id'):
        raise KeyError('固定聊天与导出目标不一致。')
    normalize_sessions(conv)
    if branch is not None and not branch_for(conv, branch):
        raise KeyError('工作会话不存在。')
    if meeting_id is not None and (not isinstance(meeting_id, str) or not ID.fullmatch(meeting_id)):
        raise ValueError('圆桌标识无效。')
    records = validate_meetings(conv)
    meetings = [meeting_view(conv, record) for record in records
                if (branch is None or record['branch'] == branch)
                and (meeting_id is None or record['id'] == meeting_id)]
    if meeting_id is not None:
        if not meetings:
            raise KeyError('圆桌记录不属于当前工作会话。')
        branch = meetings[0]['branch']
    messages = validate_roundtable_messages(conv)
    title = inline(conv.get('title')) or 'Agy 会话'
    lane = branch_for(conv, branch) if branch else None
    scope = ('圆桌：' + inline(meetings[0]['topic']) if meeting_id else
             '工作会话：' + (inline(lane.get('title')) or branch) if lane else '全部工作会话')
    sections = ['# ' + title, '', '- 导出范围：' + scope,
                '- 面板会话：' + conv['id']]
    if conv.get('codex_thread_id'):
        sections.append('- Codex 聊天：' + inline(conv['codex_thread_id']))
    if branch:
        sections.append('- 工作会话标识：' + branch)
    sections += ['', '> 导出全部已保存内容，不受页面分页影响；运行中的任务保留导出时状态。图片文件需另外下载。', '']
    timeline, order = [], 0

    def add(stamp, text):
        nonlocal order
        timeline.append((_stamp(stamp), order, text))
        order += 1

    native_slots = {slot['turn_id']: (meeting, slot) for meeting in meetings for slot in meeting['slots'] if slot['turn_id']}
    turns = [turn for turn in conv.get('turns', [])
             if (branch is None or turn.get('branch') == branch)
             and (meeting_id is None or turn.get('id') in native_slots)]
    for turn in turns:
        tid = _text(turn.get('id'))
        turn_lane = branch_for(conv, turn.get('branch')) or {}
        lane_name = inline(turn_lane.get('title')) or inline(turn.get('branch')) or '原会话'
        requestor = 'Codex → Agy' if turn.get('source') == 'codex' else '用户 → Agy'
        linked, slot = native_slots.get(tid, (None, None))
        context = ' · ' + STAGES.get(turn.get('meeting_stage'), '') if linked else ''
        label = '工作会话：' + lane_name + (' · 圆桌：' + inline(linked['topic']) if linked else '')
        stamp = _text(turn.get('created_at'))
        request = '\n'.join(['## ' + requestor + context, '', label,
                             '时间：' + stamp + ' · 任务：' + tid, '', _literal(turn.get('prompt')), ''])
        add(stamp, request)
        status = slot['status'] if slot else inline(turn.get('status'))
        model = inline(turn.get('model')) or '未知'
        stamp = turn.get('ended_at') or turn.get('progress_updated_at') or turn.get('started_at') or turn.get('created_at')
        response = ['## Agy' + context, '', label, '时间：' + _text(stamp),
                    '状态：' + _status(status) + ' | 模型：' + model + ' | 任务：' + tid, '']
        body = _text(turn.get('response'))
        if body:
            if slot and status != 'completed':
                response += ['> 以下为尚未确认为完成的输出。', '']
            response += [_markdown(body), '']
        else:
            response += ['（尚无回复）', '']
        if slot and slot.get('detail'):
            detail = slot['detail'].replace('会议内仅显示前 12000 字符；完整原文保留在关联 CLI 任务。',
                                          '此导出包含关联 CLI 任务的完整回复。')
            response += ['说明：', _literal(detail), '']
        elif turn.get('error'):
            response += ['错误：', _literal(turn.get('error')), '']
        for image in turn.get('images', []):
            if (not isinstance(image, dict) or not ID.fullmatch(tid)
                    or not isinstance(image.get('id'), str) or not IMAGE_ID.fullmatch(image['id'])):
                continue
            name = inline(image.get('name')) or '图片'
            name = name.replace('\\', '\\\\').replace('[', '\\[').replace(']', '\\]')
            url = origin + '/api/turns/' + tid + '/images/' + image['id'] + '?download=1'
            response += ['图片：[' + name + '](' + url + ')（需原电脑控制台在线）', '']
        add(stamp, '\n'.join(response))

    for meeting in meetings:
        lane_name = inline((branch_for(conv, meeting['branch']) or {}).get('title')) or meeting['branch']
        header = ['## 圆桌会议：' + inline(meeting['topic']), '',
                  '工作会话：' + lane_name + ' · 会议：' + meeting['id'],
                  '主持：' + PROVIDERS[meeting['host']] + ' | 参与：' + '、'.join(PROVIDERS[p] for p in meeting['participants']),
                  '时间：' + meeting['created_at'] + ' | 状态：' + _status(meeting['status']), '']
        preset = meeting.get('workspace', {}).get('preset')
        if preset:
            header += ['本场预设：' + inline(preset['label']), '']
        decisions = meeting.get('decisions', {})
        if decisions.get('status') == 'recorded':
            header += ['### 主持建议纪要', '', '以下是主持建议，不代表用户批准或全员共识。', '']
            for key, label in (('suggested_adoption', '建议采用'), ('disagreements', '分歧'),
                               ('needs_verification', '待验证'), ('next_steps', '下一步')):
                header += ['#### ' + label, '']
                items = decisions.get(key, [])
                if not items:
                    header += ['（未记录条目）', '']
                for item in items:
                    header += [_literal(item['text']), '']
                    for source in item['sources']:
                        identity = (PROVIDERS[source['provider']] + ' · ' + STAGES[source['stage']]
                                    + ' · ' + _status(source['status']) + ' · ' + source['source_id'])
                        header += ['来源：' + inline(identity)]
                        if source.get('turn_id'):
                            header += ['CLI 任务：' + source['turn_id']]
                        if source.get('source_url'):
                            header += ['原会话：' + source['source_url']]
                    header.append('')
        add(meeting['created_at'], '\n'.join(header))
        for slot in meeting['slots']:
            if slot.get('turn_id'):
                continue  # The full native request and output already appear above.
            stage = STAGES[slot['stage']]
            stamp = slot.get('ended_at') or slot.get('updated_at') or meeting['created_at']
            role = '主持' if slot['provider'] == meeting['host'] else '参与'
            speech = ['## ' + PROVIDERS[slot['provider']] + ' · ' + stage + ' · ' + role, '',
                      '圆桌：' + inline(meeting['topic']) + ' · 会议：' + meeting['id'],
                      '时间：' + _text(stamp) + ' | 状态：' + _status(slot['status']), '']
            speech += [_markdown(slot['text']) or '（尚无已取得的发言）', '']
            if slot.get('detail'):
                speech += ['说明：', _literal(slot['detail']), '']
            if slot.get('source_url'):
                speech += ['来源：' + slot['source_url'], '']
            add(stamp, '\n'.join(speech))

    if meeting_id is None:
        for message in messages:
            if branch is not None and message['branch'] != branch:
                continue
            speech = ['## ' + PROVIDERS[message['provider']] + ' · ' + STAGES[message['stage']], '',
                      '时间：' + message['created_at'] + ' | 来源：Codex 同步的实际发言', '', _markdown(message['text']), '']
            if message.get('source_url'):
                speech += ['来源：' + message['source_url'], '']
            add(message['created_at'], '\n'.join(speech))
    sections += [item[2] for item in sorted(timeline)] or ['（此范围尚无保存的会话内容。）']
    suffix = '圆桌-' + _name(meetings[0]['topic']) if meeting_id else _name(lane.get('title') or branch) if lane else '全部会话'
    filename = _name(title + '-' + suffix) + '.md'
    return '\n'.join(sections).rstrip() + '\n', filename
