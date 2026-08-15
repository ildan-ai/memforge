<!-- GENERATED FILE. Do not hand-edit; edit source and rerun tools/gen-cli-catalog. -->
<!-- Source of truth: this package's installed console_scripts entry points -->
<!-- (importlib.metadata), each rendered via its own --help output. -->

# MemForge CLI reference

Generated from the live --help output of every console-script entry point
this package installs. `tools/gen-cli-catalog --check` runs in CI and fails
the build when this file has drifted from source (see .github/workflows/ci.yml,
job `cli-docs-check`).

For curated one-line descriptions and install guidance, see the "Key tools"
section of the project README. This file is the exhaustive, always-fresh
--help reference; the README table is the hand-picked tour.

## `agents-md-gen`

Entry point: `memforge.cli.agents_md_gen:main`

```
usage: agents-md-gen [-h] [--cwd CWD]
                     [--max-sensitivity {public,internal,restricted,privileged}]
                     [--dry-run] [--ceiling-bytes CEILING_BYTES]
                     [--inline-above-public]

options:
  -h, --help            show this help message and exit
  --cwd CWD
  --max-sensitivity {public,internal,restricted,privileged}
  --dry-run
  --ceiling-bytes CEILING_BYTES
  --inline-above-public
                        Inline full bodies of critical memories above 'public'
                        sensitivity into the committed AGENTS.md. OFF by default:
                        AGENTS.md is shared with external tools, so above-public
                        inline content requires this explicit opt-in (mirrors
                        dedup/lint local-only-by-default posture).
```

## `memforge`

Entry point: `memforge.cli._dispatch:main`

```
usage: memforge [-h] [--version] <command> ...

MemForge reference CLI for operator + agent identity, operator-registry, key rotation
+ revocation, agent session attestation, and messaging-adapter diagnostics.

positional arguments:
  <command>
    init-operator       Generate operator-UUID + register a GPG signing key as the
                        operator identity.
    init-store          Bootstrap the .memforge/ folder in a memory-root + create a
                        signed operator-registry.
    operator-registry   Manage the operator-registry (add / verify / remove / fresh-
                        start).
    rotate-key          Rotate the current operator key (generate new keypair + cross-
                        sign).
    revoke              Build a signed `memforge: revoke <key_id>` commit body.
    revocation-snapshot
                        Walk the revocation set + emit a signed `memforge: revocation-
                        snapshot <hash>` commit.
    memories-by-key     Walk a memory folder + list every memory file whose `identity`
                        resolves to <key_id>.
    revoke-memories     Bulk-mark memories signed under a revoked key as `status:
                        superseded`.
    upgrade-v04-memories
                        Add v0.5 `identity` + `signature` frontmatter to v0.4-shaped
                        memories under a memory-root.
    revoke-cache-refresh
                        Re-fetch the remote ref pinned in .memforge/config.yaml +
                        rebuild the revocation cache (sparse/shallow mode).
    messaging-doctor    Run the v0.5.1 fail-closed checklist + report posture (OK /
                        WARN / FAIL).
    recovery-init       Generate ~/.memforge/recovery-secret.bin + anchor its SHA256
                        in the signed operator-registry.
    recovery-backup-confirm
                        Acknowledge that ~/.memforge/recovery-secret.bin has been
                        backed up to offline media.
    attest-agent        Issue a signed agent-session attestation (nonce + expires_at +
                        capability_scope).

options:
  -h, --help            show this help message and exit
  --version             show program's version number and exit
```

## `memforge-detect`

Entry point: `memforge.cli.detect:main`

