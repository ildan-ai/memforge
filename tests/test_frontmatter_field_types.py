"""A frontmatter field of the wrong YAML type is a per-file finding.

`sensitivity: [restricted]` parses as a list. audit, validate and lint look
such fields up in sets and dicts, so an unhashable value used to abort the
whole run and hide every other file. Each tool now names the file and the
field and carries on.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from memforge.cli import audit, link_rewriter, lint, validate

GOOD = (
    "---\n"
    "name: good\n"
    "description: a well-formed neighbour with enough words to describe it\n"
    "type: reference\n"
    "uid: mem-good\n"
    "tier: detail\n"
    "tags: [topic:x]\n"
    "owner: operator\n"
    "status: active\n"
    "created: 2026-01-01\n"
    "sensitivity: internal\n"
    "---\n\n"
    "Body.\n"
)

FIELDS = (
    "name", "description", "type", "sensitivity", "tier", "status", "uid",
    "owner", "tags", "access", "created", "updated", "last_reviewed",
    "decision_topic", "triggers", "always", "do_not_inject", "pinned",
    "aliases", "supersedes", "superseded_by", "replaces", "metadata",
)
WRONG_VALUES = ("[restricted]", "{k: v}", "[[nested]]", "42", "true", "~")

# Fields each tool reads as one text value.
TEXT_FIELDS = ("type", "sensitivity", "tier", "status")


def _hostile(field: str, value: str) -> str:
    kept = [line for line in GOOD.splitlines() if not line.startswith(f"{field}:")]
    kept.insert(1, f"{field}: {value}")
    return "\n".join(kept) + "\n"


def _folder(tmp_path: Path, field: str, value: str) -> Path:
    (tmp_path / "good.md").write_text(GOOD, encoding="utf-8")
    (tmp_path / "hostile.md").write_text(_hostile(field, value), encoding="utf-8")
    (tmp_path / "MEMORY.md").write_text(
        "# Index\n\n- [good](good.md): x\n- [hostile](hostile.md): y\n", encoding="utf-8"
    )
    return tmp_path


def _audit(folder: Path) -> list[str]:
    _count, blob = audit.audit_target(
        folder, stale_days=90, fix=False, add_defaults=False, json_out=True
    )
    assert blob["file_count"] == 2
    return blob["violations"]


@pytest.mark.parametrize("value", WRONG_VALUES)
@pytest.mark.parametrize("field", FIELDS)
def test_no_tool_aborts_on_a_wrongly_typed_field(tmp_path, capsys, field, value):
    folder = _folder(tmp_path, field, value)

    _audit(folder)
    findings = validate.validate_file(folder / "hostile.md")
    report = lint.lint_folder(
        folder, injected_texts=[], allow_cloud=False, allow_cloud_body=False,
        dispatcher=None, min_score=2,
    )

    assert isinstance(findings, list)
    assert "good.md" in {r["path"] for r in report["records"]}


@pytest.mark.parametrize("value", ["[restricted]", "{k: v}", "42"])
@pytest.mark.parametrize("field", TEXT_FIELDS)
def test_audit_reports_the_field_and_still_audits_the_neighbour(tmp_path, capsys, field, value):
    violations = _audit(_folder(tmp_path, field, value))

    assert f"hostile.md: field '{field}' must be text" in violations
    assert not [v for v in violations if v.startswith("good.md")]


def test_audit_export_gate_blocks_a_file_whose_sensitivity_is_not_text(tmp_path, capsys):
    """An unreadable sensitivity must not slip under the export-tier ceiling."""
    folder = _folder(tmp_path, "sensitivity", "[privileged]")

    count, _blob = audit.audit_target(
        folder, stale_days=90, fix=False, add_defaults=False, json_out=True,
        export_tier="public",
    )

    out = capsys.readouterr().out
    assert count >= 1
    assert "hostile.md: field 'sensitivity' must be text" in out


def test_audit_fails_when_the_concurrency_pass_cannot_run(tmp_path, capsys, monkeypatch):
    """That pass holds the competing-claim blockers; losing it is not a pass."""
    from memforge.cli import _concurrency_audit

    def broken(*_args, **_kwargs):
        raise RuntimeError("boom")

    monkeypatch.setattr(_concurrency_audit, "run_concurrency_audit", broken)
    violations = _audit(_folder(tmp_path, "tier", "index"))

    assert [v for v in violations if "concurrency audit" in v and "did not run" in v]


@pytest.mark.parametrize("field", ["uid", "decision_topic"])
def test_audit_names_a_wrongly_typed_identity_field(tmp_path, capsys, field):
    violations = _audit(_folder(tmp_path, field, "[x]"))

    assert [v for v in violations if f"hostile.md: field '{field}' must be text" in v]
    assert not [v for v in violations if "did not run" in v]


@pytest.mark.parametrize("value", ["[index]", "{k: v}", "42"])
@pytest.mark.parametrize("field", ["tier", "status"])
def test_validate_reports_the_field(tmp_path, field, value):
    folder = _folder(tmp_path, field, value)

    findings = validate.validate_file(folder / "hostile.md")

    assert [f.message for f in findings if f.code == "bad_field_type"] == [
        f"field '{field}' must be text"
    ]
    assert validate.validate_file(folder / "good.md") == []


@pytest.mark.parametrize("value", ["[mem-good]", "{k: v}"])
def test_link_check_indexes_the_folder_when_a_uid_is_not_text(tmp_path, capsys, value):
    folder = _folder(tmp_path, "uid", value)

    idx = link_rewriter.index_folder(folder)

    assert sorted(idx.by_uid) == ["mem-good"]
    assert link_rewriter.cmd_check(idx) == 0


@pytest.mark.parametrize("value", ["[active]", "{k: v}"])
def test_lint_reports_the_file_and_still_lints_the_neighbour(tmp_path, value):
    folder = _folder(tmp_path, "status", value)

    report = lint.lint_folder(
        folder, injected_texts=[], allow_cloud=False, allow_cloud_body=False,
        dispatcher=None, min_score=2,
    )

    by_path = {r["path"]: r for r in report["records"]}
    assert "good.md" in by_path
    assert [f["evidence"] for f in by_path["hostile.md"]["findings"]
            if f["code"] == "field_not_text"] == ["field 'status' must be text"]
