#!/usr/bin/env python3
"""render_human_summary.py -- render a done receipt into a summary a human reads.

Downstream port of the upstream renderer (docauth#314). `review.md` stays the only
canonical record; `summary.md` is a derived render, produced fresh every time and never
hand-edited -- editing one by hand is how a wrong verdict and an invented argument
reached a real review. Category tags are not a receipt field: they are post-hoc metadata
applied only at this step, and only from a fixed controlled vocabulary.

**Anti-fabrication by field structure.** A verified finding card carries exactly three
things: a controlled-vocabulary tag, a verbatim quotation lifted out of
`judgment_provenance`, and a back-reference to the finding. There is no free-prose
"explanation" field, because the incident this design answers was a sentence that
appeared in a summary and existed nowhere in the source. `match_strength` is a two-value
enum for the same reason -- free text must not re-enter under another field name. If the
quotation is not a substring of the `judgment_provenance` this script actually read
(whitespace-normalized; an empty string is never evidence), the render is refused. A
`약` (low-confidence) match is forced back to `미분류` whatever the tag said.

That is the first line of defence. `audit_summary_traceability.py` is the second: it
re-parses the rendered body itself rather than trusting the manifest trailer, so editing
only the visible text does not slip through.

**On the language of what follows.** The rendered output stays in upstream's wording,
verbatim -- the auditor matches these strings, and CONTRACT §9 examples quote them. The
inline rationale comments below are also left as upstream wrote them: they record *why
upstream decided something*, usually citing a specific review round, and translating
that is paraphrasing an argument this repo did not make. Module docstrings and CLI help
are docloop's English, as everywhere else here. Behavioural fidelity is not left to
prose either way: a test renders the same receipt through the upstream module and this
one and requires the bytes to match.
"""
from __future__ import annotations

import argparse
import hashlib
import re
import sys
from pathlib import Path
from typing import Any

import yaml

# Everything the projection already decided is imported, never re-derived here:
# the renderer's job is to lay out a projection, not to interpret a receipt a second time.
try:  # Package import in tests; sibling import when executed as a script.
    from .summary_projection import (
        ALL_TAGS,
        ANCHOR_BLANK_LINE,
        ANCHOR_UNRESOLVED,
        CONTROLLED_VOCAB,
        FALLBACK_TAG,
        MATCH_STRENGTHS,
        NO_ANCHORS,
        POLICY,
        QUESTION_STATUSES,
        RENDERED_BY_NOTICE,
        SEVERITY_ORDER,
        VERIFY_RESULTS,
        WRAPPED_PROJECTIONS,
        ContainerError,
        FieldPolicy,
        Strength,
        StructuralError,
        TargetDoc,
        UsageError,
        _display_or_none,
        _finding_anchors,
        _list_field,
        _normalize_ws,
        _require_str,
        _SAFE_ID_RE,
        anchor_list,
        bind_target_to_receipt,
        display_text,
        load_receipt,
        load_receipt_from_bytes,
        read_source_bytes,
        receipt_target_bindings,
        excerpt,
        unwrap,
        validate_anchor_token,
        wrap,
    )
except ImportError:  # pragma: no cover - exercised by CLI dispatch
    from summary_projection import (
        ALL_TAGS,
        ANCHOR_BLANK_LINE,
        ANCHOR_UNRESOLVED,
        CONTROLLED_VOCAB,
        FALLBACK_TAG,
        MATCH_STRENGTHS,
        NO_ANCHORS,
        POLICY,
        QUESTION_STATUSES,
        RENDERED_BY_NOTICE,
        SEVERITY_ORDER,
        VERIFY_RESULTS,
        WRAPPED_PROJECTIONS,
        ContainerError,
        FieldPolicy,
        Strength,
        StructuralError,
        TargetDoc,
        UsageError,
        _display_or_none,
        _finding_anchors,
        _list_field,
        _normalize_ws,
        _require_str,
        _SAFE_ID_RE,
        anchor_list,
        bind_target_to_receipt,
        display_text,
        load_receipt,
        load_receipt_from_bytes,
        read_source_bytes,
        receipt_target_bindings,
        excerpt,
        unwrap,
        validate_anchor_token,
        wrap,
    )

