# Review-gate 패킷 워크플로

English: [review-gate.md](review-gate.md)

`docloop review-gate`는 `docloop review`가 이미 스테이징해 둔 문서를 대상으로 명시적으로
호출하는 패킷 준비 워크플로다. 대상 파일 하나와 선택한 입력들을 정확히 동결하고, 결정론적
프리플라이트 검사를 실행하며, 세 가지 리뷰 렌즈, 합성, 앵커 감사, 독립 검증, 그리고 최종
사람 판단을 위한 프롬프트를 작성한다.

이 명령은 모델을 실행하거나, 대상을 편집하거나, finding을 반영하거나, 리뷰 완료를
선언하지 **않는다**. `review-gate`를 호출하지 않으면 기존 명령과 그 프롬프트, 인자,
산출물, 동작은 그대로다.

## 입력 준비

모든 `prepare` 입력 경로는 리뷰 폴더 하나를 기준으로 한 상대 경로다. 지정한 리뷰 폴더
자체는 symlink가 아닌 실제 디렉터리여야 하며, 이후 접근은 그 폴더의 canonical 디렉터리
디스크립터에 고정된다. 입력과 참조되는 provenance는 그 폴더 안의 정규 파일이어야 한다.
절대 경로, symlink, 폴더를 벗어나는 경로는 거부된다. 대상은 정확히 UTF-8 정규 파일
하나여야 한다.

각 입력은 명시적으로 선택한다:

- Decisions: assured 모드에서는 `--decisions decisions.yaml`을 전달하고, 유효한 결정
  이력이 없으면 `--unassured`를 전달한다. 유효하지 않거나, 오래됐거나, 해시 검증에
  실패한 레지스트리는 준비를 중단시킨다 — 조용히 unassured 모드로 전환하는 일은 없다.
  unassured 모드에서는 어떤 finding도 "이미 결정됨"으로 억제될 수 없으며, 사람이 결정
  이력이 없다는 사실을 명시적으로 수락해야 완료할 수 있다.
- Axes: `--axes axes.md`를 전달하거나, 생략하면 함께 배포되는 기획 문서 체크리스트를
  쓴다.
- Terms: 결정론적 용어 스캔을 요구하려면 `--terms terms.yaml`을, 아니면 `--no-terms`를
  전달한다. 대상 옆에 관례적인 `terms.yaml`이 존재하면 `--no-terms`는 거부되어 사전을
  조용히 건너뛸 수 없다.
- Document model: L3에 사람이 승인한 구조 선언을 포함하려면 `--docmodel docmodel.yaml`을,
  아니면 `--no-docmodel`을 전달한다. 대상 옆에 관례적인 `docmodel.yaml`이 존재하면
  `--no-docmodel`은 거부된다. 준비 과정은 기본 YAML, 승인, provenance, 해시를 검사하지만
  전체 스키마 검증이나 다른 문서 유형으로의 일반화를 보장한다고 주장하지 않는다.
- Convention preflight: 선택적으로 `--convention-profile profile.yaml`과
  `--convention-intake intake.yaml`을 함께 전달한다. 이 쌍은 profile을 정확히 한 번
  커버해야 하고 `phase: pre_lens`를 선언해야 한다. `target_snapshot`은 항상 실제 대상
  해시에 바인딩된다. 문서 범위로 답변된 레코드는 `target_document`가 필요하며, 존재하는
  `target_document`는 선택된 대상 소스와 일치해야 한다. 누락, 부분 입력, 중복 키, 오래된
  입력, 불일치 입력은 run 디렉터리가 예약되기 전에 실패한다. 승인된 답변은 나중에
  authoritative하지 않은 draft로만 구체화(materialize)될 수 있으며, suppression
  authority는 아니다.
- Input gate (CONTRACT §1, 필수): `--editing-state {frozen,in_progress,unknown}`와
  `--target-maturity {complete,draft,unknown}`를 전달한다. `frozen`/`complete`는 흔한
  "완결되어 더 이상 바뀌지 않는 문서를 읽는" 경우다. 편집 상태가 `in_progress` 또는
  `unknown`이면 최종 done 검증(§7)이 유예된다 — 리뷰 자체는 계속 진행할 수 있지만, 거기서
  만든 receipt는 `DEFERRED` 중간 결과까지만 도달할 수 있고 `done`에는 결코 도달하지
  못한다. 대상 성숙도가 `draft` 또는 `unknown`이면 `--open-items-ledger FILE`이
  필요하다 — 문서 자체의 등록된 open-item 원장으로, 다른 sidecar들과 함께 동결된다.
  등록된 open item은 나중에 receipt의 finding을 ("classify"로) 표시할 수는 있지만
  억제할 수는 결코 없다.
