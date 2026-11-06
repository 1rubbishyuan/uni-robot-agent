"""Keep native graphics and physics resources on their creation thread."""
from concurrent.futures import ThreadPoolExecutor


class OwnedAdapter:
    def __init__(self, factory):
        self.pool = ThreadPoolExecutor(max_workers=1, thread_name_prefix="environment-owner")
        self.closed = False
        try:
            self.backend = self.pool.submit(factory).result()
            self.description = self.backend.description
        except BaseException:
            self.pool.shutdown()
            raise

    def __getattr__(self, name):
        method = getattr(self.backend, name)
        return lambda *args, **kwargs: self.pool.submit(method, *args, **kwargs).result()

    def close(self):
        if self.closed:
            return
        self.closed = True
        try:
            self.pool.submit(self.backend.close).result()
        finally:
            self.pool.shutdown()
