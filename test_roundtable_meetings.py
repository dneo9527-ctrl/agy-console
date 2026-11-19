"""Independent meetings: actual bounded stages without a completed Agy anchor."""
import copy
import unittest
from unittest.mock import patch

from multi_ai_collaboration import (new_meeting, validate_meetings, record_meeting_event,
                                    link_meeting_turn, meeting_view, meeting_summary,
                                    roundtable_plan)

THREAD = '11111111-2222-3333-4444-555555555555'
OTHER = '66666666-7777-8888-9999-aaaaaaaaaaaa'
SESSION = 'bbbbbbbb-cccc-dddd-eeee-ffffffffffff'
MID = '1' * 32
STAMP = '2026-11-19T01:00:00+00:00'
LATER = '2026-11-19T01:01:00+00:00'
URL = 'https://muse.ai/thread/' + OTHER


def conversation(host='codex', agy=True):
    return {'codex_thread_id': THREAD, 'primary_branch': 'main',
            'branches': {'main': {'id': 'main'}, 'art': {'id': 'art'}}, 'turns': [],
            'multi_ai_collaboration': {'schema_version': 1, 'codex_thread_id': THREAD,
                'enabled': True, 'providers': {'agy': {'enabled': agy},
                'muse': {'enabled': True}, 'claude': {'enabled': False}}},
            'roundtable': {'schema_version': 1, 'codex_thread_id': THREAD,
                          'mode': 'roundtable', 'host': host, 'rounds': 2}}


