#!/usr/bin/env python3
"""docloop regression tests — split.py (deploy split) + approval_brief.py + validate_manifest sanity.
Usage: python3 tests/run_tests.py"""
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


# ── pure functions ──
check("safe_filename strips path separators", SP.safe_filename("a/b\\c") == "a_b_c")
check("safe_filename parent-ref → untitled", SP.safe_filename("..") == "untitled")
_b = SP.split_h1("# A\n\nx\n\n# B\n\ny\n")
check("split_h1 splits on H1", [t for t, _ in _b] == ["A", "B"])
check("split_h1 ignores # inside code fence", len(SP.split_h1("# A\n\n```\n# 가짜\n```\n")) == 1)
check("split_h1 strips trailing # sequence", SP.split_h1("# 제목 #\n\nx\n")[0][0] == "제목")
check("split_h1 recognizes H1 with leading spaces (≤3)", [t for t, _ in SP.split_h1("  # 들여쓴제목\n\nx\n")] == ["들여쓴제목"])
check("split_h1 4+ space indent is not H1 (code)", SP.split_h1("    # 코드주석\n")[0][0] is None)
check("split_h1 preserves 'C#' as real title (not trailing #)", SP.split_h1("# C#\n\nx\n")[0][0] == "C#")

# ── disk fixtures ──
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
check("validate_manifest: fixture passes (0 errors)", isinstance(m, dict) and m["project"]["doc_type"] == "PRD")

# dry-run: only 2 approved sections, draft/pending excluded
r = run(tmp, "--dry-run")
check("split: dry-run includes approved only (overview·goals)",
      r.returncode == 0 and "overview, goals" in r.stdout)
check("split: dry-run writes no files",
      not any(f.endswith(".md") for f in os.listdir(os.path.join(tmp, "outputs"))))

# normal build: 1 file with page_pattern substituted, 2 approved sections
r = run(tmp)
outs = [f for f in os.listdir(os.path.join(tmp, "outputs")) if f.endswith(".md")]
check("split: page_pattern substituted filename", outs == ["제품X - 케이스제출 PRD.md"])
body = open(os.path.join(tmp, "outputs", outs[0])).read() if outs else ""
check("split: approved body included, draft/pending excluded",
      "배경" in body and "목표본문" in body and "범위초안" not in body)
check("split: marker file created", os.path.exists(os.path.join(tmp, "outputs", ".docloop_output")))
check("split: SSOT untouched", open(os.path.join(tmp, "PRD.md")).read() == ORIG_SSOT)

# strict role separation: included sections (approved overview·goals) have body → strict passes.
# required but unapproved (edge pending etc.) only warns (not blocked). req_unmet warning also printed in non-strict.
r = run(tmp, "--strict")
check("split: --strict deploy-completeness passes (included body OK)", r.returncode == 0)
r = run(tmp, "--dry-run")
check("split: required-not-approved warning always printed (non-strict)", "required not approved" in r.stdout)

# include-draft: scope (draft) included
r = run(tmp, "--include-draft")
body = open(os.path.join(tmp, "outputs", outs[0])).read()
check("split: --include-draft includes draft sections", "범위초안" in body)

import shutil

# strict failure 1: approved section but SSOT missing body H1 → deploy-completeness fails
nb = tempfile.mkdtemp()
open(os.path.join(nb, "PRD.md"), "w").write("# 개요\n\n본문\n")   # goals H1 missing
open(os.path.join(nb, "manifest.yaml"), "w").write(
    "project: {doc_type: PRD, product: P, title: P, ssot: PRD.md, output_dir: outputs}\n"
    "sections:\n  - {id: a, title: \"개요\", status: approved, sources: [k]}\n"
    "  - {id: b, title: \"목표\", status: approved, sources: [k]}\n")
os.makedirs(os.path.join(nb, "outputs"))
r = run(nb, "--strict")
check("split: --strict fails when body missing", r.returncode != 0 and "no body" in (r.stdout + r.stderr))

