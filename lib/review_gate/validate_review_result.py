#!/usr/bin/env python3
"""Validate legacy v1 and ledger-bound v2 review-gate done receipts."""

from __future__ import annotations

import argparse
from dataclasses import dataclass
import hashlib
import json
from pathlib import Path
import re
import sys
from typing import Any

import yaml

try:  # Package import in tests; sibling import when executed as a script.
    from .validate_convention_profile import _exact_keys, _validate_string_list
    from .validate_input_gate import (
        classified_record_ids,
        defers_verification,
        open_items_ledger_path,
        open_items_ledger_sha256,
        validate_block as validate_input_gate_block,
        verify_source_copy_bytes,
    )
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
    from validate_convention_profile import _exact_keys, _validate_string_list
    from validate_input_gate import (
        classified_record_ids,
        defers_verification,
        open_items_ledger_path,
        open_items_ledger_sha256,
        validate_block as validate_input_gate_block,
        verify_source_copy_bytes,
    )
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
#: #233 — the structure axis (§1 optional input ⑤) is either judged against an
#: approved docmodel or, when no convention authority applies to this template,
#: explicitly undetermined. There is no silent third state.
STRUCTURE_AXIS_STATES = {"judged", "undetermined"}
DEFERRED_MESSAGE = (
    "§7 검증 유예 — 편집 중(또는 편집 상태 미확인)이라 §6 종합 검증 미실행. "
    "계약 위반은 아니지만 done 아님(중간 산출, exit 3)"
)
LEGACY_MESSAGE = (
    "schema_version 1 receipt: new runs issue schema_version 2 (§9). "
    "If this is an already-closed record you are inspecting, use --legacy"
)
COMPARISON_TABLE_SIGNATURE = "# 라운드 대조 —"


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


def _validate_verifiers(
    receipt: dict[str, Any], errors: list[str], *, defer_verification: bool = False
) -> None:
    snapshot = receipt.get("snapshot_id")
    verifiers = receipt.get("verifiers")
    if defer_verification:
        # §7 · #196: while the target is being edited (or its editing state was never
        # established) the §6 done verification is deferred, so an EMPTY verifier list
        # is the correct state rather than a contract violation. It is not an escape
        # hatch: a deferred receipt never validates as done (see evaluate() / exit 3).
        if verifiers != []:
            errors.append(
                "verification is deferred (§7): verifiers must be an empty list until "
                "the target is declared frozen"
            )
        return
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


def _validate_common(
    receipt: dict[str, Any], errors: list[str], *, defer_verification: bool = False
) -> None:
    if receipt.get("route_id") != "review-gate":
        errors.append("route_id must be review-gate")
    for field in ("route_trace", "snapshot_id"):
        if not _nonempty(receipt.get(field)):
            errors.append(f"{field} must be a nonempty string")
    _validate_verifiers(receipt, errors, defer_verification=defer_verification)
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
) -> dict[str, Any] | None:
    if not isinstance(ref, dict) or set(ref) != {"path", "sha256", "snapshot_id"}:
        errors.append("classification_ledger_ref must contain exactly path, sha256, snapshot_id")
        return None
    ledger_path = resolve_packet_file(
        packet_root,
        ref.get("path"),
        "classification_ledger_ref.path",
        errors,
    )
    if ledger_path is None:
        return None
    try:
        payload = ledger_path.read_bytes()
        loaded = load_yaml_text(payload)
        if not isinstance(loaded, dict) or set(loaded) != {ROOT_KEY} or not isinstance(loaded.get(ROOT_KEY), dict):
            raise ValueError(f"YAML must contain exactly top-level {ROOT_KEY} mapping")
        ledger = loaded[ROOT_KEY]
    except (OSError, ValueError, yaml.YAMLError) as exc:
        errors.append(f"cannot load classification ledger: {exc}")
        return None
    actual_hash = hashlib.sha256(payload).hexdigest()
    if ref.get("sha256") != actual_hash:
        errors.append("classification_ledger_ref.sha256 does not match ledger bytes")
    return ledger


