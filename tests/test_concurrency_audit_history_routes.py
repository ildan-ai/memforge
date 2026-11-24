"""Tier 2 (commit-log) routes around the member, config and snooze gates:
merges, linked or case-variant config paths, moves into `archive/`, and the
`git` program the audit runs (SPEC.md §"Audit invariants for competing
claims", §"Config file")."""

from __future__ import annotations

import os
import shutil
import stat
import subprocess
from pathlib import Path

import pytest

from memforge.cli import resolve
from memforge.cli._concurrency_audit import _load_waivers, tier2_findings

needs_links = pytest.mark.skipif(os.name == "nt", reason="needs POSIX symlinks")


def _git(repo: Path, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["git", "-C", str(repo), *args],
        capture_output=True, text=True, check=True,
    )


def _member(uid: str, *, topic: str | None, status: str = "active",
            extra: str = "", body: str = "Body.") -> str:
    lines = [
        "---",
        f"name: {uid}",
        f"description: {uid} fixture",
        "type: feedback",
        f"uid: {uid}",
        f"status: {status}",
    ]
    if topic is not None:
        lines.append(f"decision_topic: {topic}")
    return "\n".join(lines) + "\n" + extra + f"---\n\n{body}\n"


def _commit(repo: Path, message: str) -> str:
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", message)
    return _git(repo, "rev-parse", "HEAD").stdout.strip()


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


def _orphan_side(repo: Path, files: dict[str, str]) -> None:
    """A branch `side` with no history in common with `main`."""
    _git(repo, "checkout", "-q", "--orphan", "side")
    _git(repo, "rm", "-rq", "--cached", ".")
    shutil.rmtree(repo / "memory", ignore_errors=True)
    for rel, text in files.items():
        (repo / rel).parent.mkdir(parents=True, exist_ok=True)
        (repo / rel).write_text(text, encoding="utf-8")
    _commit(repo, "side root")
    _git(repo, "checkout", "-q", "-f", "main")


# ---------- merges are judged against their first parent ----------


def test_merge_that_drops_a_member_is_a_deletion_blocker(store):
    repo, memory = store
    _orphan_side(repo, {"unrelated.txt": "x\n"})
    _git(repo, "merge", "-q", "--no-commit", "--allow-unrelated-histories", "side")
    _git(repo, "rm", "-q", "memory/b.md")
    _git(repo, "commit", "-q", "-m", "Merge branch side")

    blockers = _severity(tier2_findings(memory), "BLOCKER")
    assert any("group member deletion" in m and "b.md" in m for m in blockers)


def test_merge_that_takes_the_side_topic_is_a_mutation_blocker(store):
    repo, memory = store
    _orphan_side(repo, {"memory/a.md": _member("mem-a", topic="other")})
    proc = subprocess.run(
        ["git", "-C", str(repo), "merge", "-q", "--no-commit",
         "--allow-unrelated-histories", "side"],
        capture_output=True, text=True, check=False,
    )
    assert proc.returncode != 0  # add/add conflict on a.md
    _git(repo, "checkout", "--theirs", "memory/a.md")
    _git(repo, "add", "memory/a.md")
    _git(repo, "commit", "-q", "-m", "Merge branch side")

    blockers = _severity(tier2_findings(memory), "BLOCKER")
    assert any("decision_topic mutation" in m and "a.md" in m for m in blockers)


def test_merge_that_reverts_the_config_is_a_config_blocker(store):
    repo, memory = store
    _git(repo, "branch", "side")
    (memory / ".memforge").mkdir()
    (memory / ".memforge" / "config.yaml").write_text("audit: {}\n", encoding="utf-8")
    _commit(repo, "memforge: config")
    _git(repo, "checkout", "-q", "side")
    (repo / "notes.txt").write_text("x\n", encoding="utf-8")
    _commit(repo, "notes")
    _git(repo, "checkout", "-q", "main")
    _git(repo, "merge", "-q", "--no-commit", "--no-ff", "side")
    _git(repo, "rm", "-q", "memory/.memforge/config.yaml")
    _git(repo, "commit", "-q", "-m", "Merge branch side")

    blockers = _severity(tier2_findings(memory), "BLOCKER")
    assert any("Merge branch side" in m and "config edit" in m for m in blockers)


