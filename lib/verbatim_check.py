#!/usr/bin/env python3
"""verbatim check (review/audit mode ⑥): mechanically checks whether the body's
blockquotes match the source **character-for-character**. A script does this, not an LLM —
whether something labeled a 'quote' really matches the source is an exact-substring fact,
not a judgment (hard rule against fake confirmation).

Source = NOT manifest.project.ssot but the **quote's origin (targets)**:
  pm-policy review_audit.verbatim.targets, or the manifest's document-level `verbatim:` items (source).
Check = looks up the body's (SSOT) `>` blockquotes (or quotes named in verbatim.quotes)
       in the source via **exact-substring after whitespace normalization**.

Output: reports/_verbatim_report.md (source SHA256 16 chars · full/partial/miss counts).
Usage: python3 verbatim_check.py <manifest.yaml> [--out report.md] [--strict]
  --strict: exit 1 if any quote has no match (MISS).
(ported from the gap_audit.py pattern — load_validated · esc · KST · --strict gate. reuses split.split_h1.)

Enforcement model: this script is a **mechanical block** axis (SHA · exact-substring).
Axis scoring and applying changes are done by fan-out / a human."""
import sys, os, re, argparse, hashlib
from datetime import datetime, timezone, timedelta
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from validate_manifest import load_validated
from split import split_h1   # 본문 H1 분할(인용 위치 표시용 — 보조)

KST = timezone(timedelta(hours=9))


def esc(s):
    """마크다운 표 cell 안전화: None→빈칸, |·줄바꿈·역슬래시 깨짐 방지."""
    if s is None:
        return ""
    return str(s).replace("\\", "\\\\").replace("|", "\\|").replace("\r", " ").replace("\n", " ").strip()


def _norm_ws(s):
    """공백 정규화: 모든 공백류(개행·탭·연속공백)를 단일 스페이스로 접고 양끝 trim.
    verbatim exact-substring 대조 기준 — 줄바꿈/들여쓰기 차이는 무시하되 글자는 그대로."""
    return re.sub(r"\s+", " ", str(s)).strip()


def sha16(text):
    """원문 SHA256 앞 16자(원문 동일성 기록용)."""
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:16]


def extract_blockquotes(md):
    """본문에서 `>` 인용 블록을 추출 → 인용문 리스트(연속된 `>` 줄은 한 인용으로 묶음).
    split.split_h1과 같은 코드펜스 인식: ``` ~~~ 안의 `>`는 인용으로 보지 않음."""
    quotes, cur, fence = [], [], False
    for line in md.splitlines():
        st = line.lstrip()
        if st.startswith("```") or st.startswith("~~~"):
            fence = not fence
            if cur:
                quotes.append(" ".join(cur)); cur = []
            continue
        if not fence and re.match(r" {0,3}>", line):
            cur.append(re.sub(r"^\s{0,3}>\s?", "", line))
        else:
            if cur:
                quotes.append(" ".join(cur)); cur = []
    if cur:
        quotes.append(" ".join(cur))
    return [q for q in (qq.strip() for qq in quotes) if q]


def load_targets(m, base, proj):
    """대조할 (label, 원문경로) 목록. 우선순위:
    1) manifest 문서레벨 verbatim: [{source, quotes?}] 의 source
    2) pm-policy review_audit.verbatim.targets
    경로는 manifest 폴더 기준 상대(절대면 그대로). ~ 확장."""
    targets = []
    for v in (m.get("verbatim") or []):
        if isinstance(v, dict) and v.get("source"):
            targets.append(v["source"])
    # pm-policy targets
    pol = _load_policy(proj, base)
    ra = (pol or {}).get("review_audit") or {}
    for t in ((ra.get("verbatim") or {}).get("targets") or []):
        if t not in targets:
            targets.append(t)
    resolved = []
    for t in targets:
        p = os.path.expanduser(str(t))
        if not os.path.isabs(p):
            p = os.path.join(base, p)
        resolved.append((str(t), p))
    return resolved


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


def collect_quotes(m, body):
    """대조할 인용 목록. manifest verbatim[].quotes 가 명시되면 그것, 없으면 본문 `>` 인용 전체."""
    explicit = []
    for v in (m.get("verbatim") or []):
        if isinstance(v, dict):
            for q in (v.get("quotes") or []):
                if isinstance(q, str) and q.strip():
                    explicit.append(q.strip())
    if explicit:
        return explicit
    return extract_blockquotes(body)


