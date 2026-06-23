#!/usr/bin/env python3
"""docloop 회귀 테스트 — split.py(배포 분할) + approval_brief.py + validate_manifest sanity.
사용: python3 tests/run_tests.py"""
import sys, os, tempfile, subprocess

SCRIPTS = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "lib")
sys.path.insert(0, SCRIPTS)
import validate_manifest as V  # noqa: E402
import split as SP             # noqa: E402
import approval_brief as AB    # noqa: E402
import verbatim_check as VC    # noqa: E402
import score_report as SR      # noqa: E402

_passed = _failed = 0


def check(name, cond):
    global _passed, _failed
    if cond:
        _passed += 1; print(f"ok   {name}")
    else:
        _failed += 1; print(f"FAIL {name}")


def run(cwd, *args):
    return subprocess.run([sys.executable, os.path.join(SCRIPTS, "split.py"), "manifest.yaml", *args],
                          cwd=cwd, capture_output=True, text=True)


# ── 순수 함수 ──
check("safe_filename 경로구분자 제거", SP.safe_filename("a/b\\c") == "a_b_c")
check("safe_filename 상위참조→untitled", SP.safe_filename("..") == "untitled")
_b = SP.split_h1("# A\n\nx\n\n# B\n\ny\n")
check("split_h1 H1 분할", [t for t, _ in _b] == ["A", "B"])
check("split_h1 코드펜스 내 # 무시", len(SP.split_h1("# A\n\n```\n# 가짜\n```\n")) == 1)
check("split_h1 닫는 # 시퀀스 제거", SP.split_h1("# 제목 #\n\nx\n")[0][0] == "제목")
check("split_h1 선행 공백 H1 인식(≤3칸)", [t for t, _ in SP.split_h1("  # 들여쓴제목\n\nx\n")] == ["들여쓴제목"])
check("split_h1 4칸+ 들여쓰기는 H1 아님(코드)", SP.split_h1("    # 코드주석\n")[0][0] is None)
check("split_h1 'C#' 실제 제목 보존(닫는# 아님)", SP.split_h1("# C#\n\nx\n")[0][0] == "C#")

# ── 디스크 fixture ──
tmp = tempfile.mkdtemp()
open(os.path.join(tmp, "pm-policy.yaml"), "w").write(
    "org: {name: T, product_default: 제품X}\n"
    "doc_types:\n  PRD:\n    title_pattern: \"{product} - {feature} PRD\"\n    sections:\n"
    "      - {id: overview, title: \"개요/배경\", required: true}\n"
    "      - {id: goals,    title: \"목표\", required: true}\n"
    "      - {id: scope,    title: \"범위\", required: true}\n"
    "      - {id: edge,     title: \"예외\", required: true}\n"
    "output: {platform: confluence, page_pattern: \"{product} - {feature} PRD\", approval_brief: true}\n")
open(os.path.join(tmp, "PRD.md"), "w").write("# 개요/배경\n\n배경\n\n# 목표\n\n목표본문\n\n# 범위\n\n범위초안\n")
open(os.path.join(tmp, "manifest.yaml"), "w").write(
    "project:\n  doc_type: PRD\n  product: 제품X\n  feature: 케이스제출\n  title: 제품X PRD\n"
    "  ssot: PRD.md\n  policy: ./pm-policy.yaml\n  output_dir: outputs\n"
    "sections:\n"
    "  - {id: overview, title: \"개요/배경\", status: approved, sources: [k1]}\n"
    "  - {id: goals,    title: \"목표\", status: approved, sources: [k2]}\n"
    "  - {id: scope,    title: \"범위\", status: draft}\n"
    "  - {id: edge,     title: \"예외\", status: pending}\n")
os.makedirs(os.path.join(tmp, "outputs"))
ORIG_SSOT = open(os.path.join(tmp, "PRD.md")).read()

# validate sanity
m = V.load_validated(os.path.join(tmp, "manifest.yaml"), strict=False)
check("validate_manifest: fixture 통과(오류 0)", isinstance(m, dict) and m["project"]["doc_type"] == "PRD")

