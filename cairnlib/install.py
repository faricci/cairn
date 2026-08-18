"""Manifest-driven idempotent install (`cairn apply`) with drift detection.

The canonical harness content is described by a `manifest.toml` listing
source->destination copies. `apply` copies files into their native locations in
the target repo; `--check` reports drift (missing or differing managed files)
as a diff-like list without writing. This replaces v1's registry/store/lock.
"""
from __future__ import annotations

import shutil
import stat
from dataclasses import dataclass
from pathlib import Path

import tomllib

MANIFEST_RELPATH = Path("harness") / "manifest.toml"

# Agent instruction files that can receive the Cairn managed pointer block.
# `agents`/`codex` share the emerging cross-agent standard AGENTS.md; the others
# are the native locations each tool reads.
AGENT_FILES: dict[str, str] = {
    "agents": "AGENTS.md",
    "codex": "AGENTS.md",
    "copilot": ".github/copilot-instructions.md",
    "cursor": ".cursor/rules/cairn.mdc",
    "claude": "CLAUDE.md",
    "cline": ".clinerules/cairn.md",
}
AGENT_CHOICES = sorted(AGENT_FILES) + ["auto", "all"]

_BLOCK_BEGIN = "<!-- BEGIN CAIRN (managed - do not edit inside this block) -->"
_BLOCK_END = "<!-- END CAIRN -->"
_POINTER_BODY = """\
## Cairn harness

This repository is governed by a **Cairn harness**: deterministic sensors plus a
blocking pre-commit gate that give fast, self-correcting feedback.

- You MUST run the exact command `cairn check` after each change and before
  reporting a task done. Do NOT invoke the `.cairn/sensors/*.py` scripts
  directly - only `cairn check` records to the ledger and applies thresholds,
  guidance, and trend deltas.
- Read each failing sensor's guidance, fix the code, then re-run `cairn check`
  until every sensor is PASS.
- A pre-commit gate reruns the sensors and blocks the commit if any fail; it is
  the enforced backstop, not optional.
- Prefer fixing the code over raising thresholds.

Full working agreement: `.cairn/guides/AGENTS.cairn.md`.
"""
_CURSOR_FRONTMATTER = (
    "---\n"
    "description: Cairn harness working agreement\n"
    "alwaysApply: true\n"
    "---\n\n"
)


class InstallError(Exception):
    """Raised when the manifest is missing or malformed."""


@dataclass(frozen=True)
class CopyEntry:
    src: str
    dst: str
    executable: bool = False
    once: bool = False


@dataclass
class ApplyReport:
    created: list[str]
    updated: list[str]
    unchanged: list[str]
    skipped_once: list[str]
    drift: list[str]  # (check mode) dst paths that are missing or differ
    injected: list[str]  # agent instruction files that got the managed block

    @property
    def has_drift(self) -> bool:
        return bool(self.drift)


def load_manifest(source: Path) -> list[CopyEntry]:
    path = source / MANIFEST_RELPATH
    if not path.is_file():
        raise InstallError(f"manifest not found at {path}")
    with path.open("rb") as fh:
        raw = tomllib.load(fh)
    entries = raw.get("copy")
    if not isinstance(entries, list) or not entries:
        raise InstallError(f"{path}: no [[copy]] entries")
    out: list[CopyEntry] = []
    for e in entries:
        out.append(
            CopyEntry(
                src=str(e["src"]),
                dst=str(e["dst"]),
                executable=bool(e.get("executable", False)),
                once=bool(e.get("once", False)),
            )
        )
    return out


def apply(
    source: Path,
    target: Path,
    check: bool,
    agents: list[str] | None = None,
) -> ApplyReport:
    entries = load_manifest(source)
    report = ApplyReport([], [], [], [], [], [])
    for entry in entries:
        _apply_entry(entry, source, target, check, report)
    for dst_rel in resolve_agents(target, agents):
        _inject_pointer(target, dst_rel, check, report)
    _ensure_gitignored(target, check, report)
    return report


def _ensure_gitignored(target: Path, check: bool, report: ApplyReport) -> None:
    """Make sure ``.cairn/`` is git-ignored in the target repo.

    The vendored engine lives in ``.cairn/``. Left un-ignored, sensors pointed at
    the repo root scan Cairn's own code and report findings that have nothing to
    do with the user's project - blocking commits of otherwise clean code. Most
    tools (ruff included) skip git-ignored paths, so one line fixes it.
    """
    path = target / ".gitignore"
    current = path.read_text(encoding="utf-8") if path.is_file() else ""
    if any(line.strip().rstrip("/") == ".cairn" for line in current.splitlines()):
        report.unchanged.append(".gitignore")
        return
    if check:
        report.drift.append(".gitignore")
        return
    sep = "" if not current or current.endswith("\n") else "\n"
    comment = "# Cairn harness (vendored engine; recreated by `cairn apply`)"
    block = f"{sep}\n{comment}\n.cairn/\n"
    path.write_text(current + block, encoding="utf-8")
    report.injected.append(f".gitignore ({'merged' if current else 'created'})")


