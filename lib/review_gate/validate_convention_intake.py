#!/usr/bin/env python3
"""Validate a field-complete review-gate startup convention intake."""

from __future__ import annotations

import argparse
from pathlib import Path
import re
import sys
from typing import Any

import yaml

if __package__:
    from .validate_convention_profile import (
        DuplicateKeyError,
        SCOPES,
        load_yaml,
        validate_data as validate_profile_data,
    )
else:
    from validate_convention_profile import (
        DuplicateKeyError,
        SCOPES,
        load_yaml,
        validate_data as validate_profile_data,
    )


SCHEMA_VERSION = 1
APPROVAL_STATES = {"unanswered", "inapplicable", "unapproved", "approved_to_draft"}
APPLICABILITY_RESULTS = {"applicable", "inapplicable"}
SNAPSHOT_ID = re.compile(
    r"^(?:sha256:[0-9a-f]{64}|commit:[0-9a-f]{7,64}|export:[^\s]+)$"
)
DATE = re.compile(r"^\d{4}-\d{2}-\d{2}$")


def _nonempty(value: Any) -> bool:
    return isinstance(value, str) and bool(value.strip())


def _exact_keys(value: Any, required: set[str], optional: set[str], label: str) -> list[str]:
    if not isinstance(value, dict):
        return [f"{label} must be a mapping"]
    errors: list[str] = []
    missing = required.difference(value)
    extra = set(value).difference(required | optional)
    if missing:
        errors.append(f"{label} missing fields: {', '.join(sorted(missing))}")
    if extra:
        errors.append(f"{label} unknown fields: {', '.join(sorted(extra))}")
    return errors


def validate_data(data: Any, profile: Any) -> list[str]:
    errors = validate_profile_data(profile)
    if errors:
        return [f"profile: {error}" for error in errors]
    errors = _exact_keys(
        data,
        {
            "schema_version", "phase", "profile_id", "template_id", "target_snapshot",
            "recorded_at", "records",
        },
        {"target_document"},
        "intake",
    )
    if not isinstance(data, dict):
        return errors
    if data.get("schema_version") != SCHEMA_VERSION:
        errors.append("schema_version must be 1")
    if data.get("phase") != "pre_lens":
        errors.append("phase must be pre_lens so convention intake precedes lens execution")
    if data.get("profile_id") != profile.get("profile_id"):
        errors.append("profile_id must match the convention profile")
    if data.get("template_id") != profile.get("template_id"):
        errors.append("template_id must match the convention profile")
    if not isinstance(data.get("target_snapshot"), str) or not SNAPSHOT_ID.match(data["target_snapshot"]):
        errors.append("target_snapshot must be a stable nonempty sha256, commit, or export identifier")
    if not isinstance(data.get("recorded_at"), str) or not DATE.match(data["recorded_at"]):
        errors.append("recorded_at must be YYYY-MM-DD")
    if "target_document" in data and not _nonempty(data.get("target_document")):
        errors.append("target_document must be nonempty when present")

    questions = {question["id"]: question for question in profile["questions"]}
    records = data.get("records")
    if not isinstance(records, list):
        errors.append("records must be a list")
        return errors
    seen: set[str] = set()
    for index, record in enumerate(records):
        label = f"records[{index}]"
        errors.extend(_exact_keys(
            record,
            {
                "question_id", "applicability", "asked", "response",
                "authority", "scope", "source", "approval",
            },
            set(),
            label,
        ))
        if not isinstance(record, dict):
            continue
        question_id = record.get("question_id")
        if not _nonempty(question_id):
            errors.append(f"{label}.question_id must be nonempty")
            continue
        if question_id in seen:
            errors.append(f"duplicate intake record for question_id: {question_id}")
        seen.add(question_id)
        question = questions.get(question_id)
        if question is None:
            errors.append(f"{label}.question_id is not in profile: {question_id}")
            continue

        applicability = record.get("applicability")
        errors.extend(_exact_keys(
            applicability,
            {"result", "evidence"},
            set(),
            f"{label}.applicability",
        ))
        applicability_result = None
        if isinstance(applicability, dict):
            applicability_result = applicability.get("result")
            if applicability_result not in APPLICABILITY_RESULTS:
                errors.append(f"{label}.applicability.result is invalid")
            if not _nonempty(applicability.get("evidence")):
                errors.append(f"{label}.applicability.evidence must be nonempty")
        if not isinstance(record.get("asked"), bool):
            errors.append(f"{label}.asked must be boolean")
        approval = record.get("approval")
        if approval not in APPROVAL_STATES:
            errors.append(f"{label}.approval is invalid")
            continue

        response = record.get("response")
        authority = record.get("authority")
        scope = record.get("scope")
        source = record.get("source")

        if approval == "inapplicable":
            if applicability_result != "inapplicable":
                errors.append(f"{label}: inapplicable approval requires inapplicable result")
            if record.get("asked") is not False:
                errors.append(f"{label}: inapplicable question must have asked=false")
            if any(value is not None for value in (response, authority, scope, source)):
                errors.append(f"{label}: inapplicable question must not contain answer authority")
            continue

        if applicability_result != "applicable":
            errors.append(f"{label}: {approval} requires applicable result")
        if approval == "unanswered":
            if any(value is not None for value in (response, authority, scope, source)):
                errors.append(f"{label}: unanswered question must not contain an answer")
            continue

        if record.get("asked") is not True:
            errors.append(f"{label}: answered question must have asked=true")
        option_ids = {option["id"] for option in question["options"]}
        if response not in option_ids:
            errors.append(f"{label}.response must select a declared profile option")
        if not _nonempty(authority):
            errors.append(f"{label}.authority must be nonempty for an answer")
        if scope not in SCOPES or scope not in question["allowed_scopes"]:
            errors.append(f"{label}.scope is not allowed by the profile")
        if scope == "document" and not _nonempty(data.get("target_document")):
            errors.append(f"{label}: document scope requires target_document")
        errors.extend(_exact_keys(source, {"type", "ref"}, set(), f"{label}.source"))
        if isinstance(source, dict):
            for field in ("type", "ref"):
                if not _nonempty(source.get(field)):
                    errors.append(f"{label}.source.{field} must be nonempty")
    missing = set(questions).difference(seen)
    if missing:
        errors.append(f"missing intake records: {', '.join(sorted(missing))}")
    return errors


def validate(intake_path: Path, profile_path: Path) -> list[str]:
    try:
        return validate_data(load_yaml(intake_path), load_yaml(profile_path))
    except (OSError, yaml.YAMLError, DuplicateKeyError) as exc:
        return [str(exc)]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("intake", type=Path)
    parser.add_argument("--profile", type=Path, required=True)
    args = parser.parse_args(argv)
    errors = validate(args.intake, args.profile)
    if errors:
        for error in errors:
            print(f"FAIL: {error}", file=sys.stderr)
        return 1
    print("OK: convention intake is field-complete and structurally valid")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
