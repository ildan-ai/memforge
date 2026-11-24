"""Tests for memforge.cli.watch (CommitDebouncer repo targeting)."""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

import importlib.util

import pytest

# watch.py imports watchdog at module import time and sys.exit(2)s (raises
# SystemExit, not ImportError) when it is missing, so importorskip cannot catch
# it. Probe for watchdog first and skip the whole module cleanly if absent.
if importlib.util.find_spec("watchdog") is None:
    pytest.skip("watchdog not installed", allow_module_level=True)

from memforge.cli import watch  # noqa: E402


def _git(repo: Path, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["git", "-C", str(repo), *args],
        capture_output=True, text=True, check=True,
    )


def _init_repo(path: Path) -> None:
    path.mkdir(parents=True, exist_ok=True)
    _git(path, "init", "-q")
    _git(path, "config", "user.email", "test@example.com")
    _git(path, "config", "user.name", "Test")
    (path / "seed.md").write_text("seed\n", encoding="utf-8")
    _git(path, "add", "-A")
    _git(path, "commit", "-q", "-m", "seed")


def test_commit_targets_own_repo_not_process_cwd(tmp_path: Path, monkeypatch):
    """_commit must commit into its own repo even when the process cwd points at
    a DIFFERENT repo. Regression for lifecycle/watch-01 (os.chdir race)."""
    repo_a = tmp_path / "repo_a"
    repo_b = tmp_path / "repo_b"
    _init_repo(repo_a)
    _init_repo(repo_b)

    # Make a pending change in repo_a only.
    (repo_a / "new_a.md").write_text("change in A\n", encoding="utf-8")

    # Point the process cwd at repo_b to simulate a concurrent debouncer that
    # has chdir'd elsewhere. The old os.chdir(self.repo) code would still work
    # in isolation, but with `-C <repo>` the cwd is irrelevant; assert that.
    monkeypatch.chdir(repo_b)

    deb = watch.CommitDebouncer(repo_a, debounce_ms=10, quiet=True)
    deb._commit()

    # repo_a got the commit.
    log_a = _git(repo_a, "log", "--format=%s").stdout.splitlines()
    assert len(log_a) == 2
    assert log_a[0].startswith("memory: filesystem write")
    files_a = _git(repo_a, "show", "--name-only", "--format=", "HEAD").stdout
    assert "new_a.md" in files_a

    # repo_b is untouched (no stray commit landed there).
    log_b = _git(repo_b, "log", "--format=%s").stdout.splitlines()
    assert len(log_b) == 1


def test_commit_noop_when_clean(tmp_path: Path):
    """A clean repo produces no commit."""
    repo = tmp_path / "repo"
    _init_repo(repo)
    deb = watch.CommitDebouncer(repo, debounce_ms=10, quiet=True)
    deb._commit()
    log = _git(repo, "log", "--format=%s").stdout.splitlines()
    assert len(log) == 1


def test_commit_does_not_change_process_cwd(tmp_path: Path, monkeypatch):
    """_commit must not mutate the process working directory (no os.chdir)."""
    repo = tmp_path / "repo"
    _init_repo(repo)
    (repo / "x.md").write_text("x\n", encoding="utf-8")

    start = tmp_path / "elsewhere"
    start.mkdir()
    monkeypatch.chdir(start)
    before = os.getcwd()

    watch.CommitDebouncer(repo, debounce_ms=10, quiet=True)._commit()

    assert os.getcwd() == before


def test_commit_scoped_to_md_excludes_stray_files(tmp_path: Path):
    """watch-01: a stray non-.md file (editor swap, .DS_Store) must NOT ride
    along in the scoped `memory: filesystem write` commit. Only *.md and
    .memforge/ content is staged."""
    repo = tmp_path / "repo"
    _init_repo(repo)
    (repo / "real.md").write_text("real memory\n", encoding="utf-8")
    (repo / ".DS_Store").write_text("junk\n", encoding="utf-8")
    (repo / "scratch.swp").write_text("swap\n", encoding="utf-8")

    watch.CommitDebouncer(repo, debounce_ms=10, quiet=True)._commit()

    committed = _git(repo, "show", "--name-only", "--format=", "HEAD").stdout
    assert "real.md" in committed
    assert ".DS_Store" not in committed
    assert "scratch.swp" not in committed
    # The stray files remain untracked, not committed.
    untracked = _git(repo, "ls-files", "--others", "--exclude-standard").stdout
    assert ".DS_Store" in untracked
    assert "scratch.swp" in untracked


