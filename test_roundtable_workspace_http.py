"""Workspace HTTP/persistence/export boundaries using isolated local fixtures."""
import copy
import json
import unittest
from unittest.mock import patch

import test_roundtable_meeting_http as fixtures
from test_roundtable_meeting_http import THREAD, OTHER
from server import now
from panel_backups import validate_state, validate_configuration
from roundtable_workspace import DECISION_CATEGORIES
from transcript_export import export_markdown


class WorkspaceHTTPTests(unittest.TestCase):
    setUp = fixtures.IndependentMeetingHTTPTests.setUp
    tearDown = fixtures.IndependentMeetingHTTPTests.tearDown
    new_console = fixtures.IndependentMeetingHTTPTests.new_console
    request = fixtures.IndependentMeetingHTTPTests.request
    create = fixtures.IndependentMeetingHTTPTests.create
    event = fixtures.IndependentMeetingHTTPTests.event

    def preferences(self, **changes):
        return self.request('POST', self.base + '/roundtable-workspace-preferences',
                            {'codex_thread_id': THREAD, **changes})

    def test_workspace_get_is_scoped_read_only_and_does_not_dispatch(self):
        mid = self.create()
        self.event(mid)
        state = self.app.file.read_bytes()
        mirror = self.app.coordination.path.read_bytes()
        with patch('subprocess.Popen', side_effect=AssertionError('no provider process')):
            status, value = self.request('GET', self.base + '/meetings/' + mid + '/workspace')
        self.assertEqual(status, 200, value)
        self.assertEqual(value['meeting_id'], mid)
        self.assertEqual(value['codex_thread_id'], THREAD)
        self.assertEqual(len(value['materials']), 7)
        self.assertEqual(value['decisions']['status'], 'not_recorded')
        self.assertIsNone(value['usage']['usage'])
        self.assertIn('muse', value['usage']['unknown_providers'])
        self.assertEqual(self.app.file.read_bytes(), state)
        self.assertEqual(self.app.coordination.path.read_bytes(), mirror)
        self.assertTrue(all(row['snapshot'] is None for row in value['contexts']))
        self.assertEqual(self.conv['turns'], [])

    def test_workspace_rejects_auth_queries_and_foreign_conversations(self):
        mid = self.create()
        url = self.base + '/meetings/' + mid + '/workspace'
        self.assertEqual(self.request('GET', url, **{'Host': 'evil.test'})[0], 403)
        self.assertEqual(self.request('GET', url + '?provider=agy')[0], 400)
        other = self.app.bind_codex(OTHER, 'Different fixed entry')['id']
        self.assertEqual(self.request('GET', '/api/conversations/' + other + '/meetings/' + mid + '/workspace')[0], 404)

    def test_preferences_freeze_only_new_meetings_and_preserve_pauses_models(self):
        mid = self.create()
        self.app.set_multi_ai_collaboration(self.cid, {'codex_thread_id': THREAD, 'enabled': False})
        before = copy.deepcopy(self.conv)
        mirror = self.app.coordination.path.read_bytes()
        status, value = self.preferences(preset='paper_review', deduplicate_exact=False)
        self.assertEqual(status, 200, value)
        self.assertEqual(value['preset'], 'paper_review')
        self.assertFalse(value['deduplicate_exact'])
        self.assertEqual(self.conv['multi_ai_collaboration'], before['multi_ai_collaboration'])
        self.assertEqual(self.conv['roundtable_meetings'], before['roundtable_meetings'])
        self.assertEqual(self.app.coordination.path.read_bytes(), mirror)
        self.assertEqual(self.app.meeting_workspace(self.cid, mid)['preset']['id'], 'general')
        self.app.set_multi_ai_collaboration(self.cid, {'codex_thread_id': THREAD, 'enabled': True})
        fresh = self.create('new-preset')
        self.assertEqual(self.app.meeting_workspace(self.cid, fresh)['preset']['id'], 'paper_review')
        restarted = self.new_console()
        try:
            self.assertEqual(restarted.conversation(self.cid)['roundtable_workspace_preferences'], value)
        finally:
            restarted.close()

    def test_preferences_validation_and_failed_write_leave_original_intact(self):
        for body in ({'codex_thread_id': OTHER, 'preset': 'general'},
                     {'codex_thread_id': THREAD, 'preset': 'unbounded'},
                     {'codex_thread_id': THREAD, 'providers': {'muse': True}}):
            self.assertEqual(self.request('POST', self.base + '/roundtable-workspace-preferences', body)[0], 400)
        before = copy.deepcopy(self.conv)
        with patch('server.write_json', side_effect=OSError('fixture write failure')):
            with self.assertRaises(OSError):
                self.app.set_roundtable_workspace_preferences(self.cid, {'codex_thread_id': THREAD, 'preset': 'code_review'})
        self.assertEqual(self.conv, before)
        self.assertNotIn('roundtable_workspace_preferences', json.loads(self.app.file.read_text())['conversations'][0])

    def test_metadata_and_portable_configuration_preserve_exact_thread(self):
        self.preferences(preset='code_review')
        metadata = self.app.conversation_metadata(self.conv, compact=True)
        self.assertEqual(metadata['roundtable_workspace_preferences']['preset'], 'code_review')
        self.assertEqual(len(metadata['roundtable_presets']), 3)
        document = self.app.export_configuration({}, self.cid)
        portable = document['settings']['window']['roundtable_workspace_preferences']
        self.assertNotIn('codex_thread_id', portable)
        self.assertEqual(portable['preset'], 'code_review')
        validate_configuration(document)
        validate_state(self.app.state)
        broken = copy.deepcopy(self.app.state)
        broken['conversations'][0]['roundtable_workspace_preferences']['codex_thread_id'] = OTHER
        with self.assertRaises(ValueError):
            validate_state(broken)

    def test_summary_decisions_export_real_sources_and_original_prose(self):
        mid = self.create()
        for stage in ('proposal', 'review'):
            for provider in ('codex', 'muse', 'agy'):
                status, result = self.event(mid, source_id=provider + ':' + stage, provider=provider,
                    stage=stage, status='unavailable' if provider == 'agy' else 'completed',
                    text='' if provider == 'agy' else provider + ' actual ' + stage,
                    source_url='https://muse.ai/thread/' + OTHER if provider == 'muse' else '',
                    detail='Never sent in this isolated fixture' if provider == 'agy' else '')
                self.assertEqual(status, 200, result)
        decisions = {'schema_version': 1, **{key: [] for key in DECISION_CATEGORIES}}
        decisions['suggested_adoption'] = [{'text': 'Keep the bounded material drawer',
                                            'sources': [{'source_id': 'muse:proposal'}]}]
        status, value = self.event(mid, source_id='codex:summary', provider='codex', stage='summary',
            text='Original host prose remains readable', source_url='', decisions=decisions)
        self.assertEqual(status, 200, value)
        view = self.app.meeting_workspace(self.cid, mid)
        self.assertEqual(view['decisions']['status'], 'recorded')
        validate_state(self.app.state)
        exported = export_markdown(copy.deepcopy(self.conv), meeting_id=mid, codex_thread_id=THREAD)
        markdown, _filename = exported
        self.assertIn('主持建议纪要', markdown)
        self.assertIn('不代表用户批准', markdown)
        self.assertIn('muse:proposal', markdown)
        self.assertIn('Original host prose remains readable', markdown)
        self.assertIn('Keep the bounded material drawer', markdown)


if __name__ == '__main__':
    unittest.main()
