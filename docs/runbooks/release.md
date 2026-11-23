# Release runbook

Publishing a MemForge release to PyPI and GitHub, by the project maintainer, once per release. Every command below that creates or pushes a tag, approves a deployment or changes a release is run by the maintainer only. Contributors and users never run them.

## What it does and does not do

- Does: tag a commit on `main` with a signed annotated tag, start `.github/workflows/release.yml`, approve the `pypi` environment, and check what was published.
- Does not: build or upload from a laptop. The sdist and wheel come only from the `build` job of `release.yml`, the upload only from its `publish` job by trusted publishing, and the architecture PDF only from its `pdf` job, built from the tag commit with the toolchain pinned in `docs/pdf/toolchain.conf`.
- Does: attest every file the GitHub Release attaches. The `attest` job signs one build provenance attestation over the sdist, the wheel, the SBOM and the PDF, and the release notes list the sha256 of each.
- Does not: let a tag push publish alone. The `test` job stops the run unless the tag is annotated, carries a signature GitHub verifies, and points at a commit on `main`.
- Does not: protect the tag after the run. The workflow checks the tag before the build and again before it creates the GitHub Release; a tag moved after that last check is not caught by the workflow. Keeping release tags from moving is a repository setting.
- Does not: replace a published version. PyPI refuses to overwrite a published file, so a fix ships as a new version.

## Prerequisites

- Write access to the GitHub repository and the right to approve the `pypi` environment.
- A signing key that git uses for tags (`gpg.format` and `user.signingkey` set) and that GitHub verifies for the maintainer's account.
- The PyPI project `ildan-memforge` has a trusted publisher for this repository, workflow `release.yml` and environment `pypi`.
- The release commit is on `main`, and CI (`ci.yml`) is green on it.
- `pyproject.toml` `version`, `memforge.__version__` and a `## [X.Y.Z]` heading in `CHANGELOG.md` all name the version being released.

## Steps

1. Check the version agrees in the three places, from a checkout of the release commit with the package installed in a virtual environment.

       python -c "from importlib.metadata import version; print(version('ildan-memforge'))"
       python -c "import memforge; print(memforge.__version__)"
       grep -n '^## \[X.Y.Z\]' CHANGELOG.md

   Good result: both commands print `X.Y.Z` and `grep` prints one line. The `test` job runs the same check and fails the run on a mismatch.

2. Maintainer only: create the signed annotated tag on the release commit.

       git tag -s vX.Y.Z -m "MemForge vX.Y.Z" <release-commit>

   Good result: `git cat-file -p vX.Y.Z` prints `object <release-commit>`, `type commit`, `tag vX.Y.Z` and a signature block. A lightweight tag (`git tag vX.Y.Z` with no `-s`) fails the run.

3. Maintainer only: push the tag, on its own.

       git push origin vX.Y.Z

   Good result: the push lists `* [new tag] vX.Y.Z -> vX.Y.Z`. The tag filter `v[0-9]*` starts `release.yml`.

4. Find the run and watch it.

       gh run list --workflow release.yml --limit 1
       gh run watch <run-id> --exit-status

   Good result: the `test` job prints `OK: vX.Y.Z is a verified signed tag on a commit that is on main` and `OK: version X.Y.Z consistent across metadata, __version__, and CHANGELOG`, the `build` job passes `Verify pyproject version matches tag`, and the `pdf` job prints `toolchain: pandoc <version>` and `toolchain: chrome <version>` with the versions in `docs/pdf/toolchain.conf`.

5. Maintainer only: approve the `publish` job when it waits on the `pypi` environment. Open the run (`gh run view <run-id> --web`) and use "Review deployments". The workflow names the environment; whether the job waits depends on the environment's protection rules in the repository settings.

   Good result: the `publish` job runs `pypa/gh-action-pypi-publish` and passes.

6. Check the GitHub Release.

       gh release view vX.Y.Z --json assets --jq '.assets[].name'
       gh release view vX.Y.Z --json body --jq .body

   Good result: the sdist, the wheel, `ildan-memforge-X.Y.Z.cdx.json` and `memforge-X.Y.Z-architecture.pdf`; the notes end with one `<sha256>  <file name>` line for each of the four.

7. Check the tag as the workflow saw it.

       gh api repos/ildan-ai/memforge/git/ref/tags/vX.Y.Z --jq '.object.type + " " + .object.sha'
       gh api repos/ildan-ai/memforge/git/tags/<tag-object-sha> --jq '.verification.verified'

   Good result: the first prints `tag <tag-object-sha>`; the second prints `true`.

8. Check the published package in a fresh virtual environment.

       python -m venv /tmp/mf-release-check
       /tmp/mf-release-check/bin/pip install ildan-memforge==X.Y.Z
       /tmp/mf-release-check/bin/memforge --version

   Good result: `memforge X.Y.Z`.

9. Check one attached file as a reader would, with the steps in "Verify a downloaded release file" below.

## Verify a downloaded release file

Anyone can run this, for the architecture PDF or any other file attached to a GitHub Release. It needs the GitHub CLI (`gh`), signed in to any GitHub account.

1. Download the file.

       gh release download vX.Y.Z --repo ildan-ai/memforge --pattern 'memforge-X.Y.Z-architecture.pdf'

