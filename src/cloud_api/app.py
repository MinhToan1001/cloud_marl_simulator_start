"""FastAPI dashboard integration backed by generated experiment files only."""
from __future__ import annotations

import json
import math
import os
from pathlib import Path
from typing import Literal

from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware

from cloud_sim.config import config_from_dict
from cloud_sim.storage import read_csv

ROOT = Path(__file__).resolve().parents[2]
Policy = Literal["round_robin", "least_loaded", "marl"]
Scenario = Literal["mixed", "stable", "ramp_up", "spike", "recovery"]
TEXT_COLUMNS = {"scenario", "policy", "cluster", "phase", "comparison"}
REQUIRED_COLUMNS = {
    "cluster_metrics": {"second", "policy", "cluster", "assigned", "processed", "queue_after"},
    "timeseries": {"second", "policy", "throughput_req_per_s", "error_rate_pct"},
    "comparison": {"policy", "throughput_req_per_s_mean", "throughput_req_per_s_std"},
}


def envelope(**data):
    return {"data_source": "simulation", "is_simulation": True, "metrics_are_real": False,
            "notice": "Synthetic simulator output; not real OpenStack/Kubernetes/AWS telemetry.", **data}


class ResultStore:
    def __init__(self, root: Path):
        self.root = root

    def metadata(self):
        path = self.root / "metadata.json"
        if not path.exists():
            raise HTTPException(404, "No simulation results. Run baseline or evaluation first.")
        try:
            data = json.loads(path.read_text(encoding="utf-8-sig"))
            if data.get("data_source") != "simulation":
                raise ValueError("Source is not marked simulation")
            config_from_dict(data["config"])
            return data
        except (ValueError, KeyError, TypeError, OSError) as exc:
            raise HTTPException(503, "Simulation metadata is invalid or unavailable") from exc

    def table(self, name):
        path = self.root / "csv" / f"{name}.csv"
        if not path.exists():
            raise HTTPException(404, f"Simulation dataset '{name}' has not been generated")
        try:
            converted = []
            for row in read_csv(path):
                if not REQUIRED_COLUMNS.get(name, set()).issubset(row) or None in row:
                    raise ValueError("Missing or malformed CSV columns")
                parsed = {}
                for key, raw in row.items():
                    if key in TEXT_COLUMNS:
                        parsed[key] = raw
                    elif raw in ("", None):
                        parsed[key] = None
                    else:
                        number = float(raw)
                        if not math.isfinite(number):
                            raise ValueError("Non-finite metric")
                        parsed[key] = int(raw) if raw.lstrip("-").isdigit() else number
                converted.append(parsed)
            return converted
        except (OSError, ValueError, TypeError, AttributeError) as exc:
            raise HTTPException(503, f"Simulation dataset '{name}' is invalid or unavailable") from exc


