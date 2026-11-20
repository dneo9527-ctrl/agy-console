"""Workspace/preset bridge contracts, isolated API doubles without model calls."""
import contextlib
import copy
import io
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest import mock

import bridge
from multi_ai_collaboration import roundtable_presets
from test_multi_ai_bridge import THREAD, OTHER, CID, policy
from test_roundtable_meeting_bridge import MeetingClient, MID, BASE

PREF_PATH = '/api/conversations/' + CID + '/roundtable-workspace-preferences'


def preferences(preset='general'):
    return {'schema_version': 1, 'codex_thread_id': THREAD,
            'preset': preset, 'deduplicate_exact': True}


class WorkspaceClient(MeetingClient):
    def __init__(self, supported=True, controls=None):
        super().__init__(controls=controls)
        self.features.update(roundtable_workspace=supported, roundtable_presets=supported,
                             roundtable_decisions=supported)
        self.conv.update(roundtable_workspace_preferences=preferences(),
                         roundtable_presets=roundtable_presets())
        self.workspace = {'schema_version': 1, 'meeting_id': MID, 'codex_thread_id': THREAD,
                          'branch': 'main', 'materials': [], 'contexts': [],
                          'usage': {'unknown_providers': ['codex', 'muse']},
                          'decisions': {'status': 'not_recorded'}}

    def request(self, path, body=None):
        if path == PREF_PATH:
            self.calls.append((path, copy.deepcopy(body)))
            if path == self.fail_path:
                raise ValueError('isolated preference save failed')
            self.conv['roundtable_workspace_preferences']['preset'] = body['preset']
            return copy.deepcopy(self.conv['roundtable_workspace_preferences'])
        if path == BASE + '/' + MID + '/workspace':
            self.calls.append((path, copy.deepcopy(body)))
            return copy.deepcopy(self.workspace)
        return super().request(path, body)


