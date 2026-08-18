#!/usr/bin/env python3
"""Cairn v2 - a coding-agent harness (guides + sensors + gate + ledger).

Zero external dependencies (Python stdlib only, 3.11+ for tomllib).

Model: assistant, not police. `check` runs deterministic sensors and prints a
guidance-enriched summary the agent self-corrects against. `gate` is a thin
blocking backstop for pre-commit/CI that reuses the same sensor definitions.

Command surface:
    cairn check    [--stage in-session] [--target DIR]
    cairn snapshot [--target DIR]
    cairn history  [--sensor ID] [--target DIR]
    cairn apply     --target DIR [--check] [--source DIR] [--agent NAME ...]
    cairn gate     [--stage pre-commit|ci] [--target DIR]
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

from cairnlib import install, report
from cairnlib.config import Config, ConfigError, load_config
from cairnlib.sensors import SensorResult, run_sensor
from cairnlib.state import (
    append_ledger,
    read_ledger,
    read_snapshot,
    write_snapshot,
)

__version__ = "2.0.0-dev"


def _force_utf8_output() -> None:
    """Cairn's reports use unicode (trend arrows, guidance markers); make sure
    they never crash on legacy Windows consoles (cp1252)."""
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8")  # type: ignore[attr-defined]
        except (AttributeError, ValueError):
            pass


def _load(target: str) -> Config | None:
    try:
        return load_config(target)
    except ConfigError as exc:
        print(f"cairn: {exc}", file=sys.stderr)
        return None


def _run_stage(config: Config, target: str, stage: str) -> list[SensorResult]:
    return [run_sensor(s, target) for s in config.sensors_for_stage(stage)]


def cmd_check(args: argparse.Namespace) -> int:
    config = _load(args.target)
    if config is None:
        return 2
    results = _run_stage(config, args.target, args.stage)
    if not results:
        print(f"[cairn] no sensors configured for stage '{args.stage}'")
        return 0
    snapshot = read_snapshot(args.target)
    print(report.render(config, results, snapshot))
    append_ledger(args.target, results, args.stage, by="check")
    return 0


def cmd_snapshot(args: argparse.Namespace) -> int:
    config = _load(args.target)
    if config is None:
        return 2
    results = [run_sensor(s, args.target) for s in config.sensors]
    path = write_snapshot(args.target, results)
    failing = sum(1 for r in results if not r.ok)
    print(
        f"[cairn] snapshot written to {path} "
        f"({len(results)} sensors, {failing} failing)"
    )
    return 0


def _tally(rows: list[dict]) -> dict[str, dict[str, int]]:
    """Count runs and failures per sensor id."""
    stats: dict[str, dict[str, int]] = {}
    for r in rows:
        s = stats.setdefault(r.get("sensor", "?"), {"runs": 0, "fail": 0})
        s["runs"] += 1
        if r.get("status") == "fail":
            s["fail"] += 1
    return stats


def _effectiveness_note(fail: int, runs: int) -> str:
    """Flag sensors that carry no signal because they never or always fire."""
    if fail == 0:
        return "  (never fires - candidate for removal)"
    if fail == runs:
        return "  (always fires - fix the backlog or adjust the threshold)"
    return ""


def cmd_history(args: argparse.Namespace) -> int:
    rows = read_ledger(args.target)
    if args.sensor:
        rows = [r for r in rows if r.get("sensor") == args.sensor]
    if not rows:
        print("[cairn] ledger is empty")
        return 0
    print(f"[cairn] effectiveness over {len(rows)} runs:")
    for sid, s in sorted(_tally(rows).items()):
        rate = s["fail"] / s["runs"] if s["runs"] else 0
        note = _effectiveness_note(s["fail"], s["runs"])
        print(f"  {sid:<14} {s['fail']}/{s['runs']} fail ({rate:.0%}){note}")
    return 0


def cmd_apply(args: argparse.Namespace) -> int:
    source = Path(args.source) if args.source else Path(__file__).resolve().parent
    target = Path(args.target)
    try:
        rep = install.apply(source, target, check=args.check, agents=args.agent)
    except install.InstallError as exc:
        print(f"cairn: {exc}", file=sys.stderr)
        return 2

    if args.check:
        if rep.has_drift:
            print(f"[cairn] drift detected ({len(rep.drift)} file(s)):")
            for label in rep.drift:
                print(f"  ~ {label}")
            return 1
        print("[cairn] no drift; harness is up to date")
        return 0

    for label in rep.created:
        print(f"  + {label}")
    for label in rep.updated:
        print(f"  ~ {label}")
    for label in rep.skipped_once:
        print(f"  = {label} (user-owned, kept)")
    for label in rep.injected:
        print(f"  → {label} (Cairn pointer)")
    print(
        f"[cairn] applied to {target}: "
        f"{len(rep.created)} created, {len(rep.updated)} updated, "
        f"{len(rep.unchanged)} unchanged, {len(rep.skipped_once)} kept, "
        f"{len(rep.injected)} injected"
    )
    return 0


def cmd_gate(args: argparse.Namespace) -> int:
    config = _load(args.target)
    if config is None:
        return 2
    results = _run_stage(config, args.target, args.stage)
    if not results:
        print(f"[cairn] gate: no sensors for stage '{args.stage}', passing")
        return 0
    snapshot = read_snapshot(args.target)
    print(report.render(config, results, snapshot))
    append_ledger(args.target, results, args.stage, by="gate")
    failing = [r for r in results if not r.ok]
    if failing:
        ids = ", ".join(r.sensor for r in failing)
        print(f"\n[cairn] gate BLOCKED by: {ids}", file=sys.stderr)
        return 1
    print("\n[cairn] gate passed")
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="cairn", description=__doc__.splitlines()[0])
    parser.add_argument("--version", action="version", version=f"cairn {__version__}")
    sub = parser.add_subparsers(dest="command", required=True)

    p_check = sub.add_parser(
        "check", help="run sensors, print agent summary (non-blocking)"
    )
    p_check.add_argument("--stage", default="in-session")
    p_check.add_argument("--target", default=".")
    p_check.set_defaults(func=cmd_check)

    p_snap = sub.add_parser(
        "snapshot", help="persist current sensor state for trend deltas"
    )
    p_snap.add_argument("--target", default=".")
    p_snap.set_defaults(func=cmd_snapshot)

    p_hist = sub.add_parser(
        "history", help="read the sensor ledger (effectiveness view)"
    )
    p_hist.add_argument("--sensor", default=None)
    p_hist.add_argument("--target", default=".")
    p_hist.set_defaults(func=cmd_history)

    p_apply = sub.add_parser("apply", help="idempotent install of guides + sensors")
    p_apply.add_argument("--target", required=True)
    p_apply.add_argument("--source", default=None)
    p_apply.add_argument(
        "--check", action="store_true", help="report drift instead of writing"
    )
    p_apply.add_argument(
        "--agent",
        action="append",
        choices=install.AGENT_CHOICES,
        help="agent instruction file(s) to inject the Cairn pointer into "
        "(repeatable; default: auto-detect + AGENTS.md)",
    )
    p_apply.set_defaults(func=cmd_apply)

    p_gate = sub.add_parser("gate", help="blocking backstop for pre-commit/CI")
    p_gate.add_argument("--stage", default="pre-commit", choices=["pre-commit", "ci"])
    p_gate.add_argument("--target", default=".")
    p_gate.set_defaults(func=cmd_gate)

    return parser


def main(argv: list[str] | None = None) -> int:
    _force_utf8_output()
    parser = build_parser()
    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
