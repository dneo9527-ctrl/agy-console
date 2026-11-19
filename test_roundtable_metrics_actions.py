"""Truthful meeting metrics and explicit unsent-only closure, isolated profiles."""
import copy
from datetime import datetime, timezone
import json
import unittest
from unittest.mock import patch

from multi_ai_collaboration import meeting_view
import test_roundtable_meetings as domain_fixtures
import test_roundtable_meeting_http as http_fixtures

STAMP, LATER = domain_fixtures.STAMP, domain_fixtures.LATER
THREAD, OTHER, MUSE = http_fixtures.THREAD, http_fixtures.OTHER, http_fixtures.MUSE


class MeetingMetricsTests(unittest.TestCase):
    def setUp(self):
        self.case = domain_fixtures.IndependentMeetingsTests('test_muse_proposal_immediately_without_any_agy_turn')
        self.case.setUp()

    def test_wallclock_uses_whole_meeting_not_sum_of_parallel_participants(self):
        case = self.case
        case.record('muse', status='sending')
        # Freeze only the wall clock; parse real persisted ISO timestamps.
        class Clock(datetime):
            @classmethod
            def now(cls, tz=None):
                return datetime(2026, 11, 19, 1, 2, tzinfo=timezone.utc)
        with patch('multi_ai_collaboration.datetime', Clock):
            view = meeting_view(case.conv, case.meeting)
        self.assertEqual(view['metrics']['wall_seconds'], 120)
        self.assertIsNone(view['metrics']['ended_at'])
        muse = next(s for s in view['slots'] if s['provider'] == 'muse' and s['stage'] == 'proposal')
        self.assertEqual(muse['wait_seconds'], 60)
        self.assertIsNone(muse['usage'])
        self.assertIsNone(muse['model'])
        self.assertIsNone(view['metrics']['usage'])
        self.assertIn('muse', view['metrics']['unknown_providers'])
        self.assertFalse(view['metrics']['usage_complete'])

    def test_waiting_clock_does_not_invalidate_transcript_revision(self):
        case = self.case
        case.record('muse', status='sending')
        class Clock(datetime):
            minute = 2
            @classmethod
            def now(cls, tz=None):
                return datetime(2026, 11, 19, 1, cls.minute, tzinfo=timezone.utc)
        with patch('multi_ai_collaboration.datetime', Clock):
            first = meeting_view(case.conv, case.meeting)
            Clock.minute = 3
            second = meeting_view(case.conv, case.meeting)
        self.assertEqual(first['revision'], second['revision'])
        self.assertEqual(second['metrics']['wall_seconds'] - first['metrics']['wall_seconds'], 60)
        case.record('muse', text='Actual final reply')
        self.assertNotEqual(second['revision'], meeting_view(case.conv, case.meeting)['revision'])

    def test_native_counters_keep_official_total_without_double_adding_thinking(self):
        case = self.case
        turn = case.native(status='completed', model='fixture-model', started_at=STAMP, ended_at=LATER,
                           usage={'input_tokens': 100, 'output_tokens': 20, 'thinking_tokens': 7,
                                  'cache_read_tokens': 5, 'total_tokens': 120}, usage_scope='turn')
        view = meeting_view(case.conv, case.meeting)
        metrics = view['metrics']
        self.assertEqual(metrics['usage']['total_tokens'], 120)
        self.assertEqual(metrics['usage']['thinking_tokens'], 7)
        self.assertEqual(metrics['known_turn_count'], 1)
        self.assertEqual(metrics['unmeasured_turn_count'], 0)
        self.assertTrue(metrics['usage_complete'])
        native = next(s for s in view['slots'] if s['turn_id'] == turn['id'])
        self.assertEqual(native['model'], 'fixture-model')
        self.assertEqual(native['usage'], turn['usage'])
        self.assertFalse(native['can_mark_unavailable'])
        self.assertTrue(next(s for s in view['slots'] if s['provider'] == 'muse' and s['stage'] == 'proposal')['can_mark_unavailable'])
        self.assertFalse(any(s['can_mark_unavailable'] for s in view['slots'] if s['stage'] != 'proposal'))

    def test_conversation_scoped_or_invalid_counters_never_become_a_meeting_total(self):
        case = self.case
        turn = case.native(status='completed', started_at=STAMP, ended_at=LATER,
                           usage={'total_tokens': 999}, usage_scope='conversation')
        view = meeting_view(case.conv, case.meeting)
        self.assertIsNone(view['metrics']['usage'])
        self.assertEqual(view['metrics']['known_turn_count'], 0)
        self.assertEqual(view['metrics']['unmeasured_turn_count'], 1)
        turn.update(usage_scope='turn', usage={'total_tokens': float('inf'), 'input_tokens': True,
                                             'output_tokens': -1, 'thinking_tokens': float('nan')})
        view = meeting_view(case.conv, case.meeting)
        self.assertIsNone(view['metrics']['usage'])
        json.dumps(view, allow_nan=False)

    def test_completed_wallclock_and_observed_wait_stop_at_actual_terminal_times(self):
        case = self.case
        case.record('muse', status='sending')
        case.record('muse', text='Actual observed final')
        for provider in ('codex', 'agy'):
            case.record(provider, status='unavailable')
        for provider in ('codex', 'agy', 'muse'):
            case.record(provider, stage='review', status='unavailable')
        case.record('codex', stage='summary', text='Partial result explicitly summarized by actual host')
        view = meeting_view(case.conv, case.meeting)
        self.assertEqual(view['status'], 'partial')
        self.assertEqual(view['metrics']['wall_seconds'], 60)
        self.assertEqual(view['metrics']['ended_at'], LATER)
        muse = next(s for s in view['slots'] if s['provider'] == 'muse' and s['stage'] == 'proposal')
        self.assertEqual(muse['wait_seconds'], 0)
        self.assertFalse(any(s['can_mark_unavailable'] for s in view['slots']))


