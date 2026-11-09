"""Start a temporary Uvicorn process, verify real localhost HTTP, then stop it."""
from __future__ import annotations

import argparse
import json
import socket
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from urllib.error import URLError
from urllib.request import urlopen

ROOT = Path(__file__).resolve().parents[2]


def verify(results: Path, output: Path):
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        port = listener.getsockname()[1]
    log_path = output.with_suffix(".log")
    log_path.parent.mkdir(parents=True, exist_ok=True)
    with log_path.open("w", encoding="utf-8") as log:
        process = subprocess.Popen(
            [sys.executable, "-m", "cli.serve", "--results", str(results.resolve()), "--port", str(port)],
            stdout=log, stderr=subprocess.STDOUT, cwd=ROOT,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        responses = []
        try:
            base = f"http://127.0.0.1:{port}"
            for attempt in range(100):
                if process.poll() is not None:
                    raise RuntimeError(f"Uvicorn exited with {process.returncode}; see {log_path}")
                try:
                    with urlopen(base + "/api/simulation/status", timeout=1) as response:
                        if response.status == 200:
                            break
                except (URLError, TimeoutError):
                    time.sleep(0.1)
            else:
                raise RuntimeError("Local HTTP API startup timed out")
            for endpoint in ("status", "clusters", "metrics?limit=2", "comparison?scenario=mixed"):
                with urlopen(base + "/api/simulation/" + endpoint, timeout=10) as response:
                    data = json.load(response)
                    if response.status != 200 or data.get("data_source") != "simulation":
                        raise RuntimeError(f"Invalid response at {endpoint}")
                    if endpoint == "status" and not data["ready"]:
                        raise RuntimeError("Dataset is not ready")
                    responses.append({"endpoint": endpoint, "http_status": response.status,
                                      "data_source": data["data_source"],
                                      "is_simulation": data["is_simulation"],
                                      "rows": len(data.get("rows", data.get("clusters", [])))})
            with urlopen(base + "/openapi.json", timeout=10) as response:
                schema = json.load(response)
                if len(schema["paths"]) != 4:
                    raise RuntimeError("OpenAPI must expose all four API endpoints")
            payload = {"data_source": "simulation", "transport": "real localhost HTTP via Uvicorn",
                       "created_at_utc": datetime.now(timezone.utc).isoformat(), "results": str(results.resolve()),
                       "checks": responses, "openapi_paths": len(schema["paths"])}
            output.write_text(json.dumps(payload, indent=2), encoding="utf-8")
            print(f"Verified all four API endpoints and OpenAPI over HTTP; stopped temporary server. {output}")
        finally:
            process.terminate()
            process.wait(timeout=10)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--results", type=Path, default=ROOT / "results/evaluation")
    parser.add_argument("--output", type=Path, default=ROOT / "data/validation/api.json")
    args = parser.parse_args()
    verify(args.results, args.output)


if __name__ == "__main__":
    main()
