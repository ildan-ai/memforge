# memory-link-upgrade : promote untyped [[name]] wikilinks to typed
# `relations` frontmatter entries (spec 0.9.0, pending release).
#
# Subcommands:
#   scan     : read-only. Lists every [[name]] wikilink candidate per file,
#              classified as unresolved (no matching file), ambiguous
#              (multiple files share the token's stem), already-typed (a
#              relations entry to that target already exists), or
#              promotable. Never writes anything.
#   promote  : the write path. Defaults to a DRY RUN (reports exactly what
#              scan would report, restricted to promotable candidates and
#              the given filters) and requires an explicit --write flag to
#              persist anything. See SPEC.md, "Typed relations" ->
#              "Reference implementation: memory-link-upgrade".
#
# Design constraints (safety properties this tool holds itself to, per
# SPEC.md "A relation is an assertion, not a fact" and general write-tool
# hygiene; every one deliberately declined here is disclosed with
# reasoning below, in the "Explicitly NOT covered" section, not silently
# omitted):
#
#   - NEVER infers a predicate from link context or body text. `--predicate`
#     is a required argument on `promote`; the operator makes the semantic
#     call, every time.
#   - Additive-only: appends to the `relations` frontmatter list; never
#     touches the [[name]] occurrence in the body. The promotion is
#     reversible by deleting the added frontmatter entry.
#   - Idempotent per (target, predicate) pair, not per target: a second
#     promote pass with a DIFFERENT predicate against an already-typed
#     target still adds that edge.
#   - Blanket-application guard: --write with neither --only-token nor
#     --file requires an explicit --all-matches acknowledgement, so one
#     `--predicate` cannot silently stamp every resolvable wikilink in a
#     folder without the operator consciously scoping (or acknowledging)
#     the blast radius: supplying one predicate does not by itself
#     establish that the operator judged each individual link to carry
#     that meaning.
#   - Ambiguous wikilink resolution (multiple files share a token's stem)
#     is refused, never silently resolved to the first match.
#   - Prose-only extraction: fenced code blocks, inline code spans, and
#     HTML comments are stripped before scanning for wikilinks, mirroring
#     spec invariant 28's PROSE ONLY rule (`memforge.cli.audit`'s rollup
#     pointer extraction uses the same three-regex approach independently;
#     duplicated here rather than imported, matching this package's
#     existing convention of each cli module owning its own link regexes).
#   - Signed files (a `signature` frontmatter key is present) are refused
#     on --write: mutating signed content without re-signing it would leave
#     a stale, misleading signature, and this tool does not implement the
#     signing/re-signing operation. Scan/dry-run still report candidates.
#   - A pre-existing `relations` value that is not a list (frontmatter
#     already malformed, e.g. hand-edited into a mapping) is refused, never
#     coerced -- `list(some_dict)` silently returns the dict's KEYS, which
#     would corrupt the field rather than extend it.
#   - Sensitivity-crossing guard: promoting an edge from a lower- to a
#     higher-`sensitivity` memory requires an explicit
#     --allow-sensitivity-crossing acknowledgement on --write; scan always
#     flags crossings in its report regardless.
#   - Fails closed on unvalidated predicates: --write ALWAYS requires the
#     controlled vocabulary in spec/taxonomy.yaml to have loaded
#     successfully. There is no override for --write (an earlier
#     --allow-missing-taxonomy escape hatch was removed: an explicit
#     unsafe flag does not make an unvalidated write conformant with
#     the spec's write-boundary MUST).
#   - Skips (does not write) a target file with uncommitted git changes
#     already pending, so the tool's own diff is always cleanly isolable
#     via `git diff`. Tri-state: no git repo detected -> proceed (no
#     guard, best-effort); repo detected and clean -> proceed; repo
#     detected but git itself could not confirm cleanliness -> skip
#     (fail closed on ambiguity, not fail open).
#   - Atomic write: temp file in the same directory, mode bits copied from
#     the original, fsync'd before rename, then os.replace(). A symlinked
#     target path is refused rather than silently replaced with a regular
#     file.
#   - Per-file errors are caught and reported; the run continues to the
#     remaining files, and the process exit code reflects partial failure.
#
# Explicitly NOT covered by this diff (declined-for-now, with reasoning,
# not silently dropped):
#   - Full spec_version / spec_compatible format-refusal-direction
#     enforcement (Doctrine 2's newer-than-me / no-upgrade-path message
#     shaping). NO existing tool in this package enforces spec_compatible
#     today (confirmed by grep before this diff); building that machinery
#     from scratch for this one tool, when the sibling fail-loud-contract
#     branch itself shipped doctrine text with no cross-package
#     enforcement code, is out of proportion to this task's scope. This
#     tool instead does a best-effort, non-normative WARN (see
#     _spec_compatible_warning) comparing spec/VERSION against the loaded
#     taxonomy's declared range, using simple tuple comparison, not a full
#     range grammar.
#   - Cloud-dispatch / recall / export egress redaction of `relations`
#     (the spec's new MUST on those tools). This diff does not touch
#     memory-dedup, memory-recall, memory-index-gen, or any other existing
#     egress path; that is separate follow-on work against each of those
#     tools, out of this diff's stated scope (spec text + one new
#     promotion tool).
#   - A reviewed plan/manifest file as an alternative to --all-matches.
#     --all-matches is the bounded version of the same safety property;
#     a full manifest format is a larger feature for a future revision.
#   - Optimistic-concurrency (read-hash-verify-immediately-before-replace)
#     protection against a concurrent external writer. The dirty-git guard
#     already covers the common case (another AGENT or the operator has a
#     pending edit); a true TOCTOU race against a concurrent process
#     writing the same file in the same instant is a low-probability edge
#     case for a locally-invoked, single-operator CLI tool, not the
#     multi-agent daemon threat model the spec's v0.5+ signing layer
#     targets.
#
# Defaults: per-cwd memory folder + ~/.memforge/global-memory/ (via
# memforge.paths.default_memory_paths()), same as memory-link-rewriter.
# Override with --path (repeatable). --write mode operating with no --path
# override therefore targets the operator's live memory folders by default,
# same as every other write-capable tool in this package; the --write gate
# and dry-run default are the safety boundary, not path selection.

