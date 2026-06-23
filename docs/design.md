# Design: a writing harness has no oracle

## The coding-harness pattern, and why it works

A wave of thin "coding harnesses" wrap a model CLI in a loop: generate a change,
run the checks, feed failures back, repeat until green. They are deliberately
small — no new runtime, no bespoke agent — because they don't need to be. The
intelligence is the model; the harness is just the loop.

The reason that loop *converges* is easy to miss: **code has an oracle.** The
compiler and the test suite are outside the model, and they are not fooled by
confident prose. When the harness says "done," something objective agrees.

## Writing breaks the pattern

Point the same loop at a PRD and it falls apart. There is no compiler for a spec.
A "draft → self-review → revise" loop has only the model judging the model, so it
converges on whatever the model is already confident about. Fluent, internally
consistent, and possibly wrong.

So the naive port — "lazy-PRD, just keep looping" — produces polished documents
with no guarantee they're *true*. The hard part of PM writing was never prose
generation. It's that the claims have to match reality (the code, the decisions,
the other documents) and the open questions have to stay visibly open.

More precisely: writing has no *single* oracle for the finished document. Some
claims can be checked against something outside the model; judgment cannot.
docloop is built around that seam.

## docloop's split

Instead of pretending an oracle exists, docloop separates the document into the
part that can be made convergent and the part that can't.

**Convergent (drive with loops + real checks):**

- **Factual accuracy** — labels, enums, required fields, behavior are checked
  against the source of truth (the code/schema), not asserted from memory.
- **Consistency** — `gap-audit` fans out one sub-agent per source class to find
  where the document contradicts itself or the downstream artifacts, and records
  each contradiction with a concrete location. Scripted gates (`gap_audit.py
  --strict`) refuse to pass while unresolved gaps or open questions remain.
- **An external model as independent pressure** — the `review` stage has *a
  different model* (Codex, Gemini, another Claude) attack the draft. This is not an
  oracle, and the essay won't pretend it is: a second LLM shares much of the first's
  training and blind spots, so the two can be confidently wrong together. It's an
  *attention* test, not a *truth* test — it surfaces the unsupported claims,
  contradictions, and leaps an author-model can't see in itself, and breaks the
  self-confidence loop. A correlated second opinion, not ground truth — but the
  cheapest external pressure available when no oracle exists.

**Not convergent (keep with the human):**

- **Voice and judgment** stay outside the loop. The harness does not polish style
  in a loop, because there's nothing to converge against.
- **Decisions and approvals** are never auto-confirmed. The harness's job is to
  *surface* what's undecided or contradictory and **stop** — handing a gap report
  and an open-questions list to a human. Manufacturing consensus is the one failure
  mode worse than a missing section.

## Consequences in the design

- **Manifest as state, not the document.** `manifest.yaml` tracks each section's
  status, sources, gaps, and the decision log. Re-running is idempotent: only
  sections with new evidence change. The body is the SSOT; split pages and briefs
  are derivatives, always regenerated.
- **Evidence over assertion.** A claim with no source doesn't enter the body — it
  becomes an open question (undecided) or a gap (evidence disagrees). But evidence
  isn't *truth*: the code, schema, and decision logs are what the org has committed
  to, not a guarantee it's right. When sources conflict, docloop surfaces the
  conflict; it doesn't adjudicate which reality wins.
- **The variable layer is a file.** Section order, glossary, tone, and Definition
  of Done live in `policy.yaml`, never in the engine — so the same harness serves
  any org.
- **Semi-automatic by construction.** Staging and the model invocation are
  scripted; *applying* a critique is a human gate. A wrong critique applied blindly
  is a regression, and there's no test to catch it.

## When not to use it

docloop's convergent leg assumes a source of truth already exists — code, a
schema, prior decisions — for its claims to be checked against. That makes it a
tool for **writing that describes something**: PRDs for a system being built,
manuals, specs. Its value scales less with the *amount* of source than with its
**authority, freshness, and coverage** — one signed-off decision log is worth more
than a pile of stale, conflicting docs.

Point it at **generative writing** — a vision, a net-new strategy, an essay
arguing a position no one has taken yet — and there is no prior source to check
the load-bearing claims against. The document *is* the source. Here the factual
oracle doesn't merely weaken; for those claims it isn't there at all. docloop
won't break — every unsourced claim it *detects* falls to an open question (it can
only route the claims it manages to extract) — but it will hand back a scaffold of
undecideds, which is to say it tells you nothing you didn't already know. The
bottleneck there is human judgment, which docloop deliberately refuses to automate.

So: reach for docloop when the risk is **drift** — the document quietly
disagreeing with the reality it documents. It earns less on a **blank page**,
where there's nothing to converge against — though even a net-new effort with real
inputs (interviews, decision logs, prior art) can still use it to inventory claims
and surface contradictions. It just can't invent the answer.

## What docloop does *not* give you

One thing to be honest about up front: docloop converges a document onto a
**chosen set of sources**, not onto the truth. If that set is wrong, stale, or
biased — code that encodes a bad decision, a decision log that reality has already
overtaken — docloop will faithfully produce a cleaner, more *confident* wrong
document. It shrinks the distance between your document and its sources; keeping
those sources authoritative and current is a human's job, and the most
consequential one. Grounding was never "every claim has a citation." It's "every
claim's source is one you'd still stand behind today."

The summary: **convergence where there's a check, a human where there isn't.**
