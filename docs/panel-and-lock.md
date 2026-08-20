# Role-panel review & prediction lock

한국어: [panel-and-lock.ko.md](panel-and-lock.ko.md)

> Moved from README (2026-07-22).

Two extra tools for a draft. Use **`panel`** when you want the draft looked at from several job
angles at once — a PM, a designer, front-end, back-end, QA — each reviewing on its own, then
summed up without any angle getting voted away. Use **`lock`** / **`verify`** when you want to
pin down a prediction *before* the result exists, so "I knew it" can actually be checked later.

How you actually do it:

```bash
docloop review case-x ./PRD_*.md              # stage the draft for review
docloop lock  ~/notes/b1-prediction.md        # optional: seal what you expect the panel to find
docloop panel ~/.docloop/reviews/case-x 1     # 5 default job roles review it, each on its own
docloop verify ~/notes/b1-prediction.md ~/notes/b1-prediction.md.lock.yaml   # reveal: was it untouched? then compare
```

Limitation: the panel roles are AI passes, not human experts — read them as prepared
perspectives, and the decision stays with you. `lock`/`verify` only proves a prediction file was
untouched; it judges nothing on its own (diagnostic-only).

See **Technical details** below for how the roles are kept apart, how the chair combines them,
and how the sealed prediction file works.

## Technical details

Two pieces ported (downstream) from the canonical skill repo — same thesis, new instruments.

**`docloop panel`** — one artifact, several *independent* job-role evaluators (PM · Product Designer ·
Frontend · Backend · QA, or case-specific roles). A role is a **failure-surface contract**
(questions · evidence access · abstain conditions), not a job-title persona. Each role runs as its
**own headless model process**, and role outputs are held outside the review folder until every
role finishes (the prompt additionally forbids reading PANEL_* files) — process separation on one
machine, not an air gap. An Area Chair synthesis then preserves conflicts and lone criticals, never
averages or majority-votes, marks same-model agreement as *correlated* (recorded, no confidence
boost), and hands the human **at most 5 decision items** (role outputs stay as the appendix).

**`docloop lock` / `docloop verify`** — make "I knew it" falsifiable. Hash a prediction file *before*
the outcome exists (digest goes in a **sidecar**, outside the hashed file), re-hash at reveal; a
mismatch means *judge nothing* (diagnostic-only). For third-party verifiability, commit
payload+sidecar before the reveal. Only this primitive is ported — the full learning lifecycle
stays upstream, and judgment stays with the human.

```bash
docloop review case-x ./PRD_*.md                    # stage + brief (reused)
docloop lock  ~/notes/b1-prediction.md              # optional: seal what you expect the panel to find
docloop panel ~/.docloop/reviews/case-x 1           # 5 default roles, per-process isolation
docloop panel ~/.docloop/reviews/case-x 2 pm qa pv-practitioner   # custom role set (contract in the brief)
docloop verify ~/notes/b1-prediction.md ~/notes/b1-prediction.md.lock.yaml   # reveal: intact? then compare
```