from __future__ import annotations

import argparse
import os
import re
import subprocess
import sys
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path
from typing import Optional

from memforge.frontmatter import (
    parse as _mf_parse, render as _mf_render,
    has_frontmatter as _mf_has_frontmatter,
    validate_frontmatter as _mf_validate_frontmatter,
)
from memforge.paths import default_memory_paths
from memforge.cli.link_rewriter import index_folder, WIKILINK_RE
from memforge.cli.audit_deep import load_taxonomy, find_taxonomy_path

DEFAULT_PATHS = default_memory_paths()

#: Fallback predicate list used ONLY when the taxonomy cannot be loaded at
#: all (help text and the built-in-starter-list warning path on a dry run).
#: NOT enforced via argparse `choices=`: a customized taxonomy
#: may define predicates this list does not know about, and the loaded
#: vocabulary -- not this constant -- is the actual write-time gate.
STARTER_PREDICATES: tuple[str, ...] = (
    "supersedes", "contradicts", "caused-by", "derived-from",
    "applies-to", "refines", "evidences",
)

MEM_URI_TARGET = "mem:{uid}"

SENSITIVITY_ORDER = {"public": 0, "internal": 1, "restricted": 2, "privileged": 3}

#: The exact write-time HARD schema, mirroring spec/SPEC.md invariant 29 /
#: "Validation posture" field-for-field. Applied to the COMPLETE resulting
#: `relations` list before every write (pre-existing entries carried
#: forward PLUS newly added ones), not only to the entries this tool itself
#: constructs: a pre-existing malformed entry that predates this tool must
#: not be silently carried through a write this tool performs, per the
#: spec's write-boundary MUST.
UID_RE = re.compile(r"^mem-\d{4}-\d{2}-\d{2}-[A-Za-z0-9-]+$")
TARGET_RE = re.compile(r"^mem:(mem-\d{4}-\d{2}-\d{2}-[A-Za-z0-9-]+)$")
CREATED_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")


