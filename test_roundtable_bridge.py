"""Roundtable bridge contract; isolated fake client and disposable HTTP only."""
import contextlib
import copy
import io
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import threading
import unittest
from unittest import mock

import bridge
import test_multi_ai_bridge as existing

THREAD = existing.THREAD
OTHER = existing.OTHER
CID = existing.CID


def settings(mode='assist', host='codex', thread=THREAD):
    return {'schema_version': 1, 'codex_thread_id': thread,
            'mode': mode, 'host': host, 'rounds': 2}


def plan(value, policy):
    participants = ['codex'] + [p for p in ('agy', 'muse', 'claude')
                                if policy['providers'][p]['enabled']]
    reason = ('assist_mode' if value['mode'] == 'assist' else
              'master_paused' if not policy['enabled'] else
              'host_disabled' if value['host'] not in participants else
              'no_external_participant' if len(participants) == 1 else 'ready')
    return dict(value, participants=participants, ready=reason == 'ready', reason=reason,
                execution='active_codex_skill',
                steps=['independent_proposals', 'peer_review', 'host_summary']
                      if value['mode'] == 'roundtable' else [])


class RoundtableClient(existing.FakeClient):
    def __init__(self, *, modern=True, value=None, policy=None):
        super().__init__(value=policy)
        if modern:
            self.features['roundtable_host'] = True
            self.conv['roundtable'] = copy.deepcopy(value or settings())
            self.conv['roundtable_plan'] = plan(self.conv['roundtable'], self.conv['multi_ai_collaboration'])

    def request(self, path, body=None):
        if path.endswith('/roundtable'):
            self.calls.append((path, copy.deepcopy(body)))
            if path == self.fail_path:
                raise ValueError('isolated roundtable save failed')
            assert body['codex_thread_id'] == THREAD
            self.conv['roundtable'].update({k: v for k, v in body.items()
                                            if k in ('host', 'mode', 'rounds')})
            self.conv['roundtable_plan'] = plan(self.conv['roundtable'], self.conv['multi_ai_collaboration'])
            return copy.deepcopy(self.conv['roundtable'])
        result = super().request(path, body)
        if path.endswith('/multi-ai-collaboration') or path.endswith('/collaboration'):
            if 'roundtable' in self.conv:
                self.conv['roundtable_plan'] = plan(self.conv['roundtable'], self.conv['multi_ai_collaboration'])
        if path.endswith('/turns') and 'roundtable' in self.conv:
            result['roundtable'] = copy.deepcopy(self.conv['roundtable'])
        return result


class RoundtableBridgeTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.task = Path(self.tmp.name) / 'task.txt'
        self.task.write_text('Read-only synthetic roundtable task.', encoding='utf-8')

    def tearDown(self):
        self.tmp.cleanup()

    def invoke(self, client, *args):
        stdout = io.StringIO()
        stderr = io.StringIO()
        argv = ['bridge.py', '--thread-id', THREAD, '--url', client.url, *args]
        with mock.patch.object(sys, 'argv', argv), mock.patch.object(bridge, 'Client', return_value=client), \
                contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
            status = bridge.main()
        return status, [json.loads(line) for line in stdout.getvalue().splitlines()], stderr.getvalue()

    def task_args(self):
        return ['--task-file', str(self.task), '--model', 'synthetic-model', '--no-wait']

    def test_ensure_reports_defaults_without_submission_or_launch(self):
        client = RoundtableClient()
        with mock.patch('subprocess.Popen', side_effect=AssertionError('no launch')):
            status, values, _ = self.invoke(client, '--ensure')
        self.assertEqual(status, 0)
        result = values[0]
        self.assertIs(result['roundtable_host_supported'], True)
        self.assertEqual(result['roundtable'], settings())
        self.assertEqual(result['roundtable_plan']['reason'], 'assist_mode')
        self.assertFalse(result['roundtable_plan']['ready'])
        self.assertFalse(result['model_started'])
        self.assertEqual(client.calls, [])

    def test_host_choice_saves_exact_chat_and_implies_roundtable(self):
        client = RoundtableClient(policy=existing.policy(muse=True))
        _, values, _ = self.invoke(client, '--ensure', '--host', 'muse')
        self.assertEqual(client.calls, [('/api/conversations/' + CID + '/roundtable',
                                         {'codex_thread_id': THREAD, 'host': 'muse', 'mode': 'roundtable'})])
        result = values[0]
        self.assertEqual(result['roundtable']['host'], 'muse')
        self.assertEqual(result['roundtable']['mode'], 'roundtable')
        self.assertTrue(result['roundtable_plan']['ready'])
        self.assertEqual(result['roundtable_plan']['participants'], ['codex', 'agy', 'muse'])
        self.assertEqual(len(client.bindings), 2, 'reread authoritative state after save')
        self.assertFalse(result['model_started'])

    def test_mode_only_save_preserves_host_and_does_not_enable_providers(self):
        policy = existing.policy(enabled=False, agy=False, claude=True)
        client = RoundtableClient(value=settings('roundtable', 'claude'), policy=policy)
        _, values, _ = self.invoke(client, '--ensure', '--collaboration-mode', 'assist')
        self.assertEqual(client.calls, [('/api/conversations/' + CID + '/roundtable',
                                         {'codex_thread_id': THREAD, 'mode': 'assist'})])
        self.assertEqual(values[0]['roundtable']['host'], 'claude')
        self.assertEqual(values[0]['multi_ai_collaboration'], policy)
        self.assertFalse(values[0]['collaboration_enabled'])

    def test_explicit_mode_wins_when_host_and_mode_supplied(self):
        client = RoundtableClient()
        _, values, _ = self.invoke(client, '--ensure', '--host', 'agy', '--collaboration-mode', 'assist')
        self.assertEqual(client.calls[0][1], {'codex_thread_id': THREAD, 'host': 'agy', 'mode': 'assist'})
        self.assertEqual(values[0]['roundtable']['mode'], 'assist')

    def test_disabled_host_is_saved_honestly_without_enabling_or_dispatching(self):
        client = RoundtableClient()
        _, values, _ = self.invoke(client, '--ensure', '--host', 'claude')
        result = values[0]
        self.assertEqual(result['roundtable']['host'], 'claude')
        self.assertFalse(result['roundtable_plan']['ready'])
        self.assertEqual(result['roundtable_plan']['reason'], 'host_disabled')
        self.assertFalse(result['multi_ai_collaboration']['providers']['claude']['enabled'])
        self.assertEqual(len(client.calls), 1)

    def test_master_paused_or_host_disabled_blocks_before_turn_submission(self):
        for policy, host in [(existing.policy(enabled=False), 'codex'),
                             (existing.policy(), 'muse')]:
            client = RoundtableClient(value=settings('roundtable', host), policy=policy)
            with self.subTest(host=host), self.assertRaises(ValueError):
                self.invoke(client, *self.task_args())
            self.assertEqual(client.calls, [])

    def test_ready_roundtable_dispatch_keeps_original_prompt_server_owns_roles(self):
        client = RoundtableClient(value=settings('roundtable', 'agy'))
        _, values, _ = self.invoke(client, *self.task_args())
        self.assertTrue(values[0]['submitted'])
        self.assertEqual(len(client.calls), 1)
        path, body = client.calls[0]
        self.assertEqual(path, '/api/conversations/' + CID + '/turns')
        self.assertEqual(body['prompt'], self.task.read_text())
        self.assertTrue(body['codex_dispatch'])
        self.assertNotIn('roundtable', body, 'server computes durable role snapshot from its own state')

    def test_muse_host_dispatch_only_sends_agy_participant_not_hidden_web_calls(self):
        client = RoundtableClient(value=settings('roundtable', 'muse'), policy=existing.policy(muse=True))
        _, values, _ = self.invoke(client, *self.task_args())
        self.assertTrue(values[0]['submitted'])
        self.assertEqual([path for path, _ in client.calls], ['/api/conversations/' + CID + '/turns'])
        self.assertEqual(client.calls[0][1]['prompt'], self.task.read_text())

    def test_legacy_server_ignores_unadvertised_roundtable_fields(self):
        client = RoundtableClient(modern=False)
        client.conv['roundtable'] = {'bad': True}
        _, values, _ = self.invoke(client, '--ensure')
        self.assertFalse(values[0]['roundtable_host_supported'])
        self.assertNotIn('roundtable', values[0])
        self.assertNotIn('roundtable_plan', values[0])
        self.assertEqual(client.calls, [])
        self.invoke(client, *self.task_args())
        self.assertEqual(len(client.calls), 1, 'ordinary old-server Agy dispatch remains compatible')

    def test_host_controls_require_feature_without_writing_or_submitting(self):
        for flags in [('--host', 'agy'), ('--collaboration-mode', 'roundtable')]:
            client = RoundtableClient(modern=False)
            with self.subTest(flags=flags), self.assertRaises(ValueError):
                self.invoke(client, '--ensure', *flags)
            self.assertEqual(client.calls, [])

    def test_invalid_host_mode_and_operation_mixes_reject_before_bind(self):
        for args in [('--host', 'other', '--ensure'), ('--collaboration-mode', 'auto', '--ensure'),
                     ('--host', 'agy', *self.task_args()),
                     ('--collaboration-mode', 'roundtable', *self.task_args()),
                     ('--ensure', '--host', 'agy', '--task-file', str(self.task)),
                     ('--review-turn', existing.TURN, '--review-file', str(self.task), '--host', 'agy')]:
            client = RoundtableClient()
            with self.subTest(args=args), self.assertRaises(SystemExit):
                self.invoke(client, *args)
            self.assertEqual(client.bindings, [])
            self.assertEqual(client.calls, [])

    def test_failed_save_never_reports_success_or_dispatches(self):
        client = RoundtableClient()
        client.fail_path = '/api/conversations/' + CID + '/roundtable'
        with self.assertRaisesRegex(ValueError, 'isolated roundtable save failed'):
            self.invoke(client, '--ensure', '--host', 'agy')
        self.assertEqual(client.conv['roundtable'], settings())
        self.assertEqual(len(client.calls), 1)

    def test_missing_bad_schema_id_rounds_enum_and_unknown_canonical_keys_fail_closed(self):
        bad = [None, {}, [], 'codex']
        for key, value in [('schema_version', True), ('schema_version', 2), ('codex_thread_id', OTHER),
                           ('mode', 'automatic'), ('host', 'gpt'), ('rounds', True), ('rounds', 3)]:
            item = settings(); item[key] = value; bad.append(item)
        item = settings(); item['unknown'] = 'x'; bad.append(item)
        for value in bad:
            client = RoundtableClient()
            client.conv['roundtable'] = value
            with self.subTest(value=value), self.assertRaises(ValueError):
                self.invoke(client, '--ensure')
            self.assertEqual(client.calls, [])

    def test_advertised_plan_must_be_complete_strict_and_match_canonical(self):
        mutations = [None, {}, {'extra': True}]
        for key, value in [('schema_version', True), ('codex_thread_id', OTHER), ('host', 'agy'),
                           ('mode', 'roundtable'), ('rounds', True), ('ready', 1),
                           ('participants', ['codex', 'agy', 'unknown']),
                           ('participants', ['codex', 'agy', 'agy']),
                           ('execution', 'background'), ('steps', ['host_summary'])]:
            item = plan(settings(), existing.policy()); item[key] = value; mutations.append(item)
        item = plan(settings(), existing.policy()); item['unknown'] = False; mutations.append(item)
        for value in mutations:
            client = RoundtableClient()
            client.conv['roundtable_plan'] = value
            with self.subTest(value=value), self.assertRaises(ValueError):
                self.invoke(client, '--ensure')
            self.assertEqual(client.calls, [])

    def test_false_ready_or_false_reason_cannot_become_allowing_plan(self):
        for change in [{'ready': False}, {'reason': 'host_disabled'},
                       {'participants': ['codex', 'agy', 'muse']}, {'ready': True, 'reason': 'ready'}]:
            value = settings('roundtable', 'agy') if 'ready' not in change or change['ready'] is False else settings()
            client = RoundtableClient(value=value)
            client.conv['roundtable_plan'].update(change)
            with self.subTest(change=change), self.assertRaises(ValueError):
                self.invoke(client, *self.task_args())
            self.assertEqual(client.calls, [])