#: 설계 §5 Q2 — 사람이 실사용에서 검증한 9종 카테고리를 v1 vocab으로 채택.
#: 렌더러의 고정 테이블로만 존재한다(§4 의무 필드가 아니다).
def _render_locator_lines(anchors: list[str], target: TargetDoc) -> list[str]:
    """앵커 목록 → 렌더 행 목록. 인용문은 **원문 raw 행**을 기계로 뽑은 것이며,
    이 함수는 새 문장을 만들지 않는다(#314 항목 2 — 줄번호 단독 표기 금지).

    앵커는 이미 `validate_anchor_token`을 통과한 값이다 — 형식 밖 값이 마커로
    덮여 사람에게 "근거"로 노출되는 경로는 여기 도달하기 전에 닫힌다(r1-02).
    """
    if not anchors:
        return [f"  - 위치 {NO_ANCHORS}"]
    lines = []
    for anchor in anchors:
        shown = _normalize_ws(anchor)
        numbers, raw_text, norm_text = target.resolve(shown)
        if raw_text is None:
            lines.append(f"  - 위치 {shown} → {ANCHOR_UNRESOLVED}")
        elif not norm_text:
            lines.append(f"  - 위치 {shown} → {ANCHOR_BLANK_LINE}")
        else:
            where = ", ".join(f"L{n}" for n in numbers)
            # 사람이 찾아가는 실마리 = **제목 경로 + 짧은 발췌**(사용자 결정 2026-09-03).
            # 전문을 근거로 싣지 않는다 — 판정 근거는 카드 위의 judgment_provenance다.
            # 발췌는 결정론 규칙(`excerpt`)이라 감사기가 같은 값을 다시 만들어 대조한다.
            heading = target.heading_of(numbers[0])
            # r4-01: 순서가 중요하다. `display_text` 뒤에 자르면 `<U+202E>` 같은 표시
            # 토큰의 중간이 잘려 `<U+20…`처럼 **원문에 없는 문자열**이 나간다("발췌는
            # 원문의 접두사" 불변식 위반). raw에서 먼저 자르고 그 결과를 인코딩한다.
            snippet = wrap(display_text(excerpt(raw_text)))
            if heading:
                lines.append(
                    f"  - 위치 {shown} → {where} · {wrap(display_text(heading))} › {snippet}"
                )
            else:
                lines.append(f"  - 위치 {shown} → {where} › {snippet}")
    return lines


def load_tags(path: Path | None) -> dict[str, dict[str, Any]]:
    if path is None:
        return {}
    # v2r4-03: OSError만 잡으면 UTF-8이 아닌 --tags 파일이 UnicodeDecodeError로
    # 새어나가 문서화된 usage-error exit 2 대신 처리되지 않은 트레이스백(exit 1)이
    # 됐다 — review.md 쪽(load_receipt_from_bytes)은 이미 잡고 있던 것과 맞춘다.
    try:
        text = path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError) as exc:
        raise UsageError(f"cannot read {path}: {exc}") from exc
    try:
        data = yaml.safe_load(text) or {}
    except yaml.YAMLError as exc:
        raise UsageError(f"{path}: YAML parse error: {exc}") from exc
    if not isinstance(data, dict):
        raise UsageError(f"{path}: top-level document must be a mapping")
    tags = data.get("tags")
    if tags is None:
        return {}
    if not isinstance(tags, dict):
        raise UsageError(f"{path}: top-level 'tags' must be a mapping")
    for fid, entry in tags.items():
        if not isinstance(entry, dict):
            raise UsageError(f"{path}: tags[{fid!r}] must be a mapping, got {type(entry).__name__}")
    return tags


