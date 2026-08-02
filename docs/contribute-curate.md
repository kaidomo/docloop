# Optional contribution and curation flow

`contribute`, `curate`, and `draft-curated` form an explicitly opt-in branch for
collecting several named perspectives before drafting. Nothing in this branch runs
unless you call it. The existing `docloop plan` and `docloop draft` commands do not
discover contribution or curation bundles, and their prompts, arguments, manifest,
and SSOT behavior are unchanged.

Use this branch when you want traceable suggestions from selected job perspectives,
then want a person to decide what those suggestions mean for the draft.

## Workflow

Run these commands from an initialized docloop work folder:

```bash
docloop plan "PRD for the case submission flow"

docloop contribute cc-20260803-01 pm qa backend

cp work/contributions/cc-20260803-01/payload/human-response.template.yaml \
  work/contribution-responses/cc-20260803-01.yaml

# Edit the copied response. Add any supplemental files under inputs/.

docloop curate cc-20260803-01 cur-20260803-01 \
  work/contribution-responses/cc-20260803-01.yaml

docloop draft-curated cur-20260803-01
```

Each command is a separate action:

1. `contribute <run-id> <perspective> <perspective> [...]` captures the current
   manifest, policy, and inputs, then invokes the configured model separately for
   each selected perspective. Specify two to five perspectives from `pm`,
   `product-designer`, `frontend`, `backend`, and `qa`. There is no hidden default
   list.
2. Copy `human-response.template.yaml`; do not edit or pass the generated template
   itself. In the copy, a person records one disposition for every contribution,
   adds decisions and material references where required, and explicitly sets the
   attestation fields.
3. `curate <source-run-id> <curation-id> <human-response.yaml>` validates the
   completed contribution, response, and supplemental materials. It deterministically
   creates drafting notes and an open-question list. It does not call a model or
   modify `manifest.yaml`, the body SSOT, or section approvals.
4. `draft-curated <curation-id>` validates the completed curation and its digests,
   then passes the exact `draft-notes.md` bytes to the normal drafting model in a
   marked optional-input block. It accepts no extra notes argument.

`contribute` does not run `curate`, and `curate` does not run a draft. Calling
`draft-curated` is the explicit instruction to draft with the curated input.
Calling plain `docloop draft`, even when bundles exist, remains the original flow.
Passing a path as free text to plain `draft [notes...]` also remains possible, but
that path does not validate a bundle or provide the `draft-curated` guarantees.

Run and curation IDs must match `[a-z0-9][a-z0-9-]{0,63}`. IDs are append-only:
reuse is rejected, and retrying an incomplete or failed operation requires a new ID.
`docloop init` does not add optional-flow directories; the first `contribute` call
creates `work/contributions/` and `work/contribution-responses/` as needed.

## Human response contract

The response begins as a copy of the generated template. Its important fields are:

```yaml
schema_version: 1
source_run_id: cc-20260803-01
source_index_sha256: "<copied from the template>"
operator_attested: true
attested_by: "operator label"
attested_at: "2026-08-03T00:00:00Z"
materials:
  - material_id: submission-policy
    path: inputs/submission-policy.md
dispositions:
  - item_id: cc-20260803-01/backend/01
    status: supported
    group_id: ""
    decision: ""
    material_refs: [submission-policy]
    rationale: ""
```

Every source item must occur exactly once. The statuses mean:

| Status | Required input | Drafting treatment |
|---|---|---|
| `decided` | A non-empty `decision` | Included as drafting evidence |
| `supported` | At least one known `material_id` | Included with material digests |
| `open` | Nothing else | Kept unresolved, never promoted to settled input |
| `carried` | A non-empty `rationale` | Preserved for audit, excluded from drafting evidence |
| `dismissed` | A non-empty `rationale` | Preserved for audit, excluded from drafting evidence |

Blank fields do not imply approval. Optional `group_id` values belong to the
operator: docloop does not semantically merge raw contributions. Grouped items must
agree on status, target section, decision, materials, and rationale.
Open items remain visible in `open-questions.md` and in a labeled
`Unresolved — not drafting evidence` block at the end of `draft-notes.md`; their
presence is not treated as a settled drafting decision.

`operator_attested: true`, `attested_by`, and `attested_at` are explicit
self-attestation fields. They do **not** authenticate a person's identity, prove
authorship, or establish authorization. docloop also does not verify that submitted
decisions or materials are true or current.

Material IDs must match `[a-z][a-z0-9-]{0,63}` and be unique in the response.
Material paths are POSIX paths relative to the work folder and must resolve to
regular, non-symlink files below `inputs/`. Absolute paths, `..`, NUL, paths outside
`inputs/`, symlinks, and special files are rejected. Unreferenced valid materials
are retained and marked `unused: true`; docloop does not silently discard them.

## Artifacts and accepted state

A contribution run reserves its destination before invoking a model:

```text
work/contributions/<run-id>/
├── INCOMPLETE                     # present while building; absent when accepted
├── payload/
│   ├── run.yaml
│   ├── snapshot/
│   │   ├── manifest.yaml
│   │   ├── policy.yaml
│   │   ├── inputs/...
│   │   └── inventory.yaml
│   ├── perspectives/<perspective>.yaml
│   ├── contribution-index.yaml
│   ├── human-response.template.yaml
│   └── diagnostics/...
└── COMPLETE.yaml                  # accepted marker; absent while INCOMPLETE
```

A curation produces:

```text
work/curations/<curation-id>/
├── INCOMPLETE                     # present while building; absent when accepted
├── payload/
│   ├── run.yaml
│   ├── contribution-ref.yaml
│   ├── human-response.yaml
│   ├── supplemental-materials/...
│   ├── materials.yaml
│   ├── curation.yaml
│   ├── draft-notes.md
│   └── open-questions.md
└── COMPLETE.yaml                  # accepted marker; absent while INCOMPLETE
```

