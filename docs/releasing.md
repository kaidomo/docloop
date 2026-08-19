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

### 잔류 draft Release 복구 절차

release 워크플로 실행 중 오류가 나면 draft Release가 남을 수 있습니다. 이후 재실행은 기존 Release가 draft가 아닌 published 상태와 일치하는지 검증하므로, 남은 draft는 내용이 의도한 값과 일치하더라도 fail-closed 됩니다. 복구 절차:

1. 해당 tag의 draft Release 존재 여부를 확인합니다.
2. draft의 tag/name/body가 의도한 값과 일치하는지 확인합니다(기록용 — 삭제 여부를 바꾸지 않습니다).
3. 기존 draft가 있으면 일치 여부와 무관하게 항상 삭제합니다.
4. 삭제 후 workflow를 재dispatch합니다.

draft를 검증 없이 자동으로 publish하지 않는 것이 이 워크플로의 fail-closed 정책입니다.

## Trusted signer registration is a dispatch precondition

`.github/release_allowed_signers` must contain the maintainer's real production SSH
public key before the release workflow can be dispatched meaningfully. Without it,
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
