#!/usr/bin/env bash
# Role-panel review — review one staged artifact through independent job-role evaluators.
# Ported (downstream) from the cross-functional-review skill; upstream canonical lives in the
# private skill repo (docuauthring). Each role runs as its OWN headless model process, and role
# outputs are held in a private temp dir until every role finishes — so no role can read another
# role's output through the review folder. (Process separation on one machine, not an air gap;
# the prompt additionally forbids reading PANEL_* files.)
#
# A role is a failure-surface contract (questions · evidence access · abstain conditions ·
# output envelope) — not a job-title persona. Synthesis (Area Chair) preserves conflicts and
# lone critical findings; no averaging, no majority vote, and same-model agreement is recorded
# as correlated without raising confidence.
#
# Usage: panel_review.sh <review-folder> <roundN(number)> [roles...]
#   <review-folder> must contain REVIEW_BRIEF.md + the staged target (output of stage.py).
#   Default roles: pm product-designer frontend backend qa
#   Custom roles: any [A-Za-z0-9_-] name (no duplicates) — define its contract in
#   REVIEW_BRIEF.md (failure surface · key questions · evidence access · abstain conditions),
#   or the evaluator will abstain for lack of a contract.
#   Output: PANEL_r<N>_<role>.yaml per role + PANEL_r<N>_SYNTHESIS.md (Area Chair).
#   Synthesis reads exactly this round's role files (stale files from other runs are ignored).
#
# Env:
#   DOCLOOP_MODEL  codex (default) | claude — which headless CLI runs the evaluators.
#                  codex runs sandboxed read-only; claude runs with --allowedTools "Read,Glob,Grep"
#                  (requires a claude CLI that supports --allowedTools).
#   CODEX_EFFORT   reasoning effort for codex (default high)
#   CODEX_MODEL    codex model override
#   FORCE=1        allow overwriting existing PANEL_r<N>_* files (refused by default)
#   DRY_RUN=1      print the execution plan instead of calling the model (smoke test)
set -uo pipefail

usage() { sed -n '2,31p' "$0"; exit "${1:-0}"; }
{ [ "${1:-}" = "-h" ] || [ "${1:-}" = "--help" ]; } && usage 0

DIR="${1:?review-folder path required (use -h for help)}"
N="${2:?round number required (numeric)}"
shift 2 || true
ROLES=("$@")
[ ${#ROLES[@]} -eq 0 ] && ROLES=(pm product-designer frontend backend qa)
MODEL="${DOCLOOP_MODEL:-codex}"
EFFORT="${CODEX_EFFORT:-high}"
DRY_RUN="${DRY_RUN:-0}"
FORCE="${FORCE:-0}"
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
ENVELOPE="$ROOT/templates/finding-envelope.example.yaml"

# Input validation — these go into filenames, so block injection (path escape / clobber),
# and reject duplicates (two processes writing one file is non-deterministic).
case "$N" in ''|*[!0-9]*) echo "round N must be numeric: '$N'" >&2; exit 2;; esac
for R in "${ROLES[@]}"; do
  case "$R" in ''|*[!A-Za-z0-9_-]*) echo "role name must be [A-Za-z0-9_-]: '$R'" >&2; exit 2;; esac
done
for R in "${ROLES[@]}"; do
  seen=0
  for S in "${ROLES[@]}"; do [ "$S" = "$R" ] && seen=$((seen+1)); done
  [ "$seen" -gt 1 ] && { echo "duplicate role name: '$R'" >&2; exit 2; }
done

cd "$DIR" || { echo "cd failed: $DIR" >&2; exit 1; }
[ -f REVIEW_BRIEF.md ] || { echo "REVIEW_BRIEF.md not found: $DIR (run stage.py first)" >&2; exit 1; }
[ -f "$ENVELOPE" ] || { echo "envelope template not found: $ENVELOPE" >&2; exit 1; }