def test_merge_of_a_clean_resolve_branch_audits_clean(store):
    repo, memory = store
    _git(repo, "branch", "side")
    # On main: an ordinary body edit of the loser, so the merge combines both sides.
    (memory / "a.md").write_text(
        _member("mem-a", topic="db-choice", body="Body.\n\nMore context."), encoding="utf-8"
    )
    (repo / "main.txt").write_text("main\n", encoding="utf-8")
    _commit(repo, "main work")
    _git(repo, "checkout", "-q", "side")
    snoozes = memory / ".memforge" / "snoozes"
    snoozes.mkdir(parents=True)
    (snoozes / "db-choice.yaml").write_text("decision_topic: db-choice\n", encoding="utf-8")
    _commit(repo, "memforge: snooze db-choice")
    (memory / ".memforge" / "config.yaml").write_text("audit: {}\n", encoding="utf-8")
    _commit(repo, "memforge: config")
    assert resolve.main(["db-choice", "--memory-root", str(memory), "--winner-uid", "mem-b"]) == 0
    _git(repo, "checkout", "-q", "main")
    _git(repo, "merge", "-q", "--no-ff", "--no-edit", "side")
    assert "More context." in (memory / "a.md").read_text(encoding="utf-8")
    assert "status: superseded" in (memory / "a.md").read_text(encoding="utf-8")

    findings = tier2_findings(memory)
    assert _severity(findings, "BLOCKER") == []
    assert _severity(findings, "MAJOR") == []


def test_merge_of_an_unrelated_branch_with_a_new_member_audits_clean(store):
    repo, memory = store
    _git(repo, "branch", "side")
    (repo / "main.txt").write_text("main\n", encoding="utf-8")
    _commit(repo, "main work")
    _git(repo, "checkout", "-q", "side")
    (memory / "d.md").write_text(_member("mem-d", topic="cache"), encoding="utf-8")
    _commit(repo, "add d")
    _git(repo, "checkout", "-q", "main")
    _git(repo, "merge", "-q", "--no-ff", "--no-edit", "side")

    findings = tier2_findings(memory)
    assert _severity(findings, "BLOCKER") == []
    assert _severity(findings, "MAJOR") == []


# ---------- every committed path the config loader can read ----------


@needs_links
def test_linked_memforge_folder_is_a_config_edit(store):
    repo, memory = store
    (repo / "cfg").mkdir()
    (repo / "cfg" / "config.yaml").write_text(
        "audit:\n  enforce_sensitivity_export_gate: false\n", encoding="utf-8"
    )
    os.symlink("../cfg", memory / ".memforge")
    _commit(repo, "add cfg")

    blockers = _severity(tier2_findings(memory), "BLOCKER")
    assert any("config edit" in m and "memory/.memforge " in m for m in blockers)
    assert any("config edit" in m and "cfg/config.yaml" in m for m in blockers)


@needs_links
def test_edit_to_the_file_a_committed_config_link_names_is_a_config_edit(store):
    repo, memory = store
    (repo / "notes").mkdir()
    (repo / "notes" / "settings.yaml").write_text("audit: {}\n", encoding="utf-8")
    _commit(repo, "add notes")
    (memory / ".memforge").mkdir()
    os.symlink("../../notes/settings.yaml", memory / ".memforge" / "config.yaml")
    _commit(repo, "memforge: config")
    (repo / "notes" / "settings.yaml").write_text(
        "audit:\n  enforce_sensitivity_export_gate: false\n", encoding="utf-8"
    )
    _commit(repo, "update notes")

    blockers = _severity(tier2_findings(memory), "BLOCKER")
    assert any("'update notes'" in m and "config edit" in m and "notes/settings.yaml" in m
               for m in blockers)
    assert not any("'add notes'" in m or "'memforge: config'" in m for m in blockers)


@needs_links
def test_config_only_commit_through_a_committed_link_passes(store):
    repo, memory = store
    (repo / "cfg").mkdir()
    (repo / "cfg" / "config.yaml").write_text("audit: {}\n", encoding="utf-8")
    _commit(repo, "add cfg")
    os.symlink("../cfg", memory / ".memforge")
    _commit(repo, "memforge: config")
    (repo / "cfg" / "config.yaml").write_text("audit:\n  audit_window_days: 45\n", encoding="utf-8")
    _commit(repo, "memforge: config")

    blockers = _severity(tier2_findings(memory), "BLOCKER")
    # The linked `.memforge` is also where the snooze folder is opened, which
    # memforge-resolve refuses; the config gate itself passes both commits.
    assert not any("'memforge: config'" in m and "snooze folder link" not in m
                   for m in blockers)


