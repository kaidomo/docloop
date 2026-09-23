# 진행 사건·질문 답변 기록

이 기록은 호출자가 관측한 사건과 사용자의 논평을 연결한다. 모델 내부 사고과정, 인증 정보, 숨은 도구 호출을 수집하지 않는다. `calls.json`은 기존 실행 검증의 정본이며 `events.jsonl`이 이를 대체하거나 host receipt가 되지 않는다.

## 생성되는 파일

- `events.jsonl`: helper 단계와 호스트가 명시 기록한 호출 사건. 과거 사건을 덮어쓰지 않는다.
- `feedback/<feedback_id>.json`: 원 결과 해시와 연결된 질문·답변·해석·반영 상태. 정정은 새 ID로 작성한다.
- `timeline.md`: 위 기록을 사람이 읽는 별도 화면. 재생성 가능하며 원 리뷰 결과·원응답을 수정하지 않는다.

helper의 prepare/falsify/finalize/report 단계 결과는 자동 기록한다. helper가 직접 실행하지 않는 모델 호출은 호스트가 아래 명령으로 기록한다. 프로세스 강제 종료로 마지막 사건이 없으면 미기록/중단 가능성으로 남기며 성공이나 종료 시각을 추정하지 않는다. run 생성 전 prepare 실패나 packet/입력 무결성이 깨져 사건을 결속할 수 없는 경우는 stderr에 남긴다. 사건 기록 자체가 실패하면 `observation recording failed`도 표시하며 원래 작업 오류를 숨기지 않는다.

## 모델 호출 경계 기록

호출을 실제로 시작해 호스트 ID를 받은 직후, 완료/실패를 관측한 직후 각각 JSON 파일을 작성하여 실행한다. 아래는 형식 예시이며 실제 호출을 했다는 증거가 아니다.

```json
{
  "schema_version": 1,
  "event_id": "structure-start-1",
  "packet_sha256": "packet.json 최상위 packet_sha256 값",
  "event_type": "call_started",
  "stage": "structure",
  "host_call_id": "실제 호스트 호출 식별자",
  "outcome": "running",
  "evidence_ref": "실제 호출 기록을 찾을 참조"
}
```

```sh
docloop light-review event --out /path/run --record /path/event.json
```

- `event_type`: `call_started`, `call_completed`, `call_failed`.
- `stage`: `structure`, `behavior`, `falsifier`.
- `occurred_at`을 생략하면 기록 시점의 UTC epoch다. 실제로 관측한 시각을 알고 있을 때만 숫자로 제공한다. 파일 mtime·추정 시각을 넣지 않는다.
- 완료·실패 기록에는 새 event ID를 쓰고 실제 outcome과 근거를 남긴다. 전달되지 않은 backend 모델 이름이나 강제되지 않은 도구 제한을 만들어 쓰지 않는다.
- `calls.json`에는 기존 계약대로 실제 시작/종료 시각·호스트 ID·프롬프트/응답 해시를 별도로 기록한다. timeline은 두 기록의 lane·host ID·outcome 차이와 시작 사건 없는 종료를 공시한다. finalize 시 기록 불일치가 있으면 provenance_unverified/partial로 전달한다. 원 결과가 생성된 뒤 추가한 사건은 원 결과를 덮어쓰지 않고 timeline에 차이를 표시한다. 사건은 일치하더라도 호출자 관측이며 host 인증이 아니다.

## 질문과 답변 연결

`meta-learning-loop` Quick의 의미 구분을 따른다. 사용자가 이미 대화에서 답했으면 그 원말을 연결하며 같은 질문을 다시 하지 않는다. 아래 모든 필드는 필요하며 모르는 시각은 null이다.

