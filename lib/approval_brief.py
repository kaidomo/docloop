#!/usr/bin/env python3
"""Extract an approval/handoff summary (pm-policy output.approval_brief). One page for the
approval line/reviewers: purpose/goals · scope/out-of-scope · open decisions (open_questions) ·
decision log (decisions) · section status.
Note: docloop does not 'verify' approvals/signatures (see policy.yaml) — this is just a
handoff/review artifact. Internal (not included in the release) — same reports/ family as gap_audit.py.
Usage: python3 approval_brief.py <manifest.yaml> [--out report.md] [--include-resolved]
(report scaffolding for the docloop manifest schema)"""
import sys, os, re, argparse
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from validate_manifest import load_validated
from split import split_h1   # SSOT body H1 split (for extracting purpose/scope body text)

# Identify purpose/goal and scope sections (doc_type-agnostic heuristic: match by id or title keyword)
GOAL_IDS = {"purpose", "goals", "goal", "overview", "background", "problem", "objective"}
GOAL_KW = ["목적", "목표", "배경", "문제"]
SCOPE_IDS = {"scope", "oos", "out-of-scope", "outofscope"}
SCOPE_KW = ["범위", "scope", "out of scope"]


def esc(s):
    """Sanitize a markdown table cell."""
    if s is None:
        return ""
    return str(s).replace("\\", "\\\\").replace("|", "\\|").replace("\r", " ").replace("\n", " ").strip()


def _strip_h1(blk):
    """Strip a leading H1 line (# title) from the block — prevents duplication since approval_brief adds its own ### heading.
    Only removes the line when it matches the same ATX rule as split_h1 (0–3 spaces indent + '# ' + content), to avoid stripping subheadings or code."""
    if not blk:
        return ""
    lines = blk.splitlines(keepends=True)
    if lines and re.match(r" {0,3}#\s+\S", lines[0]):
        lines = lines[1:]
    return "".join(lines).strip("\n")


def _norm(t):
    """Normalize a title for matching: strip leading numbering (`1.`, `2)`) and spaces, then lowercase — absorbs SSOT H1 variations."""
    return re.sub(r"^\s*\d+[.)]\s*", "", str(t)).replace(" ", "").lower()


def _match(sec, ids, kws):
    """Return True if the section is a purpose/scope type — exact id match, or title *starts with* a keyword (to avoid mid-string false positives)."""
    sid = (sec.get("id") or "").lower()
    t = (sec.get("title") or "").strip().lower()
    return sid in ids or any(t.startswith(k) for k in kws)


def _bodies(secs, nblocks, ids, kws, exclude=None):
    """Extract (id, title, status, body_block) for matching sections. Body text is looked up from the normalized SSOT H1 block map.
    exclude: set of section ids already used by another category (e.g. purpose) — prevents duplicate output."""
    exclude = exclude or set()
    out = []
    for s in secs:
        if s.get("id") in exclude or not _match(s, ids, kws):
            continue
        title = s.get("title") or s.get("id")
        blk = nblocks.get(_norm(title))
        out.append((s.get("id"), title, s.get("status", "?"), blk))
    return out


