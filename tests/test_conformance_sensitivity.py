"""Conformance harness for spec/SPEC.md §"Sensitivity enforcement (v0.4.0+)".

Each scenario directory under tests/conformance/sensitivity/ ships an
expected.json describing the tool to invoke (`audit` or `dlp`), CLI args,
and expected violation substrings. The harness parametrizes over every
scenario and runs the tool through its Python entrypoint, then asserts the
captured stdout/stderr matches the assertions.
"""

from __future__ import annotations

import io
import json
import sys
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from typing import Optional

import pytest

CONFORMANCE_ROOT = Path(__file__).parent / "conformance" / "sensitivity"


def _scenarios() -> list[Path]:
    if not CONFORMANCE_ROOT.is_dir():
        return []
    return sorted(
        d for d in CONFORMANCE_ROOT.iterdir()
        if d.is_dir() and (d / "expected.json").is_file()
    )


def _resolve_input_dir(scenario_dir: Path, expected: dict) -> Path:
    raw = expected.get("input_dir", "input")
    return (scenario_dir / raw).resolve()


def _run_audit(input_dir: Path, args: list[str]) -> tuple[int, str]:
    from memforge.cli.audit import main as audit_main
    full = ["--path", str(input_dir), *args]
    out = io.StringIO()
    err = io.StringIO()
    with redirect_stdout(out), redirect_stderr(err):
        rc = audit_main(full)
    return rc, out.getvalue() + "\n" + err.getvalue()


def _run_dlp(args: list[str]) -> tuple[int, str]:
    from memforge.cli.dlp_scan import main as dlp_main
    saved = sys.argv[:]
    sys.argv = ["memory-dlp-scan", *args]
    out = io.StringIO()
    err = io.StringIO()
    try:
        with redirect_stdout(out), redirect_stderr(err):
            rc = dlp_main()
    finally:
        sys.argv = saved
    return rc, out.getvalue() + "\n" + err.getvalue()


@pytest.mark.parametrize("scenario_dir", _scenarios(), ids=lambda p: p.name)
def test_sensitivity_conformance_scenario(scenario_dir: Path):
    expected = json.loads((scenario_dir / "expected.json").read_text(encoding="utf-8"))
    tool = expected["tool"]
    input_dir = _resolve_input_dir(scenario_dir, expected)
    assert input_dir.is_dir(), f"scenario input not found: {input_dir}"

    if tool == "audit":
        rc, output = _run_audit(input_dir, list(expected.get("args", [])))
    elif tool == "dlp":
        template: list[str] = list(expected.get("args_template") or expected.get("args") or [])
        args = [a.format(input_dir=str(input_dir)) for a in template]
        rc, output = _run_dlp(args)
    else:
        pytest.fail(f"unknown tool: {tool}")

    for sub in expected.get("expected_violation_substrings", []):
        assert sub in output, (
            f"missing expected violation substring: {sub!r}\n"
            f"--- captured output ---\n{output}"
        )

    for clean in expected.get("expected_clean_substrings", []):
        if expected.get("tolerate_other_violations"):
            continue
        assert clean not in output, (
            f"unexpected violation substring present: {clean!r}\n"
            f"--- captured output ---\n{output}"
        )

    if expected.get("expected_exit_nonzero"):
        assert rc != 0, f"expected nonzero exit, got {rc}\n{output}"
    else:
        if not expected.get("tolerate_other_violations"):
            assert rc == 0, f"expected zero exit, got {rc}\n{output}"
