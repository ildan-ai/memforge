# MemForge known limitations

**Updated:** 2026-06-14 (covers v0.6.1 ship state)
**Status:** Living document. Each spec release updates this file; the file ships with the Zenodo deposit for that release as a versioned snapshot.

---

## Why this document exists

Tracks residual non-BLOCKER refinements queued for future patches. A BLOCKER would block the release; anything tracked here is by definition a refinement that the release ships with.

**What this document is NOT.** This document tracks issues surfaced by the project's internal multi-voice review panel (architect + critic + threat-modeler). It is not a substitute for independent red-teaming, third-party pentest, or a formal security audit. Independent review may surface findings the internal panel did not. The "no residual BLOCKERs" status reflects the panel's review scope, not an external attestation.

---

## Closed since v0.5.0

The following items were tracked as residuals in the v0.5.0 known-limitations document and have since been closed in normative spec text. Listed for historical clarity.

| Item | Closed in | Spec section |
| ---- | --------- | ------------ |
| Agent session attestation content scope (MAJOR) | v0.5.1 | §"Agent session attestation content scope (v0.5.1+)" |
| Cross-cutting fail-closed posture documentation (MINOR) | v0.5.1 | §"Cross-cutting fail-closed posture (v0.5.1+)" |
| Privacy considerations subsection (MINOR) | v0.5.1 | §"Privacy considerations (v0.5.1+)" |
| Agent-session-id format guidance (MINOR) | v0.5.1 | §"Frontmatter additions (v0.5.0+)" |
| Canonical-form Unicode normalization MUST on signed envelopes (BLOCKER from v0.5.2 retrospective) | v0.5.2 | §"Signed envelope scope (normative)" |
| TOCTOU on file create in write_secure_yaml / write_secure_bytes (BLOCKER from v0.5.2 retrospective) | v0.5.2 | code change in `src/memforge/identity.py` |
| Seen-nonce set bounding (MAJOR; MAY -> SHOULD with explicit GC contract) | v0.5.2 | §"Agent session attestation content scope" |
| Native Windows support (POSIX-mode-only contract was a no-op on NTFS) | v0.5.2 | §"Operator-identity file (per-machine)" + integrity invariant 21 restated platform-agnostically |
| Registry-layer cool-down enforcement (MAJOR; CLI-only was a bypass surface) | v0.5.3 | §"Mandatory cool-down period" registry-layer mandate; `memforge.registry.verify_signing_key_acceptable` |
| Bounded git-log walk in `walk_revocation_set` (MAJOR; unbounded walk was a DoS surface) | v0.5.3 | §"Reader-side revocation walk" bounded-walk mandate; default caps 100k commits / 100 MB |
| TOCTOU between path-level mode-check and file read (MAJOR) | v0.5.3 | Integrity invariant 21 TOCTOU-safe-read addendum; `_security.secure_read_text` + `secure_read_bytes` |

The v0.5.3 ship closes the BLOCKER and security-relevant MAJOR residuals surfaced by the project's internal multi-voice review panel to date. Independent red-teaming or third-party pentest has not been performed.

---

## v0.6.0 (query-triggered recall)

v0.6.0 adds query-triggered recall (three optional frontmatter fields + the §"Recall operation" contract + the `memory-recall` reader and `memory-index-gen --with-recall-index`). It ships with **no BLOCKER-class known limitations**: the internal spec-delta panel and code threat-model panel (two model families each) returned zero residual BLOCKERs. The threat-model fixes (symlink-safe walk, load-time index hardening plus an untrusted-context injection preamble, fail-closed sensitivity check, bounded file reads) landed in the v0.6.0 commit.

Deferred refinements (not gaps):

- **Embedding / semantic ranking.** v0.6.0 matching is deterministic keyword + light-stem + synonym expansion. Embedding-based ranking remains out of scope (it was already deferred under the reserved `dynamic_supplement` field); promote if recall miss-rate data justifies it.
- **Per-file incremental index build.** The reference build recompiles the whole index and records per-file mtimes; it skips a rebuild when nothing changed, but does not yet do per-file incremental updates. Adequate for typical folder sizes; a v0.6.x optimization for very large folders.

---

## v0.6.1 (recall-readiness lint)

