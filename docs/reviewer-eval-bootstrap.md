# Reviewer-eval bootstrap — 골드셋 없이 시작해 골드로 수렴하는 경로

관련 결정: docloop `docs/design.md` H-01 (리뷰어 품질 = 베테랑 PM 골드 대비 **blocking-recall + precision 하한**, eval-time 전용). 그 결정의 **미결(골드셋 부재)** 을 푸는 부트스트랩 계획.

## 핵심 통찰 — 씨앗은 0이 아니다, 이미 만들어왔다 (단 아직 골드는 아니다)

"베테랑 PM 판정"의 정의 = **특정 문서에 대한, 사람이 내린 finding별 처분.** 그 처분 부산물은 이미 매 리뷰마다 생산돼 왔다:

- 리뷰 작업 폴더(로컬, repo 밖)의 `TRIAGE_r*.md` / REVIEW_BRIEF triage — finding마다 사람이 `apply/reject/invalid/defer` 처분 (예: 이번 docloop-direction r1의 6건).
- docloop repo의 `REVIEW_r*.md` (r1~r3) + gap 리포트.
- pm-authoring `_gap_report.md`, asistobe `_ground_report.md` 등 감사 산출물.

→ 각 dispositioned finding = **라벨 1개** (사람이 "이건 유효/무효/blocking"이라 판정). **이미 값을 치른 adjudicated seed.** 단, 이것은 씨앗이지 골드가 아니다 — 아래 '골드 자격 조건'을 통과하기 전까지는 A/B 기준선으로 승격하지 않는다.

## 왜 seed는 아직 gold가 아닌가 — recall 분모의 부재

triage disposition은 "리뷰어가 **제출한** finding이 유효했는가"만 보여준다. 그 기록은 **후보 finding에 조건부**라서, 해당 문서의 blocking finding 전체가 열거됐다는 보장이 없다. 따라서:

- seed로 false-positive 라벨(`reject`/`invalid`)은 얻을 수 있다.
- 그러나 **blocking-recall의 분모(문서별 blocking finding의 전집합)는 seed로 만들 수 없다.** 리뷰어가 애초에 못 본 종류는 seed에도 없기 때문.

### 골드 자격 조건 (seed → gold 승격 요건)
한 문서를 frozen A/B 기준선(gold)에 넣으려면 다음을 **모두** 만족해야 한다:
1. **독립 베테랑의 문서 전체 검토** — 후보 리뷰와 독립적으로, 베테랑 PM이 문서를 처음부터 끝까지 검토.
2. **blocking 인벤토리** — 그 문서의 모든 blocking finding을 열거·심각도 판정(전집합 = recall 분모).
3. **중복 adjudication 완료** — 기존 triage seed와 합치고 중복을 판정·제거.

이 세 조건을 만족한 문서만 frozen A/B에 포함한다. 미충족 문서의 레코드는 `seed`로만 남고 recall 판정에 쓰지 않는다.

## 단계

### Phase 0 — 기존 triage 채굴 (지금, 무료) → adjudicated seed (골드 아님)
스크립트가 위 폴더들을 스캔 → `(artifact_id, finding, human_disposition, blocking?)` 레코드로 정규화 → **`evals/seed/adjudicated-findings.jsonl`** (저장 영역·명칭 모두 `gold`가 아니라 `seed/adjudicated-findings`).
- `reject`/`invalid` = 리뷰어 과탐(false positive) 라벨.
- 사람이 **추가**한 finding(H-xx, 리뷰어가 못 잡은 것) = 과소탐(false negative) 신호. ← blocking-recall의 부분 신호로만 쓰되, **문서 전체 blocking 인벤토리가 아니므로 recall 분모로는 쓰지 않는다**(위 '골드 자격 조건' 참조).

### Phase 1 — 실버 확대 (하베스트한 렌즈 세트 투입)
triage 히스토리가 얇은 문서 영역은, **pm-* 스킬에서 하베스트한 합본 렌즈 세트**를 코퍼스(님 과거 R2/docloop 문서)에 돌린다. 단 자동 생성물의 지위를 아래처럼 제한한다.

- **자동 silver는 렌즈 세트의 `[a]` 구조검사(존재·enum·카운트 등) 하위검사로만 제한**한다.
- **`[b]` 판단형 렌즈의 출력은 silver 라벨이 아니라 `candidate_unadjudicated`로 저장**한다 — 생성기 판단을 정답 라벨로 복제하지 않기 위함. 이 후보는 사람 adjudication을 거쳐야만 라벨이 된다.
- 코퍼스: 님 과거 실제 PRD/기획서(가장 현실적) + 공개 예시 PRD 소수.
- **차원(렌즈 세트 10개 차원)별로 `auto-covered / human-covered / uncovered` 비율을 별도 보고**한다 — coverage gap을 인정만 하지 않고 수치로 드러낸다.

#### provenance 분리 (silver 순환 차단)
"silver는 1순위 제외"만으로는 간접 누수(silver 결과를 보고 reviewer/lens/prompt/threshold를 개선)가 남는다. corpus와 **모든 라벨에 provenance 태그를 문서 단위로** 붙여 세 부류로 격리한다:

- `development-silver` — 렌즈 자동 생성물. **진단 전용**: 오류 분석·학습에만 사용. 후보(reviewer/prompt/lens) 선택·**threshold 조정·최종 A/B 보고 어디에도 사용하지 않는다.**
- `development-gold` — 개발 중 확보한 사람 라벨(렌즈·reviewer 개선 과정에 노출됨). 개발엔 쓰되 최종 기준선에서는 제외.
- `hidden-gold` — 렌즈 작성·reviewer 개선에 **쓰이지 않은** 문서의 숨겨진 베테랑 골드. **최종 A/B 판정에만 단 한 번** 사용.

### Phase 2 — 스코어러 (H-01 지표 구현)
`eval_review.py`: 후보 리뷰어 출력의 finding을 라벨에 매칭 →
- **blocking-recall** (베테랑 blocking 라벨 중 리뷰어가 잡은 비율) = 1순위.
- **precision** = 아래 '비대칭 precision 계약'대로 산출, 사전등록 floor만 통과.

#### finding match 규칙
`위치+주장`은 특징 목록일 뿐 매치 규칙이 아니다(paraphrase·위치 이동·split/merge·중복에 따라 recall이 크게 흔들림). 아래를 **사전등록**한다:
- **매치 단위 = 결함 명제 + 대상 객체/범위 + 위반된 요구 + 영향** (네 요소 대조로 판정, 텍스트 유사도 아님).
- **one-to-one matching 기본.**
- **split**(하나의 gold를 여러 finding으로 쪼갬)·**merge**(여러 gold를 한 finding으로 합침)·**부분포착**·**중복**·**위치 불일치** 처리 규칙을 사전등록.
- 경계 사례는 **blind adjudication** 절차로 판정(절차도 사전등록).

#### 비대칭 precision 계약
라벨 밖의 유효 finding을 감점하지 않는 정책하에서는 unmatched candidate를 자동 FP로 볼 수 없다(중립으로 두면 환각 finding도 분모에서 사라져 floor가 무력화). 따라서:
- 모든 unmatched candidate를 **blinded 사람 adjudication** → `valid-new / duplicate / invalid / unassessable`로 분류.
- **precision은 adjudicated `invalid`를 포함해 계산**한다(valid-new는 감점 아님, 중립~가점).
- 사람 adjudication 없이 **자동 평가만** 하는 경우, 그 지표는 precision이 아니라 **"known-FP challenge-set rejection rate"**로 별도 명명한다(사전 수집된 known-FP 챌린지 세트의 기각률 — 환각 상한을 측정하는 별개 지표).

#### precision floor 사전등록
floor는 결과를 본 뒤 유리하게 고르지 않는다. **실행 전에 고정**한다:
- **값**: 현재 reviewer 또는 베테랑 baseline과 허용 가능한 invalid-finding 검토 비용에서 도출.
- **집계 단위**: **문서별 macro precision** (단일 point estimate 금지).
- **소표본 처리**: 최소 문서 수·최소 blocker 수, 신뢰구간 **하한** 사용.
- **tie/fail 규칙**: 동률·경계 판정 규칙을 명시.

### Phase 3 — A/B 게이트
**골드 자격 조건(위)을 만족한 문서의 `hidden-gold`만** 동결 기준선으로 고정한다(seed·silver·development-gold 제외). 리뷰 렌즈/프롬프트/루브릭을 바꿀 때마다 재채점 → blocking-recall이 올랐고 precision이 **사전등록 floor** 위인가? → 이게 peer-review 스킬이 요구하는 "사전등록 A/B".

## 정직한 한계 (과장 방지)
- **선택 편향**: Phase 0 seed는 "님이 리뷰한 문서"만 커버하고, 처분이 **베테랑이 아니라 님** 판정임. 규모도 작음(N 적음). → seed는 골드가 아니다(위 자격 조건).
- **생성기 편향**: 라벨된 finding은 "그때 리뷰어가 떠올린 것" 위주 → 리뷰어가 애초에 못 본 종류는 라벨에도 없음(그래서 사람이 **추가**한 finding이 특히 귀함).
- **실버 순환**: provenance 분리로 차단 — silver는 진단 전용, 최종 판정은 렌즈/reviewer 개선에 안 쓰인 `hidden-gold`로 단 한 번(위 provenance 분리 참조).
- 결론: 이건 "검증된 골드셋"이 아니라 **부트스트랩 씨앗(adjudicated seed)**. 진짜 베테랑 라벨(외부 PM 소량)로 held-out을 채워가며 점진 업그레이드. design.md의 "not yet operational"은 골드 자격 조건을 만족한 hidden-gold가 붙기 전까진 유효.

## 지금 바로 되는 것 vs 나중
- **지금(골드셋 불필요)**: Phase 0 채굴 스크립트, Phase 2 스코어러(매치 규칙·precision 계약 사전등록 포함), 하베스트 렌즈 세트의 `[a]` 구조검사로 리뷰어 **개선**(채점과 무관하게).
- **나중(사람 필요)**: 골드 자격 조건을 만족하는 held-out 베테랑 라벨 확보 → `candidate_unadjudicated`를 사람 adjudication으로 골드 승격 → A/B 정식 가동.
