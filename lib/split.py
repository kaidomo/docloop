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
        sys.exit("[abort] project.output_dir not set — the work-folder standard is 'outputs'. Specify it in the manifest.")
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
        st = s.get("status") if s else "(undefined)"
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
        warns.append("excluded (unconfirmed/pending): " + ", ".join(f"{i}({st})" for i, _, st in excluded))
    if no_body:
        warns.append("no body (included but no H1 in SSOT): " + ", ".join(i for i, _ in no_body))
    if dup_h1:
        warns.append("duplicate H1 in SSOT (first block kept, rest ignored): " + ", ".join(sorted(set(dup_h1))))
    if orphan:
        warns.append("orphan H1 (not a manifest section): " + ", ".join(orphan))
    if req_unmet:   # 항상 경고(참고). 차단은 gap_audit.py --strict — 정책상 required는 소명(deferred)으로 생략 가능
        warns.append("policy required not approved (FYI — completeness gate is gap_audit): " + ", ".join(f"{i}({st})" for i, _, st in req_unmet))
    if re.search(r"\{[^}]+\}", ptitle):
        warns.append(f"unsubstituted page_pattern token left: '{ptitle}' (check pm-policy output.page_pattern)")
    for w in warns:
        print(f"  ⚠ {w}")
    # strict 실패: 배포 완전성 — 포함 섹션 본문 없음 / SSOT 중복 H1(조용한 유실)
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

    # 쓰기 경로: rmtree 안전 가드(바로 아래 전용 폴더 + 생성 마커, realpath 기준)
    abs_out, abs_base = os.path.realpath(out_dir), os.path.realpath(base)   # output_dir 존재는 위에서 확인
    if (os.path.dirname(abs_out) != abs_base
            or os.path.basename(abs_out) in ("", ".", "..")):
        sys.exit(f"[abort] output_dir safety check failed: '{out_dir}' — must be a dedicated subfolder directly under the manifest folder (symlinks checked by real path).")
    if os.path.islink(out_dir):
        sys.exit(f"[abort] output_dir '{out_dir}' is a symlink — rejected (boundary can't be guaranteed; use a real folder).")
    if os.path.isdir(abs_out):
        if os.path.exists(os.path.join(abs_out, MARKER)):
            shutil.rmtree(abs_out)
        elif os.listdir(abs_out):
            sys.exit(f"[abort] '{out_dir}' has no generation marker ({MARKER}) and is not empty (including hidden files — e.g. .DS_Store) — "
                     f"may not be a folder this tool created, so deletion is refused. Empty the folder or point output_dir at another empty folder.")
        # else: 빈 폴더(init_workspace가 만든 outputs/) → 입양
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
