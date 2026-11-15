"""On-demand, bounded OS metrics for this console's process tree only."""
import copy
import math
from datetime import datetime, timezone
from pathlib import Path
import re
import subprocess
import threading
import time


def process_rows():
    completed = subprocess.run(['ps', '-axo', 'pid=,ppid=,pgid=,rss=,pcpu=,comm='],
                               capture_output=True, text=True, timeout=2)
    if completed.returncode:
        raise OSError('process snapshot unavailable')
    rows = []
    for line in completed.stdout.splitlines():
        values = line.strip().split(None, 5)
        if len(values) != 6:
            continue
        try:
            pid, parent, group, rss = map(int, values[:4])
            cpu = float(values[4])
        except ValueError:
            continue
        rows.append({'pid': pid, 'parent_pid': parent, 'pgid': group, 'rss_mb': round(rss * 1024 / 1000000, 1),
                     'cpu_percent': cpu, 'name': Path(values[5]).name[:80]})
    return rows


def physical_memory(pid, timeout):
    completed = subprocess.run(['footprint', '-p', str(pid)], capture_output=True,
                               text=True, timeout=timeout)
    if completed.returncode:
        return None
    found = re.search(r'^\s*phys_footprint:\s*([\d.]+)\s*(GB|MB|KB|B)\b',
                      completed.stdout, re.M | re.I)
    if not found:
        return None
    scale = {'B': 1, 'KB': 1024, 'MB': 1024**2, 'GB': 1024**3}[found[2].upper()]
    return round(float(found[1]) * scale / 1000000, 1)


def owned_process_rows(rows, roots):
    by_pid = {row['pid']: row for row in rows}
    owned = set(roots)
    groups = {pid for pid in roots if by_pid.get(pid, {}).get('pgid') == pid}
    owned.update(row['pid'] for row in rows if row.get('pgid') in groups)
    for _ in range(12):
        extra = {row['pid'] for row in rows if row['parent_pid'] in owned}
        if extra.issubset(owned):
            break
        owned.update(extra)
    candidates = [row for row in rows if row['pid'] in owned and row['name'] not in ('ps', 'footprint')]
    return sorted(candidates, key=lambda row: (row['pid'] not in roots, row['pid']))


class ResourceMonitor:
    def __init__(self, reader=None, memory_reader=None, clock=None):
        self.reader = reader or process_rows
        self.memory_reader = memory_reader or physical_memory
        self.clock = clock or time.monotonic
        self.lock = threading.Lock()
        self.cached = None
        self.signature = None
        self.checked = float('-inf')
        self.summary_lock = threading.Lock()
        self.summary_cached = None
        self.summary_signature = None
        self.summary_checked = float('-inf')

    def summary(self, roots, force=False):
        """Cheap live RSS total; never launches per-process footprint commands."""
        signature = tuple(sorted(roots.items()))
        with self.summary_lock:
            if (not force and self.summary_cached and signature == self.summary_signature
                    and self.clock() - self.summary_checked < 3):
                return copy.deepcopy(self.summary_cached)
            warning = ''
            try:
                candidates = owned_process_rows(self.reader(), roots)
            except (OSError, subprocess.SubprocessError):
                candidates = []
                warning = '暂时未能读取进程占用，请稍后刷新。'
            values = [row.get('rss_mb') for row in candidates]
            valid = bool(values) and all(type(value) in (int, float) and math.isfinite(value) and value >= 0
                                         for value in values)
            value = {'rss_total_mb': round(sum(values), 1) if valid else None,
                     'process_count': len(candidates), 'checked_at': datetime.now(timezone.utc).isoformat(),
                     'warning': warning or ('' if valid else '暂未取得有效内存读数。'),
                     'scope': '仅本控制台及它启动的 CLI；不含浏览器。', 'metric': 'rss'}
            self.summary_cached, self.summary_signature, self.summary_checked = value, signature, self.clock()
            return copy.deepcopy(value)

    def sample(self, roots, force=False):
        """No thread, timer, subprocess, or scan runs until this method is called."""
        signature = tuple(sorted(roots.items()))
        with self.lock:
            if not force and self.cached and signature == self.signature and self.clock() - self.checked < 3:
                return copy.deepcopy(self.cached)
            warning = ''
            try:
                rows = self.reader()
            except (OSError, subprocess.SubprocessError):
                rows = []
                warning = '暂时未能读取进程占用，请稍后刷新。'
            candidates = owned_process_rows(rows, roots)
            selected = []
            deadline = self.clock() + 2.5
            for row in candidates[:32]:
                item = dict(row, role=roots.get(row['pid'], '任务子进程'), footprint_mb=None)
                remaining = deadline - self.clock()
                if row['pid'] in roots and remaining > .05:
                    try:
                        item['footprint_mb'] = self.memory_reader(row['pid'], min(1.2, remaining))
                    except (OSError, ValueError, subprocess.SubprocessError):
                        pass
                selected.append(item)
            value = {'processes': selected, 'checked_at': datetime.now(timezone.utc).isoformat(),
                     'warning': warning, 'omitted': max(0, len(candidates) - 32),
                     'scope': '仅本控制台及它启动的 CLI；浏览器与其他窗口另计。',
                     'memory_note': '优先显示物理内存；未取得时标明 RSS 驻留值。单位为 MB。'}
            self.cached, self.signature, self.checked = value, signature, self.clock()
            return copy.deepcopy(value)
