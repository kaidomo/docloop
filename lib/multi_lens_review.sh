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
#
# Concurrency: same-round runs are excluded via a .review_r<N>.lock dir (mkdir) — if an abnormal exit leaves it behind, rmdir it and retry.
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
# DRY_RUN invokes no codex at all — not even this --help probe (upstream contract; port r1-01).
OUT_FLAG=""
if [ "$DRY_RUN" != "1" ]; then
  codex exec --help 2>/dev/null | grep -q -- '--output-last-message' && OUT_FLAG=1
fi

MODEL_ARG=(); MODEL_DESC=""
if [ -n "${CODEX_MODEL:-}" ]; then MODEL_ARG=(-m "$CODEX_MODEL"); MODEL_DESC=" -m $CODEX_MODEL"; fi

DRY_LABEL=""; [ "$DRY_RUN" = "1" ] && DRY_LABEL=" [DRY_RUN]"
echo "multi-lens review: ${#LENSES[@]} lenses in parallel (effort=$EFFORT${MODEL_DESC})${DRY_LABEL}"

# Clobber protection — preserve existing round outputs/evidence, incl. harness sidecars
# (.log/.err are what a lens redirects into — leaving them unvalidated loses evidence or
# writes outside the review folder). A symlinked output path (dangling or not) is rejected
# unconditionally — FORCE does not bypass; redirection would follow the link outside.
# * Same-round concurrent runs are excluded with a mkdir lock (upstream #115): without it the
#   .forcebak staging lets two runs slip past each other's preflight, and one run's rollback
#   restores stale content over the other's fresh outputs. The unconditional .forcebak
#   residue check below is a secondary defense, not a lock substitute.
# * Under FORCE=1 outputs are moved aside BEFORE the run rather than overwritten in place
#   (upstream #110): the -o capture path does not touch the file when a lens writes nothing,
#   so the previous round's content would pass the non-empty check below and be reported ✓ —
#   the human would triage LAST round's findings as this round's. Sidecars are staged in the
#   same execution unit (upstream #116): a stale inactive sidecar reads as this round's
#   diagnosis. FORCE=1 buys an overwrite, not evidence loss.
# * Validation runs in full BEFORE anything moves or is deleted (upstream #120): a rejected
#   run can never end with earlier evidence gone while no lens ran. FORCE removal stays
#   all-or-nothing — a failed stash rolls back everything stashed so far and stops.
# * Scope of the guarantee, stated so it is not overread: it is NOT a filesystem transaction.
#   kill -9, a crash between the mv and its registration, or a failing restore can leave a
#   .forcebak or the lock behind — content survives under those names; recovery is one
#   rename/rmdir (failures warn with the exact path). Validation is a point-in-time snapshot:
#   a non-cooperating external process swapping a path to a symlink between validation and
#   write (TOCTOU) is outside the guarantee — this is a human-driven review folder
#   (upstream impl r1-06).
LOCK=".review_r${N}.lock"
LOCK_HELD=0   # ownership flag — a failed acquisition must not remove someone else's lock
lock_release() {
  lr_st=$?
  if [ "$LOCK_HELD" = "1" ]; then
    rmdir "$LOCK" 2>/dev/null || [ ! -e "$LOCK" ] || echo "WARNING: could not release lock — remove by hand: $DIR/$LOCK" >&2
  fi
  exit "$lr_st"
}
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
if [ "$DRY_RUN" != "1" ]; then
  # 0) round lock (upstream #115) — taken before preflight, held until collection ends
  #    (released from the EXIT trap). Handlers are installed BEFORE acquisition, but the
  #    acquisition window only LATCHES the signal (upstream impl r1-02): an untrapped signal
  #    skips the EXIT trap and leaves the lock, while an exit-now handler processed between
  #    mkdir and LOCK_HELD=1 would leave the fresh lock unowned. Record ownership first, then
  #    honor the pending signal via the normal exit path — no catchable window remains, only
  #    kill -9-class (recovery: one rmdir, see scope note above).
  PENDING_SIG=0
  trap 'PENDING_SIG=1' INT TERM HUP
  trap 'lock_release' EXIT
  if ! mkdir "$LOCK" 2>/dev/null; then
    echo "round r${N} lock held: $DIR/$LOCK — another run is active or a previous run died. If nothing is running, rmdir it and retry." >&2
    exit 3
  fi
  LOCK_HELD=1
  trap 'exit 3' INT TERM HUP
  [ "$PENDING_SIG" = "1" ] && exit 3
  # 1) validation — everything, before anything moves (upstream #120). The three paths per
  #    lens (.md/.log/.err) are one execution unit (upstream #116).
  for L in "${LENSES[@]}"; do
    for f in "REVIEW_r${N}_${L}.md" "REVIEW_r${N}_${L}.md.log" "REVIEW_r${N}_${L}.md.err"; do
      [ -L "$f" ] && { echo "refusing: $f is a symlink — outputs must be regular files in the review folder (FORCE does not bypass)" >&2; exit 3; }
      if [ -e "$f" ] && [ ! -f "$f" ]; then
        echo "refusing: existing output is not a regular file: $f (not auto-removed)" >&2; exit 3
      fi
      if [ -e "$f.forcebak" ] || [ -L "$f.forcebak" ]; then   # -L: a dangling-symlink marker is still residue (upstream impl r1-05)
        echo "refusing: $f.forcebak left behind — residue of a previous FORCE run (crash or concurrent run). Inspect it, rename it back or delete it, then retry." >&2; exit 3
      fi
      [ "$FORCE" != "1" ] && [ -e "$f" ] && { echo "already exists: $f (use FORCE=1 or a new N)" >&2; exit 3; }
    done
  done
  # 2) staging (FORCE=1) — the restore trap is installed BEFORE the first move so an
  #    interrupt during the stash window rolls back too.
  if [ "$FORCE" = "1" ]; then
    trap 'force_restore; exit 3' INT TERM HUP
    for L in "${LENSES[@]}"; do
      for f in "REVIEW_r${N}_${L}.md" "REVIEW_r${N}_${L}.md.log" "REVIEW_r${N}_${L}.md.err"; do
        [ -e "$f" ] || continue
        if ! mv -f "$f" "$f.forcebak" 2>/dev/null; then
          force_restore
          echo "could not stash existing output: $f — stale content could be mistaken for this round; stopping (stashed files restored)" >&2; exit 3
        fi
        FORCE_BAK+=("$f.forcebak")
      done
    done
    # 3) commit — delete only once every lens is clear. Once deletion starts, restore is no
    #    longer coherent (some backups deleted, some restored — upstream impl r1-01): drop the
    #    restore trap first. On a mid-delete signal the remaining .forcebak files stay under
    #    that name and the next run's validation refuses with recovery guidance.
    trap 'exit 3' INT TERM HUP
    for b in ${FORCE_BAK[@]+"${FORCE_BAK[@]}"}; do
      rm -f "$b" || echo "WARNING: leftover stash not removed: $b" >&2
    done
    FORCE_BAK=()
  fi
