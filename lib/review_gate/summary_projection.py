#!/usr/bin/env python3
"""summary_projection.py -- the one boundary where a receipt becomes rendered facts.

Downstream port of upstream's projection layer (docauth#314, redesign S2). What lives
here is exactly what the renderer and the auditor must agree on, and nothing else:

- loading the receipt (frontmatter parsing, structural preconditions) and binding it to
  the target document
- the closed anchor grammar and `TargetDoc` (resolving source lines, refusing hash
  collisions)
- normalization, display encoding, code-span containment
  (`normalize_ws` / `display_text` / `wrap` / `unwrap`)
- the controlled vocabulary, the markers, the header literals, and the field policy table

The rule for what belongs here (upstream's, kept): share a definition only when a third
validator already covers it, or when keeping two copies has been *observed* to fail. The
consumers re-deriving a receipt independently is what this boundary exists to prevent --
each consumer interpreting the raw mapping again is how a renderer ended up stricter than
the oracle it was supposed to follow.

Output strings and inline rationale comments are upstream's, verbatim; module docstrings
and CLI help are docloop's English. See `render_human_summary.py` for why, and for the
equivalence test that makes fidelity a check rather than a claim.
"""
from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import Any

import yaml

# Anchor semantics come from one place (docauth#207 option 2: the `A<hash>` stable id
# plus legacy `L<n>`). Re-implementing them here is how a human summary and a quote
# checker end up pointing at different source lines while both look correct.
#
# The loader is the strict one that rejects duplicate keys. `yaml.safe_load` silently
# lets the last duplicate win, so appending `findings: []` to a receipt was enough to
# render it as zero findings. The oracle already reads receipts with this loader; a
# different loader here means two tools reading one file differently.
#
# `status` and `classification_verification.result` are enums the schema already owns.
# Re-listing them here would let the renderer show a human a value the schema forbids
# the moment the two copies drift (docauth#324 r4-06), so they are imported, not restated.
try:  # Package import in tests; sibling import when executed as a script.
    from .anchor_semantics import anchor_hash, norm as _norm_line
    from .validate_convention_profile import DuplicateKeyError, StrictLoader
    from .validate_review_intermediate import QUESTION_STATUSES, VERIFY_RESULTS
except ImportError:  # pragma: no cover - exercised by CLI dispatch
    from anchor_semantics import anchor_hash, norm as _norm_line
    from validate_convention_profile import DuplicateKeyError, StrictLoader
    from validate_review_intermediate import QUESTION_STATUSES, VERIFY_RESULTS


CONTROLLED_VOCAB: tuple[str, ...] = (
    "정합 안 맞음",
    "미정의",
    "모호함",
    "표기 불일치",
    "출처 불명",
    "중복",
    "근거 약함",
    "근거 없음",
)
FALLBACK_TAG = "미분류"

#: #314 항목 2 — 앵커를 원문 인용문으로 풀어 병기할 때, 풀리지 않는 경우에 쓰는
#: **고정 문자열**. 자유서술을 여기로 밀반입하지 못하게 리터럴로 못박고,
#: audit_summary_traceability.py가 같은 리터럴로만 인정한다.
ANCHOR_UNRESOLVED = "(원문에서 찾지 못함)"
ANCHOR_BLANK_LINE = "(원문 해당 행이 비어 있음)"
NO_ANCHORS = "(없음)"
ALL_TAGS: frozenset[str] = frozenset(CONTROLLED_VOCAB) | {FALLBACK_TAG}
MATCH_STRENGTHS: frozenset[str] = frozenset({"강", "약"})

SEVERITY_ORDER = {"P1": 0, "P2": 1, "P3": 2}

#: impl-r2-03 — finding_id는 마크다운 굵게(`**id**`)로 렌더되므로, id 자체에 `*`나
#: 개행이 섞이면 렌더 결과가 모호해지고 audit_summary_traceability.py의 카드 정규식이
#: 오탐/미탐할 수 있다. 실사용 관례(F-24, REC-F01 등)를 벗어나는 id는 렌더를 거부해
#: "조용히 잘못 파싱됨" 대신 "시끄럽게 거부됨"이 되게 한다.
#: **알려진 한계(comprehensive-06)**: CONTRACT.md는 finding_id의 정확한 문법을
#: 정규식으로 못박지 않는다 — 실사용 두 사례(06-account·05-orgadmin)는 전부 이
#: ASCII 패턴을 따르지만, 향후 채번 관례가 한글·특수문자를 쓰게 되면 **정당한**
#: receipt도 이 스크립트가 렌더를 거부한다. 실패 방식은 fail-closed(exit 1, 명확한
#: 사유)라 조용한 오작동은 아니다 — anti-fabrication을 뚫는 취약점이 아니라 가용성
#: 문제이므로, 실사용에서 실제로 부딪히면(현재까지 0건) 그때 문법을 넓히는 것과
#: 감사기 정규식도 함께 넓히는 회귀 테스트를 추가하는 것을 후속 이슈로 다룬다.
_SAFE_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.\-]*$")

#: 앵커 **폐쇄 문법**(Codex r1-02/r1-03). 두 형식만 인정한다 — 안정 식별자
#: `A<hash>`(#207 2안)와 레거시 행번호 `L<n>`(n>=1). 그 밖의 값은 렌더 전에
#: 구조 오류로 거부한다.
#:
#: **왜 거부인가**: 이전 판은 해석되지 않는 앵커를 전부 "못 찾음" 마커로 처리했다.
#: 그러면 `evidence_anchors: ["시스템은 완전히 안전하다"]` 같은 자유 산문이
#: `근거 시스템은 완전히 안전하다 → (원문에서 찾지 못함)`으로 **사람이 읽는 "근거"
#: 문장으로 승격**되고 감사도 통과했다 — 원문에 없는 문장을 근거 자리에 올리는
#: anti-fabrication 우회다. 형식이 아닌 값은 마커로 덮지 않고 시끄럽게 거부한다.
#:
#: **범위 앵커는 지원하지 않는다(알려진 한계, 의도적)**: `audit_quotes.py`의
#: `parse_anchors()`는 `A<h1>~A<h2>` 같은 범위를 해석하지만, 범위는 여러 행이라
#: 이 렌더의 한 줄 근거 문법으로 정확히 담을 수 없다. 부정확하게 담느니 거부한다.
#: 실사용 근거: `~/R2/_review_runs` 전체 receipt의 앵커 195개(findings·drifts·
#: variants 포함)가 100% 단일 `L<n>`이었다(2026-09-03 실측) — 범위·해시·산문 0건.
_SHA256_RE = re.compile(r"^[0-9a-fA-F]{64}$")
_SHA256_PREFIXED_RE = re.compile(r"^sha256:[0-9a-fA-F]{64}$")
_ANCHOR_HASH_RE = re.compile(r"^A[0-9a-f]{12}$")
_ANCHOR_LEGACY_RE = re.compile(r"^L([1-9]\d*)$")


