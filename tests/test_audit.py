"""Tests for memforge.cli.audit.

Regression coverage: rollup-subfolder README.md files must be treated as
pointer-comparable (per spec §"Rollup subfolders") so that legitimate
parent-MEMORY.md pointers like `[Forge state](forge/README.md)` do not
fire `Orphan pointer (no file)`.
"""

from __future__ import annotations

from pathlib import Path

from memforge.cli.audit import _disk_md_files, _files_to_audit


def _seed_top_level(folder: Path, name: str = "feedback_x.md") -> None:
    (folder / name).write_text(
        "---\n"
        "name: Top level\n"
        "description: Top-level seed\n"
        "type: feedback\n"
        "---\n\n"
        "Body.\n"
        "**Why:** test.\n"
        "**How to apply:** test.\n",
        encoding="utf-8",
    )


def _seed_rollup_readme(folder: Path, topic: str) -> None:
    sub = folder / topic
    sub.mkdir()
    (sub / "README.md").write_text(
        "---\n"
        f"name: {topic} rollup\n"
        f"description: Rollup README for {topic}\n"
        "type: reference\n"
        "tier: index\n"
        "---\n\n"
        f"Rollup body for {topic}.\n",
        encoding="utf-8",
    )


def test_disk_md_files_returns_top_level(tmp_path: Path) -> None:
    _seed_top_level(tmp_path, "feedback_a.md")
    _seed_top_level(tmp_path, "feedback_b.md")
    (tmp_path / "MEMORY.md").write_text("# index\n", encoding="utf-8")

    assert _disk_md_files(tmp_path) == ["feedback_a.md", "feedback_b.md"]


def test_disk_md_files_includes_rollup_readmes(tmp_path: Path) -> None:
    _seed_top_level(tmp_path, "feedback_a.md")
    _seed_rollup_readme(tmp_path, "forge")
    _seed_rollup_readme(tmp_path, "ildan")

    assert _disk_md_files(tmp_path) == [
        "feedback_a.md",
        "forge/README.md",
        "ildan/README.md",
    ]


def test_disk_md_files_excludes_archive(tmp_path: Path) -> None:
    _seed_top_level(tmp_path, "feedback_a.md")
    archive = tmp_path / "archive"
    archive.mkdir()
    (archive / "README.md").write_text(
        "---\nname: archived\ndescription: x\ntype: reference\n---\n",
        encoding="utf-8",
    )

    assert _disk_md_files(tmp_path) == ["feedback_a.md"]


def test_disk_md_files_skips_subfolders_without_readme(tmp_path: Path) -> None:
    _seed_top_level(tmp_path, "feedback_a.md")
    sub = tmp_path / "forge"
    sub.mkdir()
    (sub / "feedback_detail.md").write_text(
        "---\nname: detail\ndescription: x\ntype: feedback\ntier: detail\n---\n",
        encoding="utf-8",
    )

    assert _disk_md_files(tmp_path) == ["feedback_a.md"]


def test_files_to_audit_returns_top_level(tmp_path: Path) -> None:
    _seed_top_level(tmp_path, "feedback_a.md")
    _seed_top_level(tmp_path, "feedback_b.md")
    (tmp_path / "MEMORY.md").write_text("# index\n", encoding="utf-8")

    assert _files_to_audit(tmp_path) == ["feedback_a.md", "feedback_b.md"]


def test_files_to_audit_recurses_into_rollups(tmp_path: Path) -> None:
    _seed_top_level(tmp_path, "feedback_a.md")
    _seed_rollup_readme(tmp_path, "forge")
    sub = tmp_path / "forge"
    (sub / "feedback_detail_one.md").write_text(
        "---\nname: detail one\ndescription: x\ntype: feedback\ntier: detail\n---\n",
        encoding="utf-8",
    )
    (sub / "project_detail_two.md").write_text(
        "---\nname: detail two\ndescription: x\ntype: project\ntier: detail\n---\n",
        encoding="utf-8",
    )

    # Normalize path separators + sort: POSIX `os.walk` returns ASCII order
    # (uppercase 'R' < lowercase 'f', so README first); Windows NTFS returns
    # case-insensitive order (README after feedback). The audit logic
    # surfaces the right SET of files; ordering between consumers is the
    # consumer's responsibility.
    actual = sorted(p.replace("\\", "/") for p in _files_to_audit(tmp_path))
    expected = sorted([
        "feedback_a.md",
        "forge/README.md",
        "forge/feedback_detail_one.md",
        "forge/project_detail_two.md",
    ])
    assert actual == expected


