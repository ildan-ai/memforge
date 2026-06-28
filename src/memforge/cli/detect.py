"""memforge-detect - unattended detection pass for memory hygiene findings.

Orchestrates the read-only audit primitives (audit, lint, dedup,
cluster-suggest) across both default memory folders, then:

1. Runs a cost-bounded local-LLM semantic pass to triage lessons.md entries
   against existing memory rules (catches semantic dupes that keyword matching
   misses).
2. Assembles a single prioritized findings queue at
   ~/.claude/memforge-hygiene-queue.json.

The queue is TRANSACTIONAL: items have stable IDs and open/done state.
A re-run MERGES new findings into an existing open queue and deduplicates
against already-queued items. It never clobbers in-progress work.

Priority order (spec v2, D2 resolution):
  Integrity > Duplicates > Recall-weakness > Convention-drift > Lessons

This script is READ-ONLY: it never edits memory files.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import subprocess
import sys
from pathlib import Path
from typing import Optional

from memforge.cli._llm_dispatch import is_local_dispatcher
from memforge.cli.audit import audit_target
from memforge.paths import default_memory_paths


# ---------- queue file location ----------

DEFAULT_QUEUE = Path.home() / ".claude" / "memforge-hygiene-queue.json"

# ---------- priority ordering ----------

PRIORITY_ORDER = {
    "integrity": 0,
    "duplicates": 1,
    "recall-weakness": 2,
    "convention-drift": 3,
    "lessons": 4,
}


# ---------- stable item ID ----------

def _item_id(category: str, detail: str) -> str:
    """Stable deterministic ID for a finding.

    The same category+detail always produces the same ID so re-runs can
    dedup against already-queued items without false re-queuing.
    """
    raw = f"{category}|{detail}"
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:16]


# ---------- queue read / merge / write ----------

def _load_queue(queue_path: Path) -> list[dict]:
    """Load the existing queue. Returns [] if absent or malformed."""
    if not queue_path.is_file():
        return []
    try:
        data = json.loads(queue_path.read_text(encoding="utf-8"))
        if isinstance(data, list):
            return data
    except (OSError, json.JSONDecodeError):
        pass
    return []


def _merge_into_queue(
    existing: list[dict],
    new_findings: list[dict],
) -> tuple[list[dict], int, int]:
    """Merge new_findings into existing queue.

    - Items already present (by id) and already done are left as done.
    - Items already present and open are left as open (no re-open).
    - New items not in the queue are added with status 'open'.

    Returns (merged_queue, added_count, skipped_count).
    """
    existing_ids = {item["id"]: item for item in existing}
    added = 0
    skipped = 0
    merged = list(existing)
    for finding in new_findings:
        fid = finding["id"]
        if fid in existing_ids:
            skipped += 1
        else:
            merged.append({**finding, "status": "open"})
            added += 1
    # Sort by priority then by id for stable output.
    merged.sort(key=lambda x: (PRIORITY_ORDER.get(x.get("category", ""), 99), x["id"]))
    return merged, added, skipped


def _write_queue(queue_path: Path, queue: list[dict]) -> None:
    """Atomically write the queue file.

    Uses a temp file + rename to avoid partial writes corrupting an
    in-progress editor session that may have the queue open.
    """
    tmp = queue_path.with_suffix(".tmp")
    tmp.write_text(json.dumps(queue, indent=2, ensure_ascii=False), encoding="utf-8")
    tmp.replace(queue_path)


# ---------- local LLM dispatcher ----------

def _default_dispatcher() -> Optional[str]:
    """Return the first available local dispatcher executable, or None."""
    import shutil
    for cli, extra_args in (
        ("ollama", "run gemma2:2b"),
        ("llama-cli", ""),
        ("llamafile", ""),
        ("lms", "chat"),
    ):
        path = shutil.which(cli)
        if path:
            return f"{path} {extra_args}".strip()
    return None


def _run_semantic_triage(
    lessons_entries: list[dict],
    rule_catalog: list[str],
    dispatcher: str,
    timeout_s: int = 120,
) -> dict[int, dict]:
    """Use a local LLM to classify lessons entries against the rule catalog.

    Each lessons entry gets classified as one of:
      already-captured  - semantic duplicate of an existing rule
      warrants-memory   - stands on its own; needs a dedicated memory file
      generalizes       - should fold into a SKILL or CLAUDE.md rule
      transient         - keep; does not warrant promotion

    Returns dict mapping lesson index -> triage result dict. Falls back to
    empty dict if the dispatcher fails or returns unparseable output.

    The prompt is passed on stdin, never interpolated into the shell command,
    so crafted lesson content cannot inject shell.
    """
    if not lessons_entries or not rule_catalog:
        return {}

    catalog_block = "\n".join(
        f"  RULE {i+1}: {r}" for i, r in enumerate(rule_catalog[:60])
    )
    entries_block = "\n".join(
        f"  LESSON {e['index']}: {e['text'][:200]}" for e in lessons_entries[:30]
    )
    prompt = (
        "You are auditing a developer lessons log against an existing rule catalog.\n\n"
        "EXISTING RULES:\n"
        f"{catalog_block}\n\n"
        "LESSONS TO CLASSIFY:\n"
        f"{entries_block}\n\n"
        "For each LESSON, output one JSON object per line (no markdown fences, no extra text):\n"
        '{"lesson": <index>, "class": "<already-captured|warrants-memory|generalizes|transient>", '
        '"reason": "<one sentence max>"}\n\n'
        "Prefer 'transient' when uncertain. Output ONLY the JSON lines."
    )
    try:
        proc = subprocess.run(
            dispatcher,
            shell=True,
            input=prompt,
            capture_output=True,
            text=True,
            timeout=timeout_s,
        )
    except subprocess.TimeoutExpired:
        sys.stderr.write(
            f"[memforge-detect] semantic triage timed out after {timeout_s}s; "
            "continuing without triage\n"
        )
        return {}

    results: dict[int, dict] = {}
    for line in proc.stdout.splitlines():
        line = line.strip()
        if not line or not line.startswith("{"):
            continue
        try:
            obj = json.loads(line)
            lesson_idx = obj.get("lesson")
            if lesson_idx is not None and "class" in obj:
                results[int(lesson_idx)] = obj
        except (json.JSONDecodeError, TypeError, ValueError):
            continue
    return results


# ---------- lessons.md parsing ----------

def _parse_lessons(lessons_path: Path) -> list[dict]:
    """Parse a lessons.md file into individual entries.

    Splits on level-2+ markdown headers. Returns list of dicts:
      {index: int, text: str}
    """
    if not lessons_path.is_file():
        return []
    text = lessons_path.read_text(encoding="utf-8", errors="replace")
    entries: list[dict] = []
    header_re = re.compile(r"^#{2,}\s+", re.MULTILINE)
    parts = header_re.split(text)
    # The first element is everything before the first ##+ header (e.g., the
    # file title line). Skip it; only content after a header is a lesson entry.
    if len(parts) > 1:
        parts = parts[1:]
    idx = 1
    for part in parts:
        stripped = part.strip()
        if not stripped or len(stripped) < 10:
            continue
        entries.append({"index": idx, "text": stripped})
        idx += 1
    return entries


def _build_rule_catalog(targets: list[Path]) -> list[str]:
    """Extract one-line rule descriptions from memory folder description fields.

    Used as the rule catalog input for the semantic lessons triage pass.
    Local read only; never sends to a network.
    """
    from memforge.frontmatter import has_frontmatter, parse

    rules: list[str] = []
    seen: set[str] = set()
    for target in targets:
        if not target.is_dir():
            continue
        for md in sorted(target.rglob("*.md")):
            if md.name == "MEMORY.md":
                continue
            try:
                rel = md.relative_to(target)
            except ValueError:
                continue
            if rel.parts and rel.parts[0] == "archive":
                continue
            try:
                text = md.read_text(encoding="utf-8")
            except OSError:
                continue
            if not has_frontmatter(text):
                continue
            fm, _ = parse(text)
            desc = str(fm.get("description", "")).strip()
            name = str(fm.get("name", "")).strip()
            if desc and name and desc not in seen:
                seen.add(desc)
                rules.append(f"{name}: {desc}")
    return rules


# ---------- collect findings from primitives ----------

def _collect_audit_findings(targets: list[Path]) -> list[dict]:
    """Run memory-audit on each target and collect integrity + convention-drift findings."""
    import io
    from contextlib import redirect_stdout

    findings: list[dict] = []
    for target in targets:
        if not target.is_dir():
            continue
        buf = io.StringIO()
        try:
            with redirect_stdout(buf):
                _violation_count, blob = audit_target(
                    target,
                    stale_days=90,
                    fix=False,
                    add_defaults=False,
                    json_out=True,
                )
        except Exception as exc:
            sys.stderr.write(f"[memforge-detect] audit failed on {target}: {exc}\n")
            continue

        if blob is None:
            continue

        folder_label = target.name

        for v in blob.get("violations", []):
            findings.append({
                "id": _item_id("integrity", f"{folder_label}|{v}"),
                "category": "integrity",
                "folder": str(target),
                "detail": v,
            })

        for h in blob.get("health", []):
            if "[convention-drift]" in h:
                findings.append({
                    "id": _item_id("convention-drift", f"{folder_label}|{h}"),
                    "category": "convention-drift",
                    "folder": str(target),
                    "detail": h,
                })

    return findings


def _collect_dedup_findings(targets: list[Path], dispatcher: Optional[str]) -> list[dict]:
    """Run memory-dedup on each target and collect duplicate findings."""
    if not dispatcher:
        return []
    findings: list[dict] = []
    for target in targets:
        if not target.is_dir():
            continue
        cmd = [
            sys.executable, "-m", "memforge.cli.dedup",
            "--path", str(target),
            "--dispatcher", dispatcher,
            "--json",
        ]
        try:
            proc = subprocess.run(
                cmd, capture_output=True, text=True, timeout=120
            )
        except (subprocess.TimeoutExpired, OSError) as exc:
            sys.stderr.write(f"[memforge-detect] dedup failed on {target}: {exc}\n")
            continue
        out = proc.stdout.strip()
        if not out:
            continue
        try:
            groups = json.loads(out)
        except json.JSONDecodeError:
            continue
        folder_label = target.name
        for g in groups:
            ids = g.get("ids", [])
            reason = g.get("reason", "")
            detail = f"ids={ids} reason={reason}"
            findings.append({
                "id": _item_id("duplicates", f"{folder_label}|{detail}"),
                "category": "duplicates",
                "folder": str(target),
                "detail": detail,
                "dup_ids": ids,
                "reason": reason,
            })
    return findings


def _collect_recall_weakness_findings(targets: list[Path]) -> list[dict]:
    """Run memory-lint on each target and collect recall-weakness findings.

    Picks up lint findings that signal recall-weak memories (score < 0.5).
    Falls back gracefully if lint emits no JSON or does not support --json.
    """
    findings: list[dict] = []
    for target in targets:
        if not target.is_dir():
            continue
        cmd = [
            sys.executable, "-m", "memforge.cli.lint",
            "--path", str(target),
            "--json",
        ]
        try:
            proc = subprocess.run(
                cmd, capture_output=True, text=True, timeout=60
            )
        except (subprocess.TimeoutExpired, OSError) as exc:
            sys.stderr.write(f"[memforge-detect] lint failed on {target}: {exc}\n")
            continue
        folder_label = target.name
        for line in proc.stdout.splitlines():
            line = line.strip()
            if not line or not line.startswith("{"):
                continue
            try:
                record = json.loads(line)
            except json.JSONDecodeError:
                continue
            fname = record.get("file", "")
            score = record.get("recall_score")
            issues = record.get("issues", [])
            if score is not None and isinstance(score, (int, float)) and score < 0.5:
                detail = f"{fname}: recall_score={score:.2f}"
                findings.append({
                    "id": _item_id("recall-weakness", f"{folder_label}|{fname}"),
                    "category": "recall-weakness",
                    "folder": str(target),
                    "file": fname,
                    "detail": detail,
                    "recall_score": score,
                    "issues": issues,
                })
    return findings


def _collect_cluster_findings(targets: list[Path]) -> list[dict]:
    """Run memory-cluster-suggest on each target and collect rollup candidates."""
    findings: list[dict] = []
    for target in targets:
        if not target.is_dir():
            continue
        cmd = [
            sys.executable, "-m", "memforge.cli.cluster_suggest",
            "--path", str(target),
            "--json",
        ]
        try:
            proc = subprocess.run(
                cmd, capture_output=True, text=True, timeout=60
            )
        except (subprocess.TimeoutExpired, OSError) as exc:
            sys.stderr.write(f"[memforge-detect] cluster-suggest failed on {target}: {exc}\n")
            continue
        folder_label = target.name
        for line in proc.stdout.splitlines():
            line = line.strip()
            if not line or not line.startswith("{"):
                continue
            try:
                record = json.loads(line)
            except json.JSONDecodeError:
                continue
            topic = record.get("topic", "")
            members = record.get("members", [])
            detail = f"[rollup-candidate] topic={topic} count={len(members)}"
            findings.append({
                "id": _item_id("convention-drift", f"cluster|{folder_label}|{topic}"),
                "category": "convention-drift",
                "folder": str(target),
                "detail": detail,
                "topic": topic,
                "members": members,
            })
    return findings


def _collect_lessons_findings(
    lessons_path: Path,
    targets: list[Path],
    dispatcher: Optional[str],
) -> list[dict]:
    """Parse lessons.md and triage entries via a local LLM semantic pass.

    When no dispatcher is available, entries are still queued with
    classification 'unclassified' so the editor skill can handle them.
    """
    entries = _parse_lessons(lessons_path)
    if not entries:
        return []

    triage_results: dict[int, dict] = {}
    if dispatcher and is_local_dispatcher(dispatcher):
        rule_catalog = _build_rule_catalog(targets)
        if rule_catalog:
            sys.stderr.write(
                f"[memforge-detect] semantic triage: {len(entries)} lessons vs "
                f"{len(rule_catalog)} rules\n"
            )
            triage_results = _run_semantic_triage(entries, rule_catalog, dispatcher)
        else:
            sys.stderr.write(
                "[memforge-detect] no rule catalog found; skipping semantic triage\n"
            )

    findings: list[dict] = []
    for entry in entries:
        idx = entry["index"]
        triage = triage_results.get(idx, {})
        classification = triage.get("class", "unclassified")
        triage_reason = triage.get("reason", "")
        detail = entry["text"][:300]
        findings.append({
            "id": _item_id("lessons", f"lessons|{idx}|{detail[:80]}"),
            "category": "lessons",
            "lessons_index": idx,
            "detail": detail,
            "classification": classification,
            "triage_reason": triage_reason,
        })
    return findings


# ---------- main ----------

def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(
        prog="memforge-detect",
        description=(
            "Unattended detection pass: runs audit, lint, dedup, and cluster-suggest "
            "across memory folders and optionally triages a lessons.md file via a "
            "cost-bounded local LLM. Writes a prioritized transactional findings queue. "
            "READ-ONLY: never edits memory files."
        ),
    )
    p.add_argument(
        "--path", action="append", type=Path, default=[],
        help="Memory folder to scan (repeatable; overrides defaults).",
    )
    p.add_argument(
        "--lessons", type=Path, default=None,
        help="Path to lessons.md to triage (auto-detects tasks/lessons.md from cwd "
             "if not specified; skipped when not found).",
    )
    p.add_argument(
        "--no-lessons", action="store_true",
        help="Skip the lessons.md triage step entirely.",
    )
    p.add_argument(
        "--dispatcher", default=None,
        help="Local-LLM dispatcher command for semantic triage and dedup "
             "(reads prompt on stdin, writes response to stdout). "
             "Auto-detects ollama, llama-cli, or llamafile on PATH.",
    )
    p.add_argument(
        "--queue", type=Path, default=DEFAULT_QUEUE,
        help=f"Path to the findings queue file (default: {DEFAULT_QUEUE}).",
    )
    p.add_argument(
        "--dry-run", action="store_true",
        help="Print what would be queued without writing the queue file.",
    )
    p.add_argument(
        "--summary", action="store_true",
        help="Print a single-line summary suitable for end-my-week integration.",
    )
    args = p.parse_args(argv)

    targets: list[Path] = (
        [pp.expanduser().resolve() for pp in args.path]
        if args.path
        else [pp for pp in default_memory_paths() if pp.exists()]
    )
    if not targets:
        print("No memory folders found. Pass --path to specify one.")
        return 0

    # Resolve dispatcher; enforce local-only policy.
    dispatcher: Optional[str] = (
        args.dispatcher
        or os.environ.get("MEMFORGE_DETECT_DISPATCHER")
        or _default_dispatcher()
    )
    if dispatcher and not is_local_dispatcher(dispatcher):
        sys.stderr.write(
            "[memforge-detect] warning: dispatcher is not a recognized local-model "
            "runner. Semantic triage and dedup LLM pass will be skipped "
            "(local-only policy enforced).\n"
        )
        dispatcher = None

    print(f"[memforge-detect] scanning {len(targets)} folder(s)", file=sys.stderr)
    if dispatcher:
        print(f"[memforge-detect] LLM dispatcher: {dispatcher}", file=sys.stderr)
    else:
        print(
            "[memforge-detect] no local LLM dispatcher found; "
            "semantic triage and dedup LLM pass skipped",
            file=sys.stderr,
        )

    # Collect findings from all primitives.
    new_findings: list[dict] = []
    new_findings.extend(_collect_audit_findings(targets))
    new_findings.extend(_collect_dedup_findings(targets, dispatcher))
    new_findings.extend(_collect_recall_weakness_findings(targets))
    new_findings.extend(_collect_cluster_findings(targets))

    if not args.no_lessons:
        lessons_path = args.lessons
        if lessons_path is None:
            for candidate in (
                Path.cwd() / "tasks" / "lessons.md",
                Path.cwd().parent / "tasks" / "lessons.md",
            ):
                if candidate.is_file():
                    lessons_path = candidate
                    break

        if lessons_path and lessons_path.is_file():
            print(
                f"[memforge-detect] triaging lessons: {lessons_path}",
                file=sys.stderr,
            )
            new_findings.extend(
                _collect_lessons_findings(lessons_path, targets, dispatcher)
            )
        else:
            print(
                "[memforge-detect] lessons.md not found; "
                "pass --lessons PATH to specify (or --no-lessons to suppress this message)",
                file=sys.stderr,
            )

    # Sort by priority.
    new_findings.sort(
        key=lambda x: (PRIORITY_ORDER.get(x.get("category", ""), 99), x["id"])
    )

    # Merge into existing queue (transactional: never clobber in-progress work).
    queue_path: Path = args.queue.expanduser().resolve()
    existing = _load_queue(queue_path)
    done_in_existing = sum(1 for item in existing if item.get("status") == "done")

    merged, added, skipped = _merge_into_queue(existing, new_findings)

    open_count = sum(1 for item in merged if item.get("status") == "open")
    categories: dict[str, int] = {}
    for item in merged:
        if item.get("status") == "open":
            cat = item.get("category", "unknown")
            categories[cat] = categories.get(cat, 0) + 1

    if args.dry_run:
        print(f"[dry-run] would write {len(merged)} items to {queue_path}")
        print(f"  new: {added}  already-queued: {skipped}  done: {done_in_existing}")
        by_cat = ", ".join(f"{k}:{v}" for k, v in sorted(categories.items()))
        print(f"  open by category: {by_cat or '(none)'}")
        return 0

    queue_path.parent.mkdir(parents=True, exist_ok=True)
    _write_queue(queue_path, merged)

    if args.summary:
        by_cat = ", ".join(f"{k}:{v}" for k, v in sorted(categories.items()))
        print(
            f"memforge hygiene: {open_count} open findings ({by_cat}). "
            "Run /memforge-curate to review."
        )
        return 0

    print("\n[memforge-detect] done.")
    print(f"  Queue: {queue_path}")
    print(f"  Open: {open_count}  Done: {done_in_existing}")
    print(f"  Added this run: {added}  Already queued: {skipped}")
    if categories:
        print("  Open by category:")
        for cat in ["integrity", "duplicates", "recall-weakness", "convention-drift", "lessons"]:
            if cat in categories:
                print(f"    {cat}: {categories[cat]}")
    print()
    print("  Run /memforge-curate to review findings one at a time.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
