"""memforge-detect must not run code found in the working directory.

The detection pass starts child interpreters for lint, cluster-suggest and
dedup. A child started with `python -m` imports from the working directory
first, so a module planted in a memory folder would run as code whenever
someone ran the pass from inside that folder.
"""

from __future__ import annotations

import os
from pathlib import Path

import memforge
from memforge.cli import detect

# Modules every child imports at start-up, plus the package itself.
_PLANTED_MODULES = ("yaml", "json", "argparse")


def _plant_modules(folder: Path, sentinel: Path) -> None:
    payload = (
        "import os\n"
        f"with open({str(sentinel)!r}, 'a') as handle:\n"
        "    handle.write(__name__ + ' ran in pid ' + str(os.getpid()) + '\\n')\n"
    )
    for name in _PLANTED_MODULES:
        (folder / f"{name}.py").write_text(payload, encoding="utf-8")
    package = folder / "memforge"
    package.mkdir()
    (package / "__init__.py").write_text(payload, encoding="utf-8")


def _memory(folder: Path) -> None:
    (folder / "feedback_rule.md").write_text(
        "---\n"
        "name: Rule\n"
        "description: A rule used by the isolation tests\n"
        "type: feedback\n"
        "sensitivity: internal\n"
        "---\n\n"
        "Body.\n**Why:** test.\n**How to apply:** always.\n",
        encoding="utf-8",
    )


def test_collection_does_not_run_modules_planted_in_working_directory(
    tmp_path: Path, monkeypatch
):
    folder = tmp_path / "memory"
    folder.mkdir()
    _memory(folder)
    sentinel = tmp_path / "planted-code-ran"
    _plant_modules(folder, sentinel)
    monkeypatch.chdir(folder)
    # `true` is registered as a local runner so the dedup child is started
    # without reaching any real model.
    monkeypatch.setenv("MEMFORGE_LOCAL_DISPATCHERS", "true")

    detect._collect_recall_weakness_findings([folder])
    detect._collect_cluster_findings([folder])
    detect._collect_dedup_findings([folder], "true")

    assert not sentinel.exists(), sentinel.read_text(encoding="utf-8")


def test_child_imports_from_where_the_parent_does_and_not_the_working_directory(
    tmp_path: Path, monkeypatch
):
    """Isolated mode ignores PYTHONPATH, so the child is handed the parent's
    import path. This must hold in a source checkout as well as an install."""
    monkeypatch.chdir(tmp_path)
    package_root = str(Path(memforge.__file__).resolve().parents[1])

    proc = detect._run_module("site", [], timeout=60)

    assert proc.returncode == 0, proc.stderr
    # Windows paths compare without case: a venv lists `lib`, resolve() gives `Lib`.
    listed = os.path.normcase(proc.stdout)
    assert os.path.normcase(repr(package_root)) in listed
    assert os.path.normcase(repr(str(tmp_path))) not in listed
    assert repr("") not in proc.stdout


def test_isolated_child_still_runs_the_lint_module(tmp_path: Path, monkeypatch):
    folder = tmp_path / "memory"
    folder.mkdir()
    _memory(folder)
    monkeypatch.chdir(folder)

    proc = detect._run_module(
        "memforge.cli.lint", ["--path", str(folder), "--json"], timeout=60
    )

    assert proc.returncode in (0, 1), proc.stderr
    assert "feedback_rule.md" in proc.stdout
