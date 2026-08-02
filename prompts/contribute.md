# Contribution perspective pass

Read the captured `snapshot/manifest.yaml`, fixed `snapshot/policy.yaml`,
`snapshot/inventory.yaml`, and any captured `snapshot/inputs/` files. Evaluate
only the explicitly assigned perspective. This is a separate invocation and
artifact using the same captured bundle bytes as the other perspectives; it is
not filesystem isolation and does not establish independent cognition,
correctness, completeness, or consensus.

Identify concrete drafting considerations and return YAML only. Do not write
the document, approve a section, infer a human decision, or edit any file. Each
item must use exactly these keys:

```yaml
schema_version: 1
run_id: <supplied-run-id>
perspective: qa
model_lineage: codex
items:
  - item_id: example-run/qa/01
    consideration: "A concrete consideration"
    target_section: requirements
    evidence_refs: []
    human_need: decision
    human_question: "What must the operator decide?"
```

`evidence_refs` may contain only the exact path strings listed in the invocation
contract, without duplicates. Do not add `snapshot/`, an absolute prefix, or a
line/section suffix. Use an empty list when no captured file directly supports
the consideration. `consideration` and `human_question` must always be non-empty,
including when `human_need` is `none`. `human_need` is one
of `decision`, `material`, `both`, or `none`. Emit at most 50 items, numbered
sequentially from `01`.