```
usage: memforge-detect [-h] [--path PATH] [--lessons LESSONS] [--no-lessons]
                       [--dispatcher DISPATCHER] [--queue QUEUE] [--dry-run]
                       [--summary]

Unattended detection pass: runs audit, lint, dedup, and cluster-suggest across memory
folders and optionally triages a lessons.md file via a cost-bounded local LLM. Writes
a prioritized transactional findings queue. READ-ONLY: never edits memory files.

options:
  -h, --help            show this help message and exit
  --path PATH           Memory folder to scan (repeatable; overrides defaults).
  --lessons LESSONS     Path to lessons.md to triage (auto-detects tasks/lessons.md
                        from cwd if not specified; skipped when not found).
  --no-lessons          Skip the lessons.md triage step entirely.
  --dispatcher DISPATCHER
                        Local-LLM dispatcher command for semantic triage and dedup
                        (reads prompt on stdin, writes response to stdout). Auto-
                        detects ollama, llama-cli, or llamafile on PATH.
  --queue QUEUE         Path to the findings queue file (default: ~/.claude/memforge-
                        hygiene-queue.json).
  --dry-run             Print what would be queued without writing the queue file.
  --summary             Print a single-line summary suitable for end-my-week
                        integration.
```

## `memforge-migrate-claim-block`

Entry point: `memforge.cli.migrate_claim_block:main`

```
usage: memforge-migrate-claim-block [-h] [--memory-root MEMORY_ROOT] [--dry-run]

Rewrite legacy `status:` to canonical `state:` inside the per-group competing-claim
fenced block of MEMORY.md.

options:
  -h, --help            show this help message and exit
  --memory-root MEMORY_ROOT
                        Memory folder to scan (repeatable). Defaults to per-cwd memory
                        + global-memory.
  --dry-run             Show what would change without writing.
```

## `memforge-resolve`

Entry point: `memforge.cli.resolve:main`

```
usage: memforge-resolve [-h] [--memory-root MEMORY_ROOT] [--winner-uid WINNER_UID]
                        [--dry-run]
                        topic

Resolve a competing-claim group on a decision_topic.

positional arguments:
  topic                 The decision_topic slug to resolve.

options:
  -h, --help            show this help message and exit
  --memory-root MEMORY_ROOT
                        Memory folder to search (repeatable). Defaults to per-cwd
                        memory + global-memory.
  --winner-uid WINNER_UID
                        Non-interactive: pick the winner by UID instead of prompting.
  --dry-run             Show what would change without writing or committing.
```

## `memory-audit`

Entry point: `memforge.cli.audit:main`

```
usage: memory-audit [-h] [--path PATH] [--strict] [--json] [--fix] [--add-defaults]
                    [--stale-days STALE_DAYS]
                    [--export-tier {public,internal,restricted,privileged}]

Health + integrity checks for MemForge memory folders. Defaults: per-cwd memory +
~/.claude/global-memory/.

options:
  -h, --help            show this help message and exit
  --path PATH           Audit only this dir (repeatable; overrides defaults).
  --strict              Exit 1 on any integrity violation.
  --json                Emit machine-readable JSON.
  --fix                 Prompt to remove orphan pointers from MEMORY.md.
  --add-defaults        Prompt to add 'sensitivity: internal' to files missing the
                        field.
  --stale-days STALE_DAYS
                        Flag files older than N days (default: 90).
  --export-tier {public,internal,restricted,privileged}
                        v0.4 sensitivity export-tier gate: fail BLOCKER on any file
                        whose declared sensitivity exceeds <tier>. Defaults to
                        audit.default_export_tier from .memforge/config.yaml when set,
                        otherwise the gate is no-op. Privileged files always block
                        when the gate runs, regardless of config disable.
```

## `memory-audit-deep`

Entry point: `memforge.cli.audit_deep:main`

```
usage: memory-audit-deep [-h] [--path PATH] [--strict] [--stale-days STALE_DAYS]
                         [--memforge-root MEMFORGE_ROOT] [--allow-missing-taxonomy]

v0.3.0-aware recursive memory audit (Phase 1 T4).

options:
  -h, --help            show this help message and exit
  --path PATH           Folder (repeatable)
  --strict              Exit 1 on any violation
  --stale-days STALE_DAYS
                        Rollup last_reviewed staleness threshold (default 90)
  --memforge-root MEMFORGE_ROOT
                        Override memforge repo root for taxonomy.yaml lookup
  --allow-missing-taxonomy
                        Downgrade a missing/unloadable taxonomy.yaml (or absent
                        PyYAML) from a --strict hard error to a warning. The other
                        strict checks (UID uniqueness, broken mem:uid links, rollup
                        staleness) still run and still gate exit status. Use when your
                        deploy carries no namespaced tags to enforce.
```

