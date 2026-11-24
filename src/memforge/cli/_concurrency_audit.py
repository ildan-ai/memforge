"""Internal helper: v0.4+ multi-agent concurrency audit invariants.

Imported by `memforge.cli.audit` to fold competing-claim findings into the
existing audit report. Two layers:

  * Tier 1 (HEAD-pure): asymmetric supersession, exactly-one-active for
    `ever_multi_member: true` groups, status enumeration, cross-topic
    replaces, dangling replaces, alias cycle / non-mutual / cap, replaces
    cardinality cap.

  * Tier 2 (commit-log, optional): status transition, `superseded_by` write,
    decision_topic mutation and group-member deletion outside a resolve
    commit for the member's topic; resolve-commit scope violation; config
    edit outside a config-only `memforge: config` commit; waiver-file edit
    outside a waiver-only `memforge: waiver` commit; snooze edit outside a
    single-file `memforge: snooze <topic>` commit; `topic_aliases` edit on a
    current anchor outside its resolve or single-file `memforge: alias
    <topic>` commit. Authority fields are
    compared as parsed frontmatter on both sides of each commit. Walks git
    log over an audit window (default 30 days; floored at 30 by spec
    validation).

The Tier 1 layer is load-bearing and history-independent. Tier 2 is
defense-in-depth and can be skipped on shallow clones or when git history
is not available.

Per spec (SPEC.md §"Multi-agent concurrency: competing claims"):
  - Live set: {active, proposed, gated}
  - Exit set: {superseded, dropped, archived}
  - Valid status enum: union of the two sets above.
"""

from __future__ import annotations

import os
import re
import subprocess
from collections import defaultdict
from pathlib import Path, PurePosixPath
from typing import Any, Iterable, NamedTuple, Optional

import yaml

from memforge._programs import ProgramInFolder, find_program_outside, git_env
from memforge.discovery import read_text_no_follow, walk_md_files
from memforge.frontmatter import has_frontmatter, parse


VALID_STATUSES = {"active", "proposed", "gated", "superseded", "dropped", "archived"}
LIVE_STATUSES = {"active", "proposed", "gated"}
EXIT_STATUSES = {"superseded", "dropped", "archived"}

REPLACES_CARDINALITY_CAP = 20
ALIAS_CARDINALITY_CAP = 10
SLUG_RE = re.compile(r"^[a-z0-9]+(-[a-z0-9]+)*$")
SLUG_RESERVED = {
    "con", "aux", "nul", "prn",
    "com1", "com2", "com3", "com4", "com5", "com6", "com7", "com8", "com9",
    "lpt1", "lpt2", "lpt3", "lpt4", "lpt5", "lpt6", "lpt7", "lpt8", "lpt9",
    ".", "..",
}
SLUG_RESERVED_PATTERN = re.compile(r"^(con|aux|nul|prn|com[0-9]|lpt[0-9])(\..*)?$")
SLUG_MAX_LEN = 64

Finding = tuple[str, str]  # (severity, message); severity ∈ {"BLOCKER", "MAJOR", "WARN"}


# ---------- collection ----------


# Fields the checks below use as dictionary keys or look up in sets.
_TEXT_KEYS = ("status", "uid", "decision_topic")


def collect_state(target: Path) -> tuple[dict[str, list], dict[str, dict[str, Any]]]:
    """Walk all .md files (excluding MEMORY.md and archive/) and parse frontmatter.

    Returns:
      - groups: dict mapping decision_topic slug -> list of (path, fm, body) tuples
      - by_uid: dict mapping uid -> fm (for cross-reference checks)
    """
    groups: dict[str, list] = defaultdict(list)
    by_uid: dict[str, dict[str, Any]] = {}
    for path in sorted(walk_md_files(target)):
        if path.name == "MEMORY.md":
            continue
        if "archive" in path.parts:
            continue
        try:
            text = read_text_no_follow(path, root=target)
        except OSError:
            continue
        if not has_frontmatter(text):
            continue
        fm, body = parse(text)
        if not fm:
            continue
        # A file whose identity fields are not text takes no part in the
        # group checks, which use them as keys; tier 1 reports the file.
        if any(fm.get(k) is not None and not isinstance(fm[k], str) for k in _TEXT_KEYS):
            continue
        uid = fm.get("uid")
        if uid:
            by_uid[uid] = fm
        topic = fm.get("decision_topic")
        if topic:
            groups[topic].append((path, fm, body))
    return groups, by_uid


# ---------- slug validation ----------


def _is_valid_slug(slug: str) -> bool:
    if not isinstance(slug, str) or not slug:
        return False
    if len(slug.encode("utf-8")) > SLUG_MAX_LEN:
        return False
    if slug in SLUG_RESERVED:
        return False
    if SLUG_RESERVED_PATTERN.match(slug):
        return False
    return SLUG_RE.match(slug) is not None


# ---------- tier 1: HEAD-pure invariants ----------


