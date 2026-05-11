# Changelog

All notable changes to MemForge are documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

The version number tracked here is the **package / tooling** version. The on-disk **spec version** (used in memory frontmatter and folder-index files) is tracked separately at `spec/VERSION` and follows its own SemVer track. A package release MAY include a spec bump; a spec bump MAY ship in a patch release.

## [Unreleased]

The Contributor License Agreement infrastructure is counsel-blocked; external pull requests are paused until the CLA flow lands.

## [0.5.4] - 2026-05-11

**Patch release. Reference-CLI bug fix only; no spec change (spec stays at 0.5.3).** This release ran the release-rigor critic + threat-modeler voices on the patch surface pre-tag; the critic surfaced one MAJOR (over-broad archive-subfolder exclusion) which was closed in-commit before tag.

### Reference implementation changes

- `memforge.cli.audit`: fix false-positive `Orphan pointer (no file)` integrity violations for MEMORY.md pointers targeting subfolder detail files that exist on disk. Background: the audit's canonical `_disk_md_files()` returns top-level `.md` + subfolder `README.md` rollups only, per spec §"Rollup subfolders"; pointers at `<topic>/<detail>.md` failed the disk-set check and were reported as missing even when the file was present in the tree. New `_all_md_files_recursive(folder)` helper distinguishes two cases at the pointer-check site: (a) pointer file truly missing → keep as INTEGRITY violation; (b) pointer file exists but at a non-canonical path → downgrade to HEALTH advisory `"Pointer at subfolder detail file (consider rollup README)"`. `--fix` continues to operate only on truly-orphan pointers; existing valid subfolder-detail pointers are never removed. The top-level `archive/` subfolder is excluded from the recursive walk (consistent with `_files_to_audit`).

### Tests

- 3 new audit tests: no integrity violation for an existing subfolder detail pointer; health advisory emitted for the same; truly-missing subfolder pointer still raises integrity violation. Full suite: 233 pass, 2 GPG-gated skipped.

### Spec compatibility

- No spec change. spec/VERSION stays at 0.5.3. Adapters built against v0.5.3 read paths work unchanged on v0.5.4.

## [0.5.3] - 2026-05-10

**Patch release.** Spec bump 0.5.2 -> 0.5.3. Closes the 3 remaining v0.5.x MAJORs surfaced by the v0.5.2 retrospective threat-modeler pass. This release ran the full multi-voice review panel (architect + critic + threat-modeler) on both the spec delta and the new code surface pre-tag; findings identified by the panel were addressed before ship. Note: multi-voice panel review is an internal review pass, not a substitute for independent red-teaming or third-party security audit. See `spec/known-limitations.md` for the residuals that remain.

### Spec changes

- §"Mandatory cool-down period" extended with a normative **registry-layer enforcement** mandate. Cool-down checks MUST occur at the registry layer (e.g., `memforge.registry.verify_signing_key_acceptable`), not solely at the CLI layer. Closes the v0.5.2 threat-model MAJOR where a buggy or hostile alternate consumer of the registry could bypass the cool-down by signing with the new key directly.
- §"Reader-side revocation walk" extended with a normative **bounded-walk** mandate. The revocation walk MUST be bounded by both a maximum-commits cap (default 100,000) AND a maximum-bytes cap (default 100 MB). When either cap is exceeded, adapters MUST halt the walk and emit a fail-closed message pointing the operator at `memforge revocation-snapshot`. Operator-configurable via `.memforge/config.yaml` keys `revocation.walk_max_commits` and `revocation.walk_max_bytes`. Closes the v0.5.2 threat-model MAJOR where an unbounded walk on a malicious or pathological repo would OOM any adapter walking revocation state at startup.
- Integrity invariant 21 extended with a normative **TOCTOU-safe-read** addendum. Adapters MUST verify mode + ownership on the file descriptor (`fstat` after open) rather than on the path (`stat` before open). POSIX implementations MUST open with `O_NOFOLLOW`; Windows implementations MUST perform path-level verification before open (since `O_NOFOLLOW` is not a Windows concept). Closes the v0.5.2 threat-model MAJOR where a same-uid attacker could swap the file between path-level mode-check and open.

### Reference implementation changes

- `memforge.registry`: new `key_is_in_cooldown(registry, key_id, at_time=None)` + `verify_signing_key_acceptable(registry, key_id, signing_time=None)`. The first reports cool-down status; the second combines registry-membership + cool-down into a single fail-closed verify gate. Cool-down hours floor (1h) now enforced inside `_compute_cooldown_expiry`.
- `memforge.revocation.walk_revocation_set`: rewritten to stream git-log output via `subprocess.Popen` + line-by-line parse, with `max_commits` + `max_bytes` kwargs (defaults match the new spec mandate). Aborts with explicit error pointing the operator at `memforge revocation-snapshot` on cap-exceeded.
- `memforge._security`: new `secure_read_text(path)` + `secure_read_bytes(path)` primitives. POSIX backend uses `O_NOFOLLOW` + post-open fd `fstat` for mode + owner verification; Windows backend uses path-level `verify_owner_restricted`. Refactored consumers: `identity.load_operator_identity`, `agent_session.load_attestation` + `is_nonce_seen` + `record_seen_nonce`, `sender_sequence.load_sender_sequence` now all read through the TOCTOU-safe path.

