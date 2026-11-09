"""Episode-boundary resumable training; validation-only checkpoint selection."""
from __future__ import annotations

import json
import platform
import statistics
import time
from copy import deepcopy
from pathlib import Path

import numpy as np
import torch

from cloud_sim.config import SimulationConfig
from cloud_sim.experiments import fingerprint
from cloud_sim.storage import write_csv
from .config import TrainingConfig
from .env import ENV_VERSION, MultiClusterEnv
from .ippo import (CHECKPOINT_VERSION, IPPO, read_checkpoint, restore_rng, rng_state,
                   save_checkpoint, seed_everything)


def run_policy_episode(policy: IPPO, simulation: SimulationConfig, training: TrainingConfig,
                       seed: int, workload: list[int] | None = None):
    env = MultiClusterEnv(simulation, training.reward, training.action_step)
    observations, _ = env.reset(seed=seed, options={"workload": workload} if workload is not None else None)
    # Fixed policy RNG, isolated from training and exogenous workload generation.
    # Stochastic policies are evaluated as distributions rather than replacing
    # them with a potentially very different argmax controller.
    with torch.random.fork_rng(devices=[]):
        torch.manual_seed((training.seed * 1000003 + seed * 9176) % (2 ** 63 - 1))
        while env.agents:
            actions, _, _ = policy.act(observations, deterministic=training.inference_mode == "greedy")
            observations, _, _, _, _ = env.step(actions)
    return env.session.result(), env.episode_reward


def validate(policy, simulation, training, episode):
    rows = []
    for seed in training.validation_seeds:
        result, reward = run_policy_episode(policy, simulation, training, seed)
        rows.append({"episode": episode, "seed": seed, "episode_reward": reward,
                     "mean_step_reward": reward / result.summary["duration_s"], **result.summary})
    return rows, statistics.fmean(r["mean_step_reward"] for r in rows)


def collect_episode(policy, env, seed):
    observations, _ = env.reset(seed=seed)
    keys = ("observations", "actions", "log_probs", "values", "rewards", "terminated", "truncated")
    buffers = {agent: {key: [] for key in keys} for agent in env.possible_agents}
    while env.agents:
        actions, log_probs, values = policy.act(observations)
        next_observations, rewards, terminated, truncated, _ = env.step(actions)
        for agent in env.possible_agents:
            values_by_key = (observations[agent], actions[agent], log_probs[agent], values[agent],
                             rewards[agent], terminated[agent], truncated[agent])
            for key, value in zip(keys, values_by_key):
                buffers[agent][key].append(value)
        observations = next_observations
    _, _, final_values = policy.act(observations, deterministic=True)
    for agent, batch in buffers.items():
        batch["next_values"] = batch["values"][1:] + [final_values[agent]]
    return buffers


