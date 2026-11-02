"""CLI-owned monitor lifecycle; the monitor never owns robot services."""
from __future__ import annotations

from pathlib import Path
import logging
import signal
import threading
import time

from uni_robot_agent.artifacts.run import write_json_atomic
from uni_robot_agent.services.hosting import HttpServer
from .media import SnapshotReader
from .server import Collection, create_monitor_app, discover

logger = logging.getLogger(__name__)


class LiveMonitor:
    def __init__(self, args, directory):
        self.root = Path(directory).resolve()
        self.root.mkdir(parents=True, exist_ok=True)
        self.args = args
        self.record = dict(state='starting', started_at=time.time(), heartbeat_at=time.time(), integration=args.integration,
            model=args.model, task=getattr(args, 'task', None) or args.integration,
            episode_timeout_s=args.episode_timeout_s)
        self.collection = Collection([self.root])
        self.server = HttpServer(create_monitor_app(self.collection), args.monitor_host, args.monitor_port)
        self.stop_event = threading.Event()
        self.thread = None
        self.closed = False
        self.url = None
        self.lock = threading.RLock()

    def start(self):
        self.update()
        try:
            self.server.start_background()
            self.thread = threading.Thread(target=self._sample, name='monitor-preview', daemon=True)
            self.thread.start()
        except BaseException:
            self.close()
            raise
        print(f'Monitor: {self.server.url}\nReplay: uni-robot-agent monitor --run-dir {self.root}', flush=True)
        return self

    def update(self, **fields):
        with self.lock:
            self.record.update(fields)
            write_json_atomic(self.root / 'monitor.json', self.record)

    def attach(self, url):
        self.url = url
        self.update(state='running')

    def _sample(self):
        reader = None
        warned = False
        try:
            while not self.stop_event.is_set():
                if self.url:
                    try:
                        if reader is None:
                            reader = SnapshotReader(self.url, self.root)
                        info = reader.poll(self.stop_event)
                        if info is not None and not self.stop_event.is_set():
                            self.update(max_episode_steps=info.environment.max_episode_steps, instruction=info.task)
                        warned = False
                    except Exception as error:
                        if not warned:
                            logger.info('Monitor preview unavailable: %s', error)
                            warned = True
                if not self.stop_event.is_set():
                    self.update(heartbeat_at=time.time())
                self.stop_event.wait(1)
        finally:
            if reader is not None:
                reader.close()

    def close(self, state='finished', error=None):
        if self.closed:
            return
        self.closed = True
        self.stop_event.set()
        if self.thread is not None:
            self.thread.join(5)
        self.update(state=state, finished_at=time.time(), error=error)
        self.collection.closing.set()
        self.server.stop()


def serve_directories(args):
    roots = discover(args.run_dir)
    if not roots:
        raise ValueError('No result.json, trajectory.jsonl or monitor.json found under the specified directories')
    collection = Collection(roots, archive_scopes=args.run_dir)
    server = HttpServer(create_monitor_app(collection), args.host, args.port)
    stopped = threading.Event()
    previous = {s: signal.signal(s, lambda *_: stopped.set()) for s in (signal.SIGINT, signal.SIGTERM)}
    try:
        server.start_background()
        print(f'Monitor: {server.url} ({len(roots)} runs; read-only)', flush=True)
        while not stopped.wait(.5):
            pass
    finally:
        collection.closing.set()
        server.stop()
        for signum, handler in previous.items():
            signal.signal(signum, handler)
