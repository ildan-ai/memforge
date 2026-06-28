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


def _short_yaml_err(e: "yaml.YAMLError") -> str:
    """Concise one-line locator for a YAML parse failure.

    Prefers PyYAML's problem_mark (1-indexed line/column within the frontmatter
    block) over the multi-line default str(e), so a write-gate deny message
    stays terminal-readable."""
    mark = getattr(e, "problem_mark", None)
    if mark is not None:
        return f"frontmatter line {mark.line + 1}, column {mark.column + 1}"
    first = (str(e).splitlines() or [""])[0]
    return first or e.__class__.__name__


def validate_frontmatter(text: str) -> tuple[bool, str | None]:
    """Granular pre-write validation of a memory file's frontmatter block.

    Unlike parse(), which silently collapses every failure mode to ({}, text),
    this distinguishes them so a write-boundary gate can REJECT a malformed
    write and name the cause. This is the agent-neutral primitive any adapter
    (git pre-commit, a CC PreToolUse shim, editor-on-save) calls before
    accepting a memory write; no adapter reimplements YAML parsing.

    Returns (ok, reason):
      - (True, None)  : no RECOGNIZED frontmatter block (no `---` fence, or an
                        opening `---` with no closing `---` on its own line --
                        either way not invariant 27's concern, it is invariant
                        1's), OR a recognized block whose YAML parses as a
                        mapping (an EMPTY block is the benign empty mapping).
      - (False, msg)  : a recognized (closed) `---` fence whose block is
                        MALFORMED: a YAML parse error (the recurring
                        unquoted-colon break) or a non-mapping scalar/list.

    Deliberately mirrors has_frontmatter()/parse()'s leniency: an opening `---`
    with no recognized closing fence is treated as "no frontmatter" (exactly as
    parse() does), NOT a parse violation, so validate never false-positives on a
    plain markdown file that opens with a `---` thematic break, and an empty
    frontmatter block is accepted as the empty mapping (parse() returns {} for
    it). Presence of a valid block on a memory file is invariant 1's job, not
    this gate's. (Resolves the 0.9.0 panel BLOCKER: empty/degenerate fences must
    not HARD-fail.)
    """
    norm = text.replace("\r\n", "\n").replace("\r", "\n")
    if not has_frontmatter(norm):
        # No fence, or an unclosed/unrecognized one: invariant 1's concern, not
        # invariant 27's. parse() treats this as no-frontmatter; so do we.
        return True, None

    end = _close_fence_index(norm)  # guaranteed != -1 by has_frontmatter()
    block = norm[len(_OPEN):end]
    try:
        loaded = yaml.safe_load(block)
    except yaml.YAMLError as e:
        return False, (
            "frontmatter YAML failed to parse, most likely an UNQUOTED COLON "
            "(an unquoted `:` followed by a space) inside a value such as "
            "`description:` or `name:` (a bare `#`, or a leading `>`/`|`, can "
            "also break it); reword to remove the `:`-space or wrap the whole "
            f"value in single quotes. Parser detail: {_short_yaml_err(e)}"
        )

    if loaded is None:
        # An empty (whitespace-only) frontmatter block is the empty mapping --
        # benign and syntactically valid. Emptiness (missing required fields) is
        # a SOFT/audit concern, not a parse-gate failure.
        return True, None

    if not isinstance(loaded, dict):
        return False, (
            f"frontmatter parsed as {type(loaded).__name__}, not a mapping; the "
            "block must be `key: value` pairs in block style (one per line)"
        )

    return True, None


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