### Documentation

- `spec/known-limitations.md` (renamed from `spec/v0.5.0-known-limitations.md`; living document): updated to reflect v0.5.3 closures of the BLOCKERs and security-relevant MAJORs surfaced by the internal multi-voice review panel. 4 MINOR refinements remain on the living doc. SPEC.md cross-reference updated to point at the living-doc filename. The "panel-identified" qualifier matters: independent red-teaming or third-party pentest may surface findings the internal panel did not.

### Tests

- 10 new v0.5.3 tests covering registry cool-down (in-window / after-expiry / unlisted-key rejection / cool-down rejection / floor enforcement), bounded revocation walk (commit cap / byte cap / empty-history happy path), and TOCTOU-safe read (symlink refusal / happy path / relaxed-mode rejection). Full suite: 226 pass, 2 GPG-gated skipped.

### Pre-ship review

Ran the full release-rigor playbook (architect + critic + threat-modeler on both spec delta + new code surface). Findings + resolution are operator-side.

### Spec compatibility

- v0.5.2 folders remain well-formed under v0.5.3 readers. No frontmatter or normative-contract regressions.
- Adapters built against v0.5.2 read paths will work unchanged on v0.5.3; the TOCTOU-safe-read addendum is implementation-level + transparent to callers using the higher-level load functions.

## [0.5.2] - 2026-05-10

**Patch release.** Spec bump 0.5.1 -> 0.5.2. Closes 2 BLOCKERs + 1 MAJOR surfaced by the post-v0.5.1 retrospective code + spec panel pass (critic voice + threat-modeler voice on the new code surface; the panel scope skipped during the v0.5.1 first ship and run separately afterward).

### Spec changes

- New normative subsection at §"Signed envelope scope (normative)": **Canonical-form Unicode NFC normalization MUST on signed envelopes.** Closes the repudiation vector where two visually-identical inputs in different normalization forms (NFC vs NFD) would produce different signed envelopes and therefore different signatures, letting a sender repudiate a signature by claiming the verifier received the "other" form.
- §"Seen-nonce set bounding" promoted from MAY to SHOULD with explicit garbage-collection contract: discard each nonce once its corresponding attestation's `expires_at + backdating_max_skew` (default 10 minutes) has passed. Implementations that do NOT bound the set are vulnerable to a disk + memory DoS and SHOULD warn the operator at startup.

### Reference implementation changes

- `memforge.crypto.canonical_envelope` now NFC-normalizes every string in the envelope (keys + values, recursively) before JSON serialization. Backward-compatible for inputs that were already in NFC form (the common case for ASCII + standard composed Unicode). Inputs in NFD form will produce different envelope bytes (and therefore different signatures) under v0.5.2 vs v0.5.1; this is intentional and closes the repudiation vector.
- `memforge.identity.write_secure_yaml` + `write_secure_bytes` now use `O_CREAT|O_EXCL` on a sibling tmp path + `fsync` + atomic `os.replace` to close the TOCTOU window where the file would exist at default umask between create and chmod. Previously, a same-user concurrent reader could observe a file at the default umask permission for a brief window between `open(path, "w")` and `os.chmod(path, 0o600)`.
- `memforge.agent_session.record_seen_nonce` now garbage-collects expired entries on every call, per the new SHOULD-bounding contract.

### Pre-ship review

This release ran the full release-rigor playbook the v0.5.1 retrospective surfaced as missing: architect-voice + critic-voice + threat-modeler-on-new-code-surface. Findings:

- Critic (gemini-pro on spec delta): 1 MAJOR (seen-nonce set unbounded-DoS). Closed by promoting MAY to SHOULD with explicit GC contract in §"Seen-nonce set bounding".
- Threat-modeler (gemini-pro on src/memforge/ code): 2 BLOCKERs (TOCTOU on file create in identity.py write_secure_yaml; Unicode normalization missing in crypto.py canonical_envelope) + 4 MAJORs. Both BLOCKERs closed in this release. 3 MAJORs deferred to v0.5.3 backlog (registry-layer cool-down enforcement, bounded git-log walk in revocation set, TOCTOU between mode-check and read). 1 MAJOR (unbounded seen-nonce set) was the consensus finding from critic + threat-modeler and is closed here.

### Cross-platform support (NEW: native Windows in v0.5.2)

v0.5.0 / v0.5.1 enforced the "file restricted to current owner" spec contract via POSIX mode bits (0600 / 0700) + `stat().st_uid` match. This is correct on macOS / Linux but a no-op on native Windows. v0.5.2 introduces a platform-agnostic abstraction at `src/memforge/_security.py` and restates the spec normative requirement platform-agnostically.