- Prior round (CONTRACT §1 ⑨, 선택): 이번 run이 같은 대상에 대한 이전 라운드를 잇는
  것이라면 `--prior-round-output FILE --prior-round-no N`으로 그 라운드의 출력과 번호를
  전달한다. 첫 라운드라면 둘 다 생략한다. 두 번째 라운드에 여전히 수동으로 필요한 것은
  아래 "다회차 리뷰"를 참고한다.

Decisions, terms, docmodel, open-items 원장이 사용하는 모든 provenance 참조는 검증 전에
동결된다. 검증과 용어 스캔은 오직 동결된 파일에 대해서만 실행되며, 패킷 구성 중에
바뀔 수 있는 입력에 대해서는 실행되지 않는다.

준비 과정은 또한 위 input gate를 기록하고 내부 `FrontGateTrace`를 통해 세 렌즈 모두를
시작시킨 뒤 그 결과를 `deterministic/FRONT_GATE_TRACE.json`에 동결한다 — 아래
"front-gate trace" 절을 참고. 이 trace를 만들어내는 곳은 여기뿐이며, 별도의 공개
명령은 없다.

## 패킷 준비

```bash
docloop review-gate prepare ~/.docloop/reviews/case-submission rg-20260803-01 PRD.md \
  --decisions decisions.yaml \
  --terms terms.yaml \
  --no-docmodel \
  --editing-state frozen --target-maturity complete
```

선택적 sidecar 없이 명시적으로 unassured run을 도는 경우:

```bash
docloop review-gate prepare ~/.docloop/reviews/case-submission rg-20260803-02 PRD.md \
  --unassured \
  --no-terms \
  --no-docmodel \
  --editing-state frozen --target-maturity complete
```

아직 작성 중인 대상을, 그 open-item 원장과 함께 준비하는 경우:

```bash
docloop review-gate prepare ~/.docloop/reviews/case-submission rg-20260803-03 PRD.md \
  --unassured --no-terms --no-docmodel \
  --editing-state in_progress --target-maturity draft \
  --open-items-ledger open-items.yaml
```

Convention preflight를 켜려면 두 convention 옵션을 모두 추가한다. 준비 과정은 두 파일을
동결하고 `phase: pre_lens`로 `deterministic/CONVENTION_PREFLIGHT.json`을 작성한다.
`lens_started`를 내보내거나, 모델을 실행하거나, 새로 구체화된 draft를 L3에 추가하지는
않는다.

Run ID는 append-only다. 준비 과정은 `<review-folder>/review-gate/<run-id>/`를 한 번
예약하며 절대 덮어쓰거나 재사용하지 않는다. 예약 이후 실패하면 `INCOMPLETE.json`과
부분 run이 진단 증거로 남는다. 이를 준비된 것처럼 소비하거나 삭제하지 말고, 입력을
바로잡은 뒤 새 run ID를 선택한다.

준비가 끝나면 리뷰어에게 넘기기 전에 패킷을 검증한다:

```bash
docloop review-gate check \
  ~/.docloop/reviews/case-submission/review-gate/rg-20260803-01
```

`check`는 상태 마커, run ID/상태, 준비된 payload 인벤토리/다이제스트, 또는 mutable한
results 트리가 없거나 안전하지 않으면 실패한다. `results/` 아래 정규 파일의 내용물은
무시하는데, 이는 수동 리뷰 중에 자라날 것으로 예상되기 때문이다. 성공한 check는 오직
준비된 패킷의 무결성만을 증명하며, 리뷰가 통과했거나 완료됐다는 뜻은 아니다.

## 패킷 구성

