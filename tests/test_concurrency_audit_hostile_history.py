"""Tier 2 (commit-log) on committed history written to confuse it: subjects
holding line-break characters, config link chains the file system resolves
differently from their text, links where the snooze folder is opened, and
file names git would read as wildcards (SPEC.md §"Audit invariants for
competing claims", §"Config file", §"Snooze record")."""

from __future__ import annotations

import os
import subprocess
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from memforge.cli import resolve
from memforge.cli._concurrency_audit import tier2_findings

needs_links = pytest.mark.skipif(os.name == "nt", reason="needs POSIX symlinks")

# A name git reads as a wildcard. `*` is not a legal Windows file name, so
# there a bracket pattern, which git also reads as a wildcard, stands in.
WILD_FILE = "[a-z].md" if os.name == "nt" else "*.md"
WILD_FOLDER = "mem[a-z]" if os.name == "nt" else "mem*"


def _git(repo: Path, *args: str, env: dict[str, str] | None = None) -> str:
    return subprocess.run(
        ["git", "-C", str(repo), *args],
        capture_output=True, text=True, check=True, env=env,
    ).stdout.strip()


def _member(uid: str, *, topic: str | None, description: str = "") -> str:
    lines = ["---", f"name: {uid}", f"description: {description or uid + ' fixture'}",
             "type: feedback", f"uid: {uid}", "status: active"]
    if topic is not None:
        lines.append(f"decision_topic: {topic}")
    return "\n".join(lines) + "\n---\n\nBody.\n"


def _commit(repo: Path, message: str, *, days_ago: int = 0) -> None:
    env = None
    if days_ago:
        stamp = (datetime.now(timezone.utc) - timedelta(days=days_ago)).isoformat()
        env = {**os.environ, "GIT_AUTHOR_DATE": stamp, "GIT_COMMITTER_DATE": stamp}
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "--cleanup=verbatim", "-m", message, env=env)


@pytest.fixture
def store(tmp_path: Path) -> tuple[Path, Path]:
    """A repo whose memory folder holds a two-member group `db-choice`
    (a.md, b.md) and an unrelated c.md, committed as the seed on `main`."""
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init", "-q", "-b", "main")
    _git(repo, "config", "user.email", "test@example.com")
    _git(repo, "config", "user.name", "Test")
    _git(repo, "config", "commit.gpgsign", "false")
    memory = repo / "memory"
    memory.mkdir()
    (memory / "a.md").write_text(_member("mem-a", topic="db-choice"), encoding="utf-8")
    (memory / "b.md").write_text(_member("mem-b", topic="db-choice"), encoding="utf-8")
    (memory / "c.md").write_text(_member("mem-c", topic=None), encoding="utf-8")
    _commit(repo, "seed")
    return repo, memory


def _severity(findings, severity: str) -> list[str]:
    return [m for s, m in findings if s == severity]


# ---------- a subject is one commit's subject and nothing more ----------


@pytest.mark.parametrize("breaker", [
    " ", " ", "\x85", "\x0b", "\x0c", "\x1c", "\x1d", "\x1e", "\r", "\r\n",
])
def test_line_break_in_a_subject_hides_no_commit(store, breaker):
    repo, memory = store
    (memory / "b.md").unlink()
    _commit(repo, f"cleanup{breaker}notes")
    (memory / "c.md").write_text(_member("mem-c", topic=None, description="later"),
                                 encoding="utf-8")
    _commit(repo, "later")

    findings = tier2_findings(memory)

    assert not any("SKIPPED" in m for m in _severity(findings, "WARN"))
    assert any("group member deletion" in m and "b.md" in m
               for m in _severity(findings, "BLOCKER"))


# ---------- config link chains, as the file system follows them ----------


def _config_link(repo: Path, memory: Path, target: str) -> None:
    (memory / ".memforge").mkdir(exist_ok=True)
    os.symlink(target, memory / ".memforge" / "config.yaml")
    _commit(repo, "memforge: config point at notes")


@needs_links
def test_dotdot_after_an_inner_link_is_applied_after_following_it(store):
    repo, memory = store
    (repo / "notes").mkdir()
    (repo / "other" / "inner").mkdir(parents=True)
    (repo / "other" / "inner" / "keep.txt").write_text("x\n", encoding="utf-8")
    os.symlink("../other/inner", repo / "notes" / "link")
    (repo / "notes" / "settings.yaml").write_text("audit: {}\n", encoding="utf-8")
    (repo / "other" / "settings.yaml").write_text("audit: {}\n", encoding="utf-8")
    _commit(repo, "add notes")
    _config_link(repo, memory, "../../notes/link/../settings.yaml")
    (repo / "other" / "settings.yaml").write_text(
        "audit:\n  enforce_sensitivity_export_gate: false\n", encoding="utf-8"
    )
    _commit(repo, "update notes")

    blockers = _severity(tier2_findings(memory), "BLOCKER")

    assert any("'update notes'" in m and "config edit" in m and "other/settings.yaml" in m
               for m in blockers)
    assert not any("'memforge: config point at notes'" in m for m in blockers)


