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
- **An external model as a stand-in oracle** — the `review` stage has *a different
  model* (Codex, Gemini, another Claude) critique the draft. It isn't a compiler,
  but it is an independent check outside the authoring model, which is most of the
  value a real oracle provides.

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
- **Evidence is truth.** A claim with no source doesn't enter the body — it becomes
  an open question (undecided) or a gap (evidence disagrees).
- **The variable layer is a file.** Section order, glossary, tone, and Definition
  of Done live in `policy.yaml`, never in the engine — so the same harness serves
  any org.
- **Semi-automatic by construction.** Model calls and capture are automatic;
  *applying* a critique is a human gate. A wrong critique applied blindly is a
  regression, and there's no test to catch it.

The summary: **convergence where there's a check, a human where there isn't.**
