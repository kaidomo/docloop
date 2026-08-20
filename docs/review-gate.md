# Review-gate packet workflow

한국어: [review-gate.ko.md](review-gate.ko.md)

`docloop review-gate` is an explicitly invoked packet-preparation workflow for a
document already staged by `docloop review`. It freezes exactly one target and its
selected inputs, runs the deterministic preflight checks, and writes prompts for
three review lenses, synthesis, anchor auditing, independent verification, and the
final human decision.

It does **not** run a model, edit the target, apply findings, or declare a review
complete. If you do not invoke `review-gate`, existing commands and their prompts,
arguments, artifacts, and behavior are unchanged.

## Prepare the inputs

All `prepare` input paths are relative to one review folder. The named review folder
itself must be a real directory rather than a symlink; access is then anchored to its
canonical directory descriptor. Inputs and referenced provenance must be regular files
inside that folder; absolute paths,
symlinks, and paths that escape it are rejected. The target must be exactly one regular
UTF-8 file.

Choose each input explicitly:

- Decisions: pass `--decisions decisions.yaml` for assured mode, or `--unassured`
  when no valid decision history exists. An invalid, stale, or hash-unverified
  registry aborts preparation; it never silently falls back to unassured mode. In
  unassured mode no finding may be suppressed as previously decided, and a human
  must explicitly accept the missing decision history before completion.
- Axes: pass `--axes axes.md`, or omit it to use the shipped planning-document
  checklist.
- Terms: pass `--terms terms.yaml` to require the deterministic term scan, or
  `--no-terms`. If a conventional `terms.yaml` exists next to the target,
  `--no-terms` is rejected so the dictionary cannot be silently skipped.
- Document model: pass `--docmodel docmodel.yaml` to include a human-approved
  structural declaration in L3, or `--no-docmodel`. If a conventional
  `docmodel.yaml` exists next to the target, `--no-docmodel` is rejected. Preparation
  checks basic YAML, approval, provenance, and hashes, but does not claim full schema
  validation or generalization to other document types.
- Convention preflight: optionally pass both `--convention-profile profile.yaml` and
  `--convention-intake intake.yaml`. The pair must cover the profile exactly once and
  declare `phase: pre_lens`. `target_snapshot` always binds to the actual target hash.
  An answered document-scoped record requires `target_document`, and any present
  `target_document` must match the selected target source. Missing, partial,
  duplicate-key, stale, or mismatched input fails before a run directory is reserved.
  Approved answers may later be materialized
  only as a non-authoritative draft; they are not suppression authority.
- Input gate (CONTRACT §1, required): pass `--editing-state {frozen,in_progress,unknown}`
  and `--target-maturity {complete,draft,unknown}`. `frozen`/`complete` is the common
  "reading a finished, unchanging document" case. `in_progress` or `unknown` editing
  state defers the final done verification (§7) — the review can still run, but a
  receipt built from it can only reach a `DEFERRED` intermediate result, never `done`.
  `draft` or `unknown` target maturity requires `--open-items-ledger FILE` — the
  document's own registered open-item ledger, frozen alongside the other sidecars. A
  registered open item can later mark ("classify") a finding in the receipt; it can
  never suppress one.
- Prior round (CONTRACT §1 ⑨, optional): if this run follows an earlier round on the
  same target, pass `--prior-round-output FILE --prior-round-no N` naming that round's
  output and number. Omit both for a first round. See "Multi-round reviews" below for
  what a second round still needs manually.

Every provenance reference used by decisions, terms, a docmodel, or the open-items
ledger is frozen before validation. Validation and term scanning run only against the
frozen files, never against inputs that can drift during packet construction.

Preparation also records the input gate above and starts all three lenses through an
internal `FrontGateTrace`, freezing the result to
`deterministic/FRONT_GATE_TRACE.json` — see "The front-gate trace" below. This is the
only place that trace can be produced; there is no separate public command for it.

## Prepare a packet

```bash
docloop review-gate prepare ~/.docloop/reviews/case-submission rg-20260803-01 PRD.md \
  --decisions decisions.yaml \
  --terms terms.yaml \
  --no-docmodel \
  --editing-state frozen --target-maturity complete
```

For an explicit unassured run without optional sidecars:

```bash
docloop review-gate prepare ~/.docloop/reviews/case-submission rg-20260803-02 PRD.md \
  --unassured \
  --no-terms \
  --no-docmodel \
  --editing-state frozen --target-maturity complete
```

For a target that is still being drafted, with its open-item ledger:

```bash
docloop review-gate prepare ~/.docloop/reviews/case-submission rg-20260803-03 PRD.md \
  --unassured --no-terms --no-docmodel \
  --editing-state in_progress --target-maturity draft \
  --open-items-ledger open-items.yaml
```

To enable convention preflight, add both convention options. Preparation freezes both
files and writes `deterministic/CONVENTION_PREFLIGHT.json` with `phase: pre_lens`; it
does not emit `lens_started`, run a model, or add a newly materialized draft to L3.

Run IDs are append-only. Preparation reserves
`<review-folder>/review-gate/<run-id>/` once and never overwrites or reuses it.
A failure after reservation leaves `INCOMPLETE.json` and the partial run as
diagnostic evidence. Do not consume or delete it as though it were prepared; choose
a new run ID after correcting the input.

After preparation, verify the packet before handing it to reviewers:

```bash
docloop review-gate check \
  ~/.docloop/reviews/case-submission/review-gate/rg-20260803-01
```

`check` fails if state markers, run ID/state, the prepared payload inventory/digest,
or the mutable results tree are missing or unsafe. It ignores the contents of regular
files under `results/`, which are expected to grow during manual review. A successful
check proves prepared-packet integrity only; it does not mean the review passed or is
done.

## Packet contents

```text
<review-folder>/review-gate/<run-id>/
  COMPLETE.json
  RUN.yaml
  frozen/
    target.txt
    target.numbered.md
    decisions.yaml                 # assured mode only
    axes.md
    terms.yaml                     # when selected
    docmodel.yaml                  # when selected
    convention-profile.yaml       # when selected as a pair
    convention-intake.yaml        # when selected as a pair
    open-items.yaml                # when --open-items-ledger is given
    prior-round-output.md          # when --prior-round-output is given
    provenance/<typed-id>
  lens/L1/{PROMPT.md,TARGET.md}
  lens/L2/{PROMPT.md,TARGET.md,DECISIONS.yaml|UNASSURED.md}
  lens/L3/{PROMPT.md,TARGET.md,AXES.md[,DOCMODEL.yaml]}
  deterministic/DECISIONS_VALIDATION.txt  # assured mode only
  deterministic/TERM_SCAN_RAW.md   # exact upstream scanner output, when selected
  deterministic/TERM_SCAN.md       # two-digit-minimum anchor adapter used by audit
  deterministic/CONVENTION_PREFLIGHT.json  # readiness only; no lens execution
  deterministic/FRONT_GATE_TRACE.json  # digest-bound input-gate + lens-start trace
  deterministic/RECEIPT_SCAFFOLD.json  # copy-ready input_gate/front_gate_ref/round_context
  handoff/{SYNTHESIS.md,ANCHOR_AUDIT.md,VERIFICATION.md,HUMAN_DECISION.md}
  results/README.md
```

During construction the run also contains `INCOMPLETE.json`. A packet is prepared
only when `COMPLETE.json` is a regular file, `INCOMPLETE.json` is absent, and the
payload inventory and digest in `COMPLETE.json` match every prepared input, prompt,
audit, and handoff file. Both state markers and the mutable `results/` prefix are
excluded from that digest.
`RUN.yaml` records the frozen input hashes, upstream contract provenance, assured or
unassured mode, lens visibility matrix, deterministic scan digest, and explicit
non-guarantees. Prepared means only that packet construction completed; it does not
mean `passed`, `verified`, or `done`.

`results/` is the append-only output area for the manual review. Its files are excluded
from the prepared-input digest so adding model and human results does not invalidate the
packet. Never overwrite an earlier attempt; add a numeric suffix as directed by
`results/README.md`. `check` rejects symlinks and non-regular entries there, but cannot
detect that a regular result file was overwritten; append-only history remains an
operator obligation.

The three lens envelopes intentionally differ:

| Lens | Files visible in its packet | Review purpose |
| --- | --- | --- |
| L1 | Frozen target only | Cold-read discovery |
| L2 | Frozen target + validated decisions, or the unassured notice | Previous-decision and reflag checks |
| L3 | Frozen target + axes + optional docmodel | Cross-section and structural sweep |

These directories declare what you should provide to each model invocation. They are
not a security boundary and cannot prevent a process from reading parent or sibling
paths.

`target.numbered.md` uses `L01` through `L09`, then `L10` and upward. The upstream
anchor auditor deliberately ignores one-digit `L1`/`L2`/`L3` tokens because they can
mean lens names, so the packet keeps the exact scanner output in `TERM_SCAN_RAW.md` and
uses the equivalent two-digit-minimum anchors in `TERM_SCAN.md` for synthesis/auditing.

## The front-gate trace

