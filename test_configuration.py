"""Preference-only transfer: privacy, validation, persistence and failure isolation."""
import copy
import http.client
import json
import tempfile
import threading
import time
import unittest
from unittest.mock import patch
from pathlib import Path
from panel_backups import validate_configuration
from server import Console,Handler,ThreadingHTTPServer,ConflictError
from software_updates import SoftwareUpdates
import test_codex_bridge as binding_fixtures
THREAD=binding_fixtures.THREAD

FLASH='gemini-3.8-flash-high'
ARGON='gemini-4-argon-high'

class ConfigurationTests(unittest.TestCase):
    def setUp(self):
        # Configuration tests own their updater source and transport.  The
        # installed application's release-source.json may enable checks, but
        # must never start real network work or alter these fixtures' timing.
        self.updater_temp=tempfile.TemporaryDirectory()
        self.addCleanup(self.updater_temp.cleanup)
        self.updater_root=Path(self.updater_temp.name)
        (self.updater_root/'VERSION').write_text('0.1.0-preview.1')
        self.updaters=[]
        def make_updater(_root,data,*args,**kwargs):
            kwargs['fetch']=self.reject_update_network
            manager=SoftwareUpdates(self.updater_root,data,*args,**kwargs)
            self.updaters.append(manager)
            return manager
        factory=patch('server.SoftwareUpdates',side_effect=make_updater)
        factory.start();self.addCleanup(factory.stop)
        binding_fixtures.BindingTests.setUp(self)
        deadline=time.monotonic()+4
        while time.monotonic()<deadline:
            try:self.app.require_idle();break
            except ConflictError:time.sleep(.02)
        self.app.health['models']=[{'id':m,'label':m} for m in (FLASH,ARGON)]
        bound=self.app.bind_codex(THREAD,'Private title');self.conv=self.app.conversation(bound['id'])
        self.conv.update(agy_id='private-session',model_preferences={'text':FLASH,'image':''},
            fallback_preferences={'text':{'enabled':True,'models':[FLASH,ARGON]},'image':{'enabled':False,'models':[]}},
            auto_open_activity=False)
        self.app.set_collaboration(self.conv['id'], False)
        self.app.state['preferred_model']=FLASH
        self.app.save(self.conv)
    def tearDown(self):
        self.app.close()
        for manager in self.updaters:
            manager.close()
            if manager.thread:manager.thread.join(3)
        self.temp.cleanup()

    def reject_update_network(self,*_args):
        self.fail('Configuration tests must not contact an update server')

    def document(self):return self.app.export_configuration({'font':18,'idleMs':4000,'credentials':'SECRET','draft':'SECRET'},self.conv['id'])
    def preview(self,doc,window=True):return self.app.preview_configuration(doc,self.conv['id'],window)
    def apply(self,doc,preview=None,window=True):
        preview=preview or self.preview(doc,window)
        return self.app.import_configuration(doc,{'font':14,'idleMs':12000},self.conv['id'],window,preview['revision'],True)

    def test_export_excludes_identity_history_credentials_and_paths(self):
        self.conv['turns'].append({'id':'a'*32,'status':'completed','prompt':'PRIVATE PROMPT','response':'PRIVATE RESPONSE'})
        self.conv['account']={'email':'SECRET'}
        doc=self.document();raw=json.dumps(doc)
        for forbidden in ('PRIVATE','SECRET','Private title','private-session',THREAD,self.conv['id'],str(self.root)):
            self.assertNotIn(forbidden,raw)
        self.assertEqual(doc['settings']['window']['fallback_preferences']['image']['models'],[])
        self.assertEqual(doc['settings']['window']['model_preferences']['image'],'')
        self.assertEqual(doc['settings']['ui'],{'font':18,'idleMs':4000})

    def test_validation_rejects_foreign_versions_unknown_keys_and_bad_values(self):
        for mutate in (
            lambda d:d.update(schema=2),lambda d:d.update(token='secret'),
            lambda d:d['settings'].update(conversations=[]),
            lambda d:d['settings']['updates'].update(repository='https://evil.example/x'),
            lambda d:d['settings']['ui'].update(font=True),
            lambda d:d['settings']['window']['fallback_preferences']['text'].update(token='secret'),
            lambda d:d['settings']['window'].update(auto_open_activity=1)):
            doc=self.document();mutate(doc)
            with self.assertRaises(ValueError):validate_configuration(doc)

    def test_preview_is_read_only_and_unavailable_model_cannot_partially_import(self):
        doc=self.document();before=self.app.file.read_bytes();prefs=copy.deepcopy(self.app.software_updates.preferences)
        self.preview(doc);self.assertEqual(before,self.app.file.read_bytes())
        doc['settings']['global']['preferred_model']='unsupported-model'
        with self.assertRaises(ValueError):self.apply(doc)
        self.assertEqual(before,self.app.file.read_bytes());self.assertEqual(prefs,self.app.software_updates.preferences)

    def test_roundtrip_preserves_turns_bindings_and_creates_reimportable_rollback(self):
        before=copy.deepcopy(self.conv);doc=self.document()
        doc['settings']['global']['preferred_model']=ARGON
        doc['settings']['window']['model_preferences']={'text':ARGON}
        doc['settings']['window']['fallback_preferences']['text']['models']=[ARGON,FLASH]
        result=self.apply(doc)
        self.assertEqual(self.app.state['preferred_model'],ARGON)
        conv=self.app.conversation(self.conv['id'])
        self.assertEqual(conv['turns'],before['turns']);self.assertEqual(conv['agy_id'],before['agy_id']);self.assertEqual(conv['codex_thread_id'],THREAD)
        self.assertFalse(conv['gpt_collaboration']);self.assertFalse(conv['auto_open_activity'])
        self.assertEqual(conv['fallback_preferences']['text']['models'],[ARGON,FLASH])
        rollback=self.app.data/'configuration-backups'/Path(result['rollback_url']).name
        old=json.loads(rollback.with_suffix('.json').read_text());validate_configuration(old)
        self.assertEqual(old['settings']['global']['preferred_model'],FLASH)
        self.assertEqual(old['settings']['ui']['font'],14)
        self.app.close();self.app=Console(self.root/'data',self.binary,import_history=False)
        self.assertEqual(self.app.state['preferred_model'],ARGON)

    def test_global_only_does_not_change_window_and_stale_preview_is_rejected(self):
        doc=self.document();doc['settings']['window']['model_preferences']={'text':ARGON};preview=self.preview(doc,False)
        self.apply(doc,preview,False)
        self.assertEqual(self.app.conversation(self.conv['id'])['model_preferences']['text'],FLASH)
        preview=self.preview(doc);self.app.set_preferred_model(ARGON)
        with self.assertRaises(ConflictError):self.apply(doc,preview)
        self.assertEqual(self.app.state['preferred_model'],ARGON)

    def test_shared_smart_policy_roundtrip_and_legacy_document_preservation(self):
        self.app.set_global_model_policy('text', {'enabled':True,'models':[ARGON,FLASH]}, 0)
        doc=self.document()
        self.assertEqual(doc['settings']['global']['global_model_policy']['text']['models'],[ARGON,FLASH])
        doc['settings']['global']['global_model_policy']['image']={'enabled':False,'models':[FLASH]}
        self.apply(doc,window=False)
        self.assertEqual(self.app.state['global_model_policy']['image'],{'enabled':False,'models':[FLASH]})
        legacy=self.document();legacy['settings']['global'].pop('global_model_policy')
        before=copy.deepcopy(self.app.state['global_model_policy'])
        self.apply(legacy,window=False)
        self.assertEqual(self.app.state['global_model_policy'],before)
        invalid=self.document();invalid['settings']['global']['global_model_policy']['text']['models']=['missing-model']
        with self.assertRaises(ValueError):self.preview(invalid,False)
        invalid=self.document();invalid['settings']['global']['global_model_policy']['text']['models']=[True]
        with self.assertRaises(ValueError):validate_configuration(invalid)
        invalid=self.document();invalid['settings']['global']['global_model_policy']['audio']={'enabled':True}
        with self.assertRaises(ValueError):validate_configuration(invalid)

    def test_multi_ai_import_targets_selected_thread_and_preserves_pauses(self):
        self.app.set_multi_ai_collaboration(self.conv['id'], {'codex_thread_id':THREAD,
            'enabled':False, 'providers':{'muse':{'enabled':True},'agy':{'enabled':False}}})
        doc=self.document();other=self.app.bind_codex(binding_fixtures.OTHER)
        original=copy.deepcopy(self.app.multi_ai_policy(THREAD))
        preview=self.app.preview_configuration(doc,other['id'],True)
        self.app.import_configuration(doc,{},other['id'],True,preview['revision'],True)
        target=self.app.multi_ai_policy(binding_fixtures.OTHER)
        self.assertEqual(target['codex_thread_id'],binding_fixtures.OTHER)
        self.assertFalse(target['enabled']);self.assertFalse(target['providers']['agy']['enabled'])
        self.assertTrue(target['providers']['muse']['enabled'])
        self.assertEqual(self.app.multi_ai_policy(THREAD),original)
        restored=self.app.bind_codex(binding_fixtures.OTHER)
        self.assertFalse(restored['gpt_collaboration'])

    def test_failed_configuration_transaction_restores_offline_policy(self):
        before=self.app.multi_ai_policy(THREAD);doc=self.document()
        doc['settings']['window']['multi_ai_collaboration']['enabled']=True
        with patch.object(self.app.software_updates,'set_preferences',side_effect=OSError('simulated update prefs failure')):
            with self.assertRaises(OSError):self.apply(doc)
        offline=json.loads(self.app.coordination.path.read_text())
        self.assertTrue(offline['ready'])
        self.assertEqual(self.app.multi_ai_policy(THREAD),before)
        self.assertEqual(offline['threads'][THREAD]['multi_ai_collaboration'],before)

    def test_update_preference_write_failure_restores_both_files_and_pending_marker(self):
        manager=self.app.software_updates;pending=manager.cache/'pending.json';pending.write_text('{"version":"9.0.0"}')
        before=self.app.file.read_bytes();old_pending=pending.read_bytes();doc=self.document();doc['settings']['global']['preferred_model']=ARGON
        doc['settings']['updates']['repository']='fixture/repo'
        def fail(_value):
            manager.preferences={'repository':'fixture/repo','channel':'stable','automatic':True}
            (manager.cache/'preferences.json').write_text(json.dumps(manager.preferences));pending.unlink()
            raise OSError('simulated preference write failure')
        with patch.object(manager,'set_preferences',side_effect=fail):
            with self.assertRaises(OSError):self.apply(doc)
        self.assertEqual(self.app.file.read_bytes(),before);self.assertEqual(pending.read_bytes(),old_pending)
        self.assertEqual(manager.preferences['repository'],'');self.assertFalse((manager.cache/'preferences.json').exists())

    def test_active_task_or_update_prevents_import_without_cancelling(self):
        doc=self.document();self.conv['turns'].append({'id':'b'*32,'status':'queued'})
        with self.assertRaises(ConflictError):self.apply(doc)
        self.assertEqual(self.conv['turns'][-1]['status'],'queued');self.conv['turns'].clear()
        self.app.software_updates.requested='check'
        with self.assertRaises(ConflictError):self.apply(doc)
        self.app.software_updates.requested=None

    def test_automatic_check_is_reserved_before_import_can_enter(self):
        previous=self.app.software_updates;previous.close();previous.thread.join(2)
        manager=SoftwareUpdates(self.updater_root,self.app.data,idle=lambda:True,
                                fetch=self.reject_update_network,start=False)
        self.updaters.append(manager)
        manager.preferences['repository']='fixture/repo';self.app.software_updates=manager
        started=threading.Event();finish=threading.Event()
        def fake_check():
            self.assertEqual(manager.state['status'],'checking');started.set();finish.wait(3)
        manager.check=fake_check;manager.start()
        try:
            self.assertTrue(started.wait(2))
            with self.assertRaises(ConflictError):self.apply(self.document())
        finally:manager.close();finish.set();manager.thread.join(3)

    def test_http_transfer_requires_csrf_and_rollback_is_downloadable(self):
        httpd=ThreadingHTTPServer(('127.0.0.1',0),Handler);httpd.app=self.app
        worker=threading.Thread(target=httpd.serve_forever,daemon=True);worker.start()
        def request(path,body=None,authorized=True,origin=None):
            conn=http.client.HTTPConnection('127.0.0.1',httpd.server_port,timeout=4);headers={'Content-Type':'application/json'}
            if authorized:headers['X-Console-Token']=self.app.token
            if origin:headers['Origin']=origin
            conn.request('POST' if body is not None else 'GET',path,json.dumps(body) if body is not None else None,headers)
            response=conn.getresponse();result=(response.status,json.loads(response.read()));conn.close();return result
        try:
            body={'ui':{'font':14,'idleMs':12000},'conversation_id':self.conv['id']}
            self.assertEqual(request('/api/configuration/export',body,False)[0],403)
            self.assertEqual(request('/api/configuration/export',body,origin='https://elsewhere.example')[0],403)
            code,doc=request('/api/configuration/export',body);self.assertEqual(code,200)
            body.update(document=doc,apply_window=False);code,preview=request('/api/configuration/preview',body);self.assertEqual(code,200)
            body.update(revision=preview['revision'],acknowledge=True);code,result=request('/api/configuration/import',body);self.assertEqual(code,200)
            code,previous=request(result['rollback_url']);self.assertEqual(code,200);validate_configuration(previous)
        finally:httpd.shutdown();httpd.server_close();worker.join(3)

if __name__=='__main__':unittest.main(verbosity=2)