def _resolve_packet_relative(packet_root: Path, raw_path: Any, label: str) -> tuple[Path | None, list[str]]:
    """Canonicalize and packet-fence a packet-relative path reference.

    Shared by every receipt field that points at another file by packet-relative path
    (front gate trace, round comparison, input-gate run_root) so the traversal fence —
    no absolute path, no ``..``, resolved path must stay under ``packet_root`` — is
    defined once. Unlike `resolve_packet_file` (validate_review_intermediate.py), this
    accepts ``.`` (the packet root itself) — docloop's `run_root` IS the packet root by
    construction (there is no nested run-folder-within-packet concept here), so a
    receipt correctly names it with the safe, literal sentinel `.`.
    """
    if not _nonempty(raw_path) or Path(raw_path).is_absolute() or ".." in Path(raw_path).parts:
        return None, [f"{label} must be a safe packet-relative path"]
    resolved = (packet_root / raw_path).resolve()
    try:
        resolved.relative_to(packet_root.resolve())
    except ValueError:
        return None, [f"{label} escapes the packet root"]
    return resolved, []


def _resolve_front_gate_trace(
    packet_root: Path, ref: Any, errors: list[str], *, run_root: Path | None
) -> list[Any] | None:
    """#228②: load and digest-verify the pre-lens front gate trace artifact.

    The trace is the record made *before* any lens ran (`front_gate.py`'s
    `FrontGateTrace`, run internally by `docloop review-gate prepare` and frozen to
    `deterministic/FRONT_GATE_TRACE.json` — front_gate.py itself stays internal-only,
    no public execution trace). Binding it by digest closes the gap where a receipt
    could independently redeclare `editing_state`/`target_maturity` as whatever reaches
    done, regardless of what the gate actually recorded pre-lens. A digest mismatch is
    treated as untrusted content: no cross-check is attempted against bytes that do not
    match what the receipt claims to reference.

    `run_root`, when it resolved cleanly, fences the trace to the same run folder as
    the archived ⑧ copy — for docloop that is always `packet_root` itself.
    """
    if not isinstance(ref, dict) or set(ref) != {"path", "sha256"}:
        errors.append("front_gate_ref must contain exactly path, sha256")
        return None
    trace_path, path_errors = _resolve_packet_relative(packet_root, ref.get("path"), "front_gate_ref.path")
    if path_errors:
        errors.extend(path_errors)
        return None
    if run_root is not None:
        try:
            trace_path.relative_to(run_root)
        except ValueError:
            errors.append("front_gate_ref.path must be inside input_gate.run_root")
            return None
    try:
        payload = trace_path.read_bytes()
    except OSError as exc:
        errors.append(f"cannot read front gate trace: {exc}")
        return None
    actual_hash = hashlib.sha256(payload).hexdigest()
    if ref.get("sha256") != actual_hash:
        errors.append("front_gate_ref.sha256 does not match trace bytes")
        return None
    try:
        data = json.loads(payload)
    except json.JSONDecodeError as exc:
        errors.append(f"front gate trace is not valid JSON: {exc}")
        return None
    events = data.get("review_front_gate_trace") if isinstance(data, dict) else None
    if not isinstance(events, list):
        errors.append("front gate trace missing review_front_gate_trace event list")
        return None
    return events


def _validate_front_gate_binding(receipt: dict[str, Any], events: list[Any], errors: list[str]) -> None:
    """#228②: the digest-verified trace must agree with what the receipt declares.

    Closes the path where an execution could record the front gate's pre-lens
    `editing_state`/`target_maturity` honestly and then have its done receipt
    independently declare something more convenient (`frozen` / `complete`) with
    nothing to check the two against each other.
    """
    recorded = [
        event for event in events
        if isinstance(event, dict) and event.get("event") == "input_gate_recorded"
    ]
    if len(recorded) != 1:
        errors.append(
            "front gate trace must contain exactly one input_gate_recorded event "
            f"(found {len(recorded)})"
        )
        return
    event = recorded[0]
    if event.get("phase") != "pre_lens":
        errors.append("front gate trace input_gate_recorded event must have phase pre_lens")
    if event.get("source_copy_verified") is not True:
        errors.append("front gate trace input_gate_recorded event must have source_copy_verified: true")
    input_gate = receipt.get("input_gate") if isinstance(receipt.get("input_gate"), dict) else {}
    source_copy = input_gate.get("source_copy") if isinstance(input_gate.get("source_copy"), dict) else {}
    prior_round = input_gate.get("prior_round") if isinstance(input_gate.get("prior_round"), dict) else {}
    prior_round_output_ref = prior_round.get("output_ref") if isinstance(prior_round.get("output_ref"), dict) else {}
    bound = (
        ("editing_state", input_gate.get("editing_state")),
        ("target_maturity", input_gate.get("target_maturity")),
        ("source_copy_sha256", source_copy.get("sha256")),
        ("prior_round_exists", prior_round.get("exists")),
        ("prior_round_output_round_no", prior_round_output_ref.get("round_no")),
    )
    for field, receipt_value in bound:
        if event.get(field) != receipt_value:
            errors.append(
                f"front gate trace {field}={event.get(field)!r} does not match receipt "
                f"input_gate ({receipt_value!r}) — declaration was rewritten after the "
                "gate recorded it (#228②)"
            )
    if event.get("verification_deferred") != defers_verification(input_gate):
        errors.append(
            "front gate trace verification_deferred does not match receipt input_gate "
            "editing_state (#228②)"
        )
    not_applicable = any(
        isinstance(candidate, dict) and candidate.get("event") == "convention_profile_not_applicable"
        for candidate in events
    )
    if not_applicable and receipt.get("structure_axis") != "undetermined":
        errors.append(
            "front gate trace declares the convention profile not applicable to this "
            "template; receipt structure_axis must be undetermined (#233)"
        )


