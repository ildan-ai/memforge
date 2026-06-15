"""Tests for memory-query --text scope (query-text-01).

--text now matches name + description + body by default, so a term living only
in metadata is found. --in narrows the scope, and matches specific fields (not
raw YAML), so --text active does not hit status: active.
"""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

from memforge.cli import query


def _hit(name: str, desc: str, body: str, status: str = "active") -> query.Hit:
    fm = {"name": name, "description": desc, "status": status, "type": "feedback"}
    return query.Hit(path=Path("x.md"), folder=Path("."), rel="x.md", fm=fm, body=body)


def _args(text=None, text_in=None):
    base = dict(
        topic=None, tag=None, type=None, status=None, pinned=False, owner=None,
        tier=None, sensitivity=None, last_reviewed_before=None,
        last_reviewed_after=None, updated_within_days=None, last_d=None,
        text=text, text_in=text_in,
    )
    return SimpleNamespace(**base)


def test_text_matches_name_by_default():
    """query-text-01: a term in the NAME only (not the body) is now found."""
    h = _hit("Cognito auth rule", "an auth note", "body has no such word")
    assert query.matches(h, _args(text="cognito")) is True


def test_text_matches_description_by_default():
    h = _hit("Some rule", "uses the kerberos protocol", "body unrelated")
    assert query.matches(h, _args(text="kerberos")) is True


def test_text_still_matches_body():
    h = _hit("rule", "desc", "the body mentions widgets")
    assert query.matches(h, _args(text="widgets")) is True


def test_in_body_restores_body_only():
    h = _hit("Cognito rule", "desc", "no match here")
    # Term in name but --in body -> should NOT match.
    assert query.matches(h, _args(text="cognito", text_in="body")) is False


def test_text_does_not_hit_raw_frontmatter_status():
    """--text active must not match status: active (it matches fields, not YAML)."""
    h = _hit("rule", "desc", "body", status="active")
    assert query.matches(h, _args(text="active")) is False