def _verified_card(finding: dict[str, Any], tag_entry: dict[str, Any] | None) -> dict[str, Any]:
    finding_id = finding.get("finding_id")
    provenance = _require_str(finding.get("judgment_provenance") or "", f"{finding_id}.judgment_provenance")
    tag = FALLBACK_TAG
    quote: str | None = None
    strength: str | None = None

    if tag_entry:
        candidate_tag = tag_entry.get("tag")
        if candidate_tag is not None:
            candidate_tag = _require_str(candidate_tag, f"{finding_id}.tag")
            if candidate_tag not in ALL_TAGS:
                raise StructuralError(
                    f"{finding_id}: tag {candidate_tag!r} is not in the controlled "
                    f"vocabulary {sorted(ALL_TAGS)}"
                )
            tag = candidate_tag

        raw_strength = tag_entry.get("match_strength")
        if raw_strength is not None:
            raw_strength = _require_str(raw_strength, f"{finding_id}.match_strength")
            if raw_strength not in MATCH_STRENGTHS:
                raise StructuralError(
                    f"{finding_id}: match_strength {raw_strength!r} is not one of "
                    f"{sorted(MATCH_STRENGTHS)} — free-form text is not permitted here "
                    "(anti-fabrication field restriction, PLAN §6)"
                )
            strength = raw_strength

        raw_quote = tag_entry.get("quote")
        if raw_quote is not None:
            raw_quote = _require_str(raw_quote, f"{finding_id}.quote")
            if not raw_quote.strip():
                raise StructuralError(
                    f"{finding_id}: quote is present but empty/whitespace-only — an empty "
                    "quote is not evidence (Codex impl-r1-05)"
                )
            if _normalize_ws(raw_quote) not in _normalize_ws(provenance):
                raise StructuralError(
                    f"{finding_id}: tag quote is not a verbatim substring of "
                    "judgment_provenance — refusing to render a fabricated citation"
                )
            # r2-04: 여기서 **한 번만** 정규화해 본문·manifest가 같은 값을 쓰게 한다.
            # 이전 판은 본문만 접고 manifest에는 원본을 실어, 다중행·연속 공백이 든
            # 정당한 인용이 감사기의 (f) manifest↔본문 대조에서 오탐 실패했다.
            # r3 회귀: 이전 판은 본문 렌더 시점에만 인코딩해 manifest에는 raw가
            # 남았고, 감사기의 (f) 본문↔manifest 대조가 정상 산출물을 거부했다.
            # 정규화와 표시 인코딩을 여기서 함께, 한 번만 적용한다.
            quote = display_text(_normalize_ws(raw_quote))

        # 설계 §6 r2-05: 저신뢰(약) 매칭은 태그를 강제로 미분류로 내린다. 인용·강도는
        # 드릴다운·사람 표본 감사를 위해 그대로 보존한다(#314 §7 열린 결정 5).
        if strength == "약":
            tag = FALLBACK_TAG

        if tag != FALLBACK_TAG and quote is None:
            raise StructuralError(
                f"{finding_id}: tag {tag!r} was assigned without a quote — a controlled-"
                "vocabulary tag requires verbatim evidence (Codex impl-r1-05)"
            )

    return {
        "finding_id": finding_id,
        "severity": finding.get("severity"),
        "status": finding.get("status"),
        "tag": tag,
        "quote": quote,
        "match_strength": strength,
        # #314 항목 2: verified 카드에서 앵커가 통째로 빠져 있었다 — 사람이 원문을
        # 열지 않고는 무슨 얘기인지 알 수 없던 원인.
        "evidence_anchors": _finding_anchors(finding),
    }


