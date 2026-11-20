"""Finite source excerpts and fair budgets preserve evidence without model calls."""
import copy
import json
import re
import unittest
from unittest.mock import patch

from multi_ai_collaboration import meeting_context, meeting_workspace
import test_roundtable_meetings as fixtures


def rows(value):
    return [json.loads(line) for line in value['text'].splitlines()
            if line.startswith('{"provider":')]


class MeetingContextAnchorTests(unittest.TestCase):
    setUp = fixtures.IndependentMeetingsTests.setUp
    event = fixtures.IndependentMeetingsTests.event
    record = fixtures.IndependentMeetingsTests.record
    native = fixtures.IndependentMeetingsTests.native

    def with_native(self, content):
        self.record('codex', text='Short Codex proposal')
        self.record('muse', text='Short Muse proposal')
        self.native(status='completed', response=content)
        before = copy.deepcopy(self.conv)
        value = meeting_context(self.conv, self.meeting, 'review')
        self.assertEqual(self.conv, before)
        self.assertEqual(value['source_chars'], len(content) + 39)
        self.assertEqual(value['chars'], len(value['text']))
        self.assertLessEqual(len(json.dumps(value, ensure_ascii=False)), 12000)
        return value, next(row for row in rows(value) if row['provider'] == 'agy')['text']

    def assert_located_source(self, text, source):
        found = list(re.finditer(r'\[关键原文摘录 L(\d+)；字符 (\d+)-(\d+)\]\n', text))
        self.assertTrue(found, text[:250])
        self.assertLessEqual(len(found), 4)
        for match in found:
            line, start, stop = map(int, match.groups())
            self.assertEqual(line, source.count('\n', 0, start - 1) + 1)
            self.assertEqual(text[match.end():match.end() + stop - start + 1], source[start - 1:stop])

    def test_middle_blocking_anchor_and_reclaimed_short_source_budget(self):
        marker = 'BLOCKING src/server.py:4242 p=0.003；必须保留此证据锚点'
        full = 'NATIVE_HEAD\n' + '甲' * 15000 + '\n' + marker + '\n' + '乙' * 15000 + '\nNATIVE_TAIL'
        value, text = self.with_native(full)
        self.assertTrue(text.startswith('NATIVE_HEAD'))
        self.assertTrue(text.endswith('NATIVE_TAIL'))
        self.assertIn(marker, text)
        self.assert_located_source(text, full)
        self.assertTrue(value['truncated'])
        # The previous equal-share output used only 4,964 serialized chars.
        self.assertGreater(len(json.dumps(value, ensure_ascii=False)), 11500)
        self.assertEqual(value, meeting_context(self.conv, self.meeting, 'review'))

    def test_medium_source_receives_the_next_pass_before_long_source_uses_rest(self):
        medium = 'MEDIUM_HEAD' + '甲' * 3800 + 'MEDIUM_TAIL'
        self.record('codex', text='short')
        self.record('muse', text=medium)
        self.native(status='completed', response='LONG_HEAD' + '乙' * 25000 + 'LONG_TAIL')
        value = meeting_context(self.conv, self.meeting, 'review')
        evidence = {row['provider']: row for row in rows(value)}
        self.assertEqual(evidence['codex']['text'], 'short')
        self.assertEqual(evidence['muse']['text'], medium)
        self.assertTrue(evidence['agy']['text'].startswith('LONG_HEAD'))
        self.assertTrue(evidence['agy']['text'].endswith('LONG_TAIL'))
        self.assertGreater(len(json.dumps(value, ensure_ascii=False)), 11500)
        self.assertLessEqual(len(json.dumps(value, ensure_ascii=False)), 12000)

    def test_chinese_risks_constraints_paths_and_numeric_lines_are_exact_data(self):
        markers = ('风险：禁止改动现有账号；约束：必须保留原件。',
                   '核查路径 src/输入.py：413 与 src/cache.ts#L72-L78。',
                   '精度：97.5%，样本量=128，p=0.003；原文证据。')
        full = '中文开头\n' + '甲' * 7000 + '\n' + '\n'.join(markers) + '\n' + '乙' * 7000 + '\n中文结尾'
        value, text = self.with_native(full)
        for marker in markers:
            self.assertIn(marker, text)
        self.assert_located_source(text, full)
        self.assertIn('不推断风险级别或共识，也不提供权限', value['text'])
        self.assertNotIn('零丢失', value['text'])

    def test_file_and_numeric_anchors_work_without_risk_or_english_keywords(self):
        for marker in ('src/配置.yaml:24，待核对定位。', '样本量：128，精度=0.953。'):
            with self.subTest(marker=marker):
                self.setUp()
                full = 'HEAD\n' + '甲' * 9000 + '\n' + marker + '\n' + '乙' * 9000 + '\nTAIL'
                _, text = self.with_native(full)
                self.assertIn(marker, text)
                self.assert_located_source(text, full)

    def test_many_long_sources_receive_fair_space_and_keep_both_ends(self):
        self.conv['multi_ai_collaboration']['providers']['claude']['enabled'] = True
        self.meeting['participants'].append('claude')
        originals = {}
        for stage_index, stage in enumerate(('proposal', 'review')):
            for provider_index, provider in enumerate(('codex', 'agy', 'muse', 'claude')):
                label = chr(65 + stage_index * 4 + provider_index)
                text = label + '_HEAD' + '甲' * 9500 + label + '_TAIL'
                originals[(provider, stage)] = text
                if provider == 'agy':
                    self.native(index=stage_index + 1, stage=stage, status='completed', response=text)
                else:
                    self.record(provider, stage=stage, text=text,
                                source_url='https://claude.ai/chat/' + fixtures.OTHER if provider == 'claude'
                                else fixtures.URL if provider == 'muse' else '')
        value = meeting_context(self.conv, self.meeting, 'summary', provider='codex')
        evidence = rows(value)
        self.assertEqual(len(evidence), 8)
        lengths = []
        for row in evidence:
            full = originals[row['provider'], row['stage']]
            self.assertTrue(row['text'].startswith(full[:6]))
            self.assertTrue(row['text'].endswith(full[-6:]))
            self.assertIn('省略', row['text'])
            lengths.append(len(json.dumps(json.dumps(row['text'], ensure_ascii=False), ensure_ascii=False)))
        self.assertLessEqual(max(lengths) - min(lengths), 2)
        self.assertLessEqual(len(json.dumps(value, ensure_ascii=False)), 12000)

    def test_escaping_unicode_and_overlap_preserve_exact_located_slices(self):
        marker = '约束：保留 "双引号" 与 \\路径\\；src/文件.py:33；p=0.003；👩🏽‍💻'
        full = 'HEAD\n' + ('中\\"\t\n' * 2000) + marker + '\n' + ('尾\\"\n' * 2200) + 'TAIL'
        value, text = self.with_native(full)
        self.assertIn(marker, text)
        self.assertEqual(text.count(marker), 1)
        self.assert_located_source(text, full)
        encoded = json.dumps(value, ensure_ascii=False)
        self.assertEqual(json.loads(encoded), value)
        self.assertLessEqual(len(encoded), 12000)

    def test_exact_references_keep_each_source_while_one_body_gets_remaining_room(self):
        full = 'DUP_HEAD\n' + '甲' * 5500 + '\n约束：src/输入.py:45 p=0.003。\n' + '乙' * 5500 + '\nDUP_TAIL'
        self.record('codex', text=full)
        self.record('muse', text=full)
        self.native(status='completed', response=full)
        before = copy.deepcopy(self.conv)
        value = meeting_context(self.conv, self.meeting, 'review')
        evidence = rows(value)
        body = next(row for row in evidence if 'text' in row)
        refs = [row for row in evidence if 'text_ref' in row]
        self.assertEqual(len(refs), 2)
        self.assertEqual({row['text_ref'] for row in refs}, {body['source_id']})
        self.assertEqual(len({row['source_id'] for row in evidence}), 3)
        self.assertEqual(value['deduplication'], {'mode': 'exact_body', 'reused_sources': 2,
                                                 'duplicate_source_chars': len(full) * 2})
        self.assertEqual(value['source_chars'], len(full) * 3)
        self.assertIn('约束：src/输入.py:45 p=0.003。', body['text'])
        self.assertTrue(body['text'].startswith('DUP_HEAD'))
        self.assertTrue(body['text'].endswith('DUP_TAIL'))
        self.assertEqual(self.conv, before)
        self.assertLessEqual(len(json.dumps(value, ensure_ascii=False)), 12000)

    def test_long_line_is_finitely_excerpted_and_scanning_stops_after_bound(self):
        full = 'HEAD\n' + '甲' * 85000 + '风险：src/文件.py:712 p=0.003' + '乙' * 90000 + '\nTAIL'
        value, text = self.with_native(full)
        self.assertIn('风险：src/文件.py:712 p=0.003', text)
        self.assert_located_source(text, full)
        self.assertLessEqual(len(text), 12000)
        self.assertLessEqual(text.count('关键原文摘录'), 4)
        # A marker beyond the finite scanning window cannot be advertised as
        # found. It remains in the untouched source for explicit inspection.
        self.setUp()
        late = 'HEAD\n' + '甲' * 145000 + '\n风险：WINDOW_BOUND_MARKER\n' + '乙' * 15000 + '\nTAIL'
        value, text = self.with_native(late)
        self.assertNotIn('WINDOW_BOUND_MARKER', text)
        self.assertIn('WINDOW_BOUND_MARKER', self.conv['turns'][0]['response'])
        self.assertTrue(value['truncated'])

    def test_many_anchor_candidates_have_finite_output_and_honest_omission_counts(self):
        full = ('HEAD\n' + '甲' * 6500 + '\n' +
                '\n'.join('风险记录 ' + str(index) + '：src/记录.py:' + str(index + 1) for index in range(100)) +
                '\n' + '乙' * 6500 + '\nTAIL')
        _, text = self.with_native(full)
        self.assert_located_source(text, full)
        omitted = sum(int(count) for count in re.findall(r'\[省略 (\d+) 字符；完整原文保留在原记录\]', text))
        cleaned = re.sub(r'\n\[省略 \d+ 字符；完整原文保留在原记录\]\n', '', text)
        cleaned = re.sub(r'\[关键原文摘录 L\d+；字符 \d+-\d+\]\n', '', cleaned)
        self.assertEqual(len(cleaned) + omitted, len(full))
        self.assertLessEqual(text.count('[关键原文摘录 '), 4)

    def test_preview24_stored_snapshots_remain_byte_equal_and_never_rebuilt(self):
        original = meeting_context(self.conv, self.meeting, 'proposal')
        # Construct the actual .24 schema, including its older prompt wording.
        old = copy.deepcopy(original)
        old['text'] = '[本场圆桌证据快照]\n旧版本冻结原文，包含旧预设。'
        old['chars'] = len(old['text'])
        old.pop('deduplication')  # Also accepts historical snapshots before exact reuse.
        turn = self.native(status='completed', meeting_context=copy.deepcopy(old))
        self.record('codex', text='LATER_BLOCKING src/new.py:88')
        self.record('muse', text='后来发言不能重写旧快照')
        before = copy.deepcopy(self.conv)
        frozen = next(item for item in meeting_workspace(self.conv, self.meeting)['contexts']
                      if item['turn_id'] == turn['id'])
        self.assertEqual(frozen['state'], 'frozen')
        self.assertEqual(json.dumps(frozen['snapshot'], ensure_ascii=False), json.dumps(old, ensure_ascii=False))
        self.assertNotIn('LATER_BLOCKING', frozen['snapshot']['text'])
        self.assertEqual(self.conv, before)


