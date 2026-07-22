# Change-plan mode (as-is/to-be) · 변경계획 모드

> Moved from README (2026-07-22).

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
