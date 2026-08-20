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

Review-gate #160/#161 rows were re-ported 2026-08-04 against upstream `main` =
`44604347e95067fe93a9b62280b76d16f516d5b4`. Adapted deterministic files remain
blob-tracked in both directions; #162 transferability/generalization is not ported.

| downstream | class | upstream source(s) | upstream blob | downstream blob | notes |
|---|---|---|---|---|---|
| lib/split.py | blob | skills/pm-authoring/scripts/split.py | 647eea10e03dddd30738903135f61c32d33d2fd3 | 2774874bb1206e9487831a9f7e9c516c565f8cae | composite |
| lib/split.py | blob | shared/path_guards.py | f1d2916ca936a90cadc4fc32af6f0d635845a67c | 2774874bb1206e9487831a9f7e9c516c565f8cae | guard contract (secondary) |
| lib/stage.py | blob | skills/peer-review/scripts/stage.py | 11fd275ead107b0743040bceb592b93b357a0219 | ea7bf528857db8e5ae0135993a0a85278553eb83 | composite |
| lib/stage.py | blob | skills/peer-review/scripts/_staging_lib.py | 913a1196a92bace3ebd049bac1f94b2e0703d772 | ea7bf528857db8e5ae0135993a0a85278553eb83 | staging behavior canon (secondary) |
| lib/stage.py | blob | shared/path_guards.py | f1d2916ca936a90cadc4fc32af6f0d635845a67c | ea7bf528857db8e5ae0135993a0a85278553eb83 | containment contract (secondary) |
| lib/init_workspace.py | blob | skills/asistobe-authoring/scripts/init_workspace.py | c64bb35080ca6d49042be9f6894f3a5f0567ea04 | e0393a174d353dfba1a7cb11f6ab4cda1dbe9ed9 | |
| lib/validate_manifest.py | blob | skills/pm-authoring/scripts/validate_manifest.py | d4b449cd593d2b565387f0eef33e39450f3fd35c | 36bc962534f3323944007150b34b3adf70e01aac | upstream hash refreshed at 4460434; downstream composite unchanged and outside #160/#161 |
| lib/gap_audit.py | blob | skills/pm-authoring/scripts/gap_audit.py | a6fdfe370e6de04cb23dbae2e9ee5a70fd31d1ee | 930753374b7b3412f0b65c3251e852d0ded8694a | re-ported 2026-07-22 (downstream coverage = readable real files); upstream hash refreshed at 4460434, downstream composite unchanged and outside #160/#161 |
| lib/ground_audit.py | blob | skills/asistobe-authoring/scripts/ground_audit.py | 575bc1f43bf043cf7d2065b6815f4181e77cd321 | d97cbdedc8265267c78b3de6bea4284946526859 | |
| lib/approval_brief.py | blob | skills/pm-authoring/scripts/approval_brief.py | 62ba54c413742ac97ce9e0916b37e708b5dde56c | 90a9d9ff07eba81acf260b69923864147de655d7 | |
| lib/score_report.py | blob | skills/pm-authoring/scripts/score_report.py | c35cb3d17784dc86b4a17efe6613d02d4acabc98 | 1c00597234a6cd12dcc82cd0ad1248de604dfa1d | |
| lib/verbatim_check.py | blob | skills/pm-authoring/scripts/verbatim_check.py | adb02d273cf0d33d53eeb6712b2ceaaeb9f169ee | 520d18ae39a531d2cd0364bf904065b3bb23ba1c | |
| lib/multi_lens_review.sh | blob | skills/peer-review/scripts/multi_lens_review.sh | 1807f09646d0e0eb60d42f57c76b249b7f907069 | 1b78363c612c3507b399627898aeb2a1e488609c | FORCE/clobber block redesign re-ported 2026-07-31 (upstream #115/#116/#120: round lock, validate-first 3-path unit, signal ladder, pgroup kill) |
| lib/blind_lock.py | semantic-port | meta-learning-loop (prediction lock) | - | - | v0.7.0 port |
| lib/panel_review.sh | semantic-port | cross-functional-review | - | - | v0.7.0 port |
| lib/review_gate | docloop-native | - | - | - | package directory; contained upstream ports are tracked in the rows below |
| lib/review_gate/validate_decisions.py | blob | skills/review-gate/scripts/validate_decisions.py | 561debc4af5d4622e32bc2b3746d09f1417f4477 | 561debc4af5d4622e32bc2b3746d09f1417f4477 | review-gate v0.10 fail-closed registry validator; ported from upstream main df20afd; re-ported 2026-08-20 against upstream main d6d5e7f (#199/#217/#231) |
| lib/review_gate/scan_terms.py | blob | skills/review-gate/scripts/scan_terms.py | 1b6ebbf80308f1f1365d704649109fe33d9baef1 | 1b6ebbf80308f1f1365d704649109fe33d9baef1 | review-gate v0.10 deterministic dictionary scan; ported from upstream main df20afd; re-ported 2026-08-20 against upstream main d6d5e7f (#194/#226/#231) |
| lib/review_gate/audit_anchors.py | blob | skills/review-gate/scripts/audit_anchors.py | 368a51c971904928d2111c1d7a7576d20af2f6b4 | 52ea16361a258ede7ba798444c7525be72cd4f64 | composite/adapted: ledger-aware audit; `--packet-root` replaces upstream Git-root trust; re-ported 2026-08-20 against upstream main d6d5e7f (#200 multi-round --l2, #207 stable-id anchors) |
| lib/review_gate/validate_review_intermediate.py | blob | skills/review-gate/scripts/validate_review_intermediate.py | 0abd62ff4ee8efd1cae078d52605535ebdb9f1a8 | 46f1c051de029ee1600ada316229b68bfda79f8a | composite/adapted: packet-root authority containment and strict terminal ledger contract. **STALE 2026-08-20**: upstream moved to 23015975c (345-line diff since pin); not re-ported this pass, see docs/PORTS-gaps-2026-08-20.md |
| lib/review_gate/validate_review_result.py | blob | skills/review-gate/scripts/validate_review_result.py | 83fe4877b9ae88a6fa437e3737a0653eced9ee92 | 04708a3f239ea3f71690f7fbfff7f32dcd88b982 | composite/adapted: prepared-packet validation first plus exact five-field packet binding. **STALE 2026-08-20**: upstream moved to 7b2621c21 (908-line diff since pin, file nearly quadrupled); not re-ported this pass, see docs/PORTS-gaps-2026-08-20.md |
| lib/review_gate/validate_convention_profile.py | blob | skills/review-gate/scripts/validate_convention_profile.py | 7cafab71eb1feee642e913367e0152eedf4b33a5 | 7cafab71eb1feee642e913367e0152eedf4b33a5 | review-gate v0.12 generic profile validator; re-ported 2026-08-20 against upstream main d6d5e7f (invisible-character/default-ignorable id hardening) |
| lib/review_gate/validate_convention_intake.py | blob | skills/review-gate/scripts/validate_convention_intake.py | 2c35e76e93eb352f38f4a778a1807acacdbfd3b6 | aa344e93342d0f6a89f71521a8edf4521cbd2d15 | composite/adapted: generic pre-lens validator with package/script import compatibility; re-ported 2026-08-20 against upstream main d6d5e7f (#193 profile_applicability) |
| lib/review_gate/materialize_docmodel.py | blob | skills/review-gate/scripts/materialize_docmodel.py | 21e0496ea2322800f24b8e6d463c71a8b2f86082 | b6342170a2329f2d1863d5dae7e77a703852661b | composite/adapted: exclusive non-following draft output via O_EXCL/O_NOFOLLOW descriptor-anchored write; never suppression authority; re-ported 2026-08-20 against upstream main d6d5e7f (r6-04 NFC/NFD identity merge, #193 non-applicable refusal, identity-aware overwrite guard combined with existing atomic-create hardening) |
| lib/review_gate/front_gate.py | blob | skills/review-gate/scripts/review_front_gate.py | a6a302bf033515c38d6b966457c23dc38fd27064 | 9b3b283ba9c8c4b5f58783f0b817f720e8465b86 | composite/adapted: internal-only ordering guard with package/script imports; no public execution trace. **STALE 2026-08-20**: upstream moved to 4eca65553 and now depends on a wholly new upstream module `validate_input_gate.py` (CONTRACT §1 input-gate recording, #196/#206/#202, 369 lines, no docloop counterpart yet) — blocked pending that module's port, see docs/PORTS-gaps-2026-08-20.md |
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
| prompts/review-gate.md | semantic-port | review-gate SKILL.md + playbooks/review-gate/CONTRACT.md | - | - | bounded #160 ledger/receipt and generic #161 pre-lens semantics; #162 excluded |
| prompts/contribute.md | docloop-native | - | - | - | perspective contribution envelope contract; no upstream |
| templates/review-gate/default-axes.md | semantic-port | playbooks/review-gate/CONTRACT.md §1 default planning-document axes | - | - | fixed default checklist; LLM sweep remains probabilistic |
| templates/review-gate/contracts/convention-profile-schema.yaml | blob | playbooks/review-gate/contracts/convention-profile-schema.yaml | 4ba39adef1668b29bdf96e32e36728c13e031b4c | 4ba39adef1668b29bdf96e32e36728c13e031b4c | exact generic contract schema; re-ported 2026-08-20 against upstream main d6d5e7f (#193 profile_applicability note) |
| templates/review-gate/contracts/convention-intake-schema.yaml | blob | playbooks/review-gate/contracts/convention-intake-schema.yaml | ea7d7f55424f6b72825fc6197621c19c9896bfe2 | ea7d7f55424f6b72825fc6197621c19c9896bfe2 | exact generic contract schema; re-ported 2026-08-20 against upstream main d6d5e7f (#193 profile_applicability field) |
| templates/review-gate/contracts/docmodel-schema.yaml | blob | playbooks/review-gate/contracts/docmodel-schema.yaml | 92045e30a12532eb60f2207bcf7b7b8d2066a8d4 | 92045e30a12532eb60f2207bcf7b7b8d2066a8d4 | exact draft/authority contract schema; re-ported 2026-08-20 against upstream main d6d5e7f (#194/#226 quoted-date notation) |
| templates/review-gate/contracts/review-intermediate-schema.yaml | blob | playbooks/review-gate/contracts/review-intermediate-schema.yaml | 705d9adbaeb1c5827e18ff1b787f10a5fc938d31 | 705d9adbaeb1c5827e18ff1b787f10a5fc938d31 | exact v2 intermediate-ledger contract schema; re-ported 2026-08-20 against upstream main d6d5e7f (#242 approval_id registry field — schema-only; validator not yet ported, see docs/PORTS-gaps-2026-08-20.md) |
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
