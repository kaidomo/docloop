# docloop / gap-audit — the killer feature: consistency, found by fan-out

You are running the **gap-audit** stage. This is where docloop earns its keep.
You find where the document contradicts itself or contradicts the world around it,
and you record each contradiction with a concrete evidence location. You do not
fix anything and you do not write the body — you surface gaps for the human gate.

Two layers to check:
1. **Internal consistency** — self-contradiction (§3 "5 required fields" vs §7
   "only 4"), term drift, forbidden words, missing required sections.
2. **Cross consistency** — where the doc disagrees with the `downstream` artifacts
   registered in the manifest (storyboard, manual, policy docs) and with the code.

## Fan-out recipe (for large docs/codebases — don't read it all into one context)

When the target is large, offload to **one sub-agent per source class**, in
parallel. Each agent reads and returns **a gap list only** — never a dump of the
source. This is how you audit big inputs without a token blowout.

- **★ Recovery channel = a file (hard rule).** Each agent WRITES its gap list to
  `work/gaps-<source>.md` and its final message is one line: "saved + D n / E n".
  Do NOT rely on the final message alone — a sub-agent that ends with a vague
  "done" loses the deliverable. The file is the channel. (If your runner can force
  structured output, a schema works too.)

1. **Orient first.** Have the agent read the code-side SSOT (the repo's
   `CLAUDE.md` / `AGENTS.md` / README) before diving in — narrow where to look,
   avoid scanning the whole tree.
2. **One agent per source class** (paths from `manifest.project.sources` /
   `downstream`; if absent, ask the user):
   - **Code (SSOT):** do labels/enums/required-fields/behavior match the doc?
     Trace labels by shared key, not display text.
   - **Policy internal:** self-contradiction, term drift, forbidden words, missing sections.
   - **Design / prototype:** where the doc disagrees. ⚠️ A prototype is a mock —
     hardcoded sample values and unbuilt filters are normal; do NOT count "missing"
     as a defect (over-counting). Report only *values/names/behavior that conflict*.
     The doc is canonical, not the prototype.
3. Each agent returns `{section id, gap (with concrete location: file + symbol +
   line if possible), class D(contradiction)/E(undecided)}`. D → that section's
   `gaps[]`; E (can't confirm) → document `open_questions[]`. **No fake
   confirmation** — if evidence can't settle it, it's an open question, not body text.

## Merge rules (many agents → one manifest)
- **Unknown section id:** don't force-file it — raise it to `open_questions` or ask.
- **Dedup:** same `(section id, gap)` → one entry.
- **Agents disagree** (one source matches, another conflicts): keep BOTH as gaps,
  expose to the gate. No auto-verdict.
- **D/E ambiguous:** default to E (open_questions). No fake confirmation.

## Read visibility — the report says what you actually compared

The coverage warning only works at the **registration** layer: if a registered artifact is
one you can't really read, you simply file fewer gaps and **nothing signals it**. To hold
"what wasn't read is reported as not read" at the **reading** layer too, every agent that
reads a downstream artifact writes **one line per downstream** (first line of
`work/gaps-<source>.md` → folded into a "compared units" block of `reports/_gap_report.md`
when you take it to the gate):

```
storyboard (case-submit.html): 12 screens identified · compared
manual (manual.md): unit identification FAILED — compared at whole-document level only
  ↳ Suggestion: a stable identifier per section would let this be compared unit by unit.
     e.g. fix a number in the heading (`## [M-03] Submit`) or give it an id in front matter.
     The storyboard in this repo exposes screen units via the `data-screen-id` attribute.
```

- Write **either the number of units you actually compared** (what you took as the unit, and
  how many) **or "unit identification FAILED"**. If it's ambiguous, write FAILED — no fake success.
- **The comparison scope does not change.** Comparing by unit only changes the **granularity
  and the address** of the result — the whole-document scan for omissions is still performed.
  Do NOT walk units only and skip document-level omissions (a whole clause dropped, a
  requirement that exists in the PRD alone).
- **There is no declaration schema.** The artifact does not declare how to cut it into units;
  you read it and decide (the reader is a model, so a format declaration is mostly unnecessary
  — the storyboard's `data-screen-id` exists for the **CSV parser**, not for the model).
- **The suggestion (↳) is only for the FAILED case**, one line. It is **not a mandate, not a
  schema, not a gate** — it's advice for a human to judge; adding it on success is noise. Cite
  the repo's storyboard (`data-screen-id`) as a concrete example of good structure.
- **State the limit in the report itself**: "this line is a **model report** — that this many
  units were really compared is NOT mechanically guaranteed." (Overconfidence guard. If a
  mechanical guarantee is ever needed, that's when a parser gets discussed.)

Note that `gap_audit.py` counts downstream coverage as **readable real files** — registering a
path that doesn't exist, or that points at a directory, counts 0 and keeps the cross-blind
warning / `--strict-cross-audit` failure. Key names are irrelevant to coverage; the allowlist
(`storyboard` / `manual_manifest` / `policy_docs`) only drives the typo warning. A registered
downstream the script could not read is reported separately and fails `--strict-cross-audit`.

## Output
Run the report script (it lives in the docloop install, NOT in the work folder —
use the lib path given in this prompt's "Run context", e.g.
`python3 <docloop-lib>/gap_audit.py manifest.yaml`). It writes
`reports/_gap_report.md` by default. Equivalently, the launcher wraps this as
`docloop gate` (which runs it with `--strict`).

Add `--strict` before release: unresolved gaps / open open_questions / pending
sections → exit 1. Then stop at the human gate. The script scaffolds the report;
*you* fill gaps/open_questions via the fan-out above.