# Per-role contract — failure surface · key questions · evidence access · abstain conditions.
# (Condensed port of the skill's role table; a role is an evaluator contract, not a persona.)
role_contract() {
  case "$1" in
    pm)               echo "Failure surface: problem-solution fit, requirement logic, policy conflicts, scenario/exception coverage, verifiable completion criteria. Ask: does the solution trace back to the stated problem? do requirements contradict or duplicate? are exception flows covered? are completion criteria checkable? Evidence: the staged artifact + sources named in the brief. Abstain on: technical difficulty, design usability, policy-conflict claims without a policy document." ;;
    product-designer) echo "Failure surface: design reproducibility, information architecture, missing UI states (loading/empty/error/boundary), usability, accessibility, design-system consistency. Ask: are all UI states defined? does the IA match the user task? are interaction rules reproducible from the text alone? Evidence: the staged artifact + any design sources in the brief. Abstain on: implementation cost, backend constraints." ;;
    frontend)         echo "Failure surface: screen/state/interaction buildability, state combinations, client-event-driven behavior, API dependencies, platform constraints. Ask: do state combinations explode? are the APIs the screen needs defined? are client-event behaviors (leave/refresh/reselect) buildable as written? Also report feasibility (feasible|feasible_with_changes|blocked) and the six difficulty axes. Evidence: the staged artifact; existing FE code/API specs only if staged. Abstain on: server internals, product-value judgment." ;;
    backend)          echo "Failure surface: data/permission model, external integrations, async/failure recovery, transaction boundaries, performance, migration, basic security. Ask: is the state machine implementable without guessing? what happens on integration failure or races? are transaction boundaries defined? Also report feasibility (feasible|feasible_with_changes|blocked) and the six difficulty axes. Evidence: the staged artifact; existing BE code/infra constraints only if staged. Abstain on: UI usability, visual design." ;;
    qa)               echo "Failure surface: testability, acceptance criteria, boundary/failure/recovery scenarios, regression risk, observability. Ask: is every completion criterion verifiable by machine or human? are boundary and failure scenarios specified? what would a regression test anchor on? Evidence: the staged artifact. Abstain on: priority/value judgment." ;;
    *)                echo "Case-specific role '$1'. Use the contract defined for it in REVIEW_BRIEF.md (failure surface, key questions, evidence access, abstain conditions). If the brief defines no such contract, set every judgment to abstain with reason 'no contract provided'." ;;
  esac
}

# Shared rules for every evaluator (independence + envelope discipline)
panel_rules() {
  cat <<'RULES'
Rules (all roles):
- You are one axis of an independent parallel panel. Other roles' outputs do not exist for you:
  do not open, list, or reference any PANEL_* file even if one is present.
- Do not impersonate a job title's speech style — a role is a failure-surface contract, not a persona.
- Never render final conclusions in another role's surface (e.g. PM must not conclude technical feasibility).
- If evidence your contract names is not present in the staged folder, abstain on the judgments that
  depend on it (record area + reason), or proceed only with inference_boundary: hypothesis.
- Do not propose skill/document edits — verdicts and findings only; changes are a human decision after synthesis.
- Read-only: do not create, edit, delete, or rename any file. Print the completed YAML to stdout only.
- Every finding needs evidence (section number / quoted line). Findings without evidence are removed at synthesis.
RULES
}

MODEL_ARG=(); MODEL_DESC=""
if [ "$MODEL" = "codex" ] && [ -n "${CODEX_MODEL:-}" ]; then MODEL_ARG=(-m "$CODEX_MODEL"); MODEL_DESC=" -m $CODEX_MODEL"; fi

# Refuse to clobber existing round files unless FORCE=1
for R in "${ROLES[@]}"; do
  if [ -e "PANEL_r${N}_${R}.yaml" ] && [ "$FORCE" != "1" ]; then
    echo "refusing to overwrite PANEL_r${N}_${R}.yaml (set FORCE=1 to allow)" >&2; exit 3
  fi
done
if [ -e "PANEL_r${N}_SYNTHESIS.md" ] && [ "$FORCE" != "1" ]; then
  echo "refusing to overwrite PANEL_r${N}_SYNTHESIS.md (set FORCE=1 to allow)" >&2; exit 3
fi

role_prompt() {
  local role="$1"
  cat <<EOF
You are the '${role}' evaluator on an independent role panel reviewing the staged artifact in this folder.
Read REVIEW_BRIEF.md first (it names the target, sources, decided-and-settled matters, and risk).

Your contract: $(role_contract "$role")

$(panel_rules)

Output: print to stdout exactly one YAML document with the structure of the envelope template
below (same keys, same nesting — it is a schema skeleton, not content). Set its case-header
fields to exactly these values (do not invent, edit, or blank them):
  case_id: "panel-$(basename "$PWD")-r${N}"
  artifact_id: "staged artifact per REVIEW_BRIEF.md"
  reviewer_role: "${role}"
  model_lineage: "${MODEL}"
  criterion_id: "REVIEW_BRIEF.md"
You fill role_header (verdict; feasibility + difficulty only if your role is frontend/backend)
and the findings array. No prose before or after the YAML.

Envelope template (schema skeleton):
$(cat "$ENVELOPE")
EOF
}

