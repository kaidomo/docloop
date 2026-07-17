# docloop hardening & hygiene plan / 보안 가드 포트·위생 계획

- Status: draft — peer review pending (execute only after review converges to 0 findings)
- Scope: port upstream security-guard fixes into `lib/`, add guard behavior tests, establish port-provenance tracking. No workflow or prompt changes.

## 1. Audit findings (2026-07-17)

| Area | Finding | Verdict |
|---|---|---|
| `lib/split.py` write guard | Ported before upstream (docuauthring) hardened its guards. **Known bypass present**: a trailing-slash/`.`-suffixed `output_dir` (e.g. `alias/`) makes `os.path.islink(out_dir)` return false; if the symlink target is a sibling folder containing the generation marker, `rmtree` deletes the target. Upstream fixed this (lexical-normalized `islink` check) plus added marker-name validation (empty/`.`/`..`/separator markers disable the deletion guard). | **Port needed (security)** |
| `lib/validate_manifest.py` | Already has clean error handling (`_read_manifest`: missing file / OSError / YAML syntax → diagnostic messages, no tracebacks) | OK |
| `lib/stage.py` guards (r1-01·r1-05) | Missing upstream's regular-file/directory `lstat` guard (a device/FIFO target can reach `shutil.copy` — resource-exhaustion risk) and calls `os.path.commonpath` without `ValueError` handling (cross-drive traceback after the review dir was created) | **Port needed (security/robustness)** |
| Tests | `tests/run_tests.py` — 151 checks, all passing; covers split/approval_brief/validate/verbatim/score/panel. **No adversarial guard scenarios** (symlink bypass, marker abuse, preservation-on-refusal) | Extend |
| Public-repo hygiene | Leak scan: **0 hits** today. But (r1-04) the initial ".gitignore covers staging artifacts" verdict **overstated**: `REVIEW_BRIEF.md`, `PANEL_r<N>_<role>.yaml`, `PANEL_r<N>_SYNTHESIS.md` and arbitrarily-named staged targets are NOT ignored, and `stage.py --dest` can point inside the worktree — a dated zero-hit scan does not prevent later accidental commits. LICENSE present. | **Harden (D4)** |
| Port provenance | `CHANGELOG` records port events at release granularity, but there is no per-file record of *which upstream revision* each `lib/` file was ported from — drift like the guard gap above is discovered by accident | **Add lightweight tracking** |

## 2. Non-goals

- No workflow/prompt/CLI changes. No repo restructuring (current layout `bin/ lib/ prompts/ templates/ docs/ tests/` is healthy).
- No shared-vendoring machinery across repos — docloop stays a self-contained port; the fix is provenance visibility, not automation.

## 3. Work items (single PR expected; SemVer: patch+minor judgment at release)

**D1 — port upstream guard hardening into `lib/split.py` and `lib/stage.py`** (security)
- `split.py`: lexical-normalized symlink check (`os.path.islink(os.path.normpath(os.path.abspath(out_dir)))` — closes trailing `/`·`/.` bypass) + marker-name validation before any guard logic (non-empty single filename component; reject `''`, `.`, `..`, absolute, separators, NUL).
- `stage.py` (r1-01): regular-file/directory guard on staging targets (`os.lstat` + `stat.S_ISREG`/`S_ISDIR` — reject devices/FIFOs/sockets explicitly instead of relying on incidental `shutil` behavior).
- `stage.py` (r1-05): containment helper with `realpath` + `commonpath` + `ValueError → outside` for overlap/destination checks (no traceback on incomparable paths).
- Keep messages in docloop's English voice (behavior-preserving hardening of rejection paths only).

