#!/usr/bin/env python3
"""Validate the docloop manifest schema (called before gap-audit/split). Catches bad values before a crash.
Usage: python3 validate_manifest.py <manifest.yaml>   (prints results when run standalone)
Other scripts: from validate_manifest import load_validated
(schema validation for the docloop manifest)"""
import sys, os, yaml

STATUS = {"draft", "review", "approved", "pending"}
SCORE_AXES = {"completeness", "coherence", "clarity", "depth"}   # review/audit mode section scores keys (absent = pass)
# Recognized cross-audit key sets — promoted to module level so gap_audit.py / ground_audit.py
# import one source of truth for coverage counting (was inline).
KNOWN_DS = {"storyboard", "manual_manifest", "policy_docs"}
KNOWN_SRC = {"code_roots", "design", "prototypes", "docs", "logs"}   # docs/logs added for change-plan mode grounding (additive)
KIND_DEFAULT = {"bug", "intent_gap", "improvement", "new"}   # change-plan observation.kind default taxonomy (extend via policy — unknown warns only)


def _pathish(v):
    """Whether the value is a path (str) or list of paths (list[str]) — used to validate downstream/sources values."""
    return isinstance(v, str) or (isinstance(v, list) and all(isinstance(x, str) for x in v))


def _str_list(v):
    return isinstance(v, list) and all(isinstance(x, str) for x in v)


