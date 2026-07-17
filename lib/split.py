#!/usr/bin/env python3
"""output stage: unified PM SSOT body → pages for the publish platform (derivative, regenerated).
- Section order = pm-policy doc_types[doc_type].sections order (manifest sections order if no policy).
- 'Share only what's confirmed' (pm-policy principle): by default only status=approved sections
  get a body. pending is excluded as no-body.
  With --include-draft, draft/review are also included (internal preview). pending is always excluded.
- Title = pm-policy output.page_pattern with {product}/{feature}/{title} substituted (manifest title if absent).
- Safety guards: output_dir must be a dedicated subfolder directly under the manifest folder +
  generation marker (rmtree protection) + realpath/symlink checks + empty-folder adoption. Path-separator sanitizing in filenames.
Usage: python3 split.py <manifest.yaml> [--strict] [--dry-run] [--include-draft]
  --strict : publish-completeness gate — an included section with no body (H1) / duplicate H1 in SSOT → fail.
             Note: the policy required-section completeness · gaps · open_questions release gate is owned by gap_audit.py --strict.
                (Since policy required can be omitted with a justification=deferred, split only warns on unapproved required sections.)
  --dry-run: print the plan only, no deletes/writes
Note: importing into the publish platform (Confluence/Notion/Wiki, etc.) is done by a human — mind images and relative paths."""
import sys, os, re, shutil, argparse
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from validate_manifest import load_validated

try:
    import yaml
except ImportError:
    yaml = None

MARKER = ".docloop_output"   # marker for docloop-created output folder (rmtree safety)


def safe_filename(name):
    """Strip path separators and parent-directory references from a filename component (prevents writes outside the outputs/ boundary)."""
    s = str(name).replace("/", "_").replace("\\", "_").replace("\x00", "").strip()
    s = s.lstrip(".")
    return s or "untitled"


def split_h1(md):
    """Return a list of (title, block) pairs split at H1 (# ) boundaries. Ignores # inside code fences (``` ~~~)."""
    blocks, cur_title, cur, fence = [], None, [], False
    for line in md.splitlines(keepends=True):
        st = line.lstrip()
        if st.startswith("```") or st.startswith("~~~"):
            fence = not fence
        if not fence and re.match(r" {0,3}#\s+\S", line):   # ATX: 0–3 spaces of indent only (4+ spaces = code block)
            if cur_title is not None or cur:
                blocks.append((cur_title, "".join(cur)))
            title = re.sub(r"\s+#+\s*$", "", line.strip().lstrip("#").strip())  # closing ‘#’ stripped only when preceded by space (preserves ‘C#’)
            cur_title, cur = title, [line]
        else:
            cur.append(line)
    if cur_title is not None or cur:
        blocks.append((cur_title, "".join(cur)))
    return blocks


def load_policy(proj, base):
    """Load pm-policy.yaml from the path given in manifest.project.policy (returns None if absent)."""
    pol_path = proj.get("policy")
    if not pol_path or yaml is None:
        return None
    p = pol_path if os.path.isabs(pol_path) else os.path.join(base, pol_path)
    if not os.path.exists(p):
        return None
    with open(p, encoding="utf-8") as f:
        return yaml.safe_load(f)


def page_title(proj, policy):
    """Substitute output.page_pattern to produce the publish page title (falls back to manifest title/product)."""
    pat = None
    if policy:
        pat = (policy.get("output", {}) or {}).get("page_pattern")
    title = proj.get("title") or proj.get("product") or "PM doc"
    if not pat:
        return title
    product = proj.get("product") or (policy or {}).get("org", {}).get("product_default", "")
    feature = proj.get("feature") or proj.get("title") or ""
    out = pat.replace("{product}", str(product)).replace("{feature}", str(feature)).replace("{title}", str(title))
    out = out.strip()
    return out or title