def _validate_structure_axis(receipt: dict[str, Any], errors: list[str]) -> None:
    """#233: the structure axis (§1 optional input ⑤) is judged or explicitly not."""
    status = receipt.get("structure_axis")
    if status not in STRUCTURE_AXIS_STATES:
        errors.append(f"structure_axis must be one of {sorted(STRUCTURE_AXIS_STATES)}")
        return
    if status == "undetermined":
        if not _nonempty(receipt.get("structure_axis_reason")):
            errors.append(
                "structure_axis_reason must be nonempty when structure_axis is undetermined"
            )
    elif "structure_axis_reason" in receipt:
        errors.append("structure_axis_reason must be omitted when structure_axis is judged")


def _validate_execution(value: Any, errors: list[str], *, label: str = "execution") -> None:
    """#238: §0.2's "확정된 N과 그 사유는 §9 헤더의 '실행' 항에 남긴다" — always, not just
    when N > 1. `lens_rounds`/`lens_rounds_reason` are therefore unconditional; only
    `run_ids` keeps the multi-run qualifier.
    """
    shape_errors = _exact_keys(value, {"run_ids", "lens_rounds", "lens_rounds_reason"}, set(), label)
    if shape_errors:
        errors.extend(shape_errors)
        return
    run_ids = value.get("run_ids")
    errors.extend(_validate_string_list(run_ids, f"{label}.run_ids", nonempty=False))
    lens_rounds = value.get("lens_rounds")
    if not isinstance(lens_rounds, int) or isinstance(lens_rounds, bool) or lens_rounds < 1:
        errors.append(f"{label}.lens_rounds must be a positive integer")
    elif lens_rounds > 1 and not (isinstance(run_ids, list) and run_ids):
        errors.append(f"{label}.run_ids must be nonempty when lens_rounds > 1 (§0.2 다회 실행)")
    if not _nonempty(value.get("lens_rounds_reason")):
        errors.append(f"{label}.lens_rounds_reason must be nonempty (§0.2 N 확정 사유)")


def _validate_scale_component_list(value: Any, label: str) -> tuple[list[str], int | None]:
    """Shared shape for `configuration`/`upper_bound_configuration`: a nonempty list of
    `{name, count}` items with unique names. Returns (errors, sum-of-counts); the sum is
    `None` if the list itself is malformed (nothing to sum)."""
    errors: list[str] = []
    if not isinstance(value, list) or not value:
        return [f"{label} must be a nonempty list"], None
    names: set[str] = set()
    total = 0
    malformed = False
    for index, item in enumerate(value):
        prefix = f"{label}[{index}]"
        item_errors = _exact_keys(item, {"name", "count"}, set(), prefix)
        if item_errors:
            errors.extend(item_errors)
            malformed = True
            continue
        name = item.get("name")
        if not _nonempty(name):
            errors.append(f"{prefix}.name must be a nonempty string")
            malformed = True
        elif name in names:
            errors.append(f"{prefix}.name duplicates an earlier entry in {label} ({name})")
            malformed = True
        else:
            names.add(name)
        count = item.get("count")
        if not isinstance(count, int) or isinstance(count, bool) or count < 0:
            errors.append(f"{prefix}.count must be a non-negative integer")
            malformed = True
        else:
            total += count
    return errors, (None if malformed else total)


