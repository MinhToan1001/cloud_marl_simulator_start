"""Additional H2 validation with a temporal exclusion margin; no model changes."""
import argparse
import json
import math
from pathlib import Path

from integration.preprocess import aligned_samples
from integration.schema import read_records, timestamp
from placement.calibration import fit_response, replay_pod_window, error_metrics
from placement.io import write_json, write_csv

ROOT = Path(__file__).resolve().parents[2]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run", type=Path, required=True)
    args = parser.parse_args()
    target = args.run/"calibration/purged_validation.json"
    if target.exists():
        raise FileExistsError(target)
    records = read_records(ROOT/"data/runs/cluster-a-real-003/collector/metrics.csv")
    samples, _ = aligned_samples(records)
    maximum_window = max(float(json.loads(r.metadata_json)["metrics_window"].removesuffix("s")) for r in records if r.layer == "k3s_pod")
    margin = math.ceil(maximum_window+max(s["alignment_skew_s"] for s in samples))
    times = [timestamp(s["timestamp_utc"]) for s in samples]
    predictions, folds = [], []
    keys = ("cpu_usage_cores", "memory_usage_bytes", "latency_mean_ms", "completed_rps", "error_rate_pct")
    for start in range(0, len(samples), 2):
        holdout = list(range(start, min(start+2, len(samples))))
        train = [i for i in range(len(samples)) if i not in holdout and min(abs((times[i]-times[j]).total_seconds()) for j in holdout) > margin]
        if len(train) < 2:
            raise ValueError("Temporal purge leaves insufficient training samples")
        profile = fit_response([samples[i] for i in train])
        folds.append({"holdout_indices": holdout, "training_indices": train, "excluded_margin_s": margin})
        for i in holdout:
            predicted = replay_pod_window(samples[i], profile)
            row = {"timestamp_utc": samples[i]["timestamp_utc"], "holdout_block": start//2, "training_samples": len(train)}
            for key in keys:
                row[f"actual_{key}"] = samples[i][key]
                row[f"simulated_{key}"] = predicted[key]
            predictions.append(row)
    errors = {key: error_metrics([r[f"actual_{key}"] for r in predictions], [r[f"simulated_{key}"] for r in predictions]) for key in keys}
    write_json(target, {"method": "contiguous pairs held out; purge around test timestamps; full simulator pod-scoped replay",
               "margin_s": margin, "max_resource_window_s": maximum_window, "folds": folds, "errors": errors,
               "independent_external_validation": False,
               "limitations": "Single short run, 8 windows; purging reduces temporal leakage but does not establish extrapolation or capacity accuracy"})
    write_csv(args.run/"calibration/purged_predictions.csv", predictions)
    print(json.dumps(errors, indent=2))


if __name__ == "__main__":
    main()
