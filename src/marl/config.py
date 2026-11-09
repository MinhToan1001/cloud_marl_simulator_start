from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from pathlib import Path

from cloud_sim.config import integer, number


@dataclass(frozen=True)
class RewardConfig:
    throughput: float = 2.0
    latency: float = 0.4
    error: float = 4.0
    queue: float = 0.5
    overload: float = 0.2
    imbalance: float = 0.3
    switching: float = 0.1
    terminal_queue: float = 1.0
    latency_scale_ms: float = 1000.0

    def __post_init__(self):
        for key, value in asdict(self).items():
            number(key, value, positive=key == "latency_scale_ms")


@dataclass(frozen=True)
class TrainingConfig:
    seed: int = 7
    episodes: int = 300
    train_seed_start: int = 1000
    validation_seeds: tuple[int, ...] = (20001, 20002, 20003)
    hidden_size: int = 32
    learning_rate: float = 0.0003
    gamma: float = 0.99
    gae_lambda: float = 0.95
    clip_ratio: float = 0.2
    value_coef: float = 0.5
    entropy_coef: float = 0.02
    max_grad_norm: float = 0.5
    update_epochs: int = 4
    minibatch_size: int = 64
    validation_interval: int = 20
    torch_threads: int = 1
    action_step: float = 0.03
    inference_mode: str = "sample"
    reward: RewardConfig = field(default_factory=RewardConfig)

    def __post_init__(self):
        for key in ("seed", "train_seed_start"):
            integer(key, getattr(self, key))
        for key in ("episodes", "hidden_size", "update_epochs", "minibatch_size", "validation_interval", "torch_threads"):
            integer(key, getattr(self, key), 1)
        for key in ("learning_rate", "max_grad_norm", "action_step"):
            number(key, getattr(self, key), positive=True)
        for key in ("gamma", "gae_lambda", "clip_ratio"):
            number(key, getattr(self, key), positive=True)
            if getattr(self, key) > 1:
                raise ValueError(f"{key} must be <= 1")
        for key in ("value_coef", "entropy_coef"):
            number(key, getattr(self, key))
        if self.inference_mode not in {"sample", "greedy"}:
            raise ValueError("inference_mode must be sample or greedy")
        if not self.validation_seeds or len(set(self.validation_seeds)) != len(self.validation_seeds):
            raise ValueError("Validation seeds must be nonempty and unique")
        for seed in self.validation_seeds:
            integer("validation seed", seed)
        if set(self.validation_seeds).intersection(range(self.train_seed_start, self.train_seed_start + self.episodes)):
            raise ValueError("Training and validation seeds must be disjoint")

    def to_dict(self):
        return asdict(self)


def training_config_from_dict(data: dict) -> TrainingConfig:
    values = dict(data)
    values["validation_seeds"] = tuple(values.get("validation_seeds", TrainingConfig.validation_seeds))
    values["reward"] = RewardConfig(**values.get("reward", {}))
    try:
        return TrainingConfig(**values)
    except TypeError as exc:
        raise ValueError(f"Invalid training config: {exc}") from exc


def load_training_config(path: str | Path) -> TrainingConfig:
    return training_config_from_dict(json.loads(Path(path).read_text(encoding="utf-8-sig")))