fi

# Execution-phase trap — all three of INT TERM HUP switch to job teardown. A stale staging
# handler left on HUP would exit only the parent: EXIT releases the lock while the lens jobs
# keep writing, and a new same-round run can then take the lock — reopening upstream #115.
# * Jobs are killed BY PROCESS GROUP: hitting only the subshell pid (kill $(jobs -p)) lets the
#   subshell defer the signal until its foreground child (the model CLI) exits, and that child
#   never sees a signal at all — a ghost lens keeps writing after the lock is gone (measured
#   upstream, 5/5). set -m gives each lens job its own pgroup so the whole group is signalled.
# * The signal is also latched in a flag (upstream impl r1-03): between launches it would only
#   kill already-running jobs while later lenses still start, and before the first launch it
#   would be swallowed into exit 0. The flag stops further launches and forces the failure path.
MLR_SIG=0
trap 'MLR_SIG=1; for jp in $(jobs -p); do kill -- "-$jp" 2>/dev/null; done' INT TERM HUP

run_lens() {
  # Child-entry guard (upstream impl r1-03) — the flag check and the & launch are separate
  # commands; a signal in between starts a lens inside a cancelled round. The fork copies
  # the flag into the child, so filter once more here.
  [ "${MLR_SIG:-0}" = "1" ] && return 1
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
set -m   # per-lens process groups (prerequisite of the pgroup kill above) — restored after launch
for L in "${LENSES[@]}"; do
  [ "$MLR_SIG" = "1" ] && break   # no further launches after a signal (upstream impl r1-03)
  run_lens "$L" & PIDS+=($!); LNAMES+=("$L")
  # A signal between the check and the launch ran its handler before this job existed in
  # jobs — recheck right after registration and kill the fresh pgroup directly. The residual
  # window between this recheck and the fork is covered by the child-entry guard.
  if [ "$MLR_SIG" = "1" ]; then kill -- "-$!" 2>/dev/null; break; fi
done
set +m

if [ "$DRY_RUN" = "1" ]; then
  wait
  # A signalled round must not end 0 even in dry-run (upstream impl r1-03 — measured exit 0 on TERM)
  [ "$MLR_SIG" = "1" ] && { echo "dry-run interrupted by signal." >&2; exit 4; }
  echo "dry-run done (${#LENSES[@]} lenses)."; exit 0
fi

# Per-lens success — don't hide failures (any failure => non-zero).
# An exit code alone does not earn a ✓: a lens can exit 0 and write nothing, and the old check
# then printed a filename that does not exist next to a ✓ and sent the human on to triage. In
# triage that makes "the lens found nothing" indistinguishable from "the lens never ran", while
# the merge rule below (agreement raises confidence, a single-lens find can still be real)
# assumes every lens actually ran. Success = exit 0 AND the file exists AND it has
# non-whitespace content. The three reasons are reported apart; collapsing them loses the
# diagnosis. DRY_RUN produces no output by design and returns before this check.
# The failure hint names ONLY the sidecar this run actually wrote (upstream #116) — the -o
# path writes .log, the stdout fallback writes .err. Naming both sends the human to a stale file.
fail=0; failed=""; i=0
for pid in ${PIDS[@]+"${PIDS[@]}"}; do   # can be empty when a signal cut the launches short (bash 3.2 + set -u)
  L="${LNAMES[$i]}"; OUT="REVIEW_r${N}_${L}.md"; why=""
  if [ -n "$OUT_FLAG" ]; then SIDE="$OUT.log"; else SIDE="$OUT.err"; fi
  if ! wait "$pid"; then why="exit code"
  elif [ ! -f "$OUT" ]; then why="no output"
  elif ! sed -e '1s/^\xef\xbb\xbf//' "$OUT" 2>/dev/null | grep -q '[^[:space:]]'; then why="empty output"
  fi
  if [ -z "$why" ]; then echo "  ✓ $L -> $OUT"
  else fail=1; failed="$failed $L($why)"; echo "  ✗ $L — $why (-> $SIDE)"; fi
  i=$((i+1))
done
if [ "$MLR_SIG" = "1" ]; then   # a signalled round never ends as success (upstream impl r1-03)
  fail=1; failed="$failed signal-interrupted(lenses unlaunched/killed)"
fi
if [ "$fail" = "1" ]; then
  echo "some lenses failed:$failed — output incomplete. Re-run (FORCE=1) before triage." >&2
  exit 4
fi
echo "done. Merge REVIEW_r${N}_*.md -> triage. Lenses see *intentionally* different defect classes — agreement = higher confidence, a single-lens find can still be real (don't drop it)."
