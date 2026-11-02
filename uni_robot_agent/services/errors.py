"""HTTP failures retain wire evidence; callers decide execution semantics."""

class HttpCallError(RuntimeError):
    def __init__(self, response):
        super().__init__(f"HTTP {response.status_code}: {response.text[:300]}")
        self.response = response
        self.status = response.status_code
        self.content = response.content
        self.headers = response.headers


class ServiceError(RuntimeError):
    def __init__(self, code, message, *, status=409, details=None):
        super().__init__(message)
        self.code, self.status = code, status
        self.details = details or {}

    def payload(self):
        return {"error": {"code": self.code, "message": str(self), "details": self.details}}
