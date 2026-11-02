"""Mirror published observations without asking the environment to capture."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

from uni_robot_agent.services.environment.client import EnvironmentClient


class SnapshotReader:
    def __init__(self, url, root):
        self.client = EnvironmentClient(url, timeout_s=2)
        self.root = Path(root)
        self.seen = set()
        self.info = None

    def poll(self, stopped=None):
        if self.info is None:
            self.info = self.client.connect()
        snapshot = self.client.latest_snapshot()
        if snapshot.get('episode_id') != self.info.episode_id:
            raise ValueError('Environment episode changed; refusing unrelated preview')
        sid = snapshot['snapshot_id']
        if sid in self.seen:
            return None
        cameras = {}
        directory = self.root / 'monitor_media'
        directory.mkdir(exist_ok=True)
        for name, frame in snapshot.get('cameras', {}).items():
            if stopped is not None and stopped.is_set():
                return None
            ref = frame['rgb']
            if ref.get('media_type') not in {'image/png', 'image/jpeg', 'image/webp'}:
                continue
            data = self.client.artifact_bytes(ref['artifact_id'])
            suffix = {'image/png': '.png', 'image/jpeg': '.jpg', 'image/webp': '.webp'}[ref['media_type']]
            filename = hashlib.sha256(data).hexdigest() + suffix
            path = directory / filename
            if not path.exists():
                temporary = path.with_suffix('.tmp')
                temporary.write_bytes(data)
                temporary.replace(path)
            cameras[name] = {'path': 'monitor_media/' + filename}
        if stopped is not None and stopped.is_set():
            return None
        record = {key: snapshot.get(key) for key in ('snapshot_id', 'captured_at', 'state_version', 'state')}
        record['cameras'] = cameras
        with (self.root / 'monitor_frames.jsonl').open('a', encoding='utf-8') as stream:
            stream.write(json.dumps(record, ensure_ascii=False) + '\n')
        self.seen.add(sid)
        return self.info

    def close(self):
        self.client.close()
