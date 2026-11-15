"""Read identity and quota from the documented status-line payload only."""
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import re
import selectors
import shlex
import signal
import struct
import subprocess
import sys
import tempfile
if os.name != 'nt':
    import termios
import time
if os.name != 'nt':
    import fcntl

MARKER = 'AGY_CONSOLE_ACCOUNT:'
EMAIL = re.compile(r'[^\s@<>\x00-\x1f]+@[^\s@<>\x00-\x1f]+\.[^\s@<>\x00-\x1f]+')
SOURCE = '官方 CLI statusLine'


def selected_quota(payload):
    """Keep documented quota fields; missing values never become zero or full."""
    if not isinstance(payload, dict):
        return {}
    result = {}
    for bucket, value in list(payload.items())[:32]:
        if not isinstance(bucket, str) or not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9._:-]{0,99}', bucket):
            continue
        if not isinstance(value, dict):
            continue
        clean = {}
        for key, maximum in (('remaining_fraction', 1), ('remaining_amount', 10**18),
                             ('reset_in_seconds', 315360000)):
            number = value.get(key)
            if isinstance(number, (int, float)) and not isinstance(number, bool) and 0 <= number <= maximum:
                clean[key] = number
        stamp = value.get('reset_time')
        if isinstance(stamp, str) and len(stamp) <= 80:
            try:
                date = datetime.fromisoformat(stamp.replace('Z', '+00:00'))
                if date.utcoffset() is not None:
                    clean['reset_time'] = stamp
            except ValueError:
                pass
        if isinstance(value.get('disabled'), bool):
            clean['disabled'] = value['disabled']
        if clean:
            result[bucket] = clean
    return result


def selected_identity(payload):
    if not isinstance(payload, dict):
        return None
    email = payload.get('email')
    if not isinstance(email, str) or len(email) > 254 or not EMAIL.fullmatch(email):
        return None
    tier = payload.get('plan_tier')
    identity = {'email': email, 'plan_tier': tier[:80] if isinstance(tier, str) else None}
    if 'quota' in payload:
        identity['quota'] = selected_quota(payload['quota'])
    return identity


def statusline(snapshot_path=None):
    try:
        identity = selected_identity(json.loads(sys.stdin.read(512 * 1024)))
        if identity:
            # Do not forward the full CLI session payload or any authentication data.
            if snapshot_path:
                path = Path(snapshot_path)
                with tempfile.NamedTemporaryFile(mode='w', dir=path.parent, prefix='.status-',
                                                 encoding='utf-8', delete=False) as output:
                    json.dump(identity, output, ensure_ascii=False)
                Path(output.name).replace(path)
            # Keep terminal output short: the TUI truncates long status lines.
            # This also remains compatible with already-running older readers.
            print(MARKER + json.dumps({key: identity[key] for key in ('email', 'plan_tier')}, ensure_ascii=False))
    except (ValueError, OSError):
        pass


def parse_identity(text):
    # Strip terminal control sequences; the marker is from our session-only helper.
    text = re.sub(r'\x1b\][^\x07]*(?:\x07|\x1b\\)', '', text)
    text = re.sub(r'\x1b\[[0-?]*[ -/]*[@-~]', '', text)
    latest = None
    for match in re.finditer(re.escape(MARKER), text):
        try:
            payload, _ = json.JSONDecoder().raw_decode(text[match.end():].lstrip())
            identity = selected_identity(payload)
            if identity:
                latest = identity
        except ValueError:
            continue
    return latest