def validate(m):
    """Returns (errors, warnings). If there are errors, execution must abort."""
    E, W = [], []
    if not isinstance(m, dict):
        return ["manifest top level is not a mapping"], []

    proj = m.get("project")
    if not isinstance(proj, dict):
        E.append("project block missing/malformed")
    else:
        for k in ("product", "ssot"):
            if not proj.get(k):
                E.append(f"project.{k} missing")
        if not proj.get("doc_type") and "observations" not in m and "chunks" not in m:
            # doc_type is a document-mode concept; change-plan mode (observations/chunks present, even if empty) doesn't use it
            W.append("project.doc_type not set (a pm-policy doc_types key is recommended)")
        ds = proj.get("downstream")
        if ds is not None:
            if not isinstance(ds, dict):
                E.append("project.downstream must be a mapping")
            else:
                for k, val in ds.items():
                    if k not in KNOWN_DS:
                        W.append(f"project.downstream unknown key '{k}' (allowed {KNOWN_DS} — typo?)")
                    elif not _pathish(val):
                        E.append(f"project.downstream.{k} must be a path (str) or path list (list[str]): {val!r}")
        src = proj.get("sources")
        if src is not None:
            if not isinstance(src, dict):
                E.append("project.sources must be a mapping")
            else:
                for k, val in src.items():
                    if k not in KNOWN_SRC:
                        W.append(f"project.sources unknown key '{k}' (allowed {KNOWN_SRC} — typo?)")
                    elif not _pathish(val):
                        E.append(f"project.sources.{k} must be a path (str) or path list (list[str]): {val!r}")

    sections = m.get("sections", [])
    if not isinstance(sections, list):
        E.append("sections is not a list"); sections = []
    elif not sections and "observations" not in m and "chunks" not in m:
        # change-plan mode uses observations/chunks instead of sections (present, even if empty) — only warn when truly a document manifest
        W.append("sections is empty (no section skeleton — need a stage-0 draft?)")
    ids = {}
    for idx, s in enumerate(sections):
        tag = f"sections[{idx}]"
        if not isinstance(s, dict) or not s.get("id"):
            E.append(f"{tag}: id missing"); continue
        sid = s["id"]
        if not isinstance(sid, str):
            E.append(f"{tag}: id must be a string ({sid!r})"); continue
        tag = f"section '{sid}'"
        ids[sid] = ids.get(sid, 0) + 1
        if not s.get("title"):
            E.append(f"{tag}: title missing")
        st = s.get("status")
        if not isinstance(st, str) or st not in STATUS:
            E.append(f"{tag}: status '{st}' invalid (allowed {STATUS})")
        for listk in ("sources", "gaps"):
            v = s.get(listk)
            if v is None:
                continue
            if not isinstance(v, list):
                E.append(f"{tag}: {listk} must be a list"); continue
            for j, item in enumerate(v):
                if not isinstance(item, str) or not item.strip():
                    E.append(f"{tag}: {listk}[{j}] must be a non-empty string ({item!r})")
        if st == "approved" and not s.get("sources"):
            W.append(f"{tag}: status=approved but sources is empty (confirmed without evidence?)")
        # review/audit mode: optional scores (absent = pass; present = format-only validation)
        sc = s.get("scores")
        if sc is not None:
            if not isinstance(sc, dict):
                E.append(f"{tag}: scores must be a mapping")
            else:
                for ax, v in sc.items():
                    if ax not in SCORE_AXES:
                        W.append(f"{tag}: scores unknown axis '{ax}' (allowed {SCORE_AXES} — typo?)")
                    elif not isinstance(v, int) or isinstance(v, bool):
                        E.append(f"{tag}: scores.{ax} must be an integer ({v!r})")
    for sid, c in ids.items():
        if c > 1:
            E.append(f"duplicate section id: '{sid}' ×{c}")

    REQUIRED = {"open_questions": "topic", "decisions": "decision"}   # minimum required fields for human-gate report
    OQ_STATUS = {"open", "resolved", "deferred"}
    for listk in ("open_questions", "decisions"):
        v = m.get(listk, [])
        if v in (None, []):
            continue
        if not isinstance(v, list):
            E.append(f"{listk} is not a list"); continue
        seen = set()
        for i, item in enumerate(v):
            if not isinstance(item, dict) or not item.get("id"):
                E.append(f"{listk}[{i}]: id missing"); continue
            iid = item["id"]
            if not isinstance(iid, str):
                E.append(f"{listk}[{i}]: id must be a string ({iid!r})"); continue
            if iid in seen:
                E.append(f"{listk}: duplicate id '{iid}'")
            seen.add(iid)
            req = REQUIRED[listk]
            if not item.get(req):
                E.append(f"{listk} '{iid}': required field '{req}' missing")
            if listk == "open_questions":
                stq = item.get("status", "open")
                if not isinstance(stq, str) or stq not in OQ_STATUS:
                    E.append(f"open_questions '{iid}': status '{stq}' invalid (allowed {OQ_STATUS})")
                if not item.get("owner"):
                    W.append(f"open_questions '{iid}': owner recommended (gate owner unclear)")

    # review/audit mode: optional document-level verbatim (absent = pass; present = format-only validation)
    vb = m.get("verbatim")
    if vb is not None:
        if not isinstance(vb, list):
            E.append("verbatim must be a list")
        else:
            for i, item in enumerate(vb):
                tag = f"verbatim[{i}]"
                if not isinstance(item, dict) or not item.get("source"):
                    E.append(f"{tag}: source missing (source path)"); continue
                if not isinstance(item["source"], str):
                    E.append(f"{tag}: source must be a string (path) ({item['source']!r})")
                sha = item.get("sha256")
                if sha is not None and not isinstance(sha, str):
                    E.append(f"{tag}: sha256 must be a string ({sha!r})")
                qs = item.get("quotes")
                if qs is not None:
                    if not isinstance(qs, list):
                        E.append(f"{tag}: quotes must be a list")
                    else:
                        for j, q in enumerate(qs):
                            if not isinstance(q, str) or not q.strip():
                                E.append(f"{tag}: quotes[{j}] must be a non-empty string ({q!r})")

    # review/audit mode: optional review_audit apply-tracking (absent = pass; backward-compatible). pending_apply/applied link to decisions[] (SSOT) via decision_id (Codex#5).
    ra = m.get("review_audit")
    if ra is not None:
        if not isinstance(ra, dict):
            E.append("review_audit must be a mapping")
        else:
            # set of authoritative decisions[] ids — for decision_id referential integrity check (blocks dangling refs, peer r1#1)
            dec_ids = {d.get("id") for d in (m.get("decisions") or [])
                       if isinstance(d, dict) and isinstance(d.get("id"), str)}
            cross = {}   # decision_id → lists it appears in (warn if present in both pending_apply and applied, peer r1#4)
            for listk in ("pending_apply", "applied"):
                v = ra.get(listk)
                if v is None:
                    continue
                if not isinstance(v, list):
                    E.append(f"review_audit.{listk} must be a list"); continue
                seen = set()
                for i, item in enumerate(v):
                    tag = f"review_audit.{listk}[{i}]"
                    if not isinstance(item, dict):
                        E.append(f"{tag}: must be a mapping"); continue
                    did = item.get("decision_id")
                    if not isinstance(did, str) or not did.strip():
                        E.append(f"{tag}: decision_id required (non-empty string — links to decisions[], traceability)"); continue
                    if did not in dec_ids:
                        E.append(f"{tag}: decision_id '{did}' not in decisions[] (dangling — traceability)")
                    if did in seen:
                        E.append(f"review_audit.{listk}: duplicate decision_id '{did}'")
                    seen.add(did)
                    cross.setdefault(did, set()).add(listk)
            for did, lists in cross.items():
                if len(lists) > 1:
                    W.append(f"review_audit: decision_id '{did}' present in both pending_apply and applied (in transition? if applied, remove from pending_apply)")

    # ── change-plan mode (as-is/to-be): optional observations[] (=issue) + chunks[] (=handoff + as-is/to-be) ──
    # absent = pass (document mode); present = validated. Mirrors the optional-block idiom above.
    obs = m.get("observations")
    obs_ids = set()
    if obs is not None:
        if not isinstance(obs, list):
            E.append("observations must be a list"); obs = []
        dup_o = {}
        for i, o in enumerate(obs if isinstance(obs, list) else []):
            tag = f"observations[{i}]"
            if not isinstance(o, dict) or not o.get("id"):
                E.append(f"{tag}: id missing"); continue
            oid = o["id"]
            if not isinstance(oid, str):
                E.append(f"{tag}: id must be a string ({oid!r})"); continue
            tag = f"observation '{oid}'"
            dup_o[oid] = dup_o.get(oid, 0) + 1
            obs_ids.add(oid)
            if not o.get("what"):
                E.append(f"{tag}: what (the observed phenomenon) missing")
            kind = o.get("kind")
            if kind is not None:
                if not isinstance(kind, str):
                    E.append(f"{tag}: kind must be a string ({kind!r})")
                elif kind not in KIND_DEFAULT:
                    W.append(f"{tag}: kind '{kind}' not in default taxonomy {KIND_DEFAULT} (ignore if a policy extension, fix if a typo)")
            ver = o.get("verified")
            if ver is not None and not isinstance(ver, bool):
                E.append(f"{tag}: verified must be a bool ({ver!r})")
            s = o.get("sources")
            if s is not None and not _str_list(s):
                E.append(f"{tag}: sources must be a list of strings ({s!r})")
            if o.get("verified") is True and not o.get("sources"):
                W.append(f"{tag}: verified=true but sources is empty (a confirmed claim with no evidence location — as-is grounding gate risk)")
        for oid, c in dup_o.items():
            if c > 1:
                E.append(f"duplicate observation id: '{oid}' ×{c}")

    chunks = m.get("chunks")
    if chunks is not None:
        if not isinstance(chunks, list):
            E.append("chunks must be a list"); chunks = []
        dup_c, orders = {}, []
        for i, c in enumerate(chunks if isinstance(chunks, list) else []):
            tag = f"chunks[{i}]"
            if not isinstance(c, dict) or not c.get("id"):
                E.append(f"{tag}: id missing"); continue
            cid = c["id"]
            if not isinstance(cid, str):
                E.append(f"{tag}: id must be a string ({cid!r})"); continue
            tag = f"chunk '{cid}'"
            dup_c[cid] = dup_c.get(cid, 0) + 1
            if not c.get("title"):
                E.append(f"{tag}: title missing")
            st = c.get("status")
            if not isinstance(st, str) or st not in STATUS:
                E.append(f"{tag}: status '{st}' invalid (allowed {STATUS})")
            mem = c.get("members")
            if mem is not None and not _str_list(mem):
                E.append(f"{tag}: members must be a list of strings ({mem!r})")
            else:
                for mid in (mem or []):
                    if mid not in obs_ids:
                        E.append(f"{tag}: member '{mid}' not in observations[] (dangling — traceability)")
            # empty members (None or []) = no observation to trace to — strong warning when authored (ground_audit --strict blocks it)
            if not (mem or []):
                if st in ("draft", "review", "approved"):
                    W.append(f"{tag}: authored chunk but members is empty (to-be with no traceable observation — ground_audit --strict blocks it)")
                else:
                    W.append(f"{tag}: members is empty (no observation grouped)")
            od = c.get("order")
            if od is not None:
                if not isinstance(od, int) or isinstance(od, bool):
                    E.append(f"{tag}: order must be an integer ({od!r})")
                else:
                    orders.append((od, cid))
            orr = c.get("order_rationale")
            if orr is not None and (not isinstance(orr, str) or not orr.strip()):
                E.append(f"{tag}: order_rationale must be a non-empty string ({orr!r})")
            elif not orr:
                W.append(f"{tag}: order_rationale missing (why this order — without it, it's just a list. ground_audit --strict blocks it)")
            for f in ("asis", "tobe", "issues", "approach"):
                fv = c.get(f)
                if fv is not None and not isinstance(fv, str):
                    E.append(f"{tag}: {f} must be a string ({fv!r})")
            if st in ("draft", "review", "approved") and not c.get("asis"):
                W.append(f"{tag}: status={st} but asis is empty (to-be with no as-is — grounding gate risk)")
            if st in ("draft", "review", "approved") and not c.get("tobe"):
                W.append(f"{tag}: status={st} but tobe is empty (authored chunk with no to-be — incomplete deliverable)")
        for cid, c in dup_c.items():
            if c > 1:
                E.append(f"duplicate chunk id: '{cid}' ×{c}")
        seen_o = {}
        for od, cid in orders:
            seen_o.setdefault(od, []).append(cid)
        for od, cids in seen_o.items():
            if len(cids) > 1:
                W.append(f"duplicate order {od}: {cids} (ambiguous sequence — reassign)")

    return E, W


