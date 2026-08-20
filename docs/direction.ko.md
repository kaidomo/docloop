# 현재 구성과 향후 방향

English: [direction.md](direction.md)

> README에서 이동됨(2026-07-22). 하단의 "안에 있는 것"과 "구성" 섹션도 같은 작업에서
> README에서 이 문서로 옮겨졌다.

**한 줄 요약:** 지금 docloop이 출시한 것은 README에 나온 전부다 — 작성·변경계획 흐름, 리뷰
도구, 점검과 게이트, 그리고 당신의 `policy.yaml` 규칙 파일까지. 더 큰 아이디어 몇 가지는
설계만 되어 있고 **아직 만들지 않았다**: 문서 타입을 플러그인 팩으로 불러오기, 한 문서에서
다른 문서를 뽑아내기, AI 리뷰어를 전문가 판단 대비로 채점하기. 이 문서는 기능 목록이 아니라
로드맵으로 읽어야 한다 — 아래에서 "될 것이다"로 쓰인 것은 아직 존재하지 않는 것이다.

이 섹션은 기능 목록이 아니라 설계 방향이다. **현재 있는 것:** 프로토콜 커널 경계와 `policy.yaml`
가변층 — shipped verb는 `init · plan · draft · audit · review · review-gate · panel · lock · verify · gate · split ·
contribute · curate · draft-curated` + `atb-*`
변경계획 스테이지다. **계획이며 미구현:** domain-pack 로더, derivation manifest 실행 경로,
reviewer-eval 골드셋. 아래 조건법 문장은 그 계획된 조각들이 어디로 갈지를 그린다.

목표 형태는 특화 스킬군의 유일한 정본 엔진이 아니라 **공용 검증/실행 프로토콜 커널**이다. 출시된
core에는 공용 프로토콜 커널 경계가 이미 있다. 아래에 서술하는 domain-pack 로더와 derivation
manifest 실행 경로는 여전히 계획 단계다. 그 목표에서
문서의 *의미*(ontology·프롬프트·파생)는 domain pack/스킬에 두게 *될 것이고*, 선언형 조직 규칙은
이미 `policy.yaml`에 있으며, core는 프로토콜만 소유하게 *될 것이다* — 경계 판정은 **core가 어떤
문서 타입도 import하지 않는다**는 것이다.

두 방향이 뒤따르게 *될 것이다*. **파생**(PRD → 스토리보드 → 매뉴얼)은 core verb가 *아니게 될
것이고* — 향후 domain pack이 *derivation manifest*를 쓰고 core의 역할은 실행만으로 한정될 *것이다*.
그리고 **review 단계는 오라클 대용이라 그 자체도 채점 대상이 될 것**이다: 리뷰어 품질은 현재
**미가동(not operational)**이며, 향후 지표는 이를 **베테랑 PM 골드셋 대비 오프라인**(텍스트 유사도가
아니라 blocking-recall)으로 **측정할 계획**이다 — 골드셋은 아직 존재하지 않는다.

**설계와 근거**:
[`design.md`](design.md) (프로토콜 커널) ·
[`reviewer-eval-bootstrap.md`](reviewer-eval-bootstrap.md) (리뷰어 채점) ·
[`reviewer-lens-set.md`](reviewer-lens-set.md) (리뷰 렌즈 73) ·
[`cold-start-strategies.md`](cold-start-strategies.md) (증거 획득).

## 안에 있는 것

docloop은 **새 런타임도 새 에이전트도 만들지 않는다.** 가치는 세 가지에 있다:

(용어 두 개만: **커널**=나머지가 그 위에 얹히는 점검 레이어. **manifest**=문서가 뭘
약속했고 뭘 검사했는지 기록하는 작업 상태 파일.)

1. **점검기와 게이트** (`lib/`) — 팬아웃 감사(모델 보조: 정합의 gap-audit, 증거 근거성의
   ground-audit)가 결정론적 manifest 검증·릴리스 게이트·verbatim 대조·예측 파일 무결성
   확인(lock/verify, 진단 전용)으로 이어진다. 가능한 점검은 결정론적으로 수행하고, 그렇지
   않은 점검은 성공을 가장하지 않고 한계를 드러낸다.
2. **리뷰 프로토콜** — 외부 모델 교차 리뷰(`prompts/review.md`: finding ID·triage·사람
   승인 게이트·명시적 종료 상태), 역할 패널 리뷰(`panel`: 분리된 역할 실행·Area Chair 합성·
   사람 결정 핸드오프), 명시적 `review-gate` 패킷 준비기. `review-gate`는 대상 하나와 결정론
   입력을 L1/L2/L3 envelope로 동결하고 선택적 pre-lens 규약 쌍을 검증한 뒤 fresh-context
   모델 실행을 위해 멈춘다. 합성 이후에는 결정론 중간 원장과 패킷 결합 receipt로 검증·사람
   판단을 지원한다.
   자동 리뷰어나 격리 계층이 아니다.
3. **저작 파이프라인** (`prompts/`) — 저작 레이어는 커널의 클라이언트이며, 현재 두
   파이프라인을 담는다: 문서 모드(plan → draft → audit → review → gate → split)와
   변경계획 모드(`atb-*`).

## 구성

```
bin/docloop          codex / claude -p를 감싸는 얇은 런처
prompts/             단계별 프롬프트 — 문서 모드: plan/draft/gap-audit/review · 변경계획 모드: atb-capture/atb-chunk/atb-author/atb-audit
lib/                 파이썬 스크립트: init, validate, gap_audit, ground_audit, split, approval_brief, stage, ...
templates/           policy + manifest 스켈레톤(문서용 + .atb 변경계획 변형), review-brief 템플릿
docs/design.md       왜 문서에 (단순 작성 루프가 아니라) 검증 커널이 필요한가; 설계 결정(프로토콜 커널, reviewer-eval)
docs/review-gate.md  명시적 패킷 준비 CLI, 산출물, 제한, 수동 검증 계약
docs/reviewer-eval-bootstrap.md   리뷰 잔여물에서 리뷰어 골드셋 부트스트랩
docs/reviewer-lens-set.md         PM 스킬에서 하베스트한 문서 리뷰 렌즈(55 → 73개 기준)
docs/cold-start-strategies.md     저작 초기 증거 획득 패턴
```
