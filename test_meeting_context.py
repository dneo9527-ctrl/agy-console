"""Stage-scoped evidence snapshots keep prompts bounded without rewriting history."""
import copy
import json
import unittest

from multi_ai_collaboration import meeting_context
import test_roundtable_meetings as fixtures
from test_roundtable_meetings import MID, OTHER


class MeetingContextTests(unittest.TestCase):
    # Reuse the real validated fixtures, not mocked projection results.
    setUp = fixtures.IndependentMeetingsTests.setUp
    event = fixtures.IndependentMeetingsTests.event
    record = fixtures.IndependentMeetingsTests.record
    native = fixtures.IndependentMeetingsTests.native
    close_proposals = fixtures.IndependentMeetingsTests.close_proposals
    def test_proposal_never_injects_current_round_peer_text(self):
        self.record('muse', text='PEER_FIRST_ROUND_SECRET')
        value = meeting_context(self.conv, self.meeting, 'proposal')
        self.assertNotIn('PEER_FIRST_ROUND_SECRET', value['text'])
        self.assertEqual(value['source_chars'], 0)
        self.assertFalse(value['truncated'])
        self.assertIn('持久', value['text'])
        self.assertIn('不提供权限', value['text'])
        self.assertEqual(value['stage'], 'proposal')
        self.assertEqual(value['provider'], 'agy')

    def test_review_contains_only_real_proposals_and_their_status(self):
        self.record('codex', text='CODEX_PROPOSAL')
        self.record('muse', text='MUSE_PROPOSAL')
        self.record('agy', status='unavailable', detail='NO_CLI_REPLY')
        self.record('codex', stage='review', text='CURRENT_ROUND_REVIEW_SECRET')
        value = meeting_context(self.conv, self.meeting, 'review')
        for text in ('CODEX_PROPOSAL', 'MUSE_PROPOSAL', 'NO_CLI_REPLY', 'unavailable'):
            self.assertIn(text, value['text'])
        self.assertNotIn('CURRENT_ROUND_REVIEW_SECRET', value['text'])
        self.assertEqual(value['source_chars'], len('CODEX_PROPOSALMUSE_PROPOSALNO_CLI_REPLY'))

    def test_summary_exposes_failed_native_status_without_partial_text(self):
        self.record('codex', text='FIRST_CODEX')
        self.record('muse', text='FIRST_MUSE')
        self.native(status='failed', response='UNACCEPTED_PARTIAL_SECRET', error='ACTUAL_FAILURE')
        self.record('codex', stage='review', text='REVIEW_CODEX')
        self.record('muse', stage='review', status='failed', detail='BROWSER_FAILURE')
        self.record('agy', stage='review', status='unavailable', detail='CLI_UNAVAILABLE')
        value = meeting_context(self.conv, self.meeting, 'summary', provider='codex')
        for text in ('ACTUAL_FAILURE', 'BROWSER_FAILURE', 'CLI_UNAVAILABLE', 'failed', 'unavailable'):
            self.assertIn(text, value['text'])
        self.assertNotIn('UNACCEPTED_PARTIAL_SECRET', value['text'])
        self.assertIn('不能', value['text'])

    def test_current_meeting_only_and_read_only_even_while_paused(self):
        self.close_proposals()
        other = copy.deepcopy(self.meeting)
        other['id'] = '2' * 32
        other['source_id'] = 'another-meeting'
        other['events'][0]['text'] = 'OTHER_MEETING_SECRET'
        self.conv['roundtable_meetings'].append(other)
        self.conv['multi_ai_collaboration']['enabled'] = False
        before = copy.deepcopy(self.conv)
        value = meeting_context(self.conv, self.meeting, 'review')
        self.assertNotIn('OTHER_MEETING_SECRET', value['text'])
        self.assertEqual(self.conv, before)
        self.assertEqual(value['meeting_id'], MID)

    def test_refuses_unready_stage_unknown_provider_or_wrong_host(self):
        for stage, provider in [('review', 'agy'), ('summary', 'codex'), ('proposal', 'claude'), ('summary', 'agy'), ('other', 'agy')]:
            with self.subTest(stage=stage, provider=provider), self.assertRaises(ValueError):
                meeting_context(self.conv, self.meeting, stage, provider=provider)
        broken = copy.deepcopy(self.meeting)
        broken['codex_thread_id'] = OTHER
        with self.assertRaises(ValueError):
            meeting_context(self.conv, broken, 'proposal')

    def test_completed_target_slot_remains_readable(self):
        self.close_proposals()
        self.record('codex', stage='review')
        value = meeting_context(self.conv, self.meeting, 'review', provider='codex')
        self.assertEqual(value['provider'], 'codex')

    def test_long_context_is_fair_bounded_and_keeps_every_source_ends(self):
        def speech(label):
            return label + '_HEAD' + ('中\n\\"' * 2900) + label + '_TAIL'
        for provider in ('codex', 'muse'):
            self.record(provider, text=speech(provider))
        self.native(status='completed', response=speech('agy'))
        value = meeting_context(self.conv, self.meeting, 'review')
        self.assertLessEqual(len(json.dumps(value, ensure_ascii=False)), 12000)
        self.assertLessEqual(value['chars'], 12000)
        self.assertEqual(value['chars'], len(value['text']))
        self.assertTrue(value['truncated'])
        for provider in ('codex', 'muse', 'agy'):
            self.assertIn(provider + '_HEAD', value['text'])
            self.assertIn(provider + '_TAIL', value['text'])
        self.assertIn('省略', value['text'])
        self.assertGreater(value['source_chars'], value['chars'])
        self.assertEqual(value, meeting_context(self.conv, self.meeting, 'review'))

    def test_native_tail_survives_the_display_only_12000_character_limit(self):
        self.record('codex', text='Short Codex proposal')
        self.record('muse', text='Short Muse proposal')
        full = 'NATIVE_HEAD' + ('长' * 30000) + 'NATIVE_REAL_TAIL'
        self.native(status='completed', response=full)
        value = meeting_context(self.conv, self.meeting, 'review')
        self.assertIn('NATIVE_HEAD', value['text'])
        self.assertIn('NATIVE_REAL_TAIL', value['text'])
        self.assertEqual(value['source_chars'], len(full) + len('Short Codex proposalShort Muse proposal'))
        self.assertTrue(value['truncated'])

    def test_escaped_metadata_and_all_four_participants_fit_total_budget(self):
        self.conv['multi_ai_collaboration']['providers']['claude']['enabled'] = True
        self.meeting['participants'].append('claude')
        self.meeting['topic'] = '\x01' * 1000
        self.conv['roundtable_meetings'] = [self.meeting]
        claude_url = 'https://claude.ai/chat/' + OTHER
        for stage in ('proposal', 'review'):
            for provider in ('codex', 'muse', 'claude'):
                self.record(provider, stage=stage,
                            text=provider + '_' + stage + '_HEAD' + ('\\"\n' * 3000) + '_TAIL',
                            source_url=claude_url if provider == 'claude' else fixtures.URL if provider == 'muse' else '')
            self.native(index=1 if stage == 'proposal' else 2, stage=stage,
                        status='completed', response='agy_' + stage + '_HEAD' + ('\\"\n' * 6000) + '_TAIL')
        before = copy.deepcopy(self.conv)
        value = meeting_context(self.conv, self.meeting, 'summary', provider='codex')
        self.assertLessEqual(len(json.dumps(value, ensure_ascii=False)), 12000)
        self.assertEqual(self.conv, before)
        for stage in ('proposal', 'review'):
            for provider in ('codex', 'agy', 'muse', 'claude'):
                self.assertIn(provider + '_' + stage + '_HEAD', value['text'])
        self.assertTrue(value['truncated'])

    def test_summary_preserves_each_rounds_head_and_tail_for_three_and_four_parties(self):
        for participants in (('codex', 'agy', 'muse'), ('codex', 'agy', 'muse', 'claude')):
            with self.subTest(participants=participants):
                self.setUp()
                if 'claude' in participants:
                    self.conv['multi_ai_collaboration']['providers']['claude']['enabled'] = True
                    self.meeting['participants'].append('claude')
                for stage in ('proposal', 'review'):
                    for provider in participants:
                        label = provider + '_' + stage
                        speech = label + '_HEAD' + ('中\\"\n' * 2900) + label + '_TAIL'
                        if provider == 'agy':
                            self.native(index=1 if stage == 'proposal' else 2, stage=stage,
                                        status='completed', response=speech)
                        else:
                            self.record(provider, stage=stage, text=speech,
                                        source_url='https://claude.ai/chat/' + OTHER if provider == 'claude'
                                        else fixtures.URL if provider == 'muse' else '')
                value = meeting_context(self.conv, self.meeting, 'summary', provider='codex')
                encoded = json.dumps(value, ensure_ascii=False)
                self.assertEqual(json.loads(encoded), value)
                self.assertLessEqual(len(encoded), 12000)
                rows = [json.loads(line) for line in value['text'].splitlines()
                        if line.startswith('{"provider":')]
                self.assertEqual(len(rows), len(participants) * 2)
                for row in rows:
                    label = row['provider'] + '_' + row['stage']
                    self.assertTrue(row['text'].startswith(label + '_HEAD'))
                    self.assertTrue(row['text'].endswith(label + '_TAIL'))
                    self.assertIn('省略', row['text'])


if __name__ == '__main__':
    unittest.main()
