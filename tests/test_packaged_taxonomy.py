"""The tag taxonomy ships inside the package, identical to the spec's copy."""

from __future__ import annotations

from pathlib import Path

import pytest

import memforge

PACKAGED = Path(memforge.__file__).resolve().parent / "spec" / "taxonomy.yaml"
SPEC_COPY = Path(__file__).resolve().parents[1] / "spec" / "taxonomy.yaml"


def test_taxonomy_ships_inside_the_package():
    """An installed package has no repository around it; without this file
    `memory-audit-deep` skips tag checks and `--strict` fails on every install."""
    assert PACKAGED.is_file()


@pytest.mark.skipif(not SPEC_COPY.is_file(), reason="no spec/ folder in this layout")
def test_packaged_taxonomy_matches_the_spec_copy():
    assert PACKAGED.read_bytes() == SPEC_COPY.read_bytes()
