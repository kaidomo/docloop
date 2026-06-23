#!/usr/bin/env bash
# peer-review 다렌즈 병렬 Codex 리뷰 — 한 브리프를 여러 렌즈로 동시에 검토한다.
# ChatGPT Pro 한도(정액제·헤드리스·추가 과금 0)로 여러 렌즈를 병렬로 돌린다(의도: 서로 다른 결함군).
# ⚠️ A/B 실측: 소/중형은 단일 medium 패스로 충분(medium=high Δ=0, 다렌즈 추가이득 0) — 다렌즈 이득은
#    단일 medium이 놓치기 시작하는 대형·복잡 산출물에서만 가설적. 채택 전 측정. (과대선전 금지)
#
# 사용: multi_lens_review.sh <리뷰폴더> <라운드N(숫자)> [렌즈...]
#   <리뷰폴더>에 REVIEW_BRIEF.md + 스테이지된 대상이 있어야 한다(scripts/stage.py 산출).
#   기본 렌즈: correctness compliance completeness adversarial
#   렌즈명은 [A-Za-z0-9_-]만. 출력: 렌즈별 REVIEW_r<N>_<lens>.md (읽기전용·effort=high).
#   취합: 렌즈는 서로 다른 결함군을 본다 — 합의=신뢰↑, 단독 발견도 진짜일 수 있음(무시 금지).
#
# 환경변수:
#   CODEX_EFFORT  추론 effort (기본 high — escalation 도구라. 소형·기계적이면 다렌즈 말고 단일 medium 패스 권장)
#   CODEX_MODEL   모델 override (기본: codex config의 model)
#   FORCE=1       기존 REVIEW_r<N>_<lens>.md 덮어쓰기 허용(기본은 거부)
#   DRY_RUN=1     codex 호출 대신 실행 계획만 출력(스모크 테스트용)
set -uo pipefail

usage() { sed -n '2,20p' "$0"; exit "${1:-0}"; }
{ [ "${1:-}" = "-h" ] || [ "${1:-}" = "--help" ]; } && usage 0

DIR="${1:?리뷰폴더 경로 필요 (-h로 도움말)}"
N="${2:?라운드 번호 필요(숫자)}"
shift 2 || true
LENSES=("$@")
[ ${#LENSES[@]} -eq 0 ] && LENSES=(correctness compliance completeness adversarial)
EFFORT="${CODEX_EFFORT:-high}"
DRY_RUN="${DRY_RUN:-0}"
FORCE="${FORCE:-0}"

# 입력 검증 — 파일명에 들어가므로 인젝션 차단(폴더밖 경로/clobber 방지)
case "$N" in ''|*[!0-9]*) echo "라운드 N은 숫자만: '$N'" >&2; exit 2;; esac
for L in "${LENSES[@]}"; do
  case "$L" in ''|*[!A-Za-z0-9_-]*) echo "렌즈명은 [A-Za-z0-9_-]만: '$L'" >&2; exit 2;; esac
done

cd "$DIR" || { echo "cd 실패: $DIR" >&2; exit 1; }
[ -f REVIEW_BRIEF.md ] || { echo "REVIEW_BRIEF.md 없음: $DIR (stage.py 먼저)" >&2; exit 1; }

# 렌즈별 초점 — 같은 산출물을 서로 다른 blind-spot으로 본다.
lens_focus() {
  case "$1" in
    correctness)  echo "정확성·논리결함·근거(SSOT) 불일치·누락 중심." ;;
    compliance)   echo "규정 준수·추적성·권한/상태/예외 누락(해당될 때만). 규제 도메인 산출물이면 GxP까지." ;;
    regulatory)   echo "규제·GxP·추적성·권한/상태/예외 누락 중심(규제 도메인 전용 opt-in)." ;;
    completeness) echo "완전성 — MECE(범위 in/out 비중복+빠짐없음)·CRUD(데이터 조작 완전)·권한(역할×조작·상태·예외)." ;;
    adversarial)  echo "적대적 — 이 산출물의 핵심 주장을 반박하라. 불확실하면 기본 '결함 있음'으로." ;;
    *)            echo "초점: $1." ;;
  esac
}

# 캡처: -o/--output-last-message 있으면 그걸로(stdout 오염 방지), 없으면 stdout redirect(하위호환)
OUT_FLAG=""
codex exec --help 2>/dev/null | grep -q -- '--output-last-message' && OUT_FLAG=1

MODEL_ARG=(); MODEL_DESC=""
if [ -n "${CODEX_MODEL:-}" ]; then MODEL_ARG=(-m "$CODEX_MODEL"); MODEL_DESC=" -m $CODEX_MODEL"; fi

DRY_LABEL=""; [ "$DRY_RUN" = "1" ] && DRY_LABEL=" [DRY_RUN]"
echo "다렌즈 Codex ${#LENSES[@]}개 병렬 (effort=$EFFORT${MODEL_DESC})${DRY_LABEL}"

# clobber 보호 — 기존 라운드 산출물/증거 보존(FORCE=1로 우회)
if [ "$DRY_RUN" != "1" ] && [ "$FORCE" != "1" ]; then
  for L in "${LENSES[@]}"; do
    [ -e "REVIEW_r${N}_${L}.md" ] && { echo "이미 존재: REVIEW_r${N}_${L}.md (FORCE=1 또는 새 N)" >&2; exit 3; }
  done
fi

trap 'kill $(jobs -p) 2>/dev/null' INT TERM

run_lens() {
  L="$1"; OUT="REVIEW_r${N}_${L}.md"
  PROMPT="REVIEW_BRIEF.md를 읽고 동봉된 산출물을 리뷰하라. 렌즈: $(lens_focus "$L")
발견사항을 심각도(버그/robustness/설계판단/사소)와 함께 마크다운으로 출력하라. 브리프의 '이미 내린 결정'은 재논의하지 말 것. 파일을 수정하지 말고 리뷰 텍스트만 출력하라."
  if [ "$DRY_RUN" = "1" ]; then
    echo "  [dry] $L → $OUT : codex exec --skip-git-repo-check --sandbox read-only -c model_reasoning_effort=$EFFORT${MODEL_DESC}"
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

if [ "$DRY_RUN" = "1" ]; then wait; echo "dry-run 종료(렌즈 ${#LENSES[@]})."; exit 0; fi

# 렌즈별 종료코드 수집 — 실패를 은폐하지 않는다(하나라도 실패면 non-zero)
fail=0; failed=""; i=0
for pid in "${PIDS[@]}"; do
  if wait "$pid"; then echo "  ✓ ${LNAMES[$i]} → REVIEW_r${N}_${LNAMES[$i]}.md"
  else fail=1; failed="$failed ${LNAMES[$i]}"; echo "  ✗ ${LNAMES[$i]} 실패 (→ REVIEW_r${N}_${LNAMES[$i]}.md.log/.err)"; fi
  i=$((i+1))
done
if [ "$fail" = "1" ]; then
  echo "일부 렌즈 실패:$failed — 산출 불완전. triage 전에 재실행(FORCE=1) 권장." >&2
  exit 4
fi
echo "완료. 취합: REVIEW_r${N}_*.md → triage. 렌즈는 *의도상* 서로 다른 결함군 — 합의=신뢰↑, 단독 발견도 진짜일 수 있음(무시 금지)."
