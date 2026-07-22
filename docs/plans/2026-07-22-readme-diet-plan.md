# docloop README 다이어트 계획 (v5 트랙 — 언어 분리 + 한 화면 축소 + docs/ 이관)

> 작성: 2026-07-22 (Claude Code). 배경: v3(실용 우선)·v4(기획자 언어) 후에도 "여전히 너무
> 어렵고 기술적으로 복잡하다"(메인테이너). 원인 진단: ①영·한 병기로 밀도 2배 ②README가
> 설계 문서 겸직(13절) ③문장마다 단서 괄호. 처방: 재작성이 아니라 **다이어트**.

## 원칙

- **언어 분리**: `README.md`=영어 전용, `README.ko.md`=한국어 전용(표준 OSS 패턴). 서로 링크.
- **README는 한 화면(~70줄/언어)**: 리드 2문장 → What you can do 5 bullet(각 1줄) →
  Install+Quick start → mermaid 1개 → **Limitations 3줄**(흩어진 괄호 단서를 여기로 모음) →
  Learn more 링크 목록 → License.
- 정직성 보존: 단서를 지우는 게 아니라 **Limitations 절로 집결**. 과잉 주장 금지 유지
  (r2·r3에서 확정된 완화 문안의 의미를 잃지 않게).
- 독자 페르소나(v4) 유지: AI CLI 쓰는 기획자.

## 이관 (내용 무손실 — README에서 빼서 docs/로)

| README 절 | 이관처 | README 잔여 |
|---|---|---|
| Change-plan mode 전체 | `docs/change-plan-mode.md` (신규, 기존 문안 이동) | Learn more 링크 1줄 |
| Role-panel & prediction lock | `docs/panel-and-lock.md` (신규, 기존 문안 이동) | Learn more 링크 1줄 |
| Direction (planned) | `docs/direction.md` (신규, 기존 문안 이동) | Learn more 링크 1줄 |
| Why documents need a verification kernel | design.md가 이미 정본 — 절 삭제 | 리드 인용 1줄 + 링크 |
| Where docloop draws the line | design.md 링크로 대체 | Learn more 링크 1줄 |
| The variable layer: policy.yaml | `docs/policy-layer.md` (신규, 이동) | Quick start 주석 1줄("조직 규칙은 policy.yaml 한 파일") |
| Layout | README.ko/en 하단 축약 유지(5줄 이내) 또는 이관 | 구현 재량 |

- 이관 파일 머리에 "moved from README (2026-07-22)" 한 줄. 이관 문안은 기존 영·한 병기
  그대로(이번 트랙은 이동만 — 이관 문서의 기획자화는 별건).
- What-you-can-do 7 bullet → **5개 1줄 bullet**로 압축: 모순 리포트 / 근거 없는 주장(변경계획) /
  인용 어긋남 / 외부 AI 공격+사람 승인 / 정본→배포 페이지. panel·lock은 Learn more로.
- Limitations 3줄(고정 문안 방향): "찾는 건 AI다 — 판정이 아니라 검토 보조" / "출처가 참인지는
  증명하지 않는다" / "gate→split 순서는 워크플로이지 강제가 아니며, 최종 판단은 언제나 사람".

## 하지 않는 것

- 코드·verb·bin/docloop 변경 없음. 이관 문서의 내용 수정 없음(이동만). CHANGELOG 불변.
- 새 주장 없음. 이중언어 병기는 README 두 파일 분리로 대체(이관 문서는 병기 유지).

## 부록 (v2, 메인테이너 지시): 이관 문서 기획자화 + README 역수입

- **대상**: docs/change-plan-mode.md · panel-and-lock.md · policy-layer.md (3개).
  direction.md는 로드맵 성격이라 리드 2~3문장만 쉬운 말로, 본문 유지. design.md는 기술 정본
  그대로(예외).
- **문서 구조 패턴(각 문서 동일)**:
  1. 윗부분 = 기획자 언어(README와 같은 톤): 이게 뭐고 언제 쓰나 2~3문장 → 실제로 하는 법
     (명령+한 줄 주석) → 한계 1~2줄. v4 페르소나 규칙 적용(엔지니어 어휘 추방/풀이).
  2. "자세한 동작은 아래 기술 상세 참고" 연결 한 줄.
  3. `## Technical details · 기술 상세` = 기존 기술 문안 **이동**(삭제 아님, 무손실 유지).
- **README 역수입(선택)**: 쉬운 윗부분에서 나온 핵심 1~2문장이 링크 한 줄보다 명확하면
  "더 알아보기" 항목을 그 문장으로 업그레이드 가능 — 단 **README 한 화면 예산(언어당
  ~90줄) 초과 금지**, 초과 시 역수입 포기.
- 정직성 마커(검토 보조·비강제·진단 전용 등)는 쉬운 윗부분에도 유지.
- 검증·리뷰: 기존과 동일 + 이 부록 구현은 다이어트 루프의 페르소나 리뷰 r2로 검증.

## 실행 순서

1. 브랜치 `docs/readme-diet` → 구현.
2. 검증: tests green · check_ports 통과 · 변경 파일 leak scan clean · README 두 파일 상호 링크
   확인 · 이관 후 내용 무손실 확인(diff로 이동분 대조).
3. Codex 페르소나 리뷰(신규 루프 r1) → 반영 → PR.
