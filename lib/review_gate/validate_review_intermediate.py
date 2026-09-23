#!/usr/bin/env python3
"""Validate a review-gate intermediate source-to-atom classification ledger."""

from __future__ import annotations

import argparse
import hashlib
import os
import json
import os
from pathlib import Path, PurePosixPath
import posixpath
import re
import stat
import sys
from typing import Any

import yaml

try:  # Package import in tests; sibling import when executed as a script.
    from .validate_decisions import validate as validate_decisions
    from .validate_docmodel_approvals import validate as validate_docmodel_approvals
except ImportError:  # pragma: no cover - exercised by CLI dispatch
    try:
        from .validate_decisions import validate as validate_decisions
    except ImportError:
        from validate_decisions import validate as validate_decisions
    from validate_docmodel_approvals import validate as validate_docmodel_approvals


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
#: docauth#369 — 판단 불가는 열린 작업이 아니라 **종결된 리뷰 결론**이다. finding·question
#: 상태 enum의 terminal 멤버로 두되(§8 r1-08 "enum 밖 종결 없음" — 밖이 아니라 확장),
#: done(exit 0)의 조건은 종전 그대로 `rejected|verified` / `resolved`다. 이 상태는 §6
#: 산출물이라 immutable projection에 들어가지 않는다(`status`와 같은 축).
JUDGMENT_UNAVAILABLE = "judgment_unavailable"
FINDING_STATUSES = {
    "discovered", "accepted", "rejected", "planned", "applied", "verified", JUDGMENT_UNAVAILABLE,
}
DONE_FINDING_STATUSES = {"rejected", "verified"}
#: docauth#369: 실행이 `complete`가 되기 위한 terminal 집합(done 집합 ⊂ terminal 집합).
TERMINAL_FINDING_STATUSES = DONE_FINDING_STATUSES | {JUDGMENT_UNAVAILABLE}
QUESTION_STATUSES = {"open", "resolved", JUDGMENT_UNAVAILABLE}
DONE_QUESTION_STATUSES = {"resolved"}
TERMINAL_QUESTION_STATUSES = DONE_QUESTION_STATUSES | {JUDGMENT_UNAVAILABLE}
VERIFY_RESULTS = {"pass", "kill", "unresolved"}
#: docauth#369 §1 하위 블록. `reason`은 발명하지 않는다 — CONTRACT §4.3이 이미 열거한
#: `unresolved` 4경로(ⓐⓑⓒⓓ) + §6 3자 혼재 + 2값으로 닫힌다.
JUDGMENT_UNAVAILABLE_FIELDS = {"reason", "attempts", "basis", "needed_input"}
JUDGMENT_UNAVAILABLE_REASONS = {
    "narrowing_budget_exhausted",   # §4.3 ⓐ 좁힘 반송 상한 초과
    "verdict_branch_mismatch",      # §4.3 ⓑ 판정표 가지 이탈
    "severity_verdict_split",       # §4.3 ⓒ 같은 근거에서 등급 판정이 갈림(§5)
    "referral_record_rejected",     # §4.3 ⓓ 회부 기록 반려 상한 초과
    "panel_split",                  # §6 3자 pass/kill 혼재(ⓒ와 다른 개념 — 설계 r2-03)
    # 관측 노트(2026-09-08) 기준: `authority_absent` = 판정에 필요한 근거가 **대상 문서 밖**
    # (제품 실재·상위 문서·소유자 결정)에 있어 리뷰 입력으로 확보 불가 · `evidence_insufficient`
    # = 근거가 대상 문서 **안**에 있어야 하는데 없거나 모순돼 판정 불가. `needed_input`이 각각
    # "누구/무엇에서 확인"과 "문서의 어느 절이 무엇을 말해야 하는가"를 가리킨다. 이 구분은
    # 산문 기준이라 기계가 검사하지 않는다(알려진 한계).
    "authority_absent",
    "evidence_insufficient",
}
JUDGMENT_UNAVAILABLE_FINDING_ONLY_REASONS = {
    "narrowing_budget_exhausted", "verdict_branch_mismatch", "severity_verdict_split", "panel_split",
}
JUDGMENT_UNAVAILABLE_NARROWING_REASONS = {"narrowing_budget_exhausted", "verdict_branch_mismatch"}
ATTEMPT_FIELDS = {"verifier_id", "result", "snapshot_id", "evidence"}
#: docauth#351 — 부재 계열 question. "있어야 할 것 같은데 없음"은 문서 위에서 전부 같아 보이지만 그 이유는
#: 문서 **밖**에 있다(기결정·제품 실재·부모 문서·인접 근거·저자 암묵). 리뷰는 이 다섯을 가를 수 없고 사람은
#: 초 단위로 가른다 — 그래서 severity finding이 아니라 **처분 질문**(question)으로 낸다. `absence_class`는
#: 리뷰어가 관측한 부재의 형태(문서 안에서 확정할 수 있는 것)이고, 처분 5종은 사람이 dispositions.yaml에 남긴다.
ABSENCE_CLASSES = {
    "rule_absent",          # 규칙·기준이 문서에 없음(예: 재사용 금지 세대 수)
    "concept_undefined",    # 문서가 전제하는 개념이 정의돼 있지 않음(예: SSO/API 계정)
    "case_uncovered",       # 분기·경계 사례가 다뤄지지 않음(예: 설정일 NULL 계정)
    "external_reference",   # 다른 문서·정본을 전제함(예: 특수문자 31자 — 부모 account 문서)
}
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
NARROWING_STRING_FIELDS = ("original_claim", "withdrawn_scope", "residual_claim")
NARROWING_FIELDS = {"original_claim", "counter_quote_anchors", "withdrawn_scope", "residual_claim"}
# docauth#225 — schema_version 2 only (§4.3 기계 하한 상향). schema_version 1 ledgers
# never carry this field and are exempt from every check derived from it below: the
# version boundary is drawn in code, not prose, mirroring how `validate_review_result.py`
# already grandfathers schema_version 1 receipts unchanged next to schema_version 2's
# stricter requirements.
COUNTER_CITATION_VERDICTS = {"none", "partial", "full"}
COUNTER_EVIDENCE_RESOLUTIONS = {"partial", "full"}
COUNTER_EVIDENCE_REQUIRED = {
    "record_id", "finding_record_id", "resolution", "anchors", "snapshot_id",
    "public_record_digest",
}
RECORD_OPTIONAL = {
    # CONTRACT §4.3: 반대 인용으로 지적을 좁혔을 때의 재작성 기록(선택 필드).
    # 쓰면 원 지적문·근거 앵커·철회 범위·남은 주장을 전부 요구해 좁힘이 finding을 비우는 데
    # 쓰이지 못하게 한다. immutable projection에 들어가 종결 후 변조는 digest로 잡힌다.
    "finding": {"narrowing", "judgment_unavailable"},
    "question": {"authority", "scope", "source", "judgment_unavailable", "absence_class", "adjacent_anchors"},
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


def _validate_registry_source_ref(
    packet_root: Path,
    authority_path: Path,
    value: Any,
    label: str,
    errors: list[str],
) -> None:
    """Validate a registry provenance ref relative to the registry within its packet."""
    if not _nonempty(value) or "\x00" in value or "\\" in value or value.startswith(("/", "~")):
        errors.append(f"{label} must resolve below the packet root from the decision registry")
        return
    try:
        registry_relative = authority_path.relative_to(packet_root.resolve())
    except ValueError:
        errors.append(f"{label} decision registry is outside the packet root")
        return
    joined = posixpath.normpath(
        posixpath.join(PurePosixPath(registry_relative.as_posix()).parent.as_posix(), value)
    )
    if joined in {"", ".", ".."} or joined.startswith("../"):
        errors.append(f"{label} resolves outside the packet root")
        return
    resolve_packet_file(packet_root, joined, label, errors)


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


#: docauth#356 §1-4: 게이트가 고정한 합집합(`validate_decisions.load_fixed_union`의 반환). None이면 현행 디스크
#: 경로(실행과 연결되지 않은 레거시 검사) — 단 그 경로도 같은 적격 판정(`suppression_eligible`)을 쓰고
#: includes를 가진 파일은 거부한다(공유 = 스냅샷+서명 필수). 빈 합집합(absent/unchecked 게이트)은 "authority
#: 없음"이라 어떤 decision_registry 참조도 통과하지 않는다.
FixedUnion = dict


def load_run_registry(run_root: Path | None) -> tuple[FixedUnion | None, str | None]:
    """run_root의 `front_gate_decisions_state.json`이 있으면 고정 합집합을 만든다 → (registry, error).
    state 파일이 없으면 (None, None) — 호출자는 현행 디스크 경로로 간다(레거시)."""
    if run_root is None:
        return None, None
    state_path = Path(run_root) / "front_gate_decisions_state.json"
    if not state_path.is_file():
        return None, None
    # 구현 r1-03: 게이트 재실행이 실패하면 정본 trace는 회수되지만 state·아카이브는 남는다 — trace 없는 state는
    # "게이트가 열리지 않은 실행"이라 authority가 없다(디스크 폴백으로 내려가지도 않는다).
    if not (Path(run_root) / "deterministic/FRONT_GATE_TRACE.json").is_file():
        return None, (
            "gate-fixed decision registry state exists but the canonical front gate trace is missing -- "
            "the last gate run did not complete; re-run the front gate (#356)"
        )
    try:
        from .validate_decisions import load_fixed_union
    except ImportError:
        from validate_decisions import load_fixed_union

    union, errors = load_fixed_union(state_path)
    if union is None:
        return None, "gate-fixed decision registry cannot be loaded: " + "; ".join(errors)
    if errors:
        return None, "gate-fixed decision registry does not validate: " + "; ".join(errors)
    return union, None


def _validate_authority_ref(
    value: Any,
    label: str,
    packet_root: Path | None,
    errors: list[str],
    registry: FixedUnion | None = None,
) -> None:
    if not isinstance(value, dict) or set(value) not in (
        {"kind", "path", "sha256", "approval_id"},
        {"kind", "path", "sha256", "decision_id"},
    ):
        errors.append(f"{label} must be a hash-bound approved_docmodel or decision_registry reference")
        return
    kind = value.get("kind")
    expected_fields = (
        {"kind", "path", "sha256", "approval_id"}
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
    raw_path = value["path"]
    if kind == "decision_registry" and registry is not None:
        # docauth#356 §1-4 (d2-01): 디스크를 읽지 않는다 — 게이트가 고정한 합집합과만 대조한다.
        # ⓐ path ∈ files(정규화 절대 경로 동치) ⓑ sha 동일 ⓒ decision_id가 그 파일 소속이며 suppression_eligible.
        try:
            authority_path.relative_to(packet_root)
        except ValueError:
            errors.append(f"{label}.path must stay inside the repository root")
            return
        wanted = os.path.normpath(str(authority_path))
        row = next((f for f in registry.get("files", []) if os.path.normpath(f["declared_path"]) == wanted), None)
        if row is None:
            errors.append(
                f"{label} registry reference outside the gate-fixed union "
                f"({'registry state ' + str(registry.get('authority_reason')) if not registry.get('files') else raw_path}) (#356)"
            )
            return
        if row.get("sha256") != value["sha256"]:
            errors.append(f"{label}.sha256 does not match the gate-fixed registry bytes for {raw_path} (#356)")
            return
        decision_id = value.get("decision_id")
        if not _nonempty(decision_id):
            errors.append(f"{label}.decision_id must be nonempty")
            return
        entry = registry.get("decisions", {}).get(decision_id)
        if entry is None or os.path.normpath(str(entry.get("file"))) != wanted:
            errors.append(f"{label}.decision_id must identify a decision of that file in the gate-fixed union (#356)")
            return
        if not entry.get("suppression_eligible"):
            errors.append(
                f"{label}.decision_id is not suppression-eligible in the gate-fixed union: {entry.get('reason')} (#356)"
            )
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
        # docauth#242: approval is never taken from this file's own claims about itself
        # (a copied pending-item ledger can staple on "approval_state: approved" just as
        # easily as a real approver can). `path`/`sha256` above bind to an independent
        # docmodel-approvals.yaml registry -- exactly like decision_registry binds to
        # decisions.yaml -- and `approval_id` must name a live entry in it. The registry
        # itself re-hashes the docmodel it approves, so a docmodel edited after approval
        # goes stale and stops being suppression-eligible (fail-closed).
        approval_id = value.get("approval_id")
        if not _nonempty(approval_id):
            errors.append(f"{label}.approval_id must be nonempty")
            return
        registry_errors = validate_docmodel_approvals(
            authority_path,
            packet_root=packet_root,
            content=payload,
        )
        if registry_errors:
            errors.append(
                f"{label} docmodel approval registry is not suppression-eligible: "
                f"{'; '.join(registry_errors)}"
            )
            return
        try:
            registry = load_yaml_text(payload)
        except yaml.YAMLError as exc:
            errors.append(f"{label} docmodel approval registry YAML is invalid: {exc}")
            return
        match = next(
            (
                item
                for item in (registry.get("approvals", []) if isinstance(registry, dict) else [])
                if isinstance(item, dict) and item.get("id") == approval_id
            ),
            None,
        )
        if match is None or match.get("status") != "approved":
            errors.append(f"{label}.approval_id must identify a current approved docmodel approval")
            return
        docmodel_path = match.get("docmodel_path")
        if (
            not _nonempty(docmodel_path)
            or Path(docmodel_path).is_absolute()
            or ".." in Path(docmodel_path).parts
        ):
            errors.append(f"{label} approval entry has an unsafe docmodel_path")
            return
        # Codex r5-03: case-fold the check -- ".draft." (lowercase-only) let a name like
        # "model.DRAFT.yaml" through on case-insensitive filesystems (default on macOS/
        # Windows), a direct filename-policy bypass of this guard.
        if ".draft." in Path(docmodel_path).name.lower():
            errors.append(f"{label} cannot reference a draft docmodel")
            return
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
        registry_errors: list[str] = []
        for index, nested_ref in enumerate(nested_refs):
            _validate_registry_source_ref(
                packet_root,
                authority_path,
                nested_ref,
                f"{label}.nested_source_ref[{index}]",
                registry_errors,
            )
        errors.extend(registry_errors)
        if registry_errors:
            return
        decision_errors, _, _ = validate_decisions(authority_path, content=payload)
        # docauth#356 (설계 r3-01): 레거시 디스크 경로도 **같은 적격 판정**을 쓴다 — 단일 파일 합집합의
        # `suppression_eligible`. includes를 가진 파일은 이 경로에서 거부한다(공유 = 스냅샷+서명 필수).
        try:
            from .validate_decisions import validate_union
        except ImportError:
            from validate_decisions import validate_union

        data = load_yaml_text(payload)
        if isinstance(data, dict) and isinstance(data.get("meta"), dict) and data["meta"].get("includes") is not None:
            errors.append(
                f"{label} shared decision registry (meta.includes) needs a gate-fixed snapshot -- "
                "run the front gate with --decisions and validate with --run-root (#356)"
            )
            return
        decision_errors, _w, _i, union = validate_union(
            str(authority_path), files={os.path.normpath(str(authority_path)): payload}
        )
        if decision_errors:
            errors.append(f"{label} decision registry is not suppression-eligible: {'; '.join(decision_errors)}")
            return
        entry = union["decisions"].get(decision_id)
        if entry is None or not entry.get("suppression_eligible"):
            errors.append(
                f"{label}.decision_id must identify a current confirmed decision"
                + (f" ({entry.get('reason')})" if entry else "")
            )


def immutable_projection(category: str, record: dict[str, Any]) -> dict[str, Any]:
    """Return the fields whose post-closure mutation must invalidate a receipt."""
    fields = {
        "finding": (
            "record_id", "finding_id", "candidate_atom_refs", "source_candidate_refs",
            "snapshot_id", "evidence_anchors", "severity", "judgment_provenance", "narrowing",
            "counter_citation_verdict",
        ),
        "question": (
            "record_id", "convention_slot", "dependent_atom_refs",
            "resolution_derived_atom_refs", "authority", "scope", "source",
            "snapshot_id", "evidence_anchors",
            # docauth#351: §4 작성 시점 산출물(§6이 바꾸지 않는다) — projection에 넣어 종결 후 변조를 digest로 잡는다.
            "absence_class", "adjacent_anchors",
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
        "counter_evidence": (
            "record_id", "finding_record_id", "resolution", "anchors", "snapshot_id",
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


def _validate_narrowing(record: dict[str, Any], prefix: str, errors: list[str]) -> None:
    """CONTRACT §4.3 — 반대 인용으로 좁힌 finding의 재작성 기록 형식 검사.

    좁힘은 finding 을 지우는 통로가 아니다: residual_claim 이 비면 그것은 재작성이 아니라
    철회이고, 철회는 §6 kill → §8 rejected 로만 성립한다. 따라서 네 필드 전량 nonempty 를
    요구하고, 좁힘 근거 앵커가 finding 의 evidence_anchors 에 남아 있는지까지 본다.
    """
    narrowing = record.get("narrowing")
    if not isinstance(narrowing, dict):
        errors.append(f"{prefix}.narrowing must be a mapping")
        return
    if set(narrowing) != NARROWING_FIELDS:
        errors.append(
            f"{prefix}.narrowing must contain exactly {', '.join(sorted(NARROWING_FIELDS))}"
        )
        return
    for field in NARROWING_STRING_FIELDS:
        if not _nonempty(narrowing.get(field)):
            errors.append(f"{prefix}.narrowing.{field} must be nonempty")
    anchors = narrowing.get("counter_quote_anchors")
    if not _string_list(anchors):
        errors.append(f"{prefix}.narrowing.counter_quote_anchors must be a unique nonempty string list")
    elif not set(anchors).issubset(set(record.get("evidence_anchors") or [])):
        errors.append(f"{prefix}.narrowing.counter_quote_anchors must be preserved in evidence_anchors")


def required_verifier_count(category: str, record: dict[str, Any]) -> int:
    """CONTRACT §6 결정론 인원: P1 finding 검증은 3자, 그 외는 1자."""
    if category == "finding" and record.get("severity") == "P1":
        return 3
    return 1


def aggregate_verifier_results(results: list[Any]) -> str:
    """CONTRACT §6 3자 집계: 만장 pass → pass · 만장 kill → kill · 그 외(혼재·unresolved) → unresolved."""
    distinct = set(results)
    if distinct == {"pass"}:
        return "pass"
    if distinct == {"kill"}:
        return "kill"
    return "unresolved"


def _validate_judgment_unavailable(
    category: str, record: dict[str, Any], prefix: str, errors: list[str]
) -> None:
    """docauth#369 — `judgment_unavailable` 상태 ⇔ 하위 블록, 그리고 **시도 증명**.

    AC5의 기계 강제: 이 블록은 "검증자가 판단할 수 없었다"를 증명하는 자리이지 "검증을
    안 했다"를 숨기는 자리가 아니다. 그래서 (i) §6이 요구하는 **정확한 인원**의 검증자
    결과가 같은 현재 snapshot에 대해 있어야 하고(부족·초과 모두 거부 — 추가 투표로 확정
    가능한 판정을 판단 불가로 바꾸는 경로를 막는다, 설계 r1 d1-01), (ii) 그 집계가 §6
    규칙으로 `unresolved`여야 하며(만장 pass·kill이면 판단이 *가능*했다), (iii) `reason`은
    닫힌 enum이고 attempts 형태와 정합해야 한다. 비-JU 상태로의 사상(pass→accepted 등)은
    §6·§8이 정하고 여기서는 규정하지 않는다(설계 r1 d1-02).
    """
    block = record.get("judgment_unavailable")
    status = record.get("status")
    if status != JUDGMENT_UNAVAILABLE:
        if "judgment_unavailable" in record:
            errors.append(
                f"{prefix}.judgment_unavailable is only allowed when status is "
                f"{JUDGMENT_UNAVAILABLE} (status is {status!r})"
            )
        return
    if not isinstance(block, dict):
        errors.append(
            f"{prefix} status {JUDGMENT_UNAVAILABLE} requires a judgment_unavailable mapping "
            "(reason, attempts, basis, needed_input) -- a terminal judgment needs its proof of attempt"
        )
        return
    if set(block) != JUDGMENT_UNAVAILABLE_FIELDS:
        errors.append(
            f"{prefix}.judgment_unavailable must contain exactly "
            f"{', '.join(sorted(JUDGMENT_UNAVAILABLE_FIELDS))}"
        )
        return
    reason = block.get("reason")
    if reason not in JUDGMENT_UNAVAILABLE_REASONS:
        errors.append(
            f"{prefix}.judgment_unavailable.reason must be one of "
            f"{', '.join(sorted(JUDGMENT_UNAVAILABLE_REASONS))} (got {reason!r})"
        )
        reason = None
    for field in ("basis", "needed_input"):
        if not _nonempty(block.get(field)):
            errors.append(f"{prefix}.judgment_unavailable.{field} must be nonempty prose")
    if reason is not None:
        if category == "question" and reason in JUDGMENT_UNAVAILABLE_FINDING_ONLY_REASONS:
            errors.append(f"{prefix}.judgment_unavailable.reason {reason!r} applies to findings only")
        if category == "finding" and reason in JUDGMENT_UNAVAILABLE_NARROWING_REASONS and "narrowing" not in record:
            errors.append(
                f"{prefix}.judgment_unavailable.reason {reason!r} requires a narrowing record on the "
                "finding (§4.3 -- the path it names cannot have happened without one)"
            )
    attempts = block.get("attempts")
    required = required_verifier_count(category, record)
    if not isinstance(attempts, list) or not attempts:
        errors.append(
            f"{prefix}.judgment_unavailable.attempts must be a nonempty list -- verification incomplete, "
            f"not {JUDGMENT_UNAVAILABLE}"
        )
        return
    results: list[Any] = []
    seen: set[str] = set()
    well_formed = True
    for index, attempt in enumerate(attempts):
        aprefix = f"{prefix}.judgment_unavailable.attempts[{index}]"
        if not isinstance(attempt, dict) or set(attempt) != ATTEMPT_FIELDS:
            errors.append(f"{aprefix} must contain exactly verifier_id, result, snapshot_id, evidence")
            well_formed = False
            continue
        verifier_id = attempt.get("verifier_id")
        if not _nonempty(verifier_id):
            errors.append(f"{aprefix}.verifier_id must be nonempty")
        elif verifier_id in seen:
            errors.append(f"{aprefix}.verifier_id duplicates another attempt ({verifier_id})")
        else:
            seen.add(verifier_id)
        if attempt.get("result") not in VERIFY_RESULTS:
            errors.append(f"{aprefix}.result must be pass, kill, or unresolved")
            well_formed = False
        if attempt.get("snapshot_id") != record.get("snapshot_id"):
            errors.append(f"{aprefix}.snapshot_id must match the record snapshot_id (§0 same current snapshot)")
        if not _nonempty(attempt.get("evidence")):
            errors.append(f"{aprefix}.evidence must be nonempty")
        results.append(attempt.get("result"))
    if len(attempts) != required:
        who = "P1 finding" if required == 3 else category
        errors.append(
            f"{prefix}.judgment_unavailable.attempts must hold exactly {required} verifier "
            f"attempt(s) for a {who} (§6 panel size), have {len(attempts)} -- "
            + ("verification incomplete, not judgment_unavailable" if len(attempts) < required
               else "extra verifier attempts are not a §6 panel")
        )
        return
    if not well_formed:
        return
    aggregate = aggregate_verifier_results(results)
    if aggregate != "unresolved":
        errors.append(
            f"{prefix}.judgment_unavailable.attempts aggregate to {aggregate!r} under the §6 rule -- "
            f"a judgment was available, so the record cannot be {JUDGMENT_UNAVAILABLE}"
        )
        return
    mixed = {"pass", "kill"}.issubset(set(results))
    if reason == "panel_split" and not mixed:
        errors.append(
            f"{prefix}.judgment_unavailable.reason panel_split requires both pass and kill among attempts"
        )
    elif reason is not None and reason != "panel_split" and mixed:
        errors.append(
            f"{prefix}.judgment_unavailable.attempts mix pass and kill -- that is panel_split, not {reason}"
        )
    elif reason is not None and reason != "panel_split" and "unresolved" not in results:
        errors.append(
            f"{prefix}.judgment_unavailable.reason {reason} requires at least one unresolved attempt"
        )
    if category == "question":
        verification = record.get("classification_verification")
        first = attempts[0]
        if not isinstance(verification, dict) or verification.get("result") != "unresolved":
            errors.append(
                f"{prefix} {JUDGMENT_UNAVAILABLE} question must keep classification_verification.result "
                "unresolved (pass/kill means a judgment was available)"
            )
        elif any(verification.get(k) != first.get(k) for k in ("verifier_id", "result", "evidence")):
            errors.append(
                f"{prefix}.judgment_unavailable.attempts[0] must equal classification_verification "
                "(verifier_id, result, evidence) -- one verification fact, not two"
            )


def _validate_counter_evidence_record(
    record: Any,
    index: int,
    snapshot_id: Any,
    known_source_anchors: set[str],
    errors: list[str],
) -> None:
    """CONTRACT §4.3/docauth#225 — dedicated counter-evidence record (schema_version 2).

    Deliberately NOT a `PUBLIC_COLLECTIONS` category: counter-evidence bears on an
    already-classified finding record, it is not itself a terminal disposition of a
    candidate atom (docauth#225 피어리뷰 r1-04 — folding it into the atom/classification_ledger
    machinery gave a finding-WEAKENING record the atom's terminal `finding` disposition,
    a semantic error). It gets its own small shape check instead of `_validate_record`.

    `anchors ⊆ known_source_anchors` (docauth#225 피어리뷰 r2-01): without this, a `full`
    counter_evidence record's anchors had NO provenance requirement at all -- `partial`
    records get theirs transitively (`counter_evidence.anchors == narrowing.counter_quote_anchors
    ⊆ finding.evidence_anchors ⊆` atom→source chain), but `full` records carry no `narrowing`
    to bind through, so a fabricated anchor (e.g. one that appears nowhere in the document)
    would otherwise pass. Requiring membership in the ledger's full source-candidate anchor
    universe closes that without over-constraining `full` to any single finding's own anchors
    (the rebutting text for a full withdrawal legitimately lives elsewhere in the document).
    """
    prefix = f"counter_evidence[{index}]"
    if not isinstance(record, dict):
        errors.append(f"{prefix} must be a mapping")
        return
    missing = COUNTER_EVIDENCE_REQUIRED.difference(record)
    extra = set(record).difference(COUNTER_EVIDENCE_REQUIRED)
    if missing:
        errors.append(f"{prefix} missing fields: {', '.join(sorted(missing))}")
    if extra:
        errors.append(f"{prefix} unknown fields: {', '.join(sorted(extra))}")
    if not _nonempty(record.get("record_id")):
        errors.append(f"{prefix}.record_id must be nonempty")
    if not _nonempty(record.get("finding_record_id")):
        errors.append(f"{prefix}.finding_record_id must be nonempty")
    if record.get("resolution") not in COUNTER_EVIDENCE_RESOLUTIONS:
        errors.append(f"{prefix}.resolution must be partial or full")
    anchors = record.get("anchors")
    if not _string_list(anchors):
        errors.append(f"{prefix}.anchors must be a unique nonempty string list")
    elif not set(anchors).issubset(known_source_anchors):
        errors.append(f"{prefix}.anchors must be drawn from the ledger's source candidate inventory")
    if record.get("snapshot_id") != snapshot_id:
        errors.append(f"{prefix}.snapshot_id must match review snapshot_id")
    digest = record.get("public_record_digest")
    if not isinstance(digest, str) or not re.fullmatch(r"sha256:[0-9a-f]{64}", digest):
        errors.append(f"{prefix}.public_record_digest must be sha256:<64 lowercase hex>")
    elif digest != record_digest("counter_evidence", record):
        errors.append(f"{prefix}.public_record_digest does not match immutable projection")


def _validate_record(
    category: str,
    record: Any,
    index: int,
    snapshot_id: Any,
    atom_ids: set[str],
    source_ids: set[str],
    packet_root: Path | None,
    schema_version: Any,
    errors: list[str],
    registry: FixedUnion | None = None,
) -> None:
    _validate_common_record(category, record, index, snapshot_id, errors)
    if not isinstance(record, dict):
        return
    prefix = f"{PUBLIC_COLLECTIONS[category]}[{index}]"
    # docauth#225: schema_version 2 findings additionally require a counter-citation
    # verdict field (§4.3 기계 하한). schema_version 1 findings never carry it — the key
    # set they're checked against is deliberately unchanged so old records stay valid.
    effective_required = RECORD_REQUIRED[category]
    if category == "finding" and schema_version == 2:
        effective_required = effective_required | {"counter_citation_verdict"}
    # Codex 피어리뷰 r6-03(#349): 판별자(`schema_version`)가 무효면 어느 형상을 기준으로
    # 볼지가 정해지지 않았다 — 그 상태에서 missing/extra를 내면 정상 v2 record의
    # `counter_citation_verdict`가 `unknown fields`로 **왜곡 보고**된다. 판별자가 확정된
    # 경우에만 이 검사를 한다(무효 자체는 envelope 수준에서 이미 보고된다).
    version_extras = {"counter_citation_verdict"} if category == "finding" else set()
    record_missing = (
        effective_required if schema_version is not None else RECORD_REQUIRED[category]
    ).difference(record)
    record_allowed = effective_required | RECORD_OPTIONAL[category] | (
        set() if schema_version is not None else version_extras
    )
    extra = set(record).difference(record_allowed)
    if record_missing:
        errors.append(f"{prefix} missing fields: {', '.join(sorted(record_missing))}")
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
        if (
            schema_version == 2
            or (schema_version is None and "counter_citation_verdict" in record)
        ) and record.get("counter_citation_verdict") not in COUNTER_CITATION_VERDICTS:
            errors.append(f"{prefix}.counter_citation_verdict must be none, partial, or full")
        if "narrowing" in record:
            _validate_narrowing(record, prefix, errors)
        _validate_judgment_unavailable(category, record, prefix, errors)
    elif category == "question":
        if record.get("status") not in QUESTION_STATUSES:
            errors.append(f"{prefix}.status must be open, resolved, or judgment_unavailable")
        _validate_judgment_unavailable(category, record, prefix, errors)
        # docauth#351: 부재 계열 표기 — 형태는 닫힌 enum, 인접 근거 앵커는 evidence_anchors의 부분집합.
        if "absence_class" in record and record.get("absence_class") not in ABSENCE_CLASSES:
            errors.append(
                f"{prefix}.absence_class must be one of {', '.join(sorted(ABSENCE_CLASSES))} (#351)"
            )
        if "adjacent_anchors" in record:
            if "absence_class" not in record:
                errors.append(f"{prefix}.adjacent_anchors requires absence_class (#351)")
            adjacent = record.get("adjacent_anchors")
            if not _string_list(adjacent, nonempty=False):
                errors.append(f"{prefix}.adjacent_anchors must be a unique string list")
            elif not set(adjacent).issubset(set(record.get("evidence_anchors") or [])):
                errors.append(f"{prefix}.adjacent_anchors must be preserved in evidence_anchors (§3 앵커 합집합)")
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
            if not derived:
                errors.append(f"{prefix}.resolution_derived_atom_refs must be nonempty when resolved")
            if not _nonempty(record.get("authority")):
                errors.append(f"{prefix}.authority is required when resolved")
            if record.get("scope") not in {"document", "template"}:
                errors.append(f"{prefix}.scope must be document or template when resolved")
            _validate_authority_ref(record.get("source"), f"{prefix}.source", packet_root, errors, registry)
            if verification and verification.get("result") == "unresolved":
                errors.append(f"{prefix} resolved question cannot retain unresolved verification")
        else:
            # `open`과 `judgment_unavailable`(docauth#369)은 둘 다 **해소되지 않은** 질문이다 —
            # 해소 필드·파생 atom은 `resolved`에서만 생긴다.
            unresolved_label = "open" if record.get("status") == "open" else JUDGMENT_UNAVAILABLE
            if derived:
                errors.append(f"{prefix} {unresolved_label} question cannot have resolution-derived atoms")
            # docauth#348 (Codex 피어리뷰 r6-02): `open`인데 verdict가 terminal인 혼합
            # 상태를 막는다. 그 상태를 허용하면 §6 진입 게이트가 "아직 해소 안 됨"으로
            # 보고 해소 필드(authority·scope·source)를 결속에서 빼는데, 정작 판정은 이미
            # 나 있어 done에서 그 필드들을 다른 유효값으로 갈아끼울 수 있다.
            if (
                record.get("status") == "open"
                and isinstance(verification, dict)
                and verification.get("result") in {"pass", "kill"}
            ):
                errors.append(
                    f"{prefix} open question cannot already carry a terminal verification "
                    "result (open means §6 has not disposed of it yet)"
                )
            for _field in ("authority", "scope", "source"):
                if _field in record:
                    errors.append(
                        f"{prefix} {unresolved_label} question cannot carry {_field} -- resolution fields "
                        "are produced when the question is resolved"
                    )
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
            _validate_authority_ref(record.get("authority_ref"), f"{prefix}.authority_ref", packet_root, errors, registry)


def validate_data(
    envelope: dict[str, Any],
    *,
    require_closed: bool = False,
    packet_root: Path | None = None,
    registry: FixedUnion | None = None,
) -> list[str]:
    errors: list[str] = []
    schema_version = envelope.get("schema_version")
    # docauth#349 r4-03 → r5-03: `True == 1`·`2.0 == 2`라 bool·float가 정수처럼
    # **버전 분기까지 타고 들어가** 부수 오류를 왜곡했다. 판정을 **분기보다 앞에** 둔다.
    version_known = type(schema_version) is int and schema_version in (1, 2)
    if not version_known:
        errors.append("schema_version must be the integer 1 or 2")
        # Codex 피어리뷰 r6-03: 여기서 `None`으로 두면 required 집합이 사실상 v1 형상이
        # 되어, 정상적인 v2 원장의 `counter_evidence`가 `unknown fields`로, finding의
        # `counter_citation_verdict`가 unknown으로 **왜곡 보고**된다. 판별자가 무효일
        # 때는 version 전용 missing/extra 검사를 하지 않는다(무엇을 기준으로 볼지가
        # 정해지지 않았으므로).
        schema_version = None
    required = {
        "schema_version", "snapshot_id", "target", "state", "source_candidate_inventory",
        "candidate_atoms", "findings", "questions", "drifts", "suppressed", "nonissues",
        "classification_ledger",
    }
    # docauth#225: schema_version 2 adds the `counter_evidence` collection (§4.3 기계
    # 하한). schema_version 1 envelopes must NOT carry this key — the boundary is a
    # closed schema switch, not an optional extension, so an old ledger with a stray
    # `counter_evidence` key fails exactly like any other unknown field would.
    COMMON_ENVELOPE_REQUIRED = required
    if schema_version == 2:
        required = required | {"counter_evidence"}
    # Codex 피어리뷰 r6-03 → r7-03: 판별자가 무효일 때 **모든** 형태 검사를 생략하면
    # 진짜 오타(`mystery_envelope_field`)까지 숨겨 수정 반복을 만든다. 공통 필수 필드는
    # 그대로 검사하고, 허용 필드만 두 스키마의 **합집합**으로 계산한다 — 그러면 정상 v2
    # 형상은 버전 오류 한 줄만 남고, 진짜 unknown field·공통 누락은 계속 보고된다.
    allowed = required if version_known else (required | {"counter_evidence"})
    missing = (required if version_known else COMMON_ENVELOPE_REQUIRED).difference(envelope)
    extra = set(envelope).difference(allowed)
    if missing:
        errors.append(f"missing fields: {', '.join(sorted(missing))}")
    if extra:
        errors.append(f"unknown fields: {', '.join(sorted(extra))}")
    if type(schema_version) is not int or schema_version not in (1, 2):
        errors.append("schema_version must be 1 or 2")
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
        elif expected in {"nonissue", "drift"}:
            # docauth#240: a non-finding closure's evidence must cite a concrete anchor
            # from the atom's own evidence_anchors -- otherwise prose alone ("리뷰어가
            # 맥락이 다르다고 판단함") is self-consistent with the outcome and passes
            # without any check that the judgment is *true* (CONTRACT §2, "이 절이 닫는
            # 것과 닫지 못하는 것"). This does not verify the citation is correct, only
            # that the closure is anchored to something concrete rather than assertion
            # alone -- a low-cost floor, not the full independent-verification proposal.
            anchors = atom.get("evidence_anchors")
            if not isinstance(anchors, list) or not any(
                isinstance(anchor, str) and anchor in basis["evidence"] for anchor in anchors
            ):
                errors.append(
                    f"{prefix}.classification_basis.evidence must cite one of the atom's "
                    f"evidence_anchors for a {expected} outcome (docauth#240)"
                )
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
            _validate_record(
                category, record, index, snapshot_id, atom_ids, source_ids, packet_root,
                schema_version, errors, registry,
            )
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

    # docauth#225 — §4.3 기계 하한 상향 (schema_version 2 only). `counter_evidence` is a
    # dedicated, independently-checkable record type (not a `PUBLIC_COLLECTIONS` terminal
    # disposition — see `_validate_counter_evidence_record`'s docstring). Cross-checking it
    # against each finding's self-reported `counter_citation_verdict` is what makes narrowing
    # omission machine-detectable: previously nothing in the ledger recorded that a
    # counter-citation was even found, so "found it, narrowed silently without recording
    # `narrowing`" was structurally invisible (§4.3 알려진 한계 ⓐ, before this PR).
    counter_evidence_by_finding: dict[str, list[dict[str, Any]]] = {}
    # Codex 피어리뷰 r8-01(#349): 판별자가 무효라고 **존재하는 v2 전용 필드의 내용 검증까지**
    # 건너뛰면, `counter_evidence: "bogus"` 같은 것이 버전 오류 한 줄 뒤에 숨는다 —
    # r7-03이 막으려던 수정 반복이 그대로 남는다. 존재를 **요구하지는 않되**, 있으면 검사한다.
    if schema_version == 2 or (schema_version is None and "counter_evidence" in envelope):
        counter_evidence_rows = envelope.get("counter_evidence")
        if not isinstance(counter_evidence_rows, list):
            errors.append("counter_evidence must be a list")
            counter_evidence_rows = []
        known_source_anchors = {
            anchor
            for source in source_rows.values()
            for anchor in source.get("evidence_anchors", [])
        }
        for ce_index, ce_record in enumerate(counter_evidence_rows):
            _validate_counter_evidence_record(ce_record, ce_index, snapshot_id, known_source_anchors, errors)
            if not isinstance(ce_record, dict) or not _nonempty(ce_record.get("record_id")):
                continue
            ce_record_id = ce_record["record_id"]
            if ce_record_id in records:
                errors.append(f"duplicate record_id across categories: {ce_record_id}")
            else:
                records[ce_record_id] = ("counter_evidence", ce_record)
            finding_record_id = ce_record.get("finding_record_id")
            if _nonempty(finding_record_id):
                target = records.get(finding_record_id)
                if target is None or target[0] != "finding":
                    errors.append(
                        f"counter_evidence[{ce_index}].finding_record_id does not reference an existing finding"
                    )
                else:
                    counter_evidence_by_finding.setdefault(finding_record_id, []).append(ce_record)

        for record_id, (category, record) in records.items():
            if category != "finding":
                continue
            verdict = record.get("counter_citation_verdict")
            matches = counter_evidence_by_finding.get(record_id, [])
            resolutions = {match.get("resolution") for match in matches}
            has_narrowing = "narrowing" in record
            if verdict == "none":
                if matches:
                    errors.append(
                        f"finding {record_id} declares counter_citation_verdict=none but "
                        f"{len(matches)} counter_evidence record(s) target it"
                    )
                if has_narrowing:
                    errors.append(f"finding {record_id} declares counter_citation_verdict=none but has narrowing")
            elif verdict == "partial":
                if not matches or resolutions != {"partial"}:
                    errors.append(
                        f"finding {record_id} declares counter_citation_verdict=partial but lacks a "
                        f"matching partial counter_evidence record"
                    )
                if not has_narrowing:
                    errors.append(
                        f"finding {record_id} declares counter_citation_verdict=partial but narrowing is "
                        f"missing (§4.3 조건부 필수, docauth#225)"
                    )
                elif matches:
                    narrowing_value = record.get("narrowing")
                    if isinstance(narrowing_value, dict) and _string_list(narrowing_value.get("counter_quote_anchors")):
                        narrowing_anchors = set(narrowing_value["counter_quote_anchors"])
                        counter_evidence_anchors = {
                            anchor for match in matches for anchor in (match.get("anchors") or [])
                        }
                        if narrowing_anchors != counter_evidence_anchors:
                            errors.append(
                                f"finding {record_id} narrowing.counter_quote_anchors must equal the union "
                                f"of its counter_evidence record anchors"
                            )
            elif verdict == "full":
                if not matches or resolutions != {"full"}:
                    errors.append(
                        f"finding {record_id} declares counter_citation_verdict=full but lacks a "
                        f"matching full counter_evidence record"
                    )
                if has_narrowing:
                    errors.append(
                        f"finding {record_id} declares counter_citation_verdict=full but has narrowing "
                        f"(full resolution is withdrawal, not narrowing — §4.3)"
                    )
                if record.get("status") == "verified":
                    errors.append(
                        f"finding {record_id} declares counter_citation_verdict=full but status is verified "
                        f"(full resolution requires withdrawal via §6 kill → §8 rejected)"
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
    run_root: Path | None = None,
) -> list[str]:
    try:
        envelope = _load(path)
    except (OSError, ValueError, yaml.YAMLError) as exc:
        return [str(exc)]
    # docauth#356 §1-4: 게이트 실행에 속한 원장은 `--run-root`로 고정 합집합을 받는다(생략 = 실행과
    # 연결되지 않은 레거시 검사 — 디스크 경로. 게이트 실행 원장에는 필수, SKILL 진입점).
    if run_root is not None and packet_root is not None and run_root.resolve() != packet_root.resolve():
        return ["run_root must be the selected packet root"]
    registry, registry_error = load_run_registry(packet_root if packet_root is not None else run_root)
    if registry_error is not None:
        return [registry_error]
    return validate_data(envelope, require_closed=require_closed, packet_root=packet_root, registry=registry)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("packet_root", type=Path)
    parser.add_argument("ledger", help="normalized packet-relative ledger path")
    parser.add_argument("--closed", action="store_true", help="require state: closed")
    parser.add_argument(
        "--run-root", type=Path, default=None,
        help="compatibility alias for packet_root; another run cannot supply authority",
    )
    args = parser.parse_args(argv)
    path_errors: list[str] = []
    ledger_path = resolve_packet_file(args.packet_root, args.ledger, "ledger", path_errors)
    errors = path_errors
    if ledger_path is not None:
        errors.extend(
            validate(ledger_path, require_closed=args.closed, packet_root=args.packet_root, run_root=args.run_root)
        )
    if errors:
        for error in errors:
            print(f"FAIL: {error}", file=sys.stderr)
        return 1
    print("OK: review-gate intermediate ledger is structurally complete")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