def test_files_to_audit_excludes_archive_recursively(tmp_path: Path) -> None:
    _seed_top_level(tmp_path, "feedback_a.md")
    archive = tmp_path / "archive"
    archive.mkdir()
    (archive / "old_thing.md").write_text(
        "---\nname: old\ndescription: x\ntype: reference\n---\n",
        encoding="utf-8",
    )
    (archive / "README.md").write_text(
        "---\nname: archive index\ndescription: x\ntype: reference\n---\n",
        encoding="utf-8",
    )

    assert _files_to_audit(tmp_path) == ["feedback_a.md"]


def test_files_to_audit_catches_yaml_parse_failure_in_detail_file(tmp_path: Path) -> None:
    """End-to-end: a YAML parse failure in a rollup detail file must be
    reported as a violation. Pre-fix, audit silently skipped detail files."""
    from memforge.cli.audit import audit_target

    _seed_top_level(tmp_path, "feedback_a.md")
    _seed_rollup_readme(tmp_path, "forge")
    sub = tmp_path / "forge"
    # Deliberately broken YAML: colon-space inside an unquoted name
    (sub / "feedback_broken.md").write_text(
        "---\n"
        "name: FORGE: this colon-space breaks YAML parse\n"
        "description: x\n"
        "type: feedback\n"
        "---\n\n"
        "Body.\n",
        encoding="utf-8",
    )
    (tmp_path / "MEMORY.md").write_text(
        "- [feedback A](feedback_a.md)\n"
        "- [Forge](forge/README.md)\n",
        encoding="utf-8",
    )

    import io
    from contextlib import redirect_stdout

    buf = io.StringIO()
    with redirect_stdout(buf):
        audit_target(
            tmp_path,
            stale_days=365,
            fix=False,
            add_defaults=False,
            json_out=False,
        )

    output = buf.getvalue()
    assert "forge/feedback_broken.md: frontmatter YAML failed to parse" in output


def test_no_orphan_pointer_for_rollup_readme(tmp_path: Path) -> None:
    """End-to-end: a MEMORY.md pointer at a rollup README must not produce
    an `Orphan pointer (no file)` violation."""
    from memforge.cli.audit import audit_target

    _seed_top_level(tmp_path, "feedback_a.md")
    _seed_rollup_readme(tmp_path, "forge")
    (tmp_path / "MEMORY.md").write_text(
        "# Memory Index\n\n"
        "- [feedback A](feedback_a.md) - top-level entry\n"
        "- [Forge state](forge/README.md) - rollup\n",
        encoding="utf-8",
    )

    import io
    from contextlib import redirect_stdout

    buf = io.StringIO()
    with redirect_stdout(buf):
        violations, _ = audit_target(
            tmp_path,
            stale_days=365,
            fix=False,
            add_defaults=False,
            json_out=False,
        )

    output = buf.getvalue()
    assert "Orphan pointer (no file): forge/README.md" not in output
    assert "Orphan file (no pointer): forge/README.md" not in output


def _seed_subfolder_detail(folder: Path, topic: str, name: str) -> None:
    sub = folder / topic
    sub.mkdir(exist_ok=True)
    (sub / name).write_text(
        "---\n"
        f"name: {topic} {name} detail\n"
        f"description: Detail file for {topic}\n"
        "type: project\n"
        "tier: detail\n"
        "---\n\n"
        f"Detail body for {topic}/{name}.\n"
        "**Why:** test.\n"
        "**How to apply:** test.\n",
        encoding="utf-8",
    )