def _validate_scale_disclosure(receipt: dict[str, Any], errors: list[str], *, label: str = "scale_disclosure") -> None:
    """#229②: §0.2 실행 전 규모 고지를 receipt 필드로 결속한다.

    이 검사가 닫는 것: ⓐ 고지한 대상 분량이 실제로 검토된 snapshot을 가리키는지,
    ⓑ 선언된 상한이 근거 없는 임의의 수가 아니라 itemize된 구성(configuration)의
    합으로 산술 도출되는지. 도출식·근거 자료의 "타당성"까지는 검사하지 않는다.
    """
    shape_errors = _exact_keys(
        receipt.get(label),
        {"target_volume", "planned_lens_rounds", "configuration", "derived_total_agents"},
        {"upper_bound_agents", "upper_bound_configuration", "upper_bound_basis", "comparison_basis"},
        label,
    )
    if shape_errors:
        errors.extend(shape_errors)
        return
    value = receipt[label]

    target_volume = value.get("target_volume")
    tv_errors = _exact_keys(target_volume, {"lines", "snapshot_id"}, set(), f"{label}.target_volume")
    if tv_errors:
        errors.extend(tv_errors)
    else:
        lines = target_volume.get("lines")
        if not isinstance(lines, int) or isinstance(lines, bool) or lines < 1:
            errors.append(f"{label}.target_volume.lines must be a positive integer")
        if target_volume.get("snapshot_id") != receipt.get("snapshot_id"):
            errors.append(
                f"{label}.target_volume.snapshot_id must match receipt snapshot_id "
                "(고지 대상이 실제로 검토된 snapshot과 같아야 한다)"
            )

    planned = value.get("planned_lens_rounds")
    if not isinstance(planned, int) or isinstance(planned, bool) or planned < 1:
        errors.append(f"{label}.planned_lens_rounds must be a positive integer")
    else:
        execution = receipt.get("execution")
        confirmed = execution.get("lens_rounds") if isinstance(execution, dict) else None
        if isinstance(confirmed, int) and not isinstance(confirmed, bool) and planned != confirmed:
            errors.append(
                f"{label}.planned_lens_rounds ({planned}) must match execution.lens_rounds "
                f"({confirmed}) — 고지된 N과 확정 실행 N이 갈리면 재고지 없이 조용히 늘어난 것이다(§0.2)"
            )

    config_errors, config_total = _validate_scale_component_list(
        value.get("configuration"), f"{label}.configuration"
    )
    errors.extend(config_errors)

    derived = value.get("derived_total_agents")
    if not isinstance(derived, int) or isinstance(derived, bool) or derived < 0:
        errors.append(f"{label}.derived_total_agents must be a non-negative integer")
    elif config_total is not None and derived != config_total:
        errors.append(
            f"{label}.derived_total_agents ({derived}) must equal the sum of "
            f"{label}.configuration[].count ({config_total}) — 선언된 총계가 itemized "
            "구성의 합과 어긋난다"
        )

    has_upper = "upper_bound_agents" in value
    has_upper_config = "upper_bound_configuration" in value
    has_upper_basis = "upper_bound_basis" in value
    if has_upper != has_upper_config or has_upper != has_upper_basis:
        errors.append(
            f"{label}.upper_bound_agents/upper_bound_configuration/upper_bound_basis "
            "must be given together or all omitted"
        )
    elif has_upper:
        upper = value.get("upper_bound_agents")
        if not isinstance(upper, int) or isinstance(upper, bool) or upper < 0:
            errors.append(f"{label}.upper_bound_agents must be a non-negative integer")
            upper = None
        if not _nonempty(value.get("upper_bound_basis")):
            errors.append(f"{label}.upper_bound_basis must be nonempty")
        ub_errors, ub_total = _validate_scale_component_list(
            value.get("upper_bound_configuration"), f"{label}.upper_bound_configuration"
        )
        errors.extend(ub_errors)
        if upper is not None and ub_total is not None and upper != ub_total:
            errors.append(
                f"{label}.upper_bound_agents ({upper}) must equal the sum of "
                f"{label}.upper_bound_configuration[].count ({ub_total}) — 상한은 그것을 "
                "뒷받침하는 구성 후보의 합으로만 도출된다(근거 없는 임의 상한 금지)"
            )
        if (
            upper is not None
            and isinstance(derived, int)
            and not isinstance(derived, bool)
            and upper < derived
        ):
            errors.append(
                f"{label}.upper_bound_agents ({upper}) must be >= "
                f"{label}.derived_total_agents ({derived})"
            )

    if "comparison_basis" in value:
        comparison = value.get("comparison_basis")
        if not isinstance(comparison, list):
            errors.append(f"{label}.comparison_basis must be a list")
        else:
            for index, entry in enumerate(comparison):
                prefix = f"{label}.comparison_basis[{index}]"
                entry_errors = _exact_keys(
                    entry, {"label", "target_lines", "agents", "measured_at"}, set(), prefix
                )
                if entry_errors:
                    errors.extend(entry_errors)
                    continue
                if not _nonempty(entry.get("label")):
                    errors.append(f"{prefix}.label must be nonempty")
                if not _nonempty(entry.get("measured_at")):
                    errors.append(f"{prefix}.measured_at must be nonempty")
                for field in ("target_lines", "agents"):
                    field_value = entry.get(field)
                    if not isinstance(field_value, int) or isinstance(field_value, bool) or field_value < 0:
                        errors.append(f"{prefix}.{field} must be a non-negative integer")


