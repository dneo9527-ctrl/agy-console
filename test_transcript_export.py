"""Markdown scope, completeness and HTTP attachment checks; no real AI calls."""
import copy
import http.client
import json
from pathlib import Path
import tempfile
import threading
import unittest
from unittest.mock import patch

from multi_ai_collaboration import new_meeting, record_meeting_event, link_meeting_turn
from server import Console, Handler, ThreadingHTTPServer
from test_roundtable_meetings import conversation, THREAD, OTHER, SESSION, MID, STAMP, LATER, URL
from transcript_export import export_markdown, attachment


def fixture():
    conv = conversation()
    conv.update(id='a' * 32, title='研究：会话/测试', created_at=STAMP, updated_at=LATER)
    conv['branches']['main']['title'] = '文字会话'
    conv['branches']['art']['title'] = '生图会话'
    for index in range(45):
        conv['turns'].append({'id': format(index + 30, '032x'), 'branch': 'main',
            'source': 'console' if index % 2 else 'codex', 'prompt': '请求-' + str(index),
            'response': '回复-' + str(index), 'status': 'completed', 'model': 'fixture-model',
            'created_at': '2026-11-18T01:%02d:00+00:00' % index,
            'ended_at': '2026-11-18T01:%02d:15+00:00' % index,
            'account': {'email': 'NOT-EXPORTED', 'token': 'NOT-EXPORTED'},
            'events': [{'secret': 'NOT-EXPORTED'}]})
    conv['turns'].append({'id': 'b' * 32, 'branch': 'art', 'source': 'console',
        'prompt': 'ART-ONLY', 'response': 'IMAGE-REPLY', 'model': 'fixture-image',
        'status': 'failed', 'created_at': STAMP, 'error': 'Image task stopped',
        'images': [{'id': 'c' * 64, 'name': '结果[1].png', 'path': '/private/not-exported'}]})
    return conv


def add_meeting(conv):
    meeting = new_meeting(conv, {'codex_thread_id': THREAD, 'source_id': 'export:meeting',
        'topic': '真实三方会议', 'branch': 'main'}, MID, STAMP)
    conv['roundtable_meetings'] = [meeting]
    turn = {'id': 'd' * 32, 'source': 'codex', 'kind': 'text', 'prompt': 'NATIVE-MEETING-REQUEST',
        'meeting_id': MID, 'meeting_stage': 'proposal', 'branch': 'main',
        'roundtable': copy.deepcopy(meeting['roundtable']), 'roundtable_role': 'participant',
        'status': 'completed', 'response': 'FULL-NATIVE-' + '正文' * 7000 + '-END',
        'expected_agy_id': SESSION, 'agy_id': SESSION, 'model': 'native-model',
        'created_at': STAMP, 'started_at': STAMP, 'ended_at': LATER}
    conv['turns'].append(turn)
    conv['roundtable_meetings'] = [link_meeting_turn(conv, MID, 'proposal', turn, STAMP)]
    sequence = 0
    def record(provider, stage='proposal', status='completed'):
        nonlocal sequence
        sequence += 1
        event = {'codex_thread_id': THREAD, 'source_id': 'export:event-' + str(sequence),
            'provider': provider, 'stage': stage, 'status': status,
            'text': provider.upper() + '-' + stage + '-SPEECH' if status == 'completed' else '',
            'source_url': URL if provider == 'muse' and status == 'completed' else '',
            'detail': 'Not available' if status == 'unavailable' else ''}
        value, _ = record_meeting_event(conv, MID, event, LATER)
        conv['roundtable_meetings'] = [value]
    record('codex'); record('muse')
    record('codex', 'review'); record('muse', 'review'); record('agy', 'review', 'unavailable')
    record('codex', 'summary')
    return turn


