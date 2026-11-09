"""Masked three-policy IPPO with schema-checked checkpoints and frozen validation."""
from copy import deepcopy
from dataclasses import dataclass, asdict
import importlib.metadata as metadata
import json
from pathlib import Path
import random
import time

import numpy as np
import torch
from torch import nn
from torch.distributions import Categorical

from . import ENV_VERSION
from .config import config_from_dict
from .env import PlacementEnv
from .io import file_hash, fingerprint, new_directory, write_csv, write_json
from .schema import manifest, schema_hash


def finite_episode_gae(rewards, values, gamma, gae_lambda):
    """True finite-horizon termination; no external truncation is collected."""
    advantages = torch.zeros_like(rewards)
    carry = torch.zeros((), dtype=rewards.dtype)
    for t in reversed(range(len(rewards))):
        next_value = values[t+1] if t+1 < len(values) else 0.
        delta = rewards[t] + gamma*next_value - values[t]
        carry = delta + gamma*gae_lambda*carry
        advantages[t] = carry
    return advantages, advantages+values


@dataclass(frozen=True)
class TrainingConfig:
    episodes: int = 300
    hidden: int = 32
    lr: float = .0003
    gamma: float = .99
    gae_lambda: float = .95
    clip: float = .2
    entropy: float = .02
    epochs: int = 4
    batch_size: int = 64
    validation_interval: int = 20
    train_seed_start: int = 1000
    validation_seeds: tuple = (20001, 20002, 20003)


def seed_all(seed):
    random.seed(seed)
    np.random.seed(seed)
    torch.manual_seed(seed)
    torch.set_num_threads(1)
    torch.use_deterministic_algorithms(True)


def code_fingerprints():
    return {p.name: file_hash(p) for p in sorted(Path(__file__).parent.glob("*.py"))}


class ActorCritic(nn.Module):
    def __init__(self, size, hidden):
        super().__init__()
        self.trunk = nn.Sequential(nn.Linear(size, hidden), nn.Tanh(), nn.Linear(hidden, hidden), nn.Tanh())
        self.actor, self.critic = nn.Linear(hidden, 4), nn.Linear(hidden, 1)
        for layer in self.trunk:
            if isinstance(layer, nn.Linear):
                nn.init.orthogonal_(layer.weight, np.sqrt(2))
                nn.init.zeros_(layer.bias)
        nn.init.orthogonal_(self.actor.weight, .01)
        nn.init.zeros_(self.actor.bias)

    def forward(self, observation, mask):
        hidden = self.trunk(observation)
        logits = self.actor(hidden).masked_fill(~mask, -1e9)
        return Categorical(logits=logits), self.critic(hidden).squeeze(-1)


