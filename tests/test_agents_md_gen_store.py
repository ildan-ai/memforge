"""Which memories agents-md-gen reads, and which bodies it inlines.

AGENTS.md is committed in the repo it is generated for, so it must be built
from that repo's own memory store, never from the store of whatever folder the
command happened to run in. A memory's body is inlined only when its
frontmatter pins it with a real YAML boolean; every other memory stays a
description-only index entry.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

from memforge.cli import agents_md_gen as amg
from memforge.paths import claude_code_project_dirname


@pytest.fixture
def home(tmp_path: Path, monkeypatch) -> Path:
    home = tmp_path.resolve() / "home"
    (home / ".claude" / "global-memory").mkdir(parents=True)
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: home))
    monkeypatch.delenv("MEMFORGE_MEMORY_PATH", raising=False)
    return home


def _write_mem(folder: Path, name: str, fm: str, body: str) -> None:
    folder.mkdir(parents=True, exist_ok=True)
    (folder / name).write_text("---\n" + fm + "\n---\n" + body + "\n", encoding="utf-8")


def _project(home: Path, root: Path, codename: str) -> Path:
    """A git repo plus a store under the Claude Code layout holding one memory."""
    repo = root / codename.lower()
    (repo / ".git").mkdir(parents=True)
    store = home / ".claude" / "projects" / claude_code_project_dirname(str(repo)) / "memory"
    _write_mem(
        store, "project_plan.md",
        f"name: Plan\ndescription: internal plan codename {codename}\n"
        "type: project\nsensitivity: internal",
        "body",
    )
    return repo


def _run(repo: Path, monkeypatch, *extra: str) -> int:
    monkeypatch.setattr(sys, "argv", ["agents-md-gen", "--cwd", str(repo), *extra])
    return amg.main()


def test_cwd_flag_reads_the_named_repos_store_not_the_working_folders(
    home, tmp_path, monkeypatch
):
    proj_a = _project(home, tmp_path.resolve(), "BLUEFIN")
    proj_b = _project(home, tmp_path.resolve(), "MARLIN")
    monkeypatch.chdir(proj_a)

    assert _run(proj_b, monkeypatch) == 0

    text = (proj_b / "AGENTS.md").read_text()
    assert "MARLIN" in text
    assert "BLUEFIN" not in text


def test_relative_cwd_flag_resolves_against_the_working_folder(home, tmp_path, monkeypatch):
    root = tmp_path.resolve()
    _project(home, root, "BLUEFIN")
    proj_b = _project(home, root, "MARLIN")
    monkeypatch.chdir(root)

    assert _run(Path(proj_b.name), monkeypatch) == 0

    assert "MARLIN" in (proj_b / "AGENTS.md").read_text()


def test_pinned_true_body_is_inlined(home, tmp_path, monkeypatch, capsys):
    gm = home / ".claude" / "global-memory"
    _write_mem(
        gm, "any_name.md",
        "name: Pinned rule\ndescription: pinned desc\ntype: feedback\n"
        "sensitivity: public\npinned: true",
        "PINNED BODY TEXT",
    )
    repo = tmp_path / "repo"
    repo.mkdir()

    assert _run(repo, monkeypatch, "--dry-run") == 0

    out = capsys.readouterr().out
    assert "PINNED BODY TEXT" in out
    assert "- **Pinned rule**" not in out


@pytest.mark.parametrize("value", ['"true"', "1", "false", '"yes"'])
def test_pinned_that_is_not_a_yaml_true_stays_an_index_entry(
    home, tmp_path, monkeypatch, capsys, value
):
    gm = home / ".claude" / "global-memory"
    _write_mem(
        gm, "rule.md",
        "name: Loose rule\ndescription: loose desc\ntype: feedback\n"
        f"sensitivity: public\npinned: {value}",
        "LOOSE BODY TEXT",
    )
    repo = tmp_path / "repo"
    repo.mkdir()

    assert _run(repo, monkeypatch, "--dry-run") == 0

    out = capsys.readouterr().out
    assert "LOOSE BODY TEXT" not in out
    assert "- **Loose rule**: loose desc" in out


def test_a_formerly_critical_filename_is_not_inlined_without_pinned(
    home, tmp_path, monkeypatch, capsys
):
    gm = home / ".claude" / "global-memory"
    _write_mem(
        gm, "feedback_no_fabrication.md",
        "name: Named rule\ndescription: named desc\ntype: feedback\nsensitivity: public",
        "NAMED BODY TEXT",
    )
    repo = tmp_path / "repo"
    repo.mkdir()

    assert _run(repo, monkeypatch, "--dry-run") == 0

    out = capsys.readouterr().out
    assert "NAMED BODY TEXT" not in out
    assert "- **Named rule**: named desc" in out


def test_pinned_internal_body_needs_the_inline_above_public_opt_in(
    home, tmp_path, monkeypatch, capsys
):
    gm = home / ".claude" / "global-memory"
    _write_mem(
        gm, "rule.md",
        "name: Internal pinned\ndescription: d\ntype: feedback\n"
        "sensitivity: internal\npinned: true",
        "INTERNAL PINNED BODY",
    )
    repo = tmp_path / "repo"
    repo.mkdir()

    assert _run(repo, monkeypatch, "--dry-run") == 0
    assert "INTERNAL PINNED BODY" not in capsys.readouterr().out

    assert _run(repo, monkeypatch, "--dry-run", "--inline-above-public") == 0
    assert "INTERNAL PINNED BODY" in capsys.readouterr().out


def test_pinned_restricted_memory_is_left_out_entirely(home, tmp_path, monkeypatch, capsys):
    gm = home / ".claude" / "global-memory"
    _write_mem(
        gm, "rule.md",
        "name: Restricted pinned\ndescription: restricted desc\ntype: feedback\n"
        "sensitivity: restricted\npinned: true",
        "RESTRICTED PINNED BODY",
    )
    repo = tmp_path / "repo"
    repo.mkdir()

    assert _run(repo, monkeypatch, "--dry-run", "--inline-above-public") == 0

    out = capsys.readouterr().out
    assert "RESTRICTED PINNED BODY" not in out
    assert "Restricted pinned" not in out