# Role outputs are collected in a private temp dir and moved into the review folder only after
# every role has finished — an early-finishing role's output is never visible to a running one.
TMPD=""
cleanup() { [ -n "$TMPD" ] && rm -rf "$TMPD"; }
# On INT/TERM: disarm the trap, kill each background job's process TREE (recursive pgrep -P —
# reaches the model CLI and its children without signalling our own process group, which may be
# shared with the caller in non-interactive/CI shells), then clean the temp dir.
# (A child that detaches into its own session is unreachable from here — documented limit.)
killtree() { local _c; for _c in $(pgrep -P "$1" 2>/dev/null); do killtree "$_c"; done; kill "$1" 2>/dev/null; }
trap 'trap - INT TERM; for _j in $(jobs -p); do killtree "$_j"; done; cleanup' INT TERM
trap 'cleanup' EXIT

run_one() {  # <role> <out-path>
  local role="$1"
  local out="$2"
  case "$MODEL" in
    codex)
      codex exec --skip-git-repo-check --sandbox read-only -c model_reasoning_effort="$EFFORT" \
        ${MODEL_ARG[@]+"${MODEL_ARG[@]}"} --output-last-message "$out" "$(role_prompt "$role")" >/dev/null ;;
    claude)
      claude -p --allowedTools "Read,Glob,Grep" "$(role_prompt "$role")" > "$out" ;;
    *) echo "unknown DOCLOOP_MODEL '$MODEL' (use codex or claude)" >&2; return 2 ;;
  esac
}

echo "panel: round r${N}, roles: ${ROLES[*]} (model=$MODEL, per-process; outputs held back until all finish)"
if [ "$DRY_RUN" = "1" ]; then
  for R in "${ROLES[@]}"; do echo "[dry-run] $MODEL role=$R effort=$EFFORT$MODEL_DESC -> PANEL_r${N}_${R}.yaml"; done
  _list=""; for R in "${ROLES[@]}"; do _list+="PANEL_r${N}_${R}.yaml "; done
  echo "[dry-run] synthesis over exactly: ${_list}"
  echo "[dry-run] synthesis -> PANEL_r${N}_SYNTHESIS.md"
  exit 0
fi

# codex capture contract: --output-last-message is required (stdout carries the session log)
if [ "$MODEL" = "codex" ] && ! codex exec --help 2>/dev/null | grep -q -- '--output-last-message'; then
  echo "panel: this codex CLI lacks --output-last-message; cannot capture role output safely" >&2; exit 1
fi

TMPD="$(mktemp -d)"
PIDS=(); FAIL=0
for R in "${ROLES[@]}"; do run_one "$R" "$TMPD/PANEL_r${N}_${R}.yaml" & PIDS+=($!); done
for P in "${PIDS[@]}"; do wait "$P" || FAIL=1; done
[ "$FAIL" = "1" ] && { echo "panel: one or more role runs failed — synthesis skipped" >&2; exit 1; }

# Validate before publishing: every role file must exist, be non-empty, and parse as an
# envelope document (real YAML parse — a mention of the keys in a comment does not count).
for R in "${ROLES[@]}"; do
  f="$TMPD/PANEL_r${N}_${R}.yaml"
  [ -s "$f" ] || { echo "panel: role '$R' produced no output — synthesis skipped" >&2; exit 1; }
  python3 - "$f" <<'PY' || { echo "panel: role '$R' output is not a valid envelope YAML — synthesis skipped" >&2; exit 1; }
import sys, yaml
d = yaml.safe_load(open(sys.argv[1], encoding="utf-8"))
ok = isinstance(d, dict) and isinstance(d.get("role_header"), dict) and isinstance(d.get("findings"), list)
sys.exit(0 if ok else 1)
PY
done

# Synthesis runs in an isolated staging dir holding ONLY the brief + this round's role files —
# stale PANEL_* files from other rounds/roles are physically absent, not merely "please ignore".
SYNTH_DIR="$TMPD/synth"
mkdir "$SYNTH_DIR"
cp REVIEW_BRIEF.md "$SYNTH_DIR/"
ROLE_FILES=""
for R in "${ROLES[@]}"; do
  cp "$TMPD/PANEL_r${N}_${R}.yaml" "$SYNTH_DIR/PANEL_r${N}_${R}.yaml"
  ROLE_FILES+="PANEL_r${N}_${R}.yaml "
