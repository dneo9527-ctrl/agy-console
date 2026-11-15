"""Explicit Codex decisions, never inferred from an Ag reply or toggle."""
DECISIONS = {'accepted', 'rejected', 'deferred'}
CHECK_STATES = {'passed', 'partial', 'not_run'}


def clean_text(value, maximum, label, allow_empty=False):
    if not isinstance(value, str) or (not allow_empty and not value.strip()) or len(value) > maximum:
        raise ValueError(label + '格式或长度无效。')
    return value.strip()


def validate_review(value):
    if not isinstance(value, dict):
        raise ValueError('复核结果必须是一个对象。')
    items = value.get('items')
    if not isinstance(items, list) or not 1 <= len(items) <= 12:
        raise ValueError('请记录1至12条明确的处理意见。')
    result = {'summary': clean_text(value.get('summary'), 1000, '复核摘要'), 'items': []}
    for item in items:
        if not isinstance(item, dict) or item.get('decision') not in DECISIONS:
            raise ValueError('处理意见需为采纳、驳回或暂缓。')
        result['items'].append({'suggestion': clean_text(item.get('suggestion'), 500, '建议'),
                                'decision': item['decision'],
                                'reason': clean_text(item.get('reason'), 1000, '处理依据')})
    verification = value.get('verification')
    if not isinstance(verification, dict) or verification.get('status') not in CHECK_STATES:
        raise ValueError('请明确填写验证状态。')
    result['verification'] = {'status': verification['status'],
                              'details': clean_text(verification.get('details'), 3000, '验证依据')}
    return result