class IndependentMeetingsTests(unittest.TestCase):
    def setUp(self):
        self.conv = conversation()
        self.request = {'codex_thread_id': THREAD, 'source_id': 'test:meeting-1',
                        'topic': 'Long commands: collapsed or expanded?'}
        self.meeting = new_meeting(self.conv, self.request, MID, STAMP)
        self.conv['roundtable_meetings'] = [self.meeting]
        self.sequence = 0

    def event(self, provider, stage='proposal', status='completed', **extra):
        self.sequence += 1
        body = {'codex_thread_id': THREAD, 'source_id': 'test:event-' + str(self.sequence),
                'provider': provider, 'stage': stage, 'status': status,
                'text': 'Actual ' + provider + ' reply' if status == 'completed' else '',
                'source_url': URL if provider == 'muse' and status == 'completed' else '',
                'detail': 'Not available for this bounded meeting' if status in ('unavailable', 'failed', 'cancelled') else '', **extra}
        return body

    def record(self, provider, stage='proposal', status='completed', **extra):
        meeting, duplicate = record_meeting_event(self.conv, MID, self.event(provider, stage, status, **extra), LATER)
        self.conv['roundtable_meetings'] = [meeting]
        self.meeting = meeting
        return duplicate

    def native(self, index=1, stage='proposal', status='queued', **extra):
        turn = {'id': format(index, '032x'), 'source': 'codex', 'kind': 'text',
                'meeting_id': MID, 'meeting_stage': stage, 'branch': 'main',
                'roundtable': copy.deepcopy(self.meeting['roundtable']),
                'roundtable_role': 'host' if self.meeting['roundtable']['host'] == 'agy' else 'participant',
                'status': status, 'response': 'Actual native result' if status == 'completed' else '',
                'expected_agy_id': SESSION, 'agy_id': SESSION if status == 'completed' else None,
                'created_at': STAMP, 'started_at': None, 'ended_at': None, **extra}
        self.conv['turns'].append(turn)
        self.meeting = link_meeting_turn(self.conv, MID, stage, turn, LATER)
        self.conv['roundtable_meetings'] = [self.meeting]
        return turn

    def close_proposals(self):
        self.record('codex')
        self.record('muse')
        self.record('agy', status='unavailable')

    def test_muse_proposal_immediately_without_any_agy_turn(self):
        original = copy.deepcopy(self.conv)
        self.record('muse')
        view = meeting_view(self.conv, self.meeting)
        self.assertEqual(len(self.conv['turns']), 0)
        self.assertEqual(len(view['speakers']), 1)
        self.assertEqual(view['speakers'][0]['provider'], 'muse')
        self.assertEqual(view['speakers'][0]['source_url'], URL)
        self.assertEqual(view['phase'], 'proposal')
        self.assertEqual(self.conv['multi_ai_collaboration'], original['multi_ai_collaboration'])
        self.assertEqual(len(view['slots']), 7)
        for slot in view['slots']:
            self.assertTrue({'provider', 'stage', 'status', 'text', 'source_url', 'detail',
                             'turn_id', 'queued_at', 'started_at', 'ended_at', 'updated_at'} <= slot.keys())

    def test_exact_sources_idempotent_without_touching_or_reopening_pauses(self):
        body = self.event('muse')
        meeting, duplicate = record_meeting_event(self.conv, MID, body, LATER)
        self.assertFalse(duplicate)
        self.conv['roundtable_meetings'] = [meeting]
        self.conv['multi_ai_collaboration']['enabled'] = False
        same, duplicate = record_meeting_event(self.conv, MID, body, '2026-11-19T02:00:00+00:00')
        self.assertTrue(duplicate)
        self.assertEqual(same, meeting)
        with self.assertRaises(ValueError):
            record_meeting_event(self.conv, MID, dict(body, text='Changed source content'), LATER)
        self.assertFalse(self.conv['multi_ai_collaboration']['enabled'])

    def test_new_writes_require_current_master_and_provider(self):
        for edit in ('master', 'provider'):
            with self.subTest(edit=edit):
                conv = copy.deepcopy(self.conv)
                if edit == 'master':
                    conv['multi_ai_collaboration']['enabled'] = False
                else:
                    conv['multi_ai_collaboration']['providers']['muse']['enabled'] = False
                with self.assertRaises(ValueError):
                    record_meeting_event(conv, MID, self.event('muse'), LATER)
        # Historical snapshots remain readable after permissions or host change.
        self.record('muse')
        self.conv['multi_ai_collaboration']['enabled'] = False
        self.conv['roundtable']['host'] = 'agy'
        self.assertEqual(meeting_view(self.conv, self.meeting)['host'], 'codex')
        self.assertEqual(len(validate_meetings(self.conv)), 1)

    def test_pause_blocks_new_send_but_preserves_inflight_observation_and_unsent_skip(self):
        self.record('muse', status='sending')
        self.conv['multi_ai_collaboration']['enabled'] = False
        self.conv['multi_ai_collaboration']['providers']['muse']['enabled'] = False
        self.record('muse', status='waiting', detail='Delivery pending; no resend')
        self.record('muse', text='Actual reply after pause')
        self.record('agy', status='unavailable', detail='Paused before any CLI dispatch')
        view = meeting_view(self.conv, self.meeting)
        self.assertEqual(view['speakers'][0]['text'], 'Actual reply after pause')
        self.assertEqual(next(s for s in view['slots'] if s['provider'] == 'agy' and s['stage'] == 'proposal')['status'], 'unavailable')
        with self.assertRaises(ValueError):
            self.record('codex', status='sending')
        with self.assertRaises(ValueError):
            self.record('codex')
        self.assertFalse(self.conv['multi_ai_collaboration']['enabled'])

    def test_assist_mode_blocks_new_activity_but_not_inflight_result_or_unavailable(self):
        self.record('muse', status='sending')
        self.conv['roundtable']['mode'] = 'assist'
        # Already attempted browser work is still observable after the switch.
        self.record('muse', status='waiting', detail='Already sent; not a resend')
        completed_body = self.event('muse', text='Actual final reply after mode switch')
        updated, duplicate = record_meeting_event(self.conv, MID, completed_body, LATER)
        self.assertFalse(duplicate)
        self.conv['roundtable_meetings'] = [updated]
        self.meeting = updated
        self.assertTrue(record_meeting_event(self.conv, MID, completed_body, LATER)[1])
        before = copy.deepcopy(self.conv)
        for status in ('sending', 'completed', 'failed', 'cancelled'):
            with self.subTest(status=status), self.assertRaisesRegex(ValueError, '圆桌模式已关闭'):
                self.record('codex', status=status)
        self.assertEqual(self.conv, before)
        self.record('agy', status='unavailable', detail='Roundtable mode closed before dispatch')

    def test_new_activity_uses_frozen_external_host_permission_not_current_host(self):
        self.conv = conversation('muse')
        self.meeting = new_meeting(self.conv, self.request, MID, STAMP)
        self.conv['roundtable_meetings'] = [self.meeting]
        self.record('muse', status='sending')
        self.conv['roundtable']['host'] = 'codex'
        self.conv['multi_ai_collaboration']['providers']['muse']['enabled'] = False
        before = copy.deepcopy(self.conv)
        for status in ('sending', 'completed'):
            with self.subTest(status=status), self.assertRaises(ValueError):
                self.record('codex', status=status)
        self.assertEqual(self.conv, before)
        self.record('muse', text='Actual paused host reply from prior send')
        self.record('agy', status='unavailable', detail='Frozen host was disabled before this dispatch')
        self.assertEqual(meeting_view(self.conv, self.meeting)['host'], 'muse')

    def test_creation_requires_explicit_roundtable_ready_exact_branch(self):
        for change in ({'codex_thread_id': OTHER}, {'branch': 'missing'}, {'source_id': 'bad source'},
                       {'topic': ' '}, {'topic': 'x' * 1001}, {'unknown': True}):
            with self.subTest(change=change), self.assertRaises(ValueError):
                new_meeting(conversation(), dict(self.request, **change), MID, STAMP)
        for mutate in ('assist', 'paused', 'host_disabled'):
            conv = conversation()
            if mutate == 'assist': conv['roundtable']['mode'] = 'assist'
            if mutate == 'paused': conv['multi_ai_collaboration']['enabled'] = False
            if mutate == 'host_disabled': conv['roundtable']['host'] = 'claude'
            with self.subTest(mutate=mutate), self.assertRaises(ValueError):
                new_meeting(conv, self.request, MID, STAMP)

    def test_creation_retry_reuses_frozen_meeting_after_pause_and_setting_change(self):
        original = copy.deepcopy(self.meeting)
        self.conv['multi_ai_collaboration']['enabled'] = False
        self.conv['multi_ai_collaboration']['providers']['muse']['enabled'] = False
        self.conv['roundtable'].update(mode='assist', host='agy')
        same = new_meeting(self.conv, self.request, 'f' * 32, LATER)
        self.assertEqual(same, original)
        self.assertEqual(self.conv['roundtable_meetings'], [original])
        for edit in ({'topic': 'Other topic'}, {'branch': 'art'}, {'codex_thread_id': OTHER}):
            with self.subTest(edit=edit), self.assertRaises(ValueError):
                new_meeting(self.conv, dict(self.request, **edit), 'e' * 32, LATER)

    def test_malformed_enum_values_raise_validation_error_without_mutation(self):
        original = copy.deepcopy(self.conv)
        for field in ('provider', 'stage', 'status'):
            for invalid in ([], {}, None, True):
                with self.subTest(field=field, invalid=invalid), self.assertRaises(ValueError):
                    body = self.event('muse')
                    body[field] = invalid
                    record_meeting_event(self.conv, MID, body, LATER)
        self.assertEqual(self.conv, original)

    def test_one_sided_native_link_is_rejected_but_new_candidate_links_atomically(self):
        candidate = {'id': 'f' * 32, 'source': 'codex', 'kind': 'text',
                     'meeting_id': MID, 'meeting_stage': 'proposal', 'branch': 'main',
                     'roundtable': copy.deepcopy(self.meeting['roundtable']),
                     'roundtable_role': 'participant', 'status': 'queued', 'created_at': STAMP}
        self.conv['turns'].append(candidate)
        with self.assertRaises(ValueError):
            validate_meetings(self.conv)
        linked = link_meeting_turn(self.conv, MID, 'proposal', candidate, LATER)
        self.conv['roundtable_meetings'] = [linked]
        self.assertEqual(len(validate_meetings(self.conv)), 1)
        orphan = dict(candidate, id='e' * 32, meeting_id='a' * 32)
        self.conv['turns'].append(orphan)
        with self.assertRaises(ValueError):
            validate_meetings(self.conv)
        with self.assertRaises(ValueError):
            link_meeting_turn(self.conv, MID, 'proposal', candidate, LATER)

    def test_native_long_output_is_bounded_without_changing_original(self):
        text = '长' * 12001
        turn = self.native(status='completed', response=text, started_at=STAMP, ended_at=LATER)
        original = copy.deepcopy(turn)
        view = meeting_view(self.conv, self.meeting)
        slot = next(s for s in view['slots'] if s['provider'] == 'agy' and s['stage'] == 'proposal')
        self.assertEqual(len(slot['text']), 12000)
        self.assertTrue(slot['text_truncated'])
        self.assertIn('完整原文', slot['detail'])
        self.assertTrue(view['speakers'][0]['text_truncated'])
        self.assertEqual(view['speakers'][0]['text'], text[:12000])
        self.assertEqual(turn, original)

    def test_strict_sources_and_no_empty_completed_or_fake_native_speech(self):
        for extra in ({'text': ''}, {'text': ' '}, {'text': '\0'}, {'text': 'x' * 12001},
                      {'source_url': 'https://muse.ai/thread/new'},
                      {'source_url': URL + '?auth=x'}, {'source_url': URL + '#x'},
                      {'source_url': URL.replace('https://', 'https://' + 'someone:secret@')},
                      {'source_url': URL.replace('muse.ai', 'evil.test')},
                      {'source_url': URL.replace('muse.ai', 'muse.ai:443')},
                      {'source_url': ' ' + URL}):
            with self.subTest(extra=extra), self.assertRaises(ValueError):
                record_meeting_event(self.conv, MID, self.event('muse', **extra), LATER)
        for status in ('completed', 'sending', 'waiting'):
            with self.subTest(status=status), self.assertRaises(ValueError):
                record_meeting_event(self.conv, MID, self.event('agy', status=status), LATER)
        for stage in ('review', 'summary'):
            with self.subTest(stage=stage), self.assertRaises(ValueError):
                record_meeting_event(self.conv, MID, self.event('muse', stage=stage), LATER)

    def test_queued_native_is_distinct_and_failed_never_completed(self):
        turn = self.native()
        view = meeting_view(self.conv, self.meeting)
        slot = next(s for s in view['slots'] if s['provider'] == 'agy' and s['stage'] == 'proposal')
        self.assertEqual(slot['status'], 'queued')
        self.assertIsNone(slot['started_at'])
        self.assertEqual(view['speakers'], [])
        prior_revision = view['revision']
        turn.update(status='running', started_at=LATER)
        view = meeting_view(self.conv, self.meeting)
        self.assertNotEqual(view['revision'], prior_revision)
        slot = next(s for s in view['slots'] if s['provider'] == 'agy' and s['stage'] == 'proposal')
        self.assertEqual(slot['queue_seconds'], 60)
        turn.update(status='failed', ended_at='2026-11-19T01:03:00+00:00', error='Timeout')
        view = meeting_view(self.conv, self.meeting)
        slot = next(s for s in view['slots'] if s['provider'] == 'agy' and s['stage'] == 'proposal')
        self.assertEqual(slot['status'], 'failed')
        self.assertEqual(slot['run_seconds'], 120)
        self.assertEqual(view['speakers'], [])
        self.assertEqual(view['updated_at'], turn['ended_at'])

    def test_completed_native_requires_actual_reply_and_official_identity(self):
        turn = self.native(status='completed', started_at=STAMP, ended_at=LATER)
        self.assertEqual(meeting_view(self.conv, self.meeting)['speakers'][0]['text'], 'Actual native result')
        for change in ({'response': ''}, {'agy_id': None}, {'agy_id': OTHER}, {'session_mismatch': True}):
            original = copy.deepcopy(turn)
            turn.update(change)
            with self.subTest(change=change):
                view = meeting_view(self.conv, self.meeting)
                self.assertEqual(view['speakers'], [])
                self.assertEqual(next(s for s in view['slots'] if s['provider'] == 'agy' and s['stage'] == 'proposal')['status'], 'failed')
            turn.clear();turn.update(original)

    def test_native_status_and_dates_are_strict_and_interrupted_text_is_not_speech(self):
        turn = self.native(status='completed', started_at=STAMP, ended_at=LATER)
        original = copy.deepcopy(turn)
        invalid = [('status', {}), ('status', []), ('status', None), ('status', 'unknown')]
        invalid += [(field, value) for field in ('created_at', 'started_at', 'ended_at', 'progress_updated_at')
                    for value in ({}, [], '', '2026-11-19T01:00:00', float('nan'), float('inf'))]
        invalid.append(('created_at', None))
        for field, value in invalid:
            turn.clear();turn.update(original);turn[field] = value
            with self.subTest(field=field, value=value), self.assertRaises(ValueError):
                validate_meetings(self.conv)
        turn.clear();turn.update(original)
        turn.update(status='interrupted', error='Original process ended before confirmed final reply')
        view = meeting_view(self.conv, self.meeting)
        self.assertEqual(view['speakers'], [])
        slot = next(s for s in view['slots'] if s['provider'] == 'agy' and s['stage'] == 'proposal')
        self.assertEqual(slot['status'], 'interrupted')
        self.assertEqual(slot['text'], '')
        self.assertNotEqual(view['status'], 'completed')

    def test_native_link_thread_branch_role_and_stage_match(self):
        turn = self.native()
        for change in ({'meeting_id': 'f' * 32}, {'meeting_stage': 'review'}, {'branch': 'art'},
                       {'source': 'console'}, {'roundtable_role': 'host'}, {'kind': 'image'},
                       {'roundtable': dict(turn['roundtable'], host='muse')}):
            original = copy.deepcopy(turn);turn.update(change)
            with self.subTest(change=change), self.assertRaises(ValueError):
                validate_meetings(self.conv)
            turn.clear();turn.update(original)
        self.assertEqual(link_meeting_turn(self.conv, MID, 'proposal', turn, LATER), self.meeting)
        with self.assertRaises(ValueError):
            self.native(index=2)

    def test_barriers_and_terminal_slots_preserve_actual_history(self):
        self.record('muse')
        with self.assertRaises(ValueError): self.record('muse', stage='review')
        self.record('codex')
        self.record('agy', status='unavailable')
        self.record('muse', stage='review')
        with self.assertRaises(ValueError): self.record('codex', stage='summary')
        self.record('agy', stage='review', status='unavailable')
        self.record('codex', stage='review')
        self.record('codex', stage='summary')
        view = meeting_view(self.conv, self.meeting)
        self.assertEqual(view['status'], 'partial')
        self.assertEqual(view['phase'], 'finished')
        self.assertEqual(len(view['speakers']), 5)
        with self.assertRaises(ValueError): self.record('muse', stage='review', text='Replacement')
        summary = meeting_summary(view)
        self.assertNotIn('slots', summary)
        self.assertNotIn('events', summary)
        self.assertNotIn('speakers', summary)

    def test_unavailable_selected_host_does_not_become_codex_summary(self):
        self.conv = conversation('muse', agy=False)
        self.meeting = new_meeting(self.conv, self.request, MID, STAMP)
        self.conv['roundtable_meetings'] = [self.meeting]
        self.record('codex')
        self.record('muse')
        self.record('codex', stage='review')
        self.record('muse', stage='review')
        with self.assertRaises(ValueError): self.record('codex', stage='summary')
        self.record('muse', stage='summary', status='unavailable')
        view = meeting_view(self.conv, self.meeting)
        self.assertEqual(view['status'], 'partial')
        self.assertEqual(view['host'], 'muse')
        self.assertFalse(any(s['stage'] == 'summary' for s in view['speakers']))

    def test_complete_meeting_only_after_all_actual_stage_replies(self):
        self.conv = conversation(agy=False)
        self.meeting = new_meeting(self.conv, self.request, MID, STAMP)
        self.conv['roundtable_meetings'] = [self.meeting]
        for stage in ('proposal', 'review'):
            self.record('codex', stage=stage)
            self.record('muse', stage=stage)
        self.record('codex', stage='summary')
        self.assertEqual(meeting_view(self.conv, self.meeting)['status'], 'completed')

    def test_sending_unknown_never_reverts_to_unattempted(self):
        self.record('muse', status='sending')
        self.record('muse', status='waiting')
        for status in ('sending', 'unavailable'):
            with self.subTest(status=status), self.assertRaises(ValueError):
                self.record('muse', status=status)
        self.record('muse', status='failed', detail='Delivery could not be confirmed; no resend')
        with self.assertRaises(ValueError): self.record('muse', status='sending')

    def test_strict_backup_validation_and_limits_no_automatic_deletion(self):
        self.record('muse')
        for change in ({'codex_thread_id': OTHER}, {'participants': ['codex', {}]},
                       {'updated_at': STAMP}, {'branch': 'missing'}, {'events': []}):
            conv = copy.deepcopy(self.conv);conv['roundtable_meetings'][0].update(change)
            with self.subTest(change=change), self.assertRaises((ValueError, TypeError)):
                validate_meetings(conv)
        conv = copy.deepcopy(self.conv);conv['roundtable_meetings'] *= 2
        with self.assertRaises(ValueError): validate_meetings(conv)
        old = copy.deepcopy(self.conv)
        with patch('multi_ai_collaboration.MEETING_BYTES', 1), self.assertRaises(ValueError):
            record_meeting_event(self.conv, MID, self.event('codex'), LATER)
        self.assertEqual(self.conv, old)
        with patch('multi_ai_collaboration.MEETING_LIMIT', 1), self.assertRaises(ValueError):
            new_meeting(self.conv, dict(self.request, source_id='new'), '2' * 32, LATER)


if __name__ == '__main__':
    unittest.main()
