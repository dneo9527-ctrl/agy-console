"""Receipt ingestion is exact-chat metadata, never a new model dispatch."""
import contextlib
import io
import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import bridge

THREAD = '11111111-2222-3333-4444-555555555555'
OTHER = '66666666-7777-8888-9999-aaaaaaaaaaaa'
CID = 'b' * 32


class ReceiptClient:
    def __init__(self, supported=True, fail=False):
        self.features = {'roundtable_messages': supported}
        self.calls = []
        self.bindings = []
        self.fail = fail

    def bind(self, thread):
        self.bindings.append(thread)
        return {'id': CID, 'codex_thread_id': thread}

    def request(self, path, body):
        self.calls.append((path, body))
        if self.fail:
            raise ValueError('receipt disk write failed')
        return {'recorded': True, 'duplicate': False, 'message': dict(body, id='d' * 32)}


class ReceiptBridgeTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.path = Path(self.tmp.name) / 'speech.json'
        self.body = {'codex_thread_id': THREAD, 'anchor_turn_id': 'c' * 32,
                     'provider': 'muse', 'stage': 'proposal', 'text': 'Actual observed response.',
                     'source_url': 'https://muse.ai/thread/' + THREAD, 'source_id': 'test-muse-round1'}
        self.path.write_text(json.dumps(self.body), encoding='utf-8')

    def invoke(self, client, *extra):
        output = io.StringIO()
        argv = ['bridge.py', '--thread-id', THREAD, '--record-message', str(self.path), *extra]
        with mock.patch.object(bridge.sys, 'argv', argv), mock.patch.object(bridge, 'Client', return_value=client), contextlib.redirect_stdout(output):
            result = bridge.main()
        return result, json.loads(output.getvalue())

    def test_records_only_in_current_binding_without_task_or_model(self):
        client = ReceiptClient()
        result, output = self.invoke(client)
        self.assertEqual(result, 0)
        self.assertTrue(output['recorded'])
        self.assertEqual(client.bindings, [THREAD])
        self.assertEqual(client.calls, [('/api/conversations/' + CID + '/roundtable/messages', self.body)])

    def test_other_chat_and_unknown_fields_fail_before_binding(self):
        for change in ({'codex_thread_id': OTHER}, {'unknown': 'not accepted'}):
            with self.subTest(change=change):
                self.path.write_text(json.dumps(dict(self.body, **change)), encoding='utf-8')
                client = ReceiptClient()
                with contextlib.redirect_stderr(io.StringIO()), self.assertRaises(SystemExit):
                    self.invoke(client)
                self.assertFalse(client.bindings)
                self.assertFalse(client.calls)

    def test_task_settings_and_result_directory_cannot_mix_with_receipt(self):
        for flags in (['--task-file', str(self.path)], ['--ensure'], ['--out', self.tmp.name],
                      ['--model', 'explicit-model'], ['--review-turn', 'c' * 32, '--review-file', str(self.path)]):
            with self.subTest(flags=flags):
                client = ReceiptClient()
                with contextlib.redirect_stderr(io.StringIO()), self.assertRaises(SystemExit):
                    self.invoke(client, *flags)
                self.assertFalse(client.bindings)
                self.assertFalse(client.calls)

    def test_unsupported_and_write_failure_do_not_report_success(self):
        client = ReceiptClient(supported=False)
        with self.assertRaises(ValueError):
            self.invoke(client)
        self.assertFalse(client.calls)
        client = ReceiptClient(fail=True)
        with self.assertRaisesRegex(ValueError, 'disk write failed'):
            self.invoke(client)
        self.assertEqual(len(client.calls), 1)


if __name__ == '__main__':
    unittest.main()