class RoundtableHTTPTests(unittest.TestCase):
    def test_real_bridge_subprocess_saves_only_target_thread_without_cli_execution(self):
        from server import Console, Handler, ThreadingHTTPServer
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            fake = root / 'agy'
            fake.write_text('#!/bin/sh\nexit 99\n')
            fake.chmod(0o700)
            app = Console(root / 'data', fake, import_history=False,
                          account_reader=lambda *_a, **_k: {'state': 'unknown', 'email': None})
            httpd = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
            httpd.app = app
            worker = threading.Thread(target=httpd.serve_forever, daemon=True)
            worker.start()
            def run(thread, *flags):
                args = [sys.executable, str(Path(bridge.__file__)), '--ensure',
                        '--url', 'http://127.0.0.1:' + str(httpd.server_port), *flags]
                return json.loads(subprocess.check_output(args, text=True, timeout=10,
                                                         env=dict(os.environ, CODEX_THREAD_ID=thread)))
            try:
                first = run(THREAD, '--host', 'agy')
                second = run(OTHER)
                again = run(THREAD)
                self.assertEqual(first['roundtable'], settings('roundtable', 'agy'))
                self.assertEqual(second['roundtable'], settings(thread=OTHER))
                self.assertEqual(again['roundtable'], first['roundtable'])
                self.assertNotEqual(first['console_conversation_id'], second['console_conversation_id'])
                self.assertFalse(first['model_started'])
                self.assertFalse(app.processes)
                self.assertTrue(all(not conv['turns'] for conv in app.state['conversations']))
                offline = json.loads((root / 'data/coordination.json').read_text())
                self.assertEqual(offline['threads'][THREAD]['roundtable'], first['roundtable'])
                self.assertEqual(offline['threads'][OTHER]['roundtable'], second['roundtable'])
                self.assertFalse((root / 'data/runs').exists())
            finally:
                httpd.shutdown(); httpd.server_close(); worker.join(3); app.close()


if __name__ == '__main__':
    unittest.main(verbosity=2)
