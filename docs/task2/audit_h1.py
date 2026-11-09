"""Offline H1 evidence: inspect existing artifacts; never train or contact a cluster.

Run from the project root with .venv/Scripts/python.exe and a NEW --output path.
Only the supplied output directory is written. Checkpoints load weights_only=True.
"""
from __future__ import annotations

import argparse
import hashlib
import importlib.metadata as metadata
import importlib.util
import itertools
import json
import platform
import subprocess
import sys
import xml.etree.ElementTree as ET
import zipfile
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def sha256(path):
    result = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            result.update(block)
    return result.hexdigest()


def protected_manifest():
    paths = []
    for directory in ("src", "configs", "tests", "deploy", "requirements", "results", "data", "legacy", "docs"):
        paths.extend(p for p in (ROOT / directory).rglob("*") if p.is_file()
                     and "__pycache__" not in p.parts
                     and p.suffix not in {".pyc", ".pyo"}
                     and not p.is_relative_to(ROOT / "docs/task2"))
    paths.extend(ROOT / name for name in ("README.md", "requirements.txt", "pyproject.toml", "pytest.ini", ".gitignore", "cluster-a-collector.tgz")
                 if (ROOT / name).is_file())
    return {p.relative_to(ROOT).as_posix(): {"bytes": p.stat().st_size, "sha256": sha256(p)}
            for p in sorted(set(paths))}


