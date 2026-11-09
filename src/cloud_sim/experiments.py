"""Reproducible baseline runs, paired input, statistics, and an honest report."""
from __future__ import annotations

import hashlib
import json
import platform
import statistics
from datetime import datetime, timezone
from importlib.metadata import PackageNotFoundError, version
from pathlib import Path

from .config import SimulationConfig, integer
from .engine import simulate
from .policies import POLICIES
from .storage import read_workload, write_csv
from .workload import make_workload

METRICS = (
    "throughput_req_per_s", "latency_mean_ms", "latency_p95_ms", "error_rate_pct",
    "cpu_utilization_pct", "memory_utilization_pct", "load_imbalance", "processed",
    "dropped", "waiting_at_end",
)


def aggregate(summaries: list[dict], policies: tuple[str, ...] = POLICIES) -> list[dict]:
    result = []
    for policy in policies:
        runs = [s for s in summaries if s["policy"] == policy]
        row = {"policy": policy, "runs": len(runs)}
        for metric in METRICS:
            values = [s[metric] for s in runs if s[metric] is not None]
            row[f"{metric}_n"] = len(values)
            row[f"{metric}_mean"] = statistics.fmean(values) if values else None
            row[f"{metric}_std"] = statistics.stdev(values) if len(values) > 1 else (0.0 if values else None)
        result.append(row)
    return result


def fingerprint(data: object) -> str:
    return hashlib.sha256(json.dumps(data, sort_keys=True).encode("utf-8")).hexdigest()


def package_versions() -> dict:
    packages = {}
    for name in ("matplotlib", "pytest", "numpy", "gymnasium", "torch", "fastapi", "uvicorn", "httpx"):
        try:
            packages[name] = version(name)
        except PackageNotFoundError:
            packages[name] = None
    return packages


def run_baselines(config: SimulationConfig, output: Path, seeds: list[int],
                  workload_csv: Path | None = None) -> list[dict]:
    if not seeds or len(set(seeds)) != len(seeds):
        raise ValueError("Provide one or more distinct seeds")
    for seed in seeds:
        integer("seed", seed)
    if workload_csv is not None and len(seeds) != 1:
        raise ValueError("Replay accepts one seed; do not count a repeated trace as independent runs")
    all_workloads, all_rows, summaries, timeseries, histograms = [], [], [], [], []
    manifests = []
    for seed in seeds:
        workload = read_workload(workload_csv, seed) if workload_csv else make_workload(config, seed)
        values = [r["incoming_requests"] for r in workload]
        all_workloads.extend({"seed": seed, **r} for r in workload)
        manifests.append({"seed": seed, "seconds": len(values), "received": sum(values),
                          "workload_sha256": fingerprint(values)})
        for policy in POLICIES:
            run = simulate(policy, values, config)
            all_rows.extend({"seed": seed, **r} for r in run.rows)
            timeseries.extend({"seed": seed, **r} for r in run.timeseries)
            summaries.append({"seed": seed, **run.summary})
            histograms.extend({"seed": seed, "policy": policy, "latency_ms": latency, "count": count}
                              for latency, count in sorted(run.latency_histogram.items()))
    csv_dir = output / "csv"
    for name, data in (("workload", all_workloads), ("cluster_metrics", all_rows),
                       ("timeseries", timeseries), ("summary", summaries),
                       ("comparison", aggregate(summaries))):
        write_csv(csv_dir / f"{name}.csv", data)
    # Keep a valid, header-only histogram for a zero-traffic experiment.
    if histograms:
        write_csv(csv_dir / "latency_histogram.csv", histograms)
    else:
        (csv_dir / "latency_histogram.csv").write_text("seed,policy,latency_ms,count\n", encoding="utf-8-sig")
    effective = config.to_dict()
    effective["seed"] = seeds[0]
    metadata = {
        "data_source": "simulation", "model_version": "fifo-batch-v1",
        "created_at_utc": datetime.now(timezone.utc).isoformat(),
        "python": platform.python_version(), "platform": platform.platform(),
        "packages": package_versions(),
        "seeds": seeds, "config": effective, "config_sha256": fingerprint(effective),
        "workload_source": str(workload_csv.resolve()) if workload_csv else "generated",
        "workload_runs": manifests, "policies": POLICIES,
        "tick_seconds": 1, "drain_at_end": False,
    }
    (output / "metadata.json").write_text(json.dumps(metadata, indent=2), encoding="utf-8")
    write_report(output, aggregate(summaries), seeds, workload_csv is not None)
    return summaries


