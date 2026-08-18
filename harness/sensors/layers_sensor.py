#!/usr/bin/env python3
"""Cairn sensor: module layering / forbidden imports (architecture fitness).

Deterministic, stdlib-only (uses `ast`). Layer rules are passed on the command
line so the whole harness stays a single config file - no second tool config.

A rule `FROM:TO` forbids any module whose dotted path starts with `FROM` from
importing any module whose dotted path starts with `TO`.

Usage:
    python layers_sensor.py --root PKG_DIR --forbid FROM:TO [--forbid ...] [PATH ...]

Example (routes -> services -> clients; clients must not import services):
    python layers_sensor.py --root . --forbid app.clients:app.services
"""
from __future__ import annotations

import argparse
import ast
import json
from pathlib import Path

SENSOR_ID = "layers"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--root", default=".")
    parser.add_argument("--forbid", action="append", default=[], metavar="FROM:TO")
    parser.add_argument("paths", nargs="*")
    args = parser.parse_args(argv)

    rules = _parse_rules(args.forbid)
    if not rules:
        print(json.dumps(_error("no --forbid rules given")))
        return 0

    root = Path(args.root)
    files = _iter_files(root, args.paths)
    findings = []
    for path in files:
        module = _module_name(path, root)
        for imported, lineno in _imports(path, module):
            for src, dst in rules:
                if module.startswith(src) and imported.startswith(dst):
                    findings.append(
                        {
                            "file": str(path),
                            "line": lineno,
                            "message": (
                                f"'{module}' must not import '{imported}' "
                                f"({src} -> {dst} forbidden)"
                            ),
                            "severity": "error",
                        }
                    )

    print(
        json.dumps(
            {
                "sensor": SENSOR_ID,
                "status": "fail" if findings else "pass",
                "score": len(findings),
                "threshold": 0,
                "direction": "lower-is-better",
                "findings": findings,
            }
        )
    )
    return 0


def _parse_rules(raw: list[str]) -> list[tuple[str, str]]:
    rules = []
    for item in raw:
        if ":" not in item:
            continue
        src, dst = item.split(":", 1)
        src, dst = src.strip(), dst.strip()
        if src and dst:
            rules.append((src, dst))
    return rules


def _iter_files(root: Path, paths: list[str]):
    roots = [root / p for p in paths] if paths else [root]
    for base in roots:
        if base.is_file() and base.suffix == ".py":
            yield base
        elif base.is_dir():
            yield from base.rglob("*.py")


def _module_name(path: Path, root: Path) -> str:
    try:
        rel = path.resolve().relative_to(root.resolve())
    except ValueError:
        rel = path
    parts = list(rel.with_suffix("").parts)
    if parts and parts[-1] == "__init__":
        parts = parts[:-1]
    return ".".join(parts)


def _imports(path: Path, module: str):
    try:
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    except (SyntaxError, OSError):
        return
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                yield alias.name, node.lineno
        elif isinstance(node, ast.ImportFrom):
            resolved = _resolve_from(module, node)
            if resolved:
                yield resolved, node.lineno


def _resolve_from(module: str, node: ast.ImportFrom) -> str | None:
    if node.level == 0:
        return node.module
    # Relative import: anchor is the package containing `module`.
    base_parts = module.split(".")[: -node.level]
    if node.module:
        base_parts.append(node.module)
    return ".".join(base_parts) if base_parts else None


def _error(detail: str) -> dict:
    return {
        "sensor": SENSOR_ID,
        "status": "fail",
        "findings": [
            {"file": None, "line": None, "message": detail, "severity": "error"}
        ],
    }


if __name__ == "__main__":
    raise SystemExit(main())
