#!/usr/bin/env python3
"""docloop regression tests — split.py (deploy split) + approval_brief.py + validate_manifest sanity.
Usage: python3 tests/run_tests.py"""
import sys, os, tempfile, subprocess, signal, time, stat, json, hashlib, threading, yaml

SCRIPTS = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "lib")
sys.path.insert(0, SCRIPTS)
import validate_manifest as V  # noqa: E402
import split as SP             # noqa: E402
import approval_brief as AB    # noqa: E402
import verbatim_check as VC    # noqa: E402
import score_report as SR      # noqa: E402
import contribution_flow as CF # noqa: E402

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

# ── #5 (ported from pm-authoring): top-level scoring.rubric.scale/weights; legacy fixtures above lock the fallback ──
def _mk_sr(policy_yaml, sections_yaml):
    d = tempfile.mkdtemp()
    os.makedirs(os.path.join(d, "reports"))
    open(os.path.join(d, "PRD.md"), "w").write("# A\n\n본문\n")
    open(os.path.join(d, "pm-policy.yaml"), "w").write(policy_yaml)
    open(os.path.join(d, "manifest.yaml"), "w").write(
        "project: {doc_type: PRD, product: P, title: P, ssot: PRD.md, policy: ./pm-policy.yaml, output_dir: outputs}\n"
        "sections:\n" + sections_yaml)
    return d

# new path works (discriminating): pass_threshold=4 with NO legacy — a score of 3 fails ONLY if the
#   top-level scoring.rubric.scale is actually read (default threshold 3 would pass, proving nothing).
snp = _mk_sr(
    "scoring:\n"
    "  primary_axes: [completeness, coherence, clarity, depth]\n"
    "  rubric: {scale: {min: 1, max: 5, pass_threshold: 4}, weights: {regulatory: 3, coherence: 2}}\n",
    "  - {id: a, title: \"A\", status: approved, sources: [k], scores: {completeness: 3, coherence: 4, clarity: 4, depth: 4}}\n")
r = run_sr(snp, "--strict")
check("score_report(#5): top-level scoring.rubric.scale pass_threshold=4 read (score 3 below) → --strict exit 1",
      r.returncode != 0 and "below" in (r.stdout + r.stderr))

# partial migration: top-level scoring present but no rubric.scale → field-merge keeps legacy review_audit.scale=4.
#   (a block `top or old` would pick top(truthy)→scale={}→default thr 3→pass at 3; field-merge → thr 4 → block.)
smix = _mk_sr(
    "scoring:\n"
    "  primary_axes: [completeness, coherence, clarity, depth]\n"
    "review_audit:\n"
    "  scoring: {scale: {pass_threshold: 4}}\n",
    "  - {id: a, title: \"A\", status: approved, sources: [k], scores: {completeness: 3, coherence: 3, clarity: 3, depth: 3}}\n")
r = run_sr(smix, "--strict")
check("score_report(#5,partial): no rubric.scale → legacy scale.pass_threshold=4 honored, 3 below → exit 1", r.returncode != 0)

# coexistence precedence: top rubric.scale=4 wins over legacy review_audit.scale=3
sboth = _mk_sr(
    "scoring:\n"
    "  primary_axes: [completeness, coherence, clarity, depth]\n"
    "  rubric: {scale: {pass_threshold: 4}}\n"
    "review_audit:\n"
    "  scoring: {scale: {pass_threshold: 3}}\n",
    "  - {id: a, title: \"A\", status: approved, sources: [k], scores: {completeness: 3, coherence: 3, clarity: 3, depth: 3}}\n")
r = run_sr(sboth, "--strict")
check("score_report(#5,coexist): top rubric.scale=4 > legacy scale=3 → 3 below → exit 1", r.returncode != 0)

# weight ordering: regulatory flag(+3) > coherence axis-weight(2) > unweighted(1), via report row order
sord = _mk_sr(
    "scoring:\n"
    "  primary_axes: [completeness, coherence, clarity, depth]\n"
    "  rubric: {scale: {pass_threshold: 3}, weights: {regulatory: 3, coherence: 2}}\n",
    "  - {id: plain, title: \"Plain\", status: approved, sources: [k], scores: {completeness: 2, coherence: 4, clarity: 4, depth: 4}}\n"
    "  - {id: coh,   title: \"Coh\",   status: approved, sources: [k], scores: {completeness: 4, coherence: 2, clarity: 4, depth: 4}}\n"
    "  - {id: reg,   title: \"Reg\", regulatory: true, status: approved, sources: [k], scores: {completeness: 2, coherence: 4, clarity: 4, depth: 4}}\n")
r = run_sr(sord)
srep = open(os.path.join(sord, "reports", "_review_audit.md"), encoding="utf-8").read()
_MARK = "Per-section scores"   # guard: if the report header ever changes, fail as a check, not an IndexError crash
check("score_report(#5,weights): report has per-section score table", _MARK in srep)
tbl = srep.split(_MARK, 1)[1] if _MARK in srep else ""
i_reg, i_coh, i_plain = tbl.find("| reg |"), tbl.find("| coh |"), tbl.find("| plain |")
check("score_report(#5,weights): priority sort regulatory(+3) > coherence-axis(2) > unweighted(1)",
      -1 < i_reg < i_coh < i_plain)

# explicit empty weights on the new path is honored (does not leak legacy priority_rubric)
sew = _mk_sr(
    "scoring:\n"
    "  primary_axes: [completeness, coherence, clarity, depth]\n"
    "  rubric: {scale: {pass_threshold: 3}, weights: {}}\n"
    "review_audit:\n"
    "  priority_rubric: {weights: {regulatory: 3}}\n",
    "  - {id: plain, title: \"Plain\", status: approved, sources: [k], scores: {completeness: 2, coherence: 4, clarity: 4, depth: 4}}\n"
    "  - {id: reg,   title: \"Reg\", regulatory: true, status: approved, sources: [k], scores: {completeness: 2, coherence: 4, clarity: 4, depth: 4}}\n")
r = run_sr(sew)
srep = open(os.path.join(sew, "reports", "_review_audit.md"), encoding="utf-8").read()
check("score_report(#5,empty-weights): report has per-section score table", _MARK in srep)
tbl = srep.split(_MARK, 1)[1] if _MARK in srep else ""
check("score_report(#5,empty-weights): top weights:{} honored — legacy priority_rubric not leaked (plain<reg)",
      -1 < tbl.find("| plain |") < tbl.find("| reg |"))

# scalar rubric ref (contract-allowed) must not crash → falls back to legacy scale.pass_threshold=4
sref = _mk_sr(
    "scoring:\n"
    "  primary_axes: [completeness, coherence, clarity, depth]\n"
    "  rubric: ./external-rubric.yaml\n"
    "review_audit:\n"
    "  scoring: {scale: {pass_threshold: 4}}\n",
    "  - {id: a, title: \"A\", status: approved, sources: [k], scores: {completeness: 3, coherence: 3, clarity: 3, depth: 3}}\n")
r = run_sr(sref, "--strict")
check("score_report(#5,scalar-ref): rubric scalar ref does not crash → legacy scale=4 fallback, 3 below → exit 1",
      r.returncode != 0 and "Traceback" not in (r.stdout + r.stderr))

# partial scale key-merge: top rubric.scale has min/max only (no threshold) → legacy pass_threshold=4 survives
spsc = _mk_sr(
    "scoring:\n"
    "  primary_axes: [completeness, coherence, clarity, depth]\n"
    "  rubric: {scale: {min: 0, max: 10}}\n"
    "review_audit:\n"
    "  scoring: {scale: {pass_threshold: 4}}\n",
    "  - {id: a, title: \"A\", status: approved, sources: [k], scores: {completeness: 3, coherence: 3, clarity: 3, depth: 3}}\n")
r = run_sr(spsc, "--strict")
check("score_report(#5,partial-scale): min/max only → legacy pass_threshold=4 key-merged, 3 below → exit 1", r.returncode != 0)

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

# downstream only registered (0 sources, real file) + draft section → no warning, coverage counts downstream (peer r1 test-gap)
ga_ds = tempfile.mkdtemp()
open(os.path.join(ga_ds, "sb.html"), "w").write("<html><body data-screen-id='S-01'></body></html>\n")
open(os.path.join(ga_ds, "manifest.yaml"), "w").write(
    "project:\n  doc_type: PRD\n  product: P\n  title: P\n  ssot: PRD.md\n  output_dir: outputs\n"
    "  downstream: {storyboard: ./sb.html}\n"
    "sections:\n  - {id: a, title: \"A\", status: draft, sources: [k]}\n")
r = run_ga(ga_ds)
rep = open(os.path.join(ga_ds, "reports", "_gap_report.md"), encoding="utf-8").read()
check("gap_audit: downstream only + draft → no warning + coverage downstream 1",
      "Cross-consistency not run" not in rep and "**0** source path(s) + **1** downstream target(s)" in rep)
check("gap_audit: readable downstream → no 'could not be read' warning", "could not be read" not in rep)

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

# typo/unknown source key → NOT counted as coverage → cross-blind still warns (v0.1.1)
ga_typo = tempfile.mkdtemp()
open(os.path.join(ga_typo, "manifest.yaml"), "w").write(
    "project:\n  doc_type: PRD\n  product: P\n  title: P\n  ssot: PRD.md\n  output_dir: outputs\n"
    "  sources: {code_root: [\"~/code/src\"]}\n"   # typo: code_root (not code_roots)
    "sections:\n  - {id: a, title: \"A\", status: approved, sources: [k]}\n")
r = run_ga(ga_typo)
rep = open(os.path.join(ga_typo, "reports", "_gap_report.md"), encoding="utf-8").read()
check("gap_audit: typo'd source key not counted (coverage 0, cross-blind warns)",
      "**0** source path(s)" in rep and "Cross-consistency not run" in rep)

# ── gap_audit.py: downstream coverage = "readable real files" ──
# The counting basis moves from *number of path strings* to *number of existing regular files*.
# Key names are irrelevant (the typo warning stays), missing files/directories count 0, duplicates count 1.
def _mk_ga(project_extra, files=(), dirs=(), status="approved"):
    d = tempfile.mkdtemp()
    for f in files:
        p = os.path.join(d, f)
        os.makedirs(os.path.dirname(p), exist_ok=True)
        open(p, "w").write("x\n")
    for sub in dirs:
        os.makedirs(os.path.join(d, sub), exist_ok=True)
    open(os.path.join(d, "manifest.yaml"), "w").write(
        "project:\n  doc_type: PRD\n  product: P\n  title: P\n  ssot: PRD.md\n  output_dir: outputs\n"
        + project_extra +
        f"sections:\n  - {{id: a, title: \"A\", status: {status}, sources: [k]}}\n")
    return d


def _rep(d):
    return open(os.path.join(d, "reports", "_gap_report.md"), encoding="utf-8").read()


# (1) unknown/typo'd key + a real file → counted. The typo warning stays
#     (the allowlist is typo defence, not a counting restriction).
ga_unknown = _mk_ga("  downstream: {storyboard_html: ./sb.html}\n", files=["sb.html"])
r = run_ga(ga_unknown)
rep = _rep(ga_unknown)
check("gap_audit: unregistered downstream key + real file → counted 1 (key name irrelevant)",
      "**1** downstream target(s)" in rep and "Cross-consistency not run" not in rep)
check("gap_audit: unknown downstream key still warns (counted, but warned)",
      "unknown key 'storyboard_html'" in r.stderr)

# (2) registered but the file doesn't exist → count 0 → cross-blind warning + --strict-cross-audit failure
#     (counting declarations would make coverage 1 with no file and defeat the warning/gate)
ga_missing = _mk_ga("  downstream: {storyboard: ./gone.html}\n")
r = run_ga(ga_missing)
rep = _rep(ga_missing)
check("gap_audit: registered downstream with no file → count 0 + cross-blind warning",
      "**0** downstream target(s)" in rep and "Cross-consistency not run" in rep)
r = run_ga(ga_missing, "--strict-cross-audit")
check("gap_audit: missing downstream file fails --strict-cross-audit",
      r.returncode != 0 and "cross-audit not run" in (r.stdout + r.stderr))

# (3) same file registered twice (different spellings) → counted 1 (no inflation)
ga_dup = _mk_ga("  downstream:\n    storyboard: ./sb.html\n    policy_docs: [sb.html, ./sb.html]\n",
                files=["sb.html"])
r = run_ga(ga_dup)
check("gap_audit: duplicate downstream paths → counted 1", "**1** downstream target(s)" in _rep(ga_dup))

# (4) directory path → count 0 (regular files only — a directory is not a cross-check target file)
ga_dir = _mk_ga("  downstream: {policy_docs: ./policies}\n", dirs=["policies"])
r = run_ga(ga_dir)
rep = _rep(ga_dir)
check("gap_audit: downstream directory path → count 0 + cross-blind warning",
      "**0** downstream target(s)" in rep and "Cross-consistency not run" in rep)

# (5) regression: the three known keys · str + list[str] · all real files → unchanged (3),
#     with relative paths resolved against the manifest file (not cwd)
ga_legacy = _mk_ga(
    "  downstream:\n    storyboard: sb/case.html\n    manual_manifest: ./manual/manifest.yaml\n"
    "    policy_docs: [\"docs/../docs/policy.md\"]\n",
    files=["sb/case.html", "manual/manifest.yaml", "docs/policy.md"])
r = subprocess.run([sys.executable, os.path.join(SCRIPTS, "gap_audit.py"),
                    os.path.join(ga_legacy, "manifest.yaml")],
                   cwd=tempfile.mkdtemp(), capture_output=True, text=True)   # cwd-independent
rep = _rep(ga_legacy)
check("gap_audit: three known keys (str+list, 3 real files) → counted 3 (regression, cwd-independent)",
      r.returncode == 0 and "**3** downstream target(s)" in rep and "Cross-consistency not run" not in rep)

# (6) sources registered (aggregate > 0) but a registered downstream can't be read →
#     surfaced per item in the report + coverage line + stderr, and fails --strict-cross-audit.
#     Counting alone would leave this silent: n_src + n_ds > 0 means cross_blind never fires.
ga_mix = _mk_ga("  sources: {code_roots: [\"./code\"]}\n  downstream: {storyboard: ./vanished.html}\n",
                files=["code/a.py"])
r = run_ga(ga_mix, "--strict-cross-audit")
rep = _rep(ga_mix)
check("gap_audit: unreadable downstream surfaced even when sources exist",
      "could not be read" in rep and "vanished.html" in rep)
check("gap_audit: unreadable downstream also shown on the coverage line",
      "⚠️ 1 unreadable downstream" in rep)
check("gap_audit: unreadable downstream warns on stderr",
      "1 registered downstream target(s) unreadable" in r.stderr)
check("gap_audit: unreadable downstream fails --strict-cross-audit even with sources",
      r.returncode != 0 and "downstream unreadable" in (r.stdout + r.stderr))
check("gap_audit: unreadable downstream is not cross-blind (aggregate > 0 → separate warning)",
      "Cross-consistency not run" not in rep)

# (7) wording: with 0 cross-check targets the coverage line says "0 cross-check targets", not "none registered"
#     (one entry was registered — "none registered" reads as a literal falsehood)
ga_word = _mk_ga("  downstream: {storyboard: ./gone.html}\n")
r = run_ga(ga_word)
rep = _rep(ga_word)
check("gap_audit: coverage line says '0 cross-check targets', not 'none registered'",
      "⚠️ 0 cross-check targets" in rep and "none registered" not in rep)

# ── silent-omission hardening (v0.1.2): verbatim_check + score_report ──
# verbatim: 0 quotes → --strict passes but warns "nothing verified" (vacuous-pass guard)
vb_blind = tempfile.mkdtemp()
os.makedirs(os.path.join(vb_blind, "inputs")); os.makedirs(os.path.join(vb_blind, "reports"))
open(os.path.join(vb_blind, "inputs", "orig.md"), "w").write("source text\n")
open(os.path.join(vb_blind, "PRD.md"), "w").write("# Body\n\nno blockquotes here\n")
open(os.path.join(vb_blind, "pm-policy.yaml"), "w").write(
    "review_audit:\n  verbatim: {enabled: true, targets: [\"inputs/orig.md\"]}\n")
open(os.path.join(vb_blind, "manifest.yaml"), "w").write(
    "project: {doc_type: PRD, product: P, title: P, ssot: PRD.md, policy: ./pm-policy.yaml, output_dir: outputs}\n"
    "sections:\n  - {id: a, title: \"Body\", status: approved, sources: [k]}\n")
r = run_vc(vb_blind, "--strict")
rep = open(os.path.join(vb_blind, "reports", "_verbatim_report.md"), encoding="utf-8").read()
check("verbatim: 0 quotes → --strict passes but warns nothing verified",
      r.returncode == 0 and "Nothing verified" in rep and "verified nothing" in r.stderr)

# verbatim: a missing FIRST source must not shift the matched-source label (zip-misalign bug)
vb_mis = tempfile.mkdtemp()
os.makedirs(os.path.join(vb_mis, "inputs")); os.makedirs(os.path.join(vb_mis, "reports"))
open(os.path.join(vb_mis, "inputs", "present.md"), "w").write("the canonical sentence\n")
open(os.path.join(vb_mis, "PRD.md"), "w").write("# Body\n\n> the canonical sentence\n")
open(os.path.join(vb_mis, "manifest.yaml"), "w").write(
    "project: {doc_type: PRD, product: P, title: P, ssot: PRD.md, output_dir: outputs}\n"
    "verbatim:\n  - {source: inputs/missing.md}\n  - {source: inputs/present.md}\n"
    "sections:\n  - {id: a, title: \"Body\", status: approved, sources: [k]}\n")
r = run_vc(vb_mis, "--strict")
rep = open(os.path.join(vb_mis, "reports", "_verbatim_report.md"), encoding="utf-8").read()
check("verbatim: missing first source doesn't mislabel match (FULL→present.md)",
      r.returncode == 0 and "(FULL) **1**" in rep
      and "| FULL | inputs/present.md |" in rep          # matched-source column pinned
      and "| FULL | inputs/missing.md |" not in rep)      # the old zip-misalign bug

# score: sections exist but none scored → --strict passes but warns "nothing scored"
sc_blind = tempfile.mkdtemp(); os.makedirs(os.path.join(sc_blind, "reports"))
open(os.path.join(sc_blind, "PRD.md"), "w").write("# A\n\nbody\n")
open(os.path.join(sc_blind, "manifest.yaml"), "w").write(
    "project: {doc_type: PRD, product: P, title: P, ssot: PRD.md, output_dir: outputs}\n"
    "sections:\n  - {id: a, title: \"A\", status: approved, sources: [k]}\n")
r = run_sr(sc_blind, "--strict")
rep = open(os.path.join(sc_blind, "reports", "_review_audit.md"), encoding="utf-8").read()
check("score_report: 0 scored sections → --strict passes but warns nothing scored",
      r.returncode == 0 and "Nothing scored" in rep and "scoring not run" in r.stderr)