def write_json(directory, name, value):
    (directory / name).write_text(json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False) + "\n", encoding="utf-8")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--roadmap", type=Path, required=True)
    parser.add_argument("--prompt", type=Path, required=True)
    args = parser.parse_args()
    output = args.output.resolve()
    if not output.is_relative_to(ROOT / "docs/task2/evidence"):
        parser.error("Output must be a new directory inside docs/task2/evidence")
    # Validate input paths before creating any output.
    for path in (args.roadmap, args.prompt):
        if not path.is_file():
            parser.error(f"Missing input: {path}")
    output.mkdir(parents=True, exist_ok=False)
    before = protected_manifest()
    write_json(output, "protected_before.json", before)
    write_json(output, "inputs.json", {
        "created_at_utc": datetime.now(timezone.utc).isoformat(),
        "workspace": str(ROOT),
        "inputs": [{"path": str(p.resolve()), "sha256": sha256(p)} for p in (args.roadmap, args.prompt)],
        "scope": "PHAN 0 and H1 only; H2-H5 require placement design approval",
    })
    with zipfile.ZipFile(args.roadmap) as archive:
        document = ET.fromstring(archive.read("word/document.xml"))
    ns = {"w": "http://schemas.openxmlformats.org/wordprocessingml/2006/main"}
    # Only w:t contains displayed text; do not execute document fields/instructions.
    paragraphs = ["".join(t.text or "" for t in p.findall(".//w:t", ns))
                  for p in document.findall(".//w:body//w:p", ns)]
    start = next(i for i, line in enumerate(paragraphs) if line.startswith("Task 2."))
    end = next(i for i in range(start + 1, len(paragraphs)) if paragraphs[i].startswith("GIAI ĐOẠN 2"))
    (output / "roadmap_task2.txt").write_text("\n".join(paragraphs[start:end]) + "\n", encoding="utf-8")

    package_names = ("numpy", "torch", "gymnasium", "matplotlib", "fastapi", "uvicorn", "httpx", "pytest", "locust", "PyYAML", "setuptools", "cloud-marl-simulator")
    packages = {}
    for name in package_names:
        try:
            packages[name] = metadata.version(name)
        except metadata.PackageNotFoundError:
            packages[name] = None
    importable_before_path = {name: importlib.util.find_spec(name) is not None for name in ("cloud_sim", "marl", "integration")}
    pip_check = subprocess.run([sys.executable, "-m", "pip", "check"], capture_output=True, text=True, check=False)
    (output / "pip_check.txt").write_text(pip_check.stdout + pip_check.stderr, encoding="utf-8")
    write_json(output, "environment.json", {
        "python": platform.python_version(), "executable": sys.executable,
        "prefix": sys.prefix, "base_prefix": sys.base_prefix,
        "platform": platform.platform(), "packages": packages,
        "pyvenv_cfg": (ROOT / ".venv/pyvenv.cfg").read_text(encoding="utf-8"),
        "importable_before_audit_src_insertion": importable_before_path,
        "pip_check_exit_code": pip_check.returncode,
        "git_directory_present": (ROOT / ".git").is_dir(),
        "roadmap_in_docs_present": (ROOT / "docs/ROADMAP.docx").is_file(),
    })

    sys.path.insert(0, str(ROOT / "src"))
    import numpy as np
    import torch
    from cloud_sim.config import load_config
    from cloud_sim.experiments import fingerprint
    from marl.config import load_training_config
    from marl.env import ENV_VERSION, OBSERVATION_SIZE, MultiClusterEnv
    from marl.ippo import load_policy, read_checkpoint

    simulation = load_config(ROOT / "configs/simulation.json")
    training = load_training_config(ROOT / "configs/marl.json")
    checkpoint_results = []
    for path in sorted((ROOT / "results").rglob("*.pt")):
        checkpoint = read_checkpoint(path)
        policy, cfg, _ = load_policy(path, simulation)
        env = MultiClusterEnv(simulation, cfg.reward, cfg.action_step)
        observations, _ = env.reset(seed=30001, options={"workload": [270, 270]})
        actions, _, _ = policy.act(observations, deterministic=True)
        _, rewards, _, _, infos = env.step(actions)
        assert all(env.action_space(a).contains(actions[a]) for a in env.possible_agents)
        assert all(np.isfinite(value) for value in rewards.values())
        shapes = {a: {key: list(tensor.shape) for key, tensor in model.state_dict().items()}
                  for a, model in policy.models.items()}
        checkpoint_results.append({
            "path": path.relative_to(ROOT).as_posix(), "sha256": sha256(path),
            "schema_version": checkpoint["schema_version"], "algorithm": checkpoint["algorithm"],
            "env_version": checkpoint["env_version"], "episode": checkpoint["episode"],
            "best_episode": checkpoint["best_episode"], "best_score": checkpoint["best_score"],
            "history_length": len(checkpoint["history"]), "agents": policy.agents,
            "parameter_count_per_agent": {a: sum(p.numel() for p in model.parameters()) for a, model in policy.models.items()},
            "shapes": shapes,
            "matches_current_simulation": fingerprint(checkpoint["simulation_config"]) == fingerprint(simulation.to_dict()),
            "matches_current_training": fingerprint(checkpoint["training_config"]) == fingerprint(training.to_dict()),
            "training_config": cfg.to_dict(), "load_and_one_step_routing_inference": "passed",
            "greedy_probe_actions": actions, "greedy_probe_allocation": infos[env.possible_agents[0]]["allocation"],
            "placement_compatible": False,
            "placement_reason": "parallel-ratios-v1 actions adjust routing ratios; no workload placement state or transition",
        })
    write_json(output, "checkpoints.json", checkpoint_results)

    action_results = []
    for joint in itertools.product(range(3), repeat=3):
        env = MultiClusterEnv(simulation, training.reward, training.action_step)
        initial, _ = env.reset(seed=30001, options={"workload": [270, 270]})
        observation, reward, _, _, infos = env.step(dict(zip(env.possible_agents, joint)))
        info = infos[env.possible_agents[0]]
        assert sum(info["allocation"]) == 270
        assert all(env.observation_space(a).contains(o) for a, o in observation.items())
        action_results.append({"actions": list(joint), "ratios": info["ratios"], "allocation": info["allocation"],
                               "reward": reward[env.possible_agents[0]], "reward_components": info["reward_components"]})
    env = MultiClusterEnv(simulation)
    initial, _ = env.reset(seed=30001, options={"workload": [270, 270]})
    write_json(output, "routing_probe.json", {
        "data_source": "simulation", "purpose": "H1 action semantics diagnostic, not H4 evaluation",
        "env_version": ENV_VERSION, "observation_size": OBSERVATION_SIZE,
        "initial_observations": {a: value.tolist() for a, value in initial.items()},
        "action_step": training.action_step, "joint_actions": action_results,
        "all_joint_actions_preserve_demand_and_spaces": True,
        "effective_capacity": {c.name: c.effective_capacity for c in simulation.clusters},
        "max_queue": {c.name: c.max_queue for c in simulation.clusters},
        "device_used": "cpu", "torch_version": torch.__version__,
    })
    after = protected_manifest()
    changes = {name: {"before": before.get(name), "after": after.get(name)}
               for name in before.keys() | after.keys() if before.get(name) != after.get(name)}
    write_json(output, "preservation.json", {"checked_files": len(before), "changes": changes, "unchanged": not changes})
    if changes:
        raise RuntimeError("Protected artifacts changed during audit")
    print(json.dumps({"output": str(output), "protected_files_unchanged": len(before), "checkpoints": len(checkpoint_results),
                      "routing_joint_actions_checked": len(action_results), "pip_check_exit_code": pip_check.returncode}))


if __name__ == "__main__":
    main()