# dry-run: approved 2개만, draft/pending 제외
r = run(tmp, "--dry-run")
check("split: dry-run approved만 포함(overview·goals)",
      r.returncode == 0 and "overview, goals" in r.stdout)
check("split: dry-run 쓰기 없음",
      not any(f.endswith(".md") for f in os.listdir(os.path.join(tmp, "outputs"))))

# 일반 빌드: page_pattern 치환된 파일 1장, approved 2섹션
r = run(tmp)
outs = [f for f in os.listdir(os.path.join(tmp, "outputs")) if f.endswith(".md")]
check("split: page_pattern 치환 파일명", outs == ["제품X - 케이스제출 PRD.md"])
body = open(os.path.join(tmp, "outputs", outs[0])).read() if outs else ""
check("split: approved 본문 포함, draft/pending 제외",
      "배경" in body and "목표본문" in body and "범위초안" not in body)
check("split: 마커 생성", os.path.exists(os.path.join(tmp, "outputs", ".docloop_output")))
check("split: SSOT 무손상", open(os.path.join(tmp, "PRD.md")).read() == ORIG_SSOT)

# strict 역할 분리: 포함 섹션(approved overview·goals) 본문 있음 → strict 통과.
# required 미승인(edge pending 등)은 경고만(차단 X). req_unmet 경고는 비-strict에서도 출력.
r = run(tmp, "--strict")
check("split: --strict 배포완전성 통과(포함 본문 OK)", r.returncode == 0)
r = run(tmp, "--dry-run")
check("split: required 미승인 경고는 항상 출력(strict 아님)", "required not approved" in r.stdout)

# include-draft: scope(draft) 포함
r = run(tmp, "--include-draft")
body = open(os.path.join(tmp, "outputs", outs[0])).read()
check("split: --include-draft draft 포함", "범위초안" in body)

import shutil

# strict 실패 1: approved 섹션인데 SSOT에 본문 H1 없음 → 배포 완전성 실패
nb = tempfile.mkdtemp()
open(os.path.join(nb, "PRD.md"), "w").write("# 개요\n\n본문\n")   # goals H1 없음
open(os.path.join(nb, "manifest.yaml"), "w").write(
    "project: {doc_type: PRD, product: P, title: P, ssot: PRD.md, output_dir: outputs}\n"
    "sections:\n  - {id: a, title: \"개요\", status: approved, sources: [k]}\n"
    "  - {id: b, title: \"목표\", status: approved, sources: [k]}\n")
os.makedirs(os.path.join(nb, "outputs"))
r = run(nb, "--strict")
check("split: --strict 본문 없음 실패", r.returncode != 0 and "no body" in (r.stdout + r.stderr))

# strict 실패 2: SSOT 중복 H1 → 조용한 유실 방지로 실패 (#r1-1)
du = tempfile.mkdtemp()
open(os.path.join(du, "PRD.md"), "w").write("# 개요\n\n첫번째\n\n# 개요\n\n두번째\n")
open(os.path.join(du, "manifest.yaml"), "w").write(
    "project: {doc_type: PRD, product: P, title: P, ssot: PRD.md, output_dir: outputs}\n"
    "sections:\n  - {id: a, title: \"개요\", status: approved, sources: [k]}\n")
os.makedirs(os.path.join(du, "outputs"))
r = run(du, "--strict")
check("split: --strict 중복 H1 실패(#r1-1)", r.returncode != 0 and "duplicate H1" in (r.stdout + r.stderr))
r = run(du)   # 비-strict는 경고만, 앞 블록 유지
check("split: 중복 H1 비-strict 경고+앞블록 유지", r.returncode == 0
      and "첫번째" in open(os.path.join(du, "outputs", "P.md")).read())

# 빈 무마커 outputs 입양
shutil.rmtree(os.path.join(tmp, "outputs")); os.makedirs(os.path.join(tmp, "outputs"))
r = run(tmp)
check("split: 빈 무마커 폴더 입양", r.returncode == 0 and os.path.exists(os.path.join(tmp, "outputs", ".docloop_output")))

# marker 있는 폴더 rmtree 후 재생성(멱등) (#r1-9)
r = run(tmp)
check("split: 마커 폴더 멱등 재생성", r.returncode == 0 and os.path.exists(os.path.join(tmp, "outputs", outs[0])))

