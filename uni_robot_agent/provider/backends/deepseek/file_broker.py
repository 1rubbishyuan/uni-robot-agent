"""Bounded, recoverable DeepSeek image cache shared by planner requests."""
from __future__ import annotations

import argparse
import base64
import hashlib
import hmac
import json
import os
import socket
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path


class FileBroker:
    def __init__(self, state_dir, transport, api_url, authorization, *, capacity=8000,
                 namespace="uni-robot-agent", idle_seconds=60):
        self.root = Path(state_dir)
        self.root.mkdir(parents=True, exist_ok=True)
        (self.root / "releases").mkdir(exist_ok=True)
        self.transport, self.api_url = transport, api_url.rstrip("/")
        self.authorization, self.capacity = authorization, capacity
        self.namespace, self.idle_seconds = namespace, idle_seconds
        self.condition = threading.Condition(threading.RLock())
        self.entries, self.leases, self.released = {}, {}, set()
        self.uploading, self.deleting = set(), set()
        self.upload_locks = {}
        self.network = threading.BoundedSemaphore(16)
        self.last_reconcile, self.account_total = 0, None
        self.counters = {"uploads": 0, "deletes": 0, "acquire_waits": 0,
                         "upload_errors": 0, "delete_errors": 0, "orphans_deleted": 0}
        journal = self.root / "journal.jsonl"
        if journal.exists():
            lines = journal.read_bytes().splitlines(keepends=True)
            offset = 0
            for index, line in enumerate(lines):
                try:
                    event = json.loads(line)
                except ValueError:
                    if index != len(lines) - 1:
                        raise
                    with journal.open("r+b") as stream:
                        stream.truncate(offset)
                    break
                self._apply(event)
                offset += len(line)
        self.journal = journal.open("a", buffering=1)

    def _apply(self, event):
        kind = event["kind"]
        if kind == "acquire":
            self.leases[event["request_id"]] = event["lease"]
            for image in event["images"]:
                self.entries.setdefault(image["sha256"], {**image, "file_id": None,
                    "expires_at": 0, "last_used": event["at"]})
        elif kind == "uploaded":
            self.entries[event["sha256"]] = event["entry"]
        elif kind == "release":
            self.released.add(event["request_id"])
            lease = self.leases.pop(event["request_id"], None)
            if lease:
                for digest in lease["digests"]:
                    if digest in self.entries:
                        self.entries[digest]["last_used"] = event["at"]
        elif kind == "deleted":
            self.entries.pop(event["sha256"], None)

    def _record(self, kind, **fields):
        event = {"kind": kind, "at": time.time(), **fields}
        self.journal.write(json.dumps(event, separators=(",", ":")) + "\n")
        self.journal.flush()
        os.fsync(self.journal.fileno())
        self._apply(event)

    def _referenced(self):
        return {digest for lease in self.leases.values() for digest in lease["digests"]}

    def acquire(self, request_id, session, images, owner=None, disconnected=lambda: False):
        unique = {x["sha256"]: {k: x[k] for k in ["sha256", "bytes", "media_type"]}
                  for x in images}
        if len(unique) > self.capacity:
            raise ValueError("One request exceeds the complete cloud image budget")
        started = time.monotonic()
        counted = False
        while True:
            if disconnected():
                raise ConnectionError("Image reservation caller disconnected")
            with self.condition:
                if request_id in self.released:
                    raise ValueError("Image request already released")
                if request_id in self.leases:
                    return {"request_id": request_id, "files": self._lease_files(request_id)}
                missing = set(unique) - self.entries.keys()
                blocked = set(unique) & self.deleting
                if not blocked and len(self.entries) + len(missing) <= self.capacity:
                    lease = {"session": session, "digests": list(unique), "owner": owner or {},
                             "started": time.time()}
                    self._record("acquire", request_id=request_id, lease=lease,
                                 images=list(unique.values()))
                    return {"request_id": request_id, "files": self._lease_files(request_id),
                            "wait_s": round(time.monotonic() - started, 3)}
                if not counted:
                    self.counters["acquire_waits"] += 1
                    counted = True
                live = self._referenced() | self.uploading | self.deleting | set(unique)
                candidates = sorted((k for k in self.entries if k not in live),
                                    key=lambda k: self.entries[k]["last_used"])
                take = max(1, len(self.entries) + len(missing) - self.capacity)
            if candidates:
                if not self.delete(candidates[:take]):
                    with self.condition:
                        self.condition.wait(timeout=1)
            else:
                with self.condition:
                    self.condition.wait(timeout=1)

    def _lease_files(self, request_id):
        return {digest: {k: self.entries[digest].get(k) for k in
                ["file_id", "expires_at", "bytes", "media_type"]}
                for digest in self.leases[request_id]["digests"]}

    def upload(self, request_id, digest, data_url):
        prefix, payload = data_url.split(",", 1)
        media_type = prefix.removeprefix("data:").removesuffix(";base64")
        data = base64.b64decode(payload, validate=True)
        if not prefix.endswith(";base64") or hashlib.sha256(data).hexdigest() != digest:
            raise ValueError("Image content does not match its reserved SHA256")
        suffix = {"image/png": "png", "image/jpeg": "jpg", "image/webp": "webp",
                  "image/gif": "gif"}[media_type]
        with self.condition:
            lock = self.upload_locks.setdefault(digest, threading.Lock())
        with lock:
            with self.condition:
                lease = self.leases.get(request_id)
                if not lease or digest not in lease["digests"]:
                    raise ValueError("Image upload requires a live request reservation")
                entry = self.entries[digest]
                if entry["bytes"] != len(data) or entry["media_type"] != media_type:
                    raise ValueError("Image reservation metadata mismatch")
                if entry["file_id"] and entry["expires_at"] > time.time() + 3600:
                    return {**entry, "reused": True}
                if entry["file_id"]:
                    raise ValueError("Referenced cloud image needs expiry renewal")
                self.uploading.add(digest)
            try:
                with self.network:
                    response = self.transport.request("POST", self.api_url,
                        headers={"Authorization": self.authorization},
                        data={"purpose": "user_data", "expires_after[anchor]": "created_at",
                              "expires_after[seconds]": "86400"},
                        files={"file": (f"{self.namespace}-{digest}.{suffix}", data, media_type)})
                result = response.json()
                with self.condition:
                    stored = {"sha256": digest, "bytes": len(data), "media_type": media_type,
                              "file_id": result["id"], "expires_at": result["expires_at"],
                              "last_used": time.time()}
                    self._record("uploaded", sha256=digest, entry=stored)
                    self.counters["uploads"] += 1
                    return {**stored, "reused": False}
            except Exception:
                self.counters["upload_errors"] += 1
                raise
            finally:
                with self.condition:
                    self.uploading.discard(digest)
                    self.condition.notify_all()

    def release(self, request_id):
        with self.condition:
            if request_id not in self.released:
                self._record("release", request_id=request_id)
            self.condition.notify_all()
        return {"released": True}

    def close_session(self, session):
        with self.condition:
            ids = [k for k, v in self.leases.items() if v["session"] == session]
        for request_id in ids:
            self.release(request_id)
        return {"closed": True}

    def delete(self, digests):
        def one(digest):
            with self.condition:
                if digest not in self.entries or digest in self._referenced() or digest in self.uploading or digest in self.deleting:
                    return False
                self.deleting.add(digest)
                entry = dict(self.entries[digest])
            try:
                if entry["file_id"]:
                    with self.network:
                        response = self.transport.request("DELETE", self.api_url + "/" + entry["file_id"],
                            headers={"Authorization": self.authorization}, check=False, timeout_s=15)
                    if response.status_code != 404 and not (response.is_success and response.json().get("deleted")):
                        self.counters["delete_errors"] += 1
                        return False
                with self.condition:
                    self._record("deleted", sha256=digest)
                    self.counters["deletes"] += bool(entry["file_id"])
                    self.condition.notify_all()
                return True
            except Exception:
                self.counters["delete_errors"] += 1
                return False
            finally:
                with self.condition:
                    self.deleting.discard(digest)
                    self.condition.notify_all()
        with ThreadPoolExecutor(max_workers=16) as executor:
            return sum(executor.map(one, digests))

    def sweep(self):
        for path in (self.root / "releases").glob("*.json"):
            value = json.loads(path.read_text())
            self.release(value["request_id"])
            path.unlink(missing_ok=True)
        with self.condition:
            live = self._referenced() | self.uploading | self.deleting
            expired = [k for k, v in self.entries.items() if k not in live and
                       time.time() - v["last_used"] >= self.idle_seconds]
        self.delete(expired)
        self.write_status()

    def reconcile(self):
        # A unique filename namespace proves ownership even if POST timed out before its ID arrived.
        files, after = [], None
        for _ in range(30):
            url = self.api_url + "?limit=1000" + ("&after=" + after if after else "")
            response = self.transport.request("GET", url, headers={"Authorization": self.authorization})
            value = response.json()
            files.extend(value["data"])
            if not value.get("has_more"):
                break
            after = value.get("last_id") or value["data"][-1]["id"]
        else:
            raise RuntimeError("Cloud file inventory incomplete")
        with self.condition:
            known = {v["file_id"] for v in self.entries.values() if v["file_id"]}
            uploading = set(self.uploading)
        for row in files:
            name = row.get("filename", "")
            if not name.startswith(self.namespace + "-") or row["id"] in known:
                continue
            digest = name[len(self.namespace) + 1:].split(".", 1)[0]
            if digest in uploading:
                continue
            with self.network:
                response = self.transport.request("DELETE", self.api_url + "/" + row["id"],
                    headers={"Authorization": self.authorization}, check=False, timeout_s=15)
            if response.status_code == 404 or (response.is_success and response.json().get("deleted")):
                self.counters["orphans_deleted"] += 1
        self.account_total, self.last_reconcile = len(files), time.time()
        self.write_status()

    def status(self):
        with self.condition:
            return {"updated": time.time(), "capacity": self.capacity,
                    "cache_entries": len(self.entries),
                    "cloud_files": sum(bool(v["file_id"]) for v in self.entries.values()),
                    "leased_images": len(self._referenced()), "active_requests": len(self.leases),
                    "uploading": len(self.uploading), "deleting": len(self.deleting),
                    "account_total_last_listing": self.account_total,
                    "last_cloud_listing": self.last_reconcile, **self.counters}

    def write_status(self):
        path = self.root / "status.json"
        # HTTP workers never write this file; only the maintenance thread publishes status.
        if threading.current_thread().name == "files-maintenance":
            tmp = path.with_suffix(".json.new")
            tmp.write_text(json.dumps(self.status(), indent=2))
            os.replace(tmp, path)