- POSIX (macOS, Linux, *BSD): unchanged. Mode 0600 / 0700 + `stat()` uid match against effective uid.
- Windows (NTFS): ACL-based restriction via the built-in `icacls` binary. `restrict_file_to_owner` / `restrict_dir_to_owner` call `icacls /inheritance:r` + `icacls /grant:r <current-user>:F` to grant Full Control to the current user only with no inherited ACEs. `verify_owner_restricted` parses `icacls` output and rejects on the presence of any forbidden principal (Everyone, Authenticated Users, BUILTIN\\Users, BUILTIN\\Guests, NT AUTHORITY\\INTERACTIVE / NETWORK / BATCH / SERVICE / ANONYMOUS LOGON, etc.).
- Both paths satisfy the spec "file restricted to current owner" contract identically.
- Spec §"Operator-identity file (per-machine)" and integrity invariant 21 restated platform-agnostically; v0.5.0 / v0.5.1 POSIX-mode-specific language preserved as a normative POSIX implementation.

- CI matrix extends from `ubuntu-latest` to `[ubuntu-latest, macos-latest, windows-latest]` x Python 3.10 / 3.11 / 3.12.

- `os.uname()` (POSIX-only) replaced with `platform.node()` for hostname capture in operator-identity files.

### Spec compatibility

- v0.5.1 folders remain well-formed under v0.5.2 readers IF the envelope inputs were already in NFC form (ASCII + standard composed Unicode covers the common case). Inputs in NFD form produce different envelope bytes under v0.5.2 vs v0.5.1; a v0.5.1 signature over NFD content will NOT verify under v0.5.2. Operators with mixed-normalization-form historic content should re-sign affected memories via `memforge upgrade-v04-memories --apply` (the upgrade signs over the v0.5.2-canonical NFC form).
- Filesystem atomicity changes are transparent: existing files at the target paths are replaced atomically; no operator action required.
- Native Windows installs upgrading from v0.5.0 / v0.5.1 will likely have failed the FS-mode check at startup under those versions (POSIX mode bits map to no-op on NTFS); v0.5.2 is the first version where native Windows can satisfy the secure-file contract. WSL installs of v0.5.0 / v0.5.1 already worked (WSL presents a POSIX filesystem) and continue to work in v0.5.2.

## [0.5.1] - 2026-05-10

**Patch release.** Spec bump 0.5.0 -> 0.5.1. Closes the v0.5.0 agent-session-attestation content-scope MAJOR + 3 MINORs in normative text and ships the reference CLI binaries (14 commands).

### Added

- New §"Agent session attestation content scope (v0.5.1+)" subsection. Closes the v0.5.0 MAJOR with normative `nonce` (replay defense), `expires_at` (default 24h; floor 15 min; ceiling 7 days), and `capability_scope` (explicit memory_roots + allowed_operations from {write, resolve, revoke, registry-edit, key-rotation, fresh-start}). Receiver-side enforcement is an 8-step normative checklist including write-signature verification against the attested `agent_pubkey` (closes the trust-boundary gap surfaced by the v0.5.1 light-mode architect pass).
- Agent-session-id normative regex `^[a-z0-9]+-\d{4}-\d{2}-\d{2}-[a-z0-9]{8,16}$` in §"Frontmatter additions (v0.5.0+)". Closes the v0.5.0 MINOR on format guidance. Adapters MUST reject non-matching values on read (audit MAJOR) + refuse to mint non-matching on write.
- New §"Cross-cutting fail-closed posture (v0.5.1+)" section. Closes the v0.5.0 MINOR on documentation. 29-item operator reference organized as hard fail-closed (HALT; 14 items), per-write fail-closed (reject specific write; 9 items), per-config fail-closed (refuse config; 2 items), soft fail-closed (warn + acknowledge; 4 items).
- New §"Privacy considerations (v0.5.1+)" section. Closes the v0.5.0 MINOR on documentation. 7 boundary statements (operator-UUID linkability, signing-time linkability, agent-session-id leakage, sender-UID linkability, operator-name homograph + privacy, cross-store reference disclosure, receiver-side state-file leakage) + out-of-scope list.
- New integrity invariants 23-25 covering agent-session attestation verification (including write-signature verification against attested agent_pubkey), capability-scope enforcement, and agent-session-id format.
- Reference CLI: `memforge` top-level dispatcher with 14 subcommands.
  - `memforge init-operator` , generate operator-UUID + register a GPG key.
  - `memforge init-store` , bootstrap `.memforge/` in a memory-root + signed operator-registry.
  - `memforge operator-registry {add|verify|remove|fresh-start}` , manage operator-registry.
  - `memforge rotate-key` , cross-signed key rotation with 24h cool-down.
  - `memforge revoke <key_id> --reason ...` , build a signed revoke commit body.
  - `memforge revocation-snapshot` , emit a signed snapshot commit body.
  - `memforge memories-by-key <key_id>` , list memories signed by a key.
  - `memforge revoke-memories <key_id> --bulk` , mark memories under a revoked key as superseded.
  - `memforge upgrade-v04-memories --apply` , add v0.5 identity+signature to v0.4 memories in-place.
  - `memforge revoke-cache-refresh` , refresh the remote-fetch revocation cache (sparse/shallow mode).
  - `memforge messaging-doctor` , run the v0.5.1 fail-closed checklist + report posture.
  - `memforge recovery-init` , install ~/.memforge/recovery-secret.bin + anchor SHA256 in registry.
  - `memforge recovery-backup-confirm` , acknowledge offline backup; unlocks v0.5+ writes.
  - `memforge attest-agent` , issue a signed agent-session attestation.
