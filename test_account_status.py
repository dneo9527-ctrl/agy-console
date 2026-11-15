"""Account identity tests using fake CLI processes; no credentials or model calls."""
import http.client
import json
import os
from pathlib import Path
import signal
import subprocess
import sys
import tempfile
import threading
import time
import unittest

from account_status import MARKER, SOURCE, parse_identity, probe_account, selected_identity, selected_quota
from server import BUSY, Console, DEFAULT_MODEL, Handler, ThreadingHTTPServer


PRIVATE_SENTINEL = 'fake-secret-must-not-be-forwarded'
FAKE_CLI = '''#!/usr/bin/python3
import json, os, shlex, signal, subprocess, sys, time
from pathlib import Path
args = sys.argv[1:]
if '-p' in args:
    prompt = args[args.index('-p') + 1]
    print(json.dumps({'event':'init','conversation_id':'account-test-session'}), flush=True)
    print(json.dumps({'event':'result','result':{'status':'SUCCESS','response':'reply:' + prompt}}), flush=True)
    sys.exit(0)
assert '--settings' in args and '--sandbox' in args
assert '--print' not in args and '-p' not in args
assert os.isatty(0) and os.isatty(1)
assert os.environ.get('BROWSER') == '/usr/bin/false'
Path('probe-arguments.json').write_text(json.dumps(args))
mode_path = Path('mode.txt')
mode = mode_path.read_text().strip() if mode_path.exists() else 'known'
if mode in ('known', 'quota', 'delayed_quota'):
    settings = json.loads(args[args.index('--settings')+1])
    assert settings['statusLine']['enabled'] is True
    payload = {'email':'first@example.test','plan_tier':'Pro',
               'access_token':'fake-secret-must-not-be-forwarded',
               'transcript_path':'private/transcript', 'credentials':{'secret':'private'}}
    helper = subprocess.run(shlex.split(settings['statusLine']['command']),
                            input=json.dumps(payload), capture_output=True, text=True)
    assert helper.returncode == 0
    Path('helper-output.txt').write_text(helper.stdout)
    raw = ('\\x1b[32m' + helper.stdout.strip() + '\\x1b[0m\\r\\n').encode()
    for index in range(0, len(raw), 5):
        os.write(1, raw[index:index+5])
    if mode in ('quota', 'delayed_quota'):
        if mode == 'delayed_quota':
            time.sleep(.2)
            payload['email'] = 'second@example.test'
        payload['quota'] = {'gemini-weekly': {'remaining_fraction':.926,
                            'reset_time':'2026-11-18T10:19:39Z', 'reset_in_seconds':600,
                            'access_token':'fake-secret-must-not-be-forwarded'}}
        helper = subprocess.run(shlex.split(settings['statusLine']['command']),
                                input=json.dumps(payload), capture_output=True, text=True)
        print(helper.stdout, flush=True)
elif mode == 'unknown':
    print('No identity header available.', flush=True)
    sys.exit(0)
elif mode == 'api_key':
    print('Gemini API key', flush=True)
elif mode == 'signed_out':
    print('Sign in with Google: https://accounts.google.com/fake-oauth', flush=True)
elif mode == 'timeout':
    print('initializing', flush=True)
time.sleep(30)
'''


def account(email='first@example.test'):
    return {'state':'known', 'email':email, 'name':None, 'plan_tier':'Pro',
            'source':SOURCE, 'checked_at':'2026-11-11T13:00:00+00:00',
            'detail':'Test identity from official-style payload.'}


