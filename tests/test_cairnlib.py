#!/usr/bin/env python3
"""Cairn unit tests - stdlib only, no pytest required.

Run with: python3 tests/test_cairnlib.py

Deliberately small. It covers the engine's decision points (config validation,
the sensor contract, trend direction) plus regression tests for the two bugs
that made a fresh install unusable: CRLF shebangs and the harness scanning its
own vendored code.
"""
from __future__ import annotations

import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from cairnlib import install, report  # noqa: E402
from cairnlib.config import ConfigError, load_config  # noqa: E402
from cairnlib.sensors import parse_result  # noqa: E402

PASSED = 0
FAILED = 0


def check(name: str, got, want) -> None:
    global PASSED, FAILED
    if got == want:
        PASSED += 1
        print(f"  [PASS] {name}")
    else:
        FAILED += 1
        print(f"  [FAIL] {name}\n         got={got!r}\n        want={want!r}")


def raises(name: str, fn) -> None:
    global PASSED, FAILED
    try:
        fn()
    except ConfigError:
        PASSED += 1
        print(f"  [PASS] {name}")
    else:
        FAILED += 1
        print(f"  [FAIL] {name} - expected ConfigError, none raised")


def write_config(tmp: Path, body: str) -> Path:
    (tmp / ".cairn").mkdir(parents=True, exist_ok=True)
    (tmp / ".cairn" / "cairn.toml").write_text(body, encoding="utf-8")
    return tmp


GOOD = """
version = 1
[vars]
src = "app"
[[sensors]]
id = "lint"
run = "python x.py ${src}"
category = "maintainability"
stage = ["in-session", "pre-commit"]
"""


def test_config() -> None:
    print("=== config ===")
    with tempfile.TemporaryDirectory() as d:
        tmp = write_config(Path(d), GOOD)
        cfg = load_config(tmp)
        check("one sensor parsed", len(cfg.sensors), 1)
        check("${vars} interpolated", cfg.sensors[0].run, "python x.py app")
        check("stage filter works", len(cfg.sensors_for_stage("in-session")), 1)
        check("stage filter excludes", len(cfg.sensors_for_stage("ci")), 0)

    # Each of these must fail loudly rather than silently misbehave.
    bad = {
        "missing version": GOOD.replace("version = 1", ""),
        "bad category": GOOD.replace("maintainability", "nonsense"),
        "bad stage": GOOD.replace('"in-session", "pre-commit"', '"whenever"'),
        "empty id": GOOD.replace('id = "lint"', 'id = ""'),
        "undefined var": GOOD.replace('src = "app"', 'other = "app"'),
        "no sensors": "version = 1\n",
    }
    for name, body in bad.items():
        with tempfile.TemporaryDirectory() as d:
            tmp = write_config(Path(d), body)
            raises(f"rejects {name}", lambda t=tmp: load_config(t))


def test_sensor_contract() -> None:
    print("=== sensor contract ===")
    ok = parse_result("lint", {"sensor": "lint", "status": "pass", "score": 0})
    check("pass parses", ok.ok, True)

    bad = parse_result("lint", {"status": "banana"})
    check("invalid status -> error", bad.error is not None, True)
    check("invalid status -> not ok", bad.ok, False)

    # A sensor that exits non-zero merely because it found issues must still
    # be readable: status is the source of truth, not the exit code.
    fail = parse_result(
        "lint",
        {
            "sensor": "lint",
            "status": "fail",
            "score": 3,
            "findings": [
                {"file": "a.py", "line": 4, "message": "x", "severity": "error"}
            ],
        },
    )
    check("fail parses", fail.ok, False)
    check("finding captured", fail.findings[0].file, "a.py")
    odd = parse_result(
        "s", {"status": "fail", "findings": [{"message": "m", "severity": "?"}]}
    )
    check("bad severity defaults to warning", odd.findings[0].severity, "warning")
    check(
        "non-dict payload rejected",
        parse_result("s", ["nope"]).error is not None,
        True,
    )

    # Regression: the configured id must win over the payload's self-reported
    # name. Two sensors can share one wrapper (ruff with different rule sets);
    # trusting the payload would merge them, dropping their guidance and
    # colliding in the snapshot and ledger.
    renamed = parse_result("lint-strict", {"sensor": "lint", "status": "fail"})
    check("configured id wins over payload", renamed.sensor, "lint-strict")


