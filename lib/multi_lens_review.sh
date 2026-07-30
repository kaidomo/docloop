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
# N must be a canonical positive integer — '0'/'01' would desync from next_round()'s int() allocation (r1-based)
case "$N" in ''|*[!0-9]*|0*) echo "round N must be a positive integer with no leading zero: '$N'" >&2; exit 2;; esac
SEEN_LENSES=""
for L in "${LENSES[@]}"; do
  case "$L" in ''|*[!A-Za-z0-9_-]*) echo "lens name must be [A-Za-z0-9_-]: '$L'" >&2; exit 2;; esac
  # dup key is case-folded — on case-insensitive filesystems 'foo'/'FOO' resolve to the same output path
  LKEY=$(printf '%s' "$L" | tr '[:upper:]' '[:lower:]')
  case " $SEEN_LENSES " in *" $LKEY "*) echo "duplicate lens name (case-insensitive): '$L' (parallel jobs would clobber the same output)" >&2; exit 2;; esac
  SEEN_LENSES="$SEEN_LENSES $LKEY"
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

# Capture: use -o/--output-last-message if available (avoids stdout pollution), else redirect stdout (fallback).
# ${OUT}.log/.err are harness-owned sidecar evidence — the "output file only" ban in the prompt binds the reviewer model, not this wrapper.
OUT_FLAG=""
codex exec --help 2>/dev/null | grep -q -- '--output-last-message' && OUT_FLAG=1

MODEL_ARG=(); MODEL_DESC=""
if [ -n "${CODEX_MODEL:-}" ]; then MODEL_ARG=(-m "$CODEX_MODEL"); MODEL_DESC=" -m $CODEX_MODEL"; fi

DRY_LABEL=""; [ "$DRY_RUN" = "1" ] && DRY_LABEL=" [DRY_RUN]"
echo "multi-lens review: ${#LENSES[@]} lenses in parallel (effort=$EFFORT${MODEL_DESC})${DRY_LABEL}"

# Clobber protection — preserve existing round outputs/evidence, incl. harness sidecars
# (.log/.err from a failed run are evidence too). FORCE=1 bypasses ONLY the ordinary
# exists-check; a symlinked output path (dangling or not) is rejected unconditionally —
# redirection would follow the link and write outside the review folder.
# Under FORCE=1 the review file is moved aside BEFORE the run rather than overwritten in place:
# the -o capture path does not touch the file when a lens writes nothing, so the previous round's
# content would survive, pass the non-empty check below, and be reported ✓ — the human would then
# triage LAST round's findings as this round's. Stashing rolls back on any guard rejection and on
# INT/TERM/HUP, so a rejected preflight never leaves earlier evidence deleted while no lens runs —
# FORCE=1 buys an overwrite, not evidence loss.
# Scope of that guarantee, stated so it is not overread: it is NOT a filesystem transaction. A
# kill -9, a crash between the mv and its registration, or a failing restore can still leave a
# .forcebak behind. Those are reported and the content is left under its .forcebak name rather
# than dropped, so recovery is a rename.
FORCE_BAK=()
force_restore() {
  frc_left=""
  for b in ${FORCE_BAK[@]+"${FORCE_BAK[@]}"}; do
    mv -f "$b" "${b%.forcebak}" 2>/dev/null || frc_left="$frc_left $b"
  done
  [ -n "$frc_left" ] && echo "WARNING: could not restore stashed output(s):$frc_left — rename them back by hand" >&2
  FORCE_BAK=()
  return 0
}
# Installed BEFORE the first move so an interrupt during the stash window rolls back too.
[ "$DRY_RUN" != "1" ] && [ "$FORCE" = "1" ] && trap 'force_restore; exit 3' INT TERM HUP
if [ "$DRY_RUN" != "1" ]; then
  for L in "${LENSES[@]}"; do
    for f in "REVIEW_r${N}_${L}.md" "REVIEW_r${N}_${L}.md.log" "REVIEW_r${N}_${L}.md.err"; do
      [ -L "$f" ] && { force_restore; echo "refusing: $f is a symlink — outputs must be regular files in the review folder (FORCE does not bypass)" >&2; exit 3; }
      [ "$FORCE" != "1" ] && [ -e "$f" ] && { echo "already exists: $f (use FORCE=1 or a new N)" >&2; exit 3; }
    done
    OUT="REVIEW_r${N}_${L}.md"
    if [ "$FORCE" = "1" ] && [ -e "$OUT" ]; then
      if [ ! -f "$OUT" ]; then
        force_restore
        echo "refusing: existing output is not a regular file: $OUT (not auto-removed; stashed files restored)" >&2; exit 3
      fi
      if [ -e "$OUT.forcebak" ] || ! mv -f "$OUT" "$OUT.forcebak" 2>/dev/null; then
        force_restore
        echo "could not stash existing output: $OUT — stale content could be mistaken for this round; stopping (stashed files restored)" >&2; exit 3
      fi
      FORCE_BAK+=("$OUT.forcebak")
    fi
  done
  for b in ${FORCE_BAK[@]+"${FORCE_BAK[@]}"}; do
    rm -f "$b" || echo "WARNING: leftover stash not removed: $b" >&2
  done   # only once every lens is clear
  FORCE_BAK=()
