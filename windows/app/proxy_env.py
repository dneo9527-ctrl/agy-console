"""Use an enabled loopback macOS HTTP proxy for this console's children only."""
import ipaddress
import os
import re
import socket
import subprocess
import sys


PROXY_KEYS = ('HTTP_PROXY', 'HTTPS_PROXY', 'ALL_PROXY', 'http_proxy', 'https_proxy', 'all_proxy')
LOCAL_BYPASS = ('localhost', '127.0.0.1', '::1')


def system_proxy():
    result = subprocess.run(['/usr/sbin/scutil', '--proxy'], capture_output=True,
                            text=True, timeout=2)
    if result.returncode:
        return {}
    fields = {}
    for line in result.stdout.splitlines():
        match = re.fullmatch(r'\s*(HTTPSEnable|HTTPSProxy|HTTPSPort|HTTPEnable|HTTPProxy|HTTPPort)\s*:\s*(.*?)\s*', line)
        if match:
            fields[match[1]] = match[2]
    return fields


def local_http_proxy(fields, prefix):
    if str(fields.get(prefix + 'Enable')) != '1':
        return None
    host = str(fields.get(prefix + 'Proxy', '')).strip()
    try:
        port = int(fields.get(prefix + 'Port', 0))
        if host.lower() != 'localhost' and not ipaddress.ip_address(host).is_loopback:
            return None
    except (ValueError, TypeError):
        return None
    if not 1 <= port <= 65535:
        return None
    host = host.lower() if host.lower() == 'localhost' else host
    address = '[' + host + ']' if ':' in host else host
    # HTTPSProxy denotes an HTTP CONNECT proxy for HTTPS destinations.
    return host, port, 'http://' + address + ':' + str(port)


def proxy_reachable(host, port):
    try:
        with socket.create_connection((host, port), timeout=.35):
            return True
    except OSError:
        return False


def add_local_bypass(environ):
    # Keep existing exclusions; both common spellings must have the same value.
    values = []
    for raw in (environ.get('NO_PROXY', ''), environ.get('no_proxy', ''), ','.join(LOCAL_BYPASS)):
        for value in raw.split(','):
            value = value.strip()
            if value and value not in values:
                values.append(value)
    environ['NO_PROXY'] = environ['no_proxy'] = ','.join(values)


def configure_proxy(environ=None, reader=None, reachable=None, platform=None):
    """One bounded startup check, no polling, daemon, global settings or secrets."""
    environ = os.environ if environ is None else environ
    platform = sys.platform if platform is None else platform
    if reader is None and platform == 'win32':
        from windows_support import system_proxy as windows_proxy
        reader = windows_proxy
    reader = reader or system_proxy
    reachable = reachable or proxy_reachable
    platform = sys.platform if platform is None else platform
    if any(environ.get(key) for key in PROXY_KEYS):
        add_local_bypass(environ)
        return {'source': 'environment', 'detail': '网络：沿用终端代理配置；本机控制台直连。'}
    if platform not in ('darwin', 'win32'):
        return {'source': 'direct', 'detail': '网络：使用当前网络连接。'}
    try:
        fields = reader()
    except (OSError, ValueError, subprocess.SubprocessError):
        return {'source': 'unavailable', 'detail': '网络：未读到系统代理；使用当前网络连接。'}
    https = local_http_proxy(fields, 'HTTPS')
    http = local_http_proxy(fields, 'HTTP')
    if not (https or http):
        return {'source': 'direct', 'detail': '网络：未发现已启用的本机 HTTP 系统代理。'}
    targets = {item for item in (https, http) if item}
    if not all(reachable(host, port) for host, port, _url in targets):
        return {'source': 'unavailable', 'detail': '网络：本机系统代理端口未就绪；请先打开代理软件，再重开控制台。'}
    http_url, https_url = (http or https)[2], (https or http)[2]
    environ.update(HTTP_PROXY=http_url, http_proxy=http_url, HTTPS_PROXY=https_url, https_proxy=https_url)
    add_local_bypass(environ)
    return {'source': 'system', 'http': http_url, 'https': https_url,
            'detail': '网络：使用系统代理 ' + https_url + '；本机控制台直连。'}
