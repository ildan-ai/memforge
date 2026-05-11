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
            strict=False,
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
            strict=False,
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
            strict=False,
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
            strict=False,
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
            strict=False,
            fix=False,
            add_defaults=False,
            json_out=False,
        )

    output = buf.getvalue()
    assert "Orphan pointer (no file): forge/does_not_exist.md" in output
    assert violations >= 1
