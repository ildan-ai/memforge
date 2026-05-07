"""Shared pytest fixtures for the MemForge suite."""

from __future__ import annotations

import importlib.machinery
import importlib.util
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
TOOLS_DIR = REPO_ROOT / "tools"
SRC_DIR = REPO_ROOT / "src"


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


def _load_tool_module(name: str, filename: str):
    """Load a `tools/<filename>` script as an importable module.

    The MemForge tools have no .py extension and live outside the package
    tree, so they cannot be imported normally. We use spec_from_file_location
    so tests can call functions defined in the script bodies (e.g.
    `append_record`, `verify_chain`, `is_local_dispatcher`).
    """
    if str(SRC_DIR) not in sys.path:
        sys.path.insert(0, str(SRC_DIR))

    cached = sys.modules.get(name)
    if cached is not None:
        return cached

    path = TOOLS_DIR / filename
    # Tools have no .py suffix, so spec_from_file_location's suffix-based
    # loader lookup fails. Pass an explicit SourceFileLoader instead.
    loader = importlib.machinery.SourceFileLoader(name, str(path))
    spec = importlib.util.spec_from_loader(name, loader)
    if spec is None:
        raise ImportError(f"Cannot load {path} as module {name}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def audit_log_module():
    """tools/memory-audit-log loaded as importable module `mf_audit_log`."""
    return _load_tool_module("mf_audit_log", "memory-audit-log")


@pytest.fixture
def memory_dedup_module():
    """tools/memory-dedup loaded as importable module `mf_memory_dedup`."""
    return _load_tool_module("mf_memory_dedup", "memory-dedup")
