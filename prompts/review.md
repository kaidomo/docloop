# docloop / review — the Oracle loop (cross-review by an external model)

You are running the **review** stage. Code has compilers and tests as its oracle;
writing has none. docloop's substitute is an **external model** (Codex, Gemini, or
another Claude) reviewing the draft — and a disciplined loop around that review.
Staging is automatic (`docloop review` scaffolds the folder + brief); the reviewer
is invoked by the command in step 2; **applying feedback is a human gate**, so a
wrong critique is never blindly applied.

**Source-collation is a required verification axis (when evidence is enclosed).** If the
reviewed artifact is derived from another source (an SSOT, evidence docs, code — e.g. a
change plan) and that source is staged alongside it, the reviewer does not stop at confirming
that quoted strings match — in at least one pass, collate whether (a) each anchor's parent
section/context matches the source, (b) terminology/house-style matches, (c) subject/scope
claims do not exceed the source's definitions, (d) insertion-type changes (adding a row/block)
are shown with original+new side-by-side so nothing is silently replaced/deleted. **Verbatim
anchor match = false confidence** (the text can be right while the context/section is wrong).
The evidence stays `evidence only` (not the revision target) but is promoted to a
**close-reading target**.

## The loop

### 0) Scope
Pick the target (the SSOT body, or the whole work folder) and a name.

