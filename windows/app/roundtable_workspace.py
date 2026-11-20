"""Pure, bounded workspace preferences and explicit decision validation.

This module never reads local files, dispatches providers or changes permissions.
Presets describe roles for a new meeting; the frozen participant plan owns access.
"""
import copy
import re

_PRESETS = (
    {'id': 'general', 'label': '讨论', 'description': '比较方案、保留分歧并提出验证步骤。',
     'proposal': '独立提出可行方案，说明依据、限制及待验证假设。',
     'review': '核对其他提案的事实和取舍，明确支持、不同意见与缺失证据。',
     'summary': '整理建议采用、分歧、待验证和下一步；建议不代表用户批准。'},
    {'id': 'code_review', 'label': '代码审查', 'description': '围绕缺陷、回归风险与可执行验证审查代码。',
     'proposal': '依据实际代码和任务要求找出缺陷、边界条件及回归风险，引用可核查位置。',
     'review': '复核他方发现是否成立，区分真实缺陷与推测，并提出针对性测试。',
     'summary': '按风险归纳建议修复、争议、待复现项与验证步骤；未运行的测试不得写成通过。'},
    {'id': 'paper_review', 'label': '论文评审', 'description': '核查论证、方法、证据和引用的可靠性。',
     'proposal': '审查研究问题、方法、结果和论证，指出证据不足与需核对的引用。',
     'review': '交叉检查他方评审的依据，区分稿件已有证据、推断及需作者补充的内容。',
     'summary': '归纳建议修改、学术分歧、待核验引用和下一步；不得编造文献、结果或评审共识。'},
)
PRESET_IDS = tuple(item['id'] for item in _PRESETS)
DECISION_CATEGORIES = ('suggested_adoption', 'disagreements', 'needs_verification', 'next_steps')
_UUID = re.compile(r'[a-f0-9]{8}(?:-[a-f0-9]{4}){3}-[a-f0-9]{12}')
_SOURCE = re.compile(r'[A-Za-z0-9][A-Za-z0-9_.:-]{0,119}')
_TURN = re.compile(r'[a-f0-9]{32}')


def roundtable_presets():
    return copy.deepcopy(list(_PRESETS))


def _thread(conv):
    value = conv.get('codex_thread_id')
    if not isinstance(value, str) or not _UUID.fullmatch(value):
        raise ValueError('工作区设置缺少准确的固定聊天标识。')
    return value


def validate_workspace_preferences(value, thread_id, portable=False):
    required = {'schema_version', 'preset', 'deduplicate_exact'}
    if not portable:
        required.add('codex_thread_id')
    if (not isinstance(value, dict) or set(value) != required
            or type(value.get('schema_version')) is not int or value['schema_version'] != 1
            or not isinstance(value.get('preset'), str) or value['preset'] not in PRESET_IDS
            or type(value.get('deduplicate_exact')) is not bool
            or not portable and value.get('codex_thread_id') != thread_id):
        raise ValueError('圆桌材料设置无效。')
    return copy.deepcopy(value)


def roundtable_workspace_preferences(conv):
    thread_id = _thread(conv)
    value = conv.get('roundtable_workspace_preferences')
    if value is None:
        return {'schema_version': 1, 'codex_thread_id': thread_id,
                'preset': 'general', 'deduplicate_exact': True}
    return validate_workspace_preferences(value, thread_id)


def patch_roundtable_workspace_preferences(conv, body):
    thread_id = _thread(conv)
    if (not isinstance(body, dict)
            or set(body) - {'codex_thread_id', 'preset', 'deduplicate_exact'}
            or body.get('codex_thread_id') != thread_id):
        raise ValueError('圆桌材料设置与当前固定聊天不一致。')
    value = roundtable_workspace_preferences(conv)
    for key in ('preset', 'deduplicate_exact'):
        if key in body:
            value[key] = copy.deepcopy(body[key])
    return validate_workspace_preferences(value, thread_id)


def frozen_workspace(conv, preset=None):
    value = roundtable_workspace_preferences(conv)
    if preset is not None:
        if not isinstance(preset, str) or preset not in PRESET_IDS:
            raise ValueError('圆桌协作预设无效。')
        value['preset'] = preset
    return {'schema_version': 1,
            'preset': next(item for item in roundtable_presets() if item['id'] == value['preset']),
            'deduplicate_exact': value['deduplicate_exact']}


def validate_frozen_workspace(value):
    if (not isinstance(value, dict) or set(value) != {'schema_version', 'preset', 'deduplicate_exact'}
            or type(value.get('schema_version')) is not int or value['schema_version'] != 1
            or type(value.get('deduplicate_exact')) is not bool):
        raise ValueError('会议材料策略快照无效。')
    preset = value.get('preset')
    if (not isinstance(preset, dict)
            or set(preset) != {'id', 'label', 'description', 'proposal', 'review', 'summary'}
            or not isinstance(preset.get('id'), str) or preset['id'] not in PRESET_IDS):
        raise ValueError('会议协作预设快照无效。')
    # Freeze the actual guidance, including older wording, rather than silently
    # substituting today's catalog when the application is upgraded.
    for key in ('label', 'description', 'proposal', 'review', 'summary'):
        text = preset.get(key)
        maximum = 80 if key == 'label' else 600
        if not isinstance(text, str) or not text.strip() or len(text) > maximum or '\x00' in text:
            raise ValueError('会议协作预设说明无效。')
    return copy.deepcopy(value)


def validate_decisions(value, sources):
    """Accept only explicit structured data with real terminal source receipts.

    ``sources`` is supplied by the meeting projector, keyed by the genuine
    terminal event's source_id. Source existence is provenance, not agreement.
    """
    if (not isinstance(value, dict) or set(value) != {'schema_version'} | set(DECISION_CATEGORIES)
            or type(value.get('schema_version')) is not int or value['schema_version'] != 1):
        raise ValueError('主持结论须包含四类显式结构化条目。')
    clean = {'schema_version': 1}
    for category in DECISION_CATEGORIES:
        items = value.get(category)
        if not isinstance(items, list) or len(items) > 16:
            raise ValueError('每类主持结论最多保留 16 项。')
        clean[category] = []
        for item in items:
            if not isinstance(item, dict) or set(item) != {'text', 'sources'}:
                raise ValueError('主持结论条目结构无效。')
            text = item.get('text')
            refs = item.get('sources')
            if (not isinstance(text, str) or not text.strip() or len(text) > 1200 or '\x00' in text
                    or not isinstance(refs, list) or not 1 <= len(refs) <= 12):
                raise ValueError('每项主持结论须有正文及本场真实来源。')
            refs_clean, seen = [], set()
            for ref in refs:
                if (not isinstance(ref, dict) or set(ref) - {'source_id', 'provider', 'stage', 'turn_id'}
                        or not isinstance(ref.get('source_id'), str) or not _SOURCE.fullmatch(ref['source_id'])
                        or ref['source_id'] in seen):
                    raise ValueError('主持结论来源标识无效或重复。')
                actual = sources.get(ref['source_id'])
                if actual is None:
                    raise ValueError('主持结论只能引用本场已终结的提案或互评。')
                for key in ('provider', 'stage', 'turn_id'):
                    if key in ref and ref[key] != actual[key]:
                        raise ValueError('主持结论来源的提供方、阶段或 CLI 任务不匹配。')
                refs_clean.append(copy.deepcopy(ref)); seen.add(ref['source_id'])
            clean[category].append({'text': text.strip(), 'sources': refs_clean})
    return clean
