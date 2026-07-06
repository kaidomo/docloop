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

## Output
Run the report script (it lives in the docloop install, NOT in the work folder — use the lib
path from this prompt's "Run context", e.g. `python3 <docloop-lib>/ground_audit.py manifest.yaml`).
It writes `reports/_ground_report.md`. Equivalently, the launcher wraps this as `docloop atb-gate`
(which runs it with `--strict`).

Add `--strict` before handoff: ungrounded to-be / untraceable to-be / missing order_rationale /
missing as-is / pending chunks → exit 1. Then stop at the human gate — the human confirms the
to-be direction and priority, and applies the fixes by hand. The script scaffolds the report;
*you* verify as-is via the fan-out above and record `verified`/`sources` in the manifest.