`INCOMPLETE` and `COMPLETE.yaml` are mutually exclusive in an accepted bundle.
`COMPLETE.yaml` is written last with exclusive creation. A consumer accepts a
bundle only after validating the marker schema, stage and ID, the complete payload
file inventory, every size and SHA-256 digest, and the aggregate payload digest.
A marker's existence alone is not success.

Interrupted and rejected runs remain in place for diagnosis and still may contain
sensitive data. docloop does not delete or repair them automatically. A stale
`INCOMPLETE` directory is not accepted downstream; retry with a new ID.

## What the flow guarantees

- Each selected perspective gets a separate model invocation and a separate
  artifact with generation-qualified item IDs.
- Every perspective in one contribution run is instructed from the same captured
  bundle bytes. The bundle is assembled over a time interval; it is not an atomic
  point-in-time snapshot of the original tree.
- Raw contributions retain a one-to-one, stable-ID route through the index and
  curation. Only the operator's explicit statuses and optional groups are applied.
- For the same completed contribution and exact response bytes, deterministic
  curation content and `draft-notes.md` bytes are identical across new curation IDs;
  run ID and timestamp metadata can differ.
- Existing run paths and files are not overwritten. Atomic destination reservation
  lets only one same-ID invocation enter; different IDs may run concurrently.
- Downstream commands consume only bundles whose accepted-state marker, inventory,
  and digests validate. `draft-curated` is the only verified bridge into drafting.

The flow does **not** provide filesystem isolation or confidentiality between model
invocations. A captured working directory and, where supported, a read-only CLI
setting are not a security boundary against another same-UID process or other
readable paths. Separate invocations are not independent agents or fresh cognition:
they can use the same model lineage and share correlated blind spots. The output is
not guaranteed to be expert, correct, complete, uncorrelated, or consensus-worthy.

No portable whole-directory atomic transaction is claimed. The implementation
provides exclusive marker creation, full validation before acceptance, and no
clobbering of an existing run. It does not guarantee recovery from `SIGKILL`, power
loss, hostile same-UID modification, network-filesystem behavior, or manual bundle
tampering.

## Limits, privacy, and retention

Before a contribution capture, docloop reports its file count, total bytes, and
destination. Fixed MVP limits are:

| Resource | Limit |
|---|---:|
| Perspectives per contribution | 2–5 |
| Files below `inputs/` | 1,000 |
| One captured manifest, policy, or input file | 10 MiB |
| Captured manifest, policy, and inputs per contribution | 50 MiB total |
| Model stdout per perspective | 1 MiB |
| Items per perspective | 50 |
| Supplemental materials per curation | 50 |
| Supplemental materials per curation | 50 MiB total |
| Human response or generated Markdown/YAML file | 2 MiB each |

An over-limit operation fails before the affected copy, model call, or publication.
There is no silent truncation, override, `FORCE`, or recovery-in-place mode.

Contribution bundles copy the captured inputs once per run. Curation bundles copy
supplemental materials once per curation. Equal supplemental content is deduplicated
within one curation by full SHA-256, but there is no cross-run deduplication or
encryption. Run directories use mode `0700` and files use `0600`; these modes reduce
accidental local exposure but do not exclude backups or block other processes running
as the same user.

The configured model CLI/provider can receive manifest, policy, and input contents.
Provider logging and retention follow that provider's settings and are outside
docloop's control. docloop performs no upload other than through the selected model
CLI, but local bundles can contain source text, model output, operator decisions,
filenames, and digests.

With `DOCLOOP_MODEL=claude`, contribution calls use Claude Code's `--safe-mode`,
`--permission-mode dontAsk`, restricted `Read,Glob,Grep` tool list, and
`--json-schema` structured-output option. Claude Code 2.1.220 is the verified
version; older releases without those flags are not supported for `contribute`.
These options reduce customization and write-capable tool exposure for the call;
they do not create filesystem isolation or prevent other same-user processes from
reading files. For the explicitly requested `draft-curated` write step, Claude uses
non-interactive `acceptEdits` with only `Read`, `Glob`, `Grep`, `Edit`, and `Write`;
the command does not make the Bash tool available. This is a tool-exposure boundary,
not a filesystem sandbox. Plain `plan` and plain `draft` keep their existing model
invocation behavior.

With `DOCLOOP_MODEL=codex`, contribution calls explicitly use the Codex read-only
sandbox setting, while the explicitly requested `draft-curated` step uses the
workspace-write setting so it can reconcile the SSOT and manifest. These are CLI
tool settings, not claims that docloop provides process or filesystem isolation.

Normal console messages contain paths, counts, byte sizes, digests, and reason codes,
not source or model-output bodies. Diagnostics do not save an environment dump, full
command line, or assembled prompt. They can retain up to 64 KiB of provider stderr
per perspective and mark truncation, so treat diagnostics as sensitive too.

There is no automatic retention period or cleanup. Treat complete and incomplete
bundles as sensitive copies. Before deleting a contribution, inspect retained
curations' `payload/contribution-ref.yaml` files; keep the contribution while a
curation that you intend to retain refers to it. Then remove only the exact,
validated run-ID directory you selected. Apply the same exact-path rule to stale
incomplete contributions and curations—never use a wildcard or delete the entire
`work/` tree as cleanup.

For example, after deciding to discard both of these specific bundles, remove the
dependent curation first and then its source contribution:

```bash
rm -rf -- work/curations/cur-20260803-01
rm -rf -- work/contributions/cc-20260803-01
```
