"""FIFO queue sharing the console lock so claiming and reordering are atomic."""
from collections import deque
import threading


class TaskQueue:
    def __init__(self, lock):
        self.ready = threading.Condition(lock)
        self.items = deque()

    def put(self, item):
        with self.ready:
            self.items.append(item)
            self.ready.notify()

    def get(self):
        with self.ready:
            while not self.items:
                self.ready.wait()
            return self.items.popleft()

    def snapshot(self):
        with self.ready:
            return [item for item in self.items if item is not None]

    def empty(self):
        with self.ready:
            return not self.items

    def discard(self, tid):
        with self.ready:
            self.items = deque(item for item in self.items if item is None or item[1] != tid)

    def move(self, tid, direction, groups):
        if direction not in ('up', 'down'):
            raise ValueError('请选择前移或后移。')
        with self.ready:
            items = list(self.items)
            index = next((i for i, item in enumerate(items) if item and item[1] == tid), None)
            if index is None:
                raise ValueError('任务已开始调度或已经结束，请刷新队列。')
            target = index + (-1 if direction == 'up' else 1)
            if not 0 <= target < len(items) or items[target] is None:
                raise ValueError('任务已在这一方向的边界。')
            if tid not in groups or items[target][1] not in groups:
                raise ValueError('队列已变化，请刷新后再调整。')
            if groups[tid] == groups[items[target][1]]:
                raise ValueError('同一会话的任务必须保持先后顺序。')
            items[index], items[target] = items[target], items[index]
            self.items = deque(items)
