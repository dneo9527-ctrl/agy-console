"""Shared advertised-model selection for the console and Codex dispatcher."""
import math
import re
from datetime import datetime, timezone

DEFAULT_MODEL = 'gemini-4-argon-high'
DEFAULT_ORDER = [DEFAULT_MODEL, 'gemini-3.8-flash-high', 'claude-sonnet-5-5-medium',
                 'gemini-4-hydrogen-high', 'gemini-3.8-flash-medium', 'claude-sonnet-5-5-high']


def clean_global_model_policy(value):
    from quota_failover import clean_fallback
    if not isinstance(value, dict) or set(value) - {'text', 'image'}:
        raise ValueError('全局智能选择需分别设置文字或生图。')
    result = {}
    for kind, settings in value.items():
        if not isinstance(settings, dict) or set(settings) - {'enabled', 'models'}:
            raise ValueError('全局智能选择包含无效设置。')
        result[kind] = clean_fallback(settings)
    return result


def model_selection_settings(snapshot, conversation=None, kind='text'):
    """Resolve legacy/window preferences and a shared, kind-specific smart list."""
    conversation = conversation or {}
    preferences = conversation.get('model_preferences') or {}
    shared = snapshot.get('global_model_policy') or {}
    preferred = preferences[kind] if kind in preferences else '' if kind in shared else snapshot.get('preferred_model', '')
    settings = shared[kind] if kind in shared else (conversation.get('fallback_preferences') or {}).get(kind) or {}
    return preferred or '', settings


def model_family(model):
    return re.sub(r'-(minimal|low|medium|high|xhigh|max)$', '', model)


def quota_groups(model):
    if model.startswith(('claude-', 'gpt-')):
        return ['3p-5h', '3p-weekly']
    if model.startswith('gemini-'):
        return ['gemini-5h', 'gemini-weekly'] + (['gemini-argon-wtu-weekly'] if 'argon' in model or 'orchestrator' in model else [])
    return []


def quota_empty(model, account):
    if account.get('state') not in ('known', 'checking'):
        return False
    for key in quota_groups(model):
        bucket = (account.get('quota') or {}).get(key) or {}
        value = bucket.get('remaining_fraction')
        if type(value) not in (int, float) or not math.isfinite(value) or value > 0:
            continue
        try:
            if bucket.get('reset_time') and datetime.fromisoformat(bucket['reset_time'].replace('Z', '+00:00')) <= datetime.now(timezone.utc):
                continue  # A stale pre-reset zero is unknown, not a permanent ban.
        except (ValueError, TypeError):
            pass
        return True
    return False


def effective_order(snapshot):
    """One advertised order for menus, candidate tiers and worker selection."""
    available = [m['id'] for m in snapshot.get('health', {}).get('models', [])]
    ranked = list(dict.fromkeys(list(snapshot.get('model_order') or []) + DEFAULT_ORDER + available))
    groups = {}
    for model in ranked:
        if model in available:
            groups.setdefault(model_family(model), []).append(model)
    return [model for variants in groups.values() for model in variants]


def candidate_models(snapshot, kind='text', allowed=None, preferred=None):
    available = [m['id'] for m in snapshot.get('health', {}).get('models', [])]
    order = effective_order(snapshot)
    if allowed is not None:
        available = [m for m in available if m in allowed]
    # An explicitly selected tier wins within its family; other families use the
    # saved candidate tier, or the first tier in the user's ordered list.
    ranked = list(dict.fromkeys(([preferred] if preferred else []) +
                               (list(allowed) if allowed is not None else []) + list(order) + DEFAULT_ORDER + available))
    blocked = set(snapshot.get('blocked_models') or [])
    seen, result = set(), []
    for model in ranked:
        family = model_family(model)
        if model not in available or family in seen:
            continue
        seen.add(family)
        if model not in blocked and not quota_empty(model, snapshot.get('health', {}).get('account') or {}):
            result.append(model)
    return result