# strict failure 2: duplicate H1 in SSOT → fails to prevent silent data loss (#r1-1)
du = tempfile.mkdtemp()
open(os.path.join(du, "PRD.md"), "w").write("# 개요\n\n첫번째\n\n# 개요\n\n두번째\n")
open(os.path.join(du, "manifest.yaml"), "w").write(
    "project: {doc_type: PRD, product: P, title: P, ssot: PRD.md, output_dir: outputs}\n"
    "sections:\n  - {id: a, title: \"개요\", status: approved, sources: [k]}\n")
os.makedirs(os.path.join(du, "outputs"))
r = run(du, "--strict")
check("split: --strict fails on duplicate H1 (#r1-1)", r.returncode != 0 and "duplicate H1" in (r.stdout + r.stderr))
r = run(du)   # non-strict: warn only, keep first block
check("split: duplicate H1 non-strict warns and keeps first block", r.returncode == 0
      and "첫번째" in open(os.path.join(du, "outputs", "P.md")).read())

# adopt empty unmarked outputs dir
shutil.rmtree(os.path.join(tmp, "outputs")); os.makedirs(os.path.join(tmp, "outputs"))
r = run(tmp)
check("split: adopts empty unmarked output dir", r.returncode == 0 and os.path.exists(os.path.join(tmp, "outputs", ".docloop_output")))

# idempotent re-creation after rmtree of marked dir (#r1-9)
r = run(tmp)
check("split: marked dir recreated idempotently (#r1-9)", r.returncode == 0 and os.path.exists(os.path.join(tmp, "outputs", outs[0])))

# non-empty unmarked outputs dir rejected
shutil.rmtree(os.path.join(tmp, "outputs")); os.makedirs(os.path.join(tmp, "outputs"))
open(os.path.join(tmp, "outputs", "user.txt"), "w").write("x")
r = run(tmp)
check("split: rejects non-empty unmarked output dir", r.returncode != 0 and "marker" in (r.stdout + r.stderr))

# symlink output_dir rejected (#r1-9)
sl = tempfile.mkdtemp()
open(os.path.join(sl, "PRD.md"), "w").write("# 개요\n\n본문\n")
open(os.path.join(sl, "manifest.yaml"), "w").write(
    "project: {doc_type: PRD, product: P, title: P, ssot: PRD.md, output_dir: outputs}\n"
    "sections:\n  - {id: a, title: \"개요\", status: approved, sources: [k]}\n")
ext = tempfile.mkdtemp()
os.symlink(ext, os.path.join(sl, "outputs"))
r = run(sl)
check("split: rejects symlink output_dir (#r1-9)", r.returncode != 0
      and ("symlink" in (r.stdout + r.stderr) or "안전검사" in (r.stdout + r.stderr)))

# ── approval_brief.py ──
check("approval_brief _strip_h1 removes leading H1", AB._strip_h1("# 목표\n\n본문\n") == "본문")
check("approval_brief _match id/title keyword", AB._match({"id": "goals", "title": "목표"}, AB.GOAL_IDS, AB.GOAL_KW))

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
check("approval_brief: generated successfully", rb.returncode == 0 and os.path.exists(os.path.join(ab, "reports", "_approval_brief.md")))
check("approval_brief: goal body extracted (no duplicate H1)",
      "전환율 개선" in brief and "### 목표" in brief
      and not any(ln.strip() == "# 목표" for ln in brief.splitlines()))
check("approval_brief: scope body extracted", "포함: 폼" in brief)
check("approval_brief: open_questions shows open only (resolved excluded by default)",
      "충돌" in brief and "끝난것" not in brief)
check("approval_brief: decision log and section status shown", "확정" in brief and "| goals |" in brief)

# r1-2 keyword anchor: _match is prefix-only (startswith), so a goal keyword appearing mid-title (e.g. "기능 목적 + AC") is NOT a goal match — avoids mid-string false positives
check("approval_brief: goal keyword mid-title (not prefix) is not a goal match",
      not AB._match({"id": "func-ac", "title": "기능 목적 + AC(인수조건)"}, AB.GOAL_IDS, AB.GOAL_KW))
