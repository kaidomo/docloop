#!/usr/bin/env python3
"""verbatim check (review/audit mode ⑥): mechanically checks whether the body's
blockquotes match the source **character-for-character**. A script does this, not an LLM —
whether something labeled a 'quote' really matches the source is an exact-substring fact,
not a judgment (hard rule against fake confirmation).

Source = NOT manifest.project.ssot but the **quote's origin (targets)**:
  pm-policy review_audit.verbatim.targets, or the manifest's document-level `verbatim:` items (source).
Check = looks up the body's (SSOT) `>` blockquotes (or quotes named in verbatim.quotes)
       in the source via **exact-substring after whitespace normalization**.

Output: reports/_verbatim_report.md (source SHA256 16 chars · full/partial/miss counts).
Usage: python3 verbatim_check.py <manifest.yaml> [--out report.md] [--strict] [--strict-verbatim-coverage]
  --strict: exit 1 if any quote has no match (MISS).
  --strict-verbatim-coverage: imply --strict and ALSO fail when nothing was verifiable
           (0 quotes or 0 readable sources) — opt-in so release CI can't pass a vacuous check.
(ported from the gap_audit.py pattern — load_validated · esc · KST · --strict gate. reuses split.split_h1.)

Enforcement model: this script is a **mechanical block** axis (SHA · exact-substring).
Axis scoring and applying changes are done by fan-out / a human."""
import sys, os, re, argparse, hashlib
from datetime import datetime, timezone, timedelta
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from validate_manifest import load_validated
from split import split_h1   # body H1 split (for quote location display — auxiliary)

KST = timezone(timedelta(hours=9))


def esc(s):
    """Sanitize a markdown table cell: None → empty string; escape |, newlines, and backslashes."""
    if s is None:
        return ""
    return str(s).replace("\\", "\\\\").replace("|", "\\|").replace("\r", " ").replace("\n", " ").strip()


def _norm_ws(s):
    """Normalize whitespace: collapse all whitespace (newlines, tabs, runs) to a single space and strip both ends.
    Comparison baseline for verbatim exact-substring — differences in line breaks / indentation are ignored, characters are not."""
    return re.sub(r"\s+", " ", str(s)).strip()


def sha16(text):
    """First 16 hex chars of the source's SHA256 (for source identity tracking)."""
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:16]


def extract_blockquotes(md):
    """Extract `>` blockquotes from the body → list of quote strings (consecutive `>` lines merged into one quote).
    Code-fence awareness same as split.split_h1: `>` inside ``` or ~~~ fences is not treated as a blockquote."""
    quotes, cur, fence = [], [], False
    for line in md.splitlines():
        st = line.lstrip()
        if st.startswith("```") or st.startswith("~~~"):
            fence = not fence
            if cur:
                quotes.append(" ".join(cur)); cur = []
            continue
        if not fence and re.match(r" {0,3}>", line):
            cur.append(re.sub(r"^\s{0,3}>\s?", "", line))
        else:
            if cur:
                quotes.append(" ".join(cur)); cur = []
    if cur:
        quotes.append(" ".join(cur))
    return [q for q in (qq.strip() for qq in quotes) if q]


def load_targets(m, base, proj):
    """Build the list of (label, source-path) pairs to check against. Priority:
    1) manifest document-level verbatim: [{source, quotes?}] — the source field
    2) pm-policy review_audit.verbatim.targets
    Paths are relative to the manifest directory (absolute paths used as-is). ~ is expanded."""
    targets = []
    for v in (m.get("verbatim") or []):
        if isinstance(v, dict) and v.get("source"):
            targets.append(v["source"])
    # pm-policy targets
    pol = _load_policy(proj, base)
    ra = (pol or {}).get("review_audit") or {}
    for t in ((ra.get("verbatim") or {}).get("targets") or []):
        if t not in targets:
            targets.append(t)
    resolved = []
    for t in targets:
        p = os.path.expanduser(str(t))
        if not os.path.isabs(p):
            p = os.path.join(base, p)
        resolved.append((str(t), p))
    return resolved


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


def collect_quotes(m, body):
    """Collect the list of quotes to check. Uses manifest verbatim[].quotes if explicitly specified; otherwise all `>` blockquotes in the body."""
    explicit = []
    for v in (m.get("verbatim") or []):
        if isinstance(v, dict):
            for q in (v.get("quotes") or []):
                if isinstance(q, str) and q.strip():
                    explicit.append(q.strip())
    if explicit:
        return explicit
    return extract_blockquotes(body)


