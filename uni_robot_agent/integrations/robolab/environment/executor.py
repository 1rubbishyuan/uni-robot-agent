"""Dispatch native work to the main thread that owns Isaac Kit."""
from concurrent.futures import Future
from queue import Empty, Queue
import threading


class MainThreadExecutor:
    def __init__(self):
        if threading.current_thread() is not threading.main_thread():
            raise RuntimeError("Isaac native ownership must be established on the main thread")
        self.owner = threading.get_ident()
        self.queue = Queue()
        self.lock = threading.Lock()
        self.closed = False

    def run(self, operation):
        with self.lock:
            if self.closed:
                raise RuntimeError("Native executor is closed")
            own_thread = threading.get_ident() == self.owner
            if not own_thread:
                future = Future()
                self.queue.put((future, operation))
        return operation() if own_thread else future.result()

    def pump(self, timeout=.05):
        if threading.get_ident() != self.owner:
            raise RuntimeError("Only the native owner may pump Isaac work")
        try:
            future, operation = self.queue.get(timeout=timeout)
        except Empty:
            return
        if future.set_running_or_notify_cancel():
            try:
                future.set_result(operation())
            except BaseException as error:
                future.set_exception(error)

    def close(self):
        with self.lock:
            self.closed = True
            while True:
                try:
                    future, _ = self.queue.get_nowait()
                except Empty:
                    break
                future.set_exception(RuntimeError("Native executor stopped before execution"))