### 1) Stage — scaffold + brief
`python3 lib/stage.py <name> <target...>` → copies the target into a review folder
and (if absent) a `REVIEW_BRIEF.md`. **You fill the brief:** what it is, how it's
used/verified, **decisions already made** (so the reviewer won't relitigate them),
**what to look at (prioritized)**, caveats, and **`## Input / rule versions`**
(ssot_ref = input-SSOT commit/version; policy_ref.policy_version, n/a if none —
recorded once at brief creation, re-stated in `## Loop closed` for audit). Never
overwrite an existing brief — rounds accumulate.

**Packet hygiene (pre-flight).** Before handing the staged packet to the reviewer, verify it is
self-contained and reproducible — the reviewer sees only what you stage, so a silent gap can make
the review incomplete, unsupported, or wrong. This is an input-hygiene check on what you stage, a
completeness guard, not a review rubric or lens. Check:
- **claim → source inventory**: every key claim the review must judge has its source-of-truth
  excerpt actually staged, not merely referenced by path.
- **oral-decision provenance**: decisions made in conversation are captured with their source, not
  asserted bare.
- **absence claims carry search scope**: any "X does not exist / nothing does Y" states where and
  how that was searched.
- **generated stats carry inputs**: any count or metric preserves the input, command, and
  assumptions so the reviewer can reproduce it.

If an essential check fails, stop with `blocked_missing_input` (§6) or narrow the review scope —
do not hand off a packet known to be missing evidence the review depends on. Disclosure-and-proceed
(noting the gap in the brief) is only for explicitly nonessential gaps. This guard covers packet
*completeness* only — it does not catch a citation that misquotes its source (that is the
source-collation axis above) or a discarded option still lingering in a change log (an
authoring-side propagation issue, upstream of review).

### 2) Invoke — run the reviewer command (you or the launcher run it)
From the review folder:
```
codex exec --skip-git-repo-check --sandbox read-only - > REVIEW_r<N>.md <<'PEER_REVIEW_PROMPT'
Read REVIEW_BRIEF.md, then review the enclosed artifact against the brief's
"what to look at". Prefix every finding with a finding_id — format `r<N>-<nn>`
(N = this round number, nn = a 2-digit serial within this review, e.g. r1-01).
Write each finding as a markdown bullet in this order: finding_id · nature
(bug / overclaim / robustness / design) · location (file · section · line) · claim ·
suggested_fix (if any). location and claim are REQUIRED. Do not relitigate decisions
already made. If source/original files are enclosed, collate whether each quote/anchor
matches the source not just as a string but in its parent section, context, terminology
and scope claims, and whether insertion-type changes are shown with original+new
side-by-side (a string match alone does not pass). Do not modify files — output review text only.
PEER_REVIEW_PROMPT
```
- Quote the heredoc terminator (`'PEER_REVIEW_PROMPT'`) so `$`/backticks in the
  body aren't shell-expanded. If the prompt has many quotes, write it to a temp
  file and pipe `... < prompt.txt`.
- **Swap reviewers freely:** `gemini --skip-trust -p "<same instruction>" > REVIEW_r<N>.md`,
  or `claude -p`. As long as output lands in `REVIEW_r<N>.md`, the rest is identical.
- **Single medium pass is the default.** Bump effort or add lenses only for large/
  complex artifacts where a single pass starts missing things — and measure first.
- **Read-only:** pass `--sandbox read-only` explicitly and say "don't modify files".
- If you can't/won't call the model, a human can run it and paste the result into
  `REVIEW_r<N>.md` — the loop continues unchanged (file handoff).

### 3) Triage — classify each finding on four axes
Read `REVIEW_r<N>.md` (single pass) or all `REVIEW_r<N>_*.md` (multi-lens) and,
per finding, record **four independent axes**, keyed by finding_id. Mixing them into
one label lets the triager pick the disposition they prefer, so each is recorded on
its own — never collapsed:
- **validity — is the finding true?** `verified` / `unverified` (no evidence yet:
  comparison not run, cannot reproduce, source not supplied) / `refuted`.
- **nature — what kind?** `bug` / `overclaim` (the artifact claims more than it does) /
  `robustness` / `design` (a choice, not a right answer). Nature carries NO apply/reject
  verdict — the verdict lives in the disposition axis alone, never in nature.
- **lifecycle — history?** `new` / `duplicate` / `reopened` / `regression` / `carried`
  (resolved earlier and carried into a later revision).
- **disposition** — `apply` / `defer` / `reject` / `already addressed` /
  `pending verification` / `human decision` / `pending approval`. Drafted here,
  approved at the §4 human gate.

**Rules that keep the axes honest:**
- **`unverified` is never a rejection ground.** Do not `reject` without `refuted` or an
  explicit out-of-scope basis; disposition it `pending verification` and record what
  evidence would settle it.
- **Acceptance is judged by validity, not by cost.** Repair cost affects priority and
  timing only — never whether a true defect is accepted. Do not require that a fix be
  covered by existing tests (that condition structurally kills findings about missing
  tests); add the regression test as part of the fix instead.
- **Lifecycle never decides disposition.** Fold as `duplicate` only when artifact
  revision, location, cause, preconditions and evidence ALL match *and* the original
  basis still holds. New evidence, a partial fix, a different cause, or a recurring
  symptom means the canonical id is `reopened` and counts as unresolved.
- **Forbidden combinations**: `reopened` / `regression` with `already addressed`.

**Precedence — apply top-down; it resolves conflicting combinations:**

| # | Condition | Disposition |
|---|---|---|
| 1 | `refuted` | `reject` (record the refutation) |
| 2 | `unverified` | `pending verification` — regardless of nature |
| 3 | Resolving it requires changing or interpreting a rule the human owns | `human decision` — regardless of nature |
| 4 | `verified` + `design` | `human decision` (attach options and a recommendation) |
| 5 | `verified` + `bug` / `overclaim` / `robustness` | `apply` |

Rule 3 exists so that `design` cannot be used as an escape hatch: a compliance claim
stays `nature: bug` and is routed to the human by *ownership*, not by relabeling it as
a matter of taste.

**Disposition obligations:**
- **`defer` / `reject` / `pending verification` / `human decision` each carry a one-line
  reason** in the reason cell; `already addressed` records *where* it was addressed;
  `pending approval` is transient and needs none until it resolves. No thresholds, no
  scores — these accumulated reasons are the case law this loop learns from.
- **If every disposition in a round is `apply`**, record — for the single least certain
  finding — the counter-hypothesis and why it was rejected. (A bare "all of them were
  valid" does not satisfy this.)
- No requirement on the *number* of rejections. Never manufacture a rejection to satisfy
  a quota; there is no acceptance-rate target.

**Keying and aliases:**
- **finding_id keying**: key each finding by finding_id (single = `r<N>-<nn>`,
  multi-lens = lens-prefixed `r<N>-<lens>-<nn>`; both round-global-unique). If the
  Critic omitted an id, assign one in discovery order.
- **alias (duplicates / re-review)**: if several lenses raise the same finding, keep
  ONE as the canonical id and fold the rest as aliases in the reason cell (`lifecycle:
  duplicate` — only under the fold condition above). **If a prior round's finding is
  still live on re-review**, even when the new Critic re-numbers it under this round,
  triage folds that new finding onto the ORIGINAL round's finding_id and keeps the
  Applied-table canonical key at the original id (e.g. r2-05 re-confirms r1-01 →
  canonical r1-01, `lifecycle: reopened`). Never rewrite an id the reviewer already emitted.

Inherit the reviewer's `nature`; adjust only a clear misclassification. `validity`,
`lifecycle` and the disposition are always triage's own judgement — never inherited.
Cross-lens agreement can raise confidence, but a single-lens finding is never
automatically downgraded for standing alone.

### 4) ⛔ Human approval gate (the core of "semi-automatic")
Present the classification and ask the human what to apply. Apply only the approved
items, then test. Blindly applying selective/wrong critique risks regressions.

### 5) Document — record the round in the brief
Add a `## Applied (vN)` table keyed by finding_id: **finding_id** → **lens**
(multi-lens only; single pass = n/a) → finding → **validity** → **nature** →
**lifecycle** → **status (the disposition settled at the §4 gate: apply / defer /
reject / already addressed / pending verification / human decision / pending
approval)** → path → reason/test — the four axes of §3, one column each.
An `apply` row is only complete when the change is in the live original AND the
reason/test cell records its validation; an `apply` row without that validation counts
as unresolved and blocks `converged` (§6).
finding_id is the canonical id triage keys on (§3) — normally the reviewer-emitted id (§2),
but triage may assign one (if the reviewer omitted it) or alias-fold a re-review onto the
original id (single = `r<N>-<nn>`, multi-lens = lens-prefixed); it is the stable cross-round key —
per §3's alias rule, a prior finding still live on re-review keeps its ORIGINAL id here,
so dispositions and residual track across rounds. The lens column is for readability;
uniqueness rests on finding_id.
Apply to the live original (the review folder holds a copy; find the original via
`STAGE_MANIFEST.md`; in git, `git diff` once before applying). Use the staged basename
or a repo-relative path in the 'path' cell (full paths live in the manifest) so the
table doesn't overflow. Timestamp the round header `(vN — YYYY-MM-DD HH:MM:SS TZ)`.

### 6) Loop — termination (explicit)
Stop when ONE holds. Record the outcome as a contract `termination.status`
**English enum** (Korean only as a parenthetical gloss; the recorded value is English):
- **converged** — judged by the resolution state of the canonical findings, not by
  per-round event counts. It is **not** converged while ANY of these remain: a finding
  dispositioned `pending verification` or `human decision`; a finding whose lifecycle is
  `reopened` or `regression`; or an approved `apply` item not yet implemented and validated.
- **round_cap** — 3 rounds max (cost control). Leftovers → human decides.
  (Reaching the round cap is `round_cap` ONLY — never `budget_exhausted`, to avoid overlap.)
- **human_stop** — the user can end any time; stop takes priority over table state.

If the loop cannot converge and needs human intervention, close it under exactly
ONE of these 5 failure/deadlock statuses (contract `termination.status`):
- **blocked_missing_input** — a required input (SSOT / brief item / target file) is
  missing, so the review can't proceed.
- **writer_noncompliance** — an approved change was not applied to the original, or
  not applied as directed.
- **critic_disagreement** — on re-review the Critic contradicts itself on the same
  finding; automatic convergence impossible.
- **rule_conflict** — a finding conflicts with a decision already made or a policy
  hard rule.
- **budget_exhausted** — token/time/external budget spent (OUTSIDE the round cap;
  reaching the round cap is `round_cap`, never this).
Who names it: automatic detection of the unresolved/blocked state is script-side where
present, but the FINAL deadlock label is named by a human (docloop has no auto-detect
gate for these — discipline + human name it).

Record `## Loop closed` with:
- **status**: one contract `termination.status` enum (a normal value above or a failure value).
- **residual**: the finding_ids handed to the human unresolved.
- **ssot_ref · policy_ref.policy_version**: re-stated from the `## Input / rule versions`
  header for audit (these are header re-statements, NOT `termination` fields).

## Boundary
This stage is NOT a call wrapper. The model call is made by the `codex`/`gemini`/
`claude` CLI. What docloop adds on top is the **discipline**: stage → brief →
accumulating rounds → human gate → vN record → termination. Swap the tool, keep
the discipline.
