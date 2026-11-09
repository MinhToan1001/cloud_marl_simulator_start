"""Run both baselines with paired workloads and export CSV, PNG, and report."""
from __future__ import annotations

import argparse
from pathlib import Path

from cloud_sim.config import load_config
from cloud_sim.experiments import run_baselines

ROOT = Path(__file__).resolve().parents[2]


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, default=ROOT / "configs/simulation.json")
    parser.add_argument("--output", type=Path, default=ROOT / "results/baseline")
    parser.add_argument("--seeds", type=int, nargs="+", help="Default: seed in config")
    parser.add_argument("--workload-csv", type=Path, help="Replay old/new workload.csv; one seed only")
    parser.add_argument("--no-plots", action="store_true", help="Export CSV/report without Matplotlib")
    args = parser.parse_args()
    try:
        config = load_config(args.config)
        summaries = run_baselines(config, args.output, args.seeds or [config.seed], args.workload_csv)
        if not args.no_plots:
            from cloud_sim.plotting import plot_results
            plot_results(args.output / "csv", args.output / "figures")
    except (ValueError, OSError) as exc:
        parser.exit(2, f"Error: {exc}\n")
    print("SIMULATION ONLY - no OpenStack/AWS connection")
    for s in summaries:
        print(f"seed={s['seed']} {s['policy']}: received={s['received']}, "
              f"completed={s['processed']}, dropped={s['dropped']}, waiting={s['waiting_at_end']}, "
              f"throughput={s['throughput_req_per_s']:.2f} req/s")
    print(f"Results: {args.output.resolve()}")


if __name__ == "__main__":
    main()
