# review-gate re-port gaps — 2026-08-20

Context: `docs/PORTS.md` review-gate rows were re-ported against upstream `docuauthring`
(now `docauth`) `main` = `d6d5e7f64362945d6be2f84f21743c87aa527985` (2026-08-19). 10 of 13
previously-tracked rows are now current. Three are deliberately left stale, and one
upstream capability was found to have no docloop port at all. This file is the
provenance note `docs/PORTS.md` points to for each.

## Deferred: two large adapted files

- `lib/review_gate/validate_review_intermediate.py` — upstream diff since the 2026-08-04
  pin is 345 lines (old 672 → new 939). docloop's local adaptation (packet-root authority
  containment, strict terminal ledger contract) is itself 202 diff lines, in the same
  regions upstream changed. Not re-ported this pass — needs a careful 3-way merge, not a
  bulk copy.
- `lib/review_gate/validate_review_result.py` — upstream diff since the pin is **908
  lines**; the file nearly quadrupled (274 → 1084 lines), reflecting several accumulated
  upstream issues (v2 ledger/receipt validation hardening). docloop's local adaptation is
  264 diff lines. Not re-ported this pass — the size and density of upstream change here
  makes a rushed merge a real correctness risk for a fail-closed validator; needs its own
  slice, ideally reviewed change-by-change against the upstream issue list rather than as
  one bulk diff.

## Blocked: front_gate.py depends on a wholly new upstream module

`lib/review_gate/front_gate.py` (upstream `skills/review-gate/scripts/review_front_gate.py`)
now has a `record_input_gate()` method and a `declares_profile_not_applicable` early-return
branch that depend on a new upstream file with **no docloop counterpart at all**:

- `skills/review-gate/scripts/validate_input_gate.py` (369 lines, docauth-only) — CONTRACT
  §1 input-gate recording: editing_state / target_maturity / archived source-copy byte
  verification (`verify_source_copy_bytes`), covering docauth issues #196 (editing-state
  gating), #206 (open-items classification-only reference), #202 ④ (prior-round context
  binding).

This was not part of the 13-row port-staleness baseline `tools/check_ports.py` reported,
because that tool only flags existing rows going stale — it cannot detect an upstream file
with no downstream row at all. Porting `front_gate.py` correctly requires first deciding
whether/how to port `validate_input_gate.py` (a real new capability: source-copy integrity
+ editing-state gating), which is a separate, larger unit of work than the mechanical
re-ports done in this pass.

## Also noted, not actioned: docmodel-approvals gate (#242)

Separately from the three items above (found earlier in the same investigation): docauth's
`validate_docmodel_approvals.py` + `docmodel-approvals-schema.yaml` (self-approval forgery
prevention for docmodel authority — independent registry + sha256 freshness binding) has
**no PORTS.md row and no docloop file at all**. `templates/review-gate/contracts/review-intermediate-schema.yaml`
was just re-ported to its current form, which already requires an `approval_id` field for
`approved_docmodel`/decided authority kinds (docauth#242) — so the schema now expects a
field that nothing in docloop yet issues or validates. Not a regression (the field was
already required upstream at the pin we're catching up to), but it means an `approved_docmodel`
authority reference in docloop is schema-valid-but-unenforceable until this gate is ported.
This is new port work, not a re-port — it needs its own PORTS.md row.

## Out of scope for this pass

8 pre-existing `missing upstream source` failures in `tools/check_ports.py`
(`skills/pm-authoring/scripts/*`, `skills/asistobe-authoring/scripts/*`) are unrelated to
review-gate/docmodel — a separate docauth-side path reorganization. Not investigated here.