def tier1_findings(
    target: Path,
    groups: dict[str, list],
    by_uid: dict[str, dict[str, Any]],
) -> list[Finding]:
    out: list[Finding] = []

    # Status enumeration BLOCKER + slug validation BLOCKER, applied per file.
    for topic, members in groups.items():
        if not _is_valid_slug(topic):
            for path, _, _ in members:
                out.append((
                    "BLOCKER",
                    f"{path.name}: decision_topic '{topic}' fails slug pipeline "
                    f"(must be lowercase ASCII, hyphen-separated, ≤64 bytes, regex "
                    f"`^[a-z0-9]+(-[a-z0-9]+)*$`, not in reserved-name denylist)",
                ))
        for path, fm, _ in members:
            status = fm.get("status")
            if isinstance(status, str) and status not in VALID_STATUSES:
                out.append((
                    "BLOCKER",
                    f"{path.name}: invalid status value '{status}' "
                    f"(must be one of {sorted(VALID_STATUSES)})",
                ))

    # Status enumeration BLOCKER for memories WITHOUT decision_topic.
    # We need to walk every file (not just grouped ones).
    for path in sorted(walk_md_files(target)):
        if path.name == "MEMORY.md" or "archive" in path.parts:
            continue
        try:
            text = read_text_no_follow(path, root=target)
        except OSError:
            continue
        if not has_frontmatter(text):
            continue
        fm, _ = parse(text)
        if not fm:
            continue
        # One wrongly typed field must be a finding for that file, never an
        # exception that ends the pass for every other file.
        for key in _TEXT_KEYS:
            if fm.get(key) is not None and not isinstance(fm[key], str):
                out.append(("BLOCKER", f"{path.name}: field '{key}' must be text"))
        if isinstance(fm.get("decision_topic"), str) and fm["decision_topic"]:
            continue  # already checked above
        status = fm.get("status")
        if isinstance(status, str) and status not in VALID_STATUSES:
            out.append((
                "BLOCKER",
                f"{path.name}: invalid status value '{status}' "
                f"(must be one of {sorted(VALID_STATUSES)})",
            ))

    # Per-group invariants.
    for topic, members in groups.items():
        out.extend(_tier1_group_findings(topic, members, by_uid))

    # Cross-topic alias mutuality + cycle detection.
    out.extend(_tier1_alias_findings(groups))

    return out


def _tier1_group_findings(
    topic: str,
    members: list,
    by_uid: dict[str, dict[str, Any]],
) -> list[Finding]:
    out: list[Finding] = []

    actives = [(path, fm) for path, fm, _ in members if fm.get("status") == "active"]
    superseded = [(path, fm) for path, fm, _ in members if fm.get("status") == "superseded"]
    live = [(path, fm) for path, fm, _ in members if fm.get("status") in LIVE_STATUSES]

    any_ever_multi = any(fm.get("ever_multi_member") is True for _, fm, _ in members)

    # Exactly-one-active BLOCKER for ever_multi_member groups.
    if any_ever_multi:
        if len(actives) != 1:
            out.append((
                "BLOCKER",
                f"decision_topic '{topic}': ever_multi_member is true but "
                f"{len(actives)} active members (expected exactly 1; "
                f"members={[fm.get('uid') for _, fm, _ in members]})",
            ))

    # Asymmetric supersession BLOCKER.
    winners_by_uid = {fm.get("uid"): fm for _, fm in actives}
    for path, fm in superseded:
        sb = fm.get("superseded_by") or []
        if not isinstance(sb, list) or len(sb) != 1:
            out.append((
                "BLOCKER",
                f"{path.name}: status:superseded but superseded_by has length "
                f"{len(sb) if isinstance(sb, list) else 'invalid'} (expected 1)",
            ))
            continue
        winner_uid = sb[0]
        if winner_uid not in winners_by_uid:
            out.append((
                "BLOCKER",
                f"{path.name}: superseded_by points to '{winner_uid}' which is not "
                f"the sole status:active member of decision_topic '{topic}'",
            ))
            continue
        # Symmetric: winner's replaces MUST list this UID.
        winner_replaces = winners_by_uid[winner_uid].get("replaces") or []
        my_uid = fm.get("uid")
        if my_uid not in winner_replaces:
            out.append((
                "BLOCKER",
                f"asymmetry: {path.name} (uid={my_uid}) lists superseded_by={winner_uid} "
                f"but winner's replaces does not contain {my_uid}",
            ))

    # Cross-topic replaces BLOCKER + dangling replaces BLOCKER + cardinality MAJOR.
    for path, fm, _ in members:
        replaces = fm.get("replaces") or []
        if not isinstance(replaces, list):
            out.append((
                "BLOCKER",
                f"{path.name}: replaces is not a list",
            ))
            continue
        if len(replaces) > REPLACES_CARDINALITY_CAP:
            out.append((
                "MAJOR",
                f"{path.name}: replaces cardinality {len(replaces)} > {REPLACES_CARDINALITY_CAP} cap",
            ))
        my_topic = fm.get("decision_topic")
        for ref_uid in replaces:
            ref_fm = by_uid.get(ref_uid)
            if ref_fm is None:
                out.append((
                    "BLOCKER",
                    f"{path.name}: replaces references '{ref_uid}' which is not "
                    f"on disk in this memory tree (dangling)",
                ))
                continue
            ref_topic = ref_fm.get("decision_topic")
            if ref_topic != my_topic:
                out.append((
                    "BLOCKER",
                    f"{path.name}: replaces references '{ref_uid}' with "
                    f"decision_topic='{ref_topic}' but proposer is in '{my_topic}' "
                    f"(cross-topic replaces forbidden)",
                ))

    # ever_multi_member monotonicity is a Tier 2 / commit-log check; not done here.

    return out