def test_no_orphan_pointer_for_subfolder_detail_file(tmp_path: Path) -> None:
    """End-to-end: a MEMORY.md pointer at a subfolder detail file that
    EXISTS on disk (e.g., `forge/project_x_deploy_state.md`) must not be
    reported as `Orphan pointer (no file)`. The audit may downgrade to a
    health advisory recommending the rollup-README pattern, but the file
    is not missing and must not appear in the integrity-violation set.
    """
    from memforge.cli.audit import audit_target

    _seed_top_level(tmp_path, "feedback_a.md")
    _seed_subfolder_detail(tmp_path, "forge", "project_x_deploy_state.md")
    (tmp_path / "MEMORY.md").write_text(
        "# Memory Index\n\n"
        "- [feedback A](feedback_a.md) - top-level entry\n"
        "- [project X deploy](forge/project_x_deploy_state.md) - detail\n",
        encoding="utf-8",
    )

    import io
    from contextlib import redirect_stdout

    buf = io.StringIO()
    with redirect_stdout(buf):
        violations, _ = audit_target(
            tmp_path,
            stale_days=365,
            fix=False,
            add_defaults=False,
            json_out=False,
        )

    output = buf.getvalue()
    assert "Orphan pointer (no file): forge/project_x_deploy_state.md" not in output
    assert violations == 0, (
        f"expected 0 integrity violations for a pointer at an existing "
        f"subfolder detail file; got {violations}; output:\n{output}"
    )


def test_subfolder_detail_pointer_emits_health_advisory(tmp_path: Path) -> None:
    """The downgraded check emits a health advisory pointing the operator
    at the canonical rollup-README pattern, without raising an integrity
    violation.
    """
    from memforge.cli.audit import audit_target

    _seed_top_level(tmp_path, "feedback_a.md")
    _seed_subfolder_detail(tmp_path, "forge", "project_x_deploy_state.md")
    (tmp_path / "MEMORY.md").write_text(
        "# Memory Index\n\n"
        "- [feedback A](feedback_a.md) - top-level entry\n"
        "- [project X deploy](forge/project_x_deploy_state.md) - detail\n",
        encoding="utf-8",
    )

    import io
    from contextlib import redirect_stdout

    buf = io.StringIO()
    with redirect_stdout(buf):
        audit_target(
            tmp_path,
            stale_days=365,
            fix=False,
            add_defaults=False,
            json_out=False,
        )

    output = buf.getvalue()
    assert "Pointer at subfolder detail file" in output, (
        f"expected health advisory recommending rollup README; output:\n{output}"
    )


def test_truly_missing_subfolder_pointer_still_violates(tmp_path: Path) -> None:
    """Sanity: a MEMORY.md pointer at `sub/missing.md` where the file does
    NOT exist on disk must still produce an integrity violation (the bug
    fix only downgrades when the file actually exists).
    """
    from memforge.cli.audit import audit_target

    _seed_top_level(tmp_path, "feedback_a.md")
    (tmp_path / "forge").mkdir()
    (tmp_path / "MEMORY.md").write_text(
        "# Memory Index\n\n"
        "- [feedback A](feedback_a.md) - top-level entry\n"
        "- [missing](forge/does_not_exist.md) - truly orphan\n",
        encoding="utf-8",
    )

    import io
    from contextlib import redirect_stdout

    buf = io.StringIO()
    with redirect_stdout(buf):
        violations, _ = audit_target(
            tmp_path,
            stale_days=365,
            fix=False,
            add_defaults=False,
            json_out=False,
        )

    output = buf.getvalue()
    assert "Orphan pointer (no file): forge/does_not_exist.md" in output
    assert violations >= 1


# ---- rollup-parent integrity (parentless subfolder) ----------------------


