"""Persistent harness state: the trend snapshot and the append-only ledger.

- ``snapshot.json`` holds the last seen status/score per sensor, used to compute
  trend deltas in the agent summary.
- ``ledger.jsonl`` is an append-only history of every sensor run, used for the
  effectiveness view (which sensors never/always fire).
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

from .sensors import SensorResult

SNAPSHOT_RELPATH = Path(".cairn") / "snapshot.json"
LEDGER_RELPATH = Path(".cairn") / "ledger.jsonl"


def snapshot_path(target: str | Path) -> Path:
    return Path(target) / SNAPSHOT_RELPATH


def ledger_path(target: str | Path) -> Path:
    return Path(target) / LEDGER_RELPATH


def read_snapshot(target: str | Path) -> dict[str, dict]:
    """Return a mapping sensor_id -> {status, score}. Empty if none/invalid."""
    path = snapshot_path(target)
    if not path.is_file():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return {}
    return data.get("sensors", {}) if isinstance(data, dict) else {}


def write_snapshot(target: str | Path, results: list[SensorResult]) -> Path:
    path = snapshot_path(target)
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "ts": _now(),
        "sensors": {
            r.sensor: {"status": r.status, "score": r.score} for r in results
        },
    }
    path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    return path


def append_ledger(
    target: str | Path, results: list[SensorResult], stage: str, by: str
) -> Path:
    path = ledger_path(target)
    path.parent.mkdir(parents=True, exist_ok=True)
    ts = _now()
    with path.open("a", encoding="utf-8") as fh:
        for r in results:
            fh.write(
                json.dumps(
                    {
                        "ts": ts,
                        "sensor": r.sensor,
                        "status": r.status,
                        "score": r.score,
                        "stage": stage,
                        "by": by,
                    }
                )
                + "\n"
            )
    return path


def read_ledger(target: str | Path) -> list[dict]:
    path = ledger_path(target)
    if not path.is_file():
        return []
    rows: list[dict] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            rows.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    return rows


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