`prepare` records the CONTRACT §1 input gate and starts every lens through an internal
`FrontGateTrace` object — the same ordering guard upstream calls `review_front_gate.py`,
except docloop never exposes it as a separate command; it only runs inside `prepare`.
The resulting event sequence (`convention_intake_validated` or
`convention_profile_not_applicable`, then `input_gate_recorded`, then three
`lens_started` events) is frozen to `deterministic/FRONT_GATE_TRACE.json` and hash-bound.
A done receipt's `front_gate_ref` must name this exact file by path and sha256 — a
receipt cannot independently redeclare `editing_state`/`target_maturity` as something
more convenient than what the gate actually recorded before any lens ran.

If you didn't pass `--convention-profile`/`--convention-intake`, `prepare` uses an
internal, always-inapplicable placeholder profile to satisfy the trace's technical
requirement — you don't need to know it exists. The trace correctly emits
`convention_profile_not_applicable`, and the receipt's `structure_axis` must then be
`undetermined` with a `structure_axis_reason` (see `deterministic/RECEIPT_SCAFFOLD.json`,
which already has this filled in).

`deterministic/RECEIPT_SCAFFOLD.json` has the exact `input_gate`, `front_gate_ref`, and
`round_context` fields the final `DONE.md` receipt needs — copy them in verbatim rather
than retyping the hashes by hand.

## The docmodel-approvals registry

