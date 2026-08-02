# PORTS — per-file upstream provenance (hardening plan D3)

**Canonical upstream ref (pinned, moving)**: `docuauthring` repository, branch `main`
— resolved at run time by `tools/check_ports.py` (maintainer-only tool; requires a
local checkout at `$DOCUAUTHRING_ROOT`, default `~/docuauthring`). Selecting any
other ref cannot green the gate.

Row classes:
- **blob rows** — one or more upstream sources, each with the upstream blob hash
  recorded at the last port. `check_ports.py` fails a row when ① any recorded
  upstream blob differs from the same path at the resolved current ref (upstream
  moved — port may be stale) or ② the recorded downstream hash differs from the
  file in the working tree (downstream edited without updating the row).
- **semantic-port** — prompt-level ports of upstream SKILL.md semantics with no
  1:1 source file; reviewed manually at release (excluded from blob comparison).
- **docloop-native** — no upstream.

Seeded 2026-07-17 against upstream `main` = `6a32ef5a56e986c3e4a1207010cc5ee627776ead`.
The guard hardening in this PR is ported from upstream `shared/path_guards.py`
(secondary source on composite rows).

Peer-review rows re-ported 2026-07-17 against upstream `main` =
`55a0eddb9d553a118d5d7e35d6990d47ae81bb34` (upstream moved its staging logic into
a vendored `_staging_lib.py`; docloop ports the semantics — round-conflict rule,
prompt-block contract — while staying single-file, so that lib is a secondary
source on the `lib/stage.py` composite row).

