"""Real controlling-terminal lifecycle checks; fake CLI, no model requests."""
import errno
import http.client
import json
import os
from pathlib import Path
import pty
import re
import signal
import socket
import subprocess
import sys
import tempfile
import time
import unittest


ROOT = Path(__file__).resolve().parent
MODEL = 'gemini-4-argon-high'
FAKE = '''#!/usr/bin/python3
import json, os, sys, time
from pathlib import Path
if '--version' in sys.argv:
    print('test-cli-1.0')
elif 'models' in sys.argv:
    print('gemini-4-argon-high\\tFake model')
elif '-p' not in sys.argv:
    pass  # Identity check has no model prompt and must not start a fake task.
else:
    Path(os.environ['AG_CONSOLE_TEST_PID']).write_text(str(os.getpid()))
    if os.environ.get('AG_CONSOLE_TEST_PROXY_FILE'):
        Path(os.environ['AG_CONSOLE_TEST_PROXY_FILE']).write_text(json.dumps({k:os.environ.get(k) for k in ('HTTP_PROXY','HTTPS_PROXY','NO_PROXY','no_proxy')}))
    def emit(value):
        print(json.dumps(value, ensure_ascii=False), flush=True)
    emit({'event':'init', 'conversation_id':'terminal-test-session'})
    emit({'event':'step_update', 'step_update':{'text_delta':'保留终端关闭前的回复'}})
    time.sleep(60)
'''


class TerminalProcess:
    """A child with an actual controlling PTY, not merely tty-looking pipes."""
    def __init__(self, command, env):
        self.pid, self.master = pty.fork()
        if self.pid == 0:
            try:
                os.execvpe(command[0], command, env)
            finally:
                os._exit(127)
        os.set_blocking(self.master, False)
        self.output = bytearray()
        self.returncode = None

    def drain(self):
        if self.master is None:
            return
        while True:
            try:
                chunk = os.read(self.master, 65536)
            except OSError as exc:
                if exc.errno in (errno.EAGAIN, errno.EWOULDBLOCK, errno.EIO):
                    return
                raise
            if not chunk:
                return
            self.output.extend(chunk)

    def poll(self):
        self.drain()
        if self.returncode is None:
            pid, status = os.waitpid(self.pid, os.WNOHANG)
            if pid:
                self.returncode = os.waitstatus_to_exitcode(status)
        return self.returncode

    def wait(self, timeout=8):
        deadline = time.monotonic() + timeout
        while self.poll() is None and time.monotonic() < deadline:
            time.sleep(.02)
        if self.returncode is None:
            raise TimeoutError(self.output.decode(errors='replace'))
        return self.returncode

    def close_terminal(self):
        if self.master is not None:
            self.drain()
            os.close(self.master)
            self.master = None

    def cleanup(self):
        self.close_terminal()
        if self.poll() is None:
            os.kill(self.pid, signal.SIGTERM)
            try:
                self.wait()
            except TimeoutError:
                os.kill(self.pid, signal.SIGKILL)
                self.wait()


class TerminalLifecycleTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.data = self.root / 'data'
        # These real-process lifecycle tests must not inherit the installed
        # release source and start unrelated GitHub checks.  Keep the actual
        # updater and reload code, but give this disposable profile no source.
        updates = self.data / 'software-updates'
        updates.mkdir(parents=True)
        (updates / 'preferences.json').write_text(json.dumps(
            {'repository':'', 'channel':'preview', 'automatic':False}))
        self.pid_file = self.root / 'fake-cli.pid'
        self.fake = self.root / 'fake-agy'
        self.fake.write_text(FAKE)
        self.fake.chmod(0o700)
        with socket.socket() as candidate:
            candidate.bind(('127.0.0.1', 0))
            self.port = candidate.getsockname()[1]
        self.env = dict(os.environ, AG_CONSOLE_TEST_PID=str(self.pid_file))
        self.command = [sys.executable, '-u', str(ROOT / 'server.py'), '--port', str(self.port),
                        '--data', str(self.data), '--agy-bin', str(self.fake)]
        self.terminals = []
        self.orphan_pids = []
        self.proc = self.launch(self.command)
        self.addCleanup(self.cleanup)
        self.wait_until(self.server_ready, 'server did not become ready')
        self.load_token()

    def launch(self, command):
        proc = TerminalProcess(command, self.env)
        self.terminals.append(proc)
        return proc

    def cleanup(self):
        for proc in reversed(self.terminals):
            proc.cleanup()
        for pid in self.orphan_pids:
            if self.pid_alive(pid):
                try:
                    os.kill(pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass
        if self.pid_file.exists():
            pid = int(self.pid_file.read_text())
            if self.pid_alive(pid):
                try:
                    os.kill(pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass
        self.temp.cleanup()

    def wait_until(self, check, message, timeout=8):
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            for proc in self.terminals:
                proc.drain()
            if check():
                return
            time.sleep(.03)
        self.fail(message + '\n' + '\n'.join(p.output.decode(errors='replace') for p in self.terminals))

    def request(self, method, path, body=None):
        client = http.client.HTTPConnection('127.0.0.1', self.port, timeout=1)
        headers = {}
        if body is not None:
            headers = {'Content-Type':'application/json', 'X-Console-Token':self.token}
            body = json.dumps(body)
        try:
            client.request(method, path, body=body, headers=headers)
            response = client.getresponse()
            return response.status, response.read()
        finally:
            client.close()

    def load_token(self):
        _, page = self.request('GET', '/')
        self.token = re.search(r'name="console-token" content="([^"]+)"', page.decode())[1]

    def server_ready(self):
        try:
            status, raw = self.request('GET', '/api/state')
            return status == 200 and json.loads(raw)['health']['connected']
        except (OSError, http.client.HTTPException):
            return False

    @staticmethod
    def pid_alive(pid):
        try:
            os.kill(pid, 0)
            return True
        except ProcessLookupError:
            return False

    def start_slow_task(self):
        status, raw = self.request('POST', '/api/conversations', {'title':'terminal lifecycle test'})
        self.assertEqual(status, 201)
        self.cid = json.loads(raw)['id']
        status, raw = self.request('POST', '/api/conversations/' + self.cid + '/turns',
                                   {'prompt':'slow lifecycle test', 'model':MODEL})
        self.assertEqual(status, 202)
        self.tid = json.loads(raw)['id']
        def partial_received():
            _, raw = self.request('GET', '/api/state')
            conv = next(c for c in json.loads(raw)['conversations'] if c['id'] == self.cid)
            return self.pid_file.exists() and '保留终端' in conv['turns'][0]['response']
        self.wait_until(partial_received, 'fake CLI did not produce partial output')
        return int(self.pid_file.read_text())

    def assert_port_closed(self):
        with socket.socket() as client:
            self.assertNotEqual(client.connect_ex(('127.0.0.1', self.port)), 0)

    def assert_partial_history_saved(self):
        state = json.loads((self.data / 'state.json').read_text())
        conv = next(c for c in state['conversations'] if c['id'] == self.cid)
        turn = conv['turns'][0]
        self.assertEqual(turn['status'], 'cancelled')
        self.assertEqual(turn['response'], '保留终端关闭前的回复')
        self.assertEqual((self.data / 'runs' / self.tid / 'response.md').read_text(), turn['response'])

    def test_closing_real_terminal_stops_server_and_owned_cli(self):
        cli_pid = self.start_slow_task()
        self.proc.close_terminal()
        self.assertEqual(self.proc.wait(), 0)
        self.wait_until(lambda:not self.pid_alive(cli_pid), 'owned fake CLI survived terminal close')
        self.assert_port_closed()
        self.assert_partial_history_saved()

    def test_task_inherits_proxy_and_local_bypass_and_closes_with_terminal(self):
        self.proc.cleanup()
        captured = self.root / 'proxy-env.json'
        self.env.update(HTTP_PROXY='http://127.0.0.1:9001', HTTPS_PROXY='http://127.0.0.1:9001',
                        NO_PROXY='example.org', no_proxy='internal.test', AG_CONSOLE_TEST_PROXY_FILE=str(captured))
        self.proc = self.launch(self.command)
        self.wait_until(self.server_ready, 'server did not restart'); self.load_token()
        cli_pid = self.start_slow_task()
        env = json.loads(captured.read_text())
        self.assertEqual(env['HTTPS_PROXY'], 'http://127.0.0.1:9001')
        self.assertEqual(set(env['NO_PROXY'].split(',')), {'example.org', 'internal.test', 'localhost', '127.0.0.1', '::1'})
        self.assertEqual(env['NO_PROXY'], env['no_proxy'])
        self.proc.close_terminal(); self.assertEqual(self.proc.wait(), 0)
        self.wait_until(lambda:not self.pid_alive(cli_pid), 'proxied fake CLI survived terminal close')
        self.assert_port_closed(); self.assert_partial_history_saved()

    def test_ctrl_c_from_terminal_closes_idle_server(self):
        os.write(self.proc.master, b'\x03')
        self.assertEqual(self.proc.wait(), 0)
        self.assert_port_closed()
        self.assertTrue((self.data / 'state.json').exists())
        self.assertFalse(self.pid_file.exists())

    def service(self):
        return json.loads(self.request('GET','/api/service')[1])

    def test_idle_reload_preserves_pid_terminal_records_and_global_model(self):
        self.wait_until(lambda:self.service()['can_reload'],'checks did not settle')
        self.request('POST','/api/codex/bind',{'thread_id':'11111111-2222-4333-8444-555555555555','title':'kept'})
        self.request('POST','/api/model',{'model':MODEL})
        old=self.service();old_token=self.token
        status,raw=self.request('POST','/api/service/reload',{})
        self.assertEqual(status,202);self.assertEqual(json.loads(raw)['previous_instance'],old['instance_id'])
        def updated():
            try:return self.service()['instance_id']!=old['instance_id']
            except (OSError,http.client.HTTPException):return False
        self.wait_until(updated,'foreground reload failed')
        new=self.service();self.assertEqual(new['pid'],old['pid']);self.assertIsNone(self.proc.poll())
        self.load_token();self.assertNotEqual(self.token,old_token)
        state=json.loads(self.request('GET','/api/state')[1]);self.assertEqual(state['preferred_model'],MODEL)
        self.assertTrue(any(c.get('codex_thread_id')=='11111111-2222-4333-8444-555555555555' for c in state['conversations']))
        fresh=self.token;self.token=old_token;self.assertEqual(self.request('POST','/api/conversations',{})[0],403);self.token=fresh
        self.proc.close_terminal();self.assertEqual(self.proc.wait(),0);self.assert_port_closed()

    def test_reload_refuses_active_work_without_cancelling_it(self):
        pid=self.start_slow_task();old=self.service()['instance_id']
        self.assertEqual(self.request('POST','/api/service/reload',{})[0],409)
        self.assertEqual(self.service()['instance_id'],old);self.assertTrue(self.pid_alive(pid))

    def test_reload_drains_and_rejects_a_preexisting_write_request(self):
        self.wait_until(lambda:self.service()['can_reload'],'checks did not settle')
        old=self.service()['instance_id'];before=json.loads(self.request('GET','/api/state')[1])
        pending=http.client.HTTPConnection('127.0.0.1',self.port,timeout=3)
        pending.putrequest('POST','/api/conversations');pending.putheader('Content-Type','application/json')
        pending.putheader('X-Console-Token',self.token);pending.putheader('Content-Length','2');pending.endheaders()
        try:
            self.assertEqual(self.request('POST','/api/service/reload',{})[0],202)
            pending.send(b'{}');response=pending.getresponse();self.assertEqual(response.status,409);response.read()
        finally:pending.close()
        def updated():
            try:return self.service()['instance_id']!=old
            except (OSError,http.client.HTTPException):return False
        self.wait_until(updated,'reload did not finish after in-flight request drained')
        state=json.loads(self.request('GET','/api/state')[1]);self.assertEqual(len(state['conversations']),len(before['conversations']))

    def test_terminal_close_during_reload_leaves_no_server(self):
        self.wait_until(lambda:self.service()['can_reload'],'checks did not settle')
        self.assertEqual(self.request('POST','/api/service/reload',{})[0],202)
        self.proc.close_terminal();self.assertIn(self.proc.wait(),(0,2));self.assert_port_closed()

    def test_second_legal_terminal_start_preserves_first_state(self):
        cli_pid = self.start_slow_task()
        state_path = self.data / 'state.json'
        original = state_path.read_bytes()
        duplicate = self.launch(self.command)
        self.assertNotEqual(duplicate.wait(), 0)
        self.assertEqual(state_path.read_bytes(), original)
        self.assertIsNone(self.proc.poll())
        self.assertTrue(self.pid_alive(cli_pid))
        status, raw = self.request('GET', '/api/state')
        self.assertEqual(status, 200)
        conv = next(c for c in json.loads(raw)['conversations'] if c['id'] == self.cid)
        self.assertEqual(conv['turns'][0]['status'], 'running')

    def test_no_terminal_rejected_before_touching_data(self):
        untouched = self.root / 'must-not-exist'
        command = self.command + ['--data', str(untouched)]
        result = subprocess.run(command, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                                stderr=subprocess.STDOUT, env=self.env, start_new_session=True, timeout=5)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('终端', result.stdout.decode(errors='replace'))
        self.assertFalse(untouched.exists())
        preserved = self.root / 'must-not-change'
        preserved.mkdir()
        original = b'{"sentinel": "not a console state"}'
        state = preserved / 'state.json'
        state.write_bytes(original)
        result = subprocess.run(self.command + ['--data', str(preserved)], stdin=subprocess.DEVNULL,
                                stdout=subprocess.PIPE, stderr=subprocess.STDOUT, env=self.env,
                                start_new_session=True, timeout=5)
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(state.read_bytes(), original)

    def test_parent_exit_stops_server_while_terminal_remains_open(self):
        # Keep the PTY session leader alive; kill only the server's intermediate
        # parent so terminal HUP cannot stand in for the parent-loss watchdog.
        os.write(self.proc.master, b'\x03')
        self.assertEqual(self.proc.wait(), 0)
        parent_info = self.root / 'parents.json'
        wrapper = '''import json, os, subprocess, sys, time
command=json.loads(sys.argv[1])
parent=os.fork()
if parent == 0:
    proc=subprocess.Popen(command)
    with open(sys.argv[2], 'w') as out:
        json.dump({'parent':os.getpid(), 'server':proc.pid}, out)
    proc.wait()
    os._exit(0)
while True:
    time.sleep(60)
'''
        terminal = self.launch([sys.executable, '-u', '-c', wrapper, json.dumps(self.command), str(parent_info)])
        self.wait_until(lambda:parent_info.exists() and self.server_ready(), 'wrapped server did not become ready')
        info = json.loads(parent_info.read_text())
        self.orphan_pids.extend([info['parent'], info['server']])
        self.load_token()
        cli_pid = self.start_slow_task()
        os.kill(info['parent'], signal.SIGKILL)
        self.wait_until(lambda:not self.pid_alive(info['server']), 'server survived parent exit')
        self.wait_until(lambda:not self.pid_alive(cli_pid), 'owned fake CLI survived parent exit')
        self.assertIsNone(terminal.poll(), 'PTY session leader should still be alive')
        self.assertIsNotNone(terminal.master, 'PTY master should still be open')
        self.assert_port_closed()
        self.assert_partial_history_saved()


if __name__ == '__main__':
    unittest.main(verbosity=2)