# r1-3 normalization: absorbs numbering and whitespace variants
check("approval_brief: _norm absorbs numbering and whitespace", AB._norm("1. 목표 / 성공기준") == AB._norm("목표/성공기준"))
# r1-5 _strip_h1: sub-headings (####) and code are preserved
check("approval_brief: _strip_h1 preserves sub-headings", AB._strip_h1("#### 소제목\n본문\n") == "#### 소제목\n본문")
# r1-3 integrated: numbered SSOT H1 also extracts body
nb2 = tempfile.mkdtemp()
open(os.path.join(nb2, "PRD.md"), "w").write("# 1. 목표/성공기준\n\n번호달린목표\n")
open(os.path.join(nb2, "manifest.yaml"), "w").write(
    "project: {doc_type: PRD, product: P, title: P, ssot: PRD.md, output_dir: outputs}\n"
    "sections:\n  - {id: goals, title: \"목표 / 성공기준\", status: approved, sources: [k]}\n")
r = subprocess.run([sys.executable, os.path.join(SCRIPTS, "approval_brief.py"), "manifest.yaml"],
                   cwd=nb2, capture_output=True, text=True)
# r1-4 default output goes to reports/
check("approval_brief: default output to reports/", r.returncode == 0
      and os.path.exists(os.path.join(nb2, "reports", "_approval_brief.md")))
check("approval_brief: numbered H1 body extracted (normalized)",
      "번호달린목표" in open(os.path.join(nb2, "reports", "_approval_brief.md"), encoding="utf-8").read())

# ══ review/audit mode (review-audit) ══

# ── verbatim_check.py pure functions ──
check("verbatim _norm_ws collapses whitespace", VC._norm_ws("a  b\n c\t d") == "a b c d")
check("verbatim sha16 is 16 chars", len(VC.sha16("abc")) == 16)
_q = VC.extract_blockquotes("> 인용 한 줄\n> 이어진 줄\n\n본문\n\n> 다른 인용\n")
check("verbatim extract_blockquotes groups quotes", _q == ["인용 한 줄 이어진 줄", "다른 인용"])
check("verbatim extract_blockquotes ignores > inside code fence",
      VC.extract_blockquotes("```\n> 가짜인용\n```\n") == [])

# ── verbatim_check disk: match/mismatch + --strict ──
vb = tempfile.mkdtemp()
os.makedirs(os.path.join(vb, "inputs")); os.makedirs(os.path.join(vb, "reports"))
open(os.path.join(vb, "inputs", "orig.md"), "w").write(
    "원문 시작\n\n검증 실패 시 사유와 확인된 값을 함께 안내해야 한다\n\n원문 끝\n")
# SSOT: first quote matches source verbatim (FULL), second quote absent from source (MISS)
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
check("verbatim: report generated", r.returncode == 0 and os.path.exists(os.path.join(vb, "reports", "_verbatim_report.md")))
check("verbatim: FULL 1 · MISS 1 tallied", "(FULL) **1**" in rep and "(MISS) **1**" in rep)
check("verbatim: source SHA recorded", VC.sha16(open(os.path.join(vb, "inputs", "orig.md")).read())[:8] in rep)
r = run_vc(vb, "--strict")
check("verbatim: --strict exits 1 when MISS present", r.returncode != 0 and "MISS" in (r.stdout + r.stderr))

# all match → --strict passes
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
check("verbatim: --strict passes when all match", r.returncode == 0)

# ── score_report.py disk: score table + below-threshold --strict ──
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
check("score_report: report generated", r.returncode == 0 and os.path.exists(os.path.join(sc, "reports", "_review_audit.md")))
check("score_report: two sections scored and tallied", "scored sections: **2**" in srep)
check("score_report: below-threshold section b shown (completeness)",
      "below-threshold sections: **1**" in srep and "| b |" in srep.split("Below-threshold sections")[1])