def validate_anchor_token(token: str, where: str) -> str:
    """앵커 1개가 폐쇄 문법에 맞는지 검사하고 정규화된 토큰을 돌려준다."""
    shown = _normalize_ws(token)
    if _ANCHOR_HASH_RE.match(shown) or _ANCHOR_LEGACY_RE.match(shown):
        return shown
    hint = ""
    if re.search(r"[-~–]", shown):
        hint = (" — 범위 앵커는 이 렌더러가 지원하지 않는다(여러 행을 한 줄 근거로 "
                "정확히 담을 수 없어 의도적으로 거부한다)")
    raise StructuralError(
        f"{where}: anchor {token!r} is not one of the two supported forms "
        f"(L<n> with n>=1, or A<12 hex>){hint} — refusing to echo an unrecognized "
        "anchor value as human-readable evidence"
    )

_FRONTMATTER_RE = re.compile(
    r"\A(?:﻿)?---[ \t]*\r?\n(.*?)\r?\n---[ \t]*(?:\r?\n|\Z)", re.S
)

RENDERED_BY_NOTICE = (
    "이 문서는 review.md에서 자동 생성된 **파생물**이다. 손으로 고치지 않는다 —"
    " 고치고 싶으면 원본 receipt를 다시 렌더링한다."
)


#: ── 필드 정책표(POLICY) — 재설계 S1 ────────────────────────────────────────────
#: 원천 파생 필드마다 **어디에 나타나고, 감사 기대값을 어디서 다시 계산하며, 어떤
#: 강도로 대조하는지**를 한 자리에 선언한다. 이 표가 없어서 새 필드·새 인코더를
#: 적용할 때마다 자리별로 규칙이 갈렸다(r2-02 substring vs r2-04 exact vs r3 회귀
#: 3건의 "한쪽만 인코딩"). **코드 안의 이 표가 정본**이고, 설계 문서
#: `PLAN_summary_projection_redesign.md` §6의 표는 사람용 사본이다.
#:
#: **알려진 한계(재설계하지 않고 공시 — docauth#324 Q2)**: 이 표는 **선언**이며 감사
#: 런타임은 이 표를 읽지 않는다. 대조 연산은 `audit_summary_traceability.py`가 손으로
#: 유지한다(설계 8절 — 감사기는 렌더러의 주장을 믿지 않는다). 표가 보증하는 것은
#: **테스트 시점 결속**까지다: 모든 행이 스키마 필드(T8-a)와 독립 파서의 출력 키
#: (T8-b)에 묶여 있고, 모든 투영 행에 감사를 실패시키는 조작 케이스(T2)가 있다.
#: **표가 보증하지 않는 것**: 감사기가 어떤 행을 선언된 강도보다 약하게 대조하는 것
#: (강도 드리프트) — 그건 T1 험한 픽스처와 Codex 감사가 잡는다. 표를 런타임이
#: 구동하게 만드는 것(부모 설계 S2 원안)은 대조기를 표 해석기로 바꿔 독립성을 문법
#: 층에서 잃는 대가가 있어 채택하지 않았다. 재개봉 조건: 하네스가 초록인데 제품이
#: 틀린 유형이 한 라운드에 3건 이상.


class Strength(str, Enum):
    """대조 강도. 값은 사람이 읽는 표기와 같다."""

    EXACT = "exact"                              # 문자열 동일
    NORMALIZED_EXACT = "normalized-exact"        # normalize_ws 후 동일
    CONTAINED_IN = "contained-in"                # 정규화+인코딩 후 부분 문자열
    DISPLAY_ENCODED_EXACT = "display-encoded-exact"  # display_text 후 동일(공백 안 접음)
    NOT_PROJECTED = "미투영"                      # 의도적으로 싣지 않음


@dataclass(frozen=True)
class FieldPolicy:
    source: str          # 원천 경로(receipt/target/tags 기준) — 사람이 읽는 서술
    projection: str | None   # projection 필드명. None이면 미투영
    in_body: bool
    in_manifest: bool
    expectation_from: str    # 감사 기대값을 **다시 계산**하는 원천
    strength: Strength
    note: str
    #: ── 기계 결속 필드(docauth#324 r4-07) ──────────────────────────────────
    #: `source`는 산문이라 표가 구현과 갈려도 아무도 모른다 — 실제로 갈렸다
    #: (`questions[].text/detail/question`은 v2 스키마에 없는 필드였는데 T8이
    #: 이름 집합만 봐서 통과시켰다). 아래 두 필드가 그 결속을 기계로 만든다.
    #:
    #: covers: 이 행이 **책임지는 원장 레코드 필드**를 `"<record>.<field>"`로 적는다.
    #:   T8-a가 `validate_review_intermediate.RECORD_REQUIRED|RECORD_OPTIONAL`의
    #:   모든 필드가 정확히 한 번 이상 분류됐는지, 그리고 표가 **스키마에 없는
    #:   필드를 지어내지 않았는지**를 양방향으로 대조한다.
    covers: tuple[str, ...] = ()
    #: parsed_keys: 이 값이 감사기 독립 파서(`parse_body`) 출력에 나타나는 **leaf 키**
    #:   이름. T8-b가 T2 조작을 넣고 파서 출력을 diff해서, 실제로 달라진 leaf가 여기
    #:   선언된 것뿐인지 확인한다(=행이 자기가 말하는 필드를 진짜로 가리키는가).
    #:   비우면 "본문 파서의 leaf가 아님"이라는 뜻이고, 그때는 `STRUCTURAL_PROJECTIONS`
    #:   에 사유와 함께 등재해야 한다(T8-b가 강제).
    parsed_keys: tuple[str, ...] = ()


