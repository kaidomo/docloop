# docloop 재포지셔닝 계획: writing harness → verification kernel (v4 — 독자=기획자 언어)

> 작성: 2026-07-22 (Claude Code). 개정: 같은 날, 계획 리뷰 r1 9건 반영(v2) → 메인테이너 지시로
> **실용 우선 가독성 원칙** 추가(v3): "철학은 어느 정도만, 실제로 뭘 할 수 있는지가 잘 드러나게."
> 배경: 2026-07-22 인벤토리에서 repo의 50%(18/36)가 리뷰/검증 자산이고 그중 기계 게이트·감사(9)가
> 리뷰 루프(4)보다 두꺼움을 확인. README는 기존 '글쓰기 하네스' 정체성 문구로 자칭하지만 design.md 결정
> 기록은 이미 "shared validation/execution protocol kernel"로 자기규정 — 이 균열을 해소하고
> 정체성을 검증 커널 쪽으로 공식 전환한다(메인테이너 결정).

## 원칙 (경계)

- **문서·포지셔닝만 변경.** 코드 동작·verb·파일 구조·PORTS 불변(bin/docloop은 주석·help 문자열만).
- **정직성 유지**: reviewer-eval 미가동, 골드셋 부재, 품질 주장 금지 문구 전부 보존.
  **"deterministic"은 실제 결정론인 곳(manifest 검증·게이트·해시 봉인)에만 붙이고, gap/ground
  audit처럼 모델 fan-out이 수행하는 감사는 model-assisted로 명시**(r1-02) — 감사 수행과
  결과 게이트를 구분해 서술한다.
