"""Validate and schedule a short, conservative four-phase experiment."""
import math
from collections import deque


def validate_profile(profile):
    phases = profile["phases"]
    if [p["name"] for p in phases] != ["stable", "ramp_up", "spike", "recovery"]:
        raise ValueError("Use exactly stable, ramp_up, spike, recovery in order")
    maximum = profile["max_users"]
    if type(maximum) is not int or not 1 <= maximum <= 16:
        raise ValueError("max_users must be 1..16")
    for phase in phases:
        if type(phase["seconds"]) is not int or not 1 <= phase["seconds"] <= 180:
            raise ValueError("Phase duration must be 1..180s")
        for key in ("start_users", "end_users"):
            if type(phase[key]) is not int or not 0 <= phase[key] <= maximum:
                raise ValueError("User counts must fit max_users")
    if sum(p["seconds"] for p in phases) > 600 or phases[2]["seconds"] > 30:
        raise ValueError("Maximum 600s total and 30s spike")
    if not 0.5 <= profile["wait_min_s"] <= profile["wait_max_s"] <= 10:
        raise ValueError("Think time must be 0.5..10s")
    if not 0 < profile["spawn_rate"] <= 2:
        raise ValueError("Spawn rate must be >0 and <=2 users/s")
    if not 0 < profile["stop_error_fraction"] <= 1 or not 1 <= profile["stop_latency_ms"] <= 5000:
        raise ValueError("Invalid stop thresholds")
    return profile


def phase_at(profile, elapsed):
    start = 0
    for phase in profile["phases"]:
        if elapsed < start + phase["seconds"]:
            fraction = max(0, elapsed - start) / max(1, phase["seconds"] - 1)
            users = round(phase["start_users"] + min(1, fraction) * (phase["end_users"] - phase["start_users"]))
            return phase["name"], users
        start += phase["seconds"]
    return None


class LoadGuard:
    def __init__(self, profile):
        self.profile = profile
        self.samples = deque(maxlen=100)
        self.unsafe_ticks = 0

    def record(self, latency_ms, failed):
        if math.isfinite(latency_ms) and latency_ms >= 0:
            self.samples.append((latency_ms, bool(failed)))

    def must_stop(self):
        if len(self.samples) < 20:
            return False
        error = sum(failed for _, failed in self.samples) / len(self.samples)
        latency = sum(latency for latency, _ in self.samples) / len(self.samples)
        unsafe = error > self.profile["stop_error_fraction"] or latency > self.profile["stop_latency_ms"]
        self.unsafe_ticks = self.unsafe_ticks + 1 if unsafe else 0
        return self.unsafe_ticks >= 3
