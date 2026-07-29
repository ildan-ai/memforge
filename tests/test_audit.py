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
