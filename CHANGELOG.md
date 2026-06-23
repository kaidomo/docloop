# Changelog

All notable changes to docloop are documented here. This project adheres to
[Semantic Versioning](https://semver.org/). A version is tagged on every merge to `main`.

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
