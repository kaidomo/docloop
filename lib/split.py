#!/usr/bin/env python3
"""output 단계: PM 통합 SSOT 본문 → 배포 플랫폼용 페이지(파생물, 재생성).
- 섹션 순서 = pm-policy의 doc_types[doc_type].sections 순(정책 없으면 manifest sections 순).
- '확정된 것만 공유'(pm-policy 원칙): 기본은 status=approved 섹션만 본문화. pending은 본문 없음으로 제외.
  --include-draft 면 draft·review도 포함(내부 프리뷰용). pending은 항상 제외.
- 제목 = pm-policy output.page_pattern 의 {product}/{feature}/{title} 치환(없으면 manifest title).
- 안전 가드(manual-authoring split 패턴 이식): output_dir은 manifest 폴더 바로 아래 전용 하위폴더 +
  생성 마커(rmtree 보호) + realpath/​symlink 검사 + 빈 폴더 입양. 파일명 경로구분자 정제.
사용: python3 split.py <manifest.yaml> [--strict] [--dry-run] [--include-draft]
  --strict : 배포 완전성 게이트 — 포함 섹션에 본문(H1) 없음 / SSOT 중복 H1 → 실패.
             ※ 정책 required 섹션 완전성·gaps·open_questions 릴리스 게이트는 gap_audit.py --strict 담당.
                (정책상 required는 소명=deferred로 생략 가능하므로 split은 required 미승인을 경고만 한다.)
  --dry-run: 삭제/쓰기 없이 계획만 출력
주의: 배포 플랫폼(Confluence/Notion/Wiki 등)으로의 반입은 사람이 한다 — 이미지·상대경로 주의."""
import sys, os, re, shutil, argparse
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from validate_manifest import load_validated

try:
    import yaml
except ImportError:
    yaml = None

MARKER = ".pm_authoring_output"   # 스킬이 생성한 폴더 표식(rmtree 안전용)


def safe_filename(name):
    """파일명 성분에서 경로 구분자·상위참조 제거(outputs/ 경계 밖 쓰기 방지)."""
    s = str(name).replace("/", "_").replace("\\", "_").replace("\x00", "").strip()
    s = s.lstrip(".")
    return s or "untitled"


def split_h1(md):
    """H1(# ) 단위 (제목, 블록) 리스트. 코드펜스(``` ~~~) 안의 #는 무시."""
    blocks, cur_title, cur, fence = [], None, [], False
    for line in md.splitlines(keepends=True):
        st = line.lstrip()
        if st.startswith("```") or st.startswith("~~~"):
            fence = not fence
        if not fence and re.match(r" {0,3}#\s+\S", line):   # ATX: 들여쓰기 0~3칸만(4칸+ = 코드 블록)
            if cur_title is not None or cur:
                blocks.append((cur_title, "".join(cur)))
            title = re.sub(r"\s+#+\s*$", "", line.strip().lstrip("#").strip())  # 닫는 '#'는 앞 공백 있을 때만(‘C#’ 보존)
            cur_title, cur = title, [line]
        else:
            cur.append(line)
    if cur_title is not None or cur:
        blocks.append((cur_title, "".join(cur)))
    return blocks


def load_policy(proj, base):
    """manifest.project.policy 경로의 pm-policy.yaml 로드(없으면 None)."""
    pol_path = proj.get("policy")
    if not pol_path or yaml is None:
        return None
    p = pol_path if os.path.isabs(pol_path) else os.path.join(base, pol_path)
    if not os.path.exists(p):
        return None
    with open(p, encoding="utf-8") as f:
        return yaml.safe_load(f)


def page_title(proj, policy):
    """output.page_pattern 치환 → 배포 페이지 제목(없으면 manifest title/product)."""
    pat = None
    if policy:
        pat = (policy.get("output", {}) or {}).get("page_pattern")
    title = proj.get("title") or proj.get("product") or "PM 문서"
    if not pat:
        return title
    product = proj.get("product") or (policy or {}).get("org", {}).get("product_default", "")
    feature = proj.get("feature") or proj.get("title") or ""
    out = pat.replace("{product}", str(product)).replace("{feature}", str(feature)).replace("{title}", str(title))
    out = out.strip()
    return out or title