def _rejected_row(finding: dict[str, Any]) -> dict[str, Any]:
    narrowing = finding.get("narrowing") or {}
    if not isinstance(narrowing, dict):
        raise StructuralError(f"{finding.get('finding_id')}: narrowing must be a mapping")
    anchors = _finding_anchors(finding)
    return {
        "finding_id": finding.get("finding_id"),
        "severity": finding.get("severity"),
        "status": finding.get("status"),
        "counter_citation_verdict": finding.get("counter_citation_verdict"),
        "evidence_anchors": list(anchors),
        # r1-03/r2-03 반영: 앵커 1개·verdict만으론 "왜"를 재구성 못 한다 — narrowing이
        # 있으면 그 구조화 필드를, 없으면(verdict: full) judgment_provenance 자체가
        # 이미 반증 근거를 담고 있으므로 그걸 verbatim으로 싣는다(창작 없음).
        # r3 회귀: 표시 인코딩을 여기서 한 번만 적용한다(렌더 지점에서 감싸면
        # 감사 기대값과 값이 갈린다).
        "residual_claim": _display_or_none(narrowing.get("residual_claim")),
        "withdrawn_scope": _display_or_none(narrowing.get("withdrawn_scope")),
        "judgment_provenance": _display_or_none(finding.get("judgment_provenance")),
    }


def _render_verified_section(cards: list[dict[str, Any]], target: TargetDoc) -> str:
    lines: list[str] = []
    by_severity: dict[str, list[dict[str, Any]]] = {"P1": [], "P2": [], "P3": []}
    for card in cards:
        by_severity.setdefault(card["severity"], []).append(card)
    for severity in sorted(by_severity, key=lambda s: SEVERITY_ORDER.get(s, 99)):
        items = by_severity[severity]
        if not items:
            continue
        lines.append(f"### {severity} ({len(items)}건)\n")
        by_tag: dict[str, list[dict[str, Any]]] = {}
        for card in items:
            by_tag.setdefault(card["tag"], []).append(card)
        for tag in sorted(by_tag, key=lambda t: (t == FALLBACK_TAG, t)):
            group = by_tag[tag]
            lines.append(f"#### {tag} ({len(group)}건)\n")
            for card in group:
                strength_note = (
                    f" · 매칭 {card['match_strength']}" if card.get("match_strength") else ""
                )
                lines.append(f"- **{card['finding_id']}**{strength_note}")
                if card["quote"]:
                    # 인용은 카드 생성 시점에 이미 정규화됐다(r2-04) — 여기서 다시
                    # 접지 않는다. 표시 인코딩만 적용한다(r2-05).
                    lines.append(f"  > {wrap(card['quote'])}")
                lines.extend(_render_locator_lines(card["evidence_anchors"], target))
                lines.append("")
    return "\n".join(lines)


def _render_rejected_section(rows: list[dict[str, Any]], target: TargetDoc) -> str:
    if not rows:
        return "반증(rejected)된 항목 없음.\n"
    verdict_counts: dict[str, int] = {}
    for row in rows:
        verdict_counts[row["counter_citation_verdict"]] = (
            verdict_counts.get(row["counter_citation_verdict"], 0) + 1
        )
    dist = " · ".join(f"{k}: {v}" for k, v in sorted(verdict_counts.items()))
    lines = [f"총 {len(rows)}건 반증됨 (verdict 분포 — {dist})\n"]
    for row in rows:
        lines.append(f"- **{row['finding_id']}** ({row['severity']}, {row['counter_citation_verdict']})")
        # v2r3-02: 앵커 문자열 자체에 개행이 섞여 있으면(원문 오탈자·복붙 등) 한 줄
        # 규약이 깨져 감사기가 다시 파싱하지 못한다 — 인용과 같은 관례로 공백 정규화.
        rendered_anchors = ", ".join(_normalize_ws(a) for a in row["evidence_anchors"]) or NO_ANCHORS
        lines.append(f"  - 근거 앵커: {rendered_anchors}")
        lines.extend(_render_locator_lines(row["evidence_anchors"], target))
        if row["residual_claim"]:
            lines.append(f"  - 잔여 주장(narrowing): {wrap(row['residual_claim'])}")
        if row["withdrawn_scope"]:
            lines.append(f"  - 철회된 범위: {wrap(row['withdrawn_scope'])}")
        if row["judgment_provenance"]:
            lines.append(f"  > {wrap(row['judgment_provenance'])}")
        lines.append("")
    return "\n".join(lines)