_P = FieldPolicy
POLICY: tuple[FieldPolicy, ...] = (
    _P("findings[].finding_id", "finding_id", True, True, "receipt", Strength.EXACT,
       "역참조 키. _SAFE_ID_RE로 문자집합 제한",
       covers=("finding.finding_id",), parsed_keys=("finding_id",)),
    _P("findings[].severity", "severity", True, True, "receipt", Strength.EXACT, "enum",
       covers=("finding.severity",), parsed_keys=("severity",)),
    _P("findings[].status", "status", True, True, "receipt", Strength.EXACT,
       "절 위치가 곧 값 — 본문 파서에 leaf가 없다(STRUCTURAL_PROJECTIONS)",
       covers=("finding.status",)),
    _P("tags[id].tag", "tag", True, True, "tags + vocab", Strength.EXACT,
       "통제 어휘. match_strength=약이면 미분류 강제", parsed_keys=("tag",)),
    _P("tags[id].quote", "quote", True, True, "receipt.judgment_provenance", Strength.CONTAINED_IN,
       "태깅 에이전트의 발췌라 부분 일치가 정상. 절단 위험은 사람 표본 감사에 위임(공시). "
       "leaf 이름 `quote`가 judgment_provenance와 겹쳐 경로로 한정한다(r1-03)",
       parsed_keys=("verified_cards.quote",)),
    _P("tags[id].match_strength", "match_strength", True, False, "tags", Strength.EXACT, "enum",
       parsed_keys=("strength",)),
    _P("findings[].evidence_anchors[]", "evidence_anchors", True, False, "receipt", Strength.EXACT,
       "폐쇄 문법 토큰. 순서·개수까지 일치해야 함. question의 같은 필드도 이 자리에서 "
       "같은 위치 실마리로 렌더된다(drift는 drift_anchors 행)",
       covers=("finding.evidence_anchors", "question.evidence_anchors"),
       parsed_keys=("anchor",)),
    _P("target 행의 상위 제목", "anchor_heading", True, False, "target doc 재해석",
       Strength.DISPLAY_ENCODED_EXACT,
       "사람이 찾아가는 축. 실측 99% 존재. 없으면 세그먼트 자체를 생략한다",
       parsed_keys=("heading",)),
    _P("target 행의 결정론 발췌", "anchor_excerpt", True, False, "target doc 재해석",
       Strength.DISPLAY_ENCODED_EXACT,
       "`excerpt()` 규칙(40자·마지막 공백 절단)이 결정론이라 exact 대조가 성립한다. "
       "**근거가 아니라 위치 실마리다** — 판정 근거는 judgment_provenance",
       parsed_keys=("snippet",)),
    _P("target 행 번호", "anchor_where", True, False, "target doc 재해석", Strength.EXACT, "기계값",
       parsed_keys=("where",)),
    _P("(미해석·빈 행·앵커 없음)", "anchor_marker", True, False, "target doc 재해석",
       Strength.EXACT, "고정 리터럴 3종. 해석되는 앵커를 마커로 덮으면 FAIL",
       parsed_keys=("marker",)),
    _P("findings[].counter_citation_verdict", "counter_citation_verdict", True, True,
       "receipt / 행에서 재계산", Strength.EXACT,
       "enum. 분포 문구는 행에서 재계산. counter_evidence 레코드에서 파생하므로 finding "
       "스키마 필드가 아니다(covers 없음)",
       parsed_keys=("counter_citation_verdict",)),
    _P("findings[].judgment_provenance (rejected)", "judgment_provenance", True, False, "receipt",
       Strength.DISPLAY_ENCODED_EXACT,
       "반증 근거 전체가 실리는 자리 — 부분 일치면 의미 반전 가능. leaf 이름 `quote`가 "
       "태그 인용과 겹쳐 경로로 한정한다(r1-03)",
       covers=("finding.judgment_provenance",), parsed_keys=("rejected_rows.quote",)),
    _P("findings[].narrowing.residual_claim", "residual_claim", True, False, "receipt",
       Strength.DISPLAY_ENCODED_EXACT, "부재↔존재도 대조",
       covers=("finding.narrowing",), parsed_keys=("residual_claim",)),
    _P("findings[].narrowing.withdrawn_scope", "withdrawn_scope", True, False, "receipt",
       Strength.DISPLAY_ENCODED_EXACT, "부재↔존재도 대조",
       covers=("finding.narrowing",), parsed_keys=("withdrawn_scope",)),
    _P("drifts[].detail", "drift_detail", True, False, "receipt", Strength.DISPLAY_ENCODED_EXACT,
       "원문 verbatim 자리", covers=("drift.detail",), parsed_keys=("detail",)),
    _P("drifts[].evidence_anchors", "drift_anchors", True, False, "receipt", Strength.EXACT,
       "finding과 같은 폐쇄 문법", covers=("drift.evidence_anchors",), parsed_keys=("anchors",)),
    _P("drifts[].variants[]", "drift_variants", True, False, "receipt", Strength.EXACT,
       "(notation, anchors) 튜플 다중집합, 순서 포함",
       covers=("drift.variants",), parsed_keys=("variants",)),
    _P("questions[].record_id", "question_id", True, False, "receipt", Strength.EXACT,
       "식별자", covers=("question.record_id",), parsed_keys=("record_id",)),
    # ── r4-07: 옛 `question_text` 한 행이 v2 스키마에 없는 `text/detail/question`을
    #    선언하는 동안 렌더러는 아래 세 필드를 싣고 있었다. 실제로 싣는 것마다 행을
    #    둔다 — T2 조작·T8-b 결속이 필드 단위로 성립해야 오배선이 드러난다.
    _P("questions[].convention_slot", "question_convention_slot", True, False, "receipt",
       Strength.DISPLAY_ENCODED_EXACT,
       "미확정 규약 슬롯 — 원문 파생 문자열이라 코드 스팬으로 감싼다",
       covers=("question.convention_slot",), parsed_keys=("convention_slot",)),
    _P("questions[].status", "question_status", True, False, "receipt", Strength.EXACT,
       "스키마 enum(QUESTION_STATUSES). 식별자라 감싸지 않고, 대신 렌더러와 감사 문법이 "
       "둘 다 enum 밖 값을 거부한다(r4-06)",
       covers=("question.status",), parsed_keys=("status",)),
    _P("questions[].classification_verification.result", "question_verification_result", True, False,
       "receipt", Strength.EXACT,
       "스키마 enum(VERIFY_RESULTS). status와 같은 이유로 감싸지 않고 문법으로 제한한다",
       covers=("question.classification_verification",), parsed_keys=("result",)),
    _P("파생 건수(총 N건·(N건))", "counts", True, False, "행에서 재계산", Strength.EXACT,
       "렌더러가 만드는 파생값 — 선언을 믿지 않고 센다. 선언 건수는 파서가 즉시 "
       "ParseError로 거부하므로 leaf가 없다(STRUCTURAL_PROJECTIONS)"),
    _P("receipt bytes", "receipt_sha256", True, True, "--source 재해시", Strength.EXACT, "결속",
       parsed_keys=("sha256",)),
    _P("target bytes", "target_sha256", True, False,
       "--target-doc 재해시 + receipt source_copy.sha256", Strength.EXACT, "결속(r1-01)",
       parsed_keys=("target_sha256",)),
    # r4-12: 옛 `paths` 한 행은 "인자에서 검증"이라고 선언했지만 실제로 대조되는
    # 것은 target basename뿐이었고 receipt 경로는 `audit()`에 전달조차 되지 않았다.
    # 두 자리는 검사 주체가 다르므로 행도 나눈다.
    _P("경로(target)", "paths_target", True, False, "--target-doc 인자", Strength.EXACT,
       "basename 약한 검사 — 내용 결속은 sha256이 한다",
       parsed_keys=("target_path",)),
    _P("경로(receipt)", "paths_source", True, True, "(i) 감사기", Strength.EXACT,
       "basename 약한 검사 — 내용 결속은 sha256이 한다. docauth#331에서 impl-r1-03을 "
       "뒤집어 target과 **대칭**으로 대조한다(그전에는 대조하지 않아 출처 위치 주장이 "
       "거짓일 수 있었다 — 거짓 OK 방향이라 12.6 ⓔ가 '고쳐라'를 가리켰다)",
       parsed_keys=("path",)),
    _P("--tags 제공 여부 / 재실행", "manifest_rendered_by", False, True, "없음", Strength.EXACT,
       "렌더러 자기신고 — 형식만 검사(외부 증거 없음, 공시)"),
    # ── 의도적 미투영 ──
    _P("findings[].narrowing.original_claim", None, False, False, "—", Strength.NOT_PROJECTED,
       "residual/withdrawn이 재작성 결과를 담는다", covers=("finding.narrowing",)),
    _P("findings[].narrowing.counter_quote_anchors", None, False, False, "—", Strength.NOT_PROJECTED,
       "evidence_anchors의 부분집합이라 근거 행에 이미 포함", covers=("finding.narrowing",)),
    _P("findings[].record_id·public_record_digest·*_refs", None, False, False, "—",
       Strength.NOT_PROJECTED, "기계 원장 필드. 드릴다운은 finding_id로",
       covers=("finding.record_id", "finding.public_record_digest",
               "finding.candidate_atom_refs", "finding.source_candidate_refs")),
    _P("drifts[].record_id·public_record_digest·*_refs", None, False, False, "—",
       Strength.NOT_PROJECTED, "finding과 같은 이유. 드릴다운은 앵커로",
       covers=("drift.record_id", "drift.public_record_digest",
               "drift.candidate_atom_refs", "drift.source_candidate_refs")),
    _P("questions[].public_record_digest·*_refs", None, False, False, "—",
       Strength.NOT_PROJECTED, "기계 원장 필드. 드릴다운은 record_id로",
       covers=("question.public_record_digest", "question.dependent_atom_refs",
               "question.resolution_derived_atom_refs")),
    _P("questions[].authority·scope·source", None, False, False, "—", Strength.NOT_PROJECTED,
       "선택 필드 — 있을 때만 싣는 조건부 렌더는 산출물 문법을 갈라 감사 대조를 "
       "어렵게 만든다(_render_questions_section docstring)",
       covers=("question.authority", "question.scope", "question.source")),
    _P("모든 레코드의 snapshot_id", None, False, False, "—", Strength.NOT_PROJECTED,
       "원장 내부 결속값. 사람이 보는 결속은 머리의 receipt·target sha256이 한다",
       covers=("finding.snapshot_id", "question.snapshot_id", "drift.snapshot_id")),
    _P("drifts[].co_reference_basis·comparison_ref", None, False, False, "—", Strength.NOT_PROJECTED,
       "판정 근거 산문 — 싣기 시작하면 '설명' 자리가 생긴다",
       covers=("drift.co_reference_basis", "drift.comparison_ref")),
    _P("nonissues[]", None, False, False, "—", Strength.NOT_PROJECTED,
       "원문이 'receipt 공개 대상 아님'으로 규정(PLAN §6)"),
)