def _validate_relation_entry(entry, vocab: set[str], known_uids: set[str]) -> Optional[str]:
    """Validate ONE `relations` entry against the write-time HARD schema.

    Returns an error string naming the first violation found, or None if
    the entry is fully write-time conformant. `known_uids` is every uid
    already indexed in the folder(s) being operated on (target-resolution
    check); a target that is well-formed but unresolved is refused at
    write time here, not merely WARNed at read time, because this tool
    only ever constructs targets it has already resolved -- an unresolved
    target in the complete list can only be pre-existing content."""
    if not isinstance(entry, dict):
        return "entry is not a mapping"
    for field in ("predicate", "target", "created"):
        if field not in entry:
            return f"missing required field '{field}'"
    predicate = entry.get("predicate")
    if predicate not in vocab:
        return f"predicate {predicate!r} is not in the loaded taxonomy vocabulary"
    target = entry.get("target")
    m = TARGET_RE.fullmatch(str(target))
    if not m:
        return f"target {target!r} is not a well-formed mem:<uid> string"
    if m.group(1) not in known_uids:
        return f"target {target!r} does not resolve to any known uid in the indexed folder(s)"
    created = entry.get("created")
    if not CREATED_RE.fullmatch(str(created)):
        return f"created {created!r} does not match YYYY-MM-DD"
    source = entry.get("source")
    if source is not None and not isinstance(source, str):
        return "source is present but not a string"
    confidence = entry.get("confidence")
    if confidence is not None:
        if isinstance(confidence, bool) or not isinstance(confidence, (int, float)) or not (0.0 <= float(confidence) <= 1.0):
            return f"confidence {confidence!r} is not numeric in [0.0, 1.0]"
    return None

# Prose-only extraction (mirrors spec invariant 28 / memforge.cli.audit's
# independent implementation of the same rule): strip fenced code blocks,
# inline code spans, and HTML comments before scanning for wikilinks, so a
# documentation example or a commented-out note is never promoted as if it
# were a real semantic link.
_FENCE_RE = re.compile(r"```.*?```|~~~.*?~~~", re.S)
_HTML_COMMENT_RE = re.compile(r"<!--.*?-->", re.S)
# Matches a Markdown code span of ANY backtick-run length N (CommonMark's
# rule: opening run of N backticks, content with no run of exactly N
# backticks, closing run of N backticks), not just single backticks --
# `` `[[x]]` `` (double-backtick span) previously slipped through the
# single-backtick-only version.
_CODE_SPAN_RE = re.compile(r"(`+)(?:(?!\1)[\s\S])*?\1")

_SPEC_COMPAT_RE = re.compile(r">=\s*(\d+\.\d+\.\d+)\s*,\s*<\s*(\d+\.\d+\.\d+)")


def _prose_only(body: str) -> str:
    body = _FENCE_RE.sub(" ", body)
    body = _HTML_COMMENT_RE.sub(" ", body)
    body = _CODE_SPAN_RE.sub(" ", body)
    return body


@dataclass
class Candidate:
    memory_relpath: str
    token: str
    target_uid: Optional[str] = None  # None if unresolved or ambiguous
    already_typed: bool = False
    ambiguous: bool = False
    ambiguous_paths: list = field(default_factory=list)
    sensitivity_crossing: bool = False


def _sensitivity_rank(fm: dict) -> int:
    val = (fm or {}).get("sensitivity") or "internal"
    return SENSITIVITY_ORDER.get(val, SENSITIVITY_ORDER["internal"])


def load_predicate_vocab(memforge_root: Optional[Path]) -> tuple[Optional[set[str]], str]:
    """Load the controlled predicate vocabulary from spec/taxonomy.yaml's
    `relations` key.

    Returns (vocab_or_none, status). vocab is None when taxonomy.yaml could
    not be found/loaded at all (distinct from "loaded but relations key
    absent/empty", which returns an empty set -- an intentionally-emptied
    vocabulary is a real state a --write caller must still respect).
    status is a short human-readable reason, always set.
    """
    tax_path = find_taxonomy_path(memforge_root)
    if tax_path is None:
        return None, "taxonomy.yaml not found"
    taxonomy = load_taxonomy(memforge_root)
    if not taxonomy:
        return None, f"{tax_path} could not be parsed"
    relations = taxonomy.get("relations", {})
    if not isinstance(relations, dict):
        return None, f"{tax_path} has a malformed `relations` key (not a mapping)"
    return set(relations.keys()), f"loaded from {tax_path}"


