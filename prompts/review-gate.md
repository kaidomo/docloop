# docloop / review-gate — prepared packet protocol

`docloop review-gate prepare` creates a fixed-input review packet. It does not run a
model and does not declare the artifact reviewed, passed, or done.

Run `lens/L1/PROMPT.md`, `lens/L2/PROMPT.md`, and `lens/L3/PROMPT.md` in fresh model
contexts, saving their outputs under `results/`. The directories declare which inputs
each lens should see; they are organizational envelopes, not a filesystem security
boundary or proof of independent agents.

Then follow `handoff/SYNTHESIS.md`, preserving every source candidate as auditable atoms
in `results/INTERMEDIATE.yaml`. Validate the entry ledger, run `review-gate verify-gate` to freeze verification units,
follow `handoff/VERIFICATION.md` in a writer-excluded context, and record the human
disposition, close a separate final ledger, run `review-gate audit-delivery` over the
actual findings and final ledger, and write a packet-bound v2 `results/DONE.md` receipt. Validate that receipt with
`review-gate validate-result`; `review-gate check` still proves preparation only.

Convention profile/intake inputs, when explicitly selected, are validated before any
lens starts. Their preflight record is not evidence that a model ran, and a materialized
docmodel remains a non-authoritative draft until human approval and selection in a new
run. Model detection is probabilistic. Drift records are non-blocking representation
differences, not defects. Only a supplied `terms.yaml` scan is deterministic for
variants listed in that dictionary.

New packets use RUN schema 2 and require `docloop_contract_version: 2`. Frozen front
inputs are replayed; registry state is checked, absent_unassured or present_unchecked.
Unchecked/absent decisions cannot suppress findings. Explicit approved docmodel
candidates may establish structure only when their frozen selection matches intake.
Never infer an approved model from an ambient filesystem scan.

Initial findings/questions may close as `judgment_unavailable` only after the required
independent attempts, with reason, basis and needed input. This returns exit 5
COMPLETE-INDETERMINATE with `document_clearance: indeterminate`; it is not done or
approval. Deferred verification returns exit 3. Unresolved delta verification blocks.
Preserve absence_class and adjacent_anchors; absence alone is a question, not a defect.

After delivery, optional user feedback may be recorded as the user's exact words,
your interpretation, and separately evidenced actions. Feedback is not approval and
does not retroactively alter the receipt or claim a new review took place.
