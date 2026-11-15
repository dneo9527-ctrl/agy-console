import json
import unittest

from task_progress import finish_progress, record, step_progress


class ProgressTests(unittest.TestCase):
    def test_same_step_merges_and_finishes_without_payloads(self):
        turn = {'status': 'running'}
        for state in ('ACTIVE', 'ACTIVE', 'DONE'):
            step_progress(turn, {'step_index': 7, 'step_type': 'tool', 'tool_name': 'read_file',
                                 'state': state, 'tool_args': 'secret-argument', 'text_delta': 'private-reasoning'})
        self.assertEqual(len(turn['progress']), 1)
        self.assertEqual(turn['progress'][0]['state'], 'done')
        self.assertIn('read_file', turn['progress'][0]['label'])
        self.assertNotIn('secret-argument', json.dumps(turn))
        self.assertNotIn('private-reasoning', json.dumps(turn))

    def test_bound_preserves_first_and_recent_steps(self):
        turn = {}
        record(turn, 'start', '开始')
        for i in range(100):
            step_progress(turn, {'step_index': i, 'step_type': 'tool', 'state': 'DONE'})
        self.assertEqual(len(turn['progress']), 32)
        self.assertEqual(turn['progress'][0]['key'], 'start')
        self.assertEqual(turn['progress'][-1]['key'], 'step:99')
        self.assertEqual(turn['progress_omitted'], 69)

    def test_terminal_states_never_leave_steps_running(self):
        for status in ('completed', 'failed', 'cancelled', 'interrupted'):
            with self.subTest(status=status):
                turn = {'status': status, 'ended_at': 'known-end'}
                record(turn, 'tool', '使用工具')
                finish_progress(turn)
                self.assertFalse(any(r['state'] == 'active' for r in turn['progress']))
                self.assertEqual(turn['progress_updated_at'], 'known-end')
                self.assertEqual(turn['progress'][0]['state'], 'ended' if status == 'completed' else 'stopped')

    def test_unknown_states_and_unindexed_streams_stay_bounded(self):
        turn = {}
        for i in range(100):
            step_progress(turn, {'step_type': 'agent_response', 'state': 'NEW_ENUM', 'text_delta': str(i)})
        self.assertEqual(len(turn['progress']), 1)
        self.assertEqual(turn['progress'][0]['state'], 'active')
        step_progress(turn, {'step_index': 8, 'step_type': 'tool', 'tool_name': 'command with secret'})
        self.assertNotIn('secret', json.dumps(turn))

    def test_legacy_events_are_not_given_invented_times(self):
        turn = {'events': ['旧步骤'], 'status': 'interrupted', 'ended_at': 'known-end'}
        finish_progress(turn)
        self.assertEqual(turn['events'], ['旧步骤'])
        self.assertEqual(len(turn['progress']), 1)
        self.assertEqual(turn['progress'][0]['key'], 'outcome')