def _spec_compatible_warning(memforge_root: Optional[Path]) -> Optional[str]:
    """Best-effort, non-normative WARN when the loaded taxonomy's declared
    `spec_compatible` range excludes the co-located spec/VERSION.

    Deliberately NOT the full Doctrine 2 format-refusal-direction machinery
    (see the module docstring's "Explicitly NOT covered" section): this is
    a simple tuple comparison over a `>=X.Y.Z,<X.Y.Z` shape, not a general
    range grammar, and a parse failure here is silently skipped rather than
    treated as a refusal. Returns a message string, or None when everything
    parses and is in range (or can't be determined).
    """
    tax_path = find_taxonomy_path(memforge_root)
    if tax_path is None:
        return None
    version_path = tax_path.parent / "VERSION"
    if not version_path.exists():
        return None
    try:
        spec_version = version_path.read_text(encoding="utf-8").strip()
        spec_tuple = tuple(int(p) for p in spec_version.split("."))
    except (OSError, ValueError):
        return None
    taxonomy = load_taxonomy(Path(memforge_root) if memforge_root else None)
    compat = taxonomy.get("spec_compatible") if isinstance(taxonomy, dict) else None
    if not isinstance(compat, str):
        return None
    m = _SPEC_COMPAT_RE.match(compat.strip())
    if not m:
        return None
    try:
        lo = tuple(int(p) for p in m.group(1).split("."))
        hi = tuple(int(p) for p in m.group(2).split("."))
    except ValueError:
        return None
    if not (lo <= spec_tuple < hi):
        return (
            f"{tax_path} declares spec_compatible={compat!r}, which does not "
            f"cover the co-located spec/VERSION ({spec_version}). Predicate "
            "validation is proceeding anyway (this is an advisory WARN, not "
            "the full Doctrine 2 format-refusal this spec eventually wants; "
            "see the module docstring)."
        )
    return None


def _wikilink_target_stem(token: str) -> str:
    """A [[token]] resolves to `token.md` per SPEC.md invariant 28 (the
    same rule the rollup-README reachability check uses). Strip a
    `#anchor` fragment; the fragment addresses a location within the
    target, not a different target."""
    return token.split("#", 1)[0].strip()


def _resolve_target(idx, stem: str):
    """Resolve a wikilink stem to exactly one Memory, or report ambiguity.

    Returns (memory_or_none, ambiguous_relpaths). ALWAYS collects every
    file in the index whose filename stem matches, INCLUDING a root-level
    file, before deciding -- there is deliberately no "check root first,
    short-circuit" path. An earlier version special-cased an exact
    `by_relpath` root lookup ahead of the full stem scan, which meant a
    root-level `dup.md` silently won over an equally-matching
    `subfolder/dup.md` instead of being reported as ambiguous. Exactly one
    of the two return slots is populated on a non-empty result: a unique
    match returns (memory, []); zero matches returns (None, []); 2+
    matches returns (None, [relpath, ...]) rather than silently picking
    any one of them."""
    matches = [m for m in idx.memories if m.path.stem == stem]
    if len(matches) == 1:
        return matches[0], []
    if len(matches) > 1:
        return None, [str(m.relpath) for m in matches]
    return None, []


def scan_folder(idx) -> list[Candidate]:
    """Read-only scan: every [[token]] in every memory body, classified."""
    out: list[Candidate] = []
    for m in idx.memories:
        try:
            text = m.path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            continue
        fm, body = _mf_parse(text)
        prose = _prose_only(body)
        existing_targets = set()
        for rel in (fm.get("relations") or []):
            if isinstance(rel, dict) and rel.get("target"):
                existing_targets.add(str(rel["target"]))

        for wm in WIKILINK_RE.finditer(prose):
            token = wm.group(1).strip()
            stem = _wikilink_target_stem(token)
            target_mem, ambiguous_paths = _resolve_target(idx, stem)

            if ambiguous_paths:
                out.append(Candidate(str(m.relpath), token, ambiguous=True,
                                      ambiguous_paths=ambiguous_paths))
                continue
            if target_mem is None or not target_mem.uid:
                out.append(Candidate(str(m.relpath), token, None))
                continue

            already = MEM_URI_TARGET.format(uid=target_mem.uid) in existing_targets
            crossing = _sensitivity_rank(target_mem.frontmatter) > _sensitivity_rank(fm)
            out.append(Candidate(
                str(m.relpath), token, target_mem.uid,
                already_typed=already, sensitivity_crossing=crossing,
            ))
    return out