# score: a scored section missing axes → incomplete warned (not silently passing)
sc_inc = tempfile.mkdtemp(); os.makedirs(os.path.join(sc_inc, "reports"))
open(os.path.join(sc_inc, "PRD.md"), "w").write("# A\n\nbody\n")
open(os.path.join(sc_inc, "pm-policy.yaml"), "w").write(
    "review_audit:\n  scoring: {primary_axes: [completeness, coherence, clarity, depth], scale: {pass_threshold: 3}}\n")
open(os.path.join(sc_inc, "manifest.yaml"), "w").write(
    "project: {doc_type: PRD, product: P, title: P, ssot: PRD.md, policy: ./pm-policy.yaml, output_dir: outputs}\n"
    "sections:\n  - {id: a, title: \"A\", status: approved, sources: [k], scores: {completeness: 4, coherence: 4}}\n")
r = run_sr(sc_inc, "--strict")
rep = open(os.path.join(sc_inc, "reports", "_review_audit.md"), encoding="utf-8").read()
check("score_report: scored section missing axes → incomplete warned (clarity/depth)",
      r.returncode == 0 and "Incomplete scoring" in rep and "incomplete scoring" in r.stderr and "clarity, depth" in rep)

# verify_blind other half: quotes present but ALL sources missing → blind warn + --strict fails via MISS
vb_nosrc = tempfile.mkdtemp()
os.makedirs(os.path.join(vb_nosrc, "inputs")); os.makedirs(os.path.join(vb_nosrc, "reports"))
open(os.path.join(vb_nosrc, "PRD.md"), "w").write("# Body\n\n> a quoted sentence\n")
open(os.path.join(vb_nosrc, "manifest.yaml"), "w").write(
    "project: {doc_type: PRD, product: P, title: P, ssot: PRD.md, output_dir: outputs}\n"
    "verbatim:\n  - {source: inputs/gone.md}\n"
    "sections:\n  - {id: a, title: \"Body\", status: approved, sources: [k]}\n")
r = run_vc(vb_nosrc, "--strict")
rep = open(os.path.join(vb_nosrc, "reports", "_verbatim_report.md"), encoding="utf-8").read()
check("verbatim: quotes present but all sources missing → blind warn + --strict fails (MISS)",
      r.returncode != 0 and "Nothing verified" in rep and "verified nothing" in r.stderr)

# ── opt-in coverage-fail flags (v0.2.0): a vacuous gate becomes a failure ──
# verbatim: --strict-verbatim-coverage fails where plain --strict passed (0 quotes)
r = run_vc(vb_blind, "--strict-verbatim-coverage")
check("verbatim: --strict-verbatim-coverage fails on 0 quotes (vacuous)",
      r.returncode != 0 and "nothing verifiable" in (r.stdout + r.stderr))
# verifiable input (quote matches a present source) → coverage flag passes
r = run_vc(vb_mis, "--strict-verbatim-coverage")
check("verbatim: --strict-verbatim-coverage passes when quotes are verifiable", r.returncode == 0)
# coverage flag inherits --strict (a MISS still fails)
r = run_vc(vb, "--strict-verbatim-coverage")
check("verbatim: --strict-verbatim-coverage inherits --strict (MISS still fails)",
      r.returncode != 0 and "MISS" in (r.stdout + r.stderr))

# score: --strict-scoring-coverage fails on 0 scored and on incomplete (unscored axes)
r = run_sr(sc_blind, "--strict-scoring-coverage")
check("score_report: --strict-scoring-coverage fails on 0 scored (vacuous)",
      r.returncode != 0 and "nothing scored" in (r.stdout + r.stderr))
r = run_sr(sc_inc, "--strict-scoring-coverage")
check("score_report: --strict-scoring-coverage fails on unscored axes (incomplete)",
      r.returncode != 0 and "unscored axes" in (r.stdout + r.stderr))
# fully scored & above threshold → coverage flag passes
r = run_sr(sc2, "--strict-scoring-coverage")
check("score_report: --strict-scoring-coverage passes when fully scored & above threshold", r.returncode == 0)

# coverage's other vacuous half: quotes present but 0 readable sources → "nothing verifiable" (peer r1 LOW)
r = run_vc(vb_nosrc, "--strict-verbatim-coverage")
check("verbatim: --strict-verbatim-coverage fails on 0 readable sources (vacuous)",
      r.returncode != 0 and "nothing verifiable" in (r.stdout + r.stderr))

# score coverage inherits --strict: fully scored but below threshold still fails (peer r1 LOW)
sc_below = tempfile.mkdtemp(); os.makedirs(os.path.join(sc_below, "reports"))
open(os.path.join(sc_below, "PRD.md"), "w").write("# A\n\nbody\n")
open(os.path.join(sc_below, "pm-policy.yaml"), "w").write(
    "review_audit:\n  scoring: {primary_axes: [completeness, coherence, clarity, depth], scale: {pass_threshold: 3}}\n")
open(os.path.join(sc_below, "manifest.yaml"), "w").write(
    "project: {doc_type: PRD, product: P, title: P, ssot: PRD.md, policy: ./pm-policy.yaml, output_dir: outputs}\n"
    "sections:\n  - {id: a, title: \"A\", status: approved, sources: [k], scores: {completeness: 2, coherence: 4, clarity: 4, depth: 4}}\n")
r = run_sr(sc_below, "--strict-scoring-coverage")
check("score_report: --strict-scoring-coverage inherits --strict (below-threshold still fails)",
      r.returncode != 0 and "below pass_threshold" in (r.stdout + r.stderr))

# ══════════════ change-plan mode (as-is/to-be): validate + ground_audit ══════════════

def run_gr(cwd, *args):
    return subprocess.run([sys.executable, os.path.join(SCRIPTS, "ground_audit.py"), "manifest.yaml", *args],
                          cwd=cwd, capture_output=True, text=True)


def _w(d, text):
    with open(os.path.join(d, "manifest.yaml"), "w", encoding="utf-8") as f:
        f.write(text)


# ── validate (pure) ──
M_ATB_OK = {
    "project": {"product": "P", "ssot": "x.md", "sources": {"code_roots": ["~/c"]}},
    "observations": [{"id": "o1", "what": "phenomenon", "sources": ["f1"], "kind": "bug", "verified": True}],
    "chunks": [{"id": "c1", "title": "chunk", "members": ["o1"], "order": 1,
                "order_rationale": "risk first", "status": "draft", "asis": "current X", "tobe": "should be Y"}],
}
E, W = V.validate(M_ATB_OK)
check("atb validate: valid change-plan manifest passes (0 errors)", E == [])
check("atb validate: sections/doc_type warnings suppressed in change-plan mode",
      not any("sections is empty" in w or "doc_type not set" in w for w in W))

E, W = V.validate({"project": {"product": "P", "ssot": "x"}, "observations": [{"id": "o1", "what": "w"}],
                   "chunks": [{"id": "c1", "title": "t", "members": ["oX"], "order": 1, "order_rationale": "r", "status": "pending"}]})
check("atb validate: dangling member error", any("dangling" in e for e in E))
E, W = V.validate({"project": {"product": "P", "ssot": "x"},
                   "observations": [{"id": "o1", "what": "w", "verified": "yes"}]})
check("atb validate: verified non-bool error", any("verified must be a bool" in e for e in E))
E, W = V.validate({"project": {"product": "P", "ssot": "x"},
                   "observations": [{"id": "o1", "what": "w", "kind": "typo"}]})
check("atb validate: unknown kind is a warning (not error)", not any("kind" in e for e in E) and any("kind 'typo'" in w for w in W))
E, W = V.validate({"project": {"product": "P", "ssot": "x"}, "observations": [{"id": "o1", "what": "w", "verified": True, "sources": ["s"]}],
                   "chunks": [{"id": "c1", "title": "t", "members": ["o1"], "order": 1, "order_rationale": "  ", "status": "pending"}]})
check("atb validate: blank order_rationale error", any("order_rationale must be a non-empty" in e for e in E))
E, W = V.validate({"project": {"product": "P", "ssot": "x"}, "observations": [{"id": "o1", "what": "w", "verified": True, "sources": ["s"]}],
                   "chunks": [{"id": "c1", "title": "t", "members": [], "order": 1, "order_rationale": "r", "status": "draft", "asis": "a"}]})
check("atb validate: authored chunk with empty members warns", any("no traceable observation" in w for w in W))

# ── ground_audit (disk) ──
ATB_CLEAN = (
    "project: {product: P, ssot: x.md, sources: {code_roots: [\"~/c\"]}}\n"
    "observations:\n  - {id: o1, what: \"phenomenon\", sources: [f1], kind: bug, verified: true}\n"
    "chunks:\n  - {id: c1, title: \"chunk\", members: [o1], order: 1, order_rationale: \"risk first\", status: draft, asis: \"current\", tobe: \"target\"}\n"
)
d = tempfile.mkdtemp(); _w(d, ATB_CLEAN)
r = run_gr(d)
rep = open(os.path.join(d, "reports", "_ground_report.md"), encoding="utf-8").read()
check("atb ground_audit: report generated", r.returncode == 0 and os.path.exists(os.path.join(d, "reports", "_ground_report.md")))
check("atb ground_audit: clean --strict passes", run_gr(d, "--strict").returncode == 0)
check("atb ground_audit: clean --strict-cross-audit passes (sources registered)", run_gr(d, "--strict-cross-audit").returncode == 0)
check("atb ground_audit: order rationale shown in order table", "risk first" in rep)

# verified:true + empty sources member of authored chunk → ungrounded → --strict fails
d = tempfile.mkdtemp(); _w(d, (
    "project: {product: P, ssot: x.md, sources: {code_roots: [\"~/c\"]}}\n"
    "observations:\n  - {id: o1, what: \"claimed but no evidence\", sources: [], verified: true}\n"
    "chunks:\n  - {id: c1, title: \"chunk\", members: [o1], order: 1, order_rationale: \"r\", status: draft, asis: \"a\", tobe: \"b\"}\n"))
r = run_gr(d, "--strict")
check("atb ground_audit: --strict blocks verified+empty-sources false-pass", r.returncode != 0 and "ungrounded to-be" in (r.stdout + r.stderr))

# memberless authored chunk → --strict fails
d = tempfile.mkdtemp(); _w(d, (
    "project: {product: P, ssot: x.md, sources: {code_roots: [\"~/c\"]}}\n"
    "observations:\n  - {id: o1, what: \"w\", sources: [s], verified: true}\n"
    "chunks:\n  - {id: c1, title: \"chunk\", members: [], order: 1, order_rationale: \"r\", status: draft, asis: \"a\", tobe: \"b\"}\n"))
r = run_gr(d, "--strict")
check("atb ground_audit: --strict blocks untraceable (memberless) to-be", r.returncode != 0 and "untraceable to-be" in (r.stdout + r.stderr))

# pending chunk → --strict fails; missing rationale/asis → fails
d = tempfile.mkdtemp(); _w(d, (
    "project: {product: P, ssot: x.md, sources: {code_roots: [\"~/c\"]}}\n"
    "observations:\n  - {id: o1, what: \"w\", sources: [s], verified: true}\n"
    "chunks:\n  - {id: c1, title: \"chunk\", members: [o1], order: 1, order_rationale: \"r\", status: pending}\n"))
check("atb ground_audit: --strict blocks pending chunk", run_gr(d, "--strict").returncode != 0)

# cross-blind: 0 sources + authored → warn + --strict passes + --strict-cross-audit fails
d = tempfile.mkdtemp(); _w(d, (
    "project: {product: P, ssot: x.md}\n"
    "observations:\n  - {id: o1, what: \"w\", sources: [s], verified: true}\n"
    "chunks:\n  - {id: c1, title: \"chunk\", members: [o1], order: 1, order_rationale: \"r\", status: draft, asis: \"a\", tobe: \"b\"}\n"))
r = run_gr(d)
rep = open(os.path.join(d, "reports", "_ground_report.md"), encoding="utf-8").read()
check("atb ground_audit: cross-blind warning in report + stderr", "Cross-grounding not run" in rep and "cross-grounding not run" in r.stderr)
check("atb ground_audit: cross-blind not a --strict failure (internal ok)", run_gr(d, "--strict").returncode == 0)
check("atb ground_audit: --strict-cross-audit fails on cross-blind", run_gr(d, "--strict-cross-audit").returncode != 0)

# empty-string source path → coverage 0 (cross-blind kept, not hidden)
d = tempfile.mkdtemp(); _w(d, (
    "project:\n  product: P\n  ssot: x.md\n  sources: {code_roots: [\"\"]}\n"
    "observations:\n  - {id: o1, what: \"w\", sources: [s], verified: true}\n"
    "chunks:\n  - {id: c1, title: \"chunk\", members: [o1], order: 1, order_rationale: \"r\", status: draft, asis: \"a\", tobe: \"b\"}\n"))
r = run_gr(d)
rep = open(os.path.join(d, "reports", "_ground_report.md"), encoding="utf-8").read()
check("atb ground_audit: empty-string source counts 0 (cross-blind kept)", "**0** source path" in rep and "Cross-grounding not run" in rep)

# malformed YAML → clean exit (not a traceback)
d = tempfile.mkdtemp(); _w(d, "project: {product: P, ssot: [broken\n  bad: : :\n")
r = run_gr(d)
check("atb: malformed YAML clean exit (not a traceback)",
      r.returncode != 0 and "YAML syntax error" in (r.stdout + r.stderr) and "Traceback" not in (r.stdout + r.stderr))

# ── peer r1: coverage-key split, missing-tobe gate, mode detection by key presence ──

# r1#1 doc-mode regression: a doc manifest with only sources.docs/logs stays cross-blind
# (docs/logs are recognized by the validator but NOT counted by gap_audit's DOC_SRC coverage)
d = tempfile.mkdtemp(); _w(d, (
    "project: {doc_type: PRD, product: P, title: P, ssot: PRD.md, output_dir: outputs, sources: {docs: [\"./x.md\"]}}\n"
    "sections:\n  - {id: a, title: \"A\", status: approved, sources: [k]}\n"))
r = run_ga(d)
rep = open(os.path.join(d, "reports", "_gap_report.md"), encoding="utf-8").read()
check("r1#1 doc-mode: sources.docs does NOT count for doc-mode coverage (still cross-blind)",
      "**0** source path" in rep and "Cross-consistency not run" in rep)
# but change-plan ground_audit DOES count sources.docs (its own class) → not cross-blind
d = tempfile.mkdtemp(); _w(d, (
    "project: {product: P, ssot: x.md, sources: {docs: [\"./x.md\"]}}\n"
    "observations:\n  - {id: o1, what: \"w\", sources: [s], verified: true}\n"
    "chunks:\n  - {id: c1, title: \"c\", members: [o1], order: 1, order_rationale: \"r\", status: draft, asis: \"a\", tobe: \"b\"}\n"))
r = run_gr(d)
rep = open(os.path.join(d, "reports", "_ground_report.md"), encoding="utf-8").read()
check("r1#1 change-plan: sources.docs counts for ground coverage (not cross-blind)",
      "**1** source path" in rep and "Cross-grounding not run" not in rep)

# r1#4 missing-tobe: authored chunk with as-is + grounded member but no to-be → --strict fails
d = tempfile.mkdtemp(); _w(d, (
    "project: {product: P, ssot: x.md, sources: {code_roots: [\"~/c\"]}}\n"
    "observations:\n  - {id: o1, what: \"w\", sources: [s], verified: true}\n"
    "chunks:\n  - {id: c1, title: \"c\", members: [o1], order: 1, order_rationale: \"r\", status: draft, asis: \"a\"}\n"))
r = run_gr(d, "--strict")
check("r1#4: --strict blocks authored chunk with no to-be", r.returncode != 0 and "missing to-be" in (r.stdout + r.stderr))
E, W = V.validate({"project": {"product": "P", "ssot": "x"}, "observations": [{"id": "o1", "what": "w", "verified": True, "sources": ["s"]}],
                   "chunks": [{"id": "c1", "title": "t", "members": ["o1"], "order": 1, "order_rationale": "r", "status": "draft", "asis": "a"}]})
check("r1#4: validate warns on authored chunk with no to-be", any("no to-be" in w for w in W))

# r1#5: empty observations:[]/chunks:[] is still change-plan mode (key presence) → no doc-mode warnings
E, W = V.validate({"project": {"product": "P", "ssot": "x"}, "observations": [], "chunks": []})
check("r1#5: empty observations/chunks still suppresses doc-mode warnings",
      not any("sections is empty" in w or "doc_type not set" in w for w in W))


# ── blind_lock.py (lock/verify primitive — deterministic) ──
import blind_lock as BL  # noqa: E402

d = tempfile.mkdtemp()
_p = os.path.join(d, "b1.md")
open(_p, "w", encoding="utf-8").write("predicted_failure: X\n")
check("blind_lock: lock writes sidecar, exit 0", BL.lock(_p, "tester") == 0 and os.path.isfile(_p + ".lock.yaml"))
_sc = _p + ".lock.yaml"
_sct = open(_sc, encoding="utf-8").read()
check("blind_lock: sidecar carries digest/byte_length/lock_time/locker",
      all(k in _sct for k in ("digest:", "byte_length:", "lock_time:", "locker: tester")))
check("blind_lock: verify intact payload -> 0", BL.verify(_p, _sc) == 0)
open(_p, "a", encoding="utf-8").write("tampered\n")
check("blind_lock: verify tampered payload -> 1 (diagnostic-only)", BL.verify(_p, _sc) == 1)
check("blind_lock: re-lock refused (append-only discipline) -> 3", BL.lock(_p, "tester") == 3)
check("blind_lock: lock missing file -> 2", BL.lock(os.path.join(d, "nope.md")) == 2)
check("blind_lock: verify missing sidecar -> 2", BL.verify(_p, os.path.join(d, "nope.yaml")) == 2)
check("blind_lock: --locker without value -> usage error 2 (no silent lock)",
      BL.main(["lock", _p, "--locker"]) == 2)
_bad = os.path.join(d, "bad.lock.yaml")
open(_bad, "w", encoding="utf-8").write("digest: nope\n")
check("blind_lock: malformed sidecar -> 2 (not a tamper verdict)", BL.verify(_p, _bad) == 2)
_q = os.path.join(d, "weird \"name'.md")
open(_q, "w", encoding="utf-8").write("x\n")
check("blind_lock: quotes in payload path survive lock+verify",
      BL.lock(_q, 'loc"ker') == 0 and BL.verify(_q, _q + ".lock.yaml") == 0)

# ── panel_review.sh (validation + dry-run smoke — no model calls) ──
PANEL = os.path.join(SCRIPTS, "panel_review.sh")

