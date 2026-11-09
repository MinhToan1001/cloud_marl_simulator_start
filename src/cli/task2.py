"""Run local Task 2 calibration, placement IPPO and offline shadow checks.

Example: python -m cli.task2 --output results/task2/placement_v1_001
Every stage writes new paths. No SSH, load generation or infrastructure executor.
"""
import argparse
from dataclasses import replace, asdict
from pathlib import Path
import importlib.metadata as metadata
import platform
import shutil
import subprocess
import sys
import time

import psutil

from placement.calibration import calibrate_run
from placement.config import PlacementConfig, load_config
from placement.experiments import evaluate, plots, shadow_replay
from placement.io import new_directory, read_json, write_json, fingerprint, file_hash
from placement.schema import manifest, schema_hash
from placement.training import TrainingConfig, train, code_fingerprints

ROOT = Path(__file__).resolve().parents[2]


def runtime_check(output):
    available, free = psutil.virtual_memory().available, shutil.disk_usage(output).free
    if available < 1024**3 or free < 1024**3:
        raise RuntimeError("Need >=1 GiB available RAM and disk before training")
    check = subprocess.run([sys.executable, "-m", "pip", "check"], text=True, capture_output=True)
    if check.returncode:
        raise RuntimeError("Task2 runtime dependencies conflict: " + check.stdout)
    freeze = subprocess.run([sys.executable, "-m", "pip", "freeze"], text=True, capture_output=True, check=True)
    (output/"runtime-freeze.txt").write_text(freeze.stdout, encoding="utf-8")
    write_json(output/"runtime.json", {"python": platform.python_version(), "executable": sys.executable, "system_site_packages": False,
               "available_memory_bytes": available, "free_disk_bytes": free, "cpu_logical": psutil.cpu_count(), "torch_threads": 1,
               "pip_check": check.stdout.strip(), "code_fingerprints": code_fingerprints(),
               "numpy": metadata.version("numpy"), "torch": metadata.version("torch"), "device": "cpu"})


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--stage", choices=("all", "calibrate", "train", "evaluate", "shadow", "plots"), default="all")
    parser.add_argument("--episodes", type=int, default=300)
    parser.add_argument("--training-budget-s", type=int, default=1800)
    args = parser.parse_args()
    output = args.output.resolve()
    if args.episodes < 1 or args.episodes > 300 or not 1 <= args.training_budget_s <= 1800:
        parser.error("Approved cap is 300 episodes/seed and 1800 total training seconds")
    if args.stage in ("all", "calibrate"):
        new_directory(output)
        profile, report = calibrate_run(ROOT/"data/runs/cluster-a-real-003", output/"calibration")
        config = replace(PlacementConfig(), latency_mean_ms=profile["response"]["service_latency_mean_ms"],
                         memory_baseline_bytes=profile["response"]["memory_baseline_bytes"],
                         cpu_idle_cores=profile["response"]["cpu_idle_cores"], cpu_cores_per_rps=profile["response"]["cpu_cores_per_rps"],
                         profile_id=profile["profile_id"], profile_sha256=file_hash(output/"calibration/profile.json"))
        write_json(output/"placement_config.json", config.to_dict())
        write_json(output/"schema.json", manifest(config))
        write_json(output/"provenance.json", {"empirical_scope": profile["scope"], "observed_rps_range": profile["observed_rps_range"],
                   "empirical_parameters": profile["calibrated_parameters"], "maximum_capacity_identified": False,
                   "simulated_assumptions": ["namespace budgets A/B/C (not actual node capacities)", "extra workload profiles", "CPU work cost from configured 5ms, not saturation data",
                                             "CPU limit/queue capacity", "background/WAN/readiness/availability", "warmup/grace/cooldown/migration costs"],
                   "policy_scope": "research simulation; not calibrated multi-cluster production policy"})
        print(f"calibration: {report['aligned_windows']} valid windows; maximum capacity unknown", flush=True)
    config = load_config(output/"placement_config.json")
    if args.stage in ("all", "train"):
        runtime_check(output)
        start = time.monotonic()
        deadline = start+args.training_budget_s
        new_directory(output/"pilot")
        pilots = []
        for seed in (7, 17, 27):
            pilot = train(config, output/f"pilot/seed_{seed}", seed,
                          TrainingConfig(episodes=2, validation_interval=1, validation_seeds=(20001,)), deadline)
            pilots.append(pilot)
        estimate = sum(p["elapsed_s"] for p in pilots)/6*args.episodes*3
        write_json(output/"pilot_estimate.json", {"measured_pilots": pilots, "linear_main_estimate_s": estimate,
                   "approved_cap_s": args.training_budget_s, "note": "Pilot includes frequent validation; estimate is conservative"})
        if time.monotonic()+estimate > deadline:
            raise RuntimeError("Pilot estimate exceeds approved cap; no long run launched")
        new_directory(output/"training")
        runs = []
        for seed in (7, 17, 27):
            runs.append(train(config, output/f"training/seed_{seed}", seed, TrainingConfig(episodes=args.episodes), deadline))
        write_json(output/"training/manifest.json", {"runs": runs, "training_wall_s": time.monotonic()-start, "schema_hash": schema_hash(config),
                   "config_hash": fingerprint(config.to_dict()), "approved_cap_s": args.training_budget_s})
    if args.stage in ("all", "evaluate"):
        evaluate(config, output/"training", output/"evaluation")
    if args.stage in ("all", "shadow"):
        shadow_replay(config, ROOT/"data/runs/cluster-a-real-003", output/"training/seed_7/best.pt", output/"shadow")
    if args.stage in ("all", "plots"):
        plots(output)
    print(f"Task2 {args.stage} outputs: {output}", flush=True)


if __name__ == "__main__":
    main()
