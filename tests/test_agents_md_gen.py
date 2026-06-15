"""Tests for agents-md-gen, focused on the agentsmd-01 BLOCKER:

  - the rendered AGENTS.md is DLP-scanned before write and a BLOCKER finding
    refuses the write (AGENTS.md is committed and shared with five external
    tools);
  - critical bodies above 'public' sensitivity are NOT inlined into the
    committed file unless --inline-above-public is passed (local-only-by-default
    posture).
"""

from __future__ import annotations

from pathlib import Path

from memforge.cli import agents_md_gen as amg


def _write_mem(folder: Path, name: str, fm: str, body: str) -> None:
    (folder / name).write_text("---\n" + fm + "\n---\n" + body + "\n", encoding="utf-8")


def test_scan_rendered_for_secrets_flags_aws_key():
    rendered = (
        "# AGENTS.md\n\nsome rule\nAKIAIOSFODNN7EXAMPLE is an aws key\n"
    )
    findings = amg.scan_rendered_for_secrets(rendered)
    assert findings, "an AWS access key id must be flagged as a BLOCKER finding"


def test_scan_rendered_for_secrets_clean_on_benign():
    rendered = "# AGENTS.md\n\njust some ordinary cross-tool rules.\n"
    assert amg.scan_rendered_for_secrets(rendered) == []


def test_main_refuses_write_when_secret_present(tmp_path, monkeypatch):
    """A critical memory carrying a secret must NOT be written to AGENTS.md;
    main() returns 2 and does not create the file (agentsmd-01)."""
    home = tmp_path / "home"
    (home / ".claude").mkdir(parents=True)
    # No global CLAUDE.md sections needed; point home so the voice read is empty.
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: home))

    global_mem = home / ".claude" / "global-memory"
    global_mem.mkdir(parents=True)
    # A public critical memory whose body inlines a secret -> the rendered output
    # carries the secret -> DLP refuses the write.
    _write_mem(
        global_mem, "feedback_no_keys_in_files.md",
        "name: No keys in files\ndescription: never commit keys\n"
        "type: feedback\nsensitivity: public",
        "Example of what NOT to do: AKIAIOSFODNN7EXAMPLE\n",
    )

    repo = tmp_path / "repo"
    repo.mkdir()
    # main() reads argv via argparse; drive it directly.
    import sys
    monkeypatch.setattr(sys, "argv", ["agents-md-gen", "--cwd", str(repo)])
    rc = amg.main()
    assert rc == 2
    assert not (repo / "AGENTS.md").exists()


def test_scan_fails_closed_on_import_error(monkeypatch):
    """agentsmd-dlp-failopen-01: if the DLP scanner cannot be imported, the
    pre-scan must fail CLOSED (return a BLOCKER finding) so main() refuses the
    write, not fail open (return []) which would defeat the gate."""
    import builtins

    real_import = builtins.__import__

    def _boom(name, *args, **kwargs):
        if name == "memforge.cli.dlp_scan":
            raise ImportError("simulated dlp_scan import failure")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", _boom)
    findings = amg.scan_rendered_for_secrets("# AGENTS.md\n\nbenign content\n")
    assert findings, "scanner-unavailable must produce a BLOCKER (fail closed)"
    assert any("unavailable" in f for f in findings)


def test_scan_fails_closed_on_scan_error(monkeypatch):
    """If scan_text raises during scanning, fail closed (refuse the write)."""
    def _raise(*a, **k):
        raise RuntimeError("scan blew up")

    monkeypatch.setattr("memforge.cli.dlp_scan.scan_text", _raise)
    findings = amg.scan_rendered_for_secrets("# AGENTS.md\n\nbenign\n")
    assert findings
    assert any("failed during scan" in f for f in findings)


def test_main_refuses_write_when_scanner_unavailable(tmp_path, monkeypatch):
    """End-to-end: a scanner-unavailable condition makes main() return 2 and
    refuse to write AGENTS.md (the gate cannot be silently bypassed)."""
    home = tmp_path / "home"
    (home / ".claude").mkdir(parents=True)
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: home))
    (home / ".claude" / "global-memory").mkdir(parents=True)

    # Force the in-function DLP import to fail.
    monkeypatch.setattr(
        amg, "scan_rendered_for_secrets",
        lambda rendered: ["DLP scanner unavailable -- refusing to write"],
    )
    repo = tmp_path / "repo"
    repo.mkdir()
    import sys
    monkeypatch.setattr(sys, "argv", ["agents-md-gen", "--cwd", str(repo)])
    rc = amg.main()
    assert rc == 2
    assert not (repo / "AGENTS.md").exists()


def test_inline_above_public_default_excludes_above_public(tmp_path):
    """render_critical_rules defaults to PUBLIC-only inline; an internal-labeled
    critical memory body is not inlined unless inline_above_public=True."""
    e_pub = amg.MemoryEntry(
        path=Path("feedback_no_keys_in_files.md"), name="Pub rule",
        description="d", type="feedback", sensitivity="public",
        body="PUBLIC BODY TEXT", mtime=0.0,
    )
    e_int = amg.MemoryEntry(
        path=Path("assistant_name.md"), name="Internal rule",
        description="d", type="feedback", sensitivity="internal",
        body="INTERNAL BODY TEXT", mtime=0.0,
    )
    default_out = amg.render_critical_rules([e_pub, e_int])
    assert "PUBLIC BODY TEXT" in default_out
    assert "INTERNAL BODY TEXT" not in default_out  # above-public excluded

    optin_out = amg.render_critical_rules([e_pub, e_int], inline_above_public=True)
    assert "PUBLIC BODY TEXT" in optin_out
    assert "INTERNAL BODY TEXT" in optin_out


def test_enforce_ceiling_drops_by_created_date_not_mtime(tmp_path):
    """agentsmd-mtime-01: drop ordering prefers the frontmatter `created` date,
    so a stale-but-recently-reformatted memory (new mtime) is dropped before a
    foundational one with an older created date."""
    # Two reference entries; "old" has an older created date but a NEWER mtime
    # (as if recently reformatted). Dropping one to fit the ceiling must drop the
    # older-created one first.
    old = amg.MemoryEntry(
        path=Path("reference_old.md"), name="Old ref",
        description="x" * 200, type="reference", sensitivity="public",
        body="b", mtime=9999.0, created="2024-01-01",
    )
    new = amg.MemoryEntry(
        path=Path("reference_new.md"), name="New ref",
        description="y" * 200, type="reference", sensitivity="public",
        body="b", mtime=1.0, created="2026-06-01",
    )
    per_cwd_section = amg.render_memory_index([old, new], "Per-project memory")
    # Tiny ceiling forces a drop of exactly one reference entry.
    _, _, dropped = amg.enforce_ceiling(
        fixed_content="x" * 50,
        per_cwd_section=per_cwd_section,
        global_section="",
        per_cwd_entries=[old, new],
        global_entries=[],
        ceiling=120,
    )
    assert dropped, "ceiling must force at least one drop"
    # The older-created entry is dropped FIRST despite its newer mtime.
    assert dropped[0].endswith("Old ref")