def run_panel(cwd, *args, env_extra=None):
    env = dict(os.environ, DRY_RUN="1")
    if env_extra:
        env.update(env_extra)
    return subprocess.run(["bash", PANEL, *args], cwd=cwd, capture_output=True, text=True, env=env)

d = tempfile.mkdtemp()
r = run_panel(d, d, "1")
check("panel: missing REVIEW_BRIEF.md -> nonzero", r.returncode != 0 and "REVIEW_BRIEF.md not found" in r.stderr)
open(os.path.join(d, "REVIEW_BRIEF.md"), "w", encoding="utf-8").write("# brief\n")
r = run_panel(d, d, "x")
check("panel: non-numeric round -> exit 2", r.returncode == 2)
r = run_panel(d, d, "1", "bad/role")
check("panel: role name injection blocked -> exit 2", r.returncode == 2)
r = run_panel(d, d, "1", "pm", "pm")
check("panel: duplicate role names rejected -> exit 2", r.returncode == 2 and "duplicate" in r.stderr)
r = run_panel(d, d, "1")
check("panel: dry-run lists 5 default roles + synthesis",
      r.returncode == 0 and r.stdout.count("[dry-run]") == 7 and "SYNTHESIS" in r.stdout)
r = run_panel(d, d, "1", "pm", "qa", "pv-practitioner")
check("panel: custom role set honored in dry-run", r.returncode == 0 and "role=pv-practitioner" in r.stdout)
open(os.path.join(d, "PANEL_r2_pm.yaml"), "w", encoding="utf-8").write("x\n")
r = run_panel(d, d, "2", "pm")
check("panel: refuses to clobber existing round files -> exit 3", r.returncode == 3)
r = run_panel(d, d, "2", "pm", env_extra={"FORCE": "1"})
check("panel: FORCE=1 allows overwrite in dry-run", r.returncode == 0)

# real execution path via a fake `claude` shim on PATH (no network, no real model)
def _mk_shim(body):
    sd = tempfile.mkdtemp()
    sh = os.path.join(sd, "claude")
    open(sh, "w", encoding="utf-8").write("#!/bin/bash\n" + body)
    os.chmod(sh, 0o755)
    return sd

_ENVELOPE_OUT = r'''cat <<"Y"
schema_version: 1
case_id: "panel-x-r1"
artifact_id: "staged artifact per REVIEW_BRIEF.md"
reviewer_role: "pm"
model_lineage: "claude"
criterion_id: "REVIEW_BRIEF.md"
role_header:
  verdict: revise
  confidence: medium
  abstained: []
findings:
  - finding_id: f-01
    classification: robustness
    impact: minor
    confidence: medium
    applicability: applicable
    claim: c
    evidence: e
    inference_boundary: fact
    affected_artifact: a
Y'''
_SYNTH_OUT = r'''cat <<"Y"
# synthesis
## decision table
| s-01 | f-01 | e | minor | revise | none | none | human |
## lone criticals
## role conflicts (unresolved)
## abstentions
## removed findings
## correlated-agreement record
decision_item_count: 1
appendix: role outputs
Y'''
_OK_SHIM = ("\nfor last in \"$@\"; do :; done\n"
            "if printf '%s' \"$last\" | grep -q \"You are the Area Chair\"; then\n"
            + _SYNTH_OUT + "\nelse\n" + _ENVELOPE_OUT + "\nfi\n")
d = tempfile.mkdtemp()
open(os.path.join(d, "REVIEW_BRIEF.md"), "w", encoding="utf-8").write("# brief\n")
_env = {"DRY_RUN": "0", "DOCLOOP_MODEL": "claude", "PATH": _mk_shim(_OK_SHIM) + os.pathsep + os.environ["PATH"]}
r = run_panel(d, d, "1", "pm", "qa", env_extra=_env)
check("panel(real path): 2 roles + synthesis via shim -> exit 0",
      r.returncode == 0 and os.path.isfile(os.path.join(d, "PANEL_r1_pm.yaml"))
      and os.path.isfile(os.path.join(d, "PANEL_r1_qa.yaml"))
      and os.path.isfile(os.path.join(d, "PANEL_r1_SYNTHESIS.md")))

d = tempfile.mkdtemp()
open(os.path.join(d, "REVIEW_BRIEF.md"), "w", encoding="utf-8").write("# brief\n")
_env = {"DRY_RUN": "0", "DOCLOOP_MODEL": "claude", "PATH": _mk_shim("exit 1\n") + os.pathsep + os.environ["PATH"]}
r = run_panel(d, d, "1", "pm", "qa", env_extra=_env)
check("panel(real path): role failure -> exit 1, nothing published",
      r.returncode == 1 and not os.path.exists(os.path.join(d, "PANEL_r1_pm.yaml"))
      and not os.path.exists(os.path.join(d, "PANEL_r1_SYNTHESIS.md")))

d = tempfile.mkdtemp()
open(os.path.join(d, "REVIEW_BRIEF.md"), "w", encoding="utf-8").write("# brief\n")
_env = {"DRY_RUN": "0", "DOCLOOP_MODEL": "claude", "PATH": _mk_shim("exit 0\n") + os.pathsep + os.environ["PATH"]}
r = run_panel(d, d, "1", "pm", env_extra=_env)
check("panel(real path): empty role output -> exit 1, nothing published",
      r.returncode == 1 and "produced no output" in r.stderr
      and not os.path.exists(os.path.join(d, "PANEL_r1_pm.yaml")))

# comment-only spine must fail real YAML validation
_FAKE_YAML_SHIM = "\necho '# role_header: findings: (comment only)'\n"
d = tempfile.mkdtemp()
open(os.path.join(d, "REVIEW_BRIEF.md"), "w", encoding="utf-8").write("# brief\n")
_env = {"DRY_RUN": "0", "DOCLOOP_MODEL": "claude", "PATH": _mk_shim(_FAKE_YAML_SHIM) + os.pathsep + os.environ["PATH"]}
r = run_panel(d, d, "1", "pm", env_extra=_env)
check("panel(real path): comment-only envelope rejected by YAML validation",
      r.returncode == 1 and "not a valid envelope YAML" in r.stderr)

# budget cap enforced: decision_item_count > 5 -> nothing published
_OVER_SHIM = ("\nfor last in \"$@\"; do :; done\n"
              "if printf '%s' \"$last\" | grep -q \"You are the Area Chair\"; then\n"
              "  echo 'decision_item_count: 9'\nelse\n" + _ENVELOPE_OUT + "\nfi\n")
d = tempfile.mkdtemp()
open(os.path.join(d, "REVIEW_BRIEF.md"), "w", encoding="utf-8").write("# brief\n")
_env = {"DRY_RUN": "0", "DOCLOOP_MODEL": "claude", "PATH": _mk_shim(_OVER_SHIM) + os.pathsep + os.environ["PATH"]}
r = run_panel(d, d, "1", "pm", env_extra=_env)
check("panel(real path): decision_item_count>5 -> exit 1, nothing published",
      r.returncode == 1 and "exceeds the budget cap" in r.stderr
      and not os.path.exists(os.path.join(d, "PANEL_r1_pm.yaml"))
      and not os.path.exists(os.path.join(d, "PANEL_r1_SYNTHESIS.md")))

# destination-is-a-directory is an abort, not a move-into (FORCE path)
d = tempfile.mkdtemp()
open(os.path.join(d, "REVIEW_BRIEF.md"), "w", encoding="utf-8").write("# brief\n")
os.mkdir(os.path.join(d, "PANEL_r1_pm.yaml"))
_env = {"DRY_RUN": "0", "DOCLOOP_MODEL": "claude", "FORCE": "1",
        "PATH": _mk_shim(_OK_SHIM) + os.pathsep + os.environ["PATH"]}
r = run_panel(d, d, "1", "pm", env_extra=_env)
check("panel(real path): dest-is-directory aborts publish",
      r.returncode != 0 and "destination is a directory" in r.stderr)

# codex path via a codex shim honoring --output-last-message
_CODEX_SHIM = r'''
out=""; prev=""
for a in "$@"; do
  if [ "$prev" = "--output-last-message" ]; then out="$a"; fi
  prev="$a"
done
for last in "$@"; do :; done
if printf '%s' "$*" | grep -q -- "--help"; then echo "--output-last-message"; exit 0; fi
if printf '%s' "$last" | grep -q "You are the Area Chair"; then
  { echo "# synthesis"; echo "## decision table"; echo "decision_item_count: 1"; } > "$out"
else
  { echo "schema_version: 1"; echo "role_header:"; echo "  verdict: revise"; echo "  confidence: medium"; echo "  abstained: []"; echo "findings: []"; } > "$out"
fi
'''
sd = tempfile.mkdtemp()
open(os.path.join(sd, "codex"), "w", encoding="utf-8").write("#!/bin/bash\n" + _CODEX_SHIM)
os.chmod(os.path.join(sd, "codex"), 0o755)
d = tempfile.mkdtemp()
open(os.path.join(d, "REVIEW_BRIEF.md"), "w", encoding="utf-8").write("# brief\n")
_env = {"DRY_RUN": "0", "DOCLOOP_MODEL": "codex", "PATH": sd + os.pathsep + os.environ["PATH"]}
r = run_panel(d, d, "1", "pm", env_extra=_env)
check("panel(real path, codex shim): output captured via --output-last-message, synthesis published",
      r.returncode == 0 and os.path.isfile(os.path.join(d, "PANEL_r1_pm.yaml"))
      and os.path.isfile(os.path.join(d, "PANEL_r1_SYNTHESIS.md")))


# ── D2: upstream-ported guard behaviors (hardening plan 2026-07-17) ──

def _mkws():
    """minimal manifest+ssot workspace for split guard tests."""
    w = tempfile.mkdtemp()
    open(os.path.join(w, "m.yaml"), "w").write(
        "project: {product: T, title: T, ssot: b.md, output_dir: outputs, doc_type: prd}\n"
        "sections:\n  - {id: goals, title: \"목표\", status: approved}\n")
    open(os.path.join(w, "b.md"), "w").write("# 목표\n\n본문\n")
    return w

def _split_run(w):
    return subprocess.run([sys.executable, os.path.join(SCRIPTS, "split.py"), "m.yaml"],
                          cwd=w, capture_output=True, text=True, timeout=30)

w = _mkws()
victim = os.path.join(w, "victim"); os.makedirs(victim)
open(os.path.join(victim, ".docloop_output"), "w").close()
open(os.path.join(victim, "keep.md"), "w").write("피해 대상")
os.symlink(victim, os.path.join(w, "outputs"))
r = _split_run(w)
check("guard: symlink out_dir rejected (lexical islink)", r.returncode != 0 and "symlink" in r.stderr)
check("guard: symlink target preserved", os.path.exists(os.path.join(victim, "keep.md")))

# trailing-slash bypass variant — the exact upstream bug: 'outputs/' makes plain islink() False
w = _mkws()
victim2 = os.path.join(w, "victim2"); os.makedirs(victim2)
open(os.path.join(victim2, ".docloop_output"), "w").close()
open(os.path.join(victim2, "keep.md"), "w").write("피해 대상")
os.symlink(victim2, os.path.join(w, "outputs"))
open(os.path.join(w, "m.yaml"), "w").write(
    "project: {product: T, title: T, ssot: b.md, output_dir: 'outputs/', doc_type: prd}\n"
    "sections:\n  - {id: goals, title: \"목표\", status: approved}\n")
r = _split_run(w)
check("guard: trailing-slash symlink bypass rejected", r.returncode != 0 and "symlink" in r.stderr)
check("guard: trailing-slash victim preserved", os.path.exists(os.path.join(victim2, "keep.md")))

w = _mkws(); os.makedirs(os.path.join(w, "outputs"))
open(os.path.join(w, "outputs", "precious.txt"), "w").write("보존")
r = _split_run(w)
check("guard: unmarked non-empty refused", r.returncode != 0)
check("guard: refused dir contents preserved", open(os.path.join(w, "outputs", "precious.txt")).read() == "보존")

w = _mkws(); r1 = _split_run(w); r2 = _split_run(w)
check("guard: marked dir regenerated (rerun ok)", r1.returncode == 0 and r2.returncode == 0)

w = _mkws(); os.makedirs(os.path.join(w, "outputs")); r = _split_run(w)
check("guard: empty dir adopted", r.returncode == 0)

w = _mkws(); os.makedirs(os.path.join(w, "outputs"))
open(os.path.join(w, "outputs", ".other_tool_marker"), "w").close()
r = _split_run(w)
check("guard: foreign marker treated as unmarked non-empty", r.returncode != 0)
check("guard: foreign-marker dir preserved", os.path.exists(os.path.join(w, "outputs", ".other_tool_marker")))

for bad in ("", ".", "..", "a/b", "a\\b", "a\x00b", "/abs/x"):
    w = _mkws(); os.makedirs(os.path.join(w, "outputs"))
    r = subprocess.run([sys.executable, "-c",
        f"import sys; sys.path.insert(0, {SCRIPTS!r}); import split; split.MARKER = {bad!r}; "
        f"sys.argv = ['split.py', 'm.yaml']; split.main()"],
        cwd=w, capture_output=True, text=True, timeout=30)
    check(f"guard: invalid marker {bad!r} rejected via marker branch",
          r.returncode != 0 and "invalid generation marker" in r.stderr)
    check(f"guard: invalid marker {bad!r} left dir untouched", os.listdir(os.path.join(w, "outputs")) == [])

for od in ("deep/outputs", "..", "."):
    w = _mkws(); os.makedirs(os.path.join(w, "deep"), exist_ok=True)
    open(os.path.join(w, "m.yaml"), "w").write(
        f"project: {{product: T, title: T, ssot: b.md, output_dir: '{od}', doc_type: prd}}\n"
        "sections:\n  - {id: goals, title: \"목표\", status: approved}\n")
    r = _split_run(w)
    check(f"guard: out_dir '{od}' rejected (boundary)", r.returncode != 0)

import stage as ST
w = tempfile.mkdtemp(); os.mkfifo(os.path.join(w, "p.fifo"))
open(os.path.join(w, "ok.md"), "w").write("x")
r = subprocess.run([sys.executable, os.path.join(SCRIPTS, "stage.py"), "case",
                    "p.fifo", "ok.md", "--dest", os.path.join(w, "d")],
                   cwd=w, capture_output=True, text=True, timeout=15)
check("stage guard: FIFO rejected via special-file branch (bounded)",
      r.returncode == 0 and "not a regular file/directory" in r.stdout)
check("stage guard: FIFO not staged", not os.path.exists(os.path.join(w, "d", "case", "p.fifo")))
# ValueError 분기 실실행(r1-05): commonpath를 모킹해 예외 경로를 강제
_orig_cp = os.path.commonpath
def _raise(*a, **k):
    raise ValueError("simulated cross-drive")
os.path.commonpath = _raise
try:
    check("stage guard: _inside ValueError branch → outside", ST._inside("/a/b", "/a") is False)
finally:
    os.path.commonpath = _orig_cp
# FIFO 단독(수락 0건) — 실패 exit + 새 폴더 정리(r1-06)
w = tempfile.mkdtemp(); os.mkfifo(os.path.join(w, "only.fifo"))
r = subprocess.run([sys.executable, os.path.join(SCRIPTS, "stage.py"), "case0",
                    "only.fifo", "--dest", os.path.join(w, "d")],
                   cwd=w, capture_output=True, text=True, timeout=15)
check("stage guard: zero-accepted (FIFO only) → nonzero exit", r.returncode != 0)
check("stage guard: zero-accepted new folder cleaned", not os.path.exists(os.path.join(w, "d", "case0")))
# worktree 경고 실단언(r1-06): git repo 안 dest → 경고 출력, 밖 → 없음
wt = tempfile.mkdtemp(); subprocess.run(["git", "init", "-q", wt], capture_output=True)
open(os.path.join(wt, "t.md"), "w").write("x")
r = subprocess.run([sys.executable, os.path.join(SCRIPTS, "stage.py"), "case",
                    "t.md", "--dest", os.path.join(wt, "d")],
                   cwd=wt, capture_output=True, text=True, timeout=15)
check("stage guard: worktree warning emitted inside git", "inside a Git worktree" in r.stdout)
nw = tempfile.mkdtemp()
open(os.path.join(nw, "t.md"), "w").write("x")
r = subprocess.run([sys.executable, os.path.join(SCRIPTS, "stage.py"), "case",
                    "t.md", "--dest", os.path.join(nw, "d")],
                   cwd=nw, capture_output=True, text=True, timeout=15)
check("stage guard: no worktree warning outside git", "inside a Git worktree" not in r.stdout)



# ── multi_lens_review.sh: success = exit 0 AND output exists AND non-blank ──
# Ported with upstream docuauthring #110/#111. A lens can exit 0 and write nothing; judging by
# exit code alone printed a ✓ next to a file that does not exist and sent the human to triage,
# where "found nothing" and "never ran" become indistinguishable. A fake `codex` on PATH drives
# the driver's verdict contract — it does not model real codex behaviour.
MLR = os.path.join(SCRIPTS, "multi_lens_review.sh")

def _codex_shim(run_body, supports_o=True):
    """Fake `codex`. --help decides which capture path the driver picks."""
    helpline = '  -o, --output-last-message <FILE>' if supports_o else '  (no output-last-message)'
    sd = tempfile.mkdtemp()
    sh = os.path.join(sd, "codex")
    open(sh, "w", encoding="utf-8").write(
        "#!/bin/bash\n"
        'for a in "$@"; do if [ "$a" = "--help" ]; then echo "%s"; exit 0; fi; done\n' % helpline
        + run_body + "\n")
    os.chmod(sh, 0o755)
    return sd

def _mlr_dir():
    d = tempfile.mkdtemp()
    open(os.path.join(d, "REVIEW_BRIEF.md"), "w", encoding="utf-8").write("# brief\n")
    return d

def run_mlr(cwd, n, *lenses, shim=None, force=False):
    env = dict(os.environ, DRY_RUN="0")
    if shim:
        env["PATH"] = shim + os.pathsep + os.environ["PATH"]
    if force:
        env["FORCE"] = "1"
    lenses = lenses or ("correctness",)
    return subprocess.run(["bash", MLR, cwd, n, *lenses], cwd=cwd, capture_output=True, text=True, env=env)


def _marker_shim(run_body, supports_o=True):
    """_codex_shim + markers: CALLED when the run body executes, PROBED when --help is asked.
    Refusal paths assert the model was never invoked; DRY_RUN asserts not even the probe ran
    (port r1-01 — a marker placed only after the --help branch would mask a probing dry-run)."""
    sd = _codex_shim(run_body, supports_o=supports_o)
    sh = os.path.join(sd, "codex")
    body = open(sh, encoding="utf-8").read()
    body = body.replace('then echo ', f'then touch "{sd}/PROBED"; echo ', 1)
    head, tail = body.split("done\n", 1)
    open(sh, "w", encoding="utf-8").write(head + "done\n" + f'touch "{sd}/CALLED"\n' + tail)
    return sd


