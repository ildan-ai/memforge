"""memory-dedup — flag near-duplicate entries in a memory folder via LLM.

Reports candidate pairs/groups for the user to review; never modifies files.

Defaults to --local-only mode (sends to a local model only) per the
2026-05-07 code-review-panel BLOCKER on uncontrolled cloud-LLM exfil of
catalog data. Disable with --allow-cloud-dispatcher only when the operator
has confirmed the catalog contains no sensitive content.

Per spec v0.3.0: descriptions are short, public-classification metadata.
Operators MUST NOT place PII, credentials, or other sensitive data in
description fields. memory-dedup warns on descriptions > 50 characters
(default) and refuses to ship descriptions at all in --redact-descriptions
mode.

Scope: TOP-LEVEL ONLY. collect_catalog walks only top-level *.md files and does
NOT recurse into rollup subfolders, matching memory-cluster-suggest (and unlike
memory-query / memory-lint, which recurse). Near-duplicates that live in a
topic subfolder are out of dedup's scope (dedup-recursion-01).
"""

from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Optional

from memforge.frontmatter import parse  # noqa: E402


def _default_path() -> Path:
    """Default dedup target: the global-memory folder from the centralized,
    IDE/OS-neutral resolver, falling back to the per-cwd folder, then the
    resolver's first entry. dedup scans one folder, so pick the global one."""
    from memforge.paths import default_memory_paths

    paths = default_memory_paths()
    for p in paths:
        if p.name == "global-memory":
            return p
    return paths[0] if paths else Path.home() / ".memforge" / "global-memory"


DEFAULT_PATH = _default_path()

# Dispatchers we know route to a local-only model. Anything else, in
# --local-only mode, is rejected with a clear error. Operators bring
# their own dispatcher; the patterns below recognize common local-model
# CLIs (ollama, llama.cpp, lm-studio) and a small allowlist of local-
# model aliases. To add your own local-dispatcher pattern, set the
# MEMORY_DEDUP_DISPATCHER environment variable or pass --dispatcher.
#
# Dispatcher local/cloud classification lives in the shared _llm_dispatch
# module (also used by memory-lint) and is executable-allowlist based, not a
# substring heuristic, so a cloud command that merely mentions a local model
# name cannot bypass the local-only gate. Re-exported here for back-compat.
from memforge.cli._llm_dispatch import is_local_dispatcher  # noqa: E402,F401
from memforge.recall import _access_labels, _access_ok, _sensitivity_ok  # noqa: E402

# Cloud-egress sensitivity ceiling (dedup-sensitivity-02). Mirrors lint's
# CLOUD_SENSITIVITY_CEILING and recall's default surfacing posture: only
# memories at or below `internal` may have their description shipped to a CLOUD
# dispatcher. A restricted/privileged memory, or one with a restricting `access`
# label, has its description HARD-redacted regardless of --no-redact-descriptions
# so a single restricted memory in a mostly-public folder cannot leak. SPEC
# §"Expected content sensitivity": restricted/privileged content is out-of-bounds
# for cloud export.
CLOUD_SENSITIVITY_CEILING = "internal"


def cloud_egress_eligible(fm: dict) -> Optional[str]:
    """Return None when this memory's description may be shipped to a CLOUD
    dispatcher, or a machine-readable skip reason when it must not. Reuses
    recall._sensitivity_ok / _access_ok so dedup's egress filter is identical to
    recall and lint (no parallel heuristic)."""
    sens = str(fm.get("sensitivity") or "internal").lower()
    if not _sensitivity_ok(sens, CLOUD_SENSITIVITY_CEILING):
        return "sensitivity_above_ceiling"
    if not _access_ok(_access_labels(fm.get("access")), None):
        return "access_restricted"
    return None


def _which(name: str) -> Optional[str]:
    """Return the absolute path to `name` on $PATH, or None."""
    return shutil.which(name)


def default_dispatcher() -> str:
    """Pick a dispatcher by probing $PATH for known local-model CLIs."""
    for cli, args in (
        ("ollama", "run gemma2:2b"),
        ("llama.cpp", ""),
        ("lms", "chat"),
    ):
        path = _which(cli)
        if path:
            return f"{path} {args}".strip()
    # No local dispatcher on PATH. Return a placeholder that fails the
    # local-pattern check so the operator gets the helpful error message
    # instead of a confusing "command not found".
    return "no-local-dispatcher-found"