# 비어있지 않은 무마커 outputs 거부
shutil.rmtree(os.path.join(tmp, "outputs")); os.makedirs(os.path.join(tmp, "outputs"))
open(os.path.join(tmp, "outputs", "user.txt"), "w").write("x")
r = run(tmp)
check("split: 비어있지 않은 무마커 거부", r.returncode != 0 and "marker" in (r.stdout + r.stderr))

# symlink output_dir 거부 (#r1-9)
sl = tempfile.mkdtemp()
open(os.path.join(sl, "PRD.md"), "w").write("# 개요\n\n본문\n")
open(os.path.join(sl, "manifest.yaml"), "w").write(
    "project: {doc_type: PRD, product: P, title: P, ssot: PRD.md, output_dir: outputs}\n"
    "sections:\n  - {id: a, title: \"개요\", status: approved, sources: [k]}\n")
ext = tempfile.mkdtemp()
os.symlink(ext, os.path.join(sl, "outputs"))
r = run(sl)
check("split: symlink output_dir 거부", r.returncode != 0
      and ("symlink" in (r.stdout + r.stderr) or "안전검사" in (r.stdout + r.stderr)))

# ── approval_brief.py ──
check("approval_brief _strip_h1 선행 H1 제거", AB._strip_h1("# 목표\n\n본문\n") == "본문")
check("approval_brief _match id/title 키워드", AB._match({"id": "goals", "title": "목표"}, AB.GOAL_IDS, AB.GOAL_KW))

ab = tempfile.mkdtemp()
open(os.path.join(ab, "PRD.md"), "w").write("# 목표\n\n전환율 개선\n\n# 범위\n\n포함: 폼\n")
open(os.path.join(ab, "manifest.yaml"), "w").write(
    "project: {doc_type: PRD, product: P, title: P PRD, ssot: PRD.md, output_dir: outputs}\n"
    "sections:\n  - {id: goals, title: \"목표\", status: approved, sources: [k]}\n"
    "  - {id: scope, title: \"범위\", status: approved, sources: [k]}\n"
    "open_questions:\n  - {id: q1, topic: \"충돌\", owner: \"기획\", status: open, reason: \"미정\"}\n"
    "  - {id: q2, topic: \"끝난것\", owner: \"x\", status: resolved}\n"
    "decisions:\n  - {id: d1, date: 2026-06-01, decision: \"확정\", by: \"리드\"}\n")
os.makedirs(os.path.join(ab, "reports"))
rb = subprocess.run([sys.executable, os.path.join(SCRIPTS, "approval_brief.py"), "manifest.yaml",
                     "--out", "reports/_approval_brief.md"], cwd=ab, capture_output=True, text=True)
brief = open(os.path.join(ab, "reports", "_approval_brief.md"), encoding="utf-8").read()
check("approval_brief: 생성 성공", rb.returncode == 0 and os.path.exists(os.path.join(ab, "reports", "_approval_brief.md")))
check("approval_brief: 목적 본문 추출(H1 중복 없음)",
      "전환율 개선" in brief and "### 목표" in brief
      and not any(ln.strip() == "# 목표" for ln in brief.splitlines()))
check("approval_brief: 범위 본문 추출", "포함: 폼" in brief)
check("approval_brief: open_questions open만 표기(resolved 기본 제외)",
      "충돌" in brief and "끝난것" not in brief)
check("approval_brief: 결정 로그·섹션 상태 표기", "확정" in brief and "| goals |" in brief)

# r1-2 키워드 앵커: '기능 목적 + AC'는 목적류 아님(시작 매칭)
check("approval_brief: '기능 목적+AC' 목적 오탐 안 함",
      not AB._match({"id": "func-ac", "title": "기능 목적 + AC(인수조건)"}, AB.GOAL_IDS, AB.GOAL_KW))
