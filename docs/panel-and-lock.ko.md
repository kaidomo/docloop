# 역할 패널 리뷰와 예측 봉인

English: [panel-and-lock.md](panel-and-lock.md)

> README에서 이동함 (2026-07-22).

초안에 붙이는 두 도구. **`panel`**은 초안을 여러 직무 관점(PM·디자이너·FE·BE·QA)에서 한 번에
검토받고 싶을 때 쓴다 — 각자 따로 보고, 어느 관점도 다수결로 지워지지 않게 종합한다.
**`lock`** / **`verify`**는 결과가 나오기 *전에* 예측을 못 박아, 나중에 "그럴 줄 알았다"를 실제로
확인할 수 있게 할 때 쓴다.

실제로 하는 법:

```bash
docloop review case-x ./PRD_*.md              # stage the draft for review
docloop lock  ~/notes/b1-prediction.md        # optional: seal what you expect the panel to find
docloop panel ~/.docloop/reviews/case-x 1     # 5 default job roles review it, each on its own
docloop verify ~/notes/b1-prediction.md ~/notes/b1-prediction.md.lock.yaml   # reveal: was it untouched? then compare
```

한계: 패널 역할은 사람 전문가가 아니라 AI가 관점을 나눠 본 것이다 — 준비된 관점으로 읽고, 결정은
당신 몫이다. `lock`/`verify`는 예측 파일이 그대로였는지만 증명할 뿐, 그 자체로 판정하지 않는다(진단 전용).

역할을 어떻게 떼어놓는지, 의장이 어떻게 합치는지, 봉인 파일이 어떻게 동작하는지는 아래
**기술 상세**를 참고.

## 기술 상세

정본 스킬 저장소에서 다운스트림으로 포팅한 두 조각 — 같은 테제, 새 도구.

**`docloop panel`** — 한 산출물을 여러 **독립** 직무 평가자(PM·디자이너·FE·BE·QA 또는 케이스 특화
역할)가 검토한다. 역할은 직함 페르소나가 아니라 **실패면 계약**(질문·증거 접근·abstain 조건)이다.
역할마다 **별도 헤드리스 프로세스**로 돌고, 역할 출력은 전원이 끝날 때까지 리뷰 폴더 밖에 보관된다
(프롬프트도 PANEL_* 파일 열람을 금지) — 같은 머신 안의 프로세스 분리이지 물리적 차단은 아니다.
Area Chair 합성은 충돌·단독 critical을 보존하며 평균·다수결을 쓰지 않고, 같은 모델끼리의 합의는
correlated로 기록만 한다(확신도 불상승). 사람 앞에는 **결정 항목 5건 이하**만 놓인다(역할 원본은
부록).

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