#: 본문에 실리지만 **독립 파서의 leaf로는 나타나지 않는** 투영. 값이 문법 구조
#: 자체(절 소속·선언 건수)로 표현돼서, 조작하면 leaf가 바뀌는 게 아니라 파싱이
#: 실패하거나 다른 층(manifest 대조·건수 재계산)이 잡는다. T8-b가 이 목록 밖의
#: 빈 `parsed_keys`를 거부한다 — "적기 귀찮아서 비워둔 행"을 막는 자리다.
#: 본문에 실리지만 **감사가 원천과 대조하지 않는** 투영. 여기 실린 값은 사람이
#: 다른 근거(대개 sha256)로 확인해야 한다. 목록이 비어 있는 것이 이상적이고, 늘어나면
#: 그 자체가 신호다 — T8이 각 항목에 사유가 적혀 있는지, T2가 이들을 건너뛰는지
#: 강제한다. "조작 케이스를 못 만들겠으니 슬쩍 뺀다"를 막는 자리다.
UNVERIFIED_PROJECTIONS: frozenset[str] = frozenset({
    # docauth#331에서 해소돼 여기서 빠졌다 — impl-r1-03을 뒤집고 target과 대칭으로
    # basename 대조를 넣었다(LIMITS.md L9). 남는 약함(같은 이름·다른 폴더는 통과)은
    # target 쪽과 동일한 성질이라 별도 등재 대상이 아니다.
})

STRUCTURAL_PROJECTIONS: frozenset[str] = frozenset({
    "status",   # 절 위치가 곧 값 + manifest가 선언 — 어긋나면 (c) 대조가 잡는다
    "counts",   # 선언 건수. 조작하면 파서가 ParseError로 즉시 거부한다
})

#: 본문에 실리는 원천 파생 문자열은 전부 코드 스팬으로 감싼다 — T8이 이 목록과
#: POLICY의 in_body 행을 대조한다.
WRAPPED_PROJECTIONS: frozenset[str] = frozenset({
    "quote", "anchor_heading", "anchor_excerpt", "judgment_provenance", "residual_claim",
    "withdrawn_scope", "drift_detail", "drift_variants", "question_convention_slot",
})


class StructuralError(Exception):
    """정본 전제 미충족·fail-closed 사유(exit 1)."""


class UsageError(Exception):
    """인자·경로·파싱·기형 입력 오류(exit 2)."""


def _normalize_ws(text: str) -> str:
    return re.sub(r"\s+", " ", text or "").strip()


