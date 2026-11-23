"""The uninstalled-tree version fallbacks must equal the version pyproject.toml declares."""

import importlib.metadata
import os
import re

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PYPROJECT = os.path.join(ROOT, "pyproject.toml")


def _declared_version():
    if not os.path.isfile(PYPROJECT):
        pytest.skip("pyproject.toml not present (not a source tree)")
    with open(PYPROJECT, encoding="utf-8") as fh:
        text = fh.read()
    section = re.search(r"^\[project\]\s*$(.*?)(?=^\[|\Z)", text, re.S | re.M)
    match = re.search(r'^version\s*=\s*"([^"]+)"', section.group(1), re.M)
    return match.group(1)


def _uninstalled(monkeypatch):
    def missing(name):
        raise importlib.metadata.PackageNotFoundError(name)

    monkeypatch.setattr(importlib.metadata, "version", missing)


def test_package_version_fallback_matches_pyproject(monkeypatch):
    import importlib

    import memforge

    _uninstalled(monkeypatch)
    try:
        reloaded = importlib.reload(memforge)
        assert reloaded.__version__ == _declared_version()
    finally:
        monkeypatch.undo()
        importlib.reload(memforge)


def test_config_spec_version_fallback_matches_pyproject(monkeypatch):
    from memforge.cli import _config

    _uninstalled(monkeypatch)
    assert _config._default_spec_version() == _declared_version()
