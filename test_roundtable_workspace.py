"""Workspace transparency and evidence-backed conclusions, without model calls."""
import copy
import json
import unittest

from multi_ai_collaboration import (meeting_context, meeting_view, meeting_workspace,
                                    new_meeting, record_meeting_event, validate_meetings,
                                    roundtable_workspace_preferences,
                                    patch_roundtable_workspace_preferences, roundtable_presets)
from roundtable_workspace import DECISION_CATEGORIES
import test_roundtable_meetings as fixtures
from test_roundtable_meetings import THREAD, OTHER, MID, STAMP, LATER, URL


def decisions(text='Keep the bounded task', source_id='test:event-1', **ref):
    value = {'schema_version': 1, **{key: [] for key in DECISION_CATEGORIES}}
    value['suggested_adoption'] = [{'text': text, 'sources': [{'source_id': source_id, **ref}]}]
    return value


def context_rows(value):
    return [json.loads(line) for line in value['text'].splitlines() if line.startswith('{"provider":')]


class RoundtableWorkspaceTests(unittest.TestCase):
    setUp = fixtures.IndependentMeetingsTests.setUp
    event = fixtures.IndependentMeetingsTests.event
    record = fixtures.IndependentMeetingsTests.record
    native = fixtures.IndependentMeetingsTests.native
    close_proposals = fixtures.IndependentMeetingsTests.close_proposals

    def close_reviews(self):
        self.close_proposals()
        self.record('codex', stage='review')
        self.record('muse', stage='review')
        self.record('agy', stage='review', status='unavailable')

    def change_preferences(self, **values):
        self.conv['roundtable_workspace_preferences'] = patch_roundtable_workspace_preferences(
            self.conv, {'codex_thread_id': THREAD, **values})

    def test_defaults_are_pure_and_each_catalog_entry_is_independent(self):
        before = copy.deepcopy(self.conv)
        value = roundtable_workspace_preferences(self.conv)
        self.assertEqual(value, {'schema_version': 1, 'codex_thread_id': THREAD,
                                 'preset': 'general', 'deduplicate_exact': True})
        self.assertEqual([p['id'] for p in roundtable_presets()], ['general', 'code_review', 'paper_review'])
        catalog = roundtable_presets()
        catalog[0]['proposal'] = 'Changed caller copy'
        self.assertNotEqual(catalog, roundtable_presets())
        self.assertEqual(self.conv, before)

    def test_preferences_reject_wrong_chat_unknown_fields_types_and_damaged_metadata(self):
        for body in ({'codex_thread_id': OTHER, 'preset': 'general'},
                     {'codex_thread_id': THREAD, 'preset': 'unknown'},
                     {'codex_thread_id': THREAD, 'deduplicate_exact': 1},
                     {'codex_thread_id': THREAD, 'providers': {'muse': True}}):
            with self.subTest(body=body), self.assertRaises(ValueError):
                patch_roundtable_workspace_preferences(self.conv, body)
        self.conv['roundtable_workspace_preferences'] = {'schema_version': 1}
        with self.assertRaises(ValueError):
            roundtable_workspace_preferences(self.conv)

    def test_preferences_never_change_pauses_or_models(self):
        self.conv['multi_ai_collaboration']['enabled'] = False
        self.conv['models'] = {'text': 'chosen', 'image': 'image-chosen'}
        before = copy.deepcopy(self.conv)
        value = patch_roundtable_workspace_preferences(self.conv, {'codex_thread_id': THREAD,
                     'preset': 'paper_review', 'deduplicate_exact': False})
        self.assertEqual(value['preset'], 'paper_review')
        self.assertFalse(value['deduplicate_exact'])
        self.assertEqual(before, self.conv)

    def test_new_meeting_freezes_preset_and_policy_only_once(self):
        self.change_preferences(preset='paper_review', deduplicate_exact=False)
        request = dict(self.request, source_id='paper-meeting')
        new = new_meeting(self.conv, request, '2' * 32, LATER)
        self.conv['roundtable_meetings'].append(new)
        self.change_preferences(preset='code_review', deduplicate_exact=True)
        self.assertEqual(new['workspace']['preset']['id'], 'paper_review')
        self.assertFalse(new['workspace']['deduplicate_exact'])
        self.assertIn('研究问题', meeting_context(self.conv, new, 'proposal')['text'])
        same = new_meeting(self.conv, request, '3' * 32, LATER)
        self.assertEqual(same, new)
        self.assertEqual(len(meeting_view(self.conv, new)['slots']), 7)
        self.assertEqual(self.conv['roundtable'], new['roundtable'])

    def test_new_meeting_explicit_preset_does_not_change_saved_preferences(self):
        new = new_meeting(self.conv, dict(self.request, source_id='explicit', preset='code_review'), '2' * 32, LATER)
        self.assertEqual(new['workspace']['preset']['id'], 'code_review')
        self.assertEqual(roundtable_workspace_preferences(self.conv)['preset'], 'general')
        self.conv['roundtable_meetings'].append(new)
        with self.assertRaises(ValueError):
            new_meeting(self.conv, dict(self.request, source_id='explicit', preset='paper_review'), '3' * 32, LATER)

    def test_legacy_meeting_does_not_acquire_a_preset_or_deduplication(self):
        del self.meeting['workspace']
        self.change_preferences(preset='code_review')
        validate_meetings(self.conv)
        workspace = meeting_workspace(self.conv, self.meeting)
        self.assertIsNone(workspace['preset'])
        self.assertIsNone(workspace['context_policy'])
        self.assertNotIn('workspace', self.meeting)
        self.record('codex', text='Identical legacy body')
        self.record('muse', text='Identical legacy body')
        self.record('agy', status='unavailable')
        rows = context_rows(meeting_context(self.conv, self.meeting, 'review'))
        self.assertEqual(sum(row.get('text') == 'Identical legacy body' for row in rows), 2)

    def test_invalid_frozen_preset_or_extra_fields_are_rejected(self):
        for change in ('id', 'policy_type', 'unknown'):
            value = copy.deepcopy(self.conv)
            workspace = value['roundtable_meetings'][0]['workspace']
            if change == 'id':
                workspace['preset']['id'] = 'unauthorized-plan'
            elif change == 'policy_type':
                workspace['deduplicate_exact'] = 'yes'
            else:
                workspace['activation'] = True
            with self.subTest(change=change), self.assertRaises(ValueError):
                validate_meetings(value)

    def test_structured_summary_records_only_explicit_sourced_suggestions(self):
        self.close_reviews()
        self.record('codex', stage='summary', text='Host summary', decisions=decisions())
        value = meeting_view(self.conv, self.meeting)['decisions']
        self.assertEqual(value['status'], 'recorded')
        self.assertEqual(value['host'], 'codex')
        ref = value['suggested_adoption'][0]['sources'][0]
        self.assertEqual((ref['source_id'], ref['provider'], ref['stage'], ref['status']),
                         ('test:event-1', 'codex', 'proposal', 'completed'))
        self.assertIn('不代表用户批准', value['detail'])
        self.assertEqual(meeting_workspace(self.conv, self.meeting)['decisions'], value)

    def test_missing_bundle_is_not_inferred_from_consensus_words_or_json_text(self):
        for text in ('大家一致同意采用，全部通过验证。', json.dumps({'summary': 'Host', 'decisions': decisions()})):
            with self.subTest(text=text):
                self.setUp(); self.close_reviews()
                self.record('codex', stage='summary', text=text)
                value = meeting_view(self.conv, self.meeting)['decisions']
                self.assertEqual(value['status'], 'not_recorded')
                self.assertEqual(value['suggested_adoption'], [])

    def test_decisions_require_frozen_host_completed_summary(self):
        for provider, stage, status in (('muse', 'summary', 'completed'),
                                        ('codex', 'proposal', 'completed'),
                                        ('codex', 'summary', 'waiting')):
            with self.subTest(provider=provider, stage=stage, status=status):
                self.setUp()
                if stage == 'summary':
                    self.close_reviews()
                with self.assertRaises(ValueError):
                    self.record(provider, stage=stage, status=status, decisions=decisions())

    def test_arbitrary_or_other_meeting_source_is_rejected(self):
        self.close_reviews()
        before = copy.deepcopy(self.conv)
        for source in ('invented', 'another-meeting-event', 'native:' + '3' * 32):
            with self.subTest(source=source), self.assertRaises(ValueError):
                self.record('codex', stage='summary', decisions=decisions(source_id=source))
        self.assertEqual(self.conv, before)

    def test_waiting_receipt_is_not_a_terminal_source_even_after_result_arrives(self):
        self.record('muse', status='sending')
        waiting_id = self.meeting['events'][0]['source_id']
        self.record('muse', text='Actual completed result')
        self.record('codex'); self.record('agy', status='unavailable')
        for provider in ('codex', 'muse'):
            self.record(provider, stage='review')
        self.record('agy', stage='review', status='unavailable')
        with self.assertRaises(ValueError):
            self.record('codex', stage='summary', decisions=decisions(source_id=waiting_id))

    def test_supplied_provider_stage_or_turn_must_match_actual_source(self):
        self.close_reviews()
        for ref in ({'provider': 'muse'}, {'stage': 'review'}, {'turn_id': 'f' * 32}):
            with self.subTest(ref=ref), self.assertRaises(ValueError):
                self.record('codex', stage='summary', decisions=decisions(**ref))

    def test_failed_or_unavailable_sources_remain_explicitly_failed_not_agreement(self):
        self.close_reviews()
        bundle = {'schema_version': 1, **{key: [] for key in DECISION_CATEGORIES}}
        bundle['needs_verification'] = [{'text': 'Obtain the absent CLI review',
                                         'sources': [{'source_id': 'test:event-6', 'provider': 'agy', 'stage': 'review', 'turn_id': None}]}]
        self.record('codex', stage='summary', decisions=bundle)
        value = meeting_view(self.conv, self.meeting)['decisions']
        self.assertEqual(value['needs_verification'][0]['sources'][0]['status'], 'unavailable')
        self.assertFalse(value['suggested_adoption'])

    def test_invalid_structured_categories_or_unsourced_items_are_rejected(self):
        self.close_reviews()
        candidates = [dict(decisions(), consensus=True), {'schema_version': 1},
                      decisions(text=''), decisions()]
        candidates[-1]['suggested_adoption'][0]['sources'] = []
        for bundle in candidates:
            with self.subTest(bundle=bundle), self.assertRaises(ValueError):
                self.record('codex', stage='summary', decisions=bundle)

    def test_idempotency_does_not_silently_drop_or_change_structured_bundle(self):
        self.close_reviews()
        body = self.event('codex', stage='summary', decisions=decisions())
        updated, duplicate = record_meeting_event(self.conv, MID, body, LATER)
        self.assertFalse(duplicate)
        self.conv['roundtable_meetings'] = [updated]
        self.assertTrue(record_meeting_event(self.conv, MID, body, LATER)[1])
        changed = copy.deepcopy(body); changed.pop('decisions')
        with self.assertRaises(ValueError):
            record_meeting_event(self.conv, MID, changed, LATER)

    def test_frozen_host_stays_authoritative_when_current_host_changes(self):
        self.close_reviews()
        self.conv['roundtable']['host'] = 'agy'
        self.record('codex', stage='summary', decisions=decisions())
        self.assertEqual(meeting_view(self.conv, self.meeting)['decisions']['host'], 'codex')

    def _native_host_setup(self):
        self.conv = fixtures.conversation(host='agy')
        self.meeting = new_meeting(self.conv, self.request, MID, STAMP)
        self.conv['roundtable_meetings'] = [self.meeting]
        self.sequence = 0
        self.close_reviews()

    def test_native_host_requires_literal_json_with_real_source_ids(self):
        self._native_host_setup()
        turn = self.native(index=3, stage='summary', status='completed',
                           response=json.dumps({'summary': 'Real host result', 'decisions': decisions()}, ensure_ascii=False))
        value = meeting_view(self.conv, self.meeting)['decisions']
        self.assertEqual(value['status'], 'recorded')
        self.assertEqual(value['turn_id'], turn['id'])
        self.assertEqual(value['provider'], 'agy')

    def test_native_bad_source_is_visible_invalid_without_rewriting_actual_result(self):
        self._native_host_setup()
        turn = self.native(index=3, stage='summary', status='completed',
                           response=json.dumps({'summary': 'Real but unsupported', 'decisions': decisions(source_id='invented')}))
        before = copy.deepcopy(self.conv)
        value = meeting_view(self.conv, self.meeting)
        self.assertEqual(value['decisions']['status'], 'invalid')
        self.assertEqual(value['slots'][-1]['status'], 'completed')
        self.assertIn('invented', turn['response'])
        self.assertEqual(before, self.conv)

    def test_native_markdown_fence_does_not_turn_into_explicit_decisions(self):
        self._native_host_setup()
        self.native(index=3, stage='summary', status='completed',
                    response='```json\n' + json.dumps({'summary': 'Result', 'decisions': decisions()}) + '\n```')
        self.assertEqual(meeting_view(self.conv, self.meeting)['decisions']['status'], 'not_recorded')

    def test_workspace_returns_the_exact_enqueue_snapshot_not_a_later_preview(self):
        frozen = meeting_context(self.conv, self.meeting, 'proposal')
        turn = self.native(status='completed', meeting_context=copy.deepcopy(frozen))
        self.record('muse', text='LATER_PEER_TEXT')
        before = copy.deepcopy(self.conv)
        value = meeting_workspace(self.conv, self.meeting)
        item = next(item for item in value['contexts'] if item['provider'] == 'agy' and item['stage'] == 'proposal')
        self.assertEqual(item['state'], 'frozen')
        self.assertEqual(item['provenance'], 'cli_enqueue_snapshot')
        self.assertEqual(item['snapshot'], frozen)
        self.assertNotIn('LATER_PEER_TEXT', item['snapshot']['text'])
        self.assertEqual(item['turn_id'], turn['id'])
        self.assertEqual(self.conv, before)
        item['snapshot']['text'] = 'Changed caller copy'
        self.assertEqual(turn['meeting_context'], frozen)

    def test_web_inputs_and_usage_are_unknown_not_synthesized_or_zero(self):
        self.record('muse', text='Actual browser result')
        value = meeting_workspace(self.conv, self.meeting)
        item = next(item for item in value['contexts'] if item['provider'] == 'muse' and item['stage'] == 'proposal')
        self.assertEqual(item['state'], 'not_recorded')
        self.assertIsNone(item['snapshot'])
        material = next(item for item in value['materials'] if item['provider'] == 'muse' and item['stage'] == 'proposal')
        self.assertEqual(material['source_url'], URL)
        self.assertIsNone(material['usage'])
        self.assertFalse(material['usage_known'])
        self.assertIn('muse', value['usage']['unknown_providers'])

    def test_cli_usage_is_official_scoped_and_separate_from_unknown_web_usage(self):
        self.native(status='completed', usage={'input_tokens': 23, 'output_tokens': 7,
                    'cache_read_tokens': 11, 'total_tokens': 30}, usage_scope='turn')
        value = meeting_workspace(self.conv, self.meeting)
        material = next(item for item in value['materials'] if item['provider'] == 'agy' and item['stage'] == 'proposal')
        self.assertTrue(material['usage_known'])
        self.assertEqual(material['usage']['cache_read_tokens'], 11)
        self.assertEqual(value['usage']['usage']['total_tokens'], 30)
        self.assertEqual(value['usage']['usage_scope'], 'official_cli_turns')
        self.assertIn('muse', value['usage']['unknown_providers'])

    def test_old_cli_input_is_not_reconstructed_and_mismatched_snapshot_is_not_trusted(self):
        turn = self.native(status='completed')
        value = meeting_workspace(self.conv, self.meeting)
        item = next(item for item in value['contexts'] if item['turn_id'] == turn['id'])
        self.assertEqual(item['state'], 'not_recorded')
        turn['meeting_context'] = dict(meeting_context(self.conv, self.meeting, 'proposal'), meeting_id='2' * 32)
        item = next(item for item in meeting_workspace(self.conv, self.meeting)['contexts'] if item['turn_id'] == turn['id'])
        self.assertEqual(item['state'], 'invalid')
        self.assertIsNone(item['snapshot'])

    def test_workspace_preserves_full_native_length_and_display_truncation(self):
        text = 'HEAD' + '原' * 18000 + 'TAIL'
        self.native(status='completed', response=text)
        item = next(item for item in meeting_workspace(self.conv, self.meeting)['materials']
                    if item['provider'] == 'agy' and item['stage'] == 'proposal')
        self.assertEqual(item['source_chars'], len(text))
        self.assertTrue(item['truncated'])
        self.assertEqual(len(item['text']), 12000)

    def test_exact_reuse_keeps_every_source_and_the_official_history(self):
        text = 'Hard constraint: NEVER change account.\nIdentical evidence.'
        self.record('codex', text=text)
        self.record('muse', text=text)
        self.record('agy', status='unavailable', detail='CLI absent')
        before = copy.deepcopy(self.conv)
        value = meeting_context(self.conv, self.meeting, 'review')
        rows = context_rows(value)
        self.assertEqual(len(rows), 3)
        self.assertEqual(rows[0]['text'], text)
        self.assertEqual(rows[2]['text_ref'], rows[0]['source_id'])
        self.assertEqual(rows[1]['status'], 'unavailable')
        self.assertEqual(value['deduplication']['reused_sources'], 1)
        self.assertEqual(value['source_chars'], len(text) * 2 + len('CLI absent'))
        self.assertEqual(before, self.conv)

    def test_near_matches_are_not_deduplicated(self):
        self.record('codex', text='Same text')
        self.record('muse', text='Same  text')
        self.record('agy', status='unavailable')
        value = meeting_context(self.conv, self.meeting, 'review')
        self.assertEqual(value['deduplication']['reused_sources'], 0)
        self.assertEqual(sum('text' in row for row in context_rows(value)), 2)

    def test_disabled_deduplication_is_frozen_for_the_new_meeting(self):
        self.change_preferences(deduplicate_exact=False)
        self.meeting = new_meeting(self.conv, dict(self.request, source_id='no-dedup'), '2' * 32, LATER)
        self.conv['roundtable_meetings'] = [self.meeting]
        # Fixture record() uses MID, so retain its exact expected meeting id.
        self.meeting['id'] = MID
        self.change_preferences(deduplicate_exact=True)
        self.record('codex', text='Repeated body')
        self.record('muse', text='Repeated body')
        self.record('agy', status='unavailable')
        value = meeting_context(self.conv, self.meeting, 'review')
        self.assertEqual(value['deduplication']['mode'], 'off')
        self.assertEqual(sum('text' in row for row in context_rows(value)), 2)

    def test_exact_reuse_and_truncation_keep_double_json_inside_budget(self):
        text = 'HEAD' + ('\"\\\n中' * 2800) + 'TAIL'
        self.record('codex', text=text)
        self.record('muse', text=text)
        self.native(status='completed', response=text)
        value = meeting_context(self.conv, self.meeting, 'review')
        self.assertLessEqual(len(json.dumps(value, ensure_ascii=False)), 12000)
        rows = context_rows(value)
        self.assertEqual(len(rows), 3)
        self.assertEqual(value['deduplication']['reused_sources'], 2)
        self.assertTrue(rows[0]['text'].startswith('HEAD'))
        self.assertTrue(rows[0]['text'].endswith('TAIL'))
        self.assertEqual({row['source_id'] for row in rows[1:]}, {'native:' + '1'.zfill(32), 'test:event-2'})
        self.assertTrue(all(row['text_ref'] == rows[0]['source_id'] for row in rows[1:]))


if __name__ == '__main__':
    unittest.main()
