"""Tests for the v0.6 recall-field + always-set + relative-date audit gaps.

These are deterministic WARN-only checks (health, never integrity violations),
so they must NOT change the --strict exit code.
"""

from __future__ import annotations

import io
from contextlib import redirect_stdout
from pathlib import Path

from memforge.cli import audit


def _w(folder: Path, name: str, fm: str, body: str = "body") -> None:
    (folder / name).write_text("---\n" + fm + "\n---\n\n" + body + "\n", encoding="utf-8")


def _run(folder: Path, **kw):
    buf = io.StringIO()
    with redirect_stdout(buf):
        nv, blob = audit.audit_target(
            folder, stale_days=999999, fix=False,
            add_defaults=False, json_out=True, **kw)
    return nv, blob


# ---------- recall-field malformed WARNs ----------


def test_triggers_malformed_warns():
    assert audit._recall_field_warnings("a.md", {"triggers": "oauth"})
    assert audit._recall_field_warnings("a.md", {"triggers": [1, 2]})
    assert not audit._recall_field_warnings("a.md", {"triggers": ["ok", "fine"]})


def test_always_and_do_not_inject_malformed_warn():
    assert audit._recall_field_warnings("a.md", {"always": "soon"})
    assert audit._recall_field_warnings("a.md", {"do_not_inject": 3})
    # genuine booleans are clean
    assert not audit._recall_field_warnings("a.md", {"always": True, "do_not_inject": False})


def test_malformed_fields_are_health_not_violations(tmp_path: Path):
    _w(tmp_path, "x.md", "name: X\ndescription: a clear distinctive description\n"
       "type: user\nstatus: active\ntriggers: notalist")
    (tmp_path / "MEMORY.md").write_text("# I\n\n- [X](x.md) , hook\n", encoding="utf-8")
    nv, blob = _run(tmp_path)
    assert nv == 0  # WARN does not count as an integrity violation
    assert any("triggers_malformed" in h for h in blob["health"])


# ---------- relative-date heuristic (type:project only) ----------


def test_relative_date_only_for_project():
    assert audit._relative_date_warning("p.md", "project", "we ship next week")
    # same body, non-project type -> no warning
    assert audit._relative_date_warning("f.md", "feedback", "we ship next week") is None


def test_relative_date_high_precision():
    # legitimate rolling phrases must not trip the heuristic
    assert audit._relative_date_warning("p.md", "project", "checkout the current branch") is None
    assert audit._relative_date_warning("p.md", "project", "the Q3 roadmap is set") is None
    # genuine relative dates do
    assert audit._relative_date_warning("p.md", "project", "shipped yesterday")
    assert audit._relative_date_warning("p.md", "project", "revisit in two weeks ago context")


def test_relative_date_this_period_not_flagged():
    """Regression for recall-02.

    'this <period>' is a legitimate rolling reference (not an aging absolute
    date) and the comment claims the heuristic avoids it, but the old regex
    matched it. 'last'/'next' (unambiguous offsets) still flag.
    """
    assert audit._relative_date_warning("p.md", "project", "this quarter we close the round") is None
    assert audit._relative_date_warning("p.md", "project", "review this week's metrics") is None
    assert audit._relative_date_warning("p.md", "project", "this month's goal") is None
    # last/next still flagged
    assert audit._relative_date_warning("p.md", "project", "we ship next week")
    assert audit._relative_date_warning("p.md", "project", "merged last month")


# ---------- always-set budget (advisory) ----------


def test_always_set_count_warn(tmp_path: Path):
    for i in range(10):
        _w(tmp_path, f"a{i}.md", f"name: A{i}\ndescription: distinctive alpha "
           f"entry number {i}\ntype: user\nstatus: active\nalways: true")
    ptrs = "".join(f"- [A{i}](a{i}.md) , h\n" for i in range(10))
    (tmp_path / "MEMORY.md").write_text("# I\n\n" + ptrs, encoding="utf-8")
    nv, blob = _run(tmp_path, max_always_count=8, max_always_description_chars=100000)
    assert nv == 0
    assert any("always-set has 10 live memories" in h for h in blob["health"])


def test_always_set_excludes_superseded(tmp_path: Path):
    _w(tmp_path, "live.md", "name: Live\ndescription: live always entry\n"
       "type: user\nstatus: active\nalways: true")
    _w(tmp_path, "dead.md", "name: Dead\ndescription: dead always entry\n"
       "type: user\nstatus: superseded\nsuperseded_by: [mem-x]\nalways: true")
    (tmp_path / "MEMORY.md").write_text(
        "# I\n\n- [Live](live.md) , h\n- [Dead](dead.md) , h\n", encoding="utf-8")
    nv, blob = _run(tmp_path, max_always_count=0, max_always_description_chars=100000)
    # only the live one counts -> "1 live memories"
    assert any("always-set has 1 live memories" in h for h in blob["health"])


def test_always_set_char_budget_warn(tmp_path: Path):
    _w(tmp_path, "a.md", "name: A\ndescription: " + ("z" * 700) +
       "\ntype: user\nstatus: active\nalways: true")
    (tmp_path / "MEMORY.md").write_text("# I\n\n- [A](a.md) , h\n", encoding="utf-8")
    nv, blob = _run(tmp_path, max_always_count=100, max_always_description_chars=600)
    assert any("always-set descriptions total" in h for h in blob["health"])
