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

### docauth#290 — 3 findings, verified as upstream's own current behavior, filed upstream

Codex round-2 review of this PR's `_validate_front_gate_binding`/`validate_docmodel_approvals.py`
raised 3 findings. Checked directly against docauth `main` (d6d5e7f) before dispositioning —
all three are docauth's own existing behavior, not regressions from this port:

1. **`open_items`/`classified_record_ids` not bound to the front-gate trace** — a receipt
   can rewrite `input_gate.open_items.ledger_ref` to `none` or edit `classified_record_ids`
   after the gate recorded it; `_validate_front_gate_binding`'s `bound` tuple in
   `validate_review_result.py` doesn't cover this field.
2. **`prior_round.output_ref.path`/`.sha256` not verified** — only `round_no` is bound;
   whether the named prior-round output actually exists or hashes correctly is unchecked.
   (docauth's own comment already flags this as known-unclosed scope.)
3. **TOCTOU window in `validate_docmodel_approvals.py`** — `relative_to()` → `stat()` →
   later `read_bytes()` leaves a swap window; symlink/hardlink checks exist but don't close
   the stat-to-read race.

Filed as **docauth#290** (kaidomo/docauth) rather than fixed unilaterally in docloop, per
this project's own principle (logic changes go to docauth first, then re-port — fixing only
in docloop would silently diverge from the canonical source). Full disposition record:
`~/claude_codex_review/docloop-review-gate-2026-08-20-r2/REVIEW_BRIEF.md` (r1-01/r1-02/r1-03).

**Update (2026-08-20, same day): fixed in docauth, re-ported here.** User decided not to
hold this PR: fix docauth#290 immediately (docauth PR
https://github.com/kaidomo/docauth/pull/292, branch
`fix/review-gate-290-trace-binding-toctou`, still **unmerged** — user merges docauth PRs
themselves) and re-port the same fix into this branch in the same session, rather than
merging #42 first and re-porting later. All 3 findings above are closed:

1. `open_items.ledger_ref` added to `_validate_front_gate_binding`'s `bound` tuple.
2. `input_gate.prior_round.output_ref.path`/`.sha256` now resolved and hash-verified
   against the real file.
3. `validate_docmodel_approvals.py`'s `docmodel_path` read is now fd-anchored
   (`_read_verified_docmodel`, `O_NOFOLLOW|O_NONBLOCK`, single `fstat`+`read`).

A Codex peer review of the docauth fix itself (before its PR was opened) then found the
new #2 read used a plain `read_bytes()`, reintroducing the exact FIFO-open-hang bug fix
#3 had just closed — carried into this port too, as `_read_packet_file_bytes` in
`lib/review_gate/validate_review_result.py` (symmetric with
`validate_docmodel_approvals.py`'s `_read_verified_docmodel`). New tests here:
`test_open_items_ledger_ref_binding_rejects_rewritten_declaration`,
`test_prior_round_output_ref_must_point_at_real_matching_bytes`,
`test_prior_round_output_ref_fifo_is_rejected_instead_of_hanging`,
`test_docmodel_approval_hard_link_and_fifo_rejected` (all in `tests/test_review_gate_v2.py`).

**`docs/PORTS.md` provenance — resolved.** docauth PR #291 and #292 merged to `main`
(`d012910`, 2026-08-20). `check_ports.py` re-run clean for both rows (0 STALE — only the
8 pre-existing, unrelated `pm-authoring`/`asistobe-authoring` failures remain).
`docs/PORTS.md`'s two rows were updated with the re-synced blob hashes (recomputed via
`git hash-object`/`git rev-parse`, not copied from truncated `check_ports.py` output).
