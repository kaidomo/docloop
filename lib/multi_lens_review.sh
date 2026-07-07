#!/usr/bin/env bash
# Multi-lens parallel review — review one brief through several lenses at once.
# Runs the lenses in parallel via the model CLI (intent: each lens sees a different defect class).
# ⚠️ Measured (A/B): for small/medium artifacts a single medium pass is enough (medium=high, Δ=0,
#    multi-lens added 0 real findings) — multi-lens only helps, hypothetically, on large/complex
#    artifacts a single medium pass starts to miss. Measure before adopting. (No overselling.)
#
# Usage: multi_lens_review.sh <review-folder> <roundN(number)> [lenses...]
#   <review-folder> must contain REVIEW_BRIEF.md + the staged target (output of stage.py).
#   Default lenses: correctness compliance completeness adversarial
#   Lens names: [A-Za-z0-9_-] only. Output: REVIEW_r<N>_<lens>.md per lens (read-only, effort=high).
#   Merge: lenses see different defect classes — agreement = higher confidence, a single-lens find can still be real (don't drop it).
#
# Env:
#   CODEX_EFFORT  reasoning effort (default high — this is an escalation tool. For small/mechanical work prefer a single medium pass, not multi-lens)
#   CODEX_MODEL   model override (default: the codex config model)
#   FORCE=1       allow overwriting existing REVIEW_r<N>_<lens>.md (refused by default)
#   DRY_RUN=1     print the execution plan instead of calling the model (smoke test)
set -uo pipefail

usage() { sed -n '2,20p' "$0"; exit "${1:-0}"; }
{ [ "${1:-}" = "-h" ] || [ "${1:-}" = "--help" ]; } && usage 0

DIR="${1:?review-folder path required (use -h for help)}"
N="${2:?round number required (numeric)}"
shift 2 || true
LENSES=("$@")
[ ${#LENSES[@]} -eq 0 ] && LENSES=(correctness compliance completeness adversarial)
EFFORT="${CODEX_EFFORT:-high}"
DRY_RUN="${DRY_RUN:-0}"
FORCE="${FORCE:-0}"

# Input validation — these go into filenames, so block injection (path escape / clobber)
case "$N" in ''|*[!0-9]*) echo "round N must be numeric: '$N'" >&2; exit 2;; esac
for L in "${LENSES[@]}"; do
  case "$L" in ''|*[!A-Za-z0-9_-]*) echo "lens name must be [A-Za-z0-9_-]: '$L'" >&2; exit 2;; esac
done

cd "$DIR" || { echo "cd failed: $DIR" >&2; exit 1; }
[ -f REVIEW_BRIEF.md ] || { echo "REVIEW_BRIEF.md not found: $DIR (run stage.py first)" >&2; exit 1; }

# Per-lens focus — view the same artifact through different blind spots.
lens_focus() {
  case "$1" in
    correctness)  echo "Focus on correctness, logic defects, evidence (SSOT) mismatches, and omissions." ;;
    compliance)   echo "Focus on compliance, traceability, missing permissions/states/exceptions (when applicable). For a regulated-domain artifact, include GxP." ;;
    regulatory)   echo "Focus on regulatory/GxP, traceability, missing permissions/states/exceptions (regulated-domain opt-in only)." ;;
    completeness) echo "Completeness — MECE (scope in/out non-overlapping + nothing missing), CRUD (data operations complete), permissions (role×operation, states, exceptions)." ;;
    adversarial)  echo "Adversarial — try to refute this artifact's core claims. When uncertain, default to 'defect present'." ;;
    *)            echo "Focus: $1." ;;
  esac
}

# Capture: use -o/--output-last-message if available (avoids stdout pollution), else redirect stdout (fallback)
OUT_FLAG=""
codex exec --help 2>/dev/null | grep -q -- '--output-last-message' && OUT_FLAG=1

MODEL_ARG=(); MODEL_DESC=""
if [ -n "${CODEX_MODEL:-}" ]; then MODEL_ARG=(-m "$CODEX_MODEL"); MODEL_DESC=" -m $CODEX_MODEL"; fi

DRY_LABEL=""; [ "$DRY_RUN" = "1" ] && DRY_LABEL=" [DRY_RUN]"
echo "multi-lens review: ${#LENSES[@]} lenses in parallel (effort=$EFFORT${MODEL_DESC})${DRY_LABEL}"

# Clobber protection — preserve existing round outputs/evidence (bypass with FORCE=1)
if [ "$DRY_RUN" != "1" ] && [ "$FORCE" != "1" ]; then
  for L in "${LENSES[@]}"; do
    [ -e "REVIEW_r${N}_${L}.md" ] && { echo "already exists: REVIEW_r${N}_${L}.md (use FORCE=1 or a new N)" >&2; exit 3; }
  done
fi

trap 'kill $(jobs -p) 2>/dev/null' INT TERM

run_lens() {
  L="$1"; OUT="REVIEW_r${N}_${L}.md"
  PROMPT="Read REVIEW_BRIEF.md and review the enclosed artifact. Lens: $(lens_focus "$L")
Prefix every finding with a finding_id — format r${N}-${L}-<nn> (nn = a 2-digit serial within this lens review, e.g. r${N}-${L}-01). Write each finding as a markdown bullet in this order: finding_id · severity (bug / robustness / design / trivial) · location (file · section · line) · claim · suggested_fix (if any). location and claim are REQUIRED. Do not relitigate the 'decisions already made' in the brief. Do not modify files — output review text only."
  if [ "$DRY_RUN" = "1" ]; then
    echo "  [dry] $L -> $OUT : codex exec --skip-git-repo-check --sandbox read-only -c model_reasoning_effort=$EFFORT${MODEL_DESC}"
    return 0
  fi
  if [ -n "$OUT_FLAG" ]; then
    codex exec --skip-git-repo-check --sandbox read-only -c model_reasoning_effort="$EFFORT" ${MODEL_ARG[@]+"${MODEL_ARG[@]}"} -o "$OUT" "$PROMPT" >"$OUT.log" 2>&1
  else
    codex exec --skip-git-repo-check --sandbox read-only -c model_reasoning_effort="$EFFORT" ${MODEL_ARG[@]+"${MODEL_ARG[@]}"} "$PROMPT" >"$OUT" 2>"$OUT.err"
  fi
}

PIDS=(); LNAMES=()
for L in "${LENSES[@]}"; do
  run_lens "$L" & PIDS+=($!); LNAMES+=("$L")
done

if [ "$DRY_RUN" = "1" ]; then wait; echo "dry-run done (${#LENSES[@]} lenses)."; exit 0; fi

# Collect per-lens exit codes — don't hide failures (any failure => non-zero)
fail=0; failed=""; i=0
for pid in "${PIDS[@]}"; do
  if wait "$pid"; then echo "  ✓ ${LNAMES[$i]} -> REVIEW_r${N}_${LNAMES[$i]}.md"
  else fail=1; failed="$failed ${LNAMES[$i]}"; echo "  ✗ ${LNAMES[$i]} failed (-> REVIEW_r${N}_${LNAMES[$i]}.md.log/.err)"; fi
  i=$((i+1))
done
if [ "$fail" = "1" ]; then
  echo "some lenses failed:$failed — output incomplete. Re-run (FORCE=1) before triage." >&2
  exit 4
fi
echo "done. Merge REVIEW_r${N}_*.md -> triage. Lenses see *intentionally* different defect classes — agreement = higher confidence, a single-lens find can still be real (don't drop it)."
