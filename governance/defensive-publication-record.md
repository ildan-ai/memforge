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

## Channel 2 — Operator-brand timestamped disclosure

### v0.4.0

- **LinkedIn launch post:** 2026-05-07
- **ildan.ai blog post:** 2026-05-08 — https://ildan.ai/blog/memforge-typed-memory/

### v0.5.0

- *(planned; coordinated with the v0.5.0 ship narrative)*

## Channel 3 — GitHub releases as continuous prior art

| Version | Tag | Date | Release notes |
|---|---|---|---|
| 0.3.0 | v0.3.0 | 2026-05-07 (approx) | Initial public release |
| 0.3.1 | v0.3.1 | 2026-05-08 | PyPI distribution rename + 15 console scripts |
| 0.4.0 | v0.4.0 | 2026-05-08 | Multi-agent concurrency + sensitivity enforcement |
| 0.4.1 | v0.4.1 | 2026-05-08 | Audit fix + adapter improvement |
| 0.4.2 | v0.4.2 | 2026-05-08 | Recursive frontmatter audit + action bumps |
| 0.4.3 | v0.4.3 | 2026-05-08 | Frontmatter backfill round-trip render fix |
| 0.5.0 | v0.5.0 | 2026-05-10 | Multi-identity + cryptographic attribution + WebSocket messaging adapter |

The continuous-prior-art commitment: every spec-bumping commit is tagged with semver; every tag has a corresponding GitHub release; release notes name the substantive additions. Each commit is timestamped + indexed by GitHub + walkable by examiner prior-art search tools.

## Re-publication cadence

- Every **minor spec bump** (v0.3 → v0.4 → v0.5 → ...) gets a new Zenodo DOI as a new version of the existing concept-DOI.
- **Major architectural pivots** (a future ADR that changes core patterns) trigger a fresh Zenodo deposit bundle within 30 days of acceptance.
- **Patch releases** (v0.4.1 / v0.4.2 / v0.5.0.1 / etc.) do NOT mint new Zenodo DOIs; they flow through Channel 3 (GitHub release tag) only.

## Bundle improvements queued

The current minimal v0.5.0 bundle is sufficient to anchor a Zenodo DOI for prior-art purposes. Future deposits SHOULD enrich the bundle per the governance contract:

- Sanitized ADR-0001 (memory cluster discipline) — substantive content scrub for counsel/legal/patent topic references; folded into a future deposit.
- Generator + Audit Specification technical report — extracted from SPEC.md substance.
- Claim skeleton — explicit enumeration of patterns + prior-art citations (Dendron, Obsidian, adr-tools, MADR, Sphinx, Hugo, Jekyll, Eleventy, Foam, Logseq, Roam, Notion, Confluence, Yjs, Automerge).
- Software Heritage SWHID for the v0.5.0 GitHub repo snapshot.

The minimum-viable bundle anchors the DOI today; the heavier artifacts strengthen the deposit cumulatively in subsequent versions.
