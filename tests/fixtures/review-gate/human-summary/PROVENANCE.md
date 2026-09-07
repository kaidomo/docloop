# human-summary fixtures

`upstream-summary.md` is what upstream's own renderer produced for `receipt.md` and
`target.md`.

- upstream: `kaidomo/docauth` main `e59c32ff658c74991cf85a53e15347fc085f8c7f` (v0.21.0)
- `render_human_summary.py` blob: `b20a12fd9516f825d05a1a24bb6b856cb40da574`
- `summary_projection.py` blob: `58e3f6e04b7ab377e2c7e53fd375bc272316124d`
- captured: 2026-09-07

`receipt.md` is `tests/fixtures/review-gate/v2-done.md` with its symbolic
`sha256:target-snapshot` replaced by the real digest of `target.md`, because the renderer
refuses a receipt that binds to nothing -- an unbound target document is what lets a
fabricated quotation pass.

Committed rather than generated on demand for the same reason as the round-comparison
fixture: docauth is private, so a public contributor cannot run the upstream renderer at
all, and a check that quietly disappears where nobody can see it is not a check.