## `memory-audit-log`

Entry point: `memforge.cli.audit_log:main`

```
usage: memory-audit-log [-h] {append,verify,tail,export} ...

Tamper-evident hash-chain audit log for memory folders (Phase 1 T6.3).

positional arguments:
  {append,verify,tail,export}
    append              Append a new audit record
    verify              Re-walk hash chain; exit 1 on tamper
    tail                Show last N records
    export              Export records for SIEM forwarding

options:
  -h, --help            show this help message and exit
```

## `memory-cluster-suggest`

Entry point: `memforge.cli.cluster_suggest:main`

```
usage: memory-cluster-suggest [-h] [--path PATH] [--threshold THRESHOLD]
                              [--min-size MIN_SIZE]

Surface candidate rollup clusters (Phase 1 T2 v1).

options:
  -h, --help            show this help message and exit
  --path PATH           Folder (repeatable). Scope is TOP-LEVEL ONLY by design:
                        cluster suggestion operates on un-rolled top-level files (the
                        rollup model). This differs from memory-query / memory-lint,
                        which recurse into rollup subfolders (cluster-01).
  --threshold THRESHOLD
                        Pairwise similarity threshold for clustering (default 0.20).
                        0.20 calibrated for current pre-backfill data (filename-only
                        signal); raise toward 0.30+ once topic tags backfill.
  --min-size MIN_SIZE   Minimum cluster size to surface (default 5; matches Phase 2
                        D1)
```

## `memory-dedup`

Entry point: `memforge.cli.dedup:main`

```
usage: memory-dedup [-h] [--path PATH] [--dispatcher DISPATCHER]
                    [--allow-cloud-dispatcher] [--no-redact-descriptions]
                    [--description-warn-threshold DESCRIPTION_WARN_THRESHOLD] [--json]

memory-dedup — flag near-duplicate entries in a memory folder via LLM.

options:
  -h, --help            show this help message and exit
  --path PATH           Memory folder to scan (default: the global-memory folder).
                        Scope is TOP-LEVEL ONLY by design: dedup scans only top-level
                        *.md and does NOT recurse into rollup subfolders (auth/,
                        billing/, infra/, etc.). This differs from memory-query /
                        memory-lint, which recurse. Near-duplicates inside a subfolder
                        are out of scope.
  --dispatcher DISPATCHER
                        Command that reads prompt on stdin, prints response on stdout
  --allow-cloud-dispatcher
                        OPT-IN: allow cloud-tier LLM dispatchers (default: refuse).
                        Only enable when catalog is confirmed sensitive-content-free.
  --no-redact-descriptions
                        OPT-IN: send description bodies to the LLM (default: redact).
                        Only enable when descriptions are confirmed PII/credential-
                        free.
  --description-warn-threshold DESCRIPTION_WARN_THRESHOLD
                        Warn when descriptions exceed this many characters. Default 0
                        (disabled). The old default of 50 flagged essentially every
                        file in a real corpus, which is noise, not signal:
                        `description` is the authoritative recall text and is SUPPOSED
                        to be descriptive. The MEMORY.md pointer hook is derived from
                        it by deterministic truncation, so a long description costs
                        nothing at the index. Set a positive value to re-enable the
                        check.
  --json                Print the raw JSON verdict from the LLM

SECURITY: This tool ships memory metadata to an LLM. By default it runs
in --local-only mode (only local models accepted) and --redact-descriptions
mode (only filename + name + type sent, not description body). Override
both flags only when the catalog has been confirmed free of sensitive
content. See SPEC.md §sensitivity-and-redaction.
```

## `memory-dlp-scan`

Entry point: `memforge.cli.dlp_scan:main`

