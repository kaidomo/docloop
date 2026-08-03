#!/usr/bin/env python3
"""Validate a review-gate intermediate source-to-atom classification ledger."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import stat
import sys
from typing import Any

import yaml

try:  # Package import in tests; sibling import when executed as a script.
    from .validate_decisions import validate as validate_decisions
except ImportError:  # pragma: no cover - exercised by CLI dispatch
    from validate_decisions import validate as validate_decisions


ROOT_KEY = "review_intermediate"


class DuplicateKeyError(yaml.YAMLError):
    """Raised when YAML would otherwise silently overwrite a mapping key."""


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


def load_yaml_text(text: str | bytes) -> Any:
    if isinstance(text, bytes):
        text = text.decode("utf-8")
    return yaml.load(text, Loader=StrictLoader)
OUTCOMES = {"finding", "question", "drift", "suppressed", "nonissue"}
FINDING_STATUSES = {"discovered", "accepted", "rejected", "planned", "applied", "verified"}
DONE_FINDING_STATUSES = {"rejected", "verified"}
QUESTION_STATUSES = {"open", "resolved"}
VERIFY_RESULTS = {"pass", "kill", "unresolved"}
CO_REFERENCE_RESULTS = {"proven", "unknown", "not_coreferential"}
SEMANTIC_VALUE_RESULTS = {"equal", "different", "unknown", "not_applicable"}
PRESENTATION_RESULTS = {"not_violated", "binding_violated", "intentional_variant", "unknown", "not_applicable"}
PUBLIC_COLLECTIONS = {
    "finding": "findings",
    "question": "questions",
    "drift": "drifts",
    "suppressed": "suppressed",
    "nonissue": "nonissues",
}
RECORD_REQUIRED = {
    "finding": {
        "record_id", "finding_id", "candidate_atom_refs", "source_candidate_refs",
        "snapshot_id", "evidence_anchors", "severity", "judgment_provenance", "status",
        "public_record_digest",
    },
    "question": {
        "record_id", "status", "convention_slot", "dependent_atom_refs",
        "resolution_derived_atom_refs", "snapshot_id", "evidence_anchors",
        "classification_verification", "public_record_digest",
    },
    "drift": {
        "record_id", "candidate_atom_refs", "source_candidate_refs", "snapshot_id",
        "evidence_anchors", "detail", "variants", "co_reference_basis", "public_record_digest",
    },
    "suppressed": {
        "record_id", "candidate_atom_refs", "source_candidate_refs", "snapshot_id",
        "evidence_anchors", "rationale", "authority_ref", "public_record_digest",
    },
    "nonissue": {
        "record_id", "candidate_atom_refs", "source_candidate_refs", "snapshot_id",
        "evidence_anchors", "rationale", "public_record_digest",
    },
}
RECORD_OPTIONAL = {
    "finding": set(),
    "question": {"authority", "scope", "source"},
    "drift": {"comparison_ref"},
    "suppressed": set(),
    "nonissue": set(),
}


def _load(path: Path) -> dict[str, Any]:
    data = load_yaml_text(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict) or not isinstance(data.get(ROOT_KEY), dict):
        raise ValueError(f"YAML must contain top-level {ROOT_KEY} mapping")
    if set(data) != {ROOT_KEY}:
        raise ValueError(f"top-level YAML must contain exactly {ROOT_KEY}")
    return data[ROOT_KEY]


def _nonempty(value: Any) -> bool:
    return isinstance(value, str) and bool(value.strip())


def _string_list(value: Any, *, nonempty: bool = True) -> bool:
    return (
        isinstance(value, list)
        and (bool(value) or not nonempty)
        and all(_nonempty(item) for item in value)
        and len(value) == len(set(value))
    )


def _canonical(value: Any) -> bytes:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")


def _normalize_relative_path(value: Any, label: str, errors: list[str]) -> PurePosixPath | None:
    """Accept one already-normalized packet-relative POSIX path."""
    if not _nonempty(value) or "\\" in value:
        errors.append(f"{label} must be a normalized packet-relative POSIX path")
        return None
    path = PurePosixPath(value)
    if path.is_absolute() or value != path.as_posix() or any(part in {"", ".", ".."} for part in path.parts):
        errors.append(f"{label} must be a normalized packet-relative POSIX path")
        return None
    return path


def resolve_packet_file(
    packet_root: Path,
    value: Any,
    label: str,
    errors: list[str],
) -> Path | None:
    """Resolve a regular, non-symlink file below the selected packet root."""
    relative = _normalize_relative_path(value, label, errors)
    if relative is None:
        return None
    supplied_root = packet_root.absolute()
    try:
        root_mode = os.lstat(supplied_root).st_mode
    except OSError as exc:
        errors.append(f"{label} cannot open packet root: {exc}")
        return None
    if stat.S_ISLNK(root_mode) or not stat.S_ISDIR(root_mode):
        errors.append(f"{label} packet root must be a real directory")
        return None
    root = supplied_root.resolve()
    candidate = root.joinpath(*relative.parts)
    try:
        current = root
        for part in relative.parts:
            current = current / part
            mode = os.lstat(current).st_mode
            if stat.S_ISLNK(mode):
                errors.append(f"{label} cannot traverse a symlink")
                return None
        if not stat.S_ISREG(os.lstat(candidate).st_mode):
            errors.append(f"{label} must reference a regular file")
            return None
        candidate.resolve().relative_to(root)
    except (OSError, ValueError) as exc:
        errors.append(f"{label} cannot load packet file: {exc}")
        return None
    return candidate


def _expected_outcome(basis: Any) -> str | None:
    if not isinstance(basis, dict) or set(basis) != {
        "co_reference", "semantic_values", "presentation_rule", "evidence"
    }:
        return None
    co_reference = basis.get("co_reference")
    semantic_values = basis.get("semantic_values")
    presentation_rule = basis.get("presentation_rule")
    if co_reference == "unknown":
        return "question"
    if co_reference == "not_coreferential":
        return "nonissue"
    if co_reference != "proven":
        return None
    if semantic_values == "unknown":
        return "question"
    if semantic_values == "different":
        return "finding" if presentation_rule == "not_applicable" else None
    if semantic_values != "equal":
        return None
    return {
        "unknown": "question",
        "binding_violated": "finding",
        "not_violated": "drift",
        "intentional_variant": "nonissue",
    }.get(presentation_rule)


def _validate_authority_ref(
    value: Any,
    label: str,
    packet_root: Path | None,
    errors: list[str],
) -> None:
    if not isinstance(value, dict) or set(value) not in (
        {"kind", "path", "sha256"},
        {"kind", "path", "sha256", "decision_id"},
    ):
        errors.append(f"{label} must be a hash-bound approved_docmodel or decision_registry reference")
        return
    kind = value.get("kind")
    expected_fields = (
        {"kind", "path", "sha256"}
        if kind == "approved_docmodel"
        else {"kind", "path", "sha256", "decision_id"}
        if kind == "decision_registry"
        else set()
    )
    if not expected_fields or set(value) != expected_fields:
        errors.append(f"{label}.kind must be approved_docmodel or decision_registry with its exact fields")
        return
    if not isinstance(value.get("sha256"), str) or not re.fullmatch(r"[0-9a-f]{64}", value["sha256"]):
        errors.append(f"{label}.sha256 must be 64 lowercase hex characters")
        return
    if packet_root is None:
        errors.append(f"{label} cannot be verified without a packet root")
        return
    authority_path = resolve_packet_file(packet_root, value.get("path"), f"{label}.path", errors)
    if authority_path is None:
        return
    try:
        payload = authority_path.read_bytes()
    except OSError as exc:
        errors.append(f"{label} cannot load authority file: {exc}")
        return
    if hashlib.sha256(payload).hexdigest() != value["sha256"]:
        errors.append(f"{label}.sha256 does not match authority file bytes")
        return
    if kind == "approved_docmodel":
        if ".draft." in authority_path.name:
            errors.append(f"{label} cannot reference a draft docmodel")
            return
        try:
            docmodel = load_yaml_text(payload)
        except yaml.YAMLError as exc:
            errors.append(f"{label} docmodel YAML is invalid: {exc}")
            return
        meta = docmodel.get("meta") if isinstance(docmodel, dict) else None
        if not isinstance(meta, dict) or meta.get("approval_state") != "approved":
            errors.append(f"{label} docmodel must have meta.approval_state=approved")
        if not isinstance(meta, dict) or meta.get("suppression_eligible") is not True:
            errors.append(f"{label} docmodel must have meta.suppression_eligible=true")
        if not isinstance(meta, dict) or not _nonempty(meta.get("approved_by")):
            errors.append(f"{label} docmodel must have nonempty meta.approved_by")
    else:
        decision_id = value.get("decision_id")
        if not _nonempty(decision_id):
            errors.append(f"{label}.decision_id must be nonempty")
            return
        try:
            registry = load_yaml_text(payload)
        except yaml.YAMLError as exc:
            errors.append(f"{label} decision registry YAML is invalid: {exc}")
            return
        nested_refs: list[str] = []
        meta = registry.get("meta") if isinstance(registry, dict) else None
        if isinstance(meta, dict) and _nonempty(meta.get("source_ref")):
            nested_refs.append(meta["source_ref"])
        for item in registry.get("decisions", []) if isinstance(registry, dict) else []:
            if isinstance(item, dict) and _nonempty(item.get("source_ref")):
                nested_refs.append(item["source_ref"])
        for index, nested_ref in enumerate(nested_refs):
            nested_errors: list[str] = []
            if resolve_packet_file(
                packet_root,
                nested_ref,
                f"{label}.nested_source_ref[{index}]",
                nested_errors,
            ) is None:
                errors.extend(nested_errors)
        if errors:
            return
        decision_errors, _, _ = validate_decisions(authority_path)
        if decision_errors:
            errors.append(f"{label} decision registry is not suppression-eligible: {'; '.join(decision_errors)}")
            return
        data = load_yaml_text(payload)
        match = next(
            (item for item in data.get("decisions", []) if isinstance(item, dict) and item.get("id") == decision_id),
            None,
        )
        if match is None or match.get("status") not in {"확정", "재론금지"} or match.get("superseded_by"):
            errors.append(f"{label}.decision_id must identify a current confirmed decision")


def immutable_projection(category: str, record: dict[str, Any]) -> dict[str, Any]:
    """Return the fields whose post-closure mutation must invalidate a receipt."""
    fields = {
        "finding": (
            "record_id", "finding_id", "candidate_atom_refs", "source_candidate_refs",
            "snapshot_id", "evidence_anchors", "severity", "judgment_provenance",
        ),
        "question": (
            "record_id", "convention_slot", "dependent_atom_refs",
            "resolution_derived_atom_refs", "authority", "scope", "source",
            "snapshot_id", "evidence_anchors",
        ),
        "drift": (
            "record_id", "candidate_atom_refs", "source_candidate_refs", "snapshot_id",
            "evidence_anchors", "detail", "variants", "co_reference_basis", "comparison_ref",
        ),
        "suppressed": (
            "record_id", "candidate_atom_refs", "source_candidate_refs", "snapshot_id",
            "evidence_anchors", "rationale", "authority_ref",
        ),
        "nonissue": (
            "record_id", "candidate_atom_refs", "source_candidate_refs", "snapshot_id",
            "evidence_anchors", "rationale",
        ),
    }[category]
    projection = {"category": category}
    for field in fields:
        if field in record:
            projection[field] = record[field]
    if category == "question":
        verification = record.get("classification_verification")
        if isinstance(verification, dict) and "result" in verification:
            # The terminal verdict is auditable outcome state. Verifier identity and
            # evidence location remain mutable logs, but kill/pass may not be rewritten.
            projection["classification_verification_result"] = verification["result"]
    return projection


def record_digest(category: str, record: dict[str, Any]) -> str:
    return "sha256:" + hashlib.sha256(_canonical(immutable_projection(category, record))).hexdigest()


def _validate_common_record(
    category: str,
    record: Any,
    index: int,
    snapshot_id: Any,
    errors: list[str],
) -> None:
    prefix = f"{PUBLIC_COLLECTIONS[category]}[{index}]"
    if not isinstance(record, dict):
        errors.append(f"{prefix} must be a mapping")
        return
    if not _nonempty(record.get("record_id")):
        errors.append(f"{prefix}.record_id must be nonempty")
    if record.get("snapshot_id") != snapshot_id:
        errors.append(f"{prefix}.snapshot_id must match review snapshot_id")
    if not _string_list(record.get("evidence_anchors")):
        errors.append(f"{prefix}.evidence_anchors must be a unique nonempty string list")
    digest = record.get("public_record_digest")
    if not isinstance(digest, str) or not re.fullmatch(r"sha256:[0-9a-f]{64}", digest):
        errors.append(f"{prefix}.public_record_digest must be sha256:<64 lowercase hex>")
    elif digest != record_digest(category, record):
        errors.append(f"{prefix}.public_record_digest does not match immutable projection")


def _validate_record(
    category: str,
    record: Any,
    index: int,
    snapshot_id: Any,
    atom_ids: set[str],
    source_ids: set[str],
    packet_root: Path | None,
    errors: list[str],
) -> None:
    _validate_common_record(category, record, index, snapshot_id, errors)
    if not isinstance(record, dict):
        return
    prefix = f"{PUBLIC_COLLECTIONS[category]}[{index}]"
    missing = RECORD_REQUIRED[category].difference(record)
    extra = set(record).difference(RECORD_REQUIRED[category] | RECORD_OPTIONAL[category])
    if missing:
        errors.append(f"{prefix} missing fields: {', '.join(sorted(missing))}")
    if extra:
        errors.append(f"{prefix} unknown fields: {', '.join(sorted(extra))}")
    atom_field = "dependent_atom_refs" if category == "question" else "candidate_atom_refs"
    refs = record.get(atom_field)
    if not _string_list(refs) or not set(refs).issubset(atom_ids):
        errors.append(f"{prefix}.{atom_field} must reference existing candidate atoms")
    if category != "question":
        source_refs = record.get("source_candidate_refs")
        if not _string_list(source_refs) or not set(source_refs).issubset(source_ids):
            errors.append(f"{prefix}.source_candidate_refs must reference source inventory")

    if category == "finding":
        if not _nonempty(record.get("finding_id")):
            errors.append(f"{prefix}.finding_id must be nonempty")
        if record.get("severity") not in {"P1", "P2", "P3"}:
            errors.append(f"{prefix}.severity must be P1, P2, or P3")
        if record.get("status") not in FINDING_STATUSES:
            errors.append(f"{prefix}.status must be a legal finding lifecycle state; unresolved is verifier-only")
        if not _nonempty(record.get("judgment_provenance")):
            errors.append(f"{prefix}.judgment_provenance must be nonempty")
    elif category == "question":
        if record.get("status") not in QUESTION_STATUSES:
            errors.append(f"{prefix}.status must be open or resolved")
        if not _nonempty(record.get("convention_slot")):
            errors.append(f"{prefix}.convention_slot must be nonempty")
        verification = record.get("classification_verification")
        if not isinstance(verification, dict) or set(verification) != {"result", "verifier_id", "evidence"}:
            errors.append(f"{prefix}.classification_verification has invalid shape")
        elif verification.get("result") not in VERIFY_RESULTS:
            errors.append(f"{prefix}.classification_verification.result is invalid")
        derived = record.get("resolution_derived_atom_refs", [])
        if not _string_list(derived, nonempty=False) or not set(derived).issubset(atom_ids):
            errors.append(f"{prefix}.resolution_derived_atom_refs must reference candidate atoms")
        if record.get("status") == "resolved":
            if not _nonempty(record.get("authority")):
                errors.append(f"{prefix}.authority is required when resolved")
            if record.get("scope") not in {"document", "template"}:
                errors.append(f"{prefix}.scope must be document or template when resolved")
            _validate_authority_ref(record.get("source"), f"{prefix}.source", packet_root, errors)
            if not derived:
                errors.append(f"{prefix}.resolution_derived_atom_refs must be nonempty when resolved")
            if verification and verification.get("result") == "unresolved":
                errors.append(f"{prefix} resolved question cannot retain unresolved verification")
        else:
            if derived:
                errors.append(f"{prefix} open question cannot have resolution-derived atoms")
    elif category == "drift":
        forbidden = {"severity", "status", "blocking"}.intersection(record)
        if forbidden:
            errors.append(f"{prefix} drift cannot contain finding/blocking fields: {', '.join(sorted(forbidden))}")
        if not _nonempty(record.get("detail")):
            errors.append(f"{prefix}.detail must be nonempty")
        if not _nonempty(record.get("co_reference_basis")):
            errors.append(f"{prefix}.co_reference_basis must prove co-reference")
        variants = record.get("variants")
        notations: list[str] = []
        if not isinstance(variants, list) or len(variants) < 2:
            errors.append(f"{prefix}.variants must contain at least two variants")
        else:
            for variant_index, variant in enumerate(variants):
                if not isinstance(variant, dict) or set(variant) != {"notation", "anchors"}:
                    errors.append(f"{prefix}.variants[{variant_index}] has invalid shape")
                    continue
                if not _nonempty(variant.get("notation")) or not _string_list(variant.get("anchors")):
                    errors.append(f"{prefix}.variants[{variant_index}] requires notation and anchors")
                else:
                    notations.append(variant["notation"].strip())
                    if not set(variant["anchors"]).issubset(set(record.get("evidence_anchors", []))):
                        errors.append(f"{prefix}.variants[{variant_index}].anchors must be in drift evidence_anchors")
            if len(set(notations)) != len(notations):
                errors.append(f"{prefix}.variants must use distinct notations")
    else:
        if not _nonempty(record.get("rationale")):
            errors.append(f"{prefix}.rationale must be nonempty")
        if category == "suppressed" and not isinstance(record.get("authority_ref"), dict):
            errors.append(f"{prefix}.authority_ref must be present")
        if category == "suppressed":
            _validate_authority_ref(record.get("authority_ref"), f"{prefix}.authority_ref", packet_root, errors)


def validate_data(
    envelope: dict[str, Any],
    *,
    require_closed: bool = False,
    packet_root: Path | None = None,
) -> list[str]:
    errors: list[str] = []
    required = {
        "schema_version", "snapshot_id", "target", "state", "source_candidate_inventory",
        "candidate_atoms", "findings", "questions", "drifts", "suppressed", "nonissues",
        "classification_ledger",
    }
    missing = required.difference(envelope)
    extra = set(envelope).difference(required)
    if missing:
        errors.append(f"missing fields: {', '.join(sorted(missing))}")
    if extra:
        errors.append(f"unknown fields: {', '.join(sorted(extra))}")
    if type(envelope.get("schema_version")) is not int or envelope.get("schema_version") != 1:
        errors.append("schema_version must be 1")
    snapshot_id = envelope.get("snapshot_id")
    if not _nonempty(snapshot_id):
        errors.append("snapshot_id must be nonempty")
    if not _nonempty(envelope.get("target")):
        errors.append("target must be nonempty")
    state = envelope.get("state")
    if state not in {"open", "closed"}:
        errors.append("state must be open or closed")
    if require_closed and state != "closed":
        errors.append("final receipt requires a closed intermediate ledger")

    inventory = envelope.get("source_candidate_inventory")
    source_ids: set[str] = set()
    source_rows: dict[str, dict[str, Any]] = {}
    if not isinstance(inventory, list):
        errors.append("source_candidate_inventory must be a list")
        inventory = []
    for index, source in enumerate(inventory):
        prefix = f"source_candidate_inventory[{index}]"
        if not isinstance(source, dict) or set(source) != {"source_candidate_id", "lens", "statement", "evidence_anchors"}:
            errors.append(f"{prefix} has invalid shape")
            continue
        source_id = source.get("source_candidate_id")
        if not _nonempty(source_id) or source_id in source_ids:
            errors.append(f"{prefix}.source_candidate_id must be unique and nonempty")
        else:
            source_ids.add(source_id)
            source_rows[source_id] = source
        if not _nonempty(source.get("lens")) or not _nonempty(source.get("statement")):
            errors.append(f"{prefix} lens and statement must be nonempty")
        if not _string_list(source.get("evidence_anchors")):
            errors.append(f"{prefix}.evidence_anchors must be nonempty")

    atoms = envelope.get("candidate_atoms")
    atom_ids: set[str] = set()
    atom_rows: dict[str, dict[str, Any]] = {}
    covered_sources: set[str] = set()
    if not isinstance(atoms, list):
        errors.append("candidate_atoms must be a list")
        atoms = []
    for index, atom in enumerate(atoms):
        prefix = f"candidate_atoms[{index}]"
        allowed = {
            "candidate_atom_id", "source_candidate_refs", "statement", "evidence_anchors",
            "classification_basis", "derived_from_question_atom_refs",
        }
        if not isinstance(atom, dict) or not set(atom).issubset(allowed) or not {"candidate_atom_id", "source_candidate_refs", "statement", "evidence_anchors", "classification_basis"}.issubset(atom):
            errors.append(f"{prefix} has invalid shape")
            continue
        atom_id = atom.get("candidate_atom_id")
        if not _nonempty(atom_id) or atom_id in atom_ids:
            errors.append(f"{prefix}.candidate_atom_id must be unique and nonempty")
        else:
            atom_ids.add(atom_id)
            atom_rows[atom_id] = atom
        source_refs = atom.get("source_candidate_refs")
        if not _string_list(source_refs) or not set(source_refs).issubset(source_ids):
            errors.append(f"{prefix}.source_candidate_refs must reference source inventory")
        else:
            covered_sources.update(source_refs)
        if not _nonempty(atom.get("statement")) or not _string_list(atom.get("evidence_anchors")):
            errors.append(f"{prefix} statement and evidence_anchors are required")
        elif _string_list(source_refs) and set(source_refs).issubset(source_ids):
            source_anchor_union = {
                anchor
                for source_ref in source_refs
                for anchor in source_rows[source_ref].get("evidence_anchors", [])
            }
            if not set(atom["evidence_anchors"]).issubset(source_anchor_union):
                errors.append(f"{prefix}.evidence_anchors must come from referenced source candidates")
        lineage = atom.get("derived_from_question_atom_refs", [])
        if not _string_list(lineage, nonempty=False):
            errors.append(f"{prefix}.derived_from_question_atom_refs must be a unique string list")
        basis = atom.get("classification_basis")
        expected = _expected_outcome(basis)
        if expected is None:
            errors.append(f"{prefix}.classification_basis is incomplete or semantically inconsistent")
        elif not _nonempty(basis.get("evidence")):
            errors.append(f"{prefix}.classification_basis.evidence must be nonempty")
    missing_sources = source_ids.difference(covered_sources)
    if missing_sources:
        errors.append(f"source candidates without atoms: {', '.join(sorted(missing_sources))}")
    for source_id, source in source_rows.items():
        atom_anchor_union = {
            anchor
            for atom in atom_rows.values()
            if source_id in atom.get("source_candidate_refs", [])
            for anchor in atom.get("evidence_anchors", [])
        }
        missing_anchors = set(source.get("evidence_anchors", [])).difference(atom_anchor_union)
        if missing_anchors:
            errors.append(
                f"source candidate {source_id} anchors omitted during atomization: "
                f"{', '.join(sorted(missing_anchors))}"
            )

    records: dict[str, tuple[str, dict[str, Any]]] = {}
    finding_ids: set[str] = set()
    for category, collection_name in PUBLIC_COLLECTIONS.items():
        collection = envelope.get(collection_name)
        if not isinstance(collection, list):
            errors.append(f"{collection_name} must be a list")
            continue
        for index, record in enumerate(collection):
            _validate_record(category, record, index, snapshot_id, atom_ids, source_ids, packet_root, errors)
            if not isinstance(record, dict) or not _nonempty(record.get("record_id")):
                continue
            record_id = record["record_id"]
            if record_id in records:
                errors.append(f"duplicate record_id across categories: {record_id}")
            else:
                records[record_id] = (category, record)
            if category == "finding" and _nonempty(record.get("finding_id")):
                if record["finding_id"] in finding_ids:
                    errors.append(f"duplicate canonical finding_id: {record['finding_id']}")
                finding_ids.add(record["finding_id"])
            if category != "question":
                atom_refs = record.get("candidate_atom_refs", [])
                expected_sources = {
                    source_ref
                    for atom_id in atom_refs
                    for source_ref in atom_rows.get(atom_id, {}).get("source_candidate_refs", [])
                }
                if set(record.get("source_candidate_refs", [])) != expected_sources:
                    errors.append(
                        f"{collection_name}[{index}].source_candidate_refs must equal the union of its atoms' sources"
                    )
            atom_field = "dependent_atom_refs" if category == "question" else "candidate_atom_refs"
            expected_record_anchors = {
                anchor
                for atom_id in record.get(atom_field, [])
                for anchor in atom_rows.get(atom_id, {}).get("evidence_anchors", [])
            }
            if set(record.get("evidence_anchors", [])) != expected_record_anchors:
                errors.append(
                    f"{collection_name}[{index}].evidence_anchors must equal the union of its atoms' anchors"
                )

    ledger = envelope.get("classification_ledger")
    classified: set[str] = set()
    ledger_outcomes: dict[str, str] = {}
    target_atoms: dict[str, set[str]] = {}
    if not isinstance(ledger, list):
        errors.append("classification_ledger must be a list")
        ledger = []
    for index, row in enumerate(ledger):
        prefix = f"classification_ledger[{index}]"
        if not isinstance(row, dict) or set(row) != {"candidate_atom_id", "outcome", "target_record_id", "evidence_anchors"}:
            errors.append(f"{prefix} has invalid shape")
            continue
        atom_id = row.get("candidate_atom_id")
        outcome = row.get("outcome")
        target_id = row.get("target_record_id")
        if atom_id not in atom_ids:
            errors.append(f"{prefix}.candidate_atom_id does not exist")
        elif atom_id in classified:
            errors.append(f"candidate atom has multiple terminal classifications: {atom_id}")
        else:
            classified.add(atom_id)
            ledger_outcomes[atom_id] = outcome
        if outcome not in OUTCOMES:
            errors.append(f"{prefix}.outcome is invalid")
        elif atom_id in atom_rows:
            expected_outcome = _expected_outcome(atom_rows[atom_id].get("classification_basis"))
            if outcome == "suppressed":
                if expected_outcome != "finding":
                    errors.append(f"{prefix} suppressed atom must otherwise classify as finding")
            elif expected_outcome != outcome:
                errors.append(
                    f"{prefix}.outcome {outcome} contradicts atom classification_basis ({expected_outcome})"
                )
        target = records.get(target_id)
        if target is None:
            errors.append(f"{prefix}.target_record_id does not exist")
        elif target[0] != outcome:
            errors.append(f"{prefix} outcome does not match target record category")
        else:
            atom_field = "dependent_atom_refs" if outcome == "question" else "candidate_atom_refs"
            if atom_id not in target[1].get(atom_field, []):
                errors.append(f"{prefix} atom is not referenced by target record")
            target_atoms.setdefault(target_id, set()).add(atom_id)
        if not _string_list(row.get("evidence_anchors")):
            errors.append(f"{prefix}.evidence_anchors must be atom-level terminal anchors")
        elif atom_id in atom_rows and set(row["evidence_anchors"]) != set(atom_rows[atom_id].get("evidence_anchors", [])):
            errors.append(f"{prefix}.evidence_anchors must exactly preserve the candidate atom anchors")
        if target is not None and not set(row.get("evidence_anchors", [])).issubset(set(target[1].get("evidence_anchors", []))):
            errors.append(f"{prefix}.evidence_anchors must be present in the terminal target record")
    unclassified = atom_ids.difference(classified)
    if unclassified:
        errors.append(f"candidate atoms without terminal classification: {', '.join(sorted(unclassified))}")
    for record_id, (category, record) in records.items():
        atom_field = "dependent_atom_refs" if category == "question" else "candidate_atom_refs"
        if set(record.get(atom_field, [])) != target_atoms.get(record_id, set()):
            errors.append(f"record {record_id}.{atom_field} must exactly match ledger rows targeting it")

    question_by_dependent: dict[str, dict[str, Any]] = {}
    for category, record in records.values():
        if category == "question":
            for atom_id in record.get("dependent_atom_refs", []):
                question_by_dependent[atom_id] = record
    for atom_id, atom in atom_rows.items():
        lineage = atom.get("derived_from_question_atom_refs", [])
        for parent_id in lineage:
            parent = question_by_dependent.get(parent_id)
            if parent is None:
                errors.append(f"candidate atom {atom_id} has invalid question lineage parent {parent_id}")
            elif atom_id not in parent.get("resolution_derived_atom_refs", []):
                errors.append(f"candidate atom {atom_id} is absent from its question resolution lineage")
        if lineage and ledger_outcomes.get(atom_id) not in {"finding", "drift", "nonissue"}:
            errors.append(f"resolution-derived atom {atom_id} must end as finding, drift, or nonissue")
    for category, record in records.values():
        if category != "question" or record.get("status") != "resolved":
            continue
        if set(record.get("dependent_atom_refs", [])).intersection(record.get("resolution_derived_atom_refs", [])):
            errors.append(f"resolved question {record.get('record_id')} cannot derive its original question atom")
        for derived_id in record.get("resolution_derived_atom_refs", []):
            atom = atom_rows.get(derived_id, {})
            if not set(record.get("dependent_atom_refs", [])).intersection(atom.get("derived_from_question_atom_refs", [])):
                errors.append(f"resolved question {record.get('record_id')} derived atom lacks reverse lineage")
            if ledger_outcomes.get(derived_id) not in {"finding", "drift", "nonissue"}:
                errors.append(f"resolved question {record.get('record_id')} derived atom lacks terminal disposition")

    if state == "closed":
        open_questions = [r.get("record_id") for c, r in records.values() if c == "question" and r.get("status") == "open"]
        if open_questions:
            errors.append(f"closed ledger cannot contain open questions: {', '.join(open_questions)}")
    return errors


def validate(
    path: Path,
    *,
    require_closed: bool = False,
    packet_root: Path | None = None,
) -> list[str]:
    try:
        envelope = _load(path)
    except (OSError, ValueError, yaml.YAMLError) as exc:
        return [str(exc)]
    return validate_data(envelope, require_closed=require_closed, packet_root=packet_root)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("packet_root", type=Path)
    parser.add_argument("ledger", help="normalized packet-relative ledger path")
    parser.add_argument("--closed", action="store_true", help="require state: closed")
    args = parser.parse_args(argv)
    path_errors: list[str] = []
    ledger_path = resolve_packet_file(args.packet_root, args.ledger, "ledger", path_errors)
    errors = path_errors
    if ledger_path is not None:
        errors.extend(
            validate(ledger_path, require_closed=args.closed, packet_root=args.packet_root)
        )
    if errors:
        for error in errors:
            print(f"FAIL: {error}", file=sys.stderr)
        return 1
    print("OK: review-gate intermediate ledger is structurally complete")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
