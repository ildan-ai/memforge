"""The documented post-resolve sequence: resolve, memory-index-gen, memory-audit --strict.

Integrity invariant 7 requires every `tier: index` file outside `archive/` to
appear exactly once in MEMORY.md, whatever its status. A superseded member
stays on disk until it is archived, so the regenerated index must keep its
pointer and the strict audit must pass.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

from memforge.cli import audit, index_gen, resolve


def _git(repo: Path, *args: str) -> None:
    subprocess.run(["git", "-C", str(repo), *args], capture_output=True, text=True, check=True)


def _member(folder: Path, stem: str, uid: str, status: str = "active") -> Path:
    path = folder / f"{stem}.md"
    path.write_text(
        "---\n"
        f"name: Package manager choice {stem}\n"
        f"description: Which package manager this repo uses, claim {stem}\n"
        "type: feedback\n"
        f"uid: {uid}\n"
        "tier: index\n"
        "tags:\n"
        "- topic:tooling\n"
        "owner: web\n"
        f"status: {status}\n"
        "created: '2026-01-01'\n"
        "decision_topic: pkg-manager\n"
        "---\n\n"
        f"Claim {stem} body.\n\n"
        "**Why:** the build scripts depend on it.\n\n"
        "**How to apply:** use it for every install.\n",
        encoding="utf-8",
    )
    return path


def _index_gen(folder: Path, monkeypatch: pytest.MonkeyPatch) -> int:
    monkeypatch.setattr(sys, "argv", ["memory-index-gen", "--path", str(folder)])
    return index_gen.main()


@pytest.fixture
def competing_store(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    store = tmp_path / "store"
    store.mkdir()
    _git(store, "init", "-q")
    _git(store, "config", "user.email", "test@example.com")
    _git(store, "config", "user.name", "Test")
    _git(store, "config", "commit.gpgsign", "false")
    _member(store, "m1", "mem-pm1")
    _member(store, "m2", "mem-pm2")
    assert _index_gen(store, monkeypatch) == 0
    _git(store, "add", "-A")
    _git(store, "commit", "-q", "-m", "seed")
    return store


def test_index_gen_after_resolve_keeps_superseded_pointer_and_audit_passes(
    competing_store: Path, monkeypatch: pytest.MonkeyPatch
):
    store = competing_store
    assert "state: competing" in (store / "MEMORY.md").read_text(encoding="utf-8")

    assert resolve.main(["pkg-manager", "--memory-root", str(store), "--winner-uid", "mem-pm1"]) == 0
    assert audit.main(["--strict", "--path", str(store)]) == 0
    assert _index_gen(store, monkeypatch) == 0

    index = (store / "MEMORY.md").read_text(encoding="utf-8")
    assert "](m1.md)" in index
    assert "](m2.md)" in index
    assert "state: competing" not in index
    assert audit.main(["--strict", "--path", str(store)]) == 0


@pytest.mark.parametrize("status", ["superseded", "dropped", "archived"])
def test_index_gen_points_at_every_top_level_index_file_whatever_its_status(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, status: str
):
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    store = tmp_path / "store"
    store.mkdir()
    _member(store, "m1", "mem-pm1")
    _member(store, "old", "mem-old", status=status)

    assert _index_gen(store, monkeypatch) == 0

    assert "](old.md)" in (store / "MEMORY.md").read_text(encoding="utf-8")