def serve(broker, host, port):
    class Handler(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"

        def log_message(self, *args):
            pass

        def reply(self, status, value):
            data = json.dumps(value).encode()
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        def do_GET(self):
            self.reply(200 if self.path == "/info" else 404, broker.status())

        def disconnected(self):
            try:
                return self.connection.recv(1, socket.MSG_PEEK | socket.MSG_DONTWAIT) == b""
            except BlockingIOError:
                return False

        def do_POST(self):
            if not hmac.compare_digest(self.headers.get("Authorization", ""), broker.authorization):
                self.reply(401, {"error": "Unauthorized"})
                return
            try:
                value = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
                if self.path == "/acquire":
                    result = broker.acquire(**value, disconnected=self.disconnected)
                elif self.path == "/upload":
                    result = broker.upload(**value)
                elif self.path == "/release":
                    result = broker.release(**value)
                elif self.path == "/close":
                    result = broker.close_session(**value)
                else:
                    self.reply(404, {"error": "Unknown operation"})
                    return
                self.reply(200, result)
            except (ConnectionError, BrokenPipeError):
                return
            except Exception as error:
                # Provider exception bodies can contain authentication material.
                self.reply(503, {"error_type": type(error).__name__})

    def maintenance():
        while True:
            try:
                broker.sweep()
                if time.time() - broker.last_reconcile >= 60:
                    broker.reconcile()
            except Exception as error:
                print(json.dumps({"maintenance_error": type(error).__name__}), flush=True)
            time.sleep(5)
    threading.Thread(target=maintenance, name="files-maintenance", daemon=True).start()
    ThreadingHTTPServer((host, port), Handler).serve_forever()


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--state-dir", required=True)
    parser.add_argument("--namespace", required=True)
    parser.add_argument("--capacity", type=int, default=8000)
    parser.add_argument("--host", default="0.0.0.0")
    parser.add_argument("--port", type=int, default=19118)
    args = parser.parse_args()
    api_key = os.environ.get("UNI_ROBOT_AGENT_API_KEY")
    if not api_key:
        parser.error("Set UNI_ROBOT_AGENT_API_KEY before starting the Files broker")
    from uni_robot_agent.services.http import HttpTransport
    transport = HttpTransport(timeout_s=600, max_connections=64)
    broker = FileBroker(args.state_dir, transport, "https://api.deepseek.com/files",
                        "Bearer " + api_key,
                        capacity=args.capacity, namespace=args.namespace)
    serve(broker, args.host, args.port)


if __name__ == "__main__":
    main()