def test_trend() -> None:
    print("=== trend arrows ===")
    r = parse_result(
        "c", {"status": "pass", "score": 5, "direction": "lower-is-better"}
    )
    check("no history -> new", report.trend_symbol(r, None), report.NEW)
    check("score down + lower-is-better -> better",
          report.trend_symbol(r, {"status": "pass", "score": 9}), report.BETTER)
    check("score up + lower-is-better -> worse",
          report.trend_symbol(r, {"status": "pass", "score": 2}), report.WORSE)
    check("same score -> same",
          report.trend_symbol(r, {"status": "pass", "score": 5}), report.SAME)
    check("pass after fail -> better",
          report.trend_symbol(r, {"status": "fail", "score": 5}), report.BETTER)

    hib = parse_result("cov", {"status": "pass", "score": 90,
                               "direction": "higher-is-better"})
    check("score up + higher-is-better -> better",
          report.trend_symbol(hib, {"status": "pass", "score": 70}), report.BETTER)


def test_gitignore_regression() -> None:
    """Regression: the harness must not scan its own vendored engine.

    Without `.cairn/` in the target's .gitignore, sensors pointed at the repo
    root report findings in Cairn's own code and block clean commits.
    """
    print("=== .gitignore regression ===")
    with tempfile.TemporaryDirectory() as d:
        tmp = Path(d)
        rep = install.ApplyReport([], [], [], [], [], [])
        install._ensure_gitignored(tmp, check=False, report=rep)
        content = (tmp / ".gitignore").read_text(encoding="utf-8")
        check("`.cairn/` ignored", ".cairn/" in content, True)

        # Re-running must not duplicate the entry.
        rep2 = install.ApplyReport([], [], [], [], [], [])
        install._ensure_gitignored(tmp, check=False, report=rep2)
        again = (tmp / ".gitignore").read_text(encoding="utf-8")
        check("idempotent", again, content)

    # An existing .gitignore must be preserved, not replaced.
    with tempfile.TemporaryDirectory() as d:
        tmp = Path(d)
        (tmp / ".gitignore").write_text("*.log\n", encoding="utf-8")
        install._ensure_gitignored(
            tmp, check=False, report=install.ApplyReport([], [], [], [], [], [])
        )
        text = (tmp / ".gitignore").read_text(encoding="utf-8")
        check("existing rules kept", "*.log" in text, True)
        check("new rule added", ".cairn/" in text, True)


def test_crlf_regression() -> None:
    """Regression: shebang files must land as LF or they will not execute."""
    print("=== CRLF regression ===")
    with tempfile.TemporaryDirectory() as d:
        tmp = Path(d)
        src = tmp / "hook"
        src.write_bytes(b"#!/usr/bin/env python\r\nprint(1)\r\n")
        dst = tmp / "out"
        install._copy_as_lf(src, dst)
        check("no CR survives the copy", b"\r" in dst.read_bytes(), False)
        check(
            "shebang intact",
            dst.read_bytes().startswith(b"#!/usr/bin/env python\n"),
            True,
        )
        check("CRLF vs LF is not drift", install._same_bytes(src, dst), True)

    # The files shipped in this repo must already be LF.
    for rel in ("harness/hooks/pre-commit", "cairn"):
        path = ROOT / rel
        if path.is_file():
            check(f"{rel} is LF", b"\r\n" in path.read_bytes(), False)


def main() -> int:
    test_config()
    test_sensor_contract()
    test_trend()
    test_gitignore_regression()
    test_crlf_regression()
    print(f"\n{PASSED} passed, {FAILED} failed")
    return 1 if FAILED else 0


if __name__ == "__main__":
    raise SystemExit(main())