v0.6.1 is a patch bump: it adds the §"Recall-readiness lint" spec section and the §"Tier and recall (clarification, v0.6.1+)" note, the `memory-lint` reader, and a set of WARN-only `memory-audit` recall-field + always-set budget checks. No frontmatter or normative-contract change, so every v0.6.0 folder remains conformant. The internal spec-delta panel and code threat-model panel returned **no BLOCKER-class known limitations**.

Deferred refinements (not gaps):

- **Lint scoring algorithm is non-normative.** The spec defines lint by the dimensions it scores and the safety posture it MUST observe, not by a scoring algorithm, numeric threshold, or model prompt. The reference implementation's collision-based recall score and its default thresholds (`--min-score`, description/body length cutoffs) are implementation choices that MAY change between patch releases without a spec bump; do not treat a specific numeric score as a conformance signal.
- **Always-set budget is advisory only.** The `memory-audit` always-set budget check (configurable count + combined-description-char budget under `recall.*` in `.memforge/config.yaml`) is WARN-only health, never an integrity violation, so `--strict` is unaffected and existing stores never fail on upgrade. A hard budget gate is not in scope for v0.6.x.
- **Lint never mutates and never gates.** Lint is read-only and its model-assisted suggestion layer is off by default (local-only when enabled; metadata-only cloud payload unless `--allow-cloud-body`; deterministic secret pre-scan fail-closed). Auto-applying a suggested description / trigger set is out of scope; the operator edits files.

---

## Residual MINORs (v0.5.x patch targets)

These are refinements / nice-to-haves; not security gaps and not gating release rigor.

### MINOR 1: Cache TTL semantics for revocation cache in remote-fetch fallback

Document TTL semantics + edge cases for revocation cache in remote-fetch fallback mode. Includes what happens on TTL=0, on TTL > snooze-horizon, etc.

### MINOR 2: Unbounded key rotation chain length guidance

Spec recommends fresh-start every 10 rotations (operator discretion). v0.5.x adds adapter-side `max_rotation_chain_length` config (default 20) with persistent MAJOR audit warning when exceeded.

### MINOR 3: Operator name homograph audit

`memory-audit` SHOULD warn on new operator-registry additions whose `operator_name` has Levenshtein distance <= 2 from any existing operator's name. Defends against visually-similar Unicode substitutions in trust-bootstrap step. v0.5.x patch.

### MINOR 4: v0.4 memory flooding audit MAJOR rate-limit

Audit emits a one-time MAJOR per unsigned v0.4 memory under v0.5 readers. An attacker with write access could flood the repository with thousands of valid v0.4 memories, generating audit noise. v0.5.x adds `audit.v04_unsigned_memories_rate_limit` config to cap the count of such MAJORs reported per audit run (default 10; rest are summarized).

---

## Reference CLI status

v0.5.1 shipped 14 subcommands under a single `memforge` dispatcher (init-operator, init-store, operator-registry add/verify/remove/fresh-start, rotate-key, revoke, revocation-snapshot, memories-by-key, revoke-memories, upgrade-v04-memories, revoke-cache-refresh, messaging-doctor, recovery-init, recovery-backup-confirm, attest-agent).

Still v0.5.x scope:

- `memforge recovery-init --hardware <yubikey|secure-enclave|tpm>`: hardware-backed recovery-secret install.
- `memforge resolve <topic>` integration into the top-level dispatcher (currently shipping as the standalone `memforge-resolve` console script; the spec describes the resolve operation by post-conditions, so the standalone script is conformant, but the dispatcher subcommand is the intended ergonomic form).
- `memforge migrate-claim-block` integration into the top-level dispatcher (currently shipping as a standalone console script).
- `memforge verify-memory <path>`: CLI helper that runs the canonical verify-flow (registry-membership + cool-down + revocation + signature). Spec mandates the contract via `memforge.registry.verify_signing_key_acceptable`; CLI delivery is a v0.5.x ergonomics improvement.

---

## How this document is published

- SPEC.md cross-references this file as a sibling file.
- Each Zenodo deposit includes a snapshot of this file (renamed to `vX.Y.Z-known-limitations.md` inside the deposit) as a top-level artifact.
- GitHub release notes for each version enumerate the v0.5.x patch targets.
- The living version in this repo (`spec/known-limitations.md`) reflects the latest spec state; git history captures per-release snapshots.

Operators who deploy a given version encounter this document via any of these surfaces; surprise is mitigated.
