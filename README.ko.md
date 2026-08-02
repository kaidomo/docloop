# docloop

English: [README.md](README.md)

**기획 문서를 쓰면, 리뷰어보다 먼저 docloop이 어긋난 곳을 잡아준다.**
PRD·정책서·변경계획을 쓰고 나서 터미널에서 docloop을 돌린다. 이미 쓰는 AI CLI(`codex`
또는 `claude -p`)는 docloop이 대신 구동하고, 문서에는 당신이 승인한 내용만 반영된다.

> 속은 이렇다: 검증 가능한 것만 점검해 빈틈을 드러내고 멈춘다 — 판단은 사람의 몫이다.
> 이 방식의 이름이 **검증 우선 문서 커널**이다. 왜 그런지는: [`docs/design.md`](docs/design.md).

## 뭘 할 수 있나

- **PRD·스토리보드·매뉴얼이 서로 어긋난 곳을 리포트로 받는다** — `audit`가 문서들을 대조해 모순을 보고한다.
- **변경계획의 as-is 주장마다 근거가 실제로 있는지 확인한다** — 출처 없는 주장은 계획을 넘기기 전에 걸린다(변경계획 모드).
- **인용이 원본과 달라지면 잡는다** — 별도의 동반 검사가 인용을 출처와 대조해(띄어쓰기 차이는 무시) 어긋난 것을 표시한다.
- **외부 AI가 초안을 공격하게 하고, 반영은 당신이 승인한 것만** — 지적마다 번호가 붙고 반영/기각을 정한다.
- **선택한 직무 관점을 초안 전에 따로 모은다** — `contribute`가 추적 가능한 제안을 별도 기록하고, 사람이 결정·자료를 보충한 뒤 `curate`가 검증된 선택 입력으로 정리한다.
- **정본 문서를 컨플루언스 등에 올릴 페이지로 쪼갠다** — `split`이 하나뿐인 정본에서 페이지를 잘라낸다. 배포본은 언제든 재생성.

## 시작하기

### 설치

```bash
git clone https://github.com/kaidomo/docloop && cd docloop
pip install -r requirements.txt       # 점검기가 쓰는 라이브러리 하나(PyYAML)
chmod +x bin/docloop
export PATH="$PWD/bin:$PATH"          # 이 터미널 세션에서 docloop 사용(유지하려면 이 줄을 셸 프로필에 추가)
export DOCLOOP_MODEL=codex            # docloop이 구동할 AI CLI: codex 또는 claude
```

필요 사항: Python 3 + PyYAML, 그리고 `codex` 또는 `claude` CLI 중 하나가 PATH에 있어야 한다.
`DOCLOOP_MODEL=claude`에서 선택형 `contribute`를 쓸 때는 Claude Code의
`--safe-mode`와 `--json-schema` 옵션이 필요하다(2.1.220에서 확인). 평범한 `plan`과
`draft`의 Claude 호출은 기존 그대로다. 새 `draft-curated`는 비대화형 `acceptEdits`
모드에서 `Read`, `Glob`, `Grep`, `Edit`, `Write`만 허용하며 shell 실행 도구는 주지
않는다.

### 빠른 시작

```bash
docloop init ~/work/case-submission ./submission-policy.md   # 작업 폴더 생성(전달한 입력 파일은 그 안의 inputs/로 이동된다)
cd ~/work/case-submission
cp /path/to/docloop/templates/policy.example.yaml ./policy.yaml   # 조직 규칙은 policy.yaml 한 파일 — 맞게 수정

docloop plan  "케이스 제출 흐름 PRD"                # 짧은 인터뷰로 뭘 쓸지 합의
docloop draft                                       # 출처가 뒷받침하는 것만 쓴다
docloop audit                                       # 문서끼리 모순 찾기
docloop review case-submission ./PRD_*.md           # 외부 AI 교차 리뷰 준비(다음 단계로 공격 실행을 안내)
docloop gate                                        # 최종 검사: 안 풀린 문제가 있으면 막는다
docloop split                                       # 정본을 배포 페이지로 쪼갠다
```