def create_app(results_dir: Path | str | None = None) -> FastAPI:
    store = ResultStore(Path(results_dir or os.environ.get("CLOUD_SIM_RESULTS", ROOT / "results/evaluation")))
    app = FastAPI(title="Cloud MARL Simulation API", version="1.0.0",
                  description="Read-only synthetic simulation metrics. No real cloud telemetry or cloud control.")
    origins = [s.strip() for s in os.environ.get("CLOUD_SIM_CORS_ORIGINS", "").split(",") if s.strip()]
    if origins:
        app.add_middleware(CORSMiddleware, allow_origins=origins, allow_methods=["GET"],
                           allow_headers=["Accept", "Content-Type"], allow_credentials=False)

    @app.get("/api/simulation/status")
    def status():
        required = ("cluster_metrics", "timeseries", "summary", "comparison", "workload")
        available = [name for name in required if (store.root / "csv" / f"{name}.csv").exists()]
        try:
            metadata = store.metadata()
        except HTTPException as exc:
            return envelope(status="no_results" if exc.status_code == 404 else "invalid_results", ready=False,
                            available_datasets=available, detail=exc.detail)
        ready = len(available) == len(required)
        return envelope(status="ready" if ready else "incomplete", ready=ready,
                        available_datasets=available, policies=metadata.get("policies", []),
                        scenarios=metadata.get("scenarios", ["mixed"]),
                        seeds=metadata.get("testing_seeds", metadata.get("seeds", [])),
                        generated_at_utc=metadata.get("created_at_utc"),
                        checkpoint_episode=metadata.get("checkpoint_episode"))

    @app.get("/api/simulation/clusters")
    def clusters(policy: Policy | None = None, seed: int | None = Query(None, ge=0),
                 scenario: Scenario | None = None):
        metadata = store.metadata()
        config = config_from_dict(metadata["config"])
        methods = metadata.get("policies", [])
        selected_policy = policy or ("marl" if "marl" in methods else methods[0] if methods else "round_robin")
        seeds = metadata.get("testing_seeds", metadata.get("seeds", []))
        selected_seed = seed if seed is not None else seeds[0] if seeds else None
        scenarios = metadata.get("scenarios", ["mixed"])
        selected_scenario = scenario or ("mixed" if "mixed" in scenarios else scenarios[0])
        rows = [r for r in store.table("cluster_metrics") if r["policy"] == selected_policy
                and (selected_seed is None or r.get("seed") == selected_seed)
                and r.get("scenario", "mixed") == selected_scenario]
        data = []
        for c in config.clusters:
            samples = [r for r in rows if r["cluster"] == c.name]
            latest = max(samples, key=lambda r: r["second"]) if samples else None
            data.append({"name": c.name, "configured_capacity_req_per_s": c.capacity,
                         "effective_capacity_req_per_s": c.effective_capacity,
                         "cpu_cores": c.cpu_cores, "memory_mb": c.memory_mb,
                         "max_queue": c.max_queue, "base_latency_ms": c.base_latency_ms,
                         "latest_metrics": latest})
        return envelope(policy=selected_policy, seed=selected_seed, scenario=selected_scenario, clusters=data)

    @app.get("/api/simulation/metrics")
    def metrics(policy: Policy | None = None, seed: int | None = Query(None, ge=0),
                scenario: Scenario | None = None, cluster: str | None = Query(None, min_length=1, max_length=64),
                dataset: Literal["cluster", "system"] = "cluster",
                start_second: int = Query(0, ge=0), end_second: int | None = Query(None, ge=0),
                limit: int = Query(1000, ge=1, le=10000), offset: int = Query(0, ge=0)):
        metadata = store.metadata()
        if end_second is not None and end_second < start_second:
            raise HTTPException(422, "end_second must be >= start_second")
        if cluster is not None and cluster not in {c["name"] for c in metadata["config"]["clusters"]}:
            raise HTTPException(404, "Unknown simulated cluster")
        if dataset == "system" and cluster is not None:
            raise HTTPException(422, "Cluster filter is only available for dataset=cluster")
        rows = store.table("cluster_metrics" if dataset == "cluster" else "timeseries")
        rows = [r for r in rows if (policy is None or r["policy"] == policy)
                and (seed is None or r.get("seed") == seed)
                and (scenario is None or r.get("scenario", "mixed") == scenario)
                and (cluster is None or r.get("cluster") == cluster)
                and r["second"] >= start_second and (end_second is None or r["second"] <= end_second)]
        return envelope(dataset=dataset, total=len(rows), offset=offset, limit=limit, rows=rows[offset:offset + limit])

    @app.get("/api/simulation/comparison")
    def comparison(policy: Policy | None = None, scenario: Scenario | None = None):
        metadata = store.metadata()
        rows = [r for r in store.table("comparison") if (policy is None or r["policy"] == policy)
                and (scenario is None or r.get("scenario", "mixed") == scenario)]
        return envelope(aggregation="mean and sample standard deviation across seeds; std=0 for one run",
                        checkpoint_episode=metadata.get("checkpoint_episode"), rows=rows)

    return app


app = create_app()
