#!/usr/bin/env python3
"""Validate a review-gate template convention profile deterministically."""

from __future__ import annotations

import argparse
from pathlib import Path
import re
import sys
from typing import Any
import unicodedata

import yaml


SCHEMA_VERSION = 1
PROFILE_STATUSES = {"provisional", "validated"}
SCOPES = {"document", "template"}
DOCMODEL_KEYS = {"sections", "precedence", "correspondence", "ownership"}
ROLE_VALUES = {"canonical", "derived", "reference", "undetermined"}
CORRESPONDENCE_RULES = {"summary_ok", "strict_1to1", "upward_only"}
STABLE_ID = re.compile(r"[^\s/]+")
# Control, format, and separator characters are invisible: an id built from them
# names nothing while still passing a "nonempty, no whitespace" reading.
INVISIBLE_CATEGORIES = {"Cc", "Cf", "Zl", "Zp", "Zs"}
# A mark modifies the character in front of it, so marks are legitimate *inside* an id
# (decomposed `é` is `e` + U+0301) but an id made only of them modifies nothing. The
# same holds for surrogate, private-use, and unassigned code points: an id needs at
# least one character that stands on its own.
NON_BASE_CATEGORIES = INVISIBLE_CATEGORIES | {"Mn", "Me", "Mc", "Cs", "Co", "Cn"}
# Unicode's default-ignorable code points render as nothing, survive NFC, and are not
# all in a category above — U+034F and U+3164 are a mark and a *letter*. Appending one
# to an id yields a second spelling that looks identical and compares unequal, which is
# exactly what the equality-based anti-bypass rules cannot survive. Ranges follow
# DerivedCoreProperties Default_Ignorable_Code_Point.
DEFAULT_IGNORABLE_RANGES = (
    (0x00AD, 0x00AD), (0x034F, 0x034F), (0x061C, 0x061C), (0x115F, 0x1160),
    (0x17B4, 0x17B5), (0x180B, 0x180F), (0x200B, 0x200F), (0x202A, 0x202E),
    (0x2060, 0x206F), (0x3164, 0x3164), (0xFE00, 0xFE0F), (0xFEFF, 0xFEFF),
    (0xFFA0, 0xFFA0), (0xFFF0, 0xFFF8), (0x1BCA0, 0x1BCA3), (0x1D173, 0x1D17A),
    (0xE0000, 0xE0FFF),
)


def _is_default_ignorable(char: str) -> bool:
    code = ord(char)
    return any(start <= code <= end for start, end in DEFAULT_IGNORABLE_RANGES)


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


def invisible_character(value: str) -> str | None:
    """The first character that draws nothing a reader could see, if the value has one.

    Every rule that compares these strings compares them for equality, so a character
    that draws nothing — or that draws exactly what an ordinary space draws without
    being one — is a way to write a second, identical-looking spelling that compares
    unequal. That holds for a section name as much as for an id. The ordinary space is
    an exception **inside** a value: section names are prose (`작업 목적`), not
    identifiers. A **leading or trailing** space is not exempt — `"3 "` renders
    identically to `"3"` but compares unequal to it (`identity_key()` does not strip),
    so it is the same second-spelling attack wearing a plain space (r6-01, PR #227 r6).
    """
    for index, char in enumerate(value):
        if char == " ":
            if index == 0 or index == len(value) - 1:
                return char
            continue
        if unicodedata.category(char) in INVISIBLE_CATEGORIES or _is_default_ignorable(char):
            return char
    return None


def is_stable_id(value: Any) -> bool:
    """True when the value actually names something and names it exactly once.

    `fullmatch` rather than `match`: `$` also matches before a trailing newline, so
    `"profile-v1\\n"` used to pass the id rule and then compare unequal to the very
    same id — enough to slip past an equality-based fail-closed rule.
    """
    if not isinstance(value, str) or not value.strip():
        return False
    if STABLE_ID.fullmatch(value) is None:
        return False
    if invisible_character(value) is not None:
        return False
    return any(
        unicodedata.category(char) not in NON_BASE_CATEGORIES for char in value
    )


