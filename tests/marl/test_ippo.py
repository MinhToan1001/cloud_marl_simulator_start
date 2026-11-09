from dataclasses import replace

import numpy as np
import pytest
import torch

from cloud_sim.config import PhaseConfig
from marl.config import TrainingConfig
from marl.env import MultiClusterEnv
from marl.ippo import IPPO, compute_gae, load_policy, read_checkpoint, seed_everything
from marl.training import train
from marl.training import run_policy_episode


def test_gae_termination_and_truncation_by_hand():
    args = [torch.tensor([1., 1.]), torch.tensor([0.5, 0.25]), torch.tensor([0.25, 9.])]
    adv, returns = compute_gae(*args, torch.tensor([False, True]), torch.tensor([False, False]), 1, 1)
    torch.testing.assert_close(adv, torch.tensor([1.5, 0.75]))
    torch.testing.assert_close(returns, torch.tensor([2., 1.]))
    _, returns = compute_gae(*args, torch.tensor([False, False]), torch.tensor([False, True]), 1, 1)
    torch.testing.assert_close(returns, torch.tensor([11., 10.]))


def test_agents_have_independent_parameters(config):
    seed_everything(5)
    policy = IPPO(MultiClusterEnv(config).possible_agents, TrainingConfig())
    pointers = [next(model.parameters()).data_ptr() for model in policy.models.values()]
    assert len(set(pointers)) == 3


def test_training_resume_matches_uninterrupted_and_updates_weights(config, tmp_path):
    simulation = replace(config, phases=(PhaseConfig("spike", 6, 450, 450),))
    training = TrainingConfig(episodes=4, hidden_size=8, update_epochs=2, validation_interval=1,
                              validation_seeds=(20001,), minibatch_size=4)
    full = tmp_path / "full"
    split = tmp_path / "split"
    train(simulation, training, full, progress=False)
    train(simulation, replace(training, episodes=2), split, progress=False)
    train(simulation, training, split, resume=split / "checkpoints/last.pt", progress=False)
    a, b = [read_checkpoint(p / "checkpoints/last.pt") for p in (full, split)]
    assert a["history"] == b["history"]
    assert a["agent_history"] == b["agent_history"]
    for agent in a["policy_state"]["models"]:
        for key, value in a["policy_state"]["models"][agent].items():
            torch.testing.assert_close(value, b["policy_state"]["models"][agent][key], rtol=0, atol=0)
    seed_everything(training.seed)
    initial = IPPO(MultiClusterEnv(simulation).possible_agents, training)
    for agent, model in initial.models.items():
        assert any(not torch.equal(value, a["policy_state"]["models"][agent][key]) for key, value in model.state_dict().items())
    policy, _, checkpoint = load_policy(split / "checkpoints/best.pt", simulation)
    assert checkpoint["episode"] == b["best_episode"]
    assert np.isfinite([r["policy_loss"] for r in b["history"]]).all()
    with pytest.raises(ValueError, match="already exists"):
        train(simulation, training, split, progress=False)
    with pytest.raises(ValueError, match="same simulation"):
        train(simulation, replace(training, episodes=5, learning_rate=0.01), split,
              resume=split / "checkpoints/last.pt", progress=False)


def test_seed_split_validation():
    with pytest.raises(ValueError, match="disjoint"):
        TrainingConfig(episodes=10, train_seed_start=100, validation_seeds=(101,))


def test_validation_is_reproducible_and_does_not_change_training_rng(config):
    simulation = replace(config, phases=(PhaseConfig("stable", 4, 90, 90),))
    training = TrainingConfig(hidden_size=8)
    seed_everything(training.seed)
    policy = IPPO(MultiClusterEnv(simulation).possible_agents, training)
    before = torch.get_rng_state().clone()
    first, reward = run_policy_episode(policy, simulation, training, 20001)
    torch.testing.assert_close(before, torch.get_rng_state(), rtol=0, atol=0)
    second, repeated_reward = run_policy_episode(policy, simulation, training, 20001)
    assert first.rows == second.rows and reward == repeated_reward