- 이중언어(영·한) 병기 스타일 유지. 의미 parity 우선(r1-09).
- **실용 우선 가독성 (v3, 메인테이너 지시)**:
  - 정체성 블록 직후에 **"What you can do · 뭘 할 수 있나"** 절 신설 — 결과 중심 bullet 5~7개
    (예: "PRD↔스토리보드↔매뉴얼 불일치를 리포트로 받는다(gap-audit)", "as-is 주장마다 증거가
    실존하는지 감사한다(ground-audit)", "인용이 출처와 한 글자라도 다르면 게이트가 막는다
    (verbatim)", "외부 모델이 초안을 공격하고, 반영은 사람이 승인한 것만(review 루프)",
    "5개 직무 역할이 독립적으로 뜯어본다(panel)", "릴리스 게이트 통과분만 배포 페이지로
    분할(split)"). 각 bullet은 기능명이 아니라 **얻는 결과**로 시작.
  - **Quick start를 위로**: 정체성 블록 + What-you-can-do 바로 다음. 철학보다 먼저.
  - **철학 압축**: 오라클 논증 절("Why documents need…")은 핵심 3~4문장으로 줄이고 상세는
    design.md 링크로 위임. 인용 블록도 2줄 이내.
  - **mermaid 단순화**: 추상 레이어 다이어그램(dom/pol/core) 대신 **실사용 흐름 하나** —
    plan → draft → audit → review → gate → split에 각 단계가 "뭘 잡는지" 짧은 라벨.
    다이어그램은 1개만, 전문용어 최소화.
  - Direction(계획) 절은 유지하되 아래쪽 그대로, 분량 증가 금지.
- **독자 페르소나 (v4, 메인테이너 지시)**: 문서를 쓰는 사람 = **AI CLI(Claude Code/Codex)를
  쓰는 기획자**. 규칙:
  - **첫 화면(정체성~Quick start)에서 엔지니어 어휘 추방**: kernel·manifest·SSOT·fan-out·
    deterministic·scaffold 등은 첫 화면에서 쓰지 않거나 기획자 말로 번역(SSOT→정본 문서,
    manifest→작업 상태 파일 등). PM 어휘(PRD·스토리보드·매뉴얼·정본·컨플루언스)는 그대로.
  - 정체성 리드는 혜택 문장으로: "기획 문서를 쓰면, 리뷰어보다 먼저 docloop이 어긋난 곳을
    잡아준다" 톤. kernel 정체성 문장은 그 아래 한 줄(기술 독자용)로 강등.
  - mermaid 라벨도 기획자 언어("plan: 뭘 쓸지 인터뷰로 정리" 톤).
  - What's inside 이하 기술 절에서는 kernel/기술 용어 허용(정체성 유지) — 단 첫 등장 시
    한 줄 풀이. 영어판도 동일 원칙(PM이 읽는 영어).
  - 과잉 주장 금지 원칙은 그대로 — 쉬운 말이 곧 더 센 주장이 되지 않게(r2-01·02 완화 유지).
- 용어 계약(r1-04): **authoring layer가 커널의 client**이고, 그 layer 안에 pipeline이 둘(doc
  mode·change-plan mode) 있다. "client"를 파이프라인·consumer role의 뜻으로 혼용하지 않는다.
  design.md의 authoring/evaluator consumer role 구분은 그대로 둔다.

## 변경 1 — README 정체성 블록 (lines 1–13)

**to-be**:

```markdown
# docloop

**A verification-first document kernel** — mechanical gates and model-assisted audits
for documents (PRDs, specs, policies, change plans), wrapped around a model CLI you
already use (`codex` or `claude -p`). The authoring layer is a client of the kernel,
not the kernel itself.
**검증 우선 문서 커널** — 문서(PRD·명세서·정책서·변경계획)를 위한 기계 게이트와 모델 보조
감사를, 이미 쓰는 모델 CLI(`codex` 또는 `claude -p`) 위에 얹는다. 저작(글쓰기) 레이어는
커널의 클라이언트이지 커널 자체가 아니다.

> **Writing has no single oracle** — so docloop is built the other way around: check what
> can be checked (source-grounded accuracy, consistency, policy), surface the gaps, and
> stop; judgment stays with the human. The kernel is the checking layer; authoring flows
> are clients built on it.
> **글에는 단일 오라클이 없다** — 그래서 docloop은 반대 방향으로 지어졌다. 검증 가능한 것
> (출처 대비 정확성·정합·정책)만 점검해 빈틈을 드러내고 멈춘다. 판단은 사람의 몫이다.
> 커널은 점검 레이어이고, 저작 플로우는 그 위에 지어진 클라이언트다.
```

## 변경 2 — 가치 3항목 재서열 (lines 15–25)

```markdown
1. **The checks & gates** (`lib/`) — fan-out audits (model-assisted: gap-audit for
   consistency, ground-audit for evidence grounding) feeding deterministic manifest
   validation, release gates, verbatim comparison, and prediction-file integrity
   (lock/verify; diagnostic-only). Deterministic where applicable; otherwise fail-honest.
   <br>**점검기와 게이트** (`lib/`) — 팬아웃 감사(모델 보조: 정합의 gap-audit, 증거 근거성의
   ground-audit)가 결정론적 manifest 검증·릴리스 게이트·verbatim 대조·예측 파일 무결성
   확인(lock/verify, 진단 전용)으로 이어진다. 가능한 점검은 결정론적으로 수행하고, 그렇지
   않은 점검은 성공을 가장하지 않고 한계를 드러낸다.
2. **The review protocols** — external-model cross-review (`prompts/review.md`: finding
   IDs, triage, a human approval gate, explicit termination states) and role-panel review
   (`panel`: independent role runs, Area Chair synthesis, human decision handoff).
   <br>**리뷰 프로토콜** — 외부 모델 교차 리뷰(`prompts/review.md`: finding ID·triage·사람
   승인 게이트·명시적 종료 상태)와 역할 패널 리뷰(`panel`: 독립 역할 실행·Area Chair 합성·
   사람 결정 핸드오프).
3. **The authoring pipelines** (`prompts/`) — the authoring layer is a client of the
   kernel; it currently contains two pipelines: doc mode (plan → draft → audit → review →
   gate → split) and change-plan mode (`atb-*`).
   <br>**저작 파이프라인** (`prompts/`) — 저작 레이어는 커널의 클라이언트이며, 현재 두
   파이프라인을 담는다: 문서 모드(plan → draft → audit → review → gate → split)와
   변경계획 모드(`atb-*`).
```

## 변경 3 — "Why a *writing* harness differs" 절 제목·리드 (line 27)

제목: "Why documents need a verification kernel (not just a writing loop) · 왜 문서에는
(글쓰기 루프가 아니라) 검증 커널이 필요한가". 본문 논지 그대로, 리드에 커널 연결 한 문장.

## 변경 4 — "Where docloop draws the line" + Direction 절

- "shared protocol" → "shared **validation/execution protocol kernel**" (design.md와 통일).
- Direction 시제(r1-03): 두 문장으로 분리 — "The shipped core already has the shared
  protocol-kernel boundary. The domain-pack loader and derivation-manifest execution path
  described below remain planned." (한글 동일 구조). 이후 조건법·planned 표기 전부 유지.

## 변경 5 — GitHub repo description

`Verification-first document kernel — mechanical gates + model-assisted audits and
disciplined review protocols for PRDs/specs/change plans (authoring flows included)`

## 변경 6 — design.md 최소 정렬

- 제목(line 1)과 미래형 "docloop evolves into … kernel"(line 212 부근)만 현재형 정체성과
  정렬(본문 결정 기록은 무변경).

## 변경 7 — identity-copy sweep (r1-06, 동작 무변경)

README 잔여 문자열: line 57 "The harness surfaces…"(→ kernel 문안), line 68 "where this
is meant to go"/"지향점"(변경 4와 함께 정렬), Layout line 246 "why writing harnesses
differ"(새 절 제목과 동기화). `bin/docloop`: 파일 헤더 주석과 `--help` 출력의 기존
writing-harness 정체성 문구만 kernel 문안으로 수정(동작·verb 무변경). CHANGELOG의 과거 릴리스
표현은 역사 기록이므로 불변.

## 하지 않는 것

- repo 이름 변경 없음. verb·CLI 동작·코드 로직 변경 없음. PORTS.md 무변경.
- 새 기능·성능·품질 주장 없음. 상류 스킬 repo와의 관계 서술 불변.

## 실행 순서

1. ~~계획 Codex 리뷰~~ (r1 완료, 9건 반영 — 이 문서가 개정본).
2. 브랜치 `docs/verification-kernel-repositioning` → 변경 1~7 구현.
3. 검증(r1-07 반영): `tools/leak_scan.sh '<maintainer-supplied-private-token-regex>'`
   실행 — **exit 0이 통과 조건**(인자 없는 호출은 usage error). 실제 비공개 토큰 목록은
   repo 밖에서 공급. + `tools/check_ports.py` 통과 + `tests/run_tests.py` green.
4. Codex 결과물 리뷰(r2) → PR.
5. 머지 후: 메인테이너 개인 체크리스트 갱신(out-of-repo).