def main():
    ap = argparse.ArgumentParser(description="PM SSOT → 배포 페이지 분할")
    ap.add_argument("manifest")
    ap.add_argument("--strict", action="store_true")
    ap.add_argument("--dry-run", dest="dry", action="store_true")
    ap.add_argument("--include-draft", dest="include_draft", action="store_true",
                    help="draft·review 섹션도 포함(기본은 approved만)")
    a = ap.parse_args()

    m = load_validated(a.manifest)
    base = os.path.dirname(os.path.abspath(a.manifest))
    proj = m["project"]
    policy = load_policy(proj, base)

    ssot_path = os.path.join(base, proj["ssot"])
    if not os.path.exists(ssot_path):
        sys.exit(f"[중단] SSOT 본문 없음: {ssot_path}")
    body = open(ssot_path, encoding="utf-8").read()
    blocks, dup_h1 = {}, []            # 제목→블록 (중복 H1은 앞 블록 유지 + 기록)
    for t, b in split_h1(body):
        if not t:
            continue
        t = t.strip()
        if t in blocks:
            dup_h1.append(t)           # 조용한 덮어쓰기 방지 — 경고/strict로 노출
        else:
            blocks[t] = b

    od = proj.get("output_dir")
    if not od:
        sys.exit("[중단] project.output_dir 미지정 — 작업폴더 표준은 'outputs'. manifest에 명시하세요.")
    out_dir = os.path.join(base, od)

    # 섹션 순서: 정책 doc_types[doc_type].sections → 없으면 manifest sections 순
    man_secs = {s["id"]: s for s in m.get("sections", []) or []}
    pol_secs = []
    if policy and proj.get("doc_type"):
        dt = (policy.get("doc_types", {}) or {}).get(proj["doc_type"])
        if dt:
            pol_secs = dt.get("sections", []) or []
    if pol_secs:
        order = [(p["id"], p.get("title", ""), bool(p.get("required"))) for p in pol_secs]
        # manifest에만 있고 정책에 없는 섹션은 뒤에 덧붙임(누락 방지)
        for sid, s in man_secs.items():
            if sid not in {p["id"] for p in pol_secs}:
                order.append((sid, s.get("title", ""), False))
    else:
        order = [(s["id"], s.get("title", ""), False) for s in m.get("sections", []) or []]

    include = {"approved"} | ({"draft", "review"} if a.include_draft else set())
    pages, excluded, no_body, req_unmet = [], [], [], []
    for sid, pol_title, required in order:
        s = man_secs.get(sid)
        st = s.get("status") if s else "(미정의)"
        title = (s.get("title") if s else None) or pol_title or sid
        # 릴리스 게이트용: 정책 required 섹션이 approved 아님
        if required and st != "approved":
            req_unmet.append((sid, title, st))
        if st not in include:
            excluded.append((sid, title, st))
            continue
        blk = blocks.get(title)
        if blk is None:   # 제목 정확매칭 실패 → 공백 정규화 재시도
            for bt, bb in blocks.items():
                if bt.strip() == title.strip():
                    blk = bb; break
        if blk is None:
            no_body.append((sid, title)); continue
        pages.append((sid, title, blk))

    # 본문에만 있고 manifest에 없는 H1(고아 섹션)
    man_titles = {(man_secs[sid].get("title") or "") for sid in man_secs}
    orphan = [t for t in blocks if t not in man_titles and t != page_title(proj, policy)]

    ptitle = page_title(proj, policy)
    out_name = f"{safe_filename(ptitle)}.md"

    # 경고(항상). strict 실패는 '배포 완전성'(보내는 본문)만 — required 완전성 게이트는 gap_audit 담당(역할 분리).
    warns = []
    if excluded:
        warns.append("제외(미확정/pending): " + ", ".join(f"{i}({st})" for i, _, st in excluded))
    if no_body:
        warns.append("본문 없음(포함 대상인데 SSOT에 H1 없음): " + ", ".join(i for i, _ in no_body))
    if dup_h1:
        warns.append("SSOT 중복 H1(앞 블록 유지·뒤 무시): " + ", ".join(sorted(set(dup_h1))))
    if orphan:
        warns.append("고아 H1(manifest 섹션 아님): " + ", ".join(orphan))
    if req_unmet:   # 항상 경고(참고). 차단은 gap_audit.py --strict — 정책상 required는 소명(deferred)으로 생략 가능
        warns.append("정책 required 미승인(참고 — 완전성 게이트는 gap_audit): " + ", ".join(f"{i}({st})" for i, _, st in req_unmet))
    if re.search(r"\{[^}]+\}", ptitle):
        warns.append(f"page_pattern 미치환 토큰 잔존: '{ptitle}' (pm-policy output.page_pattern 확인)")
    for w in warns:
        print(f"  ⚠ {w}")
    # strict 실패: 배포 완전성 — 포함 섹션 본문 없음 / SSOT 중복 H1(조용한 유실)
    if a.strict and (no_body or dup_h1):
        fails = []
        if no_body:
            fails.append(f"본문 없음 {len(no_body)}")
        if dup_h1:
            fails.append(f"중복 H1 {len(set(dup_h1))}")
        sys.exit(f"[중단] --strict 배포 완전성 실패: {' + '.join(fails)} (required 완전성은 gap_audit.py --strict로)")

    if not pages:
        print("  (포함할 확정 섹션 없음 — approved 섹션이 없거나 본문 미작성)")

    if a.dry:
        print("=== DRY-RUN (쓰기/삭제 없음) ===")
        print(f"배포 페이지: {out_name}")
        print(f"포함 섹션 {len(pages)}개: " + ", ".join(i for i, _, _ in pages))
        if excluded:
            print(f"제외 {len(excluded)}개: " + ", ".join(i for i, _, _ in excluded))
        return

    # 쓰기 경로: rmtree 안전 가드(바로 아래 전용 폴더 + 생성 마커, realpath 기준)
    abs_out, abs_base = os.path.realpath(out_dir), os.path.realpath(base)   # output_dir 존재는 위에서 확인
    if (os.path.dirname(abs_out) != abs_base
            or os.path.basename(abs_out) in ("", ".", "..")):
        sys.exit(f"[중단] output_dir 안전검사 실패: '{out_dir}' — manifest 폴더 바로 아래 전용 하위폴더여야 함(symlink는 실경로로 검사).")
    if os.path.islink(out_dir):
        sys.exit(f"[중단] output_dir '{out_dir}'가 symlink — 경계 보장 불가로 거부(실폴더로 두세요).")
    if os.path.isdir(abs_out):
        if os.path.exists(os.path.join(abs_out, MARKER)):
            shutil.rmtree(abs_out)
        elif os.listdir(abs_out):
            sys.exit(f"[중단] '{out_dir}'에 생성 마커({MARKER}) 없고 비어있지 않음(숨김파일 포함 — 예: .DS_Store) — "
                     f"스킬이 만든 폴더가 아닐 수 있어 삭제 거부. 폴더를 비우거나 다른 빈 폴더를 output_dir로 지정하세요.")
        # else: 빈 폴더(init_workspace가 만든 outputs/) → 입양
    os.makedirs(abs_out, exist_ok=True)
    open(os.path.join(abs_out, MARKER), "w").close()

    parts = [f"<!-- 배포본(파생물): pm-authoring split.py 재생성. 편집은 SSOT에서. -->"]
    for _sid, _title, blk in pages:
        parts.append(blk.rstrip() + "\n")
    with open(os.path.join(out_dir, out_name), "w", encoding="utf-8") as f:
        f.write("\n".join(parts).rstrip() + "\n")

    print(f"배포 산출: {os.path.join(out_dir, out_name)}  (섹션 {len(pages)}개"
          + (f", 제외 {len(excluded)}" if excluded else "") + ")")
    if policy and (policy.get("output", {}) or {}).get("approval_brief"):
        print("  ℹ️ pm-policy output.approval_brief=true — 승인용 추출본은 approval_brief.py(별도)에서 생성")


if __name__ == "__main__":
    main()