class PlacementIPPO:
    def __init__(self, config, training):
        self.config, self.training = config, training
        self.agents = [s.name for s in config.services]
        size = manifest(config)["size"]
        self.models = {a: ActorCritic(size, training.hidden).cpu() for a in self.agents}
        self.optimizers = {a: torch.optim.Adam(m.parameters(), lr=training.lr, eps=1e-5) for a, m in self.models.items()}

    @torch.no_grad()
    def act(self, observations, masks, greedy=False):
        actions, log_probs, values = {}, {}, {}
        for name in self.agents:
            distribution, value = self.models[name](torch.as_tensor(observations[name]), torch.as_tensor(masks[name]))
            action = distribution.logits.argmax() if greedy else distribution.sample()
            actions[name], log_probs[name], values[name] = int(action), float(distribution.log_prob(action)), float(value)
        return actions, log_probs, values

    def update(self, name, batch):
        cfg = self.training
        observations = torch.as_tensor(np.asarray(batch["obs"]), dtype=torch.float32)
        masks = torch.as_tensor(np.asarray(batch["masks"]), dtype=torch.bool)
        actions = torch.tensor(batch["actions"])
        old_log = torch.tensor(batch["logs"])
        values = torch.tensor(batch["values"])
        advantage, returns = finite_episode_gae(torch.tensor(batch["rewards"]), values, cfg.gamma, cfg.gae_lambda)
        advantage = (advantage-advantage.mean())/advantage.std(unbiased=False).clamp_min(1e-8)
        losses = []
        for _ in range(cfg.epochs):
            permutation = torch.randperm(len(actions))
            for start in range(0, len(actions), cfg.batch_size):
                ids = permutation[start:start+cfg.batch_size]
                dist, predicted = self.models[name](observations[ids], masks[ids])
                ratio = (dist.log_prob(actions[ids])-old_log[ids]).exp()
                policy_loss = -torch.minimum(ratio*advantage[ids], ratio.clamp(1-cfg.clip, 1+cfg.clip)*advantage[ids]).mean()
                value_loss = .5*(predicted-returns[ids]).square().mean()
                loss = policy_loss + .5*value_loss - cfg.entropy*dist.entropy().mean()
                if not torch.isfinite(loss):
                    raise RuntimeError("Nonfinite placement PPO loss")
                self.optimizers[name].zero_grad()
                loss.backward()
                gradient = nn.utils.clip_grad_norm_(self.models[name].parameters(), .5, error_if_nonfinite=True)
                self.optimizers[name].step()
                losses.append((float(policy_loss.detach()), float(value_loss.detach()), float(dist.entropy().mean().detach()), float(gradient)))
        return dict(zip(("policy_loss", "value_loss", "entropy", "gradient_norm"), np.asarray(losses).mean(axis=0).tolist()))


def rollout(policy, config, seed, scenario="mixed", collect=False, trace=None):
    env = PlacementEnv(config)
    observations = env.reset(seed, scenario, trace)
    buffers = {name: {key: [] for key in ("obs", "masks", "actions", "logs", "values", "rewards")} for name in env.agents}
    reward_sum = 0.
    done = False
    while not done:
        masks = env.masks()
        actions, logs, values = policy.act(observations, masks)
        next_obs, rewards, done, _ = env.step(actions)
        if collect:
            for name in env.agents:
                for key, value in (("obs", observations[name]), ("masks", masks[name]), ("actions", actions[name]),
                                   ("logs", logs[name]), ("values", values[name]), ("rewards", float(rewards[name]))):
                    buffers[name][key].append(value)
        observations = next_obs
        reward_sum += float(rewards[env.agents[0]])
    return env, reward_sum/env.decision, buffers


def validate(policy, config, seeds):
    scores = []
    with torch.random.fork_rng(devices=[]):
        for seed in seeds:
            torch.manual_seed(seed*9176)
            _, score, _ = rollout(policy, config, seed)
            scores.append(score)
    return float(np.mean(scores))


def save_checkpoint(path, policy, seed, episode, score, history, validation):
    payload = {"schema_version": 1, "algorithm": "IPPO", "env_version": ENV_VERSION,
               "action_kind": "workload_placement", "schema": manifest(policy.config), "schema_hash": schema_hash(policy.config),
               "simulation_config": policy.config.to_dict(), "config_hash": fingerprint(policy.config.to_dict()),
               "training_config": asdict(policy.training), "policy_seed": seed, "episode": episode, "validation_score": score,
               "models": {a: m.state_dict() for a, m in policy.models.items()},
               "optimizers": {a: o.state_dict() for a, o in policy.optimizers.items()},
               "rng_torch": torch.get_rng_state(), "rng_python": random.getstate(),
               "code_fingerprints": code_fingerprints(), "history": history, "validation": validation,
               "packages": {name: metadata.version(name) for name in ("torch", "numpy")},
               "profile_sha256": policy.config.profile_sha256,
               "inference": "sample with isolated fixed per-run policy RNG", "device": "cpu"}
    temporary = path.with_suffix(".tmp")
    torch.save(payload, temporary)
    temporary.replace(path)


