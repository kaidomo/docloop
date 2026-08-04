---
doc_review_result:
  schema_version: 1
  route_id: review-gate
  route_trace: legacy direct review
  snapshot_id: sha256:target-snapshot
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
  findings:
  - id: F-01
    status: verified
  unassured_mode: false
---
# Legacy done receipt fixture