def main():
    ap = argparse.ArgumentParser(description="verbatim quote ↔ source exact-substring check")
    ap.add_argument("manifest")
    ap.add_argument("--out", default="")
    ap.add_argument("--strict", action="store_true")
    ap.add_argument("--strict-verbatim-coverage", action="store_true",
                    help="imply --strict and also fail when nothing was verifiable (0 quotes or 0 readable sources)")
    a = ap.parse_args()
    m = load_validated(a.manifest)
    base = os.path.dirname(os.path.abspath(a.manifest))
    proj = m["project"]
    out = a.out or os.path.join(base, "reports", "_verbatim_report.md")

    # body (SSOT)
    ssot_path = os.path.join(base, proj.get("ssot", ""))
    body = ""
    if proj.get("ssot") and os.path.exists(ssot_path):
        body = open(ssot_path, encoding="utf-8").read()

    # load source targets + SHA. Keep each label paired with its own normalized text so a
    # missing target can't shift the matched-source label (a plain zip(targets, present-only
    # texts) would misalign when an earlier target is missing).
    targets = load_targets(m, base, proj)
    present_sources, src_rows, n_missing = [], [], 0   # present_sources: (label, normalized text)
    for label, path in targets:
        if os.path.exists(path):
            txt = open(path, encoding="utf-8").read()
            present_sources.append((label, _norm_ws(txt)))
            src_rows.append((label, sha16(txt), "OK"))
        else:
            n_missing += 1
            src_rows.append((label, "—", "missing"))

    quotes = collect_quotes(m, body)
    results = []   # (quote excerpt, verdict, matched source label)
    n_full = n_partial = n_miss = 0
    for q in quotes:
        nq = _norm_ws(q)
        verdict, where = "MISS", ""
        # FULL: exact-substring (after normalization) against any present source
        for label, ns in present_sources:
            if nq and nq in ns:
                verdict, where = "FULL", label
                break
        if verdict == "MISS" and nq:
            # PARTIAL: check if at least a leading 60% chunk of the quote appears in any present source
            cut = nq[: max(8, int(len(nq) * 0.6))]
            for label, ns in present_sources:
                if cut and cut in ns:
                    verdict, where = "PARTIAL", label
                    break
        if verdict == "FULL":
            n_full += 1
        elif verdict == "PARTIAL":
            n_partial += 1
        else:
            n_miss += 1
        results.append((q, verdict, where))

    # honesty guard: a passing --strict is vacuous if there were no quotes or no readable
    # source to check them against — "MISS 0" then means "nothing checked", not "all match".
    verify_blind = not quotes or not present_sources

    title = proj.get("title") or proj.get("product", "PM doc")
    gen_at = datetime.now(KST).strftime("%Y-%m-%d %H:%M KST")
    L = [f"# {esc(title)} — verbatim check report (internal, not for release)", "",
         f"> Auto-generated: `verbatim_check.py` · generated: **{gen_at}** · exact-substring (whitespace-normalized). Not included in the release.", ""]
    if verify_blind:
        L += [f"> ⚠️ **Nothing verified: {len(quotes)} quote(s), {len(present_sources)} readable source(s)"
              + (f", {n_missing} declared source(s) missing" if n_missing else "")
              + ".** A passing `--strict` here does NOT mean the body's quotes match a source — there was "
              "nothing to check. Add `>` blockquotes and register a readable source.", ""]
    L += [f"- quotes: **{len(quotes)}**  ·  exact (FULL) **{n_full}** · partial (PARTIAL) {n_partial} · no match (MISS) **{n_miss}**",
          f"- sources (targets): **{len(targets)}** declared, **{len(present_sources)}** readable"
          + (f", **{n_missing}** missing" if n_missing else ""), ""]

    L += ["## 1. Sources (targets) SHA256", ""]
    if src_rows:
        L += ["| source | SHA256(16) | status |", "|------|------|------|"]
        L += [f"| {esc(lbl)} | {esc(sha)} | {esc(stt)} |" for lbl, sha, stt in src_rows]
    else:
        L.append("_No sources specified (manifest verbatim[].source or pm-policy review_audit.verbatim.targets)._")

    L += ["", "## 2. Quote check (FULL=exact match to source / PARTIAL=partial / MISS=no match)", ""]
    if quotes:
        L += ["| verdict | matched source | quote (excerpt) |", "|------|------|------|"]
        for q, v, where in results:
            short = q if len(q) <= 80 else q[:77] + "…"
            L.append(f"| {esc(v)} | {esc(where)} | {esc(short)} |")
    else:
        L.append("_No blockquotes in the body (or verbatim.quotes not specified)._")
    L.append("")

    os.makedirs(os.path.dirname(os.path.abspath(out)), exist_ok=True)
    with open(out, "w", encoding="utf-8") as f:
        f.write("\n".join(L))
    print(f"verbatim report: {out}  (FULL {n_full} / PARTIAL {n_partial} / MISS {n_miss} / sources {len(present_sources)}/{len(targets)} readable/declared)")
    if verify_blind:
        print(f"[warn] verbatim verified nothing: {len(quotes)} quote(s), {len(present_sources)} readable source(s)"
              " — a passing --strict does not imply the quotes match a source", file=sys.stderr)
    elif n_missing:
        print(f"[warn] {n_missing} declared verbatim source(s) missing — checked against "
              f"{len(present_sources)} readable source(s) only", file=sys.stderr)

    if a.strict or a.strict_verbatim_coverage:
        fails = []
        if n_miss:
            fails.append(f"{n_miss} quote(s) with no match (MISS)")
        if a.strict_verbatim_coverage and verify_blind:   # opt-in: vacuous pass is a failure
            fails.append(f"nothing verifiable (quotes {len(quotes)}, readable sources {len(present_sources)})")
        if fails:
            sys.exit("[verbatim gate FAILED] " + " + ".join(fails))


if __name__ == "__main__":
    main()
