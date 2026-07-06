#!/usr/bin/env python3
"""ground-audit (change-plan mode's killer feature): audit whether each as-is is backed by
real evidence (SSOT: code, screens, docs, logs) and emit a human-gate markdown report.
Internal document (not included in the release).

Core principle: **a to-be built on a wrong as-is is entirely invalid** -> the most expensive
mistake. So this gate surfaces:
 - observations with no confirmed evidence (verified != true, or verified but no source), and
 - authored (non-pending) chunks whose members include an ungrounded observation
   ("a to-be with no grounding"), and chunks with no members / no order_rationale / no as-is.

Usage: python3 ground_audit.py <manifest.yaml> [--out report.md] [--strict] [--strict-cross-audit]
  --strict: exit 1 (handoff gate) on any of: (A) ungrounded to-be (authored chunk with an
           ungrounded member) · (B) chunk with no order_rationale · (C) authored chunk with no
           as-is · (D) untraceable to-be (authored chunk with no members) · (E) pending chunk.
  --strict-cross-audit: implies --strict and ALSO fails when cross-grounding didn't run
           (0 project.sources registered while chunks are authored).

Note: this script is **report scaffolding**. The actual checking that as-is matches its
evidence (opening code/screens/docs/logs to confirm) is done by the fan-out recipe in
prompts/atb-audit.md, which records the result in the manifest's verified/sources. This
script audits that recorded state.
(ground-audit for the docloop change-plan manifest — modeled on gap_audit.py)"""
import sys, os, argparse
from datetime import datetime, timezone, timedelta
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from validate_manifest import load_validated

KST = timezone(timedelta(hours=9))
AUTHORED = {"draft", "review", "approved"}   # bodied states (excludes pending)
# Change-plan audited source classes (coverage counting) — the classes as-is is verified against.
# Distinct from gap_audit's DOC_SRC: change-plan grounds against docs/logs, not prototypes.
ATB_SRC = {"code_roots", "design", "docs", "logs"}


def _grounded(o):
    """Is this observation backed by evidence = verified:true AND at least one non-empty source.
    verified:true with no sources is 'confirmed without evidence' -> treated as ungrounded (blocks false-pass)."""
    if o.get("verified") is not True:
        return False
    return any(isinstance(s, str) and s.strip() for s in (o.get("sources") or []))


def esc(s):
    """Make a value safe for a markdown table cell: None->empty, escape |/newline/backslash."""
    if s is None:
        return ""
    return str(s).replace("\\", "\\\\").replace("|", "\\|").replace("\r", " ").replace("\n", " ").strip()


