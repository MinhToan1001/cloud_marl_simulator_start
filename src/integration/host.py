"""Linux /proc sampler with an explicit host/hypervisor role, never Cluster A."""
import json
import socket
import time
from pathlib import Path

from .schema import MetricRecord, digest, utc_now, write_records


def parse_cpu(text):
    line = next(line for line in text.splitlines() if line.startswith("cpu "))
    values = [int(value) for value in line.split()[1:9]]
    # guest/guest_nice excluded: already included in user/nice.
    return sum(values), values[3] + (values[4] if len(values) > 4 else 0)


def parse_memory(text):
    values = {line.split(":")[0]: int(line.split()[1]) * 1024 for line in text.splitlines() if line.split(":")[0] in {"MemTotal", "MemAvailable"}}
    if set(values) != {"MemTotal", "MemAvailable"} or values["MemAvailable"] > values["MemTotal"]:
        raise ValueError("Require valid MemTotal and MemAvailable; no guessed available memory")
    return values["MemTotal"] - values["MemAvailable"], values["MemTotal"]


def sample_host(layer, run_id, output, samples=12, interval=15, proc=Path("/proc"), workload=None):
    if layer not in {"ubuntu_host", "openstack_hypervisor"} or not 1 <= samples <= 120 or not 2 <= interval <= 120:
        raise ValueError("Explicit host role and bounded sample count/interval required")
    if output.exists():
        raise ValueError("Use a new host metrics CSV")
    previous = parse_cpu((proc / "stat").read_text())
    for _ in range(samples):
        time.sleep(interval)
        current = parse_cpu((proc / "stat").read_text())
        total, idle = current[0] - previous[0], current[1] - previous[1]
        if total <= 0 or not 0 <= idle <= total:
            raise ValueError("CPU counter reset or invalid interval")
        used, capacity = parse_memory((proc / "meminfo").read_text())
        write_records(output, [MetricRecord(timestamp_utc=utc_now(), source="real", collection_mode="live",
            layer=layer, entity_id=socket.gethostname(), run_id=run_id, workload_sha256=digest(workload or {}),
            origin="linux_procfs", interval_s=interval, cpu_utilization_pct=100 * (total - idle) / total,
            memory_usage_bytes=used, memory_capacity_bytes=capacity, memory_utilization_pct=100 * used / capacity,
            metadata_json=json.dumps({"scope": "this OS instance, not K3s guest or pod", "denominator": "all logical CPUs", "workload": workload}))], append=True)
        previous = current
