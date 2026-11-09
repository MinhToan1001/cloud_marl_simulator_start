"""Offline snapshot adapter and declarative operation plans. No executor exists."""
from copy import deepcopy
from pathlib import Path
import json

import numpy as np

from integration.parser import AppDeltaParser, ready
from integration.quantities import cpu_cores, memory_bytes
from integration.schema import timestamp
from . import ENV_VERSION
from .schema import CLUSTER_FIELDS, vectorize
from .validator import validate_packet


def unknown_cluster():
    result = {name: None for name, _, _ in CLUSTER_FIELDS}
    result.update(available=0, resource_valid=0, app_valid=0, age_s=30, reservation_valid=0)
    return result


def archived_packets(run_dir, config):
    """Replay at the capture clock; these are not fresh observations of today."""
    parser, packets = AppDeltaParser(), []
    session = json.loads((Path(run_dir)/"collector/session.json").read_text())
    for line in (Path(run_dir)/"collector/raw.jsonl").read_text().splitlines():
        wrapper = json.loads(line)
        if wrapper["source"] != "real" or wrapper["collection_mode"] != "live":
            raise ValueError("Archived adapter requires explicitly real/live source")
        snapshot = wrapper["snapshot"]
        now = timestamp(snapshot["observed_at_utc"])
        nodes = snapshot["nodes"]["items"]
        if {n["metadata"]["name"] for n in nodes} != set(session["expected_node_names"]):
            raise ValueError("Archive node identity mismatch")
        packet = {"env_version": ENV_VERSION, "action_kind": "workload_placement", "source": "real_archive",
                  "timestamp_utc": snapshot["observed_at_utc"], "historical_only": True,
                  "clusters": {c: unknown_cluster() for c in "ABC"}, "services": {}, "mapping": {}}
        node, pod = nodes[0], snapshot["pods"]["items"][0]
        node_metrics = next((n for n in snapshot.get("node_metrics", {}).get("items", []) if n["metadata"]["name"] == node["metadata"]["name"]), None)
        allocatable = node["status"].get("allocatable", {})
        capacity_cpu = cpu_cores(allocatable["cpu"]) if "cpu" in allocatable else None
        capacity_memory = memory_bytes(allocatable["memory"]) if "memory" in allocatable else None
        age = (now-timestamp(node_metrics["timestamp"])).total_seconds() if node_metrics else 1e6
        valid = node_metrics is not None and 0 <= age <= 30 and bool(capacity_cpu and capacity_memory)
        requests = pod["spec"]["containers"][0]["resources"]["requests"]
        request_cpu, request_memory = cpu_cores(requests["cpu"]), memory_bytes(requests["memory"])
        context = {k: session[k] for k in ("source", "collection_mode", "cluster_id", "run_id", "workload_sha256", "namespace")}
        app = snapshot["apps"][0]
        if app["pod_name"] != pod["metadata"]["name"] or app["namespace"] != session["namespace"]:
            raise ValueError("App/pod identity mismatch")
        app_record = parser.parse(app, snapshot["observed_at_utc"], context)
        cluster = packet["clusters"]["A"]
        cluster.update(available=int(ready(node) and valid), cpu_capacity=capacity_cpu, memory_capacity=capacity_memory,
                       cpu_utilization=cpu_cores(node_metrics["usage"]["cpu"])/capacity_cpu if valid else None,
                       memory_utilization=memory_bytes(node_metrics["usage"]["memory"])/capacity_memory if valid else None,
                       cpu_reservation=None, memory_reservation=None,  # selected pod inventory is not cluster-wide reservation inventory
                       incoming_rps=app_record.incoming_rps, completed_rps=app_record.completed_rps,
                       latency_mean_ms=app_record.latency_mean_ms, latency_p95_ms=app_record.latency_p95_ms,
                       error_fraction=app_record.error_rate_pct/100 if app_record.error_rate_pct is not None else None,
                       resource_valid=int(valid), app_valid=int(app_record.quality == "ok" and bool(app_record.latency_samples)), age_s=max(0, age))
        for i, service in enumerate(config.services):
            packet["services"][service.name] = {"active": "A" if i == 0 else None, "source": None, "target": None,
                "phase": "IDLE", "remaining_s": 0, "cooldown_s": 0, "incoming_rps": app_record.incoming_rps if i == 0 else None,
                "cpu_request": request_cpu if i == 0 else None, "memory_request": request_memory if i == 0 else None,
                "ready": int(i == 0 and ready(pod)), "migration_count": 0, "valid": int(i == 0), "slo_ms": service.slo_ms}
        packet["mapping"][config.services[0].name] = {"namespace": session["namespace"], "pod_name": pod["metadata"]["name"],
                  "pod_uid": pod["metadata"]["uid"], "node_name": node["metadata"]["name"],
                  "pod_identity_verified": True, "deployment_identity_verified": False,
                  "reason": "Archive scrubbed Deployment/ownerReferences; do not infer Deployment name from pod prefix"}
        packets.append(packet)
    return packets


def validate_shadow(packet, service_name, action, config):
    return validate_packet(packet, service_name, action, config, require_identity=True)


def action_masks(packet, config):
    return {s.name: np.asarray([True] + [validate_shadow(packet, s.name, a, config)[0] != 0 for a in range(1, 4)]) for s in config.services}


def operation_plan(packet, service_name, action, config):
    executed, reason = validate_shadow(packet, service_name, action, config)
    result = {"mode": "offline_shadow", "executable": False, "service": service_name,
              "proposed_action": action, "validated_action": executed, "reason": reason, "operations": []}
    if executed:
        result["operations"] = [{"operation": op, "target_cluster": "ABC"[executed-1]} for op in (
            "verify_identity_and_reserve_resources", "prepare_target_deployment", "wait_ready_and_health",
            "switch_serving_endpoint", "wait_grace_period", "release_source_after_verification")]
        result["rollback"] = ["keep_source_until_target_ready", "restore_source_endpoint_if_healthy", "release_failed_target_reservation"]
    return result


def observations(packet, config):
    return {s.name: vectorize(packet, config, s.name)[0] for s in config.services}