```
usage: memory-dlp-scan [-h] [--paths PATHS [PATHS ...] | --staged | --memory-folders]
                       [--strict] [--strict-major] [--no-detect-secrets]
                       [--no-entropy] [--entropy-threshold ENTROPY_THRESHOLD]
                       [--no-sensitivity-cross-check]

Pre-commit DLP scanner for MemForge folders.

options:
  -h, --help            show this help message and exit
  --paths PATHS [PATHS ...]
                        Specific files to scan
  --staged              Scan git-staged files
  --memory-folders      Scan default memory folders
  --strict              Exit 1 on any BLOCKER finding
  --strict-major        Exit 1 on any BLOCKER or MAJOR finding
  --no-detect-secrets   Skip detect-secrets supplementary scan even when installed
  --no-entropy          Disable ONLY the generic high-entropy-near-keyword heuristic.
                        The bare-secret entropy gate (base64/hex credential shapes)
                        stays ON: it is high-precision and is the credential-shape
                        backstop on the commit gate (dlp-entropy-noentropy-bypass-01).
  --entropy-threshold ENTROPY_THRESHOLD
                        Bits-per-char entropy threshold for high-entropy heuristic
                        (default 4.5)
  --no-sensitivity-cross-check
                        Skip the v0.4 sensitivity_label_mismatch check. Hard-floor:
                        cannot disable when implied tier is privileged.
```

## `memory-frontmatter-backfill`

Entry point: `memforge.cli.frontmatter_backfill:main`

```
usage: memory-frontmatter-backfill [-h] [--path PATH] [--dry-run | --apply]
                                   [--limit LIMIT]

Populate v0.3.0 frontmatter on existing memory files (migration helper).

options:
  -h, --help     show this help message and exit
  --path PATH    Folder (repeatable)
  --dry-run      Default. Print planned changes; write nothing.
  --apply        Write changes back to files.
  --limit LIMIT  Limit per-folder lines printed (0 = no limit)
```

## `memory-index-gen`

Entry point: `memforge.cli.index_gen:main`

```
usage: memory-index-gen [-h] [--path PATH] [--write | --check | --print]
                        [--viewer-tier {public,internal,restricted,privileged}]
                        [--viewer-teams VIEWER_TEAMS] [--with-recall-index]

Generate MEMORY.md from frontmatter (Phase 1 T3 + T6.1 RBAC).

options:
  -h, --help            show this help message and exit
  --path PATH           Folder to process (repeatable)
  --write
  --check
  --print
  --viewer-tier {public,internal,restricted,privileged}
                        RBAC: filter MEMORY.md output by viewer hierarchical tier.
                        Files with access label > viewer tier are excluded. When
                        unset, no RBAC filter applies (operator default).
  --viewer-teams VIEWER_TEAMS
                        RBAC team membership (repeatable, e.g. --viewer-teams
                        team:security). Files with team:<x> access label are visible
                        only when viewer is in that team. Hierarchical access labels
                        are not affected.
  --with-recall-index   Also emit the recall inverted index (.memforge/recall-
                        index.json) alongside MEMORY.md (spec v0.6.0 recall
                        operation). Write mode only.
```

## `memory-link-rewriter`

Entry point: `memforge.cli.link_rewriter:main`

```
usage: memory-link-rewriter [-h] [--path PATH] {check,rename,rename-batch,upgrade} ...

Link integrity + UID rewriting for MemForge folders (spec 0.3.0).

positional arguments:
  {check,rename,rename-batch,upgrade}
    check               Validate UID uniqueness + link integrity.
    rename              Move a file + rewrite references.
    rename-batch        Move multiple files + rewrite references in ONE pass. Reads
                        JSON [{"src":"...","dst":"..."},...] from --json or stdin.
    upgrade             Rewrite path links to mem:uid form.

options:
  -h, --help            show this help message and exit
  --path PATH           Memory folder to operate on (repeatable; defaults to per-cwd +
                        global).
```

## `memory-lint`

Entry point: `memforge.cli.lint:main`

