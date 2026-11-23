"""agents-md-gen copies nothing from the user's home folder.

AGENTS.md is committed and often pushed to a public repository, so it is
built from the memory folders and the repository's own CLAUDE.md only. The
user's private `~/.claude/CLAUDE.md` is never opened, through a link or not.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

from memforge.cli import agents_md_gen as amg

HOME_CLAUDE_MD = (
    "## Private notes\nNOT-COPIED-LINE\n\n"
    "## Writing voice\nPRIVATE-VOICE-LINE\n\n### Internal\nPRIVATE-SUBSECTION\n\n"
    "## No fabrication\nPRIVATE-FABRICATION-LINE\n\n"
    "## One question at a time\nPRIVATE-QUESTION-LINE\n\n"
    "## Shell\nPRIVATE-SHELL-LINE\n\n"
    "## Shellfish allergy\nPRIVATE-MEDICAL-LINE\n"
)

# Paths opened while agents-md-gen runs; None outside a run. An audit hook
# cannot be removed, so it records only while a run sets this list.
_opened: list[str] | None = None


def _record_open(event: str, args: tuple) -> None:
    if _opened is not None and event == "open" and isinstance(args[0], (str, bytes, os.PathLike)):
        _opened.append(os.fsdecode(args[0]))


sys.addaudithook(_record_open)


@pytest.fixture
def home(tmp_path: Path, monkeypatch) -> Path:
    home = tmp_path.resolve() / "home"
    (home / ".claude" / "global-memory").mkdir(parents=True)
    (home / ".claude" / "global-memory" / "a.md").write_text(
        "---\nname: A\ndescription: store rule\ntype: feedback\nsensitivity: public\n---\nbody\n",
        encoding="utf-8",
    )
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: home))
    monkeypatch.delenv("MEMFORGE_MEMORY_PATH", raising=False)
    return home


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    repo = tmp_path.resolve() / "repo"
    (repo / ".git").mkdir(parents=True)
    (repo / "CLAUDE.md").write_text("REPO-RULE-LINE\n", encoding="utf-8")
    return repo


def _run(repo: Path, monkeypatch) -> tuple[str, list[str]]:
    """(the AGENTS.md written, the paths opened while generating it)."""
    global _opened
    monkeypatch.setattr(sys, "argv", ["agents-md-gen", "--cwd", str(repo), "--max-sensitivity", "public"])
    _opened = []
    try:
        assert amg.main() == 0
        opened = _opened
    finally:
        _opened = None
    return (repo / "AGENTS.md").read_text(encoding="utf-8"), opened


def _assert_nothing_from_home(text: str) -> None:
    assert "PRIVATE" not in text and "NOT-COPIED" not in text and "OUTSIDE" not in text
    assert "~/.claude/CLAUDE.md" not in text
    assert "Voice and rules" not in text


def test_home_claude_md_text_never_reaches_agents_md(home, repo, monkeypatch):
    claude_md = home / ".claude" / "CLAUDE.md"
    claude_md.write_text(HOME_CLAUDE_MD, encoding="utf-8")

    text, opened = _run(repo, monkeypatch)

    _assert_nothing_from_home(text)
    assert "store rule" in text
    assert "REPO-RULE-LINE" in text
    assert str(claude_md) not in opened


def test_a_link_at_the_home_claude_md_path_is_never_opened(home, repo, tmp_path, monkeypatch):
    outside = tmp_path.resolve() / "outside" / "secret-notes.md"
    outside.parent.mkdir()
    outside.write_text("## Shell\nOUTSIDE-FILE-LINE\n", encoding="utf-8")
    link = home / ".claude" / "CLAUDE.md"
    link.symlink_to(outside)

    text, opened = _run(repo, monkeypatch)

    assert str(link) not in opened
    assert str(outside) not in opened
    _assert_nothing_from_home(text)