def _render_drift_section(drifts: list[dict[str, Any]], target: TargetDoc) -> str:
    if not drifts:
        return "표기·용어 드리프트 없음.\n"
    lines = [f"총 {len(drifts)}건 (drift count = len(drifts), CONTRACT.md:434)\n"]
    for drift in drifts:
        drift_where = str(drift.get("record_id") or "<drift>")
        detail = _display_or_none(drift.get("detail"), f"{drift_where}.detail")
        if not detail:
            raise StructuralError(
                f"{drift.get('record_id')}.detail must be a nonempty string (schema requires it)"
            )
        # comprehensive2-03: 앵커 목록이 비어 있으면 빈 문자열을 그대로 렌더했는데,
        # rejected 행("(없음)" fallback)과 형식이 달라 감사기 정규식이 최소 1글자를
        # 요구할 경우 정당한 receipt가 오탐 실패했다 — rejected와 같은 관례로 통일.
        # v2r3-02: 앵커 문자열에 개행이 섞이면 한 줄 규약이 깨지므로 rejected 앵커와
        # 같은 관례로 공백 정규화한다.
        # r1-02와 같은 경로: drift 앵커도 사람에게 그대로 노출되므로 같은 폐쇄
        # 문법을 적용한다(실측상 실사용 drift·variant 앵커도 전부 L<n>이다).
        drift_anchor_tokens = anchor_list(drift, "evidence_anchors", drift_where)
        anchors = ", ".join(drift_anchor_tokens) or NO_ANCHORS
        lines.append(f"- {wrap(detail)} (앵커: {anchors})")
        # S3: drift도 finding과 같은 위치 실마리를 받는다 — 앵커 목록만으로는 사람이
        # 어느 자리인지 알 수 없다는 #314 항목 2의 문제가 drift에도 똑같이 있었다.
        lines.extend(_render_locator_lines(drift_anchor_tokens, target))
        for variant in drift.get("variants") or []:
            if not isinstance(variant, dict):
                raise StructuralError("drift variant entries must be mappings")
            notation = _display_or_none(variant.get("notation"), f"{drift_where} variant.notation")
            if not notation:
                raise StructuralError(f"{drift_where} variant.notation must be a nonempty string")
            v_anchors = ", ".join(anchor_list(variant, "anchors", f"{drift_where} variant"))
            lines.append(f"  - {wrap(notation)} — {v_anchors}")
        lines.append("")
    return "\n".join(lines)


def _require_enum(value: Any, allowed: set[str], where: str) -> str:
    """스키마 enum 값을 **컨테이너 밖에 싣기 전에** 검사한다(docauth#324 r4-06).

    `status`·`classification_verification.result`는 원천 파생 문자열인데도 코드 스팬
    밖에 렌더된다 — 식별자라 감싸면 원문처럼 보이기 때문이다. 감싸지 않는 대신
    **값 집합을 스키마 정본(`validate_review_intermediate`)으로 제한한다**. 이 검사가
    없으면 `status: "*open*"`이나 `result: "<script>x</script>"`가 사람이 읽는 요약에
    그대로 나간다. done 오라클이 그런 receipt를 걸러 준다는 전제는 코드가 아니라
    관례라서(T6 결정: 렌더러는 오라클을 게이트하지 않는다) 여기서 다시 막는다.
    """
    text = _require_str(value, where)
    if text not in allowed:
        raise StructuralError(
            f"{where} {text!r} is not one of {sorted(allowed)} — refusing to render an "
            "out-of-schema enum value outside the code-span container"
        )
    return text


