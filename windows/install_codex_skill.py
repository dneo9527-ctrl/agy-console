#!/usr/bin/env python3
"""Install the companion skill; optional managed guidance enables default use.

Never starts a service, calls a model, or reads account/configuration files.
"""
import argparse
import json
import os
from pathlib import Path
import shlex
import shutil
import sys
import tempfile

SCRIPT_DIR = Path(__file__).resolve().parent
PACKAGE_ROOT = SCRIPT_DIR.parent if SCRIPT_DIR.name == 'scripts' else SCRIPT_DIR
START = '<!-- agy-console:managed:start -->'
END = '<!-- agy-console:managed:end -->'
GUIDANCE = '\n'.join([START,
    'For substantive project work, use the installed agy-console skill for a bounded Antigravity contribution or review through the running local Agy Console. Codex owns execution and verification. Respect this chat\'s collaboration/page/model preferences and any user instruction for Codex only. Share only relevant task material, never credentials or unrelated private information. Short replies need no model call.',
    END])


def guidance_text(original):
    if START in original or END in original:
        if original.count(START) != 1 or original.count(END) != 1 or original.index(START) > original.index(END):
            raise ValueError('Existing Agy guidance markers are malformed; no files changed.')
        begin, finish = original.index(START), original.index(END) + len(END)
        return original[:begin] + GUIDANCE + original[finish:]
    return original.rstrip() + ('\n\n' if original.strip() else '') + GUIDANCE + '\n'


def install(console_root, codex_home, enable_default=False, python=None):
    root, home = Path(console_root).resolve(), Path(codex_home).expanduser().absolute()
    bridge = root / 'bridge.py'
    if not bridge.is_file():
        bridge = root / 'app/bridge.py'
    if not bridge.is_file() or bridge.is_symlink():
        raise ValueError('Select an extracted Agy Console folder containing bridge.py or app/bridge.py.')
    source = PACKAGE_ROOT / 'skills/agy-console'
    names = ['SKILL.md', 'scripts/dispatch.py']
    for name in names:
        if not (source / name).is_file() or (source / name).is_symlink():
            raise ValueError('Companion skill files are missing or linked; extract the complete package.')
    target = home / 'skills/agy-console'
    if home.is_symlink() or (home / 'skills').is_symlink() or target.is_symlink():
        raise ValueError('Refusing a linked Codex home or skill destination; choose an explicit directory.')
    if target.exists():
        marker = target / 'agy-console-install.json'
        if marker.is_symlink() or not marker.is_file() or marker.stat().st_size > 16384:
            raise ValueError('An unrelated agy-console skill already exists; it was not overwritten.')
        existing = json.loads(marker.read_text(encoding='utf-8'))
        if existing.get('product') != 'agy-console-companion' or existing.get('schema') != 1:
            raise ValueError('Unrecognized companion installation; it was not overwritten.')
    agents = home / 'AGENTS.md'
    previous, updated = None, None
    if enable_default:
        if agents.is_symlink() or agents.exists() and (not agents.is_file() or agents.stat().st_size > 1024 * 1024):
            raise ValueError('Cannot safely update this AGENTS.md; no files changed.')
        previous = agents.read_bytes() if agents.exists() else None
        original = previous.decode('utf-8') if previous is not None else ''
        updated = guidance_text(original).encode('utf-8')
    runtime = Path(python or sys.executable).resolve()
    if not runtime.is_file():
        raise ValueError('Configured Python interpreter is unavailable.')
    target.parent.mkdir(parents=True, exist_ok=True)
    lock = home / '.agy-skill-install.lock'
    try:
        lock.mkdir()
    except FileExistsError:
        raise ValueError('Another companion installation is active; no files changed.') from None
    stage = Path(tempfile.mkdtemp(prefix='.agy-skill-stage-', dir=target.parent))
    backup, agents_backup = None, None
    try:
        for name in names:
            destination = stage / name
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(source / name, destination)
            destination.chmod(0o600)
        command = [str(runtime), str(target / 'scripts/dispatch.py')]
        if os.name == 'nt':
            rendered = '& ' + ' '.join("'" + arg.replace("'", "''") + "'" for arg in command)
            shell = 'PowerShell'
        else:
            rendered = shlex.join(command)
            shell = 'POSIX shell'
        with (stage / 'SKILL.md').open('a', encoding='utf-8') as stream:
            stream.write('\n## Local connection\n\nDispatcher command (' + shell + '):\n\n```text\n' + rendered + '\n```\n\nAppend bridge arguments to that command.\n')
        for name, value in [('connection.json', {'schema': 1, 'python': str(runtime), 'bridge': str(bridge)}),
                            ('agy-console-install.json', {'schema': 1, 'product': 'agy-console-companion'})]:
            path = stage / name
            path.write_text(json.dumps(value, indent=2) + '\n', encoding='utf-8')
            path.chmod(0o600)
        if updated is not None and previous is not None and updated != previous:
            fd, name = tempfile.mkstemp(prefix='AGENTS.md.before-agy-', dir=home)
            agents_backup = Path(name)
            with os.fdopen(fd, 'wb') as stream:
                stream.write(previous)
        if target.exists():
            backup = Path(tempfile.mkdtemp(prefix='.agy-skill-backup-', dir=target.parent))
            backup.rmdir()
            target.rename(backup)
        try:
            stage.rename(target)
            if updated is not None and updated != previous:
                current = agents.read_bytes() if agents.exists() else None
                if current != previous:
                    raise ValueError('AGENTS.md changed during installation; existing guidance was preserved.')
                fd, name = tempfile.mkstemp(prefix='.AGENTS-agy-', dir=home)
                temporary = Path(name)
                try:
                    with os.fdopen(fd, 'wb') as stream:
                        stream.write(updated)
                    os.replace(temporary, agents)
                finally:
                    temporary.unlink(missing_ok=True)
        except BaseException:
            if target.exists():
                shutil.rmtree(target)
            if backup is not None:
                backup.rename(target)
                backup = None
            raise
        return {'installed': str(target), 'default_collaboration_enabled': enable_default,
                'previous_skill_backup': str(backup) if backup else None,
                'agents_backup': str(agents_backup) if agents_backup else None,
                'model_call_or_service_start': False,
                'next': 'Reopen or start a Codex chat to discover the skill; start Agy Console in its foreground window.'}
    finally:
        if stage.exists():
            shutil.rmtree(stage)
        lock.rmdir()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--console-root', type=Path, default=PACKAGE_ROOT)
    parser.add_argument('--codex-home', type=Path, default=Path(os.environ.get('CODEX_HOME') or Path.home() / '.codex'))
    parser.add_argument('--enable-default', action='store_true', help='Add/update one managed block in Codex AGENTS.md, preserving existing guidance.')
    args = parser.parse_args()
    print(json.dumps(install(args.console_root, args.codex_home, args.enable_default), indent=2))


if __name__ == '__main__':
    try:
        main()
    except (OSError, ValueError, TypeError) as error:
        print(str(error), file=sys.stderr)
        raise SystemExit(1)
