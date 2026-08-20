# 선택적 기여·큐레이션 흐름

English: [contribute-curate.md](contribute-curate.md)

`contribute`, `curate`, `draft-curated`는 초안을 쓰기 전에 여러 이름 붙은 관점을
모으는, 명시적으로 옵트인해야 하는 분기다. 이 분기 안의 어떤 것도 직접 호출하지
않으면 실행되지 않는다. 기존 `docloop plan`과 `docloop draft` 명령은 기여·큐레이션
bundle을 자동으로 찾아 쓰지 않으며, 그 프롬프트·인자·manifest·SSOT 동작은 그대로다.

선택한 직무 관점에서 추적 가능한 제안을 받고, 그 제안이 초안에 어떤 의미인지 사람이
판단하게 하고 싶을 때 이 분기를 쓴다.

## 워크플로

초기화된 docloop 작업 폴더에서 다음 명령들을 실행한다.

```bash
docloop plan "PRD for the case submission flow"

docloop contribute cc-20260803-01 pm qa backend

cp work/contributions/cc-20260803-01/payload/human-response.template.yaml \
  work/contribution-responses/cc-20260803-01.yaml

# Edit the copied response. Add any supplemental files under inputs/.

docloop curate cc-20260803-01 cur-20260803-01 \
  work/contribution-responses/cc-20260803-01.yaml

docloop draft-curated cur-20260803-01
```

각 명령은 독립된 동작이다.

1. `contribute <run-id> <perspective> <perspective> [...]`는 현재 manifest·policy·
   input을 캡처한 뒤, 선택한 관점마다 설정된 모델을 개별로 호출한다. `pm`,
   `product-designer`, `frontend`, `backend`, `qa` 중 두 개에서 다섯 개까지 관점을
   지정한다. 숨겨진 기본 목록은 없다.
2. `human-response.template.yaml`을 복사한다. 생성된 템플릿 자체를 편집하거나
   그대로 넘기지 않는다. 복사본에서 사람이 모든 기여 항목마다 하나의 처분을
   기록하고, 필요한 곳에 decision과 자료 참조를 추가하며, attestation 필드를
   명시적으로 채운다.
3. `curate <source-run-id> <curation-id> <human-response.yaml>`는 완료된 기여,
   응답, 보충 자료를 검증한다. draft 작성 노트와 미해결 질문 목록을 결정론적으로
   만든다. 모델을 호출하지 않으며 `manifest.yaml`, 본문 SSOT, 섹션 승인 상태를
   수정하지 않는다.
4. `draft-curated <curation-id>`는 완료된 curation과 그 다이제스트를 검증한 뒤,
   `draft-notes.md`의 정확한 바이트를 표시된 선택-입력 블록에 담아 일반 초안 작성
   모델에 전달한다. 별도의 notes 인자는 받지 않는다.

`contribute`는 `curate`를 실행하지 않고, `curate`는 draft를 실행하지 않는다.
`draft-curated`를 호출하는 것 자체가 curated 입력으로 초안을 쓰라는 명시적 지시다.
bundle이 존재해도 평범한 `docloop draft`를 호출하면 원래 흐름 그대로 동작한다.
평범한 `draft [notes...]`에 경로를 자유 텍스트로 전달하는 것도 여전히 가능하지만,
그 경로는 bundle을 검증하지 않으며 `draft-curated`가 보장하는 것들을 제공하지 않는다.

run ID와 curation ID는 `[a-z0-9][a-z0-9-]{0,63}`와 일치해야 한다. ID는
append-only다. 재사용은 거부되며, 미완료·실패한 작업을 재시도하려면 새 ID가
필요하다. `docloop init`은 선택 흐름용 디렉터리를 추가하지 않는다. 첫 `contribute`
호출이 필요에 따라 `work/contributions/`와 `work/contribution-responses/`를
만든다.

## 사람 응답 계약

응답은 생성된 템플릿의 복사본에서 시작한다. 중요한 필드는 다음과 같다.