def _called(sd):
    return os.path.exists(os.path.join(sd, "CALLED"))


def _probed(sd):
    return os.path.exists(os.path.join(sd, "PROBED"))


def _rd_file(d, name):
    p = os.path.join(d, name)
    if not os.path.isfile(p):
        return ""
    return open(p, encoding="utf-8").read()


WRITES_O = 'prev=""; for a in "$@"; do [ "$prev" = "-o" ] && printf "%s\\n" "{}" > "$a"; prev="$a"; done; exit 0'
LOCK1 = ".review_r1.lock"

# -o capture path: exit 0, writes nothing at all
d = _mlr_dir()
r = run_mlr(d, "1", shim=_codex_shim("exit 0", supports_o=True))
out = r.stdout + r.stderr
check("multi_lens(-o): exit0 + no output -> ✗ and exit 4 (not ✓)",
      r.returncode == 4 and "no output" in out and "✓" not in r.stdout)
check("multi_lens: a missing file is not advertised to triage", "done. Merge" not in r.stdout)

# stdout-redirect fallback: exit 0 leaves a 0-byte file
d = _mlr_dir()
r = run_mlr(d, "1", shim=_codex_shim("exit 0", supports_o=False))
check("multi_lens(fallback): exit0 + 0-byte output -> ✗ and exit 4",
      r.returncode == 4 and "empty output" in (r.stdout + r.stderr) and "✓" not in r.stdout)

# whitespace-only output counts as empty
d = _mlr_dir()
r = run_mlr(d, "1", shim=_codex_shim('printf "  \\n\\t\\n"; exit 0', supports_o=False))
check("multi_lens: whitespace-only output judged empty",
      r.returncode == 4 and "empty output" in (r.stdout + r.stderr))

# normal: real content -> ✓
d = _mlr_dir()
r = run_mlr(d, "1", shim=_codex_shim('printf "r1-correctness-01 · bug · x · y\\n"; exit 0', supports_o=False))
check("multi_lens: content-bearing output -> ✓, exit 0, triage advertised",
      r.returncode == 0 and "✓" in r.stdout and "done. Merge" in r.stdout)

# exit-code failure keeps its own distinct reason (regression)
d = _mlr_dir()
r = run_mlr(d, "1", shim=_codex_shim('printf "partial\\n"; exit 7', supports_o=False))
check("multi_lens: exit-code failure reported as its own reason",
      r.returncode == 4 and "exit code" in (r.stdout + r.stderr))

# FORCE re-run must not let LAST round's content pass as this round's ✓
d = _mlr_dir()
OUTP = os.path.join(d, "REVIEW_r1_correctness.md")
r = run_mlr(d, "1", shim=_codex_shim('printf "round-one finding\\n"; exit 0', supports_o=False))
check("multi_lens/FORCE precondition: round 1 leaves content", r.returncode == 0 and os.path.exists(OUTP))
r = run_mlr(d, "1", shim=_codex_shim("exit 0", supports_o=True), force=True)
check("multi_lens/FORCE: re-run writing nothing -> ✗ exit 4 (stale must not earn ✓)",
      r.returncode == 4 and "no output" in (r.stdout + r.stderr) and "✓" not in r.stdout)
check("multi_lens/FORCE: stale output does not survive the re-run", not os.path.exists(OUTP))
check("multi_lens/FORCE: no .forcebak left behind", not os.path.exists(OUTP + ".forcebak"))

# FORCE re-run with real new content still succeeds and replaces the old text
d = _mlr_dir()
OUTP = os.path.join(d, "REVIEW_r1_correctness.md")
run_mlr(d, "1", shim=_codex_shim('printf "old text\\n"; exit 0', supports_o=False))
r = run_mlr(d, "1", shim=_codex_shim('printf "new text\\n"; exit 0', supports_o=False), force=True)
body = open(OUTP, encoding="utf-8").read() if os.path.exists(OUTP) else ""
check("multi_lens/FORCE: re-run with new content -> ✓ and content replaced",
      r.returncode == 0 and "new text" in body and "old text" not in body)


# Validate-first / no-touch (port r1-03): these three fixtures predate the block redesign and
# used to reach the ROLLBACK path (later lens rejected after an earlier lens was stashed).
# Validate-first now rejects them BEFORE anything is staged, so they cover the no-touch
# guarantee — the earlier lens is untouched, not "restored", and the model is never invoked.
# Real rollback coverage (a failure after a successful stash) lives in trio/110-r below.
CONTENT = 'printf "kept finding\n"; exit 0'

def _two_lens_seeded():
    """round 1 leaves real content for both lenses; returns (dir, pathA, pathB)."""
    d = _mlr_dir()
    r = run_mlr(d, "1", "aa", "bb", shim=_codex_shim(CONTENT, supports_o=False))
    a = os.path.join(d, "REVIEW_r1_aa.md"); b = os.path.join(d, "REVIEW_r1_bb.md")
    assert r.returncode == 0 and os.path.exists(a) and os.path.exists(b), r.stdout + r.stderr
    return d, a, b


def _pathset_state(d, *lenses):
    """Full no-touch snapshot (port r1-03 r2): absence / type / bytes / link target for every
    path of each lens's execution unit (.md, .md.log, .md.err) plus their .forcebak variants —
    preserving only the earlier .md would let a regression touch a sidecar and still pass."""
    st = []
    for lens in lenses:
        base = os.path.join(d, "REVIEW_r1_%s.md" % lens)
        for p in [base + suf for suf in ("", ".log", ".err")]:
            for q in (p, p + ".forcebak"):
                # lstat + S_IS* (port r1-03 r3): is-file/is-dir predicates let FIFOs, sockets
                # and device nodes fall through as "absent" — creating/removing one would
                # false-pass the no-touch comparison.
                try:
                    s = os.lstat(q)
                except FileNotFoundError:
                    st.append((q, "absent", None))
                    continue
                m = s.st_mode
                if stat.S_ISLNK(m):
                    st.append((q, "link", os.readlink(q)))
                elif stat.S_ISDIR(m):
                    st.append((q, "dir", None))
                elif stat.S_ISREG(m):
                    st.append((q, "file", open(q, "rb").read()))
                else:
                    st.append((q, "special", stat.S_IFMT(m)))
    return st

# later lens is a symlink -> reject in validation; NOTHING in either unit is touched
d, A, B = _two_lens_seeded()
os.remove(B); os.symlink("/tmp/elsewhere", B)
before = _pathset_state(d, "aa", "bb")
sd = _marker_shim(CONTENT, supports_o=False)
r = run_mlr(d, "1", "aa", "bb", shim=sd, force=True)
check("multi_lens/validate-first: later-lens symlink -> exit 3, model never invoked",
      r.returncode == 3 and not _called(sd))
check("multi_lens/validate-first: full path-set snapshot unchanged after symlink refusal",
      _pathset_state(d, "aa", "bb") == before)

# later lens already has a stale .forcebak -> refuse in validation; nothing touched
d, A, B = _two_lens_seeded()
open(B + ".forcebak", "w", encoding="utf-8").write("older\n")
before = _pathset_state(d, "aa", "bb")
sd = _marker_shim(CONTENT, supports_o=False)
r = run_mlr(d, "1", "aa", "bb", shim=sd, force=True)
check("multi_lens/validate-first: pre-existing .forcebak -> exit 3, model never invoked",
      r.returncode == 3 and not _called(sd))
check("multi_lens/validate-first: full path-set snapshot unchanged after residue refusal",
      _pathset_state(d, "aa", "bb") == before)

# later lens output is a directory (not a regular file) -> refuse in validation; untouched
d, A, B = _two_lens_seeded()
os.remove(B); os.mkdir(B)
before = _pathset_state(d, "aa", "bb")
sd = _marker_shim(CONTENT, supports_o=False)
r = run_mlr(d, "1", "aa", "bb", shim=sd, force=True)
check("multi_lens/validate-first: non-regular later output -> exit 3, model never invoked",
      r.returncode == 3 and not _called(sd))
check("multi_lens/validate-first: full path-set snapshot unchanged after non-regular refusal",
      _pathset_state(d, "aa", "bb") == before)

# mixed verdicts across lenses: one real, one empty -> overall failure, reasons kept apart
d = _mlr_dir()
mixed = r"""out=""
prev=""
for a in "$@"; do case "$prev" in -o) out="$a";; esac; prev="$a"; done
case " $* " in *"Lens: "*) :;; esac
if [ -n "$out" ]; then case "$out" in *_aa.md) printf "real\n" > "$out";; esac; fi
exit 0"""
r = run_mlr(d, "1", "aa", "bb", shim=_codex_shim(mixed, supports_o=True))
out = r.stdout + r.stderr
check("multi_lens: mixed verdicts -> exit 4 with per-lens reasons",
      r.returncode == 4 and "✓ aa" in r.stdout and "bb" in out and "no output" in out)

# BOM boundary (r1-05)
d = _mlr_dir()
r = run_mlr(d, "1", shim=_codex_shim(r'printf "\xef\xbb\xbf   \n"; exit 0', supports_o=False))
check("multi_lens: BOM-only output judged empty",
      r.returncode == 4 and "empty output" in (r.stdout + r.stderr))
d = _mlr_dir()
r = run_mlr(d, "1", shim=_codex_shim(r'printf "\xef\xbb\xbfreal finding\n"; exit 0', supports_o=False))
check("multi_lens: BOM + content still counts as content", r.returncode == 0 and "✓" in r.stdout)

# ── multi_lens_review.sh: FORCE/clobber trio (upstream #115/#116/#120 + impl r1/r2) ──
# Re-ported with the upstream block redesign: round lock, validate-first over the three-path
# execution unit, sidecar staging, signal ladder, pgroup kill. The issue-body reproductions
# are the fixtures. Shims (fake codex/mv/rm/mkdir on PATH) inject faults deterministically.

# -h must include the stale-lock recovery guidance (port r1-04 — the usage sed range cut it)
r = subprocess.run(["bash", MLR, "-h"], capture_output=True, text=True)
check("trio/help: -h names the lock and the rmdir recovery",
      r.returncode == 0 and ".review_r<N>.lock" in r.stdout and "rmdir" in r.stdout)

# 120-A: FORCE with a directory squatting on the sidecar path — validate-first must stop
# before the .md is deleted (previously the .md was already gone when redirection failed).
d = _mlr_dir(); sd = _marker_shim(WRITES_O.format("new finding"))
open(os.path.join(d, "REVIEW_r1_correctness.md"), "w").write("previous round finding\n")
os.mkdir(os.path.join(d, "REVIEW_r1_correctness.md.log"))
r = run_mlr(d, "1", shim=sd, force=True)
check("trio/120-A: FORCE + dir sidecar -> exit 3, model never invoked, .md preserved",
      r.returncode == 3 and not _called(sd)
      and _rd_file(d, "REVIEW_r1_correctness.md") == "previous round finding\n")

# 120-A2: a later lens's bad sidecar must not burn an earlier lens's evidence
d = _mlr_dir(); sd = _marker_shim(WRITES_O.format("x"))
open(os.path.join(d, "REVIEW_r1_aa.md"), "w").write("aa evidence\n")
os.mkdir(os.path.join(d, "REVIEW_r1_bb.md.err"))
r = run_mlr(d, "1", "aa", "bb", shim=sd, force=True)
check("trio/120-A2: later-lens bad sidecar -> exit 3, earlier evidence preserved",
      r.returncode == 3 and not _called(sd) and _rd_file(d, "REVIEW_r1_aa.md") == "aa evidence\n")

# 120-B: symlinked sidecar escapes the review folder — rejected even without FORCE
d = _mlr_dir(); sd = _marker_shim(WRITES_O.format("x"))
outside = os.path.join(os.path.dirname(d), "outside-" + os.path.basename(d)); os.makedirs(outside)
os.symlink(os.path.join(outside, "escaped.log"), os.path.join(d, "REVIEW_r1_correctness.md.log"))
r = run_mlr(d, "1", shim=sd)
check("trio/120-B: symlink sidecar -> exit 3 (no FORCE needed), no outside write",
      r.returncode == 3 and "symlink" in (r.stdout + r.stderr) and not _called(sd)
      and not os.path.exists(os.path.join(outside, "escaped.log")))

# 116-a: a stale INACTIVE sidecar is staged away with the FORCE re-run, both capture modes
d = _mlr_dir(); sd = _marker_shim(WRITES_O.format("new round"))
open(os.path.join(d, "REVIEW_r1_correctness.md"), "w").write("old\n")
open(os.path.join(d, "REVIEW_r1_correctness.md.err"), "w").write("stale stderr diagnosis\n")
r = run_mlr(d, "1", shim=sd, force=True)
check("trio/116-a(-o): FORCE re-run succeeds and the stale .err is gone",
      r.returncode == 0 and _rd_file(d, "REVIEW_r1_correctness.md").strip() == "new round"
      and not os.path.exists(os.path.join(d, "REVIEW_r1_correctness.md.err")))
d = _mlr_dir(); sd = _marker_shim('printf "new round\\n"; exit 0', supports_o=False)
open(os.path.join(d, "REVIEW_r1_correctness.md"), "w").write("old\n")
open(os.path.join(d, "REVIEW_r1_correctness.md.log"), "w").write("stale -o log\n")
r = run_mlr(d, "1", shim=sd, force=True)
check("trio/116-a(fallback): FORCE re-run succeeds and the stale .log is gone",
      r.returncode == 0 and not os.path.exists(os.path.join(d, "REVIEW_r1_correctness.md.log")))

# 116-b: the ✗ hint names only the sidecar this run actually wrote
d = _mlr_dir(); sd = _marker_shim("exit 7")
r = run_mlr(d, "1", shim=sd)
out = r.stdout + r.stderr
check("trio/116-b(-o): ✗ names .log only", r.returncode == 4
      and "(-> REVIEW_r1_correctness.md.log)" in out and ".md.err" not in out)
d = _mlr_dir(); sd = _marker_shim("exit 7", supports_o=False)
r = run_mlr(d, "1", shim=sd)
out = r.stdout + r.stderr
check("trio/116-b(fallback): ✗ names .err only", r.returncode == 4
      and "(-> REVIEW_r1_correctness.md.err)" in out and ".md.log" not in out)

# 116-c: a leftover regular sidecar alone refuses a non-FORCE re-run (behavior change)
d = _mlr_dir(); sd = _marker_shim(WRITES_O.format("x"))
open(os.path.join(d, "REVIEW_r1_correctness.md.err"), "w").write("failure evidence\n")
r = run_mlr(d, "1", shim=sd)
check("trio/116-c: leftover sidecar without FORCE -> exit 3, model never invoked, evidence kept",
      r.returncode == 3 and "already exists" in (r.stdout + r.stderr) and not _called(sd)
      and _rd_file(d, "REVIEW_r1_correctness.md.err") == "failure evidence\n")

# 115-a: pre-existing lock -> refuse before touching anything
d = _mlr_dir(); sd = _marker_shim(WRITES_O.format("x"))
os.mkdir(os.path.join(d, LOCK1))
open(os.path.join(d, "REVIEW_r1_correctness.md"), "w").write("evidence\n")
r = run_mlr(d, "1", shim=sd, force=True)
check("trio/115-a: pre-existing lock -> exit 3, lock named, nothing touched",
      r.returncode == 3 and "lock held" in (r.stdout + r.stderr) and not _called(sd)
      and _rd_file(d, "REVIEW_r1_correctness.md") == "evidence\n")

# 115-b: neither success nor failure leaves the lock behind
d = _mlr_dir(); sd = _marker_shim(WRITES_O.format("r1"))
r = run_mlr(d, "1", shim=sd)
check("trio/115-b: lock released after success", r.returncode == 0 and not os.path.exists(os.path.join(d, LOCK1)))
r = run_mlr(d, "1", shim=_marker_shim("exit 7"), force=True)
check("trio/115-b: lock released after failure", r.returncode == 4 and not os.path.exists(os.path.join(d, LOCK1)))

# 115-c: .forcebak residue refuses even when OUT itself is absent (unconditional check),
# and a dangling-symlink marker is still residue (upstream impl r1-05)
d = _mlr_dir(); sd = _marker_shim(WRITES_O.format("x"))
open(os.path.join(d, "REVIEW_r1_correctness.md.forcebak"), "w").write("residue\n")
r = run_mlr(d, "1", shim=sd, force=True)
check("trio/115-c: .forcebak residue with OUT absent -> exit 3",
      r.returncode == 3 and "forcebak" in (r.stdout + r.stderr) and not _called(sd))
d = _mlr_dir(); sd = _marker_shim(WRITES_O.format("x"))
os.symlink("/nonexistent-x", os.path.join(d, "REVIEW_r1_correctness.md.forcebak"))
r = run_mlr(d, "1", shim=sd, force=True)
check("trio/115-c: dangling-symlink .forcebak residue -> exit 3",
      r.returncode == 3 and "forcebak" in (r.stdout + r.stderr) and not _called(sd))

