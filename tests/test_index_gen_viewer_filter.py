"""What `memory-index-gen --viewer-tier / --viewer-teams` filters on.

These pin the behavior the spec describes, so a change to it is a decision
and not an accident:

- The viewer filter reads `access` only. spec/SPEC.md, "Access labels": "The
  `access` field (v0.3.0+) is independent of `sensitivity`."
- A memory with no `access` label is `internal`. Same section: "`internal`
  (default) , workspace members".
- Every live competing claim is surfaced, and a viewer filter does not hide
  the conflict. spec/SPEC.md, "Reader-side competing-claim contract": a
  generator run with a viewer filter still lists every live member, and shows
  only the uid and a restricted marker for a member the viewer may not read.
"""

from __future__ import annotations

import contextlib
import io
from pathlib import Path

import pytest

from memforge.cli import index_gen


def _memory(folder: Path, name: str, extra: str = "", body: str = "Body.") -> None:
    (folder / f"{name}.md").write_text(
        f"---\nname: {name}\ndescription: about {name}\ntype: reference\n"
        f"uid: mem-{name}\nstatus: active\n{extra}---\n\n{body}\n",
        encoding="utf-8",
    )


def _index(folder: Path, viewer_tier=None, viewer_teams=()) -> str:
    """The index `memory-index-gen --print` writes for this viewer."""
    out = io.StringIO()
    with contextlib.redirect_stdout(out):
        rc = index_gen.process(folder, "print", viewer_tier, list(viewer_teams))
    assert rc == 0
    return out.getvalue()


def _claims(index: str) -> str:
    return index.partition("## Competing claims")[2]


def test_viewer_tier_filters_on_access_not_on_sensitivity(tmp_path: Path):
    _memory(tmp_path, "labelled", "sensitivity: restricted\naccess: restricted\n")
    _memory(tmp_path, "unlabelled", "sensitivity: restricted\n")

    index = _index(tmp_path, viewer_tier="internal")

    assert "(labelled.md)" not in index
    assert "(unlabelled.md)" in index


def test_memory_without_access_is_internal_for_a_tier_viewer(tmp_path: Path):
    _memory(tmp_path, "unlabelled")

    assert "(unlabelled.md)" not in _index(tmp_path, viewer_tier="public")
    assert "(unlabelled.md)" in _index(tmp_path, viewer_tier="internal")


def test_team_only_viewer_sees_unlabelled_and_own_team_memories(tmp_path: Path):
    _memory(tmp_path, "unlabelled")
    _memory(tmp_path, "mine", "access: team:security\n")
    _memory(tmp_path, "theirs", "access: team:platform\n")

    index = _index(tmp_path, viewer_teams=["team:security"])

    assert "(unlabelled.md)" in index
    assert "(mine.md)" in index
    assert "(theirs.md)" not in index


CLOSED_EXTRA = (
    "decision_topic: the-topic\n"
    "owner: closed-owner\n"
    "updated: 2031-07-09\n"
    "sensitivity: privileged\n"
)


@pytest.fixture
def conflict(tmp_path: Path) -> Path:
    """Two live claims on one topic: one public, one restricted to a team."""
    _memory(tmp_path, "open", "decision_topic: the-topic\naccess: public\n", "Open claim.")
    _memory(
        tmp_path, "closed",
        CLOSED_EXTRA + "access: [restricted, team:security]\n", "Closed claim.",
    )
    return tmp_path


def _assert_closed_member_is_redacted(index: str) -> None:
    claims = _claims(index)
    assert "    - uid: mem-closed\n      restricted: true\n" in claims
    for leaked in ("Closed claim", "closed.md", "closed-owner", "2031-07-09", "privileged"):
        assert leaked not in index


def test_restricted_member_shows_only_its_uid_to_a_lower_tier_viewer(conflict: Path):
    index = _index(conflict, viewer_tier="public")

    _assert_closed_member_is_redacted(index)
    assert "- decision_topic: the-topic\n  state: competing\n  members:\n" in _claims(index)
    assert "      first_line: Open claim.\n      file_path: open.md\n" in _claims(index)
    assert _claims(index).count("    - uid: ") == 2


def test_restricted_member_shows_only_its_uid_to_a_viewer_outside_its_team(conflict: Path):
    index = _index(conflict, viewer_tier="restricted", viewer_teams=["team:platform"])

    _assert_closed_member_is_redacted(index)


def test_member_the_viewer_may_read_is_shown_in_full(conflict: Path):
    claims = _claims(_index(conflict, viewer_tier="restricted", viewer_teams=["team:security"]))

    assert "restricted: true" not in claims
    assert "      owner: closed-owner\n" in claims
    assert "      first_line: Closed claim.\n      file_path: closed.md\n" in claims


def test_group_stays_visible_when_the_viewer_may_read_no_member(conflict: Path):
    _memory(conflict, "open", "decision_topic: the-topic\naccess: restricted\n", "Open claim.")

    claims = _claims(_index(conflict, viewer_tier="public"))

    assert "- decision_topic: the-topic\n" in claims
    assert claims.count("      restricted: true\n") == 2
    assert "first_line" not in claims and "file_path" not in claims and "owner" not in claims


@pytest.mark.parametrize("status", ["[active]", "{k: v}"])
def test_claim_member_whose_status_is_not_text_is_not_live(conflict: Path, status: str):
    _memory(conflict, "odd", f"decision_topic: the-topic\nstatus: {status}\n")

    claims = _claims(_index(conflict))

    assert claims.count("    - uid: ") == 2
    assert "mem-odd" not in claims


def test_claims_block_is_unchanged_without_a_viewer_filter(conflict: Path):
    claims = _claims(_index(conflict))

    assert "restricted: true" not in claims
    assert (
        "    - uid: mem-closed\n"
        "      owner: closed-owner\n"
        "      status: active\n"
        "      updated: '2031-07-09'\n"
        "      first_line: Closed claim.\n"
        "      file_path: closed.md\n"
    ) in claims


def test_redacted_block_is_still_one_parseable_yaml_list(conflict: Path):
    import yaml

    _memory(
        conflict, "open",
        "decision_topic: the-topic\naccess: public\nupdated: 2020-01-01\n", "Open claim.",
    )
    block = _claims(_index(conflict, viewer_tier="public")).split("```yaml\n")[1].split("```")[0]

    groups = yaml.safe_load(block)

    # The restricted member is the newer one, yet it sorts as if undated: its
    # `updated` is not used, so the order tells the viewer nothing about it.
    assert [sorted(m) for m in groups[0]["members"]] == [
        ["file_path", "first_line", "owner", "status", "uid", "updated"],
        ["restricted", "uid"],
    ]
    assert groups[0]["members"][1] == {"uid": "mem-closed", "restricted": True}
