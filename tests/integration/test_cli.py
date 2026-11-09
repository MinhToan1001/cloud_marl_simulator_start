"""The documented commands work from an installed source checkout."""
from pathlib import Path
import subprocess
import sys

import pytest

ROOT = Path(__file__).resolve().parents[2]


@pytest.mark.parametrize("command", [
    "baselines", "train", "evaluate", "plot", "serve", "pipeline", "verify_api",
    "verify_results", "package", "cluster", "cluster_smoke",
])
def test_command_help(command):
    result = subprocess.run([sys.executable, "-m", f"cli.{command}", "--help"],
                            cwd=ROOT, capture_output=True, text=True, timeout=20)
    assert result.returncode == 0, result.stderr
    assert "usage:" in result.stdout