- New Python modules: `memforge.identity` (UUIDv7 + operator-identity file + agent-session-id format), `memforge.crypto` (GPG subprocess wrappers + canonical envelope), `memforge.registry` (operator-registry read/write/sign), `memforge.revocation` (revoke commit builder + revocation-set walker), `memforge.sender_sequence` (sender-uid + sender-sequence + signed checkpoints), `memforge.agent_session` (attestation build/save/load/verify + scope checks + seen-nonce set), `memforge.recovery` (recovery-secret install + SHA256 anchoring + backup acknowledgment).
- 24 new tests in `tests/test_v05_cli.py` covering: dispatcher --help smoke + subcommand registration, UUIDv7 format, now_iso, agent-session-id format + minting + validation, identity parse, canonical envelope determinism, GPG algo denylist, sender-uid format, revoke body reason-length, and (gated on `MEMFORGE_TEST_GPG=1`) end-to-end happy-path round-trips for init-operator + init-store + revoke and attest-agent + scope checks against a sandboxed GPG keyring.

### Changed

- §"Known limitations" header bumped to v0.5.1; documents the 4 v0.5.0 residuals now closed in normative text. Residual MAJORs reduced from 5 to 4; MINORs reduced from 2 to 1.
- §"Not in scope" header bumped to v0.5.1. Reference CLI moved from "deferred to v0.5.1" to "shipped in v0.5.1". Added two new deferrals: privacy-preserving cross-store unlinkability (v0.5.x / v0.6+); per-store operator-UUID derivation (v0.5.x).
- §"Versioning" current spec version updated to 0.5.1. v0.5.1 entry added to §"Versioning history".

### Spec compatibility

- v0.5.0 folders remain well-formed under v0.5.1 readers.
- v0.5.0 attestation files lacking v0.5.1 required content fields (`nonce`, `expires_at`, `capability_scope`) are accepted with a one-time MAJOR `v05_attestation_incomplete_content` per file until re-issued via the v0.5.1 reference CLI.
- v0.4 folders continue to load as `(v0.4: unsigned)` read-only-untrusted; upgrade path via `memforge upgrade-v04-memories --apply`.

### Pre-ship review

A pre-ship architect pass caught one BLOCKER: receiver enforcement omitted normative verification that the write's `signature.value` validates against the attestation's `agent_pubkey`. Closed in the same session before tag.

## [0.5.0] - 2026-05-10

**Minor release.** Spec bump 0.4.0 -> 0.5.0. Extends single-operator multi-agent format to multi-identity team-scale memory with cryptographic attribution and a real-time messaging substrate (WebSocket). Reference CLI binaries ship in v0.5.1.

### Added

- New §"Multi-identity primitives": two identity classes (operator long-lived key; agent ephemeral per-session key). New REQUIRED v0.5+ frontmatter `identity` + `signature`. v0.4 frontmatter remains valid; v0.4 memories load read-only-untrusted under v0.5 readers.
- New §"Cryptographic attribution": GPG (RSA-4096 or Ed25519) default. Signing-time-aware verification + `first_seen_at` clock-skew guard (default +/- 10 min) close the coordinated-backdate attack class. Cross-signed rotation chain bounded by min(N, 10) via fresh-start operator-registry.
- New §"Operator identity + cross-store references": UUIDv7 at `~/.memforge/operator-identity.yaml` (per-machine, 0600/0700). Operator-registry at `<memory-root>/.memforge/operator-registry.yaml` (fail-closed signature verification + content-hash-anchored cache). `MEMFORGE_STORE = <operator-uuid>:<store-name>` for cross-store refs. Multi-operator trust-bootstrap procedure documented.
- New §"Messaging adapter contract (WebSocket reference)": substrate locked WebSocket (40% latency benchmarks; OpenAI Responses API Feb 23 2026 launch alignment; Cursor/Cline/Vercel adoption). Sender-uid format `<operator-uuid>:<32-byte-hex>` mandatory. Sender-sequence + signed checkpoints every 100 sequences or 24 hours. Multi-server hard-stop for v0.5.0. Substrate-independent envelope contract: git-only writers must use same sender_uid + sequence + checkpoint machinery.
- New §"Key lifecycle + revocation": revocation events as git commits with `memforge: revoke <key_id>` prefix. Reader walks git history. Sparse-checkout / shallow-clone fallback via remote-fetch with loud startup banner + audit MAJOR (revocation events NOT signature-verified in fallback mode; v0.5.0.1 patch target). Recovery-secret filesystem mode 0600/0700 + uid-ownership check; persistent startup WARN until hardware-backed install. Revocation snapshot mechanism bounds O(N) cold-start cost.
- New §"Security considerations": operator-facing boundary statements (honest-operator assumption, software-only recovery-secret boundary, same-user shell malware, sparse-checkout caveats, cross-instance propagation lag, hardware-key recommendation, recovery-secret backup mechanism).
- New §"Known limitations": 2 documented BLOCKERs (receiver-state silent-rollback window; remote-fetch unsigned revocation events) as v0.5.0.1 patch targets. Full list at `known-limitations.md` (renamed from per-version `v0.5.0-known-limitations.md` to a living document in v0.5.3; each Zenodo deposit ships a versioned snapshot).
- New §"v0.5.0 surface map": ASCII diagram showing the Element 1 -> 2 -> 4 -> 5 dependency flow.
- New invariants 16-22 in §"Integrity invariants" covering v0.5.0 frontmatter shape, clock-skew, mixed-deployment resolve, operator-registry, sender-sequence, identity-file FS modes, revocation commit prefix discipline.