def collect_catalog(
    folder: Path,
    redact_descriptions: bool,
    warn_threshold: int,
    cloud_dispatch: bool = False,
) -> tuple[list[Path], list[str], list[str]]:
    """Walk top-level .md files, extract name/type/description.

    Returns (file_paths, catalog_lines, warnings). When redact_descriptions
    is True, descriptions are replaced with "[redacted]" before any prompt
    construction. Warnings list flags any description longer than
    warn_threshold characters so the operator sees them on stderr before
    the dispatch.

    Cloud-egress containment (dedup-sensitivity-02): when cloud_dispatch is True,
    a memory whose `sensitivity` exceeds the ceiling OR which carries a
    restricting `access` label has its description HARD-redacted regardless of
    redact_descriptions, so the documented `--no-redact-descriptions
    --allow-cloud-dispatcher` opt-in pair still cannot exfiltrate a restricted
    memory's description. Each excluded file is named in the warnings list. Name
    and type are still shipped (they are not the sensitive payload here; the
    description body is); only the description is contained. The egress filter
    mirrors recall._sensitivity_ok / _access_ok exactly.
    """
    files: list[Path] = []
    lines: list[str] = []
    warnings: list[str] = []

    candidates = sorted(folder.glob("*.md"))
    idx = 0
    for f in candidates:
        if f.name == "MEMORY.md":
            continue
        try:
            text = f.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            continue
        fm, _ = parse(text)
        name = str(fm.get("name", ""))
        type_ = str(fm.get("type", ""))
        desc = str(fm.get("description", ""))

        if len(desc) > warn_threshold:
            warnings.append(f"  {f.name}: description is {len(desc)} chars (>{warn_threshold}); review for sensitive content")

        # Cloud-egress sensitivity/access containment runs BEFORE the redaction
        # flag, so it cannot be overridden by --no-redact-descriptions.
        egress_skip = cloud_egress_eligible(fm) if cloud_dispatch else None
        if egress_skip:
            desc_to_ship = f"[redacted: {egress_skip}; not cloud-eligible]"
            warnings.append(
                f"  {f.name}: description withheld from cloud dispatch "
                f"({egress_skip}); sensitivity/access label is above the "
                f"cloud-egress ceiling"
            )
        elif redact_descriptions:
            desc_to_ship = "[redacted — pass --no-redact-descriptions to include]"
        else:
            desc_to_ship = desc

        idx += 1
        files.append(f)
        lines.append(f"{idx} | {f.name} | [{type_}] {name} — {desc_to_ship}")

    return files, lines, warnings


PROMPT_TEMPLATE = """You are auditing a personal knowledge base for near-duplicate entries.

Each entry has: id | filename | [type] name — description

CATALOG ({n} entries):
{catalog}

Task: identify groups of entries that cover the same ground and would be candidates for merging. Be CONSERVATIVE: flag only when two or more entries are genuinely redundant, not merely related. Different aspects of the same project are NOT duplicates. Related feedback rules are NOT duplicates unless they state the same rule.

Output ONLY a JSON array of objects, nothing else. Each object has:
- "ids": array of two or more catalog IDs that appear duplicative
- "reason": one-sentence explanation of the overlap

If no duplicates exist, output the empty array: []

Example output:
[{{"ids":[3,14],"reason":"Both entries describe the same meeting reschedule."}}]
"""


