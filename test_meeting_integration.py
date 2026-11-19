"""Independent meeting integration: isolated profile, no CLI/network/credential use."""
import copy
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import server
from server import Console
from multi_ai_collaboration import meeting_view

THREAD = '11111111-2222-3333-4444-555555555555'
OTHER = '66666666-7777-8888-9999-aaaaaaaaaaaa'
SESSION = 'aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee'
MUSE = 'https://muse.ai/thread/' + OTHER


class MeetingIntegrationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.stub = self.root / 'not-an-executable'
        with patch('server.SoftwareUpdates.start'):
            self.app = Console(self.root / 'data', self.stub, import_history=False,
                               account_reader=lambda *_a, **_k: {'state': 'unknown', 'email': None},
                               coordination_path=self.root / 'coordination.json')
        self.app.activity.root = self.root / 'official'
        bound = self.app.bind_codex(THREAD)
        self.conv = self.app.conversation(bound['id'])
        self.conv.update(agy_id=SESSION, primary_branch='main', branches={
            'main': {'id': 'main', 'title': 'Main', 'agy_id': SESSION},
            'art': {'id': 'art', 'title': 'Art', 'agy_id': None}})
        self.app.set_multi_ai_collaboration(self.conv['id'], {'codex_thread_id': THREAD, 'providers': {'muse': {'enabled': True}}})
        self.app.set_roundtable(self.conv['id'], {'codex_thread_id': THREAD, 'mode': 'roundtable', 'host': 'codex'})
        self.app.health['models'] = [{'id': 'fixture-model'}]
        self.app.state['preferred_model'] = 'fixture-model'
        self.app.save(self.conv)

    def tearDown(self):
        self.app.close()
        self.temp.cleanup()

    def create(self, **patches):
        return self.app.create_meeting(self.conv['id'], dict(codex_thread_id=THREAD, source_id='new-topic', topic='Only this topic', **patches))

    def event(self, mid, provider='muse', stage='proposal', status='completed', source='observed', **patches):
        body = dict(codex_thread_id=THREAD, source_id=source, provider=provider, stage=stage, status=status,
                    text='Actual observed reply' if status == 'completed' else '',
                    source_url=MUSE if provider == 'muse' and status == 'completed' else '', detail='')
        body.update(patches)
        return self.app.record_meeting_event(self.conv['id'], mid, body)

    def test_muse_records_before_any_cli_turn_and_create_event_idempotence(self):
        policy = self.app.coordination.path.read_bytes()
        with patch('subprocess.Popen', side_effect=AssertionError('no model process')):
            one = self.create()
            self.assertFalse(one['model_started'])
            repeated = self.create()
            self.assertTrue(repeated['duplicate'])
            self.assertEqual(one['meeting']['id'], repeated['meeting']['id'])
            mid = one['meeting']['id']
            reply = self.event(mid)
            self.assertTrue(self.event(mid)['duplicate'])
        self.assertEqual(self.conv['turns'], [])
        self.assertEqual(self.app.coordination.path.read_bytes(), policy)
        slot = next(s for s in reply['meeting']['slots'] if s['provider'] == 'muse' and s['stage'] == 'proposal')
        self.assertEqual(slot['text'], 'Actual observed reply')
        self.assertEqual(slot['status'], 'completed')
        self.assertEqual(reply['meeting']['status'], 'active')
        with self.assertRaises(ValueError):
            self.app.create_meeting(self.conv['id'], {'codex_thread_id': THREAD, 'source_id': 'new-topic', 'topic': 'Different'})
        with self.assertRaises(ValueError): self.event(mid, text='Different actual source')

    def test_native_turn_atomic_link_and_idempotent_dispatch(self):
        mid = self.create()['meeting']['id']
        with patch.object(self.app.jobs, 'put') as put:
            turn = self.app.submit(self.conv['id'], 'bounded assignment', 'fixture-model', codex_dispatch=True,
                                   meeting_id=mid, meeting_stage='proposal')
            again = self.app.submit(self.conv['id'], 'bounded assignment', 'fixture-model', codex_dispatch=True,
                                    meeting_id=mid, meeting_stage='proposal')
            self.assertEqual(turn['id'], again['id'])
            self.assertTrue(again['duplicate_submission'])
            self.assertEqual(put.call_count, 1)
            self.assertEqual(len(self.conv['turns']), 1)
            with self.assertRaises(ValueError):
                self.app.submit(self.conv['id'], 'other assignment', 'fixture-model', codex_dispatch=True,
                                meeting_id=mid, meeting_stage='proposal')
        slot = next(s for s in self.app.meeting_records(self.conv['id'], mid)['slots'] if s['provider'] == 'agy' and s['stage'] == 'proposal')
        self.assertEqual(slot['status'], 'queued')
        self.assertEqual(slot['turn_id'], turn['id'])
        turn.update(status='failed', error='real failure', ended_at=server.now())
        slot = next(s for s in self.app.meeting_records(self.conv['id'], mid)['slots'] if s['provider'] == 'agy' and s['stage'] == 'proposal')
        self.assertEqual(slot['status'], 'failed')
        self.assertEqual(slot['text'], '')

    def test_new_native_link_rollback_never_enqueues_on_persistence_error(self):
        mid = self.create()['meeting']['id']
        before = copy.deepcopy(self.conv)
        actual = server.write_json
        attempts = [0]
        def fail_once(path, value):
            attempts[0] += 1
            if attempts[0] == 1: raise OSError('simulated disk error')
            return actual(path, value)
        with patch.object(self.app.jobs, 'put') as put, patch('server.write_json', side_effect=fail_once):
            with self.assertRaises(OSError):
                self.app.submit(self.conv['id'], 'one assignment', 'fixture-model', codex_dispatch=True,
                                meeting_id=mid, meeting_stage='proposal')
        self.assertEqual(self.conv, before)
        put.assert_not_called()

    def test_independent_page_without_turns_branch_isolation_and_summary_no_text(self):
        first = self.create()['meeting']['id']
        self.event(first)
        self.app.create_meeting(self.conv['id'], {'codex_thread_id': THREAD, 'source_id': 'art-topic', 'topic': 'Art only', 'branch': 'art'})
        page = self.app.conversation_page(self.conv['id'], branch='main')
        self.assertEqual(page['turns'], [])
        self.assertEqual([m['id'] for m in page['roundtable_meetings']], [first])
        summary = self.app.snapshot(summaries=True)['conversations'][0]
        self.assertNotIn('roundtable_meetings', summary)
        self.assertNotIn('Actual observed reply', json.dumps(summary))
        self.assertEqual(len(summary['meeting_summaries']), 2)
        other = self.app.bind_codex(OTHER)
        self.assertEqual(self.app.meeting_records(other['id'])['meetings'], [])
        with self.assertRaises(KeyError): self.app.meeting_records(other['id'], first)

    def test_stage_barriers_and_partial_summary_honest_for_missing_participant(self):
        mid = self.create()['meeting']['id']
        with self.assertRaises(ValueError): self.event(mid, stage='review')
        for provider in ['codex', 'muse', 'agy']:
            self.event(mid, provider=provider, source=provider + ':p', status='unavailable', detail='No actual call made')
        for provider in ['codex', 'muse', 'agy']:
            self.event(mid, provider=provider, stage='review', source=provider + ':r', status='unavailable', detail='No actual review available')
        result = self.event(mid, provider='codex', stage='summary', source='host:summary', text='No complete meeting occurred.')
        self.assertEqual(result['meeting']['status'], 'partial')
        self.assertNotEqual(result['meeting']['status'], 'completed')

    def test_observed_write_rollback_and_paused_new_dispatch(self):
        mid = self.create()['meeting']['id']
        original = copy.deepcopy(self.conv)
        with patch('server.write_json', side_effect=OSError('simulated failure')):
            with self.assertRaises(OSError): self.event(mid)
        self.assertEqual(self.conv, original)
        self.app.set_multi_ai_collaboration(self.conv['id'], {'codex_thread_id': THREAD, 'providers': {'muse': {'enabled': False}}})
        with self.assertRaises(ValueError): self.event(mid)
        self.app.set_multi_ai_collaboration(self.conv['id'], {'codex_thread_id': THREAD, 'enabled': False})
        with self.assertRaises(ValueError):
            self.app.submit(self.conv['id'], 'blocked', 'fixture-model', codex_dispatch=True, meeting_id=mid, meeting_stage='proposal')


if __name__ == '__main__': unittest.main()
