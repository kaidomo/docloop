#!/usr/bin/env python3
"""pm-authoring manifest 스키마 검증 (gap-audit/split 전에 호출). 잘못된 값을 crash 전에 잡는다.
사용: python3 validate_manifest.py <manifest.yaml>   (단독 실행 시 결과 출력)
다른 스크립트: from validate_manifest import load_validated
(manual-authoring/scripts/validate_manifest.py 패턴 이식 — pm manifest 스키마용)"""
import sys, os, yaml

STATUS = {"draft", "review", "approved", "pending"}
SCORE_AXES = {"completeness", "coherence", "clarity", "depth"}   # 재검토/감사 모드 섹션 scores 키(없으면 통과)


def _pathish(v):
    """경로(str) 또는 경로 목록(list[str])인가 — downstream/sources 값 검증용."""
    return isinstance(v, str) or (isinstance(v, list) and all(isinstance(x, str) for x in v))


def validate(m):
    """(errors, warnings) 반환. errors가 있으면 실행 중단해야 함."""
    E, W = [], []
    if not isinstance(m, dict):
        return ["manifest 최상위가 매핑이 아님"], []

    proj = m.get("project")
    if not isinstance(proj, dict):
        E.append("project 블록 누락/형식오류")
    else:
        for k in ("product", "ssot"):
            if not proj.get(k):
                E.append(f"project.{k} 누락")
        if not proj.get("doc_type"):
            W.append("project.doc_type 미지정(pm-policy doc_types 키 권장)")
        ds = proj.get("downstream")
        if ds is not None:
            if not isinstance(ds, dict):
                E.append("project.downstream은 매핑이어야 함")
            else:
                KNOWN_DS = {"storyboard", "manual_manifest", "policy_docs"}
                for k, val in ds.items():
                    if k not in KNOWN_DS:
                        W.append(f"project.downstream 미지정 키 '{k}'(허용 {KNOWN_DS} — 오타?)")
                    elif not _pathish(val):
                        E.append(f"project.downstream.{k}는 경로(str) 또는 경로목록(list[str])이어야 함: {val!r}")
        src = proj.get("sources")
        if src is not None:
            if not isinstance(src, dict):
                E.append("project.sources는 매핑이어야 함")
            else:
                KNOWN_SRC = {"code_roots", "design", "prototypes"}
                for k, val in src.items():
                    if k not in KNOWN_SRC:
                        W.append(f"project.sources 미지정 키 '{k}'(허용 {KNOWN_SRC} — 오타?)")
                    elif not _pathish(val):
                        E.append(f"project.sources.{k}는 경로(str) 또는 경로목록(list[str])이어야 함: {val!r}")

    sections = m.get("sections", [])
    if not isinstance(sections, list):
        E.append("sections가 리스트가 아님"); sections = []
    elif not sections:
        W.append("sections 비어있음(섹션 골격 없음 — 0단계 초안 생성 필요?)")
    ids = {}
    for idx, s in enumerate(sections):
        tag = f"sections[{idx}]"
        if not isinstance(s, dict) or not s.get("id"):
            E.append(f"{tag}: id 누락"); continue
        sid = s["id"]
        if not isinstance(sid, str):
            E.append(f"{tag}: id는 문자열이어야 함 ({sid!r})"); continue
        tag = f"section '{sid}'"
        ids[sid] = ids.get(sid, 0) + 1
        if not s.get("title"):
            E.append(f"{tag}: title 누락")
        st = s.get("status")
        if not isinstance(st, str) or st not in STATUS:
            E.append(f"{tag}: status '{st}' 무효(허용 {STATUS})")
        for listk in ("sources", "gaps"):
            v = s.get(listk)
            if v is None:
                continue
            if not isinstance(v, list):
                E.append(f"{tag}: {listk}는 리스트여야 함"); continue
            for j, item in enumerate(v):
                if not isinstance(item, str) or not item.strip():
                    E.append(f"{tag}: {listk}[{j}]는 비어있지 않은 문자열이어야 함 ({item!r})")
        if st == "approved" and not s.get("sources"):
            W.append(f"{tag}: status=approved인데 sources 비어있음(근거 없는 확정?)")
        # 재검토/감사 모드: optional scores(없으면 통과, 있으면 형식만 검증)
        sc = s.get("scores")
        if sc is not None:
            if not isinstance(sc, dict):
                E.append(f"{tag}: scores는 매핑이어야 함")
            else:
                for ax, v in sc.items():
                    if ax not in SCORE_AXES:
                        W.append(f"{tag}: scores 미지정 축 '{ax}'(허용 {SCORE_AXES} — 오타?)")
                    elif not isinstance(v, int) or isinstance(v, bool):
                        E.append(f"{tag}: scores.{ax}는 정수여야 함 ({v!r})")
    for sid, c in ids.items():
        if c > 1:
            E.append(f"section id 중복: '{sid}' ×{c}")

    REQUIRED = {"open_questions": "topic", "decisions": "decision"}   # 사람 게이트 리포트용 최소 필드
    OQ_STATUS = {"open", "resolved", "deferred"}
    for listk in ("open_questions", "decisions"):
        v = m.get(listk, [])
        if v in (None, []):
            continue
        if not isinstance(v, list):
            E.append(f"{listk}가 리스트가 아님"); continue
        seen = set()
        for i, item in enumerate(v):
            if not isinstance(item, dict) or not item.get("id"):
                E.append(f"{listk}[{i}]: id 누락"); continue
            iid = item["id"]
            if not isinstance(iid, str):
                E.append(f"{listk}[{i}]: id는 문자열이어야 함 ({iid!r})"); continue
            if iid in seen:
                E.append(f"{listk}: id 중복 '{iid}'")
            seen.add(iid)
            req = REQUIRED[listk]
            if not item.get(req):
                E.append(f"{listk} '{iid}': 필수 필드 '{req}' 누락")
            if listk == "open_questions":
                stq = item.get("status", "open")
                if not isinstance(stq, str) or stq not in OQ_STATUS:
                    E.append(f"open_questions '{iid}': status '{stq}' 무효(허용 {OQ_STATUS})")
                if not item.get("owner"):
                    W.append(f"open_questions '{iid}': owner 권장(게이트 담당 불명)")

    # 재검토/감사 모드: optional 문서레벨 verbatim(없으면 통과, 있으면 형식만 검증)
    vb = m.get("verbatim")
    if vb is not None:
        if not isinstance(vb, list):
            E.append("verbatim는 리스트여야 함")
        else:
            for i, item in enumerate(vb):
                tag = f"verbatim[{i}]"
                if not isinstance(item, dict) or not item.get("source"):
                    E.append(f"{tag}: source 누락(원문 경로)"); continue
                if not isinstance(item["source"], str):
                    E.append(f"{tag}: source는 문자열(경로)이어야 함 ({item['source']!r})")
                sha = item.get("sha256")
                if sha is not None and not isinstance(sha, str):
                    E.append(f"{tag}: sha256는 문자열이어야 함 ({sha!r})")
                qs = item.get("quotes")
                if qs is not None:
                    if not isinstance(qs, list):
                        E.append(f"{tag}: quotes는 리스트여야 함")
                    else:
                        for j, q in enumerate(qs):
                            if not isinstance(q, str) or not q.strip():
                                E.append(f"{tag}: quotes[{j}]는 비어있지 않은 문자열이어야 함 ({q!r})")

    # 재검토/감사 모드: optional review_audit 적용추적(없으면 통과·하위호환). pending_apply/applied = decision_id로 decisions[](SSOT) 연결(Codex#5).
    ra = m.get("review_audit")
    if ra is not None:
        if not isinstance(ra, dict):
            E.append("review_audit는 매핑이어야 함")
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
                    E.append(f"review_audit.{listk}는 리스트여야 함"); continue
                seen = set()
                for i, item in enumerate(v):
                    tag = f"review_audit.{listk}[{i}]"
                    if not isinstance(item, dict):
                        E.append(f"{tag}: 매핑이어야 함"); continue
                    did = item.get("decision_id")
                    if not isinstance(did, str) or not did.strip():
                        E.append(f"{tag}: decision_id 필수(비어있지 않은 문자열 — decisions[]와 연결, 추적성)"); continue
                    if did not in dec_ids:
                        E.append(f"{tag}: decision_id '{did}'가 decisions[]에 없음(dangling — 추적성)")
                    if did in seen:
                        E.append(f"review_audit.{listk}: decision_id 중복 '{did}'")
                    seen.add(did)
                    cross.setdefault(did, set()).add(listk)
            for did, lists in cross.items():
                if len(lists) > 1:
                    W.append(f"review_audit: decision_id '{did}' pending_apply·applied 동시 존재(전이 중? 적용완료면 pending_apply에서 제거)")
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
            sys.exit(f"[중단] manifest 검증 실패: {len(E)}건")
    return m


if __name__ == "__main__":
    import argparse
    ap = argparse.ArgumentParser(description="pm-authoring manifest 스키마 검증")
    ap.add_argument("manifest")
    a = ap.parse_args()
    with open(a.manifest, encoding="utf-8") as f:
        m = yaml.safe_load(f)
    E, W = validate(m)
    for w in W:
        print(f"[warn]  {w}")
    for e in E:
        print(f"[error] {e}")
    print(f"\n검증: 오류 {len(E)} / 경고 {len(W)}")
    sys.exit(1 if E else 0)
