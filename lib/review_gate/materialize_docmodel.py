#!/usr/bin/env python3
"""Materialize approved-to-draft convention answers into a docmodel draft."""

from __future__ import annotations

import argparse
from copy import deepcopy
import hashlib
import os
from pathlib import Path
import stat
import sys
from typing import Any

import yaml

from validate_convention_intake import (
    declares_profile_not_applicable,
    validate_data as validate_intake_data,
)
from validate_convention_profile import identity_key, load_yaml


IDENTITY_FIELDS = {
    "sections": ("id",),
    "precedence": ("wins",),
    "correspondence": ("from", "to"),
    "ownership": ("detail",),
}


def _identity_tuple(item: Any, identity_fields: tuple[str, ...]) -> tuple[Any, ...]:
    """Comparison key for a declaration entry's identity fields.

    NFC-normalized via identity_key() so NFC/NFD spellings of the same identity
    merge into one entry instead of materializing as two (r6-04, PR #227 r6,
    PR #246 referral) -- the stored item keeps its original spelling; only the
    lookup key is normalized.
    """
    return tuple(
        identity_key(value) if isinstance(value, str) else value
        for value in (item.get(field) for field in identity_fields)
    )


def _normalized_for_comparison(item: dict[str, Any], identity_fields: tuple[str, ...]) -> dict[str, Any]:
    """`item` with its identity fields folded to their identity_key() form.

    Two entries whose only difference is the NFC/NFD spelling of the identity
    field itself are the same declaration, not a conflict -- only a
    difference in some other field is a genuine conflict.
    """
    normalized = dict(item)
    for field in identity_fields:
        value = normalized.get(field)
        if isinstance(value, str):
            normalized[field] = identity_key(value)
    return normalized


def _merge_named_list(existing: list[Any], incoming: list[Any], key: str) -> list[Any]:
    identity_fields = IDENTITY_FIELDS[key]
    result = deepcopy(existing)
    indexes: dict[tuple[Any, ...], int] = {}
    for index, item in enumerate(result):
        if isinstance(item, dict):
            indexes[_identity_tuple(item, identity_fields)] = index
    for item in incoming:
        identity = _identity_tuple(item, identity_fields)
        if identity in indexes:
            existing_item = result[indexes[identity]]
            if isinstance(existing_item, dict) and isinstance(item, dict):
                conflict = _normalized_for_comparison(existing_item, identity_fields) != _normalized_for_comparison(item, identity_fields)
            else:
                conflict = existing_item != item
            if conflict:
                rendered = ", ".join(str(part) for part in identity)
                raise ValueError(
                    f"conflicting approved declarations for {key}[{rendered}]"
                )
        else:
            indexes[identity] = len(result)
            result.append(deepcopy(item))
    return result


def materialize(profile: dict[str, Any], intake: dict[str, Any], intake_sha256: str) -> dict[str, Any]:
    errors = validate_intake_data(intake, profile)
    if errors:
        raise ValueError("invalid convention intake: " + "; ".join(errors))
    if declares_profile_not_applicable(intake):
        raise ValueError(
            "profile is declared not applicable to this template; refusing to materialize "
            "a docmodel from a non-applicable convention profile"
        )
    records = {record["question_id"]: record for record in intake["records"]}
    declarations: dict[str, list[Any]] = {}
    materialized_ids: list[str] = []
    scopes: set[str] = set()
    # Profile order is canonical so record reordering cannot alter draft order or digest inputs.
    for question in profile["questions"]:
        record = records[question["id"]]
        if record["approval"] != "approved_to_draft":
            continue
        option = next(item for item in question["options"] if item["id"] == record["response"])
        for key, value in option["declaration"].items():
            declarations[key] = _merge_named_list(declarations.get(key, []), value, key)
        materialized_ids.append(record["question_id"])
        scopes.add(record["scope"])
    if not materialized_ids:
        raise ValueError("no approved_to_draft answers; refusing to create an empty convention")

    draft: dict[str, Any] = {
        "meta": {
            # The validator accepts either spelling of a canonically equivalent id, so
            # the draft records the canonical one: the template names the draft file and
            # the "refusing to overwrite" guard is itself an equality check.
            "template": identity_key(intake["template_id"]),
            "updated_at": intake["recorded_at"],
            "approved_by": None,
            "approval_state": "draft",
            "suppression_eligible": False,
            "source_profile": profile["profile_id"],
            "source_intake_sha256": intake_sha256,
            "target_snapshot": intake["target_snapshot"],
            "answer_scopes": sorted(scopes),
            "materialized_question_ids": materialized_ids,
        }
    }
    if "target_document" in intake:
        draft["meta"]["target_document"] = intake["target_document"]
    draft.update(declarations)
    return draft


