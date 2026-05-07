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
_CLOSE_PATTERN = "\n---"


def has_frontmatter(text: str) -> bool:
    """True iff text starts with `---\\n` and contains a closing `\\n---`."""
    if not text.startswith(_OPEN):
        return False
    return text.find(_CLOSE_PATTERN, len(_OPEN)) != -1


def parse(text: str) -> tuple[dict[str, Any], str]:
    """Split frontmatter and body.

    Returns (frontmatter_dict, body_str). When no frontmatter is present,
    returns ({}, text). YAML parsing uses safe_load (no arbitrary types).
    """
    if not has_frontmatter(text):
        return {}, text

    end = text.find(_CLOSE_PATTERN, len(_OPEN))
    block = text[len(_OPEN):end]

    after = end + len(_CLOSE_PATTERN)
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

    YAML emitted with sort_keys=False, allow_unicode=True. List values
    render in flow style (`[a, b, c]`) for compactness; multiline strings
    use block style.
    """
    if not frontmatter:
        return body
    yaml_text = yaml.safe_dump(
        frontmatter,
        sort_keys=False,
        allow_unicode=True,
        default_flow_style=None,
        width=10_000,
    )
    return f"---\n{yaml_text}---\n{body}"
