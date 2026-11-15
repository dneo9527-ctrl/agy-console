"""Cross-window monitoring checks. Local fixtures only; no model requests."""
import json
import http.client
from pathlib import Path
import sqlite3
import tempfile
import threading
import unittest
import uuid
from unittest.mock import patch

from activity_monitor import ActivityMonitor, read_rows, visible_step, request_title

SID = '11111111-2222-3333-4444-555555555555'
SID2 = '66666666-7777-8888-9999-aaaaaaaaaaaa'


def integer(value):
    out = bytearray()
    while value > 127:
        out.append((value & 127) | 128)
        value >>= 7
    out.append(value)
    return bytes(out)


def number(field, value):
    return integer(field << 3) + integer(value)


def blob(field, data):
    if isinstance(data, str):
        data = data.encode()
    return integer((field << 3) | 2) + integer(len(data)) + data


def payload(kind, content, status=3):
    # Visible response field is whitelisted; thought field is deliberately distinct.
    data = blob(2 if kind == 14 else 1, content)
    if kind == 15:
        data += blob(2, 'PRIVATE_REASONING_MUST_NOT_APPEAR')
    return number(1, kind) + number(4, status) + blob(19 if kind == 14 else 20, data)


class ActivityTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        (self.root / 'conversations').mkdir()
        (self.root / 'presence').mkdir()
        self.index = self.root / 'conversation_summaries.db'
        with sqlite3.connect(self.index) as db:
            db.execute('CREATE TABLE conversation_summaries (conversation_id TEXT,title TEXT,workspace_uris TEXT,'
                       'status TEXT,not_fully_idle INTEGER,killed INTEGER,last_modified_time TEXT,'
                       'step_count INTEGER,parent_conversation_id TEXT)')
        self.live = [{'pid': 555, 'parent_pid': 222, 'program': 'agy', 'terminal': 'ttys004',
                      'elapsed': '00:10', 'origin': 'codex', 'window_label': '终端 ttys004'}]
        self.owners = {SID: [555]}
        self.monitor = ActivityMonitor(self.root, '/fake/agy',
                                       process_reader=lambda *_: self.live,
                                       owner_reader=lambda *_: self.owners)

    def tearDown(self):
        self.temp.cleanup()

    def summary(self, sid=SID, active=1):
        with sqlite3.connect(self.index) as db:
            db.execute('DELETE FROM conversation_summaries WHERE conversation_id=?', (sid,))
            db.execute('INSERT INTO conversation_summaries VALUES (?,?,?,?,?,?,?,?,?)',
                       (sid, '外部研究任务', '["file:///project/workspace"]',
                        'CASCADE_RUN_STATUS_RUNNING' if active else 'CASCADE_RUN_STATUS_IDLE', active,
                        0, '2026-11-11 15:00:00+00:00', 2, ''))

    def session_db(self):
        path = self.root / 'conversations' / (SID + '.db')
        db = sqlite3.connect(path)
        db.execute('CREATE TABLE steps (idx INTEGER PRIMARY KEY,step_type INTEGER,status INTEGER,metadata BLOB,step_payload BLOB)')
        db.execute('INSERT INTO steps VALUES (0,14,3,NULL,?)', (payload(14, '原窗口请求'),))
        db.execute('INSERT INTO steps VALUES (1,15,2,NULL,?)', (payload(15, '正在输出第一句', 2),))
        db.commit()
        return path, db

    def test_running_idle_and_exited_are_not_conflated(self):
        self.summary()
        row = self.monitor.scan(force=True)['sessions'][0]
        self.assertEqual(row['status'], 'running')
        self.assertEqual(row['processes'][0]['pid'], 555)
        self.summary(active=0)
        self.assertEqual(self.monitor.scan(force=True)['sessions'][0]['status'], 'idle')
        self.live, self.owners = [], {}
        self.assertEqual(self.monitor.scan(force=True)['sessions'][0]['status'], 'ended')
        self.summary(active=1)
        self.assertEqual(self.monitor.scan(force=True)['sessions'][0]['status'], 'unknown')

    def test_title_is_first_request_even_when_detail_only_contains_later_messages(self):
        self.summary()
        _, db = self.session_db()
        try:
            db.execute('UPDATE steps SET step_payload=? WHERE idx=0', (payload(14, '/plan 请检查论文统计方法和缺失值处理'),))
            for idx in range(2, 185):
                db.execute('INSERT INTO steps VALUES (?,15,3,NULL,?)', (idx, payload(15, '后续回复')))
            db.execute('INSERT INTO steps VALUES (185,14,3,NULL,?)', (payload(14, '后续问题不能覆盖原始标题'),))
            db.commit()
            session = self.monitor.scan(force=True)['sessions'][0]
            self.assertEqual(session['title'], '请检查论文统计方法和缺失值处理')
            self.assertEqual(session['request_title'], session['title'])
            self.assertNotIn('原窗口请求', [m['text'] for m in self.monitor.detail(SID)['messages']])
        finally:
            db.close()

    def test_title_removes_cli_prefix_and_uses_actual_goal_before_reference_material(self):
        self.assertEqual(request_title('/plan 用户希望 Codex 和 Antigravity 一起静态分析 ServerCat 订阅代码'), '静态分析 ServerCat 订阅代码')
        self.assertEqual(request_title('/plan You are an independent reviewer.\n\nGoal: Diagnose a native app startup crash.\n\n<reference_material>\nGoal: unrelated reference\n</reference_material>'), 'Diagnose a native app startup crash.')
        self.assertEqual(request_title('<USER_REQUEST>/plan 检查统计代码</USER_REQUEST><ADDITIONAL_METADATA>PRIVATE_SYSTEM</ADDITIONAL_METADATA>'), '检查统计代码')
        self.assertEqual(request_title('/plan 请润色论文\n<reference_material>\n目标：别把资料当任务\n</reference_material>'), '请润色论文')
        self.assertLessEqual(len(request_title('/plan ' + '内容' * 100)), 59)

    def test_title_falls_back_to_beginning_of_log_without_returning_private_fields(self):
        self.summary()
        logs = self.root / 'brain' / SID / '.system_generated/logs'
        logs.mkdir(parents=True)
        events = [{'type': 'SYSTEM_MESSAGE', 'content': 'PRIVATE_SYSTEM'},
                  {'type': 'USER_INPUT', 'content': '<USER_REQUEST>/plan 请润色研究摘要</USER_REQUEST><ADDITIONAL_METADATA>PRIVATE_METADATA</ADDITIONAL_METADATA>', 'thinking': 'PRIVATE_THOUGHT'},
                  {'type': 'USER_INPUT', 'content': '后续问题'}]
        file = logs / 'transcript_full.jsonl'
        file.write_text('\n'.join(json.dumps(e, ensure_ascii=False) for e in events))
        before = file.read_bytes()
        session = self.monitor.scan(force=True)['sessions'][0]
        self.assertEqual(session['title'], '请润色研究摘要')
        self.assertNotIn('PRIVATE_', session['title'])
        self.assertEqual(file.read_bytes(), before)

    def test_known_title_is_cached_while_new_messages_arrive(self):
        self.summary()
        _, db = self.session_db()
        try:
            self.monitor.scan(force=True)
            db.execute('UPDATE steps SET step_payload=? WHERE idx=1', (payload(15, '新回复'),))
            db.commit()
            with patch('activity_monitor.read_rows', wraps=read_rows) as reads:
                self.assertEqual(self.monitor.scan(force=True)['sessions'][0]['title'], '原窗口请求')
                self.assertFalse(any('FROM steps' in call.args[1] for call in reads.call_args_list))
        finally:
            db.close()

    def test_new_session_title_is_retried_after_request_becomes_available(self):
        self.summary()
        self.assertEqual(self.monitor.scan(force=True)['sessions'][0]['request_title'], '')
        _, db = self.session_db()
        db.close()
        deadline = self.monitor.titles[SID]['retry_at'] + 1
        with patch('activity_monitor.time.monotonic', return_value=deadline):
            self.assertEqual(self.monitor.scan(force=True)['sessions'][0]['request_title'], '原窗口请求')

    def test_discover_other_window_without_panel_submit_and_update_response(self):
        self.summary()
        _, db = self.session_db()
        try:
            self.monitor.scan(force=True)
            first = self.monitor.detail(SID)
            self.assertTrue(first['read_only'])
            self.assertEqual([m['text'] for m in first['messages']], ['原窗口请求', '正在输出第一句'])
            self.assertEqual(first['messages'][-1]['status'], 'streaming')
            db.execute('UPDATE steps SET status=3,step_payload=? WHERE idx=1', (payload(15, '正在输出第一句。现在完整回复。'),))
            db.commit()
            second = self.monitor.detail(SID)
            self.assertEqual(second['messages'][-1]['text'], '正在输出第一句。现在完整回复。')
            self.assertEqual(second['messages'][-1]['status'], 'done')
            self.assertNotIn('PRIVATE_REASONING', json.dumps(second))
        finally:
            db.close()

    def test_live_wal_read_is_fresh_and_does_not_write_source_files(self):
        path, db = self.session_db()
        try:
            db.execute('PRAGMA journal_mode=WAL')
            db.execute('UPDATE steps SET step_payload=? WHERE idx=1', (payload(15, 'WAL 中的新回复'),))
            db.commit()
            wal = Path(str(path) + '-wal')
            before = (path.read_bytes(), wal.read_bytes())
            rows = read_rows(path, 'SELECT step_payload FROM steps WHERE idx=1')
            self.assertIn('WAL 中的新回复'.encode(), rows[0]['step_payload'])
            self.assertEqual((path.read_bytes(), wal.read_bytes()), before)
        finally:
            db.close()

    def test_compatible_log_fallback_does_not_show_an_internal_warning(self):
        self.summary()
        _, db = self.session_db()
        logs = self.root / 'brain' / SID / '.system_generated/logs'
        logs.mkdir(parents=True)
        (logs/'transcript_full.jsonl').write_text(json.dumps({'step_index':1, 'type':'PLANNER_RESPONSE', 'status':'DONE', 'content':'完整的官方日志回复'}))
        try:
            db.execute('UPDATE steps SET step_payload=? WHERE idx=1', (b'\x80',))
            db.commit()
            self.monitor.scan(force=True)
            detail = self.monitor.detail(SID)
            self.assertEqual(detail['note'], '')
            self.assertIn('完整的官方日志回复', [m['text'] for m in detail['messages']])
        finally:
            db.close()

    def test_missing_message_is_still_reported_when_both_sources_cannot_supply_it(self):
        self.summary()
        _, db = self.session_db()
        try:
            db.execute('UPDATE steps SET step_payload=? WHERE idx=1', (b'\x80',))
            db.commit()
            self.monitor.scan(force=True)
            detail = self.monitor.detail(SID)
            self.assertIn('部分消息尚未同步', detail['note'])
        finally:
            db.close()

    def test_log_fallback_strips_internal_user_metadata_and_never_returns_thinking(self):
        self.summary()
        logs = self.root / 'brain' / SID / '.system_generated/logs'
        logs.mkdir(parents=True)
        file = logs / 'transcript_full.jsonl'
        events = [{'step_index': 0, 'type': 'USER_INPUT', 'status': 'DONE',
                   'content': '<USER_REQUEST>用户的完整问题</USER_REQUEST><ADDITIONAL_METADATA>PRIVATE_SYSTEM</ADDITIONAL_METADATA>'},
                  {'step_index': 1, 'type': 'PLANNER_RESPONSE', 'status': 'DONE',
                   'content': '可见回复', 'thinking': 'PRIVATE_REASONING'},
                  {'step_index': 2, 'type': 'SYSTEM_MESSAGE', 'content': 'PRIVATE_SYSTEM'}]
        file.write_text('\n'.join(json.dumps(e, ensure_ascii=False) for e in events) + '\n{"incomplete":')
        self.monitor.scan(force=True)
        detail = self.monitor.detail(SID)
        self.assertEqual([m['text'] for m in detail['messages']], ['用户的完整问题', '可见回复'])
        self.assertNotIn('PRIVATE_', json.dumps(detail))

    def test_new_activity_after_initial_scan_and_unmatched_process_are_visible(self):
        self.owners = {}
        first = self.monitor.scan(force=True)
        self.assertEqual(len(first['unmatched_processes']), 1)
        self.summary()
        self.owners = {SID: [555]}
        new = self.monitor.scan(force=True)
        self.assertEqual(len(new['sessions']), 1)
        self.assertEqual(new['unmatched_processes'], [])

    def test_no_credential_file_read_or_path_injection(self):
        (self.root / 'credentials.json').write_text('SECRET_TOKEN')
        self.summary()
        self.monitor.scan(force=True)
        with self.assertRaises(ValueError):
            self.monitor.detail('../../credentials.json')
        with self.assertRaises(KeyError):
            self.monitor.detail(SID2)
        self.assertNotIn('SECRET_TOKEN', json.dumps(self.monitor.inventory))

    def test_visible_step_is_strict_about_supported_schema(self):
        self.assertIsNone(visible_step({'idx': 2, 'step_type': 99, 'step_payload': b'secret'}))
        with self.assertRaises(ValueError):
            visible_step({'idx': 0, 'step_type': 14, 'step_payload': b'\xff'})

    def test_process_check_failure_is_unknown_not_claimed_exited(self):
        self.summary(active=0)
        def fail(*_args):
            raise OSError('unavailable')
        self.monitor.process_reader = fail
        state = self.monitor.scan(force=True)
        self.assertEqual(state['sessions'][0]['status'], 'unknown')
        self.assertTrue(state['warning'])

    def test_active_session_is_included_even_outside_recent_history_limit(self):
        self.summary()
        with sqlite3.connect(self.index) as db:
            db.execute('UPDATE conversation_summaries SET last_modified_time=? WHERE conversation_id=?',
                       ('0001-01-01 00:00:00+00:00', SID))
            for index in range(201):
                db.execute('INSERT INTO conversation_summaries VALUES (?,?,?,?,?,?,?,?,?)',
                           (str(uuid.UUID(int=index + 1)), 'history', '[]', 'CASCADE_RUN_STATUS_IDLE', 0,
                            0, '2026-11-11 15:00:00+00:00', 1, ''))
        state = self.monitor.scan(force=True)
        row = next(s for s in state['sessions'] if s['id'] == SID)
        self.assertTrue(row['active'])
        self.assertEqual(row['status'], 'running')

    def test_http_external_monitor_reads_without_creating_panel_jobs(self):
        from server import Console, Handler, ThreadingHTTPServer
        self.summary()
        _, database = self.session_db()
        database.close()
        app = Console(self.root / 'panel-data', '/fake/agy', import_history=False)
        app.activity = self.monitor
        httpd = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
        httpd.app = app
        thread = threading.Thread(target=httpd.serve_forever, daemon=True)
        thread.start()
        def request(method, path, host=None):
            client = http.client.HTTPConnection('127.0.0.1', httpd.server_port, timeout=3)
            try:
                headers = {'Content-Type': 'application/json', 'X-Console-Token': app.token}
                if host:
                    headers['Host'] = host
                client.request(method, path, '{}' if method == 'POST' else None, headers)
                response = client.getresponse()
                return response.status, json.loads(response.read())
            finally:
                client.close()
        try:
            before = app.file.read_bytes()
            code, listing = request('GET', '/api/activity')
            self.assertEqual(code, 200)
            self.assertEqual(listing['sessions'][0]['id'], SID)
            code, detail = request('GET', '/api/activity/sessions/' + SID)
            self.assertEqual(code, 200)
            self.assertEqual(detail['messages'][0]['text'], '原窗口请求')
            self.assertEqual(request('POST', '/api/conversations/' + SID + '/turns')[0], 404)
            self.assertEqual(request('GET', '/api/activity', 'not-local.example')[0], 403)
            self.assertEqual(app.file.read_bytes(), before)
            self.assertTrue(app.jobs.empty())
            self.assertEqual(app.processes, {})
        finally:
            httpd.shutdown()
            httpd.server_close()
            thread.join(timeout=2)
            app.close()


if __name__ == '__main__':
    unittest.main(verbosity=2)