def test_commit_leaves_the_recall_index_out(tmp_path: Path):
    """The recall index is a local cache that memory-recall rebuilds; it is never
    committed, so one that arrives by pull cannot be a forged one."""
    repo = tmp_path / "repo"
    _init_repo(repo)
    mf = repo / ".memforge"
    mf.mkdir()
    (mf / "recall-index.json").write_text("{}\n", encoding="utf-8")

    watch.CommitDebouncer(repo, debounce_ms=10, quiet=True)._commit()

    assert _git(repo, "log", "--format=%s").stdout.splitlines() == ["seed"]
    assert "?? .memforge/" in _git(repo, "status", "--porcelain").stdout


@pytest.mark.parametrize("name", [
    ".memforge/allowed_signers",
    ".memforge/allowed_gpg_fingerprints",
    ".memforge/allowed_identities",
])
def test_commit_never_commits_a_signer_list(tmp_path: Path, name: str):
    """An automatic commit must not carry a signer list change: who may sign
    is decided in a deliberate commit, not because a process wrote a file."""
    repo = tmp_path / "repo"
    _init_repo(repo)
    lst = repo / name
    lst.parent.mkdir(exist_ok=True)
    lst.write_text("owner key\n", encoding="utf-8")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", "list")

    lst.write_text("owner key\nplanted key\n", encoding="utf-8")
    (repo / "note.md").write_text("note\n", encoding="utf-8")

    watch.CommitDebouncer(repo, debounce_ms=10, quiet=True)._commit()

    committed = _git(repo, "show", "--name-only", "--format=", "HEAD").stdout.split()
    assert committed == ["note.md"]
    assert _git(repo, "show", f"HEAD:{name}").stdout == "owner key\n"
    assert name in _git(repo, "status", "--porcelain").stdout


def test_commit_with_only_a_signer_list_change_commits_nothing(tmp_path: Path):
    repo = tmp_path / "repo"
    _init_repo(repo)
    (repo / ".memforge").mkdir()
    (repo / ".memforge" / "allowed_signers").write_text("planted key\n", encoding="utf-8")

    watch.CommitDebouncer(repo, debounce_ms=10, quiet=True)._commit()

    assert len(_git(repo, "log", "--format=%s").stdout.splitlines()) == 1


def test_commit_leaves_out_of_scope_staged_files_staged(tmp_path: Path):
    """A staged file outside the watcher's scope is not swept into the automatic commit."""
    repo = tmp_path / "repo"
    _init_repo(repo)
    (repo / "settings.env").write_text("token\n", encoding="utf-8")
    _git(repo, "add", "settings.env")
    (repo / "note.md").write_text("note\n", encoding="utf-8")

    watch.CommitDebouncer(repo, debounce_ms=10, quiet=True)._commit()

    committed = _git(repo, "show", "--name-only", "--format=", "HEAD").stdout.split()
    assert committed == ["note.md"]
    assert "A  settings.env" in _git(repo, "status", "--porcelain").stdout


@pytest.mark.skipif(
    os.name == "nt",
    reason="a Windows file name cannot hold a colon, so a name that starts like a pathspec cannot be made",
)
def test_commit_reads_file_names_literally(tmp_path: Path):
    """A memory file whose name is a pathspec pattern commits only itself."""
    repo = tmp_path / "repo"
    _init_repo(repo)
    (repo / "private").mkdir()
    (repo / "private" / "draft.txt").write_text("draft\n", encoding="utf-8")
    _git(repo, "add", "private/draft.txt")
    odd = repo / ":(top)private"
    odd.mkdir()
    (odd / "x.md").write_text("x\n", encoding="utf-8")

    watch.CommitDebouncer(repo, debounce_ms=10, quiet=True)._commit()

    committed = _git(repo, "show", "--name-only", "--format=", "HEAD").stdout.splitlines()
    assert committed == [":(top)private/x.md"]


@pytest.mark.skipif(os.name == "nt", reason="file names are text on this platform")
def test_commit_survives_a_file_name_that_is_not_utf8(tmp_path: Path):
    repo = tmp_path / "repo"
    _init_repo(repo)
    odd = os.fsencode(repo) + b"/caf\xe9.md"
    try:
        with open(odd, "wb") as f:
            f.write(b"x\n")
    except OSError:
        pytest.skip("this file system refuses names that are not UTF-8")
    (repo / "note.md").write_text("note\n", encoding="utf-8")

    watch.CommitDebouncer(repo, debounce_ms=10, quiet=True)._commit()

    committed = _git(repo, "show", "--name-only", "--format=", "HEAD").stdout
    assert "note.md" in committed