```mermaid
flowchart LR
  P["plan<br/>뭘 쓸지 인터뷰로 정리"] --> D["draft<br/>근거 있는 것만 쓴다"]
  D --> A["audit<br/>문서끼리 모순 찾기"]
  A --> R["review<br/>외부 AI 교차 리뷰(준비 후 실행)"]
  R --> G["gate<br/>안 풀린 문제 있으면 막기"]
  G --> S["split<br/>정본을 배포 페이지로"]
```

### 선택 사항: 초안 전에 관점 기여 받기

위의 기본 `plan → draft` 경로는 그대로다. 여러 관점을 명시적으로 먼저 받고 싶을
때만 다음 별도 분기를 추가한다.

```bash
docloop contribute cc-20260803-01 pm qa backend
cp work/contributions/cc-20260803-01/payload/human-response.template.yaml \
  work/contribution-responses/cc-20260803-01.yaml
# 복사본에서 모든 처분을 기록하고 inputs/ 아래 자료를 추가한 뒤 attestation을 채운다.
docloop curate cc-20260803-01 cur-20260803-01 \
  work/contribution-responses/cc-20260803-01.yaml
docloop draft-curated cur-20260803-01
```

각 단계는 선택형이다. `contribute`와 `curate`는 다음 단계를 자동 호출하지 않고,
평범한 `docloop draft`는 bundle을 찾아 쓰지 않는다. 각 관점 호출은 동일하게 캡처된
입력 바이트를 받고 별도 산출물을 만들지만 격리되거나 독립된 agent가 아니며, 전문성·
정확성·완전성·합의를 보장하지 않는다. bundle에는 입력, 모델 출력, 사람의 판단,
보충 자료의 복사본이 남고 설정한 provider가 source 내용을 받을 수 있다. 답변 schema,
Codex 기여 호출은 read-only sandbox를, curated 초안 호출은 workspace-write를 요청하지만
이 provider 설정을 격리 보장으로 주장하지 않는다.
accepted-state 검증, 제한, privacy, 동시 실행, 수동 보존·삭제는
[기여·큐레이션 가이드](docs/contribute-curate.md)를 참고한다.

## 한계

- 찾는 건 AI 모델이다 — `audit`·`review`·`panel` 리포트는 판정이 아니라 눈 밝은 검토 보조로 쓴다.
- docloop은 당신이 고른 출처와 문서를 대조할 뿐, 그 출처가 참임을 증명하지 않는다.
- 기여 답변의 attestation은 자기 확인일 뿐 사람의 신원·작성자·권한을 인증하지 않는다.
- 검사 후 `split` 순서는 워크플로이지 도구가 강제하지 않는다 — 최종 판단은 언제나 사람의 몫이다.

## 더 알아보기

- [`docs/change-plan-mode.md`](docs/change-plan-mode.md) — 이미 있는 시스템의 수정을 계획하는 as-is/to-be 파이프라인(`atb-*`).
- [`docs/panel-and-lock.md`](docs/panel-and-lock.md) — 초안을 여러 직무 관점에서 한 번에 검토받고(`panel`), 결과가 나오기 전에 예측을 못 박아 둔다(`lock` / `verify`).
- [`docs/contribute-curate.md`](docs/contribute-curate.md) — 관점 기여를 선택적으로 모으고 사람의 결정·자료를 보충해 검증된 초안 입력으로 넘기는 흐름.
- [`docs/policy-layer.md`](docs/policy-layer.md) — 조직 문서 규칙을 담는 한 파일(`policy.yaml`).
- [`docs/direction.md`](docs/direction.md) — 지금 안에 있는 것, 그리고 계획이지만 미출시인 것.
- [`docs/design.md`](docs/design.md) — 왜 문서에는 검증 커널이 필요한가, docloop이 긋는 선.

## 라이선스

MIT — [LICENSE](LICENSE) 참고.
