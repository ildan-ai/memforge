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

### v0.12.0

- **Zenodo DOI:** *(deposit staged; DOI recorded on publish, as a new version under concept DOI 10.5281/zenodo.20113963)*.
- **Minted:** *(pending publish)*
- **Resource type:** Publication / Technical note
- **License:** Apache-2.0.
- **Bundle contents:** SPEC.md (spec_version 0.8.0, unchanged from v0.11.0); VERSION; taxonomy.yaml; known-limitations.md (living-doc snapshot at v0.12.0); CHANGELOG.md (full history through v0.12.0); LICENSE; README.md; full examples/ tree. 17 files.
- **Bundle SHA-256:** `a15de8ccff4533b3e4ceeb8d81e622cc484dd11b356f56c4981d34e8658b5a4c`
- **Bundle MD5 (to verify against Zenodo's computed checksum post-upload):** `2bafbd651145ff9f3c06cf78ab1ace1d`
- **PyPI artifacts:** wheel sha256 `6b49eed06e377e2bca4f0e4143849646ffa26a277b739ff7d784b635607c88ba`; sdist sha256 `92f7e183419b581d16e4f286706596883821d67e6ab9ffe70982a180175bae3d`.
- **Maintainer note:** anchors the v0.12.0 release, two new audit checks for defect classes that had accumulated silently in an 842-memory two-root corpus. Both share a shape: a real defect every existing check passed over, so it grew unopposed until something looked for it directly. `memory-audit` now reports a memory carrying no `topic:` tag; `tags` is load-bearing twice, since recall folds it into the trigger set alongside `name` and `description` and `memory-index-gen` derives the `MEMORY.md` topic section from it, so an untagged memory is both harder to surface and lands in a flat `(no topic)` dump. Nothing detected it because `memory-frontmatter-backfill` infers a topic only from a named subfolder or a filename matching its inline `KNOWN_TOPICS` set, so a top-level file with an unrecognized name gets nothing, silently: 59 files had accumulated that way, putting 46 of 87 pointers in one root into `(no topic)`. `memory-audit` also now reports top-level frontmatter that disagrees with a nested `metadata:` block, naming each diverging field and both values. Some agent harnesses write their own canonical shape, nesting the prior frontmatter under `metadata:` and synthesizing fresh top-level fields; nothing is lost, but every tool reads only the top level, so the nested copy becomes invisible truth and the two drift apart. Measured: 460 of 842 files carried the nested block and 74 of those had at least one identity or lifecycle field disagreeing, while `--strict` exited 0 on all of it. Both are HEALTH warnings rather than integrity violations, because the files are well-formed and a corpus that adopted a harness shape can carry many divergences through no fault of its own; a check that fails `--strict` on that becomes a flag operators switch off, and a disabled check protects nothing. Neither check repairs what it finds: choosing a topic is a semantic call and a wrong topic is worse than none, and reconciling a divergence means deciding which level is authoritative, which is the operator's call. Fire rates were measured against the real corpus before shipping rather than assumed (missing-topic 15 of 842, 1.8%; divergence 74 of 842, 8.8%), applying the standard v0.11.0 set when it disabled a warning that fired on 101 of 101 files. No spec change; `spec/VERSION` stays 0.8.0 and every conformant folder stays conformant. 17 new tests across two new modules, each verified to FAIL on the unfixed code; 627 pass.
- **Deposit timing:** the package shipped to PyPI on 2026-08-05 ahead of this archival deposit, which was staged immediately afterward on the same day rather than before the tag. The deposit is recorded here as contemporaneous with the release, and the ordering is noted rather than smoothed over.
- **Software Heritage SWHID:** save-code-now request 2410807, submitted 2026-08-06T00:23Z, `visit_status=full`. The crawl ran AFTER the v0.12.0 tag push, so the snapshot contains the tag. All three identifiers were confirmed to resolve (HTTP 200 against the archive resolve API), so they are archived fact rather than prediction.
  - Release-level (the v0.12.0 tag): `swh:1:rel:58938f273287e80e7151b2f76475202e79fd8320`.
  - Commit-level (the v0.12.0 tagged commit): `swh:1:rev:5635ee46a621e1e8c977a5628bce2873dd32f88a`.
  - Snapshot-level: `swh:1:snp:71b599a917d6389d3d1a8db469851fb2ffb56ec4`.

### v0.11.0

- **Zenodo DOI:** [10.5281/zenodo.21812200](https://doi.org/10.5281/zenodo.21812200) (new version under the same concept DOI 10.5281/zenodo.20113963).
- **Minted:** 2026-08-05
- **Resource type:** Publication / Technical note
- **License:** Apache-2.0.
- **Bundle contents:** SPEC.md (spec_version 0.8.0, unchanged from v0.10.0); VERSION; taxonomy.yaml; known-limitations.md (living-doc snapshot at v0.11.0); CHANGELOG.md (full history through v0.11.0); LICENSE; README.md; full examples/ tree.
- **Bundle SHA-256:** `61e757aa5743811ab9b27fd0cda82990204e663bba9426f91a3c56111d4648ca`
- **Bundle MD5 (verified against Zenodo's computed checksum post-upload):** `3c02e9ae71bd22ba7d9f09390a64aadb`
- **PyPI artifacts:** wheel sha256 `30451faba6386d5e91b4e0a996c18efaf08c169afdbe5551fe6d94f1b99448f5`; sdist sha256 `a5f582371ae216309b5d16f875683eea5885eb46040a5e2740bd590fbb9e3856`.
- **Maintainer note:** anchors the v0.11.0 release, two signal-quality fixes to the diagnostic surface arising from a full audit of an 834-memory two-root corpus. Neither is a correctness fix; both target one failure mode, a diagnostic that fires so often or so silently that the operator stops reading it. `memory-dedup --description-warn-threshold` now defaults to 0 (disabled) because the prior default of 50 flagged 101 of 101 top-level files, and `description` is the authoritative recall text that is SUPPOSED to be descriptive; the noise landed on the same stream that carries the cloud-egress and sensitivity warnings. The consumer guards on a positive threshold explicitly, since an unguarded comparison against zero fires on every non-empty description, the exact inverse of disabling it. `memory-audit` now reports a hook-less `MEMORY.md` pointer, the case where the title+path prefix exhausts the byte cap and `index_gen` omits the hook: it always degraded gracefully there, but silently, so the one index line with no relevance cue was also the one line nothing reported. Hard-failing the generator was considered and REJECTED, on zero observed occurrences and because it would trade a cosmetic loss for an availability one. The larger proposal that motivated the work, an authored `hook` field distinct from `description`, was reviewed by a three-voice cross-family panel plus a judge loop and NOT adopted: derived truncation cannot drift between two fields by construction. The judge withdrew several of its own earlier findings once the shipped spec was read rather than assumed, including a unanimous BLOCKER resting on a false premise about a spec-versus-tooling cap divergence that did not exist; the orchestrator had briefed the panel from derived operator docs instead of the spec. A reopen trigger is on record: a measured index-navigation failure attributed to truncation would reopen it, with an optional non-authoritative display-hint override as the pre-ruled remedy. 5 new tests, each verified to FAIL on the unfixed code; 610 pass.
- **Software Heritage SWHID:** save-code-now requests 2410413 and 2410414, both 2026-08-05, `visit_status=full`. The first was triggered early per the checklist but ran BEFORE the tag push, so its snapshot could not contain v0.11.0; the second was re-triggered after the tag and is the one recorded here.
  - Release-level (the v0.11.0 tag): `swh:1:rel:ddcfab16b9c2117181919bc817e70dd83803d98b`.
  - Commit-level (the v0.11.0 tagged commit): `swh:1:rev:c30461dbf298b06ee9c6dd23e5cee8fd39ec3a38`.
  - Snapshot-level: `swh:1:snp:9b9afb18aff6819c75f3c2a8315ce0599b70b8ae`.

### v0.10.0

- **Zenodo DOI:** [10.5281/zenodo.21762072](https://doi.org/10.5281/zenodo.21762072) (new version under the same concept DOI 10.5281/zenodo.20113963).
- **Minted:** 2026-08-02
- **Resource type:** Publication / Technical note
- **License:** Apache-2.0.
- **Bundle contents:** SPEC.md (spec_version 0.8.0, unchanged from v0.9.2); VERSION; taxonomy.yaml; v0.10.0-known-limitations.md (living-doc snapshot at v0.10.0); CHANGELOG.md (full history through v0.10.0); LICENSE; README.md; full examples/ tree.
- **Bundle SHA-256:** `85048574294c81245e4720ff44bd54328619d1e057919ba9bd312f5e1d659f87`
- **Bundle MD5 (verified against Zenodo's computed checksum post-upload):** `87b3c3a893f5d26f2248d79c988d2774`
- **Maintainer note:** anchors the v0.10.0 release, which supplies the pre-write half of the write-boundary gate that v0.9.0 described but did not deliver. v0.9.0 added `memory-validate` and named a Claude Code `PreToolUse` shim as the pre-write half; the shim was never written, and the check it would have called was parse-only, so that half did not exist in either sense. The gap between those two facts is specific and was load-bearing: a frontmatter block can be entirely valid YAML while missing a field no tool can derive, so a parse gate cannot see it, a backfill cannot repair it (the field is a semantic classification and guessing one is worse than absence), and the only component that detects it is the audit, which runs after the fact. Files in that state accumulate with nothing objecting; 96 had done so on the maintainer's own store before an audit surfaced the class. New `validate_required_fields` primitive, deliberately SEPARATE from `validate_frontmatter` because that function's docstring commits to parse-only semantics that adapters and the git pre-commit path depend on, so a strict mode would have been a breaking change wearing the costume of a flag. Two field sets ship; the default is the non-derivable three, because `uid`, `tier`, `tags`, `owner`, `status` and `created` are synthesized later by the backfill and a gate denying on them would reject the first save of every memory. The shipped `PreToolUse` gate validates the RECONSTRUCTED file on an `Edit` rather than the replacement string alone, so a removal is caught, and is fail-open on every error path because a gate in front of every editor write must never wedge the editor. `memory-frontmatter-backfill` now hoists an existing nested `metadata.type` to the required top-level key, losslessly, while still refusing to invent one. spec/VERSION unchanged at 0.8.0: the field has been required since v0.4.0, so this makes the tooling enforce and repair what the spec already stated. Pre-ship threat-model pass fixed 2 findings in-commit (an environment override that disarmed the gate silently now announces itself; an unbounded read is size-capped) and REFUTED 1 with evidence rather than accepting it (path traversal and symlink escape do not apply, because the scope check resolves each side before comparing; confirmed by probe, and the reviewer had raised it only because the bundle it received was truncated). The test suite independently caught a defect the author did not: a new helper shadowed an existing one of the same name and broke three passing tests. 605 tests pass, 26 new.
- **Software Heritage SWHID:** save-code-now request 2408111 submitted 2026-08-02; release and revision IDs are deterministic and recorded now, snapshot pending crawl completion.
  - Release-level (the v0.10.0 tag): `swh:1:rel:f5bba489d2473fb21aee3030b03fc5f45962a5ab`.
  - Commit-level (the v0.10.0 tagged commit): `swh:1:rev:2ca7df16b3e424cedf61d8a5ffcbdf36779a5c28`.
  - Snapshot-level: pending crawl.

### v0.9.2

- **Zenodo DOI:** [10.5281/zenodo.21700690](https://doi.org/10.5281/zenodo.21700690) (new version under the same concept DOI 10.5281/zenodo.20113963).
- **Minted:** 2026-07-30
- **Resource type:** Publication / Technical note
- **License:** Apache-2.0.
- **Bundle contents:** SPEC.md (spec_version 0.8.0, MINOR bump from 0.7.0); VERSION; taxonomy.yaml; v0.9.2-known-limitations.md (living-doc snapshot at v0.9.2); CHANGELOG.md (full history through v0.9.2); LICENSE; README.md; full examples/ tree.
- **Bundle SHA-256:** `2453ad974e7c53dc03b697f93c55bda27793669f5b45ec862c66ae810f1b914d`
- **Bundle MD5 (verified against Zenodo's computed checksum post-upload):** `b90a2afeebe91ce15bb155e9a62e7bd4`
- **Maintainer note:** anchors the v0.9.2 release, which closes the SECOND class of the silent memory loss v0.9.1 began on, and carries the spec change that makes the new check normative. v0.9.1 made a rollup subfolder with no `README.md` a violation; that check cannot see a subfolder whose README EXISTS but omits a sibling. Invariants 7 and 8 govern `MEMORY.md`, and by the Rollup-subfolders section a detail file is deliberately absent from it, so the rollup README is a detail file's only path to a reader. Three new integrity violations: incomplete (unreachable siblings), dangling pointer (target exists nowhere), and unreadable README (fail-closed). Conformance is a SET comparison in both directions, never a count comparison, because a duplicate pointer alongside an omission yields equal counts while a memory is still lost. spec/VERSION 0.7.0 -> 0.8.0, naming integrity invariant 28; MINOR rather than patch because a folder well-formed under 0.7.x can be non-conformant under 0.8.0. The pointer set is CLOSED to wikilink and markdown-inline-link forms and read from prose only (fenced blocks, code spans, HTML comments excluded), so the check is deterministic across adapters. BREAKING for `memory-audit --strict` on a store whose rollup README omits a sibling. The release gate did substantive work: the audit check was already reviewed and merged when the checklist found it raised an integrity violation with NO spec invariant behind it, which would have failed spec-conformant folders; the architect voice caught the bump misfiled as a patch and the pointer grammar left non-deterministic; the critic caught links in fenced blocks and HTML comments being unspecified; the threat-model pass caught a fail-open BLOCKER where an unreadable README was silently skipped, suppressing every finding for that folder. Two panel findings were REFUTED with evidence rather than accepted (a larger-bump claim resting on a false premise about the prior spec, and a NUL-byte crash that was verified not to occur), and the dead guard written against the second was removed rather than kept.
- **Software Heritage SWHID:** minted 2026-07-30 via save-code-now (request 2403490, submitted 2026-07-30T05:53Z; ingestion succeeded, visit_status=full). All three resolve (HTTP 200 against the archive resolve API), so the release and revision identifiers quoted in the Zenodo description are archived fact rather than a prediction.
  - Release-level (the v0.9.2 tag): `swh:1:rel:cd00fb306481cf6e6cc52b0446b21131185b5ab6`.
  - Commit-level (the v0.9.2 tagged commit): `swh:1:rev:0514f6cb05b914b584442e919f010222be0a4cc7`.
  - Snapshot-level: `swh:1:snp:da456af9fee39ffdb77bd5c14d8564c0f6aa8207`.

### v0.9.1

- **Zenodo DOI:** [10.5281/zenodo.21693847](https://doi.org/10.5281/zenodo.21693847) (new version under the same concept DOI 10.5281/zenodo.20113963).
- **Minted:** 2026-07-29
- **Resource type:** Publication / Technical note
- **License:** Apache-2.0.
- **Bundle contents:** SPEC.md (spec_version 0.7.0, unchanged); VERSION; taxonomy.yaml; v0.9.1-known-limitations.md (living-doc snapshot at v0.9.1); CHANGELOG.md (full history through v0.9.1); LICENSE; README.md; full examples/ tree.
- **Bundle SHA-256:** `85b72b5162d7f818c2981fe0dbef4c3abc6da0555fdd685b157ae3f02f9f0e35`
- **Maintainer note:** anchors the v0.9.1 patch release, which closes a class of SILENT memory loss in the tooling. `memory-audit` now raises an integrity violation for a rollup subfolder holding files with no `README.md` parent: the pointer-comparable set only included `<topic>/README.md` when that README existed, so a parentless subfolder contributed nothing to the pointer-versus-disk comparison and every file in it was unreachable from MEMORY.md while the audit reported clean. BREAKING for `memory-audit --strict` on any store already containing one. `memory-index-gen` now warns when about to ship a lossy index, including on the `--check` OK path. `memory-frontmatter-backfill` no longer fabricates an `access` label contradicting an operator-set `sensitivity` (not a live exposure -- absent and `internal` are equivalent in the access gate and sensitivity is enforced separately -- but it would become one under a fail-closed no-label default). spec/VERSION unchanged at 0.7.0. Pre-tag cross-family panel caught 2 real defects, both fixed with regression tests: a dot-directory exclusion gap that would have raised a violation for `.git/`, and a missing symlink guard that would have let the new walk enumerate an arbitrary tree. A pre-existing test also caught an over-broad first version of the new check.
- **Software Heritage SWHID:** minted 2026-07-29 via save-code-now (request submitted 2026-07-29T22:47Z; ingestion succeeded).
  - Release-level (the v0.9.1 tag): `swh:1:rel:f217c93389528fc347e84a5b175d27a914e282bd`.
  - Commit-level (the v0.9.1 tagged commit): `swh:1:rev:700f6c22c18e01e9092fceb19942aff715588794`.
  - Snapshot-level: `swh:1:snp:8a8bf2642cc2c2181606fb05f3c1514ba0406833`.

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

| 0.9.1   | v0.9.1 | 2026-07-29          | Patch (package): closes a class of SILENT memory loss. `memory-audit` now raises an integrity violation for a rollup subfolder that holds files but has no `README.md` parent -- the pointer-comparable set only added `<topic>/README.md` when it existed, so a parentless subfolder contributed nothing to the pointer-versus-disk comparison and every file in it was unreachable from MEMORY.md while the audit reported clean (BREAKING for `--strict` on stores already containing one; remediation is to author the rollup README, not to widen MEMORY.md). `memory-index-gen` now warns when about to ship a lossy index, including on the `--check` OK path, since OK means byte-identical-to-disk and never all-memories-reachable. `memory-frontmatter-backfill` no longer fabricates an `access` label contradicting an operator-set `sensitivity` (not a live exposure; would become one under a fail-closed no-label default). spec/VERSION unchanged at 0.7.0. Pre-tag cross-family panel caught 2 real defects (dot-directory exclusion gap that would have flagged `.git/`; missing symlink guard permitting arbitrary-tree enumeration), both fixed with regression tests; 568 tests pass, 12 new. Zenodo deposit v0.9.1 (DOI 10.5281/zenodo.21693847; concept 10.5281/zenodo.20113963) + GitHub release tag + CycloneDX SBOM. SWHID `swh:1:snp:8a8bf2642cc2c2181606fb05f3c1514ba0406833` (save-code-now 2026-07-29, ingestion succeeded). |
| 0.9.2   | v0.9.2 | 2026-07-30          | Minor (spec 0.7.0 -> 0.8.0), patch (package): closes the SECOND class of silent memory loss. `memory-audit` now detects an INCOMPLETE rollup `README.md`, not just a missing one: a README that EXISTS but omits a sibling leaves that memory exactly as unreachable, and v0.9.1's check could not see it. Three new integrity violations (incomplete, dangling pointer, unreadable README which fails closed). Set-based comparison in both directions, never count-based, because a duplicate pointer alongside an omission yields equal counts while a memory is still lost. New integrity invariant 28; pointer set CLOSED to wikilink + markdown-inline-link and read from prose only (fenced blocks, code spans, HTML comments excluded) so the check is deterministic across adapters. MINOR spec bump because a folder well-formed under 0.7.x can be non-conformant under 0.8.0. BREAKING for `--strict` on a store whose rollup README omits a sibling. Pre-ship review closed 5 findings in-commit (including a fail-open BLOCKER on an unreadable README, and a spec divergence where the check had NO invariant behind it) and refuted 2 with evidence. 12 new tests, 580 pass. Zenodo deposit v0.9.2 (DOI 10.5281/zenodo.21700690; concept 10.5281/zenodo.20113963) + signed GitHub release tag (GitHub-verified). SWHID `swh:1:snp:da456af9fee39ffdb77bd5c14d8564c0f6aa8207` (save-code-now request 2403490, 2026-07-30, visit_status=full). |
| 0.10.0  | v0.10.0 | 2026-08-02          | Minor (package), spec unchanged at 0.8.0: ships the pre-write half of the write-boundary gate v0.9.0 described but never delivered. v0.9.0 named a Claude Code `PreToolUse` shim as that half; it was never written, and the check it would have called was parse-only, so the half did not exist in either sense. The gap is specific: a frontmatter block can be entirely valid YAML while missing a field no tool can derive, so a parse gate cannot see it, a backfill cannot repair it (semantic classification; guessing is worse than absence), and only the after-the-fact audit detects it. 96 files had accumulated in that state on the maintainer's store before an audit surfaced the class. New `validate_required_fields` primitive kept SEPARATE from `validate_frontmatter`, whose docstring commits to parse-only semantics that adapters and the git pre-commit path depend on. Default field set is the non-derivable three, because the other six are synthesized later by backfill and denying on them would reject the first save of every memory. The shipped gate validates the RECONSTRUCTED file on an `Edit` so a removal is caught, and is fail-open on every error path. `memory-frontmatter-backfill` hoists an existing nested `metadata.type` to top level, losslessly, while still refusing to invent one. Threat-model pass fixed 2 findings in-commit (silent env override now announces itself; unbounded read size-capped) and refuted 1 with evidence (traversal/symlink escape do not apply; scope check resolves both sides, confirmed by probe). Test suite caught a shadowed-helper defect the author missed. 26 new tests, 605 pass. Zenodo deposit v0.10.0 (DOI 10.5281/zenodo.21762072; concept 10.5281/zenodo.20113963) + signed GitHub release tag (GitHub-verified). SWHID `swh:1:rel:f5bba489d2473fb21aee3030b03fc5f45962a5ab` / `swh:1:rev:2ca7df16b3e424cedf61d8a5ffcbdf36779a5c28` (save-code-now request 2408111, 2026-08-02; snapshot pending crawl). |
| 0.11.0  | v0.11.0 | 2026-08-05          | Minor (package), spec unchanged at 0.8.0: two signal-quality fixes to the diagnostic surface, both from a full audit of an 834-memory two-root corpus. Neither is a correctness fix; both target one failure mode, a diagnostic that fires so often or so silently that the operator stops reading it. `memory-dedup --description-warn-threshold` now defaults to `0` (disabled): the prior default of `50` flagged 101 of 101 top-level files, and `description` is the authoritative recall text that is SUPPOSED to be descriptive, so the check was pure noise on the same stderr stream that carries the cloud-egress and sensitivity warnings. The consumer guards on `warn_threshold > 0` explicitly, because a bare `len(desc) > 0` fires on every non-empty description, the exact inverse of disabling it. `memory-audit` now emits a HEALTH warning for a `MEMORY.md` pointer carrying no hook text because the title+path prefix exhausted `POINTER_LINE_BYTE_CAP`: `memory-index-gen` always degraded gracefully there but did so silently, so the one index line with no relevance cue was also the one line nothing reported. Hard-failing the generator was considered and REJECTED (zero occurrences in the motivating corpus; would trade a cosmetic loss for an availability one, since one over-long title would stop the index regenerating). A three-voice cross-family panel plus a Fable 5 judge loop considered a larger change, an authored `hook` frontmatter field distinct from `description`, and converged on NOT doing it: derived truncation cannot drift between two fields by construction, and the migration was 196 files rather than the 149 first costed. The judge withdrew several of its own prior-round findings once the shipped spec was read rather than assumed, including a unanimous BLOCKER that rested on a false premise about a spec-versus-tooling cap divergence that did not exist. 5 new tests, each verified to FAIL on the unfixed code; 610 pass. Zenodo deposit v0.11.0 (DOI 10.5281/zenodo.21812200; concept 10.5281/zenodo.20113963) + signed GitHub release tag (GitHub-verified). SWHID `swh:1:rel:ddcfab16b9c2117181919bc817e70dd83803d98b` / `swh:1:rev:c30461dbf298b06ee9c6dd23e5cee8fd39ec3a38` / `swh:1:snp:9b9afb18aff6819c75f3c2a8315ce0599b70b8ae` (save-code-now requests 2410413 + 2410414, 2026-08-05; the second was re-triggered AFTER the tag push so the snapshot actually contains v0.11.0, visit_status=full). |
| 0.12.0  | v0.12.0 | 2026-08-05          | Minor (package), spec unchanged at 0.8.0: two new audit checks for defect classes that had accumulated silently in an 842-memory two-root corpus. Both share a shape, a real defect every existing check passed over, so it grew unopposed until something looked for it directly. `memory-audit` now reports a memory carrying no `topic:` tag: `tags` is load-bearing twice, since recall folds it into the trigger set alongside `name` and `description` and `memory-index-gen` derives the `MEMORY.md` topic section from it, so an untagged memory is both harder for recall to surface and lands in a flat `(no topic)` dump. Nothing detected it because `memory-frontmatter-backfill` infers a topic only from a named subfolder or a filename matching its inline `KNOWN_TOPICS` set, so a top-level file with an unrecognized name gets nothing, silently; 59 files had accumulated that way, putting 46 of 87 pointers in one root into `(no topic)`. `memory-audit` also now reports top-level frontmatter that disagrees with a nested `metadata:` block, naming each diverging field and both values: some agent harnesses write their own canonical shape, nesting the prior frontmatter under `metadata:` and synthesizing fresh top-level fields, and while nothing is lost, every tool reads only the top level, so the nested copy becomes invisible truth and the two drift apart. Measured, 460 of 842 files carried the nested block and 74 of those had at least one identity or lifecycle field disagreeing, while `--strict` exited 0 on all of it. Both are HEALTH warnings rather than integrity violations: the files are well-formed, and a corpus that adopted a harness shape can carry many divergences through no fault of its own, so a check that fails `--strict` on that becomes a flag operators switch off, and a disabled check protects nothing. Neither check repairs what it finds, because choosing a topic is a semantic call and a wrong topic is worse than none, and reconciling a divergence means deciding which level is authoritative, which is the operator's call. Fire rates were measured against the real corpus before shipping rather than assumed (missing-topic 15 of 842, 1.8%; divergence 74 of 842, 8.8%). No spec change; `spec/VERSION` stays 0.8.0 and every conformant folder stays conformant. 17 new tests across two new modules, each verified to FAIL on the unfixed code; 627 pass. Signed GitHub release tag (GitHub-verified); Zenodo deposit staged for publish under concept DOI 10.5281/zenodo.20113963. SWHID `swh:1:rel:58938f273287e80e7151b2f76475202e79fd8320` / `swh:1:rev:5635ee46a621e1e8c977a5628bce2873dd32f88a` / `swh:1:snp:71b599a917d6389d3d1a8db469851fb2ffb56ec4` (save-code-now request 2410807, 2026-08-06, triggered AFTER the tag push so the snapshot contains v0.12.0, visit_status=full, all three confirmed to resolve). |

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