def main():
    ap = argparse.ArgumentParser(description="verbatim quote ↔ source exact-substring check")
    ap.add_argument("manifest")
    ap.add_argument("--out", default="")
    ap.add_argument("--strict", action="store_true")
    a = ap.parse_args()
    m = load_validated(a.manifest)
    base = os.path.dirname(os.path.abspath(a.manifest))
    proj = m["project"]
    out = a.out or os.path.join(base, "reports", "_verbatim_report.md")

    # 본문(SSOT)
    ssot_path = os.path.join(base, proj.get("ssot", ""))
    body = ""
    if proj.get("ssot") and os.path.exists(ssot_path):
        body = open(ssot_path, encoding="utf-8").read()

    # 원문 targets 로드 + SHA
    targets = load_targets(m, base, proj)
    src_texts, src_rows = [], []
    for label, path in targets:
        if os.path.exists(path):
            txt = open(path, encoding="utf-8").read()
            src_texts.append(txt)
            src_rows.append((label, sha16(txt), "OK"))
        else:
            src_rows.append((label, "—", "missing"))
    norm_sources = [_norm_ws(t) for t in src_texts]

    quotes = collect_quotes(m, body)
    results = []   # (인용요약, 판정, 매칭원문 라벨)
    n_full = n_partial = n_miss = 0
    for q in quotes:
        nq = _norm_ws(q)
        verdict, where = "MISS", ""
        # FULL: 어느 원문에든 정규화 후 exact-substring
        for (label, _p), ns in zip(targets, norm_sources):
            if nq and nq in ns:
                verdict, where = "FULL", label
                break
        if verdict == "MISS" and nq:
            # PARTIAL: 인용을 절반 이상(긴 토막)이라도 원문에 포함하는지(앞 60% 토막)
            cut = nq[: max(8, int(len(nq) * 0.6))]
            for (label, _p), ns in zip(targets, norm_sources):
                if cut and cut in ns:
                    verdict, where = "PARTIAL", label
                    break
        if verdict == "FULL":
            n_full += 1
        elif verdict == "PARTIAL":
            n_partial += 1
        else:
            n_miss += 1
        results.append((q, verdict, where))

    title = proj.get("title") or proj.get("product", "PM doc")
    gen_at = datetime.now(KST).strftime("%Y-%m-%d %H:%M KST")
    L = [f"# {esc(title)} — verbatim check report (internal, not for release)", "",
         f"> Auto-generated: `verbatim_check.py` · generated: **{gen_at}** · exact-substring (whitespace-normalized). Not included in the release.", "",
         f"- quotes: **{len(quotes)}**  ·  exact (FULL) **{n_full}** · partial (PARTIAL) {n_partial} · no match (MISS) **{n_miss}**",
         f"- sources (targets): **{len(targets)}**", ""]

    L += ["## 1. Sources (targets) SHA256", ""]
    if src_rows:
        L += ["| source | SHA256(16) | status |", "|------|------|------|"]
        L += [f"| {esc(lbl)} | {esc(sha)} | {esc(stt)} |" for lbl, sha, stt in src_rows]
    else:
        L.append("_No sources specified (manifest verbatim[].source or pm-policy review_audit.verbatim.targets)._")

    L += ["", "## 2. Quote check (FULL=exact match to source / PARTIAL=partial / MISS=no match)", ""]
    if quotes:
        L += ["| verdict | matched source | quote (excerpt) |", "|------|------|------|"]
        for q, v, where in results:
            short = q if len(q) <= 80 else q[:77] + "…"
            L.append(f"| {esc(v)} | {esc(where)} | {esc(short)} |")
    else:
        L.append("_No blockquotes in the body (or verbatim.quotes not specified)._")
    L.append("")

    os.makedirs(os.path.dirname(os.path.abspath(out)), exist_ok=True)
    with open(out, "w", encoding="utf-8") as f:
        f.write("\n".join(L))
    print(f"verbatim report: {out}  (FULL {n_full} / PARTIAL {n_partial} / MISS {n_miss} / sources {len(targets)})")

    if a.strict and n_miss:
        sys.exit(f"[verbatim gate FAILED] {n_miss} quote(s) with no match (MISS)")


if __name__ == "__main__":
    main()