```
usage: memory-lint [-h] [--path PATH] [--json] [--min-score MIN_SCORE]
                   [--injected-file INJECTED_FILE] [--dispatcher DISPATCHER]
                   [--allow-cloud] [--allow-cloud-body] [--strict]

Recall-readiness + token-cost quality analysis for MemForge folders. Advisory and
READ-ONLY: it never modifies memory files. Local-only by default; the LLM suggestion
layer requires an explicit opt-in.

options:
  -h, --help            show this help message and exit
  --path PATH           Lint only this dir (repeatable; overrides defaults).
  --json                Emit machine-readable JSON.
  --min-score MIN_SCORE
                        Treat memories at or below this recall score (0-5) as weak;
                        only weak memories get LLM suggestions (default 3).
  --injected-file INJECTED_FILE
                        An always-loaded instruction file (e.g. CLAUDE.md). Enables
                        do_not_inject duplicate suggestions. Repeatable.
  --dispatcher DISPATCHER
                        Shell command for the LLM suggestion layer. Must read the
                        prompt on stdin. Off by default (deterministic-only).
  --allow-cloud         Permit a non-local dispatcher. Without this, only dispatchers
                        matching a known local-model pattern run.
  --allow-cloud-body    Include the memory BODY in the cloud payload (default:
                        metadata-only). Implies the body may contain sensitive
                        content; use only on a sanitized memory set.
  --strict              Exit 1 ONLY on the DETERMINISTIC recall floor
                        (description_missing / description_generic), NOT on the graded
                        collision score. SPEC §'Recall-readiness lint' keeps lint
                        advisory and forbids it as a hard gate on a conformance-
                        correct store; the graded score must never fail CI on legacy
                        not-yet-optimized descriptions (lint-strict-01). This flag is
                        opt-in CI tightening on the deterministic floor only.
```

## `memory-preamble-extract`

Entry point: `memforge.cli.preamble_extract:main`

```
usage: memory-preamble-extract [-h] [--path PATH] [--dry-run | --apply]

Extract MEMORY.md preamble into _memforge.yaml (Phase 1 migration helper).

options:
  -h, --help   show this help message and exit
  --path PATH  Folder (repeatable)
  --dry-run
  --apply
```

## `memory-promote`

Entry point: `memforge.cli.promote:main`

```
usage: memory-promote [-h] [--source SOURCE] [--target TARGET] [--dry-run]
                      [--no-commit] [--yes]
                      filename

Move a memory file from a per-cwd MemForge folder to the global-memory folder (or
between any two MemForge folders). Updates MEMORY.md in both locations and commits
each folder.

positional arguments:
  filename         Memory file to move (bare filename or path).

options:
  -h, --help       show this help message and exit
  --source SOURCE  Source folder (default: per-cwd memory folder).
  --target TARGET  Target folder (default: ~/.claude/global-memory).
  --dry-run        Print the plan, make no changes.
  --no-commit      Move and update indexes but skip git commits.
  --yes            Skip the confirmation prompt.
```

## `memory-query`

Entry point: `memforge.cli.query:main`

```
usage: memory-query [-h] [--path PATH] [--topic TOPIC] [--tag TAG] [--type TYPE]
                    [--status STATUS] [--pinned] [--owner OWNER] [--tier TIER]
                    [--sensitivity SENSITIVITY]
                    [--last-reviewed-before LAST_REVIEWED_BEFORE]
                    [--last-reviewed-after LAST_REVIEWED_AFTER]
                    [--updated-within-days UPDATED_WITHIN_DAYS] [--last-d LAST_D]
                    [--text TEXT] [--in TEXT_IN] [--format {markdown,json,count}]
                    [--limit LIMIT]

Dynamic query layer for memory folders (Phase 1 T5).

options:
  -h, --help            show this help message and exit
  --path PATH           Folder (repeatable)
  --topic TOPIC         Filter by topic: tag value
  --tag TAG             Filter by exact tag (e.g. topic:aws)
  --type TYPE           Filter by frontmatter type (user|feedback|project|reference)
  --status STATUS       Filter by status (default: any)
  --pinned              Pinned only
  --owner OWNER         Filter by owner
  --tier TIER           Filter by tier (index|detail)
  --sensitivity SENSITIVITY
                        Filter by sensitivity
  --last-reviewed-before LAST_REVIEWED_BEFORE
                        last_reviewed before date (YYYY-MM-DD)
  --last-reviewed-after LAST_REVIEWED_AFTER
                        last_reviewed after date (YYYY-MM-DD)
  --updated-within-days UPDATED_WITHIN_DAYS
                        Updated within N days
  --last-d LAST_D       Shorthand for updated within N days
  --text TEXT           Case-insensitive substring match. Default scope is name +
                        description + body (the distinctive recall terms live in
                        name/description). Narrow with --in.
  --in TEXT_IN          Comma-separated --text scope: any of name,description,body
                        (default: name,description,body). Use --in body for the prior
                        body-only behavior. Specific metadata fields are matched, not
                        raw YAML, so --text active does not hit status: active.
  --format {markdown,json,count}
  --limit LIMIT         Cap result count (0 = no cap)
```

