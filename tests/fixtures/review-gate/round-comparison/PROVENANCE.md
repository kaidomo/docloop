# round-comparison fixtures

`upstream-output.md` is what upstream's own generator printed for `prev-round.md` and
`curr-round.md` at `--prev-round 1 --curr-round 2`.

- upstream: `kaidomo/docauth` main `e59c32ff658c74991cf85a53e15347fc085f8c7f` (v0.21.0)
- generator blob: `6c63e07fe1ddc7c8558ba465ec0978a2d12f5362`
  (`skills/review-gate/scripts/match_review_rounds.py`)
- captured: 2026-09-07

The inputs are shaped so that all five verdicts appear in one table (open, closed by
self-report, mixed, unknown, absent) plus a new-candidate row. An earlier capture missed
`불명` entirely, and a mutation probe walked straight through the suite as a result --
the fixture only checks what it exercises.

Why it is committed rather than generated on demand: docauth is private, so a public
contributor cannot run the upstream generator at all. Without this file the equivalence
check would silently disappear in exactly the environments that cannot notice — which is
what it did, until a review round ran the suite against a bogus `DOCUAUTHRING_ROOT` and
got `OK (skipped=1)`.

So the fixture carries the check everywhere, and a second test re-derives it from the live
upstream when a checkout exists. That one may skip: it answers "is this fixture still
current", not "does docloop match upstream".
