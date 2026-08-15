"""Tests for memory-link-upgrade: typed-relations promotion from [[name]]
wikilinks (spec 0.9.0, pending release).

Covers the safety properties the tool holds itself to: dry-run default, [[name]]
left untouched, idempotent per (target, predicate), predicate never
inferred, fail-closed taxonomy validation on --write, the uncommitted-
changes skip guard (tri-state: no-repo / clean / dirty / unknown), the
ambiguous-target refusal, the blanket-application --all-matches gate, the
signed-memory refusal, the malformed-`relations`-is-not-a-list refusal,
prose-only wikilink extraction, and the sensitivity-crossing gate.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from memforge.cli import link_upgrade


def _write_file(folder: Path, filename: str, *, uid: str, wikilink_body: str,
                 extra_fm: str = "") -> Path:
    p = folder / filename
    p.write_text(
        "---\n"
        f"name: {filename[:-3]}\n"
        f"description: fixture entry {filename}\n"
        "type: project\n"
        f"uid: {uid}\n"
        "tier: index\n"
        "tags: [topic:tooling]\n"
        "owner: operator\n"
        "created: 2026-01-01\n"
        "status: active\n"
        f"{extra_fm}"
        "---\n\n"
        f"# {filename}\n\n{wikilink_body}\n",
        encoding="utf-8",
    )
    return p


def _init_git(folder: Path) -> None:
    subprocess.run(["git", "init", "-q"], cwd=folder, check=True)
    subprocess.run(["git", "add", "-A"], cwd=folder, check=True)
    subprocess.run(
        ["git", "-c", "user.email=t@t.com", "-c", "user.name=t", "commit", "-q", "-m", "init"],
        cwd=folder, check=True,
    )


def _commit_all(folder: Path, msg: str = "checkpoint") -> None:
    subprocess.run(["git", "add", "-A"], cwd=folder, check=True)
    subprocess.run(
        ["git", "-c", "user.email=t@t.com", "-c", "user.name=t", "commit", "-q", "-m", msg],
        cwd=folder, check=True,
    )


def _two_file_pair(tmp_path: Path) -> Path:
    folder = tmp_path / "memory"
    folder.mkdir()
    _write_file(folder, "project-a.md", uid="mem-2026-01-01-project-a",
                wikilink_body="See [[project-b]] and [[nonexistent-thing]].")
    _write_file(folder, "project-b.md", uid="mem-2026-01-02-project-b",
                wikilink_body="Follow-on to [[project-a]].")
    _init_git(folder)
    return folder


def _promote(idx, **kw):
    defaults = dict(
        predicate="applies-to", vocab=set(link_upgrade.STARTER_PREDICATES),
        source=None, confidence=None, only_tokens=None, only_files=None,
        write=False, all_matches=False, allow_sensitivity_crossing=False,
    )
    defaults.update(kw)
    return link_upgrade.cmd_promote(idx, **defaults)


# ----- scan (read-only) -----

def test_scan_classifies_promotable_already_typed_and_unresolved(tmp_path):
    folder = _two_file_pair(tmp_path)
    idx = link_upgrade.index_folder(folder)
    candidates = link_upgrade.scan_folder(idx)

    by_token = {(c.memory_relpath, c.token): c for c in candidates}
    assert by_token[("project-a.md", "project-b")].target_uid == "mem-2026-01-02-project-b"
    assert by_token[("project-a.md", "project-b")].already_typed is False
    assert by_token[("project-a.md", "nonexistent-thing")].target_uid is None
    assert by_token[("project-b.md", "project-a")].target_uid == "mem-2026-01-01-project-a"


def test_scan_never_writes(tmp_path):
    folder = _two_file_pair(tmp_path)
    before = (folder / "project-a.md").read_text()
    idx = link_upgrade.index_folder(folder)
    link_upgrade.scan_folder(idx)
    after = (folder / "project-a.md").read_text()
    assert before == after


def test_scan_excludes_wikilinks_in_fenced_code_inline_code_and_html_comments(tmp_path):
    folder = tmp_path / "memory"
    folder.mkdir()
    _write_file(folder, "project-a.md", uid="mem-2026-01-01-project-a", wikilink_body=(
        "Real: [[project-b]].\n\n"
        "```\nExample only: [[project-b]]\n```\n\n"
        "Inline example: `[[project-b]]` should not count either.\n\n"
        "<!-- commented out: [[project-b]] -->\n"
    ))
    _write_file(folder, "project-b.md", uid="mem-2026-01-02-project-b", wikilink_body="n/a")
    idx = link_upgrade.index_folder(folder)
    candidates = link_upgrade.scan_folder(idx)
    promotable = [c for c in candidates if c.memory_relpath == "project-a.md" and c.target_uid]
    assert len(promotable) == 1, "only the real prose wikilink should be counted, not the fenced/inline/comment copies"


def test_scan_excludes_double_backtick_code_spans(tmp_path):
    """CommonMark code spans can use a run of N backticks as delimiter, not
    only single backticks -- ``[[project-b]]`` must be excluded too."""
    folder = tmp_path / "memory"
    folder.mkdir()
    _write_file(folder, "project-a.md", uid="mem-2026-01-01-project-a", wikilink_body=(
        "Real: [[project-b]].\n\n"
        "Double-backtick example: ``[[project-b]]`` should not count.\n"
    ))
    _write_file(folder, "project-b.md", uid="mem-2026-01-02-project-b", wikilink_body="n/a")
    idx = link_upgrade.index_folder(folder)
    candidates = link_upgrade.scan_folder(idx)
    promotable = [c for c in candidates if c.memory_relpath == "project-a.md" and c.target_uid]
    assert len(promotable) == 1


def test_scan_flags_sensitivity_crossing(tmp_path):
    folder = tmp_path / "memory"
    folder.mkdir()
    _write_file(folder, "public-a.md", uid="mem-2026-01-01-public-a",
                wikilink_body="See [[private-b]].",
                extra_fm="sensitivity: public\n")
    _write_file(folder, "private-b.md", uid="mem-2026-01-02-private-b",
                wikilink_body="n/a", extra_fm="sensitivity: privileged\n")
    idx = link_upgrade.index_folder(folder)
    candidates = link_upgrade.scan_folder(idx)
    cand = next(c for c in candidates if c.memory_relpath == "public-a.md")
    assert cand.sensitivity_crossing is True


# ----- ambiguous resolution -----

def test_ambiguous_target_is_refused_not_silently_resolved(tmp_path):
    folder = tmp_path / "memory"
    sub1 = folder / "sub1"
    sub2 = folder / "sub2"
    sub1.mkdir(parents=True)
    sub2.mkdir(parents=True)
    _write_file(folder, "project-a.md", uid="mem-2026-01-01-project-a",
                wikilink_body="See [[dup]].")
    _write_file(sub1, "dup.md", uid="mem-2026-01-02-dup-one", wikilink_body="n/a")
    _write_file(sub2, "dup.md", uid="mem-2026-01-03-dup-two", wikilink_body="n/a")
    idx = link_upgrade.index_folder(folder)
    candidates = link_upgrade.scan_folder(idx)
    cand = next(c for c in candidates if c.token == "dup")
    assert cand.ambiguous is True
    assert cand.target_uid is None
    assert len(cand.ambiguous_paths) == 2


def test_promote_never_writes_an_ambiguous_edge(tmp_path):
    folder = tmp_path / "memory"
    sub1 = folder / "sub1"
    sub2 = folder / "sub2"
    sub1.mkdir(parents=True)
    sub2.mkdir(parents=True)
    _write_file(folder, "project-a.md", uid="mem-2026-01-01-project-a",
                wikilink_body="See [[dup]].")
    _write_file(sub1, "dup.md", uid="mem-2026-01-02-dup-one", wikilink_body="n/a")
    _write_file(sub2, "dup.md", uid="mem-2026-01-03-dup-two", wikilink_body="n/a")
    _init_git(folder)
    idx = link_upgrade.index_folder(folder)
    _promote(idx, write=True, all_matches=True)
    from memforge.frontmatter import parse
    fm, _ = parse((folder / "project-a.md").read_text())
    assert not fm.get("relations")


# ----- promote: dry-run default -----

def test_promote_defaults_to_dry_run(tmp_path):
    folder = _two_file_pair(tmp_path)
    before = (folder / "project-a.md").read_text()
    idx = link_upgrade.index_folder(folder)
    rc = _promote(idx, write=False)
    assert rc == 0
    after = (folder / "project-a.md").read_text()
    assert before == after, "dry-run (write=False) must not modify any file"


def test_dry_run_does_not_require_all_matches(tmp_path):
    """The blanket-application guard only applies to --write."""
    folder = _two_file_pair(tmp_path)
    idx = link_upgrade.index_folder(folder)
    rc = _promote(idx, write=False, all_matches=False)
    assert rc == 0


# ----- promote: --write requires explicit scope acknowledgement -----

def test_write_without_scope_or_all_matches_is_refused(tmp_path):
    folder = _two_file_pair(tmp_path)
    idx = link_upgrade.index_folder(folder)
    rc = _promote(idx, write=True, all_matches=False, only_tokens=None, only_files=None)
    assert rc == 2
    from memforge.frontmatter import parse
    fm, _ = parse((folder / "project-a.md").read_text())
    assert not fm.get("relations")


def test_write_with_only_token_does_not_need_all_matches(tmp_path):
    folder = _two_file_pair(tmp_path)
    idx = link_upgrade.index_folder(folder)
    rc = _promote(idx, write=True, only_tokens={"project-b"})
    assert rc == 0
    from memforge.frontmatter import parse
    fm, _ = parse((folder / "project-a.md").read_text())
    assert fm.get("relations")


def test_write_with_all_matches_proceeds_unscoped(tmp_path):
    folder = _two_file_pair(tmp_path)
    idx = link_upgrade.index_folder(folder)
    rc = _promote(idx, write=True, all_matches=True)
    assert rc == 0
    from memforge.frontmatter import parse
    fm, _ = parse((folder / "project-a.md").read_text())
    assert fm.get("relations")


# ----- promote: --write persists, additive, non-destructive to [[name]] -----

def test_promote_write_adds_relations_without_touching_wikilink(tmp_path):
    folder = _two_file_pair(tmp_path)
    idx = link_upgrade.index_folder(folder)
    rc = _promote(idx, source="test", confidence=0.5, write=True, all_matches=True)
    assert rc == 0

    text = (folder / "project-a.md").read_text()
    assert "[[project-b]]" in text, "the original wikilink must survive the promotion untouched"
    assert "[[nonexistent-thing]]" in text

    from memforge.frontmatter import parse
    fm, _ = parse(text)
    rels = fm.get("relations")
    assert rels == [{
        "predicate": "applies-to",
        "target": "mem:mem-2026-01-02-project-b",
        "created": rels[0]["created"],
        "source": "test",
        "confidence": 0.5,
    }]


def test_promote_write_is_idempotent(tmp_path):
    """Re-running promote with the SAME predicate must not duplicate the
    edge. Commits between passes so this exercises the (target, predicate)
    dedup logic itself, not the (separately-tested) dirty-file skip guard."""
    folder = _two_file_pair(tmp_path)

    idx1 = link_upgrade.index_folder(folder)
    _promote(idx1, write=True, all_matches=True)
    after_first = (folder / "project-a.md").read_text()
    _commit_all(folder, "first promotion")

    idx2 = link_upgrade.index_folder(folder)
    _promote(idx2, write=True, all_matches=True)
    after_second = (folder / "project-a.md").read_text()

    assert after_first == after_second, "re-running promote must not duplicate the edge"


def test_promote_different_predicate_adds_second_edge_not_a_duplicate(tmp_path):
    folder = _two_file_pair(tmp_path)

    idx1 = link_upgrade.index_folder(folder)
    _promote(idx1, predicate="applies-to", write=True, all_matches=True)
    _commit_all(folder, "first promotion")

    idx2 = link_upgrade.index_folder(folder)
    _promote(idx2, predicate="refines", write=True, all_matches=True)

    from memforge.frontmatter import parse
    fm, _ = parse((folder / "project-a.md").read_text())
    predicates = {r["predicate"] for r in fm["relations"]}
    assert predicates == {"applies-to", "refines"}


# ----- malformed pre-existing relations field -----

def test_promote_refuses_to_coerce_a_non_list_relations_field(tmp_path):
    folder = tmp_path / "memory"
    folder.mkdir()
    _write_file(folder, "project-a.md", uid="mem-2026-01-01-project-a",
                wikilink_body="See [[project-b]].",
                extra_fm="relations: {predicate: applies-to}\n")
    _write_file(folder, "project-b.md", uid="mem-2026-01-02-project-b", wikilink_body="n/a")
    _init_git(folder)
    idx = link_upgrade.index_folder(folder)
    _promote(idx, write=True, all_matches=True)

    from memforge.frontmatter import parse
    fm, _ = parse((folder / "project-a.md").read_text())
    # must remain exactly the original malformed mapping, not be coerced into
    # a list of its own keys (e.g. ["predicate"]) and not be extended.
    assert fm["relations"] == {"predicate": "applies-to"}


# ----- malformed frontmatter: refuse rather than corrupt -----

def test_promote_refuses_to_write_a_file_with_unparseable_frontmatter(tmp_path):
    """parse() gracefully degrades unparseable YAML frontmatter to
    ({}, ORIGINAL_TEXT) for read-only callers. This tool WRITES, so it must
    refuse instead of prepending a second frontmatter fence in front of the
    file's already-broken one."""
    folder = tmp_path / "memory"
    folder.mkdir()
    broken = folder / "project-a.md"
    broken.write_text(
        "---\n"
        "name: project-a\n"
        "description: bad value: has an unquoted colon: right here\n"
        "type: project\n"
        "uid: mem-2026-01-01-project-a\n"
        "---\n\n"
        "See [[project-b]].\n",
        encoding="utf-8",
    )
    _write_file(folder, "project-b.md", uid="mem-2026-01-02-project-b", wikilink_body="n/a")
    _init_git(folder)
    before = broken.read_text()

    idx = link_upgrade.index_folder(folder)
    rc = _promote(idx, write=True, only_files={"project-a.md", "project-b.md"})

    after = broken.read_text()
    assert before == after, "an unparseable file must be left byte-for-byte untouched"
    assert rc == 1, "a refused file must be reflected as a failure, not silent success"


