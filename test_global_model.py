"""Global selection persists independently of chats and never starts a model."""
import http.client
import json
import unittest
import threading

from model_policy import default_model
from server import Console, Handler, ThreadingHTTPServer
import test_codex_bridge as binding_fixtures

FLASH = 'gemini-3.8-flash-high'
ARGON = 'gemini-4-argon-high'


class GlobalModelTests(unittest.TestCase):
    setUp = binding_fixtures.BindingTests.setUp
    tearDown = binding_fixtures.BindingTests.tearDown

    def models(self):
        self.app.health['models'] = [{'id': m, 'label': m} for m in (ARGON, FLASH)]

    def test_preference_survives_restart_and_both_snapshot_formats(self):
        self.models();before=len(self.app.state['conversations'])
        self.app.set_preferred_model(FLASH)
        for summaries in (True, False):
            s=self.app.snapshot(summaries=summaries)
            self.assertEqual(s['preferred_model'],FLASH)
            self.assertEqual(default_model(s),FLASH)
        self.assertEqual(len(self.app.state['conversations']),before)
        self.assertEqual(len(self.app.processes),0)
        self.app.close()
        self.app=Console(self.app.data,self.binary,import_history=False)
        self.assertEqual(self.app.snapshot()['preferred_model'],FLASH)

    def test_auto_obeys_quota_and_fixed_global_choice_is_explicit(self):
        self.models();self.app.health['account']={'state':'known','quota':{'gemini-argon-wtu-weekly':{'remaining_fraction':0}}}
        self.app.set_preferred_model('')
        self.assertEqual(default_model(self.app.snapshot()),FLASH)
        self.app.set_preferred_model(ARGON)
        self.assertEqual(default_model(self.app.snapshot()),ARGON)
        self.app.set_preferred_model(FLASH)
        with self.assertRaises(ValueError):self.app.set_preferred_model('not-advertised')
        with self.assertRaises(ValueError):self.app.set_preferred_model(False)
        self.assertEqual(self.app.snapshot()['preferred_model'],FLASH)

    def test_http_setting_requires_token_and_trusted_origin(self):
        self.models();httpd=ThreadingHTTPServer(('127.0.0.1',0),Handler);httpd.app=self.app
        worker=threading.Thread(target=httpd.serve_forever,daemon=True);worker.start()
        def post(headers):
            conn=http.client.HTTPConnection('127.0.0.1',httpd.server_port,timeout=3)
            conn.request('POST','/api/model',json.dumps({'model':FLASH}),headers)
            response=conn.getresponse();code=response.status;response.read();conn.close();return code
        try:
            base={'Content-Type':'application/json'}
            self.assertEqual(post(base),403)
            headers=dict(base,**{'X-Console-Token':self.app.token})
            self.assertEqual(post(dict(headers,Origin='https://elsewhere.example')),403)
            self.assertEqual(post(headers),200)
            self.assertEqual(self.app.snapshot()['preferred_model'],FLASH)
        finally:httpd.shutdown();httpd.server_close();worker.join(3)

    def test_global_change_never_rewrites_submitted_model_or_ids(self):
        self.models();conv=self.app.create();turn=self.app.submit(conv['id'],'first',FLASH)
        self.app.set_preferred_model(ARGON)
        self.assertEqual(turn['model'],FLASH)
        self.assertEqual(conv['id'],self.app.state['conversations'][0]['id'])
        self.app.stop(turn['id'])


if __name__=='__main__':unittest.main(verbosity=2)
