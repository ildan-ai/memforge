"""Tests for memforge.cli._gen_cli_catalog (tools/gen-cli-catalog's implementation).

Regression coverage driven by an adversarial-review REJECT (dsh-incorporation
Lane E, doc-sync generator). Each test below maps to a specific finding from
that pass:

- test_get_help_ignores_path_shadow: a same-named console script earlier on
  PATH must NOT be invoked in place of the real, metadata-registered target.
  This is the core "false negative" finding: PATH-based name lookup could
  silently document the wrong program.
- test_check_is_byte_exact_on_newline_only_diff: read_text/write_text-style
  universal-newline translation must not make a real CRLF/LF mismatch read
  as "no drift".
- test_load_entry_points_filters_by_distribution: a console script registered
  by some OTHER installed distribution must not be treated as ours to
  document.
- test_load_entry_points_is_deterministically_sorted: output ordering must
  not depend on dict/set iteration order.
- test_leak_scan_flags_operator_paths / test_leak_scan_clean_on_ordinary_help:
  the public-repo safety net that refuses to write content embedding an
  absolute filesystem path.
- test_main_check_detects_added_and_removed_entry_point: the check must fail
  both when a new command appears undocumented and when a documented command
  disappears, not just when a --help string changes.
- test_get_help_raises_on_empty_output / test_get_help_raises_on_nonzero_exit:
  failure-mode coverage so a broken parser fails the generator loudly
  instead of writing a hollow catalog entry.
"""

from __future__ import annotations

import os
import stat
import sys
import textwrap
from importlib.metadata import EntryPoint
from pathlib import Path
from types import SimpleNamespace

import pytest

from memforge.cli import _gen_cli_catalog as gcc


def _fake_ep(name: str, value: str, dist_name: str = gcc.DIST_NAME) -> EntryPoint:
    ep = EntryPoint(name=name, value=value, group="console_scripts")
    # importlib.metadata.EntryPoint.dist is settable via .dist = ... on
    # CPython's implementation (used by dist-selection code across the repo's
    # own CLI modules); fall back to SimpleNamespace-compatible duck typing
    # if a future stdlib makes it read-only.
    try:
        ep.dist = SimpleNamespace(name=dist_name)  # type: ignore[attr-defined]
    except AttributeError:  # pragma: no cover - defensive only
        object.__setattr__(ep, "dist", SimpleNamespace(name=dist_name))
    return ep


def _write_stub_module(tmp_path: Path, module_name: str, help_text: str) -> Path:
    """A tiny importable module exposing main(argv) that behaves like an
    argparse-based CLI entry point: --help prints help_text and exits 0."""
    src_dir = tmp_path / "stub_src"
    src_dir.mkdir(exist_ok=True)
    (src_dir / f"{module_name}.py").write_text(
        textwrap.dedent(
            f'''
            import argparse

            def main(argv=None):
                p = argparse.ArgumentParser(prog={module_name!r})
                p.add_argument("--noop", action="store_true")
                p.description = {help_text!r}
                p.parse_args(argv)
                return 0
            '''
        ),
        encoding="utf-8",
    )
    return src_dir


@pytest.fixture()
def stub_env(tmp_path, monkeypatch):
    """Puts a stub module on PYTHONPATH (inherited by get_help's subprocess
    via _child_env) and returns the tmp_path for convenience."""
    src_dir = _write_stub_module(tmp_path, "gcc_stub_real", "REAL STUB HELP TEXT")
    monkeypatch.setenv("PYTHONPATH", str(src_dir))
    return tmp_path


def test_get_help_binds_to_exact_module_function(stub_env):
    ep = _fake_ep("gcc-stub", "gcc_stub_real:main")
    help_text = gcc.get_help(ep)
    assert "REAL STUB HELP TEXT" in help_text


def test_get_help_ignores_path_shadow(stub_env, monkeypatch):
    """A malicious or stale console script named identically to our entry
    point, placed earlier on PATH, must not be what gets documented."""
    decoy_dir = stub_env / "decoy_bin"
    decoy_dir.mkdir()
    decoy = decoy_dir / "gcc-stub"
    decoy.write_text("#!/bin/sh\necho 'DECOY OUTPUT -- NOT THE REAL CLI'\n", encoding="utf-8")
    decoy.chmod(decoy.stat().st_mode | stat.S_IEXEC | stat.S_IXGRP | stat.S_IXOTH)
    monkeypatch.setenv("PATH", str(decoy_dir) + os.pathsep + os.environ.get("PATH", ""))

    ep = _fake_ep("gcc-stub", "gcc_stub_real:main")
    help_text = gcc.get_help(ep)

    assert "REAL STUB HELP TEXT" in help_text
    assert "DECOY OUTPUT" not in help_text