# ----- complete-list write-time schema validation -----

def test_promote_refuses_whole_file_when_a_pre_existing_relations_entry_is_incomplete(tmp_path):
    """A pre-existing entry missing `created` makes the COMPLETE resulting
    list fail the write-time schema, so the whole write is refused -- a
    new, otherwise-valid edge is not appended on top of already-broken
    content."""
    folder = tmp_path / "memory"
    folder.mkdir()
    _write_file(folder, "project-a.md", uid="mem-2026-01-01-project-a",
                wikilink_body="See [[project-c]].",
                extra_fm=("relations:\n"
                          "  - predicate: applies-to\n"
                          "    target: mem:mem-2026-01-02-project-b\n"))
    _write_file(folder, "project-b.md", uid="mem-2026-01-02-project-b", wikilink_body="n/a")
    _write_file(folder, "project-c.md", uid="mem-2026-01-03-project-c", wikilink_body="n/a")
    _init_git(folder)

    idx = link_upgrade.index_folder(folder)
    rc = _promote(idx, write=True, all_matches=True)

    from memforge.frontmatter import parse
    fm, _ = parse((folder / "project-a.md").read_text())
    assert len(fm["relations"]) == 1, "the new edge must NOT be appended alongside broken pre-existing content"
    assert rc == 0  # nothing FAILED outright; it was cleanly skipped and reported


