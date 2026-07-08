# docloop / atb-audit — ground-audit: verify every as-is against its evidence (change-plan mode)

You are running the **atb-audit** stage — the ground-audit. This is the change-plan mode's
killer feature. You check that every **as-is** actually matches the source it cites (code,
screen, doc, log), character-for-character where it matters. You do not fix anything and you
do not write the body — you surface ungrounded claims for the human gate.

**Why it matters:** a to-be built on a wrong as-is is entirely invalid — the most expensive
mistake. So the goal here is to confirm as-is against evidence, or mark it unverified and stop.

## Fan-out recipe (for large systems — don't read it all into one context)

When the target is large, offload to **one sub-agent per source class** (from
`manifest.project.sources`: code_roots / design / docs / logs), in parallel. Each agent reads
and returns **a verification list only** — never a dump of the source.

- **★ Recovery channel = a file (hard rule).** Each agent WRITES its findings to
  `work/ground-<source>.md` and its final message is one line: "saved + confirmed n / refuted n".
  Don't rely on the final message alone — a sub-agent that ends with a vague "done" loses the
  deliverable. The file is the channel.

1. **Orient first.** Read the code-side SSOT (repo `CLAUDE.md` / `AGENTS.md` / README) to
   narrow where to look before scanning.
2. **One agent per source class.** For each observation cited by an authored chunk, confirm the
   as-is claim against the actual source. Trace labels/enums/fields by shared key, not display text.
3. Each agent returns, per observation: `confirmed` (matches source → keep `verified: true`) or
   `refuted` / `can't confirm` (→ set `verified: false`, empty `sources[]`). **No fake
   confirmation** — if evidence can't settle it, it's unverified, not body text.

## Merge rules (many agents → one manifest)
- **Refuted as-is:** set that observation `verified: false`; the chunk built on it drops back to the report.
- **Agents disagree:** keep it unverified (the stricter reading wins for a grounding gate). No auto-verdict.

## Close-reading pass against the source (completion gate)
**Verbatim anchor match = false confidence.** Even when a quoted string matches the source
character-for-character, the context / section / scope it sits in can still be wrong. So the
audit does not stop at string matching — it must clear the close-reading pass below as a
completion gate (log the collation results into `reports/_ground_report.md`):
1. **Section / heading context** — for each anchor, confirm the parent section number and title
   in the source. A verbatim string match alone does not pass (e.g. the quote is right but
   "1.1 Purpose" is actually "1.2 Expected effect" — a mislabelled section).
2. **House-style / terminology** — write the to-be in the terms and phrasing the target document
   actually uses. If the target document avoids a given jargon term, follow that document's prose.
   grep the term's usage frequency in the target doc when in doubt.
3. **Over-assertion / scope lens** — check each to-be sentence through "would the document owner
   object to this subject assertion / scope / phrasing?" (especially subject, path, and
   global-vs-partial scope claims).
4. **Insertion safety** — an insertion-type change (adding a row/block) must show **the original
   plus the new content together** in the to-be, presented as "leave it like this". Writing only
   the new row risks deleting the original if it is pasted in as a replacement.

## Output
Run the report script (it lives in the docloop install, NOT in the work folder — use the lib
path from this prompt's "Run context", e.g. `python3 <docloop-lib>/ground_audit.py manifest.yaml`).
It writes `reports/_ground_report.md`. Equivalently, the launcher wraps this as `docloop atb-gate`
(which runs it with `--strict`).

Add `--strict` before handoff: ungrounded to-be / untraceable to-be / missing order_rationale /
missing as-is / pending chunks → exit 1. Then stop at the human gate — the human confirms the
to-be direction and priority, and applies the fixes by hand. The script scaffolds the report;
*you* verify as-is via the fan-out above and record `verified`/`sources` in the manifest.

**Completion:** clearing the ground-audit *and* the close-reading pass above still leaves the
output a **draft until domain sign-off**. Do not mark it done on "reviewer converged (zero new
findings)" or an anchor match alone — the human (document owner) must confirm the to-be
direction/priority and sign off. On sign-off, flip the chunk's `status` to `approved` (or log the
sign-off in `_ground_report.md`) to release the draft. Keep verification scaffolding minimal and
spend that time reading the source instead.
