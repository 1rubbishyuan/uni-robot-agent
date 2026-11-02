"""Independent stop receipts: never queue cancellation behind a motion lock."""

import threading
from .contracts import RequestRejected


class Interrupts:
    def __init__(self):
        self.lock = threading.RLock()
        self.active = None
        self.cancel = None
        self.receipts = {}

    def begin(self, identity, device):
        with self.lock:
            self.active = (identity.request_id, device)
            self.cancel = threading.Event()
            return self.cancel

    def end(self):
        with self.lock:
            self.active = None
            self.cancel = None

    def execute(self, request, backend):
        with self.lock:
            if request.request_id in self.receipts:
                previous = self.receipts[request.request_id]
                if previous["request"] != request.model_dump(mode="json"):
                    raise RequestRejected("Interrupt identity was reused with different arguments")
                return dict(previous)
            if request.target_request_id is not None and self.active != (
                request.target_request_id,
                request.device,
            ):
                raise RequestRejected("Interrupt target is not the active device operation")
            result = {
                "request_id": request.request_id,
                "device": request.device,
                "target_request_id": None if self.active is None else self.active[0],
                "status": "running",
                "request": request.model_dump(mode="json"),
            }
            self.receipts[request.request_id] = result
            if self.active is not None and self.active[1] == request.device:
                self.cancel.set()
            try:
                result.update(status="completed", feedback=backend.interrupt_device(request.device))
            except Exception as error:
                result.update(status="unknown", error=str(error))
            return dict(result)
