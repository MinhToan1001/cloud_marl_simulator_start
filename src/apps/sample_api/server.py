"""Dependency-free, bounded reference workload with auditable request counters."""
from __future__ import annotations

import hashlib
import json
import math
import os
import threading
import time
import uuid
from collections import deque
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlsplit


def now():
    return datetime.now(timezone.utc).isoformat()


class WorkState:
    def __init__(self, cpu_ms=5, memory_kib=64, concurrency=2, event_limit=4096):
        if not 0 <= cpu_ms <= 25 or not 0 <= memory_kib <= 1024 or not 1 <= concurrency <= 4:
            raise ValueError("Work bounds: CPU 0..25ms, memory 0..1024KiB, concurrency 1..4")
        self.config = {"cpu_ms": cpu_ms, "memory_kib": memory_kib, "max_concurrency": concurrency}
        self.lock = threading.Lock()
        self.slots = threading.BoundedSemaphore(concurrency)
        self.events = deque(maxlen=event_limit)
        self.start = time.monotonic()
        self.instance_id = str(uuid.uuid4())
        self.received = self.completed = self.errors = self.inflight = self.sequence = 0
        self.latency_sum_ms = 0.0

    def work(self):
        start = time.perf_counter()
        with self.lock:
            self.received += 1
            self.inflight += 1
        accepted = self.slots.acquire(blocking=False)
        success = False
        try:
            if accepted:
                memory = bytearray(self.config["memory_kib"] * 1024)
                # Thread CPU time, not a sleep: cgroup CPU throttling is observable.
                deadline = time.thread_time() + self.config["cpu_ms"] / 1000
                value = b"calibration"
                while time.thread_time() < deadline:
                    value = hashlib.sha256(value).digest()
                if memory:
                    memory[0] = value[0]
                success = True
            return 200 if success else 429
        finally:
            elapsed = (time.perf_counter() - start) * 1000
            if accepted:
                self.slots.release()
            with self.lock:
                self.inflight -= 1
                self.completed += int(success)
                self.errors += int(not success)
                self.latency_sum_ms += elapsed if success else 0
                self.sequence += 1
                self.events.append({"sequence": self.sequence, "success": success, "latency_ms": elapsed})

    def metrics(self, after=0):
        with self.lock:
            return {"schema_version": 1, "instance_id": self.instance_id,
                    "namespace": os.environ.get("POD_NAMESPACE", "local"),
                    "pod_name": os.environ.get("POD_NAME", "local-process"),
                    "timestamp_utc": now(), "uptime_s": time.monotonic() - self.start,
                    "received_total": self.received, "completed_total": self.completed,
                    "errors_total": self.errors, "inflight": self.inflight,
                    "latency_sum_ms": self.latency_sum_ms, "sequence": self.sequence,
                    "oldest_retained_sequence": self.events[0]["sequence"] if self.events else self.sequence + 1,
                    "events": [e for e in self.events if e["sequence"] > after], "work_config": self.config}


def make_server(host="127.0.0.1", port=8080, state=None):
    state = state or WorkState(float(os.environ.get("WORK_CPU_MS", "5")), int(os.environ.get("WORK_MEMORY_KIB", "64")))

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            path = urlsplit(self.path)
            if path.path == "/health":
                status, data = 200, {"status": "ok"}
            elif path.path == "/metrics":
                try:
                    after = int(parse_qs(path.query).get("after", [0])[0])
                    if after < 0:
                        raise ValueError()
                    status, data = 200, state.metrics(after)
                except ValueError:
                    status, data = 400, {"error": "after must be nonnegative integer"}
            elif path.path == "/work" and not path.query:
                status = state.work()
                data = {"status": "completed" if status == 200 else "busy"}
            else:
                status, data = 404, {"error": "unknown endpoint; work settings are configured via environment"}
            content = json.dumps(data, allow_nan=False).encode()
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(content)))
            self.end_headers()
            try:
                self.wfile.write(content)
            except (BrokenPipeError, ConnectionResetError):
                pass  # Completed means server work; client delivery is measured by Locust.

        def log_message(self, *args):
            pass

    server = ThreadingHTTPServer((host, port), Handler)
    server.daemon_threads = True
    server.work_state = state
    return server


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8080)
    args = parser.parse_args()
    with make_server(args.host, args.port) as server:
        server.serve_forever()
