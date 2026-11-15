"""Backup validation, actual roundtrip and rollback under injected failures."""
import copy
import hashlib
import http.client
import io
import json
import os
from pathlib import Path
import stat
import tempfile
import threading
import unittest
from unittest.mock import patch
import zipfile

from panel_backups import PanelBackups, MAX_UPLOAD, validate_state
from server import Console, ConflictError, Handler, ThreadingHTTPServer
from test_panel_actions import THREAD, SID, archived_turn, FAKE_SCOPED


def fixture_state():
    return {'conversations':[{'id':'a'*32,'title':'Saved chat','codex_thread_id':THREAD,'agy_id':SID,
                             'branches':{'main':{'id':'main','title':'Saved context','agy_id':SID}},
                             'primary_branch':'main','gpt_collaboration':False,'turns':[archived_turn(1,status='completed')]}],
            'preferred_model':'gemini-3.8-flash-high','preferred_model_updated_at':None,'history_overrides':{}}


class BackupTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.parent=Path(self.temp.name);self.root=self.parent/'data';self.root.mkdir()
        self.state=fixture_state();self.manager=PanelBackups(self.root)
        (self.root/'state.json').write_text(json.dumps(self.state))
        self.run=self.root/'runs'/format(1,'032x');self.run.mkdir(parents=True)
        (self.run/'response.md').write_text('Saved response')
        (self.run/'request.txt').write_text('Saved request')
        self.preferences={'settings':{'font':18,'idleMs':4000,'modelOrder':['gemini-3.8-flash-high']},'pins':['panel:'+'a'*32],
                          'credentials':'MUST BE EXCLUDED','draft':'MUST BE EXCLUDED'}

    def tearDown(self):self.temp.cleanup()

    def archive(self):
        result=self.manager.create(self.state,self.preferences)
        return self.root/'backups'/(result['id']+'.zip')

    def add_image(self):
        raw=b'fixture-image';digest=hashlib.sha256(raw).hexdigest();url='/api/turns/'+format(1,'032x')+'/images/'+digest
        image={'id':digest,'file':digest+'.png','name':'Fixture.png','mime':'image/png','bytes':len(raw),'url':url,'download_url':url+'?download=1'}
        self.state['conversations'][0]['turns'][0]['images']=[image]
        directory=self.run/'images';directory.mkdir();(directory/image['file']).write_bytes(raw)
        return image,raw

    def test_roundtrip_images_binding_model_and_preferences_with_rollback(self):
        self.state['conversations'][0]['auto_open_activity'] = False
        from task_progress import record, finish_progress
        turn = self.state['conversations'][0]['turns'][0]
        record(turn, 'step:1', '真实步骤', 'done')
        finish_progress(turn)
        image,raw=self.add_image();archive=self.archive();preview=self.manager.preview(archive)
        self.assertEqual(preview['counts']['images'],1);self.assertEqual(preview['counts']['bindings'],1)
        current=copy.deepcopy(self.state);current['conversations'][0]['title']='Newer live title'
        (self.root/'state.json').write_text(json.dumps(current))
        restored,result=self.manager.restore(preview['id'],current,{})
        expected={**self.state,'panel_restored_at':restored['panel_restored_at']}
        self.assertEqual(restored,expected)
        self.assertEqual((self.run/'images'/image['file']).read_bytes(),raw)
        self.assertEqual(restored['conversations'][0]['agy_id'],SID)
        self.assertFalse(restored['conversations'][0]['gpt_collaboration'])
        self.assertFalse(restored['conversations'][0]['auto_open_activity'])
        self.assertEqual(result['ui']['settings']['font'],18)
        self.assertEqual(restored['conversations'][0]['turns'][0]['progress'], turn['progress'])
        rollback=Path(result['rollback_directory'])
        self.assertEqual(json.loads((rollback/'state.json').read_text())['conversations'][0]['title'],'Newer live title')
        rollback_zip=self.root/'backups'/(result['rollback']['id']+'.zip')
        self.assertTrue(rollback_zip.is_file())
        with zipfile.ZipFile(rollback_zip) as z:
            self.assertEqual(json.loads(z.read('state.json'))['conversations'][0]['title'],'Newer live title')

    def test_malformed_progress_refused_before_restore(self):
        bad_values = ([None], 'not-a-list', [{'key': 'broken'}],
                      [{'key': 'step:1', 'label': 'label', 'state': 'active', 'started_at': 't', 'updated_at': 't'}])
        for progress in bad_values:
            with self.subTest(progress=progress):
                state = copy.deepcopy(self.state)
                state['conversations'][0]['turns'][0]['progress'] = progress
                with self.assertRaises(ValueError):validate_state(state)

    def test_backup_excludes_workspaces_accounts_unlisted_files_and_recursive_backups(self):
        for path in ['workspaces/project.txt','official/login.json','backups/older.zip','runs/'+format(1,'032x')+'/private.json']:
            target=self.root/path;target.parent.mkdir(parents=True,exist_ok=True);target.write_text('EXCLUDE')
        with zipfile.ZipFile(self.archive()) as z:
            names=z.namelist()
            self.assertEqual(set(names),{'state.json','manifest.json','runs/'+format(1,'032x')+'/request.txt','runs/'+format(1,'032x')+'/response.md'})
            manifest=json.loads(z.read('manifest.json'))
            self.assertNotIn('MUST BE EXCLUDED',json.dumps(manifest))

    def test_missing_or_changed_image_and_symlinks_refuse_export(self):
        image,_=self.add_image();file=self.run/'images'/image['file'];file.unlink()
        with self.assertRaises(ValueError):self.archive()
        file.write_bytes(b'changed')
        with self.assertRaises(ValueError):self.archive()
        self.state['conversations'][0]['turns'][0]['images']=[]
        (self.run/'response.md').unlink();(self.run/'response.md').symlink_to(self.root/'state.json')
        with self.assertRaises(ValueError):self.archive()

    def test_os_parent_alias_allowed_but_symlinked_run_parent_is_refused(self):
        alias=self.parent/'alias';alias.symlink_to(self.parent,target_is_directory=True)
        manager=PanelBackups(alias/'data');self.assertTrue(manager.create(self.state)['bytes'])
        self.add_image();runs=self.root/'runs';moved=self.root/'real-runs';runs.rename(moved);runs.symlink_to(moved,target_is_directory=True)
        with self.assertRaises(ValueError):self.archive()

    def modified_zip(self,extra=None,mutate=None):
        original=self.archive();incoming=self.parent/'incoming.zip'
        with zipfile.ZipFile(original) as source,zipfile.ZipFile(incoming,'w') as target:
            for info in source.infolist():
                raw=source.read(info)
                if mutate:raw=mutate(info.filename,raw)
                target.writestr(info,raw)
            if extra:target.writestr(*extra)
        return incoming

    def test_paths_symlinks_duplicate_entries_and_hash_tampering_refused(self):
        import warnings
        for name in ['../escape','/absolute','runs/../../escape','runs\\escape','account.json']:
            with self.subTest(name=name),self.assertRaises(ValueError):self.manager.preview(self.modified_zip((name,'bad')))
        link=zipfile.ZipInfo('runs/'+format(1,'032x')+'/stderr.log');link.create_system=3;link.external_attr=(stat.S_IFLNK|0o777)<<16
        with self.assertRaises(ValueError):self.manager.preview(self.modified_zip((link,'outside')))
        with warnings.catch_warnings():
            warnings.simplefilter('ignore',UserWarning)
            duplicate=self.modified_zip(('state.json','{}'))
        with self.assertRaises(ValueError):self.manager.preview(duplicate)
        tampered=self.modified_zip(mutate=lambda name,raw:b'corrupt' if name=='state.json' else raw)
        with self.assertRaises(ValueError):self.manager.preview(tampered)
        self.assertEqual(json.loads((self.root/'state.json').read_text()),self.state)
        self.assertFalse(list(self.parent.glob('.agy-restore-stage-*')))

    def test_size_limits_and_non_zip(self):
        incoming=self.parent/'large.zip'
        with incoming.open('wb') as stream:stream.truncate(MAX_UPLOAD+1)
        with self.assertRaises(ValueError):self.manager.preview(incoming)
        incoming.write_bytes(b'not a zip')
        with self.assertRaises(ValueError):self.manager.preview(incoming)
        with patch('panel_backups.MAX_EXPANDED',1):
            with self.assertRaises(ValueError):self.manager.preview(self.archive())

    def test_invalid_or_busy_state_binding_duplicates_and_image_links_rejected(self):
        for mutate in [lambda s:s['conversations'][0]['turns'][0].update(status='running'),
                       lambda s:s['conversations'].append(copy.deepcopy(s['conversations'][0])),
                       lambda s:s['conversations'][0].update(agy_id='bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb'),
                       lambda s:s['conversations'][0]['turns'][0].update(branch='missing'),
                       lambda s:s['conversations'][0]['turns'][0].update(elapsed=float('nan'))]:
            value=copy.deepcopy(self.state);mutate(value)
            with self.assertRaises(ValueError):validate_state(value)

    def test_second_rename_failure_rolls_back_existing_data(self):
        preview=self.manager.preview(self.archive());original=os.replace
        def fail_stage(source,target):
            if Path(source).name.startswith('.agy-restore-stage-'):raise OSError('injected rename failure')
            return original(source,target)
        with patch('panel_backups.os.replace',side_effect=fail_stage),self.assertRaises(OSError):
            self.manager.restore(preview['id'],self.state,{})
        self.assertTrue((self.root/'state.json').is_file())
        self.assertEqual(json.loads((self.root/'state.json').read_text()),self.state)

    def test_startup_recovers_gap_after_terminal_closed_between_renames(self):
        preview=self.manager.preview(self.archive());stage=self.manager.previews[preview['id']][0]
        rollback=self.parent/('data-before-restore-'+'b'*16)
        journal=self.parent/'.data-restore-journal.json'
        journal.write_text(json.dumps({'root':'data','stage':stage.name,'rollback':rollback.name}))
        self.root.rename(rollback)
        PanelBackups.recover_interrupted(self.root)
        self.assertTrue((self.root/'state.json').exists());self.assertFalse(journal.exists());self.assertFalse(rollback.exists())

    def test_preview_is_bounded_and_expires(self):
        archive=self.archive();first=self.manager.preview(archive)
        first_stage=self.manager.previews[first['id']][0]
        self.manager.preview(archive);self.manager.preview(archive)
        self.assertEqual(len(self.manager.previews),2);self.assertFalse(first_stage.exists())
        with patch('panel_backups.time.monotonic',return_value=10**20):self.manager.expire()
        self.assertFalse(self.manager.previews)