| downstream | class | upstream source(s) | upstream blob | downstream blob | notes |
|---|---|---|---|---|---|
| lib/split.py | blob | skills/pm-authoring/scripts/split.py | 647eea10e03dddd30738903135f61c32d33d2fd3 | 2774874bb1206e9487831a9f7e9c516c565f8cae | composite |
| lib/split.py | blob | shared/path_guards.py | f1d2916ca936a90cadc4fc32af6f0d635845a67c | 2774874bb1206e9487831a9f7e9c516c565f8cae | guard contract (secondary) |
| lib/stage.py | blob | skills/peer-review/scripts/stage.py | 11fd275ead107b0743040bceb592b93b357a0219 | ea7bf528857db8e5ae0135993a0a85278553eb83 | composite |
| lib/stage.py | blob | skills/peer-review/scripts/_staging_lib.py | 913a1196a92bace3ebd049bac1f94b2e0703d772 | ea7bf528857db8e5ae0135993a0a85278553eb83 | staging behavior canon (secondary) |
| lib/stage.py | blob | shared/path_guards.py | f1d2916ca936a90cadc4fc32af6f0d635845a67c | ea7bf528857db8e5ae0135993a0a85278553eb83 | containment contract (secondary) |
| lib/init_workspace.py | blob | skills/asistobe-authoring/scripts/init_workspace.py | c64bb35080ca6d49042be9f6894f3a5f0567ea04 | e0393a174d353dfba1a7cb11f6ab4cda1dbe9ed9 | |
| lib/validate_manifest.py | blob | skills/pm-authoring/scripts/validate_manifest.py | 838f345e2178d325171454d803284c4476a5dbe1 | 36bc962534f3323944007150b34b3adf70e01aac | |
| lib/gap_audit.py | blob | skills/pm-authoring/scripts/gap_audit.py | 2a2e6dbd288a93623cf4027e22b0012abeb3a428 | 930753374b7b3412f0b65c3251e852d0ded8694a | re-ported 2026-07-22 (downstream coverage = readable real files) |
| lib/ground_audit.py | blob | skills/asistobe-authoring/scripts/ground_audit.py | 575bc1f43bf043cf7d2065b6815f4181e77cd321 | d97cbdedc8265267c78b3de6bea4284946526859 | |
| lib/approval_brief.py | blob | skills/pm-authoring/scripts/approval_brief.py | 62ba54c413742ac97ce9e0916b37e708b5dde56c | 90a9d9ff07eba81acf260b69923864147de655d7 | |
| lib/score_report.py | blob | skills/pm-authoring/scripts/score_report.py | c35cb3d17784dc86b4a17efe6613d02d4acabc98 | 1c00597234a6cd12dcc82cd0ad1248de604dfa1d | |
| lib/verbatim_check.py | blob | skills/pm-authoring/scripts/verbatim_check.py | adb02d273cf0d33d53eeb6712b2ceaaeb9f169ee | 520d18ae39a531d2cd0364bf904065b3bb23ba1c | |
| lib/multi_lens_review.sh | blob | skills/peer-review/scripts/multi_lens_review.sh | 1807f09646d0e0eb60d42f57c76b249b7f907069 | 1b78363c612c3507b399627898aeb2a1e488609c | FORCE/clobber block redesign re-ported 2026-07-31 (upstream #115/#116/#120: round lock, validate-first 3-path unit, signal ladder, pgroup kill) |
| lib/blind_lock.py | semantic-port | meta-learning-loop (prediction lock) | - | - | v0.7.0 port |
| lib/panel_review.sh | semantic-port | cross-functional-review | - | - | v0.7.0 port |
| lib/review_gate | docloop-native | - | - | - | package directory; contained upstream ports are tracked in the rows below |
| lib/review_gate/validate_decisions.py | blob | skills/review-gate/scripts/validate_decisions.py | fc3cb02bffa5dc5b08be0e2d45ba4409104ebabd | fc3cb02bffa5dc5b08be0e2d45ba4409104ebabd | review-gate v0.10 fail-closed registry validator; ported from upstream main df20afd |
| lib/review_gate/scan_terms.py | blob | skills/review-gate/scripts/scan_terms.py | 24210fe43650f6d9e41b29f2ec1f4264589b12e9 | 24210fe43650f6d9e41b29f2ec1f4264589b12e9 | review-gate v0.10 deterministic dictionary scan; ported from upstream main df20afd |
| lib/review_gate/audit_anchors.py | blob | skills/review-gate/scripts/audit_anchors.py | 9d0cd142e02d8021328254fdc7fa4eeecb55dc4a | 9d0cd142e02d8021328254fdc7fa4eeecb55dc4a | review-gate v0.10 fail-closed anchor-loss audit; ported from upstream main df20afd |
| lib/review_gate/runner.py | docloop-native | - | - | - | explicit packet preparation/checking; no model or agent orchestration |
| lib/contribution_flow.py | docloop-native | - | - | - | opt-in append-only contribution/curation runner and validated draft bridge |
| prompts/atb-audit.md | semantic-port | asistobe-authoring SKILL.md | - | - | change-plan mode |
| prompts/atb-author.md | semantic-port | asistobe-authoring SKILL.md | - | - | change-plan mode |
| prompts/atb-capture.md | semantic-port | asistobe-authoring SKILL.md | - | - | change-plan mode |
| prompts/atb-chunk.md | semantic-port | asistobe-authoring SKILL.md | - | - | change-plan mode |
| prompts/plan.md | semantic-port | pm-authoring SKILL.md | - | - | |
| prompts/draft.md | semantic-port | pm-authoring SKILL.md | - | - | |
| prompts/gap-audit.md | semantic-port | pm-authoring SKILL.md | - | - | read-visibility block re-ported 2026-07-22 |
| prompts/review.md | semantic-port | peer-review SKILL.md | - | - | four-axis triage contract, re-ported 2026-07-22; `H-<nn>` human-added rows re-ported 2026-07-30 (upstream #112) |
| prompts/review-gate.md | semantic-port | review-gate SKILL.md + playbooks/review-gate/CONTRACT.md | - | - | explicit prepared-packet subset; upstream #160/#161/#162 semantics excluded |
| prompts/contribute.md | docloop-native | - | - | - | perspective contribution envelope contract; no upstream |
| templates/review-gate/default-axes.md | semantic-port | playbooks/review-gate/CONTRACT.md §1 default planning-document axes | - | - | fixed default checklist; LLM sweep remains probabilistic |
| templates/contribution-response.example.yaml | docloop-native | - | - | - | human response/material/disposition example; no upstream |
| bin/docloop · tests/ · templates/ · docs/ | docloop-native | - | - | - | |

Downstream hashes are recorded at port time; `check_ports.py` fails a blob row
when the working-tree file no longer matches (downstream edited without a
re-port row update) — both comparison directions are enforced. Release checklist: run
`python3 tools/check_ports.py` before tagging; stale rows must be re-ported or
annotated as intentional divergence.

## Appendix — public-repo leak-scan spec (hardening plan D4)

- Scope: all tracked + staged + **non-ignored untracked** candidate files (including this file).
- Command: `tools/leak_scan.sh '<private-token-classes>'` — an executable wrapper
  over `git grep --untracked` (worktree incl. non-ignored untracked contents) and
  `git grep --cached` (staged index), because raw `git grep` exits 0 ON a match:
  the wrapper inverts this to the release contract (0 = clean, 1 = any match,
  2 = scan error). An untracked canary must make it exit 1. The token
  classes are: org/product identifiers of the maintainer's employer, personal
  absolute paths (`/Users/<user>`), private workspace names, credential patterns
  (`AKIA`, `ghp_`, `-----BEGIN`). The concrete token list lives in the PRIVATE
  upstream repo only — never commit it here.
- Exit semantics: any hit = nonzero = release blocker.
- Canary proof at release: add a temp file containing one private token, confirm
  the scan fails, remove it.
