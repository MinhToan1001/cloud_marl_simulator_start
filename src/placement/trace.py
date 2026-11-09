"""Precomputed exogenous randomness shared by every policy in a paired run."""
import math
import numpy as np
from .io import fingerprint

SCENARIOS = ("stable", "ramp_up", "spike", "recovery", "periodic", "shift")


def make_trace(config, seed, scenario="mixed"):
    if scenario not in (*SCENARIOS, "mixed"):
        raise ValueError("Unknown placement workload")
    rng = np.random.default_rng(seed)
    duration = config.seconds
    rates = []
    for t in range(duration):
        x = t / duration
        mode = ("stable", "ramp_up", "spike", "recovery")[min(3, int(x * 4))] if scenario == "mixed" else scenario
        level = {"stable": 8, "ramp_up": 4 + 45*x, "spike": 50 if .35 < x < .65 else 6,
                 "recovery": 50 if x < .4 else 8, "periodic": 25 + 22*math.sin(x*6*math.pi), "shift": 25}[mode]
        proportions = np.array([.6, .25, .15]) if mode == "shift" and x < .5 else np.array([.15, .25, .6]) if mode == "shift" else np.array([.4, .35, .25])
        rates.append(np.maximum(.1, 3 * level * proportions))
    demand = rng.poisson(np.asarray(rates)).tolist()
    capacities = rng.uniform(*config.capacity_factor, 3).tolist()
    network = rng.uniform(*config.network_factor, (duration, 3)).tolist()
    readiness = np.maximum(1, np.rint(config.readiness_s * rng.uniform(*config.readiness_factor, (duration, 3)))).astype(int).tolist()
    phase = rng.uniform(0, 2*math.pi, 3)
    background = []
    for t in range(duration):
        low, high = config.background_fraction
        background.append((low + (high-low)*(.5 + .5*np.sin(t/20 + phase))).tolist())
    available = np.ones((duration, 3), dtype=bool)
    # Simulated bounded fault, fixed in time and cluster independently of actions.
    if seed % 3 == 0 and duration >= 60:
        available[duration//2:duration//2+8, seed % 5 % 3] = False
    result = {"seed": int(seed), "scenario": scenario, "source": "simulated", "demand": demand,
              "capacity_factor": capacities, "network_factor": network, "readiness_s": readiness,
              "background_fraction": background, "available": available.tolist()}
    result["sha256"] = fingerprint(result)
    return result
