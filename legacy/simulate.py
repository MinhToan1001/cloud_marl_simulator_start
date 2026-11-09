"""Bai 1: Mo phong 3 cluster va so sanh 2 bo dieu phoi.

Chi su dung thu vien chuan Python. Khong ket noi OpenStack/AWS.
Tat ca CPU/RAM/latency trong script la chi so MO PHONG, khong phai metrics that.
"""
from __future__ import annotations

import csv
import random
from dataclasses import dataclass
from pathlib import Path

OUT_DIR = Path(__file__).resolve().parent / "output"
SEED = 42
SECONDS = 180


@dataclass
class Cluster:
    name: str
    capacity: int                 # So request xu ly toi da trong 1 giay
    base_latency_ms: float       # Do tre co so gia lap
    queue: int = 0

    @property
    def max_queue(self) -> int:
        return 2 * self.capacity

    def step(self, assigned: int) -> dict:
        """Xu ly mot giay; tinh drop neu vuot nang luc/bo dem."""
        before = self.queue
        can_accept = max(0, self.capacity + self.max_queue - before)
        accepted = min(assigned, can_accept)
        dropped = assigned - accepted
        processed = min(self.capacity, before + accepted)
        self.queue = before + accepted - processed
        # Cac chi so sau la proxy gia lap, KHONG la thoi gian phan hoi do that.
        cpu = round(100 * processed / self.capacity, 2)
        ram = round(min(100, 20 + 30 * self.queue / self.max_queue), 2)
        latency_proxy = round(
            self.base_latency_ms + 1000 * (before + self.queue) / (2 * self.capacity), 2
        )
        return {
            "accepted": accepted,
            "dropped": dropped,
            "processed": processed,
            "queue_before": before,
            "queue_after": self.queue,
            "cpu_proxy_pct": cpu,
            "ram_proxy_pct": ram,
            "latency_proxy_ms": latency_proxy,
        }


def make_workload(seconds: int = SECONDS, seed: int = SEED) -> list[int]:
    """Sinh luong request moi giay, co 4 pha tai."""
    rng = random.Random(seed)
    traffic = []
    for t in range(seconds):
        if t < 45:
            base = 90           # Stable: thap
        elif t < 90:
            base = 190          # Ramp-up
        elif t < 135:
            base = 350          # Spike: qua tong nang luc (270)
        else:
            base = 180          # Recovery
        traffic.append(max(0, round(base * rng.uniform(0.85, 1.15))))
    return traffic


def allocate(policy: str, clusters: list[Cluster], demand: int, t: int) -> list[int]:
    allocated = [0] * len(clusters)
    if policy == "round_robin":
        for i in range(demand):
            allocated[(t + i) % len(clusters)] += 1
    elif policy == "least_loaded":
        # Uoc luong tai do tren backlog + request vua phan bo trong giay nay.
        for _ in range(demand):
            target = min(
                range(len(clusters)),
                key=lambda j: (clusters[j].queue + allocated[j] + 1) / clusters[j].capacity,
            )
            allocated[target] += 1
    else:
        raise ValueError(f"Policy khong ho tro: {policy}")
    assert sum(allocated) == demand
    return allocated


def simulate(policy: str, workload: list[int]) -> tuple[list[dict], dict]:
    clusters = [
        Cluster("A", capacity=60, base_latency_ms=40),
        Cluster("B", capacity=90, base_latency_ms=45),
        Cluster("C", capacity=120, base_latency_ms=50),
    ]
    rows: list[dict] = []
    received = sum(workload)
    total_processed = total_dropped = 0
    weighted_latency = 0.0

    for t, demand in enumerate(workload):
        allocated = allocate(policy, clusters, demand, t)
        for cluster, incoming in zip(clusters, allocated):
            result = cluster.step(incoming)
            rows.append({
                "second": t,
                "policy": policy,
                "cluster": cluster.name,
                "traffic_total": demand,
                "assigned": incoming,
                **result,
            })
            total_processed += result["processed"]
            total_dropped += result["dropped"]
            weighted_latency += result["processed"] * result["latency_proxy_ms"]

    waiting = sum(c.queue for c in clusters)
    assert received == total_processed + total_dropped + waiting
    summary = {
        "policy": policy,
        "received": received,
        "processed": total_processed,
        "dropped": total_dropped,
        "waiting_at_end": waiting,
        "throughput_req_per_s": round(total_processed / len(workload), 2),
        "drop_rate_pct": round(total_dropped / received * 100, 2) if received else 0,
        "latency_proxy_ms": round(weighted_latency / total_processed, 2) if total_processed else 0,
    }
    return rows, summary


def write_csv(path: Path, rows: list[dict]) -> None:
    if not rows:
        return
    with path.open("w", encoding="utf-8-sig", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)


def main() -> None:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    workload = make_workload()
    write_csv(
        OUT_DIR / "workload.csv",
        [{"second": i, "incoming_requests": n} for i, n in enumerate(workload)],
    )
    summaries = []
    all_rows = []
    for policy in ("round_robin", "least_loaded"):
        rows, summary = simulate(policy, workload)
        summaries.append(summary)
        all_rows.extend(rows)
    write_csv(OUT_DIR / "cluster_metrics.csv", all_rows)
    write_csv(OUT_DIR / "summary.csv", summaries)

    print("=== KET QUA MO PHONG (KHONG PHAI OPENSTACK THAT) ===")
    for s in summaries:
        print(f"\nPolicy: {s['policy']}")
        print(f"  Tong request: {s['received']}")
        print(f"  Xu ly: {s['processed']} | Roi: {s['dropped']} | Con trong hang doi: {s['waiting_at_end']}")
        print(f"  Throughput: {s['throughput_req_per_s']} req/s")
        print(f"  Drop rate: {s['drop_rate_pct']}%")
        print(f"  Latency proxy: {s['latency_proxy_ms']} ms")
    print(f"\nDa xuat CSV vao: {OUT_DIR}")


if __name__ == "__main__":
    main()
