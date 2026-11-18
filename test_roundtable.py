"""Roundtable metadata, exact-chat gates and actual fake-CLI role envelope."""
import copy
import http.client
import json
import threading
import unittest
from unittest.mock import patch
from pathlib import Path

from bridge import Client
from multi_ai_collaboration import (roundtable, validate_roundtable, roundtable_plan,
                                    roundtable_prompt, portable_roundtable)
from panel_backups import validate_configuration, validate_state
from server import Console, DEFAULT_MODEL, Handler, ThreadingHTTPServer
import test_codex_bridge as fixtures
import test_configuration as configuration_fixtures


class RoundtableTests(unittest.TestCase):
    setUp = fixtures.BindingTests.setUp
    tearDown = fixtures.BindingTests.tearDown
    wait = fixtures.BindingTests.wait

    def bind(self, thread=fixtures.THREAD):
        return self.app.bind_codex(thread)

    def update(self, conv, **body):
        return self.app.set_roundtable(conv['id'], dict(codex_thread_id=conv['codex_thread_id'], **body))

    def providers(self, conv, **values):
        return self.app.set_multi_ai_collaboration(conv['id'], {
            'codex_thread_id':conv['codex_thread_id'],
            'providers':{p:{'enabled':v} for p,v in values.items()}})

    def test_legacy_default_is_assist_and_read_has_no_dispatch_side_effect(self):
        conv=self.bind()
        expected={'schema_version':1,'codex_thread_id':fixtures.THREAD,'mode':'assist','host':'codex','rounds':2}
        self.assertEqual(conv['roundtable'],expected)
        self.assertEqual(conv['roundtable_plan']['reason'],'assist_mode')
        self.assertEqual(conv['roundtable_plan']['steps'],[])
        with patch('subprocess.Popen',side_effect=AssertionError('must not launch')):
            self.assertEqual(self.app.roundtable_policy(fixtures.THREAD),expected)
            self.update(conv,host='agy',mode='roundtable')
        self.assertFalse(self.app.processes)
        self.assertFalse(self.app.conversation(conv['id'])['turns'])
        snap=json.loads(self.app.coordination.path.read_text())
        self.assertEqual(snap['threads'][fixtures.THREAD]['roundtable']['host'],'agy')

    def test_wrong_thread_malformed_values_and_unknown_keys_never_write(self):
        conv=self.bind();before=self.app.file.read_bytes()
        for patch_body in ({'host':'gpt'},{'host':True},{'mode':'forever'},{'rounds':True},
                           {'rounds':3},{'schema_version':True,'host':'agy'},
                           {'host':'agy','token':'x'},{'host':'agy','codex_thread_id':fixtures.OTHER},{}):
            body=dict({'codex_thread_id':fixtures.THREAD},**patch_body)
            with self.subTest(body=body):
                with self.assertRaises(ValueError):self.app.set_roundtable(conv['id'],body)
                self.assertEqual(self.app.file.read_bytes(),before)
        with self.assertRaises(ValueError):self.app.set_roundtable(self.app.create()['id'],{'codex_thread_id':fixtures.THREAD,'host':'agy'})
        with self.assertRaises(KeyError):self.app.roundtable_policy(fixtures.OTHER)

    def test_host_switch_does_not_enable_provider_or_resume_master(self):
        conv=self.bind();self.app.set_collaboration(conv['id'],False)
        value=self.update(conv,mode='roundtable',host='muse')
        state=self.bind()
        self.assertEqual(value['host'],'muse')
        self.assertFalse(state['multi_ai_collaboration']['enabled'])
        self.assertFalse(state['multi_ai_collaboration']['providers']['muse']['enabled'])
        self.assertEqual(state['roundtable_plan']['reason'],'master_paused')
        self.app.set_collaboration(conv['id'],True)
        state=self.bind();self.assertEqual(state['roundtable_plan']['reason'],'host_disabled')
        self.assertEqual(state['roundtable']['host'],'muse')
        with self.assertRaisesRegex(ValueError,'host_disabled'):
            self.app.submit(conv['id'],'bounded task',DEFAULT_MODEL,codex_dispatch=True)
        self.providers(conv,muse=True)
        state=self.bind();self.assertTrue(state['roundtable_plan']['ready'])
        self.assertEqual(state['roundtable_plan']['participants'],['codex','agy','muse'])
        self.assertEqual(state['roundtable_plan']['steps'],['independent_proposals','peer_review','host_summary'])

    def test_plan_no_external_participant_and_switch_back_keeps_host(self):
        conv=self.bind();self.update(conv,mode='roundtable',host='codex')
        self.providers(conv,agy=False)
        self.assertEqual(self.bind()['roundtable_plan']['reason'],'no_external_participant')
        self.update(conv,mode='assist')
        state=self.bind();self.assertEqual(state['roundtable']['host'],'codex')
        self.assertEqual(state['roundtable_plan']['reason'],'assist_mode')

    def test_per_chat_isolation_rollback_and_original_turn_objects(self):
        conv=self.bind();other=self.bind(fixtures.OTHER)
        before=copy.deepcopy(self.app.conversation(conv['id']));unrelated=copy.deepcopy(self.app.conversation(other['id']))
        turns=self.app.conversation(conv['id'])['turns']
        with patch.object(self.app.coordination,'publish',side_effect=OSError('snapshot write failed')):
            with self.assertRaises(OSError):self.update(conv,host='agy',mode='roundtable')
        self.assertEqual(self.app.conversation(conv['id']),before)
        self.assertIs(self.app.conversation(conv['id'])['turns'],turns)
        self.assertEqual(self.app.conversation(other['id']),unrelated)
        self.assertFalse(json.loads(self.app.coordination.path.read_text())['ready'])
        self.app.save()
        self.update(conv,host='agy',mode='roundtable')
        self.assertEqual(self.app.roundtable_policy(fixtures.OTHER)['mode'],'assist')

    def test_existing_corrupt_roundtable_fails_closed_without_stopping_manual_work(self):
        conv=self.bind();stored=self.app.conversation(conv['id'])
        stored['roundtable']={'schema_version':1,'codex_thread_id':fixtures.OTHER,'mode':'roundtable','host':'agy','rounds':2}
        self.app.save()
        self.assertFalse(json.loads(self.app.coordination.path.read_text())['ready'])
        self.assertEqual(self.bind()['roundtable_plan']['reason'],'invalid_state')
        with self.assertRaises(ValueError):self.app.roundtable_policy(fixtures.THREAD)
        with self.assertRaises(ValueError):self.app.submit(conv['id'],'blocked',DEFAULT_MODEL,codex_dispatch=True)
        manual=self.app.submit(conv['id'],'first',DEFAULT_MODEL);self.wait(manual)
        self.assertEqual(manual['status'],'completed')
        self.assertNotIn('roundtable',manual)

    def test_actual_cli_role_envelope_preserves_visible_prompt_and_host_snapshot(self):
        conv=self.bind();self.update(conv,host='agy',mode='roundtable')
        with patch.object(self.app.jobs,'put'):
            turn=self.app.submit(conv['id'],'Please review only this proposal.',DEFAULT_MODEL,codex_dispatch=True)
        self.update(conv,host='codex')
        self.assertEqual(turn['roundtable']['host'],'agy')
        self.assertEqual(turn['roundtable_role'],'host')
        self.app.jobs.put((conv['id'],turn['id']));self.wait(turn)
        self.assertEqual(turn['status'],'completed',turn.get('error'))
        self.assertEqual(turn['prompt'],'Please review only this proposal.')
        self.assertIn('你是本次圆桌讨论的主持',turn['response'])
        self.assertIn('不编造发言、共识或会议已完成',turn['response'])
        self.assertIn(turn['prompt'],turn['response'])
        self.assertEqual((self.app.data/'runs'/turn['id']/'request.txt').read_text(),turn['worker_prompt'])
        self.assertIn(turn['prompt'],turn['worker_prompt'])
        detail=self.app.snapshot(summaries=True)['conversations'][0]['turns'][-1]
        self.assertEqual(detail['roundtable_role'],'host')
        self.assertEqual(detail['roundtable']['host'],'agy')

    def test_roundtable_to_assist_resets_persistent_role_only_for_codex_in_the_same_lane(self):
        conv=self.bind();self.update(conv,host='agy',mode='roundtable')
        meeting=self.app.submit(conv['id'],'meeting role fixture',DEFAULT_MODEL,codex_dispatch=True)
        self.wait(meeting);self.assertEqual(meeting['status'],'completed')
        self.update(conv,mode='assist')
        with patch.object(self.app.jobs,'put'):
            normal=self.app.submit(conv['id'],'ordinary next assignment',DEFAULT_MODEL,codex_dispatch=True)
        self.assertTrue(normal['roundtable_reset'])
        self.assertNotIn('roundtable',normal)
        self.assertNotIn('roundtable_role',normal)
        # Later preferences cannot turn this queued normal task back into a host.
        self.update(conv,mode='roundtable',host='agy')
        self.app.jobs.put((conv['id'],normal['id']));self.wait(normal)
        self.assertEqual(normal['status'],'completed')
        self.assertEqual(normal['prompt'],'ordinary next assignment')
        self.assertIn('不担任主持，也不继续此前的圆桌角色',normal['response'])
        self.assertEqual((self.app.data/'runs'/normal['id']/'request.txt').read_text(),normal['worker_prompt'])
        manual=self.app.submit(conv['id'],'manual next',DEFAULT_MODEL)
        self.wait(manual)
        self.assertNotIn('roundtable_reset',manual)
        self.assertNotIn('worker_prompt',manual)
        self.update(conv,mode='assist')
        self.binary.write_text(fixtures.FAKE.replace("'session-test-42'","'independent-lane-session'"))
        branch=self.app.submit(conv['id'],'independent lane',DEFAULT_MODEL,branch='different',codex_dispatch=True)
        self.wait(branch)
        self.assertNotIn('roundtable_reset',branch)
        self.assertNotIn('worker_prompt',branch)
        self.assertEqual(branch['status'],'completed')
        self.binary.write_text(fixtures.FAKE.replace("'session-test-42'","'fresh-chat-session'"))
        fresh=self.bind(fixtures.OTHER)
        legacy=self.app.submit(fresh['id'],'legacy ordinary',DEFAULT_MODEL,codex_dispatch=True)
        self.wait(legacy)
        self.assertEqual(legacy['status'],'completed')
        self.assertNotIn('roundtable_reset',legacy)
        self.assertEqual(legacy['response'],'中文回复：legacy ordinary')

    def test_quota_evidence_receives_actual_worker_prompt_without_changing_visible_request(self):
        conv=self.bind();self.update(conv,host='agy',mode='roundtable')
        fake=fixtures.FAKE if hasattr(fixtures,'FAKE') else None
        self.binary.write_text(fake.replace("'status':'SUCCESS'", "'status':'ERROR','error':'Individual quota reached'")
                               .replace("'response':response,", "'response':'',"))
        seen=[]
        def capture(turn, root):
            seen.append(turn['prompt'])
            return False
        with patch('server.AttemptGuard.current_quota_rejection',side_effect=capture):
            turn=self.app.submit(conv['id'],'review quota fixture',DEFAULT_MODEL,codex_dispatch=True)
            self.wait(turn)
        self.assertEqual(turn['status'],'failed')
        self.assertEqual(turn['prompt'],'review quota fixture')
        self.assertEqual(seen,[turn['worker_prompt']])
        self.assertIn('你是本次圆桌讨论的主持',seen[0])
        self.assertEqual(len(turn.get('attempts',[])),1)

    def test_external_host_makes_agy_a_participant_not_a_fake_meeting(self):
        conv=self.bind();self.providers(conv,claude=True)
        self.update(conv,host='claude',mode='roundtable')
        turn=self.app.submit(conv['id'],'one bounded contribution',DEFAULT_MODEL,codex_dispatch=True);self.wait(turn)
        self.assertEqual(turn['status'],'completed')
        self.assertEqual(turn['roundtable_role'],'participant')
        self.assertIn('主持为 Claude',turn['response'])
        self.assertIn('未提供其他 AI 的真实回复时',turn['response'])
        self.assertEqual(len(self.app.conversation(conv['id'])['turns']),1)
        self.assertFalse(self.app.processes)

    def test_portable_roundtable_and_backup_validation_are_strict(self):
        conv=self.bind();self.update(conv,host='agy',mode='roundtable')
        doc=self.app.export_configuration({},conv['id']);raw=json.dumps(doc)
        self.assertNotIn(fixtures.THREAD,raw)
        self.assertEqual(doc['settings']['window']['roundtable'],{'schema_version':1,'mode':'roundtable','host':'agy','rounds':2})
        validate_configuration(doc);validate_state(self.app.state)
        doc['settings']['window']['roundtable']['codex_thread_id']=fixtures.THREAD
        with self.assertRaises(ValueError):validate_configuration(doc)
        broken=copy.deepcopy(self.app.state);broken['conversations'][0]['roundtable']['host']='unknown'
        with self.assertRaises(ValueError):validate_state(broken)
        with self.assertRaises(ValueError):roundtable({'roundtable':conv['roundtable']})

    def test_http_roundtable_get_is_readonly_and_post_uses_exact_identity_and_csrf(self):
        conv=self.bind();httpd=ThreadingHTTPServer(('127.0.0.1',0),Handler);httpd.app=self.app
        worker=threading.Thread(target=httpd.serve_forever,daemon=True);worker.start()
        try:
            client=Client('http://127.0.0.1:'+str(httpd.server_port))
            self.assertEqual(client.request('/api/codex/'+fixtures.THREAD+'/roundtable'),conv['roundtable'])
            with self.assertRaises(ValueError):client.request('/api/codex/'+fixtures.OTHER+'/roundtable')
            self.assertEqual(len(self.app.state['conversations']),1)
            raw=http.client.HTTPConnection('127.0.0.1',httpd.server_port)
            raw.request('POST','/api/conversations/'+conv['id']+'/roundtable',json.dumps({'codex_thread_id':fixtures.THREAD,'host':'agy'}),{'Content-Type':'application/json'})
            reply=raw.getresponse();self.assertEqual(reply.status,403);reply.read();raw.close()
            changed=client.request('/api/conversations/'+conv['id']+'/roundtable',{'codex_thread_id':fixtures.THREAD,'host':'agy','mode':'roundtable'})
            self.assertEqual(changed['host'],'agy')
            self.assertTrue(client.request('/api/service')['features']['roundtable_host'])
        finally:
            httpd.shutdown();httpd.server_close();worker.join(3)