def train(simulation: SimulationConfig, config: TrainingConfig, output: Path,
          resume: Path | None = None, progress: bool = True) -> dict:
    started = time.monotonic()
    if resume is None and ((output / "checkpoints/last.pt").exists() or (output / "csv/training.csv").exists()):
        raise ValueError("Training output already exists. Use --resume or a new --output directory.")
    seed_everything(config.seed, config.torch_threads)
    env = MultiClusterEnv(simulation, config.reward, config.action_step)
    policy = IPPO(env.possible_agents, config)
    history, agent_history, validation_history = [], [], []
    start_episode, best_score, best_episode, best_state = 0, -float("inf"), 0, None
    previous_elapsed = 0.0
    if resume:
        checkpoint = read_checkpoint(resume)
        original = {k: v for k, v in checkpoint["training_config"].items() if k != "episodes"}
        requested = {k: v for k, v in config.to_dict().items() if k != "episodes"}
        if fingerprint(original) != fingerprint(requested) or fingerprint(checkpoint["simulation_config"]) != fingerprint(simulation.to_dict()):
            raise ValueError("Resume requires the same simulation and hyperparameters, except total episodes")
        start_episode = checkpoint["episode"]
        if config.episodes <= start_episode:
            raise ValueError("Target episodes must exceed completed episodes when resuming")
        policy.restore(checkpoint["policy_state"])
        history, agent_history = checkpoint["history"], checkpoint["agent_history"]
        validation_history = checkpoint["validation_history"]
        best_score, best_episode = checkpoint["best_score"], checkpoint["best_episode"]
        best_state = checkpoint.get("best_state", deepcopy(checkpoint))
        previous_elapsed = checkpoint.get("elapsed_seconds", 0.0)
        restore_rng(checkpoint["rng_state"])
    else:
        initial_rows, best_score = validate(policy, simulation, config, 0)
        validation_history.extend(initial_rows)

    def payload(episode):
        return {"schema_version": CHECKPOINT_VERSION, "algorithm": "IPPO", "env_version": ENV_VERSION,
                "episode": episode, "best_episode": best_episode, "best_score": best_score,
                "simulation_config": simulation.to_dict(), "training_config": config.to_dict(),
                "policy_state": policy.state(), "rng_state": rng_state(),
                "history": deepcopy(history), "agent_history": deepcopy(agent_history),
                "validation_history": deepcopy(validation_history),
                "elapsed_seconds": previous_elapsed + time.monotonic() - started}

    checkpoint_dir = output / "checkpoints"
    if best_state is None:
        best_state = payload(0)
    # Preserve the selected weights while recording the complete planned train
    # seed range after extending a run; testing must exclude that whole range.
    best_state["training_config"] = config.to_dict()
    save_checkpoint(checkpoint_dir / "best.pt", best_state)
    for episode in range(start_episode + 1, config.episodes + 1):
        seed = config.train_seed_start + episode - 1
        batches = collect_episode(policy, env, seed)
        losses = []
        for agent in env.possible_agents:
            loss = policy.update(agent, batches[agent])
            losses.append(loss)
            agent_history.append({"episode": episode, "agent": agent, **loss})
        metrics = env.session.result().summary
        history.append({"episode": episode, "workload_seed": seed, "episode_reward": env.episode_reward,
                        "mean_step_reward": env.episode_reward / metrics["duration_s"],
                        **{key: statistics.fmean(row[key] for row in losses) for key in losses[0]},
                        **metrics})
        if episode % config.validation_interval == 0 or episode == config.episodes:
            rows, score = validate(policy, simulation, config, episode)
            validation_history.extend(rows)
            if score > best_score:
                best_score, best_episode = score, episode
                best_state = payload(episode)
                save_checkpoint(checkpoint_dir / "best.pt", best_state)
            state = payload(episode)
            state["best_state"] = best_state
            save_checkpoint(checkpoint_dir / "last.pt", state)
            write_csv(output / "csv/training.csv", history)
            write_csv(output / "csv/agent_training.csv", agent_history)
            write_csv(output / "csv/validation.csv", validation_history)
            if progress:
                print(f"episode={episode}/{config.episodes} reward={env.episode_reward:.3f} "
                      f"validation={score:.4f} best={best_score:.4f}@{best_episode}", flush=True)
    metadata = {
        "data_source": "simulation", "algorithm": "IPPO", "device": "cpu",
        "completed_episodes": config.episodes, "environment_steps": config.episodes * simulation.seconds,
        "best_episode": best_episode, "best_validation_mean_step_reward": best_score,
        "elapsed_seconds": previous_elapsed + time.monotonic() - started,
        "training_seed_range": [config.train_seed_start, config.train_seed_start + config.episodes - 1],
        "validation_seeds": list(config.validation_seeds), "resumed_from": str(resume) if resume else None,
        "simulation_config": simulation.to_dict(), "training_config": config.to_dict(),
        "python": platform.python_version(), "torch": torch.__version__, "numpy": np.__version__,
        "checkpoint_interval_episodes": config.validation_interval,
        "inference_mode": config.inference_mode,
    }
    (output / "metadata.json").write_text(json.dumps(metadata, indent=2), encoding="utf-8")
    (output / "report.md").write_text(
        f"# IPPO training — simulation only\n\n"
        f"Completed episodes: {config.episodes}; environment steps: {metadata['environment_steps']}.\n"
        f"Device: CPU; elapsed seconds: {metadata['elapsed_seconds']:.2f}.\n"
        f"Training workload seeds: {metadata['training_seed_range']}; validation: {list(config.validation_seeds)}.\n"
        f"Best episode: {best_episode}; validation mean step reward: {best_score:.6f}.\n\n"
        "Three independent actor-critics; no weight sharing or imitation/pretraining. "
        "Validation chooses best.pt; testing is performed only by evaluate_marl.py. "
        "best.pt may be episode 0 if no learned policy improved validation.\n\n"
        "csv/training.csv and csv/agent_training.csv contain measured PPO losses/entropy and rollout metrics; "
        f"csv/validation.csv records {config.inference_mode} validation with an isolated fixed policy RNG. figures/ contains learning curves.\n"
        "Resume is exact at saved episode boundaries with unchanged hyperparameters except total episodes. "
        "last.pt includes optimizer and RNG states and the best checkpoint snapshot.\n", encoding="utf-8")
    return metadata