class WorkspaceBridgeTests(unittest.TestCase):
    def invoke(self, client, *flags):
        stdout = io.StringIO()
        with mock.patch.object(sys, 'argv', ['bridge.py', '--thread-id', THREAD, *flags]), \
                mock.patch.object(bridge, 'Client', return_value=client) as factory, \
                contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(io.StringIO()):
            code = bridge.main()
        return code, [json.loads(line) for line in stdout.getvalue().splitlines()], factory

    def test_workspace_exact_read_no_mutation_dispatch_wait_or_presentation(self):
        client = WorkspaceClient(controls=policy(enabled=False, agy=False, muse=True))
        before = copy.deepcopy(client.conv)
        with mock.patch('subprocess.Popen', side_effect=AssertionError('no launch')), \
                mock.patch.object(client, 'wait', side_effect=AssertionError('no wait'), create=True):
            code, output, _ = self.invoke(client, '--meeting-workspace', MID)
        self.assertEqual(code, 0)
        self.assertEqual(output, [client.workspace])
        self.assertEqual(client.calls, [(BASE + '/' + MID + '/workspace', None)])
        self.assertEqual(client.bindings, [(THREAD, None, {})])
        self.assertEqual(client.conv, before)
        self.assertNotIn('panel_action', output[0])

    def test_workspace_requires_exact_advertised_support_before_get(self):
        for supported in [False, None, 1, 'true']:
            client = WorkspaceClient(supported)
            with self.subTest(supported=supported), self.assertRaisesRegex(ValueError, '原前台'):
                self.invoke(client, '--meeting-workspace', MID)
            self.assertEqual(client.calls, [])

    def test_workspace_identity_failure_never_reads_other_binding(self):
        for changes in [{'codex_thread_id': OTHER}, {'id': '../another'}, {'id': None}]:
            client = WorkspaceClient(); client.conv.update(changes)
            with self.subTest(changes=changes), self.assertRaisesRegex(ValueError, '确切聊天'):
                self.invoke(client, '--meeting-workspace', MID)
            self.assertEqual(client.calls, [])

    def test_workspace_bad_response_is_not_printed_or_retried(self):
        for changes in [{'codex_thread_id': OTHER}, {'meeting_id': 'e' * 32},
                        {'schema_version': True}, {'schema_version': 2},
                        {'branch': '../other'}, {'materials': None}, {'contexts': {}},
                        {'usage': None}, {'decisions': []}]:
            client = WorkspaceClient(); client.workspace.update(changes)
            with self.subTest(changes=changes), self.assertRaisesRegex(ValueError, '会议材料.*不匹配'):
                self.invoke(client, '--meeting-workspace', MID)
            self.assertEqual(len(client.calls), 1)

    def test_invalid_workspace_flags_do_not_construct_client(self):
        combinations = [['--meeting-workspace', 'bad'], ['--roundtable-preset', 'general'],
                        ['--ensure', '--roundtable-preset', 'unknown']]
        combinations += [['--meeting-workspace', MID, *extra] for extra in [
            ['--ensure'], ['--task-file', '/not-read'], ['--model', 'not-used'],
            ['--branch', 'main'], ['--kind', 'image'], ['--out', '/not-created'],
            ['--meeting-view', MID], ['--meeting-context', MID],
            ['--meeting-stage', 'review'], ['--meeting-provider', 'muse'],
            ['--title', 'not-saved'], ['--timeout', '1'], ['--no-wait'],
            ['--context', '/not-read'], ['--collaboration', 'on'],
            ['--roundtable-preset', 'code_review']]]
        for flags in combinations:
            client = WorkspaceClient()
            with self.subTest(flags=flags), \
                    mock.patch.object(bridge, 'Client', return_value=client) as factory, \
                    mock.patch.object(sys, 'argv', ['bridge.py', '--thread-id', THREAD, *flags]), \
                    contextlib.redirect_stderr(io.StringIO()), self.assertRaises(SystemExit):
                bridge.main()
            factory.assert_not_called()
            self.assertEqual(client.calls, [])

    def test_ensure_exposes_verified_defaults_without_lazy_material_request(self):
        client = WorkspaceClient()
        code, values, _ = self.invoke(client, '--ensure')
        self.assertEqual(code, 0)
        value = values[0]
        self.assertIs(value['roundtable_workspace_supported'], True)
        self.assertIs(value['roundtable_presets_supported'], True)
        self.assertIs(value['roundtable_decisions_supported'], True)
        self.assertEqual(value['roundtable_workspace_preferences'], preferences())
        self.assertEqual(value['roundtable_presets'], roundtable_presets())
        self.assertFalse(value['model_started'])
        self.assertEqual(client.calls, [])

    def test_ensure_old_capability_does_not_invent_preferences(self):
        client = WorkspaceClient(False)
        client.conv.update(roundtable_workspace_preferences={'bad': True}, roundtable_presets=None)
        _, values, _ = self.invoke(client, '--ensure')
        self.assertIs(values[0]['roundtable_workspace_supported'], False)
        self.assertNotIn('roundtable_workspace_preferences', values[0])
        self.assertNotIn('roundtable_presets', values[0])
        self.assertEqual(client.calls, [])

    def test_ensure_rejects_advertised_invalid_catalog_and_preferences(self):
        for key, value in [('roundtable_workspace_preferences', None),
                           ('roundtable_workspace_preferences', preferences() | {'codex_thread_id': OTHER}),
                           ('roundtable_workspace_preferences', preferences() | {'deduplicate_exact': 1}),
                           ('roundtable_presets', []), ('roundtable_presets', roundtable_presets()[::-1]),
                           ('roundtable_presets', [dict(item, unknown=True) for item in roundtable_presets()])]:
            client = WorkspaceClient(); client.conv[key] = value
            with self.subTest(key=key, value=value), self.assertRaises(ValueError):
                self.invoke(client, '--ensure')
            self.assertEqual(client.calls, [])

    def test_preset_save_changes_only_new_meeting_guidance_and_preserves_pause(self):
        for preset in ('general', 'code_review', 'paper_review'):
            client = WorkspaceClient(controls=policy(enabled=False, agy=False, muse=True))
            client.conv.update(model_preferences={'text': 'saved-model'}, auto_open_activity=False)
            before = copy.deepcopy(client.conv)
            _, values, _ = self.invoke(client, '--ensure', '--roundtable-preset', preset)
            self.assertEqual(client.calls, [(PREF_PATH, {'codex_thread_id': THREAD, 'preset': preset})])
            before['roundtable_workspace_preferences']['preset'] = preset
            self.assertEqual(client.conv, before)
            self.assertEqual(values[0]['roundtable_workspace_preferences'], preferences(preset))
            self.assertFalse(values[0]['multi_ai_collaboration']['enabled'])
            self.assertFalse(values[0]['model_started'])
            self.assertEqual(len(client.bindings), 2)

    def test_preset_requires_both_capabilities_and_valid_binding_before_write(self):
        for change in [{'roundtable_presets': False}, {'roundtable_workspace': False}]:
            client = WorkspaceClient(); client.features.update(change)
            with self.subTest(change=change), self.assertRaisesRegex(ValueError, '原前台'):
                self.invoke(client, '--ensure', '--roundtable-preset', 'code_review')
            self.assertEqual(client.calls, [])
        client = WorkspaceClient(); client.conv['codex_thread_id'] = OTHER
        with self.assertRaisesRegex(ValueError, '确切聊天'):
            self.invoke(client, '--ensure', '--roundtable-preset', 'code_review')
        self.assertEqual(client.calls, [])

    def test_preset_write_failure_is_not_claimed_success_or_retried(self):
        client = WorkspaceClient(); client.fail_path = PREF_PATH
        with self.assertRaisesRegex(ValueError, 'save failed'):
            self.invoke(client, '--ensure', '--roundtable-preset', 'code_review')
        self.assertEqual(len(client.calls), 1)
        self.assertEqual(len(client.bindings), 1)

    def test_structured_summary_event_transports_original_source_references(self):
        body = {'meeting_id': MID, 'codex_thread_id': THREAD, 'source_id': 'host:summary',
                'provider': 'codex', 'stage': 'summary', 'status': 'completed', 'text': 'Host recommendation.',
                'decisions': {'schema_version': 1,
                              'suggested_adoption': [{'text': 'Suggested adjustment.',
                                  'sources': [{'source_id': 'actual:muse:proposal', 'provider': 'muse', 'stage': 'proposal'}]}],
                              'disagreements': [], 'needs_verification': [], 'next_steps': []}}
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / 'summary.json'; path.write_text(json.dumps(body), encoding='utf-8')
            client = WorkspaceClient()
            _, values, _ = self.invoke(client, '--meeting-event', str(path))
            expected = copy.deepcopy(body); del expected['meeting_id']
            self.assertEqual(client.calls, [(BASE + '/' + MID + '/events', expected)])
            self.assertTrue(values[0]['recorded'])
            self.assertEqual(json.loads(path.read_text()), body)
            old = WorkspaceClient(False)
            with self.assertRaisesRegex(ValueError, '结构化主持'):
                self.invoke(old, '--meeting-event', str(path))
            self.assertEqual(old.calls, [], 'never strip decisions to force an older backend to accept the summary')

    def test_explicit_new_meeting_preset_needs_feature_but_does_not_edit_future_default(self):
        body = {'codex_thread_id': THREAD, 'source_id': 'one-topic', 'topic': 'Review code',
                'branch': 'main', 'preset': 'code_review'}
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / 'create.json'; path.write_text(json.dumps(body), encoding='utf-8')
            client = WorkspaceClient(); before = copy.deepcopy(client.conv)
            self.invoke(client, '--meeting-create', str(path))
            self.assertEqual(client.calls, [(BASE, body)])
            self.assertEqual(client.conv, before)
            old = WorkspaceClient(False)
            with self.assertRaisesRegex(ValueError, '预设.*原前台'):
                self.invoke(old, '--meeting-create', str(path))
            self.assertEqual(old.calls, [])


if __name__ == '__main__':
    unittest.main()
