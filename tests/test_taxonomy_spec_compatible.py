"""Executable gate: spec/taxonomy.yaml's declared `spec_compatible` range
MUST cover the co-located spec/VERSION.

This is deliberately a real, CI-enforced test rather than only a comment in
taxonomy.yaml. A comment does not fail a build; this test does. If
spec/VERSION is ever bumped without widening spec_compatible's upper bound
in the same change, this test fails immediately, which is the whole point:
the taxonomy version defect this repo already hit once (a stale
`<0.7.0` upper bound excluding the-then-current 0.8.0 spec) is exactly the
class of drift this test exists to catch mechanically, not just by review.

Reuses `memforge.cli.link_upgrade._spec_compatible_warning` (the same
best-effort tuple-range comparison the CLI tool uses for its own advisory
WARN) rather than depending on a range-parsing library, keeping this gate
dependency-light and guaranteed to agree with the tool's own runtime check.
"""

from __future__ import annotations

from memforge.cli.link_upgrade import _spec_compatible_warning


def test_taxonomy_spec_compatible_covers_the_current_spec_version():
    warning = _spec_compatible_warning(memforge_root=None)
    assert warning is None, (
        "spec/taxonomy.yaml's spec_compatible range does not cover the "
        f"current spec/VERSION: {warning}"
    )
