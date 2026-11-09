"""Package code, documentation, legacy data, checkpoints and measured results."""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
from zipfile import ZIP_DEFLATED, ZipFile

ROOT = Path(__file__).resolve().parents[2]
EXCLUDED = {".venv", "venv", ".git", ".pytest_cache", "__pycache__", "dist", ".aws", ".codex", ".agents", "node_modules", "build"}


def package(output: Path) -> Path:
    output.parent.mkdir(parents=True, exist_ok=True)
    files = sorted(path for path in ROOT.rglob("*") if path.is_file()
                   and not any(part in EXCLUDED for part in path.relative_to(ROOT).parts)
                   and path.resolve() != output.resolve()
                   and not any(part.endswith(".egg-info") for part in path.parts)
                   and not path.name.endswith(".local.json")
                   and not path.name.startswith(".env")
                   and path.suffix not in {".pyc", ".tmp"}
                   and (path.suffix != ".zip" or path.parent in (ROOT / "data/archive", ROOT / "docs/archive")))
    manifest = []
    with ZipFile(output, "w", ZIP_DEFLATED) as archive:
        for path in files:
            relative = path.relative_to(ROOT).as_posix()
            content = path.read_bytes()
            archive.writestr(relative, content)
            manifest.append({"path": relative, "bytes": len(content), "sha256": hashlib.sha256(content).hexdigest()})
        archive.writestr("PACKAGE_MANIFEST.json", json.dumps({
            "data_source": "simulation", "files": manifest,
            "excluded": sorted(EXCLUDED), "note": "Recreate .venv with requirements.txt; no real cloud credentials included.",
        }, indent=2))
    with ZipFile(output) as archive:
        broken = archive.testzip()
        if broken:
            raise RuntimeError(f"Corrupt archive member: {broken}")
        for entry in manifest:
            if hashlib.sha256(archive.read(entry["path"])).hexdigest() != entry["sha256"]:
                raise RuntimeError(f"Archive checksum mismatch: {entry['path']}")
    print(f"Verified {len(files)} files: {output.resolve()} ({output.stat().st_size} bytes)")
    return output


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=ROOT / "dist/cloud_marl.zip")
    package(parser.parse_args().output)


if __name__ == "__main__":
    main()
