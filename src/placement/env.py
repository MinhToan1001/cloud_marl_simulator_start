"""Finite FIFO workload placement with explicit warmup, overlap and rollback.

CPU physics beyond observed low load is an assumption based on configured work
cost, never a claimed calibrated cluster maximum. This module has no network I/O.
"""
from collections import Counter, deque
from copy import deepcopy
from dataclasses import dataclass
import math
import statistics

import numpy as np

from . import ENV_VERSION
from .schema import ACTIONS, manifest, vectorize
from .trace import make_trace
from .validator import validate_packet
from .io import fingerprint


def quantile(histogram, fraction):
    count = sum(histogram.values())
    if not count:
        return None
    rank, seen = math.ceil(count*fraction), 0
    for value, frequency in sorted(histogram.items()):
        seen += frequency
        if seen >= rank:
            return value


@dataclass
class WorkloadState:
    active: int | None
    source: int | None = None
    target: int | None = None
    phase: str = "IDLE"
    remaining: int = 0
    cooldown: int = 0
    started: int = 0
    completed_moves: int = 0
    attempts: int = 0
    started_grace: int = 0


class PlacementEnv:
    def __init__(self, config):
        self.config = config
        self.agents = [s.name for s in config.services]
        self.observation_size = manifest(config)["size"]

    def reset(self, seed=1000, scenario="mixed", trace=None):
        self.trace = make_trace(self.config, seed, scenario) if trace is None else deepcopy(trace)
        if len(self.trace["demand"]) != self.config.seconds or self.trace["source"] != "simulated":
            raise ValueError("Trace duration/source differs")
        self.trace["sha256"] = fingerprint({k: v for k, v in self.trace.items() if k != "sha256"})
        self.second = self.decision = 0
        self.states = [WorkloadState(i) for i in range(3)]
        self.queues = [[deque() for _ in range(3)] for _ in range(3)]
        self.histogram = Counter()
        self.events, self.ticks = [], []
        self.migration_latencies = []
        self.total = Counter()
        self.cluster_last = [self._blank_stats() for _ in range(3)]
        self.clipped_features = 0
        self.observations_built = 0
        self.resource_sum = np.zeros((3, 4), dtype=float)
        self.assert_invariants()
        return self.observe()

    @staticmethod
    def _blank_stats():
        return {"incoming": 0, "processed": 0, "errors": 0, "latency_sum": 0., "histogram": Counter(), "cpu": 0., "memory": 0.}

    def available(self, cluster):
        return self.trace["available"][min(self.second, self.config.seconds-1)][cluster]

    def footprints(self, index):
        state = self.states[index]
        return {c for c in (state.active, state.target, state.source) if c is not None}

    def reservations(self):
        cpu, memory = np.zeros(3), np.zeros(3)
        for i, service in enumerate(self.config.services):
            for c in self.footprints(i):
                cpu[c] += service.cpu_request
                memory[c] += service.memory_request
        return cpu, memory

    def validate(self, index, action):
        return validate_packet(self.packet(), self.agents[index], action, self.config)

    def masks(self):
        return {name: np.array([True] + [self.validate(i, action)[0] != 0 for action in (1, 2, 3)], dtype=bool)
                for i, name in enumerate(self.agents)}

    def packet(self):
        cpu, memory = self.reservations()
        packet = {"env_version": ENV_VERSION, "action_kind": "workload_placement", "source": "simulated",
                  "timestamp_s": self.second, "clusters": {}, "services": {}}
        for c, config in enumerate(self.config.clusters):
            last = self.cluster_last[c]
            background = self.trace["background_fraction"][min(self.second, self.config.seconds-1)][c]
            packet["clusters"][config.name] = {
                "available": int(self.available(c)), "cpu_capacity": config.cpu_cores, "memory_capacity": config.memory_bytes,
                "cpu_utilization": min(1., last["cpu"]/config.cpu_cores) if self.second else background,
                "memory_utilization": min(1., last["memory"]/config.memory_bytes) if self.second else self.config.background_memory_fraction + memory[c]/config.memory_bytes*.5,
                "cpu_reservation": cpu[c]/config.cpu_cores, "memory_reservation": memory[c]/config.memory_bytes,
                "incoming_rps": last["incoming"], "completed_rps": last["processed"],
                "latency_mean_ms": last["latency_sum"]/last["processed"] if last["processed"] else None,
                "latency_p95_ms": quantile(last["histogram"], .95),
                "error_fraction": last["errors"]/max(1, last["incoming"]), "resource_valid": 1, "reservation_valid": 1,
                "app_valid": int(last["processed"] > 0), "age_s": 0}
        for i, service in enumerate(self.config.services):
            state = self.states[i]
            packet["services"][service.name] = {
                **{name: "ABC"[getattr(state, name)] if getattr(state, name) is not None else None for name in ("active", "target", "source")},
                "phase": state.phase, "remaining_s": state.remaining, "cooldown_s": state.cooldown,
                "incoming_rps": self.trace["demand"][self.second][i] if self.second < self.config.seconds else 0,
                "cpu_request": service.cpu_request, "memory_request": service.memory_request,
                "ready": int(state.active is not None and self.available(state.active)),
                "migration_count": state.completed_moves, "attempts": state.attempts, "valid": 1, "slo_ms": service.slo_ms}
        return packet

    def observe(self):
        packet = self.packet()
        observations = {}
        for name in self.agents:
            observations[name], clipped = vectorize(packet, self.config, name)
            self.clipped_features += clipped
            self.observations_built += len(observations[name])
        return observations

    def _drop_queue(self, index, cluster):
        count = len(self.queues[index][cluster])
        self.queues[index][cluster].clear()
        self.total["errors"] += count
        self.total["release_errors"] += count
        self.total["slo_violations"] += count

    def _lifecycle(self):
        for i, state in enumerate(self.states):
            if state.cooldown:
                state.cooldown -= 1
            if state.phase == "IDLE":
                continue
            if state.phase == "PREPARING":
                if not self.available(state.target) or self.second - state.started >= self.config.timeout_s:
                    self.total["failed_moves"] += 1
                    self.events.append({"second": self.second, "service": self.agents[i], "event": "rollback", "target": state.target})
                    state.target, state.phase, state.remaining = None, "IDLE", 0
                    state.cooldown = self.config.cooldown_s
                    continue
                ready_delay = self.trace["readiness_s"][state.started][state.target]
                state.remaining = max(0, ready_delay - (self.second-state.started))
                if not state.remaining:
                    state.source, state.active, state.phase = state.active, state.target, "GRACE"
                    state.remaining = self.config.grace_s
                    state.started_grace = self.second
                    self.events.append({"second": self.second, "service": self.agents[i], "event": "target_ready_switch", "target": state.active})
            elif state.phase == "GRACE":
                if not self.available(state.active):
                    # Roll back serving placement; failures on unavailable old source
                    # are handled by the request path rather than hidden.
                    self._drop_queue(i, state.active)
                    state.active, state.source, state.target = state.source, None, None
                    state.phase, state.remaining, state.cooldown = "IDLE", 0, self.config.cooldown_s
                    self.total["failed_moves"] += 1
                    self.events.append({"second": self.second, "service": self.agents[i], "event": "grace_rollback", "target": state.active})
                    continue
                state.remaining = max(0, self.config.grace_s - (self.second-state.started_grace))
                if not state.remaining:
                    if state.source is not None:
                        self._drop_queue(i, state.source)
                    state.source, state.target, state.phase = None, None, "IDLE"
                    state.cooldown = self.config.cooldown_s
                    state.completed_moves += 1
                    self.total["completed_moves"] += 1
                    self.migration_latencies.append(self.second - state.started)
                    self.events.append({"second": self.second, "service": self.agents[i], "event": "move_completed", "target": state.active})

    def _physics(self):
        stats = [self._blank_stats() for _ in range(3)]
        before_completed, before_errors = self.total["processed"], self.total["errors"]
        self._lifecycle()
        demand = self.trace["demand"][self.second]
        self.total["received"] += sum(demand)
        for i, count in enumerate(demand):
            service, state = self.config.services[i], self.states[i]
            if state.active is None or not self.available(state.active):
                self.total["errors"] += count
                self.total["unavailable_errors"] += count
                self.total["slo_violations"] += count
                self.total["downtime_service_s"] += 1
                continue
            c, queue = state.active, self.queues[i][state.active]
            stats[c]["incoming"] += count
            # Queue limit includes the current service batch to bound memory.
            accepted = min(count, max(0, service.queue_limit - len(queue)))
            queue.extend(self.second+(ordinal+.5)/max(1, count) for ordinal in range(accepted))
            dropped = count - accepted
            stats[c]["errors"] += dropped
            self.total["errors"] += dropped
            self.total["overflow_errors"] += dropped
            self.total["slo_violations"] += dropped
        cpu_res, mem_res = self.reservations()
        for c, cluster in enumerate(self.config.clusters):
            background = self.trace["background_fraction"][self.second][c]
            budget = cluster.cpu_cores*(1-background)*self.trace["capacity_factor"][c] if self.available(c) else 0
            indices = [i for i in range(3) if c in self.footprints(i) and len(self.queues[i][c])]
            desired = {i: min(len(self.queues[i][c])*self.config.services[i].cpu_work_s, self.config.services[i].cpu_limit) for i in indices}
            # Max-min water filling: a small queue must not steal unused budget.
            grants = {i: 0. for i in indices}
            remaining = set(indices)
            available_budget = budget
            while remaining and available_budget > 1e-12:
                share = available_budget / len(remaining)
                done = {i for i in remaining if desired[i] <= share + 1e-12}
                if not done:
                    for i in remaining:
                        grants[i] = share
                    break
                for i in done:
                    grants[i] = desired[i]
                    available_budget -= desired[i]
                remaining -= done
            workload_cpu = {i: min(self.config.services[i].cpu_limit, self.config.cpu_idle_cores)
                            for i in range(3) if c in self.footprints(i)}
            for i in indices:
                service, queue = self.config.services[i], self.queues[i][c]
                count = min(len(queue), int((grants[i]+1e-12)/service.cpu_work_s))
                next_free, completed = float(self.second), 0
                for _ in range(count):
                    arrival = queue[0]
                    service_start = max(next_free, arrival)
                    finish = service_start + self.config.latency_mean_ms/1000
                    if finish > self.second+1:
                        break
                    queue.popleft()
                    next_free = finish
                    completed += 1
                    latency = ((finish-arrival)*1000 + cluster.network_ms*self.trace["network_factor"][self.second][c])
                    value = round(latency, 1)
                    self.histogram[value] += 1
                    stats[c]["histogram"][value] += 1
                    stats[c]["latency_sum"] += value
                    self.total["latency_sum"] += value
                    self.total["slo_violations"] += int(latency > service.slo_ms)
                workload_cpu[i] = min(service.cpu_limit, self.config.cpu_idle_cores
                                      + completed*self.config.cpu_cores_per_rps/self.trace["capacity_factor"][c])
                stats[c]["processed"] += completed
                self.total["processed"] += completed
            residents = sum(len(self.queues[i][c]) for i in range(3))
            footprints = sum(c in self.footprints(i) for i in range(3))
            cpu_work = sum(workload_cpu.values())
            cpu_usage = min(cluster.cpu_cores, cluster.cpu_cores*background + cpu_work)
            memory_usage = min(cluster.memory_bytes, cluster.memory_bytes*self.config.background_memory_fraction + footprints*self.config.memory_baseline_bytes + residents*64*1024)
            stats[c]["cpu"], stats[c]["memory"] = cpu_usage, memory_usage
            self.resource_sum[c] += [cpu_usage/cluster.cpu_cores, memory_usage/cluster.memory_bytes,
                                     cpu_res[c]/cluster.cpu_cores, mem_res[c]/cluster.memory_bytes]
            unused_cpu = max(0., cpu_res[c]-cpu_work)/max(cpu_res[c], 1e-12) if cpu_res[c] else 0.
            workload_memory = footprints*self.config.memory_baseline_bytes + residents*64*1024
            unused_memory = max(0., mem_res[c]-workload_memory)/max(mem_res[c], 1) if mem_res[c] else 0.
            self.total["waste_sum"] += (unused_cpu+unused_memory)/6
        self.cluster_last = stats
        self.second += 1
        self.total["overlap_service_s"] += sum(s.phase != "IDLE" for s in self.states)
        self.ticks.append({"second": self.second-1, "incoming": sum(demand), "processed": self.total["processed"]-before_completed,
                           "errors": self.total["errors"]-before_errors, "waiting": self.waiting(),
                           "placements": ",".join("ABC"[s.active] if s.active is not None else "NONE" for s in self.states)})
        self.assert_invariants()

    def waiting(self):
        return sum(len(q) for queues in self.queues for q in queues)

    def assert_invariants(self):
        if self.total["received"] != self.total["processed"] + self.total["errors"] + self.waiting():
            raise RuntimeError("Request conservation violated")
        cpu, memory = self.reservations()
        for c, cluster in enumerate(self.config.clusters):
            if cpu[c] > cluster.cpu_cores+1e-9 or memory[c] > cluster.memory_bytes:
                raise RuntimeError("Reservation exceeds allocatable budget")
        for i, queues in enumerate(self.queues):
            if any(len(q) > self.config.services[i].queue_limit for q in queues):
                raise RuntimeError("Queue exceeded finite capacity")

    def step(self, actions):
        if self.second >= self.config.seconds:
            raise RuntimeError("Episode ended")
        if set(actions) != set(self.agents):
            raise ValueError("Provide exactly one proposal for each workload")
        previous = self.total.copy()
        previous_waiting = self.waiting()
        # Rotating arbitration reserves accepted targets immediately. All proposals
        # still originate from the same pre-step packet/masks for every algorithm.
        for offset in range(3):
            i = (self.decision+offset) % 3
            proposed = actions[self.agents[i]]
            executed, reason = self.validate(i, proposed)
            self.events.append({"second": self.second, "service": self.agents[i], "event": "proposal", "proposed": proposed,
                                "executed": executed, "reason": reason})
            if executed:
                state = self.states[i]
                state.target, state.phase, state.started = executed-1, "PREPARING", self.second
                state.remaining = self.trace["readiness_s"][self.second][state.target]
                state.attempts += 1
                self.total["accepted_moves"] += 1
        self.decision += 1
        for _ in range(min(self.config.decision_s, self.config.seconds-self.second)):
            self._physics()
        delta = self.total - previous  # Counters only increase.
        received = max(1, delta["received"]+previous_waiting)
        mean_latency = delta["latency_sum"]/delta["processed"] if delta["processed"] else 0.
        weights = self.config.reward
        components = {
            "success": weights["success"]*delta["processed"]/received,
            "latency": -weights["latency"]*min(mean_latency/statistics.mean(s.slo_ms for s in self.config.services), 10),
            "error": -weights["error"]*delta["errors"]/received,
            "slo": -weights["slo"]*delta["slo_violations"]/received,
            "waste": -weights["waste"]*delta["waste_sum"]/self.config.decision_s,
            "move": -weights["move"]*(delta["accepted_moves"] + delta["overlap_service_s"]/(3*self.config.decision_s)),
            "failed_move": -weights["failed_move"]*delta["failed_moves"],
        }
        reward = sum(components.values())
        done = self.second == self.config.seconds
        if done:
            # Outstanding requests are censored, not silently counted as success.
            reward -= weights["error"]*self.waiting()/max(1, self.total["received"])
        return self.observe(), {a: reward for a in self.agents}, done, {"reward_components": components, "data_source": "simulation"}

    def summary(self):
        t, seconds = self.total, max(1, self.second)
        resource = self.resource_sum/seconds
        offered = t["received"]
        resolved = t["processed"]+t["errors"]
        return {
            "received": offered, "processed": t["processed"], "errors": t["errors"], "unfinished": self.waiting(),
            "throughput_rps": t["processed"]/seconds, "error_fraction": t["errors"]/max(1, offered),
            "latency_mean_ms": t["latency_sum"]/t["processed"] if t["processed"] else None,
            "latency_p95_ms": quantile(self.histogram, .95), "latency_p99_ms": quantile(self.histogram, .99),
            "slo_violation_fraction_resolved": t["slo_violations"]/max(1, resolved), "slo_cohort_resolved": resolved,
            "slo_censored_requests": self.waiting(), "unavailable_errors": t["unavailable_errors"],
            "overflow_errors": t["overflow_errors"], "release_errors": t["release_errors"],
            "cpu_utilization": float(resource[:, 0].mean()), "memory_utilization": float(resource[:, 1].mean()),
            "cpu_reservation": float(resource[:, 2].mean()), "memory_reservation": float(resource[:, 3].mean()),
            "resource_waste_fraction": t["waste_sum"]/seconds,
            "accepted_moves": t["accepted_moves"], "completed_moves": t["completed_moves"], "failed_moves": t["failed_moves"],
            "pending_moves": sum(s.phase != "IDLE" for s in self.states),
            "migration_latency_mean_s": statistics.mean(self.migration_latencies) if self.migration_latencies else None,
            "downtime_service_s": t["downtime_service_s"], "moves_per_minute": t["completed_moves"]*60/seconds,
            "mean_dwell_s": 3*seconds/max(1, t["completed_moves"]+3),
            "cpu_imbalance": float(np.std(resource[:, 0])),
            "observation_clip_fraction": self.clipped_features/max(1, self.observations_built),
        }