done

synthesis_prompt() {
  cat <<EOF
You are the Area Chair for a role-panel review. Inputs: REVIEW_BRIEF.md and EXACTLY these
role outputs from this round: ${ROLE_FILES}— ignore any other PANEL_* file in the folder
(stale rounds/roles are not this panel). The Area Chair is a separate judgment step, not a summarizer.

Contract:
- Alias duplicate findings to one canonical finding_id (state your canonical-choice basis).
- Remove findings that carry no evidence — and record each removal with its reason.
- Preserve lone critical findings (one role catching it is not grounds to drop it).
- Preserve role conflicts and abstentions unresolved — surface them, never reconcile silently.
- Separate machine-decidable items from human decisions.
- No averaging, no majority vote. Same-model agreement is correlated: record it, never raise
  confidence or priority because of it. Independence comes from failure-surface and evidence
  separation, not from model count.
- Compress to at most 5 human decision items (merge by theme, never silently drop; the role
  outputs remain the appendix). Record decision_item_count.

Read-only: do not create or edit any file. Print the synthesis document to stdout as markdown,
with sections: decision table (synthesis_id | source_finding_ids+aliases | representative
evidence | impact | verdict/feasibility summary | conflict | correlation | decision_owner),
lone criticals, role conflicts (unresolved), abstentions, removed findings (with reasons),
correlated-agreement record, decision_item_count, and a pointer to the role outputs as appendix.
EOF
}

SYN="PANEL_r${N}_SYNTHESIS.md"
SYN_TMP="$TMPD/synthesis.md"
case "$MODEL" in
  codex)  (cd "$SYNTH_DIR" && codex exec --skip-git-repo-check --sandbox read-only -c model_reasoning_effort="$EFFORT" \
            ${MODEL_ARG[@]+"${MODEL_ARG[@]}"} --output-last-message "$SYN_TMP" "$(synthesis_prompt)" >/dev/null) || FAIL=1 ;;
  claude) (cd "$SYNTH_DIR" && claude -p --allowedTools "Read,Glob,Grep" "$(synthesis_prompt)" > "$SYN_TMP") || FAIL=1 ;;
esac
{ [ "$FAIL" = "1" ] || [ ! -s "$SYN_TMP" ]; } && { echo "panel: synthesis failed or empty — nothing published" >&2; exit 1; }
# Budget cap is a contract, not a hope: decision_item_count must be present and <= 5.
DC="$(grep -E '^[[:space:]]*decision_item_count[[:space:]]*:[[:space:]]*[0-9]+[[:space:]]*$' "$SYN_TMP" | grep -Eo '[0-9]+' | head -1 || true)"
[ -n "$DC" ] || { echo "panel: synthesis lacks decision_item_count — nothing published" >&2; exit 1; }
[ "$DC" -le 5 ] || { echo "panel: decision_item_count=$DC exceeds the budget cap of 5 — nothing published" >&2; exit 1; }

# Publish all-or-nothing, and only after every validation passed. Two phases: first check
# EVERY destination (a directory — or symlink to one — is an error, not a silent move-into),
# then move; nothing is removed or moved until all destinations pass.
DESTS=()
for R in "${ROLES[@]}"; do DESTS+=("PANEL_r${N}_${R}.yaml"); done
DESTS+=("$SYN")
for D2 in "${DESTS[@]}"; do
  [ -d "$D2" ] && { echo "panel: destination is a directory: $D2 — nothing published" >&2; exit 1; }
done
SRCS=()
for R in "${ROLES[@]}"; do SRCS+=("$TMPD/PANEL_r${N}_${R}.yaml"); done
SRCS+=("$SYN_TMP")
i=0
for D2 in "${DESTS[@]}"; do
  [ -e "$D2" ] && [ "$FORCE" = "1" ] && rm -f "$D2"
  mv "${SRCS[$i]}" "$D2" || { echo "panel: publish failed at $D2 — files published so far were left in place; re-run with FORCE=1 after inspecting" >&2; exit 1; }
  i=$((i+1))
done

echo "panel: done — ${ROLE_FILES}+ $SYN (human decisions stay with you)"
