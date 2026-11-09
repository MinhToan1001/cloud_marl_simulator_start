"""Build an offline isolated venv from copies of already installed dependencies.

No global installs, .pth from global site-packages, links, secrets or downloads.
Only base requirements selected by Windows/Python markers are copied. Source
libraries remain unchanged. The resulting venv has its own pip and package files.
"""
import importlib.metadata as metadata
from pathlib import Path
import shutil
import sys
import venv

from packaging.requirements import Requirement
from packaging.utils import canonicalize_name

ROOT = Path(__file__).resolve().parents[2]
target = ROOT / ".venv-task2"
if target.exists():
    raise SystemExit("Refusing to replace an existing task2 runtime")
venv.EnvBuilder(with_pip=True, system_site_packages=False).create(target)
site = target / "Lib/site-packages"
pending = ["numpy", "torch", "matplotlib", "pytest", "psutil"]
seen = set()
while pending:
    name = canonicalize_name(pending.pop())
    if name in seen:
        continue
    dist = metadata.distribution(name)
    seen.add(name)
    for raw in dist.requires or []:
        requirement = Requirement(raw)
        if requirement.marker is None or requirement.marker.evaluate({"extra": ""}):
            installed = metadata.version(requirement.name)
            if installed not in requirement.specifier:
                raise RuntimeError(f"Incompatible base dependency: {requirement}; installed {installed}")
            pending.append(requirement.name)
    for relative in dist.files or []:
        if ".." in relative.parts or relative.suffix == ".pyc":
            continue
        source = Path(dist.locate_file(relative))
        destination = site / relative
        if source.is_file():
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source, destination)
    print(f"copied {dist.metadata['Name']}=={dist.version}", flush=True)
(site / "task2-project.pth").write_text(str(ROOT / "src") + "\n", encoding="utf-8")
print(f"Isolated runtime: {target}; {len(seen)} distributions", flush=True)