@pytest.mark.parametrize("name", [
    ".memforge/config.yaml",
    ".memforge/snoozes/t.yaml",
    ".memforge/audit-waivers.yaml",
    ".memforge/recall-synonyms.yaml",
    ".memforge/notes.md",
])
def test_commit_leaves_memforge_control_files_out(tmp_path: Path, name: str):
    """Config, snooze, waiver and other .memforge/ edits must land in their own
    `memforge:` commits, so the automatic commit carries none of them."""
    repo = tmp_path / "repo"
    _init_repo(repo)
    f = repo / name
    f.parent.mkdir(parents=True, exist_ok=True)
    f.write_text("k: 1\n", encoding="utf-8")
    (repo / "note.md").write_text("note\n", encoding="utf-8")

    watch.CommitDebouncer(repo, debounce_ms=10, quiet=True)._commit()

    committed = _git(repo, "show", "--name-only", "--format=", "HEAD").stdout.split()
    assert committed == ["note.md"]
    assert _git(repo, "ls-files", "--", name).stdout == ""


def test_commit_leaves_a_hand_staged_config_staged(tmp_path: Path):
    repo = tmp_path / "repo"
    _init_repo(repo)
    (repo / ".memforge").mkdir()
    (repo / ".memforge" / "config.yaml").write_text("k: 1\n", encoding="utf-8")
    _git(repo, "add", ".memforge/config.yaml")
    (repo / "note.md").write_text("note\n", encoding="utf-8")

    watch.CommitDebouncer(repo, debounce_ms=10, quiet=True)._commit()

    committed = _git(repo, "show", "--name-only", "--format=", "HEAD").stdout.split()
    assert committed == ["note.md"]
    assert "A  .memforge/config.yaml" in _git(repo, "status", "--porcelain").stdout


def test_commit_carries_generated_memory_md_but_not_the_recall_index(tmp_path: Path):
    """MEMORY.md is rewritten by memforge tools and rides along with the memory
    change; a recall index (even a tracked one an older version committed) does
    not, and a deletion of it stays unstaged."""
    repo = tmp_path / "repo"
    _init_repo(repo)
    (repo / ".memforge").mkdir()
    index = repo / ".memforge" / "recall-index.json"
    index.write_text("{}\n", encoding="utf-8")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", "index")
    index.unlink()
    (repo / "MEMORY.md").write_text("# Memory Index\n", encoding="utf-8")

    watch.CommitDebouncer(repo, debounce_ms=10, quiet=True)._commit()

    shown = _git(repo, "show", "--name-status", "--format=", "HEAD").stdout.splitlines()
    assert shown == ["A\tMEMORY.md"]
    assert " D .memforge/recall-index.json" in _git(repo, "status", "--porcelain").stdout.splitlines()


def test_commit_survives_an_ignored_recall_index(tmp_path: Path):
    repo = tmp_path / "repo"
    _init_repo(repo)
    (repo / ".gitignore").write_text(".memforge/recall-index.json\n", encoding="utf-8")
    (repo / ".memforge").mkdir()
    (repo / ".memforge" / "recall-index.json").write_text("{}\n", encoding="utf-8")
    (repo / "note.md").write_text("note\n", encoding="utf-8")

    watch.CommitDebouncer(repo, debounce_ms=10, quiet=True)._commit()

    committed = _git(repo, "show", "--name-only", "--format=", "HEAD").stdout.split()
    assert committed == ["note.md"]


@pytest.mark.skipif(os.name == "nt", reason="needs symbolic links")
def test_commit_with_a_linked_memforge_folder_still_commits_notes(tmp_path: Path):
    """A .memforge that is a link is never followed, and the memory change
    beside it is still committed."""
    repo = tmp_path / "repo"
    _init_repo(repo)
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "planted.md").write_text("x\n", encoding="utf-8")
    (repo / ".memforge").symlink_to(outside, target_is_directory=True)
    (repo / "note.md").write_text("note\n", encoding="utf-8")

    watch.CommitDebouncer(repo, debounce_ms=10, quiet=True)._commit()

    committed = _git(repo, "show", "--name-only", "--format=", "HEAD").stdout.split()
    assert committed == ["note.md"]