def _render_questions_section(questions: list[dict[str, Any]], target: TargetDoc) -> str:
    """미결(questions) 절 — 재설계 S3.

    이전 판은 `text`/`detail`/`question` 필드를 찾고, 없으면 원본 dict의 `repr`를
    통째로 실었다. 그런데 **v2 question 레코드에는 그 필드들이 아예 없다**
    (`validate_review_intermediate.py:74-78`의 필수 집합: `record_id·status·
    convention_slot·dependent_atom_refs·resolution_derived_atom_refs·snapshot_id·
    evidence_anchors·classification_verification·public_record_digest`).
    그래서 정당한 question이 전부 repr 폴백으로 떨어졌고, 그 앵커는 폐쇄 문법 검사도
    위치 해석도 받지 못했다. 이제 **필수 필드만** 읽고 finding과 같은 위치 실마리를 붙인다.

    `authority`/`scope`/`source`는 필수 집합에 없어(선택 필드) 싣지 않는다 — 있을 때만
    싣는 조건부 렌더는 산출물 문법을 갈라 감사기 대조를 어렵게 만든다.
    """
    if not questions:
        return "미확정 규약에 걸린 미결 없음.\n"
    lines = [f"총 {len(questions)}건 (severity 없음 — CONTRACT §3)\n"]
    for question in questions:
        record_id = _require_str(question.get("record_id"), "question.record_id")
        if not _SAFE_ID_RE.match(record_id):
            raise StructuralError(
                f"question record_id {record_id!r} does not match {_SAFE_ID_RE.pattern}"
            )
        slot = _require_str(question.get("convention_slot"), f"{record_id}.convention_slot")
        status = _require_enum(
            question.get("status"), QUESTION_STATUSES, f"{record_id}.status"
        )
        verification = question.get("classification_verification")
        if not isinstance(verification, dict):
            raise StructuralError(
                f"{record_id}.classification_verification must be a mapping (schema requires it)"
            )
        result = _require_enum(
            verification.get("result"), VERIFY_RESULTS,
            f"{record_id}.classification_verification.result",
        )
        lines.append(
            f"- **{record_id}** ({wrap(display_text(_normalize_ws(slot)))} · {status} · 검증 {result})"
        )
        lines.extend(
            _render_locator_lines(anchor_list(question, "evidence_anchors", record_id), target)
        )
        lines.append("")
    return "\n".join(lines)


def _render_manifest(
    receipt_path: Path,
    receipt_sha256: str,
    cards: list[dict[str, Any]],
    rejected_rows: list[dict[str, Any]],
    rerun: bool,
    agent_invoked: bool,
) -> str:
    # 설계 §5 Q3의 opt-in disclosure 계약 shape를 그대로 따른다 — Codex impl-r1-08:
    # source_receipt는 rendered_by 아래 nested, agent_count는 태깅 에이전트가 실제로
    # 호출됐는지(--tags 제공 여부)를 반영한다(개별 카드의 quote 유무가 아니다 —
    # 에이전트가 호출됐지만 특정 finding을 미분류로 판단한 것과, 애초에 호출 안 한
    # 것은 disclosure 관점에서 다른 사실이다).
    manifest = {
        "rendered_by": {
            "agent_count": 1 if agent_invoked else 0,
            "source_receipt": {"path": str(receipt_path), "sha256": receipt_sha256},
            "rerun": rerun,
        },
        "items": [
            {
                "finding_id": c["finding_id"],
                "severity": c["severity"],
                "status": c["status"],
                "tag": c["tag"],
                "quote": c["quote"],
            }
            for c in cards
        ]
        + [
            {
                "finding_id": r["finding_id"],
                "severity": r["severity"],
                "status": r["status"],
                "counter_citation_verdict": r["counter_citation_verdict"],
            }
            for r in rejected_rows
        ],
    }
    dumped = yaml.safe_dump(manifest, allow_unicode=True, sort_keys=False)
    return f"<!-- render-manifest\n{dumped}-->\n"


