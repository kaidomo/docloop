# Review brief — <target>

> Filled by the authoring model. The reviewer reads this first, then reviews the
> enclosed artifact. Reviews and applied-changes **accumulate by round** below
> (never overwrite).

## What it is
<1-3 lines: what this is and its design philosophy>

## How it runs / usage (verified)
<key entry points, commands, behavior. How you ran it to confirm.>

## Design decisions already made (do NOT relitigate — context only)
- <decisions the reviewer should not reopen. e.g. "X is intentionally a human gate">

## What to look at (priority order)
1. <the thing you most want reviewed>
2. <...>

## Input / rule versions (traceability)
- ssot_ref: <the input-SSOT version/commit this review stands on — e.g. repo commit sha, doc vN. In git: `git rev-parse --short HEAD`>
- policy_ref.policy_version: <the policy version this loop checked against; n/a if none>

## Caveats / constraints
- <dependencies / known limits / copy location (apply fixes to the original)>

---
<!-- Round-accumulation zone below. Each header needs an HH:MM:SS timestamp:
     date "+%Y-%m-%d %H:%M:%S %Z". Replace the placeholders with the real
     review/applied content — don't leave them blank. -->
## Review (r1 — YYYY-MM-DD HH:MM:SS TZ)
<reviewer output, or a human-pasted review>

## Applied (v1 — YYYY-MM-DD HH:MM:SS TZ)
| finding_id | lens | finding | class (severity) | status (applied/pending/held/rejected) | path | reason / test |
|------------|------|---------|------------------|----------------------------------------|------|---------------|
| r1-01 | n/a | | | | | |

**Intentionally not applied**: <item + reason>

## Loop closed (YYYY-MM-DD HH:MM:SS TZ)
<!-- Fill on termination (see review.md §6). status is a contract termination.status enum. -->
- status: <converged | round_cap | human_stop | blocked_missing_input | writer_noncompliance | critic_disagreement | rule_conflict | budget_exhausted>
- residual: <finding_ids handed to the human unresolved>
- ssot_ref / policy_ref.policy_version: <re-stated from the Input / rule versions header (audit convenience; not termination fields)>
