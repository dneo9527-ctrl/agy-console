# coding: utf-8
"""Context failures do not consume the quota retry path; disposable CLI only."""
import copy
import unittest
import quota_failover
import test_quota_failover as fixtures
from test_quota_failover import ARGON, SESSION


class ContextFailureTests(unittest.TestCase):
    def test_context_takes_priority_over_mixed_quota_diagnostic(self):
        for error in (
            'RESOURCE_EXHAUSTED: input token count exceeds maximum; quota exceeded',
            'context_length_exceeded: quota exhausted',
            'prompt is too long: Individual quota reached',
            '上下文过长；quota exceeded',
            'maximum context length is 32768; quota depleted',
        ):
            with self.subTest(error=error):
                self.assertFalse(quota_failover.quota_rejection(error))

    def test_context_detector_does_not_invent_overflow_from_generic_limits(self):
        detect = getattr(quota_failover, 'context_rejection', lambda value: False)
        self.assertTrue(detect('prompt_too_long'))
        self.assertTrue(detect('input tokens exceed the maximum allowed tokens'))
        for error in (None, {}, 'RESOURCE_EXHAUSTED', '429 too many requests',
                      'Individual quota reached. Resets in 1h.',
                      'context deadline exceeded', 'token bucket rate limit'):
            with self.subTest(error=error):
                self.assertFalse(detect(error))

    def test_rate_and_payload_limits_are_not_reported_as_context_overflow(self):
        for error in (
            'quota exceeded: input tokens exceed the tokens-per-minute allowance',
            'quota exceeded: request exceeds the maximum number of tokens per minute for this model',
            'quota exceeded: rate_limit_exceeded',
        ):
            with self.subTest(error=error):
                self.assertFalse(quota_failover.context_rejection(error))
                self.assertFalse(quota_failover.quota_rejection(error))
        self.assertFalse(quota_failover.context_rejection('request payload size exceeds 20 MB'))
        self.assertTrue(quota_failover.context_rejection('context_length_exceeded; rate limit diagnostic'))


class ContextFailureWorkerTests(unittest.TestCase):
    setUp = fixtures.QuotaFailoverTests.setUp
    tearDown = fixtures.QuotaFailoverTests.tearDown
    prepare = fixtures.QuotaFailoverTests.prepare
    calls = fixtures.QuotaFailoverTests.calls
    wait = fixtures.QuotaFailoverTests.wait

    def test_mixed_current_official_error_neither_retries_nor_blocks_models(self):
        conv, _ = self.prepare()
        before = copy.deepcopy(self.app.health['account'])
        error = 'RESOURCE_EXHAUSTED: input token count exceeds maximum; quota exceeded'
        self.binary.write_text(self.binary.read_text().replace(
            'Individual quota reached. Resets in 1h.', error))
        turn = self.app.submit(conv['id'], 'context fixture work', '')
        self.wait(turn)
        with self.app.lock:
            self.assertEqual(turn['status'], 'failed')
            self.assertEqual(turn['error'], error)
            self.assertEqual(len(self.calls(conv)), 1)
            self.assertEqual(turn['model'], ARGON)
            self.assertEqual(turn['agy_id'], SESSION)
            self.assertEqual(turn.get('failure_kind'), 'context_length')
            self.assertIn('上下文过长', turn.get('fallback_notice', ''))
            self.assertIn('未自动', turn['fallback_notice'])
            self.assertNotIn('已压缩', turn['fallback_notice'])
            self.assertFalse(self.app.fallback_blocks)
            self.assertEqual(self.app.health['account'], before)
            self.assertEqual(len(turn['attempts']), 1)


if __name__ == '__main__':
    unittest.main()
