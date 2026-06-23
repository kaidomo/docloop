#!/usr/bin/env python3
"""Validate the docloop manifest schema (called before gap-audit/split). Catches bad values before a crash.
Usage: python3 validate_manifest.py <manifest.yaml>   (prints results when run standalone)
Other scripts: from validate_manifest import load_validated
(schema validation for the docloop manifest)"""
import sys, os, yaml

STATUS = {"draft", "review", "approved", "pending"}
SCORE_AXES = {"completeness", "coherence", "clarity", "depth"}   # 재검토/감사 모드 섹션 scores 키(없으면 통과)


def _pathish(v):
    """경로(str) 또는 경로 목록(list[str])인가 — downstream/sources 값 검증용."""
    return isinstance(v, str) or (isinstance(v, list) and all(isinstance(x, str) for x in v))


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
        if not proj.get("doc_type"):
            W.append("project.doc_type not set (a pm-policy doc_types key is recommended)")
        ds = proj.get("downstream")
        if ds is not None:
            if not isinstance(ds, dict):
                E.append("project.downstream must be a mapping")
            else:
                KNOWN_DS = {"storyboard", "manual_manifest", "policy_docs"}
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
                KNOWN_SRC = {"code_roots", "design", "prototypes"}
                for k, val in src.items():
                    if k not in KNOWN_SRC:
                        W.append(f"project.sources unknown key '{k}' (allowed {KNOWN_SRC} — typo?)")
                    elif not _pathish(val):
                        E.append(f"project.sources.{k} must be a path (str) or path list (list[str]): {val!r}")

    sections = m.get("sections", [])
    if not isinstance(sections, list):
        E.append("sections is not a list"); sections = []
    elif not sections:
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
        # 재검토/감사 모드: optional scores(없으면 통과, 있으면 형식만 검증)
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

    REQUIRED = {"open_questions": "topic", "decisions": "decision"}   # 사람 게이트 리포트용 최소 필드
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

    # 재검토/감사 모드: optional 문서레벨 verbatim(없으면 통과, 있으면 형식만 검증)
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

    # 재검토/감사 모드: optional review_audit 적용추적(없으면 통과·하위호환). pending_apply/applied = decision_id로 decisions[](SSOT) 연결(Codex#5).
    ra = m.get("review_audit")
    if ra is not None:
        if not isinstance(ra, dict):
            E.append("review_audit must be a mapping")
        else:
            # decisions[](권위) id 집합 — decision_id 참조 무결성 검증용(dangling 차단, peer r1#1)
            dec_ids = {d.get("id") for d in (m.get("decisions") or [])
                       if isinstance(d, dict) and isinstance(d.get("id"), str)}
            cross = {}   # decision_id → 등장한 리스트들(pending_apply↔applied 교집합 경고용, peer r1#4)
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
    return E, W


def load_validated(mpath, strict=True):
    with open(mpath, encoding="utf-8") as f:
        m = yaml.safe_load(f)
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
    with open(a.manifest, encoding="utf-8") as f:
        m = yaml.safe_load(f)
    E, W = validate(m)
    for w in W:
        print(f"[warn]  {w}")
    for e in E:
        print(f"[error] {e}")
    print(f"\nvalidation: {len(E)} error(s) / {len(W)} warning(s)")
    sys.exit(1 if E else 0)
