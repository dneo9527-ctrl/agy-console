"""Independent meeting bridge contract using API doubles, no profiles/models/network."""
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
from urllib.error import HTTPError
from urllib.parse import urlencode

import bridge
from test_multi_ai_bridge import THREAD, OTHER, CID, TURN, policy
from test_roundtable_bridge import RoundtableClient, settings, plan

MID = 'd' * 32
BASE = '/api/conversations/' + CID + '/meetings'


class MeetingClient(RoundtableClient):
    def __init__(self, *, supported=True, value=None, controls=None):
        super().__init__(value=value or settings('roundtable'), policy=controls or policy(muse=True))
        self.features['roundtable_meetings'] = supported
        self.frozen = {'id': MID, 'codex_thread_id': THREAD, 'branch': 'main',
                       'host': 'codex', 'participants': ['codex', 'agy', 'muse'],
                       'status': 'active', 'slots': []}
        self.duplicate = False

    def request(self, path, body=None):
        if path == BASE or path.startswith(BASE + '/'):
            self.calls.append((path, copy.deepcopy(body)))
            if path == self.fail_path:
                raise ValueError('isolated meeting write failed')
            if body is None:
                return {'meetings': [copy.deepcopy(self.frozen)]} if path == BASE else copy.deepcopy(self.frozen)
            return {'model_started': False, 'duplicate': self.duplicate,
                    'meeting': copy.deepcopy(self.frozen), 'recorded': path.endswith('/events')}
        result = super().request(path, body)
        if path.endswith('/turns'):
            result.update(meeting_id=body.get('meeting_id'), meeting_stage=body.get('meeting_stage'),
                          roundtable=settings('roundtable', self.frozen['host']),
                          roundtable_role='participant', duplicate_submission=self.duplicate)
        return result


class MeetingBridgeTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.create = self.root / 'create.json'
        self.event = self.root / 'event.json'
        self.task = self.root / 'task.txt'
        self.task.write_text('One bounded proposal.', encoding='utf-8')
        self.create_body = {'codex_thread_id': THREAD, 'source_id': 'stable-meeting-id',
                            'topic': 'Compact truthful meeting UI', 'branch': 'main'}
        self.event_body = {'meeting_id': MID, 'codex_thread_id': THREAD,
                           'source_id': 'muse-observed-one', 'provider': 'muse',
                           'stage': 'proposal', 'status': 'completed',
                           'text': 'Actually observed contribution.',
                           'source_url': 'https://muse.ai/thread/' + OTHER}
        self.write(self.create, self.create_body)
        self.write(self.event, self.event_body)

    @staticmethod
    def write(path, value):
        path.write_text(json.dumps(value), encoding='utf-8')

    def invoke(self, client, *flags):
        stdout = io.StringIO()
        old_umask = os.umask(0o077)
        try:
            with mock.patch.object(sys, 'argv', ['bridge.py', '--thread-id', THREAD, *flags]), \
                    mock.patch.object(bridge, 'Client', return_value=client), \
                    contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(io.StringIO()):
                code = bridge.main()
        finally:
            os.umask(old_umask)
        return code, [json.loads(line) for line in stdout.getvalue().splitlines()]

    def native(self, client, *extra):
        return self.invoke(client, '--meeting', MID, '--meeting-stage', 'proposal',
                           '--task-file', str(self.task), '--no-wait', *extra)

    def assert_rejected_before_bind(self, *flags):
        client = MeetingClient()
        with self.assertRaises((SystemExit, ValueError, OSError)):
            self.invoke(client, *flags)
        self.assertEqual(client.bindings, [], 'bad local input must not create or change a binding')
        self.assertEqual(client.calls, [])

    def test_create_only_records_exact_source_and_never_dispatches(self):
        client = MeetingClient()
        with mock.patch('subprocess.Popen', side_effect=AssertionError('no model launch')):
            code, output = self.invoke(client, '--meeting-create', str(self.create))
        self.assertEqual(code, 0)
        self.assertFalse(output[0]['model_started'])
        self.assertEqual(client.calls, [(BASE, self.create_body)])
        self.assertEqual(client.bindings, [(THREAD, None, {})])

    def test_observed_event_routes_exact_meeting_without_inventing_payload(self):
        client = MeetingClient()
        code, output = self.invoke(client, '--meeting-event', str(self.event))
        self.assertEqual(code, 0)
        self.assertTrue(output[0]['recorded'])
        expected = dict(self.event_body)
        expected.pop('meeting_id')
        self.assertEqual(client.calls, [(BASE + '/' + MID + '/events', expected)])
        self.assertEqual(json.loads(self.event.read_text()), self.event_body)

    def test_read_list_and_exact_id_are_model_free_gets_even_when_paused(self):
        for selector, path in [('list', BASE), (MID, BASE + '/' + MID)]:
            with self.subTest(selector=selector):
                client = MeetingClient(controls=policy(enabled=False))
                self.invoke(client, '--meeting-view', selector)
                self.assertEqual(client.calls, [(path, None)])

    def test_source_identity_survives_idempotent_create_and_event_replay(self):
        for flag, path in [('--meeting-create', self.create), ('--meeting-event', self.event)]:
            client = MeetingClient()
            self.invoke(client, flag, str(path))
            first = copy.deepcopy(client.calls[-1])
            client.duplicate = True
            _, output = self.invoke(client, flag, str(path))
            self.assertTrue(output[0]['duplicate'])
            self.assertEqual(client.calls[-1], first, 'retry must reuse source ID and content')

    def test_unsupported_and_failed_record_never_report_success_or_start_task(self):
        client = MeetingClient(supported=False)
        with self.assertRaises(ValueError):
            self.invoke(client, '--meeting-create', str(self.create))
        self.assertEqual(client.calls, [])
        client = MeetingClient()
        client.fail_path = BASE + '/' + MID + '/events'
        with self.assertRaisesRegex(ValueError, 'meeting write failed'):
            self.invoke(client, '--meeting-event', str(self.event))
        self.assertEqual(len(client.calls), 1)

    def test_native_submission_keeps_meeting_phase_and_saved_model_selection(self):
        client = MeetingClient()
        _, output = self.native(client)
        body = next(body for path, body in client.calls if path.endswith('/turns'))
        self.assertEqual(body, {'prompt': self.task.read_text(), 'model': '', 'kind': 'text',
                                'codex_dispatch': True, 'meeting_id': MID, 'meeting_stage': 'proposal'})
        self.assertTrue(output[0]['submitted'])
        self.assertEqual(output[0]['meeting_id'], MID)
        self.assertEqual(output[0]['meeting_stage'], 'proposal')
        self.assertEqual(output[0]['panel_action']['type'], 'none')

    def test_duplicate_native_receipt_is_reported_without_client_resubmission_loop(self):
        client = MeetingClient()
        client.duplicate = True
        _, output = self.native(client)
        self.assertTrue(output[0]['duplicate_submission'])
        self.assertEqual(len([p for p, _ in client.calls if p.endswith('/turns')]), 1)

    def test_native_dispatch_master_agy_and_malformed_policy_fail_closed(self):
        for controls in [policy(enabled=False), policy(agy=False), {'bad': True}]:
            client = MeetingClient(controls=controls if controls != {'bad': True} else policy())
            if controls == {'bad': True}:
                client.conv['multi_ai_collaboration'] = controls
            with self.subTest(controls=controls), self.assertRaises(ValueError):
                self.native(client)
            self.assertFalse(any(path.endswith('/turns') for path, _ in client.calls))

    def test_existing_frozen_host_survives_changed_future_meeting_host(self):
        client = MeetingClient(value=settings('roundtable', 'claude'), controls=policy(muse=True))
        self.assertFalse(client.conv['roundtable_plan']['ready'])
        self.assertEqual(client.frozen['host'], 'codex')
        _, output = self.native(client)
        self.assertTrue(output[0]['submitted'])
        self.assertEqual(output[0]['roundtable']['host'], 'codex')

    def test_mixed_record_task_or_settings_flags_reject_before_any_bind(self):
        combinations = [
            ['--meeting-create', str(self.create), '--meeting-event', str(self.event)],
            ['--meeting-view', MID, '--task-file', str(self.task)],
            ['--meeting-event', str(self.event), '--ensure'],
            ['--meeting-create', str(self.create), '--model', 'explicit-model'],
            ['--meeting-create', str(self.create), '--out', str(self.root / 'unexpected')],
            ['--meeting-view', MID, '--context', str(self.task)],
            ['--meeting-event', str(self.event), '--branch', 'main'],
            ['--meeting-create', str(self.create), '--kind', 'image'],
            ['--meeting-view', MID, '--review-turn', TURN, '--review-file', str(self.event)],
        ]
        for flags in combinations:
            with self.subTest(flags=flags):
                self.assert_rejected_before_bind(*flags)
        self.assertFalse((self.root / 'unexpected').exists())

    def test_bad_meeting_task_combinations_reject_before_any_bind(self):
        combinations = [
            ['--meeting', MID], ['--meeting-stage', 'proposal'],
            ['--meeting', MID, '--meeting-stage', 'proposal'],
            ['--meeting', 'invalid', '--meeting-stage', 'proposal', '--task-file', str(self.task)],
            ['--meeting', MID, '--meeting-stage', 'proposal', '--ensure', '--task-file', str(self.task)],
            ['--meeting', MID, '--meeting-stage', 'proposal', '--kind', 'image', '--task-file', str(self.task), '--no-wait'],
        ]
        for flags in combinations:
            with self.subTest(flags=flags):
                self.assert_rejected_before_bind(*flags)

    def test_wrong_thread_or_bad_local_record_rejected_before_binding(self):
        variants = [None, [], {'codex_thread_id': OTHER}, {'codex_thread_id': THREAD, 'meeting_id': 'bad'}]
        for value in variants:
            self.write(self.event, value)
            with self.subTest(value=value):
                self.assert_rejected_before_bind('--meeting-event', str(self.event))
        self.assert_rejected_before_bind('--meeting-create', str(self.root / 'missing.json'))
        self.event.write_text('invalid json', encoding='utf-8')
        self.assert_rejected_before_bind('--meeting-event', str(self.event))
        self.event.write_text('x' * 65537, encoding='utf-8')
        self.assert_rejected_before_bind('--meeting-event', str(self.event))
        self.assert_rejected_before_bind('--meeting-view', 'not-an-id')