#: r2-05 — 본문에 실리는 **모든** 원천 파생 텍스트(원문 행·인용·drift·questions)에
#: 적용하는 표시 인코더. C0/C1 제어문자·ESC·양방향 서식 문자·줄 구분자를
#: `<U+XXXX>` 가시 표기로 바꾼다. 탭(U+0009)은 제외한다 — r1-04가 요구한 raw 공백
#: 보존의 대상이고 터미널을 조작하지 않는다.
#:
#: **`<!--` 같은 마크다운/HTML 메타문자는 이스케이프하지 않는다**: 이스케이프하면
#: "원문 그대로"라는 성질이 깨진다. manifest 트레일러 위조는 다른 층에서 이미
#: 막힌다 — `_last_manifest_match`가 **마지막** 트레일러를 정본으로 삼고, 본문은
#: 항상 트레일러보다 앞이라 본문에 심은 가짜 트레일러는 이길 수 없다.
#: r4-13: 제로폭·default-ignorable 문자도 "보이지 않는 문자"다 — 코드 스팬 안에서도
#: 시각적으로 숨은 채 남아, 사람이 읽는 실마리에 보이지 않는 내용을 심을 수 있다.
#: **비용(공시)**: U+200C/U+200D는 일부 문자 체계와 이모지 결합에서 의미가 있어, 그런
#: 원문에서는 가시 표기로 바뀌어 읽기 나빠진다. 이 컴포넌트의 목적(보이지 않는 것이
#: 근거 자리에 남지 않게)에서는 가시성을 택한다.
_UNSAFE_DISPLAY_RE = re.compile(
    # r5-03: Unicode Default_Ignorable 전체가 아니라 이 도메인에서 실제 위험한 것들을
    # 열거한 목록이다(완전성 미주장 — Python 표준 라이브러리에 그 속성 질의가 없다).
    "["
    "\u0000-\u0008\u000a-\u001f\u007f-\u009f"   # C0/C1(탭 U+0009 제외)
    "\u00ad\u061c\u180e"                          # SHY·ALM·MVS
    "\u200b-\u200f\u202a-\u202e"                  # 제로폭·양방향 서식
    "\u2060-\u2064\u2066-\u2069"                  # WJ·invisible operators·격리
    "\u2028\u2029"                                 # 줄·문단 구분자
    "\ufe00-\ufe0f\ufeff\ufff9-\ufffb"            # variation selector·BOM·annotation
    "]"
)


def display_text(text: str) -> str:
    """원천 파생 텍스트를 본문에 실을 때 쓰는 유일한 인코더(r2-05).

    렌더러와 감사기가 **같은 함수**를 쓴다 — 한쪽만 인코딩하면 정당한 산출물이
    오탐나거나(감사기가 raw를 기대) 조작이 통과한다(렌더러만 인코딩).
    """
    return _UNSAFE_DISPLAY_RE.sub(lambda m: f"<U+{ord(m.group()):04X}>", text or "")


_BACKTICK_RUN_RE = re.compile(r"`+")

#: ATX 마크다운 제목. setext(`===`/`---` 밑줄) 형식은 인식하지 않는다 — 실사례
#: 2건에 등장하지 않았고, 인식 못 하면 제목 없이 렌더될 뿐 오작동이 아니다(공시).
#: r4-04: CommonMark ATX 규칙을 실제로 따른다. 이전 정규식은 ①1~3칸 들여쓴 제목을 놓치고
#: ②빈 `#` 제목이 앞 제목을 리셋하지 않고 ③`# foo#`의 의미 있는 마지막 `#`를 제거하고
#: ④**fenced code 안의 `# Fake`를 진짜 제목으로 색인**했다(사람에게 틀린 위치를 보여주는
#: 결함). setext 제목은 계속 미지원(합의된 한계) — 못 알아보면 제목 없이 렌더될 뿐이다.
_HEADING_RE = re.compile(r"^ {0,3}(#{1,6})(?:[ \t]+(.*?))?[ \t]*$")
_FENCE_RE = re.compile(r"^ {0,3}(`{3,}|~{3,})[ \t]*(\S*)")
_CLOSING_SEQ_RE = re.compile(r"^(.*?)[ \t]+#+[ \t]*$")

#: 발췌 상한(**표시 문자 기준**, r5-04). 실측 인용 길이 중앙값 82자 · 최대 297자라, 전문을 실으면 읽기
#: 어렵고 자르면 의미가 뒤집힐 수 있다(부정·단서 표현이 앞 6단어 뒤에 오는 행 27%).
#: **그래서 발췌는 "근거"가 아니라 "위치를 찾아가는 실마리"로 표시한다** — 판정
#: 근거는 카드 위의 `judgment_provenance`가 그대로 담는다(사용자 결정 2026-09-03).
EXCERPT_LIMIT = 40
EXCERPT_ELLIPSIS = "…"


def excerpt(text: str) -> str:
    """표시용 발췌 — **결정론적**이어야 렌더러와 감사기가 같은 값을 만든다.

    규칙: `EXCERPT_LIMIT`자 이하면 그대로. 넘으면 그 안의 **마지막 공백**에서 자르고
    (공백이 없으면 상한에서 하드 컷) `…`를 붙인다. 잘린 결과는 항상 원문의 **접두사**
    이므로, 감사기는 원문 파일에서 다시 만든 값과 정확히 대조할 수 있다.
    """
    # r5-04: 예산은 raw 코드포인트가 아니라 **렌더된 표시 문자** 기준이다. raw로 세면
    # 제어문자 40개가 `<U+202E>` 40개(320자)로 부풀어 상한이 뜻을 잃는다. 반환값은
    # 여전히 raw 접두사다 — 표시 인코딩은 호출부가 나중에 적용한다(r4-01).
    budget, cut = 0, 0
    for index, char in enumerate(text):
        budget += len(display_text(char))
        if budget > EXCERPT_LIMIT:
            break
        cut = index + 1
    if cut >= len(text):
        return text
    window = text[:cut]
    space = window.rfind(" ")
    head = window[:space] if space > 0 else window
    return head.rstrip() + EXCERPT_ELLIPSIS


class ContainerError(StructuralError):
    """코드 스팬 컨테이너를 만들거나 벗기지 못함 — fail-closed 사유."""