def _tier1_alias_findings(groups: dict[str, list]) -> list[Finding]:
    """Mutuality + cycle + cap on the topic_aliases graph.

    The canonical anchor of a group is the unique status:active member.
    Aliases live on the anchor's frontmatter.
    """
    out: list[Finding] = []

    # Build anchor-by-topic map.
    anchors: dict[str, dict[str, Any]] = {}
    for topic, members in groups.items():
        actives = [fm for _, fm, _ in members if fm.get("status") == "active"]
        if len(actives) == 1:
            anchors[topic] = actives[0]
        # If 0 or 2+ active, anchor is undefined; aliases are inactive but no
        # additional finding here (exactly-one-active BLOCKER already fires).

    # Cap + cycle detection over mutual-alias graph.
    mutual_edges: dict[str, set[str]] = defaultdict(set)
    for topic, anchor_fm in anchors.items():
        aliases = anchor_fm.get("topic_aliases") or []
        if not isinstance(aliases, list):
            out.append((
                "BLOCKER",
                f"decision_topic '{topic}': topic_aliases is not a list on anchor",
            ))
            continue
        if len(aliases) > ALIAS_CARDINALITY_CAP:
            out.append((
                "MAJOR",
                f"decision_topic '{topic}': topic_aliases cardinality {len(aliases)} "
                f"> {ALIAS_CARDINALITY_CAP} cap",
            ))
        for other in aliases:
            if not isinstance(other, str):
                out.append((
                    "BLOCKER",
                    f"decision_topic '{topic}': topic_aliases contains non-string entry",
                ))
                continue
            other_anchor = anchors.get(other)
            if other_anchor is None:
                out.append((
                    "WARN",
                    f"decision_topic '{topic}' lists alias '{other}' but that topic "
                    f"has no canonical anchor (zero or two-plus active members; alias inactive)",
                ))
                continue
            other_aliases = other_anchor.get("topic_aliases") or []
            if topic in other_aliases:
                mutual_edges[topic].add(other)
                mutual_edges[other].add(topic)
            else:
                out.append((
                    "WARN",
                    f"decision_topic '{topic}' lists alias '{other}' but '{other}' "
                    f"does not list '{topic}' back (alias inactive; mutuality required)",
                ))

    # Cycle detection over the mutual-only graph (transitive closure).
    visited: set[str] = set()
    for topic in mutual_edges:
        if topic in visited:
            continue
        cycle = _find_cycle(mutual_edges, topic)
        if cycle is not None:
            visited.update(cycle)
            cycle_str = " → ".join(cycle + [cycle[0]])
            out.append((
                "BLOCKER",
                f"alias cycle detected: {cycle_str}",
            ))
        else:
            # Walk the connected component to mark all visited.
            stack = [topic]
            while stack:
                t = stack.pop()
                if t in visited:
                    continue
                visited.add(t)
                stack.extend(mutual_edges[t] - visited)

    return out


def _find_cycle(graph: dict[str, set[str]], start: str) -> Optional[list[str]]:
    """DFS for a cycle in the mutual-alias graph. Mutual edges are bidirectional;
    a 2-cycle (A↔B) is normal mutuality, NOT a cycle for our purposes. Cycles
    of length ≥3 are the BLOCKER condition.
    """
    parent: dict[str, Optional[str]] = {start: None}
    stack: list[str] = [start]
    while stack:
        node = stack.pop()
        for neighbor in graph.get(node, set()):
            if neighbor == parent.get(node):
                continue  # don't go back the way we came (mutual edge is not a cycle)
            if neighbor in parent:
                # Found cycle: walk back from node and from neighbor to common ancestor.
                cycle = [node]
                cur = parent.get(node)
                while cur is not None and cur != neighbor:
                    cycle.append(cur)
                    cur = parent.get(cur)
                cycle.append(neighbor)
                if len(cycle) >= 3:
                    return cycle
            else:
                parent[neighbor] = node
                stack.append(neighbor)
    return None


# ---------- tier 2: commit-log invariants ----------


class WaiverSet(NamedTuple):
    """Parsed `.memforge/audit-waivers.yaml` (R2).

    commits: explicit short/full SHAs whose Tier-2 prefix-violation is waived.
    superseded_before: ISO date (YYYY-MM-DD); superseded-transition findings on
        commits dated strictly before it are waived (the migration-era floor).
    """

    commits: frozenset[str]
    superseded_before: Optional[str]


_WAIVER_FILE = PurePosixPath(".memforge", "audit-waivers.yaml")


def _load_waivers(target: Path) -> WaiverSet:
    """Load the audit-waiver allowlist for a memory root.

    The waiver file is `<target>/.memforge/audit-waivers.yaml` as committed at
    HEAD. The work-tree copy is never read for waivers: an untracked or locally
    edited file would otherwise waive findings no commit records.
    """
    git, _ = _find_git(target)
    if git is None:
        return WaiverSet(frozenset(), None)
    repo_top = _git_toplevel(git, target)
    if repo_top is None:
        return WaiverSet(frozenset(), None)
    store = _store_in_repo(target, repo_top)
    return _parse_waivers(_committed_text(git, repo_top, str(store / _WAIVER_FILE)))


def _find_git(target: Path) -> tuple[Optional[str], str]:
    """(absolute path of git, "") or (None, why it cannot be used).

    Only absolute PATH entries are searched, and a git inside the audited
    store, or elsewhere in the repository that holds it, is refused: anyone
    who can commit there could have placed it.
    """
    try:
        git = find_program_outside("git", target)
    except ProgramInFolder as exc:
        if exc.folder is target:
            return None, f"git resolves to {exc.path}, inside the audited store"
        return None, (f"git resolves to {exc.path}, inside {exc.folder}, the repository "
                      "that holds the audited store")
    if git is None:
        return None, "git was not found on an absolute PATH entry"
    return git, ""


def _committed_text(git: str, repo_top: Path, path: str) -> Optional[str]:
    """Text of the regular file `path` as committed at HEAD, or None when it is
    absent there, is not a regular file (a committed link), or is not UTF-8."""
    listing = subprocess.run(
        [git, "-C", str(repo_top), "ls-tree", "-z", "HEAD", "--", path],
        capture_output=True, check=False, env=git_env(),
    )
    entry = listing.stdout.split(b"\0")[0] if listing.returncode == 0 else b""
    meta, _, _ = entry.partition(b"\t")
    fields = meta.split()
    if len(fields) != 3 or fields[0] not in (b"100644", b"100755"):
        return None
    blob = subprocess.run(
        [git, "-C", str(repo_top), "cat-file", "blob", os.fsdecode(fields[2])],
        capture_output=True, check=False, env=git_env(),
    )
    if blob.returncode != 0:
        return None
    try:
        return blob.stdout.decode("utf-8")
    except UnicodeDecodeError:
        return None


