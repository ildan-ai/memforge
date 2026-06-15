"""YAML frontmatter parser using yaml.safe_load.

Closes the BLOCKER from the 2026-05-07 code-review-panel: hand-rolled
regex/split parsers across 9 tools enabled RBAC bypass, DLP evasion, and
spec-v0.4 fragility. This module is the single replacement.

Public API:
    parse(text)            -> (frontmatter_dict, body_str)
    has_frontmatter(text)  -> bool
    render(fm, body)       -> text

Frontmatter delimiter is `---` on its own line, opening at the start of file
and closing the block. Anything else is treated as no-frontmatter.
"""

from __future__ import annotations

import datetime
from typing import Any

try:
    import yaml
except ImportError as e:  # pragma: no cover
    raise ImportError(
        "PyYAML is required for memforge.frontmatter. "
        "Install with: pip install PyYAML"
    ) from e


def _stringify_dates(value: Any) -> Any:
    """yaml.safe_load auto-converts ISO date / datetime literals to Python
    date / datetime objects. Memory tooling consumes date fields as ISO
    strings; convert back here so callers don't see surprise object types.
    Recurses into dicts and lists.
    """
    if isinstance(value, (datetime.datetime, datetime.date)):
        return value.isoformat()
    if isinstance(value, dict):
        return {k: _stringify_dates(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_stringify_dates(v) for v in value]
    return value


_OPEN = "---\n"


def _close_fence_index(text: str) -> int:
    """Return the index of the `\\n` that begins the closing fence line, or -1.

    The close fence is `---` ALONE on its own line (SPEC §12-13), so we require
    a `\\n---` followed by either a newline or end-of-file. A bare `\\n---`
    substring (e.g. `\\n--- trailing text` or a 4-dash `\\n----`) is NOT a fence
    and must not split the block, otherwise trailing text leaks into the body
    (closes frontmatter-01).
    """
    start = len(_OPEN)
    n = len(text)
    while True:
        idx = text.find("\n---", start)
        if idx == -1:
            return -1
        after = idx + 4  # position just past "\n---"
        if after == n or text[after] == "\n":
            return idx
        start = idx + 1


def has_frontmatter(text: str) -> bool:
    """True iff text starts with `---\\n` and has a closing `---` on its own
    line (`\\n---` followed by a newline or end-of-file).

    Line endings are LF-normalized first so a CRLF-terminated file
    (`\\r\\n---\\r\\n`) is recognized. The package's read paths open in text mode
    (universal-newline translation), but a caller handing parse() a string from
    a non-normalizing source (bytes decoded directly) would otherwise get a
    silent no-parse (closes frontmatter-01)."""
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    if not text.startswith(_OPEN):
        return False
    return _close_fence_index(text) != -1


def parse(text: str) -> tuple[dict[str, Any], str]:
    """Split frontmatter and body.

    Returns (frontmatter_dict, body_str). When no frontmatter is present,
    returns ({}, text). YAML parsing uses safe_load (no arbitrary types).

    Line endings are LF-normalized before splitting so a CRLF-terminated file
    parses correctly (frontmatter-01); the returned body is therefore
    LF-normalized, matching the package's text-mode read paths.
    """
    if not has_frontmatter(text):
        return {}, text

    text = text.replace("\r\n", "\n").replace("\r", "\n")
    end = _close_fence_index(text)
    block = text[len(_OPEN):end]

    after = end + 4  # past "\n---"
    if after < len(text) and text[after] == "\n":
        after += 1
    body = text[after:]

    try:
        fm = yaml.safe_load(block) or {}
    except yaml.YAMLError:
        return {}, text

    if not isinstance(fm, dict):
        return {}, text

    return _stringify_dates(fm), body


def render(frontmatter: dict[str, Any], body: str) -> str:
    """Serialize a frontmatter dict + body back to a memory file string.

    YAML emitted with sort_keys=False, allow_unicode=True, and
    default_flow_style=False so the top-level mapping is ALWAYS block style
    (one key per line), matching the spec (§frontmatter, block style) and every
    hand-authored memory file. With the PyYAML default (None), a scalar-only
    mapping auto-collapses to an inline flow line (`{name: x, type: feedback}`),
    diverging from the on-disk contract (frontmatter-render-01).
    """
    if not frontmatter:
        return body
    yaml_text = yaml.safe_dump(
        frontmatter,
        sort_keys=False,
        allow_unicode=True,
        default_flow_style=False,
        width=10_000,
    )
    return f"---\n{yaml_text}---\n{body}"