def equivalent_siblings(output: Path) -> list[Path]:
    """Existing files whose name is the same identity as `output`'s, however spelled."""
    if output.exists():
        return [output]
    # No `except OSError` here on purpose: a directory we cannot read is a directory we
    # cannot clear, and "we found no draft" would be a guess, not an answer. The caller
    # reports the failure instead of writing.
    wanted = identity_key(output.name)
    return [path for path in output.parent.iterdir() if identity_key(path.name) == wanted]


def _canonical_output_path(path: Path) -> Path:
    absolute = path if path.is_absolute() else Path.cwd() / path
    if sys.platform == "darwin" and len(absolute.parts) > 1 and absolute.parts[1] in {"tmp", "var"}:
        absolute = Path("/private").joinpath(*absolute.parts[1:])
    return absolute


def _open_output_parent(path: Path) -> tuple[Path, int]:
    absolute = _canonical_output_path(path)
    directory_flag = getattr(os, "O_DIRECTORY", None)
    nofollow = getattr(os, "O_NOFOLLOW", None)
    if (
        not isinstance(directory_flag, int)
        or directory_flag == 0
        or not isinstance(nofollow, int)
        or nofollow == 0
    ):
        raise OSError("safe output creation requires O_DIRECTORY and O_NOFOLLOW")
    flags = os.O_RDONLY | directory_flag
    fd = os.open(absolute.anchor, flags)
    try:
        for part in absolute.parent.parts[1:]:
            nxt = os.open(part, flags | nofollow, dir_fd=fd)
            os.close(fd)
            fd = nxt
        return absolute, fd
    except BaseException:
        os.close(fd)
        raise


def _write_new(path: Path, raw: bytes) -> None:
    """Create *path* once through a descriptor-anchored, no-follow parent."""
    absolute, parent_fd = _open_output_parent(path)
    try:
        opened_parent = os.fstat(parent_fd)
        named_parent = os.stat(absolute.parent, follow_symlinks=False)
        if (
            not stat.S_ISDIR(named_parent.st_mode)
            or (opened_parent.st_dev, opened_parent.st_ino)
            != (named_parent.st_dev, named_parent.st_ino)
        ):
            raise OSError("output parent changed during safe create")
        flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW
        fd = os.open(absolute.name, flags, 0o600, dir_fd=parent_fd)
    finally:
        os.close(parent_fd)
    try:
        view = memoryview(raw)
        while view:
            written = os.write(fd, view)
            if written <= 0:
                raise OSError("short write while creating docmodel draft")
            view = view[written:]
        os.fsync(fd)
    finally:
        os.close(fd)


def materialize_paths(profile_path: Path, intake_path: Path) -> dict[str, Any]:
    profile = load_yaml(profile_path)
    intake = load_yaml(intake_path)
    digest = hashlib.sha256(intake_path.read_bytes()).hexdigest()
    return materialize(profile, intake, digest)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("intake", type=Path)
    parser.add_argument("--profile", type=Path, required=True)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args(argv)
    try:
        draft = materialize_paths(args.profile, args.intake)
    except (OSError, ValueError, yaml.YAMLError) as exc:
        print(f"FAIL: {exc}", file=sys.stderr)
        return 1
    output = args.output or args.intake.with_name(
        f"docmodel.{draft['meta']['template']}.draft.yaml"
    )
    # The guard is an equality check on a filename, so it has to compare identities too:
    # a draft written under an older, differently spelled name is still that draft.
    try:
        existing = next(iter(sorted(equivalent_siblings(output))), None)
    except OSError as exc:
        print(f"FAIL: could not inspect the output directory: {exc}", file=sys.stderr)
        return 1
    if existing is not None:
        print(f"FAIL: refusing to overwrite existing draft: {existing}", file=sys.stderr)
        return 1
    try:
        raw = yaml.safe_dump(draft, allow_unicode=True, sort_keys=False).encode("utf-8")
        _write_new(output, raw)
    except FileExistsError:
        print(f"FAIL: refusing to overwrite existing draft: {output}", file=sys.stderr)
        return 1
    except OSError as exc:
        print(f"FAIL: could not write draft: {exc}", file=sys.stderr)
        return 1
    print(f"OK: wrote non-authoritative docmodel draft: {output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
