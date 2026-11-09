"""CSV I/O including the original output/workload.csv schema."""
from __future__ import annotations

import csv
from pathlib import Path


def write_csv(path: Path, rows: list[dict]) -> None:
    if not rows:
        raise ValueError(f"Refusing to write an empty dataset: {path}")
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8-sig", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)


def read_csv(path: Path) -> list[dict]:
    with path.open(encoding="utf-8-sig", newline="") as stream:
        return list(csv.DictReader(stream))


def read_workload(path: Path, seed: int) -> list[dict]:
    rows = read_csv(path)
    if not rows:
        raise ValueError("Workload CSV is empty")
    if "seed" in rows[0]:
        rows = [r for r in rows if int(r["seed"]) == seed]
        if not rows:
            raise ValueError(f"No workload rows for seed {seed}")
    result = []
    for second, row in enumerate(rows):
        if int(row["second"]) != second:
            raise ValueError("Workload seconds must be ordered, contiguous, and start at zero")
        incoming = int(row["incoming_requests"])
        if incoming < 0:
            raise ValueError("incoming_requests must be nonnegative")
        result.append({"second": second, "phase": row.get("phase", "imported"),
                       "incoming_requests": incoming})
    return result