def _validate_revision_during_run(
    value: Any, errors: list[str], *, label: str, live_record_ids: set[str]
) -> None:
    """#228①: §7.1's revision bookkeeping, structured. Conditional — most runs read a
    target that never changes underneath them, and this field does not exist for those.
    """
    shape_errors = _exact_keys(
        value,
        {"observed_snapshots", "evidence_lost_record_ids", "current_snapshot_mismatch"},
        set(),
        label,
    )
    if shape_errors:
        errors.extend(shape_errors)
        return
    snapshots = value.get("observed_snapshots")
    if not isinstance(snapshots, list) or len(snapshots) < 2:
        errors.append(
            f"{label}.observed_snapshots must list at least two revisions observed "
            "during the run (§7.1) — one entry is not a revision"
        )
    else:
        for index, entry in enumerate(snapshots):
            prefix = f"{label}.observed_snapshots[{index}]"
            entry_errors = _exact_keys(entry, {"snapshot_id", "line_count"}, set(), prefix)
            if entry_errors:
                errors.extend(entry_errors)
                continue
            if not _nonempty(entry.get("snapshot_id")):
                errors.append(f"{prefix}.snapshot_id must be nonempty")
            line_count = entry.get("line_count")
            if not isinstance(line_count, int) or isinstance(line_count, bool) or line_count < 0:
                errors.append(f"{prefix}.line_count must be a non-negative integer")
    lost_ids = value.get("evidence_lost_record_ids")
    errors.extend(_validate_string_list(lost_ids, f"{label}.evidence_lost_record_ids", nonempty=False))
    if isinstance(lost_ids, list):
        for record_id in lost_ids:
            if _nonempty(record_id) and record_id not in live_record_ids:
                errors.append(
                    f"{label}.evidence_lost_record_ids entry {record_id} is not a "
                    "finding/question/drift record_id in this receipt"
                )
    if not isinstance(value.get("current_snapshot_mismatch"), bool):
        errors.append(f"{label}.current_snapshot_mismatch must be boolean")


