"""Plot current results or original legacy output without changing the CSVs."""
import argparse
from pathlib import Path

from cloud_sim.plotting import plot_results


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", type=Path, default=Path(__file__).resolve().parents[2] / "results/baseline/csv")
    parser.add_argument("--output", type=Path, default=Path(__file__).resolve().parents[2] / "results/baseline/figures")
    parser.add_argument("--scenario", help="Evaluation scenario; default mixed when available")
    args = parser.parse_args()
    try:
        paths = plot_results(args.input, args.output, args.scenario)
    except (ValueError, OSError) as exc:
        parser.exit(2, f"Error: {exc}\n")
    print(f"Created {len(paths)} simulation figures in {args.output.resolve()}")


if __name__ == "__main__":
    main()