def _git_root_for(path: Path) -> tuple[Optional[Path], str]:
    """Returns (root_or_None, state), state one of:

    - "found": `root` is the working-tree root.
    - "confirmed-absent": positive evidence `path.parent` is NOT inside a
      git working tree (git's own `not a git repository` fatal + exit 128).
    - "no-git": git itself is not installed/invocable on this system at all
      (`FileNotFoundError` on exec). Distinct from "confirmed-absent" so
      the caller CAN choose to surface this differently (a missing git
      binary is a statement about the SYSTEM, not about this particular
      folder), even though both currently map to the same "no-repo, proceed
      with no guard" dirty-status outcome.
    - "indeterminate": a repository MIGHT exist but its state could not be
      confirmed (a timeout, a permission error, or any other unexpected git
      failure). The caller MUST treat this as "cannot verify," never as
      "confirmed clean" -- an earlier version of this function collapsed
      every exception and every nonzero exit code to the same `None`, so a
      transient git failure INSIDE a real repository silently disabled the
      dirty guard instead of failing closed.
    """
    try:
        r = subprocess.run(
            ["git", "-C", str(path.parent), "rev-parse", "--show-toplevel"],
            capture_output=True, text=True, timeout=5,
        )
    except FileNotFoundError:
        return None, "no-git"
    except (OSError, subprocess.SubprocessError):
        return None, "indeterminate"
    if r.returncode == 128 and "not a git repository" in r.stderr:
        return None, "confirmed-absent"
    if r.returncode != 0:
        return None, "indeterminate"
    return Path(r.stdout.strip()), "found"


def git_dirty_status(path: Path) -> str:
    """Tri-state (four-state, counting the git-absent sub-case of
    "no-repo") git cleanliness check for a single file.

    Returns one of "no-repo" (proceed, no guard: best-effort convenience,
    not a guaranteed safety property outside a git checkout), "no-git"
    (proceed, same as "no-repo" for write purposes, but distinguishable so
    a caller can warn the operator that the dirty guard is inactive
    system-wide, not merely for this one folder), "clean" (proceed),
    "dirty" (skip: uncommitted changes already pending), or "unknown"
    (skip: EITHER repo detection itself could not be confirmed, OR a repo
    WAS detected but `git status` could not be run or failed -- fail CLOSED
    on ambiguity at either stage, never fail open).

    Design note on "no-git": this tool treats a system with no git
    installed as "the dirty guard is inactive here," the same posture as a
    plain non-repo folder, rather than as "unknown, refuse every write."
    The alternative (fail closed whenever git is unavailable) would make
    --write entirely unusable on any machine without git on PATH, which is
    a materially worse outcome than an explicitly-disclosed inactive guard
    for a tool whose OWN documented design already states the guard is
    best-effort, not a guaranteed safety property. The caller surfaces a
    one-time warning when this state is hit during a --write run (see
    cmd_promote) so the operator is told the guard is off, rather than
    silently unprotected.
    """
    root, state = _git_root_for(path)
    if root is None:
        if state in ("confirmed-absent",):
            return "no-repo"
        if state == "no-git":
            return "no-git"
        return "unknown"  # state == "indeterminate"
    try:
        r = subprocess.run(
            ["git", "-C", str(root), "status", "--porcelain", "--", str(path)],
            capture_output=True, text=True, timeout=5,
        )
    except (OSError, subprocess.SubprocessError):
        return "unknown"
    if r.returncode != 0:
        return "unknown"
    return "dirty" if r.stdout.strip() else "clean"


def _atomic_write(path: Path, text: str) -> None:
    """Temp file in the same directory, original mode bits copied, fsync'd,
    then os.replace(). Refuses a symlinked target rather than silently
    replacing the symlink itself with a regular file."""
    if path.is_symlink():
        raise OSError(f"refusing to write through a symlink: {path}")
    try:
        orig_mode = path.stat().st_mode
    except OSError:
        orig_mode = None
    tmp = path.with_name(f".{path.name}.tmp-{os.getpid()}")
    try:
        with open(tmp, "w", encoding="utf-8") as f:
            f.write(text)
            f.flush()
            os.fsync(f.fileno())
        if orig_mode is not None:
            os.chmod(tmp, orig_mode)
        os.replace(tmp, path)
    finally:
        if tmp.exists():
            try:
                tmp.unlink()
            except OSError:
                pass


