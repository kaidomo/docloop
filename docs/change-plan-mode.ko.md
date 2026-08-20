# 변경계획 모드 (as-is/to-be)

English: [change-plan-mode.md](change-plan-mode.md)

> README에서 옮겨옴 (2026-07-22).

새 문서를 쓰는 게 아니라 **이미 있는 시스템을 어떻게 고칠지** 계획할 때 쓴다. 제품·문서·로그·
코드를 docloop에 물리면, "지금은 이렇게 동작하고, 이렇게 바꾸자"는 **단일 as-is/to-be 계획서**를
만들도록 돕는다 — 반영은 사람이 손으로 한다.

실제로 하는 법:

```bash
docloop init ~/work/fix-submission ./inputs/   # make a work folder (your input files move into inputs/)
docloop atb-capture ./inputs/                  # read the system, note what's true today (with evidence)
docloop atb-chunk                              # group the fixes and put them in a sensible order
docloop atb-author                             # write the as-is/to-be plan
docloop atb-audit                              # check each "as-is" against the evidence behind it
docloop atb-gate                               # last stop: block if any as-is is still unsourced
```

한계: docloop이 확인하는 건 **as-is** 절반뿐이다 — "지금은 X로 동작한다"는 주장마다 실제 출처가
있는지(이건 기계적으로 검사). **to-be** 절반, 즉 무엇을 어떤 순서로 바꿀지는 판단이며 당신 몫이다.

전체 파이프라인과 옵션은 아래 **기술 상세**를 참고.

## 기술 상세

기존에 없는 문서를 새로 쓰는 게 아니라, **이미 있는 시스템을 어떻게 고칠지** 계획하는 다른 절반.
제품·문서·로그·코드를 읽고, 사람이 손으로 고칠 **단일 as-is/to-be 변경계획서**를 낸다(자율 실행
핸드오프 아님). manifest·검증·게이트·`init`·`review`는 공유하고, 스테이지만 별도다.

모드인 이유(각주가 아니라): docloop의 논지는 *오라클이 있는 부분과 없는 부분을 분리한다*는
것이다. 변경계획 모드는 그 깔끔한 사례다 — **as-is에는 오라클이 있다**(코드·화면·로그가 X라고
말하거나 말하지 않거나 둘 중 하나이며, ground-audit 게이트가 이를 강제한다), **to-be에는
없다**(이건 판단이며 사람의 몫으로 남는다). [`docs/design.md`](design.md) 참고.

```bash
docloop init ~/work/fix-submission ./inputs/            # scaffold + isolate inputs
cd ~/work/fix-submission
cp /path/to/docloop/templates/policy.atb.example.yaml ./policy.atb.yaml   # sequencing + consumer + taxonomy

docloop atb-capture ./inputs/     # read the system -> capture observations (with evidence)
docloop atb-chunk                 # group into chunks + sequence (order + rationale)
docloop atb-author                # write the as-is/to-be body per chunk into the SSOT
docloop atb-audit                 # ground-audit: verify each as-is against its evidence (fan-out)
docloop atb-gate                  # handoff gate (ground_audit.py --strict)
```

스테이지: `atb-capture`(관찰=이슈) → `atb-chunk`(청크=핸드오프, 순서 포함) →
`atb-author`(단일 as-is/to-be 문서) → `atb-audit` / `atb-gate`(ground-audit: 출처 없는
as-is는 차단된다 — *잘못된 as-is 위에 세운 to-be가 가장 비싼 실수다*). `blast_radius` 방향
(기본값 `high_risk_first`)과 ATB **핸드오프 컨슈머**(`consumer`, 기본값 `human` — 계획서를
작성해 전달할 대상; [`docs/design.md`](design.md)의 `authoring`/`evaluator` 컨슈머 **역할**과는
다르다)는 `templates/policy.atb.example.yaml`에 있다.
