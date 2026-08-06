"""Tests for the top-level vs nested `metadata:` divergence check.

Some agent harnesses write their own canonical frontmatter shape: they nest the
prior block under `metadata:` and synthesize fresh top-level fields. Nothing is
lost, because the old values survive in the nested block. But every memforge
tool reads ONLY the top level, so the nested copy becomes invisible truth and
the two drift apart with nothing objecting.

Measured in a real 842-memory corpus: 460 files carried the nested block, and 74
of them (8.8%) had at least one identity or lifecycle field disagreeing across
the two levels. `memory-audit --strict` exited 0 on all of it.

Two observed cases show why it matters beyond tidiness:
  * `uid` regenerated at the top level while the original sits nested. `uid` is
    the key `mem:uid` links resolve against, so every inbound reference orphans.
  * `status: active` at the top level against `status: resolved-fixed` nested,
    i.e. a memory reporting itself live when the nested record says otherwise.

HEALTH, never an integrity violation: the file is well-formed and parses fine,
and a corpus that adopted a harness shape can carry many of these through no
fault of its own. Failing `--strict` on that makes the flag something operators
switch off, which protects nothing.
"""

from __future__ import annotations

from pathlib import Path

from memforge.cli.audit import audit_target, _metadata_divergence


def _write(folder: Path, name: str, extra: str) -> None:
    (folder / name).write_text(
        "---\n"
        "name: Test memory\n"
        "description: A test memory for the divergence check\n"
        "type: reference\n"
        "sensitivity: internal\n"
        "tags:\n"
        "- topic:forge\n"
        f"{extra}"
        "---\n\n"
        "Body text.\n",
        encoding="utf-8",
    )


def _health(folder: Path) -> list[str]:
    import io
    from contextlib import redirect_stdout

    buf = io.StringIO()
    with redirect_stdout(buf):
        _c, blob = audit_target(
            folder, stale_days=9999, fix=False, add_defaults=False, json_out=True
        )
    assert blob is not None
    return blob["health"]


def _hits(health: list[str]) -> list[str]:
    return [h for h in health if "disagrees with nested metadata" in h]


# ---- helper unit coverage ----

def test_detects_uid_divergence() -> None:
    out = _metadata_divergence({"uid": "new", "metadata": {"uid": "old"}})
    assert len(out) == 1 and "uid" in out[0]
    assert "'new'" in out[0] and "'old'" in out[0], "must show BOTH values"


def test_agreeing_values_are_not_flagged() -> None:
    assert _metadata_divergence({"uid": "same", "metadata": {"uid": "same"}}) == []


def test_field_present_on_only_one_side_is_not_divergence() -> None:
    """Absence is not disagreement. A nested block that simply carries more
    fields than the top level is the normal harness shape, not a defect."""
    assert _metadata_divergence({"uid": "x", "metadata": {"tier": "detail"}}) == []


def test_no_metadata_block_is_not_divergence() -> None:
    for fm in ({"uid": "x"}, {"uid": "x", "metadata": None}, {"metadata": "scalar"}):
        assert _metadata_divergence(fm) == [], fm


def test_free_form_fields_are_out_of_scope() -> None:
    """A reworded description is ordinary editing. Only identity and lifecycle
    fields, whose divergence changes how a tool treats the memory, are compared."""
    fm = {"description": "new wording", "metadata": {"description": "old wording"}}
    assert _metadata_divergence(fm) == []


def test_reports_every_diverging_field() -> None:
    fm = {
        "uid": "a", "tier": "index", "status": "active",
        "metadata": {"uid": "b", "tier": "detail", "status": "blocked"},
    }
    out = _metadata_divergence(fm)
    assert len(out) == 3
    assert {"uid", "tier", "status"} == {o.split(" ")[0] for o in out}


# ---- audit integration ----

def test_divergent_file_is_reported_once(tmp_path: Path) -> None:
    _write(
        tmp_path, "drift.md",
        "uid: mem-2026-08-05-new\n"
        "metadata:\n"
        "  uid: mem-2026-07-17-original\n"
        "  tier: detail\n",
    )
    (tmp_path / "MEMORY.md").write_text(
        "# Index\n\n- [Test memory](drift.md): hook\n", encoding="utf-8"
    )
    hits = _hits(_health(tmp_path))
    assert len(hits) == 1, f"one finding per file, not per field; got: {hits}"
    assert "uid" in hits[0]


def test_agreeing_file_is_not_reported(tmp_path: Path) -> None:
    _write(
        tmp_path, "fine.md",
        "uid: mem-2026-07-17-original\n"
        "metadata:\n"
        "  uid: mem-2026-07-17-original\n",
    )
    (tmp_path / "MEMORY.md").write_text(
        "# Index\n\n- [Test memory](fine.md): hook\n", encoding="utf-8"
    )
    assert _hits(_health(tmp_path)) == []


def test_reported_as_health_never_as_violation(tmp_path: Path) -> None:
    _write(
        tmp_path, "drift.md",
        "uid: mem-2026-08-05-new\nmetadata:\n  uid: mem-2026-07-17-original\n",
    )
    (tmp_path / "MEMORY.md").write_text(
        "# Index\n\n- [Test memory](drift.md): hook\n", encoding="utf-8"
    )
    import io
    from contextlib import redirect_stdout

    buf = io.StringIO()
    with redirect_stdout(buf):
        _c, blob = audit_target(
            tmp_path, stale_days=9999, fix=False, add_defaults=False, json_out=True
        )
    assert blob is not None
    assert not any("disagrees with nested metadata" in v for v in blob["violations"])
    assert _hits(blob["health"])