fi

trap 'kill $(jobs -p) 2>/dev/null' INT TERM

run_lens() {
  L="$1"; OUT="REVIEW_r${N}_${L}.md"
  # Shared prompt-protocol block — ported upstream contract (finding_id grammar, required
  # fields, no-relitigate, write ban outside the output file). Keep semantics aligned
  # when re-porting (see the docs/PORTS.md row for this file).
  # ==BEGIN SHARED-PROMPT-BLOCK==
  PROMPT="Read REVIEW_BRIEF.md and review the enclosed artifact. Lens: $(lens_focus "$L")
The output file is ${OUT}.
Prefix every finding with a finding_id — format r${N}-${L}-<nn> (nn = a 2-digit serial within this lens review, e.g. r${N}-${L}-01). Write each finding as a markdown bullet in this order: finding_id · severity (bug / robustness / design / trivial) · location (file · section · line) · claim · suggested_fix (if any). location and claim are REQUIRED. Do not relitigate the 'decisions already made' in the brief. Do not modify, create, delete, or rename any file other than the ${OUT} review file — output review text only."
  # ==END SHARED-PROMPT-BLOCK==
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

# Per-lens success — don't hide failures (any failure => non-zero).
# An exit code alone does not earn a ✓: a lens can exit 0 and write nothing, and the old check
# then printed a filename that does not exist next to a ✓ and sent the human on to triage. In
# triage that makes "the lens found nothing" indistinguishable from "the lens never ran", while
# the merge rule below (agreement raises confidence, a single-lens find can still be real)
# assumes every lens actually ran. Success = exit 0 AND the file exists AND it has
# non-whitespace content. The three reasons are reported apart; collapsing them loses the
# diagnosis. DRY_RUN produces no output by design and returns before this check.
fail=0; failed=""; i=0
for pid in "${PIDS[@]}"; do
  L="${LNAMES[$i]}"; OUT="REVIEW_r${N}_${L}.md"; why=""
  if ! wait "$pid"; then why="exit code"
  elif [ ! -f "$OUT" ]; then why="no output"
  elif ! sed -e '1s/^\xef\xbb\xbf//' "$OUT" 2>/dev/null | grep -q '[^[:space:]]'; then why="empty output"
  fi
  if [ -z "$why" ]; then echo "  ✓ $L -> $OUT"
  else fail=1; failed="$failed $L($why)"; echo "  ✗ $L — $why (-> $OUT.log/.err)"; fi
  i=$((i+1))
done
if [ "$fail" = "1" ]; then
  echo "some lenses failed:$failed — output incomplete. Re-run (FORCE=1) before triage." >&2
  exit 4
fi
echo "done. Merge REVIEW_r${N}_*.md -> triage. Lenses see *intentionally* different defect classes — agreement = higher confidence, a single-lens find can still be real (don't drop it)."
