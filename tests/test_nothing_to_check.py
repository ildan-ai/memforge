"""A run that looked at no memory folder must not read as a pass.

A new user who runs `memory-audit` or `memory-validate` inside their own store
folder, with no `--path` and no default folder on disk, used to get a zero
total and exit 0 for a store the tool never opened.
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from memforge.cli import audit, validate
from memforge.paths import MEMORY_PATH_ENV

MEMORY = "---\nname: one\ndescription: the only memory\ntype: reference\n---\n\nBody.\n"


@pytest.fixture
def store_without_defaults(tmp_path: Path, monkeypatch) -> Path:
    """cwd is a folder holding a memory; HOME is empty, so no default exists."""
    home = tmp_path / "home"
    store = tmp_path / "store"
    home.mkdir()
    store.mkdir()
    (store / "one.md").write_text(MEMORY, encoding="utf-8")
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("USERPROFILE", str(home))
    monkeypatch.delenv(MEMORY_PATH_ENV, raising=False)
    monkeypatch.chdir(store)
    return home


def _assert_says_nothing_was_checked(captured, home: Path) -> None:
    assert "Total integrity violations" not in captured.out
    assert "OK" not in captured.out + captured.err
    assert "nothing" in captured.err
    assert str(home / ".memforge" / "memory") in captured.err
    assert "--path" in captured.err


def test_audit_says_so_when_no_default_folder_exists(store_without_defaults, capsys):
    rc = audit.main([])

    assert rc == 0
    _assert_says_nothing_was_checked(capsys.readouterr(), store_without_defaults)


def test_strict_audit_fails_when_it_audited_nothing(store_without_defaults, capsys):
    rc = audit.main(["--strict"])

    assert rc != 0
    _assert_says_nothing_was_checked(capsys.readouterr(), store_without_defaults)


def test_audit_with_a_path_still_prints_its_total(store_without_defaults, capsys):
    rc = audit.main(["--path", "."])

    assert rc == 0
    assert "Total integrity violations across targets:" in capsys.readouterr().out


def test_validate_says_so_when_no_default_folder_exists(store_without_defaults, capsys):
    rc = validate.main([])

    assert rc == 0
    _assert_says_nothing_was_checked(capsys.readouterr(), store_without_defaults)


def test_strict_validate_fails_when_it_validated_nothing(store_without_defaults, capsys):
    rc = validate.main(["--strict"])

    assert rc != 0
    _assert_says_nothing_was_checked(capsys.readouterr(), store_without_defaults)


def test_validate_with_a_path_still_reports_its_files(store_without_defaults, capsys):
    rc = validate.main(["--path", "."])

    assert rc == 0
    assert "1 file(s)" in capsys.readouterr().err


# ---------- a --path that names a folder that is not there ----------


@pytest.fixture
def missing(tmp_path: Path) -> Path:
    return tmp_path / "no-such-folder"


def test_audit_of_only_a_missing_path_prints_no_total(store_without_defaults, missing, capsys):
    rc = audit.main(["--path", str(missing)])

    captured = capsys.readouterr()
    assert rc == 0
    assert "Total integrity violations" not in captured.out
    assert str(missing) in captured.err and "not audited" in captured.err


def test_strict_audit_fails_on_a_missing_path(store_without_defaults, missing, capsys):
    rc = audit.main(["--path", str(missing), "--strict"])

    captured = capsys.readouterr()
    assert rc != 0
    assert "Total integrity violations" not in captured.out
    assert str(missing) in captured.err


def test_audit_checks_the_folders_that_exist_beside_a_missing_one(
    store_without_defaults, missing, capsys
):
    rc = audit.main(["--path", ".", "--path", str(missing)])

    captured = capsys.readouterr()
    assert rc == 0
    assert "Files: 1" in captured.out
    assert "Total integrity violations across targets:" in captured.out
    assert str(missing) in captured.err


def test_strict_audit_fails_when_one_of_several_paths_is_missing(
    store_without_defaults, missing, capsys
):
    (Path.cwd() / "MEMORY.md").write_text("# Index\n\n- [one](one.md): x\n", encoding="utf-8")
    assert audit.main(["--path", ".", "--strict"]) == 0
    capsys.readouterr()

    rc = audit.main(["--path", ".", "--path", str(missing), "--strict"])

    captured = capsys.readouterr()
    assert rc != 0
    assert "Files: 1" in captured.out
    assert "Total integrity violations across targets: 0" in captured.out
    assert str(missing) in captured.err


def test_validate_of_only_a_missing_path_does_not_say_ok(store_without_defaults, missing, capsys):
    rc = validate.main(["--path", str(missing)])

    captured = capsys.readouterr()
    assert rc == 0
    assert "OK" not in captured.out + captured.err
    assert str(missing) in captured.err and "not validated" in captured.err


def test_strict_validate_fails_on_a_missing_path(store_without_defaults, missing, capsys):
    rc = validate.main(["--path", str(missing), "--strict"])

    captured = capsys.readouterr()
    assert rc != 0
    assert "OK" not in captured.out + captured.err
    assert str(missing) in captured.err


def test_validate_checks_the_folders_that_exist_beside_a_missing_one(
    store_without_defaults, missing, capsys
):
    rc = validate.main(["--path", ".", "--path", str(missing)])

    captured = capsys.readouterr()
    assert rc == 0
    assert "1 file(s)" in captured.err
    assert str(missing) in captured.err


def test_strict_validate_fails_when_one_of_several_paths_is_missing(
    store_without_defaults, missing, capsys
):
    (Path.cwd() / "one.md").write_text(
        "---\nname: one\ndescription: d\ntype: reference\nuid: mem-one\ntier: detail\n"
        "tags: [topic:x]\nowner: operator\nstatus: active\ncreated: 2026-01-01\n---\n\nBody.\n",
        encoding="utf-8",
    )
    assert validate.main(["--path", ".", "--strict"]) == 0
    capsys.readouterr()

    rc = validate.main(["--path", ".", "--path", str(missing), "--strict"])

    captured = capsys.readouterr()
    assert rc != 0
    assert "1 file(s) OK" in captured.err
    assert str(missing) in captured.err


# ---------- a path that exists but is not a folder ----------

COMPLETE = (
    "---\nname: one\ndescription: d\ntype: reference\nuid: mem-one\ntier: detail\n"
    "tags: [topic:x]\nowner: operator\nstatus: active\ncreated: 2026-01-01\n---\n\nBody.\n"
)


def test_validate_path_naming_a_regular_file_validates_that_file(
    store_without_defaults, capsys
):
    """`--path FILE` is not a skipped folder: the file itself is checked."""
    Path("one.md").write_text(COMPLETE, encoding="utf-8")
    Path("broken.md").write_text("---\nname: a: b: c\n---\nBody.\n", encoding="utf-8")

    assert validate.main(["--path", "one.md", "--strict"]) == 0
    assert "1 file(s) OK" in capsys.readouterr().err
    assert validate.main(["--path", "broken.md", "--strict"]) == 1
    assert "broken.md" in capsys.readouterr().err


def _fifo(path: Path) -> Path:
    try:
        os.mkfifo(path)
    except (OSError, NotImplementedError, AttributeError):
        pytest.skip("named pipes are not available on this platform")
    return path


def test_strict_validate_fails_on_a_path_that_is_neither_file_nor_folder(
    store_without_defaults, tmp_path, capsys
):
    pipe = _fifo(tmp_path / "pipe")

    rc = validate.main(["--path", str(pipe), "--strict"])

    captured = capsys.readouterr()
    assert rc != 0
    assert "OK" not in captured.out + captured.err
    assert str(pipe) in captured.err and "not validated" in captured.err


@pytest.mark.parametrize("strict, expected_rc", [((), 0), (("--strict",), 2)])
def test_validate_default_folder_that_is_a_file_is_not_validated_as_a_memory(
    store_without_defaults, capsys, strict, expected_rc
):
    """A default memory folder is a folder. A file sitting at that path is not
    a store, and reporting `1 file(s) OK` for it would read as a pass."""
    default = store_without_defaults / ".memforge" / "memory"
    default.parent.mkdir()
    default.write_text("not a folder\n", encoding="utf-8")

    rc = validate.main([*strict])

    captured = capsys.readouterr()
    assert rc == expected_rc
    assert "OK" not in captured.out + captured.err
    assert str(default) in captured.err and "not validated" in captured.err


@pytest.mark.parametrize("strict, expected_rc", [((), 0), (("--strict",), 2)])
def test_audit_path_naming_a_regular_file_is_not_audited(
    store_without_defaults, capsys, strict, expected_rc
):
    rc = audit.main(["--path", "one.md", *strict])

    captured = capsys.readouterr()
    assert rc == expected_rc
    assert "Total integrity violations" not in captured.out
    assert "one.md" in captured.err and "not audited" in captured.err


@pytest.mark.parametrize("strict, expected_rc", [((), 0), (("--strict",), 2)])
def test_audit_default_folder_that_is_a_file_is_not_audited(
    store_without_defaults, capsys, strict, expected_rc
):
    default = store_without_defaults / ".memforge" / "memory"
    default.parent.mkdir()
    default.write_text("not a folder\n", encoding="utf-8")

    rc = audit.main([*strict])

    captured = capsys.readouterr()
    assert rc == expected_rc
    assert "Total integrity violations" not in captured.out
    assert str(default) in captured.err and "not audited" in captured.err
