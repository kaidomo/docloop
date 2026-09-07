#!/usr/bin/env python3
"""audit_summary_traceability.py -- re-verify a rendered summary against its receipt.

Downstream port of upstream's independent auditor (docauth#314). `render_human_summary.py`
is the first line of defence and refuses to render what it cannot ground; this is the
second, and its whole value is that it does not trust the first. It re-reads the
**visible rendered body** -- not the manifest trailer the renderer wrote -- and checks it
back against the receipt and the target document.

That distinction is the reason this file exists: an earlier design audited the trailer
only, so editing the body a human actually reads passed inspection. What is checked:
finding ids exist, severity and status are not re-declared, tags belong to the controlled
vocabulary, quotations are verbatim, and the rendered source quotations are pulled out of
the target document again -- anchor tokens in the receipt's declared order, no resolvable
anchor hidden behind a marker, the target document bound to the receipt's declared hash,
and severity and tag groups obeying the renderer's uniqueness and ordering rules. All
fail-closed. Quotation comparison normalizes whitespace, because a difference in spacing
is not a fabrication and a trailing space a transport stripped should not read as one.

**What it does not check**: entailment -- whether a tag actually follows from the
quotation it cites. Upstream states that as outside machine verification and covers it by
human sampling, and porting does not change it.

Module docstring and CLI help are docloop's English; output strings and inline rationale
comments are upstream's, verbatim, for the reasons `render_human_summary.py` records.
Fidelity is a check rather than a claim: a fixture requires byte-identical output against
upstream, and the tamper cases are compared against upstream's own verdicts.
"""
from __future__ import annotations

import argparse
import hashlib
import os
import re
import sys
from collections import Counter
from pathlib import Path
from typing import Any

import yaml

try:  # Package import in tests; sibling import when executed as a script.
    from .validate_convention_profile import DuplicateKeyError, StrictLoader
    from .summary_projection import (
        ALL_TAGS,
        ANCHOR_BLANK_LINE,
        ANCHOR_UNRESOLVED,
        FALLBACK_TAG,
        MATCH_STRENGTHS,
        NO_ANCHORS,
        QUESTION_STATUSES,
        RENDERED_BY_NOTICE,
        SEVERITY_ORDER,
        VERIFY_RESULTS,
        ContainerError,
        StructuralError as RenderStructuralError,
        TargetDoc,
        _display_or_none,
        _list_field,
        anchor_list,
        bind_target_to_receipt,
        display_text,
        excerpt,
        load_receipt_from_bytes,
        read_source_bytes,
        unwrap,
        validate_anchor_token,
    )
except ImportError:  # pragma: no cover - exercised by CLI dispatch
    from validate_convention_profile import DuplicateKeyError, StrictLoader
    from summary_projection import (
        ALL_TAGS,
        ANCHOR_BLANK_LINE,
        ANCHOR_UNRESOLVED,
        FALLBACK_TAG,
        MATCH_STRENGTHS,
        NO_ANCHORS,
        QUESTION_STATUSES,
        RENDERED_BY_NOTICE,
        SEVERITY_ORDER,
        VERIFY_RESULTS,
        ContainerError,
        StructuralError as RenderStructuralError,
        TargetDoc,
        _display_or_none,
        _list_field,
        anchor_list,
        bind_target_to_receipt,
        display_text,
        excerpt,
        load_receipt_from_bytes,
        read_source_bytes,
        unwrap,
        validate_anchor_token,
    )

_ID = r"[A-Za-z0-9][A-Za-z0-9_.\-]*"

# v2r2-04: 인용문이 원문 verbatim이기만 하면 되므로 우연히 리터럴 "-->"를 포함할 수
# 있다(예: 원문이 화살표 표기 자체를 다루는 경우). render_human_summary.py의
# _render_manifest는 yaml.safe_dump로 만든 내용을 그대로 싣는데, 그 안의 문자열
# 값에 낀 "-->"는 항상 줄 중간에(다른 텍스트와 같은 줄에) 나타나는 반면, 트레일러를
# 실제로 닫는 "-->"는 항상 그 줄에 단독으로만 나타난다(닫기 직전 dumped 텍스트가
# 개행으로 끝나고, f"...{dumped}-->\n"로 이어붙이기 때문). 그래서 첫 "-->"에서
# 멈추던 non-greedy 매칭 대신, 줄 시작에 단독으로 있는 "-->"만 닫는 지점으로 인정한다.
_MANIFEST_RE = re.compile(r"<!--\s*render-manifest\s*\n(.*?)^-->[ \t]*$", re.S | re.M)

_PREAMBLE_TITLE_RE = re.compile(r"^# 사람이 읽는 요약$")
_SOURCE_BINDING_RE = re.compile(r"^정본: `(?P<path>.+?)` \(sha256:(?P<sha256>[0-9a-f]{64})\)$")

_TOP_SECTION_HEADERS = (
    "## 검증된 지적",
    "## 반증된 지적",
    "## 표기·용어 드리프트",
    "## 미확정 규약에 걸린 미결",
)

_SEVERITY_HEADER_RE = re.compile(r"^### (?P<severity>P[123]) \((?P<count>\d+)건\)$")
_TAG_HEADER_RE = re.compile(rf"^#### (?P<tag>.+?) \((?P<count>\d+)건\)$")
_CARD_RE = re.compile(rf"^- \*\*(?P<fid>{_ID})\*\*(?: · 매칭 (?P<strength>\S+))?$")
_QUOTE_RE = re.compile(r"^  > (?P<quote>.+)$")

#: #314 항목 2 — 앵커를 원문 인용문으로 푼 행. 세 형태만 인정한다(자유서술 금지):
#: 해석됨 `→ L12, L40: 원문 텍스트` · 미해석 · 빈 행 · 앵커 없음.
#: 앵커 토큰 자체에 공백이 섞일 수 있다 — 개별 앵커 문자열에 리터럴 개행이 든
#: 정당한 receipt를 렌더러가 공백으로 접어 한 줄을 보장하기 때문이다(v2r3-02).
#: 그래서 `\S+`가 아니라 구분자 ` → ` 앞까지를 비탐욕으로 잡는다. 앵커 안에 그
#: 구분자 자체가 들어 있으면 잘린 토큰이 receipt와 달라져 (h)에서 fail-closed다.
#: 위치(실마리) 행 — `- 위치 <앵커> → L12 · `제목` › `발췌``. 제목은 없을 수 있다(1%).
#: 구분자 ` · `와 ` › `는 코드 스팬 밖에만 놓이므로, 제목·발췌 안의 같은 문자는
#: 컨테이너가 보호한다. 그래도 잘못 갈리면 `unwrap`이 실패해 문법 위반으로 잡힌다.
#: r4-03: 이전 판은 ` › `를 비탐욕으로 찾아 나눴다 — 제목이나 발췌 **안에** 그 구분자가
#: 있으면(코드 스팬 안이라 정당하다) 엉뚱한 자리에서 갈려 정상 산출물이 fail-closed됐다.
#: 이제 접두부만 정규식으로 잡고, 코드 스팬은 **울타리 길이를 읽어 통째로 소비**한다.
_LOCATOR_HEAD_RE = re.compile(r"^  - 위치 (?P<anchor>.+?) → (?P<where>L\d+(?:, L\d+)*)(?P<rest>.*)$")


def _take_code_span(text: str, where: str) -> tuple[str, str]:
    """앞부분의 코드 스팬 하나를 통째로 떼어 `(스팬, 나머지)`를 돌려준다."""
    m = re.match(r"`+", text)
    if not m:
        raise ParseError(f"{where}: expected a code span, got {text[:40]!r}")
    fence = m.group()
    close = text.find(fence, len(fence))
    while close != -1 and re.match(r"`+", text[close:]).group() != fence:
        close = text.find(fence, close + 1)
    if close == -1:
        raise ParseError(f"{where}: code-span fence {fence!r} is never closed in {text[:40]!r}")
    return text[: close + len(fence)], text[close + len(fence) :]


