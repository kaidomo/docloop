# Releasing docloop

한국어: [releasing.ko.md](releasing.ko.md)

`VERSION` is the single source of truth for the docloop product release version. The
first release heading in `CHANGELOG.md` and a release tag are projections of that value.
The `TOOL_VERSION` constants used by individual subsystems are protocol compatibility
markers and are intentionally independent.

docloop currently supports stable Semantic Versioning values and tags only: `X.Y.Z` and
`vX.Y.Z`. Prereleases and build metadata are not accepted by the release validator.

## Prepare a release

1. Update `VERSION` to the intended stable version and update the first
   `CHANGELOG.md` heading to the same value. Curate the release notes there.
2. Run the local contract and regression checks:

   ```bash
   docloop --version
   python3 tools/check_release.py
   python3 tests/test_release.py
   python3 tests/run_tests.py
   ```

3. Merge the reviewed change to `main`. Do not tag an unmerged branch. Confirm the exact
   merged commit and verify that CI passed before creating a tag.

## Create the release tag

From an up-to-date `main`, create an annotated tag whose name exactly matches `v` plus
the contents of `VERSION`. Signing the annotated tag is recommended when the maintainer
has a configured signing identity:

```bash
git switch main
git pull --ff-only origin main
version="$(cat VERSION)"
git tag -s "v${version}" -m "docloop v${version}"   # recommended when signing is configured
# Or, when signing is unavailable:
# git tag -a "v${version}" -m "docloop v${version}"
git show "v${version}"
git push origin "refs/tags/v${version}"
```

Inspect the tag and its signature status manually before pushing. The automated release
contract requires an annotated tag and reports signature verification status, but it
does not claim cryptographic trust. Enforcing trust requires a separately reviewed list
of trusted signer identities or keys and a verification mechanism.

The tag-push workflow checks out the existing tag with full history, runs the complete
test suite, verifies VERSION/CHANGELOG/tag equality, confirms that the tag object is
annotated and peels to a commit reachable from `origin/main`, and only then creates a
GitHub Release. The workflow never creates, moves, or replaces a tag. Generated GitHub
notes supplement the curated CHANGELOG; no package, duplicate source archive, or
checksum asset is published.

## Failure recovery

- If verification fails, fix the source on `main` and prepare a new patch version. Never
  force-move or replace the failed release tag.
- If publication fails after verification, first check whether a GitHub Release already
  exists for the tag. Rerun the failed job only when it is safe to do so; do not move the
  tag as recovery.
- If released content is wrong, correct it in a new version. Treat published tags as
  immutable.

Repository administrators should add a ruleset protecting `v*` tags from updates and
deletion, and enable GitHub Release immutability where available. The Actions setting
must allow the repository `GITHUB_TOKEN` to create Releases; the workflow grants
`contents: write` only to the publication job after verification succeeds.

### Recovering from a leftover draft Release

If an error occurs while the release workflow is running, a draft Release can be left
behind. A subsequent re-run verifies that the existing Release is in the published (not
draft) state, so a leftover draft causes a fail-closed result even if its content
matches the intended values. Recovery procedure:

1. Check whether a draft Release exists for the tag in question.
2. Check whether the draft's tag/name/body match the intended values (for the record
   only — this does not change whether it gets deleted).
3. If an existing draft is present, always delete it regardless of whether it matches.
4. After deleting it, re-dispatch the workflow.

Not automatically publishing a draft without verification is this workflow's
fail-closed policy.

## Trusted signer registration is a dispatch precondition

`.github/release_allowed_signers` must contain one or more of the maintainer's real
production SSH public keys before the release workflow can be dispatched
meaningfully; additional signing keys may be listed one per line. Without it,
`tools/check_release.py` (invoked from the `verify` job's "Validate tag and changelog
notes" step) raises `ReleaseError` and hard-fails the run before any release evidence is
frozen — the workflow does not proceed to publish with an unregistered signer.

Separately from that front gate, the `verify` job's evidence step and the `publish`
job's `EXPECTED_*` comparison independently compute `signer_sha`, `notes_sha`, and
`workflow_sha` via `sha256sum` command substitution. Local reproduction (isolated clone,
`.github/release_allowed_signers` absent) confirmed that, before this hardening, a failed
`sha256sum` inside `$(...)` does **not** abort the step even under `set -euo pipefail` —
it collapses to an empty string, which a later bare `test "" = ""` comparison then passes
unconditionally. This was a latent fail-open in the evidence-freezing and expectation-
comparison logic. It was not exploitable in practice only because the earlier
`tools/check_release.py` signer check already hard-fails the run first — so the observed
behavior end-to-end was "fail-closed by an upstream gate, with a fail-open bug sitting
unused behind it." This PR closes the latent bug structurally: each of `signer_sha`,
`notes_sha`, and `workflow_sha` (and the evidence step's `tag_object`/`tag_commit`) is now
assigned to a local variable, checked with `test -n` before use, and only then written to
`$GITHUB_OUTPUT` or compared against its `EXPECTED_*` counterpart — so a missing or
unreadable input now hard-fails at the point of computation instead of silently freezing
an empty expectation.