def test_parentless_subfolder_raises_integrity_violation(tmp_path: Path) -> None:
    """A subfolder with files and NO README.md rollup parent must raise an
    integrity violation naming the unreachable count.

    This is the regression the check exists for. `_disk_md_files()` adds
    `<topic>/README.md` to the pointer-comparable set only when that README
    exists, so a parentless subfolder previously contributed NOTHING to the
    comparison and its files were silently unreachable from MEMORY.md while
    the audit reported clean. Observed in the field at 266 files across 16
    subfolders, with a passing audit.
    """
    from memforge.cli.audit import audit_target

    _seed_top_level(tmp_path, "feedback_a.md")
    _seed_subfolder_detail(tmp_path, "lost", "detail_one.md")
    _seed_subfolder_detail(tmp_path, "lost", "detail_two.md")
    (tmp_path / "MEMORY.md").write_text(
        "# Memory Index\n\n- [feedback A](feedback_a.md) - top-level entry\n",
        encoding="utf-8",
    )

    import io
    from contextlib import redirect_stdout

    buf = io.StringIO()
    with redirect_stdout(buf):
        violations, _ = audit_target(
            tmp_path, stale_days=365, fix=False, add_defaults=False, json_out=False
        )
    output = buf.getvalue()
    assert "Rollup subfolder missing README.md: lost/" in output
    assert "2 file(s) unreachable" in output
    assert violations >= 1


def test_parentless_subfolder_ignores_directly_pointed_files(tmp_path: Path) -> None:
    """A detail file POINTED AT directly from MEMORY.md is reachable, so it
    must NOT count toward the unreachable tally.

    Non-canonical (the rollup README is the spec's pattern) and already
    covered by a health advisory -- but not lost. Counting it would raise an
    integrity violation for a memory nothing has lost, which is exactly the
    false positive `test_no_orphan_pointer_for_subfolder_detail_file` guards.
    """
    from memforge.cli.audit import audit_target

    _seed_top_level(tmp_path, "feedback_a.md")
    _seed_subfolder_detail(tmp_path, "pointed", "reachable.md")
    (tmp_path / "MEMORY.md").write_text(
        "# Memory Index\n\n"
        "- [feedback A](feedback_a.md) - top-level entry\n"
        "- [reachable](pointed/reachable.md) - pointed at directly\n",
        encoding="utf-8",
    )

    import io
    from contextlib import redirect_stdout

    buf = io.StringIO()
    with redirect_stdout(buf):
        audit_target(
            tmp_path, stale_days=365, fix=False, add_defaults=False, json_out=False
        )
    output = buf.getvalue()
    assert "Rollup subfolder missing README.md: pointed/" not in output


def test_parentless_check_counts_untiered_files_too(tmp_path: Path) -> None:
    """A file omitting `tier` defaults to index per spec, and an index-tier
    file stranded in a parentless subfolder is equally unreachable. Gating
    the count on `tier: detail` would let the worst case through.
    """
    from memforge.cli.audit import _parentless_rollup_subfolders

    sub = tmp_path / "notier"
    sub.mkdir()
    (sub / "no_tier_field.md").write_text(
        "---\nname: n\ndescription: d\ntype: reference\n---\nbody\n", encoding="utf-8"
    )
    assert _parentless_rollup_subfolders(tmp_path) == [("notier", 1)]


def test_parentless_check_skips_archive_and_populated_rollups(tmp_path: Path) -> None:
    """archive/ is excluded from the rollup contract, and a subfolder WITH a
    README.md is compliant and must never be reported."""
    from memforge.cli.audit import _parentless_rollup_subfolders

    arch = tmp_path / "archive"
    arch.mkdir()
    (arch / "old.md").write_text("---\nname: o\n---\nb\n", encoding="utf-8")
    ok = tmp_path / "compliant"
    ok.mkdir()
    (ok / "README.md").write_text("---\nname: r\n---\nb\n", encoding="utf-8")
    (ok / "detail.md").write_text("---\nname: d\n---\nb\n", encoding="utf-8")
    assert _parentless_rollup_subfolders(tmp_path) == []


