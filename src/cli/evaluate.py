"""Compare both baselines and a frozen IPPO checkpoint on held-out seeds."""
import argparse
from pathlib import Path

from cloud_sim.config import load_config
from marl.evaluation import SCENARIOS, evaluate, plot_evaluation

ROOT = Path(__file__).resolve().parents[2]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, default=ROOT / "configs/simulation.json")
    parser.add_argument("--checkpoint", type=Path, default=ROOT / "results/training/checkpoints/best.pt")
    parser.add_argument("--output", type=Path, default=ROOT / "results/evaluation")
    parser.add_argument("--seeds", nargs="+", type=int, default=[30001, 30002, 30003, 30004, 30005])
    parser.add_argument("--scenarios", nargs="+", choices=SCENARIOS, default=list(SCENARIOS))
    parser.add_argument("--no-plots", action="store_true")
    args = parser.parse_args()
    try:
        metadata = evaluate(load_config(args.config), args.checkpoint, args.output, args.seeds, args.scenarios)
        if not args.no_plots:
            plot_evaluation(args.output)
    except (ValueError, OSError) as exc:
        parser.exit(2, f"Error: {exc}\n")
    print(f"SIMULATION: {len(metadata['scenarios'])} scenarios x {len(metadata['testing_seeds'])} testing seeds x 3 methods")
    print(f"Results: {args.output.resolve()}")


if __name__ == "__main__":
    main()