def _validate_round_context(
    receipt: dict[str, Any], packet_root: Path, errors: list[str], *, label: str = "round_context"
) -> None:
    """#208 제안3: §1 ⑨(이전 라운드 산출물 존재 여부) 선언을 receipt에 결속한다."""
    shape_errors = _exact_keys(receipt.get(label), {"round_label"}, {"comparison_ref"}, label)
    if shape_errors:
        errors.extend(shape_errors)
        return
    value = receipt[label]
    round_label = value.get("round_label")
    if not isinstance(round_label, str) or not re.fullmatch(r"r[1-9]\d*", round_label):
        errors.append(f"{label}.round_label must match r<positive integer> (e.g. r1, r2)")
        round_label = None

    input_gate = receipt.get("input_gate")
    prior_round = input_gate.get("prior_round") if isinstance(input_gate, dict) else None
    exists = prior_round.get("exists") if isinstance(prior_round, dict) else None
    has_ref = "comparison_ref" in value

    if exists is True:
        if not has_ref:
            errors.append(
                f"{label}.comparison_ref is required when input_gate.prior_round.exists "
                "is true (§13 match_review_rounds.py 실행 결과 결속)"
            )
        else:
            ref = value["comparison_ref"]
            ref_errors = _exact_keys(ref, {"path", "sha256"}, set(), f"{label}.comparison_ref")
            if ref_errors:
                errors.extend(ref_errors)
            else:
                resolved, path_errors = _resolve_packet_relative(
                    packet_root, ref.get("path"), f"{label}.comparison_ref.path"
                )
                if path_errors:
                    errors.extend(path_errors)
                else:
                    try:
                        payload = resolved.read_bytes()
                    except OSError as exc:
                        errors.append(f"cannot read {label}.comparison_ref: {exc}")
                        payload = None
                    if payload is not None:
                        actual_hash = hashlib.sha256(payload).hexdigest()
                        if ref.get("sha256") != actual_hash:
                            errors.append(f"{label}.comparison_ref.sha256 does not match file bytes")
                        text = payload.decode("utf-8", errors="replace")
                        if not text.startswith(COMPARISON_TABLE_SIGNATURE):
                            errors.append(
                                f"{label}.comparison_ref must point at match_review_rounds.py "
                                f"output (expected to start with {COMPARISON_TABLE_SIGNATURE!r})"
                            )
        prior_output = prior_round.get("output_ref") if isinstance(prior_round, dict) else None
        prior_round_no = prior_output.get("round_no") if isinstance(prior_output, dict) else None
        if (
            round_label is not None
            and isinstance(prior_round_no, int)
            and not isinstance(prior_round_no, bool)
        ):
            expected_label = f"r{prior_round_no + 1}"
            if round_label != expected_label:
                errors.append(
                    f"{label}.round_label ({round_label}) must equal {expected_label} "
                    "(input_gate.prior_round.output_ref.round_no + 1)"
                )
    elif exists is False:
        if has_ref:
            errors.append(
                f"{label}.comparison_ref must be omitted when input_gate.prior_round.exists is false"
            )
        if round_label is not None and round_label != "r1":
            errors.append(
                f"{label}.round_label must be r1 when input_gate.prior_round.exists is false "
                "(§1 ⑨ — 이 산출물이 1라운드임을 명시)"
            )
    # exists가 bool이 아니면 input_gate 쪽 검사가 이미 그 오류를 내므로 여기서는
    # 추가 에러를 내지 않는다(중복 보고 방지).


def _resolve_ref(candidate: Path, packet_root: Path | None) -> Path | None:
    """Canonical filesystem identity for a reference, however it was spelled."""
    try:
        if candidate.is_absolute():
            return candidate.resolve()
        if packet_root is not None:
            return (packet_root / candidate).resolve()
    except OSError:
        return None
    return None