r = run_sr(sc, "--strict")
check("score_report: --strict exits 1 when below threshold", r.returncode != 0 and "below" in (r.stdout + r.stderr))

# all at or above threshold → --strict passes
sc2 = tempfile.mkdtemp()
os.makedirs(os.path.join(sc2, "reports"))
open(os.path.join(sc2, "PRD.md"), "w").write("# A\n\n본문\n")
open(os.path.join(sc2, "pm-policy.yaml"), "w").write(
    "review_audit:\n  scoring: {primary_axes: [completeness, coherence, clarity, depth], scale: {pass_threshold: 3}}\n")
open(os.path.join(sc2, "manifest.yaml"), "w").write(
    "project: {doc_type: PRD, product: P, title: P, ssot: PRD.md, policy: ./pm-policy.yaml, output_dir: outputs}\n"
    "sections:\n  - {id: a, title: \"A\", status: approved, sources: [k], scores: {completeness: 4, coherence: 4, clarity: 4, depth: 4}}\n")
r = run_sr(sc2, "--strict")
check("score_report: --strict passes when all above threshold", r.returncode == 0)

# ── validate_manifest: optional scores/verbatim ──
# valid: scores (integers) + verbatim (source/quotes)
m_ok = {
    "project": {"product": "P", "ssot": "x.md"},
    "sections": [{"id": "a", "title": "A", "status": "approved", "sources": ["k"],
                  "scores": {"completeness": 4, "coherence": 3, "clarity": 5, "depth": 3}}],
    "verbatim": [{"source": "inputs/o.md", "quotes": ["인용1"]}],
}
E, W = V.validate(m_ok)
check("validate: scores/verbatim valid (0 errors)", E == [])

# scores non-integer → error
m_bad = {"project": {"product": "P", "ssot": "x.md"},
         "sections": [{"id": "a", "title": "A", "status": "approved", "sources": ["k"],
                       "scores": {"completeness": "high"}}]}
E, W = V.validate(m_bad)
check("validate: scores non-integer raises error", any("scores.completeness" in e for e in E))

# verbatim missing source → error
m_bad2 = {"project": {"product": "P", "ssot": "x.md"},
          "sections": [{"id": "a", "title": "A", "status": "approved", "sources": ["k"]}],
          "verbatim": [{"quotes": ["x"]}]}
E, W = V.validate(m_bad2)
check("validate: verbatim missing source raises error", any("source missing" in e for e in E))

# no scores/verbatim → passes (backward compat)
m_none = {"project": {"product": "P", "ssot": "x.md"},
          "sections": [{"id": "a", "title": "A", "status": "approved", "sources": ["k"]}]}
E, W = V.validate(m_none)
check("validate: no scores/verbatim passes (backward compat)", E == [])

# ── validate_manifest: review_audit application tracking (backport) ──
# valid: each pending_apply/applied decision_id exists in decisions[]
m_ra_ok = {"project": {"product": "P", "ssot": "x.md"},
           "sections": [{"id": "a", "title": "A", "status": "approved", "sources": ["k"]}],
           "decisions": [{"id": "d1", "decision": "x"}, {"id": "d2", "decision": "y"}],
           "review_audit": {"pending_apply": [{"decision_id": "d2", "doc": "P.md", "note": "미반영"}],
                            "applied": [{"decision_id": "d1", "verified_at": "2026-06-03"}]}}
E, W = V.validate(m_ra_ok)
check("validate: review_audit pending_apply/applied valid (0 errors)", E == [])

# decision_id missing → error (Codex#5 traceability)
m_ra_bad = {"project": {"product": "P", "ssot": "x.md"},
            "sections": [{"id": "a", "title": "A", "status": "approved", "sources": ["k"]}],
            "review_audit": {"pending_apply": [{"doc": "P.md"}]}}
E, W = V.validate(m_ra_bad)
check("validate: pending_apply missing decision_id raises error", any("decision_id required" in e for e in E))

