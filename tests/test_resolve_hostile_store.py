"""memforge-resolve on a store whose committed content is hostile: a linked
snooze folder, and member file names that git would read as wildcards."""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

import pytest

from memforge.cli import resolve
from memforge.frontmatter import parse

needs_links = pytest.mark.skipif(os.name == "nt", reason="needs POSIX symlinks")

# A name git reads as a wildcard. `*` is not a legal Windows file name, so
# there a bracket pattern, which git also reads as a wildcard, stands in.
WILD_FILE = "[a-z].md" if os.name == "nt" else "*.md"


def _git(repo: Path, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["git", "-C", str(repo), *args],
        capture_output=True, text=True, check=True,
    )


def _member(uid: str, topic: str | None = "db") -> str:
    lines = ["---", f"name: {uid}", f"description: {uid} fixture", "type: feedback",
             f"uid: {uid}", "status: active"]
    if topic is not None:
        lines.append(f"decision_topic: {topic}")
    return "\n".join(lines) + "\n---\n\nBody.\n"


@pytest.fixture
def store(tmp_path: Path) -> Path:
    """A store that is its own repository, with live members a.md and b.md on
    topic `db` and an unrelated c.md, committed."""
    store = tmp_path / "store"
    store.mkdir()
    _git(store, "init", "-q")
    _git(store, "config", "user.email", "test@example.com")
    _git(store, "config", "user.name", "Test")
    _git(store, "config", "commit.gpgsign", "false")
    (store / "a.md").write_text(_member("mem-a"), encoding="utf-8")
    (store / "b.md").write_text(_member("mem-b"), encoding="utf-8")
    (store / "c.md").write_text(_member("mem-c", topic=None), encoding="utf-8")
    _git(store, "add", "-A")
    _git(store, "commit", "-q", "-m", "seed")
    return store


def _resolve(store: Path, topic: str = "db") -> int:
    """The exit status, whether resolve returns it or exits with it."""
    try:
        return resolve.main([topic, "--memory-root", str(store), "--winner-uid", "mem-a"])
    except SystemExit as exc:
        return exc.code


def _status(path: Path) -> str:
    return parse(path.read_text(encoding="utf-8"))[0]["status"]


@pytest.fixture(params=[True, False], ids=["by-folder", "by-path"])
def folder_checks(request, monkeypatch):
    """Run once with the descriptor walk and once with the by-path checks
    used where os.open takes no dir_fd."""
    if request.param and os.name == "nt":
        pytest.skip("Windows cannot open a folder as a descriptor, so the walk cannot run")
    monkeypatch.setattr(resolve, "_OPEN_BY_FOLDER", request.param, raising=False)


# ---------- a linked or non-folder snooze folder ----------


@needs_links
def test_linked_snooze_folder_leaving_the_store_deletes_nothing(store, tmp_path, capsys,
                                                               folder_checks):
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "db.yaml").write_text("precious\n", encoding="utf-8")
    (store / ".memforge").mkdir()
    os.symlink(outside, store / ".memforge" / "snoozes")
    _git(store, "add", "-A")
    _git(store, "commit", "-q", "-m", "memory: add notes")
    head = _git(store, "rev-parse", "HEAD").stdout

    assert _resolve(store) == 2

    assert (outside / "db.yaml").read_text(encoding="utf-8") == "precious\n"
    assert "nothing changed" in capsys.readouterr().err
    assert _status(store / "b.md") == "active"
    assert _git(store, "rev-parse", "HEAD").stdout == head


@needs_links
def test_snooze_folder_linked_to_the_config_folder_deletes_nothing(store, folder_checks):
    (store / ".memforge").mkdir()
    (store / ".memforge" / "config.yaml").write_text("audit: {}\n", encoding="utf-8")
    os.symlink(".", store / ".memforge" / "snoozes")
    (store / "a.md").write_text(_member("mem-a", topic="config"), encoding="utf-8")
    (store / "b.md").write_text(_member("mem-b", topic="config"), encoding="utf-8")
    _git(store, "add", "-A")
    _git(store, "commit", "-q", "-m", "memory: add notes")
    head = _git(store, "rev-parse", "HEAD").stdout

    assert _resolve(store, "config") == 2

    assert (store / ".memforge" / "config.yaml").read_text(encoding="utf-8") == "audit: {}\n"
    assert _git(store, "rev-parse", "HEAD").stdout == head


@needs_links
def test_linked_memforge_folder_deletes_nothing(store, tmp_path):
    outside = tmp_path / "outside"
    (outside / "snoozes").mkdir(parents=True)
    (outside / "snoozes" / "db.yaml").write_text("precious\n", encoding="utf-8")
    os.symlink(outside, store / ".memforge")
    _git(store, "add", "-A")
    _git(store, "commit", "-q", "-m", "memory: add notes")

    assert _resolve(store) == 2

    assert (outside / "snoozes" / "db.yaml").exists()
    assert _status(store / "b.md") == "active"


def test_memforge_that_is_a_file_is_refused(store, folder_checks):
    (store / ".memforge").write_text("not a folder\n", encoding="utf-8")
    _git(store, "add", "-A")
    _git(store, "commit", "-q", "-m", "memory: add notes")

    assert _resolve(store) == 2
    assert _status(store / "b.md") == "active"


@needs_links
def test_snooze_file_that_is_a_link_is_refused(store, tmp_path):
    outside = tmp_path / "outside.yaml"
    outside.write_text("precious\n", encoding="utf-8")
    (store / ".memforge" / "snoozes").mkdir(parents=True)
    os.symlink(outside, store / ".memforge" / "snoozes" / "db.yaml")
    _git(store, "add", "-A")
    _git(store, "commit", "-q", "-m", "memory: add notes")

    assert _resolve(store) == 2
    assert outside.exists()
    assert os.path.islink(store / ".memforge" / "snoozes" / "db.yaml")


def test_real_snooze_folder_still_resolves_and_deletes_the_snooze(store, folder_checks):
    snooze = store / ".memforge" / "snoozes" / "db.yaml"
    snooze.parent.mkdir(parents=True)
    snooze.write_text("decision_topic: db\n", encoding="utf-8")
    _git(store, "add", "-A")
    _git(store, "commit", "-q", "-m", "memforge: snooze db")

    assert _resolve(store) == 0

    assert not snooze.exists()
    files = _git(store, "show", "--name-only", "--format=", "HEAD").stdout.split()
    assert sorted(files) == [".memforge/snoozes/db.yaml", "a.md", "b.md"]


# ---------- member names are file names, not pathspecs ----------


def test_wildcard_member_name_commits_only_the_group(store):
    _git(store, "mv", "b.md", WILD_FILE)
    _git(store, "commit", "-q", "-m", "rename")
    with open(store / "c.md", "a", encoding="utf-8") as handle:
        handle.write("Staged edit.\n")
    _git(store, "add", "c.md")
    (store / "notes.md").write_text("untracked\n", encoding="utf-8")
    (store / "sub").mkdir()
    (store / "sub" / "other.md").write_text("untracked\n", encoding="utf-8")

    assert _resolve(store) == 0

    files = _git(store, "show", "--name-only", "--format=", "HEAD").stdout.splitlines()
    assert sorted(files) == sorted([WILD_FILE, "a.md"])
    assert _git(store, "diff", "--cached", "--name-only").stdout.split() == ["c.md"]
    untracked = _git(store, "ls-files", "--others", "--exclude-standard").stdout.split()
    assert sorted(untracked) == ["notes.md", "sub/other.md"]
