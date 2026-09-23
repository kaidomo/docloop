#!/usr/bin/env python3
"""review-gate 문서 구조 선언(docmodel.<템플릿>.yaml) 구조 검증기 — fail-closed.

closes docauth#315 파생 발견: `docmodel-schema.yaml`은 이 레포의 "스키마 정본 1개당
검증기 1개" 관례(`decisions-schema.yaml`↔`validate_decisions.py`,
`docmodel-approvals-schema.yaml`↔`validate_docmodel_approvals.py` 등)의 유일한
예외였다 — 대응 검증기가 없었다.

검사 범위는 §1 입력⑤ 직접조회 매칭(`docmodel_match.py`)이 후보를 받아들이기 전에
필요한 최소 구조뿐이다: `meta`의 필수 필드·타입, `sections[]`가 **비어 있지 않고**
각 항목이 비어 있지 않은 문자열 `title`을 갖는지(빈 `sections: []`는 부분집합
매칭에서 vacuous truth로 항상 통과해버리는 구멍이었다, Codex 피어리뷰 r1-03).
`precedence`/`correspondence`/`ownership`(선택 절)의 세부 구조는 이 검증기의
범위가 아니다 — 그 절들은 §3 상호참조 축 판정에만 쓰이고, 이번 직접조회 매칭은
`sections[].title`만 본다.

사용: python3 validate_docmodel.py <docmodel.yaml>
"""
from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path
from typing import Any

import yaml

REQUIRED_META = ("template", "updated_at", "approval_state")
APPROVAL_STATE_ENUM = {"approved", "draft"}
RE_DATE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
ROLE_ENUM = {"canonical", "derived", "reference", "undetermined"}


class DupKeyError(Exception):
    pass


class _StrictLoader(yaml.SafeLoader):
    """YAML 중복 키를 오류로 처리."""


def _strict_map(loader, node, deep=False):
    seen = set()
    for k_node, _ in node.value:
        k = loader.construct_object(k_node, deep=deep)
        if k in seen:
            raise DupKeyError(f"YAML 중복 키: {k!r} (line {k_node.start_mark.line + 1})")
        seen.add(k)
    return yaml.SafeLoader.construct_mapping(loader, node, deep)


_StrictLoader.add_constructor(yaml.resolver.BaseResolver.DEFAULT_MAPPING_TAG, _strict_map)


def _is_str(v: Any) -> bool:
    return isinstance(v, str) and v.strip() != ""


def load(path: Path, content: bytes | None = None) -> Any:
    if content is not None:
        return yaml.load(content, Loader=_StrictLoader)
    with open(path, encoding="utf-8") as f:
        return yaml.load(f, Loader=_StrictLoader)


def validate(path: Path, *, content: bytes | None = None) -> list[str]:
    """docmodel.yaml 구조를 검사한다. 파일시스템 접근 없이(content가 주어지면) 동작 가능."""
    E: list[str] = []
    try:
        data = load(path, content=content)
    except FileNotFoundError:
        return [f"파일 없음: {path}"]
    except DupKeyError as exc:
        return [str(exc)]
    except yaml.YAMLError as exc:
        return [f"YAML 파싱 실패: {exc}"]
    except TypeError as exc:
        return [f"YAML 파싱 실패(해시 불가능한 키): {exc}"]

    if not isinstance(data, dict):
        return ["최상위가 매핑이 아님"]

    meta = data.get("meta")
    if not isinstance(meta, dict):
        E.append("meta 블록 누락")
        meta = {}
    for key in REQUIRED_META:
        if not _is_str(meta.get(key)):
            E.append(f"meta.{key}: 비어있지 않은 문자열이어야 함 (현재 {type(meta.get(key)).__name__})")
    if _is_str(meta.get("updated_at")) and not RE_DATE.fullmatch(meta["updated_at"]):
        E.append("meta.updated_at: YYYY-MM-DD 형식이 아님(따옴표 없는 날짜는 YAML이 date 객체로 파싱함, #194·#226)")

    approval_state = meta.get("approval_state")
    if _is_str(approval_state) and approval_state not in APPROVAL_STATE_ENUM:
        E.append(f"meta.approval_state '{approval_state}' 무효(허용 {sorted(APPROVAL_STATE_ENUM)})")

    suppression_eligible = meta.get("suppression_eligible")
    if not isinstance(suppression_eligible, bool):
        E.append(f"meta.suppression_eligible: bool이어야 함 (현재 {type(suppression_eligible).__name__})")

    approved_by = meta.get("approved_by")
    if approval_state == "approved":
        if not _is_str(approved_by):
            E.append("meta.approved_by: approval_state가 approved면 비어있지 않은 문자열이어야 함")
    elif approval_state == "draft":
        if approved_by is not None:
            E.append("meta.approved_by: approval_state가 draft면 null이어야 함")

    sections = data.get("sections")
    if not isinstance(sections, list) or not sections:
        E.append("sections: 비어있지 않은 리스트여야 함(#315 — 빈 목록은 부분집합 매칭을 vacuous하게 항상 통과시킴)")
        sections = []

    seen_ids: dict[str, int] = {}
    for index, item in enumerate(sections):
        tag = f"sections[{index}]"
        if not isinstance(item, dict):
            E.append(f"{tag}: 매핑이 아님")
            continue
        section_id = item.get("id")
        if _is_str(section_id):
            tag = f"sections[id={section_id!r}]"
            seen_ids[section_id] = seen_ids.get(section_id, 0) + 1
        else:
            E.append(f"{tag}: id는 비어있지 않은 문자열이어야 함 (현재 {type(section_id).__name__})")
        if not _is_str(item.get("title")):
            E.append(f"{tag}: title은 비어있지 않은 문자열이어야 함 (현재 {type(item.get('title')).__name__})")
        role = item.get("role")
        if not _is_str(role) or role not in ROLE_ENUM:
            E.append(f"{tag}: role '{role}' 무효(허용 {sorted(ROLE_ENUM)})")
        if "stale_is_defect" in item and not isinstance(item.get("stale_is_defect"), bool):
            E.append(f"{tag}: stale_is_defect는 bool이어야 함")

    for section_id, count in seen_ids.items():
        if count > 1:
            E.append(f"sections[].id 중복: {section_id!r} ×{count}")

    return E


def main() -> None:
    parser = argparse.ArgumentParser(description="review-gate docmodel.<템플릿>.yaml 구조 검증(fail-closed)")
    parser.add_argument("path", type=Path)
    args = parser.parse_args()
    errors = validate(args.path)
    for message in errors:
        print(f"ERROR: {message}")
    if errors:
        print("결과: FAIL — 이 docmodel은 직접조회 매칭 후보로 사용할 수 없다(fail-closed)")
        sys.exit(1)
    print("결과: PASS")
    sys.exit(0)


if __name__ == "__main__":
    main()
