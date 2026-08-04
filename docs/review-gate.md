# Review-gate packet workflow

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

Every provenance reference used by decisions, terms, or a docmodel is frozen before
validation. Validation and term scanning run only against the frozen files, never
against inputs that can drift during packet construction.

## Prepare a packet

```bash
docloop review-gate prepare ~/.docloop/reviews/case-submission rg-20260803-01 PRD.md \
  --decisions decisions.yaml \
  --terms terms.yaml \
  --no-docmodel
```

For an explicit unassured run without optional sidecars:

```bash
docloop review-gate prepare ~/.docloop/reviews/case-submission rg-20260803-02 PRD.md \
  --unassured \
  --no-terms \
  --no-docmodel
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
    provenance/<typed-id>
  lens/L1/{PROMPT.md,TARGET.md}
  lens/L2/{PROMPT.md,TARGET.md,DECISIONS.yaml|UNASSURED.md}
  lens/L3/{PROMPT.md,TARGET.md,AXES.md[,DOCMODEL.yaml]}
  deterministic/DECISIONS_VALIDATION.txt  # assured mode only
  deterministic/TERM_SCAN_RAW.md   # exact upstream scanner output, when selected
  deterministic/TERM_SCAN.md       # two-digit-minimum anchor adapter used by audit
  deterministic/CONVENTION_PREFLIGHT.json  # readiness only; no lens execution
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
   `results/DONE.md` receipt. Validate it against the prepared packet. The review is
   not done while any finding is outside
   `verified|rejected`, verification is absent or unresolved, or an unassured run
   lacks explicit human acceptance of the missing decision history.

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
receipt path, ledger bytes, immutable records, and public record set. Packet-relative
paths must be normalized POSIX regular files below the non-Git packet root. This proves
internal consistency, not authorship or resistance to a coordinated same-UID rewrite.

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

The deterministic ledger/receipt and generic convention-preflight contracts are
supported. Second-document/docmodel generalization remains deferred. No transferability,
completeness, or model-independence guarantee is implied.