An `approved_docmodel` authority reference (in a decision's `source`, or a suppressed
finding's `authority_ref`) no longer points at the docmodel file's own self-declared
`meta.approval_state: approved`. It instead points — by path and sha256 — at an
independent **docmodel-approvals registry** file, and names one `approval_id` entry
inside it:

```yaml
meta:
  target: <what this registry approves docmodels for>
  updated_at: "2026-08-20"
approvals:
  - id: APR-example-01
    docmodel_path: frozen/docmodel.yaml   # packet-relative
    docmodel_sha256: <sha256 of that file's current bytes>
    status: approved                       # approved | revoked
    approved_by: <approver>
    approved_at: "2026-08-20"
    evidence: <where the approval actually happened — a review comment, a meeting note>
```

```yaml
authority_ref:
  kind: approved_docmodel
  path: frozen/docmodel-approvals.yaml
  sha256: <sha256 of the registry file's bytes>
  approval_id: APR-example-01
```

Validation re-hashes `docmodel_path` against `docmodel_sha256` every time — if the
docmodel changes after approval, that entry goes stale and the authority reference
fails closed until it is re-approved. A docmodel file can no longer claim its own
approval; the registry is the only source of truth.

## Multi-round reviews

A second round on the same target (`--prior-round-output`/`--prior-round-no` at
`prepare` time) is structurally supported end-to-end, but the tool that generates the
round-comparison table (`match_review_rounds.py`) is not yet ported to docloop. The
receipt's `round_context.comparison_ref` must point at a file that starts with
`# 라운드 대조 —` and hashes to what it claims; for now, that file has to be produced by
hand or with an external tool in that exact format. First rounds (the common case —
`prior_round.exists: false`, `round_context.round_label: r1`) need none of this.

## Complete the review manually

The packet is provider-neutral. Follow the generated handoff files rather than
treating preparation as the review:

1. Run each of `lens/L1/PROMPT.md`, `lens/L2/PROMPT.md`, and
   `lens/L3/PROMPT.md` in a separate fresh context with only that lens directory's
   declared inputs. Save outputs under the append-only names described by
   `results/README.md`.
2. Run `handoff/SYNTHESIS.md` in another fresh context. Preserve every source
   candidate as one or more auditable atoms in `results/INTERMEDIATE.yaml`, with each
   atom assigned exactly one terminal outcome. Each canonical finding must
   include all six required fields: finding ID; verbatim evidence and location;
   contrary-text search result; decision-registry comparison; severity with its
   decision path; and lifecycle state.
3. Validate the ledger, then run the ledger-aware anchor audit before using the
   synthesis. Missing source, atom, ledger-row, or terminal-record anchors fail
   closed. Repair in the same synthesis context at most twice; after two failed
   repairs, record the run as failed and refer it to a human.
4. A human accepts or rejects each finding. Accepted findings move through
   `discovered → accepted → planned → applied → verified`; rejected findings end at
   `rejected`. Dispositions are append-only.
5. Verify each applied finding with a fresh-context kill attempt. Record
   `pass`, `kill`, or blocking `unresolved`. P1 findings require three reviewers;
   other findings require one. Where three reviews are required, unanimous pass is
   pass, unanimous kill rejects an individual finding, and any mixture is blocking
   `unresolved`; there is no majority vote. The final done-preflight also requires
   three, and any kill or unresolved result there returns to synthesis.
6. Follow `handoff/HUMAN_DECISION.md`, append the human record, and create the v2
   `results/DONE.md` receipt. Copy `input_gate`, `front_gate_ref`, and `round_context`
   from `deterministic/RECEIPT_SCAFFOLD.json`, and add `structure_axis` (+
   `structure_axis_reason` if undetermined), `execution` (confirmed lens-round count
   and why), and `scale_disclosure` (the pre-execution scale you disclosed, itemized).
   Validate it against the prepared packet. The review is
   not done while any finding is outside
   `verified|rejected`, verification is absent or unresolved, an unassured run lacks
   explicit human acceptance of the missing decision history, or `editing_state` was
   `in_progress`/`unknown` at prepare time (that receipt can only reach `DEFERRED`,
   never `done` — re-run `prepare` once the target is frozen).

Severity is a review claim, not an ordering guarantee. Read every finding, including
P3 and findings seen by only one run. The protocol prescribes three done verifiers
and three reviewers for P1 findings, but does not claim that those counts are
empirically justified or sufficient for complete detection.

## Deterministic tools

The deterministic tools can also be invoked directly. Legacy commands preserve their
behavior
and exit-code semantics:

```text
docloop review-gate validate-decisions <decisions.yaml> [--skip-hash]
docloop review-gate validate-intermediate <run-folder> <ledger-relative-path> [--closed]
docloop review-gate validate-result <run-folder> <receipt-relative-path>
docloop review-gate validate-convention-profile <profile.yaml>
docloop review-gate validate-convention-intake <intake.yaml> --profile <profile.yaml>
docloop review-gate materialize-docmodel <intake.yaml> --profile <profile.yaml> [--output FILE]
docloop review-gate scan-terms <terms.yaml> <target>
docloop review-gate audit-anchors <synthesis> [--ledger LEDGER --packet-root ROOT] \
  [--lens [LENS ...]] [--l2 L2] \
  [--scan SCAN] [--extra-re EXTRA_RE] [--id-re ID_RE]
```

`validate-intermediate` enforces candidate/atom coverage, terminal uniqueness, anchor
lineage, drift shape, and question authority. A drift is a co-referential equal-value
representation difference and is non-blocking; it is not a defect. Candidate-dependent
questions block closure until authority and reverse lineage resolve them.

`validate-result` first validates the real prepared packet, then validates a v1 or v2
receipt. V2 binds the exact run ID, target source and snapshot, prepared payload digest,
receipt path, ledger bytes, immutable records, and public record set — plus the input
gate, the digest-bound front-gate trace, structure axis, execution disclosure, scale
disclosure, and round context described above. Packet-relative
paths must be normalized POSIX regular files below the non-Git packet root. This proves
internal consistency, not authorship or resistance to a coordinated same-UID rewrite.
A `schema_version: 1` receipt can never validate as done any more — it predates the
input gate entirely; inspect an already-closed one with `--legacy` (field-completeness
only, never a done verdict) instead.

Convention validation is data-driven and document-type neutral. Materialization consumes
only `approved_to_draft` answers, refuses identity conflicts, and creates a no-clobber
draft with `approval_state: draft` and `suppression_eligible: false`. A human must approve
and explicitly select it in a new run.

`validate-decisions` and `scan-terms` are fail-closed. `audit-anchors` detects lost
line/source anchors; it does not determine whether a finding is correct or whether
the lenses missed a defect.

## Guarantees and boundaries

Packet preparation guarantees, for accepted local inputs:

- one frozen target snapshot and frozen copies of selected sidecars and provenance;
- validation before suppression and deterministic term scanning when selected;
- exclusive, no-overwrite run reservation with a non-consumable incomplete state;
- a committed inventory and digest for the prepared inputs, prompts, audits, and
  handoffs (excluding state markers and append-only `results/`); and
- fixed L1/L2/L3 input envelopes plus explicit manual handoff instructions.

It does not guarantee filesystem isolation, independent agents, reviewer expertise,
complete defect detection, correct severity, trustworthy confidence ordering, a
justified number of repeated runs, or a correct final document. The term scan is
deterministic only for relationships encoded in the supplied dictionary. Human
review, disposition, and verification remain mandatory.

The deterministic ledger/receipt, generic convention-preflight, input-gate/front-gate
trace, and docmodel-approvals registry contracts are supported. Per-template docmodel
generalization (a template-specific structure-declaration package, beyond the generic
schema) and the §13 round-comparison generator (`match_review_rounds.py`) remain
deferred — see `docs/PORTS-gaps-2026-08-20.md`. No transferability,
completeness, or model-independence guarantee is implied.
