"""Tools that walk a memory folder: links are not followed, and a wrongly
typed field is a finding, not a crash."""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from memforge.cli import _concurrency_audit, audit_deep, detect, promote

MEMORY = "---\nname: {name}\ndescription: d\ntype: feedback\nuid: mem-{name}\nstatus: active\n---\nsecret body of {name}\n"


@pytest.fixture
def linked(tmp_path: Path):
    """A memory folder holding one real file and one link to a file outside it."""
    if not hasattr(os, "symlink"):
        pytest.skip("no symlinks on this platform")
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "private.md").write_text(MEMORY.format(name="private"), encoding="utf-8")
    folder = tmp_path / "store"
    folder.mkdir()
    (folder / "real.md").write_text(MEMORY.format(name="real"), encoding="utf-8")
    try:
        os.symlink(outside / "private.md", folder / "innocent.md")
        os.symlink(outside, folder / "linkdir", target_is_directory=True)
    except OSError:
        pytest.skip("cannot create symlinks here")
    return folder


def test_concurrency_audit_does_not_read_linked_files(linked: Path):
    _groups, by_uid = _concurrency_audit.collect_state(linked)
    assert set(by_uid) == {"mem-real"}


def test_audit_deep_does_not_read_linked_files(linked: Path):
    names = {r.path.name for r in audit_deep.discover_files(linked)}
    assert names == {"real.md"}


def test_detect_rule_catalog_does_not_read_linked_files(linked: Path):
    catalog = "\n".join(detect._build_rule_catalog([linked]))
    assert "private" not in catalog


def test_promote_link_scan_does_not_read_linked_files(linked: Path, monkeypatch):
    opened: list[str] = []
    real_read = Path.read_text

    def spy(self, *a, **k):
        opened.append(self.name)
        return real_read(self, *a, **k)

    monkeypatch.setattr(Path, "read_text", spy)
    promote._scan_inbound_path_links(linked, linked / "real.md")
    assert "innocent.md" not in opened and "private.md" not in opened


@pytest.mark.parametrize("field", ["status", "uid", "decision_topic"])
def test_concurrency_audit_reports_a_list_valued_field_instead_of_raising(tmp_path: Path, field: str):
    good = MEMORY.format(name="good").replace("status: active", "status: active\ndecision_topic: pick-a-db")
    (tmp_path / "good.md").write_text(good, encoding="utf-8")
    bad = MEMORY.format(name="bad").replace("status: active", "status: active\ndecision_topic: pick-a-db")
    key = {"status": "status: active", "uid": "uid: mem-bad", "decision_topic": "decision_topic: pick-a-db"}[field]
    bad = bad.replace(key, f"{field}: [x]")
    (tmp_path / "bad.md").write_text(bad, encoding="utf-8")

    blockers, _majors, _warns = _concurrency_audit.run_concurrency_audit(tmp_path)

    assert any("bad.md" in msg and field in msg for _, msg in blockers)
