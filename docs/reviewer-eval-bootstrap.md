# Reviewer-eval bootstrap — starting without a gold set and converging toward gold

한국어: [reviewer-eval-bootstrap.ko.md](reviewer-eval-bootstrap.ko.md)

Related decision: docloop `docs/design.md` H-01 (reviewer quality = **blocking-recall + a precision floor**, measured against veteran-PM gold, eval-time only). This is the bootstrap plan for resolving that decision's **open item (no gold set yet)**.

## Core insight — the seed isn't zero, we've already been producing it (though it isn't gold yet)

The definition of a "veteran PM judgment" = **a human disposition on each finding, for a specific document.** That disposition byproduct has already been produced by every review:

- `TRIAGE_r*.md` / REVIEW_BRIEF triage in the review work folder (local, outside the repo) — a human disposes each finding as `apply/reject/invalid/defer` (e.g., the 6 items from this round's docloop-direction r1).
- `REVIEW_r*.md` (r1–r3) + gap reports in the docloop repo.
- Audit artifacts such as pm-authoring's `_gap_report.md`, asistobe's `_ground_report.md`, and so on.

→ Each dispositioned finding = **one label** (a human judgment of "this is valid / invalid / blocking"). This is an **adjudicated seed we've already paid the cost for.** However, it's a seed, not gold — it is not promoted to an A/B baseline until it passes the "gold eligibility conditions" below.

## Why the seed isn't gold yet — the missing recall denominator

A triage disposition only shows whether the finding the reviewer **submitted** was valid. Because that record is **conditional on the candidate findings**, there is no guarantee that the full set of blocking findings in that document has been enumerated. Therefore:

- The seed can yield false-positive labels (`reject`/`invalid`).
- But **the denominator for blocking-recall (the complete set of blocking findings per document) cannot be built from the seed.** Any category of finding the reviewer never spotted in the first place is also absent from the seed.

### Gold eligibility conditions (requirements for promoting seed → gold)

To include a document in the frozen A/B baseline (gold), it must satisfy **all** of the following:
1. **Independent veteran full-document review** — a veteran PM reviews the document start to finish, independently of the candidate review.
2. **Blocking inventory** — every blocking finding in that document is enumerated and rated for severity (the complete set = the recall denominator).
3. **Duplicate adjudication complete** — merged with the existing triage seed, with duplicates judged and removed.

Only documents that satisfy these three conditions are included in the frozen A/B set. Records for documents that don't meet them remain `seed`-only and are not used for recall judgments.

## Phases

### Phase 0 — mine existing triage (now, free) → adjudicated seed (not gold)
A script scans the folders above → normalizes them into `(artifact_id, finding, human_disposition, blocking?)` records → **`evals/seed/adjudicated-findings.jsonl`** (both the storage location and the name are `seed/adjudicated-findings`, never `gold`).
- `reject`/`invalid` = a label for a reviewer false positive.
- A finding **added** by a human (H-xx, something the reviewer missed) = a false-negative signal. ← Use this only as a partial signal for blocking-recall — **it's not used as the recall denominator, since it isn't a full-document blocking inventory** (see "Gold eligibility conditions" above).

### Phase 1 — expand silver (deploy the harvested lens set)
For document areas where the triage history is thin, we run the **consolidated lens set harvested from the pm-* skills** against a corpus (your past R2/docloop documents). However, we constrain the status of the auto-generated output as follows.

- **Automatic silver is limited to the `[a]` structural-check subchecks of the lens set** (existence, enum, counts, etc.).
- **The output of `[b]` judgment-type lenses is stored as `candidate_unadjudicated`, not as a silver label** — this avoids duplicating the generator's judgment as a ground-truth label. These candidates become labels only after passing through human adjudication.
- Corpus: your actual past PRDs/planning documents (the most realistic) plus a small number of public example PRDs.
- **Report the `auto-covered / human-covered / uncovered` ratio separately for each dimension** (across the lens set's 10 dimensions) — surfacing the coverage gap numerically rather than merely acknowledging it.

#### Provenance separation (blocking the silver feedback loop)
Simply "excluding silver as the top priority" still leaves an indirect leak (improving the reviewer/lens/prompt/threshold by looking at silver results). We attach a **provenance tag, per document, to the corpus and to every label**, and isolate them into three categories:

- `development-silver` — automatic lens output. **Diagnostics only**: used only for error analysis and learning. **Never used** for candidate (reviewer/prompt/lens) selection, **threshold tuning, or the final A/B report.**
- `development-gold` — human labels obtained during development (exposed during lens/reviewer improvement). Used in development, but excluded from the final baseline.
- `hidden-gold` — hidden veteran gold for documents that were **not used** in lens authoring or reviewer improvement. Used **exactly once, for the final A/B judgment only.**

### Phase 2 — scorer (implementing the H-01 metrics)
`eval_review.py`: matches findings from the candidate reviewer's output against the labels →
- **blocking-recall** (the share of veteran blocking labels the reviewer caught) = top priority.
- **precision** = computed per the "asymmetric precision contract" below, needs to clear only a pre-registered floor.

#### Finding match rules
"Location + claim" is a list of features, not a matching rule (recall swings widely with paraphrasing, location shifts, split/merge, and duplicates). We **pre-register** the following:
- **Match unit = defect proposition + target object/scope + violated requirement + impact** (judged by comparing these four elements, not by text similarity).
- **One-to-one matching by default.**
- Pre-register handling rules for **split** (one gold item broken into multiple findings), **merge** (multiple gold items combined into one finding), **partial capture**, **duplicates**, and **location mismatch**.
- Borderline cases are judged through a **blind adjudication** procedure (the procedure itself is also pre-registered).

#### Asymmetric precision contract
Under a policy that doesn't penalize a valid finding that falls outside the label set, an unmatched candidate cannot automatically be treated as a false positive (and if left neutral, hallucinated findings also disappear from the denominator, defeating the floor). Therefore:
- Every unmatched candidate goes through **blinded human adjudication** → classified as `valid-new / duplicate / invalid / unassessable`.
- **Precision is computed including adjudicated `invalid`s** (valid-new is not a penalty; it's neutral-to-positive).
- When only **automatic evaluation** is run, without human adjudication, that metric is named separately as **"known-FP challenge-set rejection rate"** rather than precision (the rejection rate on a pre-collected known-FP challenge set — a separate metric that measures an upper bound on hallucination).

#### Pre-registering the precision floor
The floor is not chosen favorably after seeing the results. It is **fixed before execution**:
- **Value**: derived from the current reviewer or the veteran baseline, and an acceptable cost of reviewing invalid findings.
- **Aggregation unit**: **per-document macro precision** (a single point estimate is prohibited).
- **Small-sample handling**: minimum document count, minimum blocker count, and using the **lower bound** of the confidence interval.
- **Tie/fail rules**: specify the rules for ties and borderline judgments.

### Phase 3 — A/B gate
Only **`hidden-gold` for documents that satisfy the gold eligibility conditions (above)** is locked in as the frozen baseline (seed, silver, and development-gold are excluded). Every time the review lens/prompt/rubric changes, rescore → did blocking-recall go up, and is precision above the **pre-registered floor**? → This is the "pre-registered A/B" that the peer-review skill requires.

## Honest limitations (to avoid overclaiming)
- **Selection bias**: the Phase 0 seed covers only "documents you reviewed," and the disposition is **your** judgment, not a veteran's. The scale is also small (low N). → the seed is not gold (see the eligibility conditions above).
- **Generator bias**: labeled findings skew toward "what the reviewer happened to notice at the time" → categories the reviewer never spotted in the first place are also absent from the labels (which is why findings a human **adds** are especially valuable).
- **Silver feedback loop**: blocked by provenance separation — silver is diagnostics-only, and the final judgment uses `hidden-gold` that was never used for lens/reviewer improvement, exactly once (see provenance separation above).
- Bottom line: this is not a "validated gold set" but a **bootstrap seed (adjudicated seed)**. It gets upgraded incrementally by filling in held-out sets with real veteran labels (a small amount from an external PM). design.md's "not yet operational" status remains valid until hidden-gold that satisfies the gold eligibility conditions is attached.

## What can be done right now vs. later
- **Now (no gold set needed)**: the Phase 0 mining script, the Phase 2 scorer (including pre-registering the match rules and the precision contract), and **improving** the reviewer using the `[a]` structural checks of the harvested lens set (independent of scoring).
- **Later (needs humans)**: obtaining held-out veteran labels that satisfy the gold eligibility conditions → promoting `candidate_unadjudicated` to gold via human adjudication → formally running the A/B.