2. Check its build provenance attestation.

       gh attestation verify memforge-X.Y.Z-architecture.pdf --repo ildan-ai/memforge --signer-workflow ildan-ai/memforge/.github/workflows/release.yml --source-ref refs/tags/vX.Y.Z

   Good result: `Verification succeeded!`, then the attestation's repository, workflow and ref. The attestation was signed by `release.yml` running on the tag, so the file was built by the workflow from the tagged sources and has not changed since.

3. Compare its sha256 with the release notes.

       sha256sum memforge-X.Y.Z-architecture.pdf
       gh release view vX.Y.Z --repo ildan-ai/memforge --json body --jq .body

   Good result: the hex value `sha256sum` prints (`shasum -a 256` on macOS) is the one on the file's line in the notes.

A failure at step 2 or 3 means the file is not the one the release workflow built. Do not use it; report it as a security issue (see `SECURITY.md`).

## Exit codes and outputs

| Output or exit code | Meaning | What to do |
|---|---|---|
| `tag vX.Y.Z points at <commit>, which is not on main` | The tagged commit never reached `main` | Delete the tag, merge through review, tag the merged commit |
| `tag vX.Y.Z is not an annotated tag (found commit)` | A lightweight tag was pushed | Delete it and tag again with `git tag -s` |
| `tag vX.Y.Z now points at ..., not the commit ... this run is building` | The tag moved after the push that started the run | Find out why the tag moved before any new run |
| `tag vX.Y.Z has no signature GitHub can verify` | The signing key is not registered or verified on GitHub | Register the key, delete the tag, tag again |
| `CHANGELOG.md has no '## [X.Y.Z]' heading for the shipping version` or `memforge.__version__ (...) != installed metadata (...)` | The version disagrees in the repository | Fix on `main` through review, then tag the new commit |
| `ERROR: tag 'X.Y.Z' does not match pyproject version '...'` | The tag name and `pyproject.toml` disagree | Delete the tag and tag the right version |
| `pytest` fails in the `test` job | The tag commit does not pass its tests | Fix on `main`; do not release |
| `publish` job waiting | The `pypi` environment needs approval | Step 5 |
| `tag vX.Y.Z now names ..., not the signed tag object ... the test job verified` | The tag was replaced while the build and publish ran; the upload already used the checked commit | Do not create the release by hand; investigate the tag change first |
| `build-pdf: <tool> <version> is not the pinned version <version>` in the `pdf` job | The runner installed a pandoc or Chrome other than the pin | Fix the pin or the install step on `main` through review, then tag a new version |
| `Error running filter .../architecture.lua` with `raw HTML is not allowed` (the message names the format of raw content in any language, `{=<format>}`, a TeX command such as `\newpage` included), `image outside diagrams/`, `image not found: <src> (a diagram is read only under its exact name, as a regular file)`, `the attribute <name> is not allowed` or `document metadata is not allowed` in the `pdf` job | The architecture docs at the tag carry raw content, an image, a diagram named under another spelling than its file name, an attribute or metadata the PDF build refuses, because each would let the printed PDF hold a local file or bytes fetched at build time | Find out how it reached `main`, remove it through review, then tag a new version |
| `build-pdf: not a regular file: <path>`, `build-pdf: not a regular folder: <path>` or `build-pdf: architecture.css may not contain ...` in the `pdf` job | A file or folder the PDF build reads is a link, any entry of `docs/architecture/diagrams/` (of any name or case, hidden ones included) is a link or not a regular file, or the stylesheet could load something into the page | Find out how it reached `main`, replace it with the real file or remove the rule through review, then tag a new version |
| `ARCHITECTURE_DIAGRAMS names no list of checked diagrams; build the PDF with build-pdf.sh` | `architecture.lua` was run by hand, not through `docs/pdf/build-pdf.sh`, which writes the list of checked diagrams the filter needs | Build with `docs/pdf/build-pdf.sh` |
| `gh attestation verify` fails, or a sha256 differs from the release notes | The file is not the one the release workflow built | Do not use the file; investigate before anyone else downloads it |
| `gh run watch --exit-status` exits non-zero | A job failed | `gh run view <run-id> --log-failed`, fix, and rerun only if the cause was not in the repository (`gh run rerun <run-id> --failed`) |

## Rollback

A published version cannot be replaced or re-uploaded. `skip-existing` makes a rerun of the same good build a no-op, not a fix. To withdraw a bad release, yank it on PyPI from the project's web settings (it stays installable by exact version) and publish a new patch version with the fix. The GitHub Release can be deleted with `gh release delete vX.Y.Z`; do not move or reuse the tag for different content.

## Residual risks

- Approval on the `pypi` environment and protection of release tags are repository settings that `release.yml` does not check. The maintainer owns them.
- The last tag check runs after the upload and guards only the GitHub Release page.
- The workflow trusts GitHub's tag signature verification and the action versions it pins by commit.
- The PDF's fonts and the system libraries Chrome needs come from the runner image and its package mirror, not from a pin. pandoc and Chrome are pinned; a rebuild on another machine can differ in layout, so a reader checks the attached file, not a rebuild.