```text
<review-folder>/review-gate/<run-id>/
  COMPLETE.json
  RUN.yaml
  frozen/
    target.txt
    target.numbered.md
    decisions.yaml                 # assured 모드에서만
    axes.md
    terms.yaml                     # 선택한 경우
    docmodel.yaml                  # 선택한 경우
    convention-profile.yaml       # 쌍으로 선택한 경우
    convention-intake.yaml        # 쌍으로 선택한 경우
    open-items.yaml                # --open-items-ledger를 준 경우
    prior-round-output.md          # --prior-round-output을 준 경우
    provenance/<typed-id>
  lens/L1/{PROMPT.md,TARGET.md}
  lens/L2/{PROMPT.md,TARGET.md,DECISIONS.yaml|UNASSURED.md}
  lens/L3/{PROMPT.md,TARGET.md,AXES.md[,DOCMODEL.yaml]}
  deterministic/DECISIONS_VALIDATION.txt  # assured 모드에서만
  deterministic/TERM_SCAN_RAW.md   # 선택한 경우, 업스트림 스캐너의 원본 출력 그대로
  deterministic/TERM_SCAN.md       # 감사에 쓰이는, 두 자리 이상 anchor 어댑터 적용본
  deterministic/CONVENTION_PREFLIGHT.json  # 준비 상태만 나타냄, 렌즈 실행 없음
  deterministic/FRONT_GATE_TRACE.json  # 다이제스트에 바인딩된 input-gate + lens-start trace
  deterministic/RECEIPT_SCAFFOLD.json  # 그대로 복사해 쓰는 input_gate/front_gate_ref/round_context
  handoff/{SYNTHESIS.md,ANCHOR_AUDIT.md,VERIFICATION.md,HUMAN_DECISION.md}
  results/README.md
```

구성 중에는 run에 `INCOMPLETE.json`도 함께 존재한다. `COMPLETE.json`이 정규 파일이고
`INCOMPLETE.json`이 없으며, `COMPLETE.json`의 payload 인벤토리와 다이제스트가 준비된
모든 입력, 프롬프트, 감사, handoff 파일과 일치할 때만 패킷이 준비된 것이다. 두 상태
마커와 mutable한 `results/` 프리픽스는 그 다이제스트에서 제외된다.
`RUN.yaml`은 동결된 입력 해시, 업스트림 CONTRACT provenance, assured/unassured 모드,
렌즈 가시성 매트릭스, 결정론적 스캔 다이제스트, 그리고 명시적인 비보장 사항을 기록한다.
준비됐다는 것은 패킷 구성이 끝났다는 뜻일 뿐이며 `passed`, `verified`, `done`을
의미하지 않는다.

`results/`는 수동 리뷰 출력을 위한 append-only 영역이다. 이 안의 파일들은 준비된-입력
다이제스트에서 제외되므로 모델·사람 결과를 추가해도 패킷이 무효화되지 않는다. 이전
시도를 절대 덮어쓰지 말고, `results/README.md`가 지시하는 대로 숫자 접미사를 붙여
추가한다. `check`는 그 안의 symlink와 비정규 항목을 거부하지만, 정규 결과 파일이
덮어써졌는지는 감지할 수 없다 — append-only 이력 유지는 운영자의 책임으로 남는다.

세 렌즈 envelope은 의도적으로 서로 다르다:

| 렌즈 | 해당 패킷에서 보이는 파일 | 리뷰 목적 |
| --- | --- | --- |
| L1 | 동결된 대상만 | 콜드리드 발견 |
| L2 | 동결된 대상 + 검증된 결정, 또는 unassured 안내 | 이전 결정 및 재플래그 확인 |
| L3 | 동결된 대상 + axes + 선택적 docmodel | 섹션 간·구조적 스윕 |

이 디렉터리들은 각 모델 호출에 무엇을 제공해야 하는지를 선언한다. 보안 경계가 아니며
프로세스가 상위·형제 경로를 읽는 것을 막지 못한다.

`target.numbered.md`는 `L01`부터 `L09`까지, 이후 `L10` 이상을 쓴다. 업스트림 anchor
감사기는 한 자리 `L1`/`L2`/`L3` 토큰을 의도적으로 무시하는데, 이는 렌즈 이름을 뜻할 수도
있기 때문이다. 그래서 패킷은 스캐너의 정확한 원본 출력을 `TERM_SCAN_RAW.md`에 그대로
보존하고, 합성/감사에는 `TERM_SCAN.md`의 동등한 두 자리 이상 anchor를 쓴다.

## front-gate trace

