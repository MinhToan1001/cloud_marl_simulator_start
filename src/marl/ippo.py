"""Three independent actor-critics, clipped PPO and termination-aware GAE."""
from __future__ import annotations

import random
from copy import deepcopy
from pathlib import Path

import numpy as np
import torch
from torch import nn
from torch.distributions import Categorical

from cloud_sim.config import SimulationConfig
from .config import TrainingConfig, training_config_from_dict
from .env import ENV_VERSION, OBSERVATION_SIZE

CHECKPOINT_VERSION = 1


def seed_everything(seed: int, threads: int = 1):
    random.seed(seed)
    np.random.seed(seed % (2 ** 32))
    torch.manual_seed(seed)
    torch.set_num_threads(threads)
    torch.use_deterministic_algorithms(True)


def compute_gae(rewards, values, next_values, terminated, truncated, gamma, gae_lambda):
    """Bootstrap across external time limits, never across true terminal states.

    The recursion stops at either kind of episode boundary; next_values must contain
    the actual final observation value for a truncated transition, not reset obs.
    """
    advantages = torch.zeros_like(rewards)
    carry = torch.zeros((), dtype=rewards.dtype)
    for t in reversed(range(len(rewards))):
        delta = rewards[t] + gamma * next_values[t] * (~terminated[t]).float() - values[t]
        carry = delta + gamma * gae_lambda * (~(terminated[t] | truncated[t])).float() * carry
        advantages[t] = carry
    return advantages, advantages + values


class ActorCritic(nn.Module):
    def __init__(self, hidden_size: int):
        super().__init__()
        self.trunk = nn.Sequential(nn.Linear(OBSERVATION_SIZE, hidden_size), nn.Tanh(),
                                   nn.Linear(hidden_size, hidden_size), nn.Tanh())
        self.actor = nn.Linear(hidden_size, 3)
        self.critic = nn.Linear(hidden_size, 1)
        for layer in self.trunk:
            if isinstance(layer, nn.Linear):
                nn.init.orthogonal_(layer.weight, np.sqrt(2))
                nn.init.zeros_(layer.bias)
        nn.init.orthogonal_(self.actor.weight, 0.01)
        nn.init.orthogonal_(self.critic.weight, 1)
        nn.init.zeros_(self.actor.bias)
        nn.init.zeros_(self.critic.bias)

    def forward(self, observation):
        hidden = self.trunk(observation)
        return self.actor(hidden), self.critic(hidden).squeeze(-1)