def write_report(output: Path, comparison: list[dict], seeds: list[int], replay: bool) -> None:
    lines = [
        "# Báo cáo baseline A + B", "",
        "Dữ liệu **simulation**, không phải metrics OpenStack/Kubernetes/AWS.", "",
        f"Seeds: {', '.join(map(str, seeds))}. Mỗi seed dùng cùng workload và cấu hình cho hai thuật toán.",
        "Nguồn workload: " + ("CSV phát lại." if replay else "Stable → Ramp-up tuyến tính → Spike → Recovery; jitter có seed."),
        "Tick = 1 giây; FIFO hữu hạn; không xả queue sau thời điểm kết thúc.",
        "CPU/RAM, thời gian phục vụ và độ trễ mạng cơ sở đều là tham số mô hình.", "",
        "| Thuật toán | Throughput req/s | Latency mean ms | Latency p95 ms | Error % | Thành công | Chờ cuối |",
        "|---|---:|---:|---:|---:|---:|---:|",
    ]
    for row in comparison:
        cells = [row["policy"]]
        for metric in ("throughput_req_per_s", "latency_mean_ms", "latency_p95_ms",
                       "error_rate_pct", "processed", "waiting_at_end"):
            mean, std = row[f"{metric}_mean"], row[f"{metric}_std"]
            cells.append(f"{mean:.2f} ± {std:.2f}" if mean is not None else "N/A")
        lines.append("| " + " | ".join(cells) + " |")
    before, after = comparison
    deltas = {metric: after[f"{metric}_mean"] - before[f"{metric}_mean"]
              for metric in ("throughput_req_per_s", "error_rate_pct", "latency_mean_ms")
              if before[f"{metric}_mean"] is not None and after[f"{metric}_mean"] is not None}
    lines.extend([
        "", "Các giá trị là mean ± sample standard deviation giữa các seed; một seed có std=0 theo quy ước.",
        "P95 tính nearest-rank từ các request hoàn tất của từng run, rồi tổng hợp giữa seed; không lấy p95 của trung bình theo giây.",
        "Request bị loại hoặc còn chờ không có latency hoàn tất; do đó latency chịu ảnh hưởng của censoring.", "",
        "## Đọc kết quả", "",
        "Throughput, latency và error cần được xem đồng thời. Throughput cao hơn không đảm bảo latency thấp hơn.",
        "Round Robin đơn giản và không cần trạng thái tải; Least Loaded cần backlog hiện tại, năng lực và giới hạn queue.",
        "Không có MARL trong thí nghiệm A + B; chưa thể kết luận về MARL hoặc ý nghĩa thống kê.", "",
        "## Biểu đồ và dữ liệu", "",
        "`figures/workload.png`, `cpu_utilization.png`, `memory_utilization.png`, `queue_length.png`,",
        "`latency_throughput.png`, `error_rate.png`, `comparison.png`.",
        "Biểu đồ theo thời gian hiển thị seed đầu tiên; comparison tổng hợp tất cả seed.",
        "Dữ liệu gốc nằm trong `csv/`; `metadata.json` lưu cấu hình, môi trường và hash workload để kiểm tra tái lập.", "",
        "## Hạn chế và bước tiếp theo", "",
        "Chưa hiệu chỉnh bằng cluster thật; arrival theo lô đầu giây, tài nguyên đồng nhất cho mọi request, không có lỗi mạng ngẫu nhiên.",
        "Error chỉ gồm request bị loại vì vượt tổng chỗ phục vụ và buffer/RAM, không mô phỏng lỗi ứng dụng.",
        "Base latency là thành phần cộng độc lập, không chiếm slot phục vụ. CPU là trung bình trong tick; RAM là đỉnh trước phục vụ.",
        "CPU/RAM tổng hợp là trung bình đều theo cluster và thời gian, không phải trung bình có trọng số phần cứng.",
        "Load imbalance = std dân số của (processed + queue_after) / capacity giữa ba cluster, rồi trung bình theo thời gian.",
        "Cần thiết kế và kiểm thử môi trường MARL (C) trước huấn luyện (D); dùng seed testing riêng ở E; API ở F chưa triển khai.", "",
    ])
    if deltas:
        statement = "\nChênh lệch mean (Least Loaded − Round Robin) trong các run này: "
        units = {"throughput_req_per_s": "req/s throughput", "error_rate_pct": "điểm phần trăm error",
                 "latency_mean_ms": "ms latency mean"}
        statement += "; ".join(f"{value:+.2f} {units[metric]}" for metric, value in deltas.items()) + ".\n"
        lines.insert(lines.index("## Biểu đồ và dữ liệu"), statement)
    (output / "report.md").write_text("\n".join(lines), encoding="utf-8")