class MeetingBridgeHTTPTests(unittest.TestCase):
    def test_selected_meeting_get_is_bounded_ordered_scoped_and_read_only(self):
        from server import Console, Handler, ThreadingHTTPServer
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            with mock.patch('server.SoftwareUpdates.start'):
                app = Console(root / 'data', root / 'never-executed-agy', import_history=False,
                              account_reader=lambda *_a, **_k: {'state': 'unknown'},
                              coordination_path=root / 'coordination.json')
            app.activity.root = root / 'official'
            cid = app.bind_codex(THREAD)['id']
            conv = app.conversation(cid)
            conv.update(primary_branch='main', branches={
                'main': {'id': 'main', 'title': 'Main', 'agy_id': None},
                'art': {'id': 'art', 'title': 'Art', 'agy_id': None}})
            app.set_roundtable(cid, {'codex_thread_id': THREAD, 'host': 'codex', 'mode': 'roundtable'})
            mids = [app.create_meeting(cid, {'codex_thread_id': THREAD, 'source_id': 'batch-' + str(index),
                                            'topic': 'Batch topic ' + str(index), 'branch': 'main'})['meeting']['id']
                    for index in range(21)]
            art = app.create_meeting(cid, {'codex_thread_id': THREAD, 'source_id': 'batch-art',
                                           'topic': 'Different lane', 'branch': 'art'})['meeting']['id']
            other_cid = app.bind_codex(OTHER)['id']
            app.conversation(other_cid).update(primary_branch='main', branches={
                'main': {'id': 'main', 'title': 'Other main', 'agy_id': None}})
            app.save()
            before, mirror = app.file.read_bytes(), app.coordination.path.read_bytes()
            state, revision = copy.deepcopy(app.state), app.revision
            httpd = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
            httpd.app = app
            worker = threading.Thread(target=httpd.serve_forever, daemon=True)
            worker.start()
            client = bridge.Client('http://127.0.0.1:' + str(httpd.server_port))

            def read(ids, *, branch='main', expected=200, owner=cid, **query):
                url = client.url + '/api/conversations/' + owner + '/meetings?' + urlencode(
                    dict(ids=','.join(ids), branch=branch, **query))
                try:
                    with client.opener.open(url, timeout=5) as response:
                        self.assertEqual(response.status, expected)
                        return json.load(response)
                except HTTPError as error:
                    self.assertEqual(error.code, expected)
                    return json.load(error)

            try:
                with mock.patch('server.write_json', side_effect=AssertionError('GET must not persist')), \
                        mock.patch.object(app.jobs, 'put', side_effect=AssertionError('GET must not enqueue')), \
                        mock.patch('subprocess.Popen', side_effect=AssertionError('GET must not launch')):
                    wanted = list(reversed(mids[:20]))
                    result = read(wanted)
                    self.assertEqual([item['id'] for item in result['meetings']], wanted)
                    self.assertEqual(read([art], branch='art')['meetings'][0]['id'], art)
                    for invalid in [[mids[0], mids[0]], mids, ['malformed'], ['', '']]:
                        with self.subTest(invalid=invalid):
                            read(invalid, expected=400)
                    read([mids[0]], before=mids[1], expected=400)
                    read(['f' * 32], expected=404)
                    read([mids[0], art], expected=404)
                    read([mids[0]], owner=other_cid, expected=404)
                self.assertEqual(app.state, state)
                self.assertEqual(app.revision, revision)
                self.assertEqual(app.file.read_bytes(), before)
                self.assertEqual(app.coordination.path.read_bytes(), mirror)
                self.assertFalse(app.processes)
            finally:
                httpd.shutdown()
                httpd.server_close()
                worker.join(3)
                app.close()

    def test_real_bridge_records_muse_before_cli_and_duplicate_native_enqueues_once(self):
        from server import Console, Handler, ThreadingHTTPServer
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            with mock.patch('server.SoftwareUpdates.start'):
                app = Console(root / 'data', root / 'never-executed-agy', import_history=False,
                              account_reader=lambda *_a, **_k: {'state': 'unknown'},
                              coordination_path=root / 'coordination.json')
            app.activity.root = root / 'official'
            bound = app.bind_codex(THREAD)
            cid = bound['id']
            app.conversation(cid).update(primary_branch='main', branches={
                'main': {'id': 'main', 'title': 'Main', 'agy_id': None}})
            app.set_multi_ai_collaboration(cid, {'codex_thread_id': THREAD,
                                                 'providers': {'muse': {'enabled': True}}})
            app.set_roundtable(cid, {'codex_thread_id': THREAD, 'host': 'codex', 'mode': 'roundtable'})
            app.health['models'] = [{'id': 'fixture-model'}]
            app.state['preferred_model'] = 'fixture-model'
            app.save()
            httpd = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
            httpd.app = app
            worker = threading.Thread(target=httpd.serve_forever, daemon=True)
            worker.start()
            command = [sys.executable, str(Path(bridge.__file__)), '--url',
                       'http://127.0.0.1:' + str(httpd.server_port)]

            def invoke(*flags, thread=THREAD, ok=True):
                result = subprocess.run(command + list(flags), capture_output=True, text=True, timeout=8,
                                        env=dict(os.environ, CODEX_THREAD_ID=thread))
                if ok:
                    self.assertEqual(result.returncode, 0, result.stderr)
                    return [json.loads(line) for line in result.stdout.splitlines()][-1]
                self.assertNotEqual(result.returncode, 0)
                return result

            try:
                create = root / 'create.json'
                create.write_text(json.dumps({'codex_thread_id': THREAD, 'source_id': 'real-http-meeting',
                                               'topic': 'No fake completed CLI anchor'}))
                first = invoke('--meeting-create', str(create))
                mid = first['meeting']['id']
                self.assertFalse(first['model_started'])
                self.assertTrue(invoke('--meeting-create', str(create))['duplicate'])
                event = root / 'event.json'
                event.write_text(json.dumps({'meeting_id': mid, 'codex_thread_id': THREAD,
                                              'source_id': 'real-http-observed-muse', 'provider': 'muse',
                                              'stage': 'proposal', 'status': 'completed', 'text': 'Synthetic test observation.',
                                              'source_url': 'https://muse.ai/thread/' + OTHER}))
                receipt = invoke('--meeting-event', str(event))
                self.assertTrue(receipt['recorded'])
                self.assertEqual(app.conversation(cid)['turns'], [])
                self.assertTrue(invoke('--meeting-event', str(event))['duplicate'])
                viewed = invoke('--meeting-view', mid)
                self.assertEqual(viewed['id'], mid)
                self.assertEqual(next(s for s in viewed['slots'] if s['provider'] == 'muse'
                                      and s['stage'] == 'proposal')['text'], 'Synthetic test observation.')
                task = root / 'task.txt'
                task.write_text('Single synthetic native meeting contribution.')
                # Changing the preference for future meetings must not block
                # the first native contribution to this frozen Codex meeting.
                app.set_roundtable(cid, {'codex_thread_id': THREAD, 'host': 'claude', 'mode': 'roundtable'})
                with mock.patch.object(app.jobs, 'put') as put:
                    args = ['--meeting', mid, '--meeting-stage', 'proposal', '--task-file', str(task), '--no-wait']
                    submitted = invoke(*args)
                    # --no-wait emits just the dispatch receipt. Retrying keeps
                    # native identity and lets server prevent a second queue put.
                    again = invoke(*args)
                    self.assertEqual(submitted['turn_id'], again['turn_id'])
                    self.assertTrue(again['duplicate_submission'])
                    self.assertEqual(put.call_count, 1)
                    self.assertTrue(invoke(*args)['duplicate_submission'])
                    self.assertEqual(put.call_count, 1)
                other = app.bind_codex(OTHER)
                result = invoke('--meeting-view', mid, thread=OTHER, ok=False)
                self.assertIn('圆桌', result.stderr)
                self.assertEqual(app.conversation(other['id'])['turns'], [])
                self.assertFalse(app.processes)
                self.assertFalse((root / 'data' / 'runs').exists())
            finally:
                httpd.shutdown()
                httpd.server_close()
                worker.join(3)
                app.close()


if __name__ == '__main__':
    unittest.main(verbosity=2)
