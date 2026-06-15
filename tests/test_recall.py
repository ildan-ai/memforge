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
    assert payload["spec"] == "0.6.1"  # doc-07: stamped spec matches package/spec
    assert payload["version"] == recall.INDEX_VERSION


def test_build_index_strips_control_chars(tmp_path: Path):
    """recall-01: build_index feeds the CLI --rebuild path, which passes the
    in-memory payload straight to recall() without load_index/_sanitize_payload.
    So build_index itself MUST strip ANSI/control-char escapes from name/desc/
    path, or hostile frontmatter smuggles terminal escapes into injected output
    on the rebuild path (while the load path strips them)."""
    # YAML double-quoted scalars with \x1b (ESC) + \x07 (BEL) escape sequences;
    # yaml.safe_load decodes these to the actual control chars in the parsed
    # frontmatter values (the smuggling vector this test guards against).
    _write(
        tmp_path, "evil.md",
        'name: "kubernetes ev\\x1b[31mil"\n'
        'description: "distinctivetoken ev\\x1b]0;ttl\\x07"\n'
        "type: feedback\nstatus: active",
    )
    payload = recall.build_index(tmp_path)
    entry = next(iter(payload["entries"].values()))
    assert "\x1b" not in entry["name"]
    assert "\x1b" not in entry["desc"]
    assert "\x07" not in entry["desc"]
    # And the same is true when surfaced as a Hit via recall() on the freshly
    # built (un-loaded) payload.
    hits = recall.recall("kubernetes distinctivetoken", [payload])
    for h in hits:
        assert "\x1b" not in h.name and "\x1b" not in h.desc
        assert "\x07" not in h.desc


def test_build_index_empty_folder(tmp_path: Path):
    payload = recall.build_index(tmp_path)
    assert payload["counts"]["entries"] == 0
    assert payload["entries"] == {}
    # recall-03: the derived always-set is exposed only as a count for human/
    # debug visibility; there is no unused top-level 'always' list to carry.
    assert payload["counts"]["always"] == 0
    assert "always" not in payload


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


def test_recall_access_counsel_default_deny(tmp_path: Path):
    # BLOCKER recall-access-01: a non-team restricting access label (counsel)
    # must fail CLOSED. A teamless viewer must NOT see its description.
    _write(tmp_path, "counsel.md",
           "name: Privileged counsel note\ndescription: privileged widget legal advice\n"
           "type: reference\nstatus: active\naccess: [counsel]\ntriggers: [widget]")
    p = recall.build_index(tmp_path)
    # Default viewer (no teams): counsel memory is suppressed.
    assert "Privileged counsel note" not in _names(recall.recall("widget", [p]))
    # Even a viewer holding a team does not gain counsel access (no counsel role).
    assert "Privileged counsel note" not in _names(
        recall.recall("widget", [p], viewer_teams={"team:security"}))


def test_recall_access_public_internal_surface(tmp_path: Path):
    # public/internal access labels are open and MUST still surface (not over-blocked).
    _write(tmp_path, "pub.md",
           "name: Public widget note\ndescription: public widget data\n"
           "type: reference\nstatus: active\naccess: [public]\ntriggers: [widget]")
    _write(tmp_path, "int.md",
           "name: Internal widget note\ndescription: internal widget data\n"
           "type: reference\nstatus: active\naccess: [internal]\ntriggers: [widget]")
    p = recall.build_index(tmp_path)
    names = _names(recall.recall("widget", [p]))
    assert "Public widget note" in names
    assert "Internal widget note" in names


def test_recall_access_team_still_visible_to_member(tmp_path: Path):
    # A team:x viewer-in-team still sees its team memory (BLOCKER fix must not
    # over-restrict the existing team path).
    _write(tmp_path, "team.md",
           "name: Platform team note\ndescription: platform widget rotation\n"
           "type: reference\nstatus: active\naccess: [team:platform]\ntriggers: [widget]")
    p = recall.build_index(tmp_path)
    assert "Platform team note" not in _names(recall.recall("widget", [p]))
    assert "Platform team note" in _names(
        recall.recall("widget", [p], viewer_teams={"team:platform"}))


def test_recall_access_multi_team_any_match(tmp_path: Path):
    # Mirror index_gen.apply_rbac_filter: with multiple team labels, holding ANY
    # one grants access.
    _write(tmp_path, "multi.md",
           "name: Multi team note\ndescription: shared widget rotation\n"
           "type: reference\nstatus: active\naccess: [team:security, team:platform]\n"
           "triggers: [widget]")
    p = recall.build_index(tmp_path)
    assert "Multi team note" in _names(
        recall.recall("widget", [p], viewer_teams={"team:platform"}))
    assert "Multi team note" not in _names(
        recall.recall("widget", [p], viewer_teams={"team:ops"}))


def test_recall_access_restricted_label_default_deny(tmp_path: Path):
    # A restricted/privileged access label (not public/internal, not team) is
    # unsatisfiable at recall time and must fail closed.
    _write(tmp_path, "restr.md",
           "name: Restricted access note\ndescription: restricted widget data\n"
           "type: reference\nstatus: active\naccess: [restricted]\ntriggers: [widget]")
    p = recall.build_index(tmp_path)
    assert "Restricted access note" not in _names(recall.recall("widget", [p]))