```yaml
schema_version: 1
source_run_id: cc-20260803-01
source_index_sha256: "<copied from the template>"
operator_attested: true
attested_by: "operator label"
attested_at: "2026-08-03T00:00:00Z"
materials:
  - material_id: submission-policy
    path: inputs/submission-policy.md
dispositions:
  - item_id: cc-20260803-01/backend/01
    status: supported
    group_id: ""
    decision: ""
    material_refs: [submission-policy]
    rationale: ""
```

모든 출처 항목은 정확히 한 번씩 나타나야 한다. 상태값의 의미는 다음과 같다.

| 상태 | 필요한 입력 | draft 작성 처리 |
|---|---|---|
| `decided` | 비어있지 않은 `decision` | draft 근거로 포함 |
| `supported` | 알려진 `material_id` 최소 하나 | 자료 다이제스트와 함께 포함 |
| `open` | 없음 | 미해결로 유지, 확정된 draft 입력으로 승격되지 않음 |
| `carried` | 비어있지 않은 `rationale` | 감사용으로 보존, draft 근거에서 제외 |
| `dismissed` | 비어있지 않은 `rationale` | 감사용으로 보존, draft 근거에서 제외 |

빈 필드는 승인을 의미하지 않는다. 선택적 `group_id` 값은 오퍼레이터의 몫이다.
docloop은 원본 기여를 의미론적으로 병합하지 않는다. 그룹으로 묶인 항목들은
상태, 대상 섹션, decision, 자료, rationale이 모두 일치해야 한다.
open 항목은 `open-questions.md`와 `draft-notes.md` 끝의
`Unresolved — not drafting evidence`로 표시된 블록에 계속 보이며, 그 존재 자체가
확정된 draft 결정으로 취급되지 않는다.

`operator_attested: true`, `attested_by`, `attested_at`은 명시적 자기 확인
(self-attestation) 필드다. 이 필드들은 사람의 신원을 인증하거나, 작성자를
증명하거나, 권한을 확립하지 **않는다**. docloop은 제출된 decision이나 자료가
사실인지, 최신인지도 검증하지 않는다.

Material ID는 `[a-z][a-z0-9-]{0,63}`와 일치해야 하고 응답 안에서 고유해야 한다.
Material 경로는 작업 폴더 기준 상대 POSIX 경로여야 하며 `inputs/` 아래의 일반
파일(symlink 아님)로 해석되어야 한다. 절대 경로, `..`, NUL, `inputs/` 밖 경로,
symlink, 특수 파일은 거부된다. 참조되지 않은 유효한 material은 그대로 유지되고
`unused: true`로 표시된다. docloop은 이를 조용히 버리지 않는다.

## 산출물과 accepted 상태

기여(contribution) run은 모델을 호출하기 전에 자신의 목적지를 예약한다.

```text
work/contributions/<run-id>/
├── INCOMPLETE                     # present while building; absent when accepted
├── payload/
│   ├── run.yaml
│   ├── snapshot/
│   │   ├── manifest.yaml
│   │   ├── policy.yaml
│   │   ├── inputs/...
│   │   └── inventory.yaml
│   ├── perspectives/<perspective>.yaml
│   ├── contribution-index.yaml
│   ├── human-response.template.yaml
│   └── diagnostics/...
└── COMPLETE.yaml                  # accepted marker; absent while INCOMPLETE
```

curation은 다음을 만든다.

```text
work/curations/<curation-id>/
├── INCOMPLETE                     # present while building; absent when accepted
├── payload/
│   ├── run.yaml
│   ├── contribution-ref.yaml
│   ├── human-response.yaml
│   ├── supplemental-materials/...
│   ├── materials.yaml
│   ├── curation.yaml
│   ├── draft-notes.md
│   └── open-questions.md
└── COMPLETE.yaml                  # accepted marker; absent while INCOMPLETE
```

