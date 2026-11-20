"""Windows-only prompt guard, exercised with a fake CLI and simulated platform name."""
import copy
import os
from pathlib import Path
import sys
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / 'windows/app'))
import server
from multi_ai_collaboration import roundtable_prompt
import test_meeting_context_integration as fixtures


class WindowsOS:
    name = 'nt'

    def __getattr__(self, key):
        return getattr(os, key)


class MeetingWindowsPromptLimitTests(unittest.TestCase):
    setUp = fixtures.MeetingContextIntegrationTests.setUp
    tearDown = fixtures.MeetingContextIntegrationTests.tearDown
    prepare = fixtures.MeetingContextIntegrationTests.prepare
    record = fixtures.MeetingContextIntegrationTests.record
    submit = fixtures.MeetingContextIntegrationTests.submit

    def prompt_for(self, extra=0):
        context = self.app.meeting_context(self.cid, self.mid, 'proposal', 'agy')
        meeting = next(m for m in self.app.conversation(self.cid)['roundtable_meetings'] if m['id'] == self.mid)
        prefix = roundtable_prompt('', meeting['roundtable'], fixtures.fixtures.THREAD) + '\n\n' + context['text']
        length = 26000 - len(prefix.encode('utf-16-le')) // 2 + extra
        return '🙂' * (length // 2) + 'x' * (length % 2)

    def test_snapshot_pushes_originally_legal_prompt_over_limit_before_mutation(self):
        self.prepare()
        prompt = self.prompt_for(extra=1)
        self.assertLessEqual(len(prompt.encode('utf-16-le')) // 2, 26000)
        before = copy.deepcopy(self.app.conversation(self.cid))
        with patch.object(server, 'os', WindowsOS()), patch.object(self.app.jobs, 'put') as jobs:
            with self.assertRaisesRegex(ValueError, 'UTF-16'):
                self.submit('proposal', prompt)
            jobs.assert_not_called()
        self.assertEqual(before, self.app.conversation(self.cid))

    def test_exact_rendered_utf16_limit_is_accepted_with_frozen_snapshot(self):
        self.prepare()
        with patch.object(server, 'os', WindowsOS()), patch.object(self.app.jobs, 'put'):
            turn = self.submit('proposal', self.prompt_for())
        rendered = roundtable_prompt(turn['prompt'], turn['roundtable'], fixtures.fixtures.THREAD) + '\n\n' + turn['meeting_context']['text']
        self.assertEqual(len(rendered.encode('utf-16-le')) // 2, 26000)
        self.assertEqual(turn['status'], 'queued')


if __name__ == '__main__':
    unittest.main()
