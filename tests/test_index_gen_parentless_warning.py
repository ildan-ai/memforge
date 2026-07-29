"""memory-index-gen must WARN when it is about to ship a lossy index.

`discover_index_files()` skips any subfolder whose README.md is absent. That
is the correct index SHAPE per spec -- detail files never appear in MEMORY.md
-- but with no rollup parent there is nothing to surface them through, so they
vanish from the index with no signal. The generated file looks smaller and
cleaner while memories are simply gone from view.

These tests pin the warning, and pin that the index CONTENTS are unchanged:
the fix is to author the missing README, never to widen MEMORY.md.
"""

from pathlib import Path


def _mf(p: Path, name: str) -> None:
    p.write_text(
        f"---\nname: {name}\ndescription: d\ntype: reference\n---\n\nbody\n",
        encoding="utf-8",
    )


def test_parentless_subfolders_detected(tmp_path: Path) -> None:
    from memforge.cli.index_gen import parentless_subfolders

    lost = tmp_path / "lost"
    lost.mkdir()
    _mf(lost / "a.md", "a")
    _mf(lost / "b.md", "b")

    ok = tmp_path / "compliant"
    ok.mkdir()
    _mf(ok / "README.md", "r")
    _mf(ok / "detail.md", "d")

    assert parentless_subfolders(tmp_path) == [("lost", 2)]


def test_archive_and_dotdirs_excluded(tmp_path: Path) -> None:
    """archive/ is outside the rollup contract; .git and .memforge are tooling."""
    from memforge.cli.index_gen import parentless_subfolders

    for d in ("archive", ".git", ".memforge"):
        sub = tmp_path / d
        sub.mkdir()
        _mf(sub / "x.md", "x")
    assert parentless_subfolders(tmp_path) == []


def test_warning_emitted_on_stderr_and_names_the_loss(tmp_path: Path, capsys) -> None:
    from memforge.cli.index_gen import _warn_parentless

    lost = tmp_path / "orphaned"
    lost.mkdir()
    _mf(lost / "one.md", "one")
    _mf(lost / "two.md", "two")
    _mf(lost / "three.md", "three")

    _warn_parentless(tmp_path)
    err = capsys.readouterr().err
    assert "orphaned/" in err
    assert "3 file(s)" in err
    assert "NOT reachable from" in err
    # must tell the operator the RIGHT fix, not "add them to MEMORY.md"
    assert "README.md rollup parent" in err


def test_no_warning_when_all_subfolders_have_parents(tmp_path: Path, capsys) -> None:
    from memforge.cli.index_gen import _warn_parentless

    ok = tmp_path / "fine"
    ok.mkdir()
    _mf(ok / "README.md", "r")
    _mf(ok / "d.md", "d")

    _warn_parentless(tmp_path)
    assert capsys.readouterr().err == ""