# r1-3 정규화: 번호·공백 변형 흡수
check("approval_brief: _norm 번호·공백 흡수", AB._norm("1. 목표 / 성공기준") == AB._norm("목표/성공기준"))
# r1-5 _strip_h1: 소제목(####)·코드는 보존
check("approval_brief: _strip_h1 소제목 보존", AB._strip_h1("#### 소제목\n본문\n") == "#### 소제목\n본문")
# r1-3 통합: 번호 붙은 SSOT H1도 본문 추출
nb2 = tempfile.mkdtemp()
open(os.path.join(nb2, "PRD.md"), "w").write("# 1. 목표/성공기준\n\n번호달린목표\n")
open(os.path.join(nb2, "manifest.yaml"), "w").write(
    "project: {doc_type: PRD, product: P, title: P, ssot: PRD.md, output_dir: outputs}\n"
    "sections:\n  - {id: goals, title: \"목표 / 성공기준\", status: approved, sources: [k]}\n")
r = subprocess.run([sys.executable, os.path.join(SCRIPTS, "approval_brief.py"), "manifest.yaml"],
                   cwd=nb2, capture_output=True, text=True)
# r1-4 기본 출력이 reports/ 로
check("approval_brief: 기본 출력 reports/", r.returncode == 0
      and os.path.exists(os.path.join(nb2, "reports", "_approval_brief.md")))
check("approval_brief: 번호 H1도 본문 추출(정규화)",
      "번호달린목표" in open(os.path.join(nb2, "reports", "_approval_brief.md"), encoding="utf-8").read())

# ══ 재검토/감사 모드 (review-audit) ══

# ── verbatim_check.py 순수 함수 ──
check("verbatim _norm_ws 공백 접기", VC._norm_ws("a  b\n c\t d") == "a b c d")
check("verbatim sha16 16자", len(VC.sha16("abc")) == 16)
_q = VC.extract_blockquotes("> 인용 한 줄\n> 이어진 줄\n\n본문\n\n> 다른 인용\n")
check("verbatim extract_blockquotes 인용 묶기", _q == ["인용 한 줄 이어진 줄", "다른 인용"])
check("verbatim extract_blockquotes 코드펜스 내 > 무시",
      VC.extract_blockquotes("```\n> 가짜인용\n```\n") == [])

# ── verbatim_check 디스크: 일치/불일치 + --strict ──
vb = tempfile.mkdtemp()
os.makedirs(os.path.join(vb, "inputs")); os.makedirs(os.path.join(vb, "reports"))
open(os.path.join(vb, "inputs", "orig.md"), "w").write(
    "원문 시작\n\n검증 실패 시 사유와 확인된 값을 함께 안내해야 한다\n\n원문 끝\n")
# SSOT: 첫 인용은 원문과 글자 일치(FULL), 둘째 인용은 원문에 없음(MISS)
open(os.path.join(vb, "PRD.md"), "w").write(
    "# 본문\n\n> 검증 실패 시 사유와 확인된 값을 함께 안내해야 한다\n\n설명\n\n> 원문에 전혀 없는 문장입니다\n")
open(os.path.join(vb, "pm-policy.yaml"), "w").write(
    "org: {name: T}\nreview_audit:\n  verbatim: {enabled: true, targets: [\"inputs/orig.md\"]}\n")
open(os.path.join(vb, "manifest.yaml"), "w").write(
    "project: {doc_type: PRD, product: P, title: P, ssot: PRD.md, policy: ./pm-policy.yaml, output_dir: outputs}\n"
    "sections:\n  - {id: a, title: \"본문\", status: approved, sources: [k]}\n")


def run_vc(cwd, *args):
    return subprocess.run([sys.executable, os.path.join(SCRIPTS, "verbatim_check.py"), "manifest.yaml", *args],
                          cwd=cwd, capture_output=True, text=True)


r = run_vc(vb)
rep = open(os.path.join(vb, "reports", "_verbatim_report.md"), encoding="utf-8").read()
check("verbatim: 리포트 생성", r.returncode == 0 and os.path.exists(os.path.join(vb, "reports", "_verbatim_report.md")))
check("verbatim: FULL 1 · MISS 1 집계", "(FULL) **1**" in rep and "(MISS) **1**" in rep)
check("verbatim: 원문 SHA 기록", VC.sha16(open(os.path.join(vb, "inputs", "orig.md")).read())[:8] in rep)
r = run_vc(vb, "--strict")
check("verbatim: --strict MISS 있으면 exit 1", r.returncode != 0 and "MISS" in (r.stdout + r.stderr))