accepted 상태인 bundle에서 `INCOMPLETE`와 `COMPLETE.yaml`은 상호 배타적이다.
`COMPLETE.yaml`은 배타적 생성(exclusive creation) 방식으로 마지막에 쓰인다.
소비자는 마커 스키마, stage와 ID, 전체 payload 파일 인벤토리, 모든 크기와
SHA-256 다이제스트, payload 전체의 집계 다이제스트를 검증한 뒤에만 bundle을
받아들인다. 마커가 존재한다는 사실만으로는 성공이 아니다.

중단되거나 거부된 run은 진단을 위해 그대로 남아 있으며 여전히 민감한 데이터를
담고 있을 수 있다. docloop은 이를 자동으로 삭제하거나 복구하지 않는다. 오래된
`INCOMPLETE` 디렉터리는 다운스트림에서 받아들여지지 않는다. 새 ID로 재시도한다.

## 이 흐름이 보장하는 것

- 선택된 관점마다 별도의 모델 호출과 세대 구분자가 붙은 item ID를 가진 별도의
  산출물이 생긴다.
- 하나의 기여 run에 속한 모든 관점은 동일하게 캡처된 bundle 바이트로부터
  지시를 받는다. bundle은 일정 시간 구간에 걸쳐 조립되며, 원본 트리의 원자적
  시점 스냅샷은 아니다.
- 원본 기여는 index와 curation을 거치는 동안 1:1 안정 ID 경로를 유지한다.
  오직 오퍼레이터의 명시적 상태와 선택적 그룹만이 적용된다.
- 동일하게 완료된 기여와 동일한 응답 바이트에 대해서는, 새 curation ID로
  실행해도 결정론적 curation 내용과 `draft-notes.md` 바이트가 동일하다. run ID와
  타임스탬프 메타데이터는 달라질 수 있다.
- 기존 run 경로와 파일은 덮어써지지 않는다. 원자적 목적지 예약은 동일 ID 호출
  하나만 진입시킨다. 서로 다른 ID는 동시에 실행될 수 있다.
- 다운스트림 명령은 accepted-state 마커, 인벤토리, 다이제스트가 검증된
  bundle만 소비한다. `draft-curated`는 draft 작성으로 들어가는 유일하게 검증된
  다리다.

이 흐름은 모델 호출 사이의 파일시스템 격리나 기밀성을 제공하지 **않는다**.
캡처된 작업 디렉터리와, 지원되는 경우 읽기 전용 CLI 설정은 동일 UID의 다른
프로세스나 읽을 수 있는 다른 경로에 대한 보안 경계가 아니다. 별도의 호출들은
독립된 agent도 아니고 새로운 인지 과정도 아니다. 같은 모델 계열을 쓸 수 있고
상관된 맹점을 공유할 수 있다. 출력이 전문적이거나, 정확하거나, 완전하거나,
서로 독립적이거나, 합의할 만한 것이라는 보장은 없다.

디렉터리 전체를 아우르는 이식 가능한 원자적 트랜잭션이라고 주장하지 않는다.
구현이 제공하는 것은 배타적 마커 생성, 수락 전 전체 검증, 기존 run을 덮어쓰지
않는 것이다. `SIGKILL`, 정전, 동일 UID의 악의적 수정, 네트워크 파일시스템
동작, 수동 bundle 변조로부터의 복구는 보장하지 않는다.

## 제한, 개인정보, 보존

기여 캡처 전에 docloop은 파일 개수, 총 바이트, 목적지를 보고한다. 고정된 MVP
제한은 다음과 같다.

| 자원 | 제한 |
|---|---:|
| Perspectives per contribution | 2–5 |
| Files below `inputs/` | 1,000 |
| One captured manifest, policy, or input file | 10 MiB |
| Captured manifest, policy, and inputs per contribution | 50 MiB total |
| Model stdout per perspective | 1 MiB |
| Items per perspective | 50 |
| Supplemental materials per curation | 50 |
| Supplemental materials per curation | 50 MiB total |
| Human response or generated Markdown/YAML file | 2 MiB each |

한도를 초과하는 작업은 해당 복사, 모델 호출, 발행이 일어나기 전에 실패한다.
조용한 잘림, override, `FORCE`, 즉석 복구 모드는 없다.