def test_promote_refuses_whole_file_when_a_pre_existing_target_does_not_resolve(tmp_path):
    folder = tmp_path / "memory"
    folder.mkdir()
    _write_file(folder, "project-a.md", uid="mem-2026-01-01-project-a",
                wikilink_body="See [[project-b]].",
                extra_fm=("relations:\n"
                          "  - predicate: applies-to\n"
                          "    target: mem:mem-2026-01-09-does-not-exist\n"
                          "    created: '2026-01-01'\n"))
    _write_file(folder, "project-b.md", uid="mem-2026-01-02-project-b", wikilink_body="n/a")
    _init_git(folder)

    idx = link_upgrade.index_folder(folder)
    _promote(idx, write=True, all_matches=True)

    from memforge.frontmatter import parse
    fm, _ = parse((folder / "project-a.md").read_text())
    assert len(fm["relations"]) == 1, "the unresolved pre-existing entry must block the write; nothing new is appended"


def test_one_files_unexpected_failure_does_not_abort_the_run(tmp_path, monkeypatch):
    """The outer per-file exception boundary: an unexpected exception while
    processing ONE file must not prevent the other files in the same run
    from being processed and written."""
    folder = tmp_path / "memory"
    folder.mkdir()
    _write_file(folder, "project-a.md", uid="mem-2026-01-01-project-a",
                wikilink_body="See [[project-c]].")
    _write_file(folder, "project-b.md", uid="mem-2026-01-02-project-b",
                wikilink_body="See [[project-c]].")
    _write_file(folder, "project-c.md", uid="mem-2026-01-03-project-c",
                wikilink_body="n/a")
    _init_git(folder)

    real_render = link_upgrade._mf_render
    calls = {"n": 0}

    def _boom_on_first_call(fm, body):
        calls["n"] += 1
        if calls["n"] == 1:
            raise RuntimeError("simulated unexpected failure")
        return real_render(fm, body)

    monkeypatch.setattr(link_upgrade, "_mf_render", _boom_on_first_call)

    idx = link_upgrade.index_folder(folder)
    rc = _promote(idx, write=True, all_matches=True)

    assert rc == 1, "the run must report a failure, not silently succeed"
    from memforge.frontmatter import parse
    fm_a, _ = parse((folder / "project-a.md").read_text())
    fm_b, _ = parse((folder / "project-b.md").read_text())
    # exactly one of the two files hit the simulated failure; the OTHER
    # must still have been processed and written despite the first file's
    # unexpected exception.
    assert bool(fm_a.get("relations")) != bool(fm_b.get("relations"))


