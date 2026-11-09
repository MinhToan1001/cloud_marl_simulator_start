"""Run tests, baseline, IPPO, held-out evaluation and plots in one command."""
import argparse
import json
import os
import subprocess
import sys
from dataclasses import replace
from pathlib import Path

from cloud_sim.config import load_config
from cloud_sim.experiments import run_baselines
from cloud_sim.plotting import plot_results
from marl.config import load_training_config
from marl.evaluation import SCENARIOS, evaluate, plot_evaluation
from marl.plotting import plot_training
from marl.training import train

ROOT = Path(__file__).resolve().parents[2]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=ROOT / "results/reproduced")
    parser.add_argument("--config", type=Path, default=ROOT / "configs/simulation.json")
    parser.add_argument("--hyperparameters", type=Path, default=ROOT / "configs/marl.json")
    parser.add_argument("--episodes", type=int)
    parser.add_argument("--smoke", action="store_true", help="Two train episodes, two testing seeds, mixed scenario only")
    args = parser.parse_args()
    if args.output.exists() and any(args.output.iterdir()):
        parser.exit(2, "Error: output must be a new or empty directory to preserve past experiments\n")
    env = dict(os.environ, PYTEST_DISABLE_PLUGIN_AUTOLOAD="1")
    subprocess.run([sys.executable, "-m", "pytest", "-q"], cwd=ROOT, env=env, check=True)
    simulation = load_config(args.config)
    training = load_training_config(args.hyperparameters)
    changes = {}
    if args.episodes is not None:
        changes["episodes"] = args.episodes
    if args.smoke:
        changes.update(episodes=2, validation_interval=1, validation_seeds=(training.validation_seeds[0],))
    training = replace(training, **changes)
    print("[A+B] Paired baselines", flush=True)
    run_baselines(simulation, args.output / "baseline", [42, 43, 44, 45, 46])
    plot_results(args.output / "baseline/csv", args.output / "baseline/figures")
    print("[C+D] IPPO training", flush=True)
    metadata = train(simulation, training, args.output / "training")
    plot_training(args.output / "training")
    print("[E] Held-out evaluation", flush=True)
    evaluation = evaluate(simulation, args.output / "training/checkpoints/best.pt", args.output / "evaluation",
                          [30001, 30002] if args.smoke else [30001, 30002, 30003, 30004, 30005],
                          ["mixed"] if args.smoke else list(SCENARIOS))
    plot_evaluation(args.output / "evaluation")
    (args.output / "pipeline.json").write_text(json.dumps({
        "data_source": "simulation", "smoke": args.smoke,
        "training_episodes": metadata["completed_episodes"],
        "checkpoint_episode": evaluation["checkpoint_episode"],
        "api_command": f'"{sys.executable}" -m cli.serve --results "{args.output / "evaluation"}"',
    }, indent=2), encoding="utf-8")
    print(f"[F] API ready: run python -m cli.serve --results {args.output / 'evaluation'}")
    print(f"Completed pipeline: {args.output.resolve()}")


if __name__ == "__main__":
    main()
