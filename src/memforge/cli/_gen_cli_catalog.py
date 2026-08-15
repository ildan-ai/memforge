"""Generate docs/cli-reference.md from the live --help output of every
console-script entry point this package actually installs.

This is a doc-sync generator, not a doc. The catalog is derived from the
installed package's own entry-point metadata (importlib.metadata), not by
re-parsing pyproject.toml text, so there is exactly one source of truth:
what pip actually registered for the `ildan-memforge` distribution. Each
entry's help text comes from running its exact registered module:function
target as a subprocess, so drift in an argparse parser (a renamed flag, a
changed help string, an added/removed command) shows up here automatically
the next time this generator runs.

Design notes (adversarial-review-driven, see CHANGELOG "Unreleased"):

- Entries are executed by importing the exact `module:function` string
  metadata reports for THIS distribution, never by looking up a bare
  command name on PATH. A same-named console script from another
  installed distribution (or a stale wrapper earlier on PATH) cannot be
  substituted in silently; Python's own import system, not PATH order,
  resolves the module.
- The child subprocess runs with a pinned COLUMNS/LINES and a fixed
  UTF-8 locale so argparse's line-wrapping cannot differ between the
  machine that regenerates this file and CI, which would otherwise cause
  the check to flap on unrelated environment differences.
- Drift comparison is byte-exact (read_bytes/write_bytes with an explicit
  UTF-8 encode), so the check is not fooled by newline-translation
  ("universal newlines") turning a real CRLF/LF mismatch into an
  apparent match.
- Every path shown in output is repo-relative. This is a public repo;
  nothing here prints an absolute filesystem path, and the generated
  content itself is scanned for the operator's home directory and this
  checkout's absolute path before being written or accepted, refusing
  loudly rather than shipping a leak.

Usage:
    tools/gen-cli-catalog            regenerate docs/cli-reference.md
    tools/gen-cli-catalog --check    exit 1 if the committed file is stale

Wire --check into CI (see .github/workflows/ci.yml, job cli-docs-check).
A generator with no verify step only proves the generator ran once; it does
not prove the shipped docs still describe the shipped CLI. --check is the
part that keeps that true over time.
"""
from __future__ import annotations

import argparse
import difflib
import os
import subprocess
import sys
from importlib.metadata import EntryPoint, entry_points
from pathlib import Path

DIST_NAME = "ildan-memforge"
ROOT = Path(__file__).resolve().parents[3]
OUTPUT = ROOT / "docs" / "cli-reference.md"
OUTPUT_DISPLAY = "docs/cli-reference.md"

# Pinned so argparse's HelpFormatter wraps identically regardless of which
# machine (or CI runner) regenerates the file.
_CHILD_COLUMNS = "88"
_CHILD_LINES = "24"

HEADER = """<!-- GENERATED FILE. Do not hand-edit; edit source and rerun tools/gen-cli-catalog. -->
<!-- Source of truth: this package's installed console_scripts entry points -->
<!-- (importlib.metadata), each rendered via its own --help output. -->

# MemForge CLI reference

Generated from the live --help output of every console-script entry point
this package installs. `tools/gen-cli-catalog --check` runs in CI and fails
the build when this file has drifted from source (see .github/workflows/ci.yml,
job `cli-docs-check`).

For curated one-line descriptions and install guidance, see the "Key tools"
section of the project README. This file is the exhaustive, always-fresh
--help reference; the README table is the hand-picked tour.
"""


class CatalogError(RuntimeError):
    """Raised for any generator failure. Message is always repo-relative;
    never carries an absolute filesystem path (this repo is public)."""


def load_entry_points() -> list[EntryPoint]:
    """Every console_scripts entry point THIS package's distribution installs.

    Filtering by dist.name (not just presence in the environment) matters:
    a dependency installed in the same venv can also register console
    scripts under the shared `console_scripts` group, and those are not
    ours to document.
    """
    eps = entry_points(group="console_scripts")
    mine = [ep for ep in eps if ep.dist is not None and ep.dist.name == DIST_NAME]
    return sorted(mine, key=lambda ep: ep.name)


def _child_env() -> dict[str, str]:
    """A minimal, deterministic environment for the --help subprocess.

    Starts from nothing rather than inheriting the parent wholesale, so an
    operator-specific env var cannot leak into a --help string a CLI module
    might echo into its own help text (e.g. a "default: $SOME_VAR" note).
    Carries over exactly what is needed to resolve the same interpreter and
    installed package the parent process is using.
    """
    env: dict[str, str] = {
        "PATH": os.environ.get("PATH", ""),
        "PYTHONIOENCODING": "utf-8",
        "LC_ALL": "C.UTF-8",
        "LANG": "C.UTF-8",
        "COLUMNS": _CHILD_COLUMNS,
        "LINES": _CHILD_LINES,
    }
    for key in ("VIRTUAL_ENV", "PYTHONPATH", "SYSTEMROOT"):
        if key in os.environ:
            env[key] = os.environ[key]
    return env


