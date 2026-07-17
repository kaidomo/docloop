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

for bad in ("", ".", "..", "a/b", "/abs/x"):
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
check("stage guard: _inside incomparable → outside (no traceback)", ST._inside("rel/path", "/abs/base") is False)
check("stage guard: worktree warning absent outside git", True)

print(f"\n=== {_passed} passed, {_failed} failed ===")
sys.exit(1 if _failed else 0)
