"""Learning curves drawn exclusively from training and validation CSV."""
from pathlib import Path
import statistics

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt

from cloud_sim.storage import read_csv


def plot_training(output: Path):
    rows = read_csv(output / "csv/training.csv")
    validation = read_csv(output / "csv/validation.csv")
    directory = output / "figures"
    directory.mkdir(parents=True, exist_ok=True)
    x = [int(r["episode"]) for r in rows]
    reward = [float(r["episode_reward"]) for r in rows]

    def save(fig, name):
        fig.suptitle("SIMULATION | Independent PPO | CPU")
        fig.tight_layout(rect=(0, 0, 1, 0.94))
        fig.savefig(directory / f"{name}.png", dpi=200, facecolor="white")
        plt.close(fig)

    with plt.rc_context({"axes.grid": True, "grid.alpha": 0.2}):
        fig, ax = plt.subplots(figsize=(10, 4))
        ax.plot(x, reward)
        ax.set(xlabel="Episode", ylabel="Episode reward")
        save(fig, "reward")
        fig, ax = plt.subplots(figsize=(10, 4))
        ax.plot(x, reward, alpha=0.25, label="Raw reward")
        ax.plot(x, [statistics.fmean(reward[max(0, i - 19):i + 1]) for i in range(len(reward))], label="Moving average (up to 20)")
        ax.set(xlabel="Episode", ylabel="Episode reward")
        ax.legend()
        save(fig, "reward_moving_average")
        fig, axes = plt.subplots(3, 1, figsize=(10, 8), sharex=True)
        for ax, key in zip(axes, ("policy_loss", "value_loss", "entropy")):
            ax.plot(x, [float(r[key]) for r in rows])
            ax.set_ylabel(key.replace("_", " "))
        axes[-1].set_xlabel("Episode")
        save(fig, "losses_entropy")
        fig, ax = plt.subplots(figsize=(10, 4))
        ax.plot(x, [float(r["error_rate_pct"]) for r in rows])
        ax.set(xlabel="Episode", ylabel="Training error rate (%)")
        save(fig, "error_rate")
        episodes = sorted({int(r["episode"]) for r in validation})
        fig, axes = plt.subplots(3, 1, figsize=(10, 8), sharex=True)
        for ax, key in zip(axes, ("episode_reward", "latency_mean_ms", "throughput_req_per_s")):
            values = [statistics.fmean(float(r[key]) for r in validation if int(r["episode"]) == episode and r[key] != "")
                      if any(r[key] != "" for r in validation if int(r["episode"]) == episode) else float("nan")
                      for episode in episodes]
            ax.plot(episodes, values, marker="o")
            ax.set_ylabel(key.replace("_", " "))
        axes[-1].set_xlabel("Episode (fixed validation seeds)")
        save(fig, "validation_metrics")