# 115-d: the lock is held THROUGH collection — block A inside the fake lens via a gate file,
# require the startup handshake, then assert a same-round B is refused without a model call
d = _mlr_dir()
sdA = _codex_shim("touch STARTED\nwhile [ ! -e release ]; do sleep 0.05; done\n" + WRITES_O.format("A finding"))
envA = dict(os.environ, DRY_RUN="0"); envA["PATH"] = sdA + os.pathsep + envA["PATH"]
pA = subprocess.Popen(["bash", MLR, d, "1", "correctness"], cwd=d, env=envA,
                      stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
_started = False
for _ in range(200):
    if os.path.exists(os.path.join(d, "STARTED")):
        _started = True
        break
    time.sleep(0.05)
check("trio/115-d: startup handshake observed", _started)
check("trio/115-d: A holds the lock while its lens runs", os.path.exists(os.path.join(d, LOCK1)))
sdB = _marker_shim(WRITES_O.format("B finding"))
rB = run_mlr(d, "1", shim=sdB, force=True)
check("trio/115-d: same-round B refused while A runs, model never invoked",
      rB.returncode == 3 and "lock held" in (rB.stdout + rB.stderr) and not _called(sdB))
open(os.path.join(d, "release"), "w").write("")
pA.communicate(timeout=30)
check("trio/115-d: A completes and releases; re-entry works",
      pA.returncode == 0 and _rd_file(d, "REVIEW_r1_correctness.md").strip() == "A finding"
      and not os.path.exists(os.path.join(d, LOCK1))
      and run_mlr(d, "1", shim=sdB, force=True).returncode == 0)

# 110-r: a stash failure mid-staging still rolls back everything (all-or-nothing survives
# validate-first) — a fake mv fails only the later lens's stash and delegates the rest
d = _mlr_dir(); sd = _marker_shim(WRITES_O.format("x"))
open(os.path.join(sd, "mv"), "w").write(
    '#!/bin/sh\nfor a in "$@"; do case "$a" in *REVIEW_r1_bb.md.forcebak) exit 1;; esac; done\nexec /bin/mv "$@"\n')
os.chmod(os.path.join(sd, "mv"), 0o755)
for nm, c in [("REVIEW_r1_aa.md", "aa evidence\n"), ("REVIEW_r1_aa.md.log", "log evidence\n"),
              ("REVIEW_r1_aa.md.err", "err evidence\n"), ("REVIEW_r1_bb.md", "bb evidence\n")]:
    open(os.path.join(d, nm), "w").write(c)
r = run_mlr(d, "1", "aa", "bb", shim=sd, force=True)
check("trio/110-r: mid-staging mv failure -> exit 3, model never invoked",
      r.returncode == 3 and not _called(sd))
check("trio/110-r: all three earlier-lens files restored, no .forcebak residue",
      _rd_file(d, "REVIEW_r1_aa.md") == "aa evidence\n"
      and _rd_file(d, "REVIEW_r1_aa.md.log") == "log evidence\n"
      and _rd_file(d, "REVIEW_r1_aa.md.err") == "err evidence\n"
      and _rd_file(d, "REVIEW_r1_bb.md") == "bb evidence\n"
      and not [f for f in os.listdir(d) if f.endswith(".forcebak")])

# impl r1-01: a signal during commit (backup deletion) must not half-restore — remaining
# backups stay under .forcebak (content preserved; the next run refuses with guidance)
d = _mlr_dir(); sd = _marker_shim(WRITES_O.format("x"))
open(os.path.join(sd, "rm"), "w").write(
    '#!/bin/sh\nfor a in "$@"; do case "$a" in *REVIEW_r1_aa.md.forcebak) kill -TERM $PPID;; esac; done\nexec /bin/rm "$@"\n')
os.chmod(os.path.join(sd, "rm"), 0o755)
open(os.path.join(d, "REVIEW_r1_aa.md"), "w").write("aa evidence\n")
open(os.path.join(d, "REVIEW_r1_bb.md"), "w").write("bb evidence\n")
r = run_mlr(d, "1", "aa", "bb", shim=sd, force=True)
check("trio/impl-r1-01: signal during commit -> exit 3, model never invoked, lock released",
      r.returncode == 3 and not _called(sd) and not os.path.exists(os.path.join(d, LOCK1)))
check("trio/impl-r1-01: no partial restore — remaining backup stays as .forcebak",
      not os.path.exists(os.path.join(d, "REVIEW_r1_bb.md"))
      and _rd_file(d, "REVIEW_r1_bb.md.forcebak") == "bb evidence\n"
      and not os.path.exists(os.path.join(d, "REVIEW_r1_aa.md.forcebak")))

# impl r1-02: a signal in the staging window must not leave the lock; content survives under
# either its own name or .forcebak (documented non-guarantee between mv and registration)
d = _mlr_dir(); sd = _marker_shim(WRITES_O.format("x"))
open(os.path.join(sd, "mv"), "w").write(
    '#!/bin/sh\nfor a in "$@"; do case "$a" in *REVIEW_r1_correctness.md.forcebak) kill -TERM $PPID;; esac; done\nexec /bin/mv "$@"\n')
os.chmod(os.path.join(sd, "mv"), 0o755)
open(os.path.join(d, "REVIEW_r1_correctness.md"), "w").write("c evidence\n")
r = run_mlr(d, "1", shim=sd, force=True)
check("trio/impl-r1-02: staging-window signal -> exit 3, lock released, content preserved",
      r.returncode == 3 and not _called(sd) and not os.path.exists(os.path.join(d, LOCK1))
      and (_rd_file(d, "REVIEW_r1_correctness.md") == "c evidence\n"
           or _rd_file(d, "REVIEW_r1_correctness.md.forcebak") == "c evidence\n"))

# impl r1-02b: a TRAPPED signal right after mkdir succeeds must not strand the fresh lock —
# the acquisition window latches; ownership is recorded before the pending signal is honored
d = _mlr_dir(); sd = _marker_shim(WRITES_O.format("x"))
open(os.path.join(sd, "mkdir"), "w").write(
    '#!/bin/sh\ncase "$*" in *.review_r1.lock*) /bin/mkdir "$@"; s=$?; kill -TERM $PPID; exit $s;; esac\nexec /bin/mkdir "$@"\n')
os.chmod(os.path.join(sd, "mkdir"), 0o755)
r = run_mlr(d, "1", shim=sd)
check("trio/impl-r1-02b: trapped signal right after acquisition -> exit 3, no stale lock",
      r.returncode == 3 and not os.path.exists(os.path.join(d, LOCK1)) and not _called(sd))

# impl r1-03d: a signalled round must not end 0 even in dry-run. Determinism comes from pipe
# BACKPRESSURE via ONE lens with a giant name (port r1-02 r2): its single [dry] line
# (~120KB — the name appears twice) exceeds the pipe buffer nobody reads, so that one job
# blocks mid-write and the run CANNOT complete before we drain. One lens also removes the
# startup dependency the 1500-lens variant had (1500 sequential `tr` validation calls).
# We still wait for the FIRST [dry] bytes, not just any output: the header prints BEFORE the
# execution trap is installed, so signalling on the header races a default-action death
# (measured). [dry] bytes prove a lens job ran, which proves the parent passed the trap line;
# our few 256-byte reads do not meaningfully drain the ~120KB pending write.
import select as _select
d = _mlr_dir()
# Environmental invariants, stated rather than assumed silently (port r1-02 r3/r4): the name
# must satisfy BOTH (a) single-argument size < Linux MAX_ARG_STRLEN (32 pages = 128KiB on
# 4KiB-page systems — a 200KB name aborted Popen with E2BIG in a Linux container, r4) and
# (b) one [dry] line (the name appears twice) >> DEFAULT pipe capacity (16-64KiB on
# macOS/Linux; raising it needs an explicit fcntl nothing here does). 100,000 gives ~30KiB
# headroom under (a) and a ~200KB line, >3x margin over (b). If either invariant shifts, the
# Popen guard or the liveness gate below fails loudly rather than letting the test false-pass.
_giant = "L" + "x" * 100000
envD = dict(os.environ, DRY_RUN="1")
try:
    pD = subprocess.Popen(["bash", MLR, d, "1", _giant], cwd=d, env=envD,
                          stdout=subprocess.PIPE, stderr=subprocess.PIPE)
except OSError as _e:               # E2BIG 등 — 스위트 중단이 아니라 FAIL로 기록(r4)
    pD = None
    check("trio/impl-r1-03d: fixture launches within per-arg limits", False)
if pD is not None:
  try:
    _buf = b""
    _deadline = time.time() + 30
    while b"[dry]" not in _buf and time.time() < _deadline:
        _ready, _, _ = _select.select([pD.stdout], [], [], 1)
        if not _ready:
            continue
        _chunk = os.read(pD.stdout.fileno(), 256)
        if not _chunk:
            break
        _buf += _chunk
    check("trio/impl-r1-03d: first [dry] observed while held by pipe backpressure", b"[dry]" in _buf)
    _alive = pD.poll() is None
    check("trio/impl-r1-03d: child alive (blocked) before signalling", _alive)
    _killed_ok = False
    if _alive:                      # GATING (r3): never signal a possibly-reaped/recycled PID
        try:
            os.kill(pD.pid, signal.SIGTERM)
            _killed_ok = True
        except ProcessLookupError:
            _killed_ok = False
    try:
        outD, errD = pD.communicate(timeout=60)
    except subprocess.TimeoutExpired:
        pD.kill()
        try:
            outD, errD = pD.communicate(timeout=10)
        except subprocess.TimeoutExpired:
            outD, errD = b"", b""
    check("trio/impl-r1-03d: TERM during dry-run -> exit 4, no success line",
          _killed_ok and pD.returncode == 4
          and b"dry-run done" not in (_buf + outD) and b"interrupted" in errD)
  finally:
    # Bounded reap on any assertion path. Killing the outer bash closes our read ends via
    # communicate(); the lens (own pgroup) blocked in write then dies on SIGPIPE — no orphan.
    if pD.poll() is None:
        pD.kill()
        try:
            pD.communicate(timeout=10)
        except subprocess.TimeoutExpired:
            pass

# DRY-g: DRY_RUN stays inert on top of every guard state (lock, dir sidecar, symlink, residue)
d = _mlr_dir(); sd = _marker_shim(WRITES_O.format("x"))
open(os.path.join(d, "REVIEW_r1_correctness.md"), "w").write("old\n")
os.mkdir(os.path.join(d, "REVIEW_r1_correctness.md.log"))
os.symlink("/nonexistent-y", os.path.join(d, "REVIEW_r1_correctness.md.err"))
open(os.path.join(d, "REVIEW_r1_correctness.md.forcebak"), "w").write("residue\n")
os.mkdir(os.path.join(d, LOCK1))
snap = sorted(os.listdir(d))
ok = True
for force in (False, True):
    env = dict(os.environ, DRY_RUN="1")
    env["PATH"] = sd + os.pathsep + env["PATH"]
    if force:
        env["FORCE"] = "1"
    rr = subprocess.run(["bash", MLR, d, "1", "correctness"], cwd=d, capture_output=True, text=True, env=env)
    ok = ok and rr.returncode == 0
check("trio/DRY-g: preseeded guard states + DRY_RUN (±FORCE) -> exit 0, folder snapshot unchanged",
      ok and sorted(os.listdir(d)) == snap and not _called(sd)
      and _rd_file(d, "REVIEW_r1_correctness.md") == "old\n")
check("trio/DRY-g: DRY_RUN invokes no codex at all — not even the --help probe (port r1-01)",
      not _probed(sd))

# lock-w: a failed unlock is not swallowed — warn with the path, keep the exit status
d = _mlr_dir()
sd = _codex_shim("echo junk > %s/junk\n%s" % (LOCK1, WRITES_O.format("success")))
r = run_mlr(d, "1", shim=sd)
check("trio/lock-w: rmdir failure -> warning names the lock, exit status preserved (0)",
      r.returncode == 0 and "could not release lock" in (r.stderr or "") and LOCK1 in (r.stderr or ""))

# sig-x: INT/TERM/HUP during a running lens — jobs die BY PROCESS GROUP before the lock is
# released, and the round ends 4 (never swallowed into 0). The fake lens does NOT poll its
# parent: a real model CLI does not self-terminate, and a parent-poll would mask the
# pid-only-kill regression (subshell dies, grandchild survives — measured upstream).
for sig, sname in ((signal.SIGINT, "INT"), (signal.SIGTERM, "TERM"), (signal.SIGHUP, "HUP")):
    d = _mlr_dir()
    sd = _codex_shim("echo $$ > LENSPID\ntouch STARTED\n"
                     "while [ ! -e release ]; do sleep 0.05; done\n" + WRITES_O.format("late write"))
    envH = dict(os.environ, DRY_RUN="0", FORCE="1")
    envH["PATH"] = sd + os.pathsep + envH["PATH"]
    open(os.path.join(d, "REVIEW_r1_correctness.md"), "w").write("old\n")
    pH = subprocess.Popen(["bash", MLR, d, "1", "correctness"], cwd=d, env=envH,
                          stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    started = False
    for _ in range(200):
        if os.path.exists(os.path.join(d, "STARTED")):
            started = True
            break
        time.sleep(0.05)
    check("trio/sig-x(%s): startup handshake observed" % sname, started)
    os.kill(pH.pid, sig)
    pH.communicate(timeout=30)
    lenspid = int((_rd_file(d, "LENSPID").strip() or "0"))
    alive = lenspid > 0
    deadline = time.time() + 3
    while alive and time.time() < deadline:
        try:
            os.kill(lenspid, 0)
            time.sleep(0.05)
        except ProcessLookupError:
            alive = False
    check("trio/sig-x(%s): exit 4 (not swallowed), lock released, lens PID actually dead" % sname,
          pH.returncode == 4 and not os.path.exists(os.path.join(d, LOCK1)) and lenspid > 0 and not alive)
    open(os.path.join(d, "release"), "w").write("")
    late = False
    for _ in range(10):
        if os.path.exists(os.path.join(d, "REVIEW_r1_correctness.md")):
            late = True
            break
        time.sleep(0.05)
    check("trio/sig-x(%s): no ghost write after the lock is gone" % sname, not late)

# ══ optional contribute -> curate flow ══
# These integration tests deliberately use only the public launcher contract.  The
# model shim captures argv/cwd/prompt verbatim, allowing the legacy plan/draft
# path and the opt-in draft bridge to be checked without a provider invocation.
CC_FIXTURES = os.path.join(os.path.dirname(os.path.abspath(__file__)), "fixtures", "contribute-curate")


def _cc_write(path, body, mode=None):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8", newline="") as f:
        f.write(body)
    if mode is not None:
        os.chmod(path, mode)


def _cc_workspace():
    # macOS exposes /var as a symlink to /private/var; bash normalizes cwd.
    # Use the canonical path so argv/cwd goldens compare the same spelling.
    d = os.path.realpath(tempfile.mkdtemp())
    os.mkdir(os.path.join(d, "inputs"))
    os.mkdir(os.path.join(d, "work"))
    os.mkdir(os.path.join(d, "outputs"))
    os.mkdir(os.path.join(d, "reports"))
    _cc_write(os.path.join(d, "inputs", "source.md"), "source evidence\n")
    _cc_write(os.path.join(d, "pm-policy.yaml"),
              "org: {name: T}\n"
              "doc_types:\n  PRD:\n    sections:\n"
              "      - {id: overview, title: Overview, required: true}\n"
              "      - {id: rules, title: Rules, required: true}\n"
              "      - {id: out-of-scope, title: Out of scope, required: false}\n")
    _cc_write(os.path.join(d, "manifest.yaml"),
              "project:\n  doc_type: PRD\n  product: P\n  feature: F\n  title: P F PRD\n"
              "  ssot: PRD.md\n  policy: ./pm-policy.yaml\n  output_dir: outputs\n"
              "sections:\n"
              "  - {id: overview, title: Overview, status: draft, sources: [inputs/source.md]}\n"
              "  - {id: rules, title: Rules, status: draft, sources: [inputs/source.md]}\n"
              "  - {id: out-of-scope, title: Out of scope, status: pending}\n")
    _cc_write(os.path.join(d, "PRD.md"), "# Overview\n\nExisting draft.\n")
    return d


def _cc_model_shim():
    sd = tempfile.mkdtemp()
    script = r'''#!/usr/bin/env python3
import json, os, re, sys
prompt = sys.argv[sys.argv.index("-p") + 1] if "-p" in sys.argv else max(sys.argv[1:] or [""], key=len)
capture = os.environ.get("CC_CAPTURE")
if capture:
    os.makedirs(capture, exist_ok=True)
    n = len(os.listdir(capture))
    with open(os.path.join(capture, "%04d.json" % n), "w", encoding="utf-8") as f:
        json.dump({"argv": sys.argv[1:], "cwd": os.getcwd(), "prompt": prompt}, f,
                  ensure_ascii=False, sort_keys=True)
if os.environ.get("CC_MODEL_SLEEP"):
    import time
    time.sleep(float(os.environ["CC_MODEL_SLEEP"]))
if os.environ.get("CC_MODEL_EXIT"):
    sys.stderr.write(os.environ.get("CC_MODEL_STDERR", "model failed"))
    raise SystemExit(int(os.environ["CC_MODEL_EXIT"]))
if os.environ.get("CC_MODEL_STDERR"):
    sys.stderr.write(os.environ["CC_MODEL_STDERR"])
mode = os.environ.get("CC_MODEL_OUTPUT", "valid")
if mode == "none":
    raise SystemExit(0)
if mode == "empty":
    sys.stdout.write(" \ufeff \n")
    raise SystemExit(0)
if mode == "malformed":
    sys.stdout.write("not: [valid\n")
    raise SystemExit(0)
if mode == "huge":
    sys.stdout.write("x" * (1024 * 1024 + 1))
    raise SystemExit(0)
contract = prompt.rsplit("## Invocation contract", 1)[-1]
run = (re.search(r'(?m)^-?\s*(?:Run ID|run_id|Source run ID):\s*`?([a-z0-9-]+)', contract)
       or re.search(r'\b(cc-[a-z0-9-]+)\b', contract))
run_id = os.environ.get("CC_RUN_ID") or (run.group(1) if run else "cc-e2e")
explicit = re.search(r'(?m)^-?\s*Perspective:\s*`?([a-z-]+)', contract)
pers = explicit.group(1) if explicit else None
for candidate in ("pm", "product-designer", "frontend", "backend", "qa"):
    if pers is None and re.search(r'(?<![a-z-])' + re.escape(candidate) + r'(?![a-z-])', contract):
        pers = candidate
        break
pers = os.environ.get("CC_PERSPECTIVE", pers or "pm")
section = "overview" if pers == "pm" else "rules"
lines = [
    "schema_version: 1", "run_id: " + run_id, "perspective: " + pers,
    "model_lineage: " + os.environ.get("CC_LINEAGE", os.path.basename(sys.argv[0])), "items:",
    "  - item_id: %s/%s/01" % (run_id, pers),
    "    consideration: %s consideration" % pers,
    "    target_section: " + section, "    evidence_refs: []",
    "    human_need: decision", "    human_question: Decide %s?" % pers,
]
body = "\n".join(lines) + "\n"
out = None
for flag in ("-o", "--output-last-message"):
    if flag in sys.argv:
        i = sys.argv.index(flag)
        if i + 1 < len(sys.argv): out = sys.argv[i + 1]
if out:
    with open(out, "w", encoding="utf-8") as f: f.write(body)
else:
    sys.stdout.write(body)
'''
    _cc_write(os.path.join(sd, "codex"), script, 0o755)
    _cc_write(os.path.join(sd, "claude"), script, 0o755)
    return sd


def _cc_run(d, *args, model="codex", extra_env=None):
    env = dict(os.environ, DOCLOOP_MODEL=model)
    shim = _cc_model_shim()
    env["PATH"] = shim + os.pathsep + env.get("PATH", "")
    if extra_env:
        env.update(extra_env)
    return subprocess.run(["bash", BIN, *args], cwd=d, capture_output=True, text=True, env=env)


def _cc_popen(d, *args, extra_env=None, shim=None):
    env = dict(os.environ, DOCLOOP_MODEL="codex")
    shim = shim or _cc_model_shim()
    env["PATH"] = shim + os.pathsep + env.get("PATH", "")
    if extra_env:
        env.update(extra_env)
    return subprocess.Popen(["bash", BIN, *args], cwd=d, stdout=subprocess.PIPE,
                            stderr=subprocess.PIPE, text=True, env=env, start_new_session=True)


def _cc_wait_for(predicate, timeout=5):
    deadline = time.time() + timeout
    while time.time() < deadline:
        if predicate(): return True
        time.sleep(0.001)
    return False


def _cc_captures(path):
    if not os.path.isdir(path):
        return []
    result = []
    for name in sorted(os.listdir(path)):
        with open(os.path.join(path, name), encoding="utf-8") as f:
            result.append(json.load(f))
    return result


def _legacy_prompt(stage, cwd, *user):
    with open(os.path.join(os.path.dirname(SCRIPTS), "prompts", stage), encoding="utf-8") as f:
        # stage_prompt writes the file through command substitution, so the
        # file's own trailing LF is stripped before the literal separator.
        text = f.read()
    text += "\n\n---\n## Run context\n"
    text += "- Work folder: %s\n" % cwd
    text += "- docloop lib (scripts): %s\n" % SCRIPTS
    if os.path.isfile(os.path.join(cwd, "manifest.yaml")):
        text += "- Manifest: %s/manifest.yaml\n" % cwd
    if user:
        text += "- User request: %s\n" % " ".join(user)
    # Bash command substitution in bin/docloop removes trailing newlines.
    return text.rstrip("\n")


# BC-01..06, BC-05: exact legacy argv/cwd/prompt; opt-in bundles are ignored.
for cc_model in ("codex", "claude"):
    d = _cc_workspace(); cap = tempfile.mkdtemp()
    rp = _cc_run(d, "plan", "alpha", model=cc_model, extra_env={"CC_CAPTURE": cap})
    cp = _cc_captures(cap)
    expected_argv = (["exec", "--skip-git-repo-check", _legacy_prompt("plan.md", d, "alpha")]
                     if cc_model == "codex" else ["-p", _legacy_prompt("plan.md", d, "alpha")])
    check("contribute/BC plan %s preserves argv, cwd, and assembled prompt bytes" % cc_model,
          rp.returncode == 0 and len(cp) == 1 and cp[0]["cwd"] == d and cp[0]["argv"] == expected_argv)

    # Both complete-looking and incomplete-looking optional trees must be inert.
    os.makedirs(os.path.join(d, "work", "contributions", "old", "payload"))
    _cc_write(os.path.join(d, "work", "contributions", "old", "COMPLETE.yaml"), "invalid but ignored\n")
    os.makedirs(os.path.join(d, "work", "curations", "partial", "payload"))
    _cc_write(os.path.join(d, "work", "curations", "partial", "INCOMPLETE"), "\n")
    cap2 = tempfile.mkdtemp()
    rd = _cc_run(d, "draft", model=cc_model, extra_env={"CC_CAPTURE": cap2})
    cd = _cc_captures(cap2)
    expected_draft_argv = (["exec", "--skip-git-repo-check", _legacy_prompt("draft.md", d)]
                           if cc_model == "codex" else ["-p", _legacy_prompt("draft.md", d)])
    check("contribute/BC plain draft %s ignores optional complete/incomplete bundles" % cc_model,
          rd.returncode == 0 and len(cd) == 1 and cd[0]["cwd"] == d
          and cd[0]["argv"] == expected_draft_argv)

d = tempfile.mkdtemp()
src = os.path.join(d, "seed.txt"); _cc_write(src, "seed\n")
ri = _cc_run(d, "init", os.path.join(d, "workspace"), src)
check("contribute/BC init creates no opt-in contribution or curation directories",
      ri.returncode == 0
      and not os.path.exists(os.path.join(d, "workspace", "work", "contributions"))
      and not os.path.exists(os.path.join(d, "workspace", "work", "curations")))

rh = subprocess.run(["bash", BIN, "--help"], capture_output=True, text=True)
help_text = rh.stdout + rh.stderr
check("contribute/BC help additively advertises contribute, curate, and draft-curated",
      rh.returncode == 0 and all(x in help_text for x in ("contribute", "curate", "draft-curated")))
ru = subprocess.run(["bash", BIN, "definitely-unknown"], capture_output=True, text=True)
check("contribute/BC unknown command retains diagnostic and nonzero exit",
      ru.returncode != 0 and "unknown command 'definitely-unknown'" in (ru.stdout + ru.stderr))

# C-01/C-03/C-04/C-09b: all argument errors happen before reservation/model use.
cc_bad_cases = [
    ("zero perspectives", ("contribute", "cc-zero")),
    ("one perspective", ("contribute", "cc-one", "pm")),
    ("duplicate perspective", ("contribute", "cc-dup", "pm", "pm")),
    ("undefined perspective", ("contribute", "cc-unknown", "pm", "security")),
    ("path perspective", ("contribute", "cc-path", "pm", "../qa")),
    ("six perspectives", ("contribute", "cc-six", "pm", "qa", "backend", "frontend",
                          "product-designer", "pm-extra")),
    ("uppercase run id", ("contribute", "CC-UPPER", "pm", "qa")),
    ("slash run id", ("contribute", "cc/bad", "pm", "qa")),
    ("dot-dot run id", ("contribute", "..", "pm", "qa")),
    ("65-character run id", ("contribute", "a" * 65, "pm", "qa")),
]
for label, argv in cc_bad_cases:
    d = _cc_workspace(); cap = tempfile.mkdtemp()
    rr = _cc_run(d, *argv, extra_env={"CC_CAPTURE": cap})
    check("contribute/input rejects %s before model invocation or artifact creation" % label,
          rr.returncode != 0 and _cc_captures(cap) == []
          and not os.path.lexists(os.path.join(d, "work", "contributions")))

for valid_id in ("a", "a" * 64):
    d = _cc_workspace(); cap = tempfile.mkdtemp()
    rr = _cc_run(d, "contribute", valid_id, "pm", "qa",
                 extra_env={"CC_CAPTURE": cap, "CC_RUN_ID": valid_id})
    check("contribute/input accepts valid run id length %d" % len(valid_id),
          rr.returncode == 0 and os.path.isfile(os.path.join(
              d, "work", "contributions", valid_id, "COMPLETE.yaml")))

# C-11..14: provider failures and invalid envelopes never become accepted runs.
for label, mode, extra in (
        ("nonzero child", "valid", {"CC_MODEL_EXIT": "7"}),
        ("no output", "none", {}),
        ("empty/BOM-only output", "empty", {}),
        ("malformed YAML", "malformed", {}),
        ("stdout cap exceeded", "huge", {})):
    d = _cc_workspace(); run_id = "cc-fail-" + label.split("/")[0].replace(" ", "-").lower()
    env = {"CC_MODEL_OUTPUT": mode, "CC_RUN_ID": run_id}; env.update(extra)
    rr = _cc_run(d, "contribute", run_id, "pm", "qa", extra_env=env)
    run_dir = os.path.join(d, "work", "contributions", run_id)
    check("contribute/validation %s leaves no accepted COMPLETE marker" % label,
          rr.returncode != 0 and not os.path.exists(os.path.join(run_dir, "COMPLETE.yaml")))

# U-19: canonical serializer and Markdown escaping are byte-for-byte golden.
canonical_index = {"schema_version": 1, "run_id": "cc-canonical", "items": [
    {"item_id": "cc-canonical/pm/01", "consideration": "line one\n# heading \"quoted\"",
     "target_section": "overview", "evidence_refs": [], "human_need": "decision",
     "human_question": "Choose \"A\"\nor B?"},
    {"item_id": "cc-canonical/backend/01", "consideration": "API evidence",
     "target_section": "rules", "evidence_refs": [], "human_need": "material",
     "human_question": "Which source?"},
    {"item_id": "cc-canonical/qa/01", "consideration": "Edge case",
     "target_section": "rules", "evidence_refs": [], "human_need": "decision",
     "human_question": "Still open?"},
]}
canonical_response = {"dispositions": [
    {"item_id": "cc-canonical/pm/01", "status": "decided", "group_id": "",
     "decision": "Ship \"A\"\ncarefully", "material_refs": [], "rationale": ""},
    {"item_id": "cc-canonical/backend/01", "status": "supported", "group_id": "evidence",
     "decision": "Use source", "material_refs": ["mat"], "rationale": ""},
    {"item_id": "cc-canonical/qa/01", "status": "open", "group_id": "",
     "decision": "", "material_refs": [], "rationale": ""},
]}
canonical_outputs = CF._curation_outputs(
    "cur-canonical", "cc-canonical", "a" * 64, "b" * 64, "2026-08-03T00:00:00Z",
    canonical_index, canonical_response, {"mat": "c" * 64},
    [("overview", "Overview \"quoted\""), ("rules", "Rules"), ("out-of-scope", "Out of scope")])
for name, actual in zip(("canonical-curation.yaml", "canonical-draft-notes.md", "canonical-open-questions.md"),
                        canonical_outputs):
    with open(os.path.join(CC_FIXTURES, name), "rb") as f: expected = f.read()
    check("curate/golden %s is byte-identical" % name, actual == expected)


def _cc_load(path):
    with open(path, encoding="utf-8") as f:
        return yaml.safe_load(f)


def _cc_dump(path, value):
    _cc_write(path, yaml.safe_dump(value, sort_keys=False, allow_unicode=True,
                                   default_flow_style=False, width=10 ** 9))


def _cc_tree_digest(path):
    h = hashlib.sha256()
    for root, dirs, files in os.walk(path):
        dirs.sort(); files.sort()
        for name in files:
            p = os.path.join(root, name); rel = os.path.relpath(p, path)
            h.update(rel.encode() + b"\0")
            with open(p, "rb") as f: h.update(f.read())
    return h.hexdigest()


def _cc_path_snapshot(path):
    rows = []
    for root, dirs, files in os.walk(path, followlinks=False):
        dirs.sort(); files.sort()
        for name in dirs + files:
            item = os.path.join(root, name); st = os.lstat(item)
            rel = os.path.relpath(item, path)
            kind = "symlink" if stat.S_ISLNK(st.st_mode) else "dir" if stat.S_ISDIR(st.st_mode) else "file"
            body = os.readlink(item).encode() if kind == "symlink" else open(item, "rb").read() if kind == "file" else b""
            rows.append((rel, kind, stat.S_IMODE(st.st_mode), body))
    return rows


def _cc_accepted(work, path, stage, run_id):
    old = os.getcwd()
    try:
        os.chdir(work)
        return CF._accepted_bundle(CF.Path(path), stage, run_id)
    finally:
        os.chdir(old)


# C-02/C-05/C-10/C-18/C-19 and S-11: successful append-only contribution.
flow = _cc_workspace(); flow_cap = tempfile.mkdtemp()
rc = _cc_run(flow, "contribute", "cc-e2e", "pm", "qa",
             extra_env={"CC_CAPTURE": flow_cap, "FORCE": "1"})
contrib = os.path.join(flow, "work", "contributions", "cc-e2e")
contrib_payload = os.path.join(contrib, "payload")
contrib_marker = _cc_load(os.path.join(contrib, "COMPLETE.yaml")) if rc.returncode == 0 else {}
contrib_index = _cc_load(os.path.join(contrib_payload, "contribution-index.yaml")) if rc.returncode == 0 else {}
check("contribute/e2e executes exactly the two explicit perspectives as separate invocations",
      rc.returncode == 0 and len(_cc_captures(flow_cap)) == 2
      and sorted(os.listdir(os.path.join(contrib_payload, "perspectives"))) == ["pm.yaml", "qa.yaml"])
check("contribute/Codex keeps exact read-only sandbox argv",
      all(call["argv"][:-1] == ["exec", "--skip-git-repo-check", "--sandbox", "read-only"]
          for call in _cc_captures(flow_cap)))
check("contribute/e2e emits stable generation-qualified one-to-one index IDs",
      [x["item_id"] for x in contrib_index.get("items", [])]
      == ["cc-e2e/pm/01", "cc-e2e/qa/01"])
check("contribute/e2e COMPLETE inventory and aggregate digest validate",
      rc.returncode == 0 and _cc_accepted(flow, contrib, "contribute", "cc-e2e")[0]
      ["payload_digest_sha256"] == contrib_marker.get("payload_digest_sha256"))
all_modes_ok = stat.S_IMODE(os.stat(contrib).st_mode) == 0o700
for root, dirs, files in os.walk(contrib):
    all_modes_ok = all_modes_ok and all(stat.S_IMODE(os.stat(os.path.join(root, x)).st_mode) == 0o700 for x in dirs)
    all_modes_ok = all_modes_ok and all(stat.S_IMODE(os.stat(os.path.join(root, x)).st_mode) == 0o600 for x in files)
check("contribute/privacy uses 0700 directories and 0600 files", all_modes_ok)
before = _cc_tree_digest(contrib)
rerun_cap = tempfile.mkdtemp()
rerun = _cc_run(flow, "contribute", "cc-e2e", "pm", "qa",
                extra_env={"CC_CAPTURE": rerun_cap, "FORCE": "1"})
check("contribute/no-clobber rejects reused ID despite FORCE and preserves exact bytes",
      rerun.returncode != 0 and _cc_captures(rerun_cap) == [] and _cc_tree_digest(contrib) == before)

# S-02: same-ID reservation is atomic under a real process race.
race = _cc_workspace(); race_results = []
def _race_one():
    race_results.append(_cc_run(race, "contribute", "cc-race", "pm", "qa",
                                extra_env={"CC_MODEL_SLEEP": "0.15", "CC_RUN_ID": "cc-race"}))
threads = [threading.Thread(target=_race_one) for _ in range(2)]
for t in threads: t.start()
for t in threads: t.join()
check("contribute/concurrency same ID has exactly one winner and one pre-launch loser",
      sorted(x.returncode for x in race_results) == [0, 1]
      and os.path.isfile(os.path.join(race, "work", "contributions", "cc-race", "COMPLETE.yaml")))

parallel = _cc_workspace(); parallel_results = []
def _parallel_one(run_id):
    parallel_results.append(_cc_run(parallel, "contribute", run_id, "pm", "qa",
                                    extra_env={"CC_MODEL_SLEEP": "0.05", "CC_RUN_ID": run_id}))
threads = [threading.Thread(target=_parallel_one, args=(x,)) for x in ("cc-par-a", "cc-par-b")]
for t in threads: t.start()
for t in threads: t.join()
check("contribute/concurrency different IDs complete without cross-write",
      [x.returncode for x in parallel_results].count(0) == 2
      and all(os.path.isfile(os.path.join(parallel, "work", "contributions", x, "COMPLETE.yaml"))
              for x in ("cc-par-a", "cc-par-b")))

# Snapshot type and byte caps are enforced before any provider invocation.
for label, prepare in (
        ("symlink input", lambda d: os.symlink(os.path.join(d, "inputs", "source.md"),
                                                os.path.join(d, "inputs", "linked.md"))),
        ("input file over 10 MiB", lambda d: open(os.path.join(d, "inputs", "large.bin"), "wb").truncate(10 * 1024 * 1024 + 1))):
    d = _cc_workspace(); prepare(d); cap = tempfile.mkdtemp()
    rr = _cc_run(d, "contribute", "cc-cap", "pm", "qa", extra_env={"CC_CAPTURE": cap})
    check("contribute/snapshot rejects %s before model invocation" % label,
          rr.returncode != 0 and _cc_captures(cap) == []
          and not os.path.exists(os.path.join(d, "work", "contributions", "cc-cap", "COMPLETE.yaml")))

# U-03/U-05/U-07b/U-13 and D-01..03: attested deterministic curation and bridge.
template_path = os.path.join(contrib_payload, "human-response.template.yaml")
response = _cc_load(template_path)
response["operator_attested"] = True
response["attested_by"] = "operator"
response["attested_at"] = "2026-08-03T00:00:00Z"
_cc_write(os.path.join(flow, "inputs", "unused.txt"), "same material\n")
response["materials"] = [{"material_id": "unused", "path": "inputs/unused.txt"}]
for disp in response["dispositions"]:
    if disp["item_id"] == "cc-e2e/pm/01":
        disp["status"] = "decided"; disp["decision"] = "Ship it"
answers = os.path.join(flow, "answers.yaml"); _cc_dump(answers, response)
cur = _cc_run(flow, "curate", "cc-e2e", "cur-e2e", "answers.yaml")
cur_dir = os.path.join(flow, "work", "curations", "cur-e2e")
cur_payload = os.path.join(cur_dir, "payload")
notes = open(os.path.join(cur_payload, "draft-notes.md"), encoding="utf-8").read() if cur.returncode == 0 else ""
materials = _cc_load(os.path.join(cur_payload, "materials.yaml")) if cur.returncode == 0 else {}
check("curate/e2e accepts attestation and writes a validated COMPLETE bundle",
      cur.returncode == 0 and bool(_cc_accepted(flow, cur_dir, "curate", "cur-e2e")))
check("curate/e2e keeps decided as evidence and open only as unresolved",
      'Status: decided' in notes and 'cc-e2e/qa/01' in notes
      and notes.index("## Unresolved — not drafting evidence") < notes.index("cc-e2e/qa/01"))
check("curate/material valid unreferenced material is preserved and marked unused",
      materials.get("materials", [])[0]["sources"][0]["unused"] is True)

plain_cap = tempfile.mkdtemp()
plain = _cc_run(flow, "draft", extra_env={"CC_CAPTURE": plain_cap})
plain_prompt = _cc_captures(plain_cap)[0]["prompt"] if _cc_captures(plain_cap) else ""
check("draft/plain remains byte-identical and contains no curated bridge",
      plain.returncode == 0 and plain_prompt == _legacy_prompt("draft.md", flow)
      and "docloop-curated-input" not in plain_prompt)

draft_cap = tempfile.mkdtemp()
drafted = _cc_run(flow, "draft-curated", "cur-e2e", extra_env={"CC_CAPTURE": draft_cap})
draft_capture = _cc_captures(draft_cap)
cur_marker = _cc_load(os.path.join(cur_dir, "COMPLETE.yaml"))
notes_bytes = open(os.path.join(cur_payload, "draft-notes.md"), "rb").read()
expected_bridge = (_legacy_prompt("draft.md", flow)
    + "\n\n---\n## Verified optional curated input\n"
    + "- Curation ID: cur-e2e\n"
    + "- COMPLETE payload digest: %s\n" % cur_marker["payload_digest_sha256"]
    + "- draft-notes sha256: %s\n\n" % hashlib.sha256(notes_bytes).hexdigest()
    + "<docloop-curated-input>\n" + notes_bytes.decode("utf-8") + "</docloop-curated-input>\n")
check("draft-curated/full prompt golden includes exact verified notes bytes and three digests",
      drafted.returncode == 0 and len(draft_capture) == 1 and draft_capture[0]["prompt"] == expected_bridge)
check("draft-curated/Codex uses explicit workspace-write sandbox argv",
      len(draft_capture) == 1
      and draft_capture[0]["argv"][:-1]
          == ["exec", "--skip-git-repo-check", "--sandbox", "workspace-write"])

cur2 = _cc_run(flow, "curate", "cc-e2e", "cur-e2e-2", "answers.yaml")
curation1 = _cc_load(os.path.join(cur_payload, "curation.yaml"))
curation2_path = os.path.join(flow, "work", "curations", "cur-e2e-2", "payload", "curation.yaml")
curation2 = _cc_load(curation2_path) if cur2.returncode == 0 else {}
notes2_path = os.path.join(flow, "work", "curations", "cur-e2e-2", "payload", "draft-notes.md")
check("curate/determinism new curation ID keeps semantic digest and draft notes bytes identical",
      cur2.returncode == 0
      and curation1["semantic_digest_sha256"] == curation2.get("semantic_digest_sha256")
      and open(os.path.join(cur_payload, "draft-notes.md"), "rb").read() == open(notes2_path, "rb").read())

claude_draft_cap = tempfile.mkdtemp()
claude_draft = _cc_run(flow, "draft-curated", "cur-e2e-2", model="claude",
                       extra_env={"CC_CAPTURE": claude_draft_cap})
claude_draft_calls = _cc_captures(claude_draft_cap)
cur2_dir = os.path.join(flow, "work", "curations", "cur-e2e-2")
cur2_marker = _cc_load(os.path.join(cur2_dir, "COMPLETE.yaml"))
notes2_bytes = open(notes2_path, "rb").read()
expected_claude_draft_prompt = (_legacy_prompt("draft.md", flow)
    + "\n\n---\n## Verified optional curated input\n"
    + "- Curation ID: cur-e2e-2\n"
    + "- COMPLETE payload digest: %s\n" % cur2_marker["payload_digest_sha256"]
    + "- draft-notes sha256: %s\n\n" % hashlib.sha256(notes2_bytes).hexdigest()
    + "<docloop-curated-input>\n" + notes2_bytes.decode("utf-8") + "</docloop-curated-input>\n")
check("draft-curated/Claude uses noninteractive acceptEdits with bounded write tools and no Bash/plan",
      claude_draft.returncode == 0 and len(claude_draft_calls) == 1
      and claude_draft_calls[0]["argv"][:-1]
          == ["--permission-mode", "acceptEdits", "--tools", "Read,Glob,Grep,Edit,Write",
              "--no-session-persistence", "-p"]
      and "Bash" not in claude_draft_calls[0]["argv"]
      and "plan" not in claude_draft_calls[0]["argv"])
check("draft-curated/Claude preserves the exact validated curated prompt golden",
      len(claude_draft_calls) == 1 and claude_draft_calls[0]["prompt"] == expected_claude_draft_prompt)

extra_cap = tempfile.mkdtemp()
extra = _cc_run(flow, "draft-curated", "cur-e2e", "unverified", extra_env={"CC_CAPTURE": extra_cap})
check("draft-curated rejects extra unverified arguments before model invocation",
      extra.returncode != 0 and _cc_captures(extra_cap) == [])
with open(os.path.join(cur_payload, "draft-notes.md"), "ab") as f: f.write(b"tamper\n")
tamper_cap = tempfile.mkdtemp()
tampered = _cc_run(flow, "draft-curated", "cur-e2e", extra_env={"CC_CAPTURE": tamper_cap})
check("draft-curated rejects payload digest mismatch before model invocation",
      tampered.returncode != 0 and _cc_captures(tamper_cap) == [])

# U-02/U-03/U-06/U-07/U-08/U-09: response validation failures do not reserve curation IDs.
bad_response_cases = []
base = _cc_load(template_path)
attested = json.loads(json.dumps(base)); attested.update(operator_attested=True, attested_by="op", attested_at="now")
bad_response_cases.append(("bundle template itself", template_path, None))
bad_response_cases.append(("operator_attested false", None, base))
decided_blank = json.loads(json.dumps(attested)); decided_blank["dispositions"][0]["status"] = "decided"
bad_response_cases.append(("decided without decision", None, decided_blank))
supported_blank = json.loads(json.dumps(attested)); supported_blank["dispositions"][0]["status"] = "supported"
bad_response_cases.append(("supported without material", None, supported_blank))
dismissed_blank = json.loads(json.dumps(attested)); dismissed_blank["dispositions"][0]["status"] = "dismissed"
bad_response_cases.append(("dismissed without rationale", None, dismissed_blank))
missing_item = json.loads(json.dumps(attested)); missing_item["dispositions"].pop()
bad_response_cases.append(("missing source item", None, missing_item))
outside_material = json.loads(json.dumps(attested))
outside_material["materials"] = [{"material_id": "outside", "path": "pm-policy.yaml"}]
bad_response_cases.append(("material outside inputs", None, outside_material))
too_many_materials = json.loads(json.dumps(attested))
too_many_materials["materials"] = [{"material_id": "m%02d" % n, "path": "inputs/source.md"} for n in range(51)]
bad_response_cases.append(("51-material cap", None, too_many_materials))
for i, (label, existing, value) in enumerate(bad_response_cases):
    path = existing
    if path is None:
        path = os.path.join(flow, "bad-%d.yaml" % i); _cc_dump(path, value)
    cid = "cur-bad-%d" % i
    rr = _cc_run(flow, "curate", "cc-e2e", cid, path)
    check("curate/validation rejects %s before curation reservation" % label,
          rr.returncode != 0 and not os.path.exists(os.path.join(flow, "work", "curations", cid)))

# Hardened destination chains: a same-UID race may replace runner-owned
# directories, but must not redirect publication into the attacker's target.
for target_kind in ("diagnostics", "perspectives", "payload"):
    d = _cc_workspace(); outside = tempfile.mkdtemp(); cap = tempfile.mkdtemp()
    p = _cc_popen(d, "contribute", "cc-symlink-" + target_kind, "pm", "qa",
                  extra_env={"CC_MODEL_SLEEP": "0.5", "CC_CAPTURE": cap,
                             "CC_RUN_ID": "cc-symlink-" + target_kind})
    payload = os.path.join(d, "work", "contributions", "cc-symlink-" + target_kind, "payload")
    target = payload if target_kind == "payload" else os.path.join(payload, target_kind)
    ready = _cc_wait_for(lambda: bool(os.listdir(cap)) and os.path.isdir(target))
    swapped = False
    if ready:
        saved = target + ".saved"
        try:
            os.rename(target, saved); os.symlink(outside, target); swapped = True
        except OSError:
            pass
    try:
        p.communicate(timeout=10)
    except subprocess.TimeoutExpired:
        p.kill(); p.communicate()
    complete = os.path.join(d, "work", "contributions", "cc-symlink-" + target_kind, "COMPLETE.yaml")
    check("contribute/path-race symlinked %s cannot write outside or become accepted" % target_kind,
          ready and swapped and p.returncode != 0 and os.listdir(outside) == [] and not os.path.exists(complete))

for ancestor in ("work", "work/contributions"):
    d = _cc_workspace(); outside = tempfile.mkdtemp(); target = os.path.join(d, *ancestor.split("/"))
    if os.path.isdir(target): os.rmdir(target)
    os.makedirs(os.path.dirname(target), exist_ok=True); os.symlink(outside, target)
    cap = tempfile.mkdtemp()
    rr = _cc_run(d, "contribute", "cc-ancestor", "pm", "qa", extra_env={"CC_CAPTURE": cap})
    check("contribute/path rejects nested ancestor symlink %s without outside writes" % ancestor,
          rr.returncode != 0 and _cc_captures(cap) == [] and os.listdir(outside) == [])

# Deterministic nested snapshot swap: replace the just-created destination
# parent immediately before the runner writes the nested captured input.
nested_work = _cc_workspace()
os.makedirs(os.path.join(nested_work, "inputs", "nested"))
_cc_write(os.path.join(nested_work, "inputs", "nested", "source.md"), "nested evidence\n")
nested_payload = CF.Path(nested_work) / "work" / "nested-race" / "payload"
nested_payload.mkdir(mode=0o700, parents=True)
nested_outside = tempfile.mkdtemp(); _cc_write(os.path.join(nested_outside, "sentinel.txt"), "outside\n")
nested_before = _cc_path_snapshot(nested_outside)
original_write_exclusive = CF._write_exclusive
original_write_exclusive_below = CF._write_exclusive_below
nested_swapped = [False]
def _snapshot_swap_write(base, relative, data, mode=0o600,
                         max_bytes=CF.MAX_ARTIFACT_BYTES, create_parents=False):
    relative = CF.PurePosixPath(relative)
    if not nested_swapped[0] and CF.Path(base).name == "snapshot" and relative.as_posix() == "inputs/nested/source.md":
        parent = CF.Path(base) / "inputs" / "nested"
        parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        os.rename(parent, CF.Path(str(parent) + ".saved"))
        os.symlink(nested_outside, parent); nested_swapped[0] = True
    return original_write_exclusive_below(base, relative, data, mode=mode, max_bytes=max_bytes,
                                          create_parents=create_parents)
CF._write_exclusive_below = _snapshot_swap_write
try:
    try:
        CF._capture_snapshot(CF.Path(nested_work), nested_payload); nested_rejected = False
    except CF.FlowError:
        nested_rejected = True
finally:
    CF._write_exclusive_below = original_write_exclusive_below
check("contribute/path-race nested snapshot directory swap leaves entire outside tree unchanged",
      nested_swapped[0] and nested_rejected and _cc_path_snapshot(nested_outside) == nested_before)

# The same boundary applies to content-addressed supplemental-material output.
material_root = os.path.realpath(tempfile.mkdtemp()); material_source = os.path.join(material_root, "material.txt")
_cc_write(material_source, "material bytes\n")
material_payload = CF.Path(material_root) / "payload"; material_payload.mkdir(mode=0o700)
material_outside = tempfile.mkdtemp(); _cc_write(os.path.join(material_outside, "sentinel.txt"), "outside\n")
material_before = _cc_path_snapshot(material_outside)
material_swapped = [False]
def _material_swap_write(path, data, mode=0o600, max_bytes=CF.MAX_ARTIFACT_BYTES):
    path = CF.Path(path)
    if not material_swapped[0] and path.parent.name == "supplemental-materials":
        parent = path.parent; os.rename(parent, CF.Path(str(parent) + ".saved"))
        os.symlink(material_outside, parent); material_swapped[0] = True
    return original_write_exclusive(path, data, mode=mode, max_bytes=max_bytes)
CF._write_exclusive = _material_swap_write
try:
    try:
        CF._capture_materials(
            [{"material_id": "mat", "path": "inputs/material.txt", "source": CF.Path(material_source)}],
            material_payload, {"mat"})
        material_rejected = False
    except CF.FlowError:
        material_rejected = True
finally:
    CF._write_exclusive = original_write_exclusive
check("curate/path-race supplemental-material directory swap leaves entire outside tree unchanged",
      material_swapped[0] and material_rejected and _cc_path_snapshot(material_outside) == material_before)

# Late diagnostic swap: after pm.yaml is durably published but before temporary
# stderr cleanup, pathname-based unlink must not touch an attacker-controlled
# `.pm.stderr` in the swapped destination.
late_diag_root = os.path.realpath(tempfile.mkdtemp())
late_diag_payload = CF.Path(late_diag_root) / "payload"; late_diag_payload.mkdir(mode=0o700)
late_diagnostics = late_diag_payload / "diagnostics"
late_diag_outside = os.path.realpath(tempfile.mkdtemp())
_cc_write(os.path.join(late_diag_outside, ".pm.stderr"), "outside stderr\n")
_cc_write(os.path.join(late_diag_outside, "sentinel.txt"), "outside sentinel\n")
late_diag_before = _cc_path_snapshot(late_diag_outside)
original_write_at = CF._write_exclusive_at; late_diag_swapped = [False]
def _late_diag_write_at(parent_fd, name, display_path, data, mode, max_bytes):
    result = original_write_at(parent_fd, name, display_path, data, mode, max_bytes)
    if not late_diag_swapped[0] and name == "pm.yaml" and CF.Path(display_path).parent.name == "diagnostics":
        parent = CF.Path(display_path).parent
        os.rename(parent, CF.Path(str(parent) + ".saved")); os.symlink(late_diag_outside, parent)
        late_diag_swapped[0] = True
    return result
CF._write_exclusive_at = _late_diag_write_at
late_shim = _cc_model_shim(); old_path = os.environ.get("PATH", "")
os.environ["PATH"] = late_shim + os.pathsep + old_path
os.environ["CC_RUN_ID"] = "cc-late-diag"; os.environ["CC_PERSPECTIVE"] = "pm"
try:
    try:
        late_raw = CF._run_model("## Invocation contract\n- Run ID: cc-late-diag\n- Perspective: pm\n",
                                 "codex", CF.Path(late_diag_root), late_diagnostics, "pm", True)
        late_diag_safe = bool(late_raw)
    except CF.FlowError:
        late_diag_safe = True
    except Exception:
        late_diag_safe = False
finally:
    CF._write_exclusive_at = original_write_at
    os.environ["PATH"] = old_path
    os.environ.pop("CC_RUN_ID", None); os.environ.pop("CC_PERSPECTIVE", None)
check("contribute/cleanup late diagnostics swap leaves entire outside tree unchanged",
      late_diag_swapped[0] and late_diag_safe
      and _cc_path_snapshot(late_diag_outside) == late_diag_before)

# Late marker swap: inject immediately after COMPLETE publication, then surface
# an interrupt so rollback cleanup executes. All marker cleanup must stay bound
# to the originally opened run directory.
late_marker_root = CF.Path(os.path.realpath(tempfile.mkdtemp()))
late_run = late_marker_root / "run"; late_run.mkdir(mode=0o700); (late_run / "payload").mkdir(mode=0o700)
_cc_write(str(late_run / "INCOMPLETE"), "incomplete\n"); _cc_write(str(late_run / "payload" / "item"), "payload\n")
late_marker_outside = os.path.realpath(tempfile.mkdtemp())
_cc_write(os.path.join(late_marker_outside, "COMPLETE.yaml"), "outside complete\n")
_cc_write(os.path.join(late_marker_outside, "INCOMPLETE"), "outside incomplete\n")
late_marker_before = _cc_path_snapshot(late_marker_outside); late_marker_swapped = [False]
def _late_marker_write_at(parent_fd, name, display_path, data, mode, max_bytes):
    result = original_write_at(parent_fd, name, display_path, data, mode, max_bytes)
    if not late_marker_swapped[0] and name == "COMPLETE.yaml":
        parent = CF.Path(display_path).parent
        os.rename(parent, CF.Path(str(parent) + ".saved")); os.symlink(late_marker_outside, parent)
        late_marker_swapped[0] = True; CF._interrupted = True
    return result
old_interrupted = CF._interrupted; CF._interrupted = False; CF._write_exclusive_at = _late_marker_write_at
try:
    try:
        CF._finalize(late_run, "contribute", "cc-late-marker"); late_marker_rejected = False
    except CF.FlowError:
        late_marker_rejected = True
finally:
    CF._write_exclusive_at = original_write_at; CF._interrupted = old_interrupted
check("contribute/cleanup late marker swap leaves entire outside tree unchanged",
      late_marker_swapped[0] and late_marker_rejected
      and _cc_path_snapshot(late_marker_outside) == late_marker_before)

# Capture must reject membership and identity changes, not merely content edits.
# A direct barrier after the first complete source-state read makes this
# deterministic without depending on filesystem polling speed.
for mutation in ("addition", "removal", "replacement", "manifest", "policy"):
    d = _cc_workspace(); payload = CF.Path(d) / "work" / "barrier" / mutation / "payload"
    payload.mkdir(mode=0o700, parents=True)
    original_capture_state = CF._capture_source_state
    calls = [0]
    def _barrier_capture_state(work, mutation=mutation):
        result = original_capture_state(work); calls[0] += 1
        if calls[0] == 1:
            source = os.path.join(d, "inputs", "source.md")
            if mutation == "addition":
                _cc_write(os.path.join(d, "inputs", "added.md"), "added\n")
            elif mutation == "removal":
                os.unlink(source)
            elif mutation == "replacement":
                replacement = os.path.join(d, "inputs", "replacement.tmp")
                _cc_write(replacement, "changed source!\n"); os.replace(replacement, source)
            elif mutation == "manifest":
                with open(os.path.join(d, "manifest.yaml"), "a", encoding="utf-8") as f: f.write("# changed\n")
            else:
                with open(os.path.join(d, "pm-policy.yaml"), "a", encoding="utf-8") as f: f.write("# changed\n")
        return result
    CF._capture_source_state = _barrier_capture_state
    try:
        try:
            CF._capture_snapshot(CF.Path(d), payload); capture_rejected = False
        except CF.FlowError:
            capture_rejected = True
    finally:
        CF._capture_source_state = original_capture_state
    check("contribute/capture barrier rejects source %s between inventories" % mutation,
          capture_rejected and calls[0] == 2 and not (payload / "snapshot" / "inventory.yaml").exists())

# Diagnostics retain bounded metadata only even when provider stderr exceeds 64 KiB.
d = _cc_workspace(); stderr_canary = "stderr-secret-canary-" + ("x" * (70 * 1024))
rr = _cc_run(d, "contribute", "cc-stderr", "pm", "qa",
             extra_env={"CC_RUN_ID": "cc-stderr", "CC_MODEL_STDERR": stderr_canary})
diag_path = os.path.join(d, "work", "contributions", "cc-stderr", "payload", "diagnostics", "pm.yaml")
diag_raw = open(diag_path, "rb").read() if os.path.isfile(diag_path) else b""
diag = yaml.safe_load(diag_raw) if diag_raw else {}
check("contribute/diagnostics records stderr byte/hash/truncation without persisting body",
      rr.returncode == 0 and diag.get("stderr_bytes") == len(stderr_canary.encode())
      and diag.get("stderr_truncated") is True and stderr_canary[:24].encode() not in diag_raw
      and len(diag_raw) < 4096)

fast_shim = tempfile.mkdtemp()
fast_script = "#!/usr/bin/env python3\nimport os\nos.write(1, b'x' * (8 * 1024 * 1024))\n"
_cc_write(os.path.join(fast_shim, "codex"), fast_script, 0o755)
fast_work = _cc_workspace()
fast = _cc_popen(fast_work, "contribute", "cc-fast-cap", "pm", "qa", shim=fast_shim)
fast.communicate(timeout=10)
fast_stdout = os.path.join(fast_work, "work", "contributions", "cc-fast-cap", "payload",
                           "diagnostics", ".pm.stdout")
check("contribute/fast-exit 8MiB stdout retains at most 1MiB on disk",
      fast.returncode != 0 and os.path.isfile(fast_stdout)
      and os.path.getsize(fast_stdout) <= 1024 * 1024
      and not os.path.exists(os.path.join(fast_work, "work", "contributions", "cc-fast-cap", "COMPLETE.yaml")))

resist_shim = tempfile.mkdtemp(); resist_dir = tempfile.mkdtemp()
resist_script = r'''#!/usr/bin/env python3
import os, signal, time
root = os.environ["CC_RESIST_DIR"]
child = os.fork()
if child == 0:
    signal.signal(signal.SIGTERM, signal.SIG_IGN)
    open(os.path.join(root, "descendant.pid"), "w").write(str(os.getpid()))
    while True:
        os.write(2, b"held-pipe\n"); time.sleep(.05)
open(os.path.join(root, "provider.pid"), "w").write(str(os.getpid()))
while True: time.sleep(.05)
'''
_cc_write(os.path.join(resist_shim, "codex"), resist_script, 0o755)
resist_work = _cc_workspace()
resist = _cc_popen(resist_work, "contribute", "cc-resistant", "pm", "qa", shim=resist_shim,
                   extra_env={"CC_RESIST_DIR": resist_dir})
resist_started = _cc_wait_for(lambda: all(os.path.isfile(os.path.join(resist_dir, name))
                                          for name in ("provider.pid", "descendant.pid")))
resist_start = time.monotonic()
if resist_started: os.kill(resist.pid, signal.SIGTERM)
resist_prompt = True
try:
    resist.communicate(timeout=7)
except subprocess.TimeoutExpired:
    resist_prompt = False
    for name in ("provider.pid", "descendant.pid"):
        try: os.kill(int(open(os.path.join(resist_dir, name)).read()), signal.SIGKILL)
        except (OSError, ValueError): pass
    resist.kill(); resist.communicate()
check("contribute/TERM KILLs resistant pipe-holding descendant without hanging",
      resist_started and resist_prompt and time.monotonic() - resist_start < 7
      and resist.returncode != 0 and not os.path.exists(os.path.join(
          resist_work, "work", "contributions", "cc-resistant", "COMPLETE.yaml")))

# Strict YAML/schema boundaries: bool is not integer schema version and duplicate
# keys cannot silently replace an earlier security-critical value.
try:
    CF._validate_envelope(
        b"schema_version: true\nrun_id: cc-bool\nperspective: pm\nmodel_lineage: fake\nitems: []\n",
        "cc-bool", "pm", {"overview"}, set(), "codex")
    bool_schema_rejected = False
except CF.FlowError:
    bool_schema_rejected = True
check("contribute/schema rejects boolean schema_version", bool_schema_rejected)
try:
    CF._load_yaml_bytes(b"schema_version: 1\nschema_version: 2\n", "duplicate-key fixture")
    duplicate_yaml_rejected = False
except CF.FlowError:
    duplicate_yaml_rejected = True
check("contribute/YAML rejects duplicate mapping keys", duplicate_yaml_rejected)
try:
    CF._validate_envelope(
        b"schema_version: 1\nrun_id: cc-refs\nperspective: pm\nmodel_lineage: codex\nitems:\n"
        b"- item_id: cc-refs/pm/01\n  consideration: x\n  target_section: overview\n"
        b"  evidence_refs: [[]]\n  human_need: decision\n  human_question: q\n",
        "cc-refs", "pm", {"overview"}, {"inputs/source.md"}, "codex")
    nested_refs_flow_error = False
except CF.FlowError:
    nested_refs_flow_error = True
except Exception:
    nested_refs_flow_error = False
check("contribute/schema nested evidence_refs raises FlowError rather than TypeError", nested_refs_flow_error)

claude_work = _cc_workspace(); claude_cap = tempfile.mkdtemp()
claude_run = _cc_run(claude_work, "contribute", "cc-claude", "pm", "qa", model="claude",
                     extra_env={"CC_CAPTURE": claude_cap, "CC_RUN_ID": "cc-claude"})
claude_calls = _cc_captures(claude_cap)
claude_payload = os.path.join(claude_work, "work", "contributions", "cc-claude", "payload")
claude_meta = _cc_load(os.path.join(claude_payload, "run.yaml")) if claude_run.returncode == 0 else {}
claude_pm = _cc_load(os.path.join(claude_payload, "perspectives", "pm.yaml")) if claude_run.returncode == 0 else {}
check("contribute/Claude completes two explicit perspective invocations",
      claude_run.returncode == 0 and len(claude_calls) == 2)
check("contribute/Claude uses compatible noninteractive read-only argv without plan mode",
      all(call["argv"][:7] == ["--safe-mode", "--permission-mode", "dontAsk", "--tools",
                                     "Read,Glob,Grep", "--no-session-persistence", "--json-schema"]
             and call["argv"][-2] == "-p" and len(call["argv"]) == 10
             for call in claude_calls)
      and all("plan" not in call["argv"] for call in claude_calls))
try:
    claude_schemas = [json.loads(call["argv"][7]) for call in claude_calls] if len(claude_calls) == 2 else []
except (json.JSONDecodeError, IndexError):
    claude_schemas = []
schema_properties = claude_schemas[0].get("properties", {}) if claude_schemas else {}
item_schema = schema_properties.get("items", {}).get("items", {})
item_properties = item_schema.get("properties", {})
check("contribute/Claude static JSON Schema is positioned before -p and constrains the full envelope",
      len(claude_schemas) == 2 and claude_schemas[0] == claude_schemas[1]
      and claude_schemas[0].get("type") == "object"
      and claude_schemas[0].get("additionalProperties") is False
      and claude_schemas[0].get("required")
          == ["schema_version", "run_id", "perspective", "model_lineage", "items"]
      and schema_properties.get("schema_version") == {"type": "integer", "const": 1}
      and schema_properties.get("perspective", {}).get("enum")
          == ["backend", "frontend", "pm", "product-designer", "qa"]
      and schema_properties.get("model_lineage") == {"type": "string", "const": "claude"}
      and schema_properties.get("items", {}).get("type") == "array"
      and schema_properties.get("items", {}).get("maxItems") == 50
      and item_schema.get("type") == "object" and item_schema.get("additionalProperties") is False
      and item_schema.get("required")
          == ["item_id", "consideration", "target_section", "evidence_refs", "human_need", "human_question"]
      and item_properties.get("evidence_refs")
          == {"type": "array", "uniqueItems": True, "items": {"type": "string"}}
      and item_properties.get("human_need", {}).get("enum") == ["decision", "material", "both", "none"]
      and all(item_properties.get(key, {}).get("minLength") == 1
              for key in ("item_id", "consideration", "target_section", "human_question")))
check("contribute/Claude records exact lineage in envelope and run metadata",
      claude_meta.get("model_lineage") == "claude" and claude_pm.get("model_lineage") == "claude")
mismatch_work = _cc_workspace(); mismatch_cap = tempfile.mkdtemp()
mismatch = _cc_run(mismatch_work, "contribute", "cc-lineage", "pm", "qa", model="claude",
                   extra_env={"CC_CAPTURE": mismatch_cap, "CC_RUN_ID": "cc-lineage", "CC_LINEAGE": "codex"})
check("contribute/Claude rejects envelope claiming a different model lineage",
      mismatch.returncode != 0 and not os.path.exists(os.path.join(
          mismatch_work, "work", "contributions", "cc-lineage", "COMPLETE.yaml")))

# The synthetic policy bucket is always last, even if supplied in the middle.
sections = CF._policy_sections(
    {"project": {"doc_type": "PRD"}, "sections": []},
    {"doc_types": {"PRD": {"sections": [
        {"id": "overview", "title": "Overview"},
        {"id": "out-of-scope", "title": "Excluded"},
        {"id": "rules", "title": "Rules"},
    ]}}})
check("curate/ordering forces out-of-scope section last", [x[0] for x in sections] == ["overview", "rules", "out-of-scope"])

# Interrupt arriving inside finalization cannot remove INCOMPLETE or publish COMPLETE.
final_dir = CF.Path(tempfile.mkdtemp()) / "run"
final_dir.mkdir(mode=0o700); (final_dir / "payload").mkdir(); _cc_write(str(final_dir / "INCOMPLETE"), "incomplete\n")
_cc_write(str(final_dir / "payload" / "item.txt"), "payload\n")
old_fsync, old_interrupted = CF._fsync_tree, CF._interrupted
def _interrupting_fsync(path):
    old_fsync(path); CF._interrupted = True
CF._fsync_tree = _interrupting_fsync; CF._interrupted = False
try:
    try:
        CF._finalize(final_dir, "contribute", "cc-finalize")
        finalize_rejected = False
    except CF.FlowError:
        finalize_rejected = True
finally:
    CF._fsync_tree = old_fsync; CF._interrupted = old_interrupted
check("contribute/signal during finalize preserves INCOMPLETE and cannot publish COMPLETE",
      finalize_rejected and (final_dir / "INCOMPLETE").exists() and not (final_dir / "COMPLETE.yaml").exists())

# A draft-curated provider and its grandchild are one owned process group: TERM
# must return promptly only after both are gone, with no late ghost write.
pg_shim = tempfile.mkdtemp()
pg_script = r'''#!/usr/bin/env python3
import os, signal, time
root = os.environ["CC_PGROUP_DIR"]
def stopped(_sig, _frame):
    open(os.path.join(root, "terminated-" + str(os.getpid())), "w").write("term\n")
    raise SystemExit(0)
signal.signal(signal.SIGTERM, stopped)
child = os.fork()
if child == 0:
    open(os.path.join(root, "grandchild.pid"), "w").write(str(os.getpid()))
    while True: time.sleep(.05)
open(os.path.join(root, "provider.pid"), "w").write(str(os.getpid()))
while True: time.sleep(.05)
'''
_cc_write(os.path.join(pg_shim, "codex"), pg_script, 0o755)
pg_dir = tempfile.mkdtemp()
p = _cc_popen(flow, "draft-curated", "cur-e2e-2", shim=pg_shim, extra_env={"CC_PGROUP_DIR": pg_dir})
started = _cc_wait_for(lambda: all(os.path.isfile(os.path.join(pg_dir, x))
                                  for x in ("provider.pid", "grandchild.pid")))
if started: os.kill(p.pid, signal.SIGTERM)
prompt_exit = True
try:
    p.communicate(timeout=6)
except subprocess.TimeoutExpired:
    prompt_exit = False
    for name in ("provider.pid", "grandchild.pid"):
        try: os.kill(int(open(os.path.join(pg_dir, name)).read()), signal.SIGKILL)
        except (OSError, ValueError): pass
    p.kill(); p.communicate()
owned_pids = [int(open(os.path.join(pg_dir, name)).read()) for name in ("provider.pid", "grandchild.pid")] if started else []
owned_dead = _cc_wait_for(lambda: all(os.path.isfile(os.path.join(pg_dir, "terminated-" + str(pid)))
                                      for pid in owned_pids), timeout=3)
check("draft-curated/TERM kills and reaps provider process group before returning",
      started and prompt_exit and p.returncode != 0 and owned_dead
      and not os.path.exists(os.path.join(pg_dir, "late-write")))

# A logical symlink cwd must assemble the same legacy draft prefix for plain and
# curated draft; only the verified optional block may differ.
link_parent = tempfile.mkdtemp(); logical = os.path.join(link_parent, "logical-work"); os.symlink(flow, logical)
def _run_logical(command, cap):
    shim = _cc_model_shim(); env = dict(os.environ, DOCLOOP_MODEL="codex", CC_CAPTURE=cap)
    env["PATH"] = shim + os.pathsep + env.get("PATH", "")
    return subprocess.run(["bash", "-c", 'cd -L "$1" && launcher="$2" && shift 2 && exec bash "$launcher" "$@"',
                           "logical", logical, BIN, *command],
                          capture_output=True, text=True, env=env)
logical_plain_cap = tempfile.mkdtemp(); logical_cur_cap = tempfile.mkdtemp()
logical_plain = _run_logical(("draft",), logical_plain_cap)
logical_cur = _run_logical(("draft-curated", "cur-e2e-2"), logical_cur_cap)
logical_plain_prompt = _cc_captures(logical_plain_cap)[0]["prompt"] if _cc_captures(logical_plain_cap) else ""
logical_cur_prompt = _cc_captures(logical_cur_cap)[0]["prompt"] if _cc_captures(logical_cur_cap) else ""
check("draft-curated/symlink cwd preserves exact plain-draft prompt prefix",
      logical_plain.returncode == 0 and logical_cur.returncode == 0
      and logical_cur_prompt.startswith(logical_plain_prompt + "\n\n---\n## Verified optional curated input\n"))

# Accepted-state consumers revalidate every marker dimension and the complete
# payload tree. Each mutation starts from the same known-good contribution.
def _accepted_rejects_mutation(label, mutate):
    root = tempfile.mkdtemp(); clone = os.path.join(root, "work", "contributions", "cc-e2e")
    os.makedirs(os.path.dirname(clone)); shutil.copytree(contrib, clone, symlinks=True)
    marker_path = os.path.join(clone, "COMPLETE.yaml"); marker = _cc_load(marker_path)
    mutate(clone, marker)
    if marker is not None: _cc_dump(marker_path, marker)
    try:
        CF._accepted_bundle(CF.Path(clone), "contribute", "cc-e2e"); rejected = False
    except CF.FlowError:
        rejected = True
    except Exception:
        rejected = False
    check("contribute/accepted-state rejects %s mutation" % label, rejected)


def _marker_schema(_clone, marker): marker["schema_version"] = True
def _marker_stage(_clone, marker): marker["stage"] = "curate"
def _marker_id(_clone, marker): marker["run_id"] = "cc-other"
def _marker_path(_clone, marker): marker["payload_files"][0]["path"] = "../escape"
def _marker_inventory(_clone, marker): marker["payload_files"].pop()
def _marker_bytes(_clone, marker): marker["payload_files"][0]["bytes"] += 1
def _marker_hash(_clone, marker): marker["payload_files"][0]["sha256"] = "0" * 64
def _marker_aggregate(_clone, marker): marker["payload_digest_sha256"] = "0" * 64
def _payload_extra(clone, _marker): _cc_write(os.path.join(clone, "payload", "extra.txt"), "extra\n")
def _payload_symlink(clone, _marker):
    payload = os.path.join(clone, "payload"); outside = tempfile.mkdtemp()
    shutil.copytree(payload, os.path.join(outside, "payload")); shutil.rmtree(payload)
    os.symlink(os.path.join(outside, "payload"), payload)

for label, mutate in (
        ("boolean schema", _marker_schema), ("stage", _marker_stage), ("run ID", _marker_id),
        ("unsafe path", _marker_path), ("inventory omission", _marker_inventory),
        ("byte count", _marker_bytes), ("file hash", _marker_hash),
        ("aggregate digest", _marker_aggregate), ("unlisted payload file", _payload_extra),
        ("payload-root symlink", _payload_symlink)):
    _accepted_rejects_mutation(label, mutate)

# Material publication is content-addressed: identical bytes deduplicate while
# colliding basenames with different bytes remain distinct. Group inconsistency
# is rejected before curation reservation.
dedup_response = _cc_load(template_path)
dedup_response.update(operator_attested=True, attested_by="op", attested_at="now")
_cc_write(os.path.join(flow, "inputs", "same-a.txt"), "same\n")
_cc_write(os.path.join(flow, "inputs", "same-b.txt"), "same\n")
dedup_response["materials"] = [
    {"material_id": "same-a", "path": "inputs/same-a.txt"},
    {"material_id": "same-b", "path": "inputs/same-b.txt"},
]
dedup_response["dispositions"][0].update(status="supported", material_refs=["same-a", "same-b"])
dedup_path = os.path.join(flow, "dedup.yaml"); _cc_dump(dedup_path, dedup_response)
dedup_run = _cc_run(flow, "curate", "cc-e2e", "cur-dedup", "dedup.yaml")
dedup_materials_path = os.path.join(flow, "work", "curations", "cur-dedup", "payload", "materials.yaml")
dedup_materials = _cc_load(dedup_materials_path) if dedup_run.returncode == 0 else {}
check("curate/material identical content deduplicates with both source names retained",
      dedup_run.returncode == 0 and len(dedup_materials.get("materials", [])) == 1
      and len(dedup_materials["materials"][0]["sources"]) == 2)

os.makedirs(os.path.join(flow, "inputs", "left")); os.makedirs(os.path.join(flow, "inputs", "right"))
_cc_write(os.path.join(flow, "inputs", "left", "same.txt"), "left\n")
_cc_write(os.path.join(flow, "inputs", "right", "same.txt"), "right\n")
collision_response = _cc_load(template_path)
collision_response.update(operator_attested=True, attested_by="op", attested_at="now")
collision_response["materials"] = [
    {"material_id": "left", "path": "inputs/left/same.txt"},
    {"material_id": "right", "path": "inputs/right/same.txt"},
]
collision_response["dispositions"][0].update(status="supported", material_refs=["left", "right"])
collision_path = os.path.join(flow, "collision.yaml"); _cc_dump(collision_path, collision_response)
collision_run = _cc_run(flow, "curate", "cc-e2e", "cur-collision", "collision.yaml")
collision_materials_path = os.path.join(flow, "work", "curations", "cur-collision", "payload", "materials.yaml")
collision_materials = _cc_load(collision_materials_path) if collision_run.returncode == 0 else {}
check("curate/material same basename with different content publishes two full-hash files",
      collision_run.returncode == 0 and len(collision_materials.get("materials", [])) == 2
      and len(set(x["stored_path"] for x in collision_materials["materials"])) == 2)

group_response = _cc_load(template_path)
group_response.update(operator_attested=True, attested_by="op", attested_at="now")
for disp in group_response["dispositions"]: disp["group_id"] = "shared"
group_response["dispositions"][0].update(status="decided", decision="yes")
group_path = os.path.join(flow, "group-invalid.yaml"); _cc_dump(group_path, group_response)
group_run = _cc_run(flow, "curate", "cc-e2e", "cur-group-invalid", "group-invalid.yaml")
check("curate/group rejects members with inconsistent status/section/decision",
      group_run.returncode != 0 and not os.path.exists(os.path.join(
          flow, "work", "curations", "cur-group-invalid")))

# ── review-gate port (separate focused suite; included in the canonical full run) ──
review_gate_suite = subprocess.run(
    [sys.executable, os.path.join(os.path.dirname(os.path.abspath(__file__)), "test_review_gate.py")],
    capture_output=True, text=True,
)
if review_gate_suite.stdout:
    print(review_gate_suite.stdout, end="")
if review_gate_suite.stderr:
    print(review_gate_suite.stderr, end="", file=sys.stderr)
check("review-gate focused suite", review_gate_suite.returncode == 0)

for suite_name, suite_file in (
        ("review-gate convention suite", "test_review_gate_convention.py"),
        ("review-gate intermediate contract suite", "test_review_gate_intermediate_contract.py"),
        ("review-gate v2 receipt suite", "test_review_gate_v2.py"),
        ("review-gate v0.13 runner integration suite", "test_review_gate_runner_v013.py")):
    suite = subprocess.run(
        [sys.executable, os.path.join(os.path.dirname(os.path.abspath(__file__)), suite_file)],
        capture_output=True, text=True,
    )
    if suite.stdout:
        print(suite.stdout, end="")
    if suite.stderr:
        print(suite.stderr, end="", file=sys.stderr)
    check(suite_name, suite.returncode == 0)

print(f"\n=== {_passed} passed, {_failed} failed ===")
sys.exit(1 if _failed else 0)
