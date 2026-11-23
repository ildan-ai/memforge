"""How `memory-audit-deep` treats a tag value its taxonomy does not list.

The taxonomy shipped in the package is a starter vocabulary, so against it an
unknown tag value is a warning: reported apart from the violations, not
counted, and never an exit-code change. A taxonomy the operator points at with
`--memforge-root` is their own vocabulary, and there an unknown value stays a
violation that fails `--strict`.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from memforge.cli import audit_deep

UNKNOWN_TAG = "topic:not-in-any-taxonomy"


def _memory(folder: Path, name: str, uid: str, tag: str) -> None:
    (folder / f"{name}.md").write_text(
        f"---\nname: {name}\ndescription: d\ntype: feedback\nuid: {uid}\n"
        f"tags: [{tag}]\n---\nbody\n",
        encoding="utf-8",
    )


@pytest.fixture
def store(tmp_path: Path) -> Path:
    folder = tmp_path / "mem"
    folder.mkdir()
    (folder / "MEMORY.md").write_text("# Index\n", encoding="utf-8")
    _memory(folder, "a", "mem-a", UNKNOWN_TAG)
    return folder


@pytest.fixture
def own_taxonomy(tmp_path: Path) -> Path:
    """A --memforge-root whose taxonomy lists one topic value, `alpha`."""
    root = tmp_path / "own"
    (root / "spec").mkdir(parents=True)
    (root / "spec" / "taxonomy.yaml").write_text(
        "namespaces:\n  topic:\n    values:\n      alpha: {}\n", encoding="utf-8"
    )
    return root


def _run(monkeypatch, capsys, *args: str) -> tuple[int, str, str]:
    monkeypatch.setattr("sys.argv", ["memory-audit-deep", *args])
    rc = audit_deep.main()
    captured = capsys.readouterr()
    return rc, captured.out, captured.err


@pytest.mark.parametrize("strict", [(), ("--strict",)])
def test_packaged_taxonomy_unknown_tag_is_a_warning(store, monkeypatch, capsys, strict):
    rc, out, _err = _run(monkeypatch, capsys, "--path", str(store), *strict)

    assert rc == 0
    assert "Total violations: 0" in out
    assert "clean (no violations)" in out
    violations, _, warnings = out.partition("TAG WARNINGS (1")
    assert UNKNOWN_TAG not in violations
    assert f"a.md: tag '{UNKNOWN_TAG}'" in warnings


def test_packaged_taxonomy_warning_is_not_counted_beside_a_violation(store, monkeypatch, capsys):
    _memory(store, "b", "mem-a", UNKNOWN_TAG)

    rc, out, _err = _run(monkeypatch, capsys, "--path", str(store), "--strict")

    assert rc == 1
    assert "VIOLATIONS (1):" in out
    assert "UID collision 'mem-a'" in out
    assert "TAG WARNINGS (2" in out
    assert "Total violations: 1" in out


def test_packaged_taxonomy_known_tag_prints_no_warning_heading(store, monkeypatch, capsys):
    known = sorted(
        audit_deep.known_namespace_values(audit_deep.load_taxonomy(None))["topic"]
    )[0]
    _memory(store, "a", "mem-a", f"topic:{known}")

    rc, out, _err = _run(monkeypatch, capsys, "--path", str(store), "--strict")

    assert rc == 0
    assert "TAG WARNINGS" not in out


def test_own_taxonomy_unknown_tag_is_a_violation(store, own_taxonomy, monkeypatch, capsys):
    rc, out, _err = _run(
        monkeypatch, capsys, "--path", str(store), "--memforge-root", str(own_taxonomy)
    )

    assert rc == 0
    assert "VIOLATIONS (1):" in out
    assert f"a.md: tag '{UNKNOWN_TAG}' value not in taxonomy namespace 'topic'" in out
    assert "TAG WARNINGS" not in out
    assert "Total violations: 1" in out


def test_own_taxonomy_unknown_tag_fails_strict(store, own_taxonomy, monkeypatch, capsys):
    rc, out, _err = _run(
        monkeypatch, capsys, "--path", str(store),
        "--memforge-root", str(own_taxonomy), "--strict",
    )

    assert rc == 1
    assert "Total violations: 1" in out


def test_own_taxonomy_known_tag_is_clean(store, own_taxonomy, monkeypatch, capsys):
    _memory(store, "a", "mem-a", "topic:alpha")

    rc, out, _err = _run(
        monkeypatch, capsys, "--path", str(store),
        "--memforge-root", str(own_taxonomy), "--strict",
    )

    assert rc == 0
    assert "Total violations: 0" in out
