"""Read-only readiness checks. Never read credentials or make model calls."""
import importlib
import os
from pathlib import Path
import socket
import subprocess
import sys
import tempfile
ROOT=Path(__file__).resolve().parent
sys.path.insert(0,str(ROOT/'app'))
for stream in (sys.stdout,sys.stderr):
    if hasattr(stream,'reconfigure'):stream.reconfigure(encoding='utf-8',errors='replace')
print('Agy Console Windows readiness check\nPython:',sys.version.split()[0],sys.platform)
failed=False
for name in ('psutil','winpty','server','bridge','windows_support'):
    try:importlib.import_module(name);print('[OK]',name)
    except Exception as e:print('[FAIL]',name,type(e).__name__,str(e)[:180]);failed=True
from windows_support import find_agy
binary=find_agy()
if Path(binary).is_file():
    try:
        result=subprocess.run([binary,'--version'],capture_output=True,text=True,timeout=10)
        print('[OK] official CLI',result.stdout.strip() if result.returncode==0 else 'version check failed')
    except OSError as error:print('[FAIL] official CLI:',error);failed=True
else:
    print('[MISSING] Official CLI. Install: https://antigravity.google/download#antigravity-cli');failed=True
try:
    with tempfile.TemporaryDirectory(dir=ROOT):pass
    print('[OK] portable folder is writable')
except OSError:print('[FAIL] unpack to a writable folder, outside Program Files.');failed=True
with socket.socket() as conn:
    if conn.connect_ex(('127.0.0.1',8765))==0:print('[INFO] Port 8765 already in use; no process was stopped.')
    else:print('[OK] port 8765 is free')
print('No sign-in data was read; no model call was made.')
raise SystemExit(1 if failed else 0)
