#!/usr/bin/env python3
"""gap-audit (docloop's killer feature): emits a markdown report (for the dev/PM team)
of the manifest's gaps (evidence↔body · PRD↔downstream mismatches, D),
open_questions (open decisions, E), and pending sections.
Internal document (not included in the release).
Usage: python3 gap_audit.py <manifest.yaml> [--out report.md] [--strict] [--strict-cross-audit]
  --strict: exit 1 (release gate) if there are gaps · open open_questions ·
           pending sections · review_audit.pending_apply (unapplied). open_questions with
           status=resolved/deferred are treated as passing.
  --strict-cross-audit: implies --strict and ALSO fails when cross-audit didn't run
           (0 project.sources/downstream registered while sections are non-pending).
           Opt-in for release CI that must not pass an internal-only check as "clean".
(report scaffolding for the docloop manifest schema)

Note: this script is **report scaffolding**. The actual cross-checking that *fills*
gaps/open_questions (doc ↔ code·policy·design·prototype) is done by the fan-out recipe
(sub-agents) in prompts/gap-audit.md."""
import sys, os, argparse
from datetime import datetime, timezone, timedelta
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from validate_manifest import load_validated, KNOWN_DS

# Document-mode audited source classes (coverage counting). Intentionally NOT the full validator
# KNOWN_SRC: docs/logs are recognized by the validator (no unknown-key warning) but the doc-mode
# fan-out audits code/design/prototypes only — counting docs/logs here would hide a cross-blind doc.
DOC_SRC = {"code_roots", "design", "prototypes"}

KST = timezone(timedelta(hours=9))


def esc(s):
    """Make a value safe for a markdown table cell: None->empty, escape |/newline/backslash."""
    if s is None:
        return ""
    return str(s).replace("\\", "\\\\").replace("|", "\\|").replace("\r", " ").replace("\n", " ").strip()