def render(
    receipt_path: Path,
    receipt: dict[str, Any],
    receipt_sha256: str,
    tags: dict[str, dict[str, Any]],
    rerun: bool,
    agent_invoked: bool,
    target: TargetDoc,
) -> str:
    # r1-01: 인용을 뽑기 **전에** 이 대상 문서가 정말 이 receipt가 리뷰한 문서인지 확인한다.
    bind_target_to_receipt(receipt, target)

    findings = receipt["findings"]
    verified = [f for f in findings if f.get("status") == "verified"]
    rejected = [f for f in findings if f.get("status") == "rejected"]
    unknown = [f for f in findings if f.get("status") not in ("verified", "rejected")]
    if unknown:
        raise StructuralError(
            "findings with status outside {verified, rejected} found: "
            f"{[f.get('finding_id') for f in unknown]} — receipt is not done "
            "(CONTRACT §0 requires every finding to be verified or rejected)"
        )

    for f in findings:
        fid = f.get("finding_id")
        if not isinstance(fid, str) or not _SAFE_ID_RE.match(fid):
            raise StructuralError(
                f"finding_id {fid!r} does not match the expected identifier pattern "
                f"{_SAFE_ID_RE.pattern} — refusing to render (impl-r2-03: unusual "
                "characters would make the rendered card ambiguous to re-parse)"
            )

    cards = [_verified_card(f, tags.get(f.get("finding_id"))) for f in verified]
    rejected_rows = [_rejected_row(f) for f in rejected]

    parts = [
        "# 사람이 읽는 요약\n",
        f"{RENDERED_BY_NOTICE}\n",
        f"정본: `{receipt_path}` (sha256:{receipt_sha256})\n",
        # #314 항목 2: 인용문이 어느 판본의 원문에서 나왔는지 결속한다 — 이 줄이
        # 없으면 인용은 검산할 수 없는 문자열일 뿐이다.
        f"대상 문서: `{target.path}` (sha256:{target.sha256})\n",
        "\n## 검증된 지적\n",
        _render_verified_section(cards, target),
        "\n## 반증된 지적\n",
        _render_rejected_section(rejected_rows, target),
        "\n## 표기·용어 드리프트\n",
        _render_drift_section(list(_list_field(receipt.get("drifts"), "receipt.drifts")), target),
        "\n## 미확정 규약에 걸린 미결\n",
        _render_questions_section(list(_list_field(receipt.get("questions"), "receipt.questions")), target),
        "\n---\n",
        _render_manifest(receipt_path, receipt_sha256, cards, rejected_rows, rerun, agent_invoked),
    ]
    return "\n".join(parts)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("review", type=Path, help="the done receipt to render (review.md)")
    parser.add_argument("--output", type=Path, required=True, help="where to write summary.md")
    parser.add_argument("--tags", type=Path, default=None, help="tag file from the tagging step (optional; everything renders as 미분류 without it)")
    parser.add_argument(
        "--target-doc",
        type=Path,
        required=True,
        help="the full review target document -- the source evidence_anchors are resolved against",
    )
    parser.add_argument("--force", action="store_true", help="re-render over an existing output (refused without it; a re-render says so in the output)")
    args = parser.parse_args(argv)

    rerun = args.output.exists()
    if rerun and not args.force:
        print(f"USAGE-ERROR: {args.output} already exists — pass --force to re-render", file=sys.stderr)
        return 2

    try:
        raw = read_source_bytes(args.review)
        receipt_sha256 = hashlib.sha256(raw).hexdigest()
        receipt = load_receipt_from_bytes(args.review, raw)
        tags = load_tags(args.tags)
        target = TargetDoc(args.target_doc, read_source_bytes(args.target_doc))
        output_text = render(
            args.review,
            receipt,
            receipt_sha256,
            tags,
            rerun,
            agent_invoked=args.tags is not None,
            target=target,
        )
    except UsageError as exc:
        print(f"USAGE-ERROR: {exc}", file=sys.stderr)
        return 2
    except StructuralError as exc:
        print(f"FAIL: {exc}", file=sys.stderr)
        return 1

    try:
        args.output.write_text(output_text, encoding="utf-8")
    except OSError as exc:
        print(f"USAGE-ERROR: cannot write {args.output}: {exc}", file=sys.stderr)
        return 2
    print(f"OK: rendered {args.output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
