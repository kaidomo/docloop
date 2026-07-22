# Role-panel review & prediction lock · 역할 패널 리뷰와 예측 봉인

> Moved from README (2026-07-22).

Two pieces ported (downstream) from the canonical skill repo — same thesis, new instruments.
정본 스킬 저장소에서 다운스트림으로 포팅한 두 조각 — 같은 테제, 새 도구.

**`docloop panel`** — one artifact, several *independent* job-role evaluators (PM · Product Designer ·
Frontend · Backend · QA, or case-specific roles). A role is a **failure-surface contract**
(questions · evidence access · abstain conditions), not a job-title persona. Each role runs as its
**own headless model process**, and role outputs are held outside the review folder until every
role finishes (the prompt additionally forbids reading PANEL_* files) — process separation on one
machine, not an air gap. An
Area Chair synthesis then preserves conflicts and lone criticals, never averages or majority-votes,
marks same-model agreement as *correlated* (recorded, no confidence boost), and hands the human
**at most 5 decision items** (role outputs stay as the appendix).
**`docloop panel`** — 한 산출물을 여러 **독립** 직무 평가자(PM·디자이너·FE·BE·QA 또는 케이스 특화
역할)가 검토한다. 역할은 직함 페르소나가 아니라 **실패면 계약**(질문·증거 접근·abstain 조건)이다.
역할마다 **별도 헤드리스 프로세스**로 돌고, 역할 출력은 전원이 끝날 때까지 리뷰 폴더 밖에 보관된다
(프롬프트도 PANEL_* 파일 열람을 금지) — 같은 머신 안의 프로세스 분리이지 물리적 차단은 아니다.
Area Chair 합성은 충돌·단독
critical을 보존하며 평균·다수결을 쓰지 않고, 같은 모델끼리의 합의는 correlated로 기록만 한다(확신도
불상승). 사람 앞에는 **결정 항목 5건 이하**만 놓인다(역할 원본은 부록).

**`docloop lock` / `docloop verify`** — make "I knew it" falsifiable. Hash a prediction file *before*
the outcome exists (digest goes in a **sidecar**, outside the hashed file), re-hash at reveal; a
mismatch means *judge nothing* (diagnostic-only). For third-party verifiability, commit
payload+sidecar before the reveal. Only this primitive is ported — the full learning lifecycle
stays upstream, and judgment stays with the human.
**`docloop lock` / `docloop verify`** — "그럴 줄 알았다"를 반증 가능하게 만든다. 결과가 존재하기
*전에* 예측 파일을 해시로 봉인하고(digest는 해시 대상 밖 **sidecar**에), 공개 시점에 재해시한다.
불일치면 *판정하지 않는다*(diagnostic-only). 제3자 검증이 필요하면 공개 전에 payload+sidecar를
커밋해 둔다. 포팅된 것은 이 프리미티브뿐 — 학습 lifecycle 전체는 정본(스킬) 쪽에 있고, 판단은
사람의 몫이다.

```bash
docloop review case-x ./PRD_*.md                    # stage + brief (reused)
docloop lock  ~/notes/b1-prediction.md              # optional: seal what you expect the panel to find
docloop panel ~/.docloop/reviews/case-x 1           # 5 default roles, per-process isolation
docloop panel ~/.docloop/reviews/case-x 2 pm qa pv-practitioner   # custom role set (contract in the brief)
docloop verify ~/notes/b1-prediction.md ~/notes/b1-prediction.md.lock.yaml   # reveal: intact? then compare
```