def wrap(text: str) -> str:
    """원천 파생 문자열을 **인라인 코드 스팬**으로 감싼다(재설계 S1, 표시 안전성).

    왜 이스케이프가 아니라 컨테이너인가: 문자를 치환하면 "원문 그대로"가 깨진다.
    CommonMark 코드 스팬 안에서는 `<!--`·`![](…)`·`*`·`[`가 전부 리터럴이라, 렌더된
    Markdown에서 뒤 내용을 가리거나 링크·이미지로 해석되는 경로가 **범주째** 사라진다.
    파일 안의 글자는 하나도 바뀌지 않는다 — 울타리(백틱)와 필요한 경우의 패딩 공백
    하나씩만 붙는다. 감사기는 파일을 보므로 `unwrap` 후 대조하면 충실성은 그대로다.

    규칙(CommonMark 6.1):
    - 울타리 길이 = 내용 안 최장 백틱 연속 + 1 (내용의 백틱이 울타리를 닫지 못하게).
    - 내용의 첫 글자나 끝 글자가 공백 또는 백틱이면 양쪽에 공백 1개를 덧댄다.
      CommonMark는 "앞뒤가 **모두** 공백이고 전부 공백은 아닐 때 한 개씩 벗긴다"이므로
      이 패딩은 렌더 시 정확히 되돌려진다.

    **전부 공백이거나 빈 문자열은 감싸지 않는다**(CommonMark의 벗김 규칙이 적용되지
    않아 왕복이 깨진다). 호출부는 그 경우 이미 고정 마커를 쓴다.
    """
    if not text or not text.strip(" "):
        raise ContainerError(
            f"refusing to wrap {text!r} — an empty or all-space string has no round-trippable "
            "code-span form; callers must use a fixed marker instead"
        )
    longest = max((len(m.group()) for m in _BACKTICK_RUN_RE.finditer(text)), default=0)
    fence = "`" * (longest + 1)
    body = text
    if body[0] in " `" or body[-1] in " `":
        body = f" {body} "
    return f"{fence}{body}{fence}"


def unwrap(rendered: str) -> str:
    """`wrap`의 역함수. `unwrap(wrap(s)) == s`가 표 기반 테스트로 고정된다."""
    m = _BACKTICK_RUN_RE.match(rendered)
    if m is None:
        raise ContainerError(f"not a code span (no opening backtick fence): {rendered!r}")
    fence = m.group()
    if len(rendered) < 2 * len(fence) or not rendered.endswith(fence):
        raise ContainerError(f"code-span fence is not closed by an equal run: {rendered!r}")
    body = rendered[len(fence) : -len(fence)]
    inner = _BACKTICK_RUN_RE.fullmatch(body)
    if inner is not None:
        raise ContainerError(f"code-span body is only backticks: {rendered!r}")
    if len(body) >= 2 and body[0] == " " and body[-1] == " " and body.strip(" "):
        body = body[1:-1]
    # r5-02: 벗긴 결과가 렌더러가 실제로 낼 형태인지 확인한다. 이전 판은 울타리
    # 길이·패딩을 검사하지 않아 비정규 스팬(CommonMark상 코드 스팬으로 성립하지
    # 않는 형태)을 받아들였다 — "컨테이너가 보호한다"는 전제가 거기서 깨진다.
    if wrap(body) != rendered:
        raise ContainerError(
            f"code span is not in the canonical form the renderer emits: {rendered!r} "
            f"(canonical would be {wrap(body)!r})"
        )
    return body


def _display_or_none(value: Any, what: str = "field") -> str | None:
    """`None`(부재)은 그대로 두고, 값이 있으면 정규화+표시 인코딩을 한 번에 적용한다.

    r4-05: 이전 판은 `str(value)`로 강제 변환해서, 필드가 빠진 receipt가 **원문에 없는
    `None` 문자열**을 렌더했고(예: variant notation 누락 → ``- `None` — L1``) 감사도
    통과했다. 문자열이 아닌 값은 구조 오류다 — 없는 것과 잘못된 것을 구분한다.
    """
    if value is None:
        return None
    if not isinstance(value, str):
        raise StructuralError(
            f"{what} must be a string when present, got {type(value).__name__} {value!r} — "
            "coercing it would put a Python repr into the summary as if it were source text"
        )
    return display_text(_normalize_ws(value))


def _list_field(value: Any, what: str) -> list[Any]:
    """`값 or []`의 함정을 막는다(r2-06).

    `""`·`{}`·`0`·`false` 같은 falsey 비-list 값이 "정상 빈 목록"으로 승격되면,
    앵커 폐쇄 문법을 우회해 근거를 통째로 없앨 수 있다. **필드 부재/명시적 null**과
    **존재하지만 list가 아님**을 구분해, 후자는 truthiness와 무관하게 거부한다.
    """
    if value is None:
        return []
    if not isinstance(value, list):
        raise StructuralError(
            f"{what} must be a list (or absent), got {type(value).__name__} {value!r} — a "
            "falsey non-list must not be silently promoted to an empty list"
        )
    return value