class IPPO:
    def __init__(self, agents: list[str], config: TrainingConfig):
        self.agents, self.config = list(agents), config
        # Deliberately separate parameters and optimizers for all three agents.
        self.models = {agent: ActorCritic(config.hidden_size).cpu() for agent in agents}
        self.optimizers = {agent: torch.optim.Adam(self.models[agent].parameters(), lr=config.learning_rate, eps=1e-5)
                           for agent in agents}

    @torch.no_grad()
    def act(self, observations: dict, deterministic: bool = False):
        actions, log_probs, values = {}, {}, {}
        for agent in self.agents:
            logits, value = self.models[agent](torch.as_tensor(observations[agent], dtype=torch.float32))
            distribution = Categorical(logits=logits)
            action = logits.argmax() if deterministic else distribution.sample()
            actions[agent] = int(action.item())
            log_probs[agent] = float(distribution.log_prob(action).item())
            values[agent] = float(value.item())
        return actions, log_probs, values

    def update(self, agent: str, batch: dict) -> dict:
        cfg, model = self.config, self.models[agent]
        obs = torch.tensor(np.asarray(batch["observations"]), dtype=torch.float32)
        actions = torch.tensor(batch["actions"], dtype=torch.int64)
        old_log_probs = torch.tensor(batch["log_probs"], dtype=torch.float32)
        values = torch.tensor(batch["values"], dtype=torch.float32)
        next_values = torch.tensor(batch["next_values"], dtype=torch.float32)
        advantages, returns = compute_gae(
            torch.tensor(batch["rewards"], dtype=torch.float32), values, next_values,
            torch.tensor(batch["terminated"], dtype=torch.bool), torch.tensor(batch["truncated"], dtype=torch.bool),
            cfg.gamma, cfg.gae_lambda)
        advantages = (advantages - advantages.mean()) / advantages.std(unbiased=False).clamp_min(1e-8)
        totals = {key: 0.0 for key in ("policy_loss", "value_loss", "entropy", "clip_fraction", "approx_kl")}
        count = 0
        for _ in range(cfg.update_epochs):
            permutation = torch.randperm(len(actions))
            for start in range(0, len(actions), cfg.minibatch_size):
                indices = permutation[start:start + cfg.minibatch_size]
                logits, predicted_value = model(obs[indices])
                distribution = Categorical(logits=logits)
                log_ratio = distribution.log_prob(actions[indices]) - old_log_probs[indices]
                ratio = log_ratio.exp()
                policy_loss = -torch.minimum(
                    ratio * advantages[indices],
                    ratio.clamp(1 - cfg.clip_ratio, 1 + cfg.clip_ratio) * advantages[indices]).mean()
                value_loss = 0.5 * (predicted_value - returns[indices]).square().mean()
                entropy = distribution.entropy().mean()
                loss = policy_loss + cfg.value_coef * value_loss - cfg.entropy_coef * entropy
                if not torch.isfinite(loss):
                    raise RuntimeError("Non-finite PPO loss")
                self.optimizers[agent].zero_grad(set_to_none=True)
                loss.backward()
                nn.utils.clip_grad_norm_(model.parameters(), cfg.max_grad_norm, error_if_nonfinite=True)
                self.optimizers[agent].step()
                with torch.no_grad():
                    entries = (policy_loss, value_loss, entropy,
                               ((ratio - 1).abs() > cfg.clip_ratio).float().mean(),
                               ((ratio - 1) - log_ratio).mean())
                    for key, value in zip(totals, entries):
                        totals[key] += float(value.item()) * len(indices)
                count += len(indices)
        return {key: value / count for key, value in totals.items()}

    def state(self):
        return {"models": {a: deepcopy(m.state_dict()) for a, m in self.models.items()},
                "optimizers": {a: deepcopy(o.state_dict()) for a, o in self.optimizers.items()}}

    def restore(self, state: dict, optimizers: bool = True):
        if set(state["models"]) != set(self.agents):
            raise ValueError("Checkpoint agent IDs differ from the simulation config")
        for agent in self.agents:
            self.models[agent].load_state_dict(state["models"][agent])
            if optimizers:
                self.optimizers[agent].load_state_dict(state["optimizers"][agent])


def rng_state() -> dict:
    numpy_state = np.random.get_state()
    return {"python": random.getstate(), "torch": torch.get_rng_state(),
            "numpy": [numpy_state[0], numpy_state[1].tolist(), int(numpy_state[2]),
                      int(numpy_state[3]), float(numpy_state[4])]}


def restore_rng(state: dict):
    random.setstate(state["python"])
    torch.set_rng_state(state["torch"])
    name, array, position, gaussian, cached = state["numpy"]
    np.random.set_state((name, np.asarray(array, dtype=np.uint32), position, gaussian, cached))


def save_checkpoint(path: Path, payload: dict):
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(".tmp")
    torch.save(payload, temporary)
    temporary.replace(path)


def read_checkpoint(path: Path) -> dict:
    checkpoint = torch.load(path, map_location="cpu", weights_only=True)
    if (checkpoint.get("schema_version") != CHECKPOINT_VERSION or checkpoint.get("algorithm") != "IPPO"
            or checkpoint.get("env_version") != ENV_VERSION):
        raise ValueError("Unsupported checkpoint schema/algorithm/environment")
    # Early pilot files predate configurable stochastic inference.
    checkpoint["training_config"].setdefault("inference_mode", "greedy")
    return checkpoint


def load_policy(path: Path, simulation: SimulationConfig):
    checkpoint = read_checkpoint(path)
    cfg = training_config_from_dict(checkpoint["training_config"])
    seed_everything(cfg.seed, cfg.torch_threads)
    if checkpoint["simulation_config"]["clusters"] != simulation.to_dict()["clusters"]:
        # Dataclass asdict retains tuples internally; canonical JSON handles list/tuple differences below.
        from cloud_sim.experiments import fingerprint
        if fingerprint(checkpoint["simulation_config"]["clusters"]) != fingerprint(simulation.to_dict()["clusters"]):
            raise ValueError("Checkpoint cluster resources differ from evaluation config")
    policy = IPPO([f"cluster_{c.name}" for c in simulation.clusters], cfg)
    policy.restore(checkpoint["policy_state"], optimizers=False)
    return policy, cfg, checkpoint