_LOCATOR_MARKER_RE = re.compile(
    r"^  - 위치 (?P<anchor>.+?) → (?P<marker>"
    + re.escape(ANCHOR_UNRESOLVED) + r"|" + re.escape(ANCHOR_BLANK_LINE) + r")$"
)
_LOCATOR_NONE_RE = re.compile(r"^  - 위치 " + re.escape(NO_ANCHORS) + r"$")
_TARGET_BINDING_RE = re.compile(
    r"^대상 문서: `(?P<path>.+?)` \(sha256:(?P<sha256>[0-9a-f]{64})\)$"
)

_REJECTED_EMPTY_RE = re.compile(r"^반증\(rejected\)된 항목 없음\.$")
_REJECTED_SUMMARY_RE = re.compile(r"^총 (?P<count>\d+)건 반증됨 \(verdict 분포 — (?P<dist>.+)\)$")
_REJECTED_ROW_RE = re.compile(rf"^- \*\*(?P<fid>{_ID})\*\* \((?P<severity>P[123]), (?P<verdict>[a-z_]+)\)$")
_REJECTED_ANCHORS_RE = re.compile(r"^  - 근거 앵커: (?P<anchors>.+)$")
_REJECTED_RESIDUAL_RE = re.compile(r"^  - 잔여 주장\(narrowing\): (?P<text>.+)$")
_REJECTED_WITHDRAWN_RE = re.compile(r"^  - 철회된 범위: (?P<text>.+)$")

_DRIFT_EMPTY_RE = re.compile(r"^표기·용어 드리프트 없음\.$")
_DRIFT_SUMMARY_RE = re.compile(r"^총 (?P<count>\d+)건 \(drift count = len\(drifts\), CONTRACT\.md:434\)$")
_DRIFT_ITEM_RE = re.compile(r"^- (?P<detail>.+) \(앵커: (?P<anchors>.*)\)$")
_DRIFT_VARIANT_RE = re.compile(r"^  - (?P<notation>.+) — (?P<anchors>.*)$")

_QUESTIONS_EMPTY_RE = re.compile(r"^미확정 규약에 걸린 미결 없음\.$")
_QUESTIONS_SUMMARY_RE = re.compile(r"^총 (?P<count>\d+)건 \(severity 없음 — CONTRACT §3\)$")
#: S3: v2 question 레코드의 **필수 필드**만 싣는 새 형식.
#: r4-06: `status`·`result`는 코드 스팬 밖에 실리는 원천 파생 문자열이라, 문법 자체를
#: 스키마 enum 대체식으로 좁힌다(severity를 `P[123]`으로 좁힌 것과 같은 패턴). 이전의
#: `[^ ·]+`는 `*open*`·`<script>x</script>` 같은 값을 그대로 받아들였다. 렌더러 쪽
#: `_require_enum`과 **대칭**이다 — 한쪽만 고치면 손편집이 다시 통과한다.
_Q_STATUS_ALT = "|".join(sorted(QUESTION_STATUSES))
_Q_RESULT_ALT = "|".join(sorted(VERIFY_RESULTS))
_QUESTIONS_ID_RE = re.compile(
    rf"^- \*\*(?P<qid>{_ID})\*\* \((?P<slot>.+) · (?P<status>{_Q_STATUS_ALT})"
    rf" · 검증 (?P<result>{_Q_RESULT_ALT})\)$"
)
_QUESTIONS_UNKNOWN_RE = re.compile(r"^  \(알려지지 않은 필드 형태 — 원본 그대로\) `(?P<payload>.+)`$")


class UsageError(Exception):
    pass


class ParseError(Exception):
    """본문이 render_human_summary.py가 만들 수 있는 정확한 문법과 다르다 — 원인이
    무엇이든(위조·손편집·중복·생략·순서 오류) 이 자체가 fail-closed 사유다."""


def _unwrap_or_fail(rendered: str, where: str) -> str:
    """렌더된 컨테이너를 벗긴다. 코드 스팬이 아니면 **문법 위반**이다 — 렌더러는
    원천 파생 문자열을 반드시 감싸므로, 감싸지지 않은 값은 손으로 넣은 것이다."""
    try:
        return unwrap(rendered)
    except ContainerError as exc:
        raise ParseError(f"{where}: {exc}") from exc


def _normalize_ws(text: str) -> str:
    return re.sub(r"\s+", " ", text or "").strip()


class Cursor:
    """줄 목록을 순회하는 재귀 하강 파서용 커서. 빈 줄은 어디서든 건너뛴다(공백
    개수 자체는 문법 대상이 아니다 — join 구현이 바뀌어도 안 깨지게)."""

    def __init__(self, text: str):
        self.lines = text.splitlines()
        self.pos = 0

    def _skip_blank(self) -> None:
        while self.pos < len(self.lines) and self.lines[self.pos].strip() == "":
            self.pos += 1

    def at_end(self) -> bool:
        self._skip_blank()
        return self.pos >= len(self.lines)

    def peek_raw(self) -> str | None:
        self._skip_blank()
        return self.lines[self.pos] if self.pos < len(self.lines) else None

    def take(self, regex: re.Pattern) -> re.Match | None:
        self._skip_blank()
        if self.pos >= len(self.lines):
            return None
        m = regex.match(self.lines[self.pos])
        if m:
            self.pos += 1
        return m

    def expect(self, regex: re.Pattern, what: str) -> re.Match:
        m = self.take(regex)
        if m is None:
            found = self.peek_raw()
            where = f"line {self.pos + 1}: {found!r}" if found is not None else "end of document"
            raise ParseError(f"expected {what}, found {where}")
        return m

    def take_raw_line(self) -> str | None:
        self._skip_blank()
        if self.pos >= len(self.lines):
            return None
        line = self.lines[self.pos]
        self.pos += 1
        return line


def parse_preamble(cur: Cursor) -> dict[str, str]:
    cur.expect(_PREAMBLE_TITLE_RE, "'# 사람이 읽는 요약' title line")
    notice_line = cur.take_raw_line()
    if notice_line is None or notice_line.strip() == "":
        raise ParseError("expected the derivation notice line after the title")
    binding = cur.expect(_SOURCE_BINDING_RE, "'정본: `<path>` (sha256:<64hex>)' binding line")
    target = cur.expect(
        _TARGET_BINDING_RE, "'대상 문서: `<path>` (sha256:<64hex>)' binding line (#314 item 2)"
    )
    return {
        "notice": notice_line,
        "path": binding.group("path"),
        "sha256": binding.group("sha256"),
        "target_path": target.group("path"),
        "target_sha256": target.group("sha256"),
    }


def parse_anchor_lines(cur: Cursor, where: str) -> list[dict[str, Any]]:
    """근거 행 1개 이상(또는 '(없음)' 1행)을 읽는다 — 0행은 문법 위반이다.

    렌더러는 앵커가 없으면 반드시 `(없음)` 한 행을 찍으므로, 근거 행이 하나도 없는
    카드는 render_human_summary.py가 만들 수 없는 형태다(#314 항목 2 이전의 옛 산출물
    포함) — fail-closed."""
    if cur.take(_LOCATOR_NONE_RE):
        return []
    rows: list[dict[str, Any]] = []
    while True:
        m = cur.take(_LOCATOR_HEAD_RE)
        if m:
            rest, heading = m.group("rest"), None
            if rest.startswith(" · "):
                span, rest = _take_code_span(rest[3:], f"{where} locator heading")
                heading = _unwrap_or_fail(span, f"{where} locator heading")
            if not rest.startswith(" › "):
                raise ParseError(
                    f"{where}: expected ' › ' before the locator excerpt, got {rest[:30]!r}"
                )
            span, tail = _take_code_span(rest[3:], f"{where} locator excerpt")
            if tail:
                raise ParseError(f"{where}: trailing content after the locator excerpt: {tail[:30]!r}")
            rows.append(
                {
                    "anchor": m.group("anchor"),
                    "where": m.group("where"),
                    "heading": heading,
                    "snippet": _unwrap_or_fail(span, f"{where} locator excerpt"),
                    "marker": None,
                }
            )
            continue
        m = cur.take(_LOCATOR_MARKER_RE)
        if m:
            rows.append({"anchor": m.group("anchor"), "where": None, "heading": None,
                         "snippet": None, "marker": m.group("marker")})
            continue
        break
    if not rows:
        raise ParseError(
            f"expected at least one '위치 …' line for {where} — the renderer always emits "
            f"either resolved locators or the fixed '{NO_ANCHORS}' line"
        )
    return rows


