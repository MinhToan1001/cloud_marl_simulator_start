"""Offline development pipeline plus a tiny localhost-only Locust smoke test."""
import argparse
import hashlib
import json
import subprocess
import sys
import threading
from pathlib import Path

from apps.sample_api.server import make_server
from integration.calibration import calibrate
from integration.collector import collect
from integration.export import export_simulation, normalize_locust
from integration.preprocess import preprocess

ROOT = Path(__file__).resolve().parents[2]


def verify_preservation():
    manifest = json.loads((ROOT / "data/validation/preserved-files.json").read_text())
    for relative, expected in manifest.items():
        if hashlib.sha256((ROOT / relative).read_bytes()).hexdigest() != expected:
            raise RuntimeError(f"A-F preservation failed: {relative}")
    return len(manifest)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=ROOT / "data/demo/stage-g")
    args = parser.parse_args()
    output = args.output
    if output.exists() and any(output.iterdir()):
        parser.exit(2, "Use a new/empty output directory\n")
    config = json.loads((ROOT / "configs/integration/collector.example.json").read_text())
    profile = json.loads((ROOT / "configs/integration/load-safe.json").read_text())
    collection = collect(config, output / "collector", profile, ROOT / "tests/fixtures/cluster_a.json", samples=8)
    samples, rejected = preprocess(output / "collector/metrics.csv", output / "preprocessed", allow_simulated=True)
    calibration = calibrate(output / "collector/metrics.csv", output / "calibration", ROOT / "configs/simulation.json", allow_simulated=True)
    exported = export_simulation(ROOT / "results/baseline", output / "simulated-baseline.csv")
    with make_server(port=0) as server:
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            command = [sys.executable, "-m", "cli.cluster", "load", "--host", f"http://127.0.0.1:{server.server_address[1]}",
                       "--profile", str(ROOT / "configs/integration/load-smoke.json"), "--run-id", "windows-local-smoke",
                       "--source", "simulated", "--output", str((output / "load").resolve())]
            with (output / "locust-smoke.log").open("w", encoding="utf-8") as log:
                subprocess.run(command, stdout=log, stderr=subprocess.STDOUT, cwd=ROOT, timeout=45, check=True)
            app = server.work_state.metrics()
            assert app["received_total"] > 0 and app["received_total"] == app["completed_total"] + app["errors_total"] + app["inflight"]
            negative = command.copy()
            negative[negative.index("--source") + 1] = "real"
            negative[negative.index("--output") + 1] = str((output / "load-real-rejected").resolve())
            with (output / "locust-preflight-rejection.log").open("w", encoding="utf-8") as log:
                rejected_load = subprocess.run(negative, stdout=log, stderr=subprocess.STDOUT, cwd=ROOT, timeout=20)
            assert rejected_load.returncode != 0 and server.work_state.metrics()["received_total"] == app["received_total"]
        finally:
            server.shutdown()
            thread.join(timeout=3)
    normalized = normalize_locust(output / "load/client/requests.csv", output / "load/client/workload.json", output / "client-common.csv")
    evidence = {"source": "simulated", "connected_to_real_cluster": False,
                "fixture_collection_records": collection["records"], "aligned_windows": len(samples),
                "calibration_status": calibration["status"], "simulated_export_records": exported,
                "local_http_requests": app["received_total"], "local_http_successes": app["completed_total"],
                "live_load_preflight_rejects_local": True,
                "normalized_client_windows": normalized, "protected_files_unchanged": verify_preservation(),
                "note": "Fixture and localhost only; no Ubuntu/OpenStack/K3s connection attempted; no MARL training executed"}
    (output / "smoke-evidence.json").write_text(json.dumps(evidence, indent=2), encoding="utf-8")
    print(json.dumps(evidence, indent=2))


if __name__ == "__main__":
    main()
