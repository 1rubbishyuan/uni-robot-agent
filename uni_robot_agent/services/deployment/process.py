"""Own exact process groups and wait for structured service identity."""
from contextlib import contextmanager
import os
from pathlib import Path
import signal
import socket
import subprocess
import time
import sys
from urllib.parse import urlsplit

import httpx


def enable_child_reaping():
    """CLI-only Linux ownership: reap orphaned native helpers after their leader exits."""
    if sys.platform == 'linux':
        import ctypes
        libc = ctypes.CDLL(None, use_errno=True)
        if libc.prctl(36, 1, 0, 0, 0) != 0:  # PR_SET_CHILD_SUBREAPER
            raise OSError(ctypes.get_errno(), 'Cannot enable deployment child reaping')


@contextmanager
def cancellation_signals():
    def interrupt(signum, frame):
        raise KeyboardInterrupt
    previous = {}
    try:
        for sig in (signal.SIGTERM, signal.SIGINT):
            previous[sig] = signal.signal(sig, interrupt)
        yield
    finally:
        for sig, handler in previous.items():
            signal.signal(sig, handler)


def gpu_environment(index):
    if index < 0:
        raise ValueError('GPU index must be nonnegative')
    visible = os.environ.get('CUDA_VISIBLE_DEVICES')
    if visible is None:
        selected = str(index)
    else:
        devices = [s.strip() for s in visible.split(',') if s.strip() and s.strip() != '-1']
        if index >= len(devices):
            raise ValueError(f'GPU {index} is not in CUDA_VISIBLE_DEVICES')
        selected = devices[index]
    return {'CUDA_VISIBLE_DEVICES': selected}


def require_free_port(url):
    address = urlsplit(url)
    with socket.socket() as sock:
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        try:
            sock.bind((address.hostname, address.port))
        except OSError as exc:
            raise RuntimeError(f'Service port is unavailable: {url}') from exc


def check_identity(info, expected):
    for key, value in expected.items():
        actual = info
        for component in key.split('.'):
            actual = actual.get(component) if isinstance(actual, dict) else None
        if actual != value:
            raise RuntimeError(f'Service identity mismatch for {key}: expected {value!r}, received {actual!r}')


class OwnedProcess:
    def __init__(self, command, *, environment=None, cwd=None, log_path=None, shutdown_timeout=30):
        self.command = list(map(str, command))
        self.environment, self.cwd = environment or {}, cwd
        self.log_path = Path(log_path) if log_path else None
        self.shutdown_timeout = shutdown_timeout
        self.process = None
        self._log = None
        self.forced = False
        self._closed = False

    def start(self, *, input_data=None):
        if self.process is not None:
            raise RuntimeError('Process already started')
        if self.log_path:
            self.log_path.parent.mkdir(parents=True, exist_ok=True)
            self._log = self.log_path.open('ab')
        try:
            self.process = subprocess.Popen(self.command, cwd=self.cwd,
                env={**os.environ, **self.environment}, start_new_session=True,
                stdin=subprocess.PIPE if input_data is not None else subprocess.DEVNULL,
                stdout=self._log, stderr=subprocess.STDOUT if self._log else None)
            if input_data is not None:
                try:
                    self.process.stdin.write(input_data)
                    self.process.stdin.close()
                except BaseException:
                    self.close()
                    raise
        except BaseException:
            if self._log:
                self._log.close()
            raise
        return self

    def send_signal(self, sig):
        if self.process is not None:
            try:
                os.killpg(self.process.pid, sig)
            except ProcessLookupError:
                pass

    def close(self, *, sig=signal.SIGTERM):
        if self.process is None or self._closed:
            return
        self._closed = True
        # The child has its own group; grandchildren must not survive a parent exit.
        self.send_signal(sig)
        deadline = time.monotonic() + self.shutdown_timeout
        while True:
            self.process.poll()
            self._reap_group()
            try:
                os.killpg(self.process.pid, 0)
            except ProcessLookupError:
                break
            if time.monotonic() >= deadline:
                self.forced = True
                self.send_signal(signal.SIGKILL)
                break
            time.sleep(.05)
        self.process.wait(timeout=5)
        # SIGKILL is asynchronous. Collect adopted native helpers before reporting cleanup.
        finish = time.monotonic() + 5
        while time.monotonic() < finish:
            self._reap_group()
            try:
                os.killpg(self.process.pid, 0)
            except ProcessLookupError:
                break
            time.sleep(.05)
        if self._log:
            self._log.close()

    def _reap_group(self):
        while True:
            try:
                pid, status = os.waitpid(-self.process.pid, os.WNOHANG)
            except ChildProcessError:
                return
            if not pid:
                return
            if pid == self.process.pid:
                self.process.returncode = os.waitstatus_to_exitcode(status)

    def wait_ready(self, url, expected, timeout):
        def alive():
            if self.process.poll() is not None:
                raise RuntimeError(f'Service exited {self.process.returncode}; see {self.log_path}')
        return wait_for_service(url, expected, timeout, check_alive=alive)

    def wait(self):
        return self.process.wait()


def wait_for_service(url, expected, timeout, *, check_alive=None, contract=None):
    """Read-only readiness check; never reset an episode or own an external process."""
    deadline = time.monotonic() + timeout
    last_error = None
    with httpx.Client(trust_env=False, timeout=2) as client:
        while time.monotonic() < deadline:
            if check_alive:
                check_alive()
            try:
                response = client.get(url.rstrip('/') + '/info', timeout=min(2, max(.01, deadline-time.monotonic())))
                response.raise_for_status()
                info = response.json()
                check_identity(info, expected)
                if contract:
                    contract.model_validate(info)
                return info
            except (httpx.TransportError, httpx.HTTPStatusError) as exc:
                last_error = exc
            time.sleep(min(.2, max(0, deadline - time.monotonic())))
    raise TimeoutError(f'Service readiness timed out: {url}') from last_error