# decision_id whitespace-only → error (peer r1#1 strip)
m_ra_blank = {"project": {"product": "P", "ssot": "x.md"},
              "sections": [{"id": "a", "title": "A", "status": "approved", "sources": ["k"]}],
              "decisions": [{"id": "d1", "decision": "x"}],
              "review_audit": {"applied": [{"decision_id": "  "}]}}
E, W = V.validate(m_ra_blank)
check("validate: whitespace-only decision_id raises error", any("decision_id required" in e for e in E))

# dangling: decision_id not in decisions[] → error (peer r1#1 referential integrity)
m_ra_dangling = {"project": {"product": "P", "ssot": "x.md"},
                 "sections": [{"id": "a", "title": "A", "status": "approved", "sources": ["k"]}],
                 "decisions": [{"id": "d1", "decision": "x"}],
                 "review_audit": {"pending_apply": [{"decision_id": "dZ", "doc": "P.md"}]}}
E, W = V.validate(m_ra_dangling)
check("validate: dangling decision_id raises error", any("dangling" in e for e in E))

# duplicate decision_id within list → error (peer r1#4)
m_ra_dup = {"project": {"product": "P", "ssot": "x.md"},
            "sections": [{"id": "a", "title": "A", "status": "approved", "sources": ["k"]}],
            "decisions": [{"id": "d1", "decision": "x"}],
            "review_audit": {"applied": [{"decision_id": "d1"}, {"decision_id": "d1"}]}}
E, W = V.validate(m_ra_dup)
check("validate: duplicate decision_id in applied raises error", any("duplicate" in e for e in E))

# pending_apply ↔ applied intersection → warning (peer r1#4, not an error)
m_ra_cross = {"project": {"product": "P", "ssot": "x.md"},
              "sections": [{"id": "a", "title": "A", "status": "approved", "sources": ["k"]}],
              "decisions": [{"id": "d1", "decision": "x"}],
              "review_audit": {"pending_apply": [{"decision_id": "d1"}], "applied": [{"decision_id": "d1"}]}}
E, W = V.validate(m_ra_cross)
check("validate: pending_apply↔applied intersection warns (not an error)",
      E == [] and any("both" in w for w in W))

# no review_audit → passes (backward compat)
E, W = V.validate(m_none)
check("validate: no review_audit passes (backward compat)", E == [])

# ── gap_audit.py: pending_apply gate (backport) ──
def run_ga(cwd, *args):
    return subprocess.run([sys.executable, os.path.join(SCRIPTS, "gap_audit.py"), "manifest.yaml", *args],
                          cwd=cwd, capture_output=True, text=True)


# pending_apply non-empty → shown in report + --strict exit 1
ga = tempfile.mkdtemp()
open(os.path.join(ga, "manifest.yaml"), "w").write(
    "project: {doc_type: PRD, product: P, title: P, ssot: PRD.md, output_dir: outputs}\n"
    "sections:\n  - {id: a, title: \"A\", status: approved, sources: [k]}\n"
    "decisions:\n  - {id: d2, date: 2026-06-02, decision: \"전화번호 optional\", by: \"리드\"}\n"
    "review_audit:\n  pending_apply:\n    - {decision_id: d2, doc: PRD.md, note: \"본문 미반영\"}\n")
r = run_ga(ga)
grep = open(os.path.join(ga, "reports", "_gap_report.md"), encoding="utf-8").read()
check("gap_audit: pending_apply shown in report", r.returncode == 0 and "| d2 |" in grep and "pending_apply (unapplied): **1**" in grep)
r = run_ga(ga, "--strict")
check("gap_audit: --strict exits 1 when pending_apply non-empty", r.returncode != 0 and "pending_apply" in (r.stdout + r.stderr))

# pending_apply empty (applied only) → --strict passes (backward compat: no gaps/open/pending)
ga2 = tempfile.mkdtemp()
open(os.path.join(ga2, "manifest.yaml"), "w").write(
    "project: {doc_type: PRD, product: P, title: P, ssot: PRD.md, output_dir: outputs}\n"
    "sections:\n  - {id: a, title: \"A\", status: approved, sources: [k]}\n"
    "decisions:\n  - {id: d1, date: 2026-06-01, decision: \"확정\", by: \"리드\"}\n"
    "review_audit:\n  applied:\n    - {decision_id: d1, verified_at: 2026-06-03}\n")