`prepare`는 CONTRACT §1 input gate를 기록하고 내부 `FrontGateTrace` 객체를 통해 모든
렌즈를 시작시킨다 — 업스트림이 `review_front_gate.py`라 부르는 것과 같은 순서 보장
장치이지만, docloop은 이를 별도 명령으로 노출하지 않고 오직 `prepare` 안에서만
실행한다. 그 결과 만들어지는 이벤트 시퀀스(`convention_intake_validated` 또는
`convention_profile_not_applicable`, 이어서 `input_gate_recorded`, 이어서 세 번의
`lens_started`)는 `deterministic/FRONT_GATE_TRACE.json`에 동결되고 해시로 바인딩된다.
done receipt의 `front_gate_ref`는 이 파일을 경로와 sha256으로 정확히 지목해야 한다 —
receipt는 어떤 렌즈도 실행되기 전에 gate가 실제로 기록한 것보다 더 편리한 값으로
`editing_state`/`target_maturity`를 독자적으로 재선언할 수 없다.

`--convention-profile`/`--convention-intake`를 전달하지 않았다면 `prepare`는 trace의
기술적 요구를 만족시키기 위해 내부의, 항상 부적용(inapplicable) 상태인 placeholder
profile을 쓴다 — 이 존재를 알 필요는 없다. trace는 정확히
`convention_profile_not_applicable`을 내보내고, receipt의 `structure_axis`는 그 경우
반드시 `structure_axis_reason`과 함께 `undetermined`여야 한다(이미 채워져 있는
`deterministic/RECEIPT_SCAFFOLD.json`을 참고).

`deterministic/RECEIPT_SCAFFOLD.json`에는 최종 `DONE.md` receipt에 필요한 정확한
`input_gate`, `front_gate_ref`, `round_context` 필드가 들어 있다 — 해시를 손으로 다시
치지 말고 그대로 복사해 쓴다.

## docmodel-approvals 레지스트리

`approved_docmodel` authority 참조(decision의 `source` 안, 또는 억제된 finding의
`authority_ref` 안)는 더 이상 docmodel 파일 자신이 선언한
`meta.approval_state: approved`를 가리키지 않는다. 대신 경로와 sha256으로 독립된
**docmodel-approvals 레지스트리** 파일을 가리키고, 그 안의 `approval_id` 항목 하나를
지목한다:

```yaml
meta:
  target: <이 레지스트리가 어떤 docmodel들을 승인하는지>
  updated_at: "2026-08-20"
approvals:
  - id: APR-example-01
    docmodel_path: frozen/docmodel.yaml   # 패킷 상대 경로
    docmodel_sha256: <해당 파일 현재 바이트의 sha256>
    status: approved                       # approved | revoked
    approved_by: <승인자>
    approved_at: "2026-08-20"
    evidence: <승인이 실제로 이루어진 곳 — 리뷰 코멘트, 회의록 등>
```

```yaml
authority_ref:
  kind: approved_docmodel
  path: frozen/docmodel-approvals.yaml
  sha256: <레지스트리 파일 바이트의 sha256>
  approval_id: APR-example-01
```

검증은 매번 `docmodel_path`를 `docmodel_sha256`에 대해 다시 해시한다 — 승인 이후
docmodel이 바뀌면 그 항목은 오래된 것이 되고, authority 참조는 재승인되기 전까지
fail-closed 상태가 된다. docmodel 파일은 더 이상 스스로 승인을 주장할 수 없다 —
레지스트리만이 유일한 진실 소스다.

## 다회차 리뷰

같은 대상에 대한 두 번째 라운드(`prepare` 시점의 `--prior-round-output`/
`--prior-round-no`)는 end-to-end로 구조적으로 지원되지만, 라운드 비교표를 생성하는
도구(`match_review_rounds.py`)는 아직 docloop으로 포팅되지 않았다. receipt의
`round_context.comparison_ref`는 `# 라운드 대조 —`로 시작하고 스스로 주장하는 해시와
일치하는 파일을 가리켜야 한다. 지금은 그 형식 그대로의 파일을 손으로 만들거나 외부
도구로 만들어야 한다. 첫 라운드(흔한 경우 — `prior_round.exists: false`,
`round_context.round_label: r1`)는 이 중 아무것도 필요하지 않다.

## 리뷰를 수동으로 완료하기