# ----- signed-memory refusal -----

def test_promote_refuses_to_write_a_signed_memory(tmp_path):
    folder = tmp_path / "memory"
    folder.mkdir()
    _write_file(folder, "project-a.md", uid="mem-2026-01-01-project-a",
                wikilink_body="See [[project-b]].",
                extra_fm="signature:\n  algo: gpg-ed25519\n  signing_time: '2026-01-01T00:00:00Z'\n  value: deadbeef\n")
    _write_file(folder, "project-b.md", uid="mem-2026-01-02-project-b", wikilink_body="n/a")
    _init_git(folder)
    idx = link_upgrade.index_folder(folder)
    _promote(idx, write=True, all_matches=True)

    from memforge.frontmatter import parse
    fm, _ = parse((folder / "project-a.md").read_text())
    assert not fm.get("relations"), "a signed memory must be skipped, not mutated with a now-stale signature"
    assert fm.get("signature", {}).get("value") == "deadbeef", "the original signature must be untouched"


# ----- sensitivity-crossing gate -----

def test_write_skips_crossing_edge_without_acknowledgement(tmp_path):
    folder = tmp_path / "memory"
    folder.mkdir()
    _write_file(folder, "public-a.md", uid="mem-2026-01-01-public-a",
                wikilink_body="See [[private-b]].", extra_fm="sensitivity: public\n")
    _write_file(folder, "private-b.md", uid="mem-2026-01-02-private-b",
                wikilink_body="n/a", extra_fm="sensitivity: privileged\n")
    _init_git(folder)
    idx = link_upgrade.index_folder(folder)
    _promote(idx, write=True, all_matches=True, allow_sensitivity_crossing=False)

    from memforge.frontmatter import parse
    fm, _ = parse((folder / "public-a.md").read_text())
    assert not fm.get("relations")


