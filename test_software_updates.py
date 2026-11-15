"""Disposable release/installation fixtures. No real GitHub or model calls."""
import hashlib
import io
import json
from pathlib import Path
import stat
import tempfile
import unittest
from unittest.mock import patch
import zipfile
from software_updates import SoftwareUpdates, safe_url, version_key
from update_bootstrap import apply_pending, confirm, recover, platform_target


class UpdateTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.root=Path(self.temp.name)/'应用程序';self.root.mkdir()
        self.data=Path(self.temp.name)/'数据';self.data.mkdir();(self.data/'keep.json').write_text('private fixture')
        (self.root/'VERSION').write_text('0.1.0-preview.1');(self.root/'app.js').write_text('old')
        (self.root/'release-source.json').write_text(json.dumps({'repository':'fixture/project','channel':'preview'}))
        self.version='0.1.1-preview.1';self.target=platform_target();self.make_archive()
        self.updater=SoftwareUpdates(self.root,self.data,idle=lambda:True,fetch=self.fetch,start=False)

    def tearDown(self):self.updater.close();self.temp.cleanup()

    def make_archive(self,extra=None,target=None):
        files={'VERSION':self.version.encode(),'app.js':b'new'}
        if extra:files.update(extra)
        manifest={'schema':1,'version':self.version,'target':target or self.target,'files':{k:hashlib.sha256(v).hexdigest() for k,v in files.items()}}
        stream=io.BytesIO()
        with zipfile.ZipFile(stream,'w') as z:
            for name,value in files.items():z.writestr(name,value)
            z.writestr('payload.json',json.dumps(manifest))
        self.archive=stream.getvalue();self.digest=hashlib.sha256(self.archive).hexdigest()

    def fetch(self,url,path,limit):
        if '/releases?' in url:
            value=[{'tag_name':'v'+self.version,'draft':False,'prerelease':True,'assets':[
                {'name':'updates.json','size':123},{'name':'program.zip','size':len(self.archive),'digest':'sha256:'+self.digest}]}]
            path.write_text(json.dumps(value))
        elif url.endswith('updates.json'):
            path.write_text(json.dumps({'schema':1,'version':self.version,'platforms':{self.target:{'file':'program.zip','bytes':len(self.archive),'sha256':self.digest}}}))
        else:path.write_bytes(self.archive)

    def test_stage_apply_confirm_preserves_data(self):
        self.assertEqual(self.updater.check()['latest']['version'],self.version)
        self.assertEqual(self.updater.stage()['pending_version'],self.version)
        self.assertEqual((self.root/'app.js').read_text(),'old','download cannot mutate a running installation')
        self.assertTrue(apply_pending(self.root,self.data,self.target))
        self.assertEqual((self.root/'app.js').read_text(),'new');confirm(self.root,self.data)
        self.assertFalse((self.data/'software-updates/pending.json').exists())
        self.assertEqual((self.data/'keep.json').read_text(),'private fixture')
        self.assertFalse(recover(self.root,self.data))

    def test_incomplete_startup_rolls_back(self):
        self.updater.check();self.updater.stage();apply_pending(self.root,self.data,self.target)
        self.assertTrue(recover(self.root,self.data));self.assertEqual((self.root/'app.js').read_text(),'old')
        self.assertEqual((self.root/'VERSION').read_text(),'0.1.0-preview.1')
        self.assertFalse(apply_pending(self.root,self.data,self.target))

    def test_copy_failure_rolls_back_and_no_downgrade(self):
        self.updater.check();self.updater.stage()
        import update_bootstrap
        original=update_bootstrap.replace_copy;calls=[]
        def copy(source,destination):
            calls.append(destination.name)
            if len(calls)==2:raise OSError('fixture interrupted copy')
            return original(source,destination)
        with patch('update_bootstrap.replace_copy',side_effect=copy):
            with self.assertRaises(OSError):apply_pending(self.root,self.data,self.target)
        self.assertEqual((self.root/'VERSION').read_text(),'0.1.0-preview.1');self.assertEqual((self.root/'app.js').read_text(),'old')
        self.updater.stage();(self.root/'VERSION').write_text('9.0.0')
        with self.assertRaises(ValueError):apply_pending(self.root,self.data,self.target)

    def test_real_bootstrap_failed_start_restarts_old_program(self):
        import subprocess,sys,update_bootstrap
        (self.root/'server.py').write_text('print("old-program-started")')
        self.make_archive({'server.py':b'raise RuntimeError("fixture startup failure")'})
        self.updater.check();self.updater.stage()
        run=subprocess.run([sys.executable,str(Path(update_bootstrap.__file__)),'--app-root',str(self.root),'--data',str(self.data)],capture_output=True,text=True,timeout=10)
        self.assertEqual(run.returncode,0,run.stderr);self.assertIn('old-program-started',run.stdout)
        self.assertEqual((self.root/'VERSION').read_text(),'0.1.0-preview.1')
        self.assertEqual((self.data/'keep.json').read_text(),'private fixture')

    def test_busy_never_downloads(self):
        self.updater.check();self.updater.idle=lambda:False
        with self.assertRaises(ValueError):self.updater.stage()
        self.assertFalse((self.data/'software-updates/pending.json').exists())

    def test_keyboard_interrupt_does_not_restart_program(self):
        import subprocess,sys,update_bootstrap
        (self.root/'server.py').write_text('print("old-program-started")')
        self.make_archive({'server.py':b'raise KeyboardInterrupt()'})
        self.updater.check();self.updater.stage()
        run=subprocess.run([sys.executable,str(Path(update_bootstrap.__file__)),'--app-root',str(self.root),'--data',str(self.data)],capture_output=True,text=True,timeout=10)
        self.assertNotEqual(run.returncode,0);self.assertNotIn('old-program-started',run.stdout)
        self.assertTrue(recover(self.root,self.data));self.assertEqual((self.root/'VERSION').read_text(),'0.1.0-preview.1')

    def test_platform_archive_and_hash_rejections(self):
        for extra,target in [({'../outside':b'bad'},None),({'update_bootstrap.py':b'bad'},None),({'server.py':b'not valid python !!!'},None),(None,'other-platform')]:
            with self.subTest(extra=extra,target=target):
                self.make_archive(extra,target);self.updater.check()
                with self.assertRaises((ValueError,SyntaxError)):self.updater.stage()
                self.assertFalse((self.data/'software-updates/pending.json').exists())
        self.make_archive();self.updater.check();self.archive+=b'corrupted'
        with self.assertRaises(ValueError):self.updater.stage()

    def test_duplicate_and_symlink_archives(self):
        for symlink in [False,True]:
            self.make_archive();stream=io.BytesIO(self.archive)
            with zipfile.ZipFile(stream,'a') as z:
                if symlink:
                    item=zipfile.ZipInfo('server.py');item.create_system=3;item.external_attr=(stat.S_IFLNK|0o777)<<16;z.writestr(item,'/outside')
                else:z.writestr('app.js',b'duplicate')
            self.archive=stream.getvalue();self.digest=hashlib.sha256(self.archive).hexdigest();self.updater.check()
            with self.assertRaises(ValueError):self.updater.stage()

    def test_channels_source_change_and_scope(self):
        self.updater.set_preferences({'repository':'fixture/project','channel':'stable','automatic':False})
        self.assertEqual(self.updater.check()['status'],'current')
        self.updater.set_preferences({'repository':'fixture/project','channel':'preview','automatic':True});self.updater.check();self.updater.stage()
        self.updater.set_preferences({'repository':'fixture/new','channel':'preview','automatic':False})
        self.assertIsNone(self.updater.snapshot()['pending_version'])
        self.assertIsNone(self.updater.snapshot()['latest'])
        for url in ['http://github.com/x','https://evil.test/x','https://github.com.evil.test/x','https://u:p@github.com/x','https://127.0.0.1/x']:
            with self.assertRaises(ValueError):safe_url(url)
        for repo in ['x/y/z','https://github.com/x/y','../repo']:
            with self.assertRaises(ValueError):self.updater.set_preferences({'repository':repo,'channel':'stable','automatic':False})

    def test_version_order(self):
        ordered=['0.1.0-preview.1','0.1.0-preview.2','0.1.0-preview.10','0.1.0','0.1.1','1.0.0']
        self.assertEqual(sorted(ordered,key=version_key),ordered)
        for bad in ['1','v1.0.0','01.0.0','1.0.0/../x',None]:
            with self.assertRaises(ValueError):version_key(bad)


if __name__=='__main__':unittest.main()