def resolve_agents(target: Path, requested: list[str] | None) -> list[str]:
    """Resolve the agent instruction file(s) to inject, deduped by path.

    `auto` (default) always ensures AGENTS.md and adds any agent-specific file
    that already exists in the target. `all` targets every known location.
    """
    req = requested or ["auto"]
    if "all" in req:
        keys = set(AGENT_FILES)
    elif "auto" in req:
        keys = {"agents"}
        for key, rel in AGENT_FILES.items():
            if (target / rel).is_file():
                keys.add(key)
    else:
        keys = set(req)
    # Dedupe by destination path (agents/codex both map to AGENTS.md).
    return sorted({AGENT_FILES[key] for key in keys})


def _managed_block() -> str:
    return f"{_BLOCK_BEGIN}\n{_POINTER_BODY}{_BLOCK_END}\n"


def _upsert_block(content: str, is_new_cursor: bool) -> str:
    begin = content.find(_BLOCK_BEGIN)
    end = content.find(_BLOCK_END)
    if begin != -1 and end > begin:
        tail = content[end + len(_BLOCK_END):]
        return content[:begin] + _managed_block().rstrip("\n") + tail
    if not content.strip():
        prefix = _CURSOR_FRONTMATTER if is_new_cursor else ""
        return prefix + _managed_block()
    if content.endswith("\n\n"):
        sep = ""
    elif content.endswith("\n"):
        sep = "\n"
    else:
        sep = "\n\n"
    return content + sep + _managed_block()


def _inject_pointer(
    target: Path, dst_rel: str, check: bool, report: ApplyReport
) -> None:
    path = target / dst_rel
    exists = path.is_file()
    current = path.read_text(encoding="utf-8") if exists else ""
    is_new_cursor = dst_rel.endswith(".mdc") and not exists
    updated = _upsert_block(current, is_new_cursor=is_new_cursor)
    if not updated.endswith("\n"):
        updated += "\n"
    if updated == current:
        report.unchanged.append(dst_rel)
        return
    if check:
        report.drift.append(dst_rel)
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(updated, encoding="utf-8")
    report.injected.append(f"{dst_rel} ({'merged' if exists else 'created'})")


def _apply_entry(
    entry: CopyEntry, source: Path, target: Path, check: bool, report: ApplyReport
) -> None:
    src = source / entry.src
    if not src.exists():
        raise InstallError(f"manifest source missing: {src}")
    pairs = _file_pairs(entry, src, target)
    for src_file, dst_file, dst_label in pairs:
        _handle_file(entry, src_file, dst_file, dst_label, check, report)


def _file_pairs(entry: CopyEntry, src: Path, target: Path):
    """Yield (src_file, dst_file, label) pairs, expanding directories."""
    if src.is_dir():
        for f in sorted(src.rglob("*")):
            if f.is_file() and not _is_ignored(f):
                rel = f.relative_to(src)
                yield f, target / entry.dst / rel, f"{entry.dst}/{rel.as_posix()}"
    else:
        yield src, target / entry.dst, entry.dst


def _is_ignored(path: Path) -> bool:
    return "__pycache__" in path.parts or path.suffix == ".pyc"


def _handle_file(
    entry: CopyEntry,
    src_file: Path,
    dst_file: Path,
    label: str,
    check: bool,
    report: ApplyReport,
) -> None:
    exists = dst_file.is_file()
    if entry.once and exists:
        report.skipped_once.append(label)
        return
    same = exists and _same_bytes(src_file, dst_file)
    if check:
        if not exists or not same:
            report.drift.append(label)
        else:
            report.unchanged.append(label)
        return
    if same:
        report.unchanged.append(label)
        return
    dst_file.parent.mkdir(parents=True, exist_ok=True)
    if entry.executable:
        _copy_as_lf(src_file, dst_file)
        _make_executable(dst_file)
    else:
        shutil.copyfile(src_file, dst_file)
    (report.updated if exists else report.created).append(label)


def _copy_as_lf(src: Path, dst: Path) -> None:
    """Copy a script, forcing LF line endings.

    Executable entries (the pre-commit hook, the launcher) start with a shebang.
    A CRLF there makes the kernel look for an interpreter named ``python\\r``,
    which fails on Linux/macOS. Normalising on copy keeps the harness working
    even when the source was checked out with ``core.autocrlf=true``.
    """
    dst.write_bytes(src.read_bytes().replace(b"\r\n", b"\n"))


def _normalised(path: Path) -> bytes:
    """File bytes with CRLF collapsed to LF, so line endings never look like drift."""
    return path.read_bytes().replace(b"\r\n", b"\n")


def _same_bytes(a: Path, b: Path) -> bool:
    """Compare files, ignoring CRLF-vs-LF differences for executables.

    Without this, a hook normalised to LF on install would look like drift on
    every subsequent `apply --check` against a CRLF source.
    """
    try:
        return _normalised(a) == _normalised(b)
    except OSError:
        return False


def _make_executable(path: Path) -> None:
    try:
        mode = path.stat().st_mode
        path.chmod(mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
    except OSError:
        pass