def _worktree_differs(git: str, repo_top: Path, path: str) -> bool:
    """True when the work-tree or index state of `path` is not what HEAD holds:
    untracked, ignored, staged, edited or removed, or git cannot tell. `path`
    is a file name: a store folder named `mem*` must not match another one."""
    proc = subprocess.run(
        [git, "--literal-pathspecs", "-C", str(repo_top), "status", "--porcelain=v1", "-z",
         "--untracked-files=all", "--ignored", "--", path],
        capture_output=True, check=False, env=git_env(),
    )
    return proc.returncode != 0 or bool(proc.stdout.strip(b"\0"))


def _parse_waivers(text: Optional[str]) -> WaiverSet:
    """Parse waiver-file text into a WaiverSet.

    The file is a frontmatter-free YAML mapping (so `memory-validate` can check
    it). FAIL-CLOSED: absent (None), malformed, or non-mapping text yields an
    EMPTY waiver set, so genuine violations still fire.
    """
    if text is None:
        return WaiverSet(frozenset(), None)
    try:
        data = yaml.safe_load(text)
    except Exception:
        # Fail-closed on ANY parse failure. safe_load can raise more than
        # YAMLError: an unquoted invalid date (`2026-13-99`) raises ValueError
        # from the timestamp constructor. A waiver loader that crashes is worse
        # than one that ignores a malformed file, so the contract is empty-set
        # on every malformation.
        return WaiverSet(frozenset(), None)
    if not isinstance(data, dict):
        return WaiverSet(frozenset(), None)
    # FAIL-CLOSED on a non-list waived_commits (a scalar like `waived_commits: 1234567`
    # would otherwise raise TypeError on iteration, or a bare string would iterate
    # per-character). Anything that is not a list yields no commit waivers.
    raw = data.get("waived_commits")
    if not isinstance(raw, list):
        raw = []
    # Only honor sha entries >= 7 chars so a stray short token cannot waive
    # broadly by prefix-match.
    commits = frozenset(
        str(c).strip() for c in raw if isinstance(c, (str, int)) and len(str(c).strip()) >= 7
    )
    # FAIL-CLOSED on a non-date cutoff. `str(before)[:10]` on a YAML bool / int
    # (e.g. `superseded_transition_waived_before: true` -> "True") would, via
    # string comparison, waive ALL superseded transitions ("2026-..." < "True").
    # Require a real YYYY-MM-DD; reject anything else to None (no cutoff).
    raw_before = data.get("superseded_transition_waived_before")
    before = _coerce_iso_date(raw_before)
    return WaiverSet(commits, before)


def _coerce_iso_date(value: Any) -> Optional[str]:
    """Return a canonical YYYY-MM-DD string iff `value` is a valid ISO date,
    else None. yaml.safe_load may hand back a datetime.date (unquoted ISO date)
    or a string; a bool/int/garbage value yields None (fail-closed)."""
    import datetime
    if isinstance(value, datetime.date):  # safe_load parses an unquoted date
        return value.isoformat()
    if not isinstance(value, str):
        return None
    try:
        return datetime.date.fromisoformat(value.strip()[:10]).isoformat()
    except ValueError:
        return None


def _is_waived(sha: str, commit_date: str, kind: str, waivers: WaiverSet) -> bool:
    """True iff this (commit, finding) pair is covered by the waiver set."""
    for w in waivers.commits:
        if sha.startswith(w):  # w may be short (8) or full (40); sha is full
            return True
    if (
        waivers.superseded_before
        and "superseded" in kind.lower()
        and commit_date
        and commit_date[:10] < waivers.superseded_before
    ):
        return True
    return False


