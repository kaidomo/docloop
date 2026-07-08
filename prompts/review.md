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

### 2) Invoke — run the reviewer command (you or the launcher run it)
From the review folder:
```
codex exec --skip-git-repo-check --sandbox read-only - > REVIEW_r<N>.md <<'PEER_REVIEW_PROMPT'
Read REVIEW_BRIEF.md, then review the enclosed artifact against the brief's
"what to look at". Prefix every finding with a finding_id — format `r<N>-<nn>`
(N = this round number, nn = a 2-digit serial within this review, e.g. r1-01).
Write each finding as a markdown bullet in this order: finding_id · severity
(bug / robustness / design / trivial) · location (file · section · line) · claim ·
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

### 3) Triage — classify each finding
Read `REVIEW_r<N>.md` (single pass) or all `REVIEW_r<N>_*.md` (multi-lens) and,
per finding, draft (a) a **severity** and (b) a **disposition**, keyed by finding_id:
- **severity = the *nature* of the finding only**: bug / robustness / design / trivial.
  It carries NO apply/reject verdict — accept/reject lives in the `## Applied (vN)`
  table's `status` column (the disposition), never in severity.
- **disposition draft**: apply / apply-recommended / discuss / reject — the direction,
  approved at the §4 human gate.
- **finding_id keying**: key each finding by finding_id (single = `r<N>-<nn>`,
  multi-lens = lens-prefixed `r<N>-<lens>-<nn>`; both round-global-unique). If the
  Critic omitted an id, assign one in discovery order.
- **alias (duplicates / re-review)**: if several lenses raise the same finding, keep
  ONE as the canonical id and fold the rest as aliases in the reason cell. **If a prior
  round's finding is still live on re-review**, even when the new Critic re-numbers it
  under this round, triage folds that new finding onto the ORIGINAL round's finding_id
  and keeps the Applied-table canonical key at the original id (e.g. r2-05 re-confirms
  r1-01 → canonical r1-01). Never rewrite an id the reviewer already emitted.
Inherit the reviewer's severity; adjust only a clear misclassification.

### 4) ⛔ Human approval gate (the core of "semi-automatic")
Present the classification and ask the human what to apply. Apply only the approved
items, then test. Blindly applying selective/wrong critique risks regressions.

### 5) Document — record the round in the brief
Add a `## Applied (vN)` table keyed by finding_id: **finding_id** → **lens**
(multi-lens only; single pass = n/a) → finding → class (severity) →
**status (applied / pending / held / rejected)** → path → reason/test.
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
- **converged** — every bug/robustness finding from the last round is dispositioned
  (applied / held-with-reason / rejected) and no approved-apply item is unresolved.
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
