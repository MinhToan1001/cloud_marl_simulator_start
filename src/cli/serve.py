"""Serve read-only simulation results on localhost."""
import argparse
import os
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--results", type=Path, default=ROOT / "results/evaluation")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8000)
    args = parser.parse_args()
    if not 1 <= args.port <= 65535:
        parser.error("port must be 1..65535")
    os.environ["CLOUD_SIM_RESULTS"] = str(args.results.resolve())
    import uvicorn
    uvicorn.run("cloud_api.app:app", host=args.host, port=args.port, reload=False)


if __name__ == "__main__":
    main()
