"""Parallel dict API, plus an actual Gymnasium adapter for tooling/checkers."""
from __future__ import annotations

import math
from dataclasses import asdict

import gymnasium as gym
import numpy as np
from gymnasium import spaces

from cloud_sim.config import SimulationConfig, integer, number
from cloud_sim.engine import SimulationSession
from cloud_sim.workload import make_workload
from .config import RewardConfig

OBSERVATION_SIZE = 26
ENV_VERSION = "parallel-ratios-v1"


def ratios_to_counts(ratios: np.ndarray, demand: int) -> list[int]:
    """Largest-remainder apportionment: exact integer conservation, stable ties."""
    integer("demand", demand)
    weights = np.asarray(ratios, dtype=np.float64)
    if weights.shape != (3,) or not np.isfinite(weights).all() or (weights < 0).any() or weights.sum() <= 0:
        raise ValueError("Expected three finite nonnegative weights with positive sum")
    raw = weights / weights.sum() * demand
    counts = np.floor(raw).astype(np.int64)
    remainder = demand - int(counts.sum())
    for index in np.argsort(-(raw - counts), kind="stable")[:remainder]:
        counts[index] += 1
    return [int(value) for value in counts]


class MultiClusterEnv:
    """reset -> (obs, infos); step -> (obs, rewards, terminations, truncations, infos).

    Dictionaries are keyed by three agent IDs, without a special __all__ key.
    A finite workload ending is termination; an optional earlier max_steps is truncation.
    """

    metadata = {"name": ENV_VERSION, "render_modes": []}

    def __init__(self, config: SimulationConfig, reward: RewardConfig | None = None,
                 action_step: float = 0.03, max_steps: int | None = None):
        number("action_step", action_step, positive=True)
        if max_steps is not None:
            integer("max_steps", max_steps, 1)
        self.config, self.reward_config = config, reward or RewardConfig()
        self.action_step, self.max_steps = action_step, max_steps
        self.possible_agents = [f"cluster_{c.name}" for c in config.clusters]
        self.agents: list[str] = []
        self.observation_spaces = {a: spaces.Box(0, 10, shape=(OBSERVATION_SIZE,), dtype=np.float32) for a in self.possible_agents}
        self.action_spaces = {a: spaces.Discrete(3) for a in self.possible_agents}
        self.session: SimulationSession | None = None

    def observation_space(self, agent):
        return self.observation_spaces[agent]

    def action_space(self, agent):
        return self.action_spaces[agent]

    def reset(self, seed: int | None = None, options: dict | None = None):
        options = options or {}
        if set(options) - {"workload"}:
            raise ValueError("Only reset option 'workload' is supported")
        actual_seed = self.config.seed if seed is None else seed
        integer("seed", actual_seed)
        if "workload" in options:
            self.workload = list(options["workload"])
        else:
            self.workload = [r["incoming_requests"] for r in make_workload(self.config, actual_seed)]
        if not self.workload:
            raise ValueError("Workload must be nonempty")
        for value in self.workload:
            integer("incoming_requests", value)
        self.session = SimulationSession(self.config, "marl")
        self.agents = self.possible_agents.copy()
        self.second = 0
        self.ratios = np.full(3, 1 / 3, dtype=np.float64)
        self.episode_reward = 0.0
        for index, agent in enumerate(self.possible_agents):
            self.action_spaces[agent].seed(actual_seed + index)
        return self._observe(), {a: {"data_source": "simulation"} for a in self.agents}

    def _observe(self):
        total_capacity = sum(c.effective_capacity for c in self.config.clusters)
        last = self.session.rows[-3:] if self.session and self.session.rows else [None] * 3
        features = []
        for index, (c, row) in enumerate(zip(self.config.clusters, last)):
            queue = self.session.clusters[index].queue
            features.append([
                row["cpu_utilization_pct"] / 100 if row else 0,
                row["memory_utilization_pct"] / 100 if row else c.idle_memory_mb / c.memory_mb,
                queue / max(1, c.max_queue),
                row["assigned"] / total_capacity if row else 0,
                (row["latency_mean_ms"] or 0) / self.reward_config.latency_scale_ms if row else 0,
                ((row["processed"] if row else 0) + queue) / c.effective_capacity,
                c.effective_capacity / total_capacity,
                self.ratios[index],
            ])
        demand = self.workload[self.second] if self.second < len(self.workload) else 0
        global_features = [demand / total_capacity, (len(self.workload) - self.second) / len(self.workload)]
        return {agent: np.clip(np.asarray(
            sum((features[(own + offset) % 3] for offset in range(3)), []) + global_features,
            dtype=np.float32), 0, 10)
            for own, agent in enumerate(self.possible_agents)}

    def step(self, actions: dict):
        if not self.agents or self.session is None:
            raise RuntimeError("Call reset before step; reset again after an episode ends")
        if set(actions) != set(self.possible_agents):
            raise ValueError("Provide exactly one action for each agent")
        for agent, action in actions.items():
            if isinstance(action, (bool, np.bool_)) or not self.action_space(agent).contains(action):
                raise ValueError(f"Invalid action for {agent}: {action}")
        previous_ratios = self.ratios.copy()
        # Multiplicative changes preserve a strictly positive share, bounded log odds.
        logits = np.log(np.maximum(self.ratios, 1e-12))
        logits += self.action_step * (np.asarray([actions[a] for a in self.possible_agents]) - 1)
        logits = np.clip(logits - logits.max(), -8, 0)
        self.ratios = np.exp(logits) / np.exp(logits).sum()
        demand = self.workload[self.second]
        allocation = ratios_to_counts(self.ratios, demand)
        tick = self.session.step(demand, allocation)
        self.second += 1
        terminated = self.second == len(self.workload)
        truncated = not terminated and self.max_steps is not None and self.second >= self.max_steps
        c = self.reward_config
        rows = self.session.rows[-3:]
        capacity = sum(cluster.effective_capacity for cluster in self.config.clusters)
        components = {
            "throughput": c.throughput * tick["processed"] / capacity,
            "latency": -c.latency * min((tick["latency_mean_ms"] or 0) / c.latency_scale_ms, 10),
            "error": -c.error * tick["error_rate_pct"] / 100,
            "queue": -c.queue * float(np.mean([r["queue_after"] / max(1, r["max_queue"]) for r in rows])),
            "overload": -c.overload * float(np.mean([max(0, r["cpu_utilization_pct"] - 90) / 10 for r in rows])),
            "imbalance": -c.imbalance * tick["load_imbalance"],
            "switching": -c.switching * float(np.abs(self.ratios - previous_ratios).sum()),
            "terminal_queue": -c.terminal_queue * tick["queue_after"] / max(1, sum(cluster.max_queue for cluster in self.config.clusters)) if terminated else 0.0,
        }
        reward = float(sum(components.values()))
        if not math.isfinite(reward):
            raise RuntimeError("Non-finite reward")
        self.episode_reward += reward
        info = {"data_source": "simulation", "metrics": tick.copy(), "ratios": self.ratios.tolist(),
                "allocation": allocation, "reward_components": components}
        observations = self._observe()
        result = (observations, {a: reward for a in self.possible_agents},
                  {a: terminated for a in self.possible_agents}, {a: truncated for a in self.possible_agents},
                  {a: {**info, "own_ratio": float(self.ratios[i])} for i, a in enumerate(self.possible_agents)})
        if terminated or truncated:
            self.agents = []
        return result

    def close(self):
        self.agents = []


class JointGymEnv(gym.Env):
    """Joint-action Gymnasium wrapper, useful for check_env and integrations.

    IPPO uses MultiClusterEnv directly; this wrapper does not merge the three policies.
    """
    metadata = {"render_modes": []}

    def __init__(self, config: SimulationConfig, **kwargs):
        self.parallel = MultiClusterEnv(config, **kwargs)
        self.observation_space = spaces.Dict(self.parallel.observation_spaces)
        self.action_space = spaces.Dict(self.parallel.action_spaces)

    def reset(self, *, seed=None, options=None):
        super().reset(seed=seed)
        return self.parallel.reset(seed=seed, options=options)

    def step(self, action):
        obs, rewards, terms, truncs, infos = self.parallel.step(action)
        first = self.parallel.possible_agents[0]
        return obs, rewards[first], terms[first], truncs[first], infos[first]

    def close(self):
        self.parallel.close()