def _count_paths(d, known):
    """Count registered path entries under recognized keys only (non-empty str, or list of non-empty str).
    Unknown/typo'd keys and empty strings are ignored — counting them would inflate coverage and hide a cross-blind plan."""
    if not isinstance(d, dict):
        return 0
    n = 0
    for k, v in d.items():
        if k not in known:
            continue
        if isinstance(v, str) and v.strip():
            n += 1
        elif isinstance(v, list):
            n += sum(1 for x in v if isinstance(x, str) and x.strip())
    return n


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("manifest")
    ap.add_argument("--out", default="")
    ap.add_argument("--strict", action="store_true")
    ap.add_argument("--strict-cross-audit", action="store_true",
                    help="imply --strict and also fail when cross-grounding didn't run (0 sources, authored chunks)")
    a = ap.parse_args()
    m = load_validated(a.manifest)
    base = os.path.dirname(os.path.abspath(a.manifest))
    out = a.out or os.path.join(base, "reports", "_ground_report.md")
    os.makedirs(os.path.dirname(out), exist_ok=True)

    obs = m.get("observations", []) or []
    obs_by_id = {o["id"]: o for o in obs if isinstance(o, dict) and isinstance(o.get("id"), str)}
    unverified = [o for o in obs if isinstance(o, dict) and not _grounded(o)]

    chunks = m.get("chunks", []) or []
    status_count = {}
    pending, no_rationale, no_asis, no_tobe, ungrounded, memberless = [], [], [], [], [], []
    for c in chunks:
        if not isinstance(c, dict):
            continue
        cid = c.get("id", "?"); st = c.get("status", "?"); title = c.get("title", "")
        status_count[st] = status_count.get(st, 0) + 1
        if st == "pending":
            pending.append((cid, title))
        if not (c.get("order_rationale") or "").strip():
            no_rationale.append((cid, title))
        if st in AUTHORED and not (c.get("asis") or "").strip():
            no_asis.append((cid, title))
        if st in AUTHORED and not (c.get("tobe") or "").strip():
            no_tobe.append((cid, title))
        members = [mid for mid in (c.get("members") or []) if isinstance(mid, str)]
        # traceability: an authored chunk with no grouped observation is a to-be that can't be traced back to evidence
        if st in AUTHORED and not members:
            memberless.append((cid, title))
        # ungrounded to-be: authored chunk with an ungrounded member (the most expensive mistake)
        if st in AUTHORED:
            for mid in members:
                if mid in obs_by_id and not _grounded(obs_by_id[mid]):
                    ungrounded.append((cid, title, mid, obs_by_id[mid].get("what", "")))

    # cross-grounding coverage (honesty guard): if 0 sources are registered, as-is rests on the body's
    # self-assertion only. Surface that instead of letting "0 findings" read as "verified".
    proj = m.get("project", {}) or {}
    n_src = _count_paths(proj.get("sources"), ATB_SRC)
    grounded_ct = sum(v for k, v in status_count.items() if k != "pending")
    cross_blind = n_src == 0 and grounded_ct > 0

    title = proj.get("product", "as-is/to-be")
    gen_at = datetime.now(KST).strftime("%Y-%m-%d %H:%M KST")
    L = [f"# {esc(title)} — ground audit report (internal, not for release)", "",
         f"> Auto-generated: `ground_audit.py` · generated: **{gen_at}** · evidence=SSOT (code/screens/docs/logs). Not for release.", ""]
    if cross_blind:
        L += [f"> ⚠️ **Cross-grounding not run: 0 project.sources registered.** The as-is of "
              f"{grounded_ct} authored chunk(s) was checked as *self-assertion* only — the evidence "
              "locations were not cross-checked against code/screens/docs/logs. Register "
              "`project.sources` (code_roots/design/docs/logs) to cross-check.", ""]
    L += [f"- ungrounded observations (verified≠true or no sources): **{len(unverified)}**  ·  ungrounded to-be (authored chunk with an ungrounded member): **{len(ungrounded)}**",
          f"- untraceable to-be (authored, no members): **{len(memberless)}**  ·  no order_rationale: **{len(no_rationale)}**  ·  authored with no as-is: **{len(no_asis)}**  ·  authored with no to-be: **{len(no_tobe)}**  ·  pending chunks: **{len(pending)}**",
          f"- cross-grounding coverage: **{n_src}** source path(s)" + ("  ·  ⚠️ none registered" if n_src == 0 else ""),
          "- chunk status: " + (", ".join(f"{k} {v}" for k, v in sorted(status_count.items())) or "_none_"), ""]

    L += ["## 1. Ungrounded to-be (the most expensive mistake — authored chunk with an ungrounded observation)", ""]
    if ungrounded:
        L += ["| chunk | title | ungrounded obs | phenomenon |", "|------|------|------|------|"]
        L += [f"| {esc(c)} | {esc(t)} | {esc(mid)} | {esc(w)} |" for c, t, mid, w in ungrounded]
    else:
        L.append("_none_")

    L += ["", "## 2. Ungrounded observations (verified≠true or no sources)", ""]
    if unverified:
        L += ["| ID | phenomenon | kind | sources |", "|------|------|------|------|"]
        L += [f"| {esc(o.get('id',''))} | {esc(o.get('what',''))} | {esc(o.get('kind',''))} | {esc(', '.join(o.get('sources',[]) or []))} |"
              for o in unverified]
    else:
        L.append("_none_")

    L += ["", "## 3. Missing order_rationale (no sequencing reason — just a list)", ""]
    if no_rationale:
        L += ["| chunk | title |", "|------|------|"]
        L += [f"| {esc(c)} | {esc(t)} |" for c, t in no_rationale]
    else:
        L.append("_none_")

    L += ["", "## 4. Missing as-is (authored but no as-is — grounding gate)", ""]
    if no_asis:
        L += ["| chunk | title |", "|------|------|"]
        L += [f"| {esc(c)} | {esc(t)} |" for c, t in no_asis]
    else:
        L.append("_none_")

    L += ["", "## 5. Missing to-be (authored but no to-be — incomplete deliverable)", ""]
    if no_tobe:
        L += ["| chunk | title |", "|------|------|"]
        L += [f"| {esc(c)} | {esc(t)} |" for c, t in no_tobe]
    else:
        L.append("_none_")

    L += ["", "## 6. Pending chunks (no evidence yet — body deferred)", ""]
    if pending:
        L += ["| chunk | title |", "|------|------|"]
        L += [f"| {esc(c)} | {esc(t)} |" for c, t in pending]
    else:
        L.append("_none_")

    L += ["", "## 7. Untraceable to-be (authored but no grouped observation — can't trace to evidence)", ""]
    if memberless:
        L += ["| chunk | title |", "|------|------|"]
        L += [f"| {esc(c)} | {esc(t)} |" for c, t in memberless]
    else:
        L.append("_none_")

    ordered = sorted(((c.get("order"), c.get("id", "?"), c.get("title", ""), c.get("order_rationale", ""))
                      for c in chunks if isinstance(c, dict) and isinstance(c.get("order"), int) and not isinstance(c.get("order"), bool)),
                     key=lambda x: x[0])
    L += ["", "## 8. Resolution order (by order — human confirms)", ""]
    if ordered:
        L += ["| # | chunk | title | order rationale |", "|------|------|------|------|"]
        L += [f"| {esc(od)} | {esc(cid)} | {esc(t)} | {esc(orr)} |" for od, cid, t, orr in ordered]
    else:
        L.append("_none_")
    L.append("")

    with open(out, "w", encoding="utf-8") as f:
        f.write("\n".join(L))
    print(f"ground report: {out}  (ungrounded-obs {len(unverified)} / ungrounded-tobe {len(ungrounded)} / "
          f"memberless {len(memberless)} / no-rationale {len(no_rationale)} / no-asis {len(no_asis)} / no-tobe {len(no_tobe)} / pending {len(pending)})")
    if cross_blind:
        print(f"[warn] cross-grounding not run: 0 project.sources registered "
              f"({grounded_ct} authored chunk(s)) — as-is reflects self-assertion only", file=sys.stderr)

    if a.strict or a.strict_cross_audit:
        fails = []
        if ungrounded:
            fails.append(f"ungrounded to-be {len(ungrounded)}")
        if memberless:
            fails.append(f"untraceable to-be {len(memberless)}")
        if no_rationale:
            fails.append(f"missing order_rationale {len(no_rationale)}")
        if no_asis:
            fails.append(f"missing as-is {len(no_asis)}")
        if no_tobe:
            fails.append(f"missing to-be {len(no_tobe)}")
        if pending:
            fails.append(f"pending chunks {len(pending)}")
        if a.strict_cross_audit and cross_blind:
            fails.append(f"cross-grounding not run (0 sources, {grounded_ct} authored chunk(s))")
        if fails:
            sys.exit("[handoff gate FAILED] " + " + ".join(fails))


if __name__ == "__main__":
    main()
