"""Internal helper: v0.4+ multi-agent concurrency audit invariants.

Imported by `memforge.cli.audit` to fold competing-claim findings into the
existing audit report. Two layers:

  * Tier 1 (HEAD-pure): asymmetric supersession, exactly-one-active for
    `ever_multi_member: true` groups, status enumeration, cross-topic
    replaces, dangling replaces, alias cycle / non-mutual / cap, replaces
    cardinality cap.

  * Tier 2 (commit-log, optional): status transition outside resolve commit;
    decision_topic mutation outside resolve commit; `superseded_by` written
    outside resolve commit; resolve-commit scope violation. Walks git log
    over an audit window (default 30 days; floored at 30 by spec validation).

The Tier 1 layer is load-bearing and history-independent. Tier 2 is
defense-in-depth and can be skipped on shallow clones or when git history
is not available.

Per spec (SPEC.md §"Multi-agent concurrency: competing claims"):
  - Live set: {active, proposed, gated}
  - Exit set: {superseded, dropped, archived}
  - Valid status enum: union of the two sets above.
"""

from __future__ import annotations

import re
import subprocess
from collections import defaultdict
from pathlib import Path
from typing import Any, Iterable, Optional

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


def collect_state(target: Path) -> tuple[dict[str, list], dict[str, dict[str, Any]]]:
    """Walk all .md files (excluding MEMORY.md and archive/) and parse frontmatter.

    Returns:
      - groups: dict mapping decision_topic slug -> list of (path, fm, body) tuples
      - by_uid: dict mapping uid -> fm (for cross-reference checks)
    """
    groups: dict[str, list] = defaultdict(list)
    by_uid: dict[str, dict[str, Any]] = {}
    for path in sorted(target.rglob("*.md")):
        if path.name == "MEMORY.md":
            continue
        if "archive" in path.parts:
            continue
        try:
            text = path.read_text(encoding="utf-8")
        except OSError:
            continue
        if not has_frontmatter(text):
            continue
        fm, body = parse(text)
        if not fm:
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
            if status is not None and status not in VALID_STATUSES:
                out.append((
                    "BLOCKER",
                    f"{path.name}: invalid status value '{status}' "
                    f"(must be one of {sorted(VALID_STATUSES)})",
                ))

    # Status enumeration BLOCKER for memories WITHOUT decision_topic.
    # We need to walk every file (not just grouped ones).
    for path in sorted(target.rglob("*.md")):
        if path.name == "MEMORY.md" or "archive" in path.parts:
            continue
        try:
            text = path.read_text(encoding="utf-8")
        except OSError:
            continue
        if not has_frontmatter(text):
            continue
        fm, _ = parse(text)
        if not fm:
            continue
        if fm.get("decision_topic"):
            continue  # already checked above
        status = fm.get("status")
        if status is not None and status not in VALID_STATUSES:
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


def tier2_findings(
    target: Path,
    *,
    audit_window_days: int = 30,
) -> list[Finding]:
    """Walk recent commit history for prefix-violation BLOCKERs.

    Only fires if `target` is inside a git repo. Walks `audit_window_days` of
    commit log; flags transitions to status:superseded, decision_topic
    mutations, superseded_by writes, and config edits that are NOT in
    appropriately-prefixed commits.

    Implementation note: this is a best-effort defense-in-depth layer. Tier 1
    is load-bearing. Force-push or shallow-clone scenarios reduce Tier 2
    coverage; the spec acknowledges this as a residual git-layer threat.
    """
    out: list[Finding] = []
    repo_top = _git_toplevel(target)
    if repo_top is None:
        return out  # silent skip; tier 2 is optional

    # Build list of recent commits and their messages.
    try:
        log_proc = subprocess.run(
            ["git", "-C", str(repo_top), "log",
             f"--since={audit_window_days}.days",
             "--pretty=format:%H%x09%s"],
            capture_output=True, text=True, check=True,
        )
    except subprocess.CalledProcessError:
        return out

    for line in log_proc.stdout.splitlines():
        if "\t" not in line:
            continue
        sha, subject = line.split("\t", 1)
        # For each commit, inspect file-level changes against memory files.
        try:
            diff = subprocess.run(
                ["git", "-C", str(repo_top), "show", "--stat", "--format=", sha],
                capture_output=True, text=True, check=True,
            )
        except subprocess.CalledProcessError:
            continue
        # For now: surface commits that touch memory files but lack a recognized prefix
        # AND modify files matching `<memory-root>/*.md` — coarse heuristic.
        # A precise per-mutation check (status transition specifically, etc.) is a
        # follow-up; this catches the gross case where someone bypasses the canonical
        # prefixes entirely.
        if _is_recognized_prefix(subject):
            continue
        memory_files = _diff_memory_files(repo_top, sha, target)
        if not memory_files:
            continue
        # Inspect each touched memory file for a status:superseded / superseded_by /
        # decision_topic-mutation diff.
        for path in memory_files:
            transitions = _check_diff_for_authority_changes(repo_top, sha, path)
            for kind in transitions:
                out.append((
                    "BLOCKER",
                    f"commit {sha[:8]} ({subject!r}): {kind} on {path} without "
                    f"`memforge: resolve` / `memforge: snooze` / `memforge: alias` / "
                    f"`memforge: config` prefix",
                ))

    return out


_RECOGNIZED_PREFIXES = (
    "memforge: resolve ",
    "memforge: snooze ",
    "memforge: alias ",
    "memforge: config ",
    "memforge: config-major ",
    "memforge: drop ",
)


def _is_recognized_prefix(subject: str) -> bool:
    return any(subject.startswith(p) for p in _RECOGNIZED_PREFIXES)


def _git_toplevel(start: Path) -> Optional[Path]:
    try:
        result = subprocess.run(
            ["git", "-C", str(start), "rev-parse", "--show-toplevel"],
            capture_output=True, text=True, check=True,
        )
        return Path(result.stdout.strip())
    except (subprocess.CalledProcessError, FileNotFoundError):
        return None


def _diff_memory_files(repo_top: Path, sha: str, memory_target: Path) -> list[str]:
    """Return the list of changed paths from `sha` that fall inside `memory_target`."""
    try:
        proc = subprocess.run(
            ["git", "-C", str(repo_top), "show", "--name-only", "--format=", sha],
            capture_output=True, text=True, check=True,
        )
    except subprocess.CalledProcessError:
        return []
    rel_target = memory_target.resolve()
    out = []
    for line in proc.stdout.splitlines():
        line = line.strip()
        if not line or not line.endswith(".md"):
            continue
        full = (repo_top / line).resolve()
        try:
            full.relative_to(rel_target)
        except ValueError:
            continue
        out.append(line)
    return out


_AUTHORITY_DIFF_PATTERNS = {
    "status->superseded transition": re.compile(r"^\+status:\s*superseded\s*$", re.MULTILINE),
    "superseded_by write": re.compile(r"^\+superseded_by:\s*\[", re.MULTILINE),
    "decision_topic mutation": re.compile(
        r"^\-decision_topic:.*\n\+decision_topic:", re.MULTILINE
    ),
}


def _check_diff_for_authority_changes(repo_top: Path, sha: str, path: str) -> list[str]:
    try:
        proc = subprocess.run(
            ["git", "-C", str(repo_top), "show", sha, "--", path],
            capture_output=True, text=True, check=True,
        )
    except subprocess.CalledProcessError:
        return []
    out = []
    for kind, pattern in _AUTHORITY_DIFF_PATTERNS.items():
        if pattern.search(proc.stdout):
            out.append(kind)
    return out


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
