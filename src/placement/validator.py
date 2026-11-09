"""Shared, pure placement validator for simulator policies and shadow plans."""


def validate_packet(packet, name, action, config, require_identity=False):
    if type(action) is not int or action not in range(4):
        return 0, "invalid_action"
    if action == 0:
        return 0, "keep"
    service = packet["services"][name]
    target = "ABC"[action-1]
    if target == service["active"]:
        return 0, "already_active"
    if not service["valid"] or (require_identity and not packet.get("mapping", {}).get(name, {}).get("deployment_identity_verified")):
        return 0, "unverified_workload_mapping"
    if service["phase"] != "IDLE":
        return 0, "transition_in_progress"
    if service["cooldown_s"]:
        return 0, "cooldown"
    if service.get("attempts", service["migration_count"]) >= config.max_moves:
        return 0, "migration_limit"
    if sum(s["phase"] != "IDLE" for s in packet["services"].values()) >= config.max_concurrent_moves:
        return 0, "concurrency_limit"
    cluster = packet["clusters"][target]
    if not cluster["available"] or not cluster["resource_valid"] or cluster["age_s"] > 30:
        return 0, "unavailable_or_stale_target"
    for key in ("cpu_capacity", "memory_capacity", "cpu_reservation", "memory_reservation", "cpu_utilization", "memory_utilization"):
        if cluster.get(key) is None:
            return 0, "missing_cluster_resource_inventory"
    if not cluster.get("reservation_valid", 0) or not cluster["cpu_capacity"] or not cluster["memory_capacity"]:
        return 0, "missing_cluster_resource_inventory"
    cpu_need = service["cpu_request"]/cluster["cpu_capacity"]
    mem_need = service["memory_request"]/cluster["memory_capacity"]
    # Usage already includes workloads; max avoids adding the same workload
    # twice. Requests and measured headroom are separate conservative checks.
    if max(cluster["cpu_reservation"], cluster["cpu_utilization"])+cpu_need > 1+1e-9:
        return 0, "insufficient_cpu_headroom"
    if max(cluster["memory_reservation"], cluster["memory_utilization"])+mem_need > 1+1e-9:
        return 0, "insufficient_memory_headroom"
    return action, "accepted"