def cmd_scan(idx) -> int:
    candidates = scan_folder(idx)
    unresolved = [c for c in candidates if c.target_uid is None and not c.ambiguous]
    ambiguous = [c for c in candidates if c.ambiguous]
    already = [c for c in candidates if c.already_typed]
    promotable = [c for c in candidates if c.target_uid and not c.already_typed]
    crossing = [c for c in promotable if c.sensitivity_crossing]

    print(f"  {len(candidates)} wikilink occurrence(s) scanned")
    print(f"  {len(promotable)} promotable (resolved target, no existing relations entry)")
    for c in promotable[:10]:
        flag = " [sensitivity-crossing]" if c.sensitivity_crossing else ""
        print(f"    {c.memory_relpath}: [[{c.token}]] -> mem:{c.target_uid}{flag}")
    if len(promotable) > 10:
        print(f"    ... +{len(promotable) - 10} more")
    if crossing:
        print(f"  {len(crossing)} of the above cross a sensitivity tier (lower- to higher-sensitivity target)")
    print(f"  {len(already)} already-typed (relations entry already exists for that target)")
    print(f"  {len(ambiguous)} ambiguous (multiple files share the token's stem; refused, not resolved)")
    for c in ambiguous[:5]:
        print(f"    {c.memory_relpath}: [[{c.token}]] -> {c.ambiguous_paths}")
    print(f"  {len(unresolved)} unresolved (no memory file matches the token)")
    for c in unresolved[:5]:
        print(f"    {c.memory_relpath}: [[{c.token}]] (no matching file)")
    if len(unresolved) > 5:
        print(f"    ... +{len(unresolved) - 5} more")
    return 0


