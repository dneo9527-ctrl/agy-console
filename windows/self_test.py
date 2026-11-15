"""Local tests only. Native Windows checks never call a model or use an account."""
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time
import unittest
ROOT=Path(__file__).resolve().parent
sys.path.insert(0,str(ROOT/'app'))
from windows_support import PipeSelector

if len(sys.argv)>1 and sys.argv[1]=='--leaf-fixture':
    leaf=subprocess.Popen([sys.executable,'-c','import time; time.sleep(60)'])
    Path(sys.argv[2]).write_text(json.dumps([os.getpid(),leaf.pid]))
    time.sleep(60);raise SystemExit()
if len(sys.argv)>1 and sys.argv[1]=='--job-fixture':
    from launcher import run_owned
    raise SystemExit(run_owned([sys.executable,__file__,'--leaf-fixture',sys.argv[2]],ROOT))

class PortableTests(unittest.TestCase):
    def test_pipe_output_and_error_and_eof(self):
        proc=subprocess.Popen([sys.executable,'-u','-c','import sys; print("stream-ok"); print("error-ok",file=sys.stderr)'],stdout=subprocess.PIPE,stderr=subprocess.PIPE)
        reader=PipeSelector();reader.register(proc.stdout,1,'out');reader.register(proc.stderr,1,'err')
        buffers={'out':b'','err':b''};deadline=time.monotonic()+5
        try:
            while reader.get_map() and time.monotonic()<deadline:
                for key,_ in reader.select(.1):
                    if not key.chunk:reader.unregister(key.fileobj)
                    else:buffers[key.data]+=key.chunk
            self.assertFalse(reader.get_map());self.assertIn(b'stream-ok',buffers['out']);self.assertIn(b'error-ok',buffers['err'])
        finally:
            proc.wait(timeout=3);reader.close();proc.stdout.close();proc.stderr.close()

    def test_policy_order_tiers_and_quota(self):
        from model_policy import candidate_models, effective_order
        ids=['gemini-4-argon-medium','gemini-4-argon-high','gemini-3.8-flash-high','gemini-3.8-flash-low','claude-sonnet-5-5-medium']
        state={'health':{'models':[{'id':m} for m in ids]},'model_order':['gemini-3.8-flash-low','gemini-4-argon-medium']}
        self.assertEqual(candidate_models(state)[:2],['gemini-3.8-flash-low','gemini-4-argon-medium'])
        self.assertEqual(effective_order(state)[0],'gemini-3.8-flash-low')
        state['health']['account']={'state':'unknown','quota':{'gemini-5h':{'remaining_fraction':0}}}
        self.assertEqual(candidate_models(state)[0],'gemini-3.8-flash-low')
        state['health']['account']['state']='known'
        self.assertEqual(candidate_models(state),['claude-sonnet-5-5-medium'])

    @unittest.skipUnless(os.name=='nt','native Windows required')
    def test_native_imports(self):
        import psutil,winpty,server,account_status
        self.assertTrue(psutil.Process().memory_info().rss>0)

    @unittest.skipUnless(os.name=='nt','native Windows required')
    def test_conpty_output_and_cleanup(self):
        from winpty import PtyProcess, Backend
        import threading
        proc=PtyProcess.spawn([sys.executable,'-u','-c','print("conpty-ok")'],backend=Backend.ConPTY)
        chunks=[]
        def read():
            try:
                while True:chunks.append(proc.read(4096))
            except (OSError,EOFError):pass
        reader=threading.Thread(target=read,daemon=True);reader.start()
        try:
            reader.join(timeout=8)
            self.assertIn('conpty-ok',''.join(chunks))
        finally:
            proc.close(force=True);reader.join(timeout=.5)
        self.assertFalse(proc.isalive())

    @unittest.skipUnless(os.name=='nt','native Windows required')
    def test_supervisor_death_removes_children(self):
        import psutil
        with tempfile.TemporaryDirectory() as directory:
            path=Path(directory)/'pids.json'
            supervisor=subprocess.Popen([sys.executable,__file__,'--job-fixture',str(path)])
            ids=[]
            try:
                deadline=time.monotonic()+8
                while not path.exists() and time.monotonic()<deadline and supervisor.poll() is None:time.sleep(.05)
                self.assertTrue(path.exists(),'job child did not start safely')
                ids=json.loads(path.read_text())
                owned=[psutil.Process(pid) for pid in ids]
                supervisor.terminate();supervisor.wait(timeout=5)
                gone,alive=psutil.wait_procs(owned,timeout=5)
                self.assertFalse(alive,'owned processes survived supervisor termination')
            finally:
                if supervisor.poll() is None:supervisor.kill();supervisor.wait(timeout=3)
                # Only disposable test fixture PIDs, never external CLI processes.
                for pid in ids:
                    try:psutil.Process(pid).kill()
                    except psutil.NoSuchProcess:pass

if __name__=='__main__':unittest.main(verbosity=2)