def main():
    ap = argparse.ArgumentParser(description="extract an approval/handoff summary")
    ap.add_argument("manifest")
    ap.add_argument("--out", default="")
    ap.add_argument("--include-resolved", action="store_true", help="also show resolved open_questions")
    a = ap.parse_args()

    m = load_validated(a.manifest)
    base = os.path.dirname(os.path.abspath(a.manifest))
    proj = m["project"]
    out = a.out or os.path.join(base, "reports", "_approval_brief.md")   # reports/ family (workspace standard)

    # SSOT body (fills purpose/scope text when present). Keys are normalized titles (absorbs spacing and numbering variants)
    nblocks = {}
    ssot_path = os.path.join(base, proj.get("ssot", ""))
    if proj.get("ssot") and os.path.exists(ssot_path):
        for t, b in split_h1(open(ssot_path, encoding="utf-8").read()):
            if t and _norm(t) not in nblocks:
                nblocks[_norm(t)] = b

    secs = m.get("sections", []) or []
    status_count = {}
    for s in secs:
        st = s.get("status", "?"); status_count[st] = status_count.get(st, 0) + 1

    oq = m.get("open_questions", []) or []
    open_oq = [q for q in oq if q.get("status", "open") == "open"]
    deferred = [q for q in oq if q.get("status") == "deferred"]
    resolved = [q for q in oq if q.get("status") == "resolved"]
    decisions = m.get("decisions", []) or []

    title = proj.get("title") or proj.get("product", "PM doc")
    L = [f"# {esc(title)} — approval/handoff summary (review brief)", "",
         "> Auto-generated: `approval_brief.py` · **for handoff/review** (not included in the release). "
         "This tool does not verify approvals/signatures (see pm-policy).", "",
         f"- sections: " + (", ".join(f"{k} {v}" for k, v in sorted(status_count.items())) or "_none_"),
         f"- open_questions: open **{len(open_oq)}** · deferred {len(deferred)} · resolved {len(resolved)}  ·  decisions **{len(decisions)}**", ""]

    # 1. Purpose / goals
    L += ["## 1. Purpose / goals", ""]
    goals = _bodies(secs, nblocks, GOAL_IDS, GOAL_KW)
    if goals:
        for _id, t, st, blk in goals:
            L.append(f"### {esc(t)}  ·  _{esc(st)}_")
            L.append(_strip_h1(blk) if blk else "_(no body — no H1 in the SSOT)_")
            L.append("")
    else:
        L += ["_No purpose/goal section found (check section id/title)._", ""]

    # 2. Scope / out of scope (exclude sections already captured under purpose — avoid duplication)
    L += ["## 2. Scope / out of scope", ""]
    scope = _bodies(secs, nblocks, SCOPE_IDS, SCOPE_KW, exclude={g[0] for g in goals})
    if scope:
        for _id, t, st, blk in scope:
            L.append(f"### {esc(t)}  ·  _{esc(st)}_")
            L.append(_strip_h1(blk) if blk else "_(no body)_")
            L.append("")
    else:
        L += ["_No scope section found._", ""]

    # 3. Open decisions
    L += ["## 3. Open decisions (open_questions)", ""]
    show_oq = open_oq + deferred + (resolved if a.include_resolved else [])
    if show_oq:
        L += ["| id | topic | owner | status | reason/note |", "|------|------|------|------|------|"]
        for q in show_oq:
            note = q.get("reason") or q.get("note") or ""
            if q.get("reason") and q.get("note"):
                note = f"{q['reason']} / {q['note']}"
            L.append(f"| {esc(q.get('id'))} | {esc(q.get('topic'))} | {esc(q.get('owner'))} | {esc(q.get('status', 'open'))} | {esc(note)} |")
    else:
        L.append("_No open items._")

    # 4. Decision log
    L += ["", "## 4. Decision log (decisions)", ""]
    if decisions:
        L += ["| id | date | decision | decided by |", "|------|------|------|------|"]
        for d in decisions:
            L.append(f"| {esc(d.get('id'))} | {esc(d.get('date'))} | {esc(d.get('decision'))} | {esc(d.get('by'))} |")
    else:
        L.append("_No recorded decisions._")

    # 5. Section status
    L += ["", "## 5. Section status", ""]
    if secs:
        L += ["| id | title | status |", "|------|------|------|"]
        for s in secs:
            L.append(f"| {esc(s.get('id'))} | {esc(s.get('title'))} | {esc(s.get('status'))} |")
    else:
        L.append("_No sections._")

    os.makedirs(os.path.dirname(os.path.abspath(out)), exist_ok=True)
    with open(out, "w", encoding="utf-8") as f:
        f.write("\n".join(L).rstrip() + "\n")
    print(f"approval brief: {out}  (open_q {len(open_oq)} / decisions {len(decisions)} / sections {len(secs)})")


if __name__ == "__main__":
    main()
