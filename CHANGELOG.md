# Changelog

All notable changes to docloop are documented here. This project adheres to
[Semantic Versioning](https://semver.org/). A version is tagged on every merge to `main`.

## [0.10.1] — 2026-07-30
### Fixed
- **다렌즈 드라이버가 빈 산출물을 성공으로 보고하던 것** — upstream `docuauthring` #110/#111 재포팅.
  `multi_lens_review.sh`가 렌즈의 **종료코드만** 보고 ✓를 찍었다. 렌즈가 exit 0인데 리뷰 파일을
  남기지 않으면 존재하지 않는 파일명을 ✓와 함께 출력하고 triage로 안내했고, 그러면 triage에서
  "렌즈가 아무것도 못 찾았다"와 "렌즈가 아예 돌지 않았다"가 구분되지 않는다 — 취합 규칙(합의=신뢰↑,
  단독 발견도 진짜일 수 있음)은 각 렌즈가 실제로 돌았다는 전제 위에 있다.
  이제 성공 = 종료코드 0 **그리고** 파일 실재 **그리고** 공백 아닌 내용이며, 실패 사유를
  `exit code` / `no output` / `empty output` 셋으로 구분해 보고한다.
  함께 재포팅: **FORCE=1 재실행이 낡은 산출물을 ✓로 통과시키던 것** — `-o` 캡처 경로는 렌즈가
  아무것도 안 쓰면 파일을 건드리지 않아 이전 라운드 내용이 살아남는다. 이제 실행 전에 `.forcebak`으로
  치우고, 가드 거부·INT/TERM/HUP에서 되돌린 뒤 중단한다 — FORCE가 수용한 것은 덮어쓰기이지 증거
  소실이 아니다. **범위를 과장하지 않기 위해**: 이는 파일시스템 트랜잭션이 아니다. `kill -9`나
  mv와 등록 사이의 크래시, 복원 실패는 여전히 `.forcebak`을 남길 수 있다 — 그 경우 경고를 찍고
  내용을 `.forcebak` 이름으로 **남겨두므로 복구는 rename 한 번**이다.
  **이 저장소에는 이 스크립트의 테스트가 0건이었다.** 회귀 21건을 새로 썼다(199 → 220): 가짜 `codex`를
  PATH에 놓아 두 캡처 경로(`-o` / stdout 폴백)를 모두 태우고, 산출물 없음 · 0바이트 · 공백만 · 정상 ·
  종료코드 실패 · FORCE 낡은내용 · FORCE 신규내용을 각각 확인한다. 변이 3종으로 가드가 실제로 무는 것을
  확인했다 — 산출물 검사 제거 → 5건 FAIL · 공백판정을 `[ -s ]`로 약화 → 1건 FAIL · FORCE 치움 제거 →
  2건 FAIL · 심링크 분기 롤백 제거 → 2건 FAIL · BOM 제거 되돌림 → 1건 FAIL · `.forcebak` 선점 검사 제거 → 3건 FAIL.
  롤백 경로는 **부분 치움 상태에서 실제로 도달**하는 다렌즈 케이스로 덮었다(앞 렌즈 치움 후 뒤 렌즈가
  심링크·비정규파일·기존 `.forcebak`으로 거부되는 세 갈래 — 앞 렌즈가 바이트 단위로 복원되는지 확인).
  선행 UTF-8 BOM 하나는 공백 판정 전에 제거한다(BOM만 있는 산출물이 내용으로 통과하던 경계).
  가짜 `codex` 픽스처가 검증하는 것은 **드라이버의 판정 계약**이지 codex의 행동이 아니다.
  `docs/PORTS.md` 해시 갱신 — 이 재포팅으로 `check_ports.py`가 **0 failures**가 됐다.

## [0.10.0] — 2026-07-30
### Added
- **Human-added triage rows (`H-<nn>`)** — re-ported from upstream `docuauthring` #112
  (`peer-review`/`codex-peer-review` SKILL.md §5). Every other triage row comes from review
  output, so the set is conditional on what the Critic happened to raise and can never show
  what a reviewer *misses*. `H-<nn>` rows are the loop's only false-negative signal: the
  human notes an issue the reviewer never surfaced, keyed with its own id, classified on the
  same four axes, `human-added; no review file` in the reason cell.
  Guards on both sides — no quota (against over-recording), and an explicit statement that
  capture is opportunistic so zero `H` rows is never evidence of zero misses and `H` counts
  are never a recall denominator, gate, or reviewer-quality claim (against under-recording).
  `H` ids are unique across the whole review loop, not per round, and never reuse an `r` id.
  Upstream note: the same change had to reach the brief templates and the shared FID
  validator, not just the prose — the contract is enforced in more places than it is stated.

## [0.9.1] — 2026-07-22
### Fixed
- **`gap-audit` downstream coverage now counts readable real files** (re-port from the canonical
  upstream `pm-authoring` skill). It used to count *path strings* under three hardcoded keys, which
  produced two false signals: ① registering a target under any other key made coverage 0 and raised
  a **false cross-blind** warning even though a real target existed — key names are now irrelevant
  to counting (the allowlist `storyboard` / `manual_manifest` / `policy_docs` stays, but only as the
  validator's **typo warning**); ② a declaration was counted even with no file behind it, so
  coverage could be 1 with nothing to read, defeating the cross-blind warning and the
  `--strict-cross-audit` gate. Paths now resolve **against the manifest file** (`~` expanded) and
  only **existing, readable regular files** count — a missing file or a directory counts 0 — with
  **duplicate paths de-duplicated** by realpath. `sources` coverage and the validator's public
  signatures are unchanged (a code root is a directory, so real-file counting doesn't fit there).
- **A registered downstream that can't be read is surfaced per item.** Fixing the count alone left a
  hole: with at least one source registered the aggregate is non-zero, so `cross_blind` never fires
  and a downstream that silently vanished produced **no signal at all**. Read failures are now
  collected separately and surfaced ① as a warning block in the report (showing the declaration as
  written) ② as a count on the coverage line ③ on stderr ④ as a `--strict-cross-audit` failure.
  "What wasn't read is reported as not read" now holds **per item**, not in aggregate.
- **Coverage-line wording**: with 0 cross-check targets the line said `⚠️ none registered`, which
  could be read as a literal falsehood ("1 registered, 0 readable"). It now says
  `⚠️ 0 cross-check targets`.
### Changed
- **`gap-audit` prompt gains a "read visibility" contract** — the agent reading each downstream
  records **the number of units it actually compared** or **"unit identification FAILED"** (ambiguous
  → FAILED, no fake success). The comparison scope is unchanged: comparing by unit only changes the
  granularity and address of a finding, and the whole-document omission scan is still performed.
  There is no declaration schema (the reader is a model). A one-line improvement suggestion is
  emitted **only on identification failure** (not a mandate, schema, or gate — it cites the repo's
  storyboard `data-screen-id` as an example of good structure), and the report states the limit:
  this is a model report, so the stated count is not mechanically guaranteed.
### Notes
- Tests 185 → 199. `check_ports` 0 failures against upstream `main`.

## [0.9.0] — 2026-07-22
### Changed
- **`review` triage is now a four-axis contract** (re-port from the canonical upstream
  `peer-review` skill). What used to be one `severity` label plus an `apply /
  apply-recommended / discuss / reject` direction is split into four independently
  recorded axes: **validity** (`verified` / `unverified` / `refuted`), **nature**
  (`bug` / `overclaim` / `robustness` / `design` — `trivial` dropped), **lifecycle**
  (`new` / `duplicate` / `reopened` / `regression` / `carried`) and **disposition**
  (`apply` / `defer` / `reject` / `already addressed` / `pending verification` /
  `human decision` / `pending approval`). Collapsing them into one label let the
  triager pick the verdict they preferred.
- **Acceptance rules that close the known escape hatches**: `unverified` is never a
  rejection ground (→ `pending verification`); acceptance is judged by validity, not by
  repair cost, and a fix may NOT be required to be covered by existing tests (that
  condition structurally killed findings about missing tests); lifecycle never decides
  disposition, `duplicate` folds only under a full match, `reopened` counts as
  unresolved; a top-down **precedence table** resolves conflicting combinations and
  routes rule-ownership questions to the human so `design` cannot be used as an escape
  hatch.
- **Disposition obligations**: every non-`apply` disposition carries a one-line reason;
  an all-`apply` round must record the counter-hypothesis for its least certain finding.
  No rejection quota, no acceptance-rate target.
- **`converged` re-defined** by the resolution state of canonical findings, not per-round
  event counts: not converged while any `pending verification` / `human decision`
  disposition, any `reopened` / `regression` lifecycle, or any approved-but-unvalidated
  `apply` item remains.
- The `## Applied (vN)` table (`review` §5 and `REVIEW_BRIEF.template.md`) gains one
  column per axis; the reviewer prompt now asks for `nature`, not `severity`.
### Notes
- Semantic-port row only; no blob/script logic changed (`check_ports` 0 failures against
  upstream `main`). Tests 185/0.

## [0.8.0] — 2026-07-21
### Added
- **Change-plan audit gains two close-reading gates** (re-port from the canonical upstream
  `asistobe-authoring` skill): `atb-audit` items 5) **evidence-transfer fidelity** — provenance /
  modality / freshness, the three properties that silently drop when evidence is carried into a
  derived claim or a locator — and 6) **change-impact propagation** — sync summaries / change-logs /
  counts and confirm no discarded option lingers when a decision changes.
- **Executable apply-instruction contract** in `atb-author` for execution-oriented output only:
  stable anchors, an exact replacement or a canonical clause pointer (no ellipsis / partial-edit),
  census + expected result, marked placeholders, dependent edits, and a row+cell-cardinality-
  lossless DoD.
- **Packet hygiene (pre-flight)** in `review` — before handing a staged packet to the reviewer,
  verify it is self-contained/reproducible (claim→source inventory, oral-decision provenance,
  absence-claim search scope, reproducible generated stats); an essential gap stops with
  `blocked_missing_input`.
- **Role-selection risk-signal** in `panel_review` (advisory): transcription-accuracy risk →
  `qa` candidate; claim↔evidence-loss risk → an `evidence-auditor` candidate.
### Notes
- `--strict` clarified as a **structure check only** (not semantic consistency / assertion
  strength / self-contradiction — those are the close-reading pass and human audit).
- Semantic-port rows only; no blob/script logic changed (`check_ports` 0 failures against upstream
  `main`). Codex re-port review r1 (3 fidelity findings applied). Tests 185/0.

## [0.7.0] — 2026-07-17
### Added
- **Role-panel review (`docloop panel`)** — ported (downstream) from the canonical
  `cross-functional-review` skill (docuauthring v0.8.0). Independent job-role evaluators
  (default pm · product-designer · frontend · backend · qa; case-specific roles allowed)
  each run as their **own headless model process**, with role outputs held in a private temp dir until every role finishes (and the prompt forbids reading PANEL_* files) — process separation on one machine, not an air gap —
  then an Area Chair synthesis preserves conflicts, abstentions, and lone criticals, never
  averages or majority-votes, records same-model agreement as correlated (no confidence
  boost), and compresses to at most 5 human decision items. New: `lib/panel_review.sh`
  (modeled on `multi_lens_review.sh`: DRY_RUN / FORCE / filename-injection guards),
  `templates/finding-envelope.example.yaml` (ported envelope; upstream canonical wins on drift).
- **Prediction lock (`docloop lock` / `docloop verify`)** — the B1 blind-diagnosis primitive
  from the canonical `meta-learning-loop` skill: seal a prediction file with a sha256
  **sidecar** (digest lives outside the hashed file — in-file digests are circular) *before*
  the outcome exists; re-hash at reveal. Tampered payload → "judge nothing, diagnostic-only".
  Re-lock refused (append-only). Only the primitive is ported — the full learning lifecycle
  (experiment cards, lesson states, human gate) stays upstream. New: `lib/blind_lock.py`.
- Tests: 126 → 151 (blind_lock lock/verify/tamper/re-lock/malformed-sidecar/quoted-paths; panel validation, dry-run smoke, and real-execution paths via a fake CLI shim: publish-after-validate, failure propagation, empty-output rejection).

## [0.6.0] — 2026-07-08
### Changed
- **Ported the source-fidelity quality patch from the canonical skills (docuauthring #33).**
  The review contract verified in-plan consistency and verbatim anchor matches but never
  close-read the *source document* (`reviewed_artifact = CHANGE_PLAN, source = evidence only`),
  so mislabelled sections, over-asserted subject/scope, house-style violations, and
  insertion-vs-replacement deletion risk slipped through. The fix adds **close-reading to the
  atb-audit completion gate** and **source-collation as a required review verification axis**
  (not extra review rounds).
  - `prompts/atb-audit.md` (ground-audit): a **close-reading pass against the source** with 4
    gates — (1) section/heading context, (2) house-style/terminology, (3) over-assertion/scope
    lens, (4) insertion safety — framed by "verbatim anchor match = false confidence". Plus a
    **completion definition**: clearing the gates still leaves the output a *draft until domain
    sign-off* (not "reviewer converged" / anchor match); on sign-off flip the chunk `status` to
    `approved` (or log it in `_ground_report.md`).
  - `prompts/review.md` (Oracle loop): **source-collation is a required verification axis when
    evidence is enclosed** — collate anchors' section/context, terminology, scope, and
    original+new side-by-side for insertions, not just string matches. Evidence stays
    `evidence only` (not the revision target) but is promoted to a close-reading target.
    Same instruction added to the reviewer heredoc prompt.
  Prompt-only change (no `lib/` or schema change); 126 tests still pass. Mirrors the canonical
  skills so the two round-trip; docuauthring peer review (Codex r1) had already folded the
  insertion-safety gate into the review contract (finding r1-02).

## [0.5.0] — 2026-07-07
### Changed
- **Ported change #5 — `score_report.py` reads the policy top-level `scoring` (contract 1).**
  Mirrors the pm-authoring canonical change (Q2: scoring axes hoisted from `review_audit`
  into a top-level `scoring` block). `lib/score_report.py` now reads the top-level
  `scoring` first, falling back to legacy `review_audit.scoring` / `priority_rubric`.
  - **Field-level merge** (not a block `top or old`): a partially-migrated policy keeps the
    legacy `pass_threshold` instead of silently dropping to the default 3; `scale` is
    key-merged; a scalar `rubric` ref is guarded (no crash); `weights` fall back by key
    presence (an explicit empty `{}` is honored, not leaked from the legacy path).
  - Tests: 9 new-path checks (partial-migration, coexistence precedence, weight ordering,
    empty-weights, scalar-ref crash guard, partial-scale key-merge). Legacy fixtures retained
    to lock the fallback. 126 passed.
  Note: docloop policy templates carry no `scoring` block (pure writing harness), so nothing
  to hoist there — this keeps the reader in round-trip sync with the canonical skill.
  `score_report.py` stays dispatcher-dormant; the change is inert until wired. This closes the
  0.4.0 note that "policy (contract 1) is out of scope" for the scoring-reader surface.

## [0.4.0] — 2026-07-07
### Changed
- **Ported the review (Oracle) loop to the peer-review canonical (contracts 2 & 3).**
  peer-review is upstream; docloop mirrors it so the two round-trip.
  - Reviewer prompts (single pass `prompts/review.md`, multi-lens `lib/multi_lens_review.sh`)
    now require a **finding_id** per finding (`r<N>-<nn>`; multi-lens `r<N>-<lens>-<nn>`)
    plus **location + claim** as required fields — the shared key for contracts 2 & 3.
  - Triage **severity** carries the finding's *nature* only (bug / robustness / design /
    trivial); accept/reject moved fully to the Applied table's `status` (removed "reject"
    from the severity axis). Triage also mirrors the canonical **alias-fold** rule
    (re-confirmed prior-round findings keep their original finding_id).
  - `REVIEW_BRIEF` **Applied (vN)** table is keyed by **finding_id** (+ lens column);
    status vocabulary fixed to `applied / pending / held / rejected` (was `hold`).
  - Termination recorded as a contract `termination.status` English enum: normal
    `converged / round_cap / human_stop` **plus 5 failure/deadlock states**
    (blocked_missing_input / writer_noncompliance / critic_disagreement / rule_conflict /
    budget_exhausted), with a `residual` finding_id list and ssot_ref/policy_ref re-stated.
  - `REVIEW_BRIEF` gains an **Input / rule versions** header
    (`ssot_ref`, `policy_ref.policy_version`) for round traceability.
  Note: severity token `design` follows decision Q4. `manifest.yaml` document-state and the
  `gap_audit`/`ground_audit` hard gates are unchanged; policy (contract 1) is out of scope.
  `prompts/review.md` and `lib/multi_lens_review.sh` are shared by document and change-plan
  (atb) modes — this affects both by design.