def main():
    ap = argparse.ArgumentParser(description="split PM SSOT into publish pages")
    ap.add_argument("manifest")
    ap.add_argument("--strict", action="store_true")
    ap.add_argument("--dry-run", dest="dry", action="store_true")
    ap.add_argument("--include-draft", dest="include_draft", action="store_true",
                    help="also include draft/review sections (default: approved only)")
    a = ap.parse_args()

    m = load_validated(a.manifest)
    base = os.path.dirname(os.path.abspath(a.manifest))
    proj = m["project"]
    policy = load_policy(proj, base)

    ssot_path = os.path.join(base, proj["ssot"])
    if not os.path.exists(ssot_path):
        sys.exit(f"[abort] SSOT body not found: {ssot_path}")
    body = open(ssot_path, encoding="utf-8").read()
    blocks, dup_h1 = {}, []            # title→block (duplicate H1: keep first block + record)
    for t, b in split_h1(body):
        if not t:
            continue
        t = t.strip()
        if t in blocks:
            dup_h1.append(t)           # prevent silent overwrite — surfaced via warning/strict
        else:
            blocks[t] = b

    od = proj.get("output_dir")
    if not od:
        sys.exit("[abort] project.output_dir not set — the work-folder standard is 'outputs'. Specify it in the manifest.")
    out_dir = os.path.join(base, od)

    # Section order: policy doc_types[doc_type].sections → fall back to manifest sections order
    man_secs = {s["id"]: s for s in m.get("sections", []) or []}
    pol_secs = []
    if policy and proj.get("doc_type"):
        dt = (policy.get("doc_types", {}) or {}).get(proj["doc_type"])
        if dt:
            pol_secs = dt.get("sections", []) or []
    if pol_secs:
        order = [(p["id"], p.get("title", ""), bool(p.get("required"))) for p in pol_secs]
        # Sections present in the manifest but absent from the policy are appended at the end (prevents omission)
        for sid, s in man_secs.items():
            if sid not in {p["id"] for p in pol_secs}:
                order.append((sid, s.get("title", ""), False))
    else:
        order = [(s["id"], s.get("title", ""), False) for s in m.get("sections", []) or []]

    include = {"approved"} | ({"draft", "review"} if a.include_draft else set())
    pages, excluded, no_body, req_unmet = [], [], [], []
    for sid, pol_title, required in order:
        s = man_secs.get(sid)
        st = s.get("status") if s else "(undefined)"
        title = (s.get("title") if s else None) or pol_title or sid
        # For the release gate: a policy-required section is not approved
        if required and st != "approved":
            req_unmet.append((sid, title, st))
        if st not in include:
            excluded.append((sid, title, st))
            continue
        blk = blocks.get(title)
        if blk is None:   # exact title match failed → retry with whitespace normalization
            for bt, bb in blocks.items():
                if bt.strip() == title.strip():
                    blk = bb; break
        if blk is None:
            no_body.append((sid, title)); continue
        pages.append((sid, title, blk))

    # H1 blocks present in the body but absent from the manifest (orphan sections)
    man_titles = {(man_secs[sid].get("title") or "") for sid in man_secs}
    orphan = [t for t in blocks if t not in man_titles and t != page_title(proj, policy)]

    ptitle = page_title(proj, policy)
    out_name = f"{safe_filename(ptitle)}.md"

    # Warnings always emitted. --strict failures cover 'publish completeness' (outgoing body) only — required-section completeness gate belongs to gap_audit (separation of concerns).
    warns = []
    if excluded:
        warns.append("excluded (unconfirmed/pending): " + ", ".join(f"{i}({st})" for i, _, st in excluded))
    if no_body:
        warns.append("no body (included but no H1 in SSOT): " + ", ".join(i for i, _ in no_body))
    if dup_h1:
        warns.append("duplicate H1 in SSOT (first block kept, rest ignored): " + ", ".join(sorted(set(dup_h1))))
    if orphan:
        warns.append("orphan H1 (not a manifest section): " + ", ".join(orphan))
    if req_unmet:   # always a warning (informational). blocking is gap_audit.py --strict — policy required sections may be omitted with a justification (deferred)
        warns.append("policy required not approved (FYI — completeness gate is gap_audit): " + ", ".join(f"{i}({st})" for i, _, st in req_unmet))
    if re.search(r"\{[^}]+\}", ptitle):
        warns.append(f"unsubstituted page_pattern token left: '{ptitle}' (check pm-policy output.page_pattern)")
    for w in warns:
        print(f"  ⚠ {w}")
    # strict failure: publish completeness — included section has no body / duplicate H1 in SSOT (silent loss)
    if a.strict and (no_body or dup_h1):
        fails = []
        if no_body:
            fails.append(f"no body {len(no_body)}")
        if dup_h1:
            fails.append(f"duplicate H1 {len(set(dup_h1))}")
        sys.exit(f"[abort] --strict publish-completeness failed: {' + '.join(fails)} (required completeness via gap_audit.py --strict)")

    if not pages:
        print("  (no confirmed sections to include — no approved sections or no body written)")

    if a.dry:
        print("=== DRY-RUN (no writes/deletes) ===")
        print(f"publish page: {out_name}")
        print(f"included sections {len(pages)}: " + ", ".join(i for i, _, _ in pages))
        if excluded:
            print(f"excluded {len(excluded)}: " + ", ".join(i for i, _, _ in excluded))
        return

    # Write path: rmtree safety guard (dedicated direct subfolder + generation marker, realpath-based)
    abs_out, abs_base = os.path.realpath(out_dir), os.path.realpath(base)   # output_dir existence already checked above
    if (os.path.dirname(abs_out) != abs_base
            or os.path.basename(abs_out) in ("", ".", "..")):
        sys.exit(f"[abort] output_dir safety check failed: '{out_dir}' — must be a dedicated subfolder directly under the manifest folder (symlinks checked by real path).")
    # Marker must be a single filename component — an empty/'.'/'..'/separator marker
    # would make any existing non-empty folder look "marked" and disable the rmtree guard.
    if (not MARKER or MARKER in (".", "..") or os.path.isabs(MARKER)
            or os.path.basename(MARKER) != MARKER or "/" in MARKER or "\\" in MARKER or "\x00" in MARKER):
        sys.exit(f"[abort] invalid generation marker name: {MARKER!r} — must be a single filename component.")
    # Lexically-normalized symlink check: 'alias/' or 'alias/.' would make islink(out_dir)
    # return false and delegate rmtree to the symlink target (upstream-hardened guard).
    if os.path.islink(os.path.normpath(os.path.abspath(out_dir))):
        sys.exit(f"[abort] output_dir '{out_dir}' is a symlink — rejected (boundary can't be guaranteed; use a real folder).")
    if os.path.isdir(abs_out):
        if os.path.exists(os.path.join(abs_out, MARKER)):
            shutil.rmtree(abs_out)
        elif os.listdir(abs_out):
            sys.exit(f"[abort] '{out_dir}' has no generation marker ({MARKER}) and is not empty (including hidden files — e.g. .DS_Store) — "
                     f"may not be a folder this tool created, so deletion is refused. Empty the folder or point output_dir at another empty folder.")
        # else: empty folder (outputs/ created by init_workspace) → adopt it
    os.makedirs(abs_out, exist_ok=True)
    open(os.path.join(abs_out, MARKER), "w").close()

    parts = [f"<!-- generated page (derivative): regenerated by docloop split.py. edit the SSOT, not this. -->"]
    for _sid, _title, blk in pages:
        parts.append(blk.rstrip() + "\n")
    with open(os.path.join(out_dir, out_name), "w", encoding="utf-8") as f:
        f.write("\n".join(parts).rstrip() + "\n")

    print(f"published: {os.path.join(out_dir, out_name)}  ({len(pages)} sections"
          + (f", {len(excluded)} excluded" if excluded else "") + ")")
    if policy and (policy.get("output", {}) or {}).get("approval_brief"):
        print("  ℹ️ pm-policy output.approval_brief=true — generate the approval extract separately via approval_brief.py")


if __name__ == "__main__":
    main()