@needs_links
@pytest.mark.parametrize("spelling", ["git-top", "real-path"])
def test_absolute_link_into_the_repository_is_followed(store, spelling):
    repo, memory = store
    (repo / "notes").mkdir()
    (repo / "notes" / "settings.yaml").write_text("audit: {}\n", encoding="utf-8")
    _commit(repo, "add notes")
    top = _git(repo, "rev-parse", "--show-toplevel")
    if spelling == "real-path":
        top = os.path.realpath(top)
    _config_link(repo, memory, f"{top}/notes/settings.yaml")
    (repo / "notes" / "settings.yaml").write_text(
        "audit:\n  enforce_sensitivity_export_gate: false\n", encoding="utf-8"
    )
    _commit(repo, "update notes")

    blockers = _severity(tier2_findings(memory), "BLOCKER")

    assert any("'update notes'" in m and "config edit" in m and "notes/settings.yaml" in m
               for m in blockers)
    assert not any("'memforge: config point at notes'" in m for m in blockers)


@needs_links
@pytest.mark.parametrize("target", ["/etc/memforge-config.yaml", "../../../outside.yaml",
                                    "loop.yaml"])
def test_config_link_that_cannot_be_resolved_in_the_repository_is_a_blocker(store, target):
    repo, memory = store
    (memory / ".memforge").mkdir()
    if target == "loop.yaml":
        os.symlink("config.yaml", memory / ".memforge" / "loop.yaml")
        _commit(repo, "add loop")
    _config_link(repo, memory, target)

    blockers = _severity(tier2_findings(memory), "BLOCKER")

    assert any("'memforge: config point at notes'" in m and "config link" in m
               and "config.yaml" in m for m in blockers)


@needs_links
def test_config_link_inside_the_repository_in_a_config_commit_passes(store):
    repo, memory = store
    (repo / "notes").mkdir()
    (repo / "notes" / "settings.yaml").write_text("audit: {}\n", encoding="utf-8")
    _commit(repo, "add notes")
    _config_link(repo, memory, "../../notes/settings.yaml")

    assert _severity(tier2_findings(memory), "BLOCKER") == []


# ---------- links where the snooze folder is opened ----------


@needs_links
@pytest.mark.parametrize("link, target", [
    (".memforge/snoozes", "../../outside"),
    (".memforge/snoozes", "."),
    (".memforge", "../elsewhere"),
    (".memforge/snoozes/db-choice.yaml", "../config.yaml"),
])
def test_committed_link_on_the_snooze_folder_path_is_a_blocker(store, link, target):
    repo, memory = store
    (repo / "elsewhere" / "snoozes").mkdir(parents=True)
    (repo / "elsewhere" / "snoozes" / "keep.yaml").write_text("x: 1\n", encoding="utf-8")
    path = memory / link
    path.parent.mkdir(parents=True, exist_ok=True)
    os.symlink(target, path)
    _commit(repo, "memory: add notes")

    blockers = _severity(tier2_findings(memory), "BLOCKER")

    assert any("'memory: add notes'" in m and "snooze folder link" in m
               and f"memory/{link} " in m for m in blockers)


def test_real_snooze_folder_has_no_link_blocker(store):
    repo, memory = store
    snooze = memory / ".memforge" / "snoozes" / "db-choice.yaml"
    snooze.parent.mkdir(parents=True)
    snooze.write_text("decision_topic: db-choice\n", encoding="utf-8")
    _commit(repo, "memforge: snooze db-choice")

    assert _severity(tier2_findings(memory), "BLOCKER") == []


# ---------- file names are not pathspecs ----------


def test_wildcard_store_folder_name_reads_only_its_own_waiver_state(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init", "-q", "-b", "main")
    _git(repo, "config", "user.email", "test@example.com")
    _git(repo, "config", "user.name", "Test")
    _git(repo, "config", "commit.gpgsign", "false")
    memory = repo / WILD_FOLDER
    (memory / ".memforge").mkdir(parents=True)
    (memory / "a.md").write_text(_member("mem-a", topic=None), encoding="utf-8")
    (repo / "memo" / ".memforge").mkdir(parents=True)
    (repo / "memo" / ".memforge" / "audit-waivers.yaml").write_text("waivers: []\n",
                                                                     encoding="utf-8")
    _commit(repo, "seed")
    (repo / "memo" / ".memforge" / "audit-waivers.yaml").write_text("edited: true\n",
                                                                     encoding="utf-8")

    assert not any("audit-waivers.yaml" in m for m in _severity(tier2_findings(memory), "WARN"))



def test_wildcard_file_name_does_not_pull_old_commits_into_a_merge_judgment(store):
    repo, memory = store
    _git(repo, "mv", "memory/b.md", f"memory/{WILD_FILE}")
    _commit(repo, "rename", days_ago=50)
    _git(repo, "branch", "side")
    (repo / "main.txt").write_text("main\n", encoding="utf-8")
    _commit(repo, "main work")
    _git(repo, "checkout", "-q", "side")
    (memory / "c.md").write_text(_member("mem-c", topic=None, description="old"),
                                 encoding="utf-8")
    _commit(repo, "old notes", days_ago=40)
    assert resolve.main(["db-choice", "--memory-root", str(memory), "--winner-uid", "mem-a"]) == 0
    _git(repo, "checkout", "-q", "main")
    _git(repo, "merge", "-q", "--no-ff", "--no-edit", "side")

    findings = tier2_findings(memory, audit_window_days=30)

    assert _severity(findings, "BLOCKER") == []