class IdentitySelectionTests(unittest.TestCase):
    def test_only_email_and_tier_leave_statusline_helper(self):
        payload = {'email':'first@example.test', 'plan_tier':'Pro',
                   'access_token':PRIVATE_SENTINEL, 'name':'private name',
                   'credentials':{'refresh_token':PRIVATE_SENTINEL}}
        self.assertEqual(selected_identity(payload), {'email':'first@example.test','plan_tier':'Pro'})
        helper = subprocess.run([sys.executable, str(Path(__file__).with_name('account_status.py')), '--statusline'],
                                input=json.dumps(payload), capture_output=True, text=True, timeout=3)
        self.assertEqual(helper.returncode, 0)
        self.assertIn(MARKER, helper.stdout)
        self.assertNotIn(PRIVATE_SENTINEL, helper.stdout + helper.stderr)
        self.assertNotIn('credentials', helper.stdout)

    def test_invalid_or_missing_email_never_becomes_known(self):
        for payload in (None, [], {}, {'email':'invalid'}, {'email':'bad\n@example.test'},
                        {'email':'<first@example.test>'}, {'email':15}):
            with self.subTest(payload=payload):
                self.assertIsNone(selected_identity(payload))

    def test_ansi_and_fragmented_transport_preserve_structured_identity(self):
        text = ('\x1b]0;private terminal title\x07\x1b[32m' + MARKER +
                '{"email":"first@example.test","plan_tier":"Pro","token":"' + PRIVATE_SENTINEL + '"}\x1b[0m')
        pieces = [text[i:i+3] for i in range(0, len(text), 3)]
        result = parse_identity(''.join(pieces))
        self.assertEqual(result, {'email':'first@example.test','plan_tier':'Pro'})
        self.assertNotIn(PRIVATE_SENTINEL, json.dumps(result))
        self.assertIsNone(parse_identity('unrelated header user@example.test'))

    def test_quota_validation_keeps_zero_and_filters_unrelated_or_invalid_fields(self):
        quota = selected_quota({
            'gemini-weekly': {'remaining_fraction':0, 'remaining_amount':12.5,
                             'reset_in_seconds':0, 'reset_time':'2026-11-18T10:19:39Z',
                             'disabled':False, 'access_token':PRIVATE_SENTINEL},
            'invalid': {'remaining_fraction':True, 'remaining_amount':float('inf'),
                        'reset_in_seconds':-1, 'reset_time':'2026-11-18T10:19:39', 'disabled':'false'},
            'nan': {'remaining_fraction':float('nan')},
            'out-of-range': {'remaining_fraction':1.01},
            'bad\nname': {'remaining_fraction':1},
            'disabled': {'disabled':True},
        })
        self.assertEqual(quota, {
            'gemini-weekly': {'remaining_fraction':0, 'remaining_amount':12.5,
                             'reset_in_seconds':0, 'reset_time':'2026-11-18T10:19:39Z', 'disabled':False},
            'disabled': {'disabled':True},
        })
        self.assertEqual(selected_quota(None), {})
        self.assertEqual(selected_quota([]), {})

    def test_private_snapshot_preserves_quota_and_short_terminal_output_stays_compatible(self):
        payload = {'email':'first@example.test', 'quota':{
            'gemini-5h':{'remaining_fraction':1, 'credentials':PRIVATE_SENTINEL}},
            'access_token':PRIVATE_SENTINEL}
        with tempfile.TemporaryDirectory() as folder:
            target = Path(folder) / 'identity.json'
            helper = subprocess.run([sys.executable, str(Path(__file__).with_name('account_status.py')),
                                     '--statusline', '--snapshot', str(target)],
                                    input=json.dumps(payload), capture_output=True, text=True, timeout=3)
            saved = json.loads(target.read_text())
            self.assertEqual(saved['quota'], {'gemini-5h':{'remaining_fraction':1}})
            self.assertNotIn(PRIVATE_SENTINEL, target.read_text())
            self.assertEqual(target.stat().st_mode & 0o777, 0o600)
        self.assertNotIn(PRIVATE_SENTINEL, helper.stdout)
        wire = helper.stdout.split(MARKER)[1].strip()
        self.assertEqual(wire.count('{'), 1)
        result = parse_identity(helper.stdout)
        self.assertEqual(result, {'email':'first@example.test','plan_tier':None})

    def test_latest_complete_payload_replaces_earlier_account_and_quota(self):
        first = MARKER + json.dumps({'email':'first@example.test', 'quota':{'gemini-5h':{'remaining_fraction':.7}}})
        second = MARKER + json.dumps({'email':'second@example.test', 'quota':{}})
        result = parse_identity(first + '\n' + second + '\n' + MARKER + '{"email":')
        self.assertEqual(result['email'], 'second@example.test')
        self.assertEqual(result['quota'], {})
        result = parse_identity(MARKER + json.dumps({'email':'first@example.test', 'quota_b64':'ignored'}))
        self.assertEqual(result, {'email':'first@example.test','plan_tier':None})


class AccountProbeTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.binary = self.root / 'fake-agy'
        self.binary.write_text(FAKE_CLI)
        self.binary.chmod(0o700)
        self.registered, self.unregistered = [], []

    def tearDown(self):
        for proc in self.registered:
            if proc.poll() is None:
                os.killpg(proc.pid, signal.SIGKILL)
                proc.wait(timeout=3)
        self.temp.cleanup()

    def probe(self, mode, **options):
        (self.root / 'mode.txt').write_text(mode)
        result = probe_account(self.binary, self.root,
                               register=self.registered.append,
                               unregister=self.unregistered.append, **options)
        self.assertEqual(self.registered, self.unregistered)
        self.assertTrue(all(proc.poll() is not None for proc in self.registered))
        self.assertNotIn(PRIVATE_SENTINEL, json.dumps(result))
        return result

    def test_real_probe_protocol_uses_session_only_settings_and_reaps_cli(self):
        result = self.probe('known', timeout=3)
        self.assertEqual((result['state'], result['email']), ('known', 'first@example.test'))
        self.assertEqual(result['plan_tier'], 'Pro')
        self.assertEqual(result['source'], SOURCE)
        self.assertTrue(result['checked_at'])
        args = json.loads((self.root / 'probe-arguments.json').read_text())
        self.assertIn('--settings', args)
        self.assertNotIn('-p', args)
        self.assertNotIn(PRIVATE_SENTINEL, (self.root / 'helper-output.txt').read_text())
        self.assertFalse((self.root / 'settings.json').exists())

    def test_unavailable_account_clears_identity(self):
        result = self.probe('unknown', timeout=1)
        self.assertEqual(result['state'], 'unknown')
        self.assertIsNone(result['email'])

    def test_api_key_and_login_required_are_distinct_no_email_states(self):
        for mode in ('api_key', 'signed_out'):
            with self.subTest(mode=mode):
                result = self.probe(mode, timeout=2)
                self.assertEqual(result['state'], mode)
                self.assertIsNone(result['email'])
                self.assertNotIn('https://accounts.google.com', json.dumps(result))

    def test_timeout_and_stopping_reap_owned_cli(self):
        started = time.monotonic()
        result = self.probe('timeout', timeout=.25)
        self.assertEqual(result['state'], 'unknown')
        self.assertLess(time.monotonic() - started, 4)
        result = self.probe('timeout', timeout=20, stopping=lambda:bool(self.registered))
        self.assertEqual(result['state'], 'unknown')
        self.assertTrue(all(proc.returncode is not None for proc in self.registered))

    def test_quota_can_arrive_after_initial_identity_without_leaking_old_account(self):
        for mode, email in (('quota','first@example.test'), ('delayed_quota','second@example.test')):
            with self.subTest(mode=mode):
                result = self.probe(mode, timeout=3)
                self.assertEqual(result['email'], email)
                self.assertEqual(result['quota']['gemini-weekly']['remaining_fraction'], .926)


class AccountConsoleTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.binary = self.root / 'fake-agy'
        self.binary.write_text(FAKE_CLI)
        self.binary.chmod(0o700)
        self.current = account()
        self.app = Console(self.root / 'data', self.binary, import_history=False,
                           account_reader=lambda *_args, **_kwargs:dict(self.current))
        self.conv = self.app.create()

    def tearDown(self):
        self.app.close()
        self.temp.cleanup()

    def turn(self, prompt):
        turn = self.app.submit(self.conv['id'], prompt, DEFAULT_MODEL)
        deadline = time.monotonic() + 4
        while turn['status'] in BUSY and time.monotonic() < deadline:
            time.sleep(.02)
        self.assertEqual(turn['status'], 'completed', turn.get('error'))
        return turn

    def test_changed_accounts_are_recorded_per_task_and_persisted(self):
        first = self.turn('first')
        self.current = account('second@example.test')
        second = self.turn('second')
        self.assertEqual(first['account']['email'], 'first@example.test')
        self.assertEqual(second['account']['email'], 'second@example.test')
        self.assertEqual(self.app.snapshot()['health']['account']['email'], 'second@example.test')
        persisted = json.loads(self.app.file.read_text())['conversations'][0]['turns']
        self.assertEqual([turn['account']['email'] for turn in persisted],
                         ['first@example.test','second@example.test'])
        self.assertNotIn(PRIVATE_SENTINEL, self.app.file.read_text())

    def test_failed_recheck_does_not_preserve_previous_known_email(self):
        self.assertEqual(self.app.check_account()['email'], 'first@example.test')
        self.current = {'state':'unknown','email':None,'source':SOURCE,
                        'checked_at':'2026-11-11T14:00:00+00:00','detail':'No account found.'}
        unknown = self.app.check_account()
        self.assertEqual(unknown['state'], 'unknown')
        self.assertIsNone(unknown['email'])
        turn = self.turn('account unknown still runs')
        self.assertNotIn('account', turn)
        self.assertEqual(turn['account_check']['state'], 'unknown')

    def test_account_change_and_failed_check_do_not_reuse_quota(self):
        self.current['quota'] = {'gemini-5h':{'remaining_fraction':.4}}
        self.assertEqual(self.app.check_account()['quota']['gemini-5h']['remaining_fraction'], .4)
        self.current = account('second@example.test')
        self.assertFalse(self.app.check_account().get('quota'))
        self.current = {'state':'unknown','email':None,'quota':{},'checked_at':None}
        self.assertFalse(self.app.check_account().get('quota'))
        self.assertTrue(self.app.service_info()['features']['quota'])

    def test_refresh_http_requires_same_origin_and_console_token(self):
        httpd = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
        httpd.app = self.app
        thread = threading.Thread(target=httpd.serve_forever, daemon=True)
        thread.start()
        def refresh(headers):
            client = http.client.HTTPConnection('127.0.0.1', httpd.server_port, timeout=3)
            try:
                client.request('POST', '/api/account/refresh', '{}', headers)
                response = client.getresponse()
                status = response.status
                response.read()
                return status
            finally:
                client.close()
        try:
            self.assertEqual(refresh({'Content-Type':'application/json'}), 403)
            headers = {'Content-Type':'application/json','X-Console-Token':self.app.token}
            self.assertEqual(refresh(dict(headers, Origin='https://untrusted.example')), 403)
            self.current = account('refreshed@example.test')
            self.assertEqual(refresh(headers), 202)
            self.app.account_thread.join(timeout=3)
            self.assertEqual(self.app.health['account']['email'], 'refreshed@example.test')
        finally:
            httpd.shutdown()
            httpd.server_close()
            thread.join(timeout=3)


if __name__ == '__main__':
    unittest.main(verbosity=2)
