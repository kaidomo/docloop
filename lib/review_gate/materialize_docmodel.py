#!/usr/bin/env python3
"""Materialize approved-to-draft convention answers into a docmodel draft."""

from __future__ import annotations

import argparse
from copy import deepcopy
import hashlib
import os
from pathlib import Path
import sys
from typing import Any

import yaml

from validate_convention_intake import validate_data as validate_intake_data
from validate_convention_profile import load_yaml


IDENTITY_FIELDS = {
    "sections": ("id",),
    "precedence": ("wins",),
    "correspondence": ("from", "to"),
    "ownership": ("detail",),
}


def _merge_named_list(existing: list[Any], incoming: list[Any], key: str) -> list[Any]:
    identity_fields = IDENTITY_FIELDS[key]
    result = deepcopy(existing)
    indexes: dict[tuple[Any, ...], int] = {}
    for index, item in enumerate(result):
        if isinstance(item, dict):
            indexes[tuple(item.get(field) for field in identity_fields)] = index
    for item in incoming:
        identity = tuple(item.get(field) for field in identity_fields)
        if identity in indexes:
            if result[indexes[identity]] != item:
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
            "template": intake["template_id"],
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


def materialize_paths(profile_path: Path, intake_path: Path) -> dict[str, Any]:
    profile = load_yaml(profile_path)
    intake = load_yaml(intake_path)
    digest = hashlib.sha256(intake_path.read_bytes()).hexdigest()
    return materialize(profile, intake, digest)


def _write_new(path: Path, raw: bytes) -> None:
    """Create *path* exactly once without following an existing final symlink."""
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0)
    fd = os.open(path, flags, 0o600)
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
