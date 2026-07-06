# docloop / atb-chunk — group observations into chunks and sequence them (change-plan mode)

You are running the **atb-chunk** stage. This is where the change-plan mode earns its keep:
you cluster scattered observations into **cohesive chunks** and assign a **resolution order**.
This is the automation of the daily "so how do I actually fix this?" step. You do NOT write
the as-is/to-be body here — you produce `chunks[]`.

## Inputs (provided by the launcher)
- `manifest.yaml` with `observations[]` (from atb-capture).
- The change-plan policy file (path in `manifest.project.policy`, e.g. `policy.atb.yaml`) — read `sequencing.blast_radius`, `sequencing.weights`.

## What to do

1. **Cluster.** Group observations that share a code area, a user flow, or a root cause into
   one `chunks[]` entry — the unit you'd touch together. Each chunk:
   `{id, title, members[] (observation ids), order, order_rationale, status: pending}`.

2. **Sequence.** Assign `order` by these criteria (weights from `policy.yaml`):
   1. **Dependency** (if A is a prerequisite of B, A first) — always wins, regardless of blast radius.
   2. **Blast radius** — `policy.yaml sequencing.blast_radius`: `high_risk_first` (tackle the
      big/uncertain first to remove uncertainty early — the default) or `small_isolated_first`
      (small isolated changes first for rhythm).
   3. Risk. 4. Effort.

3. **`order_rationale` is mandatory (one line per chunk).** State *why this order* — dependency,
   risk, blast, effort. Without it, a chunk list is just a list. The ground-audit `--strict`
   gate blocks chunks with no `order_rationale`.

4. **Hand back.** Output the manifest plus a summary: chunk count, the ordered list with
   one-line rationales.

## Hard rules
- Every chunk needs a non-empty `order_rationale`.
- Every member must reference an existing observation id (no dangling references).
- A dependency ordering overrides the blast-radius heuristic — record that in the rationale when it applies.
- Sequencing rules come from `policy.yaml`. Never hardcode the direction.

Write the manifest to the work-folder root. Return the ordered summary as your final message.
