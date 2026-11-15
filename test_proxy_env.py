"""Routing setup and direct loopback control; no real model or credentials."""
import json
import os
import subprocess
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from unittest.mock import patch

from bridge import Client
from proxy_env import configure_proxy, local_http_proxy, system_proxy

SYSTEM = {'HTTPEnable': '1', 'HTTPProxy': '127.0.0.1', 'HTTPPort': '7897',
          'HTTPSEnable': '1', 'HTTPSProxy': '127.0.0.1', 'HTTPSPort': '7897'}


class ProxyEnvironmentTests(unittest.TestCase):
    def configure(self, env=None, fields=None, reachable=None):
        env = {} if env is None else env
        info = configure_proxy(env, reader=lambda: SYSTEM if fields is None else fields,
                               reachable=reachable or (lambda *_: True), platform='darwin')
        return env, info

    def test_enabled_system_proxy_and_local_exclusions(self):
        env, info = self.configure({'EXISTING_OPTION': 'keep'})
        self.assertEqual(info['source'], 'system')
        for key in ('HTTP_PROXY', 'HTTPS_PROXY', 'http_proxy', 'https_proxy'):
            self.assertEqual(env[key], 'http://127.0.0.1:7897')
        self.assertEqual(env['EXISTING_OPTION'], 'keep')
        self.assertEqual(set(env['NO_PROXY'].split(',')), {'localhost', '127.0.0.1', '::1'})
        self.assertEqual(env['NO_PROXY'], env['no_proxy'])

    def test_http_connect_for_https_ipv6_and_single_endpoint_fallback(self):
        env, _ = self.configure(fields=dict(SYSTEM, HTTPSProxy='::1', HTTPSPort='7898'))
        self.assertEqual(env['HTTPS_PROXY'], 'http://[::1]:7898')
        self.assertEqual(env['HTTP_PROXY'], 'http://127.0.0.1:7897')
        for prefix in ('HTTP', 'HTTPS'):
            one = {k: v for k, v in SYSTEM.items() if k in (prefix+'Enable', prefix+'Proxy', prefix+'Port')}
            env, info = self.configure(fields=one)
            self.assertEqual(info['source'], 'system')
            self.assertEqual(env['HTTP_PROXY'], env['HTTPS_PROXY'])

    def test_disabled_remote_pac_and_socks_only_do_not_reroute(self):
        for fields in ({}, dict(SYSTEM, HTTPEnable='0', HTTPSEnable='0'),
                       dict(SYSTEM, HTTPProxy='proxy.example.com', HTTPSProxy='proxy.example.com'),
                       {'ProxyAutoConfigEnable': '1', 'ProxyAutoConfigURLString': 'https://example.com/proxy.pac'},
                       {'SOCKSEnable': '1', 'SOCKSProxy': '127.0.0.1', 'SOCKSPort': '7897'}):
            env, info = self.configure(fields=fields)
            self.assertEqual(info['source'], 'direct')
            self.assertEqual(env, {})

    def test_dead_proxy_leaves_no_dead_environment(self):
        env, info = self.configure(reachable=lambda *_: False)
        self.assertEqual(info['source'], 'unavailable')
        self.assertEqual(env, {})

    def test_explicit_proxy_precedes_system_and_is_not_logged(self):
        for key in ('HTTPS_PROXY', 'https_proxy', 'ALL_PROXY'):
            value = 'http://example-user:example-password@127.0.0.1:9000'
            env = {key: value, 'NO_PROXY': 'example.org,localhost', 'no_proxy': 'internal.test'}
            info = configure_proxy(env, reader=lambda: self.fail('must preserve explicit proxy'), platform='darwin')
            self.assertEqual(env[key], value)
            self.assertEqual(info['source'], 'environment')
            self.assertNotIn('example-password', json.dumps(info))
            self.assertEqual(set(env['NO_PROXY'].split(',')), {'example.org', 'localhost', 'internal.test', '127.0.0.1', '::1'})

    def test_invalid_endpoints_never_become_environment(self):
        for host, port in [('127.0.0.1', 0), ('127.0.0.1', 65536), ('localhost', 'oops'),
                           ('127.0.0.1; touch /tmp/no', 7897), ('http://localhost', 7897), ('192.168.1.2', 7897)]:
            self.assertIsNone(local_http_proxy({'HTTPEnable': '1', 'HTTPProxy': host, 'HTTPPort': port}, 'HTTP'))

    def test_system_reader_is_bounded_and_only_selects_route_fields(self):
        sample = '<dictionary> {\n HTTPEnable : 1\n HTTPProxy : 127.0.0.1\n HTTPPort : 7897\n ExceptionsList : <array> {\n 0 : localhost\n }\n}\n'
        result = subprocess.CompletedProcess([], 0, stdout=sample, stderr='')
        with patch('proxy_env.subprocess.run', return_value=result) as run:
            self.assertEqual(system_proxy(), {'HTTPEnable': '1', 'HTTPProxy': '127.0.0.1', 'HTTPPort': '7897'})
            self.assertEqual(run.call_args.args[0], ['/usr/sbin/scutil', '--proxy'])
            self.assertEqual(run.call_args.kwargs['timeout'], 2)

    def test_missing_reader_or_non_mac_keeps_startup_available(self):
        for error in (OSError('missing'), subprocess.TimeoutExpired('scutil', 2)):
            env = {}
            with patch('proxy_env.system_proxy', side_effect=error):
                self.assertEqual(configure_proxy(env, platform='darwin')['source'], 'unavailable')
            self.assertEqual(env, {})
        self.assertEqual(configure_proxy({}, reader=lambda: self.fail('not macOS'), platform='linux')['source'], 'direct')

    def test_local_dispatch_bypasses_proxy_without_no_proxy(self):
        received = []
        class Handler(BaseHTTPRequestHandler):
            def do_GET(self):
                body = b'<meta name="console-token" content="test-local-token">' if self.path == '/' else b'{"ok":true}'
                self.send_response(200); self.end_headers(); self.wfile.write(body)
            def do_POST(self):
                received.append(self.headers.get('X-Console-Token'))
                self.rfile.read(int(self.headers.get('Content-Length', '0')))
                self.send_response(200); self.end_headers(); self.wfile.write(b'{"ok":true}')
            def log_message(self, *_): pass
        httpd = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
        worker = threading.Thread(target=httpd.serve_forever, daemon=True); worker.start()
        try:
            env = {k: 'http://127.0.0.1:1' for k in ('HTTP_PROXY', 'HTTPS_PROXY', 'http_proxy', 'https_proxy', 'ALL_PROXY', 'all_proxy')}
            env.update(NO_PROXY='', no_proxy='')
            with patch.dict(os.environ, env):
                for host in ('127.0.0.1', 'localhost'):
                    client = Client('http://'+host+':'+str(httpd.server_port), timeout=2)
                    self.assertEqual(client.request('/api/check'), {'ok': True})
                    self.assertEqual(client.request('/api/check', {'hello': 'local'}), {'ok': True})
            self.assertEqual(received, ['test-local-token', 'test-local-token'])
        finally:
            httpd.shutdown(); httpd.server_close(); worker.join(3)


if __name__ == '__main__': unittest.main()
