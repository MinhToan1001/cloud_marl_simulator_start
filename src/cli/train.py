"""Train CPU Independent PPO, smoke-test, or resume a saved episode boundary."""
import argparse
from dataclasses import replace
from pathlib import Path

from cloud_sim.config import load_config
from marl.config import load_training_config
from marl.training import train

ROOT = Path(__file__).resolve().parents[2]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, default=ROOT / "configs/simulation.json")
    parser.add_argument("--hyperparameters", type=Path, default=ROOT / "configs/marl.json")
    parser.add_argument("--output", type=Path)
    parser.add_argument("--episodes", type=int, help="Total target episodes, including resumed episodes")
    parser.add_argument("--seed", type=int, help="Policy initialization/sampling seed")
    parser.add_argument("--resume", type=Path)
    parser.add_argument("--smoke", action="store_true", help="Two episodes; one validation seed; separate output")
    parser.add_argument("--no-plots", action="store_true")
    args = parser.parse_args()
    try:
        simulation = load_config(args.config)
        config = load_training_config(args.hyperparameters)
        if args.smoke and args.resume:
            raise ValueError("Use --episodes for resume; --smoke creates a separate new run")
        changes = {}
        if args.episodes is not None:
            changes["episodes"] = args.episodes
        if args.seed is not None:
            changes["seed"] = args.seed
        if args.smoke:
            changes.update(episodes=2, validation_interval=1, validation_seeds=(config.validation_seeds[0],))
        config = replace(config, **changes)
        output = args.output or ROOT / ("results/training_smoke" if args.smoke else "results/training")
        metadata = train(simulation, config, output, args.resume)
        if not args.no_plots:
            from marl.plotting import plot_training
            plot_training(output)
    except (ValueError, OSError) as exc:
        parser.exit(2, f"Error: {exc}\n")
    print(f"Completed IPPO on CPU: {metadata['completed_episodes']} episodes; best={metadata['best_episode']}; output={output.resolve()}")


if __name__ == "__main__":
    main()
