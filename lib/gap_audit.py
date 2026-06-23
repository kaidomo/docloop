#!/usr/bin/env python3
"""gap-audit (pm-authoring의 킬러 기능): manifest의 gaps(근거↔본문·PRD↔다운스트림 불일치 D)·
open_questions(미해결 의사결정 E)·pending 섹션을 개발/기획팀 공유용 마크다운 리포트로 출력.
내부 비공개 문서(배포본 미포함).
사용: python3 gap_audit.py <manifest.yaml> [--out report.md] [--strict]
  --strict: gaps · open(미해결) open_questions · pending 섹션 · review_audit.pending_apply(미반영 적용)가
           있으면 exit 1(릴리스 게이트). open_questions status=resolved·deferred는 통과로 본다.
(manual-authoring/scripts/audit_report.py 패턴 이식 — pm manifest 스키마용)

주의: 이 스크립트는 **리포트 스캐폴딩**이다. gaps·open_questions를 *채우는* 실제 대조 사고
(기획서 ↔ 코드·정책·디자인·프로토타입)는 SKILL.md의 fan-out 레시피(서브에이전트)가 한다."""
import sys, os, argparse
from datetime import datetime, timezone, timedelta
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from validate_manifest import load_validated

KST = timezone(timedelta(hours=9))


def esc(s):
    """마크다운 표 cell 안전화: None→빈칸, |·줄바꿈·역슬래시 깨짐 방지."""
    if s is None:
        return ""
    return str(s).replace("\\", "\\\\").replace("|", "\\|").replace("\r", " ").replace("\n", " ").strip()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("manifest")
    ap.add_argument("--out", default="")
    ap.add_argument("--strict", action="store_true")
    a = ap.parse_args()
    m = load_validated(a.manifest)
    base = os.path.dirname(os.path.abspath(a.manifest))
    out = a.out or os.path.join(base, "_gap_report.md")

    gaps, pend, status_count = [], [], {}
    for s in m.get("sections", []) or []:
        sid = s["id"]; st = s.get("status", "?")
        status_count[st] = status_count.get(st, 0) + 1
        for g in s.get("gaps", []) or []:
            gaps.append((sid, s.get("title", ""), g))
        if st == "pending":
            pend.append((sid, s.get("title", "")))
    oq = m.get("open_questions", []) or []
    open_oq = [q for q in oq if q.get("status", "open") == "open"]   # open만 게이트 실패(resolved·deferred는 통과)
    decisions = m.get("decisions", []) or []
    # 재검토/감사 모드 적용추적(백포트): pending_apply = 구두확정·SSOT 미반영 → 비어야 통과(false-pass 차단, Codex#2/#8)
    # malformed shape는 load_validated(strict)가 이미 차단하지만, 게이트 자기완결성 위해 방어(peer r1#2)
    ra = m.get("review_audit")
    ra = ra if isinstance(ra, dict) else {}
    pa = ra.get("pending_apply")
    pending_apply = pa if isinstance(pa, list) else []

    title = m["project"].get("title") or m["project"].get("product", "PM 문서")
    gen_at = datetime.now(KST).strftime("%Y-%m-%d %H:%M KST")
    L = [f"# {esc(title)} — gap 감사 리포트 (내부 비공개)", "",
         f"> 자동 생성: `gap_audit.py` · 생성: **{gen_at}** · 근거=SSOT 기준. 배포본 미포함.", "",
         f"- 불일치(gaps): **{len(gaps)}건**  ·  open_questions: **{len(oq)}건**(open {len(open_oq)})  ·  pending 섹션: **{len(pend)}개**  ·  미반영 적용(pending_apply): **{len(pending_apply)}건**",
         "- 섹션 상태: " + (", ".join(f"{k} {v}" for k, v in sorted(status_count.items())) or "_없음_"), ""]

    L += ["## 1. 불일치 (D — 근거↔본문 또는 PRD↔다운스트림)", ""]
    if gaps:
        L += ["| 섹션 | 제목 | 불일치 |", "|------|------|------|"]
        L += [f"| {esc(i)} | {esc(t)} | {esc(g)} |" for i, t, g in gaps]
    else:
        L.append("_없음_")

    L += ["", "## 2. 미해결 의사결정 (E — open_questions)", ""]
    if oq:
        L += ["| ID | 주제 | 사유 | 담당 | 상태 |", "|------|------|------|------|------|"]
        L += [f"| {esc(q.get('id',''))} | {esc(q.get('topic',''))} | {esc(q.get('reason',''))} | {esc(q.get('owner',''))} | {esc(q.get('status','open'))} |" for q in oq]
    else:
        L.append("_없음_")

    L += ["", "## 3. pending 섹션 (근거 미확보 — 본문화 보류)", ""]
    if pend:
        L += ["| 섹션 | 제목 |", "|------|------|"]
        L += [f"| {esc(i)} | {esc(t)} |" for i, t in pend]
    else:
        L.append("_없음_")

    L += ["", "## 4. 확정 결정 로그 (추적성)", ""]
    if decisions:
        L += ["| ID | 일자 | 결정 | 결정자 |", "|------|------|------|------|"]
        L += [f"| {esc(d.get('id',''))} | {esc(d.get('date',''))} | {esc(d.get('decision',''))} | {esc(d.get('by',''))} |" for d in decisions]
    else:
        L.append("_없음_")

    L += ["", "## 5. 미반영 적용 (pending_apply — 구두확정·SSOT 미반영, 비어야 릴리스 통과)", ""]
    if pending_apply:
        L += ["| decision_id | 문서 | 비고 |", "|------|------|------|"]
        L += [f"| {esc(p.get('decision_id',''))} | {esc(p.get('doc',''))} | {esc(p.get('note',''))} |" for p in pending_apply]
    else:
        L.append("_없음_")
    L.append("")

    with open(out, "w", encoding="utf-8") as f:
        f.write("\n".join(L))
    print(f"gap report: {out}  (gaps {len(gaps)} / open_questions {len(oq)} / pending {len(pend)} / pending_apply {len(pending_apply)})")

    if a.strict:
        fails = []
        if gaps:
            fails.append(f"gaps {len(gaps)}")
        if open_oq:
            fails.append(f"open_questions(open) {len(open_oq)}")
        if pend:
            fails.append(f"pending 섹션 {len(pend)}")
        if pending_apply:
            fails.append(f"pending_apply(미반영) {len(pending_apply)}")
        if fails:
            sys.exit("[릴리스 게이트 실패] " + " + ".join(fails))


if __name__ == "__main__":
    main()
