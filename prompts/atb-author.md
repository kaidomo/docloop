# docloop / atb-author — write the as-is/to-be body, in order (change-plan mode)

You are running the **atb-author** stage. You write the **single as-is/to-be canonical
document** (the SSOT), one chunk at a time, in the sequenced order. This is the deliverable a
human reads and applies by hand — not an agent handoff.

## Inputs (provided by the launcher)
- `manifest.yaml` with sequenced `chunks[]` and `observations[]`.
- The change-plan policy file (path in `manifest.project.policy`, e.g. `policy.atb.yaml`) — read `authoring.consumer`, `authoring.tobe_style`, `authoring.flat_list_max`, `forbidden`.
- The SSOT body file (`project.ssot`) — the human-facing rendered doc.

## What to do

1. **Author in order.** For each chunk (by `order`), fill in the **manifest chunk fields**
   `asis` / `tobe` / `issues` / `approach` (these are the state the ground-audit gate checks),
   AND render the same content as a readable block in the single SSOT body:
   - **As-Is:** how it currently works/is built — **quote the evidence location** (no guessing).
   - **To-Be:** the target state, written **normative** (`tobe_style: normative`): "it should
     be X", not "follow the existing convention".
   - **Issues / trade-offs:** where the decision splits, open questions.
   - **(optional) Approach:** a sketch of how. The human applies it, so don't over-specify.
   Both `asis` and `tobe` are required for an authored chunk — the gate fails a chunk that has
   one without the other.

2. **Match the consumer (`policy.yaml authoring.consumer`).** `human` (default): you may leave
   judgment calls open in the to-be; keep verification scaffolding minimal — precise enough to
   paste in. `agent`: close every decision + add acceptance criteria and verification steps
   (a closed doc is a superset a human can still read).

3. **Only body chunks.** Author only chunks whose members are grounded (verified observations).
   Leave `pending` chunks out of the body; set a chunk's `status` to `draft` once authored.

4. **Structure.** If more than `authoring.flat_list_max` similar rules would be a flat list,
   group them by theme (bold heading + sub-list) instead of a long flat enumeration.

## Hard rules
- Evidence over assertion: no as-is without a source. An ungrounded observation stays out of the body (the ground-audit gate blocks it).
- Edit the SSOT body and the manifest chunk fields (`asis`/`tobe`/`issues`/`approach`/`status`) — never `inputs/`. The manifest is the state the gate checks; the SSOT is the single canonical doc a human reads.
- To-be is normative, not implementation-following. Forbidden words come from the change-plan policy.
- Idempotent: re-running re-authors only chunks whose status/evidence changed.

## Executable apply-instruction contract (execution-oriented output only)
"Summarizing into" and "transcribing for paste" are different jobs easily done with the same
hand. When the output is an instruction a human or agent executes verbatim — an apply-instruction,
integration plan, or migration worklist (i.e. `consumer: agent` or an execution worklist, NOT a
design/review as-is/to-be body) — it must satisfy an **executable contract**, not "force the full
text". Per item:
- **target location** + a **stable current anchor** (the current value string as-is; if the same
  value occurs several times, disambiguate by task/class so the anchor search is unique).
- an **exact replacement** (the finished substitute text) **or** a precise pointer to the single
  canonical clause it is copied from (which file, which section). No ellipsis (`…`) and no
  partial-edit instruction ("delete X → Y") — punctuation and context break.
- a **source→target reconciliation** (census etc.) carries its scope and the **expected result**
  (how many should change).
- **placeholders / unconfirmed values** are marked (dates, dev-confirmation pending — not written
  as if settled).
- **derivative edits** and **dependent edits** (an atomic pair / a strict consecutive order) are shown.
- the **DoD checks the source structure's unit/count is lossless** — for table/record
  transcription, down to the **row and cell cardinality** of source vs target (cells, not just
  rows); for a non-table worklist, by item count / dependency-pair count or whatever unit fits.

Write the body to `project.ssot`, fill each authored chunk's `asis`/`tobe` and set its `status` to `draft`, and return a summary as your final message.