def test_parentless_check_excludes_dot_directories(tmp_path: Path) -> None:
    """Tooling dot-directories are not rollup subfolders.

    arch-review-critic finding: audit excluded only `archive` while index_gen
    excluded `archive`, `.git`, and `.memforge`. Harmless before this check
    existed -- a dot-dir without a README simply contributed nothing to the
    pointer-comparable set. With the check in place it would raise an
    integrity VIOLATION for `.git/` as soon as any markdown landed there.
    """
    from memforge.cli.audit import _parentless_rollup_subfolders

    for d in (".git", ".memforge", ".memforge-rollup-history"):
        sub = tmp_path / d
        sub.mkdir()
        (sub / "stray.md").write_text("---\nname: s\n---\nb\n", encoding="utf-8")
    real = tmp_path / "realtopic"
    real.mkdir()
    (real / "detail.md").write_text("---\nname: d\n---\nb\n", encoding="utf-8")

    assert _parentless_rollup_subfolders(tmp_path) == [("realtopic", 1)]


def test_parentless_check_does_not_descend_symlinked_dirs(tmp_path: Path) -> None:
    """A symlinked directory must never be walked.

    threat-model finding: index_gen guards symlinks in five places, audit.py
    had none. A symlink pointing outside the store (worst case `-> /`) turns
    this walk into an arbitrary-tree enumeration -- a cheap DoS against the
    audit -- and could report a "subfolder" that is not part of the store.
    """
    import os
    from memforge.cli.audit import _parentless_rollup_subfolders

    outside = tmp_path.parent / f"outside_{tmp_path.name}"
    outside.mkdir()
    (outside / "stray.md").write_text("---\nname: s\n---\nb\n", encoding="utf-8")

    link = tmp_path / "linked"
    try:
        os.symlink(outside, link)
    except (OSError, NotImplementedError):  # pragma: no cover
        import pytest
        pytest.skip("symlinks unavailable on this platform")

    real = tmp_path / "realtopic"
    real.mkdir()
    (real / "detail.md").write_text("---\nname: d\n---\nb\n", encoding="utf-8")

    assert _parentless_rollup_subfolders(tmp_path) == [("realtopic", 1)]


# Regression coverage for the rollup-README-completeness check.
#
# memory-audit already fails a subfolder that has NO README.md
# (`_parentless_rollup_subfolders`). It did NOT detect a README.md that
# EXISTS but omits some sibling detail files' pointers, so those files were
# unreachable from MEMORY.md while the folder audited clean. Observed in
# practice: a rollup can omit most of its siblings, or a rename can leave
# every pointer dangling, and the audit still reports clean.


def _seed_rollup_readme_with_body(folder: Path, topic: str, body: str) -> None:
    sub = folder / topic
    sub.mkdir(exist_ok=True)
    (sub / "README.md").write_text(
        "---\n"
        f"name: {topic} rollup\n"
        f"description: Rollup README for {topic}\n"
        "type: reference\n"
        "tier: index\n"
        "---\n\n"
        f"{body}\n",
        encoding="utf-8",
    )


def test_rollup_readme_gaps_complete_markdown_style(tmp_path: Path) -> None:
    """A complete rollup using markdown-link pointers raises nothing."""
    from memforge.cli.audit import _rollup_readme_gaps

    _seed_rollup_readme_with_body(
        tmp_path, "topic",
        "- [A](topic_a.md)\n- [B](./topic_b.md)\n",
    )
    _seed_subfolder_detail(tmp_path, "topic", "topic_a.md")
    _seed_subfolder_detail(tmp_path, "topic", "topic_b.md")

    gaps = _rollup_readme_gaps(tmp_path, {"topic_a.md", "topic_b.md"})
    assert gaps == []


def test_rollup_readme_gaps_wikilink_unreachable_file(tmp_path: Path) -> None:
    """Wikilink-style rollup with one sibling omitted from the Members list.

    This is the shape that matters most in practice. A checker that only
    parses markdown links (`](...)`) would score this rollup as having ZERO
    pointers and either miss the gap entirely or over-report every sibling as
    unreachable. Set comparison against a wikilink-aware extractor must name
    exactly the one omitted file.
    """
    from memforge.cli.audit import _rollup_readme_gaps

    _seed_rollup_readme_with_body(
        tmp_path, "topic",
        "## Members\n\n- [[topic_a]]\n",
    )
    _seed_subfolder_detail(tmp_path, "topic", "topic_a.md")
    _seed_subfolder_detail(tmp_path, "topic", "topic_b.md")

    gaps = _rollup_readme_gaps(tmp_path, {"topic_a.md", "topic_b.md"})
    assert gaps == [("topic", ["topic_b.md"], [], False)]