def identity_key(value: str) -> str:
    """Canonical comparison key for an id or a section name.

    Korean ids such as `submission-기획서-v1` have distinct NFC and NFD encodings that
    render identically; macOS filesystems hand out the decomposed form. Comparing raw
    strings would let the decomposed spelling of an id count as a *different* id.
    NFC only — NFKC would fold visibly distinct ids (`ﬁ`→`fi`) onto one key.
    """
    return unicodedata.normalize("NFC", value)


def same_identity(left: Any, right: Any) -> bool:
    """Compare two stable ids as identities, not as byte strings."""
    if not isinstance(left, str) or not isinstance(right, str):
        return False
    return identity_key(left) == identity_key(right)


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
    for item in value:
        invisible = invisible_character(item)
        if invisible is not None:
            if invisible == " ":
                return [
                    f"{label} entries must not have leading or trailing spaces "
                    "(U+0020); a boundary space renders identically to its trimmed "
                    "form but compares unequal to it, producing a second spelling"
                ]
            return [
                f"{label} entries must not contain invisible characters "
                f"(U+{ord(invisible):04X}); they render as nothing and produce a second "
                "spelling that compares unequal"
            ]
    keys = [identity_key(item) for item in value]
    if len(keys) != len(set(keys)):
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
                    # id is an identity compared for equality elsewhere (precedence,
                    # correspondence, ownership all reference it) -- is_stable_id()
                    # closes the invisible/default-ignorable-character bypass the
                    # same way #227 closed it for questions/options ids (r6-02).
                    if not is_stable_id(section.get("id")):
                        errors.append(f"{item_label}.id must be a stable nonempty id")
                    if not _nonempty(section.get("title")):
                        errors.append(f"{item_label}.title must be nonempty")
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
                    # wins references a section id by identity (r6-02, same reasoning
                    # as sections[].id above).
                    if not is_stable_id(rule.get("wins")):
                        errors.append(f"{item_label}.wins must be a stable nonempty id")
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
                    # from/to reference section ids by identity (r6-02).
                    for field in ("from", "to"):
                        if not is_stable_id(rule.get(field)):
                            errors.append(f"{item_label}.{field} must be a stable nonempty id")
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
                    # required_in references a section id -- a strict identifier
                    # (r6-02, PR #227 r6, PR #246 referral).
                    if not is_stable_id(item.get("required_in")):
                        errors.append(f"{item_label}.required_in must be a stable nonempty id")
                    # detail is prose, not an id (schema's own example: "페이지당
                    # 개수·정렬" -- interior spaces are legitimate), but it IS the
                    # equality key _merge_named_list() dedups ownership entries on
                    # (r6-04). Same rule as observed_sections/declaration_targets:
                    # interior space allowed, invisible/boundary characters are not.
                    detail = item.get("detail")
                    if not _nonempty(detail):
                        errors.append(f"{item_label}.detail must be nonempty")
                    else:
                        invisible = invisible_character(detail)
                        if invisible is not None:
                            errors.append(
                                f"{item_label}.detail must not contain invisible "
                                f"characters (U+{ord(invisible):04X}); they render as "
                                "nothing and produce a second spelling that compares "
                                "unequal"
                            )
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
        if not is_stable_id(value):
            errors.append(f"{field} must be a stable nonempty id without spaces, slashes, or invisible characters")
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
        if not is_stable_id(question_id):
            errors.append(f"{label}.id must be a stable nonempty id")
        elif identity_key(question_id) in question_ids:
            # identity_key, not raw: two ids that are stable individually can still be
            # the same identity in NFC/NFD (r6-03, PR #227 r6) — is_stable_id() rejects
            # invisible characters but does not fold decomposition.
            errors.append(f"duplicate question id: {question_id}")
        else:
            question_ids.add(identity_key(question_id))
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
            if not is_stable_id(option_id):
                errors.append(f"{option_label}.id must be a stable nonempty id")
            elif identity_key(option_id) in option_ids:
                # identity_key, not raw — same reasoning as question_id above (r6-03).
                errors.append(f"{label} duplicate option id: {option_id}")
            else:
                option_ids.add(identity_key(option_id))
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