def test_write_persists_crossing_edge_with_explicit_acknowledgement(tmp_path):
    folder = tmp_path / "memory"
    folder.mkdir()
    _write_file(folder, "public-a.md", uid="mem-2026-01-01-public-a",
                wikilink_body="See [[private-b]].", extra_fm="sensitivity: public\n")
    _write_file(folder, "private-b.md", uid="mem-2026-01-02-private-b",
                wikilink_body="n/a", extra_fm="sensitivity: privileged\n")
    _init_git(folder)
    idx = link_upgrade.index_folder(folder)
    _promote(idx, write=True, all_matches=True, allow_sensitivity_crossing=True)

    from memforge.frontmatter import parse
    fm, _ = parse((folder / "public-a.md").read_text())
    assert fm.get("relations")


# ----- CLI-level: taxonomy fail-closed on --write, no override exists -----

def test_cli_write_fails_closed_when_taxonomy_unloadable(monkeypatch, tmp_path, capsys):
    folder = _two_file_pair(tmp_path)
    monkeypatch.setattr("sys.argv", [
        "memory-link-upgrade", "--path", str(folder),
        "--memforge-root", str(tmp_path / "nonexistent-root"),
        "promote", "--predicate", "refines", "--write", "--all-matches",
    ])
    rc = link_upgrade.main()
    assert rc == 1
    err = capsys.readouterr().err
    assert "taxonomy.yaml" in err
    from memforge.frontmatter import parse
    fm, _ = parse((folder / "project-a.md").read_text())
    assert not fm.get("relations")