def test_rollup_readme_gaps_numbered_prose_not_bullet_anchored(tmp_path: Path) -> None:
    """A numbered-prose Members list (link mid-sentence, no leading `-`
    bullet) must be parsed. The real convention this regresses is
    '1. **Title** ([label](file.md)).' An earlier checker anchored to a
    leading bullet and produced a false all-unreachable reading against
    exactly this shape.
    """
    from memforge.cli.audit import _rollup_readme_gaps

    _seed_rollup_readme_with_body(
        tmp_path, "topic",
        "1. **First rule** ([label](topic_a.md)). Trailing prose.\n"
        "2. **Second rule** ([label](topic_b.md)). More prose.\n",
    )
    _seed_subfolder_detail(tmp_path, "topic", "topic_a.md")
    _seed_subfolder_detail(tmp_path, "topic", "topic_b.md")

    gaps = _rollup_readme_gaps(tmp_path, {"topic_a.md", "topic_b.md"})
    assert gaps == []


def test_rollup_readme_gaps_dangling_pointer_pre_rename(tmp_path: Path) -> None:
    """A pointer to a pre-rename filename that exists NOWHERE in the store
    is reported as dangling, named explicitly."""
    from memforge.cli.audit import _rollup_readme_gaps

    _seed_rollup_readme_with_body(
        tmp_path, "topic",
        "- [A](topic_a.md)\n- [Stale](old_name_before_rename.md)\n",
    )
    _seed_subfolder_detail(tmp_path, "topic", "topic_a.md")

    gaps = _rollup_readme_gaps(tmp_path, {"topic_a.md"})
    assert gaps == [("topic", [], ["old_name_before_rename.md"], False)]


def test_rollup_readme_gaps_out_of_folder_ref_not_dangling(tmp_path: Path) -> None:
    """A pointer to a file that lives in a DIFFERENT folder (or store root)
    is a legitimate cross-reference, not a dangling pointer. Resolved
    against the whole store, not just this subfolder.
    """
    from memforge.cli.audit import _rollup_readme_gaps

    _seed_rollup_readme_with_body(
        tmp_path, "topic",
        "- [A](topic_a.md)\n- [Cross-ref](other_topic/other_file.md)\n",
    )
    _seed_subfolder_detail(tmp_path, "topic", "topic_a.md")
    _seed_subfolder_detail(tmp_path, "other_topic", "other_file.md")

    gaps = _rollup_readme_gaps(tmp_path, {"topic_a.md", "other_file.md"})
    assert gaps == []


def test_rollup_readme_gaps_duplicate_pointer_masks_omission(tmp_path: Path) -> None:
    """Compare SETS, never counts. A duplicate pointer to the same file, plus
    an omitted sibling, must still surface the omission. A count-based check
    (2 pointers == 2 files) would pass and lose a memory.
    """
    from memforge.cli.audit import _rollup_readme_gaps

    _seed_rollup_readme_with_body(
        tmp_path, "topic",
        "- [A](topic_a.md)\n- [A again](topic_a.md)\n",
    )
    _seed_subfolder_detail(tmp_path, "topic", "topic_a.md")
    _seed_subfolder_detail(tmp_path, "topic", "topic_b.md")  # never pointed to

    gaps = _rollup_readme_gaps(tmp_path, {"topic_a.md", "topic_b.md"})
    assert gaps == [("topic", ["topic_b.md"], [], False)]


