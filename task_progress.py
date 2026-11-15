"""Small, public stage records only: never tool arguments or model reasoning."""
from datetime import datetime, timezone
import re

LIMIT = 32
LABELS = {'user_input': '接收任务', 'agent_response': '组织回复',
          'checkpoint': '保存会话', 'tool': '使用工具'}


def record(turn, key, label, state='active', at=None):
    at = at or datetime.now(timezone.utc).isoformat()
    rows = turn.setdefault('progress', [])
    row = next((r for r in rows if r['key'] == key), None)
    if row is None:
        row = {'key': key, 'label': label, 'started_at': at}
        rows.append(row)
    row.update(label=label, state=state, updated_at=at)
    if len(rows) > LIMIT:
        turn['progress_omitted'] = turn.get('progress_omitted', 0) + len(rows) - LIMIT
        rows[1:len(rows) - LIMIT + 1] = []
    turn['progress_updated_at'] = at


def step_progress(turn, step):
    kind = step.get('step_type')
    if not kind:
        return
    # Only a short tool identifier is displayable. Do not include payloads/text.
    tool = step.get('tool_name')
    tool = tool if isinstance(tool, str) and re.fullmatch(r'[A-Za-z0-9_.:/-]{1,80}', tool) else ''
    label = LABELS.get(kind, 'CLI 处理') + (' · ' + tool if tool else '')
    index = step.get('step_index')
    raw_id = step.get('step_id')
    key = ('step:' + str(index) if type(index) is int and index >= 0 else
           'step:' + raw_id if isinstance(raw_id, str) and re.fullmatch(r'[A-Za-z0-9_-]{1,80}', raw_id) else
           'stage:' + (kind if kind in LABELS else 'other') + ':' + tool)
    state = {'DONE': 'done', 'COMPLETED': 'done', 'FAILED': 'failed',
             'ERROR': 'failed', 'CANCELLED': 'stopped', 'SKIPPED': 'skipped'}.get(str(step.get('state', '')).upper(), 'active')
    record(turn, key, label, state)


def finish_progress(turn):
    status = turn['status']
    at = turn.get('ended_at') or datetime.now(timezone.utc).isoformat()
    # A successful round doesn't prove that each previously active tool succeeded.
    state = 'ended' if status == 'completed' else 'stopped'
    for row in turn.get('progress', []):
        if row.get('state') == 'active':
            row.update(state=state, updated_at=at)
    label = {'completed': '任务完成', 'failed': '任务未完成', 'cancelled': '任务已停止',
             'interrupted': '服务中断，任务未确认完成'}.get(status, '任务结束')
    record(turn, 'outcome', label, 'done' if status == 'completed' else status, at)