def cmd_promote(
    idx,
    *,
    predicate: str,
    vocab: Optional[set[str]],
    source: Optional[str],
    confidence: Optional[float],
    only_tokens: Optional[set[str]],
    only_files: Optional[set[str]],
    write: bool,
    all_matches: bool = False,
    allow_sensitivity_crossing: bool = False,
) -> int:
    scanned = scan_folder(idx)
    ambiguous = [c for c in scanned if c.ambiguous]
    for c in ambiguous:
        print(f"  SKIP (ambiguous target): {c.memory_relpath}: [[{c.token}]] -> {c.ambiguous_paths}")

    candidates = [c for c in scanned if c.target_uid and not c.ambiguous]
    scoped = bool(only_tokens or only_files)
    if only_tokens:
        candidates = [c for c in candidates if c.token in only_tokens]
    if only_files:
        candidates = [c for c in candidates if c.memory_relpath in only_files]

    if write and not scoped and not all_matches:
        print(
            "error: --write with neither --only-token nor --file requires an "
            "explicit --all-matches acknowledgement (one --predicate would "
            "otherwise be stamped onto every resolvable wikilink in the "
            "targeted folder(s); the operator must consciously acknowledge "
            "that blast radius, or scope the run).",
            file=sys.stderr,
        )
        return 2

    if not candidates:
        print("  no promotable candidates match the given filters")
        return 0

    by_file: dict[str, list[Candidate]] = {}
    for c in candidates:
        by_file.setdefault(c.memory_relpath, []).append(c)

    created = date.today().isoformat()
    known_uids = {u for u in idx.by_uid.keys() if u}
    warned_no_git = False
    written = 0
    skipped_dirty = 0
    skipped_signed = 0
    skipped_malformed = 0
    skipped_crossing = 0
    skipped_invalid_schema = 0
    failed = 0
    added_edges = 0

    for relpath, cands in sorted(by_file.items()):
        m = idx.by_relpath[relpath]

        if write:
            status = git_dirty_status(m.path)
            if status == "no-git" and not warned_no_git:
                print(
                    "warning: git is not installed/invocable on this system; the "
                    "uncommitted-changes skip guard is INACTIVE for this entire run "
                    "(best-effort convenience only, never a guaranteed safety "
                    "property outside a git checkout -- see SPEC.md, \"Typed "
                    "relations\" -> \"Reference implementation\")",
                    file=sys.stderr,
                )
                warned_no_git = True
            if status in ("dirty", "unknown"):
                reason = "uncommitted changes pending" if status == "dirty" else "git status could not be verified"
                print(f"  SKIP ({reason}): {relpath}")
                skipped_dirty += 1
                continue

        # Everything from here through the write for THIS file lives inside
        # one outer boundary. The specific skip branches below use `continue`
        # for a deliberate, expected non-write decision (signed memory,
        # malformed pre-existing data, an unacknowledged sensitivity
        # crossing, a schema-invalid resulting list); `continue` inside a
        # `try` exits the try normally without touching `except`, so those
        # branches behave exactly as before. The `except` at the bottom is
        # the safety net for anything UNEXPECTED anywhere in this sequence
        # (read, frontmatter validation, parse, entry construction, schema
        # validation, render, write) -- one file's surprise failure must
        # never abort the run for every other file.
        try:
            text = m.path.read_text(encoding="utf-8")

            # `frontmatter.parse()` gracefully degrades a file whose
            # frontmatter block does not parse as YAML to
            # ({}, ORIGINAL_TEXT) -- by design, for callers that only ever
            # READ. This tool WRITES, and treating that degraded ({}, text)
            # as "no frontmatter, empty dict" would go on to prepend a
            # brand-new frontmatter fence in front of the file's
            # already-broken one, corrupting it further. Refuse instead.
            if write and _mf_has_frontmatter(text):
                ok, why = _mf_validate_frontmatter(text)
                if not ok:
                    print(f"  SKIP (frontmatter does not parse; refusing to write over it: {why}): {relpath}")
                    failed += 1
                    continue

            fm, body = _mf_parse(text)

            if write and "signature" in fm:
                print(f"  SKIP (signed memory; re-signing not implemented by this tool): {relpath}")
                skipped_signed += 1
                continue

            existing_raw = fm.get("relations")
            if existing_raw is not None and not isinstance(existing_raw, list):
                print(f"  SKIP (pre-existing `relations` is not a list; refusing to coerce): {relpath}")
                skipped_malformed += 1
                continue

            relations = list(existing_raw or [])
            existing_pairs = {
                (str(r.get("target")), str(r.get("predicate")))
                for r in relations if isinstance(r, dict)
            }

            new_entries = []
            crossing_here = False
            for c in cands:
                if c.sensitivity_crossing:
                    crossing_here = True
                    if write and not allow_sensitivity_crossing:
                        continue  # this specific edge is skipped, not the whole file
                target = MEM_URI_TARGET.format(uid=c.target_uid)
                if (target, predicate) in existing_pairs:
                    continue  # idempotent: this exact edge already exists
                entry = {"predicate": predicate, "target": target, "created": created}
                if source:
                    entry["source"] = source
                if confidence is not None:
                    entry["confidence"] = confidence
                new_entries.append(entry)
                existing_pairs.add((target, predicate))

            if crossing_here and write and not allow_sensitivity_crossing:
                print(f"  SKIP edge(s) crossing sensitivity tier (pass --allow-sensitivity-crossing to include): {relpath}")
                skipped_crossing += 1

            if not new_entries:
                continue

            print(f"  {relpath}: +{len(new_entries)} relations entr{'y' if len(new_entries)==1 else 'ies'}")
            for e in new_entries:
                print(f"    predicate={e['predicate']} target={e['target']}")

            if write:
                complete = relations + new_entries
                bad = None
                for i, entry in enumerate(complete):
                    reason = _validate_relation_entry(entry, vocab or set(), known_uids)
                    if reason:
                        bad = (i, entry, reason)
                        break
                if bad is not None:
                    i, entry, reason = bad
                    print(
                        f"  SKIP (relations entry #{i} fails the write-time schema: "
                        f"{reason}; entry={entry!r}): {relpath}"
                    )
                    skipped_invalid_schema += 1
                    continue

                fm["relations"] = complete
                new_text = _mf_render(fm, body)
                _atomic_write(m.path, new_text)
                written += 1
            added_edges += len(new_entries)
        except Exception as e:  # noqa: BLE001 -- deliberately broad: the
            # outer per-file boundary. Any unexpected failure anywhere in
            # the sequence above (read, frontmatter validation, parse,
            # entry construction, schema validation, render, write) is
            # caught, reported, and counted here so it can never abort
            # processing of the remaining files in the run.
            print(f"  FAIL ({type(e).__name__}): {relpath}: {e}", file=sys.stderr)
            failed += 1
            continue

    print(f"\n{added_edges} relation edge(s) across {written if write else len(by_file)} file(s)")
    if not write:
        print("(dry-run; no changes written -- pass --write to persist)")
    if skipped_dirty:
        print(f"{skipped_dirty} file(s) skipped: uncommitted or unverifiable git state")
    if skipped_signed:
        print(f"{skipped_signed} file(s) skipped: signed memory, re-signing not implemented")
    if skipped_malformed:
        print(f"{skipped_malformed} file(s) skipped: pre-existing malformed `relations` field")
    if skipped_invalid_schema:
        print(f"{skipped_invalid_schema} file(s) skipped: resulting `relations` list would fail the write-time schema")
    if skipped_crossing:
        print(f"{skipped_crossing} file(s) had edge(s) skipped: sensitivity-tier crossing not acknowledged")
    if ambiguous:
        print(f"{len(ambiguous)} candidate(s) skipped: ambiguous wikilink target")
    if failed:
        print(f"{failed} file(s) FAILED (see stderr)")
        return 1
    return 0