### Changed

- Spec version 0.4.0 -> 0.5.0.
- `spec/VERSION` updated to `0.5.0`.
- Versioning history extended with v0.5.0 entry.
- §"Not in scope" relabeled "Not in scope for v0.5.0" with expanded list covering hardware-key reference impl, 2-of-N multi-key signing, post-quantum, centralized identity, sub-second propagation, etc.

### Closed in v0.5.0 (originally tagged as v0.5.0.1 patch targets)

Two BLOCKER-class issues identified during v0.5 development have been closed in the v0.5.0 normative spec rather than deferred:

- **Receiver-state silent-rollback window** -> closed via §"Receiver state (MUST)": mandates `<memory-root>/.memforge/receiver-state/<sender-uid>.yaml` (FS mode 0600/0700; ownership check); receiver MUST reject `seq <= highest_seen_sequence`; HALT on corruption.
- **Remote-fetch unsigned revocation events** -> closed via §"Revocation events as git commits" (every revocation commit MUST be GPG-signed; signing-key-matches-revoked_by check) + §"Sparse-checkout / shallow-clone fallback verification mode" (pin remote URL + transport; TOFU on first fetch; fast-forward-only after; signature verification on every fetched revocation commit).

### Known limitations

v0.5.0 ships with **no BLOCKER-class known limitations**. Residual MAJORs + MINORs (refinements; not security gaps) tracked at `known-limitations.md` (living document; renamed from per-version filename in v0.5.3) for v0.5.1 / v0.5.x patches:

- 6 MAJORs (checkpoint signer ambiguity, revocation snapshot ancestor + canonical hash, sender/receiver posture nuance, cache TTL high-stakes, cross-cutting fail-closed documentation, agent session attestation content scope).
- 7 MINORs (cross-cutting fail-closed posture, TTL semantics, trust graph disclosure, agent session ID format, key rotation chain DoS, operator name homograph audit, v0.4 memory flooding rate-limit).
- 4 reference-CLI MAJORs (`memforge init-operator`, `init-store`, `operator-registry`, `revoke-memories`) ship in v0.5.1 binary release.

## [0.4.3] - 2026-05-08

Patch release: closes the duplicate-keys / growing-frontmatter regression in `memory-frontmatter-backfill` that surfaced when a memory file's `description:` value contained an unquoted colon-space. No spec changes (spec stays at 0.4.0).

### Fixed

- `memory-frontmatter-backfill` no longer corrupts files whose YAML frontmatter fails to parse. Previously, when `yaml.safe_load` raised on a value like `description: A: B description with embedded colon-space`, the parser collapsed the failure to an empty dict, every required field appeared "missing", and `apply_change` line-appended a fresh set of fields. The `memory-auto-commit.sh` PostToolUse hook then re-ran backfill on every Write/Edit, producing growing blocks of duplicate keys. Two-layer fix: (1) `plan_change` detects "frontmatter present but YAML parse failed" via the new `_frontmatter_present_but_unparseable` helper and skips with a stderr warning; (2) `apply_change` now does a dict-merge round trip via `memforge.frontmatter.render` instead of line-level appending, so duplicate keys are structurally impossible in the output even when called repeatedly.

### Tests

- New `tests/test_frontmatter_backfill.py` covers the colon-space detection path, the duplicate-key edge case (PyYAML silently last-wins, helper correctly returns False), the round-trip render of valid YAML, idempotency under repeated `apply_change` calls, preservation of existing operator-set fields, and the defense-in-depth skip in `apply_change` when fed broken YAML directly.

## [0.4.2] - 2026-05-08

Patch release: completes the `memory-audit` recursive-validation gap surfaced after v0.4.1, and bumps GitHub Actions to Node.js 24-compatible versions. No spec changes (spec stays at 0.4.0).

### Fixed

- `memory-audit` per-file frontmatter validation now recurses into rollup subfolders (excluding `archive/`), as required by spec §"Rollup subfolders" ("Audit tools MUST recurse into rollup subfolders to validate frontmatter, but MUST NOT generate parent-MEMORY.md pointers for detail files"). Previously the per-file audit reused the pointer-comparable file set, so detail-tier files inside rollups were silently skipped: YAML parse failures, missing frontmatter, invalid types, sensitivity issues, and staleness in those files all went unreported. v0.4.1 closed the orphan-pointer half of this gap; v0.4.2 closes the per-file half.

### Changed

