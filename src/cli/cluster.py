"""Stage G: collection, preprocessing, calibration and short bounded load tests."""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from pathlib import Path

from integration.calibration import calibrate
from integration.collector import collect
from integration.export import export_simulation, normalize_locust
from integration.host import sample_host
from integration.preprocess import preprocess
from loadtests.profile import validate_profile

ROOT = Path(__file__).resolve().parents[2]


def load_json(path):
    return json.loads(Path(path).read_text(encoding="utf-8-sig"))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    p = commands.add_parser("collect")
    p.add_argument("--config", type=Path, default=ROOT / "configs/integration/collector.example.json")
    p.add_argument("--workload", type=Path, default=ROOT / "configs/integration/load-safe.json")
    p.add_argument("--output", type=Path, required=True)
    p.add_argument("--fixture", type=Path)
    p.add_argument("--samples", type=int, default=12)
    p.add_argument("--interval", type=float, default=15)
    p = commands.add_parser("preprocess")
    p.add_argument("--input", type=Path, required=True)
    p.add_argument("--output", type=Path, required=True)
    p.add_argument("--allow-simulated", action="store_true")
    p.add_argument("--max-skew", type=float, default=20)
    p = commands.add_parser("calibrate")
    p.add_argument("--input", type=Path, required=True)
    p.add_argument("--output", type=Path, required=True)
    p.add_argument("--allow-simulated", action="store_true")
    p.add_argument("--simulator-config", type=Path, default=ROOT / "configs/simulation.json")
    p.add_argument("--write-candidate", type=Path)
    p = commands.add_parser("export-simulation")
    p.add_argument("--input", type=Path, default=ROOT / "results/evaluation")
    p.add_argument("--output", type=Path, required=True)
    p = commands.add_parser("normalize-locust")
    p.add_argument("--input", type=Path, required=True)
    p.add_argument("--workload-metadata", type=Path, required=True)
    p.add_argument("--output", type=Path, required=True)
    p = commands.add_parser("host")
    p.add_argument("--layer", choices=["ubuntu_host", "openstack_hypervisor"], required=True)
    p.add_argument("--workload", type=Path, default=ROOT / "configs/integration/load-safe.json")
    p.add_argument("--run-id", required=True)
    p.add_argument("--output", type=Path, required=True)
    p.add_argument("--samples", type=int, default=12)
    p.add_argument("--interval", type=float, default=15)
    p = commands.add_parser("load")
    p.add_argument("--host", required=True)
    p.add_argument("--profile", type=Path, default=ROOT / "configs/integration/load-safe.json")
    p.add_argument("--run-id", required=True)
    p.add_argument("--output", type=Path, required=True)
    p.add_argument("--source", choices=["real", "simulated"], default="simulated")
    args = parser.parse_args()
    try:
        if args.command == "collect":
            result = collect(load_json(args.config), args.output, load_json(args.workload), args.fixture, args.samples, args.interval)
        elif args.command == "preprocess":
            samples, rejected = preprocess(args.input, args.output, args.allow_simulated, args.max_skew)
            result = {"accepted": len(samples), "rejected": len(rejected)}
        elif args.command == "calibrate":
            result = calibrate(args.input, args.output, args.simulator_config, args.allow_simulated, args.write_candidate)
        elif args.command == "export-simulation":
            result = {"source": "simulated", "records": export_simulation(args.input, args.output)}
        elif args.command == "normalize-locust":
            result = {"records": normalize_locust(args.input, args.workload_metadata, args.output)}
        elif args.command == "host":
            sample_host(args.layer, args.run_id, args.output, args.samples, args.interval, workload=load_json(args.workload))
            result = {"layer": args.layer, "cluster_id": None}
        else:
            profile = validate_profile(load_json(args.profile))
            if args.output.exists() and any(args.output.iterdir()):
                raise ValueError("Load output must be new/empty")
            (args.output / "locust").mkdir(parents=True, exist_ok=True)
            env = dict(os.environ, CALIBRATION_PROFILE=str(args.profile.resolve()), CALIBRATION_RUN_ID=args.run_id,
                       CALIBRATION_SOURCE=args.source, CALIBRATION_OUTPUT_DIR=str((args.output / "client").resolve()))
            command = [sys.executable, "-m", "locust", "-f", str(ROOT / "src/loadtests/locustfile.py"), "--headless",
                       "--host", args.host, "--csv", str((args.output / "locust/stats").resolve()), "--csv-full-history", "--stop-timeout", "3"]
            subprocess.run(command, env=env, cwd=ROOT, timeout=sum(p["seconds"] for p in profile["phases"]) + 30, check=True)
            result = {"source": args.source, "output": str(args.output), "note": "Target must be verified separately; local smoke is not evidence of K3s connectivity"}
    except (ValueError, KeyError, OSError, subprocess.SubprocessError) as exc:
        parser.exit(2, f"Error: {exc}\n")
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