def probe_account(binary, workspace, timeout=15, register=None, unregister=None, stopping=None):
    if os.name == 'nt':
        from windows_support import probe_account as windows_probe
        return windows_probe(binary, workspace, timeout, register, unregister, stopping)
    checked_at = datetime.now(timezone.utc).isoformat()
    unknown = {'state': 'unknown', 'email': None, 'name': None, 'plan_tier': None, 'quota': {},
               'source': SOURCE, 'checked_at': checked_at,
               'detail': '本次未读取到 CLI 账号，请在终端确认登录后刷新。'}
    proc = None
    master = slave = None
    selector = None
    capture = None
    try:
        # Per-probe private, temporary metadata; never reuse another account's file.
        capture = tempfile.TemporaryDirectory(prefix='status-', dir=workspace)
        capture_path = Path(capture.name) / 'identity.json'
        master, slave = os.openpty()
        fcntl.ioctl(slave, termios.TIOCSWINSZ, struct.pack('HHHH', 35, 200, 0, 0))
        command = (shlex.quote(sys.executable) + ' ' + shlex.quote(str(Path(__file__).resolve())) +
                   ' --statusline --snapshot ' + shlex.quote(str(capture_path)))
        settings = {'statusLine': {'type': 'command', 'command': command, 'enabled': True}}
        env = dict(os.environ, TERM='xterm-256color', BROWSER='/usr/bin/false')
        proc = subprocess.Popen([str(binary), '--sandbox', '--settings', json.dumps(settings)],
                                cwd=workspace, stdin=slave, stdout=slave, stderr=slave,
                                env=env, start_new_session=True)
        os.close(slave)
        slave = None
        if register:
            register(proc)
        selector = selectors.DefaultSelector()
        selector.register(master, selectors.EVENT_READ)
        output = bytearray()
        deadline = time.monotonic() + timeout
        found = None
        identity_deadline = None
        while time.monotonic() < deadline and not (stopping and stopping()):
            for _, _mask in selector.select(.15):
                try:
                    chunk = os.read(master, 32768)
                except OSError:
                    chunk = b''
                if not chunk:
                    break
                output.extend(chunk)
                if len(output) > 512 * 1024:
                    del output[:-256 * 1024]
                text = output.decode('utf-8', errors='replace')
                identity = parse_identity(text)
                if identity:
                    found = dict(unknown, state='known', detail='已读取官方 CLI 当前会话的账号。', **identity)
                    if identity.get('quota'):
                        return found
                    if identity_deadline is None:
                        identity_deadline = min(deadline, time.monotonic() + 5)
                if 'Gemini API key' in text:
                    return dict(unknown, state='api_key', detail='CLI 使用 Gemini API key，此模式没有登录邮箱。')
                # Do not initiate, complete, or expose any OAuth link through the web console.
                if any(word in text for word in ('https://accounts.google.com/', 'authorization code', 'Sign in with Google')):
                    return dict(unknown, state='signed_out', detail='CLI 需要登录，请在自己的终端完成登录后刷新。')
            try:
                if capture_path.stat().st_size <= 32768:
                    identity = selected_identity(json.loads(capture_path.read_text(encoding='utf-8')))
                    if identity:
                        found = dict(unknown, state='known', detail='已读取官方 CLI 当前会话的账号。', **identity)
                        if identity.get('quota'):
                            return found
                        if identity_deadline is None:
                            identity_deadline = min(deadline, time.monotonic() + 5)
            except (OSError, ValueError):
                pass
            if proc.poll() is not None:
                break
            if identity_deadline is not None and time.monotonic() >= identity_deadline:
                break
        return found or unknown
    except (OSError, ValueError):
        return dict(unknown, detail='无法检查 CLI 账号，请确认已安装并登录官方 agy。')
    finally:
        if selector:
            selector.close()
        if proc:
            if proc.poll() is None:
                try:
                    os.killpg(proc.pid, signal.SIGTERM)
                except PermissionError:
                    proc.terminate()
                except ProcessLookupError:
                    pass
            try:
                proc.wait(timeout=3)
            except subprocess.TimeoutExpired:
                try:
                    os.killpg(proc.pid, signal.SIGKILL)
                except PermissionError:
                    proc.kill()
                except ProcessLookupError:
                    pass
                proc.wait(timeout=3)
            if unregister:
                unregister(proc)
        for descriptor in (master, slave):
            if descriptor is not None:
                os.close(descriptor)
        if capture:
            capture.cleanup()


if __name__ == '__main__':
    if sys.argv[1:] == ['--statusline']:
        statusline()
    elif len(sys.argv) == 4 and sys.argv[1:3] == ['--statusline', '--snapshot']:
        statusline(sys.argv[3])