- New helper `_files_to_audit()` in `memforge.cli.audit` returns the recursive audit set (top-level `.md` plus every `.md` inside any first-level subfolder, excluding `archive/`). The orphan-pointer comparison still runs against `_disk_md_files()` (top-level + rollup READMEs only), so detail-tier files do NOT generate spurious `Orphan file (no pointer)` violations. The per-file audit at `audit_target` line ~193 now iterates `_files_to_audit()`.
- `.github/workflows/release.yml`: bumped pinned action SHAs to Node.js 24-compatible major versions:
  - `actions/checkout` v4 -> v6.0.2
  - `actions/setup-python` v5 -> v6.2.0
  - `actions/upload-artifact` v4 -> v7.0.1
  - `actions/download-artifact` v4 -> v8.0.1
  - `pypa/gh-action-pypi-publish` `release/v1` -> v1.14.0

  Closes the v0.4.1 publish-workflow annotation about Node.js 20 deprecation (forced default June 2, 2026; runner removal September 16, 2026).

### Added

- `tests/test_audit.py` grows from 5 to 9 cases. New: `_files_to_audit` semantics (top-level only / recurses into rollups / excludes archive recursively) plus an end-to-end test asserting that a YAML parse failure in a rollup detail file is correctly reported (regression coverage for the silent-skip behavior pre-v0.4.2).

## [0.4.1] - 2026-05-08

Patch release: audit fix, adapter improvement, install docs. No spec changes (spec stays at 0.4.0).

### Fixed

- `memory-audit` no longer fires false-positive `Orphan pointer (no file)` for rollup-subfolder README files. `_disk_md_files` now returns top-level `.md` files plus `<topic>/README.md` for each first-level subfolder (excluding `archive/`), matching the spec's §"Rollup subfolders" rule that rollup READMEs are tier:index files surfacing in the parent `MEMORY.md`. Per-file frontmatter validation also extends to those READMEs as a side effect, narrowing the audit-vs-spec gap on subfolder coverage. Closes #8.

### Changed

- Adapter `adapters/claude-code/hooks/memory-auto-commit.sh` now runs `memory-frontmatter-backfill --apply` before committing on every Write/Edit inside a memory folder. Auto-normalizes v0.3-shaped frontmatter (the Claude Code harness auto-memory instruction emits 3 fields) to v0.4 shape on write. Portable: uses `command -v` to find the backfill CLI; silently skips if absent (works on machines without the package installed).

### Documentation

- README adds a "macOS / Homebrew Python (PEP 668)" subsection covering pipx, dedicated venv, and `--user --break-system-packages` install variants. Same guidance applies to most current Linux distributions that mark their system Python `EXTERNALLY-MANAGED`.
- `tools/README.md` clarifies that the `.py` shim scripts are reference implementations, not the canonical install path. `pip install ildan-memforge` installs all CLI commands as `console_scripts` entry points on `$PATH`, which is the supported install path.
- Tool-count references updated from "15 CLI commands" / "15+ commands" to "17 commands" (v0.4.0 added `memforge-resolve` and `memforge-migrate-claim-block`).

## [0.4.0] - 2026-05-08

Package release carrying spec v0.4.0.

### Added

- `memforge-resolve` CLI: walk the operator through reconciling competing claims for a `decision_topic`. Mutates winner / loser frontmatter, deletes any active snooze, and commits with `memforge: resolve <topic>` prefix.
- `memforge-migrate-claim-block` CLI: idempotent depth-aware fixer for the canonical reader-side competing-claim block in `MEMORY.md`.
- `memory-audit --export-tier=<level>` flag: v0.4 sensitivity export-tier gate. Reads `audit.default_export_tier` from `.memforge/config.yaml` when the flag is absent. Privileged-tier files always block when the gate runs, regardless of `audit.enforce_sensitivity_export_gate` config.
- `memory-dlp-scan --no-sensitivity-cross-check` flag: per-invocation override for the v0.4 `sensitivity_label_mismatch` BLOCKER. Cannot disable when implied tier is `privileged` (hard floor).
- `memforge.cli._config` module: shared loader for `.memforge/config.yaml` with auto-discovery, defaults merge, frontmatter sensitivity parser, and canonical `TIER_ORDER` constant.
- `memforge.cli._concurrency_audit` module: layered Tier 1 (HEAD-pure) and Tier 2 (commit-log) audit invariants for the v0.4 multi-agent concurrency surface.
- `tests/conformance/sensitivity/`: five conformance scenarios covering export-tier-{public, internal, restricted, privileged} and label-mismatch-blocked.
- `.gitleaks.toml` allowlist for the DLP scanner files (their own regex patterns are detected by gitleaks otherwise).

### Changed

- `memory-dlp-scan`: `Pattern` dataclass gains `implied_tier` field; PATTERNS table maps every detector to a tier (secret-class -> restricted, PII -> restricted, high-entropy heuristic -> internal). Cross-check fires by default; emits BLOCKER `sensitivity_label_mismatch` when declared sensitivity is below the highest implied tier across findings.
- `memory-audit`: integrates the v0.4 concurrency audit (`run_concurrency_audit`) and the export-tier gate. Surfaces `[v0.4]` and `[v0.4 MAJOR]` / `[v0.4 WARN]` prefixes in the violation list.
- `memory-index-gen`: `render_competing_claims_block(folder_root)` emits the canonical fenced YAML block per the v0.4 reader-side contract; byte-match CI on the rendered block.

