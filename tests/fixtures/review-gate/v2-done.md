---
doc_review_result:
  schema_version: 2
  route_id: review-gate
  route_trace: direct independent source inspection
  snapshot_id: sha256:target-snapshot
  target: docs/target.md
  verifiers:
  - verifier_id: V-1
    result: pass
    snapshot_id: sha256:target-snapshot
    evidence: reports/v1.md
  - verifier_id: V-2
    result: pass
    snapshot_id: sha256:target-snapshot
    evidence: reports/v2.md
  - verifier_id: V-3
    result: pass
    snapshot_id: sha256:target-snapshot
    evidence: reports/v3.md
  classification_ledger_ref:
    path: results/INTERMEDIATE.yaml
    sha256: a5acb6df177d3001cf9f3a001c193c99b649205da4c7fca0aa0ec8f0434c6c12
    snapshot_id: sha256:target-snapshot
  findings:
  - record_id: REC-F
    finding_id: F-01
    candidate_atom_refs:
    - ATOM-F
    source_candidate_refs:
    - SC-Q
    snapshot_id: sha256:target-snapshot
    evidence_anchors:
    - L10
    severity: P2
    judgment_provenance: approved convention says the value is required
    status: verified
    public_record_digest: sha256:8845a161ee2e12c2ef2c94d37ba9bde2fee153ac58db18a9601c303a413e7540
  questions:
  - record_id: REC-Q
    status: resolved
    convention_slot: ownership.filters
    dependent_atom_refs:
    - ATOM-Q
    resolution_derived_atom_refs:
    - ATOM-F
    authority: document owner
    scope: document
    source:
      kind: approved_docmodel
      path: frozen/approved-docmodel.yaml
      sha256: f3a31cb49628d2ffcab04431b6a9d3c697f539f4d50b298d1f0b821a74c45d60
    snapshot_id: sha256:target-snapshot
    evidence_anchors:
    - L10
    classification_verification:
      result: pass
      verifier_id: V-Q
      evidence: approved docmodel
    public_record_digest: sha256:d280b03bf5d452816a969956aba6954b13c48881bf7d5f9d61ec9830dc77563d
  drifts:
  - record_id: REC-D
    candidate_atom_refs:
    - ATOM-D
    source_candidate_refs:
    - SC-D
    snapshot_id: sha256:target-snapshot
    evidence_anchors:
    - L20
    - L30
    detail: 같은 상한값의 조사 차이
    variants:
    - notation: 200개
      anchors:
      - L20
    - notation: 200건
      anchors:
      - L30
    co_reference_basis: docmodel ownership[상한값].mirrors_ok
    comparison_ref: ownership:상한값
    public_record_digest: sha256:f3149289c2d85ae8ec5bd21e99e2eef3391d23b7624c6425ba37d2d23e603f6d
  unassured_mode: false
  packet_binding:
    run_id: fixture-run
    target_source: docs/target.md
    target_snapshot: sha256:target-snapshot
    prepared_payload_digest_sha256: '0000000000000000000000000000000000000000000000000000000000000000'
    receipt_path: results/DONE.md
---
# Ledger-bound v2 done receipt fixture

## findings

| id | severity | status |
|---|---|---|
| F-01 | P2 | verified |

## questions

| id | status | authority |
|---|---|---|
| REC-Q | resolved | document owner |

## drifts

- drift_count: 1

| id | detail | variants |
|---|---|---|
| REC-D | 같은 상한값의 조사 차이 | `200개` / `200건` |

## state

- done: true
