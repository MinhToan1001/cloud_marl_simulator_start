import json
import threading
from pathlib import Path
from urllib.error import HTTPError
from urllib.request import urlopen

import pytest
import yaml

from apps.sample_api.server import WorkState, make_server
from loadtests.profile import LoadGuard, phase_at, validate_profile

ROOT = Path(__file__).resolve().parents[2]


def test_sample_app_http_and_request_conservation():
    state = WorkState(cpu_ms=0, concurrency=1)
    with make_server(port=0, state=state) as server:
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        base = f"http://127.0.0.1:{server.server_address[1]}"
        try:
            with urlopen(base + "/health") as response:
                assert response.status == 200
            with urlopen(base + "/work") as response:
                assert response.status == 200
            state.slots.acquire()
            with pytest.raises(HTTPError) as error:
                urlopen(base + "/work")
            assert error.value.code == 429
            state.slots.release()
            with urlopen(base + "/metrics") as response:
                data = json.load(response)
            assert data["received_total"] == 2
            assert data["completed_total"] == data["errors_total"] == 1
            assert data["inflight"] == 0 and len(data["events"]) == 2
        finally:
            server.shutdown()
            thread.join(timeout=2)


def test_bounded_event_retention():
    state = WorkState(cpu_ms=0, event_limit=2)
    for _ in range(5): state.work()
    data = state.metrics()
    assert len(data["events"]) == 2 and data["oldest_retained_sequence"] == 4
    assert data["received_total"] == data["completed_total"] == 5


def test_load_shape_and_guard():
    profile = validate_profile(json.loads((ROOT / "configs/integration/load-safe.json").read_text()))
    assert phase_at(profile, 0) == ("stable", 1)
    assert phase_at(profile, 59) == ("ramp_up", 3)
    assert phase_at(profile, 60) == ("spike", 6)
    assert phase_at(profile, 75) == ("recovery", 1)
    assert phase_at(profile, 120) is None
    guard = LoadGuard(profile)
    for _ in range(20): guard.record(2000, True)
    assert [guard.must_stop() for _ in range(3)] == [False, False, True]
    with pytest.raises(ValueError):
        validate_profile(dict(profile, max_users=100))


def test_manifests_are_bounded_and_consistent():
    deployment = yaml.safe_load((ROOT / "deploy/k3s/deployment.yaml").read_text())
    service = yaml.safe_load((ROOT / "deploy/k3s/service.yaml").read_text())
    spec = deployment["spec"]["template"]["spec"]
    container = spec["containers"][0]
    assert deployment["spec"]["replicas"] == 1
    assert spec["automountServiceAccountToken"] is False
    assert container["resources"]["limits"] == {"cpu": "250m", "memory": "96Mi"}
    assert service["spec"]["type"] == "ClusterIP"
    assert service["spec"]["selector"] == deployment["spec"]["selector"]["matchLabels"]
    for resource in yaml.safe_load_all((ROOT / "deploy/k3s/collector-rbac.yaml").read_text()):
        for rule in resource.get("rules", []):
            assert set(rule["verbs"]) <= {"get", "list"}