def _tag_sort_key(tag: str) -> tuple[bool, str]:
    """render_human_summary.py의 태그 그룹 정렬 키와 **같은 식**이어야 한다
    (`sorted(by_tag, key=lambda t: (t == FALLBACK_TAG, t))`)."""
    return (tag == FALLBACK_TAG, tag)


def parse_verified_section(cur: Cursor) -> list[dict[str, Any]]:
    cards: list[dict[str, Any]] = []
    # r1-05: 렌더러는 severity를 SEVERITY_ORDER 순서로 **각 1회씩만** 내보내고, 그
    # 안의 태그 그룹도 유일·정렬돼 있다. 이전 판은 각 그룹의 지역 건수만 맞으면
    # 통과해서, 같은 severity를 여러 그룹으로 쪼개거나 P1/P3 순서를 뒤집어
    # **사람이 보는 우선순위 배열을 조작**해도 걸리지 않았다.
    seen_severities: list[str] = []
    while True:
        sev_m = cur.take(_SEVERITY_HEADER_RE)
        if sev_m is None:
            break
        severity = sev_m.group("severity")
        if severity in seen_severities:
            raise ParseError(
                f"severity header '{severity}' appears more than once — the renderer emits each "
                "severity exactly once"
            )
        if seen_severities and SEVERITY_ORDER.get(severity, 99) < SEVERITY_ORDER.get(
            seen_severities[-1], 99
        ):
            raise ParseError(
                f"severity header '{severity}' follows '{seen_severities[-1]}' — the renderer emits "
                f"severities in {list(SEVERITY_ORDER)} order"
            )
        seen_severities.append(severity)
        declared_sev_count = int(sev_m.group("count"))
        if declared_sev_count < 1:
            raise ParseError(f"severity header '{severity}' declares 0건 — the renderer never emits an empty group")
        sev_cards: list[dict[str, Any]] = []
        seen_tags: list[str] = []
        while True:
            tag_m = cur.take(_TAG_HEADER_RE)
            if tag_m is None:
                break
            tag = tag_m.group("tag")
            declared_tag_count = int(tag_m.group("count"))
            if declared_tag_count < 1:
                raise ParseError(f"tag header '{tag}' under {severity} declares 0건")
            if tag in seen_tags:
                raise ParseError(
                    f"tag header '{tag}' appears more than once under {severity} — the renderer "
                    "groups each tag exactly once"
                )
            if seen_tags and _tag_sort_key(tag) < _tag_sort_key(seen_tags[-1]):
                raise ParseError(
                    f"tag header '{tag}' follows '{seen_tags[-1]}' under {severity} — the renderer "
                    "emits tag groups in sorted order (미분류 last)"
                )
            seen_tags.append(tag)
            for _ in range(declared_tag_count):
                card_m = cur.expect(_CARD_RE, f"a verified card under {severity}/{tag}")
                quote = None
                q_m = cur.take(_QUOTE_RE)
                if q_m:
                    quote = _unwrap_or_fail(q_m.group("quote"), "verified card quote")
                anchors = parse_anchor_lines(cur, f"verified card {card_m.group('fid')}")
                sev_cards.append(
                    {
                        "finding_id": card_m.group("fid"),
                        "severity": severity,
                        "tag": tag,
                        "quote": quote,
                        "strength": card_m.group("strength"),
                        "anchor_rows": anchors,
                    }
                )
        if not sev_cards:
            raise ParseError(f"severity header '{severity}' has no tag group following it")
        if len(sev_cards) != declared_sev_count:
            raise ParseError(
                f"severity '{severity}' declares {declared_sev_count}건 but {len(sev_cards)} card(s) "
                "actually follow across its tag group(s)"
            )
        cards.extend(sev_cards)
    return cards


def parse_rejected_section(cur: Cursor) -> tuple[list[dict[str, Any]], str | None]:
    if cur.take(_REJECTED_EMPTY_RE):
        return [], None
    summary_m = cur.expect(
        _REJECTED_SUMMARY_RE,
        "'총 N건 반증됨 (verdict 분포 — ...)' summary line or '반증(rejected)된 항목 없음.' marker",
    )
    declared = int(summary_m.group("count"))
    declared_dist = summary_m.group("dist")
    rows: list[dict[str, Any]] = []
    for _ in range(declared):
        row_m = cur.expect(_REJECTED_ROW_RE, "a rejected row '- **id** (Pn, verdict)'")
        cur_anchors = cur.expect(_REJECTED_ANCHORS_RE, "the required '근거 앵커' line (renderer always emits it)")
        anchor_rows = parse_anchor_lines(cur, f"rejected row {row_m.group('fid')}")
        residual = None
        r_m = cur.take(_REJECTED_RESIDUAL_RE)
        if r_m:
            residual = _unwrap_or_fail(r_m.group("text"), "rejected residual_claim")
        withdrawn = None
        w_m = cur.take(_REJECTED_WITHDRAWN_RE)
        if w_m:
            withdrawn = _unwrap_or_fail(w_m.group("text"), "rejected withdrawn_scope")
        quote = None
        q_m = cur.take(_QUOTE_RE)
        if q_m:
            quote = _unwrap_or_fail(q_m.group("quote"), "rejected judgment_provenance")
        rows.append(
            {
                "finding_id": row_m.group("fid"),
                "severity": row_m.group("severity"),
                "counter_citation_verdict": row_m.group("verdict"),
                "anchors": cur_anchors.group("anchors"),
                "anchor_rows": anchor_rows,
                "residual_claim": residual,
                "withdrawn_scope": withdrawn,
                "quote": quote,
            }
        )
    return rows, declared_dist


def parse_drift_section(cur: Cursor) -> list[dict[str, Any]]:
    if cur.take(_DRIFT_EMPTY_RE):
        return []
    summary_m = cur.expect(
        _DRIFT_SUMMARY_RE, "'총 N건 (drift count = len(drifts), ...)' summary line or empty marker"
    )
    declared = int(summary_m.group("count"))
    # v2r4-02: render_human_summary.py는 항목이 0개면 언제나 "없음" 마커만 쓰고
    # "총 0건 (...)" 요약 줄은 절대 만들지 않는다(comprehensive5-02가 검증된 절의
    # 0건 severity 헤더에 대해 이미 세운 것과 같은 불변식) — 그런데 문법상으로는
    # 파싱 가능해서 렌더러가 절대 못 만드는 형태가 그대로 통과했다.
    if declared == 0:
        raise ParseError(
            "'총 0건 ...' summary line is never emitted by render_human_summary.py "
            "(it always uses the '없음' marker for zero items)"
        )
    items: list[dict[str, Any]] = []
    for _ in range(declared):
        item_m = cur.expect(_DRIFT_ITEM_RE, "a drift item '- detail (앵커: anchors)'")
        # v2r2-01: variant 하위 줄은 이전 판까지 소비만 하고 내용(표기·앵커)은
        # 버렸다 — render_human_summary.py가 실제로 렌더하는 콘텐츠이므로 이제
        # 캡처해서 review.md와 대조한다.
        drift_locators = parse_anchor_lines(cur, f"drift item {len(items) + 1}")
        variants: list[tuple[str, str]] = []
        while True:
            v_m = cur.take(_DRIFT_VARIANT_RE)
            if not v_m:
                break
            variants.append((
                _unwrap_or_fail(v_m.group("notation"), "drift variant notation"),
                _normalize_ws(v_m.group("anchors")),
            ))
        items.append(
            {
                "detail": _unwrap_or_fail(item_m.group("detail"), "drift detail"),
                "anchors": _normalize_ws(item_m.group("anchors")),
                "locators": drift_locators,
                "variants": tuple(variants),
            }
        )
    return items