### Test coverage

- 163 tests pass on Python 3.10 / 3.11 / 3.12 (39 new in this release).

## [spec 0.4.0] - 2026-05-08

v0.4 is a major bump per SemVer applied to spec semantics. The package release carrying this spec is `[0.4.0]` (above).

### Added (spec)

- **§"Multi-agent concurrency: competing claims" section** in SPEC.md. Five new frontmatter keys (`decision_topic`, `replaces`, `superseded_by`, `topic_aliases`, `ever_multi_member`), a snooze record at `.memforge/snoozes/<topic>.yaml`, a config file at `.memforge/config.yaml` with hard-floor + edit-gate protection, the resolve operation contract (tool-neutral; CLI reference + Claude Code skill + Cursor / Continue / Aider / shell wrappers), the canonical reader-side competing-claim YAML block (byte-match CI), and a layered Tier 1 (HEAD-pure) + Tier 2 (commit-log) audit rule set. Closes the v0.3.x "Multi-user concurrency semantics (Phase 2+ concern)" deferral.
- **Status enumeration BLOCKER**: any value outside `{active, proposed, gated, superseded, dropped, archived}` is a HEAD-pure audit BLOCKER.
- **Status transition gating**: transitions to `superseded` MUST occur in a `memforge: resolve <decision_topic>` commit (Tier 2 BLOCKER if violated). Transitions to `archived` go through the generator (existing Phase-1 contract).
- **Secure-mode adapter conformance** (informative): adapters MAY claim secure-mode by detecting branch protection + required signed commits at startup; informative startup notice required when not in secure-mode.
- **Reserved-name slug denylist** for `decision_topic` (Windows device names: `con`, `aux`, `nul`, `prn`, `com[0-9]`, `lpt[0-9]`).
- **§"Sensitivity enforcement (v0.4.0+)"** in SPEC.md. Three default-on, operator-disable-able checks tied to a hard floor at the `privileged` tier: an audit-side **export-tier gate** (`memory-audit --export-tier=<level>` or `audit.default_export_tier` config), a DLP-side **label/content cross-check** that emits BLOCKER `sensitivity_label_mismatch` when declared sensitivity is below the implied tier of body content, and a **conformance fixture set** (`tests/conformance/sensitivity/`) covering five scenarios that secure-mode adapters MUST pass. Adds three config keys (`audit.default_export_tier`, `audit.enforce_sensitivity_export_gate`, `dlp.enforce_sensitivity_cross_check`); a `--no-sensitivity-cross-check` CLI flag on `memory-dlp-scan`; and a `--export-tier` flag on `memory-audit`. Privileged-tier enforcement cannot be disabled by config.

### Changed (spec)

- **Required frontmatter fields expanded.** `uid`, `tier`, `tags`, `owner`, `status`, `created` are now required (formerly optional in v0.3.x). A v0.4-conformant memory must carry: `name`, `description`, `type`, `uid`, `tier`, `tags`, `owner`, `status`, `created`. The `sensitivity` field remains independent.
- **v0.3.x backward compatibility: degraded mode.** Files written under v0.3.x that lack the newly-required fields load in degraded mode. Adapters MUST accept them, MAY warn, and SHOULD prompt the operator to backfill. Degraded-mode memories appear in `MEMORY.md` but cannot participate in rollup contracts, status-driven archival, or any v0.4 reader-side contract that depends on a v0.4-required field. Backfill is one-shot per file.
- **Integrity invariants** extended (rules 11-15) to cover the asymmetric-supersession contract, exactly-one-active per `ever_multi_member: true` group, `ever_multi_member` monotonicity, status enumeration, and the layered audit rule set.
- **§"Not in scope"** retitled to v0.4.0; multi-user concurrency removed (now in scope), v0.5.0 deferrals enumerated (centralized taxonomy, per-decision ledger, DAG cycle rejection, UUIDv7, vector-clock tie-breaker, CRDT, cryptographic provenance).

## [0.3.1] - 2026-05-07

Patch release wiring proper console scripts so `pip install ildan-memforge`
actually ships the CLI. Same on-disk format as 0.3.0; no schema or behavior
changes.

### Distribution name

The PyPI distribution is published under **`ildan-memforge`** because the
shorter `memforge` name on PyPI is already held by an unrelated project.
The Python import path is still `memforge`, and the CLI command names are
still `memory-audit`, `memory-watch`, etc. Only the install command differs.

### Added

- **`[project.scripts]` entry points for all 15 CLI tools.** After
  `pip install ildan-memforge`, the following commands land on `$PATH`:
  `memory-audit`, `memory-audit-deep`, `memory-audit-log`,
  `memory-cluster-suggest`, `memory-dedup`, `memory-dlp-scan`,
  `memory-frontmatter-backfill`, `memory-index-gen`, `memory-link-rewriter`,
  `memory-preamble-extract`, `memory-promote`, `memory-query`,
  `memory-rollup`, `memory-watch`, `agents-md-gen`. Previously the package
  was importable but none of the tools were on `$PATH`.

