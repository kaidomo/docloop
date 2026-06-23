# docloop / review — the Oracle loop (cross-review by an external model)

You are running the **review** stage. Code has compilers and tests as its oracle;
writing has none. docloop's substitute is an **external model** (Codex, Gemini, or
another Claude) reviewing the draft — and a disciplined loop around that review.
Calls and capture are automatic; **applying feedback is a human gate**, so a wrong
critique is never blindly applied.

## The loop

### 0) Scope
Pick the target (the SSOT body, or the whole work folder) and a name.

### 1) Stage — scaffold + brief
`python3 lib/stage.py <name> <target...>` → copies the target into a review folder
and (if absent) a `REVIEW_BRIEF.md`. **You fill the brief:** what it is, how it's
used/verified, **decisions already made** (so the reviewer won't relitigate them),
**what to look at (prioritized)**, caveats. Never overwrite an existing brief —
rounds accumulate.

### 2) Invoke — call the external reviewer (automatic)
From the review folder:
```
codex exec --skip-git-repo-check --sandbox read-only - > REVIEW_r<N>.md <<'PEER_REVIEW_PROMPT'
Read REVIEW_BRIEF.md, then review the enclosed artifact against the brief's
"what to look at". Report findings with severity (bug / robustness / design / minor)
as markdown. Do not relitigate decisions already made. Do not modify files —
output review text only.
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
Read `REVIEW_r<N>.md` and draft a disposition per finding:
**bug (apply) / robustness (apply recommended) / design (discuss) / reject (reason)**.

### 4) ⛔ Human approval gate (the core of "semi-automatic")
Present the classification and ask the human what to apply. Apply only the approved
items, then test. Blindly applying selective/wrong critique risks regressions.

### 5) Document — record the round in the brief
Add a `## Applied (vN)` table: finding → class → status (applied / pending /
hold / rejected) → path → reason/test. Apply to the live original (the review
folder holds a copy; find the original via `STAGE_MANIFEST.md`). Timestamp the
round header `(vN — YYYY-MM-DD HH:MM:SS TZ)`.

### 6) Loop — termination (explicit)
Stop when ONE holds:
- **Converged:** every bug/robustness finding from the last round is dispositioned
  (applied / hold-with-reason / rejected) and no approved-apply item is unresolved.
- **Cap:** 3 rounds max (cost control). Leftovers → human decides.
- **Human stop:** the user can end any time; stop takes priority over table state.
Record `## Loop closed` (converged / cap / stop + leftovers) when done.

## Boundary
This stage is NOT a call wrapper. The model call is made by the `codex`/`gemini`/
`claude` CLI. What docloop adds on top is the **discipline**: stage → brief →
accumulating rounds → human gate → vN record → termination. Swap the tool, keep
the discipline.
