#!/usr/bin/env python3
"""Validate legacy v1 and ledger-bound v2 review-gate done receipts."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import re
import sys
from typing import Any

import yaml

try:  # Package import in tests; sibling import when executed as a script.
    from .validate_review_intermediate import (
        DONE_FINDING_STATUSES,
        PUBLIC_COLLECTIONS,
        ROOT_KEY,
        load_yaml_text,
        record_digest,
        resolve_packet_file,
        validate_data as validate_intermediate_data,
    )
except ImportError:  # pragma: no cover - exercised by CLI dispatch
    from validate_review_intermediate import (
        DONE_FINDING_STATUSES,
        PUBLIC_COLLECTIONS,
        ROOT_KEY,
        load_yaml_text,
        record_digest,
        resolve_packet_file,
        validate_data as validate_intermediate_data,
    )


DONE_STATUSES = DONE_FINDING_STATUSES


def _frontmatter(path: Path) -> dict[str, Any]:
    text = path.read_text(encoding="utf-8")
    match = re.match(r"\A(?:\ufeff)?---[ \t]*\r?\n(.*?)\r?\n---[ \t]*(?:\r?\n|\Z)", text, re.S)
    if not match:
        raise ValueError("leading YAML frontmatter is required")
    data = load_yaml_text(match.group(1))
    if not isinstance(data, dict) or not isinstance(data.get("doc_review_result"), dict):
        raise ValueError("frontmatter must contain doc_review_result mapping")
    return data["doc_review_result"]


def _nonempty(value: Any) -> bool:
    return isinstance(value, str) and bool(value.strip())


def _validate_verifiers(receipt: dict[str, Any], errors: list[str]) -> None:
    snapshot = receipt.get("snapshot_id")
    verifiers = receipt.get("verifiers")
    if not isinstance(verifiers, list) or len(verifiers) != 3:
        errors.append("verifiers must contain exactly three independent done verifiers")
        return
    seen: set[str] = set()
    for index, verifier in enumerate(verifiers):
        prefix = f"verifiers[{index}]"
        if not isinstance(verifier, dict) or set(verifier) != {"verifier_id", "result", "snapshot_id", "evidence"}:
            errors.append(f"{prefix} must contain exactly verifier_id, result, snapshot_id, evidence")
            continue
        verifier_id = verifier.get("verifier_id")
        if not _nonempty(verifier_id):
            errors.append(f"{prefix}.verifier_id must be nonempty")
        elif verifier_id in seen:
            errors.append(f"duplicate verifier_id: {verifier_id}")
        else:
            seen.add(verifier_id)
        if verifier.get("result") != "pass":
            errors.append(f"{prefix}.result must be pass for done")
        if verifier.get("snapshot_id") != snapshot:
            errors.append(f"{prefix}.snapshot_id must match current snapshot_id")
        if not _nonempty(verifier.get("evidence")):
            errors.append(f"{prefix}.evidence must be nonempty")


def _validate_common(receipt: dict[str, Any], errors: list[str]) -> None:
    if receipt.get("route_id") != "review-gate":
        errors.append("route_id must be review-gate")
    for field in ("route_trace", "snapshot_id"):
        if not _nonempty(receipt.get(field)):
            errors.append(f"{field} must be a nonempty string")
    _validate_verifiers(receipt, errors)
    unassured = receipt.get("unassured_mode", False)
    if not isinstance(unassured, bool):
        errors.append("unassured_mode must be boolean")
    if unassured and not _nonempty(receipt.get("unassured_accepted_by")):
        errors.append("unassured mode requires nonempty unassured_accepted_by")


def _validate_v1(receipt: dict[str, Any]) -> list[str]:
    errors: list[str] = []
    required = {"schema_version", "route_id", "route_trace", "snapshot_id", "verifiers", "findings"}
    optional = {"unassured_mode", "unassured_accepted_by"}
    missing = required.difference(receipt)
    extra = set(receipt).difference(required | optional)
    if missing:
        errors.append(f"missing fields: {', '.join(sorted(missing))}")
    if extra:
        errors.append(f"unknown fields: {', '.join(sorted(extra))}")
    _validate_common(receipt, errors)
    findings = receipt.get("findings")
    if not isinstance(findings, list):
        errors.append("findings must be a list")
    else:
        seen: set[str] = set()
        for index, finding in enumerate(findings):
            prefix = f"findings[{index}]"
            if not isinstance(finding, dict) or set(finding) != {"id", "status"}:
                errors.append(f"{prefix} must contain exactly id and status")
                continue
            finding_id = finding.get("id")
            if not _nonempty(finding_id):
                errors.append(f"{prefix}.id must be nonempty")
            elif finding_id in seen:
                errors.append(f"duplicate finding id: {finding_id}")
            else:
                seen.add(finding_id)
            if finding.get("status") not in DONE_STATUSES:
                errors.append(f"{prefix}.status must be rejected or verified")
    return errors


def _resolve_ledger(
    packet_root: Path,
    ref: Any,
    errors: list[str],
) -> tuple[Path | None, dict[str, Any] | None]:
    if not isinstance(ref, dict) or set(ref) != {"path", "sha256", "snapshot_id"}:
        errors.append("classification_ledger_ref must contain exactly path, sha256, snapshot_id")
        return None, None
    ledger_path = resolve_packet_file(
        packet_root,
        ref.get("path"),
        "classification_ledger_ref.path",
        errors,
    )
    if ledger_path is None:
        return None, None
    try:
        payload = ledger_path.read_bytes()
        loaded = load_yaml_text(payload)
        if not isinstance(loaded, dict) or set(loaded) != {ROOT_KEY} or not isinstance(loaded.get(ROOT_KEY), dict):
            raise ValueError(f"YAML must contain exactly top-level {ROOT_KEY} mapping")
        ledger = loaded[ROOT_KEY]
    except (OSError, ValueError, yaml.YAMLError) as exc:
        errors.append(f"cannot load classification ledger: {exc}")
        return ledger_path, None
    actual_hash = hashlib.sha256(payload).hexdigest()
    if ref.get("sha256") != actual_hash:
        errors.append("classification_ledger_ref.sha256 does not match ledger bytes")
    return ledger_path, ledger


def _validate_packet_binding(
    receipt: dict[str, Any],
    expected: dict[str, Any],
    errors: list[str],
) -> None:
    binding = receipt.get("packet_binding")
    fields = {
        "run_id",
        "target_source",
        "target_snapshot",
        "prepared_payload_digest_sha256",
        "receipt_path",
    }
    if not isinstance(binding, dict) or set(binding) != fields:
        errors.append(
            "packet_binding must contain exactly run_id, target_source, target_snapshot, "
            "prepared_payload_digest_sha256, receipt_path"
        )
        return
    for field in sorted(fields):
        if binding.get(field) != expected.get(field):
            errors.append(f"packet_binding.{field} does not match the prepared packet")


def _validate_v2(
    path: Path,
    receipt: dict[str, Any],
    packet_root: Path,
    expected_packet_binding: dict[str, Any],
) -> list[str]:
    errors: list[str] = []
    required = {
        "schema_version", "route_id", "route_trace", "snapshot_id", "target", "verifiers",
        "classification_ledger_ref", "packet_binding", "findings", "questions", "drifts",
    }
    optional = {"unassured_mode", "unassured_accepted_by"}
    missing = required.difference(receipt)
    extra = set(receipt).difference(required | optional)
    if missing:
        errors.append(f"missing fields: {', '.join(sorted(missing))}")
    if extra:
        errors.append(f"unknown fields: {', '.join(sorted(extra))}")
    _validate_common(receipt, errors)
    _validate_packet_binding(receipt, expected_packet_binding, errors)
    binding = receipt.get("packet_binding")
    if isinstance(binding, dict):
        if receipt.get("snapshot_id") != binding.get("target_snapshot"):
            errors.append("receipt snapshot_id must match packet_binding.target_snapshot")
        if receipt.get("target") != binding.get("target_source"):
            errors.append("receipt target must match packet_binding.target_source")
    ref = receipt.get("classification_ledger_ref")
    _, ledger = _resolve_ledger(packet_root, ref, errors)
    if isinstance(ref, dict) and ref.get("snapshot_id") != receipt.get("snapshot_id"):
        errors.append("classification_ledger_ref.snapshot_id must match receipt snapshot_id")
    if ledger is None:
        return errors
    errors.extend(
        f"ledger: {error}"
        for error in validate_intermediate_data(
            ledger,
            require_closed=True,
            packet_root=packet_root,
        )
    )
    if ledger.get("snapshot_id") != receipt.get("snapshot_id"):
        errors.append("ledger snapshot_id must match receipt snapshot_id")
    if ledger.get("target") != receipt.get("target") or not _nonempty(receipt.get("target")):
        errors.append("ledger target must match nonempty receipt target")

    ledger_records: dict[str, tuple[str, dict[str, Any]]] = {}
    for category, collection_name in PUBLIC_COLLECTIONS.items():
        for record in ledger.get(collection_name, []):
            if isinstance(record, dict) and _nonempty(record.get("record_id")):
                ledger_records[record["record_id"]] = (category, record)

    final_ids: set[str] = set()
    for category, collection_name in (("finding", "findings"), ("question", "questions"), ("drift", "drifts")):
        collection = receipt.get(collection_name)
        if not isinstance(collection, list):
            errors.append(f"{collection_name} must be a list")
            continue
        for index, record in enumerate(collection):
            prefix = f"{collection_name}[{index}]"
            if not isinstance(record, dict):
                errors.append(f"{prefix} must be a mapping")
                continue
            record_id = record.get("record_id")
            if not _nonempty(record_id) or record_id in final_ids:
                errors.append(f"{prefix}.record_id must be unique and nonempty")
                continue
            final_ids.add(record_id)
            ledger_entry = ledger_records.get(record_id)
            if ledger_entry is None or ledger_entry[0] != category:
                errors.append(f"{prefix} is missing from matching ledger category")
                continue
            if set(record) != set(ledger_entry[1]):
                errors.append(f"{prefix} fields must exactly match the closed ledger record shape")
            if record.get("snapshot_id") != receipt.get("snapshot_id"):
                errors.append(f"{prefix}.snapshot_id must match receipt snapshot_id")
            if record.get("public_record_digest") != ledger_entry[1].get("public_record_digest"):
                errors.append(f"{prefix}.public_record_digest must match closed ledger")
            if record.get("public_record_digest") != record_digest(category, record):
                errors.append(f"{prefix} immutable payload does not match public_record_digest")
            if category == "finding" and record.get("status") not in DONE_STATUSES:
                errors.append(f"{prefix}.status must be rejected or verified for done")
            if category == "question" and record.get("status") != "resolved":
                errors.append(f"{prefix}.status must be resolved for done")
            if category == "question":
                verification = record.get("classification_verification")
                if not isinstance(verification, dict) or set(verification) != {"result", "verifier_id", "evidence"}:
                    errors.append(f"{prefix}.classification_verification has invalid shape")
                else:
                    if verification.get("result") not in {"pass", "kill"}:
                        errors.append(f"{prefix}.classification_verification.result must be pass or kill for done")
                    for field in ("verifier_id", "evidence"):
                        if not _nonempty(verification.get(field)):
                            errors.append(f"{prefix}.classification_verification.{field} must be nonempty")
            if category == "drift" and {"severity", "status", "blocking"}.intersection(record):
                errors.append(f"{prefix} drift cannot carry finding or blocking fields")

    expected_public = {
        record_id for record_id, (category, _) in ledger_records.items()
        if category in {"finding", "question", "drift"}
    }
    missing_public = expected_public.difference(final_ids)
    extra_public = final_ids.difference(expected_public)
    if missing_public:
        errors.append(f"final receipt omits ledger public records: {', '.join(sorted(missing_public))}")
    if extra_public:
        errors.append(f"final receipt contains records absent from ledger: {', '.join(sorted(extra_public))}")
    return errors


def validate_legacy(path: Path) -> list[str]:
    try:
        receipt = _frontmatter(path)
    except (OSError, ValueError, yaml.YAMLError) as exc:
        return [str(exc)]
    version = receipt.get("schema_version")
    if type(version) is int and version == 1:
        return _validate_v1(receipt)
    if type(version) is int and version == 2:
        return ["schema-v2 validation requires packet root and exact packet binding"]
    return ["schema_version must be 1 or 2"]


def validate(
    packet_root: Path,
    receipt_relative_path: str,
    expected_packet_binding: dict[str, Any],
) -> list[str]:
    path_errors: list[str] = []
    receipt_path = resolve_packet_file(
        packet_root,
        receipt_relative_path,
        "receipt_path",
        path_errors,
    )
    if receipt_path is None:
        return path_errors
    try:
        receipt = _frontmatter(receipt_path)
    except (OSError, ValueError, yaml.YAMLError) as exc:
        return [str(exc)]
    version = receipt.get("schema_version")
    if type(version) is int and version == 1:
        return _validate_v1(receipt)
    if type(version) is int and version == 2:
        return _validate_v2(
            receipt_path,
            receipt,
            packet_root,
            expected_packet_binding,
        )
    return ["schema_version must be 1 or 2"]


def packet_binding_from_prepared(packet_root: Path, receipt_relative_path: str) -> tuple[dict[str, Any] | None, list[str]]:
    """Read binding metadata after the caller has validated prepared-packet integrity."""
    errors: list[str] = []
    run_path = resolve_packet_file(packet_root, "RUN.yaml", "RUN.yaml", errors)
    complete_path = resolve_packet_file(packet_root, "COMPLETE.json", "COMPLETE.json", errors)
    if run_path is None or complete_path is None:
        return None, errors
    try:
        run = load_yaml_text(run_path.read_text(encoding="utf-8"))
        complete = json.loads(complete_path.read_text(encoding="utf-8"), object_pairs_hook=_reject_duplicate_json)
    except (OSError, UnicodeError, ValueError, yaml.YAMLError) as exc:
        return None, [f"cannot load prepared packet metadata: {exc}"]
    return packet_binding_from_metadata(run, complete, receipt_relative_path)


def packet_binding_from_metadata(
    run: Any,
    complete: Any,
    receipt_relative_path: str,
) -> tuple[dict[str, Any] | None, list[str]]:
    """Build the exact v2 binding from already-validated packet metadata."""
    target = run.get("target") if isinstance(run, dict) else None
    if not isinstance(target, dict):
        return None, ["RUN.yaml target must be a mapping"]
    run_id = run.get("run_id")
    target_source = target.get("source")
    target_sha = target.get("sha256")
    digest = complete.get("payload_digest_sha256") if isinstance(complete, dict) else None
    if not all(_nonempty(value) for value in (run_id, target_source, target_sha, digest)):
        return None, ["prepared packet metadata is missing binding fields"]
    return {
        "run_id": run_id,
        "target_source": target_source,
        "target_snapshot": f"sha256:{target_sha}",
        "prepared_payload_digest_sha256": digest,
        "receipt_path": receipt_relative_path,
    }, []


def _reject_duplicate_json(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError(f"duplicate JSON key {key!r}")
        result[key] = value
    return result


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("packet_root", type=Path)
    parser.add_argument("receipt", help="normalized packet-relative receipt path")
    args = parser.parse_args(argv)
    try:
        try:
            from .runner import GateError, _validate_prepared_packet
        except ImportError:  # pragma: no cover - exercised by CLI dispatch
            from runner import GateError, _validate_prepared_packet
    except ImportError as exc:
        binding, errors = None, [f"prepared packet validation failed: {exc}"]
        run_root = args.packet_root
    else:
        try:
            run_root, run, complete = _validate_prepared_packet(args.packet_root)
        except (GateError, OSError, ValueError, yaml.YAMLError) as exc:
            binding, errors = None, [f"prepared packet validation failed: {exc}"]
            run_root = args.packet_root
        else:
            binding, errors = packet_binding_from_metadata(run, complete, args.receipt)
    if binding is not None:
        errors.extend(validate(run_root, args.receipt, binding))
    if errors:
        for error in errors:
            print(f"FAIL: {error}", file=sys.stderr)
        return 1
    print("OK: review-gate done receipt is current and complete")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