def _validate_open_item_classification(
    receipt: dict[str, Any],
    input_gate: Any,
    ledger_records: dict[str, tuple[str, dict[str, Any]]],
    packet_root: Path | None,
) -> list[str]:
    """Hold the #206 line: registered open items CLASSIFY findings, never suppress them.

    1. The document's own open-item ledger may never appear as a suppression
       `authority_ref`. §2 keeps a single suppression channel — a verified decision
       registry entry — and a document's "not decided yet" table is not one.
    2. Anything the receipt marks as landing on a registered open item must still be a
       live finding in the receipt.
    """
    errors: list[str] = []
    ledger_path = open_items_ledger_path(input_gate)
    if ledger_path is not None:
        declared_sha = open_items_ledger_sha256(input_gate)
        candidate = Path(ledger_path)
        resolved = _resolve_ref(candidate, packet_root)
        for record_id, (category, record) in sorted(ledger_records.items()):
            if category != "suppressed":
                continue
            authority = record.get("authority_ref")
            if not isinstance(authority, dict) or not _nonempty(authority.get("path")):
                continue
            authority_path = Path(authority["path"])
            same_file = str(authority_path) == str(candidate)
            authority_resolved = _resolve_ref(authority_path, packet_root)
            settled = False
            if not same_file and resolved is not None and authority_resolved is not None:
                same_file = authority_resolved == resolved
                if not same_file:
                    try:
                        same_file = resolved.samefile(authority_resolved)
                        settled = True
                    except OSError:
                        pass
            if not same_file and not settled and declared_sha is not None:
                same_file = authority.get("sha256") == declared_sha
            if same_file:
                errors.append(
                    f"suppressed record {record_id} cites the document's open-item ledger as "
                    "suppression authority; registered open items classify findings, they never "
                    "suppress them (§2)"
                )
    findings = receipt.get("findings")
    live = {
        record.get("record_id"): record
        for record in (findings if isinstance(findings, list) else [])
        if isinstance(record, dict)
    }
    for record_id in classified_record_ids(input_gate):
        if record_id not in live:
            errors.append(
                f"input_gate.open_items.classified_record_ids entry {record_id} is not a live "
                "finding in this receipt; classification marks a finding, it never removes one (§9)"
            )
        elif live[record_id].get("status") == "rejected":
            errors.append(
                f"input_gate.open_items.classified_record_ids entry {record_id} is rejected; "
                "a registered open item never rejects a finding (§2·§9). If §6 killed it on "
                "other grounds, remove it from classified_record_ids"
            )
    return errors


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
    receipt: dict[str, Any],
    packet_root: Path,
    expected_packet_binding: dict[str, Any],
) -> list[str]:
    errors: list[str] = []
    required = {
        "schema_version", "route_id", "route_trace", "snapshot_id", "target", "verifiers",
        "input_gate", "classification_ledger_ref", "packet_binding", "findings", "questions",
        "drifts",
        # #228②: pre-lens front gate trace, digest-bound. #233: structure axis judged
        # or explicitly not. #238: §0.2's confirmed N and its reason. #229②: §0.2's
        # pre-execution scale disclosure, machine-bound to the executed configuration.
        # #208 제안3: §1 ⑨ prior-round declaration, bound to actual round-comparison proof.
        "front_gate_ref", "structure_axis", "execution", "scale_disclosure", "round_context",
    }
    optional = {
        "unassured_mode", "unassured_accepted_by",
        "structure_axis_reason", "revision_during_run",
    }
    missing = required.difference(receipt)
    extra = set(receipt).difference(required | optional)
    if missing:
        errors.append(f"missing fields: {', '.join(sorted(missing))}")
    if extra:
        errors.append(f"unknown fields: {', '.join(sorted(extra))}")
    input_gate = receipt.get("input_gate")
    errors.extend(
        validate_input_gate_block(
            input_gate,
            label="input_gate",
            allow_classification=True,
            require_run_root=True,
            snapshot_id=receipt.get("snapshot_id"),
        )
    )
    deferred = defers_verification(input_gate)
    _validate_common(receipt, errors, defer_verification=deferred)
    _validate_packet_binding(receipt, expected_packet_binding, errors)
    binding = receipt.get("packet_binding")
    if isinstance(binding, dict):
        if receipt.get("snapshot_id") != binding.get("target_snapshot"):
            errors.append("receipt snapshot_id must match packet_binding.target_snapshot")
        if receipt.get("target") != binding.get("target_source"):
            errors.append("receipt target must match packet_binding.target_source")

    # #228③: the receipt no longer just carries the ⑧ source_copy hash as a claim —
    # it re-hashes the archived bytes at the run_root it points to (for docloop this
    # is always the packet root itself).
    run_root_path: Path | None = None
    if isinstance(input_gate, dict):
        run_root_path, run_root_errors = _resolve_packet_relative(
            packet_root, input_gate.get("run_root"), "input_gate.run_root"
        )
        errors.extend(run_root_errors)
        if run_root_path is not None:
            errors.extend(verify_source_copy_bytes(input_gate, run_root_path, label="input_gate"))

    # #228②: bind the receipt's own editing_state/target_maturity/source_copy
    # declaration to the digest-verified pre-lens trace, so a receipt cannot declare
    # something the front gate never actually recorded before lenses ran.
    events = _resolve_front_gate_trace(
        packet_root, receipt.get("front_gate_ref"), errors, run_root=run_root_path
    )
    if events is not None:
        _validate_front_gate_binding(receipt, events, errors)

    _validate_structure_axis(receipt, errors)
    _validate_execution(receipt.get("execution"), errors)
    _validate_scale_disclosure(receipt, errors)
    _validate_round_context(receipt, packet_root, errors)

    receipt_record_ids: set[str] = set()
    for collection_name in ("findings", "questions", "drifts"):
        collection = receipt.get(collection_name)
        if isinstance(collection, list):
            for record in collection:
                if isinstance(record, dict) and _nonempty(record.get("record_id")):
                    receipt_record_ids.add(record["record_id"])
    if "revision_during_run" in receipt:
        _validate_revision_during_run(
            receipt.get("revision_during_run"),
            errors,
            label="revision_during_run",
            live_record_ids=receipt_record_ids,
        )

    ref = receipt.get("classification_ledger_ref")
    ledger = _resolve_ledger(packet_root, ref, errors)
    if isinstance(ref, dict) and ref.get("snapshot_id") != receipt.get("snapshot_id"):
        errors.append("classification_ledger_ref.snapshot_id must match receipt snapshot_id")
    if ledger is None:
        return errors
    errors.extend(
        f"ledger: {error}"
        for error in validate_intermediate_data(
            ledger,
            require_closed=not deferred,
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
            if not deferred and category == "finding" and record.get("status") not in DONE_STATUSES:
                errors.append(f"{prefix}.status must be rejected or verified for done")
            if not deferred and category == "question" and record.get("status") != "resolved":
                errors.append(f"{prefix}.status must be resolved for done")
            if category == "question":
                verification = record.get("classification_verification")
                if not isinstance(verification, dict) or set(verification) != {"result", "verifier_id", "evidence"}:
                    errors.append(f"{prefix}.classification_verification has invalid shape")
                else:
                    if not deferred and verification.get("result") not in {"pass", "kill"}:
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
    errors.extend(
        _validate_open_item_classification(receipt, input_gate, ledger_records, packet_root)
    )
    return errors


@dataclass(frozen=True)
class LegacyFieldReport:
    """Field-completeness result for an already-closed ``schema_version: 1`` record.

    A plain list return here used to be read as `== []` meaning "done" -- the exact
    shape a real done verdict has. Wrapping the list in a dataclass (no `__getitem__`,
    no `__iter__`) closes that conflation: the only way to reach the wrapped list is
    the named `.field_errors` attribute, never a bare `== []`/`[0]`/unpack.
    """

    field_errors: list[str]


def legacy_field_errors(path: Path) -> LegacyFieldReport:
    """Field errors in an already-closed ``schema_version: 1`` record.

    **This is not a validator and returns no verdict.** It answers "were this closed
    record's fields complete?" -- never "may a run stop here". A v1 receipt predates
    the §1 input gate, so it cannot answer the latter at all; use :func:`validate`.
    """
    try:
        receipt = _frontmatter(path)
    except (OSError, ValueError, yaml.YAMLError) as exc:
        return LegacyFieldReport([str(exc)])
    if receipt.get("schema_version") != 1:
        return LegacyFieldReport(
            ["legacy_field_errors only inspects schema_version 1 records; use validate()"]
        )
    return LegacyFieldReport(_validate_v1(receipt))


def validate(
    packet_root: Path,
    receipt_relative_path: str,
    expected_packet_binding: dict[str, Any],
) -> list[str]:
    """Done oracle. A deferred receipt is *not* done, so it is never an empty list.

    Callers (tests, hooks) treat `validate(...) == []` as "this is done". This oracle
    has no legacy switch at all -- there is no argument a caller can pass to make it
    accept a v1 receipt, so the §1 input gate cannot be skipped through it. Inspecting
    an already-closed historical record is a different question with a different name,
    and that name is not a verdict: :func:`legacy_field_errors`.
    """
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
        return [LEGACY_MESSAGE]
    if type(version) is int and version != 2:
        return ["schema_version must be 1 or 2"]
    errors = _validate_v2(receipt, packet_root, expected_packet_binding)
    deferred = defers_verification(receipt.get("input_gate"))
    if deferred and not errors:
        return [DEFERRED_MESSAGE]
    return errors


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
    parser.add_argument(
        "--legacy",
        action="store_true",
        help="field-check an already-closed schema_version: 1 record (never a done verdict)",
    )
    args = parser.parse_args(argv)
    if args.legacy:
        receipt_path = args.packet_root / args.receipt
        report = legacy_field_errors(receipt_path)
        if report.field_errors:
            for error in report.field_errors:
                print(f"FAIL: {error}", file=sys.stderr)
            return 1
        # §0 binds done to exit 0, so a v1 record must never reach it.
        print(f"LEGACY-OK: field-complete schema_version 1 record. {LEGACY_MESSAGE}")
        return 4
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
    if errors == [DEFERRED_MESSAGE]:
        print(f"DEFERRED: {DEFERRED_MESSAGE}")
        return 3
    if errors:
        for error in errors:
            print(f"FAIL: {error}", file=sys.stderr)
        return 1
    print("OK: review-gate done receipt is current and complete")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
