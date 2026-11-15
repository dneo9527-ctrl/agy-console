"""Bounded local search and explicit, session-preserving recovery prompts."""
import re


def search_history(state, query, cid=None, limit=30):
    if not isinstance(query, str) or not 1 <= len(query.strip()) <= 100:
        raise ValueError('请输入1–100字的搜索词。')
    if type(limit) is not int or not 1 <= limit <= 50:
        raise ValueError('最多返回50条结果。')
    pattern = re.compile(re.escape(query.strip()), re.I)
    results, scanned, chars, truncated = [], 0, 0, False
    conversations = sorted((c for c in state['conversations'] if not c.get('deleted_at') and (cid is None or c['id'] == cid)),
                           key=lambda c: c.get('updated_at', ''), reverse=True)
    for conv in conversations:
        for turn in reversed(conv.get('turns', [])):
            scanned += 1
            for key, role in (('prompt', 'user'), ('response', 'assistant')):
                text = str(turn.get(key) or '')
                chars += len(text)
                if chars > 12_000_000 or scanned > 20000:
                    return {'results': results, 'scanned': scanned, 'truncated': True}
                match = pattern.search(text)
                if not match:
                    continue
                if len(results) >= limit:
                    return {'results': results, 'scanned': scanned, 'truncated': True}
                start, end = max(0, match.start() - 75), min(len(text), match.end() + 170)
                branch = turn.get('branch') or conv.get('primary_branch') or 'main'
                results.append({'conversation_id': conv['id'], 'codex_thread_id': conv.get('codex_thread_id'),
                                'title': str(conv['title'])[:80], 'branch': branch,
                                'session_title': str(conv.get('branches', {}).get(branch, {}).get('title', '原会话'))[:40],
                                'turn_id': turn['id'], 'role': role, 'created_at': turn.get('created_at'),
                                'snippet': ('…' if start else '') + text[start:end] + ('…' if end < len(text) else '')})
    return {'results': results, 'scanned': scanned, 'truncated': truncated}


def recovery_prompt(turn, strategy):
    if strategy not in ('continue', 'retry'):
        raise ValueError('请选择继续未完成部分或重试原任务。')
    intent = '继续完成尚未完成的部分。' if strategy == 'continue' else '重新尝试原任务。'
    def bounded(value, maximum):
        raw = str(value or '').encode('utf-8')
        return raw[:maximum].decode('utf-8', errors='ignore') + ('\n（过长部分已省略，请参照原轮记录。）' if len(raw) > maximum else '')
    return ('用户手动请求恢复上一轮任务：' + intent + '\n'
            '先核对原会话、已有结果和当前工作区状态，避免重复修改、重复提交或重复生成。'
            '如已完成，请说明依据；工具权限仍受原限制，不绕过权限。\n\n'
            '[原任务，仅供核对]\n' + bounded(turn.get('prompt'), 60000) + '\n\n'
            '[上一轮状态与错误]\n' + str(turn.get('status')) + '\n' + bounded(turn.get('error'), 4000) + '\n\n'
            '[上一轮已返回内容，仅供核对]\n' + bounded(turn.get('response'), 28000))
