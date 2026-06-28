"""Single source of truth for MemForge structural caps + enums.

Before 0.9.0 the pointer-line byte cap was defined independently in
``cli/audit.py`` and ``cli/index_gen.py`` (and consulted again by the new
``cli/validate.py``). Three copies of one number drift: a 2026-06 cycle shipped
audit at 150 while the index generator + write-gate used 180. This module is the
single definition the audit, validate, and index-gen operations all import, so
the value cannot diverge again (closes S5 / the 150-vs-180 split).

The caps are SHOULD-level conventions per ``spec/SPEC.md`` §"MEMORY.md format":
tools flag overruns as warnings, never hard errors.
"""

from __future__ import annotations

# UTF-8 byte budget for a single ``MEMORY.md`` pointer line (`- [title](path): hook`).
# Keeps the index terminal-readable; the index generator truncates the hook
# excerpt to fit (lossless for recall, which surfaces the frontmatter
# ``description``, never the truncated hook). Em-dashes cost 3 bytes each.
POINTER_LINE_BYTE_CAP = 180

# Total-line budget for the ``MEMORY.md`` index so it stays parseable at session
# load. A folder over this should roll detail memories into topic subfolders.
MEMORY_MD_LINE_CAP = 180

# Frontmatter ``tier`` enum (v0.4+): an index-hotlist memory vs a detail memory.
VALID_TIERS = frozenset({"index", "detail"})

# Frontmatter ``status`` enum (v0.4+): the union of the live set
# {active, proposed, gated} (surfaced by recall) and the exit set
# {superseded, dropped, archived}. Mirrors _concurrency_audit.VALID_STATUSES;
# absence of ``status`` is treated as ``active``.
VALID_STATUSES = frozenset(
    {"active", "proposed", "gated", "superseded", "dropped", "archived"}
)