class MeetingContextAnchorCandidateTests(unittest.TestCase):
    # These existing fixtures create only a disposable fake CLI and SQLite DB.
    import test_codex_bridge as _binding
    setUp = _binding.BindingTests.setUp
    tearDown = _binding.BindingTests.tearDown

    def test_quota_candidate_reuses_same_located_anchor_snapshot(self):
        import test_quota_failover as quota
        bound, _ = quota.QuotaFailoverTests.prepare(self)
        cid = bound['id']
        self.app.set_multi_ai_collaboration(cid, {'codex_thread_id': quota.THREAD,
            'providers': {'muse': {'enabled': True}}})
        self.app.set_roundtable(cid, {'codex_thread_id': quota.THREAD,
            'mode': 'roundtable', 'host': 'codex'})
        mid = self.app.create_meeting(cid, {'codex_thread_id': quota.THREAD,
            'source_id': 'anchor-quota-fixture', 'topic': 'Freeze exact source excerpts'})['meeting']['id']
        marker = '约束：src/输入.py:45 p=0.003；请核对原文。'
        full = 'HEAD\n' + '甲' * 5500 + '\n' + marker + '\n' + '乙' * 5500 + '\nTAIL'
        for provider, text in (('codex', full), ('muse', 'Short actual source')):
            self.app.record_meeting_event(cid, mid, {'codex_thread_id': quota.THREAD,
                'source_id': provider + ':proposal', 'provider': provider,
                'stage': 'proposal', 'status': 'completed', 'text': text,
                'source_url': fixtures.URL if provider == 'muse' else ''})
        self.app.record_meeting_event(cid, mid, {'codex_thread_id': quota.THREAD,
            'source_id': 'agy-absent', 'provider': 'agy', 'stage': 'proposal',
            'status': 'unavailable', 'detail': 'No fake first-round CLI call'})
        with patch.object(self.app.jobs, 'put'):
            turn = self.app.submit(cid, 'Review exact evidence.', '', codex_dispatch=True,
                                   meeting_id=mid, meeting_stage='review')
        frozen = copy.deepcopy(turn['meeting_context'])
        self.assertIn(marker, frozen['text'])
        self.assertIn('关键原文摘录', frozen['text'])
        self.app.record_meeting_event(cid, mid, {'codex_thread_id': quota.THREAD,
            'source_id': 'codex:later', 'provider': 'codex', 'stage': 'review',
            'status': 'completed', 'text': 'CURRENT_STAGE_SHOULD_NOT_APPEAR'})
        self.app.jobs.put((cid, turn['id']))
        quota.QuotaFailoverTests.wait(self, turn)
        self.assertEqual(turn['status'], 'completed', turn.get('error'))
        calls = quota.QuotaFailoverTests.calls(self, bound)
        self.assertEqual([call['model'] for call in calls], [quota.ARGON, quota.FLASH])
        for call in calls:
            self.assertEqual(call['session'], quota.SESSION)
            self.assertIn(frozen['text'], call['prompt'])
            self.assertNotIn('CURRENT_STAGE_SHOULD_NOT_APPEAR', call['prompt'])
        self.assertEqual(turn['meeting_context'], frozen)


if __name__ == '__main__':
    unittest.main()