def main() -> int:
    p = argparse.ArgumentParser(
        prog="memory-link-upgrade",
        description=(
            "Promote untyped [[name]] wikilinks to typed `relations` "
            "frontmatter entries (spec 0.9.0, pending release). "
            "[[name]] links remain valid; this tool only ever ADDS "
            "relations entries, never rewrites or removes a wikilink."
        ),
    )
    p.add_argument("--path", action="append", type=Path,
                    help="Memory folder to operate on (repeatable; defaults to per-cwd + global).")
    p.add_argument("--memforge-root", default=None,
                    help="Override memforge repo root for spec/taxonomy.yaml lookup.")
    sub = p.add_subparsers(dest="cmd", required=True)

    sub.add_parser("scan", help="Read-only: list wikilink promotion candidates.")

    p_promote = sub.add_parser(
        "promote",
        help="Add typed relations entries for promotable wikilinks (dry-run by default).",
    )
    p_promote.add_argument(
        "--predicate", required=True,
        help=(
            "REQUIRED. The tool never infers a predicate; the operator states "
            "the relationship. Validated against the loaded spec/taxonomy.yaml "
            f"vocabulary (source of truth); the starter vocabulary ships "
            f"{', '.join(STARTER_PREDICATES)}."
        ),
    )
    p_promote.add_argument("--source", default=None,
                            help="Provenance string recorded on each new relations entry. "
                                 "An unauthenticated, writer-supplied label -- not cryptographic attribution.")
    p_promote.add_argument("--confidence", type=float, default=None,
                            help="Optional float in [0.0, 1.0] recorded on each new entry.")
    p_promote.add_argument("--only-token", action="append", dest="only_tokens", default=None,
                            help="Restrict to wikilink tokens matching exactly (repeatable).")
    p_promote.add_argument("--file", action="append", dest="only_files", default=None,
                            help="Restrict to memory files by relpath (repeatable).")
    p_promote.add_argument("--all-matches", action="store_true",
                            help="Required on --write when neither --only-token nor --file scopes the run: "
                                 "explicit acknowledgement that --predicate applies to every resolvable match.")
    p_promote.add_argument("--allow-sensitivity-crossing", action="store_true",
                            help="Required on --write to persist an edge from a lower- to a higher-`sensitivity` memory.")
    p_promote.add_argument("--write", action="store_true",
                            help="Persist changes. Without this flag, promote is a dry run.")

    args = p.parse_args()
    paths = args.path or DEFAULT_PATHS
    paths = [pth for pth in paths if pth.is_dir()]
    if not paths:
        print("no memory folders found", file=sys.stderr)
        return 2

    memforge_root = Path(args.memforge_root).resolve() if args.memforge_root else None

    if args.cmd == "scan":
        rc = 0
        for root in paths:
            print(f"\n====== {root} ======")
            rc = max(rc, cmd_scan(index_folder(root)))
        return rc

    # promote
    if args.confidence is not None and not (0.0 <= args.confidence <= 1.0):
        print(f"error: --confidence must be in [0.0, 1.0], got {args.confidence}", file=sys.stderr)
        return 2

    vocab, status = load_predicate_vocab(memforge_root)
    if vocab is None:
        if args.write:
            print(
                f"error: spec/taxonomy.yaml predicate vocabulary unavailable ({status}); "
                "refusing to --write an unvalidated predicate. There is no override for "
                "--write; run without --write for a dry-run report against the built-in "
                "starter list.",
                file=sys.stderr,
            )
            return 1
        print(f"warning: {status}; predicate validated against the built-in starter list only",
              file=sys.stderr)
        vocab = set(STARTER_PREDICATES)
    else:
        warn = _spec_compatible_warning(memforge_root)
        if warn:
            print(f"warning: {warn}", file=sys.stderr)

    if args.predicate not in vocab:
        print(
            f"error: predicate '{args.predicate}' is not in the loaded taxonomy vocabulary "
            f"({status}): {sorted(vocab)}",
            file=sys.stderr,
        )
        return 2

    only_tokens = set(args.only_tokens) if args.only_tokens else None
    only_files = set(args.only_files) if args.only_files else None

    rc = 0
    for root in paths:
        print(f"\n====== {root} ======")
        rc = max(rc, cmd_promote(
            index_folder(root),
            predicate=args.predicate,
            vocab=vocab,
            source=args.source,
            confidence=args.confidence,
            only_tokens=only_tokens,
            only_files=only_files,
            write=args.write,
            all_matches=args.all_matches,
            allow_sensitivity_crossing=args.allow_sensitivity_crossing,
        ))
    return rc


if __name__ == "__main__":
    sys.exit(main())
