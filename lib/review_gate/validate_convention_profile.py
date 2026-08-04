#!/usr/bin/env python3
"""Validate a review-gate template convention profile deterministically."""

from __future__ import annotations

import argparse
from pathlib import Path
import re
import sys
from typing import Any

import yaml


SCHEMA_VERSION = 1
PROFILE_STATUSES = {"provisional", "validated"}
SCOPES = {"document", "template"}
DOCMODEL_KEYS = {"sections", "precedence", "correspondence", "ownership"}
ROLE_VALUES = {"canonical", "derived", "reference", "undetermined"}
CORRESPONDENCE_RULES = {"summary_ok", "strict_1to1", "upward_only"}
STABLE_ID = re.compile(r"^[^\s/]+$")


class DuplicateKeyError(ValueError):
    pass


class StrictLoader(yaml.SafeLoader):
    pass


def _strict_mapping(loader: StrictLoader, node: yaml.MappingNode, deep: bool = False) -> Any:
    seen: set[Any] = set()
    for key_node, _ in node.value:
        key = loader.construct_object(key_node, deep=deep)
        if key in seen:
            raise DuplicateKeyError(
                f"duplicate YAML key {key!r} at line {key_node.start_mark.line + 1}"
            )
        seen.add(key)
    return yaml.SafeLoader.construct_mapping(loader, node, deep=deep)


StrictLoader.add_constructor(
    yaml.resolver.BaseResolver.DEFAULT_MAPPING_TAG,
    _strict_mapping,
)


def load_yaml(path: Path) -> Any:
    with path.open(encoding="utf-8") as handle:
        return yaml.load(handle, Loader=StrictLoader)


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


def _validate_string_list(value: Any, label: str, *, nonempty: bool = True) -> list[str]:
    if not isinstance(value, list) or (nonempty and not value):
        return [f"{label} must be a{' nonempty' if nonempty else ''} list"]
    if any(not _nonempty(item) for item in value):
        return [f"{label} entries must be nonempty strings"]
    if len(value) != len(set(value)):
        return [f"{label} entries must be unique"]
    return []


def _validate_declaration(fragment: Any, label: str) -> list[str]:
    if not isinstance(fragment, dict) or not fragment:
        return [f"{label} must be a nonempty mapping"]
    errors: list[str] = []
    extra = set(fragment).difference(DOCMODEL_KEYS)
    if extra:
        errors.append(f"{label} contains unsupported docmodel fields: {', '.join(sorted(extra))}")

    sections = fragment.get("sections")
    if sections is not None:
        if not isinstance(sections, list) or not sections:
            errors.append(f"{label}.sections must be a nonempty list")
        else:
            for index, section in enumerate(sections):
                item_label = f"{label}.sections[{index}]"
                errors.extend(_exact_keys(
                    section,
                    {"id", "title", "role"},
                    {"stale_is_defect", "note"},
                    item_label,
                ))
                if isinstance(section, dict):
                    for field in ("id", "title"):
                        if not _nonempty(section.get(field)):
                            errors.append(f"{item_label}.{field} must be nonempty")
                    if section.get("role") not in ROLE_VALUES:
                        errors.append(f"{item_label}.role is invalid")
                    if "stale_is_defect" in section and not isinstance(section["stale_is_defect"], bool):
                        errors.append(f"{item_label}.stale_is_defect must be boolean")

    precedence = fragment.get("precedence")
    if precedence is not None:
        if not isinstance(precedence, list) or not precedence:
            errors.append(f"{label}.precedence must be a nonempty list")
        else:
            for index, rule in enumerate(precedence):
                item_label = f"{label}.precedence[{index}]"
                errors.extend(_exact_keys(rule, {"wins", "over"}, {"note"}, item_label))
                if isinstance(rule, dict):
                    if not _nonempty(rule.get("wins")):
                        errors.append(f"{item_label}.wins must be nonempty")
                    errors.extend(_validate_string_list(rule.get("over"), f"{item_label}.over"))

    correspondence = fragment.get("correspondence")
    if correspondence is not None:
        if not isinstance(correspondence, list) or not correspondence:
            errors.append(f"{label}.correspondence must be a nonempty list")
        else:
            for index, rule in enumerate(correspondence):
                item_label = f"{label}.correspondence[{index}]"
                errors.extend(_exact_keys(
                    rule,
                    {"from", "to", "rule"},
                    {"matrix_location", "note"},
                    item_label,
                ))
                if isinstance(rule, dict):
                    for field in ("from", "to"):
                        if not _nonempty(rule.get(field)):
                            errors.append(f"{item_label}.{field} must be nonempty")
                    if rule.get("rule") not in CORRESPONDENCE_RULES:
                        errors.append(f"{item_label}.rule is invalid")

    ownership = fragment.get("ownership")
    if ownership is not None:
        if not isinstance(ownership, list) or not ownership:
            errors.append(f"{label}.ownership must be a nonempty list")
        else:
            for index, item in enumerate(ownership):
                item_label = f"{label}.ownership[{index}]"
                errors.extend(_exact_keys(
                    item,
                    {"detail", "required_in"},
                    {"value_authority", "mirrors_ok", "note"},
                    item_label,
                ))
                if isinstance(item, dict):
                    for field in ("detail", "required_in"):
                        if not _nonempty(item.get(field)):
                            errors.append(f"{item_label}.{field} must be nonempty")
                    if "value_authority" in item and not _nonempty(item["value_authority"]):
                        errors.append(f"{item_label}.value_authority must be nonempty")
                    if "mirrors_ok" in item:
                        errors.extend(_validate_string_list(
                            item["mirrors_ok"], f"{item_label}.mirrors_ok", nonempty=False
                        ))
    return errors


