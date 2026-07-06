# Changelog

All notable changes to docloop are documented here. This project adheres to
[Semantic Versioning](https://semver.org/). A version is tagged on every merge to `main`.

## [0.3.0] — 2026-07-06
### Added
- **Change-plan mode (as-is/to-be)** — a second, delineated pipeline for planning fixes to an
  existing system (vs. writing a fresh doc). Read product/docs/logs/code → capture observations →
  group into ordered chunks → write a single as-is/to-be canonical doc for a human to apply.
  - Stages `atb-capture → atb-chunk → atb-author → atb-audit`, plus `atb-gate`, added as flat
    `bin/docloop` commands (no pipeline-selector abstraction — matches the existing dispatcher).
    Reuses `init` and `review`.
  - `lib/ground_audit.py` — the ground-audit gate: a to-be built on a wrong as-is is the most
    expensive mistake, so `--strict` blocks ungrounded to-be (authored chunk with an unverified
    member), untraceable to-be (no members), missing `order_rationale`, missing as-is, and pending
    chunks. Mirrors gap-audit's honesty guard: `--strict-cross-audit` fails when 0 `project.sources`
    are registered while chunks are authored (as-is is self-assertion only).
  - `validate_manifest`: optional `observations[]` (=issue) and `chunks[]` (=handoff + as-is/to-be,
    with `order`/`order_rationale`) blocks — absent = pass (document mode unaffected), present =
    validated (referential integrity of `members` → `observations`, same idiom as `decision_id`).
    `sections`/`doc_type` "empty" warnings are suppressed when a manifest is in change-plan mode.
  - `templates/manifest.atb.example.yaml` + `templates/policy.atb.example.yaml` (sequencing
    direction, `consumer`, taxonomy). New prompts under `prompts/atb-*.md`.
- **`project.sources` recognizes `docs` and `logs`** (additive) for change-plan grounding.
  Coverage counting stays mode-specific — `gap_audit` counts `code_roots/design/prototypes`
  (document mode's audited classes, unchanged), `ground_audit` counts `code_roots/design/docs/logs`
  — so recognizing a key in the validator never silently changes a mode's cross-blind honesty guard.
### Fixed
- **`validate_manifest`: file/YAML errors now exit cleanly** (a `[abort]` message) instead of a
  raw traceback — shared by both gates.

## [0.2.0] — 2026-06-24
### Added
- **Opt-in flags to fail (not just warn) on a vacuous gate**, for release CI that must not
  pass a check that verified nothing:
  - `verbatim_check.py --strict-verbatim-coverage` — implies `--strict` and also fails when
    nothing was verifiable (0 quotes or 0 readable sources).
  - `score_report.py --strict-scoring-coverage` — implies `--strict` and also fails when
    nothing was scored, or scored sections left configured axes unscored.
  Plain `--strict` is unchanged (still warns only on vacuity); the new flags are additive.
  This completes the family started by gap-audit's `--strict-cross-audit`.

## [0.1.2] — 2026-06-23
Silent-omission hardening — extends the gap-audit honesty guard to the other gate scripts
(a self-audit found the same "passes because nothing was checked" pattern elsewhere).
### Fixed
- **verbatim_check: a missing source target no longer mislabels the matched source.**
  `zip(targets, present-only-texts)` misaligned when an earlier target was missing, so a
  quote could be reported as matching the wrong source. Each label now travels with its own
  normalized text.
### Added
- **verbatim_check: vacuous-pass warning.** With 0 quotes or 0 readable sources, `MISS 0` /
  passing `--strict` means "nothing was checked", not "all quotes match" — now surfaced in
  the report and on stderr (also notes missing declared sources).
- **score_report: vacuous-pass + incomplete-scoring warnings.** Passing `--strict` with 0
  scored sections ("nothing scored"), or scored sections missing axes (an absent axis is
  never counted as below-threshold), are now surfaced in the report and on stderr.
- Existing `--strict` behavior is unchanged (warnings only); these mirror gap-audit's guard.

## [0.1.1] — 2026-06-23
### Fixed
- **gap-audit coverage counts recognized schema keys only.** `_count_paths` used to
  count any key under `project.sources` / `downstream`, so a typo (e.g. `code_root`
  instead of `code_roots`) inflated coverage and suppressed the cross-blind warning —
  a false "clean" signal the honesty guard exists to prevent. It now counts only the
  known keys (`code_roots`/`design`/`prototypes`, `storyboard`/`manual_manifest`/
  `policy_docs`), mirroring `validate_manifest`.

## [0.1.0] — 2026-06-23
Initial public release + same-day design/honesty refinements (baseline tag).
### Added
- Thin writing harness: `bin/docloop` wraps `codex`/`claude -p`; stage prompts
  (plan/draft/gap-audit/review) + lib scripts (validate, gap-audit, split, …).
- **gap-audit cross-audit coverage honesty guard**: surfaces when `gaps: 0` reflects
  internal consistency only (0 sources/downstream registered); opt-in
  `--strict-cross-audit` makes that a release-gate failure.
### Changed
- `docs/design.md`: the review stage is framed as *independent pressure* (an
  attention test, not a stand-in oracle); added "What docloop does not give you"
  (converges on a chosen source set, not the truth). "Evidence over assertion".
- Internal comments/docstrings translated to English.