### Changed

- **Tool source moved into `src/memforge/cli/<name>.py` modules.** Each tool
  is now an importable module with a `main()` function; the corresponding
  `tools/<name>` script is now a thin shim that re-enters through the
  module's `main()`. Both invocation paths share the same code.
- **`memory-audit` rewritten in Python** (was bash). Cross-platform, uses
  `memforge.frontmatter.parse` for YAML parsing instead of awk, and gives a
  clearer error when frontmatter YAML fails to parse (single
  `"frontmatter YAML failed to parse"` violation instead of a misleading
  trio of `missing name/description/type`).
- **`memory-promote` rewritten in Python** (was bash). Cross-platform git
  operations via `subprocess`; same flag surface (`--source`, `--target`,
  `--dry-run`, `--no-commit`, `--yes`).

### Removed

- Bash sys.path / `_HERE` shims at the top of each tool (no longer needed
  now that tools live inside the installed package).

[Unreleased]: https://github.com/ildan-ai/memforge/compare/v0.3.1...HEAD
[0.3.1]: https://github.com/ildan-ai/memforge/releases/tag/v0.3.1

## [0.3.0] - 2026-05-07

First public release.

### Added

- Project published under the `memforge` name. Tools, package imports (`memforge.frontmatter`, `memforge.discovery`, etc.), and CLI binaries all use the `memforge` namespace.
- **Spec 0.3.0** at `spec/SPEC.md`. Codifies:
  - The four memory types: `user`, `feedback`, `project`, `reference`.
  - Required `**Why:**` and `**How to apply:**` lines for `feedback` and `project` bodies (so future readers can judge edge cases instead of pattern-matching).
  - Rollup-with-subfolder pattern for topic clusters of five or more memories.
  - Optional UID frontmatter field (stable identifier for cross-folder linking via `mem:uid`).
  - Optional `access:` label for sensitivity-and-redaction scoping.
- **Reference adapter for Claude Code** at `adapters/claude-code/`. Includes auto-commit PostToolUse hook and the index-loading instruction that ships in `~/.claude/CLAUDE.md`.
- **Production adapters for Aider, Codex (OpenAI), Cursor, and GitHub Copilot Chat (VS Code)** under `adapters/`. Each adapter documents the agent-specific surface (rules file, instructions file, etc.) it bridges to the shared MemForge folder.
- **Cross-platform `memory-watch`** (Linux/macOS via watchdog).
- **Shared tooling at `tools/`:**
  - `memory-index-gen` (RBAC-aware folder index generator with `access:` filtering).
  - `memory-audit` and `memory-audit-deep` (integrity + health checks; `--strict` exits nonzero on violations).
  - `memory-cluster-suggest` (rollup candidate detection; O(n²) avoidance via inverted topic + token indexes).
  - `memory-query` (read-only structured query over a folder).
  - `memory-rollup` (rollup primitive with batch link rewriting).
  - `memory-link-rewriter` (UID-link integrity; `check`, `rename`, `rename-batch`, `upgrade` subcommands).
  - `memory-frontmatter-backfill` (Phase 1 migration helper).
  - `memory-preamble-extract` (Phase 1 migration helper).
  - `memory-dlp-scan` (detect-secrets + Shannon-entropy heuristic for memory bodies).
  - `memory-audit-log` (append-only JSONL with chained hash; `tail_record` for append without full reread).
  - `memory-dedup` (LLM-assisted near-duplicate detection; `--local-only` and `--redact-descriptions` are defaults).
- **Controlled topic taxonomy** at `spec/taxonomy.yaml`. Pinned vocabulary for the `topic:` axis to prevent label sprawl. Version 0.3.1 of the taxonomy ships alongside spec 0.3.0.
- **Repository-level `SECURITY.md`** with the `security@ildan.ai` reporting channel and the disclosure SLA.

### Changed

- **All shared tooling now imports `memforge.frontmatter.parse`** for YAML-frontmatter parsing. Eliminates seven prior copies of the same parser across the tooling tree.
- **`memory-dedup` rewritten in Python.** Defaults to local-only mode (no remote LLM call) and to redacted descriptions when remote dispatch is opted in.
- **`memory-cluster-suggest` clustering pre-filter** uses inverted topic and token indexes; eliminates the O(n²) pairwise pass on large folders.
- **Bash portability fixes** in `memory-audit`. Replaced macOS-specific `stat -f` and `date -r` with python3 inline calls so the tool runs the same on Linux + macOS.

### Fixed

- `memory-audit-log` no longer reads the full JSONL on append; uses `tail_record()` (seek-from-EOF) to read the prior chain hash in O(1).
- `memory-index-gen` preserves `access_labels` as a list (was being scalar-coerced); tier-and-team composition now applied correctly.

### Security

- DLP pass is now part of `memory-audit` health checks rather than a separate manual step.
- `memory-dedup` redacts memory descriptions before any optional remote LLM dispatch.

[0.3.0]: https://github.com/ildan-ai/memforge/releases/tag/v0.3.0
