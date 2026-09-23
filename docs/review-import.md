# Import review evidence into a change plan

```bash
docloop atb-import-review PACKET results/DONE.md --manifest manifest.yaml --live current.md
# Or explicitly declare unknown freshness: --no-live
```

The existing manifest must validate. The command checks prepared integrity and the
complete receipt, accepting done (0) or complete-indeterminate (5). Thin, invalid,
deferred and legacy field-only receipts are rejected before either output changes.
Paths inside the packet are read-only; output aliases, symlinks and parent traversal
are rejected. `--contract FILE` selects a severity mapping (default
`templates/review-import.yaml`); `--report FILE` changes the report destination.
`--dry-run` writes neither the manifest nor report.

Verified findings become `RG-<finding-id>` observations. Unavailable judgments become
pending issues, never verified changes; rejected findings and drift are not imported
as observations. The report explains exclusions. Source identity is the canonical
review folder and target, stable across run IDs and snapshots. A conflicting ID from
another source fails without changes.

With `--live`, changed anchored lines require revalidation; an unchanged line shifted
by an insertion keeps its evidence. `--no-live` downgrades every observation. Authoring
must not use `needs_revalidation`/`thin_source` observations until reread/revalidated.

Repeated imports preserve user-edited fields using `review_ref.imported` and durable
`human_overrides`. An overridden/withdrawn observation can only be downgraded, never
automatically restored. To explicitly return ownership, a human removes the override/
withdrawn markers and restores the relevant fields to their `imported` baseline.
Missing findings in a later round are reported, not automatically withdrawn.

The manifest is committed atomically after validation. Exit 1 means invalid input or
an IO failure; exit 2 means usage/identity collision. **Exit 6 means the manifest was
committed but the report failed**; repair the report destination and rerun idempotently.
A report is not a transaction rollback. Human disposition remains separate from import.
