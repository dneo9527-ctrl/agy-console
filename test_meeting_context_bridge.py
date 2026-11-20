"""Stage context CLI contract: API doubles only, no live model or profile."""
import contextlib
import copy
import io
import json
import sys
import unittest
from unittest import mock

import bridge
from test_multi_ai_bridge import THREAD, OTHER, CID
from test_roundtable_meeting_bridge import MeetingClient, MID, BASE


class ContextClient(MeetingClient):
    def __init__(self, supported=True):
        super().__init__()
        self.features['roundtable_context'] = supported
        self.snapshot = {'schema_version': 1, 'meeting_id': MID,
                         'codex_thread_id': THREAD, 'branch': 'main',
                         'stage': 'review', 'provider': 'muse',
                         'text': 'Only actual preceding-stage evidence.',
                         'chars': 37, 'source_chars': 37, 'truncated': False}

    def request(self, path, body=None):
        if '/context?' in path:
            self.calls.append((path, copy.deepcopy(body)))
            return copy.deepcopy(self.snapshot)
        return super().request(path, body)


class MeetingContextBridgeTests(unittest.TestCase):
    def invoke(self, client, *flags):
        output = io.StringIO()
        with mock.patch.object(sys, 'argv', ['bridge.py', '--thread-id', THREAD, *flags]), \
                mock.patch.object(bridge, 'Client', return_value=client) as factory, \
                contextlib.redirect_stdout(output), contextlib.redirect_stderr(io.StringIO()):
            code = bridge.main()
        return code, [json.loads(line) for line in output.getvalue().splitlines()], factory

    @staticmethod
    def context_flags(stage='review', provider='muse'):
        return ['--meeting-context', MID, '--meeting-stage', stage,
                '--meeting-provider', provider]

    def test_read_exact_meeting_is_one_get_without_dispatch_or_presentation(self):
        client = ContextClient()
        with mock.patch('subprocess.Popen', side_effect=AssertionError('no process launch')), \
                mock.patch.object(client, 'wait', side_effect=AssertionError('no model wait'), create=True):
            code, output, _ = self.invoke(client, *self.context_flags())
        self.assertEqual(code, 0)
        self.assertEqual(output, [client.snapshot])
        self.assertEqual(client.bindings, [(THREAD, None, {})])
        self.assertEqual(client.calls, [(BASE + '/' + MID + '/context?stage=review&provider=muse', None)])
        self.assertNotIn('panel_action', output[0])
        self.assertNotIn('submitted', output[0])

    def test_all_provider_stage_choices_route_explicitly_without_changing_preferences(self):
        for stage, providers in [('proposal', ('codex', 'agy', 'muse', 'claude')),
                                 ('review', ('codex', 'agy', 'muse', 'claude')),
                                 ('summary', ('codex',))]:
            for provider in providers:
                with self.subTest(stage=stage, provider=provider):
                    client = ContextClient()
                    client.snapshot.update(stage=stage, provider=provider)
                    client.conv['multi_ai_collaboration']['enabled'] = False
                    before = copy.deepcopy(client.conv)
                    self.invoke(client, *self.context_flags(stage, provider))
                    self.assertEqual(client.calls, [(BASE + '/' + MID + '/context?stage=' + stage + '&provider=' + provider, None)])
                    self.assertEqual(client.conv, before, 'read does not restore paused collaboration')

    def test_invalid_local_flags_are_rejected_before_client_or_binding(self):
        base = self.context_flags()
        invalid = [
            ['--meeting-context', MID],
            ['--meeting-context', MID, '--meeting-stage', 'review'],
            ['--meeting-context', MID, '--meeting-provider', 'muse'],
            ['--meeting-context', 'not-an-id', '--meeting-stage', 'review', '--meeting-provider', 'muse'],
            ['--meeting-context', MID, '--meeting-stage', 'other', '--meeting-provider', 'muse'],
            ['--meeting-context', MID, '--meeting-stage', 'review', '--meeting-provider', 'other'],
            ['--meeting-provider', 'muse'],
            ['--meeting-provider', 'muse', '--ensure'],
        ]
        invalid.extend(base + flags for flags in [
            ['--ensure'], ['--task-file', '/not-read'], ['--context', '/not-read'],
            ['--meeting-view', MID], ['--meeting-create', '/not-read'],
            ['--meeting-event', '/not-read'], ['--meeting', MID],
            ['--review-turn', 'c' * 32, '--review-file', '/not-read'],
            ['--record-message', '/not-read'], ['--import-result', '/not-read'],
            ['--stage-import'], ['--model', 'never-changed'], ['--kind', 'image'],
            ['--branch', 'main'], ['--branch-title', 'never-created'],
            ['--title', 'never-written'], ['--out', '/never-created'], ['--no-wait'],
            ['--collaboration', 'on'], ['--auto-open', 'on'], ['--auto-start', 'on'],
            ['--host', 'agy'], ['--collaboration-mode', 'roundtable'],
            ['--provider', 'muse', '--provider-collaboration', 'on'], ['--timeout', '1'],
        ])
        for flags in invalid:
            with self.subTest(flags=flags):
                client = ContextClient()
                with mock.patch.object(bridge, 'Client', return_value=client) as factory, \
                        mock.patch.object(sys, 'argv', ['bridge.py', '--thread-id', THREAD, *flags]), \
                        contextlib.redirect_stderr(io.StringIO()), self.assertRaises(SystemExit):
                    bridge.main()
                factory.assert_not_called()
                self.assertEqual(client.bindings, [])
                self.assertEqual(client.calls, [])

    def test_missing_feature_requires_foreground_update_without_context_request(self):
        for supported in [False, None, 'true', 1]:
            with self.subTest(supported=supported):
                client = ContextClient(supported)
                with self.assertRaisesRegex(ValueError, '原前台.*新版'):
                    self.invoke(client, *self.context_flags())
                self.assertEqual(client.bindings, [(THREAD, None, {})])
                self.assertEqual(client.calls, [])

    def test_bound_chat_and_cid_are_checked_before_read(self):
        for changes in [{'codex_thread_id': OTHER}, {'id': '../other'}, {'id': ''}]:
            with self.subTest(changes=changes):
                client = ContextClient()
                client.conv.update(changes)
                with self.assertRaisesRegex(ValueError, '当前.*聊天'):
                    self.invoke(client, *self.context_flags())
                self.assertEqual(client.calls, [], 'unverified binding cannot choose another context target')

    def test_response_identity_and_stage_are_checked_before_printing(self):
        for changes in [{'codex_thread_id': OTHER}, {'meeting_id': 'e' * 32},
                        {'stage': 'proposal'}, {'provider': 'agy'}, {'branch': '../other'},
                        {'schema_version': 2}, {'schema_version': True}, {'text': None}]:
            with self.subTest(changes=changes):
                client = ContextClient()
                client.snapshot.update(changes)
                with self.assertRaisesRegex(ValueError, '上下文.*不匹配'):
                    self.invoke(client, *self.context_flags())
                self.assertEqual(len(client.calls), 1, 'read is not retried with a different identity')

    def test_ensure_advertises_context_capability_without_reading_context(self):
        for supported in [True, False]:
            client = ContextClient(supported)
            code, output, _ = self.invoke(client, '--ensure')
            self.assertEqual(code, 0)
            self.assertIs(output[0]['roundtable_context_supported'], supported)
            self.assertEqual(client.calls, [])
            self.assertEqual(client.bindings, [(THREAD, None, {})])


if __name__ == '__main__':
    unittest.main()