def test_rollup_readme_gaps_backtick_prose_not_a_pointer(tmp_path: Path) -> None:
    """A backtick bare-filename citation inside ordinary prose (e.g. a Why or
    How-to-apply paragraph citing an old filename) is NOT read as a pointer,
    and must not surface as a dangling reference.
    """
    from memforge.cli.audit import _rollup_readme_gaps

    _seed_rollup_readme_with_body(
        tmp_path, "topic",
        "**Why:** this rollup replaces the old `topic_a.md` naming with "
        "clearer slugs, described here in prose.\n\n"
        "- [AA](topic_aa.md)\n",
    )
    _seed_subfolder_detail(tmp_path, "topic", "topic_aa.md")

    gaps = _rollup_readme_gaps(tmp_path, {"topic_aa.md"})
    assert gaps == []  # the backtick citation must not surface as dangling


def test_rollup_readme_gaps_backtick_is_never_a_pointer(tmp_path: Path) -> None:
    """Spec invariant 28 CLOSES the pointer set to wikilink + markdown link. A
    backtick bare filename is NOT a pointer, wherever it appears, including on a
    Members-style list-item line.

    Expectation deliberately INVERTED from an earlier draft of this check, which
    counted a backticked filename on a list-item line and excluded it only in
    prose. The architect spec-delta pass called that out: an open pointer set
    lets two conforming implementations disagree on the same folder, which an
    audit (binary) must never do. So a README whose ONLY reference to a sibling
    is backticked leaves that sibling unreachable, and the audit says so.
    """
    from memforge.cli.audit import _rollup_readme_gaps

    _seed_rollup_readme_with_body(
        tmp_path, "topic",
        "## Members\n\n- Rule one, see `topic_a.md` for detail.\n",
    )
    _seed_subfolder_detail(tmp_path, "topic", "topic_a.md")

    gaps = _rollup_readme_gaps(tmp_path, {"topic_a.md"})
    assert gaps == [("topic", ["topic_a.md"], [], False)]


def test_rollup_readme_gaps_backtick_external_repo_path_ignored(tmp_path: Path) -> None:
    """A backtick citation of an EXTERNAL repo path on a bullet line is not a
    store pointer, even though it ends in `.md` and sits on a list item.

    Found by running this check against a real store: a Cross-references
    bullet citing another repository's `<org>/<repo>/ARCHITECTURE.md` was
    reported as a dangling pointer, because taking the basename of a
    multi-segment external path yields a filename that is not a store member
    and never was. Under the closed pointer set of spec invariant 28 a
    backticked filename is not a pointer at all, so the citation cannot
    produce a phantom finding by any route. Markdown links still accept
    `folder/file.md`, the legitimate in-folder cross-subfolder form.
    """
    from memforge.cli.audit import _rollup_readme_gaps

    _seed_rollup_readme_with_body(
        tmp_path, "topic",
        "## Members\n\n- [[topic_a]]\n\n"
        "## Cross-references\n\n"
        "- Canonical index: `some-org/some-repo/ARCHITECTURE.md`.\n",
    )
    _seed_subfolder_detail(tmp_path, "topic", "topic_a.md")

    gaps = _rollup_readme_gaps(tmp_path, {"topic_a.md"})
    assert gaps == []


def test_rollup_readme_gaps_prose_only_ignores_fence_and_comment(tmp_path: Path) -> None:
    """Spec invariant 28 reads pointers from PROSE ONLY. A link inside a fenced
    code block, an inline code span, or an HTML comment is not a membership
    claim and MUST NOT count in either direction.

    Raised by the adversarial critic on the spec delta: without an explicit
    exclusion, two conforming implementations can disagree on the same folder,
    which an audit (binary) must never permit. Latent rather than live at the
    time (no real rollup had a link inside a fence or comment), fixed before it
    could become live.

    `topic_a` is reachable only via a fenced example, a code span, and a
    commented-out note, so it must still be reported unreachable. The fenced
    link to `not_a_member.md` must NOT be reported dangling.
    """
    from memforge.cli.audit import _rollup_readme_gaps

    _seed_rollup_readme_with_body(
        tmp_path, "topic",
        "## Members\n\n"
        "Example of the pointer syntax:\n\n"
        "```markdown\n- [[topic_a]]\n- [Missing](not_a_member.md)\n```\n\n"
        "Inline form is `[[topic_a]]` for reference.\n\n"
        "<!-- - [[topic_a]] was here before the rename -->\n",
    )
    _seed_subfolder_detail(tmp_path, "topic", "topic_a.md")

    gaps = _rollup_readme_gaps(tmp_path, {"topic_a.md"})
    assert gaps == [("topic", ["topic_a.md"], [], False)]


