# Change-plan mode (as-is/to-be)

한국어: [change-plan-mode.ko.md](change-plan-mode.ko.md)

> Moved from README (2026-07-22).

Reach for this when you're **not** writing a brand-new document, but planning how to fix a
system that already exists. You point docloop at the product, its docs, logs, and code; it
helps you produce a single **as-is/to-be** plan — "here's how it works today, here's what to
change" — that a person then applies by hand.

How you actually do it:

```bash
docloop init ~/work/fix-submission ./inputs/   # make a work folder (your input files move into inputs/)
docloop atb-capture ./inputs/                  # read the system, note what's true today (with evidence)
docloop atb-chunk                              # group the fixes and put them in a sensible order
docloop atb-author                             # write the as-is/to-be plan
docloop atb-audit                              # check each "as-is" against the evidence behind it
docloop atb-gate                               # last stop: block if any as-is is still unsourced
```

Limitation: docloop only checks the **as-is** half — that each "today it works like X" claim
has a real source (that part is mechanical). The **to-be** half — what to change, in what
order — is judgment, and stays with you.

See **Technical details** below for the full pipeline and options.

## Technical details

A second, delineated pipeline for the other half of the job: not writing a fresh doc, but
**planning fixes to a system that already exists.** You read the product/docs/logs/code, then
produce a single **as-is/to-be** change plan for a human to apply by hand (not an agent handoff).
It reuses the same machinery (manifest, validate, gates, `init`, `review`) with its own stages.

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
