"""Stage-scoped evidence reaches the real queue/worker; fake CLI only."""
import copy
import json
import threading
import unittest
from unittest.mock import patch

from bridge import Client
from server import DEFAULT_MODEL, Handler, ThreadingHTTPServer
import test_codex_bridge as fixtures
import test_quota_failover as quota_fixtures


class MeetingContextIntegrationTests(unittest.TestCase):
    setUp = fixtures.BindingTests.setUp
    tearDown = fixtures.BindingTests.tearDown
    wait = fixtures.BindingTests.wait

    def prepare(self):
        self.cid = self.app.bind_codex(fixtures.THREAD, agy_id=fixtures.SESSION)['id']
        self.app.set_multi_ai_collaboration(self.cid, {'codex_thread_id': fixtures.THREAD,
            'providers': {'muse': {'enabled': True}}})
        self.app.set_roundtable(self.cid, {'codex_thread_id': fixtures.THREAD,
            'mode': 'roundtable', 'host': 'codex'})
        self.mid = self.app.create_meeting(self.cid, {'codex_thread_id': fixtures.THREAD,
            'source_id': 'context-fixture', 'topic': 'Evidence scoped to this meeting'})['meeting']['id']
        self.record('codex', 'proposal', 'CODEX_PROPOSAL_ONLY')
        self.record('muse', 'proposal', 'MUSE_PROPOSAL_ONLY')

    def record(self, provider, stage, text):
        self.app.record_meeting_event(self.cid, self.mid, {
            'codex_thread_id': fixtures.THREAD, 'source_id': provider + ':' + stage,
            'provider': provider, 'stage': stage, 'status': 'completed', 'text': text,
            'source_url': 'https://muse.ai/thread/' + fixtures.OTHER if provider == 'muse' else ''})

    def submit(self, stage, prompt):
        return self.app.submit(self.cid, prompt, DEFAULT_MODEL, codex_dispatch=True,
                               meeting_id=self.mid, meeting_stage=stage)

    def test_proposal_snapshot_excludes_completed_peers_and_reaches_worker(self):
        self.prepare()
        with patch.object(self.app.jobs, 'put'):
            turn = self.submit('proposal', 'Independent proposal please.')
        context = copy.deepcopy(turn['meeting_context'])
        self.assertEqual(context['meeting_id'], self.mid)
        self.assertNotIn('CODEX_PROPOSAL_ONLY', context['text'])
        self.assertNotIn('MUSE_PROPOSAL_ONLY', context['text'])
        self.assertEqual(turn['prompt'], 'Independent proposal please.')
        self.app.jobs.put((self.cid, turn['id']))
        self.wait(turn)
        self.assertEqual(turn['status'], 'completed', turn.get('error'))
        self.assertIn(context['text'], turn['worker_prompt'])
        self.assertEqual(turn['agy_id'], fixtures.SESSION)
        self.assertEqual(turn['meeting_context'], context)

    def test_review_freezes_prior_round_and_excludes_same_round(self):
        self.prepare()
        proposal = self.submit('proposal', 'Independent proposal please.')
        self.wait(proposal)
        with patch.object(self.app.jobs, 'put'):
            turn = self.submit('review', 'Review the evidence.')
        context = copy.deepcopy(turn['meeting_context'])
        self.assertIn('CODEX_PROPOSAL_ONLY', context['text'])
        self.assertIn('MUSE_PROPOSAL_ONLY', context['text'])
        evidence = [json.loads(line) for line in context['text'].splitlines()
                    if line.startswith('{"provider":')]
        self.assertEqual(next(row['text'] for row in evidence if row['provider'] == 'agy'), proposal['response'])
        self.record('codex', 'review', 'SAME_ROUND_MUST_NOT_LEAK')
        # An idempotent re-read must retain the original evidence snapshot.
        duplicate = self.submit('review', 'Review the evidence.')
        self.assertTrue(duplicate['duplicate_submission'])
        self.assertEqual(duplicate['meeting_context'], context)
        self.app.jobs.put((self.cid, turn['id']))
        self.wait(turn)
        self.assertEqual(turn['status'], 'completed', turn.get('error'))
        self.assertIn(context['text'], turn['worker_prompt'])
        self.assertNotIn('SAME_ROUND_MUST_NOT_LEAK', turn['worker_prompt'])
        self.assertEqual(turn['meeting_context'], context)

    def test_read_only_context_http_checks_scope_phase_and_provider(self):
        self.prepare()
        server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
        server.app = self.app
        worker = threading.Thread(target=server.serve_forever, daemon=True)
        worker.start()
        try:
            client = Client('http://127.0.0.1:' + str(server.server_port))
            client.bind(fixtures.THREAD)
            self.assertTrue(client.features['roundtable_context'])
            path = '/api/conversations/' + self.cid + '/meetings/' + self.mid + '/context'
            before = copy.deepcopy(self.app.conversation(self.cid))
            context = client.request(path + '?stage=proposal&provider=muse')
            self.assertEqual(context['provider'], 'muse')
            self.assertEqual(context['source_chars'], 0)
            self.assertEqual(before, self.app.conversation(self.cid))
            for query in ('?stage=review&provider=muse', '?stage=proposal&provider=claude',
                          '?stage=proposal&provider=muse&stage=summary'):
                with self.assertRaises(ValueError):
                    client.request(path + query)
            other = self.app.bind_codex(fixtures.OTHER)['id']
            with self.assertRaises(ValueError):
                client.request(path.replace(self.cid, other) + '?stage=proposal&provider=muse')
        finally:
            server.shutdown()
            server.server_close()
            worker.join(3)

    def test_quota_candidate_reuses_frozen_evidence_and_official_session(self):
        bound, _ = quota_fixtures.QuotaFailoverTests.prepare(self)
        self.cid = bound['id']
        self.app.set_multi_ai_collaboration(self.cid, {'codex_thread_id': fixtures.THREAD,
            'providers': {'muse': {'enabled': True}}})
        self.app.set_roundtable(self.cid, {'codex_thread_id': fixtures.THREAD,
            'mode': 'roundtable', 'host': 'codex'})
        self.mid = self.app.create_meeting(self.cid, {'codex_thread_id': fixtures.THREAD,
            'source_id': 'quota-context-fixture', 'topic': 'Snapshot during quota fallback'})['meeting']['id']
        self.record('codex', 'proposal', 'CODEX_PROPOSAL_ONLY')
        self.record('muse', 'proposal', 'MUSE_PROPOSAL_ONLY')
        self.app.record_meeting_event(self.cid, self.mid, {
            'codex_thread_id': fixtures.THREAD, 'source_id': 'agy-proposal-skipped',
            'provider': 'agy', 'stage': 'proposal', 'status': 'unavailable',
            'detail': 'Fixture first round not dispatched'})
        with patch.object(self.app.jobs, 'put'):
            turn = self.app.submit(self.cid, 'Review once.', '', codex_dispatch=True,
                                   meeting_id=self.mid, meeting_stage='review')
        frozen = copy.deepcopy(turn['meeting_context'])
        self.record('codex', 'review', 'LATER_REVIEW_MUST_NOT_LEAK')
        self.app.jobs.put((self.cid, turn['id']))
        quota_fixtures.QuotaFailoverTests.wait(self, turn)
        self.assertEqual(turn['status'], 'completed', turn.get('error'))
        calls = quota_fixtures.QuotaFailoverTests.calls(self, bound)
        self.assertEqual(len(calls), 2)
        self.assertEqual([call['model'] for call in calls], [quota_fixtures.ARGON, quota_fixtures.FLASH])
        for call in calls:
            self.assertEqual(call['session'], fixtures.SESSION)
            self.assertIn(frozen['text'], call['prompt'])
            self.assertNotIn('LATER_REVIEW_MUST_NOT_LEAK', call['prompt'])
        self.assertEqual(turn['meeting_context'], frozen)
        self.assertEqual(len(self.app.conversation(self.cid)['turns']), 1)


if __name__ == '__main__':
    unittest.main()
