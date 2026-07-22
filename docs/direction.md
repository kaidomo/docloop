# What's inside today, and direction · 현재 구성과 향후 방향

> Moved from README (2026-07-22). The "What's inside" and "Layout" sections at the bottom
> were moved here from README in the same pass.

**Short version:** what docloop ships today is the checking-and-gates core plus your
`policy.yaml` rules file — that's it. A few larger ideas are designed but **not built**:
loading document types as plug-in packs, generating one document from another, and grading
the AI reviewer against expert judgment. Read this page as a roadmap, not a feature list —
anything written in "would" below does not exist yet.
**한 줄 요약:** 지금 출시된 것은 점검·게이트 코어와 `policy.yaml` 규칙 파일뿐이다. 문서 타입을
플러그인 팩으로 불러오기, 한 문서에서 다른 문서를 뽑아내기, AI 리뷰어를 전문가 판단 대비로
채점하기 — 이 셋은 설계만 되어 있고 **아직 만들지 않았다**. 이 문서는 기능 목록이 아니라
로드맵으로 읽으면 된다. 아래에서 "될 것이다"로 쓰인 것은 아직 없는 것이다.

This section is design direction, not a feature list. **Current:** the protocol-kernel
boundary and the `policy.yaml` variable layer — the shipped verb set is `init · plan ·
draft · audit · review · panel · lock · verify · gate · split` plus the `atb-*` change-plan stages. **Planned,
not shipped:** a domain-pack loader, a derivation-manifest execution path, and the
reviewer-eval gold set. The conditional-tense text below describes where those planned pieces would go.

이 섹션은 기능 목록이 아니라 설계 방향이다. **현재 있는 것:** 프로토콜 커널 경계와 `policy.yaml`
가변층 — shipped verb는 `init · plan · draft · audit · review · panel · lock · verify · gate · split` + `atb-*`
변경계획 스테이지다. **계획이며 미구현:** domain-pack 로더, derivation manifest 실행 경로,
reviewer-eval 골드셋. 아래 조건법 문장은 그 계획된 조각들이 어디로 갈지를 그린다.

The target shape is a **shared validation/execution protocol kernel** rather than the single
canonical engine behind a family of specialized authoring skills. The shipped core already has
the shared protocol-kernel boundary. The domain-pack loader and derivation-manifest execution
path described below remain planned. In that target, document *meaning*
(ontology, prompts, derivations) *would* live in domain packs/skills; declarative org rules
already live in `policy.yaml`; the core *would* own only the protocol — the boundary test
being that **core imports no document type**.

목표 형태는 특화 스킬군의 유일한 정본 엔진이 아니라 **공용 검증/실행 프로토콜 커널**이다. 출시된
core에는 공용 프로토콜 커널 경계가 이미 있다. 아래에 서술하는 domain-pack 로더와 derivation
manifest 실행 경로는 여전히 계획 단계다. 그 목표에서
문서의 *의미*(ontology·프롬프트·파생)는 domain pack/스킬에 두게 *될 것이고*, 선언형 조직 규칙은
이미 `policy.yaml`에 있으며, core는 프로토콜만 소유하게 *될 것이다* — 경계 판정은 **core가 어떤
문서 타입도 import하지 않는다**는 것이다.

Two directions *would* follow. **Derivation** (PRD → storyboard → manual) *would not* be a
core verb — a future domain pack *would* author a *derivation manifest* and the core's
intended role *would be* protocol execution only. And because the **review stage is an
oracle stand-in, it would need grading too**: reviewer quality is **not operational** today,
and the planned metric *would* evaluate it **offline against a veteran-PM gold set**
(blocking-recall, not text similarity) — the gold set does not yet exist.

두 방향이 뒤따르게 *될 것이다*. **파생**(PRD → 스토리보드 → 매뉴얼)은 core verb가 *아니게 될
것이고* — 향후 domain pack이 *derivation manifest*를 쓰고 core의 역할은 실행만으로 한정될 *것이다*.
그리고 **review 단계는 오라클 대용이라 그 자체도 채점 대상이 될 것**이다: 리뷰어 품질은 현재
**미가동(not operational)**이며, 향후 지표는 이를 **베테랑 PM 골드셋 대비 오프라인**(텍스트 유사도가
아니라 blocking-recall)으로 **측정할 계획**이다 — 골드셋은 아직 존재하지 않는다.

**Design & rationale · 설계와 근거**:
[`design.md`](design.md) (protocol kernel · 프로토콜 커널) ·
[`reviewer-eval-bootstrap.md`](reviewer-eval-bootstrap.md) (grading the reviewer · 리뷰어 채점) ·
[`reviewer-lens-set.md`](reviewer-lens-set.md) (73 review lenses · 리뷰 렌즈 73) ·
[`cold-start-strategies.md`](cold-start-strategies.md) (evidence acquisition · 증거 획득).

## What's inside · 안에 있는 것

docloop adds **no new runtime and no new agent.** The value is in three things:
docloop은 **새 런타임도 새 에이전트도 만들지 않는다.** 가치는 세 가지에 있다:

(Two terms, once: the **kernel** is the checking layer everything else sits on; a
**manifest** is the work-state file that records what the document promised and what
was checked.)
(용어 두 개만: **커널**=나머지가 그 위에 얹히는 점검 레이어. **manifest**=문서가 뭘
약속했고 뭘 검사했는지 기록하는 작업 상태 파일.)

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

## Layout · 구성

```
bin/docloop          thin launcher (wraps codex / claude -p)
prompts/             stage prompts — doc mode: plan/draft/gap-audit/review · change-plan mode: atb-capture/atb-chunk/atb-author/atb-audit
lib/                 python scripts: init, validate, gap_audit, ground_audit, split, approval_brief, stage, ...
templates/           policy + manifest skeletons (doc + .atb change-plan variants), review-brief template
docs/design.md       why documents need a verification kernel (not just a writing loop); design decisions (protocol kernel, reviewer-eval)
docs/reviewer-eval-bootstrap.md   bootstrapping a reviewer-quality gold set from review residue · 리뷰 잔여물에서 리뷰어 골드셋 부트스트랩
docs/reviewer-lens-set.md         document-review lenses harvested from PM skills (55 → 73 criteria) · PM 스킬에서 하베스트한 문서 리뷰 렌즈
docs/cold-start-strategies.md     initial evidence-acquisition patterns for authoring · 저작 초기 증거 획득 패턴
```