def tier2_findings(
    target: Path,
    *,
    audit_window_days: int = 30,
) -> list[Finding]:
    """Walk recent commit history for prefix-violation BLOCKERs.

    Only fires if `target` is inside a git repo. Walks `audit_window_days` of
    commit log. A `memforge:` prefix is a claimed scope, not an exemption: a
    resolve commit covers only the members of the topic it names and that
    topic's snooze file, so every other change in it is checked as if the
    commit had no prefix, and the commit itself is a scope violation.

    A merge is judged by what it changes on its first parent. A change that a
    merged branch's own commits made, and that this walk audits on those
    commits, is not reported again on the merge; a change the merge itself
    brings in, or one whose branch commits fall outside the window, is.

    R2: a finding whose commit is covered by `.memforge/audit-waivers.yaml`, as
    committed at HEAD, is suppressed from the BLOCKER list but REPORTED in a
    visible "N waived" summary (waivers are never silent). Findings on a commit
    that changes the waiver file are never waived.

    Implementation note: this is a best-effort defense-in-depth layer. Tier 1
    is load-bearing. Force-push or shallow-clone scenarios reduce Tier 2
    coverage; the spec acknowledges this as a residual git-layer threat.
    """
    out: list[Finding] = []
    waived: list[str] = []
    git, problem = _find_git(target)
    if git is None:
        out.append((
            "WARN",
            f"Tier 2 commit-log audit SKIPPED: {problem}; commit-log invariants "
            "were NOT evaluated",
        ))
        return out
    repo_top = _git_toplevel(git, target)
    if repo_top is None:
        # Not a git repo, or the git binary is unavailable. Surface a WARN so a
        # partner running --strict on a shallow CI checkout (or without git)
        # does not read silence as "commit-log invariants passed" when they were
        # never evaluated (closes recall-06).
        out.append((
            "WARN",
            "Tier 2 commit-log audit SKIPPED: no git toplevel for "
            f"{target} (not a git repo, git unavailable, or git's answer is "
            "empty, relative or not a folder that holds it); commit-log "
            "invariants were NOT evaluated",
        ))
        return out

    # Build list of recent commits, their parents and their messages. Fields
    # and records are NUL-separated: a subject may hold any other character,
    # line breaks included, and must stay one commit's subject.
    try:
        log_proc = subprocess.run(
            [git, "-C", str(repo_top), "log", "-z",
             f"--since={audit_window_days}.days",
             "--pretty=format:%H%x00%P%x00%cI%x00%s"],
            capture_output=True, text=True, check=True, env=git_env(),
        )
    except (subprocess.CalledProcessError, FileNotFoundError) as exc:
        out.append((
            "WARN",
            "Tier 2 commit-log audit SKIPPED: `git log` failed for "
            f"{repo_top} ({type(exc).__name__}); commit-log invariants were "
            "NOT evaluated",
        ))
        return out

    fields = log_proc.stdout.split("\0") if log_proc.stdout else []
    records = [fields[i:i + 4] for i in range(0, len(fields), 4)]
    if len(fields) % 4:
        out.append((
            "WARN",
            "Tier 2 commit-log audit SKIPPED: `git log` for "
            f"{repo_top} printed a line that is not a commit record; commit-log "
            "invariants were NOT evaluated",
        ))
        return out

    store = _store_in_repo(target, repo_top)
    repo = _Repo(git, repo_top, {}, {})
    waiver_path = str(store / _WAIVER_FILE)
    waivers = _parse_waivers(_committed_text(git, repo_top, waiver_path))
    if _worktree_differs(git, repo_top, waiver_path):
        out.append((
            "WARN",
            f"work-tree {waiver_path} is not the version committed at HEAD "
            "(untracked, staged, edited or removed); only the committed "
            "waiver file is honored",
        ))
    groups, _ = collect_state(target)
    anchors = _anchor_uids(groups)
    audited = {parts[0] for parts in records}

    for sha, parent_text, commit_date, subject in records:
        parents = parent_text.split()
        paths = _changed_paths(repo, sha, parents)
        if paths is None:
            out.append((
                "WARN",
                f"commit {sha[:8]} ({subject!r}): could not list its changed "
                "files; its commit-log invariants were NOT evaluated",
            ))
            continue
        # A waiver cannot vouch for a commit that wrote the waiver file: the
        # file is the thing under review there.
        waivable = waiver_path not in paths
        findings = _commit_findings(
            repo, store, sha, parents[0] if parents else None, subject, paths, anchors
        )
        if len(parents) > 1:
            findings = _not_made_by_merged_sides(repo, store, sha, parents, paths, anchors,
                                                 findings, audited)
        for severity, kind, path, message in findings:
            if waivable and _is_waived(sha, commit_date, kind, waivers):
                waived.append(sha[:8])
                continue
            out.append((severity, f"commit {sha[:8]} ({subject!r}): {kind} on {path} {message}"))

    if waived:
        uniq = sorted(set(waived))
        out.append((
            "WARN",
            f"{len(waived)} commit-log invariant finding(s) waived per "
            f".memforge/audit-waivers.yaml (commits: {', '.join(uniq)})",
        ))

    return out


_OPERATIONS = ("resolve", "snooze", "alias", "config-major", "config", "drop", "waiver")

# A committed link chain longer than this is not followed further; the file
# system refuses far shorter chains (ELOOP), so the loader reads nothing there.
_MAX_LINK_HOPS = 40

_LINK_MODE = b"120000"


class _Repo(NamedTuple):
    """The git program, the repository top, and per-revision caches of the
    committed tree (path -> (mode, object id)) and its links (casefolded path
    -> (path, link text))."""

    git: str
    top: Path
    trees: dict[str, Optional[dict[str, tuple[bytes, str]]]]
    links: dict[str, dict[str, tuple[str, str]]]

    def run(self, *args: str) -> subprocess.CompletedProcess:
        # A path given here is a file name, never a pattern: a committed file
        # named `*.md` must not stand for every other one.
        return subprocess.run(
            [self.git, "--literal-pathspecs", "-C", str(self.top), *args],
            capture_output=True, check=False, env=git_env(),
        )


def _anchor_uids(groups: dict[str, list]) -> set[str]:
    """uids of the current canonical anchors: the sole active member of each
    group, chosen as Tier 1 chooses them."""
    anchors = set()
    for members in groups.values():
        actives = [fm.get("uid") for _, fm, _ in members if fm.get("status") == "active"]
        if len(actives) == 1 and isinstance(actives[0], str):
            anchors.add(actives[0])
    return anchors


def _commit_operation(subject: str) -> tuple[Optional[str], str]:
    """Return (operation, argument) for a `memforge: <operation> <argument>`
    subject, or (None, "") for any other subject. `config-major` counts as
    `config`: both are config commits (SPEC.md §"Config file")."""
    for op in _OPERATIONS:
        prefix = f"memforge: {op}"
        if subject == prefix or subject.startswith(prefix + " "):
            rest = subject[len(prefix):].split()
            return ("config" if op == "config-major" else op), (rest[0] if rest else "")
    return None, ""


def _git_toplevel(git: str, start: Path) -> Optional[Path]:
    """The top level git reports for `start`, or None when git fails or the
    answer is empty, relative, or not a folder at or above `start`: a relative
    answer is read against the current folder, so the audit would judge some
    other folder's history, or none, as this store's."""
    try:
        result = subprocess.run(
            [git, "-C", str(start), "rev-parse", "--show-toplevel"],
            capture_output=True, text=True, check=True, env=git_env(),
        )
    except (subprocess.CalledProcessError, FileNotFoundError):
        return None
    top = result.stdout.strip()
    if not os.path.isabs(top) or not start.resolve().is_relative_to(Path(top).resolve()):
        return None
    return Path(top)


def _store_in_repo(target: Path, repo_top: Path) -> PurePosixPath:
    """`target` relative to the top `_git_toplevel` returned for it."""
    return PurePosixPath(target.resolve().relative_to(repo_top.resolve()).as_posix())


