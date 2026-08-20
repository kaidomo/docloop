# What's inside today, and direction

한국어: [direction.ko.md](direction.ko.md)

> Moved from README (2026-07-22). The "What's inside" and "Layout" sections at the bottom
> were moved here from README in the same pass.

**Short version:** what docloop ships today is everything the README shows — the writing and
change-plan flows, the review tools, the checks and gates, and your `policy.yaml` rules file.
A few larger ideas are designed but **not built**:
loading document types as plug-in packs, generating one document from another, and grading
the AI reviewer against expert judgment. Read this page as a roadmap, not a feature list —
anything written in "would" below does not exist yet.

This section is design direction, not a feature list. **Current:** the protocol-kernel
boundary and the `policy.yaml` variable layer — the shipped verb set is `init · plan ·
draft · audit · review · review-gate · panel · lock · verify · gate · split · contribute ·
curate · draft-curated` plus the `atb-*` change-plan stages. **Planned,
not shipped:** a domain-pack loader, a derivation-manifest execution path, and the
reviewer-eval gold set. The conditional-tense text below describes where those planned pieces would go.

The target shape is a **shared validation/execution protocol kernel** rather than the single
canonical engine behind a family of specialized authoring skills. The shipped core already has
the shared protocol-kernel boundary. The domain-pack loader and derivation-manifest execution
path described below remain planned. In that target, document *meaning*
(ontology, prompts, derivations) *would* live in domain packs/skills; declarative org rules
already live in `policy.yaml`; the core *would* own only the protocol — the boundary test
being that **core imports no document type**.

Two directions *would* follow. **Derivation** (PRD → storyboard → manual) *would not* be a
core verb — a future domain pack *would* author a *derivation manifest* and the core's
intended role *would be* protocol execution only. And because the **review stage is an
oracle stand-in, it would need grading too**: reviewer quality is **not operational** today,
and the planned metric *would* evaluate it **offline against a veteran-PM gold set**
(blocking-recall, not text similarity) — the gold set does not yet exist.

**Design & rationale**:
[`design.md`](design.md) (protocol kernel) ·
[`reviewer-eval-bootstrap.md`](reviewer-eval-bootstrap.md) (grading the reviewer) ·
[`reviewer-lens-set.md`](reviewer-lens-set.md) (73 review lenses) ·
[`cold-start-strategies.md`](cold-start-strategies.md) (evidence acquisition).

## What's inside

docloop adds **no new runtime and no new agent.** The value is in three things:

(Two terms, once: the **kernel** is the checking layer everything else sits on; a
**manifest** is the work-state file that records what the document promised and what
was checked.)

1. **The checks & gates** (`lib/`) — fan-out audits (model-assisted: gap-audit for
   consistency, ground-audit for evidence grounding) feeding deterministic manifest
   validation, release gates, verbatim comparison, and prediction-file integrity
   (lock/verify; diagnostic-only). Deterministic where applicable; otherwise fail-honest.
2. **The review protocols** — external-model cross-review (`prompts/review.md`: finding
   IDs, triage, a human approval gate, explicit termination states), role-panel review
   (`panel`: separate role runs, Area Chair synthesis, human decision handoff), and the
   explicit `review-gate` packet builder. `review-gate` freezes one target and deterministic
   inputs into fixed L1/L2/L3 envelopes and validates an optional pre-lens convention
   pair before stopping for fresh-context model runs. After synthesis it supports a
   deterministic intermediate ledger and packet-bound receipt for verification and a
   human decision; it is not an automatic reviewer or isolation layer.
3. **The authoring pipelines** (`prompts/`) — the authoring layer is a client of the
   kernel; it currently contains two pipelines: doc mode (plan → draft → audit → review →
   gate → split) and change-plan mode (`atb-*`).

## Layout

```
bin/docloop          thin launcher (wraps codex / claude -p)
prompts/             stage prompts — doc mode: plan/draft/gap-audit/review · change-plan mode: atb-capture/atb-chunk/atb-author/atb-audit
lib/                 python scripts: init, validate, gap_audit, ground_audit, split, approval_brief, stage, ...
templates/           policy + manifest skeletons (doc + .atb change-plan variants), review-brief template
docs/design.md       why documents need a verification kernel (not just a writing loop); design decisions (protocol kernel, reviewer-eval)
docs/review-gate.md  explicit packet-preparation CLI, artifacts, limits, and manual verification contract
docs/reviewer-eval-bootstrap.md   bootstrapping a reviewer-quality gold set from review residue
docs/reviewer-lens-set.md         document-review lenses harvested from PM skills (55 → 73 criteria)
docs/cold-start-strategies.md     initial evidence-acquisition patterns for authoring
```