def test_get_help_raises_on_empty_output(tmp_path, monkeypatch):
    src_dir = tmp_path / "stub_src"
    src_dir.mkdir()
    (src_dir / "gcc_stub_empty.py").write_text(
        "def main(argv=None):\n    return 0\n", encoding="utf-8"
    )
    monkeypatch.setenv("PYTHONPATH", str(src_dir))
    ep = _fake_ep("gcc-empty", "gcc_stub_empty:main")
    with pytest.raises(gcc.CatalogError, match="produced no output"):
        gcc.get_help(ep)


def test_get_help_raises_on_nonzero_exit(tmp_path, monkeypatch):
    src_dir = tmp_path / "stub_src"
    src_dir.mkdir()
    (src_dir / "gcc_stub_fail.py").write_text(
        "import sys\ndef main(argv=None):\n    sys.exit(3)\n", encoding="utf-8"
    )
    monkeypatch.setenv("PYTHONPATH", str(src_dir))
    ep = _fake_ep("gcc-fail", "gcc_stub_fail:main")
    with pytest.raises(gcc.CatalogError, match="exited 3"):
        gcc.get_help(ep)


def test_load_entry_points_filters_by_distribution(monkeypatch):
    ours = _fake_ep("mine", "memforge.cli.audit:main", dist_name=gcc.DIST_NAME)
    theirs = _fake_ep("not-mine", "other_pkg.cli:main", dist_name="some-other-package")
    monkeypatch.setattr(gcc, "entry_points", lambda group: [theirs, ours])

    result = gcc.load_entry_points()

    assert [ep.name for ep in result] == ["mine"]


def test_load_entry_points_is_deterministically_sorted(monkeypatch):
    eps = [
        _fake_ep("zeta", "memforge.cli.z:main"),
        _fake_ep("alpha", "memforge.cli.a:main"),
        _fake_ep("mu", "memforge.cli.m:main"),
    ]
    monkeypatch.setattr(gcc, "entry_points", lambda group: eps)

    result = gcc.load_entry_points()

    assert [ep.name for ep in result] == ["alpha", "mu", "zeta"]


def test_leak_scan_flags_operator_paths():
    findings = gcc._leak_scan("some help text\ndefault: /Users/someone/project\n")
    assert findings


def test_leak_scan_clean_on_ordinary_help():
    findings = gcc._leak_scan("usage: memory-audit [-h] [--strict]\n\noptions:\n  -h, --help\n")
    assert findings == []


def test_check_is_byte_exact_on_newline_only_diff(tmp_path, monkeypatch):
    """A file that is byte-identical except for CRLF vs LF must be reported
    as stale, not silently treated as matching via universal-newline reads."""
    monkeypatch.setattr(gcc, "ROOT", tmp_path)
    monkeypatch.setattr(gcc, "OUTPUT", tmp_path / "docs" / "cli-reference.md")
    (tmp_path / "docs").mkdir()

    eps = [_fake_ep("solo", "gcc_stub_solo:main")]
    monkeypatch.setattr(gcc, "load_entry_points", lambda: eps)
    monkeypatch.setattr(gcc, "get_help", lambda ep: "usage: solo [-h]")

    lf_bytes = gcc.render(eps).encode("utf-8")
    crlf_bytes = lf_bytes.replace(b"\n", b"\r\n")
    gcc.OUTPUT.write_bytes(crlf_bytes)

    exit_code = gcc.main(["--check"])

    assert exit_code == 1


def test_main_check_detects_added_and_removed_entry_point(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(gcc, "ROOT", tmp_path)
    monkeypatch.setattr(gcc, "OUTPUT", tmp_path / "docs" / "cli-reference.md")
    (tmp_path / "docs").mkdir()

    alpha = _fake_ep("alpha", "gcc_stub_alpha:main")
    beta = _fake_ep("beta", "gcc_stub_beta:main")
    monkeypatch.setattr(gcc, "get_help", lambda ep: f"usage: {ep.name} [-h]")

    # Baseline: only alpha exists; generate and confirm it checks clean.
    monkeypatch.setattr(gcc, "load_entry_points", lambda: [alpha])
    assert gcc.main([]) == 0
    assert gcc.main(["--check"]) == 0

    # A command is added upstream but the committed catalog is not regenerated.
    monkeypatch.setattr(gcc, "load_entry_points", lambda: [alpha, beta])
    assert gcc.main(["--check"]) == 1
    captured = capsys.readouterr()
    assert "stale" in captured.err

    # Regenerate to the two-command state, then remove alpha upstream; the
    # stale catalog still documents a command that no longer exists.
    assert gcc.main([]) == 0
    monkeypatch.setattr(gcc, "load_entry_points", lambda: [beta])
    assert gcc.main(["--check"]) == 1
