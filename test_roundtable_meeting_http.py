"""Independent meetings over real loopback HTTP, isolated restart and backup paths."""
import copy
import http.client
import json
from pathlib import Path
import tempfile
import threading
import unittest
from unittest.mock import patch
import zipfile

import server
from panel_backups import validate_state
from server import Console, Handler, ThreadingHTTPServer, now


THREAD = '11111111-2222-3333-4444-555555555555'
OTHER = '66666666-7777-8888-9999-aaaaaaaaaaaa'
SESSION = 'aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee'
MUSE = 'https://muse.ai/thread/' + OTHER


class IndependentMeetingHTTPTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.stub = self.root / 'never-run-agy'
        self.stub.write_text('#!/bin/sh\nexit 99\n', encoding='utf-8')
        self.stub.chmod(0o700)
        self.updates = patch('server.SoftwareUpdates.start')
        self.updates.start()
        self.addCleanup(self.updates.stop)
        self.app = self.new_console()
        bound = self.app.bind_codex(THREAD, 'Independent meeting HTTP fixture')
        self.cid = bound['id']
        self.conv = self.app.conversation(self.cid)
        self.conv.update(agy_id=SESSION, primary_branch='main', branches={
            'main': {'id': 'main', 'title': 'Main', 'agy_id': SESSION},
            'art': {'id': 'art', 'title': 'Art', 'agy_id': None}})
        self.app.set_multi_ai_collaboration(self.cid, {'codex_thread_id': THREAD,
            'providers': {'muse': {'enabled': True}}})
        self.app.set_roundtable(self.cid, {'codex_thread_id': THREAD, 'mode': 'roundtable', 'host': 'codex'})
        self.app.health['models'] = [{'id': 'fixture-model'}]
        self.app.state['preferred_model'] = 'fixture-model'
        self.app.save(self.conv)
        self.httpd = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
        self.httpd.app = self.app
        self.worker = threading.Thread(target=self.httpd.serve_forever, daemon=True)
        self.worker.start()
        self.base = '/api/conversations/' + self.cid
        self.origin = 'http://127.0.0.1:' + str(self.httpd.server_port)

    def new_console(self):
        app = Console(self.root / 'data', self.stub, import_history=False,
                      account_reader=lambda *_a, **_k: {'state': 'unknown', 'email': None},
                      coordination_path=self.root / 'isolated-coordination.json')
        app.activity.root = self.root / 'empty-official-profile'
        return app

    def tearDown(self):
        self.httpd.shutdown()
        self.httpd.server_close()
        self.worker.join(3)
        self.app.close()
        self.temp.cleanup()

    def request(self, method, path, body=None, **headers):
        connection = http.client.HTTPConnection('127.0.0.1', self.httpd.server_port, timeout=3)
        merged = {'Content-Type': 'application/json', 'X-Console-Token': self.app.token}
        merged.update(headers)
        raw = None if body is None else json.dumps(body, ensure_ascii=False).encode('utf-8')
        try:
            connection.request(method, path, raw, merged)
            response = connection.getresponse()
            data = response.read()
            return response.status, json.loads(data) if data else None
        finally:
            connection.close()

    def create(self, source='http:topic', branch='main'):
        status, payload = self.request('POST', self.base + '/meetings', {
            'codex_thread_id': THREAD, 'source_id': source,
            'topic': 'Bounded independent HTTP fixture', 'branch': branch})
        self.assertEqual(status, 200, payload)
        self.assertFalse(payload['model_started'])
        return payload['meeting']['id']

    def event(self, mid, **changes):
        body = {'codex_thread_id': THREAD, 'source_id': 'muse:http:proposal',
                'provider': 'muse', 'stage': 'proposal', 'status': 'completed',
                'text': 'A real observed fixture reply', 'source_url': MUSE, 'detail': ''}
        body.update(changes)
        return self.request('POST', self.base + '/meetings/' + mid + '/events', body)

    def native(self, mid, **changes):
        body = {'prompt': 'One bounded native fixture assignment', 'model': 'fixture-model',
                'kind': 'text', 'codex_dispatch': True, 'meeting_id': mid,
                'meeting_stage': 'proposal', 'branch': 'main'}
        body.update(changes)
        return self.request('POST', self.base + '/turns', body)

    def test_endpoints_record_muse_before_cli_and_native_duplicate_enqueues_once(self):
        mirror = self.app.coordination.path.read_bytes()
        with patch('subprocess.Popen', side_effect=AssertionError('no real process')):
            mid = self.create()
            self.assertEqual(self.create(), mid)
            status, event = self.event(mid)
            self.assertEqual(status, 200, event)
            self.assertEqual(self.conv['turns'], [])
            self.assertTrue(self.event(mid)[1]['duplicate'])
            with patch.object(self.app.jobs, 'put') as enqueue:
                status, first = self.native(mid)
                self.assertEqual(status, 202, first)
                status, duplicate = self.native(mid)
                self.assertEqual(status, 202, duplicate)
                self.assertTrue(duplicate['duplicate_submission'])
                self.assertEqual(first['id'], duplicate['id'])
                enqueue.assert_called_once_with((self.cid, first['id']))
                status, conflict = self.native(mid, prompt='Different content for same stage')
                self.assertEqual(status, 409, conflict)
                self.assertEqual(enqueue.call_count, 1)
        self.assertEqual(self.app.coordination.path.read_bytes(), mirror)
        status, meeting = self.request('GET', self.base + '/meetings/' + mid)
        self.assertEqual(status, 200, meeting)
        self.assertEqual([s['provider'] for s in meeting['speakers']], ['muse'])
        native = next(s for s in meeting['slots'] if s['provider'] == 'agy' and s['stage'] == 'proposal')
        self.assertEqual(native['status'], 'queued')
        self.assertIsNone(native['started_at'])
        self.assertEqual(native['turn_id'], first['id'])

    def test_host_origin_and_token_checks_precede_any_mutation(self):
        mid = self.create()
        before = self.app.file.read_bytes()
        path = self.base + '/meetings/' + mid + '/events'
        body = {'codex_thread_id': THREAD, 'source_id': 'muse:security', 'provider': 'muse',
                'stage': 'proposal', 'status': 'completed', 'text': 'Blocked', 'source_url': MUSE}
        cases = ({'Host': 'evil.test'}, {'Host': '127.0.0.1'},
                 {'Origin': 'https://evil.test'}, {'Origin': self.origin + '.evil.test'},
                 {'X-Console-Token': ''}, {'X-Console-Token': 'invalid'})
        for headers in cases:
            with self.subTest(headers=headers):
                self.assertEqual(self.request('POST', path, body, **headers)[0], 403)
                self.assertEqual(self.app.file.read_bytes(), before)
        self.assertEqual(self.request('GET', self.base + '/meetings', Host='evil.test')[0], 403)
        self.assertEqual(self.request('POST', path, body, Origin=self.origin)[0], 200)

    def test_malformed_json_enums_return_400_without_mutation_or_enqueue(self):
        mid = self.create()
        before = self.app.file.read_bytes()
        with patch.object(self.app.jobs, 'put') as enqueue:
            for field in ('provider', 'stage', 'status', 'source_id'):
                for value in ([], {}, None, True):
                    with self.subTest(field=field, value=value):
                        status, error = self.event(mid, **{field: value})
                        self.assertEqual(status, 400, error)
                        self.assertEqual(self.app.file.read_bytes(), before)
            for field in ('meeting_id', 'meeting_stage'):
                for value in ([], {}):
                    with self.subTest(native_field=field, value=value):
                        status, error = self.native(mid, **{field: value})
                        self.assertEqual(status, 400, error)
            enqueue.assert_not_called()

    def test_branch_and_fixed_chat_filters_never_borrow_meetings(self):
        main = self.create()
        art = self.create('http:art', 'art')
        other = self.app.bind_codex(OTHER)
        for branch, wanted in [('main', main), ('art', art)]:
            status, value = self.request('GET', self.base + '/meetings?branch=' + branch)
            self.assertEqual(status, 200, value)
            self.assertEqual([m['id'] for m in value['meetings']], [wanted])
        wrong = '/api/conversations/' + other['id'] + '/meetings'
        self.assertEqual(self.request('GET', wrong)[1]['meetings'], [])
        self.assertEqual(self.request('GET', wrong + '/' + main)[0], 404)
        self.assertEqual(self.request('GET', self.base + '/meetings?branch=missing')[0], 400)
        before = self.app.file.read_bytes()
        self.assertEqual(self.event(main, codex_thread_id=OTHER)[0], 400)
        self.assertEqual(self.request('POST', wrong + '/' + main + '/events', {
            'codex_thread_id': OTHER, 'source_id': 'other:event', 'provider': 'muse',
            'stage': 'proposal', 'status': 'completed', 'text': 'Not owned', 'source_url': MUSE})[0], 400)
        self.assertEqual(self.app.file.read_bytes(), before)

    def fresh_binding(self):
        conv = self.app.conversation(self.app.bind_codex(OTHER, 'Fresh no-CLI fixture')['id'])
        self.assertEqual(conv['branches'], {})
        self.assertIsNone(conv['agy_id'])
        self.app.set_multi_ai_collaboration(conv['id'], {'codex_thread_id': OTHER,
            'providers': {'muse': {'enabled': True}}})
        self.app.set_roundtable(conv['id'], {'codex_thread_id': OTHER, 'mode': 'roundtable', 'host': 'codex'})
        return conv, '/api/conversations/' + conv['id'] + '/meetings'

    def test_fresh_binding_can_create_default_main_without_dummy_native_call(self):
        conv, path = self.fresh_binding()
        mirror = self.app.coordination.path.read_bytes()
        with patch('subprocess.Popen', side_effect=AssertionError('no dummy CLI')), patch.object(self.app.jobs, 'put') as enqueue:
            status, value = self.request('POST', path, {'codex_thread_id': OTHER,
                'source_id': 'fresh:meeting', 'topic': 'Muse may speak before CLI exists'})
            self.assertEqual(status, 200, value)
            self.assertFalse(value['model_started'])
            self.assertEqual(value['meeting']['branch'], 'main')
            self.assertEqual(conv['primary_branch'], 'main')
            self.assertEqual(set(conv['branches']), {'main'})
            self.assertIsNone(conv['branches']['main']['agy_id'])
            self.assertIsNone(conv['agy_id'])
            self.assertEqual(conv['turns'], [])
            enqueue.assert_not_called()
        self.assertEqual(self.app.coordination.path.read_bytes(), mirror)
        self.assertEqual(self.request('GET', path + '?branch=main')[1]['total'], 1)

    def test_initial_display_lane_rolls_back_with_failed_first_meeting_write(self):
        conv, path = self.fresh_binding()
        original = copy.deepcopy(conv)
        before = self.app.file.read_bytes()
        mirror = self.app.coordination.path.read_bytes()
        write = server.write_json
        attempts = []
        def fail_once(file, value):
            attempts.append(str(file))
            if len(attempts) == 1:
                raise OSError('simulated first meeting persistence failure')
            return write(file, value)
        with patch('server.write_json', side_effect=fail_once), patch.object(self.app.jobs, 'put') as enqueue:
            status, value = self.request('POST', path, {'codex_thread_id': OTHER,
                'source_id': 'fresh:rollback', 'topic': 'No phantom default lane'})
            self.assertEqual(status, 400, value)
            enqueue.assert_not_called()
        self.assertEqual(conv, original)
        self.assertEqual(self.app.file.read_bytes(), before)
        self.assertEqual(self.app.coordination.path.read_bytes(), mirror)
        self.assertEqual(conv['branches'], {})
        self.assertNotIn('roundtable_meetings', conv)

    def test_recent_and_older_meeting_pages_are_bounded_branch_scoped_and_cursor_checked(self):
        mids = [self.create('page:main:' + str(i)) for i in range(12)]
        art = self.create('page:art', 'art')
        status, latest = self.request('GET', self.base + '/meetings?branch=main')
        self.assertEqual(status, 200, latest)
        self.assertEqual([m['id'] for m in latest['meetings']], mids[2:])
        self.assertEqual(latest['total'], 12)
        self.assertTrue(latest['has_older'])
        self.assertEqual((latest['first_id'], latest['last_id']), (mids[2], mids[-1]))
        status, older = self.request('GET', self.base + '/meetings?branch=main&before=' + latest['first_id'] + '&limit=2')
        self.assertEqual(status, 200, older)
        self.assertEqual([m['id'] for m in older['meetings']], mids[:2])
        self.assertEqual(older['total'], 12)
        self.assertFalse(older['has_older'])
        first_page = self.request('GET', self.base + '/meetings?branch=main&before=' + mids[0])[1]
        self.assertEqual(first_page['meetings'], [])
        self.assertIsNone(first_page['first_id'])
        self.assertIsNone(first_page['last_id'])
        for cursor in ('f' * 32, art, 'not-a-cursor'):
            with self.subTest(cursor=cursor):
                self.assertEqual(self.request('GET', self.base + '/meetings?branch=main&before=' + cursor)[0], 404)
        for count in ('0', '-1', '21', 'true', '1.5', 'abc'):
            with self.subTest(limit=count):
                self.assertEqual(self.request('GET', self.base + '/meetings?limit=' + count)[0], 400)
        status, scoped_exact = self.request('GET', self.base + '/meetings/' + mids[0] + '?branch=art')
        self.assertEqual(status, 404, scoped_exact)

    def test_malformed_native_status_and_timestamps_return_validation_errors_not_500(self):
        mid = self.create()
        with patch.object(self.app.jobs, 'put'):
            self.assertEqual(self.native(mid)[0], 202)
        turn = self.conv['turns'][0]
        original = copy.deepcopy(turn)
        malformed = [('status', {}), ('status', []), ('status', 'not-a-status')]
        malformed.extend((field, value) for field in ('created_at', 'started_at', 'ended_at', 'progress_updated_at')
                         for value in ({}, [], '', 'not-a-date', float('nan'), float('inf')))
        for field, value in malformed:
            turn.clear();turn.update(original);turn[field] = value
            with self.subTest(field=field, value=value):
                status, error = self.request('GET', self.base + '/meetings/' + mid)
                self.assertEqual(status, 400, error)
        turn.clear();turn.update(original)
        # A separate ordinary malformed task has no meeting provenance to read;
        # it must not become a speech or break the unrelated meeting endpoint.
        self.conv['turns'].append({'id': 'f' * 32, 'status': {'bad': 'state'}, 'response': None})
        status, value = self.request('GET', self.base + '/meetings/' + mid)
        self.assertEqual(status, 200, value)
        self.assertEqual(value['speakers'], [])
        self.conv['turns'].pop()

    def test_foreground_restart_preserves_observed_event_and_cancelled_native_link(self):
        mid = self.create()
        self.assertEqual(self.event(mid)[0], 200)
        with patch.object(self.app.jobs, 'put'):
            status, native = self.native(mid)
        self.assertEqual(status, 202, native)
        original = copy.deepcopy(self.conv['roundtable_meetings'])
        self.app.close()
        self.app = self.new_console()
        self.httpd.app = self.app
        self.conv = self.app.conversation(self.cid)
        self.assertEqual(self.conv['roundtable_meetings'], original)
        self.assertEqual(self.conv['agy_id'], SESSION)
        status, meeting = self.request('GET', self.base + '/meetings/' + mid)
        self.assertEqual(status, 200, meeting)
        self.assertEqual(meeting['speakers'][0]['text'], 'A real observed fixture reply')
        slot = next(s for s in meeting['slots'] if s['provider'] == 'agy' and s['stage'] == 'proposal')
        self.assertEqual(slot['turn_id'], native['id'])
        self.assertEqual(slot['status'], 'cancelled')
        self.assertEqual(slot['text'], '')
        self.assertEqual(meeting['status'], 'active')
        self.assertFalse(self.app.processes)

    def test_backup_roundtrip_preserves_independent_and_native_provenance_rejects_orphans(self):
        mid = self.create()
        self.assertEqual(self.event(mid)[0], 200)
        with patch.object(self.app.jobs, 'put'):
            status, submitted = self.native(mid)
        self.assertEqual(status, 202, submitted)
        turn = self.conv['turns'][0]
        turn.update(status='completed', response='Actual native fixture reply', agy_id=SESSION,
                    started_at=turn['created_at'], ended_at=now())
        self.app.save(self.conv)
        validate_state(self.app.state)
        original = copy.deepcopy(self.conv['roundtable_meetings'])
        result = self.app.backups.create(self.app.state, {'font': 14})
        path = self.app.data / 'backups' / (result['id'] + '.zip')
        with zipfile.ZipFile(path) as archive:
            stored = json.loads(archive.read('state.json'))
        self.assertEqual(stored['conversations'][0]['roundtable_meetings'], original)
        validate_state(stored)
        for edit in ('no-event', 'wrong-native-id', 'wrong-meeting-id', 'partial-link'):
            broken = copy.deepcopy(stored)
            conv = broken['conversations'][0]
            if edit == 'no-event':
                conv['roundtable_meetings'][0]['events'] = conv['roundtable_meetings'][0]['events'][:1]
                conv['roundtable_meetings'][0]['updated_at'] = conv['roundtable_meetings'][0]['events'][0]['created_at']
            elif edit == 'wrong-native-id': conv['turns'][0]['id'] = 'f' * 32
            elif edit == 'wrong-meeting-id': conv['turns'][0]['meeting_id'] = 'e' * 32
            else: conv['turns'][0].pop('meeting_stage')
            with self.subTest(edit=edit), self.assertRaises(ValueError):
                validate_state(broken)
        portable = json.dumps(self.app.export_configuration({}, self.cid), ensure_ascii=False)
        self.assertNotIn('roundtable_meetings', portable)
        self.assertNotIn('Actual native fixture reply', portable)
        self.assertNotIn('A real observed fixture reply', portable)
        preview = self.app.backups.preview(path)
        restored, receipt = self.app.backups.restore(preview['id'], self.app.state, {})
        self.assertTrue(receipt['restored'])
        self.assertEqual(restored['conversations'][0]['roundtable_meetings'], original)
        self.assertEqual(restored['conversations'][0]['turns'][0]['meeting_id'], mid)
        self.assertEqual(restored['conversations'][0]['turns'][0]['response'], 'Actual native fixture reply')
        validate_state(restored)


if __name__ == '__main__':
    unittest.main()
