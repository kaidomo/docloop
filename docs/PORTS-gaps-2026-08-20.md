# review-gate re-port gaps — 2026-08-20

Context: `docs/PORTS.md` review-gate/docmodel rows were re-ported against upstream
`docauth` (formerly `docuauthring`) `main` = `d6d5e7f64362945d6be2f84f21743c87aa527985`
(2026-08-19). All 13 tracked rows are current as of this pass, plus 3 new rows
(`validate_input_gate.py`, `validate_docmodel_approvals.py`,
`docmodel-approvals-schema.yaml`) and a docloop-native `runner.py prepare` integration
that did not exist before this pass. This file records what is genuinely still open.

## Resolved this pass (no longer gaps)

Earlier drafts of this file listed `validate_review_intermediate.py`,
`validate_review_result.py`, and `front_gate.py` as deferred/blocked. All three are now
fully re-ported and tested (152 review-gate tests + full 362-test canonical suite green):

- **`front_gate_ref` / input gate wiring** (#196/#202④/#206/#228②③): `front_gate.py`
  still has no public CLI (deliberate, matches PORTS.md's original "internal-only, no
  public execution trace" design), but `runner.py`'s `prepare` now runs
  `FrontGateTrace` internally — recording the CONTRACT §1 input gate
  (`--editing-state`, `--target-maturity`, `--open-items-ledger`,
  `--prior-round-output`/`--prior-round-no`) and freezing the digest-bound trace to
  `deterministic/FRONT_GATE_TRACE.json`. `deterministic/RECEIPT_SCAFFOLD.json` gives
  the receipt author a copy-ready `input_gate`/`front_gate_ref`/`round_context`.
  `FrontGateTrace.preflight()` requires convention intake+profile data structurally;
  callers who don't supply `--convention-profile`/`--convention-intake` get an
  internally-synthesized, always-inapplicable placeholder pair (`runner.py`'s
  `_synthetic_convention_profile`/`_synthetic_convention_intake`) so the flags stay
  optional without forcing convention checking on everyone.
- **`structure_axis`** (#233), **`execution`** (#238), **`scale_disclosure`** (#229②),
  **`revision_during_run`** (#228①), **`round_context`** (#208 제안3) are all ported
  into `validate_review_result.py` and required on every v2 receipt.
- **`open_item_classification`** (#206): registered open items can classify findings
  but never serve as suppression authority; ported and tested.
- **§7 deferred verification** (#196): `editing_state: in_progress`/`unknown` now
  correctly defers to `DEFERRED_MESSAGE`/exit 3 instead of demanding 3 verifiers.
- **`docmodel-approvals` gate** (docauth#242): `validate_docmodel_approvals.py` +
  `docmodel-approvals-schema.yaml` ported; `_validate_authority_ref`'s
  `approved_docmodel` branch now requires `approval_id` against an independent
  registry, matching upstream exactly.
- **`legacy_field_errors`/`LegacyFieldReport`** rename (was `validate_legacy` →
  plain list): ported. A `schema_version: 1` receipt can never validate as done
  through `validate()` any more — `legacy_field_errors()` is a field-completeness
  check only, never a verdict.

## Still open

### `match_review_rounds.py` (§13 round-comparison generator) — not ported

`_validate_round_context` (ported, active) correctly REQUIRES `round_context.comparison_ref`
to point at a file starting with `# 라운드 대조 —` whenever `input_gate.prior_round.exists`
is `true`, and validates it byte-for-byte if supplied. What is **not** ported is the
upstream tool that *produces* that comparison file. Practical effect: docloop fully
supports **first-round** reviews (`prior_round.exists: false`, `round_context.round_label:
r1`) end-to-end — this is what `runner.py prepare` and every current test exercise. A
**second round** on the same target is structurally supported (`--prior-round-output`/
`--prior-round-no` flow through correctly) but the comparison-table file itself has to be
hand-authored to match the expected signature/format until `match_review_rounds.py` is
ported. Not silently dropped: the validator enforces the requirement either way.

### `#162` (기획서-v1 template docmodel) — deliberately not ported

Unchanged from the prior pass: user-specific content, correctly out of scope for a
public generic tool. See the 2026-08-20 conversation record — user confirmed this
should stay out of docloop's public core.

### 8 pre-existing `check_ports.py` failures — unrelated, not investigated

`skills/pm-authoring/scripts/*` and `skills/asistobe-authoring/scripts/*` "missing
upstream source" failures are a separate docauth-side path reorganization, unrelated
to review-gate/docmodel. Not touched in either pass.

### Upstream continues to move

`docauth` main advanced to `1b95a95` (2026-08-20, after this pass's `d6d5e7f` pin) while
this port was in progress. Not re-synced in this pass — the newer commits were not
inspected for review-gate relevance. Next re-port should diff `d6d5e7f..1b95a95` first.
