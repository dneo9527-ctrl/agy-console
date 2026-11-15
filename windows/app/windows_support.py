"""Windows helpers; no service installation, credential access or global settings."""
import os
from pathlib import Path
import queue
import shutil
import subprocess
import sys
import threading
import time
from types import SimpleNamespace


def find_agy():
    override = os.environ.get('AGY_BINARY')
    candidates = ([override] if override else []) + [shutil.which('agy.exe'),
        str(Path(os.environ.get('LOCALAPPDATA', Path.home() / 'AppData/Local')) / 'agy/bin/agy.exe'),
        str(Path.home() / '.local/bin/agy.exe')]
    return next((str(Path(x).resolve()) for x in candidates if x and Path(x).is_file()), 'agy.exe')


def console_attached():
    if os.name != 'nt':
        return False
    import ctypes
    from ctypes import wintypes
    kernel = ctypes.WinDLL('kernel32', use_last_error=True)
    kernel.GetStdHandle.argtypes = [wintypes.DWORD]
    kernel.GetStdHandle.restype = wintypes.HANDLE
    kernel.GetConsoleMode.argtypes = [wintypes.HANDLE, ctypes.POINTER(wintypes.DWORD)]
    mode = wintypes.DWORD()
    return all(kernel.GetConsoleMode(kernel.GetStdHandle(code & 0xffffffff), ctypes.byref(mode)) for code in (-10, -11))


def terminate_tree(proc):
    if proc.poll() is not None:
        return
    # PID is one of this console's registered children, never a name-wide kill.
    subprocess.run(['taskkill', '/PID', str(proc.pid), '/T', '/F'], capture_output=True,
                   timeout=5, creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))


class PipeSelector:
    """Bounded stream queue; socket-only Windows select cannot read CLI pipes."""
    def __init__(self):
        self.events = queue.Queue(maxsize=16)
        self.keys = {}
        self.closed = threading.Event()
        self.threads = []

    def register(self, fileobj, events, data=None):
        key = SimpleNamespace(fileobj=fileobj, data=data)
        self.keys[fileobj] = key
        def publish(value):
            while not self.closed.is_set():
                try:
                    self.events.put(value, timeout=.1)
                    return
                except queue.Full:
                    pass
        def read():
            try:
                while not self.closed.is_set():
                    chunk = os.read(fileobj.fileno(), 65536)
                    if not chunk:
                        break
                    publish((key, chunk))
            except (OSError, ValueError):
                pass
            finally:
                publish((key, b''))
        thread = threading.Thread(target=read, daemon=True)
        self.threads.append(thread)
        thread.start()
        return key

    def get_map(self):
        return self.keys

    def select(self, timeout=None):
        try:
            key, chunk = self.events.get(timeout=timeout)
            return [(SimpleNamespace(fileobj=key.fileobj, data=key.data, chunk=chunk), 1)]
        except queue.Empty:
            return []

    def unregister(self, fileobj):
        self.keys.pop(fileobj, None)

    def close(self):
        self.closed.set()
        # OS reads bypass BufferedReader's lock; joining is always bounded.
        for stream in list(self.keys):
            try:
                stream.close()
            except OSError:
                pass
        self.keys.clear()
        for thread in self.threads:
            thread.join(timeout=.2)


def process_rows():
    import psutil
    rows = []
    for proc in psutil.process_iter(['pid', 'ppid', 'name', 'memory_info']):
        try:
            value = proc.info
            rows.append({'pid': value['pid'], 'parent_pid': value['ppid'], 'pgid': None,
                         'rss_mb': round(value['memory_info'].rss / 1000000, 1),
                         'cpu_percent': None, 'name': (value['name'] or '')[:80]})
        except (psutil.Error, KeyError, AttributeError):
            continue
    return rows


def activity_processes(binary, excluded):
    rows = process_rows()
    by_pid = {p['pid']: p for p in rows}
    return [dict(pid=p['pid'], parent_pid=p['parent_pid'], terminal=None, elapsed='',
                 program=p['name'], origin=by_pid.get(p['parent_pid'], {}).get('name', 'Windows'),
                 window_label='CLI process ' + str(p['pid']))
            for p in rows if p['name'].lower() == Path(binary).name.lower() and p['pid'] not in excluded]


