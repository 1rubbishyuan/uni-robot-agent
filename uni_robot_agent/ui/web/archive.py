"""Read existing environment snapshot archives inside explicitly selected directories."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path


class ArchivedSnapshots:
    def __init__(self, root, scopes=()):
        self.root = Path(root).resolve()
        self.scopes = tuple(Path(p).resolve() for p in scopes) or (self.root,)
        self.records = []
        self.media = {}
        self.loaded = False

    def load(self, events):
        if self.loaded:
            return
        self.loaded = True
        # A shared snapshot identity establishes ownership; directory proximity does not.
        linked = {e['output']['snapshot_id'] for e in events
                  if isinstance(e.get('output'), dict) and e['output'].get('snapshot_id')}
        if not linked:
            return
        candidates = set()
        for parent in (self.root, *self.root.parents):
            if not any(parent.is_relative_to(scope) for scope in self.scopes):
                continue
            for name in ('environment', 'env'):
                directory = (parent / name).resolve()
                if directory.is_relative_to(parent) and (directory / 'artifacts').is_dir():
                    candidates.add(directory)
        for directory in sorted(candidates):
            snapshots = []
            for meta in (directory / 'artifacts').glob('*.json'):
                if not meta.resolve().is_relative_to(directory):
                    continue
                try:
                    ref = json.loads(meta.read_text())
                    if ref.get('media_type') != 'application/json':
                        continue
                    payload = meta.with_suffix('')
                    if not payload.resolve().is_relative_to(directory):
                        continue
                    item = json.loads(payload.read_text())
                    if (isinstance(item, dict) and item.get('artifact_type') == 'environment_snapshot'
                            and item.get('schema_version') == 2):
                        snapshots.append(dict(item, snapshot_id=payload.name))
                except (OSError, ValueError, AttributeError):
                    continue
            episodes = {s['episode_id'] for s in snapshots if s['snapshot_id'] in linked}
            prefix = 'archived/' + hashlib.sha256(str(directory).encode()).hexdigest()[:16]
            for snapshot in snapshots:
                if snapshot['episode_id'] not in episodes:
                    continue
                cameras = {}
                for name, frame in snapshot.get('cameras', {}).items():
                    ref = frame.get('rgb', {})
                    suffix = {'image/png': '.png', 'image/jpeg': '.jpg', 'image/webp': '.webp'}.get(ref.get('media_type'))
                    digest = ref.get('sha256', '')
                    if not suffix or len(digest) != 64 or any(c not in '0123456789abcdef' for c in digest):
                        continue
                    path = (directory / 'images' / (digest + suffix)).resolve()
                    if not path.is_relative_to(directory) or not path.is_file():
                        continue
                    key = prefix + '/images/' + path.name
                    self.media[key] = (path, directory)
                    cameras[name] = {'path': key}
                if cameras:
                    self.records.append({**snapshot, 'cameras': cameras})
        self.records.sort(key=lambda s: s['captured_at'])

    def path(self, key):
        entry = self.media.get(key)
        if entry is None:
            return None
        path, directory = entry
        resolved = path.resolve()
        return resolved if resolved.is_relative_to(directory) and resolved.is_file() else None