```json
{
  "schema_version": 1,
  "feedback_id": "feedback-1",
  "packet_sha256": "packet.json 최상위 packet_sha256 값",
  "result_sha256": "원 result.json 바이트 SHA256",
  "candidate_ids": ["structure-1"],
  "question_id": "question-1",
  "question_text": "사용자에게 실제로 보여준 질문 또는 설명 원문",
  "answer_text": "사용자의 답변 원문",
  "answer_source": "출처 메시지 식별자 또는 확인 가능한 대화 참조",
  "received_at": null,
  "interpretation": "AI 해석. 기존 결정 설명인지 새 정책인지와 적용 조건을 명시. 사용자가 이 해석까지 승인했다고 추정하지 않음.",
  "interpretation_confirmed": false,
  "change_status": "not_applied",
  "change_refs": [],
  "supersedes": null
}
```

```sh
docloop light-review feedback --out /path/run --record /path/feedback.json
docloop light-review timeline --out /path/run
```

- `candidate_ids`는 원 결과의 실제 후보 ID다. 별도 질문이면 빈 배열을 쓸 수 있다. 원 질문이 후보를 과장·축소했다면 `question_text`에 실제 전달 문구를 보존한다.
- `feedback_id`와 `question_id`는 영문/숫자로 시작하는 1~128자 ID이며 뒤에 영문/숫자/점/밑줄/콜론/하이픈을 쓸 수 있다. 경로를 ID에 쓰지 않는다.
- `change_status`: `not_applied`, `applied`, `unverified`. `applied`에는 실제 diff·게시 버전·검증 기록 등 `change_refs`가 필요하다. 링크가 있다고 실제 반영을 자동 인증하는 것은 아니다.
- 정정 답변은 새 feedback ID와 동일 질문 ID를 쓰고 `supersedes`에 같은 run의 이전 feedback ID를 연결한다. 정정 이유와 적용 범위는 interpretation에 기록한다. 후속 반영 상태를 갱신할 때도 새 기록을 남기며 원말의 수신 시각을 새 답변 시각으로 바꾸지 않는다.
- 새 정책이 리뷰 뒤에 추가된 경우 과거 리뷰의 오류로 소급하지 않는다. 해석 승인 여부는 사용자 명시 응답에 근거하고 미확인은 false로 둔다.
- 원응답/원결과는 덮어쓰지 않는다. 오류, 거부된 기록, 쓰기 실패는 사용자에게 알리고 실제 보존된 파일을 확인한다. 중간에 일부 파일만 생성됐으면 재시도로 덮어쓰지 않는다.

## 기존 실행과 학습 기록

기존 실행에 feedback를 연결할 수 있지만 그때 없던 실행 사건을 소급 생성하지 않는다. answer_source/interpretation에서 사후 연결과 미확인 시각을 밝힌다. timeline은 실제 기록된 사건만 보여주므로 비어 있는 호출 이력을 성공 실행으로 읽지 않는다.

실행을 가로지르는 문제 등록부에는 `run 경로 + packet/result 해시 + 원 candidate ID + feedback ID`를 연결한다. 사용자 원말, 전달자 오탐 판단, 원인 가설, 실험 결과를 서로 다른 근거로 남긴다. 기존 meta-learning-loop의 lesson/experiment 계약을 사용하며 이 helper에서 학습 상태나 스킬 지침을 자동 승격하지 않는다.

다음 독립 리뷰에는 그 시점에 적용되는 제품 결정·근거만 입력한다. 지난 결과나 기대 정답을 같이 주어 오탐을 없애는 방식으로 개선을 측정하지 않는다.

## 부분 실패와 표시 갱신

falsify에서 거부된 후보나 입력 오류가 있으면 CLI는 status partial, 거부 상세와 exit 3을 반환한다. 유효 후보의 반증 프롬프트는 보존한다. 원응답을 수정하거나 성공을 얻기 위해 재실행하지 않는다.

원 기록을 저장한 뒤 파생 timeline 갱신만 실패하면 기록 성공과 표시 실패를 구분한다. stderr에 `timeline refresh failed`와 view stale을 알리고 성공 사건을 실패 사건으로 다시 쓰지 않는다. `timeline` 명령으로 표시만 재생성할 수 있다. timeline은 임시 파일 작성 후 교체한다. 기록 명령은 동일 run에서 호출자가 순서대로 실행하며 동시 작성은 지원하지 않는다.