class MeetingClosureHTTPTests(unittest.TestCase):
    def setUp(self):
        self.case = http_fixtures.IndependentMeetingHTTPTests('test_endpoints_record_muse_before_cli_and_native_duplicate_enqueues_once')
        self.case.setUp()
        self.mid = self.case.create()

    def tearDown(self):
        self.case.tearDown()
        self.case.doCleanups()

    def action(self, **changes):
        body = {'codex_thread_id': THREAD, 'action': 'mark_unavailable', 'provider': 'muse',
                'stage': 'proposal', 'source_id': 'ui:unsent:muse:proposal',
                'reason': 'Browser is unavailable; this stage has never been sent', 'acknowledge': True}
        body.update(changes)
        return self.case.request('POST', self.case.base + '/meetings/' + self.mid + '/actions', body)

    def test_explicit_pending_unavailable_is_idempotent_and_never_calls_a_model(self):
        case = self.case
        original_policy = case.app.coordination.path.read_bytes()
        case.app.set_multi_ai_collaboration(case.cid, {'codex_thread_id': THREAD, 'enabled': False})
        paused_policy = case.app.coordination.path.read_bytes()
        with patch('subprocess.Popen', side_effect=AssertionError('no model')), patch.object(case.app.jobs, 'put') as enqueue:
            status, first = self.action()
            self.assertEqual(status, 200, first)
            self.assertFalse(first['model_started'])
            self.assertFalse(first['summary_ready'])
            status, repeated = self.action()
            self.assertEqual(status, 200, repeated)
            self.assertTrue(repeated['duplicate'])
            self.assertEqual(len(case.conv['roundtable_meetings'][0]['events']), 1)
            self.assertEqual(self.action(reason='Different reason')[0], 409)
            enqueue.assert_not_called()
        self.assertNotEqual(original_policy, paused_policy)
        self.assertEqual(case.app.coordination.path.read_bytes(), paused_policy)
        self.assertEqual(case.conv['turns'], [])

    def test_confirmation_reason_identity_and_stage_barrier_are_required(self):
        case = self.case
        before = case.app.file.read_bytes()
        for changes in ({'acknowledge': False}, {'acknowledge': 1}, {'reason': ''}, {'reason': '  '},
                        {'reason': 'x' * 1001}, {'codex_thread_id': OTHER}, {'provider': []},
                        {'stage': {}}, {'source_id': 'bad source'}, {'unexpected': True}):
            with self.subTest(changes=changes):
                self.assertEqual(self.action(**changes)[0], 400)
                self.assertEqual(case.app.file.read_bytes(), before)
        self.assertEqual(self.action(stage='review', source_id='ui:premature')[0], 409)
        self.assertEqual(case.app.file.read_bytes(), before)

    def test_sent_browser_and_linked_native_never_become_unsent_unavailable(self):
        case = self.case
        self.assertEqual(case.event(self.mid, status='sending', text='', source_url=MUSE)[0], 200)
        before = case.app.file.read_bytes()
        self.assertEqual(self.action()[0], 409)
        self.assertEqual(case.app.file.read_bytes(), before)
        with patch.object(case.app.jobs, 'put'):
            self.assertEqual(case.native(self.mid)[0], 202)
        self.assertEqual(self.action(provider='agy', source_id='ui:agy:skip')[0], 409)
        case.conv['turns'][0].update(status='running', started_at=case.conv['turns'][0]['created_at'])
        self.assertEqual(self.action(provider='agy', source_id='ui:agy:skip')[0], 409)

    def test_inspection_is_readonly_honest_about_web_and_native_and_exact(self):
        case = self.case
        self.assertEqual(case.event(self.mid, status='sending', text='', source_url=MUSE)[0], 200)
        with patch.object(case.app.jobs, 'put'):
            status, native = case.native(self.mid)
        self.assertEqual(status, 202, native)
        before = case.app.file.read_bytes()
        for provider, kind in (('muse', 'codex_observed'), ('agy', 'native_cli'), ('codex', 'not_sent')):
            status, value = case.request('POST', case.base + '/meetings/' + self.mid + '/actions', {
                'codex_thread_id': THREAD, 'action': 'inspect_result', 'provider': provider, 'stage': 'proposal'})
            self.assertEqual(status, 200, value)
            self.assertFalse(value['model_started'])
            self.assertEqual(value['inspection']['source_kind'], kind)
            if provider == 'muse': self.assertEqual(value['inspection']['source_url'], MUSE)
            if provider == 'agy': self.assertEqual(value['inspection']['turn_id'], native['id'])
        self.assertEqual(case.app.file.read_bytes(), before)

    def test_failed_persistence_rolls_back_and_service_busy_blocks_action(self):
        case = self.case
        original = copy.deepcopy(case.conv)
        before = case.app.file.read_bytes()
        with patch('server.write_json', side_effect=OSError('simulated storage failure')):
            self.assertEqual(self.action()[0], 400)
        self.assertEqual(case.conv, original)
        self.assertEqual(case.app.file.read_bytes(), before)
        case.app.maintenance = True
        try:
            self.assertEqual(self.action()[0], 409)
        finally:
            case.app.maintenance = False

    def test_explicit_absence_unlocks_only_frozen_host_partial_summary_after_both_rounds(self):
        case = self.case
        for stage in ('proposal', 'review'):
            for provider in ('codex', 'agy', 'muse'):
                status, value = self.action(provider=provider, stage=stage, source_id='ui:' + provider + ':' + stage)
                self.assertEqual(status, 200, value)
        self.assertTrue(value['summary_ready'])
        self.assertEqual(case.event(self.mid, provider='muse', stage='summary', source_id='wrong:host')[0], 400)
        status, value = case.event(self.mid, provider='codex', stage='summary', source_id='actual:host:summary',
                                   text='Actual Codex summary: all other stages explicitly unavailable', source_url='')
        self.assertEqual(status, 200, value)
        self.assertEqual(value['meeting']['status'], 'partial')


if __name__ == '__main__':
    unittest.main()