## `memory-recall`

Entry point: `memforge.cli.recall:main`

```
usage: memory-recall [-h] [--path PATH] [--stdin] [--rebuild] [--force-rebuild]
                     [--top-k TOP_K] [--char-budget CHAR_BUDGET]
                     [--sensitivity-max {public,internal,restricted,privileged}]
                     [--viewer-team VIEWER_TEAM] [--format {markdown,json}]
                     [query ...]

Query-time recall reader (spec v0.6.0 recall operation).

positional arguments:
  query                 Query string (e.g. the user's prompt).

options:
  -h, --help            show this help message and exit
  --path PATH           Memory folder (repeatable). Default: per-cwd + global memory.
  --stdin               Read the query from stdin instead of argv.
  --rebuild             (Re)build the recall index for the folders before querying
                        ONLY when stale (index missing or an in-scope file changed).
                        With no query, this is a build-only refresh.
  --force-rebuild       With --rebuild, rebuild unconditionally even when the existing
                        index is up to date (skip the staleness check).
  --top-k TOP_K
  --char-budget CHAR_BUDGET
  --sensitivity-max {public,internal,restricted,privileged}
                        Exclude memories above this sensitivity tier. WARNING: when
                        UNSET there is NO sensitivity ceiling, so a memory labeled
                        restricted or privileged is eligible for injection.
                        Descriptions are spec'd public-class, but in a not-yet-
                        compliant store pass an explicit ceiling (e.g. internal) on
                        the per-prompt recall path to be safe.
  --viewer-team VIEWER_TEAM
                        Viewer team membership (repeatable, e.g. team:security).
  --format {markdown,json}
```

## `memory-rollup`

Entry point: `memforge.cli.rollup:main`

```
usage: memory-rollup [-h] [--path PATH] {create,undo,list} ...

Rollup primitive: bulk-move files into topic subfolder + generate README parent (Phase
1 T1).

positional arguments:
  {create,undo,list}
    create            Create a new rollup
    undo              Undo most recent rollup matching slug
    list              List rollup history entries

options:
  -h, --help          show this help message and exit
  --path PATH         Memory folder root (default: per-cwd memory)
```

## `memory-validate`

Entry point: `memforge.cli.validate:main`

```
usage: memory-validate [-h] [--path PATH] [--strict] [--json] [files ...]

Syntax-aware write-boundary gate for MemForge memory files. HARD check (always fails):
frontmatter parses as a YAML mapping. SOFT checks (fail only with --strict):
pointer/line caps, required v0.4 fields, tier/status enums. Read-only; never mutates.

positional arguments:
  files        Specific file(s) to validate. Overrides --path/defaults.

options:
  -h, --help   show this help message and exit
  --path PATH  Validate every *.md under this dir (repeatable). Ignored when FILE
               positionals are given.
  --strict     Exit nonzero on SOFT (warn) findings too, not just HARD errors.
  --json       Emit machine-readable JSON.
```

## `memory-watch`

Entry point: `memforge.cli.watch:main`

```
usage: memory-watch [-h] [--path PATH] [--quiet] [--debounce-ms DEBOUNCE_MS]

Cross-platform memory-folder auto-commit watcher.

options:
  -h, --help            show this help message and exit
  --path PATH           Memory folder (repeatable)
  --quiet
  --debounce-ms DEBOUNCE_MS
```
