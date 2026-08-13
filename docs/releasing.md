# Releasing docloop

`VERSION` is the single source of truth for the docloop product release version. The
first release heading in `CHANGELOG.md` and a release tag are projections of that value.
The `TOOL_VERSION` constants used by individual subsystems are protocol compatibility
markers and are intentionally independent.

docloop currently supports stable Semantic Versioning values and tags only: `X.Y.Z` and
`vX.Y.Z`. Prereleases and build metadata are not accepted by the release validator.

## Prepare a release

1. Update `VERSION` to the intended stable version and update the first
   `CHANGELOG.md` heading to the same value. Curate the release notes there.
2. Install the release-check dependency and run the local contract and regression checks:

   ```bash
   python3 -m pip install -r requirements.txt
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
git tag -s "v${version}" -m "docloop v${version}"
git show "v${version}"
git push origin "refs/tags/v${version}"
```

The repository must carry `.github/release_allowed_signers` with the reviewed production
SSH public signer entry before publication. Signed annotated tags are mandatory; do not
use an unsigned fallback. Do not create placeholder or disposable production signer
bytes. If the file is absent, release validation stops closed.

The dispatch workflow checks out the existing tag with full history, runs the complete
test suite, verifies VERSION/CHANGELOG/tag equality, confirms that the tag object is
annotated, trusted by the repository-local signer allowlist, and peels to a commit
exactly equal to the live `origin/main` tip. An existing Release is accepted only when its tag, title,
draft/prerelease flags, and body exactly match the dated CHANGELOG section. Missing,
partial, conflicting, or API-error states fail closed. Tag pushes do not publish
Releases; no generated notes or release assets are published.

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
