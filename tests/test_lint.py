"""Tests for memory-lint (recall-readiness + token-cost + cloud-safety).

Covers the design-panel-mandated behaviors: collision-based recall scoring,
local-only-by-default dispatch, metadata-only payload, fail-closed secret
prescan, read-only guarantee, and CLI guards.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from memforge.cli import lint


def _w(folder: Path, name: str, fm: str, body: str = "Some body content here.") -> None:
    (folder / name).write_text("---\n" + fm + "\n---\n\n" + body + "\n", encoding="utf-8")


@pytest.fixture
def corpus(tmp_path: Path) -> Path:
    # A small but varied corpus. Distinctive terms (pkce, cognito, quokka) occur
    # once; generic terms recur so collision scoring has something to bite on.
    _w(tmp_path, "feedback_oauth.md",
       "name: Login rule\ndescription: Prefer PKCE authorization-code flow for "
       "Cognito user pools\ntype: feedback\nstatus: active\ntags: [topic:auth]")
    _w(tmp_path, "project_notes.md",
       "name: Session notes\ndescription: Notes\ntype: project\nstatus: active")
    _w(tmp_path, "user_redundant.md",
       "name: Quokka migration runbook\ndescription: Quokka migration runbook\n"
       "type: reference\nstatus: active")
    _w(tmp_path, "user_superseded.md",
       "name: Old cognito thing\ndescription: superseded cognito note\n"
       "type: reference\nstatus: superseded\nsuperseded_by: [mem-x]")
    return tmp_path


# ---------- recall-context scoring ----------


def test_strong_description_scores_high(corpus: Path):
    rep = lint.lint_folder(corpus, injected_texts=[], allow_cloud=False,
                           allow_cloud_body=False, dispatcher=None, min_score=3)
    by_path = {r["path"]: r for r in rep["records"]}
    assert by_path["feedback_oauth.md"]["recall_context"]["score"] == 5
    assert by_path["feedback_oauth.md"]["recall_context"]["finding"] == "recall_strong"


def test_generic_description_flagged(corpus: Path):
    rep = lint.lint_folder(corpus, injected_texts=[], allow_cloud=False,
                           allow_cloud_body=False, dispatcher=None, min_score=3)
    by_path = {r["path"]: r for r in rep["records"]}
    assert by_path["project_notes.md"]["recall_context"]["finding"] == "description_generic"
    assert by_path["project_notes.md"]["recall_context"]["score"] == 1


def test_redundant_description_flagged(corpus: Path):
    rep = lint.lint_folder(corpus, injected_texts=[], allow_cloud=False,
                           allow_cloud_body=False, dispatcher=None, min_score=3)
    by_path = {r["path"]: r for r in rep["records"]}
    assert by_path["user_redundant.md"]["recall_context"]["finding"] == \
        "description_adds_no_distinct_term"


def test_superseded_excluded(corpus: Path):
    rep = lint.lint_folder(corpus, injected_texts=[], allow_cloud=False,
                           allow_cloud_body=False, dispatcher=None, min_score=3)
    paths = {r["path"] for r in rep["records"]}
    assert "user_superseded.md" not in paths
    assert rep["entries"] == 3


def test_missing_description_scores_zero(tmp_path: Path):
    _w(tmp_path, "x.md", "name: A thing\ntype: user\nstatus: active", body="b")
    rep = lint.lint_folder(tmp_path, injected_texts=[], allow_cloud=False,
                           allow_cloud_body=False, dispatcher=None, min_score=3)
    assert rep["records"][0]["recall_context"]["finding"] == "description_missing"


# ---------- token-cost findings ----------


def test_description_too_long_flagged(tmp_path: Path):
    _w(tmp_path, "x.md", "name: Big\ndescription: " + ("word " * 60) +
       "\ntype: user\nstatus: active")
    rep = lint.lint_folder(tmp_path, injected_texts=[], allow_cloud=False,
                           allow_cloud_body=False, dispatcher=None, min_score=3)
    codes = [f["code"] for f in rep["records"][0]["findings"]]
    assert "description_too_long" in codes


def test_always_with_weak_recall_finding():
    # Unit-test the finding logic directly: always:true + a weak recall score.
    weak = lint.token_cost_findings({"always": True, "description": "x"}, "b", 2)
    assert "always_with_weak_recall" in [f["code"] for f in weak]
    strong = lint.token_cost_findings({"always": True, "description": "x"}, "b", 5)
    assert "always_with_weak_recall" not in [f["code"] for f in strong]


def test_all_terms_common_scores_two(tmp_path: Path):
    # A corpus of identical memories: every token is high-collision, so no
    # memory has a distinctive term and each scores 2 (all_terms_common).
    for i in range(8):
        _w(tmp_path, f"m{i}.md", "name: Routine standard common entry\n"
           "description: routine standard common shared boilerplate entry\n"
           "type: user\nstatus: active")
    rep = lint.lint_folder(tmp_path, injected_texts=[], allow_cloud=False,
                           allow_cloud_body=False, dispatcher=None, min_score=3)
    assert all(r["recall_context"]["finding"] == "all_terms_common"
               for r in rep["records"])
    assert all(r["recall_context"]["score"] == 2 for r in rep["records"])


def test_injected_file_suggestion_only_with_manifest(tmp_path: Path):
    _w(tmp_path, "x.md", "name: No em-dashes in prose\ndescription: avoid em "
       "dashes when drafting\ntype: feedback\nstatus: active\nalways: true")
    # without manifest: no finding
    rep = lint.lint_folder(tmp_path, injected_texts=[], allow_cloud=False,
                           allow_cloud_body=False, dispatcher=None, min_score=3)
    assert not any(f["code"] == "possible_duplicate_of_injected"
                   for f in rep["records"][0]["findings"])
    # with manifest naming the rule: finding appears
    rep = lint.lint_folder(tmp_path, injected_texts=["... No em-dashes in prose ..."],
                           allow_cloud=False, allow_cloud_body=False,
                           dispatcher=None, min_score=3)
    assert any(f["code"] == "possible_duplicate_of_injected"
               for f in rep["records"][0]["findings"])


# ---------- cloud-safety (the panel's top BLOCKER) ----------


def test_secret_prescan_detects_and_clears():
    assert lint.secret_prescan("key AKIA1234567890ABCDEF here")
    assert lint.secret_prescan("password: hunter2")
    assert lint.secret_prescan("-----BEGIN RSA PRIVATE KEY-----")
    assert lint.secret_prescan("a normal sentence about auth and config") is None


def test_secret_prescan_entropy_gates_broad_rules():
    """lint-prescan-01: benign long tokens (git anchors, long CamelCase names)
    no longer trip the broad base64/hex rules, so safe memories keep getting
    suggestions. A genuinely high-entropy token still fires."""
    # Low-entropy 40-char hex git anchor -> no longer a false positive.
    assert lint.secret_prescan(
        "git anchor a1b2c3d4e5f6a7b8c9d0e1f2a3b4c5d6e7f8a9b0"
    ) is None
    # A long English CamelCase name -> not flagged.
    assert lint.secret_prescan(
        "name: ConfigurationManagementSystemArchitectureDocument"
    ) is None
    # A genuinely high-entropy 40-char base64 token still fires.
    assert lint.secret_prescan("aB3xK9mP2qZ7wL4nR8tY6vB1jH5gF0dS8aQwErTy") is not None


def test_no_dispatcher_means_no_llm(tmp_path: Path):
    _w(tmp_path, "x.md", "name: T\ndescription: Notes\ntype: user\nstatus: active")
    rep = lint.lint_folder(tmp_path, injected_texts=[], allow_cloud=False,
                           allow_cloud_body=False, dispatcher=None, min_score=3)
    assert "llm" not in rep["records"][0]


def test_cloud_disabled_by_default(tmp_path: Path):
    _w(tmp_path, "x.md", "name: T\ndescription: Notes\ntype: user\nstatus: active")
    rep = lint.lint_folder(tmp_path, injected_texts=[], allow_cloud=False,
                           allow_cloud_body=False, dispatcher="curl https://api.example.com",
                           min_score=3)
    assert rep["records"][0]["llm"]["skipped"] == "cloud_disabled"


def test_secret_in_body_blocks_dispatch(tmp_path: Path):
    _w(tmp_path, "x.md", "name: T\ndescription: Notes\ntype: user\nstatus: active",
       body="leak AKIA1234567890ABCDEF in body")
    rep = lint.lint_folder(tmp_path, injected_texts=[], allow_cloud=True,
                           allow_cloud_body=True, dispatcher="echo '{}'", min_score=3)
    assert rep["records"][0]["llm"]["skipped"] == "secret_prescan_blocked"


# ---------- sensitivity/access cloud-egress gate (lint-sensitivity-01) ----------


def test_restricted_memory_never_dispatched_to_cloud(tmp_path: Path):
    """lint-sensitivity-01 BLOCKER: a restricted memory with NO secret pattern
    must NOT reach a cloud dispatcher even with --allow-cloud --allow-cloud-body.
    The secret pre-scan is a net for credential SHAPES only; the sensitivity gate
    is the normative egress posture. The dispatcher below would echo a valid
    suggestion if it ran, so the skip reason proves it never ran."""
    _w(tmp_path, "x.md",
       "name: T\ndescription: Notes\ntype: user\nstatus: active\n"
       "sensitivity: restricted")
    rep = lint.lint_folder(tmp_path, injected_texts=[], allow_cloud=True,
                           allow_cloud_body=True,
                           dispatcher="echo '{\"suggested_description\":\"LEAKED\"}'",
                           min_score=3)
    llm = rep["records"][0]["llm"]
    assert llm == {"skipped": "sensitivity_above_ceiling"}
    assert "suggested_description" not in llm


def test_privileged_memory_never_dispatched_to_cloud(tmp_path: Path):
    _w(tmp_path, "x.md",
       "name: T\ndescription: Notes\ntype: user\nstatus: active\n"
       "sensitivity: privileged")
    rep = lint.lint_folder(tmp_path, injected_texts=[], allow_cloud=True,
                           allow_cloud_body=True,
                           dispatcher="echo '{\"suggested_description\":\"LEAKED\"}'",
                           min_score=3)
    assert rep["records"][0]["llm"] == {"skipped": "sensitivity_above_ceiling"}


def test_access_restricted_memory_never_dispatched_to_cloud(tmp_path: Path):
    """A memory with a restricting access label (access: [counsel]) is excluded
    from cloud egress even at internal sensitivity (mirrors recall._access_ok
    fail-closed posture)."""
    _w(tmp_path, "x.md",
       "name: T\ndescription: Notes\ntype: user\nstatus: active\n"
       "sensitivity: internal\naccess: [counsel]")
    rep = lint.lint_folder(tmp_path, injected_texts=[], allow_cloud=True,
                           allow_cloud_body=True,
                           dispatcher="echo '{\"suggested_description\":\"LEAKED\"}'",
                           min_score=3)
    assert rep["records"][0]["llm"] == {"skipped": "access_restricted"}


def test_restricted_memory_still_dispatched_to_LOCAL(tmp_path: Path, monkeypatch):
    """The egress gate is CLOUD-only: a local model never leaves the box, so a
    restricted memory may still get a local suggestion. is_local_dispatcher is
    forced True so we exercise the local branch without a real local runner."""
    monkeypatch.setattr(lint, "is_local_dispatcher", lambda d: True)
    _w(tmp_path, "x.md",
       "name: T\ndescription: Notes\ntype: user\nstatus: active\n"
       "sensitivity: restricted")
    rep = lint.lint_folder(tmp_path, injected_texts=[], allow_cloud=False,
                           allow_cloud_body=False,
                           dispatcher="echo '{\"suggested_description\":\"ok\"}'",
                           min_score=3)
    assert rep["records"][0]["llm"].get("suggested_description") == "ok"


def test_public_internal_memory_still_cloud_eligible(tmp_path: Path):
    """Regression guard: a public/internal memory with no secret still dispatches
    to the cloud (the gate must not over-block)."""
    _w(tmp_path, "x.md",
       "name: T\ndescription: Notes\ntype: user\nstatus: active\n"
       "sensitivity: internal")
    rep = lint.lint_folder(tmp_path, injected_texts=[], allow_cloud=True,
                           allow_cloud_body=False,
                           dispatcher="echo '{\"suggested_description\":\"ok\"}'",
                           min_score=3)
    assert rep["records"][0]["llm"].get("suggested_description") == "ok"


def test_metadata_only_excludes_body(tmp_path: Path):
    # Body has a secret but metadata-only mode never ships the body, so a clean
    # description dispatches successfully.
    _w(tmp_path, "x.md", "name: T\ndescription: Notes\ntype: user\nstatus: active",
       body="leak AKIA1234567890ABCDEF in body")
    rep = lint.lint_folder(tmp_path, injected_texts=[], allow_cloud=True,
                           allow_cloud_body=False,
                           dispatcher="echo '{\"suggested_description\":\"ok\"}'",
                           min_score=3)
    assert rep["records"][0]["llm"].get("suggested_description") == "ok"


def test_lint_never_mutates(corpus: Path):
    before = {p: p.read_text() for p in corpus.glob("*.md")}
    lint.lint_folder(corpus, injected_texts=[], allow_cloud=True, allow_cloud_body=True,
                     dispatcher="echo '{\"suggested_description\":\"x\"}'", min_score=5)
    after = {p: p.read_text() for p in corpus.glob("*.md")}
    assert before == after


def test_cli_allow_cloud_body_requires_opt_in(corpus: Path):
    rc = lint.main(["--path", str(corpus), "--allow-cloud-body",
                    "--dispatcher", "curl x", "--json"])
    assert rc == 2


def test_cli_strict_exit_on_floor(corpus: Path):
    """--strict trips on the DETERMINISTIC floor (the corpus has a
    description_generic and a redundant memory) (lint-strict-01)."""
    rc = lint.main(["--path", str(corpus), "--strict", "--json"])
    assert rc == 1  # corpus has description_generic (deterministic floor)


def test_cli_strict_does_not_trip_on_graded_score_only(tmp_path: Path):
    """lint-strict-01: a store whose descriptions are all PRESENT and non-filler
    but score low on the graded collision metric (every term high-collision)
    MUST NOT fail --strict. The spec keeps the graded score advisory; only the
    deterministic floor (missing/generic description) is a hard gate."""
    # All descriptions are real sentences (no floor violation) but share the same
    # high-collision vocabulary, so the graded score is weak (all_terms_common).
    common = ("type: feedback\nstatus: active")
    _w(tmp_path, "a.md",
       "name: alpha\ndescription: the team the team the team the team\n" + common)
    _w(tmp_path, "b.md",
       "name: beta\ndescription: the team the team the team the team\n" + common)
    _w(tmp_path, "c.md",
       "name: gamma\ndescription: the team the team the team the team\n" + common)
    rc = lint.main(["--path", str(tmp_path), "--strict", "--json"])
    assert rc == 0  # weak graded score, but no deterministic-floor violation