## [0.3.0] — 2026-07-06
### Added
- **Change-plan mode (as-is/to-be)** — a second, delineated pipeline for planning fixes to an
  existing system (vs. writing a fresh doc). Read product/docs/logs/code → capture observations →
  group into ordered chunks → write a single as-is/to-be canonical doc for a human to apply.
  - Stages `atb-capture → atb-chunk → atb-author → atb-audit`, plus `atb-gate`, added as flat
    `bin/docloop` commands (no pipeline-selector abstraction — matches the existing dispatcher).
    Reuses `init` and `review`.
  - `lib/ground_audit.py` — the ground-audit gate: a to-be built on a wrong as-is is the most
    expensive mistake, so `--strict` blocks ungrounded to-be (authored chunk with an unverified
    member), untraceable to-be (no members), missing `order_rationale`, missing as-is, and pending
    chunks. Mirrors gap-audit's honesty guard: `--strict-cross-audit` fails when 0 `project.sources`
    are registered while chunks are authored (as-is is self-assertion only).
  - `validate_manifest`: optional `observations[]` (=issue) and `chunks[]` (=handoff + as-is/to-be,
    with `order`/`order_rationale`) blocks — absent = pass (document mode unaffected), present =
    validated (referential integrity of `members` → `observations`, same idiom as `decision_id`).
    `sections`/`doc_type` "empty" warnings are suppressed when a manifest is in change-plan mode.
  - `templates/manifest.atb.example.yaml` + `templates/policy.atb.example.yaml` (sequencing
    direction, `consumer`, taxonomy). New prompts under `prompts/atb-*.md`.