# 전부 일치하면 --strict 통과
vb2 = tempfile.mkdtemp()
os.makedirs(os.path.join(vb2, "inputs")); os.makedirs(os.path.join(vb2, "reports"))
open(os.path.join(vb2, "inputs", "orig.md"), "w").write("두 자료의 안내 경험은 같아야 한다\n")
open(os.path.join(vb2, "PRD.md"), "w").write("# 본문\n\n> 두 자료의 안내 경험은 같아야 한다\n")
open(os.path.join(vb2, "pm-policy.yaml"), "w").write(
    "review_audit:\n  verbatim: {enabled: true, targets: [\"inputs/orig.md\"]}\n")
open(os.path.join(vb2, "manifest.yaml"), "w").write(
    "project: {doc_type: PRD, product: P, title: P, ssot: PRD.md, policy: ./pm-policy.yaml, output_dir: outputs}\n"
    "sections:\n  - {id: a, title: \"본문\", status: approved, sources: [k]}\n")
r = run_vc(vb2, "--strict")
check("verbatim: 전부 일치 시 --strict 통과", r.returncode == 0)

# ── score_report.py 디스크: 점수표 + 임계 미달 --strict ──
sc = tempfile.mkdtemp()
os.makedirs(os.path.join(sc, "reports"))
open(os.path.join(sc, "PRD.md"), "w").write("# A\n\n본문\n")
open(os.path.join(sc, "pm-policy.yaml"), "w").write(
    "review_audit:\n  scoring: {primary_axes: [completeness, coherence, clarity, depth], scale: {min: 1, max: 5, pass_threshold: 3}}\n"
    "  priority_rubric: {weights: {regulatory: 3, blocking: 3, coherence: 2, clarity: 1}}\n")
open(os.path.join(sc, "manifest.yaml"), "w").write(
    "project: {doc_type: PRD, product: P, title: P, ssot: PRD.md, policy: ./pm-policy.yaml, output_dir: outputs}\n"
    "sections:\n"
    "  - {id: a, title: \"A\", status: approved, sources: [k], scores: {completeness: 5, coherence: 4, clarity: 4, depth: 3}}\n"
    "  - {id: b, title: \"B\", status: draft, scores: {completeness: 2, coherence: 5, clarity: 5, depth: 5}}\n")


def run_sr(cwd, *args):
    return subprocess.run([sys.executable, os.path.join(SCRIPTS, "score_report.py"), "manifest.yaml", *args],
                          cwd=cwd, capture_output=True, text=True)


r = run_sr(sc)
srep = open(os.path.join(sc, "reports", "_review_audit.md"), encoding="utf-8").read()
check("score_report: 리포트 생성", r.returncode == 0 and os.path.exists(os.path.join(sc, "reports", "_review_audit.md")))
check("score_report: 두 섹션 채점 집계", "scored sections: **2**" in srep)
check("score_report: 임계 미달 섹션 b 표기(completeness)",
      "below-threshold sections: **1**" in srep and "| b |" in srep.split("Below-threshold sections")[1])
r = run_sr(sc, "--strict")
check("score_report: --strict 임계 미달 시 exit 1", r.returncode != 0 and "below" in (r.stdout + r.stderr))

# 전부 임계 이상이면 --strict 통과
sc2 = tempfile.mkdtemp()
os.makedirs(os.path.join(sc2, "reports"))
open(os.path.join(sc2, "PRD.md"), "w").write("# A\n\n본문\n")
open(os.path.join(sc2, "pm-policy.yaml"), "w").write(
    "review_audit:\n  scoring: {primary_axes: [completeness, coherence, clarity, depth], scale: {pass_threshold: 3}}\n")
open(os.path.join(sc2, "manifest.yaml"), "w").write(
    "project: {doc_type: PRD, product: P, title: P, ssot: PRD.md, policy: ./pm-policy.yaml, output_dir: outputs}\n"
    "sections:\n  - {id: a, title: \"A\", status: approved, sources: [k], scores: {completeness: 4, coherence: 4, clarity: 4, depth: 4}}\n")