class TargetDoc:
    """대상 문서(리뷰된 원문)의 행 색인 — 앵커를 실제 인용문으로 푸는 데만 쓴다.

    `audit_quotes.Source`를 그대로 쓰지 않는 이유는 두 가지다. ① 이 렌더러는 행
    단위 조회만 필요하고 통짜 텍스트 검색(`hits`)은 쓰지 않는다. ② `Source`는
    해시용으로 한 번, 파싱용으로 또 한 번 파일을 열어 그 사이 파일이 바뀌면 인용과
    sha256이 서로 다른 판본을 가리킬 수 있다(receipt 쪽에서 이미 고친 TOCTOU와 같은
    문제). 여기서는 **바이트를 한 번만 읽어** 해시와 행 색인을 둘 다 그 바이트에서
    만든다. 앵커 해시 의미론 자체는 `anchor_hash`/`norm`을 그대로 import해
    audit_quotes와 한 정의를 공유한다.

    **raw / normalized 분리(Codex r1-04)**: 이전 판은 모든 행을 `norm()`으로 접은 뒤
    그 변형을 "원문 verbatim"이라며 인용문으로 실었다 — 탭·연속 공백·앞뒤 공백이
    소실됐다(`'  직전\t\t재사용 금지  '` → `'직전 재사용 금지'`). 이제 **출력에는 raw
    행**을 쓰고, `norm()`은 **A-hash 조회와 빈 행 판정에만** 쓴다.

    **해시 충돌 fail-closed(Codex r1-06)**: `anchor_hash`는 sha256의 앞 48비트만
    쓴다. 서로 다른 정규화 텍스트가 같은 digest를 내면 이전 판은 그중 첫 행 하나를
    조용히 골라 인용문으로 실었다. 이제 digest마다 매핑된 **서로 다른 정규화
    텍스트를 전부 기억**해, 둘 이상이면 그 digest의 조회를 구조 오류로 거부한다.
    """

    def __init__(self, path: Path, raw: bytes):
        self.path = path
        self.sha256 = hashlib.sha256(raw).hexdigest()
        try:
            text = raw.decode("utf-8")
        except UnicodeDecodeError as exc:
            raise UsageError(f"{path}: not valid UTF-8: {exc}") from exc
        self.raw_lines = text.splitlines()
        self.norm_lines = [_norm_line(line) for line in self.raw_lines]
        # 각 행을 덮는 제목(가장 가까운 앞선 heading). 0-based 병렬 배열.
        self._heading_index: list[str | None] = []
        current: str | None = None
        fence: str | None = None
        for raw_line in self.raw_lines:
            fence_m = _FENCE_RE.match(raw_line)
            if fence is not None:
                if (fence_m and fence_m.group(1)[0] == fence[0]
                        and len(fence_m.group(1)) >= len(fence) and not fence_m.group(2)):
                    fence = None
                self._heading_index.append(current)
                continue
            if fence_m and not (fence_m.group(1)[0] == "`" and "`" in fence_m.group(2)):
                fence = fence_m.group(1)
                self._heading_index.append(current)
                continue
            heading = _HEADING_RE.match(raw_line)
            if heading:
                text = heading.group(2) or ""
                closing = _CLOSING_SEQ_RE.match(text)
                if closing:
                    text = closing.group(1)
                elif re.fullmatch(r"#+", text):
                    text = ""
                # 빈 제목은 "제목 없는 새 절" — 앞 제목을 계속 물려주면 틀린 위치가 된다.
                current = text.strip() or None
            self._heading_index.append(current)
        self.hash_index: dict[str, list[int]] = {}
        self._digest_texts: dict[str, set[str]] = {}
        for number, norm_line in enumerate(self.norm_lines, start=1):
            digest = anchor_hash(norm_line)
            self.hash_index.setdefault(digest, []).append(number)
            self._digest_texts.setdefault(digest, set()).add(norm_line)

    def heading_of(self, number: int) -> str | None:
        """그 행을 덮는 **가장 가까운 앞선 마크다운 제목**. 없으면 None.

        #314 항목 2의 실제 요구는 "정확한 인용"이 아니라 **사람이 그 자리를 찾아갈 수
        있어야 한다"였다 — 줄 번호는 기계에는 정확하지만 사람에게는 쓸모가 없다.
        실측(2026-09-03, 실사례 2건 178건): 근거 행의 **99%**가 제목 아래에 있다.
        """
        if not 1 <= number <= len(self.raw_lines):
            return None
        return self._heading_index[number - 1]

    def _at(self, numbers: list[int]) -> tuple[list[int], str, str]:
        """행 번호들 → (번호들, raw 텍스트, 정규화 텍스트).

        같은 digest에 여러 행이 걸리는 것은 **정규화 텍스트가 같을 때만** 허용되므로
        (다르면 `resolve`가 이미 거부한다) 인용문은 하나다. 다만 raw는 공백만 다를 수
        있어 첫 행의 raw를 대표로 싣는다 — 감사기도 같은 규칙으로 첫 행을 고르고,
        대조는 공백 정규화 후 이뤄지므로 둘의 판정이 갈리지 않는다.
        """
        first = numbers[0]
        return numbers, self.raw_lines[first - 1], self.norm_lines[first - 1]

    def resolve(self, anchor: str) -> tuple[list[int], str | None, str | None]:
        """앵커 → (원문 행 번호 목록, raw 텍스트, 정규화 텍스트).

        못 찾으면 `([], None, None)`. 앵커가 폐쇄 문법(`validate_anchor_token`)을
        통과한 값이라는 전제이며, 문법 밖 값은 여기 오기 전에 거부된다.
        """
        anchor = _normalize_ws(anchor)
        if _ANCHOR_HASH_RE.match(anchor):
            numbers = self.hash_index.get(anchor)
            if not numbers:
                return [], None, None
            texts = self._digest_texts.get(anchor) or set()
            if len(texts) > 1:
                raise StructuralError(
                    f"anchor {anchor!r} maps to {len(texts)} different normalized lines in "
                    f"{self.path} — a 48-bit anchor-hash collision cannot be resolved to one "
                    "quotation, refusing to guess (Codex r1-06)"
                )
            return self._at(list(numbers))
        legacy = _ANCHOR_LEGACY_RE.match(anchor)
        if legacy:
            number = int(legacy.group(1))
            if 1 <= number <= len(self.raw_lines):
                return self._at([number])
            return [], None, None
        return [], None, None


def receipt_target_bindings(receipt: dict[str, Any]) -> list[tuple[str, str]]:
    """receipt가 선언한 "리뷰된 대상 문서" 해시들 — (필드명, sha256) 목록.

    CONTRACT §1 입력 게이트의 `input_gate.source_copy.sha256`와 `snapshot_id`
    (`sha256:` 형식일 때)가 그것이다. 실측(2026-09-03): `~/R2/_review_runs`의
    schema_version 2 receipt 2건 모두 두 값을 가지고 있고 서로 일치한다.
    """
    def _sha256(field: str, value: Any) -> None:
        # 기형 선언을 "선언 없음"으로 조용히 넘기면, 값을 망가뜨리는 것만으로 결속
        # 검사를 끌 수 있다 — 형식이 어긋나면 넘기지 않고 거부한다(fail-closed).
        if not isinstance(value, str) or not _SHA256_RE.match(value.strip()):
            raise StructuralError(
                f"receipt {field} is {value!r}, which is not a 64-hex sha256 — a malformed "
                "binding cannot bind --target-doc and must not silently disable the check"
            )
        bindings.append((field, value.strip().lower()))

    bindings: list[tuple[str, str]] = []
    # r2-03: 이전 판은 leaf 해시만 엄격히 보고 **상위 구조가 기형이면 조용히
    # 넘겼다** — 한 선언을 문자열·리스트로 망가뜨려 검사에서 빼고 다른 선언으로
    # decoy를 결속할 수 있었다. "필드 부재"와 "필드가 있는데 모양이 틀림"을 구분해,
    # 후자는 전부 거부한다.
    input_gate = receipt.get("input_gate")
    if input_gate is not None:
        if not isinstance(input_gate, dict):
            raise StructuralError(
                f"receipt input_gate must be a mapping (or absent), got {type(input_gate).__name__}"
            )
        source_copy = input_gate.get("source_copy")
        if source_copy is not None:
            if not isinstance(source_copy, dict):
                raise StructuralError(
                    "receipt input_gate.source_copy must be a mapping (or absent), got "
                    f"{type(source_copy).__name__}"
                )
            if "sha256" not in source_copy:
                raise StructuralError(
                    "receipt input_gate.source_copy exists but declares no sha256 — an incomplete "
                    "binding must not silently disable the target-document check"
                )
            _sha256("input_gate.source_copy.sha256", source_copy.get("sha256"))
    snapshot_id = receipt.get("snapshot_id")
    if snapshot_id is not None:
        if not isinstance(snapshot_id, str):
            raise StructuralError(
                f"receipt snapshot_id must be a string (or absent), got {type(snapshot_id).__name__}"
            )
        # **오라클보다 엄격한 규칙을 여기서 새로 만들지 않는다.** `validate_input_gate.py`는
        # snapshot_id가 **해시 모양일 때만** 결속으로 취급하고, `sha256:target-snapshot`
        # 같은 기호적 id를 "실재하는 확립된 픽스처 관례"로 명시 허용한다(같은 파일 주석).
        # 이전 판은 `sha256:` 접두사만 보고 64-hex가 아니면 거부해, **레포 자신의 정본
        # 픽스처**(`tests/fixtures/review-output/v2-done.md` — 오라클은 exit 0)를 렌더 못
        # 하게 만들었다. 기호적 id는 "기형 결속"이 아니라 **결속이 아닌 것**이다.
        # "값을 망가뜨려 검사를 끈다"는 우려는 아래 "결속이 하나도 없으면 거부"가 막는다 —
        # 실제 결속인 `input_gate.source_copy.sha256`은 오라클이 이미 64-hex를 강제한다.
        if _SHA256_PREFIXED_RE.match(snapshot_id):
            _sha256("snapshot_id", snapshot_id[len("sha256:"):])
    return bindings


