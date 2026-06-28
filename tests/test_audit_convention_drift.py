"""Tests for memory-audit convention-drift health checks.

Verifies that MEMORY.md line-count overage and pointer byte-length overage
are emitted as HEALTH warnings (not integrity violations), per spec v0.6.1
and the v2 design panel resolution (D2).
"""

from __future__ import annotations

from pathlib import Path

from memforge.cli.audit import audit_target, MEMORY_MD_LINE_CAP, POINTER_LINE_BYTE_CAP


def _make_memory_file(folder: Path, name: str = "feedback_x.md") -> None:
    (folder / name).write_text(
        "---\n"
        "name: Test memory\n"
        "description: A test memory for convention drift checks\n"
        "type: feedback\n"
        "sensitivity: internal\n"
        "---\n\n"
        "Body text.\n"
        "\n"
        "**Why:** test convention drift.\n"
        "**How to apply:** always.\n",
        encoding="utf-8",
    )


def _run_audit(folder: Path) -> tuple[int, list[str], list[str]]:
    """Run audit_target and return (violations, health) lists."""
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


# ---- MEMORY.md line count ----

def test_memory_md_within_line_cap_no_health_warning(tmp_path: Path) -> None:
    _make_memory_file(tmp_path)
    lines = ["- [Test memory](feedback_x.md) : description"] * 10
    (tmp_path / "MEMORY.md").write_text("\n".join(lines) + "\n", encoding="utf-8")

    count, violations, health = _run_audit(tmp_path)
    drift_health = [h for h in health if "[convention-drift]" in h and "lines" in h]
    assert drift_health == [], f"unexpected convention-drift health: {drift_health}"


def test_memory_md_over_line_cap_emits_health_not_violation(tmp_path: Path) -> None:
    _make_memory_file(tmp_path)
    # Build a MEMORY.md that is MEMORY_MD_LINE_CAP + 5 lines.
    pointer = "- [Test memory](feedback_x.md) : test description"
    filler = "# section\n"
    lines = [pointer] + [filler] * (MEMORY_MD_LINE_CAP + 4)
    (tmp_path / "MEMORY.md").write_text("\n".join(lines) + "\n", encoding="utf-8")

    count, violations, health = _run_audit(tmp_path)

    # Must be a health warning, not a violation.
    drift_health = [h for h in health if "[convention-drift]" in h and "lines" in h]
    assert len(drift_health) == 1, f"expected 1 convention-drift health item, got: {health}"
    assert str(MEMORY_MD_LINE_CAP) in drift_health[0]

    # Must NOT be an integrity violation.
    drift_violations = [v for v in violations if "lines" in v and "cap" in v]
    assert drift_violations == [], f"should not be a violation: {drift_violations}"


# ---- pointer byte length ----

def test_pointer_within_byte_cap_no_health_warning(tmp_path: Path) -> None:
    _make_memory_file(tmp_path)
    # Construct a pointer line that is exactly at the cap.
    base = "- [Test memory](feedback_x.md) : "
    padding = "x" * (POINTER_LINE_BYTE_CAP - len(base.encode("utf-8")))
    pointer = base + padding
    assert len(pointer.encode("utf-8")) <= POINTER_LINE_BYTE_CAP
    (tmp_path / "MEMORY.md").write_text(pointer + "\n", encoding="utf-8")

    count, violations, health = _run_audit(tmp_path)
    drift_health = [h for h in health if "[convention-drift]" in h and "bytes" in h]
    assert drift_health == [], f"unexpected convention-drift health: {drift_health}"


def test_pointer_over_byte_cap_emits_health_not_violation(tmp_path: Path) -> None:
    _make_memory_file(tmp_path)
    # Construct a pointer line that exceeds the byte cap by 10 bytes.
    base = "- [Test memory](feedback_x.md) : "
    padding = "x" * (POINTER_LINE_BYTE_CAP - len(base.encode("utf-8")) + 10)
    pointer = base + padding
    assert len(pointer.encode("utf-8")) > POINTER_LINE_BYTE_CAP
    (tmp_path / "MEMORY.md").write_text(pointer + "\n", encoding="utf-8")

    count, violations, health = _run_audit(tmp_path)

    # Must be a health warning.
    drift_health = [h for h in health if "[convention-drift]" in h and "bytes" in h]
    assert len(drift_health) == 1, f"expected 1 byte convention-drift health item, got: {health}"
    assert str(POINTER_LINE_BYTE_CAP) in drift_health[0]

    # Must NOT be an integrity violation.
    drift_violations = [v for v in violations if "pointer lines" in v and "bytes" in v]
    assert drift_violations == [], f"should not be a violation: {drift_violations}"


def test_em_dash_counted_as_3_bytes(tmp_path: Path) -> None:
    """A pointer line using an em-dash costs 3 bytes per em-dash.

    Verify that a line that is exactly at the byte cap with an em-dash present
    correctly triggers when it pushes past the cap once the multi-byte cost
    is counted (this catches regressions from a len(line) char count).
    """
    _make_memory_file(tmp_path)
    # em-dash is '—', 3 bytes in UTF-8.
    # Build a line that would be <= 150 chars but > 150 bytes due to em-dashes.
    base = "- [Test memory](feedback_x.md) — "
    # base is len(base) chars but len(base.encode('utf-8')) = len(base) + 2 extra (3-1 per dash).
    em_dash_extra = len(base.encode("utf-8")) - len(base)  # extra bytes from em-dash
    # Pad to push byte count just over cap while keeping char count under cap.
    char_budget = POINTER_LINE_BYTE_CAP - em_dash_extra
    padding = "a" * (char_budget - len(base) + 5)
    pointer = base + padding
    if len(pointer.encode("utf-8")) <= POINTER_LINE_BYTE_CAP:
        # Not enough padding; add more.
        pointer = pointer + "b" * 10
    (tmp_path / "MEMORY.md").write_text(pointer + "\n", encoding="utf-8")

    if len(pointer.encode("utf-8")) > POINTER_LINE_BYTE_CAP:
        count, violations, health = _run_audit(tmp_path)
        drift_health = [h for h in health if "[convention-drift]" in h and "bytes" in h]
        assert len(drift_health) >= 1, "em-dash byte overhead should trigger convention-drift health"
