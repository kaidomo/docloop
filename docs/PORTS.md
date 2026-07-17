# PORTS — per-file upstream provenance (hardening plan D3)

**Canonical upstream ref (pinned, moving)**: `docuauthring` repository, branch `main`
— resolved at run time by `tools/check_ports.py` (maintainer-only tool; requires a
local checkout at `$DOCUAUTHRING_ROOT`, default `~/docuauthring`). Selecting any
other ref cannot green the gate.

Row classes:
- **blob rows** — one or more upstream sources, each with the upstream blob hash
  recorded at the last port. `check_ports.py` fails a row when ① any recorded
  upstream blob differs from the same path at the resolved current ref (upstream
  moved — port may be stale) or ② the recorded downstream hash differs from the
  file in the working tree (downstream edited without updating the row).
- **semantic-port** — prompt-level ports of upstream SKILL.md semantics with no
  1:1 source file; reviewed manually at release (excluded from blob comparison).
- **docloop-native** — no upstream.

Seeded 2026-07-17 against upstream `main` = `6a32ef5a56e986c3e4a1207010cc5ee627776ead`.
The guard hardening in this PR is ported from upstream `shared/path_guards.py`
(secondary source on composite rows).

| downstream | class | upstream source(s) | upstream blob | downstream blob | notes |
|---|---|---|---|---|---|
| lib/split.py | blob | skills/pm-authoring/scripts/split.py | 647eea10e03dddd30738903135f61c32d33d2fd3 | (auto) | composite |
| lib/split.py | blob | shared/path_guards.py | f1d2916ca936a90cadc4fc32af6f0d635845a67c | (auto) | guard contract (secondary) |
| lib/stage.py | blob | skills/peer-review/scripts/stage.py | cf0fe637b0d828cfe1be1c8a9c6666d161c39732 | (auto) | composite |
| lib/stage.py | blob | shared/path_guards.py | f1d2916ca936a90cadc4fc32af6f0d635845a67c | (auto) | containment contract (secondary) |
| lib/init_workspace.py | blob | skills/asistobe-authoring/scripts/init_workspace.py | c64bb35080ca6d49042be9f6894f3a5f0567ea04 | (auto) | |
| lib/validate_manifest.py | blob | skills/pm-authoring/scripts/validate_manifest.py | 838f345e2178d325171454d803284c4476a5dbe1 | (auto) | |
| lib/gap_audit.py | blob | skills/pm-authoring/scripts/gap_audit.py | ac5297c945b0c76e915317a940e9f329bcdbd7d0 | (auto) | |
| lib/ground_audit.py | blob | skills/asistobe-authoring/scripts/ground_audit.py | 575bc1f43bf043cf7d2065b6815f4181e77cd321 | (auto) | |
| lib/approval_brief.py | blob | skills/pm-authoring/scripts/approval_brief.py | 62ba54c413742ac97ce9e0916b37e708b5dde56c | (auto) | |
| lib/score_report.py | blob | skills/pm-authoring/scripts/score_report.py | c35cb3d17784dc86b4a17efe6613d02d4acabc98 | (auto) | |
| lib/verbatim_check.py | blob | skills/pm-authoring/scripts/verbatim_check.py | adb02d273cf0d33d53eeb6712b2ceaaeb9f169ee | (auto) | |
| lib/multi_lens_review.sh | blob | skills/peer-review/scripts/multi_lens_review.sh | 7524d8ff19172ca0caa38e9e2794f05c3fed1e95 | (auto) | |
| lib/blind_lock.py | semantic-port | meta-learning-loop (prediction lock) | - | - | v0.7.0 port |
| lib/panel_review.sh | semantic-port | cross-functional-review | - | - | v0.7.0 port |
| prompts/atb-*.md | semantic-port | asistobe-authoring SKILL.md | - | - | change-plan mode |
| prompts/{plan,draft,gap-audit,review}.md | semantic-port | pm-authoring SKILL.md | - | - | |
| bin/docloop · tests/ · templates/ · docs/ | docloop-native | - | - | - | |

`(auto)` downstream hashes are computed live by `check_ports.py` (recorded here
only when a row is intentionally frozen). Release checklist: run
`python3 tools/check_ports.py` before tagging; stale rows must be re-ported or
annotated as intentional divergence.

## Appendix — public-repo leak-scan spec (hardening plan D4)

- Scope: all tracked + staged + non-ignored candidate files (including this file).
- Command: `git grep -inE "<private-token-classes>" $(git ls-files)` where the token
  classes are: org/product identifiers of the maintainer's employer, personal
  absolute paths (`/Users/<user>`), private workspace names, credential patterns
  (`AKIA`, `ghp_`, `-----BEGIN`). The concrete token list lives in the PRIVATE
  upstream repo only — never commit it here.
- Exit semantics: any hit = nonzero = release blocker.
- Canary proof at release: add a temp file containing one private token, confirm
  the scan fails, remove it.