r = run_sr(sc2, "--strict")
check("score_report: 전부 임계 이상 시 --strict 통과", r.returncode == 0)

# ── validate_manifest: optional scores/verbatim ──
# 정상: scores(정수) + verbatim(source/quotes)
m_ok = {
    "project": {"product": "P", "ssot": "x.md"},
    "sections": [{"id": "a", "title": "A", "status": "approved", "sources": ["k"],
                  "scores": {"completeness": 4, "coherence": 3, "clarity": 5, "depth": 3}}],
    "verbatim": [{"source": "inputs/o.md", "quotes": ["인용1"]}],
}
E, W = V.validate(m_ok)
check("validate: scores/verbatim 정상 통과(오류 0)", E == [])

# scores 비정수 → 오류
m_bad = {"project": {"product": "P", "ssot": "x.md"},
         "sections": [{"id": "a", "title": "A", "status": "approved", "sources": ["k"],
                       "scores": {"completeness": "high"}}]}
E, W = V.validate(m_bad)
check("validate: scores 비정수 오류", any("scores.completeness" in e for e in E))

# verbatim source 누락 → 오류
m_bad2 = {"project": {"product": "P", "ssot": "x.md"},
          "sections": [{"id": "a", "title": "A", "status": "approved", "sources": ["k"]}],
          "verbatim": [{"quotes": ["x"]}]}
E, W = V.validate(m_bad2)
check("validate: verbatim source 누락 오류", any("source missing" in e for e in E))

# scores/verbatim 없으면 통과(하위호환)
m_none = {"project": {"product": "P", "ssot": "x.md"},
          "sections": [{"id": "a", "title": "A", "status": "approved", "sources": ["k"]}]}
E, W = V.validate(m_none)
check("validate: scores/verbatim 없으면 통과(하위호환)", E == [])

# ── validate_manifest: review_audit 적용추적(백포트) ──
# 정상: pending_apply/applied 각 decision_id가 decisions[]에 존재
m_ra_ok = {"project": {"product": "P", "ssot": "x.md"},
           "sections": [{"id": "a", "title": "A", "status": "approved", "sources": ["k"]}],
           "decisions": [{"id": "d1", "decision": "x"}, {"id": "d2", "decision": "y"}],
           "review_audit": {"pending_apply": [{"decision_id": "d2", "doc": "P.md", "note": "미반영"}],
                            "applied": [{"decision_id": "d1", "verified_at": "2026-06-03"}]}}
E, W = V.validate(m_ra_ok)
check("validate: review_audit pending_apply/applied 정상(오류 0)", E == [])

# decision_id 누락 → 오류(Codex#5 추적성)
m_ra_bad = {"project": {"product": "P", "ssot": "x.md"},
            "sections": [{"id": "a", "title": "A", "status": "approved", "sources": ["k"]}],
            "review_audit": {"pending_apply": [{"doc": "P.md"}]}}
E, W = V.validate(m_ra_bad)
check("validate: pending_apply decision_id 누락 오류", any("decision_id required" in e for e in E))

# decision_id 공백문자만 → 오류(peer r1#1 strip)
m_ra_blank = {"project": {"product": "P", "ssot": "x.md"},
              "sections": [{"id": "a", "title": "A", "status": "approved", "sources": ["k"]}],
              "decisions": [{"id": "d1", "decision": "x"}],
              "review_audit": {"applied": [{"decision_id": "  "}]}}
E, W = V.validate(m_ra_blank)
check("validate: decision_id 공백만 오류", any("decision_id required" in e for e in E))

# dangling: decision_id가 decisions[]에 없음 → 오류(peer r1#1 참조무결성)
m_ra_dangling = {"project": {"product": "P", "ssot": "x.md"},
                 "sections": [{"id": "a", "title": "A", "status": "approved", "sources": ["k"]}],
                 "decisions": [{"id": "d1", "decision": "x"}],
                 "review_audit": {"pending_apply": [{"decision_id": "dZ", "doc": "P.md"}]}}
E, W = V.validate(m_ra_dangling)
check("validate: dangling decision_id 오류", any("dangling" in e for e in E))