패킷은 provider-neutral하다. 준비를 곧 리뷰로 취급하지 말고 생성된 handoff 파일을
따른다:

1. `lens/L1/PROMPT.md`, `lens/L2/PROMPT.md`, `lens/L3/PROMPT.md` 각각을 그 렌즈
   디렉터리가 선언한 입력만 가지고 별도의 fresh context에서 실행한다.
   `results/README.md`가 설명하는 append-only 이름으로 출력을 저장한다.
2. `handoff/SYNTHESIS.md`를 또 다른 fresh context에서 실행한다. 모든 source candidate를
   `results/INTERMEDIATE.yaml`에서 하나 이상의 감사 가능한 atom으로 보존하고, 각 atom에
   정확히 하나의 terminal outcome을 부여한다. 각 canonical finding은 여섯 개 필수
   필드를 모두 포함해야 한다: finding ID; 원문 그대로의 근거와 위치; 반대 텍스트 검색
   결과; 결정 레지스트리 대조; 결정 경로가 있는 severity; lifecycle 상태.
3. 원장을 검증한 뒤, 합성 결과를 쓰기 전에 원장을 인지하는(ledger-aware) anchor 감사를
   실행한다. source, atom, ledger-row, terminal-record anchor가 없으면 fail-closed다.
   같은 합성 context 안에서 최대 두 번까지 복구를 시도한다. 두 번 복구에 실패하면 그
   run을 실패로 기록하고 사람에게 넘긴다.
4. 사람이 각 finding을 승인하거나 기각한다. 승인된 finding은
   `discovered → accepted → planned → applied → verified`를 거치고, 기각된 finding은
   `rejected`에서 끝난다. 처분은 append-only다.
5. 적용된 각 finding을 fresh-context kill 시도로 검증한다. `pass`, `kill`, 또는 블로킹
   `unresolved`를 기록한다. P1 finding은 리뷰어 세 명이 필요하고, 그 외 finding은 한
   명이면 된다. 세 명의 리뷰가 필요한 곳에서는 만장일치 pass만 pass이고, 만장일치
   kill은 그 finding 하나를 기각하며, 그 외의 조합은 모두 블로킹 `unresolved`다 —
   다수결은 없다. 최종 done-프리플라이트에도 세 명이 필요하며, 거기서 kill이나
   unresolved가 나오면 합성으로 되돌아간다.
6. `handoff/HUMAN_DECISION.md`를 따라 사람의 판단 기록을 추가하고 v2 `results/DONE.md`
   receipt를 만든다. `deterministic/RECEIPT_SCAFFOLD.json`에서 `input_gate`,
   `front_gate_ref`, `round_context`를 복사하고, `structure_axis`(undetermined라면
   `structure_axis_reason`도), `execution`(확인된 렌즈-라운드 수와 그 이유),
   `scale_disclosure`(사전에 공개한 실행 규모를 항목별로)를 추가한다. 준비된 패킷에
   대해 이를 검증한다. 어떤 finding이든
   `verified|rejected` 바깥에 있거나, 검증이 없거나 unresolved 상태이거나, unassured
   run이 결정 이력 부재에 대한 사람의 명시적 수락 없이 남아 있거나, 준비 시점의
   `editing_state`가 `in_progress`/`unknown`이었다면(그 receipt는 `DEFERRED`까지만
   도달할 수 있고 `done`에는 결코 도달하지 못한다 — 대상이 동결된 뒤 `prepare`를 다시
   실행한다) 리뷰는 완료된 것이 아니다.

Severity는 리뷰 주장이지 순서 보장이 아니다. P3와 단 한 run에서만 발견된 finding을
포함해 모든 finding을 읽는다. 이 프로토콜은 done 검증자 세 명과 P1 finding에 리뷰어
세 명을 요구하지만, 그 인원 수가 경험적으로 정당하거나 완전한 탐지에 충분하다고
주장하지 않는다.

## 결정론적 도구

결정론적 도구는 직접 호출할 수도 있다. 레거시 명령은 동작과 exit-code 의미를
그대로 유지한다:

