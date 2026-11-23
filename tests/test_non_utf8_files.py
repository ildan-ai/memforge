"""A memory file that is not valid UTF-8 is reported, never skipped or a crash.

One byte that is not UTF-8 must not hide a file from a check that reports
clean, and must not stop the check for every other file.
"""

from __future__ import annotations

import contextlib
import io
import json
from pathlib import Path

import pytest

from memforge.cli import audit_deep, validate

BAD = b"---\nname: bad\ntype: user\ndescription: d\n---\nbad \xff byte\n"
GOOD = "---\nname: good\ntype: user\ndescription: d\n---\nok\n"


@pytest.fixture
def folder(tmp_path: Path) -> Path:
    path = tmp_path / "memory"
    path.mkdir()
    (path / "bad.md").write_bytes(BAD)
    (path / "good.md").write_text(GOOD, encoding="utf-8")
    return path


def test_audit_deep_reports_a_file_that_is_not_utf8(folder: Path):
    violations = audit_deep.audit(folder, {}, 90)

    assert any(v.startswith("bad.md: cannot read file") for v in violations)


def test_validate_reports_a_file_that_is_not_utf8_and_checks_the_rest(folder: Path):
    out = io.StringIO()
    with contextlib.redirect_stdout(out):
        rc = validate.main(["--path", str(folder), "--json"])

    report = json.loads(out.getvalue())
    assert rc == 1
    assert report["files_checked"] == 2
    codes = {(Path(f["file"]).name, f["code"]) for f in report["findings"]}
    assert ("bad.md", "read_error") in codes


def test_audit_deep_reports_a_file_that_cannot_be_read_again_for_links(tmp_path: Path, monkeypatch):
    # The mem: link check reads each file a second time; a file that cannot be
    # read then must not drop out of the check while the audit reports clean.
    folder = tmp_path / "memory"
    folder.mkdir()
    (folder / "good.md").write_text(GOOD, encoding="utf-8")
    real = audit_deep.read_text_no_follow
    calls = {"n": 0}

    def second_read_fails(path, *args, **kwargs):
        calls["n"] += 1
        if calls["n"] > 1:
            raise OSError("refused")
        return real(path, *args, **kwargs)

    monkeypatch.setattr(audit_deep, "read_text_no_follow", second_read_fails)

    violations = audit_deep.audit(folder, {}, 90)

    assert calls["n"] == 2
    assert any(v.startswith("good.md: cannot read file") for v in violations)