def _changed_paths(repo: _Repo, sha: str, parents: list[str]) -> Optional[list[str]]:
    """Repo-relative paths that `sha` changes, or None if git cannot list them.

    No rename detection, so a moved file shows as its old and its new path.
    A merge lists every path it changes on its first parent, including a path
    whose result equals another parent, which `git show` leaves out.
    """
    if len(parents) > 1:
        args = ("diff", "--name-only", "--no-renames", "--no-ext-diff", "-z", parents[0], sha)
    else:
        args = ("show", "--name-only", "--no-renames", "-z", "--format=", sha)
    proc = repo.run(*args)
    if proc.returncode != 0:
        return None
    return [os.fsdecode(p) for p in proc.stdout.split(b"\0") if p.strip()]


def _frontmatter_at(repo: _Repo, rev: Optional[str], path: str) -> Optional[dict[str, Any]]:
    """Parsed frontmatter of `path` at `rev`, or None when it is absent there
    (or `rev` is None: the parent of a root commit).

    A file whose frontmatter does not decode or parse yields {}: it carries no
    authority fields, so a member that loses them reads as changed, not clean.
    """
    if rev is None:
        return None
    proc = repo.run("cat-file", "blob", f"{rev}:{path}")
    if proc.returncode != 0:
        return None
    try:
        text = proc.stdout.decode("utf-8")
    except UnicodeDecodeError:
        return {}
    return parse(text)[0]


def _tree(repo: _Repo, rev: str) -> Optional[dict[str, tuple[bytes, str]]]:
    """Every committed entry at `rev` as path -> (mode, object id), or None
    when git cannot list the tree."""
    if rev not in repo.trees:
        proc = repo.run("ls-tree", "-r", "-z", "--full-tree", rev)
        entries: Optional[dict[str, tuple[bytes, str]]] = None
        if proc.returncode == 0:
            entries = {}
            for record in proc.stdout.split(b"\0"):
                meta, tab, name = record.partition(b"\t")
                fields = meta.split()
                if tab and len(fields) == 3:
                    entries[os.fsdecode(name)] = (fields[0], os.fsdecode(fields[2]))
        repo.trees[rev] = entries
    return repo.trees[rev]


def _links(repo: _Repo, rev: str) -> dict[str, tuple[str, str]]:
    """The committed symbolic links at `rev`: casefolded path -> (path, link
    text). Casefolded because macOS and Windows open a path in any case."""
    if rev not in repo.links:
        found: dict[str, tuple[str, str]] = {}
        for path, (mode, oid) in (_tree(repo, rev) or {}).items():
            if mode == _LINK_MODE:
                proc = repo.run("cat-file", "blob", oid)
                if proc.returncode == 0:
                    found[path.casefold()] = (path, os.fsdecode(proc.stdout))
        repo.links[rev] = found
    return repo.links[rev]


def _opened_path(repo: _Repo, rev: str, path: str) -> tuple[list[str], Optional[str]]:
    """(links, final) for opening repo-relative `path` in the tree of `rev`,
    one name at a time as the file system does: a link is followed where the
    walk meets it, so a `..` after it climbs from where the link led, and an
    absolute target under the repository top is that repository path.

    `links` lists each committed link the open passes through; `final` is the
    path it ends at, or None when the chain cannot be followed inside the
    repository (a target above the top or outside it, or more than
    `_MAX_LINK_HOPS` links), where git holds nothing to judge.
    """
    links = _links(repo, rev)
    pending = list(PurePosixPath(path).parts)
    done: list[str] = []
    passed: list[str] = []
    while pending:
        name = pending.pop(0)
        if name in ("", "."):
            continue
        if name == "..":
            if not done:
                return passed, None
            done.pop()
            continue
        link = links.get("/".join([*done, name]).casefold())
        if link is None:
            done.append(name)
            continue
        passed.append(link[0])
        if len(passed) > _MAX_LINK_HOPS:
            return passed, None
        target = link[1].replace("\\", "/")
        if target.startswith("/") or re.match(r"[A-Za-z]:", target):
            below_top = _below_top(repo.top, target)
            if below_top is None:
                return passed, None
            done = []
            pending = below_top + pending
        else:
            # Read from the link's own folder, which is where the walk is.
            pending = target.split("/") + pending
    return passed, "/".join(done)


def _below_top(top: Path, target: str) -> Optional[list[str]]:
    """The names below the repository `top` that absolute link text `target`
    names, or None when it does not lead through `top`. The top is matched by
    identity, so another spelling of the same folder (a linked parent, as
    `/var` is on macOS) still counts as inside."""
    parts = Path(target).parts
    if not Path(target).is_absolute():
        return None
    for i in range(1, len(parts) + 1):
        try:
            if os.path.samefile(Path(*parts[:i]), top):
                return list(parts[i:])
        except OSError:
            continue
    return None


class _GatedPaths(NamedTuple):
    """Casefolded repo-relative paths the config and snooze gates cover in one
    commit: what the loaders could open before or after it."""

    configs: set[str]
    config_exits: set[str]
    snooze_links: set[str]
    snooze_dirs: set[str]


def _gated_paths(repo: _Repo, store: PurePosixPath, revs: list[str]) -> _GatedPaths:
    """The config file `load_config` can find for this store sits in the store
    or an ancestor folder inside the repo, and it is opened with links
    followed and, on macOS and Windows, in any case. So every spelling of the
    path, every committed link on the way and the file a link chain ends at
    are config paths. A chain that cannot be followed inside the repository
    at the last of `revs` (the commit judged) puts its links in
    `config_exits`: what the loader reads there is out of the audit's sight.
    The snooze folder is read the same way."""
    configs: set[str] = set()
    config_exits: set[str] = set()
    for folder in (store, *store.parents):
        literal = folder / ".memforge" / "config.yaml"
        configs.update({str(literal).casefold(), str(literal.parent).casefold()})
        for rev in revs:
            passed, final = _opened_path(repo, rev, str(literal))
            configs.update(p.casefold() for p in passed)
            if final is not None:
                configs.add(final.casefold())
            elif rev == revs[-1]:
                config_exits.update(p.casefold() for p in passed)
    snoozes = store / ".memforge" / "snoozes"
    snooze_links: set[str] = set()
    snooze_dirs = {str(snoozes).casefold()}
    for rev in revs:
        passed, final = _opened_path(repo, rev, str(snoozes))
        snooze_links.update(p.casefold() for p in passed)
        if final is not None:
            snooze_dirs.add(final.casefold())
    return _GatedPaths(configs, config_exits, snooze_links, snooze_dirs)


