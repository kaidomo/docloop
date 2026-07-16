#!/usr/bin/env bash
# Role-panel review — review one staged artifact through independent job-role evaluators.
# Ported (downstream) from the cross-functional-review skill; upstream canonical lives in the
# private skill repo (docuauthring). Each role runs as its OWN headless model process, so
# isolation is structural: no role can see another role's output or the author's prior notes.
#
# A role is a failure-surface contract (questions · evidence access · abstain conditions ·
# output envelope) — not a job-title persona. Synthesis (Area Chair) preserves conflicts and
# lone critical findings; no averaging, no majority vote, and same-model agreement is recorded
# as correlated without raising confidence.
#
# Usage: panel_review.sh <review-folder> <roundN(number)> [roles...]
#   <review-folder> must contain REVIEW_BRIEF.md + the staged target (output of stage.py).
#   Default roles: pm product-designer frontend backend qa
#   Custom roles: any [A-Za-z0-9_-] name — define its contract in REVIEW_BRIEF.md
#   (failure surface · key questions · evidence access · abstain conditions), or the
#   evaluator will abstain for lack of a contract.
#   Output: PANEL_r<N>_<role>.yaml per role + PANEL_r<N>_SYNTHESIS.md (Area Chair).
#
# Env:
#   DOCLOOP_MODEL  codex (default) | claude — which headless CLI runs the evaluators
#   CODEX_EFFORT   reasoning effort for codex (default high)
#   CODEX_MODEL    codex model override
#   FORCE=1        allow overwriting existing PANEL_r<N>_* files (refused by default)
#   DRY_RUN=1      print the execution plan instead of calling the model (smoke test)
set -uo pipefail

usage() { sed -n '2,27p' "$0"; exit "${1:-0}"; }
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

# Input validation — these go into filenames, so block injection (path escape / clobber)
case "$N" in ''|*[!0-9]*) echo "round N must be numeric: '$N'" >&2; exit 2;; esac
for R in "${ROLES[@]}"; do
  case "$R" in ''|*[!A-Za-z0-9_-]*) echo "role name must be [A-Za-z0-9_-]: '$R'" >&2; exit 2;; esac
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
- You are one axis of an independent parallel panel. Other roles' outputs do not exist for you.
- Do not impersonate a job title's speech style — a role is a failure-surface contract, not a persona.
- Never render final conclusions in another role's surface (e.g. PM must not conclude technical feasibility).
- If evidence your contract names is not present in the staged folder, abstain on the judgments that
  depend on it (record area + reason), or proceed only with inference_boundary: hypothesis.
- Do not propose skill/document edits — verdicts and findings only; changes are a human decision after synthesis.
- Read-only: do not create, edit, delete, or rename any file. Print the completed YAML to stdout only.
- Every finding needs evidence (section number / quoted line). Findings without evidence are removed at synthesis.
RULES
}

# Capture: use -o/--output-last-message if available (avoids stdout pollution), else redirect stdout
OUT_FLAG=""
[ "$MODEL" = "codex" ] && codex exec --help 2>/dev/null | grep -q -- '--output-last-message' && OUT_FLAG=1

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

Output: exactly one YAML document following the envelope below. The case header is pre-filled —
use the given values verbatim (do not invent, edit, or blank them). You fill role_header
(verdict; feasibility + difficulty only if your role is frontend/backend) and the findings array.

case_id: "panel-$(basename "$PWD")-r${N}"
artifact_id: "staged artifact per REVIEW_BRIEF.md"
reviewer_role: "${role}"
model_lineage: "${MODEL}"
criterion_id: "REVIEW_BRIEF.md"

Envelope (canonical structure — follow it exactly):
$(cat "$ENVELOPE")
EOF
}

run_one() {  # <role>
  local role="$1"
  local out="PANEL_r${N}_${role}.yaml"
  if [ "$DRY_RUN" = "1" ]; then
    echo "[dry-run] $MODEL role=$role effort=$EFFORT$MODEL_DESC -> $out"
    return 0
  fi
  case "$MODEL" in
    codex)
      if [ -n "$OUT_FLAG" ]; then
        codex exec --skip-git-repo-check --sandbox read-only -c model_reasoning_effort="$EFFORT" \
          "${MODEL_ARG[@]}" --output-last-message "$out" "$(role_prompt "$role")" >/dev/null
      else
        codex exec --skip-git-repo-check --sandbox read-only -c model_reasoning_effort="$EFFORT" \
          "${MODEL_ARG[@]}" "$(role_prompt "$role")" > "$out"
      fi ;;
    claude)
      claude -p "$(role_prompt "$role")" > "$out" ;;
    *) echo "unknown DOCLOOP_MODEL '$MODEL' (use codex or claude)" >&2; return 2 ;;
  esac
}

echo "panel: round r${N}, roles: ${ROLES[*]} (model=$MODEL, isolation=per-process)"
PIDS=(); FAIL=0
for R in "${ROLES[@]}"; do run_one "$R" & PIDS+=($!); done
for P in "${PIDS[@]}"; do wait "$P" || FAIL=1; done
[ "$FAIL" = "1" ] && { echo "panel: one or more role runs failed — synthesis skipped" >&2; exit 1; }
[ "$DRY_RUN" = "1" ] && { echo "[dry-run] synthesis -> PANEL_r${N}_SYNTHESIS.md"; exit 0; }

synthesis_prompt() {
  cat <<EOF
You are the Area Chair for a role-panel review. Inputs: REVIEW_BRIEF.md and the role outputs
PANEL_r${N}_*.yaml in this folder. The Area Chair is a separate judgment step, not a summarizer.

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

Write PANEL_r${N}_SYNTHESIS.md (the ONLY file you may create; edit nothing else) with sections:
decision table (synthesis_id | source_finding_ids+aliases | representative evidence | impact |
verdict/feasibility summary | conflict | correlation | decision_owner), lone criticals,
role conflicts (unresolved), abstentions, removed findings (with reasons), correlated-agreement
record, and a pointer to the role outputs as appendix.
EOF
}

case "$MODEL" in
  codex)  codex exec --skip-git-repo-check -c model_reasoning_effort="$EFFORT" "${MODEL_ARG[@]}" "$(synthesis_prompt)" ;;
  claude) claude -p "$(synthesis_prompt)" ;;
esac

echo "panel: done — PANEL_r${N}_<role>.yaml + PANEL_r${N}_SYNTHESIS.md (human decisions stay with you)"