r = run_ga(ga2, "--strict")
check("gap_audit: --strict passes when pending_apply empty", r.returncode == 0)

# no review_audit key → existing behavior (backward compat) — pending_apply count 0
ga3 = tempfile.mkdtemp()
open(os.path.join(ga3, "manifest.yaml"), "w").write(
    "project: {doc_type: PRD, product: P, title: P, ssot: PRD.md, output_dir: outputs}\n"
    "sections:\n  - {id: a, title: \"A\", status: approved, sources: [k]}\n")
r = run_ga(ga3, "--strict")
check("gap_audit: --strict passes with no review_audit key (backward compat)", r.returncode == 0)

# ── gap_audit.py: cross-audit coverage honesty guard (silent-omission fix) ──
# 0 sources/downstream + drafted section → warn that gaps==0 is internal-only, NOT clean.
ga_blind = tempfile.mkdtemp()
open(os.path.join(ga_blind, "manifest.yaml"), "w").write(
    "project: {doc_type: PRD, product: P, title: P, ssot: PRD.md, output_dir: outputs}\n"
    "sections:\n  - {id: a, title: \"A\", status: approved, sources: [k]}\n")
r = run_ga(ga_blind)
rep = open(os.path.join(ga_blind, "reports", "_gap_report.md"), encoding="utf-8").read()
check("gap_audit: 0 sources+drafted → cross-blind warning in report",
      "Cross-consistency not run" in rep and "**0** source path(s) + **0** downstream target(s)" in rep)
check("gap_audit: cross-blind warning printed to stderr", "cross-consistency not run" in r.stderr)
r = run_ga(ga_blind, "--strict")
check("gap_audit: cross-blind does not fail --strict (internal-only is valid)", r.returncode == 0)

# sources registered → no warning
ga_src = tempfile.mkdtemp()
open(os.path.join(ga_src, "manifest.yaml"), "w").write(
    "project:\n  doc_type: PRD\n  product: P\n  title: P\n  ssot: PRD.md\n  output_dir: outputs\n"
    "  sources: {code_roots: [\"~/code/src\"]}\n"
    "sections:\n  - {id: a, title: \"A\", status: approved, sources: [k]}\n")
r = run_ga(ga_src)
rep = open(os.path.join(ga_src, "reports", "_gap_report.md"), encoding="utf-8").read()
check("gap_audit: sources registered → no cross-blind warning + coverage 1",
      "Cross-consistency not run" not in rep and "**1** source path(s)" in rep)

# 0 sources but all pending (grounded 0) → no misleading context → no warning
ga_pend = tempfile.mkdtemp()
open(os.path.join(ga_pend, "manifest.yaml"), "w").write(
    "project: {doc_type: PRD, product: P, title: P, ssot: PRD.md, output_dir: outputs}\n"
    "sections:\n  - {id: a, title: \"A\", status: pending}\n")
r = run_ga(ga_pend)
rep = open(os.path.join(ga_pend, "reports", "_gap_report.md"), encoding="utf-8").read()
check("gap_audit: 0 sources + all pending → no cross-blind warning (no misleading context)",
      "Cross-consistency not run" not in rep)

# downstream only registered (0 sources) + draft section → no warning, coverage counts downstream (peer r1 test-gap)
ga_ds = tempfile.mkdtemp()
open(os.path.join(ga_ds, "manifest.yaml"), "w").write(
    "project:\n  doc_type: PRD\n  product: P\n  title: P\n  ssot: PRD.md\n  output_dir: outputs\n"
    "  downstream: {storyboard: ../sb.html}\n"
    "sections:\n  - {id: a, title: \"A\", status: draft, sources: [k]}\n")