def test_rollup_readme_gaps_unreadable_readme_fails_closed(tmp_path: Path) -> None:
    """An unreadable rollup README is itself a violation, and every sibling is
    reported unreachable.

    Threat-model BLOCKER (2026-07-30): an earlier draft caught OSError and
    silently skipped the subfolder, so making a README unreadable (a permissions
    glitch, or a deliberate chmod 000) SUPPRESSED every finding for that folder.
    A check whose purpose is finding completeness gaps must never report clean
    because it could not look.
    """
    import os
    import stat

    from memforge.cli.audit import _rollup_readme_gaps

    _seed_rollup_readme_with_body(tmp_path, "topic", "## Members\n\n- [[topic_a]]\n")
    _seed_subfolder_detail(tmp_path, "topic", "topic_a.md")
    readme = tmp_path / "topic" / "README.md"
    try:
        os.chmod(readme, 0o000)
        if os.access(readme, os.R_OK):  # running as root, or a permissive FS
            import pytest
            pytest.skip("cannot make a file unreadable in this environment")
        gaps = _rollup_readme_gaps(tmp_path, {"topic_a.md"})
    finally:
        os.chmod(readme, stat.S_IRUSR | stat.S_IWUSR)

    assert gaps == [("topic", ["topic_a.md"], [], True)]


def test_rollup_readme_gaps_nul_byte_in_pointer_does_not_crash(tmp_path: Path) -> None:
    """A NUL byte in a pointer target must not crash the audit.

    A threat-model pass flagged an embedded NUL as an unhandled-exception risk.
    VERIFIED FALSE before acting on it: `Path(x).name` does not raise on a NUL,
    and this code never opens a pointer target, it only compares basenames. So
    no crash is reachable. Kept as a regression test for the real property: a
    NUL-bearing pointer does not crash, matches no file, and is reported as
    dangling.
    """
    from memforge.cli.audit import _rollup_readme_gaps

    _seed_rollup_readme_with_body(
        tmp_path, "topic",
        "## Members\n\n- [[topic_a]]\n- [Bad](bad\x00name.md)\n",
    )
    _seed_subfolder_detail(tmp_path, "topic", "topic_a.md")

    gaps = _rollup_readme_gaps(tmp_path, {"topic_a.md"})
    assert len(gaps) == 1
    sub_name, unreachable, dangling, unreadable = gaps[0]
    assert sub_name == "topic"
    assert unreachable == []
    assert unreadable is False
    assert dangling == ["bad\x00name.md"]


def test_rollup_readme_gaps_end_to_end_via_audit_target(tmp_path: Path) -> None:
    """End-to-end: a wikilink rollup missing one pointer raises an
    INTEGRITY VIOLATION through `audit_target`, naming folder and file."""
    from memforge.cli.audit import audit_target

    _seed_top_level(tmp_path, "feedback_a.md")
    _seed_rollup_readme_with_body(
        tmp_path, "topic",
        "## Members\n\n- [[topic_a]]\n",
    )
    _seed_subfolder_detail(tmp_path, "topic", "topic_a.md")
    _seed_subfolder_detail(tmp_path, "topic", "topic_b.md")
    (tmp_path / "MEMORY.md").write_text(
        "# Memory Index\n\n"
        "- [feedback A](feedback_a.md) - top-level entry\n"
        "- [Topic rollup](topic/README.md) - rollup\n",
        encoding="utf-8",
    )

    import io
    from contextlib import redirect_stdout

    buf = io.StringIO()
    with redirect_stdout(buf):
        violations, _ = audit_target(
            tmp_path, stale_days=365, fix=False, add_defaults=False, json_out=False
        )
    output = buf.getvalue()
    assert (
        "Rollup README incomplete: topic/README.md has no pointer to "
        "1 file(s): topic_b.md"
    ) in output
    assert violations >= 1
