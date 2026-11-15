"""Independent window text/image choices, shared fallback, and real bridge routing."""
import copy
import json
from pathlib import Path
import subprocess
import sys
import threading
import unittest
from model_policy import default_model
from panel_backups import validate_state
from server import Console, Handler, ThreadingHTTPServer
import test_codex_bridge as bridge_tests
from test_panel_backups import fixture_state

ARGON='gemini-4-argon-high';FLASH='gemini-3.8-flash-high'
THREAD=bridge_tests.THREAD;OTHER=bridge_tests.OTHER


class WindowModelTests(unittest.TestCase):
    setUp=bridge_tests.BindingTests.setUp
    tearDown=bridge_tests.BindingTests.tearDown
    wait=bridge_tests.BindingTests.wait

    def setup_models(self):
        self.app.health['models']=[{'id':x,'label':x} for x in (ARGON,FLASH)]
        a=self.app.bind_codex(THREAD);b=self.app.bind_codex(OTHER)
        return a,b

    def test_windows_modes_and_inherit_are_independent_persist_and_back_up(self):
        a,b=self.setup_models();self.app.set_preferred_model(ARGON)
        self.app.set_window_model(a['id'],'text',FLASH)
        self.app.set_window_model(a['id'],'image',ARGON)
        s=self.app.snapshot();ca=next(c for c in s['conversations'] if c['id']==a['id']);cb=next(c for c in s['conversations'] if c['id']==b['id'])
        self.assertEqual(default_model(s,ca,'text'),FLASH);self.assertEqual(default_model(s,ca,'image'),ARGON)
        self.assertEqual(default_model(s,cb,'text'),ARGON);self.assertEqual(s['preferred_model'],ARGON)
        self.app.set_preferred_model(FLASH)
        s=self.app.snapshot();self.assertEqual(default_model(s,ca,'image'),ARGON);self.assertEqual(default_model(s,cb,'image'),FLASH)
        self.app.set_window_model(a['id'],'image',None)
        self.assertNotIn('image',self.app.conversation(a['id'])['model_preferences'])
        self.app.close();self.app=Console(self.app.data,self.binary,import_history=False)
        self.assertEqual(self.app.conversation(a['id'])['model_preferences'],{'text':FLASH})
        state=fixture_state();state['conversations'][0]['model_preferences']={'text':FLASH,'image':''};validate_state(state)
        state['conversations'][0]['model_preferences']['bad']='invalid'
        with self.assertRaises(ValueError):validate_state(state)

    def test_smart_choice_does_not_inherit_fixed_global_model(self):
        a,_=self.setup_models();self.app.set_preferred_model(ARGON)
        self.app.health['account']={'state':'known','quota':{'gemini-argon-wtu-weekly':{'remaining_fraction':0}}}
        self.app.set_window_model(a['id'],'text','');conv=self.app.conversation(a['id'])
        self.assertEqual(default_model(self.app.snapshot(),conv,'text'),FLASH)
        self.assertEqual(default_model(self.app.snapshot(),conv,'image'),ARGON)
        with self.assertRaises(ValueError):self.app.set_window_model(a['id'],'text','no-such-model')
        with self.assertRaises(ValueError):self.app.set_window_model(a['id'],'image',False)
        with self.assertRaises(ValueError):self.app.set_window_model(a['id'],'audio',FLASH)

    def test_pending_jobs_keep_model_and_explicit_task_overrides_only_one_turn(self):
        a,b=self.setup_models();self.app.set_preferred_model(ARGON);self.app.set_window_model(a['id'],'text',FLASH)
        with self.app.lock:
            t1=self.app.submit(a['id'],'first','');t2=self.app.submit(b['id'],'first','')
            explicit=self.app.submit(a['id'],'first',ARGON)
            self.app.set_window_model(a['id'],'text',ARGON)
            self.app.set_preferred_model(FLASH)
            self.assertEqual([t1['model'],t2['model'],explicit['model']],[FLASH,ARGON,ARGON])
            for t in (t1,t2,explicit):self.app.stop(t['id'])
        self.assertEqual(self.app.conversation(a['id'])['model_preferences']['text'],ARGON)

    def test_new_image_job_uses_its_window_image_setting(self):
        a,_=self.setup_models();self.app.set_preferred_model(ARGON)
        self.app.set_window_model(a['id'],'text',ARGON);self.app.set_window_model(a['id'],'image',FLASH)
        with self.app.lock:
            image=self.app.submit(a['id'],'make image','','image')
            self.assertEqual(image['model'],FLASH);self.app.stop(image['id'])

    def test_bridge_without_model_uses_window_setting_and_emits_panel_action(self):
        a,_=self.setup_models();self.app.set_preferred_model(ARGON);self.app.set_window_model(a['id'],'text',FLASH)
        task=self.root/'task.txt';task.write_text('first')
        httpd=ThreadingHTTPServer(('127.0.0.1',0),Handler);httpd.app=self.app
        worker=threading.Thread(target=httpd.serve_forever,daemon=True);worker.start()
        try:
            result=subprocess.run([sys.executable,str(Path(__file__).with_name('bridge.py')),'--url','http://127.0.0.1:'+str(httpd.server_port),
                                   '--thread-id',THREAD,'--task-file',str(task),'--timeout','8','--out',str(self.root/'result')],capture_output=True,text=True,timeout=12)
            self.assertEqual(result.returncode,0,result.stderr)
            sent=json.loads(result.stdout.splitlines()[0]);self.assertEqual(sent['model'],FLASH)
            self.assertEqual(sent['panel_action']['placement'],'right');self.assertFalse(sent['panel_action']['verified_visible'])
            self.assertEqual(json.loads((self.root/'result/result.json').read_text())['model'],FLASH)
            self.assertEqual(self.app.state['preferred_model'],ARGON)
        finally:httpd.shutdown();httpd.server_close();worker.join(3)

if __name__=='__main__':unittest.main()