def select_turn_model(snapshot, turn, verified_images=(), retry=False):
    """Pure selection shared by the worker and the composer's quota preview."""
    current = turn['model']
    available = {m['id'] for m in snapshot.get('health', {}).get('models', [])}
    if (not retry and current in available and current not in snapshot.get('blocked_models', [])
            and not quota_empty(current, snapshot.get('health', {}).get('account') or {})):
        return current
    allowed = turn.get('fallback_models')
    if turn.get('kind') == 'image' and allowed is None:
        allowed = list(verified_images)
        if not retry:
            allowed = list(dict.fromkeys([current] + allowed))
    options = candidate_models(snapshot, turn.get('kind', 'text'), allowed=allowed,
                               preferred=None if retry else current)
    if retry:
        used = {tuple(quota_groups(a['model'])) for a in turn.get('attempts', [])}
        options = [m for m in options if tuple(quota_groups(m)) not in used]
    if not options:
        raise ValueError('候补模型暂无可用额度；请等待重置或调整本窗口候补列表。')
    return options[0]


def window_model_resolution(snapshot, conversation, kind='text', verified_images=()):
    """Read-only forecast. Never changes a saved preference or fixes a queued model."""
    preferred, settings = model_selection_settings(snapshot, conversation, kind)
    shared = kind in (snapshot.get('global_model_policy') or {})
    enabled = settings.get('enabled', True)
    result = {'requested_model': preferred or '', 'effective_model': '', 'fallback_enabled': enabled,
              'automatic': False, 'error': '', 'checked_at': (snapshot.get('health', {}).get('account') or {}).get('checked_at')}
    try:
        model = preferred
        if not model:
            allowed = settings.get('models') if enabled or shared else None
            if kind == 'image' and allowed is None:
                allowed = list(verified_images)
            candidates = candidate_models(snapshot, kind, allowed=allowed)
            if not candidates:
                raise ValueError('生图候补中没有可用模型，请选择生图模型或调整候补。' if kind == 'image'
                                 else '候补模型暂无可用额度；请等待重置或调整本窗口候补列表。')
            model = candidates[0]
        if enabled or not preferred:
            turn = {'model': model, 'kind': kind}
            if (enabled or shared) and 'models' in settings:
                turn['fallback_models'] = settings['models']
            model = select_turn_model(snapshot, turn, verified_images)
        elif model not in {m['id'] for m in snapshot.get('health', {}).get('models', [])}:
            raise ValueError('所选模型当前不可用，请选择其他模型。')
        result.update(effective_model=model, automatic=bool(preferred and preferred != model))
    except ValueError as error:
        result['error'] = str(error)
    return result


def default_model(snapshot, conversation=None, kind='text'):
    """Avoid a known exhausted quota when no exact model was requested."""
    health = snapshot.get('health') or {}
    available = [m['id'] for m in health.get('models', []) if isinstance(m, dict) and isinstance(m.get('id'), str)]
    preferences = (conversation or {}).get('model_preferences') or {}
    scoped = kind in preferences
    preferred, settings = model_selection_settings(snapshot, conversation, kind)
    if preferred:
        if preferred not in available:
            raise ValueError(('本窗口' if scoped else '全局默认') + '模型当前不可用，请在网页重新选择。')
        return preferred
    shared = kind in (snapshot.get('global_model_policy') or {})
    allowed = settings.get('models') if shared else None
    if shared and kind == 'image' and allowed is None:
        allowed = list(snapshot.get('verified_image_models') or [])
    candidates = candidate_models(snapshot, kind, allowed=allowed)
    if candidates:
        return candidates[0]
    if not available:
        raise ValueError('尚未获取可用模型，请在控制台重新检查连接。')
    raise ValueError('当前可用模型的已知额度均已耗尽；请等待重置或在控制台选择其他已获授权的模型。')

