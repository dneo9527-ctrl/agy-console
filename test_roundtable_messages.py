"""Observed roundtable speech stays scoped, durable and separate from CLI work."""
import copy
import http.client
import json
from pathlib import Path
import tempfile
import threading
import unittest
from unittest.mock import patch
import zipfile

from bridge import Client
from multi_ai_collaboration import roundtable_message_request, validate_roundtable_messages
from panel_backups import validate_state
from server import Console, ConflictError, DEFAULT_MODEL, Handler, ThreadingHTTPServer, write_json

THREAD = '11111111-2222-3333-4444-555555555555'
OTHER = '66666666-7777-8888-9999-aaaaaaaaaaaa'
SESSION = 'bbbbbbbb-cccc-dddd-eeee-ffffffffffff'
ART_SESSION = 'aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee'
MUSE_URL = 'https://muse.ai/thread/' + OTHER
CLAUDE_URL = 'https://claude.ai/chat/' + OTHER


class RoundtableMessagesTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.binary = self.root / 'must-not-run-agy'
        self.binary.write_text('#!/bin/sh\nexit 99\n')
        self.binary.chmod(0o700)
        # No update network, official history, real CLI or shared policy writes.
        self.updater = patch('server.SoftwareUpdates.start')
        self.updater.start()
        self.addCleanup(self.updater.stop)
        self.app = Console(self.root / 'data', self.binary, import_history=False,
                           account_reader=lambda *_a, **_k: {'state': 'unknown', 'email': None},
                           coordination_path=self.root / 'coordination.json')
        self.app.activity.root = self.root / 'official'
        bound = self.app.bind_codex(THREAD, 'Roundtable test')
        self.conv = self.app.conversation(bound['id'])
        self.conv.update(agy_id=SESSION, primary_branch='main', branches={
            'main': {'id': 'main', 'title': 'Text', 'agy_id': SESSION},
            'art': {'id': 'art', 'title': 'Images', 'agy_id': ART_SESSION}})
        self.app.set_multi_ai_collaboration(self.conv['id'], {'codex_thread_id': THREAD,
            'providers': {'muse': {'enabled': True}, 'claude': {'enabled': True}}})
        self.app.set_roundtable(self.conv['id'], {'codex_thread_id': THREAD,
                                               'mode': 'roundtable', 'host': 'codex'})
        self.anchor = self.turn(1)
        self.conv['turns'].append(self.anchor)
        self.app.save(self.conv)

    def tearDown(self):
        self.app.close()
        self.temp.cleanup()

    def turn(self, index, branch='main', **extra):
        return {'id': format(index, '032x'), 'branch': branch, 'source': 'codex',
                'prompt': 'Bounded assignment', 'response': 'Observed Agy reply',
                'status': 'completed', 'agy_id': SESSION if branch == 'main' else ART_SESSION,
                'model': DEFAULT_MODEL, 'kind': 'text', 'usage': {'input_tokens': 20},
                'roundtable': copy.deepcopy(self.conv['roundtable']),
                'roundtable_role': 'participant', **extra}

    def body(self, **extra):
        return {'codex_thread_id': THREAD, 'anchor_turn_id': self.anchor['id'],
                'provider': 'muse', 'stage': 'proposal', 'text': 'Muse 的实际建议。',
                'source_url': MUSE_URL, 'source_id': 'muse:observed-r1', **extra}

    def record(self, **extra):
        return self.app.record_roundtable_message(self.conv['id'], self.body(**extra))

    def test_persists_actual_record_without_cli_turn_context_or_policy_changes(self):
        turns = copy.deepcopy(self.conv['turns'])
        policy = self.app.coordination.path.read_bytes()
        models = self.app.state['preferred_model'], copy.deepcopy(self.conv.get('model_preferences'))
        with patch('subprocess.Popen', side_effect=AssertionError('must not launch')):
            result = self.record()
        self.assertTrue(result['recorded'])
        self.assertFalse(result['duplicate'])
        message = result['message']
        self.assertEqual(message['provider'], 'muse')
        self.assertEqual(message['provenance'], 'codex_observed')
        self.assertEqual(message['role'], 'participant')
        self.assertEqual(message['branch'], 'main')
        self.assertEqual(self.conv['turns'], turns)
        self.assertEqual((self.app.state['preferred_model'], self.conv.get('model_preferences')), models)
        self.assertEqual(self.app.coordination.path.read_bytes(), policy)
        self.assertFalse(self.app.processes)
        self.assertEqual(list((self.app.data / 'runs').glob('*')), [])
        stored = json.loads(self.app.file.read_text())['conversations'][0]
        self.assertEqual(stored['roundtable_messages'], [message])
        result['message']['text'] = 'Caller mutation'
        self.assertNotEqual(self.conv['roundtable_messages'][0]['text'], 'Caller mutation')

    def test_thread_and_anchor_require_actual_completed_codex_roundtable_response(self):
        for changes in ({'codex_thread_id': OTHER}, {'anchor_turn_id': 'f' * 32},
                        {'anchor_turn_id': None}, {'anchor_turn_id': '../x'}):
            with self.subTest(changes=changes), self.assertRaises(ValueError):
                self.record(**changes)
        for changes in ({'source': 'manual'}, {'status': 'running'}, {'response': '   '},
                        {'agy_id': None}, {'agy_id': 'fake-session'}, {'roundtable_role': 'host'},
                        {'roundtable': dict(self.conv['roundtable'], mode='assist')},
                        {'roundtable': dict(self.conv['roundtable'], codex_thread_id=OTHER)},
                        {'branch': 'missing'}, {'branch': []}):
            original = copy.deepcopy(self.anchor)
            try:
                self.anchor.update(changes)
                with self.subTest(changes=changes), self.assertRaises(ValueError):
                    self.record()
            finally:
                self.anchor.clear(); self.anchor.update(original)
        other = self.app.bind_codex(OTHER)
        with self.assertRaises(ValueError):
            self.app.record_roundtable_message(other['id'], self.body())
        self.assertNotIn('roundtable_messages', self.conv)

    def test_bad_fields_providers_stages_source_ids_and_text_are_rejected_before_write(self):
        before = self.app.file.read_bytes()
        bad = ({'provider': 'agy'}, {'provider': []}, {'stage': 'running'},
               {'source_id': ''}, {'source_id': 'x' * 121}, {'source_id': '中文'},
               {'text': ''}, {'text': '  '}, {'text': None}, {'text': 'a\x00b'},
               {'text': 'x' * 12001}, {'text': '😀' * 12001})
        for changes in bad:
            with self.subTest(changes={k: str(v)[:40] for k, v in changes.items()}):
                with self.assertRaises(ValueError): self.record(**changes)
        for body in (dict(self.body(), token='secret'), {k: v for k, v in self.body().items() if k != 'text'}, []):
            with self.assertRaises(ValueError): self.app.record_roundtable_message(self.conv['id'], body)
        self.assertEqual(self.app.file.read_bytes(), before)

    def test_provider_urls_only_accept_specific_https_conversations(self):
        for url in ('http://muse.ai/thread/' + OTHER, 'https://muse.ai/thread/new',
                    'https://muse.ai/', MUSE_URL + '?', MUSE_URL + '#', MUSE_URL + '?token=x',
                    MUSE_URL + '#message', MUSE_URL + '/', MUSE_URL.replace('muse.ai', 'evil.test'),
                    MUSE_URL.replace('muse.ai', 'muse.ai.evil.test'),
                    MUSE_URL.replace('muse.ai', 'u:p@muse.ai'),
                    MUSE_URL.replace('muse.ai', 'muse.ai:443'), CLAUDE_URL):
            with self.subTest(url=url), self.assertRaises(ValueError): self.record(source_url=url)
        self.record(source_url=MUSE_URL.replace('muse.ai', 'www.muse.ai'))
        self.record(provider='claude', source_url=CLAUDE_URL, source_id='claude:observed-r1')
        with self.assertRaises(ValueError): self.record(provider='codex', source_url=MUSE_URL)
        codex = self.record(provider='codex', source_url='', source_id='codex:summary', stage='summary')
        self.assertEqual(codex['message']['role'], 'host')
        with self.assertRaises(ValueError): self.record(stage='summary', source_id='muse:not-host')

    def test_frozen_host_and_branch_survive_current_setting_changes(self):
        self.anchor.update(branch='art', agy_id=ART_SESSION,
                           roundtable=dict(self.conv['roundtable'], host='muse'))
        self.app.set_roundtable(self.conv['id'], {'codex_thread_id': THREAD, 'mode': 'assist', 'host': 'agy'})
        result = self.record(stage='summary')['message']
        self.assertEqual(result['roundtable']['host'], 'muse')
        self.assertEqual(result['role'], 'host')
        self.assertEqual(result['branch'], 'art')
        with self.assertRaises(ValueError):
            self.record(provider='codex', source_url='', stage='summary', source_id='codex:wrong-host')

    def test_pauses_block_new_messages_but_idempotent_receipt_survives_pause(self):
        initial = self.record()
        self.app.set_multi_ai_collaboration(self.conv['id'], {'codex_thread_id': THREAD,
            'providers': {'muse': {'enabled': False}}})
        with self.assertRaises(ValueError): self.record(source_id='muse:new')
        repeated = self.record()
        self.assertTrue(repeated['duplicate'])
        self.assertEqual(repeated['message'], initial['message'])
        self.app.set_collaboration(self.conv['id'], False)
        with self.assertRaises(ValueError):
            self.record(provider='codex', source_url='', source_id='codex:new')
        self.assertTrue(self.record()['duplicate'])
        self.assertFalse(self.conv['multi_ai_collaboration']['enabled'])

    def test_conflicting_source_id_preserves_original_record_and_disk(self):
        self.record()
        before = self.app.file.read_bytes()
        with self.assertRaises(ConflictError): self.record(text='Different response under same source ID')
        self.assertEqual(self.app.file.read_bytes(), before)
        self.assertEqual(len(self.conv['roundtable_messages']), 1)
        self.assertTrue(self.record()['duplicate'])

    def test_failed_state_write_rolls_back_memory_disk_identity_and_policy(self):
        self.record()
        before = copy.deepcopy(self.conv)
        disk = self.app.file.read_bytes()
        policy = self.app.coordination.path.read_bytes()
        turns = self.conv['turns']; turn = self.anchor
        def partial_failure(path, value):
            # Cover replacement followed by an I/O failure, not just no-op failure.
            write_json(path, value)
            raise OSError('simulated state failure')
        with patch('server.write_json', side_effect=partial_failure):
            with self.assertRaises(OSError): self.record(source_id='muse:failed-write')
        self.assertEqual(self.conv, before)
        self.assertEqual(self.app.file.read_bytes(), disk)
        self.assertEqual(self.app.coordination.path.read_bytes(), policy)
        self.assertIs(self.conv['turns'], turns)
        self.assertIs(self.conv['turns'][0], turn)

    def test_first_failed_write_does_not_leave_phantom_collection(self):
        before = copy.deepcopy(self.conv)
        with patch('server.write_json', side_effect=OSError('disk full')):
            with self.assertRaises(OSError): self.record()
        self.assertEqual(self.conv, before)
        self.assertNotIn('roundtable_messages', self.conv)

    def test_summary_is_body_free_and_receipts_follow_anchor_paging_and_filters(self):
        before = self.app.snapshot(summaries=True)['conversations'][0]['revision']
        self.record()
        art = self.turn(2, branch='art'); self.conv['turns'].append(art)
        self.record(anchor_turn_id=art['id'], source_id='muse:art')
        manual = self.turn(3, source='manual'); self.conv['turns'].append(manual)
        self.app.save(self.conv)
        summary = self.app.snapshot(summaries=True)['conversations'][0]
        self.assertNotIn('roundtable_messages', summary)
        self.assertEqual(summary['roundtable_message_count'], 2)
        self.assertNotEqual(summary['revision'], before)
        self.assertNotIn('Muse 的实际建议', json.dumps(summary, ensure_ascii=False))
        page = self.app.conversation_page(self.conv['id'], limit=1, source='codex')
        self.assertEqual([m['source_id'] for m in page['roundtable_messages']], ['muse:art'])
        main = self.app.conversation_page(self.conv['id'], branch='main', source='codex')
        self.assertEqual([m['source_id'] for m in main['roundtable_messages']], ['muse:observed-r1'])
        old = self.app.conversation_page(self.conv['id'], before=art['id'], limit=1)
        self.assertEqual([m['anchor_turn_id'] for m in old['roundtable_messages']], [self.anchor['id']])
        latest = self.app.conversation_page(self.conv['id'], limit=1)
        self.assertEqual(latest['roundtable_messages'], [])
        self.assertEqual(latest['roundtable_message_count'], 2)

    def test_full_backup_restores_receipts_and_portable_settings_exclude_them(self):
        message = self.record()['message']
        validate_state(self.app.state)
        settings = self.app.export_configuration({'font': 14, 'idleMs': 12000}, self.conv['id'])
        raw = json.dumps(settings, ensure_ascii=False)
        for forbidden in ('roundtable_messages', message['text'], message['source_url'], message['source_id']):
            self.assertNotIn(forbidden, raw)
        result = self.app.backups.create(self.app.state, {})
        path = self.app.data / 'backups' / (result['id'] + '.zip')
        with zipfile.ZipFile(path) as archive:
            stored = json.loads(archive.read('state.json'))
        self.assertEqual(stored['conversations'][0]['roundtable_messages'], [message])
        validate_state(stored)
        preview = self.app.backups.preview(path)
        restored, _ = self.app.backups.restore(preview['id'], self.app.state, {})
        self.assertEqual(restored['conversations'][0]['roundtable_messages'], [message])

    def test_malformed_stored_records_never_pass_full_backup_validation(self):
        self.record()
        for changes in ({'role': 'host'}, {'branch': 'art'}, {'provenance': 'official_cli'},
                        {'created_at': 'yesterday'}, {'created_at': '2026-11-18T12:00:00'},
                        {'created_at': True}, {'id': 'not-a-uuid'},
                        {'roundtable': dict(self.conv['roundtable'], host='muse')},
                        {'anchor_turn_id': 'f' * 32}, {'token': 'unexpected'}):
            state = copy.deepcopy(self.app.state)
            state['conversations'][0]['roundtable_messages'][0].update(changes)
            with self.subTest(changes=changes), self.assertRaises(ValueError): validate_state(state)
        for bad in ('not-list', [None]):
            broken = copy.deepcopy(self.conv); broken['roundtable_messages'] = bad
            with self.assertRaises(ValueError): validate_roundtable_messages(broken)

    def test_collection_limits_and_duplicate_stored_identity_are_enforced(self):
        message = self.record()['message']
        for key in ('id', 'source_id'):
            other = copy.deepcopy(message)
            other.update(id='f' * 32, source_id='muse:second')
            other[key] = message[key]
            broken = dict(self.conv, roundtable_messages=[message, other])
            with self.subTest(key=key), self.assertRaises(ValueError): validate_roundtable_messages(broken)
        with patch('multi_ai_collaboration.ROUNDTABLE_MESSAGE_LIMIT', 1):
            with self.assertRaises(ValueError): self.record(source_id='muse:too-many')
        with patch('multi_ai_collaboration.ROUNDTABLE_MESSAGE_BYTES', 1):
            with self.assertRaises(ValueError): validate_roundtable_messages(self.conv)
        self.assertEqual(len(self.conv['roundtable_messages']), 1)

    def test_service_stopping_or_maintenance_rejects_without_side_effects(self):
        for flag in ('stopping', 'maintenance'):
            setattr(self.app, flag, True)
            try:
                with self.subTest(flag=flag), self.assertRaises(ConflictError): self.record()
            finally: setattr(self.app, flag, False)
        self.assertNotIn('roundtable_messages', self.conv)

    def test_http_endpoint_requires_csrf_exact_identity_and_returns_canonical_record(self):
        httpd = ThreadingHTTPServer(('127.0.0.1', 0), Handler); httpd.app = self.app
        worker = threading.Thread(target=httpd.serve_forever, daemon=True); worker.start()
        path = '/api/conversations/' + self.conv['id'] + '/roundtable/messages'
        try:
            raw = http.client.HTTPConnection('127.0.0.1', httpd.server_port, timeout=3)
            raw.request('POST', path, json.dumps(self.body()), {'Content-Type': 'application/json'})
            response = raw.getresponse(); self.assertEqual(response.status, 403); response.read(); raw.close()
            client = Client('http://127.0.0.1:' + str(httpd.server_port))
            self.assertTrue(client.request('/api/service')['features']['roundtable_messages'])
            with self.assertRaises(ValueError): client.request(path, self.body(codex_thread_id=OTHER))
            result = client.request(path, self.body())
            self.assertTrue(result['recorded'])
            self.assertEqual(result['message']['provider'], 'muse')
            self.assertTrue(client.request(path, self.body())['duplicate'])
            with self.assertRaises(ValueError): client.request(path, self.body(text='conflict'))
        finally:
            httpd.shutdown(); httpd.server_close(); worker.join(3)


if __name__ == '__main__':
    unittest.main(verbosity=2)