def test_cli_has_no_allow_missing_taxonomy_flag(monkeypatch, tmp_path, capsys):
    """Round-2 fix: the earlier --allow-missing-taxonomy escape hatch for
    --write was removed. An explicit unsafe flag does not make an
    unvalidated write conformant with the spec's write-boundary MUST."""
    folder = _two_file_pair(tmp_path)
    monkeypatch.setattr("sys.argv", [
        "memory-link-upgrade", "--path", str(folder), "promote",
        "--predicate", "refines", "--allow-missing-taxonomy",
    ])
    with pytest.raises(SystemExit):
        link_upgrade.main()
    err = capsys.readouterr().err
    assert "unrecognized arguments" in err or "--allow-missing-taxonomy" in err


def test_cli_dry_run_proceeds_even_when_taxonomy_unloadable(monkeypatch, tmp_path):
    folder = _two_file_pair(tmp_path)
    monkeypatch.setattr("sys.argv", [
        "memory-link-upgrade", "--path", str(folder),
        "--memforge-root", str(tmp_path / "nonexistent-root"),
        "promote", "--predicate", "refines",
    ])
    rc = link_upgrade.main()
    assert rc == 0
    text = (folder / "project-a.md").read_text()
    from memforge.frontmatter import parse
    fm, _ = parse(text)
    assert not fm.get("relations"), "no --write flag was passed; must remain a dry-run"


def test_cli_rejects_out_of_vocabulary_predicate(monkeypatch, tmp_path, capsys):
    folder = _two_file_pair(tmp_path)
    monkeypatch.setattr("sys.argv", [
        "memory-link-upgrade", "--path", str(folder), "promote", "--predicate", "made-up-predicate",
    ])
    rc = link_upgrade.main()
    assert rc == 2
    err = capsys.readouterr().err
    assert "made-up-predicate" in err


def test_cli_rejects_out_of_range_confidence(monkeypatch, tmp_path, capsys):
    folder = _two_file_pair(tmp_path)
    monkeypatch.setattr("sys.argv", [
        "memory-link-upgrade", "--path", str(folder), "promote",
        "--predicate", "refines", "--confidence", "1.5",
    ])
    rc = link_upgrade.main()
    assert rc == 2
    assert "0.0, 1.0" in capsys.readouterr().err


# ----- git-dirty guard: tri-state (no-repo / clean / dirty / unknown) -----

def test_write_skips_file_with_uncommitted_changes(tmp_path):
    folder = _two_file_pair(tmp_path)
    with open(folder / "project-a.md", "a", encoding="utf-8") as f:
        f.write("\nan uncommitted local edit\n")

    idx = link_upgrade.index_folder(folder)
    _promote(idx, write=True, all_matches=True)

    from memforge.frontmatter import parse
    fm, _ = parse((folder / "project-a.md").read_text())
    assert not fm.get("relations"), "dirty file must be skipped, not written to"
    fm_b, _ = parse((folder / "project-b.md").read_text())
    assert fm_b.get("relations")


def test_write_outside_git_repo_proceeds_without_dirty_guard(tmp_path):
    folder = tmp_path / "memory"
    folder.mkdir()
    _write_file(folder, "project-a.md", uid="mem-2026-01-01-project-a",
                wikilink_body="See [[project-b]].")
    _write_file(folder, "project-b.md", uid="mem-2026-01-02-project-b",
                wikilink_body="Nothing here.")

    idx = link_upgrade.index_folder(folder)
    rc = _promote(idx, write=True, all_matches=True)
    assert rc == 0
    from memforge.frontmatter import parse
    fm, _ = parse((folder / "project-a.md").read_text())
    assert fm.get("relations")


def test_git_dirty_status_unknown_when_status_check_fails(tmp_path, monkeypatch):
    """When a repo IS confirmed but `git status` itself cannot confirm
    cleanliness, the tri-state helper reports 'unknown' and the file must
    be skipped -- fail closed on ambiguity, not fail open."""
    folder = _two_file_pair(tmp_path)
    target = folder / "project-a.md"

    monkeypatch.setattr(link_upgrade, "_git_root_for", lambda p: (folder, "found"))

    def _boom(*a, **kw):
        raise OSError("simulated git failure")

    monkeypatch.setattr(link_upgrade.subprocess, "run", _boom)
    assert link_upgrade.git_dirty_status(target) == "unknown"


