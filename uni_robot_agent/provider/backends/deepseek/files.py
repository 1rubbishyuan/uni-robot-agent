"""Upload original images once per client/credential; retain local replay sources."""

from __future__ import annotations

import base64
import hashlib
import os
import time
from concurrent.futures import ThreadPoolExecutor
from threading import Lock

from uni_robot_agent.services.http import HttpTransport
from uni_robot_agent.telemetry.logging import get_logger

logger = get_logger("provider.backends.deepseek.files")
_FORMATS = {"image/png": "png", "image/jpeg": "jpg", "image/gif": "gif", "image/webp": "webp"}
_LIFETIME_S = 86400


class DeepSeekFiles:
    def __init__(self, transport: HttpTransport, base_url: str) -> None:
        self._transport = transport
        self._url = base_url.rstrip("/").removesuffix("/v1") + "/files"
        self._cache: dict[tuple[str, str], dict] = {}
        self._owned: list[tuple[str, dict]] = []
        self._lock = Lock()
        self._closed = False
        broker_url = os.environ.get("UNI_ROBOT_AGENT_DEEPSEEK_FILES_BROKER_URL")
        state_dir = os.environ.get("UNI_ROBOT_AGENT_DEEPSEEK_FILES_STATE_DIR")
        if broker_url and not state_dir:
            raise ValueError("Set UNI_ROBOT_AGENT_DEEPSEEK_FILES_STATE_DIR when using a Files broker")
        self._broker = _BrokerFiles(transport, broker_url, state_dir) if broker_url else None

    def prepare_request(self, body: dict, headers: dict, exchange: dict) -> None:
        if self._broker is not None:
            with self._lock:
                if self._closed:
                    raise RuntimeError("DeepSeek Files session is closed")
                self._broker.prepare_request(body, headers, exchange)
            return
        authorization = next((v for k, v in headers.items() if k.lower() == "authorization"), "")
        records = exchange.setdefault("provider_files", [])
        # Serialize preparation, including uploads, to deduplicate simultaneous turns.
        with self._lock:
            if self._closed:
                raise RuntimeError("DeepSeek Files session is closed")
            for message in body["messages"]:
                content = message.get("content")
                if not isinstance(content, list):
                    continue
                for index, part in enumerate(content):
                    if part.get("type") != "image_url":
                        continue
                    image = part["image_url"]
                    url = image["url"]
                    # File references ignore detail; preserve explicit low-resolution inputs.
                    if not url.startswith("data:") or image.get("detail") == "low":
                        continue
                    content[index] = {
                        "type": "file", "file_id": self._file_id(url, authorization, records),
                    }

    def _file_id(self, url: str, authorization: str, records: list[dict]) -> str:
        if not authorization:
            raise ValueError("DeepSeek image uploads require Authorization")
        prefix, payload = url.split(",", 1)
        media_type = prefix.removeprefix("data:").removesuffix(";base64")
        if media_type not in _FORMATS or not prefix.endswith(";base64"):
            raise ValueError("DeepSeek Files requires base64 JPEG, PNG, GIF or WebP")
        data = base64.b64decode(payload, validate=True)
        if len(data) > 64 * 1024 * 1024:
            raise ValueError("DeepSeek image exceeds the 64 MiB upload limit")
        digest = hashlib.sha256(data).hexdigest()
        key = (authorization, digest)
        stored = self._cache.get(key)
        # Leave an hour for generation retries; older IDs stay owned until close.
        reused = stored is not None and stored["expires_at"] > time.time() + 3600
        record = {"sha256": digest, "bytes": len(data), "reused": reused}
        records.append(record)
        if not reused:
            try:
                result = self._transport.request(
                    "POST", self._url, headers={"Authorization": authorization},
                    data={"purpose": "user_data", "expires_after[anchor]": "created_at",
                          "expires_after[seconds]": str(_LIFETIME_S)},
                    files={"file": (digest + "." + _FORMATS[media_type], data, media_type)},
                ).json()
                stored = {"file_id": result["id"], "expires_at": result["expires_at"]}
                self._owned.append((authorization, stored))
                self._cache[key] = stored
            except Exception as exc:
                record["error"] = f"{type(exc).__name__}: {exc}"
                raise
        record.update(stored)
        return stored["file_id"]

    def finish_request(self, exchange: dict | None) -> None:
        if self._broker is not None:
            self._broker.finish_request(exchange)

    def close(self) -> None:
        if self._broker is not None:
            with self._lock:
                self._closed = True
            self._broker.close()
            return
        with self._lock:
            if self._closed:
                return
            self._closed = True
            owned, self._owned = self._owned, []
            self._cache.clear()
        if not owned:
            return
        # Bound best-effort cleanup so server shutdown is not held by an unavailable API.
        # Expiration covers interrupted processes, upload timeouts and failed deletions.
        deadline = time.monotonic() + 3

        def delete(item):
            authorization, stored = item
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                return False
            try:
                response = self._transport.request(
                    "DELETE", self._url + "/" + stored["file_id"],
                    headers={"Authorization": authorization}, check=False,
                    timeout_s=min(1, remaining),
                )
                return response.status_code == 404 or (
                    response.is_success and response.json().get("deleted") is True
                )
            except Exception:
                return False

        with ThreadPoolExecutor(max_workers=16) as pool:
            deleted = sum(pool.map(delete, owned))
        if deleted != len(owned):
            logger.warning("DeepSeek Files cleanup: %d/%d deleted; remaining files expire automatically",
                           deleted, len(owned))