@pytest.mark.parametrize("rel", [".memforge/Config.yaml", ".MemForge/config.yaml"])
def test_case_variant_config_path_is_a_config_edit(store, rel):
    repo, memory = store
    _git(repo, "config", "core.ignorecase", "false")
    target = memory / rel
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text("audit:\n  enforce_sensitivity_export_gate: false\n", encoding="utf-8")
    _git(repo, "add", "--", f"memory/{rel}")
    _git(repo, "commit", "-q", "-m", "tidy")

    blockers = _severity(tier2_findings(memory), "BLOCKER")
    assert any("config edit" in m and rel in m for m in blockers)


def test_case_variant_snooze_path_is_a_snooze_edit(store):
    repo, memory = store
    _git(repo, "config", "core.ignorecase", "false")
    target = memory / ".memforge" / "Snoozes" / "db-choice.yaml"
    target.parent.mkdir(parents=True)
    target.write_text("decision_topic: db-choice\n", encoding="utf-8")
    _git(repo, "add", "--", "memory/.memforge/Snoozes/db-choice.yaml")
    _git(repo, "commit", "-q", "-m", "tidy")

    majors = _severity(tier2_findings(memory), "MAJOR")
    assert any("snooze edit" in m and "Snoozes/db-choice.yaml" in m for m in majors)


# ---------- a live member moved into archive/ leaves its group ----------


def test_live_member_moved_to_archive_is_a_deletion_blocker(store):
    repo, memory = store
    (memory / "archive").mkdir()
    _git(repo, "mv", "memory/b.md", "memory/archive/b.md")
    _commit(repo, "tidy")

    blockers = _severity(tier2_findings(memory), "BLOCKER")
    assert any("group member deletion" in m and "b.md" in m for m in blockers)


def test_live_member_archived_in_its_resolve_commit_passes(store):
    repo, memory = store
    (memory / "archive").mkdir()
    _git(repo, "mv", "memory/b.md", "memory/archive/b.md")
    _commit(repo, "memforge: resolve db-choice")

    assert not any("group member deletion" in m
                   for m in _severity(tier2_findings(memory), "BLOCKER"))


def test_superseded_member_moved_to_archive_is_not_a_deletion(store):
    repo, memory = store
    assert resolve.main(["db-choice", "--memory-root", str(memory), "--winner-uid", "mem-b"]) == 0
    (memory / "archive").mkdir()
    _git(repo, "mv", "memory/a.md", "memory/archive/a.md")
    _commit(repo, "archive the loser")

    assert not any("group member deletion" in m
                   for m in _severity(tier2_findings(memory), "BLOCKER"))


# ---------- the git program the audit runs ----------


def _planted_git(folder: Path, marker: Path) -> None:
    script = folder / "git"
    script.write_text(f"#!/bin/sh\necho ran > '{marker}'\nexit 1\n", encoding="utf-8")
    script.chmod(script.stat().st_mode | stat.S_IXUSR)


needs_sh = pytest.mark.skipif(not Path("/bin/sh").exists(), reason="needs /bin/sh")


@needs_sh
def test_a_dot_path_entry_never_runs_a_git_in_the_store(store, tmp_path, monkeypatch):
    repo, memory = store
    marker = tmp_path / "ran"
    _planted_git(memory, marker)
    monkeypatch.chdir(memory)
    monkeypatch.setenv("PATH", f".{os.pathsep}{os.environ['PATH']}")

    tier2_findings(memory)
    _load_waivers(memory)
    assert not marker.exists()


@needs_sh
def test_a_git_inside_the_store_is_refused_with_a_warn(store, tmp_path, monkeypatch):
    repo, memory = store
    marker = tmp_path / "ran"
    folder = memory / "bin"
    folder.mkdir()
    _planted_git(folder, marker)
    monkeypatch.setenv("PATH", f"{folder}{os.pathsep}{os.environ['PATH']}")

    findings = tier2_findings(memory)
    assert _load_waivers(memory).commits == frozenset()
    assert not marker.exists()
    warns = _severity(findings, "WARN")
    assert any("inside the audited store" in m and "NOT evaluated" in m for m in warns)


def test_git_not_found_is_a_warn_that_tier2_was_not_evaluated(store, tmp_path, monkeypatch):
    repo, memory = store
    empty = tmp_path / "empty-bin"
    empty.mkdir()
    monkeypatch.setenv("PATH", str(empty))

    findings = tier2_findings(memory)
    assert [s for s, _ in findings] == ["WARN"]
    assert "NOT evaluated" in findings[0][1]
