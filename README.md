# docloop

**A thin writing harness for PM/spec documents.** It wraps the model CLI you
already use (`codex` or `claude -p`) into a disciplined loop for writing, auditing,
and cross-reviewing documents — PRDs, specs, policies.

docloop adds **no new runtime and no new agent.** The value is in three things:

1. **The prompts** (`prompts/`) — a five-stage pipeline: plan → draft → gap-audit → review → split.
2. **The scripts** (`lib/`) — manifest validation, consistency reporting, release gates, publish split.
3. **The loop discipline** — manifest-as-state, evidence-over-plausibility, and a human approval gate.

## Why a *writing* harness is different from a coding harness

Coding harnesses (the lazy-coding tools) work because code has an **oracle**: the
compiler and the test suite tell you, objectively, whether the loop converged. You
can let an agent grind because something outside the agent can say "still wrong."

**Writing has no oracle.** There is no compiler for a PRD. So a naive "write,
self-check, rewrite" loop just converges on its own confident prose.

docloop's answer is to split the problem:

- **What *can* be made convergent** — factual accuracy, internal/cross-document
  consistency, policy compliance — is driven by loops with real checks:
  gap-audit (fan-out consistency), scripted release gates, and an **external model
  as a stand-in oracle** (the review stage: Codex/Gemini/another Claude critiques
  the draft).
- **What can't** — voice, judgment, the actual decisions — stays **outside the
  loop, with the human.** The harness surfaces gaps and stops; it never
  manufactures consensus.

See [`docs/design.md`](docs/design.md) for the full argument.

## 한국어 요약

**docloop은 PM·기획 문서(PRD·정책서 등)를 위한 얇은 글쓰기 하네스다.** 네가 이미 쓰는
모델 CLI(`codex` 또는 `claude -p`)를 감싸서 **계획 → 작성 → 정합 감사 → 교차 리뷰 → 분할**
파이프라인으로 묶는다. 새 런타임도 새 에이전트도 만들지 않는다 — 가치는 ① 프롬프트(`prompts/`)
② 스크립트(`lib/`) ③ **루프 규율**(manifest=상태, 근거 우선, 사람 승인 게이트)에 있다.

**왜 코딩 하네스와 다른가:** 코드엔 컴파일러·테스트라는 **Oracle**(정답 판정자)이 있어 루프가
수렴한다. 하지만 **글엔 Oracle이 없다.** 그래서 docloop은 문제를 쪼갠다 —

- **수렴 가능한 것**(사실 정확성·문서 간 정합·정책 준수)은 루프로 돌린다: gap-audit(팬아웃
  정합 점검), 스크립트 릴리스 게이트, 그리고 **외부 모델을 대리 Oracle로**(review 단계 = Codex/
  Gemini/다른 Claude가 초안을 교차 검증).
- **수렴 불가능한 것**(문체·판단·실제 의사결정)은 **루프 밖, 사람 몫**으로 둔다. 하네스는
  빈틈을 드러내고 멈출 뿐, 합의를 지어내지 않는다.

회사별 규칙(섹션 순서·용어·금칙어·DoD)은 엔진이 아니라 **`policy.yaml` 한 장**에 둔다. 사용법은
아래 Quick start 참고.

## Install

```bash
git clone https://github.com/kaidomo/docloop && cd docloop
pip install -r requirements.txt       # PyYAML (used by the lib/ scripts)
chmod +x bin/docloop
export PATH="$PWD/bin:$PATH"          # or symlink bin/docloop onto your PATH
export DOCLOOP_MODEL=codex            # or: claude   (default: codex)
```

Requirements: Python 3 + PyYAML (`pip install -r requirements.txt`), and one of
the `codex` or `claude` CLIs on your PATH.

## Quick start

```bash
docloop init ~/work/case-submission ./submission-policy.md   # scaffold + isolate inputs
cd ~/work/case-submission
cp /path/to/docloop/templates/policy.example.yaml ./policy.yaml   # edit to your house style

docloop plan  "PRD for the case submission flow"   # interview -> manifest
docloop draft                                       # write grounded sections
docloop audit                                       # find contradictions, report
docloop review case-submission ./PRD_*.md           # Oracle: external-model cross-review
docloop gate                                        # release gate (strict)
docloop split                                       # regenerate publish pages
```

## The variable layer: `policy.yaml`

Your org's section order, required sections, glossary, forbidden words, tone, and
Definition of Done live in **one file** (`policy.yaml`) — never in the engine. Swap
orgs, swap that one file. See `templates/policy.example.yaml`.

## Layout

```
bin/docloop          thin launcher (wraps codex / claude -p)
prompts/             the five stage prompts (plan/draft/gap-audit/review)
lib/                 python scripts: init, validate, gap_audit, split, approval_brief, stage, ...
templates/           policy + manifest skeletons, review-brief template
docs/design.md       why writing harnesses differ from coding harnesses
```

## License

MIT — see [LICENSE](LICENSE).