def _authority_changes(
    before: Optional[dict[str, Any]], after: Optional[dict[str, Any]]
) -> list[str]:
    """Kinds of authority change from `before` to `after` (None = absent)."""
    if after is None:
        if before is not None and before.get("decision_topic"):
            return ["group member deletion"]
        return []
    old = before or {}
    kinds = []
    if after.get("status") == "superseded" and old.get("status") != "superseded":
        kinds.append("status->superseded transition")
    if after.get("superseded_by") and after.get("superseded_by") != old.get("superseded_by"):
        kinds.append("superseded_by write")
    if old.get("decision_topic") and after.get("decision_topic") != old.get("decision_topic"):
        kinds.append("decision_topic mutation")
    return kinds


def _alias_changed(
    before: Optional[dict[str, Any]], after: Optional[dict[str, Any]], anchors: set[str]
) -> bool:
    """True when `after` is a current anchor and its `topic_aliases` differ from
    `before` (an absent file or key reads as no aliases)."""
    uid = after.get("uid") if after is not None else None
    if not isinstance(uid, str) or uid not in anchors:
        return False
    return ((before or {}).get("topic_aliases") or []) != (after.get("topic_aliases") or [])


def _in_archive(store: PurePosixPath, path: str) -> bool:
    """True for a path under an `archive/` folder of the store, which the live
    set (`collect_state`) leaves out."""
    return "archive" in PurePosixPath(path).relative_to(store).parts


_AUTHORITY_MESSAGE = "without `memforge: resolve` prefix for its decision_topic"


def _memory_sides(
    repo: _Repo, store: PurePosixPath, old_rev: Optional[str], new_rev: str, paths: list[str]
) -> tuple[dict[str, list[Optional[dict[str, Any]]]], dict[str, str]]:
    """(sides, origin) for the memory files among `paths`, from `old_rev` to
    `new_rev`: sides maps each path to [before, after] frontmatter (None =
    absent); origin maps a moved memory's new path to its old one.

    A memory moved inside the store keeps its uid. Its new path is compared
    with its old content, and the old path is not a deletion.
    """
    sides: dict[str, list[Optional[dict[str, Any]]]] = {}
    for p in paths:
        pp = PurePosixPath(p)
        if pp.suffix == ".md" and pp.is_relative_to(store):
            sides[p] = [_frontmatter_at(repo, old_rev, p), _frontmatter_at(repo, new_rev, p)]
    gone: dict[str, str] = {}
    for p, (before, after) in sides.items():
        if before is not None and after is None and isinstance(before.get("uid"), str):
            gone.setdefault(before["uid"], p)
    origin: dict[str, str] = {}
    for p, side in sides.items():
        before, after = side
        uid = after.get("uid") if after is not None else None
        if (
            before is None and isinstance(uid, str) and uid in gone
            and gone[uid] not in origin.values()
        ):
            side[0] = sides[gone[uid]][0]
            origin[p] = gone[uid]
    return sides, origin


def _memory_kinds(
    store: PurePosixPath,
    path: str,
    side: list[Optional[dict[str, Any]]],
    origin: Optional[str],
    anchors: set[str],
) -> list[tuple[str, str, str]]:
    """(severity, kind, message) for each authority change to one memory."""
    before, after = side
    out = [("BLOCKER", kind, _AUTHORITY_MESSAGE) for kind in _authority_changes(before, after)]
    # A live member moved into archive/ leaves its group as surely as a delete.
    # A superseded, dropped or archived member may move there (spec §"Status").
    if (
        origin is not None
        and _in_archive(store, path)
        and not _in_archive(store, origin)
        and before is not None
        and before.get("decision_topic")
        and before.get("status") not in EXIT_STATUSES
    ):
        out.append((
            "BLOCKER", "group member deletion",
            f"(live member moved from {origin} into archive/) {_AUTHORITY_MESSAGE}",
        ))
    if _alias_changed(before, after, anchors):
        out.append((
            "MAJOR", "alias edit",
            "outside a `memforge: resolve <topic>` commit or a single-file "
            "`memforge: alias <topic>` commit for its decision_topic",
        ))
    return out


