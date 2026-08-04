# docloop / review-gate — prepared packet protocol

`docloop review-gate prepare` creates a fixed-input review packet. It does not run a
model and does not declare the artifact reviewed, passed, or done.

Run `lens/L1/PROMPT.md`, `lens/L2/PROMPT.md`, and `lens/L3/PROMPT.md` in fresh model
contexts, saving their outputs under `results/`. The directories declare which inputs
each lens should see; they are organizational envelopes, not a filesystem security
boundary or proof of independent agents.

Then follow `handoff/SYNTHESIS.md`, preserving every source candidate as auditable atoms
in `results/INTERMEDIATE.yaml`. Validate the ledger, run the ledger-aware anchor audit,
follow `handoff/VERIFICATION.md` in a writer-excluded context, and record the human
disposition plus a packet-bound v2 `results/DONE.md` receipt. Validate that receipt with
`review-gate validate-result`; `review-gate check` still proves preparation only.

Convention profile/intake inputs, when explicitly selected, are validated before any
lens starts. Their preflight record is not evidence that a model ran, and a materialized
docmodel remains a non-authoritative draft until human approval and selection in a new
run. Model detection is probabilistic. Drift records are non-blocking representation
differences, not defects. Only a supplied `terms.yaml` scan is deterministic for
variants listed in that dictionary.