def _read_manifest(mpath):
    """Load YAML, converting file/syntax errors into a clean exit instead of a traceback."""
    try:
        with open(mpath, encoding="utf-8") as f:
            return yaml.safe_load(f)
    except FileNotFoundError:
        sys.exit(f"[abort] manifest file not found: {mpath}")
    except OSError as e:
        sys.exit(f"[abort] cannot read manifest: {mpath} ({e})")
    except yaml.YAMLError as e:
        sys.exit(f"[abort] manifest YAML syntax error: {mpath}\n  {e}")


def load_validated(mpath, strict=True):
    m = _read_manifest(mpath)
    E, W = validate(m)
    for w in W:
        print(f"  [warn] {w}", file=sys.stderr)
    if E:
        for e in E:
            print(f"  [error] {e}", file=sys.stderr)
        if strict:
            sys.exit(f"[abort] manifest validation failed: {len(E)} error(s)")
    return m


if __name__ == "__main__":
    import argparse
    ap = argparse.ArgumentParser(description="validate the docloop manifest schema")
    ap.add_argument("manifest")
    a = ap.parse_args()
    m = _read_manifest(a.manifest)
    E, W = validate(m)
    for w in W:
        print(f"[warn]  {w}")
    for e in E:
        print(f"[error] {e}")
    print(f"\nvalidation: {len(E)} error(s) / {len(W)} warning(s)")
    sys.exit(1 if E else 0)
