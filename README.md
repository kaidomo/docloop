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

## Install

```bash
git clone https://github.com/<you>/docloop && cd docloop
chmod +x bin/docloop
export PATH="$PWD/bin:$PATH"          # or symlink bin/docloop onto your PATH
export DOCLOOP_MODEL=codex            # or: claude   (default: codex)
```

Requirements: Python 3, and one of the `codex` or `claude` CLIs on your PATH.

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
