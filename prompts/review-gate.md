# docloop / review-gate — prepared packet protocol

`docloop review-gate prepare` creates a fixed-input review packet. It does not run a
model and does not declare the artifact reviewed, passed, or done.

Run `lens/L1/PROMPT.md`, `lens/L2/PROMPT.md`, and `lens/L3/PROMPT.md` in fresh model
contexts, saving their outputs under `results/`. The directories declare which inputs
each lens should see; they are organizational envelopes, not a filesystem security
boundary or proof of independent agents.

Then follow `handoff/SYNTHESIS.md`, run the vendored anchor audit as instructed, follow
`handoff/VERIFICATION.md` in a writer-excluded context, and record the human disposition
using `handoff/HUMAN_DECISION.md`. Model detection is probabilistic. Only a supplied
`terms.yaml` scan is deterministic for variants listed in that dictionary.