def get_help(ep: EntryPoint) -> str:
    """Run this exact entry point's module:function target with --help.

    Binds to `ep.value` ("module:function") directly rather than resolving
    a bare command name on PATH, so a same-named console script from
    another installed distribution cannot be invoked in this package's
    place: Python's import system, not PATH search order, decides which
    module answers.
    """
    module, _, attr = ep.value.partition(":")
    if not module or not attr:
        raise CatalogError(
            f"entry point {ep.name!r} has an unparseable target {ep.value!r} "
            "(expected 'module:function')"
        )
    # Set sys.argv rather than calling the target with an explicit argv list:
    # every main() in this codebase either takes an optional argv parameter
    # (defaulting to None, which makes argparse read sys.argv) or takes no
    # parameter at all and reads sys.argv directly. A zero-argument call
    # works for both signatures; passing ['--help'] positionally does not.
    code = (
        "import sys\n"
        f"sys.argv = [{ep.name!r}, '--help']\n"
        f"from {module} import {attr}\n"
        f"sys.exit({attr}())\n"
    )
    try:
        proc = subprocess.run(
            [sys.executable, "-c", code],
            capture_output=True,
            encoding="utf-8",
            errors="strict",
            timeout=30,
            cwd=str(ROOT),
            env=_child_env(),
        )
    except subprocess.TimeoutExpired as exc:
        raise CatalogError(f"{ep.name!r} --help timed out after {exc.timeout}s") from None
    except UnicodeDecodeError as exc:
        raise CatalogError(f"{ep.name!r} --help produced non-UTF-8 output: {exc}") from None

    if proc.returncode != 0:
        raise CatalogError(
            f"{ep.name!r} ({ep.value}) --help exited {proc.returncode} (expected 0); "
            f"it ran and failed rather than being missing: {proc.stderr.strip()}"
        )
    help_text = proc.stdout.strip()
    if not help_text:
        raise CatalogError(
            f"{ep.name!r} ({ep.value}) --help exited 0 but produced no output; "
            "refusing to generate an empty catalog entry"
        )
    return help_text


def render(eps: list[EntryPoint]) -> str:
    parts = [HEADER]
    for ep in eps:
        help_text = get_help(ep)
        parts.append(f"## `{ep.name}`\n")
        parts.append(f"Entry point: `{ep.value}`\n")
        parts.append("```")
        parts.append(help_text)
        parts.append("```\n")
    return "\n".join(parts).rstrip() + "\n"


def _leak_scan(text: str) -> list[str]:
    """Refuse to write/accept generated content carrying an absolute
    filesystem path. Public-repo safety net: independent of whether the
    generator's own messages are clean, this checks the CONTENT that would
    be committed, which is the thing that actually ships."""
    findings: list[str] = []
    home = str(Path.home())
    checkout = str(ROOT)
    for label, needle in (("operator home directory", home), ("this checkout's path", checkout)):
        if needle and needle in text:
            findings.append(f"generated content contains {label} ({needle!r})")
    for marker in ("/Users/", "/home/", "C:\\Users\\"):
        if marker in text:
            findings.append(f"generated content contains an absolute-path marker ({marker!r})")
    return findings


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="gen-cli-catalog",
        description="Generate docs/cli-reference.md from installed console_scripts.",
    )
    parser.add_argument(
        "--check",
        action="store_true",
        help="exit 1 if docs/cli-reference.md is stale instead of writing it",
    )
    args = parser.parse_args(argv)

    eps = load_entry_points()
    if not eps:
        print(
            f"error: no console_scripts entry points found for dist {DIST_NAME!r}. "
            "Is the package installed (pip install -e .)?",
            file=sys.stderr,
        )
        return 1

    try:
        generated = render(eps)
    except CatalogError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1

    leaks = _leak_scan(generated)
    if leaks:
        print(
            "error: refusing to write docs/cli-reference.md: a generated --help "
            "string appears to embed an absolute filesystem path, which must "
            "never land in this public repo's committed docs:",
            file=sys.stderr,
        )
        for finding in leaks:
            print(f"  - {finding}", file=sys.stderr)
        return 1

    generated_bytes = generated.encode("utf-8")

    if args.check:
        if not OUTPUT.exists():
            print(
                f"error: {OUTPUT_DISPLAY} does not exist. "
                "Run tools/gen-cli-catalog to generate it.",
                file=sys.stderr,
            )
            return 1
        current_bytes = OUTPUT.read_bytes()
        if current_bytes != generated_bytes:
            print(
                f"error: {OUTPUT_DISPLAY} is stale. "
                "Run tools/gen-cli-catalog and commit the result.",
                file=sys.stderr,
            )
            diff = difflib.unified_diff(
                current_bytes.decode("utf-8", errors="replace").splitlines(keepends=True),
                generated_bytes.decode("utf-8", errors="replace").splitlines(keepends=True),
                fromfile=f"{OUTPUT_DISPLAY} (committed)",
                tofile=f"{OUTPUT_DISPLAY} (generated)",
            )
            sys.stderr.writelines(list(diff)[:400])
            return 1
        print(f"OK: {OUTPUT_DISPLAY} matches {len(eps)} installed entry points.")
        return 0

    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT.write_bytes(generated_bytes)
    print(f"Wrote {OUTPUT_DISPLAY} ({len(eps)} entry points).")
    return 0


if __name__ == "__main__":
    sys.exit(main())