r = run_ga(ga_ds)
rep = open(os.path.join(ga_ds, "reports", "_gap_report.md"), encoding="utf-8").read()
check("gap_audit: downstream only + draft → no warning + coverage downstream 1",
      "Cross-consistency not run" not in rep and "**0** source path(s) + **1** downstream target(s)" in rep)

# empty list value → coverage 0 → cross-blind warning (peer r1 test-gap)
ga_empty = tempfile.mkdtemp()
open(os.path.join(ga_empty, "manifest.yaml"), "w").write(
    "project:\n  doc_type: PRD\n  product: P\n  title: P\n  ssot: PRD.md\n  output_dir: outputs\n"
    "  sources: {code_roots: []}\n"
    "sections:\n  - {id: a, title: \"A\", status: approved, sources: [k]}\n")
r = run_ga(ga_empty)
rep = open(os.path.join(ga_empty, "reports", "_gap_report.md"), encoding="utf-8").read()
check("gap_audit: empty list source → coverage 0 + cross-blind warning",
      "**0** source path(s)" in rep and "Cross-consistency not run" in rep)

# ── gap_audit.py: --strict-cross-audit opt-in (cross-blind treated as gate failure) ──
# cross-blind (0 sources + drafted) + --strict-cross-audit → exit 1
ga_xca = tempfile.mkdtemp()
open(os.path.join(ga_xca, "manifest.yaml"), "w").write(
    "project: {doc_type: PRD, product: P, title: P, ssot: PRD.md, output_dir: outputs}\n"
    "sections:\n  - {id: a, title: \"A\", status: approved, sources: [k]}\n")
r = run_ga(ga_xca, "--strict-cross-audit")
check("gap_audit: --strict-cross-audit + cross-blind → exit 1",
      r.returncode != 0 and "cross-audit not run" in (r.stdout + r.stderr))
# same manifest with plain --strict → passes (existing behavior unchanged)
r = run_ga(ga_xca, "--strict")
check("gap_audit: plain --strict passes cross-blind (backward compat unchanged)", r.returncode == 0)

# sources registered → --strict-cross-audit also passes (not cross-blind)
ga_xca2 = tempfile.mkdtemp()
open(os.path.join(ga_xca2, "manifest.yaml"), "w").write(
    "project:\n  doc_type: PRD\n  product: P\n  title: P\n  ssot: PRD.md\n  output_dir: outputs\n"
    "  sources: {code_roots: [\"~/code/src\"]}\n"
    "sections:\n  - {id: a, title: \"A\", status: approved, sources: [k]}\n")
r = run_ga(ga_xca2, "--strict-cross-audit")
check("gap_audit: --strict-cross-audit passes when sources registered", r.returncode == 0)

# ── bin/docloop gate wrapper: flag passthrough + manifest-default contract (peer r1 LOW) ──
BIN = os.path.join(os.path.dirname(SCRIPTS), "bin", "docloop")


def run_gate(cwd, *args):
    return subprocess.run(["bash", BIN, "gate", *args], cwd=cwd, capture_output=True, text=True)


# cross-blind manifest at default path; flag-first must NOT be consumed as the manifest path
gw = tempfile.mkdtemp()
open(os.path.join(gw, "manifest.yaml"), "w").write(
    "project: {doc_type: PRD, product: P, title: P, ssot: PRD.md, output_dir: outputs}\n"
    "sections:\n  - {id: a, title: \"A\", status: approved, sources: [k]}\n")
r = run_gate(gw, "--strict-cross-audit")
check("docloop gate: flag-first forwards + defaults manifest.yaml → cross-blind exit 1",
      r.returncode != 0 and "cross-audit not run" in (r.stdout + r.stderr))
r = run_gate(gw)
check("docloop gate: plain gate (no flag) passes cross-blind", r.returncode == 0)
r = run_gate(gw, "manifest.yaml", "--strict-cross-audit")
check("docloop gate: explicit manifest + flag → cross-blind exit 1",
      r.returncode != 0 and "cross-audit not run" in (r.stdout + r.stderr))

print(f"\n=== {_passed} passed, {_failed} failed ===")
sys.exit(1 if _failed else 0)