- **`project.sources` recognizes `docs` and `logs`** (additive) for change-plan grounding.
  Coverage counting stays mode-specific — `gap_audit` counts `code_roots/design/prototypes`
  (document mode's audited classes, unchanged), `ground_audit` counts `code_roots/design/docs/logs`
  — so recognizing a key in the validator never silently changes a mode's cross-blind honesty guard.
### Fixed
- **`validate_manifest`: file/YAML errors now exit cleanly** (a `[abort]` message) instead of a
  raw traceback — shared by both gates.

## [0.2.0] — 2026-06-24
### Added
- **Opt-in flags to fail (not just warn) on a vacuous gate**, for release CI that must not
  pass a check that verified nothing:
  - `verbatim_check.py --strict-verbatim-coverage` — implies `--strict` and also fails when
    nothing was verifiable (0 quotes or 0 readable sources).
  - `score_report.py --strict-scoring-coverage` — implies `--strict` and also fails when
    nothing was scored, or scored sections left configured axes unscored.
  Plain `--strict` is unchanged (still warns only on vacuity); the new flags are additive.
  This completes the family started by gap-audit's `--strict-cross-audit`.

## [0.1.2] — 2026-06-23
Silent-omission hardening — extends the gap-audit honesty guard to the other gate scripts
(a self-audit found the same "passes because nothing was checked" pattern elsewhere).
### Fixed
- **verbatim_check: a missing source target no longer mislabels the matched source.**
  `zip(targets, present-only-texts)` misaligned when an earlier target was missing, so a
  quote could be reported as matching the wrong source. Each label now travels with its own
  normalized text.
### Added
- **verbatim_check: vacuous-pass warning.** With 0 quotes or 0 readable sources, `MISS 0` /
  passing `--strict` means "nothing was checked", not "all quotes match" — now surfaced in
  the report and on stderr (also notes missing declared sources).
- **score_report: vacuous-pass + incomplete-scoring warnings.** Passing `--strict` with 0
  scored sections ("nothing scored"), or scored sections missing axes (an absent axis is
  never counted as below-threshold), are now surfaced in the report and on stderr.
- Existing `--strict` behavior is unchanged (warnings only); these mirror gap-audit's guard.

## [0.1.1] — 2026-06-23
### Fixed
- **gap-audit coverage counts recognized schema keys only.** `_count_paths` used to
  count any key under `project.sources` / `downstream`, so a typo (e.g. `code_root`
  instead of `code_roots`) inflated coverage and suppressed the cross-blind warning —
  a false "clean" signal the honesty guard exists to prevent. It now counts only the
  known keys (`code_roots`/`design`/`prototypes`, `storyboard`/`manual_manifest`/
  `policy_docs`), mirroring `validate_manifest`.

## [0.1.0] — 2026-06-23
Initial public release + same-day design/honesty refinements (baseline tag).
### Added
- Thin writing harness: `bin/docloop` wraps `codex`/`claude -p`; stage prompts
  (plan/draft/gap-audit/review) + lib scripts (validate, gap-audit, split, …).
- **gap-audit cross-audit coverage honesty guard**: surfaces when `gaps: 0` reflects
  internal consistency only (0 sources/downstream registered); opt-in
  `--strict-cross-audit` makes that a release-gate failure.
### Changed
- `docs/design.md`: the review stage is framed as *independent pressure* (an
  attention test, not a stand-in oracle); added "What docloop does not give you"
  (converges on a chosen source set, not the truth). "Evidence over assertion".
- Internal comments/docstrings translated to English.
