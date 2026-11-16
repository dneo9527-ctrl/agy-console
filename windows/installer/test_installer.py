"""Native CI lifecycle test of the real installer. No account/model operations."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time


def run(command, **kwargs):
    return subprocess.run([str(x) for x in command], capture_output=True, text=True,
                          encoding='utf-8', errors='replace', timeout=90, **kwargs)


def require(condition, message):
    if not condition:
        raise AssertionError(message)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--setup', type=Path, required=True)
    parser.add_argument('--baseline-setup', type=Path)
    parser.add_argument('--baseline-sha256')
    parser.add_argument('--report', type=Path, required=True)
    args = parser.parse_args()
    require(os.name == 'nt', 'Native Windows required; never count a skipped test as passed')
    setup = args.setup.resolve()
    require(bool(args.baseline_setup) == bool(args.baseline_sha256), 'Baseline file and digest must be supplied together')
    if args.baseline_setup:
        require(hashlib.sha256(args.baseline_setup.read_bytes()).hexdigest() == args.baseline_sha256,
                'Public baseline installer digest mismatch')
    report = {'status': 'running', 'checks': [], 'official_login_or_model_call': False}
    args.report.parent.mkdir(parents=True, exist_ok=True)

    def passed(name, **details):
        report['checks'].append(dict(name=name, status='passed', **details))
        print('PASS', name, flush=True)

    data = Path(os.environ['LOCALAPPDATA']) / 'AgyConsole' / 'data'
    # This script is intended solely for disposable CI hosts, never a user's PC.
    require(os.environ.get('GITHUB_ACTIONS') == 'true', 'Run lifecycle fixtures on a disposable GitHub Windows runner')
    require(not data.exists(), 'Refuse to touch an existing data directory')
    data.mkdir(parents=True)
    data_sentinel = data / 'ci-preserved-data.json'
    data_bytes = b'{"fixture":"nonsecret retained history"}\n'
    data_sentinel.write_bytes(data_bytes)
    with tempfile.TemporaryDirectory(prefix='Agy CI path with spaces ') as temp:
        root = Path(temp).resolve()  # Normalize RUNNER~1/8.3 aliases before comparing.
        installed = root / 'Program Files With Spaces' / 'AgyConsole'
        official_fixture = root / 'Independent Official Login Fixture'
        official_fixture.mkdir()
        official_marker = official_fixture / 'nonsecret-marker.txt'
        official_marker.write_text('not a credential', encoding='utf-8')
        prefix = [setup, '/VERYSILENT', '/SUPPRESSMSGBOXES', '/SP-', '/NORESTART', '/NOCLOSEAPPLICATIONS', '/NORESTARTAPPLICATIONS', '/DIR=' + str(installed)]
        if args.baseline_setup:
            proc = run([args.baseline_setup.resolve()] + prefix[1:] + ['/LOG=' + str(root / 'baseline-install.log')])
            require(proc.returncode == 0, 'Public .7 baseline install failed')
            require((installed / 'app/VERSION').read_text().strip() == '0.1.0-preview.7', 'Unexpected public baseline version')
            require(data_sentinel.read_bytes() == data_bytes, 'Baseline install changed external fixture data')
            passed('public_7_baseline_installed', sha256=args.baseline_sha256)
        proc = run(prefix + ['/LOG=' + str(root / 'install.log')])
        require(proc.returncode == 0, 'Install failed: ' + str(proc.returncode) + proc.stderr)
        python = installed / 'runtime/python.exe'
        require(python.is_file() and (installed / 'installed-mode.json').is_file(), 'Missing installed runtime/layout')
        passed('real_exe_install_path_with_spaces')
        if args.baseline_setup:
            require((installed / 'app/VERSION').read_text().strip() == '0.1.0-preview.8', 'Public .7 to .8 upgrade version mismatch')
            require(data_sentinel.read_bytes() == data_bytes and official_marker.read_text() == 'not a credential',
                    'Public baseline upgrade changed external fixture data')
            passed('public_7_to_8_installer_upgrade_preserves_data')
        for name in ('Start-Agy.cmd', 'Login-Agy.cmd', 'Install-Official-CLI.url', 'Open-Panel.url', 'Getting-Started.html', 'installation.py'):
            require((installed / name).is_file(), 'Missing first-use entry: ' + name)
        require('http://127.0.0.1:8765/' in (installed / 'Open-Panel.url').read_text(), 'Wrong panel shortcut URL')
        require('https://antigravity.google/' in (installed / 'Install-Official-CLI.url').read_text(), 'Wrong official CLI URL')
        passed('first_use_entries_and_public_urls')

        code = 'import installation,json;from pathlib import Path;print(json.dumps({"data":str(installation.data_directory(Path.cwd())),"portable":str(installation.data_directory(Path.cwd()/"portable-fixture")),"override":str(installation.data_directory(Path.cwd(),str(Path.cwd()/"explicit-data")))}))'
        proc = run([python, '-B', '-c', code], cwd=installed)
        require(proc.returncode == 0, 'Installed Python import failed: ' + proc.stderr)
        layout = json.loads(proc.stdout)
        require(Path(layout['data']) == data, 'Installed data must be outside program directory')
        require(Path(layout['portable']) == installed / 'portable-fixture/data', 'Portable layout changed')
        require(Path(layout['override']).resolve() == (installed / 'explicit-data').resolve(),
                'Explicit data argument ignored: ' + repr(layout['override']))
        passed('installed_portable_and_explicit_data_layout')

        proc = run([python, '-B', installed / 'self_test.py'], cwd=installed)
        require(proc.returncode == 0, 'Bundled-runtime self test failed: ' + proc.stderr)
        require('skipped' not in proc.stderr.lower(), 'Native test was skipped')
        passed('bundled_runtime_native_imports_pipes_conpty_and_job_cleanup', test_output=proc.stderr)

        # Shell-known folder paths, rather than assumptions about Desktop redirection.
        shortcuts_script = r'''$shell = New-Object -ComObject WScript.Shell
$desktop = [Environment]::GetFolderPath('Desktop')
$programs = [Environment]::GetFolderPath('Programs')
$files = @((Join-Path $desktop 'Agy Console.lnk'), (Join-Path $programs 'Agy Console\Agy Console.lnk'), (Join-Path $programs 'Agy Console\Official CLI Login.lnk'), (Join-Path $programs 'Agy Console\Open Panel.lnk'))
$rows = foreach ($file in $files) { if (!(Test-Path -LiteralPath $file)) { throw "Missing shortcut" }; $s = $shell.CreateShortcut($file); @{ target=$s.TargetPath; arguments=$s.Arguments; cwd=$s.WorkingDirectory } }
ConvertTo-Json -InputObject @($rows) -Compress'''
        proc = run(['powershell.exe', '-NoProfile', '-NonInteractive', '-Command', shortcuts_script])
        require(proc.returncode == 0, 'Shortcut inspection failed: ' + proc.stderr)
        shortcuts = json.loads(proc.stdout)
        expected = ['Start-Agy.cmd', 'Start-Agy.cmd', 'Login-Agy.cmd', 'Open-Panel.url']
        for row, name in zip(shortcuts, expected):
            require(Path(row['target']) == installed / name and Path(row['cwd']) == installed, 'Incorrect shortcut quoting/working directory')
            require(not row['arguments'], 'Unexpected shortcut arguments')
        passed('desktop_and_start_menu_shortcut_targets_working_directory')

        # Simulate a previous installed version, then invoke the same real installer.
        # This tests actual overwrite and preserved data, not two public releases.
        version = installed / 'app/VERSION'
        new_version = version.read_bytes()
        version.write_text('0.1.0-preview.6\n', encoding='utf-8')
        proc = run(prefix + ['/LOG=' + str(root / 'upgrade.log')])
        require(proc.returncode == 0 and version.read_bytes() == new_version, 'Upgrade did not replace program version')
        require(data_sentinel.read_bytes() == data_bytes and official_marker.read_text() == 'not a credential', 'Upgrade changed external fixture data')
        passed('real_reinstall_upgrade_replaces_program_preserves_data', older_version='simulated installed .6 marker')

        guard_ready = root / 'guard-ready'
        fixture = root / 'guard_fixture.py'
        fixture.write_text('import sys,time\nfrom pathlib import Path\nsys.path.insert(0,sys.argv[1])\nfrom installation import ForegroundGuard\nwith ForegroundGuard():\n Path(sys.argv[2]).write_text("ready")\n time.sleep(90)\n', encoding='utf-8')
        guard = subprocess.Popen([str(python), '-B', str(fixture), str(installed), str(guard_ready)], cwd=installed)
        uninstaller = installed / 'unins000.exe'
        try:
            deadline = time.monotonic() + 10
            while not guard_ready.exists() and guard.poll() is None and time.monotonic() < deadline:
                time.sleep(.05)
            require(guard_ready.exists(), 'Foreground lifetime mutex fixture did not start')
            before = hashlib.sha256((installed / 'launcher.py').read_bytes()).hexdigest()
            proc = run(prefix + ['/LOG=' + str(root / 'blocked-upgrade.log')])
            require(proc.returncode != 0, 'Running-tool upgrade was allowed')
            require(guard.poll() is None, 'Installer killed a running fixture')
            require(hashlib.sha256((installed / 'launcher.py').read_bytes()).hexdigest() == before, 'Blocked upgrade changed program')
            passed('running_tool_blocks_upgrade_without_killing')
            proc = run([uninstaller, '/VERYSILENT', '/SUPPRESSMSGBOXES', '/NORESTART', '/LOG=' + str(root / 'blocked-uninstall.log')])
            require(proc.returncode != 0 and uninstaller.exists() and guard.poll() is None, 'Running-tool uninstall was allowed or killed fixture')
            require(data_sentinel.read_bytes() == data_bytes, 'Blocked uninstall touched user data')
            passed('running_tool_blocks_uninstall_without_killing')
        finally:
            # This is only our explicit disposable mutex fixture, never another tool.
            guard.terminate()
            guard.wait(timeout=10)

        # Pause the real uninstaller on its normal confirmation dialog, after
        # InitializeUninstall acquired the maintenance mutex. Do not add a test
        # backdoor or sleep option to the shipped installer.
        uninstalling = subprocess.Popen([str(uninstaller), '/NORESTART'])
        try:
            probe = ('import sys;sys.path.insert(0,sys.argv[1]);'
                     'from installation import ForegroundGuard;g=ForegroundGuard();'
                     'g.__enter__();g.close()')
            deadline = time.monotonic() + 15
            blocked = None
            while uninstalling.poll() is None and time.monotonic() < deadline:
                blocked = run([python, '-B', '-c', probe, installed], cwd=installed)
                if blocked.returncode != 0 and 'Installation is in progress' in blocked.stderr:
                    break
                time.sleep(.05)
            require(blocked is not None and blocked.returncode != 0 and 'Installation is in progress' in blocked.stderr,
                    'New foreground launch was not blocked during real uninstall')
            proc = run(prefix + ['/LOG=' + str(root / 'concurrent-setup.log')])
            require(proc.returncode != 0, 'Setup ran concurrently with uninstall')
            passed('real_uninstall_holds_guard_blocks_new_launch_and_parallel_setup')

            # Decline the real confirmation only in the disposable CI process.
            import ctypes
            from ctypes import wintypes as W
            import psutil
            user = ctypes.WinDLL('user32', use_last_error=True)
            user.GetWindowThreadProcessId.argtypes = [W.HWND, ctypes.POINTER(W.DWORD)]
            user.PostMessageW.argtypes = [W.HWND, W.UINT, W.WPARAM, W.LPARAM]
            callback_type = ctypes.WINFUNCTYPE(W.BOOL, W.HWND, W.LPARAM)
            user.EnumWindows.argtypes = [callback_type, W.LPARAM]
            tree = psutil.Process(uninstalling.pid)
            owned = {tree.pid} | {p.pid for p in tree.children(recursive=True)}
            @callback_type
            def decline(window, _):
                pid = W.DWORD()
                user.GetWindowThreadProcessId(window, ctypes.byref(pid))
                if pid.value in owned:
                    user.PostMessageW(window, 0x111, 7, 0)  # WM_COMMAND, IDNO
                    user.PostMessageW(window, 0x111, 2, 0)  # IDCANCEL if no confirm
                return True
            user.EnumWindows(decline, 0)
            uninstalling.wait(timeout=15)
            require(python.exists() and data_sentinel.read_bytes() == data_bytes, 'Declined uninstall removed data/program')
            passed('cancelled_uninstall_releases_guard_and_keeps_files')
        finally:
            if uninstalling.poll() is None:
                # Failure cleanup is limited to this explicit disposable fixture.
                import psutil
                tree = psutil.Process(uninstalling.pid)
                for child in tree.children(recursive=True):
                    child.terminate()
                uninstalling.terminate()
                uninstalling.wait(timeout=10)

        proc = run([uninstaller, '/VERYSILENT', '/SUPPRESSMSGBOXES', '/NORESTART', '/LOG=' + str(root / 'uninstall.log')])
        require(proc.returncode == 0, 'Uninstall failed: ' + str(proc.returncode))
        deadline = time.monotonic() + 15
        while (installed / 'launcher.py').exists() and time.monotonic() < deadline:
            time.sleep(.1)
        require(not (installed / 'launcher.py').exists() and not python.exists(), 'Program files survived uninstall')
        require(data_sentinel.read_bytes() == data_bytes and official_marker.read_text() == 'not a credential', 'Uninstall deleted external data')
        passed('real_uninstall_preserves_external_data')
        # Inno's original uninstaller exits before its temporary cleanup child
        # has closed the log. Require real handle release before removing the
        # disposable fixture; do not hide failed cleanup or kill the child.
        deadline = time.monotonic() + 15
        for log in root.glob('*.log'):
            released = log.with_suffix('.released-log')
            while True:
                try:
                    log.rename(released)  # A non-share-delete writer blocks this.
                    break
                except PermissionError:
                    if time.monotonic() >= deadline:
                        raise AssertionError('Uninstaller did not release its own log handle: ' + log.name)
                    time.sleep(.1)
        # Also require the actual maintenance guard to disappear, rather than
        # treating deletion of the visible program files as the commit point.
        probe = ('import sys;sys.path.insert(0,sys.argv[1]);'
                 'from installation import ForegroundGuard;'
                 'g=ForegroundGuard();g.__enter__();g.close()')
        proc = run([sys.executable, '-B', '-c', probe, Path(__file__).resolve().parents[1]])
        require(proc.returncode == 0, 'Uninstall left its maintenance guard active: ' + proc.stderr)
        passed('uninstaller_releases_own_logs_and_maintenance_guard_before_cleanup')
    report.update(status='passed', installation_performed='Only disposable GitHub runner',
                  baseline_upgrade='public .7 installer to current candidate' if args.baseline_setup else 'simulated previous marker only',
                  limitations=['No actual official CLI login or model call', 'No Windows SmartScreen/Defender/old OS acceptance claim'])
    args.report.write_text(json.dumps(report, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    print('Native installer lifecycle passed:', len(report['checks']), flush=True)


if __name__ == '__main__':
    main()
