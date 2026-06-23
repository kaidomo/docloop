#!/usr/bin/env python3
"""채점 리포트 (재검토/감사 모드 ①②⑦): manifest 섹션의 선택적 `scores`(4축)와
pm-policy `review_audit`(척도·pass_threshold·priority_rubric)를 읽어 섹션별 점수표 +
우선순위 가중 정렬 + 임계 미달 표시를 `reports/_review_audit.md`로 출력.

판정 라벨(점수)은 **검증자(사람/검증 에이전트)**가 manifest에 채워 넣는다 — 이 스크립트는
그걸 집계·정렬·게이트하는 스캐폴딩이다(생성 에이전트가 자기 점수 매기지 않음 — 하드룰).

사용: python3 score_report.py <manifest.yaml> [--out report.md] [--strict]
  --strict: pass_threshold 미달 축이 있는 섹션이 하나라도 있으면 exit 1.
(gap_audit.py 패턴 이식 — load_validated·esc·KST·--strict 게이트.)

강제 모델: 점수 임계(pass_threshold)는 **기계 차단**(--strict). 축 채점 자체는 fan-out/사람."""
import sys, os, argparse
from datetime import datetime, timezone, timedelta
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from validate_manifest import load_validated

KST = timezone(timedelta(hours=9))

DEFAULT_AXES = ["completeness", "coherence", "clarity", "depth"]


def esc(s):
    """마크다운 표 cell 안전화: None→빈칸, |·줄바꿈·역슬래시 깨짐 방지."""
    if s is None:
        return ""
    return str(s).replace("\\", "\\\\").replace("|", "\\|").replace("\r", " ").replace("\n", " ").strip()


def _load_policy(proj, base):
    """manifest.project.policy 의 pm-policy.yaml 로드(없으면 None)."""
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


def main():
    ap = argparse.ArgumentParser(description="재검토/감사 모드 채점 리포트")
    ap.add_argument("manifest")
    ap.add_argument("--out", default="")
    ap.add_argument("--strict", action="store_true")
    a = ap.parse_args()
    m = load_validated(a.manifest)
    base = os.path.dirname(os.path.abspath(a.manifest))
    proj = m["project"]
    out = a.out or os.path.join(base, "reports", "_review_audit.md")

    pol = _load_policy(proj, base)
    ra = (pol or {}).get("review_audit") or {}
    scoring = ra.get("scoring") or {}
    axes = scoring.get("primary_axes") or DEFAULT_AXES
    scale = scoring.get("scale") or {}
    smin = scale.get("min", 1)
    smax = scale.get("max", 5)
    thr = scale.get("pass_threshold", 3)
    rubric = (ra.get("priority_rubric") or {}).get("weights") or {}

    # 우선순위 가중: 미달 축이 많을수록·미달폭이 클수록 + rubric 가중(축 이름이 weights 키와 겹치면 가산)
    rows, below = [], []        # below: (sid, title, 미달축 리스트)
    for s in m.get("sections", []) or []:
        sc = s.get("scores")
        if not isinstance(sc, dict) or not sc:
            continue
        sid = s["id"]; title = s.get("title", "")
        per_axis, deficit, weight = {}, 0, 0
        miss_axes = []
        for ax in axes:
            v = sc.get(ax)
            per_axis[ax] = v
            if isinstance(v, int):
                if v < thr:
                    deficit += (thr - v)
                    miss_axes.append(ax)
                    weight += int(rubric.get(ax, 1))
        # rubric의 비-축 키(regulatory·blocking 등)는 섹션 flag로 가산
        for k, w in rubric.items():
            if k not in axes and s.get(k):
                weight += int(w)
        prio = weight * 10 + deficit       # 가중 우선 → 미달폭 tiebreak
        rows.append((sid, title, per_axis, deficit, prio))
        if miss_axes:
            below.append((sid, title, miss_axes))

    rows.sort(key=lambda r: r[4], reverse=True)   # 우선순위 가중 정렬

    title = proj.get("title") or proj.get("product", "PM 문서")
    gen_at = datetime.now(KST).strftime("%Y-%m-%d %H:%M KST")
    L = [f"# {esc(title)} — 재검토/감사 채점 리포트 (내부 비공개)", "",
         f"> 자동 생성: `score_report.py` · 생성: **{gen_at}** · 척도 {smin}~{smax}, 통과 임계 **{thr}**. 배포본 미포함.", "",
         f"- 채점된 섹션: **{len(rows)}개**  ·  임계 미달 섹션: **{len(below)}개**",
         f"- 축: " + (", ".join(axes)), ""]

    L += ["## 1. 섹션별 점수 (우선순위 가중 정렬)", ""]
    if rows:
        hdr = "| 섹션 | 제목 | " + " | ".join(axes) + " | 미달폭 | 우선순위 |"
        sep = "|------|------|" + "------|" * len(axes) + "------|------|"
        L += [hdr, sep]
        for sid, t, per_axis, deficit, prio in rows:
            cells = []
            for ax in axes:
                v = per_axis.get(ax)
                cells.append("—" if v is None else (f"**{v}**⚠" if isinstance(v, int) and v < thr else str(v)))
            L.append(f"| {esc(sid)} | {esc(t)} | " + " | ".join(cells) + f" | {deficit} | {prio} |")
    else:
        L.append("_채점된 섹션 없음 (섹션에 scores: {completeness,…} 추가 후 재실행)._")

    L += ["", "## 2. 임계 미달 섹션 (pass_threshold 미만 축)", ""]
    if below:
        L += ["| 섹션 | 제목 | 미달 축 |", "|------|------|------|"]
        for sid, t, miss in below:
            L.append(f"| {esc(sid)} | {esc(t)} | {esc(', '.join(miss))} |")
    else:
        L.append("_임계 미달 없음._")
    L.append("")

    os.makedirs(os.path.dirname(os.path.abspath(out)), exist_ok=True)
    with open(out, "w", encoding="utf-8") as f:
        f.write("\n".join(L))
    print(f"review-audit report: {out}  (채점 {len(rows)} / 임계미달 {len(below)} / 임계 {thr})")

    if a.strict and below:
        sys.exit(f"[채점 게이트 실패] pass_threshold({thr}) 미달 섹션 {len(below)}건")


if __name__ == "__main__":
    main()
