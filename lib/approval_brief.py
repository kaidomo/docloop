#!/usr/bin/env python3
"""승인/전달용 요약 추출 (pm-policy output.approval_brief). 승인 라인·리뷰어가 볼 한 장:
목적/목표 · 범위/비범위 · 미해결 의사결정(open_questions) · 결정 로그(decisions) · 섹션 상태.
※ 이 스킬은 승인·서명을 '검증'하지 않는다(pm-policy 참고) — 이건 전달/리뷰용 산출물일 뿐.
내부 비공개(배포본 미포함) — gap_audit.py 와 같은 reports/ 계열.
사용: python3 approval_brief.py <manifest.yaml> [--out report.md] [--include-resolved]
(manual-authoring/scripts/audit_report.py · gap_audit.py 패턴 — pm manifest 스키마용)"""
import sys, os, re, argparse
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from validate_manifest import load_validated
from split import split_h1   # SSOT 본문 H1 분할(목적/범위 본문 추출용)

# 목적/목표·범위 섹션 식별(doc_type 무관 휴리스틱: id 또는 title 키워드)
GOAL_IDS = {"purpose", "goals", "goal", "overview", "background", "problem", "objective"}
GOAL_KW = ["목적", "목표", "배경", "문제"]
SCOPE_IDS = {"scope", "oos", "out-of-scope", "outofscope"}
SCOPE_KW = ["범위", "scope", "out of scope"]


def esc(s):
    """마크다운 표 cell 안전화."""
    if s is None:
        return ""
    return str(s).replace("\\", "\\\\").replace("|", "\\|").replace("\r", " ").replace("\n", " ").strip()


def _strip_h1(blk):
    """블록 선두의 자체 H1(# title) 한 줄 제거 — approval brief가 ### 제목을 따로 붙이므로 중복 방지.
    split_h1과 동일한 ATX 기준(0~3칸 들여쓰기 + '# ' + 내용)일 때만 제거(소제목/코드 오제거 방지)."""
    if not blk:
        return ""
    lines = blk.splitlines(keepends=True)
    if lines and re.match(r" {0,3}#\s+\S", lines[0]):
        lines = lines[1:]
    return "".join(lines).strip("\n")


def _norm(t):
    """제목 매칭 정규화: 선행 번호(`1.`·`2)`)·공백 제거 + 소문자 — SSOT H1 변형 흡수."""
    return re.sub(r"^\s*\d+[.)]\s*", "", str(t)).replace(" ", "").lower()


def _match(sec, ids, kws):
    """섹션이 목적/범위류인가 — id 정확매칭 또는 title이 키워드로 *시작*(중간 등장 오탐 방지)."""
    sid = (sec.get("id") or "").lower()
    t = (sec.get("title") or "").strip().lower()
    return sid in ids or any(t.startswith(k) for k in kws)


def _bodies(secs, nblocks, ids, kws, exclude=None):
    """매칭 섹션들의 (id, title, status, 본문블록) 추출. 본문은 SSOT H1(정규화) 블록에서 찾음.
    exclude: 이미 다른 분류(목적)에 쓰인 섹션 id — 중복 노출 방지."""
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
    ap = argparse.ArgumentParser(description="승인/전달용 요약 추출")
    ap.add_argument("manifest")
    ap.add_argument("--out", default="")
    ap.add_argument("--include-resolved", action="store_true", help="resolved open_questions도 표기")
    a = ap.parse_args()

    m = load_validated(a.manifest)
    base = os.path.dirname(os.path.abspath(a.manifest))
    proj = m["project"]
    out = a.out or os.path.join(base, "reports", "_approval_brief.md")   # reports/ 계열(작업폴더 표준)

    # SSOT 본문(있으면 목적/범위 본문 채움). 키는 정규화 제목(공백·번호 변형 흡수)
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

    title = proj.get("title") or proj.get("product", "PM 문서")
    L = [f"# {esc(title)} — 승인/전달용 요약 (review brief)", "",
         "> 자동 생성: `approval_brief.py` · **전달/리뷰용**(배포본 미포함). "
         "이 스킬은 승인·서명을 검증하지 않음(pm-policy 참고).", "",
         f"- 섹션: " + (", ".join(f"{k} {v}" for k, v in sorted(status_count.items())) or "_없음_"),
         f"- open_questions: open **{len(open_oq)}** · deferred {len(deferred)} · resolved {len(resolved)}  ·  decisions **{len(decisions)}**", ""]

    # 1. 목적/목표
    L += ["## 1. 목적 / 목표", ""]
    goals = _bodies(secs, nblocks, GOAL_IDS, GOAL_KW)
    if goals:
        for _id, t, st, blk in goals:
            L.append(f"### {esc(t)}  ·  _{esc(st)}_")
            L.append(_strip_h1(blk) if blk else "_(본문 없음 — SSOT에 H1 미작성)_")
            L.append("")
    else:
        L += ["_목적/목표 섹션을 찾지 못함(섹션 id/title 확인)._", ""]

    # 2. 범위/비범위 (목적에 이미 잡힌 섹션은 제외 — 중복 노출 방지)
    L += ["## 2. 범위 / 비범위", ""]
    scope = _bodies(secs, nblocks, SCOPE_IDS, SCOPE_KW, exclude={g[0] for g in goals})
    if scope:
        for _id, t, st, blk in scope:
            L.append(f"### {esc(t)}  ·  _{esc(st)}_")
            L.append(_strip_h1(blk) if blk else "_(본문 없음)_")
            L.append("")
    else:
        L += ["_범위 섹션을 찾지 못함._", ""]

    # 3. 미해결 의사결정
    L += ["## 3. 미해결 의사결정 (open_questions)", ""]
    show_oq = open_oq + deferred + (resolved if a.include_resolved else [])
    if show_oq:
        L += ["| id | 주제 | 담당 | 상태 | 사유/비고 |", "|------|------|------|------|------|"]
        for q in show_oq:
            note = q.get("reason") or q.get("note") or ""
            if q.get("reason") and q.get("note"):
                note = f"{q['reason']} / {q['note']}"
            L.append(f"| {esc(q.get('id'))} | {esc(q.get('topic'))} | {esc(q.get('owner'))} | {esc(q.get('status', 'open'))} | {esc(note)} |")
    else:
        L.append("_미해결 없음._")

    # 4. 결정 로그
    L += ["", "## 4. 결정 로그 (decisions)", ""]
    if decisions:
        L += ["| id | 일자 | 결정 | 결정자 |", "|------|------|------|------|"]
        for d in decisions:
            L.append(f"| {esc(d.get('id'))} | {esc(d.get('date'))} | {esc(d.get('decision'))} | {esc(d.get('by'))} |")
    else:
        L.append("_기록된 결정 없음._")

    # 5. 섹션 상태
    L += ["", "## 5. 섹션 상태", ""]
    if secs:
        L += ["| id | 제목 | 상태 |", "|------|------|------|"]
        for s in secs:
            L.append(f"| {esc(s.get('id'))} | {esc(s.get('title'))} | {esc(s.get('status'))} |")
    else:
        L.append("_섹션 없음._")

    os.makedirs(os.path.dirname(os.path.abspath(out)), exist_ok=True)
    with open(out, "w", encoding="utf-8") as f:
        f.write("\n".join(L).rstrip() + "\n")
    print(f"approval brief: {out}  (open_q {len(open_oq)} / decisions {len(decisions)} / 섹션 {len(secs)})")


if __name__ == "__main__":
    main()