def load_policy(path, config, enforce_code=True):
    saved = torch.load(path, map_location="cpu", weights_only=True)
    if saved.get("env_version") != ENV_VERSION or saved.get("action_kind") != "workload_placement" or saved.get("algorithm") != "IPPO" or saved.get("schema_version") != 1:
        raise ValueError("Checkpoint is not a placement IPPO checkpoint")
    if saved["schema_hash"] != schema_hash(config) or fingerprint(saved["schema"]) != schema_hash(config):
        raise ValueError("Observation/action manifest mismatch")
    if saved["config_hash"] != fingerprint(config.to_dict()):
        raise ValueError("Placement resource/reward/profile config mismatch")
    if enforce_code and saved["code_fingerprints"] != code_fingerprints():
        raise ValueError("Placement implementation fingerprint changed")
    values = dict(saved["training_config"])
    values["validation_seeds"] = tuple(values["validation_seeds"])
    policy = PlacementIPPO(config, TrainingConfig(**values))
    if set(saved["models"]) != set(policy.agents):
        raise ValueError("Checkpoint workload ownership mismatch")
    for name in policy.agents:
        policy.models[name].load_state_dict(saved["models"][name], strict=True)
    return policy, saved


def train(config, output, policy_seed, training=None, deadline=None):
    training = training or TrainingConfig()
    if training.episodes < 1 or set(training.validation_seeds).intersection(range(training.train_seed_start, training.train_seed_start+training.episodes)):
        raise ValueError("Invalid episodes or overlapping validation seeds")
    output = new_directory(output)
    seed_all(policy_seed)
    policy = PlacementIPPO(config, training)
    initial_weights = {a: {k: v.clone() for k, v in m.state_dict().items()} for a, m in policy.models.items()}
    history, validation = [], []
    best_score = validate(policy, config, training.validation_seeds)
    validation.append({"episode": 0, "score": best_score})
    best_episode = 0
    save_checkpoint(output/"best.pt", policy, policy_seed, 0, best_score, history, validation)
    started = time.monotonic()
    scenarios = ("mixed", "stable", "ramp_up", "spike", "recovery", "periodic", "shift")
    for episode in range(1, training.episodes+1):
        if deadline is not None and time.monotonic() >= deadline:
            raise TimeoutError("Approved total training time cap reached")
        env, reward, buffers = rollout(policy, config, training.train_seed_start+episode-1, scenarios[(episode-1) % len(scenarios)], True)
        losses = [policy.update(name, buffers[name]) for name in env.agents]
        row = {"episode": episode, "workload_seed": training.train_seed_start+episode-1, "scenario": env.trace["scenario"],
               "mean_reward": reward, **{k: float(np.mean([v[k] for v in losses])) for k in losses[0]}, **env.summary()}
        row = json.loads(json.dumps(row))
        history.append(row)
        if episode % training.validation_interval == 0 or episode == training.episodes:
            score = validate(policy, config, training.validation_seeds)
            validation.append({"episode": episode, "score": score})
            if score > best_score:
                best_score, best_episode = score, episode
                save_checkpoint(output/"best.pt", policy, policy_seed, episode, score, deepcopy(history), deepcopy(validation))
            save_checkpoint(output/"last.pt", policy, policy_seed, episode, score, deepcopy(history), deepcopy(validation))
            print(f"placement seed={policy_seed} episode={episode}/{training.episodes} validation={score:.4f} best={best_score:.4f}@{best_episode}", flush=True)
    changed = {a: any(not torch.equal(initial_weights[a][k], value) for k, value in model.state_dict().items()) for a, model in policy.models.items()}
    metadata_result = {"policy_seed": policy_seed, "episodes": training.episodes, "best_episode": best_episode,
                       "best_score": best_score, "elapsed_s": time.monotonic()-started, "weights_changed": changed,
                       "environment_steps": training.episodes*(config.seconds//config.decision_s), "device": "cpu",
                       "packages": {name: metadata.version(name) for name in ("torch", "numpy")},
                       "training_config": asdict(training), "schema_hash": schema_hash(config),
                       "config_hash": fingerprint(config.to_dict()), "source": "simulated"}
    write_csv(output/"training.csv", history)
    write_csv(output/"validation.csv", validation)
    write_json(output/"metadata.json", metadata_result)
    return metadata_result
