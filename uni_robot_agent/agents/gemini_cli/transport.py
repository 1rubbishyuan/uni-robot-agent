"""One owned ACP stdio connection, with strict response identity and EOF handling."""
import json
import queue
import threading
import time


class AcpTransport:
    def __init__(self, process, directory):
        self.process = process
        self.events = queue.Queue()
        self.lock = threading.Lock()
        self.sequence = 0
        self.workers = []
        for label, stream in [('stdout', process.stdout), ('stderr', process.stderr)]:
            worker = threading.Thread(target=self.read, args=(label, stream, directory), daemon=True)
            worker.start()
            self.workers.append(worker)

    def read(self, label, stream, directory):
        try:
            with (directory / f'{label}.log').open('w') as archive:
                for line in stream:
                    archive.write(line)
                    archive.flush()
                    if label == 'stdout':
                        message = json.loads(line)
                        if not isinstance(message, dict) or message.get('jsonrpc') != '2.0':
                            raise ValueError('Invalid ACP JSON-RPC message')
                        self.events.put(message)
            if label == 'stdout':
                self.events.put(EOFError('Gemini ACP stdout closed'))
        except Exception as error:
            self.events.put(error)

    def send(self, message):
        with self.lock:
            self.process.stdin.write(json.dumps({'jsonrpc': '2.0', **message}) + '\n')
            self.process.stdin.flush()

    def notify(self, method, params):
        self.send({'method': method, 'params': params})

    def request(self, method, params, *, check_running, on_notification, timeout=None):
        # The adapter makes one outstanding request. Notifications (including
        # cancellation from another thread) can still interleave on the pipe.
        self.sequence += 1
        request_id = self.sequence
        self.send({'id': request_id, 'method': method, 'params': params})
        deadline = time.monotonic() + timeout if timeout else None
        while True:
            check_running()
            if deadline is not None and time.monotonic() >= deadline:
                raise TimeoutError('ACP request timed out: ' + method)
            try:
                message = self.events.get(timeout=0.1)
            except queue.Empty:
                continue
            if isinstance(message, Exception):
                raise message
            if 'method' in message:
                if 'id' in message:
                    if message['method'] == 'session/request_permission':
                        self.send({'id': message['id'], 'result': {'outcome': {'outcome': 'cancelled'}}})
                    else:
                        self.send({'id': message['id'], 'error': {
                            'code': -32601, 'message': 'Client capability not supported'}})
                    raise RuntimeError('Unexpected ACP client request: ' + message['method'])
                on_notification(message['method'], message.get('params', {}))
                continue
            if type(message.get('id')) is not int or message['id'] != request_id:
                raise RuntimeError('Unknown or duplicate ACP response ID')
            if 'error' in message:
                raise RuntimeError('ACP error: ' + json.dumps(message['error']))
            if 'result' not in message or not isinstance(message['result'], dict):
                raise RuntimeError('ACP response is missing an object result')
            return message['result']

    def close(self):
        # The owner terminates the process before joining pipe readers.
        for worker in self.workers:
            worker.join(timeout=5)
        if any(worker.is_alive() for worker in self.workers):
            raise RuntimeError('Gemini ACP readers did not terminate')
