"""Shared pytest fixtures for the MemForge suite."""

from __future__ import annotations

from pathlib import Path

import pytest


@pytest.fixture
def tmp_memory_folder(tmp_path: Path) -> Path:
    """A clean memory folder with one well-formed entry already present.

    Tests that need an empty folder can use `tmp_path` directly; this fixture
    seeds a single canonical memory file so audit / index / discovery tests
    have something to chew on without each test re-writing boilerplate.
    """
    seed = tmp_path / "feedback_seed_rule.md"
    seed.write_text(
        "---\n"
        "name: Seed rule\n"
        "description: Canonical fixture entry; do not depend on its body content\n"
        "type: feedback\n"
        "---\n\n"
        "Use the cheapest defensible tier first.\n"
        "\n"
        "**Why:** cost discipline.\n"
        "**How to apply:** every dispatch.\n",
        encoding="utf-8",
    )
    return tmp_path


@pytest.fixture
def audit_log_module():
    """The audit-log CLI module (memforge.cli.audit_log)."""
    from memforge.cli import audit_log
    return audit_log


@pytest.fixture
def memory_dedup_module():
    """The dedup CLI module (memforge.cli.dedup)."""
    from memforge.cli import dedup
    return dedup
