"""Foreground Windows supervisor: a suspended child enters a kill-on-close job."""
import ctypes
from ctypes import wintypes as W
import os
from pathlib import Path
import subprocess
import sys

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT / 'app'))


def run_owned(command, cwd):
    if os.name != 'nt':
        raise RuntimeError('This launcher requires Windows 10/11 x64.')
    kernel = ctypes.WinDLL('kernel32', use_last_error=True)
    SIZE = ctypes.c_size_t
    class BASIC(ctypes.Structure):
        _fields_ = [('ProcessTime',ctypes.c_int64),('JobTime',ctypes.c_int64),('Flags',W.DWORD),
                    ('Minimum',SIZE),('Maximum',SIZE),('Active',W.DWORD),('Affinity',SIZE),
                    ('Priority',W.DWORD),('Scheduling',W.DWORD)]
    class IO(ctypes.Structure):
        _fields_ = [(name,ctypes.c_uint64) for name in ('ReadOp','WriteOp','OtherOp','ReadBytes','WriteBytes','OtherBytes')]
    class LIMIT(ctypes.Structure):
        _fields_ = [('Basic',BASIC),('Io',IO),('ProcessMemory',SIZE),('JobMemory',SIZE),('PeakProcess',SIZE),('PeakJob',SIZE)]
    class STARTUP(ctypes.Structure):
        _fields_ = [('cb',W.DWORD),('reserved',W.LPWSTR),('desktop',W.LPWSTR),('title',W.LPWSTR),
                    ('x',W.DWORD),('y',W.DWORD),('width',W.DWORD),('height',W.DWORD),
                    ('charsX',W.DWORD),('charsY',W.DWORD),('fill',W.DWORD),('flags',W.DWORD),
                    ('show',W.WORD),('reservedBytes',W.WORD),('reservedPtr',ctypes.POINTER(ctypes.c_byte)),
                    ('stdin',W.HANDLE),('stdout',W.HANDLE),('stderr',W.HANDLE)]
    class STARTUPEX(ctypes.Structure):
        _fields_ = [('Startup',STARTUP),('Attributes',ctypes.c_void_p)]
    class PROCESS(ctypes.Structure):
        _fields_ = [('process',W.HANDLE),('thread',W.HANDLE),('pid',W.DWORD),('tid',W.DWORD)]
    kernel.CreateJobObjectW.argtypes=[ctypes.c_void_p,W.LPCWSTR];kernel.CreateJobObjectW.restype=W.HANDLE
    kernel.SetInformationJobObject.argtypes=[W.HANDLE,ctypes.c_int,ctypes.c_void_p,W.DWORD]
    kernel.AssignProcessToJobObject.argtypes=[W.HANDLE,W.HANDLE]
    kernel.CreateProcessW.argtypes=[W.LPCWSTR,W.LPWSTR,ctypes.c_void_p,ctypes.c_void_p,W.BOOL,W.DWORD,ctypes.c_void_p,W.LPCWSTR,ctypes.c_void_p,ctypes.POINTER(PROCESS)]
    kernel.InitializeProcThreadAttributeList.argtypes=[ctypes.c_void_p,W.DWORD,W.DWORD,ctypes.POINTER(SIZE)]
    kernel.UpdateProcThreadAttribute.argtypes=[ctypes.c_void_p,W.DWORD,SIZE,ctypes.c_void_p,SIZE,ctypes.c_void_p,ctypes.c_void_p]
    kernel.DeleteProcThreadAttributeList.argtypes=[ctypes.c_void_p]
    kernel.ResumeThread.argtypes=[W.HANDLE];kernel.ResumeThread.restype=W.DWORD
    kernel.WaitForSingleObject.argtypes=[W.HANDLE,W.DWORD];kernel.WaitForSingleObject.restype=W.DWORD
    kernel.GetExitCodeProcess.argtypes=[W.HANDLE,ctypes.POINTER(W.DWORD)]
    kernel.CloseHandle.argtypes=[W.HANDLE]
    kernel.TerminateJobObject.argtypes=[W.HANDLE,W.UINT]
    kernel.TerminateProcess.argtypes=[W.HANDLE,W.UINT]
    kernel.GenerateConsoleCtrlEvent.argtypes=[W.DWORD,W.DWORD]
    job=kernel.CreateJobObjectW(None,None)
    if not job:
        raise ctypes.WinError(ctypes.get_last_error())
    child=PROCESS();started=False;attributes=None;attributes_initialized=False
    try:
        limit=LIMIT();limit.Basic.Flags=0x2000  # JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
        if not kernel.SetInformationJobObject(job,9,ctypes.byref(limit),ctypes.sizeof(limit)):
            raise ctypes.WinError(ctypes.get_last_error())
        # Atomically assign the new process to this job at creation. This avoids
        # even a suspended orphan if the supervisor is killed before assignment.
        size=SIZE()
        kernel.InitializeProcThreadAttributeList(None,1,0,ctypes.byref(size))
        attributes=ctypes.create_string_buffer(size.value)
        if not kernel.InitializeProcThreadAttributeList(attributes,1,0,ctypes.byref(size)):
            raise ctypes.WinError(ctypes.get_last_error())
        attributes_initialized=True
        jobs=(W.HANDLE*1)(job)
        if not kernel.UpdateProcThreadAttribute(attributes,0,0x2000D,ctypes.byref(jobs),ctypes.sizeof(jobs),None,None):
            raise ctypes.WinError(ctypes.get_last_error())
        startup=STARTUPEX();startup.Startup.cb=ctypes.sizeof(startup)
        startup.Attributes=ctypes.cast(attributes,ctypes.c_void_p)
        flags=0x4|0x200|0x80000  # SUSPENDED, NEW_PROCESS_GROUP, EXTENDED_STARTUPINFO
        line=ctypes.create_unicode_buffer(subprocess.list2cmdline(command))
        if not kernel.CreateProcessW(None,line,None,None,False,flags,None,str(cwd),ctypes.byref(startup),ctypes.byref(child)):
            raise ctypes.WinError(ctypes.get_last_error())
        started=True
        if kernel.ResumeThread(child.thread)==0xffffffff:
            raise ctypes.WinError(ctypes.get_last_error())
        kernel.CloseHandle(child.thread);child.thread=None
        # Closing the console kills this supervisor; closing its only job handle
        # then kills the service and every descendant, even after forced exit.
        try:
            while kernel.WaitForSingleObject(child.process,200)==0x102:
                pass
        except KeyboardInterrupt:
            kernel.GenerateConsoleCtrlEvent(1,child.pid)  # CTRL_BREAK to owned group
            if kernel.WaitForSingleObject(child.process,7000)==0x102:
                kernel.TerminateJobObject(job,130)
        code=W.DWORD();kernel.GetExitCodeProcess(child.process,ctypes.byref(code))
        return code.value
    finally:
        if attributes_initialized:kernel.DeleteProcThreadAttributeList(attributes)
        kernel.CloseHandle(job)
        if started:
            if child.thread:kernel.CloseHandle(child.thread)
            if child.process:kernel.CloseHandle(child.process)


def main():
    for stream in (sys.stdout,sys.stderr):
        if hasattr(stream,'reconfigure'):stream.reconfigure(encoding='utf-8',errors='replace')
    from windows_support import console_attached, find_agy
    if not console_attached():
        print('Please double-click Start-Agy.cmd in an interactive terminal.');return 2
    agy=find_agy()
    if not Path(agy).is_file():
        print('Official agy.exe was not found.\nInstall from https://antigravity.google/download#antigravity-cli\nThen run Login-Agy.cmd once.\nCustom location: set AGY_BINARY to the full agy.exe path.')
        return 2
    if '--login' in sys.argv:
        return run_owned([agy],ROOT)
    return run_owned([sys.executable,'-u',str(ROOT/'update_bootstrap.py'),'--app-root',str(ROOT/'app'),'--data',str(ROOT/'data'),
                      '--agy-bin',agy]+sys.argv[1:],ROOT)


if __name__=='__main__':
    try:
        raise SystemExit(main())
    except OSError as error:
        print('Could not start safely:',error)
        raise SystemExit(1)