def main() -> int:
    parser = argparse.ArgumentParser(
        prog="memory-dedup",
        description=__doc__.split("\n\n", 1)[0],
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
SECURITY: This tool ships memory metadata to an LLM. By default it runs
in --local-only mode (only local models accepted) and --redact-descriptions
mode (only filename + name + type sent, not description body). Override
both flags only when the catalog has been confirmed free of sensitive
content. See SPEC.md §sensitivity-and-redaction.
""",
    )
    parser.add_argument("--path", type=Path, default=DEFAULT_PATH,
                        help="Memory folder to scan (default: the global-memory "
                             "folder). Scope is TOP-LEVEL ONLY by design: dedup "
                             "scans only top-level *.md and does NOT recurse into "
                             "rollup subfolders (auth/, billing/, infra/, etc.). "
                             "This differs from memory-query / memory-lint, which "
                             "recurse. Near-duplicates inside a subfolder are out "
                             "of scope.")
    parser.add_argument("--dispatcher", default=None,
                        help="Command that reads prompt on stdin, prints response on stdout")
    parser.add_argument("--allow-cloud-dispatcher", action="store_true",
                        help="OPT-IN: allow cloud-tier LLM dispatchers (default: refuse). "
                             "Only enable when catalog is confirmed sensitive-content-free.")
    parser.add_argument("--no-redact-descriptions", action="store_true",
                        help="OPT-IN: send description bodies to the LLM (default: redact). "
                             "Only enable when descriptions are confirmed PII/credential-free.")
    parser.add_argument("--description-warn-threshold", type=int, default=50,
                        help="Warn when descriptions exceed this length (default: 50 chars)")
    parser.add_argument("--json", action="store_true",
                        help="Print the raw JSON verdict from the LLM")
    args = parser.parse_args()

    target: Path = args.path.expanduser().resolve()
    if not target.is_dir():
        sys.stderr.write(f"error: path not a directory: {target}\n")
        return 2

    dispatcher = args.dispatcher or os.environ.get("MEMORY_DEDUP_DISPATCHER") or default_dispatcher()

    if not args.allow_cloud_dispatcher and not is_local_dispatcher(dispatcher):
        sys.stderr.write(
            "error: dispatcher does not match a known local-model pattern.\n"
            f"       dispatcher: {dispatcher}\n"
            "       This tool defaults to --local-only mode to prevent uncontrolled\n"
            "       data exfil to cloud LLMs. To proceed:\n"
            "         1. Use a local dispatcher (route --task memory-dedup, ollama, etc), OR\n"
            "         2. Pass --allow-cloud-dispatcher AFTER confirming the catalog\n"
            "            has no sensitive content (no credentials, no PII, no privileged data).\n"
        )
        return 2

    redact = not args.no_redact_descriptions

    # A cloud dispatch is one that left the local-only gate: the operator passed
    # --allow-cloud-dispatcher AND the dispatcher is not a recognized local
    # runner. Only then does the per-memory sensitivity/access egress filter
    # apply (a local model never leaves the box). (dedup-sensitivity-02)
    cloud_dispatch = args.allow_cloud_dispatcher and not is_local_dispatcher(dispatcher)

    files, catalog_lines, warnings = collect_catalog(
        target,
        redact_descriptions=redact,
        warn_threshold=args.description_warn_threshold,
        cloud_dispatch=cloud_dispatch,
    )
    if not files:
        print(f"No memory files found in {target}")
        return 0

    if warnings:
        sys.stderr.write("⚠ description-length warnings (review before dispatch):\n")
        for w in warnings:
            sys.stderr.write(w + "\n")
        sys.stderr.write("\n")

    sys.stderr.write(
        f"Scanning {len(files)} entries in {target}\n"
        f"  via: {dispatcher}\n"
        f"  local-only: {not args.allow_cloud_dispatcher}\n"
        f"  redact-descriptions: {redact}\n\n"
    )

    catalog = "\n".join(catalog_lines)
    prompt = PROMPT_TEMPLATE.format(n=len(files), catalog=catalog)

    # shell=True executes the OPERATOR-supplied dispatcher string verbatim
    # (dedup-shell-injection-06). The prompt (which may carry hostile memory
    # descriptions) is passed on STDIN, never interpolated into the command, so
    # crafted memory content cannot inject shell. The dispatcher string itself
    # comes from --dispatcher / MEMORY_DEDUP_DISPATCHER / probed PATH, all
    # operator-controlled and trusted as such; treat env/flag values as trusted.
    try:
        proc = subprocess.run(
            dispatcher,
            shell=True,
            input=prompt,
            capture_output=True,
            text=True,
            timeout=300,
        )
    except subprocess.TimeoutExpired:
        sys.stderr.write("error: dispatcher timed out after 300s\n")
        return 2

    response = proc.stdout.strip()
    if not response:
        sys.stderr.write(
            f"error: dispatcher returned no output. Check '{dispatcher}' is callable.\n"
            f"       stderr: {proc.stderr.strip()[:200]}\n"
        )
        return 2

    json_text = re.sub(r"^```(?:json)?\s*$", "", response, flags=re.MULTILINE).strip()
    if json_text.endswith("```"):
        json_text = json_text[:-3].rstrip()

    try:
        groups = json.loads(json_text)
        if not isinstance(groups, list):
            raise ValueError("response is not a JSON array")
    except (json.JSONDecodeError, ValueError) as e:
        sys.stderr.write(f"warning: response was not valid JSON ({e}). Raw response:\n----\n{response}\n----\n")
        return 2

    if args.json:
        print(json.dumps(groups, indent=2))
        return 0

    if not groups:
        print(f"No near-duplicates flagged across {len(files)} entries.")
        return 0

    print(f"Candidate duplicate groups ({len(groups)}):\n")
    for g in groups:
        ids = g.get("ids", [])
        reason = g.get("reason", "")
        names: list[str] = []
        for i in ids:
            try:
                idx = int(i) - 1
                if 0 <= idx < len(files):
                    names.append(files[idx].name)
            except (TypeError, ValueError):
                continue
        if not names:
            continue
        print(f"- {', '.join(str(i) for i in ids)}")
        print(f"    files: {', '.join(names)}")
        print(f"    reason: {reason}")
        print()

    print("Review each group and decide: merge, keep both, or drop one.")
    print("memory-dedup never edits memory files; it only surfaces candidates.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
