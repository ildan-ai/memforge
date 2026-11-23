"""`memory-audit --help` names the default folders the way the resolver picks them."""

from __future__ import annotations

import pytest

from memforge.cli import audit


def test_help_points_at_the_resolver_and_names_every_layout(capsys):
    with pytest.raises(SystemExit):
        audit.main(["--help"])
    help_text = " ".join(capsys.readouterr().out.split())

    assert "memforge paths" in help_text
    assert "MEMFORGE_MEMORY_PATH" in help_text
    assert "~/.memforge/memory" in help_text
    assert "~/.memforge/global-memory" in help_text