def test_git_dirty_status_unknown_when_repo_detection_is_indeterminate(tmp_path, monkeypatch):
    """When repo detection ITSELF cannot be confirmed (a transient git
    failure, not a confirmed 'not a repository'), the tri-state helper
    reports 'unknown', not 'no-repo' -- fail closed at the detection stage
    too, not only at the status-check stage."""
    folder = _two_file_pair(tmp_path)
    target = folder / "project-a.md"

    monkeypatch.setattr(link_upgrade, "_git_root_for", lambda p: (None, "indeterminate"))
    assert link_upgrade.git_dirty_status(target) == "unknown"


def test_git_dirty_status_is_no_git_when_git_binary_is_missing(tmp_path, monkeypatch):
    """A missing git binary is distinguished from both a confirmed
    non-repo and an indeterminate failure: it maps to 'no-git', which
    `cmd_promote` treats as proceed-with-a-warning, not skip."""
    folder = _two_file_pair(tmp_path)
    target = folder / "project-a.md"
    monkeypatch.setattr(link_upgrade, "_git_root_for", lambda p: (None, "no-git"))
    assert link_upgrade.git_dirty_status(target) == "no-git"


def test_write_warns_once_when_git_binary_is_missing(tmp_path, monkeypatch, capsys):
    folder = _two_file_pair(tmp_path)
    monkeypatch.setattr(link_upgrade, "git_dirty_status", lambda p: "no-git")

    idx = link_upgrade.index_folder(folder)
    rc = _promote(idx, write=True, all_matches=True)
    assert rc == 0

    from memforge.frontmatter import parse
    fm, _ = parse((folder / "project-a.md").read_text())
    assert fm.get("relations"), "no-git must proceed (with a warning), not skip"

    err = capsys.readouterr().err
    assert err.count("git is not installed") == 1, "the warning must print exactly once per run, not per file"


def test_git_root_for_distinguishes_confirmed_absence_from_indeterminate(tmp_path):
    """`git rev-parse` against a directory OUTSIDE any working tree returns
    a confirmed ('confirmed-absent') -- proceed, no guard -- not an
    indeterminate result."""
    outside = tmp_path / "not-a-repo"
    outside.mkdir()
    (outside / "f.md").write_text("x", encoding="utf-8")
    root, state = link_upgrade._git_root_for(outside / "f.md")
    assert root is None
    assert state == "confirmed-absent"


# ----- filters -----

def test_only_token_filter_restricts_promotion(tmp_path):
    folder = tmp_path / "memory"
    folder.mkdir()
    _write_file(folder, "project-a.md", uid="mem-2026-01-01-project-a",
                wikilink_body="See [[project-b]] and [[project-c]].")
    _write_file(folder, "project-b.md", uid="mem-2026-01-02-project-b", wikilink_body="n/a")
    _write_file(folder, "project-c.md", uid="mem-2026-01-03-project-c", wikilink_body="n/a")
    _init_git(folder)

    idx = link_upgrade.index_folder(folder)
    _promote(idx, write=True, only_tokens={"project-b"})

    from memforge.frontmatter import parse
    fm, _ = parse((folder / "project-a.md").read_text())
    targets = {r["target"] for r in fm["relations"]}
    assert targets == {"mem:mem-2026-01-02-project-b"}


# ----- atomic write: mode bits + symlink refusal -----

def test_atomic_write_preserves_mode_bits(tmp_path):
    folder = tmp_path / "memory"
    folder.mkdir()
    target = _write_file(folder, "project-a.md", uid="mem-2026-01-01-project-a",
                          wikilink_body="n/a")
    target.chmod(0o640)
    link_upgrade._atomic_write(target, target.read_text() + "\n")
    assert (target.stat().st_mode & 0o777) == 0o640


def test_atomic_write_refuses_symlink_target(tmp_path):
    folder = tmp_path / "memory"
    folder.mkdir()
    real = _write_file(folder, "real.md", uid="mem-2026-01-01-real", wikilink_body="n/a")
    link_path = folder / "link.md"
    link_path.symlink_to(real)
    with pytest.raises(OSError):
        link_upgrade._atomic_write(link_path, "should not be written")
    assert link_path.is_symlink(), "the symlink itself must survive a refused write"
