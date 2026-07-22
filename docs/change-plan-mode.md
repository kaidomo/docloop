# Change-plan mode (as-is/to-be) · 변경계획 모드

> Moved from README (2026-07-22).

Reach for this when you're **not** writing a brand-new document, but planning how to fix a
system that already exists. You point docloop at the product, its docs, logs, and code; it
helps you produce a single **as-is/to-be** plan — "here's how it works today, here's what to
change" — that a person then applies by hand.
새 문서를 쓰는 게 아니라 **이미 있는 시스템을 어떻게 고칠지** 계획할 때 쓴다. 제품·문서·로그·
코드를 docloop에 물리면, "지금은 이렇게 동작하고, 이렇게 바꾸자"는 **단일 as-is/to-be 계획서**를
만들도록 돕는다 — 반영은 사람이 손으로 한다.

How you actually do it:
실제로 하는 법:

```bash
docloop init ~/work/fix-submission ./inputs/   # make a work folder (your input files move into inputs/)
docloop atb-capture ./inputs/                  # read the system, note what's true today (with evidence)
docloop atb-chunk                              # group the fixes and put them in a sensible order
docloop atb-author                             # write the as-is/to-be plan
docloop atb-gate                               # block any "as-is" that has no source behind it
```

Limitation: docloop only checks the **as-is** half — that each "today it works like X" claim
has a real source (that part is mechanical). The **to-be** half — what to change, in what
order — is judgment, and stays with you.
한계: docloop이 확인하는 건 **as-is** 절반뿐이다 — "지금은 X로 동작한다"는 주장마다 실제 출처가
있는지(이건 기계적으로 검사). **to-be** 절반, 즉 무엇을 어떤 순서로 바꿀지는 판단이며 당신 몫이다.

See **Technical details** below for the full pipeline and options.
전체 파이프라인과 옵션은 아래 **기술 상세**를 참고.

## Technical details · 기술 상세

A second, delineated pipeline for the other half of the job: not writing a fresh doc, but
**planning fixes to a system that already exists.** You read the product/docs/logs/code, then
produce a single **as-is/to-be** change plan for a human to apply by hand (not an agent handoff).
It reuses the same machinery (manifest, validate, gates, `init`, `review`) with its own stages.
기존에 없는 문서를 새로 쓰는 게 아니라, **이미 있는 시스템을 어떻게 고칠지** 계획하는 다른 절반.
제품·문서·로그·코드를 읽고, 사람이 손으로 고칠 **단일 as-is/to-be 변경계획서**를 낸다(자율 실행 핸드오프 아님).
manifest·검증·게이트·`init`·`review`는 공유하고, 스테이지만 별도다.

Why it's a mode, not a footnote: docloop's thesis is *separate the part with an oracle from the
part without.* Change-plan mode is a clean instance — **as-is has an oracle** (the code/screen/log
either says X or it doesn't; the ground-audit gate enforces it), **to-be doesn't** (it's judgment,
left to the human). See [`docs/design.md`](design.md).

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

Stages: `atb-capture` (observations=issues) → `atb-chunk` (chunks=handoff, with ordering) →
`atb-author` (single as-is/to-be doc) → `atb-audit` / `atb-gate` (ground-audit: an as-is with no
source is blocked — *a to-be built on a wrong as-is is the most expensive mistake*). The
`blast_radius` direction (default `high_risk_first`) and the ATB **handoff consumer**
(`consumer`, default `human` — the recipient the plan is written up for; distinct from the
`authoring`/`evaluator` consumer *role* in [`docs/design.md`](design.md)) live in
`templates/policy.atb.example.yaml`.
