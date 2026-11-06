"""Own an OpenCode HTTP server and consume its SSE notifications."""
import json
import queue
import re
import threading
import time

import httpx


class OpenCodeTransport:
    def __init__(self, process, directory, *, password, timeout):
        self.process, self.directory = process, directory
        self.events = queue.Queue()
        self.addresses = queue.Queue()
        self.closed = threading.Event()
        self.connected = threading.Event()
        self.readers = []
        self.client = None
        self.consumer = None
        self.command_timeout = timeout
        self.stream_error = None
        deadline = time.monotonic() + timeout
        for label, stream in [('stdout', process.stdout), ('stderr', process.stderr)]:
            worker = threading.Thread(target=self.read_output, args=(label, stream), daemon=True)
            worker.start()
            self.readers.append(worker)
        try:
            try:
                address = self.addresses.get(timeout=max(0, deadline - time.monotonic()))
            except queue.Empty:
                raise TimeoutError('OpenCode did not report a loopback server address') from None
            self.client = httpx.Client(base_url=address, auth=('opencode', password),
                                       trust_env=False, timeout=httpx.Timeout(timeout, read=None))
            self.request('GET', '/global/health', timeout=max(0, deadline - time.monotonic()))
            self.consumer = threading.Thread(target=self.consume_events, daemon=True)
            self.consumer.start()
            # The first instance request loads plugins and their cold dependencies.
            if not self.connected.wait(max(0, deadline - time.monotonic())):
                raise TimeoutError('OpenCode SSE stream did not connect')
            if self.stream_error is not None:
                raise self.stream_error
        except Exception:
            from uni_robot_agent.agents.managed import stop_process
            stop_process(process)
            self.close()
            raise

    def read_output(self, label, stream):
        with (self.directory / f'{label}.log').open('w') as log:
            for line in stream:
                log.write(line)
                log.flush()
                match = re.search(r'http://127\.0\.0\.1:[0-9]+', line)
                if match:
                    self.addresses.put(match[0])

    def request(self, method, path, body=None, *, timeout=None):
        response = self.client.request(method, path, json=body,
                                       timeout=self.command_timeout if timeout is None else timeout)
        response.raise_for_status()
        return response.json() if response.content else None

    def consume_events(self):
        try:
            with self.client.stream('GET', '/event') as response:
                response.raise_for_status()
                self.connected.set()
                data = []
                with (self.directory / 'events.jsonl').open('w') as archive:
                    for line in response.iter_lines():
                        if self.closed.is_set():
                            break
                        if line.startswith('data:'):
                            data.append(line[5:].lstrip())
                        elif not line and data:
                            event = json.loads('\n'.join(data))
                            data.clear()
                            archive.write(json.dumps(event) + '\n')
                            archive.flush()
                            self.events.put(event)
            if not self.closed.is_set():
                raise RuntimeError('OpenCode event stream disconnected')
        except Exception as error:
            self.stream_error = error
            self.events.put(error)
            self.connected.set()

    def close(self):
        self.closed.set()
        if self.client is not None:
            self.client.close()
        if self.consumer is not None:
            self.consumer.join(timeout=5)
        for reader in self.readers:
            reader.join(timeout=5)
        if (self.consumer is not None and self.consumer.is_alive()) or any(reader.is_alive() for reader in self.readers):
            raise RuntimeError('OpenCode transport workers did not terminate')