def _commit_findings(
    repo: _Repo,
    store: PurePosixPath,
    sha: str,
    parent: Optional[str],
    subject: str,
    paths: list[str],
    anchors: set[str],
) -> list[tuple[str, str, str, str]]:
    """(severity, kind, path, message) for each Tier 2 violation in one commit,
    judged from `parent` (None for a root commit) to `sha`.

    `store` is the memory root relative to the repo top; `anchors` holds the
    uids of the current canonical anchors.
    """
    op, arg = _commit_operation(subject)
    sides, origin = _memory_sides(repo, store, parent, sha, paths)
    moved = set(origin.values())
    gated = _gated_paths(repo, store, [r for r in (parent, sha) if r])
    own_snoozes = {f"{d}/{arg}.yaml".casefold() for d in gated.snooze_dirs} if arg else set()
    waiver = str(store / _WAIVER_FILE)
    scope = f"(not a `{arg}` group member or its snooze file)"
    out: list[tuple[str, str, str, str]] = []
    for p in paths:
        key = p.casefold()
        own_snooze = key in own_snoozes
        if p == waiver and (op != "waiver" or len(paths) > 1):
            out.append((
                "BLOCKER", "waiver edit", p,
                "outside a `memforge: waiver` commit that touches only the waiver file",
            ))
        if key in gated.configs and (op != "config" or len(paths) > 1):
            out.append((
                "BLOCKER", "config edit", p,
                "outside a `memforge: config` commit that touches only the config file",
            ))
        if key in gated.config_exits:
            out.append((
                "BLOCKER", "config link", p,
                "that cannot be followed to a file inside the repository",
            ))
        is_snooze = key in gated.snooze_links or any(
            key.startswith(d + "/") for d in gated.snooze_dirs
        )
        # memforge-resolve refuses to delete a snooze through a link, so a
        # committed one there is either an attack or a store resolve cannot use.
        if is_snooze and key in _links(repo, sha):
            out.append((
                "BLOCKER", "snooze folder link", p,
                "(memforge-resolve refuses a link on the way to or under `.memforge/snoozes`)",
            ))
        if is_snooze and not (
            own_snooze and (op == "resolve" or (op == "snooze" and len(paths) == 1))
        ):
            out.append((
                "MAJOR", "snooze edit", p,
                "outside a `memforge: snooze <topic>` commit that touches only that snooze file",
            ))
        if p not in sides:
            if op == "resolve" and not own_snooze:
                out.append(("BLOCKER", "resolve-commit scope violation", p, scope))
            continue
        if p in moved:
            continue
        before, after = sides[p]
        in_topic = bool(arg) and all(
            fm.get("decision_topic") == arg for fm in (before, after) if fm is not None
        )
        if op == "resolve" and in_topic:
            continue
        if op == "resolve":
            out.append(("BLOCKER", "resolve-commit scope violation", p, scope))
        for severity, kind, message in _memory_kinds(store, p, sides[p], origin.get(p), anchors):
            if kind == "alias edit" and op == "alias" and in_topic and len(paths) == 1:
                continue
            out.append((severity, kind, p, message))
    return out


def _not_made_by_merged_sides(
    repo: _Repo,
    store: PurePosixPath,
    sha: str,
    parents: list[str],
    paths: list[str],
    anchors: set[str],
    findings: list[tuple[str, str, str, str]],
    audited: set[str],
) -> list[tuple[str, str, str, str]]:
    """The findings on merge `sha` that no merged branch made itself.

    A merged branch's net change is what its own commits did since the merge
    base. A path the merge takes unchanged from that branch (the first parent
    still holds the base version) is the branch's change. So is an authority
    change the branch made to a memory since the base, but only where the
    merge keeps the branch's authority fields for it: a merge that writes
    another value of the same kind made that value itself. Either way the
    finding is left to the branch's commits only when every one of them that
    touched the path is in `audited` (the commits this walk checks); a branch
    commit older than the window is never checked, so the merge keeps the
    finding. Anything else the merge brings onto its first parent is its
    own: a side picked against a change on the first parent, a branch with
    no merge base, or a new result.
    """
    flagged = {f[2] for f in findings}
    made: dict[str, set[str]] = defaultdict(set)
    first = parents[0]
    for other in parents[1:]:
        proc = repo.run("merge-base", first, other)
        base = proc.stdout.decode("ascii", "replace").strip() if proc.returncode == 0 else ""
        if not base:
            continue
        trees = [_tree(repo, rev) for rev in (base, first, other, sha)]
        if any(t is None for t in trees):
            continue
        t_base, t_first, t_other, t_merge = trees
        branch_made: dict[str, set[str]] = {}
        for p in flagged:
            if t_base.get(p) == t_first.get(p) and t_merge.get(p) == t_other.get(p):
                branch_made[p] = {"*"}
        sides, origin = _memory_sides(repo, store, base, other, paths)
        moved = set(origin.values())
        for p, side in sides.items():
            if p not in flagged or p in moved or p in branch_made:
                continue
            if _authority_fields(side[1]) != _authority_fields(_frontmatter_at(repo, sha, p)):
                continue
            kinds = {kind for _, kind, _ in _memory_kinds(store, p, side, origin.get(p), anchors)}
            if kinds:
                branch_made[p] = kinds
        for p, kinds in branch_made.items():
            touched = [p] + ([origin[p]] if p in origin else [])
            if _branch_commits_audited(repo, base, other, touched, audited):
                made[p] |= kinds
    return [f for f in findings if not ({"*", f[1]} & made.get(f[2], set()))]


# The frontmatter fields whose change `_memory_kinds` reports.
_AUTHORITY_FIELDS = ("status", "superseded_by", "decision_topic", "topic_aliases")


def _authority_fields(fm: Optional[dict[str, Any]]) -> Optional[tuple[Any, ...]]:
    """The authority field values of one memory, or None when it is absent."""
    return None if fm is None else tuple(fm.get(k) for k in _AUTHORITY_FIELDS)


def _branch_commits_audited(
    repo: _Repo, base: str, tip: str, paths: list[str], audited: set[str]
) -> bool:
    """True when the branch from `base` to `tip` has commits that touched
    `paths` and every one of them is in `audited`; False when it has none or
    git cannot list them."""
    proc = repo.run("rev-list", "--full-history", f"{base}..{tip}", "--", *paths)
    if proc.returncode != 0:
        return False
    commits = set(proc.stdout.decode("ascii", "replace").split())
    return bool(commits) and commits <= audited


# ---------- public entry point ----------


def run_concurrency_audit(
    target: Path,
    *,
    audit_window_days: int = 30,
    skip_tier2: bool = False,
) -> tuple[list[Finding], list[Finding], list[Finding]]:
    """Run Tier 1 + Tier 2 concurrency audits on `target`.

    Returns three lists keyed by severity: (blockers, majors, warns).
    Caller folds these into its own audit report.
    """
    groups, by_uid = collect_state(target)
    findings: list[Finding] = list(tier1_findings(target, groups, by_uid))
    if not skip_tier2:
        findings.extend(tier2_findings(target, audit_window_days=audit_window_days))

    blockers = [f for f in findings if f[0] == "BLOCKER"]
    majors = [f for f in findings if f[0] == "MAJOR"]
    warns = [f for f in findings if f[0] == "WARN"]
    return blockers, majors, warns