class BackupHttpTests(unittest.TestCase):
    def test_http_binary_auth_roundtrip_idle_and_missing_official_context(self):
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder);binary=root/'fake';binary.write_text(FAKE_SCOPED);binary.chmod(0o700)
            app=Console(root/'data',binary,import_history=False,account_reader=lambda *_a,**_k:{'state':'unknown','email':None})
            app.activity.root=root/'official';app.state=fixture_state();app.save()
            httpd=ThreadingHTTPServer(('127.0.0.1',0),Handler);httpd.app=app
            worker=threading.Thread(target=httpd.serve_forever,daemon=True);worker.start()
            headers={'Content-Type':'application/json','X-Console-Token':app.token}
            def request(method,path,body=None,extra=None):
                client=http.client.HTTPConnection('127.0.0.1',httpd.server_port,timeout=10)
                try:
                    client.request(method,path,body,extra or {});response=client.getresponse();return response.status,response.read()
                finally:client.close()
            def post(path,body):
                code,raw=request('POST',path,json.dumps(body),headers);return code,json.loads(raw)
            try:
                self.assertEqual(request('POST','/api/backups/preview',b'zip',{'Content-Type':'application/zip'})[0],403)
                status,exported=post('/api/backups/export',{})
                self.assertEqual(status,201)
                status,archive=request('GET',exported['url']);self.assertEqual(status,200);self.assertTrue(zipfile.is_zipfile(io.BytesIO(archive)))
                binary_headers={'Content-Type':'application/zip','X-Console-Token':app.token}
                bad_origin={**binary_headers,'Origin':'https://evil.example'}
                self.assertEqual(request('POST','/api/backups/preview',archive,bad_origin)[0],403)
                status,raw=request('POST','/api/backups/preview',archive,binary_headers)
                self.assertEqual(status,200);preview=json.loads(raw)
                self.assertEqual(post('/api/backups/restore',{'id':preview['id']})[0],400)
                app.state['conversations'][0]['title']='Changed title';app.save()
                old_revision=app.conversation_revisions['a'*32]
                status,result=post('/api/backups/restore',{'id':preview['id'],'acknowledge':True})
                self.assertEqual(status,200);self.assertTrue(result['restored']);self.assertEqual(len(result['missing_contexts']),1)
                self.assertEqual(app.state['conversations'][0]['title'],'Saved chat')
                self.assertGreater(app.conversation_revisions['a'*32],old_revision)
                saved=json.loads((app.data/'state.json').read_text())
                self.assertTrue(saved['conversations'][0]['branches']['main']['restored_context_missing'])
                with self.assertRaisesRegex(ValueError,'官方会话上下文'):app.submit('a'*32,'continue','gemini-4-argon-high')
                self.assertFalse(app.maintenance);self.assertFalse(app.processes)
                app.state['conversations'][0]['turns'].append(archived_turn(2,status='running'))
                self.assertEqual(post('/api/backups/export',{})[0],409)
                self.assertEqual(post('/api/backups/restore',{'id':preview['id'],'acknowledge':True})[0],409)
                app.state['conversations'][0]['turns'].pop()
            finally:httpd.shutdown();httpd.server_close();worker.join(3);app.close()
            with patch.object(Console,'import_history') as legacy_import:
                restored=Console(root/'data',binary,import_history=True,
                                 account_reader=lambda *_a,**_k:{'state':'unknown','email':None})
                try:
                    legacy_import.assert_not_called()
                    self.assertEqual(len(restored.state['conversations']),1)
                    self.assertEqual(restored.state['conversations'][0]['agy_id'],SID)
                finally:restored.close()


if __name__=='__main__':unittest.main()