```text
docloop review-gate validate-decisions <decisions.yaml> [--skip-hash]
docloop review-gate validate-intermediate <run-folder> <ledger-relative-path> [--closed]
docloop review-gate validate-result <run-folder> <receipt-relative-path>
docloop review-gate validate-convention-profile <profile.yaml>
docloop review-gate validate-convention-intake <intake.yaml> --profile <profile.yaml>
docloop review-gate materialize-docmodel <intake.yaml> --profile <profile.yaml> [--output FILE]
docloop review-gate scan-terms <terms.yaml> <target>
docloop review-gate audit-anchors <synthesis> [--ledger LEDGER --packet-root ROOT] \
  [--lens [LENS ...]] [--l2 L2] \
  [--scan SCAN] [--extra-re EXTRA_RE] [--id-re ID_RE]
```

`validate-intermediate`는 candidate/atom 커버리지, terminal 유일성, anchor 계보,
drift 형태, question authority를 강제한다. drift는 같은 대상을 같은 값으로 표현한
차이이며 non-blocking이다 — 결함이 아니다. candidate에 의존하는 question은 authority와
역방향 계보가 풀릴 때까지 closure를 막는다.

`validate-result`는 먼저 실제 준비된 패킷을 검증한 뒤 v1 또는 v2 receipt를 검증한다.
v2는 정확한 run ID, 대상 소스와 스냅샷, 준비된 payload 다이제스트, receipt 경로, ledger
바이트, immutable 레코드, 공개 레코드 집합을 결합하며 — 여기에 더해 위에서 설명한
input gate, 다이제스트로 바인딩된 front-gate trace, structure axis, execution 공개,
scale 공개, round context까지 결합한다. 패킷 상대 경로는 non-Git 패킷 루트 아래의
정규화된 POSIX 정규 파일이어야 한다. 이는 내부 일관성을 증명할 뿐, 작성자나 같은 UID로
협조된 재작성에 대한 저항력을 증명하지 않는다. `schema_version: 1` receipt는 더 이상
done으로 검증될 수 없다 — input gate가 생기기 전의 스키마이기 때문이다. 이미 닫힌
receipt를 살펴보려면 `--legacy`를 쓴다(필드 완전성만 확인할 뿐 done 판정은 아니다).

Convention 검증은 데이터 기반이며 문서 유형에 중립적이다. Materialization은
`approved_to_draft` 답변만 소비하고, identity 충돌을 거부하며, `approval_state: draft`와
`suppression_eligible: false`인 no-clobber draft를 만든다. 사람이 이를 승인하고 새 run에서
명시적으로 선택해야 한다.

`validate-decisions`와 `scan-terms`는 fail-closed다. `audit-anchors`는 잃어버린
line/source anchor를 탐지할 뿐, finding이 옳은지 또는 렌즈가 결함을 놓쳤는지를
판정하지 않는다.

## 보장하는 것과 보장하지 않는 것

패킷 준비는 받아들여진 로컬 입력에 대해 다음을 보장한다:

- 동결된 대상 스냅샷 하나와, 선택한 sidecar 및 provenance의 동결된 사본;
- 억제 이전의 검증과, 선택한 경우 결정론적 용어 스캔;
- 배타적이고 덮어쓰지 않는 run 예약과, 소비할 수 없는 incomplete 상태;
- 준비된 입력, 프롬프트, 감사, handoff에 대한 커밋된 인벤토리와 다이제스트(상태
  마커와 append-only `results/`는 제외); 그리고
- 고정된 L1/L2/L3 입력 envelope과 명시적인 수동 handoff 지침.

이 명령은 파일시스템 격리, 독립된 agent, 리뷰어의 전문성, 완전한 결함 탐지, 정확한
severity, 신뢰할 수 있는 confidence 순서, 반복 실행 횟수의 정당성, 또는 정확한 최종
문서를 보장하지 않는다. 용어 스캔은 제공된 사전에 인코딩된 관계에 대해서만
결정론적이다. 사람의 리뷰, 처분, 검증은 여전히 필수다.

결정론적 ledger/receipt, 범용 convention-preflight, input-gate/front-gate trace,
docmodel-approvals 레지스트리 계약은 지원된다. 템플릿별 docmodel 일반화(범용 스키마를
넘어서는 템플릿 전용 구조 선언 패키지)와 §13 round-comparison 생성기
(`match_review_rounds.py`)는 여전히 유보 상태다 — `docs/PORTS-gaps-2026-08-20.md`를
참고. 이식 가능성, 완전성, 모델 독립성에 대한 어떤 보장도 함의되지 않는다.