def parse_questions_section(cur: Cursor) -> list[dict[str, Any]]:
    if cur.take(_QUESTIONS_EMPTY_RE):
        return []
    summary_m = cur.expect(_QUESTIONS_SUMMARY_RE, "'총 N건 (severity 없음 — ...)' summary line or empty marker")
    declared = int(summary_m.group("count"))
    if declared < 1:
        # v2r4-02: 0건이면 렌더러는 항상 "없음" 마커만 쓴다 — '총 0건' 헤더는
        # 렌더러가 만들 수 없는 형태다.
        raise ParseError("questions header declares 0건 — the renderer emits the empty marker instead")
    items: list[dict[str, Any]] = []
    for _ in range(declared):
        m = cur.expect(_QUESTIONS_ID_RE, "a question line '- **id** (`slot` · status · 검증 result)'")
        items.append(
            {
                "record_id": m.group("qid"),
                "convention_slot": _unwrap_or_fail(m.group("slot"), "question convention_slot"),
                "status": m.group("status"),
                "result": m.group("result"),
                "locators": parse_anchor_lines(cur, f"question {m.group('qid')}"),
            }
        )
    if len(items) != declared:
        raise ParseError(f"questions header declares {declared}건 but {len(items)} item(s) follow")
    return items


def parse_body(body_text: str) -> dict[str, Any]:
    """render()가 만드는 본문(첫 `<!-- render-manifest` 앞) 전체를 문법대로
    엄격히 파싱한다. 한 조각이라도 문법과 다르면 ParseError로 즉시 실패한다."""
    # render()는 본문과 manifest 트레일러 사이에 "\n---\n" 구분자를 넣는다(그 앞뒤
    # 개행 수는 join 구현에 따라 늘어날 수 있음) — 이건 절 콘텐츠가 아니므로 EOF
    # 판정 전에 제거한다.
    body_text = re.sub(r"\n-{3,}\s*\Z", "\n", body_text)
    cur = Cursor(body_text)
    preamble = parse_preamble(cur)
    cur.expect(re.compile(r"^" + re.escape(_TOP_SECTION_HEADERS[0]) + r"$"), f"'{_TOP_SECTION_HEADERS[0]}' header")
    verified_cards = parse_verified_section(cur)
    cur.expect(re.compile(r"^" + re.escape(_TOP_SECTION_HEADERS[1]) + r"$"), f"'{_TOP_SECTION_HEADERS[1]}' header")
    rejected_rows, rejected_declared_dist = parse_rejected_section(cur)
    cur.expect(re.compile(r"^" + re.escape(_TOP_SECTION_HEADERS[2]) + r"$"), f"'{_TOP_SECTION_HEADERS[2]}' header")
    drift_items = parse_drift_section(cur)
    cur.expect(re.compile(r"^" + re.escape(_TOP_SECTION_HEADERS[3]) + r"$"), f"'{_TOP_SECTION_HEADERS[3]}' header")
    question_items = parse_questions_section(cur)
    if not cur.at_end():
        raise ParseError(f"unexpected trailing content at line {cur.pos + 1}: {cur.peek_raw()!r}")
    return {
        "preamble": preamble,
        "verified_cards": verified_cards,
        "rejected_rows": rejected_rows,
        "rejected_declared_dist": rejected_declared_dist,
        "drift_items": drift_items,
        "question_items": question_items,
    }


def _last_manifest_match(text: str) -> re.Match | None:
    # v2-04: 인용 등 본문 콘텐츠 안에 우연히 manifest 마커와 비슷한 문자열이 있을
    # 가능성에 대비해, 항상 문서 맨 끝의(=마지막) manifest 블록을 정본으로 삼는다.
    matches = list(_MANIFEST_RE.finditer(text))
    return matches[-1] if matches else None


def _trailing_content_after_manifest(body: str) -> str:
    """impl-r7-04: manifest 블록(`-->`) 뒤에 이어붙인 내용은 이전 판이 아예 안
    읽었다 — 사람에게는 여전히 렌더돼 보이는 내용이라 감사 없이 통과시키면 안 된다."""
    match = _last_manifest_match(body)
    if not match:
        return ""
    return body[match.end():].strip()


def extract_manifest(summary_text: str) -> dict[str, Any] | None:
    match = _last_manifest_match(summary_text)
    if not match:
        return None
    try:
        # r4-11: receipt에는 StrictLoader를 적용했는데 manifest는 safe_load라 중복 키가
        # 통과했다(`agent_count: 999` 뒤에 `agent_count: 0`을 덧붙인 수제 요약이 오류 0건).
        data = yaml.load(match.group(1), Loader=StrictLoader)
    except DuplicateKeyError as exc:
        raise UsageError(f"render-manifest has a duplicate YAML key ({exc}) — a later duplicate "
                         "would silently win and split the disclosure reading") from exc
    except yaml.YAMLError as exc:
        raise UsageError(f"render-manifest YAML parse error: {exc}") from exc
    if not isinstance(data, dict):
        raise UsageError("render-manifest must be a mapping")
    return data


