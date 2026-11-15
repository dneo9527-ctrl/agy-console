"""Small, on-demand facts from one console work session; no CLI or model calls."""
from datetime import datetime, timezone
import math
import re


def context_insights(conversation, branch):
    turns = [turn for turn in conversation.get('turns', []) if turn.get('branch') == branch]
    compact = None
    latest_usage = None
    for index, turn in enumerate(turns):
        if (turn.get('status') == 'completed' and not turn.get('error')
                and re.match(r'^\s*/compact(?:\s|$)', str(turn.get('prompt', '')))):
            compact = {'turn_id': turn['id'], 'completed_at': turn.get('ended_at') or turn.get('created_at'),
                       'checkpoint_verified': bool(turn.get('compaction_evidence')),
                       'turns_after': len(turns) - index - 1}
        usage = turn.get('usage') if isinstance(turn.get('usage'), dict) else {}
        safe_usage = {key: value for key, value in usage.items()
                      if key in ('input_tokens', 'output_tokens', 'cache_read_tokens', 'total_tokens')
                      and isinstance(value, (int, float)) and not isinstance(value, bool)
                      and 0 <= value <= 2**53 - 1 and math.isfinite(value)}
        if safe_usage and turn.get('status') in ('completed', 'failed', 'cancelled', 'interrupted'):
            latest_usage = {'turn_id': turn['id'], 'usage': safe_usage,
                            'usage_scope': turn.get('usage_scope') if turn.get('usage_scope') in ('turn', 'conversation') else None, 'status': turn['status'],
                            'recorded_at': turn.get('ended_at') or turn.get('created_at')}
    lane = conversation.get('branches', {}).get(branch, {})
    return {'conversation_id': conversation['id'], 'branch': branch,
            'session_title': lane.get('title', '当前会话'), 'turn_count': len(turns),
            'manual_compaction': compact, 'latest_usage': latest_usage,
            'context_percent': None, 'automatic_compactions': None,
            'checked_at': datetime.now(timezone.utc).isoformat()}
