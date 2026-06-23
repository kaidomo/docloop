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

## Output
Run `python3 lib/gap_audit.py <manifest.yaml> --out reports/_gap_report.md`
(add `--strict` before release: unresolved gaps / open open_questions / pending
sections → exit 1). Then stop at the human gate. The script scaffolds the report;
*you* fill gaps/open_questions via the fan-out above.