class RoundtableImportTests(unittest.TestCase):
    setUp = configuration_fixtures.ConfigurationTests.setUp
    tearDown = configuration_fixtures.ConfigurationTests.tearDown
    reject_update_network = configuration_fixtures.ConfigurationTests.reject_update_network
    document = configuration_fixtures.ConfigurationTests.document
    # Reuse network-isolated configuration fixture, not inherited test methods.
    def test_roundtable_target_import_does_not_reuse_exported_chat_identity(self):
        self.app.set_roundtable(self.conv['id'],{'codex_thread_id':fixtures.THREAD,'mode':'roundtable','host':'agy'})
        other=self.app.bind_codex(fixtures.OTHER)
        doc=self.document()
        with self.assertRaisesRegex(ValueError,'固定聊天'):
            self.app.preview_configuration(doc,self.app.create()['id'],True)
        preview=self.app.preview_configuration(doc,other['id'],True)
        self.app.import_configuration(doc,{},other['id'],True,preview['revision'],True)
        target=self.app.roundtable_policy(fixtures.OTHER)
        self.assertEqual(target['codex_thread_id'],fixtures.OTHER)
        self.assertEqual(target['host'],'agy')
        self.assertEqual(target['mode'],'roundtable')
        self.assertEqual(self.app.roundtable_policy(fixtures.THREAD)['codex_thread_id'],fixtures.THREAD)


if __name__=='__main__':unittest.main()