def bind_target_to_receipt(receipt: dict[str, Any], target: "TargetDoc") -> None:
    """`--target-doc`이 **이 receipt가 실제로 리뷰한 문서**인지 확인한다(Codex r1-01).

    이 결속이 없으면 렌더와 감사 양쪽에 같은 decoy 문서를 주는 것만으로, 원문에
    없는 문장이 "근거 인용"으로 요약에 들어가고도 감사가 통과했다 — 이 기능의
    존재 이유(사람이 원문을 안 열고도 근거를 믿을 수 있게)를 정면으로 무너뜨린다.
    선언이 하나도 없으면 통과시키지 않고 거부한다(fail-closed).
    """
    bindings = receipt_target_bindings(receipt)
    if not bindings:
        raise StructuralError(
            "receipt declares neither input_gate.source_copy.sha256 nor a sha256: "
            "snapshot_id — there is nothing to bind --target-doc to, and an unbound "
            "target document lets fabricated quotations pass (Codex r1-01)"
        )
    for field, declared in bindings:
        if declared != target.sha256:
            raise StructuralError(
                f"--target-doc {target.path} hashes to {target.sha256} but the receipt's "
                f"{field} declares {declared!r} — this is not the document that was reviewed"
            )



def _require_str(value: Any, what: str) -> str:
    if not isinstance(value, str):
        raise UsageError(f"{what} must be a string, got {type(value).__name__}")
    return value


def read_source_bytes(path: Path) -> bytes:
    # comprehensive-07: 이전 판은 파싱용으로 한 번(read_text), 해시용으로 또 한 번
    # (read_bytes) 파일을 열어 그 사이 파일이 바뀌면 파싱한 내용과 해시가 서로 다른
    # 버전을 가리킬 수 있었다(TOCTOU) — 한 번만 읽어 양쪽 다 그 바이트에서 만든다.
    try:
        return path.read_bytes()
    except OSError as exc:
        raise UsageError(f"cannot read {path}: {exc}") from exc


def load_receipt_from_bytes(path: Path, raw: bytes) -> dict[str, Any]:
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise UsageError(f"{path}: not valid UTF-8: {exc}") from exc
    match = _FRONTMATTER_RE.match(text)
    if not match:
        raise StructuralError(f"{path}: leading YAML frontmatter is required")
    try:
        data = yaml.load(match.group(1), Loader=StrictLoader)
    except DuplicateKeyError as exc:
        raise StructuralError(
            f"{path}: frontmatter has a duplicate YAML key ({exc}) — a later duplicate would "
            "silently win and could empty findings[] without any other trace"
        ) from exc
    except yaml.YAMLError as exc:
        raise UsageError(f"{path}: frontmatter YAML parse error: {exc}") from exc
    if not isinstance(data, dict) or not isinstance(data.get("doc_review_result"), dict):
        raise StructuralError(f"{path}: frontmatter must contain doc_review_result mapping")
    receipt = data["doc_review_result"]
    if receipt.get("schema_version") != 2:
        raise StructuralError(
            f"{path}: only schema_version 2 receipts are in scope for v1 "
            "(legacy v1 records are closed and out of scope for this renderer)"
        )
    findings = receipt.get("findings")
    if not isinstance(findings, list) or not all(isinstance(f, dict) for f in findings):
        raise StructuralError(f"{path}: doc_review_result.findings must be a list of mappings")
    for key in ("drifts", "questions"):
        value = receipt.get(key)
        if value is not None and (
            not isinstance(value, list) or not all(isinstance(v, dict) for v in value)
        ):
            raise StructuralError(f"{path}: doc_review_result.{key} must be a list of mappings")
    return receipt


def load_receipt(path: Path) -> dict[str, Any]:
    """`read_source_bytes` + `load_receipt_from_bytes`의 단순 합성 — 해시가 필요
    없는 호출부(audit_summary_traceability.py 등)를 위한 편의 래퍼."""
    return load_receipt_from_bytes(path, read_source_bytes(path))



def anchor_list(container: dict[str, Any], key: str, where: str) -> list[str]:
    """`container[key]`를 앵커 토큰 목록으로 읽는다 — 타입 검사(r2-06)와 폐쇄
    문법(r1-02)을 한 자리에서 적용한다. findings·drifts·variants가 모두 이 함수를
    쓴다(같은 규칙을 세 곳에 따로 쓰면 그 사이가 벌어진다 — r2-01의 뿌리)."""
    anchors = _list_field(container.get(key), f"{where}.{key}")
    if not all(isinstance(a, str) for a in anchors):
        raise StructuralError(f"{where}.{key} must be a list of strings")
    return [validate_anchor_token(a, where) for a in anchors]


def _finding_anchors(finding: dict[str, Any]) -> list[str]:
    where = str(finding.get("finding_id") or finding.get("record_id") or "<anchor list>")
    return anchor_list(finding, "evidence_anchors", where)