기여 bundle은 캡처된 입력을 run당 한 번 복사한다. curation bundle은 보충
자료를 curation당 한 번 복사한다. 동일한 보충 콘텐츠는 하나의 curation 안에서
전체 SHA-256 기준으로 중복 제거되지만, run 간 중복 제거나 암호화는 없다. run
디렉터리는 `0700` 모드를, 파일은 `0600` 모드를 쓴다. 이 모드는 로컬에서의
우발적 노출은 줄이지만 백업을 배제하거나 같은 사용자로 실행되는 다른
프로세스를 막지는 못한다.

설정된 모델 CLI/provider는 manifest, policy, input 내용을 전달받을 수 있다.
provider의 로깅과 보존 정책은 그 provider의 설정을 따르며 docloop의 통제
밖이다. docloop은 선택된 모델 CLI를 거치는 것 외에는 업로드를 하지 않지만,
로컬 bundle에는 원본 텍스트, 모델 출력, 오퍼레이터의 decision, 파일명,
다이제스트가 남을 수 있다.

`DOCLOOP_MODEL=claude`일 때, 기여 호출은 Claude Code의 `--safe-mode`,
`--permission-mode dontAsk`, 제한된 `Read,Glob,Grep` 도구 목록,
`--json-schema` 구조화 출력 옵션을 쓴다. Claude Code 2.1.220이 검증된
버전이며, 이 플래그들이 없는 이전 릴리즈는 `contribute`에서 지원되지 않는다.
이 옵션들은 해당 호출의 커스터마이징과 쓰기 가능한 도구 노출을 줄이지만,
파일시스템 격리를 만들거나 동일 사용자의 다른 프로세스가 파일을 읽는 것을
막지는 않는다. 명시적으로 요청되는 `draft-curated` 쓰기 단계에서는 Claude가
비대화형 `acceptEdits` 모드로 `Read`, `Glob`, `Grep`, `Edit`, `Write`만 쓰며,
이 명령은 Bash 도구를 제공하지 않는다. 이는 도구 노출 경계이지 파일시스템
샌드박스가 아니다. 평범한 `plan`과 평범한 `draft`는 기존 모델 호출 동작을
그대로 유지한다.

`DOCLOOP_MODEL=codex`일 때, 기여 호출은 Codex의 읽기 전용 sandbox 설정을
명시적으로 쓰고, 명시적으로 요청되는 `draft-curated` 단계는 SSOT와 manifest를
조정할 수 있도록 workspace-write 설정을 쓴다. 이는 CLI 도구 설정일 뿐,
docloop이 프로세스나 파일시스템 격리를 제공한다는 주장이 아니다.

일반 콘솔 메시지에는 경로, 개수, 바이트 크기, 다이제스트, reason code가
담기며 원본이나 모델 출력 본문은 담기지 않는다. 진단 정보는 환경변수 덤프,
전체 명령줄, 조립된 프롬프트를 저장하지 않는다. 관점당 최대 64 KiB의 provider
stderr를 보관하고 잘림 여부를 표시할 수 있으므로, 진단 정보도 민감하게
취급한다.

자동 보존 기간이나 정리는 없다. 완료된 것과 미완료된 bundle 모두 민감한
사본으로 취급한다. 기여를 삭제하기 전에, 남아 있는 curation들의
`payload/contribution-ref.yaml` 파일을 확인한다. 유지하려는 curation이 해당
기여를 참조하고 있다면 그 기여는 유지한다. 그런 뒤 선택한 정확한, 검증된
run-ID 디렉터리만 제거한다. 오래된 미완료 기여와 curation에도 같은
정확-경로 규칙을 적용한다 — 절대 wildcard를 쓰거나 `work/` 트리 전체를 정리
삼아 삭제하지 않는다.

예를 들어, 아래 두 개의 특정 bundle을 폐기하기로 결정했다면, 의존하는
curation을 먼저 지우고 그다음 그 출처 기여를 지운다.

```bash
rm -rf -- work/curations/cur-20260803-01
rm -rf -- work/contributions/cc-20260803-01
```
