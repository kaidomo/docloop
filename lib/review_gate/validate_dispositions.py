#!/usr/bin/env python3
"""validate_dispositions.py — 부재 계열 question의 사람 처분 기록(dispositions.yaml) 검증 (docauth#351).

사용: python3 validate_dispositions.py <dispositions.yaml> [--ledger <ledger.yaml>]
스키마 정본: playbooks/review-gate/contracts/dispositions-schema.yaml

검사: 형식(필수 필드·enum·날짜) · `--ledger`가 있으면 question_record_id가 원장의 question이고 `absence_class`를
가진 record인지(부재 질문이 아닌 record에 처분을 붙이지 못한다) · 같은 question에 항목이 여럿이면 마지막이
현행(append-only — 이전 항목은 이력, WARN으로 통지). 출력: `real_defect` 목록(다음 라운드 finding 후보)과
`decided` 목록(결정 레지스트리 시드 후보 — 등록은 사람 승격 도구, 자동 등록 없음).
종료 코드: 0 PASS · 1 FAIL · 2 사용 오류.
"""
from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

import yaml

sys.path.insert(0, str(Path(__file__).resolve().parent))
try:
    from .validate_review_intermediate import ABSENCE_CLASSES, load_yaml_text
except ImportError:
    from validate_review_intermediate import ABSENCE_CLASSES, load_yaml_text

DISPOSITIONS = {"decided", "no_concept", "parent_document", "adjacent_evidence", "real_defect"}
REQUIRED = ("question_record_id", "disposition", "by", "date", "note")
OPTIONAL = ("evidence_ref",)
RE_DATE = re.compile(r"^\d{4}-\d{2}-\d{2}$")


def _nonempty(v) -> bool:
    return isinstance(v, str) and bool(v.strip())


def validate(path: Path, ledger_path: Path | None = None) -> tuple[list[str], list[str], dict]:
    errors: list[str] = []
    warnings: list[str] = []
    summary = {"real_defect": [], "decided": [], "current": {}}
    try:
        data = load_yaml_text(path.read_bytes())
    except (OSError, ValueError, yaml.YAMLError) as exc:
        return [f"cannot read dispositions: {exc}"], [], summary
    if not isinstance(data, dict):
        return ["top level must be a mapping"], [], summary
    if data.get("schema_version") != 1:
        errors.append("schema_version must be 1")
    for key in ("target", "snapshot_id"):
        if not _nonempty(data.get(key)):
            errors.append(f"{key} must be a nonempty string")
    items = data.get("dispositions")
    if not isinstance(items, list):
        return errors + ["dispositions must be a list"], warnings, summary
    ledger_questions: dict[str, dict] | None = None
    if ledger_path is not None:
        try:
            ledger = load_yaml_text(ledger_path.read_bytes())
        except (OSError, ValueError, yaml.YAMLError) as exc:
            return errors + [f"cannot read ledger: {exc}"], warnings, summary
        body = ledger.get("review_intermediate") if isinstance(ledger, dict) else None
        # 구현 r1-02: 원장이 주어졌으면 구조를 먼저 본다 — 최상위 키·questions 목록이 없는 파일은 '원장'이 아니다.
        if not isinstance(body, dict) or not isinstance(body.get("questions"), list):
            return errors + ["--ledger is not a review_intermediate ledger with a questions list"], warnings, summary
        ledger_questions = {q.get("record_id"): q for q in body["questions"] if isinstance(q, dict)}
        if body.get("snapshot_id") != data.get("snapshot_id"):
            errors.append("snapshot_id must match the ledger snapshot_id")
        if body.get("target") != data.get("target"):
            errors.append("target must match the ledger target")
    seen: dict[str, int] = {}
    for index, item in enumerate(items):
        prefix = f"dispositions[{index}]"
        if not isinstance(item, dict):
            errors.append(f"{prefix} must be a mapping")
            continue
        extra = set(item) - set(REQUIRED) - set(OPTIONAL)
        if extra:
            errors.append(f"{prefix} unknown fields: {', '.join(sorted(extra))}")
        for field in REQUIRED:
            if not _nonempty(item.get(field)):
                errors.append(f"{prefix}.{field} must be a nonempty string")
        if item.get("disposition") not in DISPOSITIONS:
            errors.append(f"{prefix}.disposition must be one of {', '.join(sorted(DISPOSITIONS))}")
        if _nonempty(item.get("date")) and not RE_DATE.fullmatch(item["date"]):
            errors.append(f"{prefix}.date must be YYYY-MM-DD (quoted)")
        # 구현 r1-03: 선택 필드도 있으면 문자열이어야 한다(생략과 잘못된 타입을 구분).
        if "evidence_ref" in item and not _nonempty(item.get("evidence_ref")):
            errors.append(f"{prefix}.evidence_ref must be a nonempty string when present")
        elif item.get("disposition") in {"decided", "parent_document", "adjacent_evidence"} and not _nonempty(item.get("evidence_ref")):
            warnings.append(f"{prefix}: {item.get('disposition')} without evidence_ref -- 근거 위치가 없으면 다음 라운드가 같은 질문을 다시 낸다")
        qid = item.get("question_record_id")
        if _nonempty(qid):
            seen[qid] = seen.get(qid, 0) + 1
            summary["current"][qid] = item.get("disposition")
            if ledger_questions is not None:
                q = ledger_questions.get(qid)
                if q is None:
                    errors.append(f"{prefix}.question_record_id {qid!r} is not a question in the ledger")
                elif q.get("absence_class") not in ABSENCE_CLASSES:
                    errors.append(f"{prefix}.question_record_id {qid!r} is not an absence-class question (#351)")
    for qid, count in seen.items():
        if count > 1:
            warnings.append(f"question {qid} has {count} dispositions -- the last one is current (append-only history)")
    for qid, disposition in summary["current"].items():
        if disposition == "real_defect":
            summary["real_defect"].append(qid)
        elif disposition == "decided":
            summary["decided"].append(qid)
    return errors, warnings, summary


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("dispositions", type=Path)
    parser.add_argument("--ledger", type=Path, default=None, help="원장(ledger.yaml) — question 실재·absence_class 대조")
    args = parser.parse_args(argv)
    errors, warnings, summary = validate(args.dispositions, args.ledger)
    for w in warnings:
        print(f"WARN: {w}")
    if errors:
        for e in errors:
            print(f"FAIL: {e}", file=sys.stderr)
        return 1
    print(
        f"DISPOSITIONS-OK: {len(summary['current'])} question(s) disposed · real_defect(다음 라운드 finding 후보): "
        f"{', '.join(summary['real_defect']) or '없음'} · decided(레지스트리 시드 후보 — 사람 승격): "
        f"{', '.join(summary['decided']) or '없음'}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
