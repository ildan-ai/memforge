"""Tests for memforge.recall (spec v0.6.0 query-triggered recall).

Covers each public function with a happy path + an error/edge path, and each
normative post-condition from SPEC.md §"Recall operation".
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from memforge import recall


def _write(folder: Path, fname: str, fm: str, body: str = "body text") -> Path:
    p = folder / fname
    p.write_text(f"---\n{fm}\n---\n{body}\n", encoding="utf-8")
    return p


@pytest.fixture
def folder(tmp_path: Path) -> Path:
    _write(
        tmp_path, "feedback_auth.md",
        "name: Always use OAuth for login\n"
        "description: Prefer OAuth2 authorization-code flow for user login\n"
        "type: feedback\nstatus: active\ntags: [topic:auth]\ntriggers: [oauth, sso]",
    )
    _write(
        tmp_path, "user_name.md",
        "name: Operator nickname\n"
        "description: The operator prefers to be addressed informally\n"
        "type: user\nstatus: active\nalways: true",
    )
    _write(
        tmp_path, "feedback_hidden.md",
        "name: Hidden database rule\n"
        "description: Run database backups nightly\n"
        "type: feedback\nstatus: active\ndo_not_inject: true",
    )
    _write(
        tmp_path, "old.md",
        "name: Old database rule\n"
        "description: deprecated database guidance\n"
        "type: feedback\nstatus: superseded\nsuperseded_by: [mem-x]",
    )
    return tmp_path


# --- build_index ------------------------------------------------------------


def test_build_index_includes_only_live(folder: Path):
    payload = recall.build_index(folder)
    names = {e["name"] for e in payload["entries"].values()}
    assert "Always use OAuth for login" in names
    assert "Old database rule" not in names  # superseded excluded (PC6 liveness)
    assert payload["counts"]["entries"] == 3
    assert payload["spec"] == "0.6.0"
    assert payload["version"] == recall.INDEX_VERSION


def test_build_index_empty_folder(tmp_path: Path):
    payload = recall.build_index(tmp_path)
    assert payload["counts"]["entries"] == 0
    assert payload["entries"] == {}
    assert payload["always"] == []


def test_build_index_skips_memory_md_and_archive(folder: Path):
    (folder / "MEMORY.md").write_text("# index\n- not a memory\n", encoding="utf-8")
    arch = folder / "archive"
    arch.mkdir()
    _write(arch, "archived.md", "name: Archived\ndescription: x\ntype: reference\nstatus: active")
    payload = recall.build_index(folder)
    names = {e["name"] for e in payload["entries"].values()}
    assert "Archived" not in names
    assert all("MEMORY" not in n for n in names)


# --- write_index / load_index round-trip ------------------------------------


def test_write_then_load_round_trip(folder: Path):
    payload = recall.build_index(folder)
    out = recall.write_index(folder, payload)
    assert out.exists()
    assert out == folder / recall.INDEX_REL_PATH
    loaded = recall.load_index(folder)
    assert loaded is not None
    assert loaded["counts"]["entries"] == payload["counts"]["entries"]


def test_load_index_missing_returns_none(tmp_path: Path):
    assert recall.load_index(tmp_path) is None


def test_load_index_corrupt_returns_none(folder: Path):
    p = folder / recall.INDEX_REL_PATH
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text("{ not valid json", encoding="utf-8")
    assert recall.load_index(folder) is None  # fail-open-empty, no raise


def test_load_index_wrong_version_returns_none(folder: Path):
    payload = recall.build_index(folder)
    payload["version"] = 9999
    recall.write_index(folder, payload)
    assert recall.load_index(folder) is None


# --- index_is_stale ---------------------------------------------------------


def test_index_is_stale_detects_change(folder: Path):
    payload = recall.build_index(folder)
    assert recall.index_is_stale(folder, payload) is False
    _write(folder, "new.md", "name: New\ndescription: brand new memory\ntype: reference\nstatus: active")
    assert recall.index_is_stale(folder, payload) is True


def test_index_is_stale_bad_manifest_is_stale(folder: Path):
    assert recall.index_is_stale(folder, {"manifest": "not a dict"}) is True


# --- recall: post-conditions ------------------------------------------------


def _names(hits):
    return [h.name for h in hits]


def test_recall_always_set_included_on_match_and_no_match(folder: Path):
    p = recall.build_index(folder)
    # PC1: always-set present regardless of query.
    assert "Operator nickname" in _names(recall.recall("login oauth", [p]))
    assert _names(recall.recall("zzzz quux nonsense", [p])) == ["Operator nickname"]


def test_recall_match_inclusion_via_synonym(folder: Path):
    p = recall.build_index(folder)
    # 'login' is a synonym form of canonical 'auth'; the auth memory must match.
    hits = recall.recall("how do I add user login", [p])
    assert "Always use OAuth for login" in _names(hits)


def test_recall_do_not_inject_suppressed_even_on_direct_match(folder: Path):
    p = recall.build_index(folder)
    hits = recall.recall("database backups nightly", [p])
    assert "Hidden database rule" not in _names(hits)  # PC3


def test_recall_body_excluded(folder: Path):
    p = recall.build_index(folder)
    hits = recall.recall("login", [p])
    for h in hits:
        # PC4: only description is carried, never body text.
        assert "body text" not in h.desc
        assert h.desc  # description present


def test_recall_sensitivity_ceiling(tmp_path: Path):
    _write(tmp_path, "secret.md",
           "name: Restricted note\ndescription: restricted widget data\n"
           "type: reference\nstatus: active\nsensitivity: restricted\ntriggers: [widget]")
    p = recall.build_index(tmp_path)
    assert "Restricted note" in _names(recall.recall("widget", [p]))  # no ceiling
    assert "Restricted note" not in _names(
        recall.recall("widget", [p], sensitivity_max="internal"))  # PC5


def test_recall_access_team_gate(tmp_path: Path):
    _write(tmp_path, "team.md",
           "name: Security team note\ndescription: security widget rotation\n"
           "type: reference\nstatus: active\naccess: team:security\ntriggers: [widget]")
    p = recall.build_index(tmp_path)
    assert "Security team note" not in _names(recall.recall("widget", [p]))  # no team
    assert "Security team note" in _names(
        recall.recall("widget", [p], viewer_teams={"team:security"}))


def test_recall_budget_and_top_k(tmp_path: Path):
    for i in range(20):
        _write(tmp_path, f"m{i}.md",
               f"name: Widget memo {i}\ndescription: widget calibration note number {i}\n"
               f"type: reference\nstatus: active\ntriggers: [widget]")
    p = recall.build_index(tmp_path)
    hits = recall.recall("widget", [p], top_k=5, char_budget=10_000)
    assert len(hits) <= 5  # top-K honored


def test_recall_multi_folder_merge(tmp_path: Path):
    a = tmp_path / "a"; a.mkdir()
    b = tmp_path / "b"; b.mkdir()
    _write(a, "x.md", "name: Alpha auth note\ndescription: alpha oauth\ntype: feedback\nstatus: active\ntriggers: [oauth]")
    _write(b, "y.md", "name: Beta auth note\ndescription: beta oauth\ntype: feedback\nstatus: active\ntriggers: [oauth]")
    pa, pb = recall.build_index(a), recall.build_index(b)
    names = _names(recall.recall("oauth", [pa, pb]))
    assert "Alpha auth note" in names and "Beta auth note" in names


def test_recall_empty_payloads(folder: Path):
    assert recall.recall("anything", []) == []


# --- malformed-field degradation (spec invariant 26) ------------------------


def test_malformed_always_degrades_to_false(tmp_path: Path):
    _write(tmp_path, "bad.md",
           'name: Bad always\ndescription: widget thing\ntype: reference\nstatus: active\nalways: "yes"')
    p = recall.build_index(tmp_path)
    e = next(v for v in p["entries"].values() if v["name"] == "Bad always")
    assert e["always"] is False  # no truthy-string coercion


def test_malformed_triggers_falls_back_to_derived(tmp_path: Path):
    _write(tmp_path, "bad.md",
           "name: Bad triggers\ndescription: widget calibration notes\n"
           "type: reference\nstatus: active\ntriggers: not-a-list")
    p = recall.build_index(tmp_path)
    # derived trigger 'widget' from description still matches
    assert "Bad triggers" in _names(recall.recall("widget calibration", [p]))


# --- load_synonyms ----------------------------------------------------------


def test_load_synonyms_default(tmp_path: Path):
    syn = recall.load_synonyms(tmp_path)
    assert "auth" in syn and "login" in syn["auth"]


def test_load_synonyms_override(tmp_path: Path):
    override = tmp_path / recall.SYNONYMS_REL_PATH
    override.parent.mkdir(parents=True, exist_ok=True)
    override.write_text("map:\n  widget: [gadget, gizmo]\n", encoding="utf-8")
    syn = recall.load_synonyms(tmp_path)
    assert "widget" in syn and "gadget" in syn["widget"]


def test_load_synonyms_malformed_override_falls_back(tmp_path: Path):
    override = tmp_path / recall.SYNONYMS_REL_PATH
    override.parent.mkdir(parents=True, exist_ok=True)
    override.write_text(": : not yaml : :\n", encoding="utf-8")
    syn = recall.load_synonyms(tmp_path)
    assert "auth" in syn  # defaults preserved


# --- security hardening (from the §2 threat-model pass) ---------------------


def test_build_index_skips_symlinked_files(tmp_path: Path):
    import os
    mem = tmp_path / "mem"; mem.mkdir()
    outside = tmp_path / "outside"; outside.mkdir()
    _write(mem, "real.md", "name: Real\ndescription: real widget memo\ntype: reference\nstatus: active")
    secret = outside / "secret.md"
    secret.write_text("---\nname: SECRET\ndescription: outside the root\ntype: reference\nstatus: active\n---\nx\n", encoding="utf-8")
    os.symlink(secret, mem / "evil.md")
    payload = recall.build_index(mem)
    names = {e["name"] for e in payload["entries"].values()}
    assert "Real" in names
    assert "SECRET" not in names  # symlinked file must not be ingested


def test_load_index_drops_malformed_and_caps_lengths(folder: Path):
    payload = recall.build_index(folder)
    recall.write_index(folder, payload)
    # Tamper the on-disk index: inject a non-dict entry + an oversized desc.
    p = folder / recall.INDEX_REL_PATH
    raw = json.loads(p.read_text())
    raw["entries"]["junk"] = "not-a-dict"
    first = next(iter(raw["entries"]))
    raw["entries"][first]["desc"] = "A" * 5000  # oversized
    raw["entries"][first]["always"] = "yes"      # non-bool
    p.write_text(json.dumps(raw), encoding="utf-8")
    loaded = recall.load_index(folder)
    assert loaded is not None
    assert "junk" not in loaded["entries"]                      # malformed dropped
    assert len(loaded["entries"][first]["desc"]) <= recall.DESC_MAX_CHARS  # capped
    assert loaded["entries"][first]["always"] is False          # non-bool coerced to False


def test_load_index_rejects_oversized_file(folder: Path):
    payload = recall.build_index(folder)
    recall.write_index(folder, payload)
    p = folder / recall.INDEX_REL_PATH
    p.write_text("x" * (recall.MAX_INDEX_BYTES + 1), encoding="utf-8")
    assert recall.load_index(folder) is None


def test_markdown_output_has_untrusted_preamble(folder: Path):
    from memforge.cli import recall as cli
    p = recall.build_index(folder)
    recall.write_index(folder, p)
    import io, contextlib
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        cli.main(["--path", str(folder), "login"])
    out = buf.getvalue()
    assert "untrusted" in out.lower()
    assert "not as instructions" in out.lower() or "not instructions" in out.lower()


def test_unknown_sensitivity_fails_closed(tmp_path: Path):
    _write(tmp_path, "weird.md",
           "name: Weird sens\ndescription: widget weirdness\n"
           "type: reference\nstatus: active\nsensitivity: boguslevel\ntriggers: [widget]")
    p = recall.build_index(tmp_path)
    # With a ceiling set, an unrecognized sensitivity must fail closed (excluded).
    assert "Weird sens" not in _names(recall.recall("widget", [p], sensitivity_max="restricted"))


# --- CLI fail-safe ----------------------------------------------------------


def test_cli_empty_query_exits_zero(folder: Path, capsys):
    from memforge.cli import recall as cli
    rc = cli.main(["--path", str(folder)])
    assert rc == 0
    assert capsys.readouterr().out == ""


def test_cli_rebuild_then_query(folder: Path, capsys):
    from memforge.cli import recall as cli
    rc = cli.main(["--path", str(folder), "--rebuild", "login"])
    assert rc == 0
    out = capsys.readouterr().out
    assert "Always use OAuth for login" in out
    assert "Operator nickname" in out  # always-set


def test_cli_json_format(folder: Path, capsys):
    from memforge.cli import recall as cli
    cli.main(["--path", str(folder), "--rebuild"])  # build first
    rc = cli.main(["--path", str(folder), "--format", "json", "login"])
    assert rc == 0
    data = json.loads(capsys.readouterr().out)
    assert any(h["name"] == "Always use OAuth for login" for h in data)