def _count_paths(d, known):
    """Count registered path entries under recognized keys only (str or list[str] values).
    Unknown/typo'd keys are ignored — they aren't real cross-audit targets (validate_manifest
    only warns on them), so counting them would inflate coverage and hide a cross-blind doc."""
    if not isinstance(d, dict):
        return 0
    n = 0
    for k, v in d.items():
        if k not in known:
            continue
        if isinstance(v, str):
            n += 1
        elif isinstance(v, list):
            n += sum(1 for x in v if isinstance(x, str))
    return n


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("manifest")
    ap.add_argument("--out", default="")
    ap.add_argument("--strict", action="store_true")
    ap.add_argument("--strict-cross-audit", action="store_true",
                    help="imply --strict and also fail when cross-audit didn't run (0 sources/downstream)")
    a = ap.parse_args()
    m = load_validated(a.manifest)
    base = os.path.dirname(os.path.abspath(a.manifest))
    out = a.out or os.path.join(base, "reports", "_gap_report.md")
    os.makedirs(os.path.dirname(out), exist_ok=True)

    gaps, pend, status_count = [], [], {}
    for s in m.get("sections", []) or []:
        sid = s["id"]; st = s.get("status", "?")
        status_count[st] = status_count.get(st, 0) + 1
        for g in s.get("gaps", []) or []:
            gaps.append((sid, s.get("title", ""), g))
        if st == "pending":
            pend.append((sid, s.get("title", "")))
    oq = m.get("open_questions", []) or []
    open_oq = [q for q in oq if q.get("status", "open") == "open"]   # only 'open' fails the gate (resolved/deferred pass)
    decisions = m.get("decisions", []) or []
    # review/audit-mode apply tracking (backport): pending_apply = verbally confirmed but not in SSOT -> must be empty to pass (blocks false-pass, Codex#2/#8)
    # malformed shapes are already blocked by load_validated(strict), but guard here too for gate self-sufficiency (peer r1#2)
    ra = m.get("review_audit")
    ra = ra if isinstance(ra, dict) else {}
    pa = ra.get("pending_apply")
    pending_apply = pa if isinstance(pa, list) else []

    # cross-audit coverage (honesty guard): gaps==0 is meaningless if nothing was
    # cross-checked. Count the source/downstream paths the fan-out had to check
    # against; if there are none but sections are drafted, "gaps: 0" reflects
    # INTERNAL consistency only — surface that instead of letting it read as "clean".
    proj = m["project"]
    # downstream keys are shared with the validator; source coverage is doc-mode-specific (see DOC_SRC)
    n_src = _count_paths(proj.get("sources"), DOC_SRC)
    n_ds = _count_paths(proj.get("downstream"), KNOWN_DS)
    n_cross = n_src + n_ds
    grounded = sum(v for k, v in status_count.items() if k != "pending")
    cross_blind = n_cross == 0 and grounded > 0

    title = proj.get("title") or proj.get("product", "PM doc")
    gen_at = datetime.now(KST).strftime("%Y-%m-%d %H:%M KST")
    L = [f"# {esc(title)} — gap audit report (internal, not for release)", "",
         f"> Auto-generated: `gap_audit.py` · generated: **{gen_at}** · evidence=SSOT. Not included in the release.", ""]
    if cross_blind:
        L += [f"> ⚠️ **Cross-consistency not run: 0 sources/downstream registered.** "
              f"{grounded} non-pending section(s) (draft/review/approved) were checked for *internal* consistency "
              "only — `gaps: 0` here does NOT mean the document agrees with code, design, or "
              "downstream docs. Register `project.sources`/`downstream` to cross-audit, or omit "
              "deliberately for an internal-only doc.", ""]
    L += [f"- gaps: **{len(gaps)}**  ·  open_questions: **{len(oq)}**(open {len(open_oq)})  ·  pending sections: **{len(pend)}**  ·  pending_apply (unapplied): **{len(pending_apply)}**",
          f"- cross-audit coverage: **{n_src}** source path(s) + **{n_ds}** downstream target(s)" + ("  ·  ⚠️ none registered" if n_cross == 0 else ""),
          "- section status: " + (", ".join(f"{k} {v}" for k, v in sorted(status_count.items())) or "_none_"), ""]

    L += ["## 1. Gaps (D — evidence↔body or PRD↔downstream)", ""]
    if gaps:
        L += ["| section | title | gap |", "|------|------|------|"]
        L += [f"| {esc(i)} | {esc(t)} | {esc(g)} |" for i, t, g in gaps]
    else:
        L.append("_none_")

    L += ["", "## 2. Open decisions (E — open_questions)", ""]
    if oq:
        L += ["| ID | topic | reason | owner | status |", "|------|------|------|------|------|"]
        L += [f"| {esc(q.get('id',''))} | {esc(q.get('topic',''))} | {esc(q.get('reason',''))} | {esc(q.get('owner',''))} | {esc(q.get('status','open'))} |" for q in oq]
    else:
        L.append("_none_")

    L += ["", "## 3. Pending sections (no evidence yet — body deferred)", ""]
    if pend:
        L += ["| section | title |", "|------|------|"]
        L += [f"| {esc(i)} | {esc(t)} |" for i, t in pend]
    else:
        L.append("_none_")

    L += ["", "## 4. Confirmed decision log (traceability)", ""]
    if decisions:
        L += ["| ID | date | decision | decided by |", "|------|------|------|------|"]
        L += [f"| {esc(d.get('id',''))} | {esc(d.get('date',''))} | {esc(d.get('decision',''))} | {esc(d.get('by',''))} |" for d in decisions]
    else:
        L.append("_none_")

    L += ["", "## 5. pending_apply (verbally confirmed, not yet in SSOT — must be empty to pass release)", ""]
    if pending_apply:
        L += ["| decision_id | doc | note |", "|------|------|------|"]
        L += [f"| {esc(p.get('decision_id',''))} | {esc(p.get('doc',''))} | {esc(p.get('note',''))} |" for p in pending_apply]
    else:
        L.append("_none_")
    L.append("")

    with open(out, "w", encoding="utf-8") as f:
        f.write("\n".join(L))
    print(f"gap report: {out}  (gaps {len(gaps)} / open_questions {len(oq)} / pending {len(pend)} / pending_apply {len(pending_apply)})")
    if cross_blind:
        print(f"[warn] cross-consistency not run: 0 sources/downstream registered "
              f"({grounded} grounded section(s)) — 'gaps' reflects internal checks only", file=sys.stderr)

    if a.strict or a.strict_cross_audit:
        fails = []
        if gaps:
            fails.append(f"gaps {len(gaps)}")
        if open_oq:
            fails.append(f"open_questions(open) {len(open_oq)}")
        if pend:
            fails.append(f"pending sections {len(pend)}")
        if pending_apply:
            fails.append(f"pending_apply(unapplied) {len(pending_apply)}")
        if a.strict_cross_audit and cross_blind:
            fails.append(f"cross-audit not run (0 sources/downstream, {grounded} non-pending section(s))")
        if fails:
            sys.exit("[release gate FAILED] " + " + ".join(fails))


if __name__ == "__main__":
    main()
