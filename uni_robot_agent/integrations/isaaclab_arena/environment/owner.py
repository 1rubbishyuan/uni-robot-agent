"""Marshal Arena operations onto the main thread required by Isaac Kit."""
from concurrent.futures import Future
from queue import Empty, Queue
import threading


class NativeOwner:
    def __init__(self, backend):
        self.backend, self.description = backend, backend.description
        self.thread = threading.get_ident()
        self.queue = Queue()

    def __getattr__(self, name):
        operation = getattr(self.backend, name)
        def call(*args, **kwargs):
            if threading.get_ident() == self.thread:
                return operation(*args, **kwargs)
            result = Future()
            self.queue.put((operation, args, kwargs, result))
            return result.result()
        return call

    def pump(self):
        if threading.get_ident() != self.thread:
            raise RuntimeError("Only the Arena main thread may execute native operations")
        try:
            operation, args, kwargs, result = self.queue.get(timeout=.1)
        except Empty:
            return
        try:
            result.set_result(operation(*args, **kwargs))
        except BaseException as error:
            result.set_exception(error)


def serve_native(owner, service, server):
    """Keep servicing accepted work while HTTP and service shutdown complete."""
    errors = []
    def start():
        try:
            server.start()
        except BaseException as error:
            errors.append(error)
    host = threading.Thread(target=start, name="arena-http")
    host.start()
    try:
        while host.is_alive() and owner.backend.app.is_running():
            owner.pump()
    finally:
        server.request_stop()
        while host.is_alive():
            owner.pump()
        host.join()
        def close():
            try:
                service.close()
            except BaseException as error:
                errors.append(error)
        closer = threading.Thread(target=close, name="arena-shutdown")
        closer.start()
        while closer.is_alive():
            owner.pump()
        closer.join()
    if errors:
        raise RuntimeError("Arena HTTP service failed") from errors[0]
