"""Tests for audit_deep taxonomy resolution + fail-loud behavior (craft-01)."""

from __future__ import annotations

from pathlib import Path

from memforge.cli import audit_deep


def test_taxonomy_loads_from_source_tree():
    """With no --memforge-root, load_taxonomy must find the repo-root
    spec/taxonomy.yaml in a source checkout (craft-01: the prior single
    Path(__file__).parent.parent/spec path existed in NEITHER layout, so
    tag-membership enforcement was silently off everywhere)."""
    path = audit_deep.find_taxonomy_path(None)
    assert path is not None and path.exists()
    tax = audit_deep.load_taxonomy(None)
    assert isinstance(tax, dict) and tax  # non-empty taxonomy loaded


def test_explicit_root_does_not_fall_through(tmp_path):
    """An explicit --memforge-root that has no taxonomy must NOT silently fall
    back to the packaged/source copy."""
    assert audit_deep.find_taxonomy_path(tmp_path) is None
    assert audit_deep.load_taxonomy(tmp_path) == {}


def test_strict_fails_when_taxonomy_missing(tmp_path, monkeypatch, capsys):
    """Under --strict, a taxonomy that cannot be loaded is a hard error: the tool
    must not report success while tag-membership enforcement is disabled."""
    mem = tmp_path / "mem"
    mem.mkdir()
    (mem / "MEMORY.md").write_text("# Index\n", encoding="utf-8")
    (mem / "a.md").write_text(
        "---\nname: a\ndescription: d\ntype: feedback\nuid: mem-a\n---\nbody\n",
        encoding="utf-8",
    )
    # Point --memforge-root at an empty dir so taxonomy cannot be found.
    monkeypatch.setattr(
        "sys.argv",
        ["memory-audit-deep", "--path", str(mem),
         "--memforge-root", str(tmp_path / "empty"), "--strict"],
    )
    rc = audit_deep.main()
    assert rc == 1
    err = capsys.readouterr().err
    assert "taxonomy" in err.lower()


def test_non_strict_warns_but_succeeds_when_taxonomy_missing(tmp_path, monkeypatch, capsys):
    mem = tmp_path / "mem"
    mem.mkdir()
    (mem / "MEMORY.md").write_text("# Index\n", encoding="utf-8")
    monkeypatch.setattr(
        "sys.argv",
        ["memory-audit-deep", "--path", str(mem),
         "--memforge-root", str(tmp_path / "empty")],
    )
    rc = audit_deep.main()
    assert rc == 0
    out = capsys.readouterr().out
    # The skip is surfaced in the per-folder report, not only on stderr.
    assert "not loaded" in out.lower() or "skipped" in out.lower()
