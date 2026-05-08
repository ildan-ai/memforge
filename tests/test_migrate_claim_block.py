"""Tests for memforge.cli.migrate_claim_block."""

from __future__ import annotations

from pathlib import Path
from textwrap import dedent

from memforge.cli.migrate_claim_block import migrate_text


# ---------- happy path: per-group rewritten, per-member untouched ----------


def test_per_group_status_rewritten():
    text = dedent("""\
        # Index

        ```yaml
        # memforge:competing-claims:begin
        - decision_topic: foo
          status: competing
          members:
            - uid: mem-a
              owner: mike
              status: active
              updated: 2026-05-08
              first_line: First
              file_path: a.md
        # memforge:competing-claims:end
        ```
        """)
    new_text, count = migrate_text(text)
    assert count == 1
    # Per-group `status:` rewritten
    assert "  state: competing" in new_text
    assert "  status: competing" not in new_text
    # Per-member `status:` (6-space indent) unchanged
    assert "      status: active" in new_text


def test_per_member_status_not_rewritten():
    """Member-level status: at 6-space indent must NOT be touched."""
    text = dedent("""\
        ```yaml
        # memforge:competing-claims:begin
        - decision_topic: bar
          state: competing
          members:
            - uid: mem-a
              status: proposed
              file_path: a.md
            - uid: mem-b
              status: gated
              file_path: b.md
        # memforge:competing-claims:end
        ```
        """)
    new_text, count = migrate_text(text)
    assert count == 0  # nothing to rewrite (already canonical)
    assert "      status: proposed" in new_text
    assert "      status: gated" in new_text


def test_idempotent():
    text = dedent("""\
        # memforge:competing-claims:begin
        - decision_topic: foo
          status: competing
          members:
            - uid: mem-a
              status: active
        # memforge:competing-claims:end
        """)
    once, c1 = migrate_text(text)
    twice, c2 = migrate_text(once)
    assert c1 == 1
    assert c2 == 0  # second run is a no-op
    assert once == twice


def test_no_block_no_op():
    text = "# Just a normal MEMORY.md\n\n- [pointer](file.md) — hook\n"
    new_text, count = migrate_text(text)
    assert count == 0
    assert new_text == text


def test_outside_block_status_not_touched():
    """A `  status: ...` line OUTSIDE the fenced block must NOT be rewritten."""
    text = dedent("""\
        Some preamble.

          status: this-is-not-in-the-block

        # memforge:competing-claims:begin
        - decision_topic: foo
          state: competing
        # memforge:competing-claims:end

          status: this-is-also-not-in-the-block
        """)
    new_text, count = migrate_text(text)
    assert count == 0
    assert new_text.count("status: this-is-not-in-the-block") == 1
    assert new_text.count("status: this-is-also-not-in-the-block") == 1


def test_two_groups_both_rewritten():
    text = dedent("""\
        # memforge:competing-claims:begin
        - decision_topic: foo
          status: competing
          members:
            - uid: mem-a
              status: active
        - decision_topic: bar
          status: snoozed
          snoozed_until: 2026-05-21
          members:
            - uid: mem-b
              status: proposed
        # memforge:competing-claims:end
        """)
    new_text, count = migrate_text(text)
    assert count == 2
    assert "  state: competing" in new_text
    assert "  state: snoozed" in new_text
    assert "  status: competing" not in new_text
    assert "  status: snoozed" not in new_text
    # Member-level status untouched
    assert "      status: active" in new_text
    assert "      status: proposed" in new_text


def test_preserves_line_endings_unix():
    text = "# memforge:competing-claims:begin\n- decision_topic: foo\n  status: competing\n# memforge:competing-claims:end\n"
    new_text, count = migrate_text(text)
    assert count == 1
    assert "\r" not in new_text
    assert new_text.endswith("\n")


def test_file_migration_writes_file(tmp_path: Path):
    """Smoke check: the file-level migrator writes when changes occur."""
    from memforge.cli.migrate_claim_block import migrate_file

    p = tmp_path / "MEMORY.md"
    p.write_text(
        "# memforge:competing-claims:begin\n"
        "- decision_topic: foo\n"
        "  status: competing\n"
        "# memforge:competing-claims:end\n",
        encoding="utf-8",
    )
    count = migrate_file(p, dry_run=False)
    assert count == 1
    assert "  state: competing" in p.read_text(encoding="utf-8")


def test_file_migration_dry_run_no_write(tmp_path: Path):
    from memforge.cli.migrate_claim_block import migrate_file

    p = tmp_path / "MEMORY.md"
    original = (
        "# memforge:competing-claims:begin\n"
        "- decision_topic: foo\n"
        "  status: competing\n"
        "# memforge:competing-claims:end\n"
    )
    p.write_text(original, encoding="utf-8")
    count = migrate_file(p, dry_run=True)
    assert count == 1
    assert p.read_text(encoding="utf-8") == original  # unchanged