# list 내 중복 decision_id → 오류(peer r1#4)
m_ra_dup = {"project": {"product": "P", "ssot": "x.md"},
            "sections": [{"id": "a", "title": "A", "status": "approved", "sources": ["k"]}],
            "decisions": [{"id": "d1", "decision": "x"}],
            "review_audit": {"applied": [{"decision_id": "d1"}, {"decision_id": "d1"}]}}
E, W = V.validate(m_ra_dup)
check("validate: applied 내 decision_id 중복 오류", any("duplicate" in e for e in E))

# pending_apply ↔ applied 교집합 → 경고(peer r1#4, 오류 아님)
m_ra_cross = {"project": {"product": "P", "ssot": "x.md"},
              "sections": [{"id": "a", "title": "A", "status": "approved", "sources": ["k"]}],
              "decisions": [{"id": "d1", "decision": "x"}],
              "review_audit": {"pending_apply": [{"decision_id": "d1"}], "applied": [{"decision_id": "d1"}]}}
E, W = V.validate(m_ra_cross)
check("validate: pending_apply↔applied 교집합 경고(오류 아님)",
      E == [] and any("both" in w for w in W))

# review_audit 없으면 통과(하위호환)
E, W = V.validate(m_none)
check("validate: review_audit 없으면 통과(하위호환)", E == [])

# ── gap_audit.py: pending_apply 게이트(백포트) ──
def run_ga(cwd, *args):
    return subprocess.run([sys.executable, os.path.join(SCRIPTS, "gap_audit.py"), "manifest.yaml", *args],
                          cwd=cwd, capture_output=True, text=True)


# pending_apply 비어있지 않음 → 리포트 표기 + --strict exit 1
ga = tempfile.mkdtemp()
open(os.path.join(ga, "manifest.yaml"), "w").write(
    "project: {doc_type: PRD, product: P, title: P, ssot: PRD.md, output_dir: outputs}\n"
    "sections:\n  - {id: a, title: \"A\", status: approved, sources: [k]}\n"
    "decisions:\n  - {id: d2, date: 2026-06-02, decision: \"전화번호 optional\", by: \"리드\"}\n"
    "review_audit:\n  pending_apply:\n    - {decision_id: d2, doc: PRD.md, note: \"본문 미반영\"}\n")
r = run_ga(ga)
grep = open(os.path.join(ga, "reports", "_gap_report.md"), encoding="utf-8").read()
check("gap_audit: pending_apply 리포트 표기", r.returncode == 0 and "| d2 |" in grep and "pending_apply (unapplied): **1**" in grep)
r = run_ga(ga, "--strict")
check("gap_audit: --strict pending_apply 있으면 exit 1", r.returncode != 0 and "pending_apply" in (r.stdout + r.stderr))

# pending_apply 비어있음(applied만) → --strict 통과(하위호환: gaps/open/pending 없음)
ga2 = tempfile.mkdtemp()
open(os.path.join(ga2, "manifest.yaml"), "w").write(
    "project: {doc_type: PRD, product: P, title: P, ssot: PRD.md, output_dir: outputs}\n"
    "sections:\n  - {id: a, title: \"A\", status: approved, sources: [k]}\n"
    "decisions:\n  - {id: d1, date: 2026-06-01, decision: \"확정\", by: \"리드\"}\n"
    "review_audit:\n  applied:\n    - {decision_id: d1, verified_at: 2026-06-03}\n")
r = run_ga(ga2, "--strict")
check("gap_audit: pending_apply 비면 --strict 통과", r.returncode == 0)

# review_audit 키 없으면 기존 동작(하위호환) — pending_apply 0건
ga3 = tempfile.mkdtemp()
open(os.path.join(ga3, "manifest.yaml"), "w").write(
    "project: {doc_type: PRD, product: P, title: P, ssot: PRD.md, output_dir: outputs}\n"
    "sections:\n  - {id: a, title: \"A\", status: approved, sources: [k]}\n")
r = run_ga(ga3, "--strict")
check("gap_audit: review_audit 없으면 --strict 통과(하위호환)", r.returncode == 0)

print(f"\n=== {_passed} passed, {_failed} failed ===")
sys.exit(1 if _failed else 0)
