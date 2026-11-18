"""Synthetic CLI/receiver only: never opens installed Bark or contacts a model."""
import copy
import json
import os
from pathlib import Path
import subprocess
import tempfile
import threading
import time
import unittest
from unittest.mock import patch

import server
from server import BUSY, Console, DEFAULT_MODEL, bark_notification_payload, notify_bark
from test_server import FAKE
import test_quota_failover as quota_fixture


class BarkBridgeTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.script = self.root / 'bark.py'
        self.script.write_text('# synthetic receiver; never executed\n')
        self.script.chmod(0o700)
        self.payload = bark_notification_payload('completed', 'a' * 32, 'b' * 32, '窗口\n\x00\u202e 名称')

    def test_payload_has_only_short_identity_and_final_outcome(self):
        self.assertEqual(set(self.payload), {'status', 'conversation_id', 'turn_id', 'chat_title'})
        self.assertEqual(self.payload['chat_title'], '窗口 名称')
        self.assertLessEqual(len(bark_notification_payload('failed', 'a' * 32, 'b' * 32, '长' * 900)['chat_title']), 80)
        for status in ('queued', 'running', 'cancelling', 'cancelled', 'interrupted'):
            self.assertIsNone(bark_notification_payload(status, 'a' * 32, 'b' * 32, '名称'))
        for cid, tid in [('bad\n', 'b' * 32), ('a' * 32, '../unsafe')]:
            self.assertIsNone(bark_notification_payload('completed', cid, tid, '名称'))

    def test_safe_receiver_gets_json_stdin_and_bounded_isolated_python(self):
        with patch.object(server, 'BARK_SCRIPT', self.script), patch.object(server.sys, 'platform', 'darwin'), \
                patch.object(server.subprocess, 'run') as run:
            notify_bark(self.payload)
        self.assertEqual(run.call_args.args[0], ['/usr/bin/python3', '-I', '-B', str(self.script), 'agy-event'])
        self.assertEqual(json.loads(run.call_args.kwargs['input']), self.payload)
        self.assertEqual(run.call_args.kwargs['timeout'], 1)
        self.assertEqual(run.call_args.kwargs['stdout'], subprocess.DEVNULL)
        self.assertEqual(run.call_args.kwargs['stderr'], subprocess.DEVNULL)

    def test_missing_symlink_writable_or_wrong_owner_receiver_is_skipped(self):
        link = self.root / 'link.py'; link.symlink_to(self.script)
        for script in (self.root / 'missing.py', link):
            with self.subTest(script=script), patch.object(server, 'BARK_SCRIPT', script), \
                    patch.object(server.sys, 'platform', 'darwin'), patch.object(server.subprocess, 'run') as run:
                notify_bark(self.payload); run.assert_not_called()
        for mode in (0o720, 0o702):
            self.script.chmod(mode)
            with patch.object(server, 'BARK_SCRIPT', self.script), patch.object(server.sys, 'platform', 'darwin'), \
                    patch.object(server.subprocess, 'run') as run:
                notify_bark(self.payload); run.assert_not_called()
        self.script.chmod(0o700)
        with patch.object(server, 'BARK_SCRIPT', self.script), patch.object(server.sys, 'platform', 'darwin'), \
                patch.object(server.os, 'getuid', return_value=os.getuid() + 1), patch.object(server.subprocess, 'run') as run:
            notify_bark(self.payload); run.assert_not_called()

    def test_unknown_fields_and_non_macos_are_never_forwarded(self):
        with patch.object(server, 'BARK_SCRIPT', self.script), patch.object(server.sys, 'platform', 'darwin'), \
                patch.object(server.subprocess, 'run') as run:
            notify_bark({**self.payload, 'response': 'must remain local'})
            run.assert_not_called()
        with patch.object(server.sys, 'platform', 'linux'), patch.object(server.subprocess, 'run') as run:
            notify_bark(self.payload); run.assert_not_called()

    def test_receiver_timeout_or_launch_failure_is_swallowed(self):
        for error in (subprocess.TimeoutExpired('synthetic', 1), OSError('synthetic failure')):
            with patch.object(server, 'BARK_SCRIPT', self.script), patch.object(server.sys, 'platform', 'darwin'), \
                    patch.object(server.subprocess, 'run', side_effect=error):
                self.assertIsNone(notify_bark(self.payload))


class BarkLifecycleTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.binary = self.root / 'fake-agy'
        self.binary.write_text(FAKE); self.binary.chmod(0o700)
        self.calls = []
        self.received = threading.Event()
        self.app = Console(self.root / 'data', self.binary, import_history=False,
                           account_reader=lambda *_a, **_k: {'state': 'unknown', 'email': None},
                           notification_sender=self.record)
        self.conv = self.app.create('通知窗口')

    def tearDown(self):
        self.app.close()
        self.temp.cleanup()

    def record(self, payload):
        # A sender can inspect the already-persisted synthetic state without a
        # console lock; no task prompt/result/error fields are handed to it.
        owned = self.app.lock._is_owned()
        saved = json.loads(self.app.file.read_text())
        self.calls.append((copy.deepcopy(payload), owned, saved))
        self.received.set()

    def wait(self, turn):
        until = time.monotonic() + 8
        while time.monotonic() < until:
            with self.app.lock:
                if turn['status'] not in BUSY:
                    return
            time.sleep(.02)
        self.fail('synthetic turn did not finish')

    def test_success_and_final_failure_emit_after_save_outside_lock(self):
        for prompt, expected in [('first', 'completed'), ('fail', 'failed')]:
            self.received.clear()
            turn = self.app.submit(self.conv['id'], prompt, DEFAULT_MODEL)
            self.wait(turn)
            self.assertTrue(self.received.wait(2))
            payload, owned, saved = self.calls[-1]
            self.assertEqual(payload, {'status': expected, 'conversation_id': self.conv['id'],
                                      'turn_id': turn['id'], 'chat_title': self.conv['title']})
            self.assertFalse(owned)
            row = next(t for c in saved['conversations'] for t in c['turns'] if t['id'] == turn['id'])
            self.assertEqual(row['status'], expected)
        self.assertEqual(len(self.calls), 2)

    def test_running_and_queued_cancel_do_not_notify(self):
        slow = self.app.submit(self.conv['id'], 'slow', DEFAULT_MODEL)
        until = time.monotonic() + 5
        while not slow['response'] and time.monotonic() < until: time.sleep(.02)
        self.assertTrue(slow['response'])
        other = self.app.create('待取消窗口')
        queued = self.app.submit(other['id'], 'queued', DEFAULT_MODEL)
        self.app.stop(queued['id']); self.app.stop(slow['id'])
        self.wait(slow); self.wait(queued)
        # A subsequent turn proves worker cleanup and cancellation handoff ended.
        next_turn = self.app.submit(self.conv['id'], 'first', DEFAULT_MODEL)
        self.wait(next_turn)
        self.assertTrue(self.received.wait(2))
        self.assertEqual([c[0]['turn_id'] for c in self.calls], [next_turn['id']])

    def test_single_quota_retry_notifies_only_its_final_outcome(self):
        conv, _ = quota_fixture.QuotaFailoverTests.prepare(self)
        turn = self.app.submit(conv['id'], 'work', '')
        self.wait(turn)
        self.assertTrue(self.received.wait(2))
        self.assertEqual(turn['status'], 'completed')
        self.assertEqual(len(turn['attempts']), 2)
        self.assertEqual([c[0]['status'] for c in self.calls], ['completed'])

    def test_exhausted_single_retry_emits_one_final_failure(self):
        conv, _ = quota_fixture.QuotaFailoverTests.prepare(self, mode='twice')
        turn = self.app.submit(conv['id'], 'work', '')
        self.wait(turn)
        self.assertTrue(self.received.wait(2))
        self.assertEqual(turn['status'], 'failed')
        self.assertEqual(len(turn['attempts']), 2)
        self.assertEqual([c[0]['status'] for c in self.calls], ['failed'])

    def test_adapter_exception_does_not_change_result_or_kill_worker(self):
        observed = []
        def broken(payload):
            observed.append(payload); self.received.set(); raise RuntimeError('synthetic adapter failure')
        self.app.notification_sender = broken
        for prompt, expected in [('first', 'completed'), ('fail', 'failed'), ('followup', 'completed')]:
            self.received.clear()
            turn = self.app.submit(self.conv['id'], prompt, DEFAULT_MODEL)
            self.wait(turn); self.assertTrue(self.received.wait(2))
            self.assertEqual(turn['status'], expected)
        self.assertEqual(len(observed), 3)
        self.assertTrue(self.app.worker_thread.is_alive())

    def test_reopening_synthetic_history_does_not_replay(self):
        turn = self.app.submit(self.conv['id'], 'first', DEFAULT_MODEL)
        self.wait(turn); self.assertTrue(self.received.wait(2))
        self.app.close(); self.calls.clear(); self.received.clear()
        self.app = Console(self.root / 'data', self.binary, import_history=False,
                           notification_sender=self.record)
        self.assertFalse(self.received.wait(.1))
        self.assertFalse(self.calls)


if __name__ == '__main__': unittest.main(verbosity=2)
