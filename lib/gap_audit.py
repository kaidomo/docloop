#!/usr/bin/env python3
"""gap-audit (docloop's killer feature): emits a markdown report (for the dev/PM team)
of the manifest's gaps (evidence↔body · PRD↔downstream mismatches, D),
open_questions (open decisions, E), and pending sections.
Internal document (not included in the release).
Usage: python3 gap_audit.py <manifest.yaml> [--out report.md] [--strict]
  --strict: exit 1 (release gate) if there are gaps · open open_questions ·
           pending sections · review_audit.pending_apply (unapplied). open_questions with
           status=resolved/deferred are treated as passing.
(report scaffolding for the docloop manifest schema)

Note: this script is **report scaffolding**. The actual cross-checking that *fills*
gaps/open_questions (doc ↔ code·policy·design·prototype) is done by the fan-out recipe
(sub-agents) in prompts/gap-audit.md."""
import sys, os, argparse
from datetime import datetime, timezone, timedelta
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from validate_manifest import load_validated

KST = timezone(timedelta(hours=9))


def esc(s):
    """마크다운 표 cell 안전화: None→빈칸, |·줄바꿈·역슬래시 깨짐 방지."""
    if s is None:
        return ""
    return str(s).replace("\\", "\\\\").replace("|", "\\|").replace("\r", " ").replace("\n", " ").strip()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("manifest")
    ap.add_argument("--out", default="")
    ap.add_argument("--strict", action="store_true")
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
    open_oq = [q for q in oq if q.get("status", "open") == "open"]   # open만 게이트 실패(resolved·deferred는 통과)
    decisions = m.get("decisions", []) or []
    # 재검토/감사 모드 적용추적(백포트): pending_apply = 구두확정·SSOT 미반영 → 비어야 통과(false-pass 차단, Codex#2/#8)
    # malformed shape는 load_validated(strict)가 이미 차단하지만, 게이트 자기완결성 위해 방어(peer r1#2)
    ra = m.get("review_audit")
    ra = ra if isinstance(ra, dict) else {}
    pa = ra.get("pending_apply")
    pending_apply = pa if isinstance(pa, list) else []

    title = m["project"].get("title") or m["project"].get("product", "PM doc")
    gen_at = datetime.now(KST).strftime("%Y-%m-%d %H:%M KST")
    L = [f"# {esc(title)} — gap audit report (internal, not for release)", "",
         f"> Auto-generated: `gap_audit.py` · generated: **{gen_at}** · evidence=SSOT. Not included in the release.", "",
         f"- gaps: **{len(gaps)}**  ·  open_questions: **{len(oq)}**(open {len(open_oq)})  ·  pending sections: **{len(pend)}**  ·  pending_apply (unapplied): **{len(pending_apply)}**",
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

    if a.strict:
        fails = []
        if gaps:
            fails.append(f"gaps {len(gaps)}")
        if open_oq:
            fails.append(f"open_questions(open) {len(open_oq)}")
        if pend:
            fails.append(f"pending sections {len(pend)}")
        if pending_apply:
            fails.append(f"pending_apply(unapplied) {len(pending_apply)}")
        if fails:
            sys.exit("[release gate FAILED] " + " + ".join(fails))


if __name__ == "__main__":
    main()
