"""Incremental, restartable readers for append-only run artifacts."""
from __future__ import annotations

import json
from pathlib import Path
import threading
import time


def read_json(path):
    try:
        value = json.loads(path.read_text())
        return value if isinstance(value, dict) else {}
    except (OSError, ValueError):
        return {}


class JsonLines:
    def __init__(self, path):
        self.path = path
        self.offset = 0
        self.inode = None
        self.records = []
        self.errors = 0

    def refresh(self):
        try:
            stat = self.path.stat()
        except FileNotFoundError:
            return False
        if stat.st_ino != self.inode or stat.st_size < self.offset:
            self.offset = 0
            self.records = []
            self.errors = 0
            self.inode = stat.st_ino
        changed = False
        with self.path.open('rb') as stream:
            stream.seek(self.offset)
            while line := stream.readline():
                # The writer may still be appending the last JSON record.
                if not line.endswith(b'\n'):
                    break
                self.offset = stream.tell()
                changed = True
                try:
                    value = json.loads(line)
                    if isinstance(value, dict):
                        self.records.append(value)
                    else:
                        self.errors += 1
                except (ValueError, UnicodeError):
                    self.errors += 1
        return changed


class RunReader:
    def __init__(self, root, archive_scopes=()):
        self.root = Path(root).resolve()
        from .archive import ArchivedSnapshots
        self.archive = ArchivedSnapshots(self.root, archive_scopes)
        self.events = JsonLines(self.root / 'trajectory.jsonl')
        self.frames = JsonLines(self.root / 'monitor_frames.jsonl')
        self.lock = threading.RLock()
        self.version = 0
        self.state = None
        self.metadata = None

    def refresh(self):
        from .state import project
        with self.lock:
            changed = self.events.refresh() | self.frames.refresh()
            names = ('result.json', 'monitor.json', 'deployment.json', 'supervisor.json', 'native_outcome.json')
            metadata = {n[:-5]: read_json(self.root / n) for n in names}
            monitor = dict(metadata['monitor'])
            heartbeat = monitor.pop('heartbeat_at', None)
            if (monitor.get('state') in {'starting', 'deploying', 'running', 'finishing'}
                    and heartbeat is not None and time.time() - heartbeat > 15):
                monitor.update(state='stale', finished_at=heartbeat)
            metadata['monitor'] = monitor
            # Heartbeats establish liveness, but must not resend megabytes of unchanged history.
            if changed or metadata != self.metadata or self.state is None:
                self.metadata = metadata
                records = self.events.records
                # Subscribers can finish out of order across emitting threads.
                records = sorted(enumerate(records), key=lambda pair: (pair[1].get('event_seq', pair[0] + 1), pair[0]))
                seen = set()
                ordered = []
                for _, record in records:
                    key = (record.get('session_id', ''), record.get('event_seq'))
                    if key[1] and key in seen:
                        continue
                    seen.add(key)
                    ordered.append(record)
                if metadata['result']:
                    self.archive.load(ordered)
                snapshots = sorted(self.archive.records + self.frames.records, key=lambda s: s.get('captured_at') or 0)
                self.state = project(ordered, snapshots,
                    metadata, self.root.name)
                self.state['read_errors'] = self.events.errors + self.frames.errors
                self.version += 1
                self.state['revision'] = self.version
            return self.state
