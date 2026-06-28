# Defensive publication record

This file tracks MemForge's defensive-publication artifacts: the citable archival deposits + timestamped operator-brand disclosures + GitHub release tags that together establish the prior-art record for the format and tooling. The cadence: every minor spec bump (v0.3 → v0.4 → v0.5 → ...) gets a new Zenodo deposit version; major architectural pivots trigger a fresh deposit bundle.

## Channel 1 — Zenodo + Software Heritage (citable archival deposits)

### v0.5.0

- **Zenodo DOI:** [10.5281/zenodo.20113964](https://doi.org/10.5281/zenodo.20113964)
- **Minted:** 2026-05-10
- **Resource type:** Publication / Technical note
- **License:** Apache-2.0 (code + spec); CC-BY-4.0 (prose)
- **Bundle contents:** SPEC.md (v0.5.0); spec/VERSION; spec/taxonomy.yaml; spec/v0.5.0-known-limitations.md; CHANGELOG.md (through v0.5.0); LICENSE; README.md (bundle map + Zenodo metadata template).
- **Software Heritage SWHID:** *(pending; submit https://github.com/ildan-ai/memforge to https://archive.softwareheritage.org for a v0.5.0 snapshot SWHID; record here when minted)*

### v0.5.3

- **Zenodo DOI:** [10.5281/zenodo.20114965](https://doi.org/10.5281/zenodo.20114965) (new version under the same concept DOI as v0.5.0)
- **Minted:** 2026-05-10
- **Resource type:** Software
- **License:** Apache-2.0
- **Bundle contents:** SPEC.md (v0.5.3); spec/VERSION; spec/taxonomy.yaml; v0.5.3-known-limitations.md (renamed living-doc snapshot); CHANGELOG.md (full history through v0.5.3); LICENSE; README.md.
- **Bundle SHA-256:** `c5b9775838f6130ba9c144c2ea31f763a1c60cce86df8423e77ad3b1df104023`
- **Maintainer note:** v0.5.1 and v0.5.2 were skipped for Zenodo by maintainer decision; v0.5.3 is the first end-to-end-baked release in the v0.5.x line. Patch-release-no-Zenodo cadence rule below is suspended for the v0.5.x line; resumes from v0.6 minor bump.
- **Software Heritage SWHIDs:** minted 2026-05-11 via save-code-now (request 2327338; ingestion completed 9 seconds after submission, visit_status=full).
  - Release-level (the v0.5.3 tag): `swh:1:rel:bdd321df0d1a57b7bb1e5bae4c68bd7c237beea1` — canonical prior-art anchor for "what we tagged as v0.5.3".
  - Commit-level (the v0.5.3 tagged commit): `swh:1:rev:2c9a6f41e0112bdf7fefcdd40c0e877e87d0474a` — anchor for the underlying source-tree state.
  - Snapshot-level (whole repo at ingestion): `swh:1:snp:834e3bf3b276f5fa5017cb725308aabdb5719215` — anchor for "the whole memforge repo as it existed when the v0.5.3 ingestion ran".

### v0.5.4

- **Zenodo DOI:** *(none; patch-release-no-Zenodo cadence rule from §"Re-publication cadence" applies — v0.5.4 is a reference-CLI bug fix with no spec change, so it flows through Channel 3 GitHub release tag + Channel 1 Software Heritage only)*
- **Tag pushed:** 2026-05-11
- **PyPI published:** 2026-05-11 (verified via https://pypi.org/pypi/ildan-memforge/json)
- **Software Heritage SWHIDs:** minted 2026-05-11 via save-code-now (request 2327403; ingestion succeeded on first poll, visit_status=full).
  - Release-level (the v0.5.4 tag): `swh:1:rel:d25a13163db64530921a3bf96e7903f00ce4d272` — canonical prior-art anchor for "what we tagged as v0.5.4".
  - Commit-level (the v0.5.4 tagged commit): `swh:1:rev:1943ee451d1ea8ad876f885c78008a73577403fd` — anchor for the underlying source-tree state.
  - Snapshot-level: `swh:1:snp:7508cf252667e1a640aaad3bc58da65de1546113`.

### v0.5.6

- **Zenodo DOI:** [10.5281/zenodo.20115596](https://doi.org/10.5281/zenodo.20115596) (new version under the same concept DOI as v0.5.0 + v0.5.3).
- **Minted:** 2026-05-11.
- **Resource type:** Software.
- **License:** Apache-2.0.
- **Bundle contents:** SPEC.md (spec_version 0.5.3); VERSION; taxonomy.yaml; v0.5.6-known-limitations.md (living-doc snapshot at v0.5.6); CHANGELOG.md (full history through v0.5.6); LICENSE; README.md; full examples/ tree (commit-msg hook bash + PowerShell, auto-commit watcher bash + PowerShell, WebSocket scaffold).
- **Bundle SHA-256:** `39a0b20a757d5a3565bad39d1373abf6bb8cb8c72deddff755255a5da090334e`
- **Maintainer note:** v0.5.4 and v0.5.5 were skipped for Zenodo per the patch-no-Zenodo cadence rule; v0.5.6 mints a fresh Zenodo version because the new examples/ directory is canonical operator-side surface worth anchoring alongside the spec snapshot. The v0.5.3 Zenodo record at DOI 10.5281/zenodo.20114965 remains the historical anchor for the v0.5.3 spec snapshot.
- **Software Heritage SWHIDs:** minted 2026-05-11 via save-code-now (request 2327504; ingestion succeeded, visit_status=full).
  - Release-level (the v0.5.6 tag): `swh:1:rel:d53e346866ee28926494d71e985e7b7f083c141b`.
  - Commit-level (the v0.5.6 tagged commit): `swh:1:rev:b5be5757991943dadc7875cf37cbba84eceac63e`.
  - Snapshot-level: `swh:1:snp:2f7c7e1d201503679d62f3a49bc5f14c3ec33bf1`.

### v0.6.1

- **Zenodo DOI:** [10.5281/zenodo.20580544](https://doi.org/10.5281/zenodo.20580544) (new version under the same concept DOI 10.5281/zenodo.20113963 as v0.5.0 + v0.5.3 + v0.5.6).
- **Minted:** 2026-06-07.
- **Resource type:** Software.
- **License:** Apache-2.0.
- **Bundle contents:** SPEC.md (spec_version 0.6.0); VERSION; taxonomy.yaml; known-limitations.md (living-doc snapshot at v0.6.1); CHANGELOG.md (full history through v0.6.1); LICENSE; README.md; full examples/ tree (commit-msg hook bash + PowerShell, auto-commit watcher bash + PowerShell, WebSocket scaffold, and the new examples/recall/ surface).
- **Bundle SHA-256:** `4c5478c65da97a5389744234ff349ed72570e03bcf80de05a5641866485f2e9c`
- **Maintainer note:** this deposit anchors the v0.6 spec line. The v0.6.0 minor spec bump (query-triggered recall) had not been deposited at its tag time, so the v0.6.1 head (current corrected state, carrying the docs + packaging patch over the unchanged v0.6.0 spec snapshot) is the deposited version. No separate v0.6.0 Zenodo record exists; spec/VERSION is 0.6.0 in this bundle. The v0.5.6 Zenodo record at DOI 10.5281/zenodo.20115596 remains the historical anchor for the v0.5.x line.
- **Software Heritage SWHIDs:** minted 2026-06-07 via save-code-now (request 2352141; ingestion succeeded, visit_status=full).
  - Release-level (the v0.6.1 tag): `swh:1:rel:9aa073ef43f770aa61880abbaa8b48f5bac6d2d7`.
  - Commit-level (the v0.6.1 tagged commit): `swh:1:rev:1ed9248d59819e00964886ad0286f574e15d825d`.
  - Snapshot-level: `swh:1:snp:c290470c870e72a515042a9937e5318d0c1202d9`.

### v0.7.0

- **Zenodo DOI:** [10.5281/zenodo.20695178](https://doi.org/10.5281/zenodo.20695178) (new version under the same concept DOI 10.5281/zenodo.20113963 as v0.5.0 + v0.5.3 + v0.5.6 + v0.6.1).
- **Minted:** 2026-06-14.
- **Resource type:** Software.
- **License:** Apache-2.0.
- **Bundle contents:** SPEC.md (spec_version 0.6.1); VERSION; taxonomy.yaml; known-limitations.md (living-doc snapshot at v0.7.0); CHANGELOG.md (full history through v0.7.0); LICENSE; README.md; full examples/ tree (commit-msg hook bash + PowerShell, auto-commit watcher bash + PowerShell, WebSocket scaffold, examples/recall/ surface).
- **Bundle SHA-256:** `5217e6197944e288cf1fd228d2534a74e0e0104a51493f03fda4f1db8de1f824`
- **PyPI artifacts:** wheel sha256 `e06c68183083faa7e23c14303ce2eb9e91de74b3b4ca57622a80bafc522659a2`; sdist sha256 `089e4cbb3abeb0578d1a30567c67450439c7ff048bd0b2b73f9a7160749009bb`.
- **Maintainer note:** anchors the v0.7.0 package release (memory-lint quality CLI plus a security and correctness hardening pass). spec/VERSION is 0.6.1 in this bundle; the spec line moves on its own SemVer track (the additive lint section landed at 0.6.1). The v0.6.1 Zenodo record at DOI 10.5281/zenodo.20580544 remains the historical anchor for the v0.6 spec line.
- **Software Heritage SWHIDs:** minted 2026-06-14 via save-code-now (request 2359659; ingestion succeeded, visit_status=full).
  - Release-level (the v0.7.0 tag): `swh:1:rel:2bffd6724e08a1d80c368f4e6c3ecf3b4e701168`.
  - Commit-level (the v0.7.0 tagged commit): `swh:1:rev:f09906d03241e56b8a7506b4f30a3747cb088514`.
  - Snapshot-level: `swh:1:snp:f37220e4468588dba8edf32ab89b1876ca289272`.

### v0.8.1

- **Zenodo DOI:** [10.5281/zenodo.20995032](https://doi.org/10.5281/zenodo.20995032) (new version under the same concept DOI 10.5281/zenodo.20113963).
- **License:** Apache-2.0.
- **Bundle contents:** SPEC.md (spec_version 0.6.3); VERSION; taxonomy.yaml; v0.8.1-known-limitations.md (living-doc snapshot at v0.8.1); CHANGELOG.md (full history through v0.8.1); LICENSE; README.md; full examples/ tree.
- **Bundle SHA-256:** `8cf75d37308281e1e5798598506e2cfa984fcff7dad17d734a81a5febad7bf08`
- **Maintainer note:** anchors the v0.8.1 package release (deterministic pointer-hook truncation in memory-index-gen; spec 0.6.3). Backward-compatible; regenerating an index only shortens over-cap pointer hooks.
- **Software Heritage SWHID:** minted 2026-06-28 via save-code-now (request 2376063; ingestion succeeded, visit_status=full).
  - Snapshot-level: `swh:1:snp:b70f08ef7f51e0e2c52848db99bca6f166baa312`.

### v0.9.0

- **Zenodo DOI:** [10.5281/zenodo.20999236](https://doi.org/10.5281/zenodo.20999236) (new version under the same concept DOI 10.5281/zenodo.20113963).
- **Minted:** 2026-06-28
- **Resource type:** Publication / Technical note
- **License:** Apache-2.0.
- **Bundle contents:** SPEC.md (spec_version 0.7.0); VERSION; taxonomy.yaml; v0.9.0-known-limitations.md (living-doc snapshot at v0.9.0); CHANGELOG.md (full history through v0.9.0); LICENSE; README.md; full examples/ tree.
- **Bundle SHA-256:** `8cbbb5ce9a63a04cddb9a967350a1d14195746fe0a0aef225a51c5b81f5e7280`
- **Maintainer note:** anchors the v0.9.0 package release (write-boundary hardening: the `memory-validate` write-gate operation plus integrity invariant 27; the `memory-audit` `.memforge/audit-waivers.yaml` mechanism; single-sourced MEMORY.md caps; folds the `memory-detect` hygiene orchestrator). spec/VERSION 0.7.0. Additive and backward-compatible; no new required frontmatter field and no existing well-formed folder breaks. Pre-tag cross-family release-rigor panel caught and fixed 1 critic BLOCKER (empty-fence over-strictness) plus 2 threat-modeler MAJORs (waiver-loader fail-closed + date-cutoff type-confusion), with a re-review confirming convergence.
- **Software Heritage SWHID:** minted 2026-06-28 via save-code-now (request submitted 2026-06-28T12:18Z; ingestion succeeded, visit_status=full).
  - Release-level (the v0.9.0 tag): `swh:1:rel:8d5f7e0417aaf2f3954f9c4c1f0746fdae2f8aba`.
  - Commit-level (the v0.9.0 tagged commit): `swh:1:rev:77cd8e56bc071f2fabec2f4755584522e9d440a5`.
  - Snapshot-level: `swh:1:snp:2e10f18a55af6293d124ae85daf7b9b962155349`.

## Channel 2 — Operator-brand timestamped disclosure

### v0.4.0

- **LinkedIn launch post:** 2026-05-07
- **ildan.ai blog post:** 2026-05-08 — https://ildan.ai/blog/memforge-typed-memory/

### v0.5.0

- *(planned; coordinated with the v0.5.0 ship narrative)*

## Channel 3 — GitHub releases as continuous prior art

| Version | Tag    | Date                | Release notes |
| ------- | ------ | ------------------- | ------------- |
| 0.3.0   | v0.3.0 | 2026-05-07 (approx) | Initial public release |
| 0.3.1   | v0.3.1 | 2026-05-08          | PyPI distribution rename + 15 console scripts |
| 0.4.0   | v0.4.0 | 2026-05-08          | Multi-agent concurrency + sensitivity enforcement |
| 0.4.1   | v0.4.1 | 2026-05-08          | Audit fix + adapter improvement |
| 0.4.2   | v0.4.2 | 2026-05-08          | Recursive frontmatter audit + action bumps |
| 0.4.3   | v0.4.3 | 2026-05-08          | Frontmatter backfill round-trip render fix |
| 0.5.0   | v0.5.0 | 2026-05-10          | Multi-identity + cryptographic attribution + WebSocket messaging adapter |
| 0.5.1   | v0.5.1 | 2026-05-10          | Reference CLI + agent session attestation content scope |
| 0.5.2   | v0.5.2 | 2026-05-10          | Canonical-form NFC normalization + atomic secure-write + bounded seen-nonce set + native Windows |
| 0.5.3   | v0.5.3 | 2026-05-10          | Registry-layer cool-down enforcement + bounded revocation walk + TOCTOU-safe read + SID-based Windows ACL denylist + framing-injection defense |
| 0.5.4   | v0.5.4 | 2026-05-11          | memory-audit subfolder-pointer false-positive fix (no spec change) |
| 0.5.5   | v0.5.5 | 2026-05-11          | Docs-only patch: WebSocket-vs-git decision framing + commit-hygiene section |
| 0.5.6   | v0.5.6 | 2026-05-11          | Docs + examples patch: cross-platform commit-msg hook + auto-commit watcher (bash + PowerShell) + WebSocket scaffold (config example + Python relay-probe) |
| 0.6.0   | v0.6.0 | 2026-06-07          | Minor spec bump: query-triggered recall (triggers/always/do_not_inject frontmatter + Recall operation spec contract); memory-recall reader + memory-index-gen --with-recall-index |
| 0.6.1   | v0.6.1 | 2026-06-07          | Docs + packaging patch: PyPI trove classifiers (pyversions badge fix); README Status / CLI-count / tool-table corrections; DOI switched to concept DOI (no spec change) |
| 0.7.0   | v0.7.0 | 2026-06-14          | Minor: memory-lint recall-readiness + token-cost quality CLI (20th console script) + security/correctness hardening (path-traversal containment, recall/lint/dedup RBAC, cryptographic-attribution trust root, broadened DLP); spec 0.6.1 lint section; signed tag + CycloneDX SBOM + pip-audit supply-chain gate |
| 0.8.0   | v0.8.0 | 2026-06-27          | Minor (package): wikilink rewriting in memory-link-rewriter rename/rename-batch (renames no longer orphan inbound [[wikilinks]]; alias-set false-rewrite guard; cross-root disambiguation; idempotent; every rewrite logged), pointer-line + MEMORY.md SHOULD caps raised 150 -> 180 for descriptive filename slugs, and a memory-audit advisory warning on non-spec tier values; spec 0.6.2; Zenodo deposit v0.8.0 (DOI 10.5281/zenodo.20975501; concept 10.5281/zenodo.20113963) + GitHub release tag + CycloneDX SBOM + signed tag. SWHID `swh:1:snp:e8b89d19b50b8a1defd23ce391439e6f19bb7b42` (save-code-now request 2375439, 2026-06-27, visit_status=full). |
| 0.8.1   | v0.8.1 | 2026-06-28          | Patch: deterministic pointer-hook truncation in memory-index-gen (generated MEMORY.md pointer lines now truncate the hook on a UTF-8 boundary to stay within the 180-byte cap; full description preserved in frontmatter + recall index, so lossless for recall; hook omitted when the title/path prefix leaves <=3 bytes); spec 0.6.3 (generator pointer-truncation rule, no new folder integrity invariant); reconciles the generator with the existing 180-byte audit check. Zenodo deposit v0.8.1 (DOI 10.5281/zenodo.20995032; concept 10.5281/zenodo.20113963) + GitHub release tag + CycloneDX SBOM + signed tag. SWHID `swh:1:snp:b70f08ef7f51e0e2c52848db99bca6f166baa312` (save-code-now request 2376063, 2026-06-28, visit_status=full). |
| 0.9.0   | v0.9.0 | 2026-06-28          | Minor (package): write-boundary hardening. New `memory-validate` write-gate operation (HARD-rejects frontmatter that does not parse as a YAML mapping, the unquoted-colon break, integrity invariant 27; SOFT caps/fields/enums; shares parser + caps with memory-audit; git pre-commit = universal wiring, CC PreToolUse = pre-write). New `memory-audit` `.memforge/audit-waivers.yaml` mechanism (explicit, reported, fail-closed allowlist that zeroes the immutable migration-era Tier 2 floor). Single-sourced MEMORY.md caps (audit + validate + index-gen). Folds the `memory-detect` hygiene orchestrator + audit convention-drift demotion. spec 0.7.0. Pre-tag cross-family panel caught 1 BLOCKER + 2 MAJORs, all fixed with regression tests (re-review converged). Zenodo deposit v0.9.0 (DOI 10.5281/zenodo.20999236; concept 10.5281/zenodo.20113963) + GitHub release tag + CycloneDX SBOM + signed tag. SWHID `swh:1:snp:2e10f18a55af6293d124ae85daf7b9b962155349` (save-code-now 2026-06-28, visit_status=full). |

The continuous-prior-art commitment: every spec-bumping commit is tagged with semver; every tag has a corresponding GitHub release; release notes name the substantive additions. Each commit is timestamped + indexed by GitHub + walkable by examiner prior-art search tools.

## Re-publication cadence

- Every **minor spec bump** (v0.3 → v0.4 → v0.5 → ...) gets a new Zenodo DOI as a new version of the existing concept-DOI.
- **Major architectural pivots** (a future ADR that changes core patterns) trigger a fresh Zenodo deposit bundle within 30 days of acceptance.
- **Patch releases.** As of v0.8.1, every tagged release including patch releases mints a new-version Zenodo DOI under the concept record (deposit-every-release). Earlier patch releases (v0.5.4 through v0.6.1) predate this practice and flowed through Channel 3 (GitHub release tag) plus Channel 1 Software Heritage only. All releases, patch or otherwise, get a GitHub release tag and a Software Heritage SWHID.

## Bundle improvements queued

The current minimal v0.5.0 bundle is sufficient to anchor a Zenodo DOI for prior-art purposes. Future deposits SHOULD enrich the bundle per the governance contract:

- Sanitized ADR-0001 (memory cluster discipline) — substantive content scrub for counsel/legal/patent topic references; folded into a future deposit.
- Generator + Audit Specification technical report — extracted from SPEC.md substance.
- Claim skeleton — explicit enumeration of patterns + prior-art citations (Dendron, Obsidian, adr-tools, MADR, Sphinx, Hugo, Jekyll, Eleventy, Foam, Logseq, Roam, Notion, Confluence, Yjs, Automerge).
- Software Heritage SWHID for the v0.5.0 GitHub repo snapshot.

The minimum-viable bundle anchors the DOI today; the heavier artifacts strengthen the deposit cumulatively in subsequent versions.
