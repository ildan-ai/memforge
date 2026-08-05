"""Tests for the bare-pointer tripwire in memory-audit.

`index_gen._bullet` omits the hook entirely when the `- [title](path): ` prefix
leaves 3 or fewer bytes of POINTER_LINE_BYTE_CAP. The emitted line is still a
correct, recall-lossless link, but it carries no relevance text, so the reader
loses its only in-index cue.

The panel (2026-08-05) ruled this graceful degradation rather than a build
failure: hard-failing the generator over one long title would trade a cosmetic
loss for an availability loss. So it is reported as a HEALTH warning carrying the
byte accounting needed to fix it at the source, never as an integrity violation.

Measured at ruling time: zero instances across an 834-memory corpus. This is a
tripwire, so the "no false positive" cases below matter as much as the positive.
"""

from __future__ import annotations

from pathlib import Path

from memforge.cli.audit import audit_target, POINTER_LINE_BYTE_CAP


def _make_memory_file(folder: Path, name: str, description: str = "A real description") -> None:
    (folder / name).write_text(
        "---\n"
        "name: Test memory\n"
        f"description: {description}\n"
        "type: feedback\n"
        "sensitivity: internal\n"
        "---\n\n"
        "Body text.\n"
        "\n"
        "**Why:** test the bare-pointer tripwire.\n"
        "**How to apply:** always.\n",
        encoding="utf-8",
    )


def _run_audit(folder: Path) -> tuple[int, list[str], list[str]]:
    import io
    from contextlib import redirect_stdout

    buf = io.StringIO()
    with redirect_stdout(buf):
        violation_count, blob = audit_target(
            folder,
            stale_days=9999,
            fix=False,
            add_defaults=False,
            json_out=True,
        )
    assert blob is not None
    return violation_count, blob["violations"], blob["health"]


def _bare_pointer_health(health: list[str]) -> list[str]:
    return [h for h in health if "has no hook text" in h]


def test_bare_pointer_from_long_prefix_emits_health(tmp_path: Path) -> None:
    """A hook-less pointer whose prefix exhausts the cap is reported."""
    # Build a filename long enough that `- [title](path): ` alone leaves <= 3 bytes.
    long_stem = "a" * (POINTER_LINE_BYTE_CAP - 20)
    fname = f"{long_stem}.md"
    _make_memory_file(tmp_path, fname)
    (tmp_path / "MEMORY.md").write_text(
        f"# Index\n\n- [Test memory]({fname})\n", encoding="utf-8"
    )

    count, violations, health = _run_audit(tmp_path)

    hits = _bare_pointer_health(health)
    assert len(hits) == 1, f"expected 1 bare-pointer health item, got: {health}"
    # Must carry the byte accounting an operator needs to fix it at source.
    assert str(POINTER_LINE_BYTE_CAP) in hits[0]
    assert "bytes" in hits[0]
    assert "Shorten the title" in hits[0]
    # HEALTH, never an integrity violation.
    assert not any("no hook text" in v for v in violations)


def test_normal_pointer_with_hook_is_not_flagged(tmp_path: Path) -> None:
    """The common case must not trip the wire."""
    _make_memory_file(tmp_path, "feedback_x.md")
    (tmp_path / "MEMORY.md").write_text(
        "# Index\n\n- [Test memory](feedback_x.md): a perfectly good hook\n",
        encoding="utf-8",
    )

    count, violations, health = _run_audit(tmp_path)
    assert _bare_pointer_health(health) == []


def test_short_bare_pointer_is_not_flagged(tmp_path: Path) -> None:
    """A hook-less pointer with a SHORT prefix is a different defect.

    Room existed for a hook, so the omission means an empty description, which the
    frontmatter checks already cover. This tripwire must not double-report it, or
    it stops meaning "the prefix ate the budget".
    """
    _make_memory_file(tmp_path, "feedback_x.md")
    (tmp_path / "MEMORY.md").write_text(
        "# Index\n\n- [Test memory](feedback_x.md)\n", encoding="utf-8"
    )

    count, violations, health = _run_audit(tmp_path)
    assert _bare_pointer_health(health) == []