class TranscriptExportTests(unittest.TestCase):
    def test_complete_chronological_text_and_metadata_only_allowlist(self):
        conv = fixture()
        raw, filename = export_markdown(copy.deepcopy(conv), branch='main')
        self.assertIn('请求-0', raw); self.assertIn('回复-44', raw)
        self.assertLess(raw.index('请求-0'), raw.index('回复-0'))
        self.assertLess(raw.index('回复-0'), raw.index('请求-1'))
        self.assertIn('Codex → Agy', raw); self.assertIn('用户 → Agy', raw)
        self.assertIn('fixture-model', raw); self.assertIn('completed', raw)
        self.assertNotIn('ART-ONLY', raw); self.assertNotIn('NOT-EXPORTED', raw)
        self.assertNotIn('/private/', raw); self.assertTrue(filename.endswith('.md'))
        all_text, _ = export_markdown(copy.deepcopy(conv), origin='http://127.0.0.1:8765')
        self.assertIn('ART-ONLY', all_text)
        self.assertIn('http://127.0.0.1:8765/api/turns/' + 'b' * 32 + '/images/' + 'c' * 64, all_text)
        self.assertIn('结果\\[1\\].png', all_text)

    def test_meeting_contains_all_actual_speakers_and_full_native_once(self):
        conv = fixture(); native = add_meeting(conv)
        raw, filename = export_markdown(copy.deepcopy(conv), branch='main', meeting_id=MID)
        for value in ('CODEX-proposal-SPEECH', 'MUSE-proposal-SPEECH', 'CODEX-review-SPEECH',
                      'MUSE-review-SPEECH', 'CODEX-summary-SPEECH', URL, 'unavailable'):
            self.assertIn(value, raw)
        self.assertEqual(raw.count(native['response']), 1)
        self.assertEqual(raw.count('NATIVE-MEETING-REQUEST'), 1)
        self.assertIn('完整回复', raw)
        self.assertNotIn('请求-0', raw); self.assertNotIn('ART-ONLY', raw)
        self.assertIn('圆桌', filename)
        normal, _ = export_markdown(copy.deepcopy(conv), branch='main')
        self.assertIn(native['response'], normal); self.assertIn('MUSE-proposal-SPEECH', normal)

    def test_scope_never_falls_back_to_other_chat_branch_or_meeting(self):
        conv = fixture(); add_meeting(conv)
        for kwargs in ({'branch': 'missing'}, {'branch': 'art', 'meeting_id': MID},
                       {'meeting_id': 'e' * 32}, {'codex_thread_id': OTHER}):
            with self.subTest(kwargs=kwargs), self.assertRaises(KeyError):
                export_markdown(copy.deepcopy(conv), **kwargs)
        for kwargs in ({'branch': '../main'}, {'meeting_id': '../bad'}):
            with self.subTest(kwargs=kwargs), self.assertRaises(ValueError):
                export_markdown(copy.deepcopy(conv), **kwargs)

    def test_active_partial_output_preserved_and_filename_header_safe(self):
        conv = fixture()
        conv['title'] = '../标题\r\nInjected: yes/".md'
        conv['turns'][0].update(status='running', response='PARTIAL-OUTPUT', ended_at=None)
        raw, filename = export_markdown(copy.deepcopy(conv), branch='main')
        self.assertIn('running', raw); self.assertIn('PARTIAL-OUTPUT', raw)
        header = attachment(filename, conv['id'])
        for char in ('\r', '\n', '/', '\\'):
            self.assertNotIn(char, filename)
        self.assertNotIn('\r', header); self.assertNotIn('\n', header)
        self.assertIn("filename*=UTF-8''", header)

    def test_prompt_fences_and_unfinished_reply_do_not_consume_next_speaker(self):
        conv = fixture()
        prompt = 'User sample\n```python\nprint(1)\n```\n````\n## fake speaker'
        conv['turns'][0].update(prompt=prompt, response='```python\nprint("unfinished")')
        raw, _ = export_markdown(copy.deepcopy(conv), branch='main')
        self.assertIn('`````text\n' + prompt + '\n`````', raw)
        self.assertIn('```python\nprint("unfinished")\n```\n\n## 用户 → Agy', raw)
        self.assertIn('请求-1', raw)

    def test_legacy_actual_receipt_is_kept_with_its_source(self):
        conv = fixture()
        anchor = conv['turns'][0]
        anchor.update(agy_id=SESSION, roundtable=copy.deepcopy(conv['roundtable']), roundtable_role='participant')
        conv['roundtable_messages'] = [{'id': 'f' * 32, 'source_id': 'legacy:muse',
            'anchor_turn_id': anchor['id'], 'provider': 'muse', 'stage': 'proposal',
            'role': 'participant', 'text': 'LEGACY-ACTUAL-MUSE', 'source_url': URL,
            'created_at': STAMP, 'roundtable': copy.deepcopy(conv['roundtable']),
            'branch': 'main', 'provenance': 'codex_observed'}]
        raw, _ = export_markdown(copy.deepcopy(conv), branch='main')
        self.assertIn('LEGACY-ACTUAL-MUSE', raw); self.assertIn(URL, raw)
        art, _ = export_markdown(copy.deepcopy(conv), branch='art')
        self.assertNotIn('LEGACY-ACTUAL-MUSE', art)


class TranscriptExportHTTPTests(unittest.TestCase):
    def test_http_scope_attachment_and_no_state_changes(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory); binary = root / 'never-agy'
            binary.write_text('#!/bin/sh\nexit 99\n'); binary.chmod(0o700)
            with patch('server.SoftwareUpdates.start'):
                app = Console(root / 'data', binary, import_history=False,
                    account_reader=lambda *_a, **_k: {'state': 'unknown', 'email': None},
                    coordination_path=root / 'coordination.json')
            httpd = ThreadingHTTPServer(('127.0.0.1', 0), Handler); httpd.app = app
            worker = threading.Thread(target=httpd.serve_forever, daemon=True); worker.start()
            conv = fixture(); add_meeting(conv)
            with app.lock:
                app.state['conversations'].append(conv); app.save(conv)
                before = copy.deepcopy(app.state)
                disk = app.file.read_bytes()
            def get(query='', host=None):
                client = http.client.HTTPConnection('127.0.0.1', httpd.server_port, timeout=3)
                try:
                    client.request('GET', '/api/conversations/' + conv['id'] + '/export' + query,
                                   headers={'Host': host} if host else {})
                    response = client.getresponse()
                    return response.status, dict(response.getheaders()), response.read().decode()
                finally:
                    client.close()
            try:
                code, headers, raw = get('?branch=main&meeting=' + MID + '&codex=' + THREAD)
                self.assertEqual(code, 200); self.assertIn('text/markdown', headers['Content-Type'])
                self.assertIn('filename*=UTF-8', headers['Content-Disposition'])
                self.assertIn('MUSE-proposal-SPEECH', raw); self.assertNotIn('ART-ONLY', raw)
                self.assertEqual(get()[0], 200)
                for query, code in (('?branch=main&branch=art', 400), ('?unexpected=x', 400),
                                    ('?branch=', 400), ('?meeting=', 400), ('?codex=', 400),
                                    ('?branch=../main', 400), ('?codex=' + OTHER, 404),
                                    ('?branch=art&meeting=' + MID, 404)):
                    self.assertEqual(get(query)[0], code, query)
                self.assertEqual(get(host='elsewhere.example')[0], 403)
                with app.lock:
                    self.assertEqual(app.state, before)
                self.assertEqual(app.file.read_bytes(), disk)
                self.assertFalse(app.processes)
            finally:
                httpd.shutdown(); httpd.server_close(); worker.join(3); app.close()


if __name__ == '__main__':
    unittest.main(verbosity=2)