class _BrokerFiles:
    """Lease request images while the provider can still read them."""

    def __init__(self, transport, url, state_dir):
        import uuid
        from pathlib import Path
        self.transport, self.url = transport, url.rstrip("/")
        self.state_dir = Path(state_dir)
        self.session = uuid.uuid4().hex
        self.requests = {}
        self.authorization = None

    def _post(self, path, body, authorization, *, timeout_s=30):
        import json
        return self.transport.request("POST", self.url + path,
            headers={"Authorization": authorization, "Content-Type": "application/json"},
            content=json.dumps(body, separators=(",", ":")).encode(),
            timeout_s=timeout_s).json()

    def prepare_request(self, body, headers, exchange):
        import os
        import uuid
        authorization = next((v for k, v in headers.items() if k.lower() == "authorization"), "")
        self.authorization = authorization
        parts, images = [], {}
        for message in body["messages"]:
            content = message.get("content")
            if not isinstance(content, list):
                continue
            for index, part in enumerate(content):
                if part.get("type") != "image_url":
                    continue
                image = part["image_url"]
                url = image["url"]
                if not url.startswith("data:") or image.get("detail") == "low":
                    continue
                prefix, payload = url.split(",", 1)
                media_type = prefix.removeprefix("data:").removesuffix(";base64")
                data = base64.b64decode(payload, validate=True)
                if media_type not in _FORMATS or not prefix.endswith(";base64"):
                    raise ValueError("DeepSeek Files requires base64 JPEG, PNG, GIF or WebP")
                if len(data) > 64 * 1024 * 1024:
                    raise ValueError("DeepSeek image exceeds the 64 MiB upload limit")
                digest = hashlib.sha256(data).hexdigest()
                images[digest] = {"sha256": digest, "bytes": len(data), "media_type": media_type,
                                  "data_url": url}
                parts.append((content, index, digest))
        if not images:
            return
        request_id = uuid.uuid4().hex
        exchange["provider_files_lease"] = request_id
        self.requests[request_id] = authorization
        try:
            reservation = self._post("/acquire", {
                "request_id": request_id, "session": self.session,
                "images": [{k: x[k] for k in ["sha256", "bytes", "media_type"]}
                           for x in images.values()],
                "owner": {"pod": os.environ.get("HOSTNAME"), "pid": os.getpid()},
            }, authorization, timeout_s=10800)
            exchange["provider_files_wait_s"] = reservation.get("wait_s", 0)
            stored = reservation["files"]

            def ensure(item):
                digest, image = item
                record = stored[digest]
                if record["file_id"]:
                    return digest, {**record, "reused": True}
                result = self._post("/upload", {"request_id": request_id,
                    "digest": digest, "data_url": image["data_url"]}, authorization, timeout_s=600)
                return digest, result

            with ThreadPoolExecutor(max_workers=8) as pool:
                records = dict(pool.map(ensure, images.items()))
            evidence = exchange.setdefault("provider_files", [])
            for content, index, digest in parts:
                record = records[digest]
                content[index] = {"type": "file", "file_id": record["file_id"]}
                evidence.append({"sha256": digest, "bytes": images[digest]["bytes"],
                                 "file_id": record["file_id"], "expires_at": record["expires_at"],
                                 "reused": record["reused"]})
        except BaseException:
            self.finish_request(exchange)
            raise

    def finish_request(self, exchange):
        import json
        import os
        if not exchange:
            return
        request_id = exchange.get("provider_files_lease")
        authorization = self.requests.pop(request_id, None)
        if not authorization:
            return
        try:
            self._post("/release", {"request_id": request_id}, authorization, timeout_s=15)
        except Exception as error:
            # A unique durable receipt lets the broker finish a failed release RPC.
            try:
                path = self.state_dir / "releases" / (request_id + ".json")
                path.parent.mkdir(parents=True, exist_ok=True)
                temporary = path.with_suffix(".json.new")
                temporary.write_text(json.dumps({"request_id": request_id}))
                os.replace(temporary, path)
            except OSError:
                logger.warning("DeepSeek image release remains protected: %s", type(error).__name__)

    def close(self):
        for request_id in list(self.requests):
            self.finish_request({"provider_files_lease": request_id})
        if self.authorization:
            try:
                self._post("/close", {"session": self.session}, self.authorization, timeout_s=15)
            except Exception as error:
                logger.warning("DeepSeek image session close deferred: %s", type(error).__name__)
