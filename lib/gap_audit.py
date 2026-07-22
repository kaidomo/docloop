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
           (0 cross-check targets = 0 project.sources paths + 0 readable project.downstream
           files, while sections are non-pending), or when a registered downstream target
           could not be read.
           Opt-in for release CI that must not pass an internal-only check as "clean".
(report scaffolding for the docloop manifest schema)

Note: this script is **report scaffolding**. The actual cross-checking that *fills*
gaps/open_questions (doc ↔ code·policy·design·prototype) is done by the fan-out recipe
(sub-agents) in prompts/gap-audit.md."""
import sys, os, argparse
from datetime import datetime, timezone, timedelta
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from validate_manifest import load_validated

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
    only warns on them), so counting them would inflate coverage and hide a cross-blind doc.
    ※ sources only. downstream uses `_downstream_coverage` (readable real files) — a code root
    is a directory, so real-file counting doesn't fit the source side."""
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


def _iter_pathish(d):
    """Yield every non-empty path string in a mapping's values (str or list[str]) — key name ignored."""
    if not isinstance(d, dict):
        return
    for v in d.values():
        items = [v] if isinstance(v, str) else (v if isinstance(v, list) else [])
        for p in items:
            if isinstance(p, str) and p.strip():
                yield p.strip()


def _downstream_coverage(d, base):
    """downstream coverage → `(number of readable real files, declarations that could NOT be read)`.

    Why the second value is needed: counting alone leaves a hole — when at least one source is
    registered, `n_src + n_ds > 0`, so `cross_blind` never fires and a registered downstream that
    has silently vanished produces **no signal at all**. To hold "what wasn't read is reported as
    not read" **per item** rather than in aggregate, the read failures have to be surfaced
    themselves.

    Counting rules:

    - **Key names are irrelevant** — every registered entry is a target. The validator still warns
      about unknown keys (typo defence), but a target that really exists is counted: a name outside
      the allowlist used to make coverage 0 and raise a false `cross_blind`.
    - Relative paths resolve **against the manifest file** (`~` expanded).
    - Only **existing, readable regular files** count — a directory, a missing file, or an
      unreadable one counts 0. Counting declarations would let coverage be 1 with no file present,
      which defeats the `cross_blind` warning and the `--strict-cross-audit` gate.
    - **Duplicate paths are de-duplicated** (by realpath) — registering the same file twice is 1.
    """
    seen, missing, missing_seen = set(), [], set()
    for p in _iter_pathish(d):
        fp = os.path.expanduser(p)
        if not os.path.isabs(fp):
            fp = os.path.join(base, fp)
        fp = os.path.realpath(fp)
        if os.path.isfile(fp) and os.access(fp, os.R_OK):
            seen.add(fp)                       # the set absorbs duplicate paths
        elif fp not in seen and fp not in missing_seen:
            missing_seen.add(fp)               # de-duplicate failures too (same file twice → 1)
            missing.append(p)                  # keep the **declaration as written** so a human can fix it
    return len(seen), missing


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
    # cross-checked. Count the targets the fan-out had to check against (sources=registered
    # paths / downstream=readable real files); if there are none but sections are drafted,
    # "gaps: 0" reflects INTERNAL consistency only — surface that instead of letting it
    # read as "clean".
    proj = m["project"]
    # sources: doc-mode-specific path-string count (see DOC_SRC — code roots are directories)
    n_src = _count_paths(proj.get("sources"), DOC_SRC)
    # downstream: key name irrelevant · relative to the manifest · existing readable regular files only · de-duplicated
    n_ds, ds_missing = _downstream_coverage(proj.get("downstream"), base)
    n_cross = n_src + n_ds
    grounded = sum(v for k, v in status_count.items() if k != "pending")
    cross_blind = n_cross == 0 and grounded > 0
    # registered but unreadable downstream: surfaced per item even when the aggregate isn't 0
    ds_unreadable = bool(ds_missing)

    title = proj.get("title") or proj.get("product", "PM doc")
    gen_at = datetime.now(KST).strftime("%Y-%m-%d %H:%M KST")
    L = [f"# {esc(title)} — gap audit report (internal, not for release)", "",
         f"> Auto-generated: `gap_audit.py` · generated: **{gen_at}** · evidence=SSOT. Not included in the release.", ""]
    if cross_blind:
        L += [f"> ⚠️ **Cross-consistency not run: 0 cross-check targets** (0 source paths + 0 readable "
              f"downstream files)**.** "
              f"{grounded} non-pending section(s) (draft/review/approved) were checked for *internal* consistency "
              "only — `gaps: 0` here does NOT mean the document agrees with code, design, or "
              "downstream docs. Register `project.sources`/`downstream` to cross-audit (a downstream "
              "path only counts when it **actually points at that file**), or omit "
              "deliberately for an internal-only doc.", ""]
    if ds_unreadable and not cross_blind:
        L += [f"> ⚠️ **{len(ds_missing)} registered downstream target(s) could not be read** — "
              + ", ".join(f"`{esc(p)}`" for p in ds_missing[:5])
              + (f" and {len(ds_missing) - 5} more" if len(ds_missing) > 5 else "")
              + ". Those targets were **not cross-checked** (check for a path typo, a moved file, or "
                "permissions). Other targets exist, so `gaps` is still produced — but for these "
                "documents **`gaps: 0` does NOT mean they agree.**", ""]
    L += [f"- gaps: **{len(gaps)}**  ·  open_questions: **{len(oq)}**(open {len(open_oq)})  ·  pending sections: **{len(pend)}**  ·  pending_apply (unapplied): **{len(pending_apply)}**",
          f"- cross-audit coverage: **{n_src}** source path(s) + **{n_ds}** downstream target(s) (readable real files)"
          + ("  ·  ⚠️ 0 cross-check targets" if n_cross == 0 else "")
          + (f"  ·  ⚠️ {len(ds_missing)} unreadable downstream" if ds_unreadable else ""),
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
        print(f"[warn] cross-consistency not run: 0 cross-check targets "
              f"(0 source paths + 0 readable downstream files, {grounded} grounded section(s)) "
              f"— 'gaps' reflects internal checks only", file=sys.stderr)
    if ds_unreadable:
        print(f"[warn] {len(ds_missing)} registered downstream target(s) unreadable: "
              + ", ".join(ds_missing[:5]) + (f" and {len(ds_missing) - 5} more" if len(ds_missing) > 5 else "")
              + " — those targets were NOT cross-checked", file=sys.stderr)

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
            fails.append(f"cross-audit not run (0 cross-check targets — 0 source paths + 0 readable "
                         f"downstream files, {grounded} non-pending section(s))")
        if a.strict_cross_audit and ds_unreadable:
            fails.append(f"downstream unreadable {len(ds_missing)} ("
                         + ", ".join(ds_missing[:3])
                         + (f" and {len(ds_missing) - 3} more" if len(ds_missing) > 3 else "") + ")")
        if fails:
            sys.exit("[release gate FAILED] " + " + ".join(fails))


if __name__ == "__main__":
    main()