def audit(
    summary_text: str,
    receipt: dict[str, Any],
    source_bytes: bytes,
    target: TargetDoc,
    source_path: str | os.PathLike[str] | None = None,
) -> list[str]:
    errors: list[str] = []
    by_id = {f.get("finding_id"): f for f in receipt["findings"]}

    # v2-04: render_human_summary.py는 인용(quote)이 원문의 verbatim 부분 문자열
    # 이기만 하면 내용을 제한하지 않는다 — 인용 안에 우연히 리터럴 "<!-- render-
    # manifest" 문자열이 있으면(극히 드물지만 가능) 첫 occurrence에서 자르는
    # naive split은 정당한 산출물의 본문을 잘라 거짓 TRACE-FAIL을 냈다. 그 리터럴이
    # manifest 트레일러 자신의 items[].quote 값 안에도 그대로 실려(본문 blockquote와
    # 동일 문자열이므로) 나타날 수 있어, 마지막 occurrence를 쓰는 naive rsplit도
    # 여전히 틀린 지점에서 자를 수 있다 — 실제 트레일러는 `<!-- render-manifest\n...
    # -->` 정규 형태를 갖춘 블록 하나뿐이므로, 같은 정규식(_last_manifest_match)으로
    # 그 블록의 시작 위치를 찾아 자른다(extract_manifest와 동일 기준으로 일치시킴).
    manifest_match = _last_manifest_match(summary_text)
    if manifest_match is not None:
        # r5-01: manifest 구간은 본문 감사가 닿지 않는다(본문은 manifest 시작 전까지만
        # 본다). 그래서 거기에 YAML 주석으로 위장한 `-->`와 그 뒤 임의 Markdown을 심으면,
        # HTML 주석이 첫 `-->`에서 닫혀 **원문에 없는 내용이 사람에게 보이는데** 감사는
        # 통과했다.
        #
        # 처음엔 "구간 안의 `-->`를 전부 거부"로 막으려 했으나, 그건 인용문에 정당하게
        # `-->`가 든 receipt까지 거부하는 오탐이었다(기존 회귀 v2r2-04). 대신 manifest가
        # **렌더러가 만들 수 있는 정확한 직렬화 형태**인지 확인한다 — 코드 스팬에 canonical
        # 검사를 건 것과 같은 원리다. 주석·들여쓰기 변형은 재직렬화에서 살아남지 못한다.
        region = summary_text[manifest_match.start() : manifest_match.end()]
        try:
            reparsed = yaml.load(manifest_match.group(1), Loader=StrictLoader)
        except (DuplicateKeyError, yaml.YAMLError) as exc:
            raise UsageError(f"render-manifest YAML parse error: {exc}") from exc
        canonical = ("<!-- render-manifest\n"
                     + yaml.safe_dump(reparsed, allow_unicode=True, sort_keys=False)
                     + "-->")
        if region.rstrip("\n") != canonical.rstrip("\n"):
            errors.append(
                "(f) render-manifest is not in the canonical serialization the renderer emits — "
                "content that survives here escapes the body audit entirely (r5-01)"
            )
    if manifest_match is None:
        raise UsageError(
            "no <!-- render-manifest --> block found — was this file produced by render_human_summary.py?"
        )
    body_text = summary_text[: manifest_match.start()]
    try:
        parsed = parse_body(body_text)
    except ParseError as exc:
        # 문법 위반은 그 자체로 fail-closed 사유다 — 어느 필드가 어떻게 조작됐는지와
        # 무관하게, render_human_summary.py가 만들 수 없는 형태라는 사실만으로 충분.
        return [f"(g) visible body does not match the expected render_human_summary.py grammar: {exc}"]

    preamble = parsed["preamble"]
    if _normalize_ws(preamble["notice"]) != _normalize_ws(RENDERED_BY_NOTICE):
        errors.append("(g) preamble notice text does not match the expected derivation notice")
    actual_sha_for_preamble = hashlib.sha256(source_bytes).hexdigest()
    if preamble["sha256"] != actual_sha_for_preamble:
        errors.append(
            f"(g) preamble declares source sha256={preamble['sha256']!r} but --source actually hashes to "
            f"{actual_sha_for_preamble!r}"
        )

    if preamble["target_sha256"] != target.sha256:
        errors.append(
            f"(g) preamble declares target-doc sha256={preamble['target_sha256']!r} but --target-doc "
            f"actually hashes to {target.sha256!r} — the quotes were rendered from a different revision"
        )
    # (i) r1-01: 헤더 자기정합성만으로는 부족하다 — 렌더와 감사 양쪽에 같은 decoy
    # 문서를 주면 통과했다. receipt가 선언한 "리뷰된 문서" 해시에 독립적으로 결속한다.
    try:
        bind_target_to_receipt(receipt, target)
    except RenderStructuralError as exc:
        errors.append(f"(i) --target-doc is not bound to this receipt: {exc}")
    # 헤더에 적힌 대상 문서 이름과 실제로 감사에 넘긴 파일 이름이 다르면 알린다.
    # **약한 검사**다(내용 결속은 위 sha256이 한다) — 파일을 옮기거나 이름을 바꿔
    # 감사하면 걸리지만, 그 경우 요약을 다시 렌더하는 것이 정상 경로다.
    if os.path.basename(preamble["target_path"]) != os.path.basename(str(target.path)):
        errors.append(
            f"(i) preamble names target document {preamble['target_path']!r} but this audit was given "
            f"{str(target.path)!r} — re-render the summary instead of auditing a differently named file"
        )
    # r4-12 해소(docauth#331, 사용자 결정 2026-09-07 — 선택지 A): receipt 경로에도
    # **target과 대칭으로** 같은 검사를 건다. 이전에는 걸지 않았고 그 상태를
    # LIMITS.md L9로 공시만 했는데, **그것은 CONTRACT 12.6 ⓔ의 "공시 + 확장 중단"이
    # 아니었다** — ⓔ의 기준을 그대로 대면 이 도구는 fail-closed 확정 판정을 내리고
    # 잔여 실패의 방향도 **불안전**(거짓 OK: 출처 위치를 거짓으로 주장한 요약이 통과)
    # 하므로 ⓔ는 "고쳐라" 쪽을 가리킨다. 그래서 고쳤다.
    #
    # **뒤집은 결정**: impl-r1-03("내용이 바이트 단위로 동일한 receipt는 경로가 달라도
    # TRACE-OK — 경로가 아니라 내용 기준"). 그 결정이 지키던 것은 "내용 결속은 해시가
    # 한다"는 원칙인데, 그 원칙은 여전히 (g)의 sha256 대조가 지킨다. 이 검사가 추가로
    # 막는 것은 **내용은 진짜인데 출처 위치 주장이 거짓인 요약**이다 — preamble과
    # manifest의 경로를 **함께** 바꾸면 둘의 상호 일치 검사((f))만 통과했다.
    #
    # target 쪽과 같은 이유로 **약한 검사**다(basename까지만): 파일을 옮기거나 이름을
    # 바꿔 감사하면 걸리지만, 그 경우 요약을 다시 렌더하는 것이 정상 경로다.
    if source_path is not None and os.path.basename(preamble["path"]) != os.path.basename(str(source_path)):
        errors.append(
            f"(i) preamble names source receipt {preamble['path']!r} but this audit was given "
            f"{str(source_path)!r} — re-render the summary instead of auditing a differently named file"
        )

    def check_anchor_rows(fid: str, rows: list[dict[str, Any]], source: dict[str, Any]) -> None:
        """(h) #314 항목 2 — 렌더된 근거 행을 receipt·원문 양쪽에 다시 결속한다.

        ① 앵커 토큰 목록이 receipt의 evidence_anchors와 **순서까지 정확히** 같아야
           한다(누락·추가·재배열 전부 잡는다). ② 해석된 인용문은 이 감사기가 원문에서
           **다시 뽑은** 텍스트와 정확히 같아야 한다 — 렌더 산출물의 인용문만 손으로
           고치는 조작을 막는 지점이다. ③ 고정 마커는 실제로 해석 불가일 때만 인정한다
           (해석되는 앵커를 '찾지 못함'으로 숨기지 못하게).
        """
        try:
            # r1-02: 감사기도 폐쇄 문법을 **독립적으로** 적용한다 — 렌더러만 검사하면
            # 손으로 만든 요약에 형식 밖 앵커를 넣는 경로가 열린 채로 남는다.
            declared = [
                validate_anchor_token(a, fid)
                for a in _list_field(source.get("evidence_anchors"), f"{fid}.evidence_anchors")
            ]
        except RenderStructuralError as exc:
            errors.append(f"(h) {fid}: receipt anchor is not in the supported closed grammar: {exc}")
            return
        rendered = [row["anchor"] for row in rows]
        if rendered != declared:
            errors.append(
                f"(h) {fid}: rendered evidence anchors {rendered} != review.md evidence_anchors {declared}"
            )
            return
        for row in rows:
            try:
                numbers, raw_text, norm_text = target.resolve(row["anchor"])
            except RenderStructuralError as exc:
                errors.append(f"(h) {fid}: anchor {row['anchor']!r} cannot be resolved: {exc}")
                continue
            if raw_text is None:
                expected_marker = ANCHOR_UNRESOLVED
            elif not norm_text:
                expected_marker = ANCHOR_BLANK_LINE
            else:
                expected_marker = None
            if expected_marker is not None:
                if row["marker"] != expected_marker:
                    errors.append(
                        f"(h) {fid}: anchor {row['anchor']!r} resolves to {expected_marker} in the target "
                        f"document but the summary renders {row['marker'] or row['snippet']!r}"
                    )
                continue
            if row["marker"] is not None:
                errors.append(
                    f"(h) {fid}: anchor {row['anchor']!r} does resolve in the target document but the "
                    f"summary hides it behind the marker {row['marker']!r}"
                )
                continue
            expected_where = ", ".join(f"L{n}" for n in numbers)
            if row["where"] != expected_where:
                errors.append(
                    f"(h) {fid}: anchor {row['anchor']!r} rendered at {row['where']!r} but re-resolves to "
                    f"{expected_where!r}"
                )
            # 실마리 두 조각을 **대상 문서에서 다시 만들어** 정확히 대조한다. 발췌 규칙이
            # 결정론이라 exact 비교가 성립하고, 그래서 발췌만 손대는 조작이 걸린다.
            # r4-02: 렌더러는 제목의 내부 공백을 보존한다(DISPLAY_ENCODED_EXACT).
            # 기대값만 _normalize_ws로 접으면 `Head  Gap` 같은 정상 제목이 오탐 실패한다.
            expected_heading = target.heading_of(numbers[0])
            expected_heading = display_text(expected_heading) if expected_heading else None
            if row["heading"] != expected_heading:
                errors.append(
                    f"(h) {fid}: anchor {row['anchor']!r} rendered heading {row['heading']!r} but the "
                    f"target document has {expected_heading!r}"
                )
            expected_snippet = display_text(excerpt(raw_text))
            if row["snippet"] != expected_snippet:
                errors.append(
                    f"(h) {fid}: anchor {row['anchor']!r} excerpt is not the deterministic excerpt of the "
                    f"target-document line — rendered {row['snippet']!r}, expected {expected_snippet!r}"
                )

    verified_cards = parsed["verified_cards"]
    rejected_rows = parsed["rejected_rows"]
    drift_items = parsed["drift_items"]
    question_items = parsed["question_items"]

    # (a) finding_id 실재성 — review.md의 findings[] 전체와 정확히 일치해야 한다
    # (누락·추가 둘 다 잡는다). 문법 파서는 중복 finding_id 자체를 막지 않으므로
    # (같은 id로 두 번 카드를 만드는 것도 문법상 유효한 반복) 여기서 별도 확인한다.
    all_ids = [c["finding_id"] for c in verified_cards] + [r["finding_id"] for r in rejected_rows]
    duplicates = sorted({fid for fid in all_ids if all_ids.count(fid) > 1})
    if duplicates:
        errors.append(f"(a) finding_id(s) rendered more than once in the visible body: {duplicates}")
    visible_ids = set(all_ids)
    receipt_ids = set(by_id.keys())
    missing = receipt_ids - visible_ids
    extra = visible_ids - receipt_ids
    if missing:
        errors.append(f"(a) finding(s) present in review.md but missing from summary.md: {sorted(missing)}")
    if extra:
        errors.append(f"(a) finding(s) in summary.md that do not exist in review.md: {sorted(extra)}")

    for card in verified_cards:
        fid = card["finding_id"]
        source = by_id.get(fid)
        if source is None:
            continue  # 이미 (a)에서 보고됨
        if source.get("status") != "verified":
            errors.append(f"(b) {fid}: rendered as a verified finding but review.md status is {source.get('status')!r}")
        if card["severity"] != source.get("severity"):
            errors.append(
                f"(b) {fid}: rendered severity {card['severity']!r} != review.md severity {source.get('severity')!r}"
            )
        if card["tag"] not in ALL_TAGS:
            errors.append(f"(c) {fid}: rendered tag {card['tag']!r} is not in the controlled vocabulary")
        if card["tag"] != FALLBACK_TAG and not card["quote"]:
            errors.append(
                f"(d) {fid}: rendered with controlled-vocabulary tag {card['tag']!r} but no quote — "
                "a non-fallback tag requires verbatim evidence"
            )
        if card["quote"]:
            # 본문 인용은 표시 인코딩(r2-05)된 값이므로, 기대값도 같은 인코더를 통과한
            # provenance여야 한다 — 한쪽만 인코딩하면 정당한 산출물이 오탐난다.
            provenance = display_text(_normalize_ws(source.get("judgment_provenance") or ""))
            if _normalize_ws(card["quote"]) not in provenance:
                errors.append(f"(d) {fid}: rendered quote is not a verbatim substring of judgment_provenance")
        check_anchor_rows(fid, card["anchor_rows"], source)
        if card["strength"] is not None and card["strength"] not in MATCH_STRENGTHS:
            errors.append(
                f"(c) {fid}: rendered match_strength {card['strength']!r} is not one of "
                f"{sorted(MATCH_STRENGTHS)} — free-form text is not permitted here"
            )
        # v2-02: render_human_summary.py는 match_strength가 "약"이면 태그를
        # 무조건 미분류로 내린다(§6 r2-05) — 이전 판은 태그·strength 값 각각의
        # 유효성만 보고 이 관계(약 → 미분류) 자체는 검증하지 않아, 렌더러가
        # 만들 수 없는 "약한 매칭인데 통제 태그 유지" 조합을 통과시켰다.
        if card["strength"] == "약" and card["tag"] != FALLBACK_TAG:
            errors.append(
                f"(c) {fid}: match_strength '약' but tag is {card['tag']!r} — the renderer always "
                f"downgrades a weak match to {FALLBACK_TAG!r}, this combination cannot be legitimate"
            )

    for row in rejected_rows:
        fid = row["finding_id"]
        source = by_id.get(fid)
        if source is None:
            continue
        if source.get("status") != "rejected":
            errors.append(f"(b) {fid}: rendered as a rejected finding but review.md status is {source.get('status')!r}")
        if row["severity"] != source.get("severity"):
            errors.append(
                f"(b) {fid}: rendered severity {row['severity']!r} != review.md severity {source.get('severity')!r}"
            )
        if row["counter_citation_verdict"] != source.get("counter_citation_verdict"):
            errors.append(
                f"(b) {fid}: rendered counter_citation_verdict {row['counter_citation_verdict']!r} != "
                f"review.md value {source.get('counter_citation_verdict')!r}"
            )
        source_anchors = list(_list_field(source.get("evidence_anchors"), f"{fid}.evidence_anchors"))
        # v2r3-02: 렌더러가 앵커 하나하나를 공백 정규화한 뒤 join하므로(개행이
        # 섞여도 한 줄을 보장), 기대값도 같은 방식으로 계산해야 정당한 산출물이
        # 오탐나지 않는다.
        expected_anchors_text = ", ".join(_normalize_ws(a) for a in source_anchors) or NO_ANCHORS
        if row["anchors"] != expected_anchors_text:
            errors.append(
                f"(h) {fid}: rendered '근거 앵커' is {row['anchors']!r} but review.md evidence_anchors "
                f"render to {expected_anchors_text!r}"
            )
        check_anchor_rows(fid, row["anchor_rows"], source)
        source_narrowing = source.get("narrowing") or {}
        expected_residual = _display_or_none(source_narrowing.get("residual_claim"))
        if expected_residual:
            if row["residual_claim"] is None or row["residual_claim"] != expected_residual:
                errors.append(f"(h) {fid}: rendered '잔여 주장' missing or does not match review.md narrowing.residual_claim")
        elif row["residual_claim"] is not None:
            errors.append(f"(h) {fid}: rendered '잔여 주장' but review.md has no narrowing.residual_claim for this finding")
        expected_withdrawn = _display_or_none(source_narrowing.get("withdrawn_scope"))
        if expected_withdrawn:
            if row["withdrawn_scope"] is None or row["withdrawn_scope"] != expected_withdrawn:
                errors.append(f"(h) {fid}: rendered '철회된 범위' missing or does not match review.md narrowing.withdrawn_scope")
        elif row["withdrawn_scope"] is not None:
            errors.append(f"(h) {fid}: rendered '철회된 범위' but review.md has no narrowing.withdrawn_scope for this finding")
        source_provenance = source.get("judgment_provenance") or ""
        if source_provenance and row["quote"] is None:
            errors.append(f"(d) {fid}: review.md has judgment_provenance but the rendered rejected row has no quote line")
        if row["quote"]:
            # r2-02: rejected 행의 인용은 렌더러가 judgment_provenance **전체**를 싣는
            # 자리다. 부분 문자열만 요구하면 뒤쪽 부정어·단서절을 잘라 의미를 뒤집을 수
            # 있다("시스템은 안전하지 않다." → "시스템은 안전"). 전체 일치를 요구한다.
            # (verified 카드의 인용은 태깅 에이전트가 고른 **발췌**라 부분 문자열이 정상 —
            # 두 자리는 계약이 다르고, 그 차이를 여기 명시한다.)
            expected_quote = display_text(_normalize_ws(source_provenance))
            if _normalize_ws(row["quote"]) != expected_quote:
                errors.append(
                    f"(d) {fid}: rendered rejected quote is not the whole judgment_provenance — "
                    f"rendered {row['quote']!r}, expected {expected_quote!r}"
                )

    # v2-03: 반증 절 요약줄의 "verdict 분포" 텍스트는 렌더러가 rejected 행들에서
    # 계산해 넣는 파생값이다(render_human_summary.py의 verdict_counts → sorted →
    # " · " join) — 이전 판은 이 텍스트를 파싱만 하고 실제 행들과 대조하지 않아
    # 임의로 조작된 분포 문구가 그대로 통과했다. 같은 계산을 여기서도 수행해
    # 파싱된 문구와 정확히 일치하는지 확인한다.
    actual_verdict_counts: dict[str, int] = {}
    for row in rejected_rows:
        verdict = row["counter_citation_verdict"]
        actual_verdict_counts[verdict] = actual_verdict_counts.get(verdict, 0) + 1
    expected_dist = " · ".join(f"{k}: {v}" for k, v in sorted(actual_verdict_counts.items()))
    rejected_declared_dist = parsed["rejected_declared_dist"]
    if rejected_rows:
        if rejected_declared_dist != expected_dist:
            errors.append(
                f"(h) rejected section summary declares verdict 분포 {rejected_declared_dist!r} but the "
                f"rendered rows actually distribute as {expected_dist!r}"
            )
    elif rejected_declared_dist is not None:
        errors.append(
            f"(g) rejected section has no rows but declares a verdict 분포 {rejected_declared_dist!r}"
        )

    # v2r2-01: variants[]도 이제 review.md drifts[].variants[]와 3-튜플
    # (detail, anchors, variants)로 함께 대조한다 — variants 내용만 바꿔치기해도
    # detail/anchors가 그대로면 이전 판은 놓쳤다.
    # r2-01: drift·variant 앵커에는 폐쇄 문법이 감사기 쪽에서 적용되지 않아,
    # receipt와 수제 요약 양쪽에 산문 앵커를 넣으면 통과했다(렌더러는 거부하므로
    # 두 도구의 판정이 갈렸다). 이제 findings와 **같은 헬퍼**로 검증한다.
    # r2-05: 기대 표시값도 본문과 같은 인코더를 통과시킨다.
    def _variant_tuple(v: Any, where: str) -> tuple[str, str] | None:
        if not isinstance(v, dict):
            return None
        return (
            _display_or_none(v.get("notation"), f"{where} variant.notation"),
            ", ".join(anchor_list(v, "anchors", f"{where} variant")),
        )  # 본문은 이 값을 wrap한 것 — 파서가 unwrap한 뒤 이 값과 대조한다

    source_drift_details: list[tuple[str, str, tuple]] = []
    receipt_drifts = _list_field(receipt.get("drifts"), "receipt.drifts")
    try:
        for d in receipt_drifts:
            drift_where = str(d.get("record_id") or "<drift>")
            source_drift_details.append(
                (
                    _display_or_none(d.get("detail"), f"{drift_where}.detail"),
                    ", ".join(anchor_list(d, "evidence_anchors", drift_where)) or NO_ANCHORS,
                    tuple(
                        t
                        for t in (
                            _variant_tuple(v, drift_where)
                            for v in _list_field(d.get("variants"), f"{drift_where}.variants")
                        )
                        if t is not None
                    ),
                )
            )
    except RenderStructuralError as exc:
        errors.append(f"(h) review.md drifts[] violate the anchor/type contract: {exc}")
        source_drift_details = []
    rendered_drift_details = [(item["detail"], item["anchors"], item["variants"]) for item in drift_items]
    if len(rendered_drift_details) != len(source_drift_details):
        errors.append(
            f"(h) drift section renders {len(rendered_drift_details)} item(s) but review.md has "
            f"{len(source_drift_details)} drift record(s)"
        )
    seen_drift_items: dict[tuple[str, str, tuple], int] = {}
    for item in rendered_drift_details:
        seen_drift_items[item] = seen_drift_items.get(item, 0) + 1
        if item not in source_drift_details:
            errors.append(
                f"(h) rendered drift item not found in review.md drifts[]: detail={item[0]!r} "
                f"anchors={item[1]!r} variants={item[2]!r}"
            )
    for idx, item in enumerate(drift_items):
        if idx < len(receipt_drifts) and isinstance(receipt_drifts[idx], dict):
            check_anchor_rows(f"drift {receipt_drifts[idx].get('record_id')}",
                              item["locators"], receipt_drifts[idx])
    for item, count in seen_drift_items.items():
        if count > source_drift_details.count(item):
            errors.append(f"(h) drift item rendered {count} time(s) but review.md has it {source_drift_details.count(item)} time(s): detail={item[0]!r}")

    source_questions = list(_list_field(receipt.get("questions"), "receipt.questions"))
    source_question_ids = [str(q.get("record_id")) if isinstance(q, dict) else "(id 없음)"
                           for q in source_questions]
    rendered_question_ids = [item["record_id"] for item in question_items]
    if len(rendered_question_ids) != len(source_question_ids):
        errors.append(
            f"(h) questions section renders {len(rendered_question_ids)} item(s) but review.md has "
            f"{len(source_question_ids)}"
        )
    # S3: question은 이제 **필수 필드**만 싣는다 — 위치(순서)로 원본과 1:1 대조하고,
    # 각 필드를 receipt에서 다시 읽어 정확히 맞춘다. 위치 실마리는 finding과 같은
    # 검사(h)를 그대로 받는다.
    for idx, item in enumerate(question_items):
        if idx >= len(source_questions):
            continue
        source_q = source_questions[idx]
        if not isinstance(source_q, dict):
            errors.append(f"(h) question at position {idx}: review.md record is not a mapping")
            continue
        qid = item["record_id"]
        if qid != source_question_ids[idx]:
            errors.append(
                f"(h) question at position {idx}: rendered id {qid!r} != review.md questions[{idx}] "
                f"id {source_question_ids[idx]!r} — id/content pair may have been swapped"
            )
        expected_slot = display_text(_normalize_ws(str(source_q.get("convention_slot"))))
        if item["convention_slot"] != expected_slot:
            errors.append(
                f"(h) question {qid}: rendered convention_slot {item['convention_slot']!r} != review.md "
                f"{expected_slot!r}"
            )
        if item["status"] != str(source_q.get("status")):
            errors.append(
                f"(h) question {qid}: rendered status {item['status']!r} != review.md "
                f"{source_q.get('status')!r}"
            )
        verification = source_q.get("classification_verification")
        expected_result = str(verification.get("result")) if isinstance(verification, dict) else None
        if item["result"] != expected_result:
            errors.append(
                f"(h) question {qid}: rendered verification result {item['result']!r} != review.md "
                f"{expected_result!r}"
            )
        check_anchor_rows(f"question {qid}", item["locators"], source_q)

    trailing = _trailing_content_after_manifest(summary_text)
    if trailing:
        errors.append(f"(g) content found after the render-manifest block that was never audited: {trailing[:200]!r}")

    manifest = extract_manifest(summary_text)
    if manifest is None:
        raise UsageError(
            "no <!-- render-manifest --> block found — was this file produced by render_human_summary.py?"
        )

    rendered_by = manifest.get("rendered_by")
    if not isinstance(rendered_by, dict):
        raise UsageError(f"render-manifest.rendered_by must be a mapping, got {type(rendered_by).__name__}")
    declared_source = rendered_by.get("source_receipt")
    if not isinstance(declared_source, dict):
        raise UsageError(
            f"render-manifest.rendered_by.source_receipt must be a mapping, got {type(declared_source).__name__}"
        )
    # r2-07: manifest 자체의 생성 문법을 강제한다 — 이전 판은 개별 item 값만
    # 대조해서, `agent_count: 999` · 위조 `source_receipt.path` · `items: {}`를
    # 동시에 넣어도 오류 0건이었다(`items: {}`는 falsey라 `or []`가 정상 빈 목록으로
    # 승격시켰다). 본문과 달리 manifest는 **렌더러가 기계로 만드는 고정 구조**이므로
    # 정확한 키·타입 집합을 요구할 수 있다.
    if set(manifest.keys()) != {"rendered_by", "items"}:
        errors.append(
            f"(f) render-manifest top-level keys are {sorted(manifest.keys())}, expected "
            "['items', 'rendered_by']"
        )
    if set(rendered_by.keys()) != {"agent_count", "source_receipt", "rerun"}:
        errors.append(
            f"(f) render-manifest.rendered_by keys are {sorted(rendered_by.keys())}, expected "
            "['agent_count', 'rerun', 'source_receipt']"
        )
    if rendered_by.get("agent_count") not in (0, 1):
        errors.append(
            f"(f) render-manifest.rendered_by.agent_count is {rendered_by.get('agent_count')!r} — the "
            "renderer only ever writes 0 or 1"
        )
    if not isinstance(rendered_by.get("rerun"), bool):
        errors.append(
            f"(f) render-manifest.rendered_by.rerun must be a boolean, got "
            f"{type(rendered_by.get('rerun')).__name__}"
        )
    if set(declared_source.keys()) != {"path", "sha256"}:
        errors.append(
            f"(f) render-manifest.rendered_by.source_receipt keys are {sorted(declared_source.keys())}, "
            "expected ['path', 'sha256']"
        )
    if declared_source.get("path") != preamble["path"]:
        errors.append(
            f"(f) manifest source_receipt.path {declared_source.get('path')!r} != preamble path "
            f"{preamble['path']!r} — body/manifest inconsistency"
        )
    if not isinstance(manifest.get("items"), list):
        errors.append(
            f"(f) render-manifest.items must be a list, got {type(manifest.get('items')).__name__} — a "
            "falsey non-list must not be treated as an empty item list"
        )
    # **잔여 한계(공시)**: `agent_count`·`rerun`은 렌더러가 스스로 적은 사실 주장이다.
    # 이 감사기는 그 값이 형식에 맞는지만 보고, 태깅 에이전트가 실제로 호출됐는지·
    # 재렌더가 실제로 있었는지는 외부 증거가 없어 검증하지 않는다.
    declared_sha = declared_source.get("sha256")
    actual_sha = hashlib.sha256(source_bytes).hexdigest()
    if declared_sha != actual_sha:
        errors.append(
            f"(e) manifest declares source_receipt.sha256={declared_sha!r} but --source file's actual "
            f"sha256 is {actual_sha!r} — mismatched or stale receipt binding"
        )

    by_body_status = {c["finding_id"]: "verified" for c in verified_cards}
    by_body_status.update({r["finding_id"]: "rejected" for r in rejected_rows})
    by_body: dict[str, dict[str, Any]] = {c["finding_id"]: c for c in verified_cards}
    by_body.update({r["finding_id"]: r for r in rejected_rows})
    # v2r2-03: render_human_summary.py의 _render_manifest는 verified item에
    # finding_id/severity/status/tag/quote를, rejected item에
    # finding_id/severity/status/counter_citation_verdict를 예외 없이 전부 채워
    # 넣는다(조건부 생략이 없다) — 그런데 이전 판은 "키가 있으면 대조"로만 짜여
    # 있어, manifest에서 그 키를 통째로 지우면 대조 자체가 스킵됐다. 정당한
    # 렌더러가 항상 채우는 필드이므로, 키 존재 여부로 게이트하지 않고 해당
    # status 타입에 대해 무조건 대조한다(dict.get은 키가 없으면 None을 주므로
    # 삭제된 키는 자동으로 불일치로 잡힌다). tag/quote는 verified에만, quote는
    # rejected manifest item에는 애초에 없는 설계라 verified일 때만 검사한다.
    manifest_id_list = [
        item.get("finding_id") for item in (manifest.get("items") if isinstance(manifest.get("items"), list) else [])
        if isinstance(item, dict)
    ]
    dup_manifest_ids = sorted({fid for fid in manifest_id_list if fid is not None and manifest_id_list.count(fid) > 1})
    if dup_manifest_ids:
        errors.append(f"(f) finding_id(s) duplicated in render-manifest items[]: {dup_manifest_ids}")

    _MANIFEST_ITEM_KEYS = {
        "verified": {"finding_id", "severity", "status", "tag", "quote"},
        "rejected": {"finding_id", "severity", "status", "counter_citation_verdict"},
    }
    for item in (manifest.get("items") if isinstance(manifest.get("items"), list) else []):
        if not isinstance(item, dict):
            errors.append("(f) render-manifest item is not a mapping")
            continue
        fid = item.get("finding_id")
        body_item = by_body.get(fid)
        if body_item is None:
            errors.append(f"(f) {fid}: present in render-manifest but not found in visible body")
            continue  # r4-14: 아래 body_item.get() 호출이 AttributeError로 죽는다
        expected_keys = _MANIFEST_ITEM_KEYS.get(item.get("status"))
        if expected_keys is not None and set(item.keys()) != expected_keys:
            # r2-07 잔여: status별로 렌더러가 싣는 키 집합은 고정이다 — 낯선 키를
            # 끼우거나 필요한 키를 빼는 조작을 문법 자체로 막는다.
            errors.append(
                f"(f) {fid}: render-manifest item keys are {sorted(item.keys())}, expected "
                f"{sorted(expected_keys)} for status {item.get('status')!r}"
            )
            continue
        status = by_body_status.get(fid)
        if item.get("status") != status:
            errors.append(
                f"(f) {fid}: manifest status {item.get('status')!r} != visible body section "
                f"({status!r}) — body/manifest inconsistency"
            )
        if item.get("severity") != body_item.get("severity"):
            errors.append(
                f"(f) {fid}: manifest severity {item.get('severity')!r} != visible body severity "
                f"{body_item.get('severity')!r} — body/manifest inconsistency"
            )
        if status == "verified":
            if item.get("tag") != body_item.get("tag"):
                errors.append(
                    f"(f) {fid}: manifest tag {item.get('tag')!r} != visible body tag "
                    f"{body_item.get('tag')!r} — body/manifest inconsistency"
                )
            if (item.get("quote") or None) != (body_item.get("quote") or None):
                errors.append(f"(f) {fid}: manifest quote != visible body quote — body/manifest inconsistency")
        # v2-05: rejected 카드의 counter_citation_verdict는 render_human_summary.py가
        # manifest item에도 싣는 필드다 — 이전 판은 status/tag/severity/quote만 대조하고
        # 이 필드는 검증하지 않아, verdict만 골라 조작한 manifest가 그대로 통과했다.
        if status == "rejected":
            if item.get("counter_citation_verdict") != body_item.get("counter_citation_verdict"):
                errors.append(
                    f"(f) {fid}: manifest counter_citation_verdict {item.get('counter_citation_verdict')!r} != "
                    f"visible body value {body_item.get('counter_citation_verdict')!r} — body/manifest inconsistency"
                )

    # v2-01: manifest items[]가 body의 모든 finding_id를 커버하는지도 강제한다 —
    # 위 루프는 manifest에 있는 항목만 순회하므로, manifest에서 통째로 빠진
    # finding_id는 지금까지 잡히지 않았다(예: 카드를 몰래 추가하면서 manifest는
    # 갱신하지 않는 조작).
    manifest_ids = {
        item.get("finding_id") for item in (manifest.get("items") if isinstance(manifest.get("items"), list) else [])
        if isinstance(item, dict)
    }
    body_ids = set(by_body.keys())
    missing_from_manifest = sorted(body_ids - manifest_ids)
    if missing_from_manifest:
        errors.append(
            f"(f) finding(s) present in visible body but missing from render-manifest items[]: "
            f"{missing_from_manifest}"
        )

    return errors


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("summary", type=Path, help="the rendered summary to re-verify")
    parser.add_argument("--source", type=Path, required=True, help="the canonical receipt the summary claims to derive from")
    parser.add_argument(
        "--target-doc",
        type=Path,
        required=True,
        help="the full review target document -- rendered source quotations are pulled out of it "
             "again and compared, independently of the renderer",
    )
    args = parser.parse_args(argv)

    # v2r4-03: OSError만 잡으면 UTF-8이 아닌 summary.md가 UnicodeDecodeError로
    # 새어나가 문서화된 usage-error exit 2 대신 처리되지 않은 트레이스백(exit 1)이
    # 됐다.
    try:
        summary_text = args.summary.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError) as exc:
        print(f"USAGE-ERROR: cannot read {args.summary}: {exc}", file=sys.stderr)
        return 2

    try:
        source_bytes = read_source_bytes(args.source)
        receipt = load_receipt_from_bytes(args.source, source_bytes)
    except UsageError as exc:
        print(f"USAGE-ERROR: {exc}", file=sys.stderr)
        return 2
    except RenderStructuralError as exc:
        print(f"USAGE-ERROR: {exc}", file=sys.stderr)
        return 2

    try:
        target = TargetDoc(args.target_doc, read_source_bytes(args.target_doc))
    except UsageError as exc:
        print(f"USAGE-ERROR: {exc}", file=sys.stderr)
        return 2

    try:
        errors = audit(summary_text, receipt, source_bytes, target, args.source)
    except UsageError as exc:
        print(f"USAGE-ERROR: {exc}", file=sys.stderr)
        return 2

    if errors:
        print("TRACE-FAIL:")
        for err in errors:
            print(f"  - {err}")
        return 1

    print(f"TRACE-OK: visible body independently re-verified against {args.source}")
    print(
        "NOTE: entailment (does the tag actually follow from the quote?) is NOT "
        "verified by this tool — residual risk, see PLAN_human_summary_v0.md §6/§7."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