def _declaration_targets(fragment: Any) -> set[str]:
    """Return stable collection identities written by a declaration fragment."""
    if not isinstance(fragment, dict):
        return set()
    targets: set[str] = set()
    for section in fragment.get("sections", []):
        if isinstance(section, dict) and _nonempty(section.get("id")):
            targets.add(f"sections[{section['id']}]")
    for rule in fragment.get("precedence", []):
        if isinstance(rule, dict) and _nonempty(rule.get("wins")):
            targets.add(f"precedence[{rule['wins']}]")
    for rule in fragment.get("correspondence", []):
        if (
            isinstance(rule, dict)
            and _nonempty(rule.get("from"))
            and _nonempty(rule.get("to"))
        ):
            targets.add(f"correspondence[{rule['from']}->{rule['to']}]")
    for item in fragment.get("ownership", []):
        if isinstance(item, dict) and _nonempty(item.get("detail")):
            targets.add(f"ownership[{item['detail']}]")
    return targets


def validate_data(data: Any) -> list[str]:
    errors = _exact_keys(
        data,
        {"schema_version", "profile_id", "template_id", "status", "transferability", "questions"},
        set(),
        "profile",
    )
    if not isinstance(data, dict):
        return errors
    if data.get("schema_version") != SCHEMA_VERSION:
        errors.append("schema_version must be 1")
    for field in ("profile_id", "template_id"):
        value = data.get(field)
        if not _nonempty(value) or not STABLE_ID.match(value):
            errors.append(f"{field} must be a stable nonempty id without spaces or slashes")
    status = data.get("status")
    if status not in PROFILE_STATUSES:
        errors.append("status must be provisional or validated")

    transferability = data.get("transferability")
    errors.extend(_exact_keys(
        transferability,
        {"universal", "deferred_to_issue", "note"},
        set(),
        "transferability",
    ))
    if isinstance(transferability, dict):
        if not isinstance(transferability.get("universal"), bool):
            errors.append("transferability.universal must be boolean")
        if not _nonempty(transferability.get("note")):
            errors.append("transferability.note must be nonempty")
        if status == "provisional":
            if transferability.get("universal") is not False:
                errors.append("provisional profile must explicitly set transferability.universal=false")
            if not _nonempty(transferability.get("deferred_to_issue")):
                errors.append(
                    "provisional profile transferability must set deferred_to_issue "
                    "to a nonempty issue reference"
                )
        elif transferability.get("deferred_to_issue") is not None and not _nonempty(
            transferability.get("deferred_to_issue")
        ):
            errors.append("transferability.deferred_to_issue must be null or nonempty")

    questions = data.get("questions")
    if not isinstance(questions, list) or not questions:
        errors.append("questions must be a nonempty list")
        return errors
    question_ids: set[str] = set()
    for q_index, question in enumerate(questions):
        label = f"questions[{q_index}]"
        errors.extend(_exact_keys(
            question,
            {
                "id", "prompt", "applicability", "declaration_targets",
                "allowed_scopes", "provenance_required", "options",
            },
            set(),
            label,
        ))
        if not isinstance(question, dict):
            continue
        question_id = question.get("id")
        if not _nonempty(question_id) or not STABLE_ID.match(question_id):
            errors.append(f"{label}.id must be a stable nonempty id")
        elif question_id in question_ids:
            errors.append(f"duplicate question id: {question_id}")
        else:
            question_ids.add(question_id)
        if not _nonempty(question.get("prompt")):
            errors.append(f"{label}.prompt must be nonempty")
        errors.extend(_validate_string_list(
            question.get("declaration_targets"), f"{label}.declaration_targets"
        ))
        declared_targets = set(question.get("declaration_targets", [])) \
            if isinstance(question.get("declaration_targets"), list) else set()
        applicability = question.get("applicability")
        errors.extend(_exact_keys(
            applicability,
            {"all_sections_present", "note"},
            set(),
            f"{label}.applicability",
        ))
        if isinstance(applicability, dict):
            errors.extend(_validate_string_list(
                applicability.get("all_sections_present"),
                f"{label}.applicability.all_sections_present",
            ))
            if not _nonempty(applicability.get("note")):
                errors.append(f"{label}.applicability.note must be nonempty")
        scopes = question.get("allowed_scopes")
        errors.extend(_validate_string_list(scopes, f"{label}.allowed_scopes"))
        if isinstance(scopes, list) and any(scope not in SCOPES for scope in scopes):
            errors.append(f"{label}.allowed_scopes may contain only document or template")
        if question.get("provenance_required") is not True:
            errors.append(f"{label}.provenance_required must be true")

        options = question.get("options")
        if not isinstance(options, list) or not options:
            errors.append(f"{label}.options must be a nonempty list")
            continue
        option_ids: set[str] = set()
        for o_index, option in enumerate(options):
            option_label = f"{label}.options[{o_index}]"
            errors.extend(_exact_keys(option, {"id", "label", "declaration"}, set(), option_label))
            if not isinstance(option, dict):
                continue
            option_id = option.get("id")
            if not _nonempty(option_id) or not STABLE_ID.match(option_id):
                errors.append(f"{option_label}.id must be a stable nonempty id")
            elif option_id in option_ids:
                errors.append(f"{label} duplicate option id: {option_id}")
            else:
                option_ids.add(option_id)
            if not _nonempty(option.get("label")):
                errors.append(f"{option_label}.label must be nonempty")
            declaration = option.get("declaration")
            errors.extend(_validate_declaration(declaration, f"{option_label}.declaration"))
            actual_targets = _declaration_targets(declaration)
            if actual_targets != declared_targets:
                missing_targets = declared_targets.difference(actual_targets)
                extra_targets = actual_targets.difference(declared_targets)
                detail: list[str] = []
                if missing_targets:
                    detail.append(f"missing {', '.join(sorted(missing_targets))}")
                if extra_targets:
                    detail.append(f"undeclared {', '.join(sorted(extra_targets))}")
                errors.append(
                    f"{option_label}.declaration identities must exactly match declaration_targets"
                    + (f": {'; '.join(detail)}" if detail else "")
                )
    return errors


def validate(path: Path) -> list[str]:
    try:
        return validate_data(load_yaml(path))
    except (OSError, yaml.YAMLError, DuplicateKeyError) as exc:
        return [str(exc)]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("profile", type=Path)
    args = parser.parse_args(argv)
    errors = validate(args.profile)
    if errors:
        for error in errors:
            print(f"FAIL: {error}", file=sys.stderr)
        return 1
    print("OK: convention profile is structurally valid")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
