#!/usr/bin/env python3
"""Scoring report (review/audit mode ①②⑦): reads each manifest section's optional
`scores` (4 axes) and pm-policy `review_audit` (scale · pass_threshold · priority_rubric)
and emits a per-section score table + priority-weighted sort + below-threshold markers
to `reports/_review_audit.md`.

The verdict labels (scores) are filled into the manifest by a **verifier (human/verification
agent)** — this script only aggregates, sorts, and gates them (the authoring agent does not
score its own work — hard rule).

Usage: python3 score_report.py <manifest.yaml> [--out report.md] [--strict]
  --strict: exit 1 if any section has an axis below pass_threshold.
(ported from the gap_audit.py pattern — load_validated · esc · KST · --strict gate.)

Enforcement model: the score threshold (pass_threshold) is a **mechanical block** (--strict).
The axis scoring itself is done by fan-out / a human."""
import sys, os, argparse
from datetime import datetime, timezone, timedelta
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from validate_manifest import load_validated

KST = timezone(timedelta(hours=9))

DEFAULT_AXES = ["completeness", "coherence", "clarity", "depth"]


def esc(s):
    """Sanitize a markdown table cell: None→empty string; escape |, newlines, and backslashes to prevent table breakage."""
    if s is None:
        return ""
    return str(s).replace("\\", "\\\\").replace("|", "\\|").replace("\r", " ").replace("\n", " ").strip()


def _load_policy(proj, base):
    """Load the pm-policy.yaml referenced by manifest.project.policy (returns None if absent)."""
    try:
        import yaml
    except ImportError:
        return None
    pol_path = proj.get("policy")
    if not pol_path:
        return None
    p = pol_path if os.path.isabs(pol_path) else os.path.join(base, pol_path)
    if not os.path.exists(p):
        return None
    with open(p, encoding="utf-8") as f:
        return yaml.safe_load(f)


def main():
    ap = argparse.ArgumentParser(description="review/audit-mode scoring report")
    ap.add_argument("manifest")
    ap.add_argument("--out", default="")
    ap.add_argument("--strict", action="store_true")
    a = ap.parse_args()
    m = load_validated(a.manifest)
    base = os.path.dirname(os.path.abspath(a.manifest))
    proj = m["project"]
    out = a.out or os.path.join(base, "reports", "_review_audit.md")

    pol = _load_policy(proj, base)
    ra = (pol or {}).get("review_audit") or {}
    scoring = ra.get("scoring") or {}
    axes = scoring.get("primary_axes") or DEFAULT_AXES
    scale = scoring.get("scale") or {}
    smin = scale.get("min", 1)
    smax = scale.get("max", 5)
    thr = scale.get("pass_threshold", 3)
    rubric = (ra.get("priority_rubric") or {}).get("weights") or {}

    # Priority weighting: more failing axes and larger deficits increase weight + rubric bonus (axis name matches a weights key → added)
    rows, below = [], []        # below: (sid, title, list of failing axes)
    for s in m.get("sections", []) or []:
        sc = s.get("scores")
        if not isinstance(sc, dict) or not sc:
            continue
        sid = s["id"]; title = s.get("title", "")
        per_axis, deficit, weight = {}, 0, 0
        miss_axes = []
        for ax in axes:
            v = sc.get(ax)
            per_axis[ax] = v
            if isinstance(v, int):
                if v < thr:
                    deficit += (thr - v)
                    miss_axes.append(ax)
                    weight += int(rubric.get(ax, 1))
        # non-axis rubric keys (regulatory, blocking, etc.) are added as section flags
        for k, w in rubric.items():
            if k not in axes and s.get(k):
                weight += int(w)
        prio = weight * 10 + deficit       # weighted priority → deficit as tiebreak
        rows.append((sid, title, per_axis, deficit, prio))
        if miss_axes:
            below.append((sid, title, miss_axes))

    rows.sort(key=lambda r: r[4], reverse=True)   # sort by priority weight descending

    title = proj.get("title") or proj.get("product", "PM doc")
    gen_at = datetime.now(KST).strftime("%Y-%m-%d %H:%M KST")
    L = [f"# {esc(title)} — review/audit scoring report (internal, not for release)", "",
         f"> Auto-generated: `score_report.py` · generated: **{gen_at}** · scale {smin}~{smax}, pass threshold **{thr}**. Not included in the release.", "",
         f"- scored sections: **{len(rows)}**  ·  below-threshold sections: **{len(below)}**",
         f"- axes: " + (", ".join(axes)), ""]

    L += ["## 1. Per-section scores (sorted by priority weight)", ""]
    if rows:
        hdr = "| section | title | " + " | ".join(axes) + " | deficit | priority |"
        sep = "|------|------|" + "------|" * len(axes) + "------|------|"
        L += [hdr, sep]
        for sid, t, per_axis, deficit, prio in rows:
            cells = []
            for ax in axes:
                v = per_axis.get(ax)
                cells.append("—" if v is None else (f"**{v}**⚠" if isinstance(v, int) and v < thr else str(v)))
            L.append(f"| {esc(sid)} | {esc(t)} | " + " | ".join(cells) + f" | {deficit} | {prio} |")
    else:
        L.append("_No scored sections (add scores: {completeness,…} to sections and re-run)._")

    L += ["", "## 2. Below-threshold sections (axes under pass_threshold)", ""]
    if below:
        L += ["| section | title | failing axes |", "|------|------|------|"]
        for sid, t, miss in below:
            L.append(f"| {esc(sid)} | {esc(t)} | {esc(', '.join(miss))} |")
    else:
        L.append("_None below threshold._")
    L.append("")

    os.makedirs(os.path.dirname(os.path.abspath(out)), exist_ok=True)
    with open(out, "w", encoding="utf-8") as f:
        f.write("\n".join(L))
    print(f"review-audit report: {out}  (scored {len(rows)} / below-threshold {len(below)} / threshold {thr})")

    if a.strict and below:
        sys.exit(f"[scoring gate FAILED] {len(below)} section(s) below pass_threshold({thr})")


if __name__ == "__main__":
    main()