**D2 — guard behavior tests** (r1-03: exercise the *specific* guard branch — no false passes through unrelated guards)
- Trailing-slash and `/.` symlink variants → rejected, symlink target preserved (assert file survival). Bare-symlink output dir → rejected.
- Unmarked non-empty dir → refused, contents preserved. Marked dir → regenerated. Empty dir → adopted. Foreign marker (another tool's marker file) → refused as unmarked-non-empty.
- Invalid marker values: tested against an **empty** output dir (so no other guard can reject first), asserting the **marker-specific error text** and unchanged state, enumerating every invalid class.
- Boundary cases: `base` itself, nested (not direct child), outside-base paths → each rejected.
- `stage.py`: special-file target (FIFO) → explicit guard branch, no staged copy, cleanup when zero targets accepted; cross-drive/incomparable path → treated as outside, no traceback. **FIFO scenarios run in a subprocess with a timeout** (r2-02 — with the guard removed, `shutil.copy` on an unopened FIFO blocks forever): assert prompt nonzero exit via the special-file branch, guaranteed process/fixture cleanup, and the revert-proof mutation must produce a *bounded* failure (timeout counts as failure, not a hang).
- **Failure-capability proofs (G2), one per fix**: revert lexical normalization → trailing-slash test fails; remove marker validation → invalid-marker test fails; remove special-file guard → FIFO test fails.

**D3 — port provenance record with drift detection** (r1-02: a "ported-from" history table alone cannot surface staleness)
- `docs/PORTS.md`: one row per `lib/` **and** `prompts/` file — public-safe upstream identity, **one or more upstream source records** (r3-01: composite ports like `lib/split.py` = PM split + shared guards, `lib/stage.py` = staging + shared containment contract — each declared source carries its own upstream path + immutable commit + blob hash), downstream blob hash, intentional-divergence notes. Every declared source must match the pinned current ref.
- `tools/check_ports.py` (read-only, release-time) — **freshness gate, not a self-selected ref** (r2-01): the canonical upstream baseline is pinned in `docs/PORTS.md` header as a *moving* ref (upstream default branch); the tool resolves it at run time, records the resolved commit in its report, and compares **both directions**: ① each recorded upstream blob hash vs the same path at the resolved current ref (upstream moved = stale row = failure) ② each recorded downstream hash vs the file in the current release tree (downstream edited without updating the row = failure). Choosing a historical ref cannot green the gate — the ref is pinned in the table header, not a CLI argument.
- Failure fixtures shipped with the tool: changed-upstream, changed-downstream, missing-source, incomplete-coverage, **secondary-source-only change** (r3-01: only a composite port's secondary upstream — e.g. the shared guards file — changes while the primary script blob is untouched; must fail) — each must fail.
- Release checklist gains: run `check_ports.py` before tagging (report includes the resolved upstream commit).

**D4 — hygiene hardening** (r1-04)
- Extend `.gitignore`: `REVIEW_BRIEF.md`, `PANEL_r*_*.yaml`, `PANEL_r*_SYNTHESIS.md` (current review-artifact families not yet covered).
- `stage.py`: emit an **unconditional warning** (no new flag — preserves the settled "no CLI changes" boundary, r2-03) when the review destination resolves inside a Git worktree; behavior is otherwise unchanged. Blocking-by-default would require revising the settled CLI decision and is out of scope.
- Leak-scan spec pinned in `docs/PORTS.md` appendix: file scope = tracked + staged + non-ignored candidates (including generated `docs/PORTS.md`), token set (public/private identifier classes documented), exit semantics (any hit = nonzero), and a **canary failure proof** (temporary file with a private token must make the scan fail, then removed).

## 4. Gates

| # | Gate | Check |
|---|---|---|
| G1 | Existing behavior preserved | `python3 tests/run_tests.py` — all pre-existing checks still pass (guard changes touch rejection paths only) |
| G2 | Guard scenarios + failure capability | All D2 checks pass; **three separate revert proofs** (lexical normalization / marker validation / special-file guard) each make their specific test fail |
| G3 | Provenance complete + fresh | `docs/PORTS.md` covers every `lib/` **and** `prompts/` file (upstream blob hash(es) or explicit "docloop-native"); `tools/check_ports.py` passes against **the canonical moving upstream ref pinned in `docs/PORTS.md`, resolved at run time** (r3-02 — not a self-selected ref), comparing both upstream-current and downstream-release-tree directions |
| G4 | Leak scan | 0 hits with the pinned spec (scope/tokens/exit defined) + canary failure proof executed |

## 5. Risks

| Risk | Mitigation |
|---|---|
| Guard message wording drift vs upstream | Acceptable — docloop owns its English voice; the *logic* is what's ported (PORTS.md records the upstream revision) |
| Future upstream guard fixes missed again | PORTS.md makes staleness visible per file; release checklist gains a "check upstream rows" step |

## 6. Change log

- 2026-07-17 draft (audit-based).
- 2026-07-17 r3 applied (2 residuals): r3-01 multi-source rows for composite ports + secondary-source failure fixture / r3-02 G3 wording pinned to the canonical moving ref (both comparison directions retained).
- 2026-07-17 r2 applied (3 residuals): r2-01 freshness gate pinned to a canonical moving upstream ref with two-directional hash comparison + 4 failure fixtures / r2-02 FIFO tests bounded by subprocess timeout (revert proof must fail bounded, not hang) / r2-03 unconditional warning without a new flag (settled CLI boundary preserved).
- 2026-07-17 r1 applied (5 findings, standing delegation): r1-01 stage.py special-file guard port / r1-02 PORTS.md redesigned with blob hashes + read-only check_ports.py release check (lib+prompts) / r1-03 guard tests target the specific branch (empty-dir marker tests, foreign-marker, boundaries, 3 revert proofs) / r1-04 hygiene hardening (.gitignore families, worktree-dest warning, pinned scan spec + canary) / r1-05 containment helper with ValueError→outside.
