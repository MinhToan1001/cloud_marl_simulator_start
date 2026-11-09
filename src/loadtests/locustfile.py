"""Single-process, bounded test. Configure CALIBRATION_PROFILE and RUN_ID."""
import csv
import json
import os
import time
from pathlib import Path

from locust import HttpUser, LoadTestShape, between, events, task

from integration.schema import MetricRecord, digest, utc_now, write_records
from loadtests.profile import LoadGuard, phase_at, validate_profile

ROOT = Path(__file__).resolve().parents[2]
PROFILE = validate_profile(json.loads(Path(os.environ.get("CALIBRATION_PROFILE", ROOT / "configs/integration/load-safe.json")).read_text()))
SOURCE = os.environ.get("CALIBRATION_SOURCE", "simulated")
RUN_ID = os.environ.get("CALIBRATION_RUN_ID", "local-load-test")
OUTPUT = Path(os.environ.get("CALIBRATION_OUTPUT_DIR", ROOT / "data/runs" / RUN_ID / "client"))
GUARD = LoadGuard(PROFILE)
ENVIRONMENT = None
START = 0.0
FILE = None
WRITER = None


class CalibrationUser(HttpUser):
    wait_time = between(PROFILE["wait_min_s"], PROFILE["wait_max_s"])

    @task
    def work(self):
        self.client.get("/work", name="/work", timeout=2)


class CalibrationShape(LoadTestShape):
    def tick(self):
        point = phase_at(PROFILE, self.get_run_time())
        if point is None or GUARD.must_stop():
            return None
        return point[1], PROFILE["spawn_rate"]


@events.test_start.add_listener
def start(environment, **kwargs):
    global ENVIRONMENT, START, FILE, WRITER
    from integration.collector import http_json
    host = environment.host or environment.parsed_options.host
    try:
        if SOURCE not in {"real", "simulated"}:
            raise ValueError("CALIBRATION_SOURCE must be real or simulated")
        identity = http_json(host.rstrip("/") + "/metrics")
        if SOURCE == "real" and (identity.get("namespace") != "cloud-calibration" or identity.get("pod_name") == "local-process"):
            raise ValueError("Real mode requires verified sample pod identity; local runs must be simulated")
        if OUTPUT.exists() and any(OUTPUT.iterdir()):
            raise ValueError("Use a fresh Locust output directory for each run")
    except (OSError, ValueError, KeyError):
        environment.process_exit_code = 2
        environment.runner.quit()
        raise
    OUTPUT.mkdir(parents=True, exist_ok=True)
    (OUTPUT / "workload.json").write_text(json.dumps({"source": SOURCE, "run_id": RUN_ID,
        "workload_sha256": digest(PROFILE), "profile": PROFILE, "app_work_config": identity.get("work_config"),
        "app_instance_id": identity["instance_id"], "started_at_utc": utc_now()}, indent=2), encoding="utf-8")
    FILE = (OUTPUT / "requests.csv").open("w", encoding="utf-8", newline="")
    WRITER = csv.DictWriter(FILE, fieldnames=["timestamp_utc", "source", "run_id", "phase", "latency_ms", "failed", "workload_sha256"])
    WRITER.writeheader()
    ENVIRONMENT, START = environment, time.monotonic()


@events.request.add_listener
def request(name, response_time, exception, **kwargs):
    if name != "/work" or WRITER is None:
        return
    GUARD.record(response_time, exception is not None)
    point = phase_at(PROFILE, time.monotonic() - START)
    WRITER.writerow({"timestamp_utc": utc_now(), "source": SOURCE, "run_id": RUN_ID,
                     "phase": point[0] if point else "finished", "latency_ms": response_time,
                     "failed": int(exception is not None), "workload_sha256": digest(PROFILE)})
    FILE.flush()


@events.test_stop.add_listener
def stop(environment, **kwargs):
    if FILE:
        FILE.close()