def test_recall_honors_operator_synonym_override(tmp_path: Path):
    # MAJOR recall-01: build/query synonym symmetry. The index is built with the
    # merged map (default + .memforge/recall-synonyms.yaml override). A query on
    # the surface form (k8s) must find the memory indexed under the canonical
    # (kubernetes) WITHOUT the caller passing synonyms= explicitly.
    override = tmp_path / recall.SYNONYMS_REL_PATH
    override.parent.mkdir(parents=True, exist_ok=True)
    override.write_text("map:\n  kubernetes: [k8s, kube]\n", encoding="utf-8")
    _write(tmp_path, "infra.md",
           "name: Kubernetes deploy note\ndescription: how we deploy kubernetes workloads\n"
           "type: reference\nstatus: active\ntriggers: [kubernetes]")
    p = recall.build_index(tmp_path)  # built with merged override map
    recall.write_index(tmp_path, p)
    loaded = recall.load_index(tmp_path)
    # Query the surface form; reader self-loads the folder override -> match.
    assert "Kubernetes deploy note" in _names(recall.recall("k8s rollout", [loaded]))


def test_recall_char_budget_counts_folder_prefix(tmp_path: Path):
    # MINOR recall-02: the folder prefix is part of the rendered line and must be
    # counted against the budget, so a long folder shrinks how many hits fit.
    for i in range(10):
        _write(tmp_path, f"m{i}.md",
               f"name: Widget memo {i}\ndescription: widget calibration note number {i}\n"
               f"type: reference\nstatus: active\ntriggers: [widget]")
    p = recall.build_index(tmp_path)
    # Force the long real folder string into the payload.
    p["folder"] = "/srv/memory-store/global-memory/some/deep/nested/path"
    short = recall.build_index(tmp_path)
    short["folder"] = "/x"
    n_long = len(recall.recall("widget", [p], top_k=100, char_budget=400))
    n_short = len(recall.recall("widget", [short], top_k=100, char_budget=400))
    # A longer folder prefix consumes more budget per line -> at most as many hits.
    assert n_long <= n_short
    assert n_short >= 1


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


def test_cli_rebuild_skips_when_not_stale(folder: Path, capsys):
    # MINOR recall-03: --rebuild now consumes index_is_stale and reuses an
    # up-to-date index instead of rewriting it every time.
    from memforge.cli import recall as cli
    cli.main(["--path", str(folder), "--rebuild"])  # initial build
    idx = folder / recall.INDEX_REL_PATH
    mtime_before = idx.stat().st_mtime_ns
    import time
    time.sleep(0.01)
    rc = cli.main(["--path", str(folder), "--rebuild", "login"])
    assert rc == 0
    # Not stale -> index file untouched (no rewrite).
    assert idx.stat().st_mtime_ns == mtime_before
    out = capsys.readouterr().out
    assert "Always use OAuth for login" in out  # still queries correctly


def test_cli_force_rebuild_always_rewrites(folder: Path):
    from memforge.cli import recall as cli
    cli.main(["--path", str(folder), "--rebuild"])
    idx = folder / recall.INDEX_REL_PATH
    mtime_before = idx.stat().st_mtime_ns
    import time
    time.sleep(0.01)
    cli.main(["--path", str(folder), "--rebuild", "--force-rebuild"])
    assert idx.stat().st_mtime_ns != mtime_before  # forced rewrite


def test_cli_json_format(folder: Path, capsys):
    from memforge.cli import recall as cli
    cli.main(["--path", str(folder), "--rebuild"])  # build first
    rc = cli.main(["--path", str(folder), "--format", "json", "login"])
    assert rc == 0
    data = json.loads(capsys.readouterr().out)
    assert any(h["name"] == "Always use OAuth for login" for h in data)


def test_cli_rebuild_path_strips_control_chars(tmp_path: Path, capsys):
    """recall-01: the CLI --rebuild path passes the freshly built payload to
    recall() WITHOUT load_index/_sanitize_payload. With the build-time sanitizer
    in place, ANSI/control-char escapes from hostile frontmatter must not reach
    the emitted markdown/JSON on the rebuild path."""
    from memforge.cli import recall as cli

    _write(
        tmp_path, "evil.md",
        'name: "kubernetes ev\\x1b[31mil"\n'
        'description: "distinctivetoken ev\\x1b]0;ttl\\x07"\n'
        "type: feedback\nstatus: active",
    )
    rc = cli.main(["--path", str(tmp_path), "--rebuild", "--format", "json",
                   "kubernetes distinctivetoken"])
    assert rc == 0
    out = capsys.readouterr().out
    assert "\x1b" not in out
    assert "\x07" not in out


def test_recall_dedups_uid_across_payloads(tmp_path: Path):
    """recall-04: a uid present in two payloads (e.g. the same global folder
    configured twice) must be listed once, not double-listed."""
    _write(
        tmp_path, "always_rule.md",
        "name: Operator nickname\n"
        "description: addresses informally\n"
        "type: user\nstatus: active\nuid: mem-dup\nalways: true",
    )
    payload = recall.build_index(tmp_path)
    # Same payload twice simulates one uid surfaced from two configured folders.
    hits = recall.recall("nickname", [payload, payload])
    uids = [h.uid for h in hits]
    assert uids.count("mem-dup") == 1