def system_proxy():
    import winreg
    from urllib.parse import urlsplit
    with winreg.OpenKey(winreg.HKEY_CURRENT_USER,
                        r'Software\Microsoft\Windows\CurrentVersion\Internet Settings') as key:
        if winreg.QueryValueEx(key, 'ProxyEnable')[0] != 1:
            return {}
        raw = winreg.QueryValueEx(key, 'ProxyServer')[0]
    if not isinstance(raw, str) or len(raw) > 2048:
        return {}
    endpoints = dict(part.split('=', 1) for part in raw.split(';') if '=' in part)
    if not endpoints:
        endpoints = {'http': raw, 'https': raw}
    result = {}
    for scheme, prefix in (('http', 'HTTP'), ('https', 'HTTPS')):
        value = endpoints.get(scheme)
        if not value:
            continue
        address = urlsplit(value if '://' in value else 'http://' + value)
        if address.username or address.password or address.scheme != 'http':
            continue
        result.update({prefix+'Enable': '1', prefix+'Proxy': address.hostname or '',
                       prefix+'Port': str(address.port or 0)})
    return result


def probe_account(binary, workspace, timeout=15, register=None, unregister=None, stopping=None):
    from account_status import selected_identity, parse_identity, SOURCE
    from datetime import datetime, timezone
    import json
    import tempfile
    unknown = {'state': 'unknown', 'email': None, 'name': None, 'plan_tier': None, 'quota': {},
               'source': SOURCE, 'checked_at': datetime.now(timezone.utc).isoformat(),
               'detail': '暂未取得 Windows CLI 账号或额度，请先完成官方登录。'}
    process = None
    adapter = None
    # ConPTY is included in the portable package. Never interpret failure as zero quota.
    try:
        from winpty import PtyProcess, Backend
        with tempfile.TemporaryDirectory(prefix='status-', dir=workspace, ignore_cleanup_errors=True) as directory:
            capture = Path(directory) / 'identity.json'
            hook = subprocess.list2cmdline([sys.executable, str(Path(__file__).with_name('account_status.py')),
                                            '--statusline', '--snapshot', str(capture)])
            settings = {'statusLine': {'type':'command', 'command':hook, 'enabled':True}}
            command = [str(binary), '--sandbox', '--settings', json.dumps(settings)]
            env = dict(os.environ, TERM='xterm-256color', BROWSER=str(Path(__file__).with_name('no-browser.cmd')))
            process = PtyProcess.spawn(command, cwd=str(workspace), env=env, dimensions=(35, 200), backend=Backend.ConPTY)
            class Adapter:
                pid = process.pid
                def poll(self):
                    return None if process.isalive() else 0
                def wait(self, timeout=None):
                    deadline = time.monotonic() + (timeout or 3)
                    while process.isalive() and time.monotonic() < deadline:
                        time.sleep(.05)
                    if process.isalive():
                        raise subprocess.TimeoutExpired('account check', timeout)
                    return 0
                def terminate(self):
                    process.terminate(force=True)
                kill = terminate
            adapter = Adapter()
            if register:
                register(adapter)
            closed = threading.Event()
            def drain():
                try:
                    while not closed.is_set() and process.isalive():
                        process.read(8192)
                except (OSError, EOFError):
                    pass
            reader = threading.Thread(target=drain, daemon=True)
            reader.start()
            deadline = time.monotonic() + timeout
            found = None
            while time.monotonic() < deadline and not (stopping and stopping()):
                try:
                    if capture.stat().st_size <= 32768:
                        value = selected_identity(json.loads(capture.read_text(encoding='utf-8')))
                        if value:
                            found = dict(unknown, state='known', detail='已读取官方 CLI 当前会话的账号。', **value)
                            if value.get('quota'):
                                break
                except (OSError, ValueError):
                    pass
                if not process.isalive():
                    break
                time.sleep(.1)
            closed.set()
            process.terminate(force=True)
            if adapter.poll() is None:
                terminate_tree(adapter)
            adapter.wait(timeout=3)
            process.close(force=True)
            reader.join(timeout=.5)
            return found or unknown
    except Exception:
        return unknown
    finally:
        if process:
            try:
                process.close(force=True)
            except Exception:
                pass
        if adapter and unregister:
            unregister(adapter)
