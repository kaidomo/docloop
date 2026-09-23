#!/usr/bin/env python3
"""Validate legacy v1 and ledger-bound v2 review-gate done receipts."""

from __future__ import annotations

import argparse
from dataclasses import dataclass
import hashlib
import json
import os
from pathlib import Path
import re
import stat
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
    try:
        from .validate_review_intermediate import (DONE_FINDING_STATUSES, PUBLIC_COLLECTIONS, ROOT_KEY, load_yaml_text, record_digest, resolve_packet_file, validate_data as validate_intermediate_data)
    except ImportError:
        from validate_review_intermediate import (DONE_FINDING_STATUSES, PUBLIC_COLLECTIONS, ROOT_KEY, load_yaml_text, record_digest, resolve_packet_file, validate_data as validate_intermediate_data)
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
    try:
        from .validate_review_intermediate import (DONE_FINDING_STATUSES, PUBLIC_COLLECTIONS, ROOT_KEY, load_yaml_text, record_digest, resolve_packet_file, validate_data as validate_intermediate_data)
    except ImportError:
        from validate_review_intermediate import (DONE_FINDING_STATUSES, PUBLIC_COLLECTIONS, ROOT_KEY, load_yaml_text, record_digest, resolve_packet_file, validate_data as validate_intermediate_data)


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



try:
    try:
        from .validate_review_intermediate import (DONE_QUESTION_STATUSES, JUDGMENT_UNAVAILABLE, TERMINAL_FINDING_STATUSES, TERMINAL_QUESTION_STATUSES)
    except ImportError:
        from validate_review_intermediate import (DONE_QUESTION_STATUSES, JUDGMENT_UNAVAILABLE, TERMINAL_FINDING_STATUSES, TERMINAL_QUESTION_STATUSES)
except ImportError:
    try:
        from .validate_review_intermediate import (DONE_QUESTION_STATUSES, JUDGMENT_UNAVAILABLE, TERMINAL_FINDING_STATUSES, TERMINAL_QUESTION_STATUSES)
    except ImportError:
        from validate_review_intermediate import (DONE_QUESTION_STATUSES, JUDGMENT_UNAVAILABLE, TERMINAL_FINDING_STATUSES, TERMINAL_QUESTION_STATUSES)

EXECUTION_STATUSES = {"complete", "incomplete"}
DOCUMENT_CLEARANCES = {"clear", "findings_present", "indeterminate"}
INDETERMINATE_MESSAGE = (
    "실행 완료·판단 불가 항목 존재 — 모든 항목이 terminal이지만 judgment_unavailable이 있어 "
    "document_clearance: indeterminate. 계약 위반은 아니지만 done 아님(exit 5, COMPLETE-INDETERMINATE)"
)
RE_SHA256 = re.compile(r"^[0-9a-f]{64}$")
LEGACY_FIELDS_MESSAGE = (
    "종결된 schema_version 1 기록의 필드는 완전하다. **done 판정이 아니다** — "
    "v1 은 §1 입력 게이트 이전 형식이라 done 을 물을 수 있는 기록이 아니다(exit 4)"
)
LEDGER_REF_FIELDS = {"path", "sha256", "snapshot_id", "schema_version"}
FRONT_GATE_INTAKE_CANONICAL_RELPATH = "front_gate_intake.yaml"
FRONT_GATE_PROFILE_CANONICAL_RELPATH = "front_gate_profile.yaml"
FRONT_GATE_INPUT_GATE_CANONICAL_RELPATH = "front_gate_input_gate.yaml"
FRONT_GATE_DOCMODEL_CANONICAL_RELPATH = "front_gate_docmodel.yaml"
FRONT_GATE_DECISIONS_CANONICAL_RELPATH = "front_gate_decisions.yaml"
FRONT_GATE_DECISIONS_STATE_CANONICAL_RELPATH = "front_gate_decisions_state.json"
FRONT_GATE_DECISIONS_CANONICAL_RELDIR = "front_gate_decisions"
DECISION_REGISTRY_EVENT = "decision_registry_recorded"
DECISION_REGISTRY_STATES = {"checked": "checked", "absent_unassured": "absent", "present_unchecked": "unchecked"}
ANCHOR_GUARD_TRACE_CANONICAL_RELPATH = "anchor_guard_trace.json"
VERIFY_GATE_TRACE_CANONICAL_RELPATH = "verify_gate_trace.json"
VERIFY_GATE_LEDGER_CANONICAL_RELPATH = "verify_gate_ledger.yaml"
VERIFY_UNITS_CANONICAL_RELDIR = "verify_units"
VERIFY_GATE_SOURCE_CANONICAL_RELPATH = "verify_gate_source.md"

def _validate_front_gate_recomputation(
    events: list[Any], run_root: Path, errors: list[str]
) -> None:
    """docauth#299: close the gap #228②/#296 left open -- the digest binding above only
    proves the receipt agrees with *some* trace file, never that the trace itself came
    from a real ``review_front_gate.py`` run. If the gate archived the three pre-lens
    inputs it actually read (``review_front_gate.py --run-root``), replay them through
    ``build_trace()`` and require the result to reproduce the stored trace exactly.

    Comparison is event-by-event (parsed values), not byte-for-byte, so the trailing
    newline ``main()`` appends to stdout can never cause a false mismatch.

    No archives (older runs, or --run-root not used at gate time) skips this check --
    no retroactive invalidation, same policy as every prior binding in this file. A
    partial archive (one or two of the three files) is NOT treated as "no archive": it
    means --run-root was used but the set is incomplete, so it fails closed instead of
    silently skipping the check it was supposed to enable.
    """
    intake_path = run_root / FRONT_GATE_INTAKE_CANONICAL_RELPATH
    profile_path = run_root / FRONT_GATE_PROFILE_CANONICAL_RELPATH
    input_gate_path = run_root / FRONT_GATE_INPUT_GATE_CANONICAL_RELPATH
    present = [p for p in (intake_path, profile_path, input_gate_path) if p.is_file()]
    if not present:
        errors.append("new packet requires archived front-gate inputs")
        return
    if len(present) != 3:
        errors.append(
            "front gate input archive is incomplete -- expected all three of "
            f"{FRONT_GATE_INTAKE_CANONICAL_RELPATH!r}, {FRONT_GATE_PROFILE_CANONICAL_RELPATH!r}, "
            f"{FRONT_GATE_INPUT_GATE_CANONICAL_RELPATH!r} in run_root or none of them"
        )
        return
    # docauth#315 (PLAN §3.7): the fourth archive is CONDITIONAL -- most runs never
    # touch the structure axis, so its absence is not "incomplete" the way missing
    # one of the three above would be. When present, pass it straight through as an
    # already-resolved docmodel_path -- preflight() only re-checks the archived
    # bytes' sections against the archived intake's observed_sections (both pinned),
    # never rescans contracts/docmodel.*.yaml or the live approvals registry. If the
    # stored trace recorded a judged-via-direct-docmodel event but this archive is
    # missing or its bytes were swapped, the recomputed event simply won't match the
    # stored one -- the generic comparison below catches it without a dedicated check.
    docmodel_archive_path = run_root / FRONT_GATE_DOCMODEL_CANONICAL_RELPATH
    docmodel_path = docmodel_archive_path if docmodel_archive_path.is_file() else None
    # docauth#352: the registry declaration archive is replayed the same way. Absent
    # (pre-0.32 archived run) → no registry event is replayed; the stored trace then
    # must not carry one either (generic prefix comparison below).
    decisions_state = None
    decisions_path = None
    decisions_bytes = None
    state_archive = run_root / FRONT_GATE_DECISIONS_STATE_CANONICAL_RELPATH
    if not state_archive.is_file():
        # Codex 구현 r1-01: 3종 아카이브가 있는 실행은 0.32 게이트를 거친 것이다(그 게이트는 선언
        # 아카이브를 항상 쓴다). 여기서 조용히 넘기면 아카이브만 지우고 이벤트를 재계산 범위 밖(뒤)에
        # 붙여 `checked`를 위조할 수 있다 — 3종이 있으면 선언 아카이브도 **필수**(불완전 아카이브 규칙과 동일).
        errors.append(
            f"front gate input archive is present but {FRONT_GATE_DECISIONS_STATE_CANONICAL_RELPATH!r} is "
            "missing -- a 0.32 gate run always archives the decision registry declaration (#352)"
        )
        return
    if state_archive.is_file():
        state_payload, state_error = _read_packet_file_bytes(state_archive, "front gate decisions state archive")
        if state_error is not None:
            errors.append(state_error)
            return
        try:
            declared = json.loads((state_payload or b"").decode("utf-8"))
        except (UnicodeDecodeError, ValueError) as exc:
            errors.append(f"cannot read the archived decision registry declaration: {exc}")
            return
        if not isinstance(declared, dict) or declared.get("state") not in {"checked", "absent", "unchecked"}:
            errors.append("archived decision registry declaration is malformed (#352)")
            return
        decisions_state = declared["state"]
        decisions_path = declared.get("path")
        decisions_files = None
        if decisions_state != "absent":
            registry_archive = run_root / FRONT_GATE_DECISIONS_CANONICAL_RELPATH
            registry_payload, registry_error = _read_packet_file_bytes(registry_archive, "front gate decisions archive")
            if registry_error is not None:
                errors.append(registry_error)
                return
            decisions_bytes = registry_payload
            # docauth#356: `checked`는 루트+전이적 includes를 파일별로 아카이브한다(state.files). 재생은 그
            # 바이트 매핑으로 한다 — 매핑 없이 루트만 주면 includes가 "not archived"로 재생이 어긋난다.
            rows = declared.get("files")
            if decisions_state == "checked" and "files" in declared:
                if not isinstance(rows, list) or not rows:
                    errors.append("archived decision registry declaration has a malformed files list (#356)")
                    return
                decisions_files = {}
                for row in rows:
                    if not isinstance(row, dict) or not isinstance(row.get("archive"), str) or not isinstance(row.get("declared_path"), str):
                        errors.append("archived decision registry declaration has a malformed files row (#356)")
                        return
                    archive_path = (run_root / row["archive"]).resolve()
                    try:
                        archive_path.relative_to(run_root.resolve())
                    except ValueError:
                        errors.append(f"archived decision registry file {row['archive']!r} escapes run_root (#356)")
                        return
                    payload, read_error = _read_packet_file_bytes(archive_path, "front gate decisions file archive")
                    if read_error is not None:
                        errors.append(read_error)
                        return
                    if hashlib.sha256(payload or b"").hexdigest() != row.get("sha256"):
                        errors.append(f"archived decision registry file {row['archive']!r} does not match its recorded sha256 (#356)")
                        return
                    decisions_files[os.path.normpath(row["declared_path"])] = payload

    # Deferred import: review_front_gate.py imports FRONT_GATE_TRACE_CANONICAL_RELPATH
    # (and, as of #299, the three constants above) from this module at its own top
    # level, so importing it back at our top level would be circular. By call time
    # both modules are already loaded, so this resolves cleanly either way this file
    # is invoked (as __main__ or imported).
    try:
        from .front_gate import (build_trace)
    except ImportError:
        from front_gate import (build_trace)

    try:
        recomputed = build_trace(
            profile_path, intake_path, input_gate_path, run_root=run_root, docmodel_path=docmodel_path,
            decisions_state=decisions_state, decisions_path=decisions_path, decisions_bytes=decisions_bytes,
            decisions_files=decisions_files,
        )
    except (OSError, RuntimeError, ValueError, yaml.YAMLError) as exc:
        errors.append(f"cannot recompute front gate trace from archived inputs: {exc}")
        return
    # Intermediate/candidate-question events (if any) are outside #299's scope -- the
    # archive holds only the three pre-lens inputs -- so only the corresponding prefix
    # of the stored trace is checked, never the full trailing candidate-question tail.
    if recomputed != events[: len(recomputed)]:
        errors.append(
            "recomputed front gate trace does not match the archived trace -- the "
            "stored front_gate_trace.json does not match what its own archived inputs "
            "produce (#299)"
        )

def _trace_path_mismatch(raw: Any, expected: Path, repo_root: Path | None, label: str) -> list[str]:
    """가드 trace가 적은 경로가 receipt가 가리키는 파일과 **같은 파일**인가 (Codex r4-01).

    문자열 동일이 아니라 파일 동일성(`os.path.samefile`)으로 본다 — 대소문자 비구분
    파일시스템이나 심링크에서는 같은 파일의 `resolve()` 문자열이 다를 수 있어, 문자열
    비교는 거짓 FAIL을 낸다. 경로 자체는 `_resolve_packet_relative`와 같은 펜스를 받는다:
    절대경로·`..`·저장소 탈출은 거절한다.
    """
    if not isinstance(raw, str) or not raw:
        return [f"{label} must be a nonempty string"]
    if repo_root is None:
        return [f"cannot locate repository root for {label}"]
    resolved, path_errors = _resolve_packet_relative(repo_root, raw, label)
    if path_errors:
        return path_errors
    assert resolved is not None
    try:
        if os.path.samefile(resolved, expected):
            return []
    except OSError:
        pass
    return [f"{label} does not name the same file the receipt does"]

def _validate_anchor_guard_ref(
    receipt: dict[str, Any],
    errors: list[str],
    *,
    repo_root: Path | None,
    run_root: Path | None,
    ledger_path: Path | None,
    ledger_sha256: str | None,
) -> None:
    """docauth#349 ④: 앵커 가드를 **무엇에** 돌렸는지 receipt에 남긴다.

    2026-09-07 실행은 `audit_anchors.py`를 합성본 `canonical.md`에 돌려 ANCHOR-OK를
    받고 receipt에 `누락 0`을 선언했다. 그런데 실제로 사람에게 전달된 것은
    `findings.md`였고, **그 기준으로는 선언이 참이 아니었다.** 계약이 요구하는 대상과
    실제 검사 대상이 갈렸고, 무엇에 돌렸는지가 판정문 어디에도 남지 않아 사후에
    확인할 방법도 없었다.

    계약 §3은 이제 **전달본**으로 대상을 고정한다(사용자 확정 2026-09-07). 기계가
    "이 파일이 정말 전달본인가"를 판정할 수는 없다 — 그건 규범이다.

    **이 검사의 지위**(Codex 피어리뷰 r2-01): 이것은 receipt·가드 trace·원장 사이의
    **자기정합성 검사**이지 가드 실행의 재계산이 아니다. run 폴더에 쓸 수 있는 실행자는
    세 값이 맞물린 trace를 손으로 만들 수 있다 — #296·#298이 `front_gate_ref`에 대해 남긴
    것과 같은 위협모델 경계다. 그것을 닫으려면 가드 입력 전량을 아카이브해 done 시점에
    판정을 재계산해야 하고(#299가 front gate에 한 일), 그건 별건으로 남겼다.
    여기서 실제로 닫히는 것은 **부주의한 재선언**이다: 합성본에 돌리고 전달본을 선언하는
    것, 다른 원장으로 통과한 가드를 이 원장에 결속하는 것, 실패한 실행 뒤 옛 성공 trace가
    남는 것 — 2026-09-07 사고가 정확히 그 부류였다.
    """
    ref = receipt.get("anchor_guard_ref")
    if not isinstance(ref, dict) or set(ref) != {"path", "sha256", "trace_path", "trace_sha256"}:
        errors.append("anchor_guard_ref must contain exactly path, sha256, trace_sha256")
        return
    if repo_root is None:
        errors.append("cannot locate repository root for anchor_guard_ref")
        return
    target_path, path_errors = _resolve_packet_relative(repo_root, ref.get("path"), "anchor_guard_ref.path")
    if path_errors:
        errors.extend(path_errors)
        return
    if run_root is not None:
        # 이 실행의 산출물이어야 한다 — 다른 실행 폴더의 전달본을 가리키는 것은
        # `front_gate_ref`/`verify_gate_ref`가 이미 막는 것과 같은 재활용이다.
        try:
            target_path.relative_to(run_root)
        except ValueError:
            errors.append("anchor_guard_ref.path must be inside input_gate.run_root")
            return
    payload, read_error = _read_packet_file_bytes(target_path, "anchor_guard_ref")
    if read_error is not None:
        errors.append(read_error)
        return
    assert payload is not None
    target_hash = hashlib.sha256(payload).hexdigest()
    if ref.get("sha256") != target_hash:
        errors.append("anchor_guard_ref.sha256 does not match the referenced file's bytes")
        return
    # Codex 피어리뷰 r1-01(#349): 대상 경로·해시 **선언**만으로는 "합성본에 돌리고
    # 전달본을 선언하는" 조합이 그대로 통과한다 — 선언과 실행이 결속되지 않는다.
    # `audit_anchors.py --run-root`가 남긴 구조화된 trace가 그 결속이다: 도구 자신이
    # 자기가 해시한 대상과 판정을 적으므로, 선언된 대상의 바이트와 도구가 실제로
    # 본 바이트가 같아야만 done이 성립한다.
    if run_root is None:
        errors.append("cannot fence anchor_guard_ref without a resolved input_gate.run_root")
        return
    delivery = _result_attempt(repo_root, ref.get("trace_path"), ANCHOR_GUARD_TRACE_CANONICAL_RELPATH, errors)
    if delivery is None:
        return
    trace_path = delivery / ANCHOR_GUARD_TRACE_CANONICAL_RELPATH
    trace_payload, trace_error = _read_packet_file_bytes(trace_path, "anchor guard trace")
    if trace_error is not None:
        errors.append(trace_error)
        return
    assert trace_payload is not None
    if ref.get("trace_sha256") != hashlib.sha256(trace_payload).hexdigest():
        errors.append("anchor_guard_ref.trace_sha256 does not match the anchor guard trace bytes")
        return
    try:
        trace = json.loads(trace_payload)
    except (json.JSONDecodeError, UnicodeDecodeError) as exc:
        errors.append(f"anchor guard trace is not valid JSON: {exc}")
        return
    body = trace.get("review_anchor_guard_trace") if isinstance(trace, dict) else None
    if not isinstance(body, dict):
        errors.append("anchor guard trace missing review_anchor_guard_trace mapping")
        return
    sources = body.get("sources")
    if not isinstance(sources, list) or not sources:
        errors.append("anchor guard trace must bind at least one lens/l2/scan source")
    else:
        seen_sources = set()
        for index, source in enumerate(sources):
            label = f"anchor guard sources[{index}]"
            if not isinstance(source, dict) or set(source) != {"kind", "path", "sha256"}:
                errors.append(f"{label} must contain kind, path and sha256")
                continue
            if source.get("kind") not in {"lens", "l2:신규쟁점", "scan:HIT"}:
                errors.append(f"{label} has unsupported kind")
            source_path = resolve_packet_file(repo_root, source.get("path"), label, errors)
            if source_path is None:
                continue
            identity = (source.get("kind"), str(source_path))
            if identity in seen_sources:
                errors.append(f"{label} duplicates a source")
            seen_sources.add(identity)
            source_raw, source_error = _read_packet_file_bytes(source_path, label)
            if source_error:
                errors.append(source_error)
            elif source.get("sha256") != hashlib.sha256(source_raw).hexdigest():
                errors.append(f"{label} bytes changed since delivery audit")
    synth = body.get("synth")
    if not isinstance(synth, dict) or synth.get("sha256") != target_hash:
        errors.append(
            "anchor guard trace declares different bytes than anchor_guard_ref names -- the "
            "trace, the receipt and the ledger are not self-consistent about which artifact "
            "the guard was pointed at (#349 ④)"
        )
    else:
        # Codex 피어리뷰 r4-01(#349): 해시만 보면 `canonical.md`와 `findings.md`가 **같은
        # 바이트**일 때 trace가 전자를, receipt가 후자를 선언해도 통과한다 — 이 조항이
        # 막으려던 조합 그 자체다. 경로도 대조하되, 심링크·대소문자 별칭은 문자열이 아니라
        # **파일 동일성**으로 본다.
        errors.extend(
            _trace_path_mismatch(
                synth.get("path"), target_path, repo_root, "anchor guard trace synth.path"
            )
        )
    if body.get("result") != "ANCHOR-OK":
        errors.append(
            f"anchor guard trace records result {body.get('result')!r}, not ANCHOR-OK"
        )
    # Codex 피어리뷰 r2-02(#349): v2에서 **실제 앵커 판정 대상은 SYNTH가 아니라 원장**이다
    # (`classification_ledger[].evidence_anchors`가 terminal SSOT). trace가 원장 경로·해시를
    # 적어 두는데도 receipt의 `classification_ledger_ref`와 대조하지 않으면, 원장 A로 가드를
    # **실제로 돌려 통과한 뒤** receipt는 앵커가 누락된 원장 B를 참조해도 SYNTH 해시만 같으면
    # done이 된다 — 위조 없이 성립하는 구멍이다.
    trace_ledger = body.get("ledger")
    if ledger_sha256 is None:
        # 원장을 못 읽었으면 그 실패는 `_resolve_ledger`가 이미 보고한다.
        return
    if trace_ledger is None:
        errors.append(
            "anchor guard trace declares no ledger -- a v2 run binds its anchors to the "
            "classification ledger, so the guard must have been given --ledger (#349 ④)"
        )
    elif not isinstance(trace_ledger, dict) or trace_ledger.get("sha256") != ledger_sha256:
        errors.append(
            "anchor guard trace declares a different classification ledger than "
            "classification_ledger_ref names -- in v2 the ledger IS the anchor SSOT, so a "
            "guard verdict recorded against another ledger says nothing about this one (#349 ④)"
        )
    elif ledger_path is not None:
        # Codex 피어리뷰 r3-02(#349): 문자열 비교는 ⓐ 상대경로 호출을 절대경로와 견줘
        # 정상 실행을 거부하고, ⓑ `path`가 list/dict이면 `TypeError`로 검증기를 죽인다.
        # 타입을 먼저 확인하고 **같은 저장소 루트 기준으로 resolve해 Path끼리** 비교한다.
        errors.extend(
            _trace_path_mismatch(
                trace_ledger.get("path"), ledger_path, repo_root,
                "anchor guard trace ledger.path",
            )
        )

def _resolve_verify_gate_trace(
    receipt_path: Path, ref: Any, errors: list[str], *, run_root: Path | None
) -> list[Any] | None:
    """docauth#348: §6 진입 게이트가 남긴 trace를 읽고 digest로 결속한다.

    `_resolve_front_gate_trace`와 같은 규약이다 — 형식·저장소 루트·경로 펜스·
    정본 파일명(#296)·digest 일치·JSON 파싱 순으로 fail-closed한다. 다른 점은
    이벤트 목록의 키(`review_verify_gate_trace`)와, 이 게이트가 §1이 아니라
    §4 → §6 경계를 증언한다는 것뿐이다.
    """
    if not isinstance(ref, dict) or set(ref) != {"path", "sha256"}:
        errors.append("verify_gate_ref must contain exactly path, sha256")
        return None
    root = receipt_path
    if root is None:
        errors.append("cannot locate repository root for verify_gate_ref")
        return None
    trace_path, path_errors = _resolve_packet_relative(root, ref.get("path"), "verify_gate_ref.path")
    if path_errors:
        errors.extend(path_errors)
        return None
    if run_root is not None:
        try:
            trace_path.relative_to(run_root)
        except ValueError:
            errors.append("verify_gate_ref.path must be inside input_gate.run_root")
            return None
        if trace_path != run_root / VERIFY_GATE_TRACE_CANONICAL_RELPATH:
            errors.append(
                f"verify_gate_ref.path must be {VERIFY_GATE_TRACE_CANONICAL_RELPATH!r} "
                "relative to input_gate.run_root"
            )
            return None
    payload, read_error = _read_packet_file_bytes(trace_path, "verify gate trace")
    if read_error is not None:
        errors.append(read_error)
        return None
    assert payload is not None
    if ref.get("sha256") != hashlib.sha256(payload).hexdigest():
        errors.append("verify_gate_ref.sha256 does not match trace bytes")
        return None
    try:
        data = json.loads(payload)
    except (json.JSONDecodeError, UnicodeDecodeError) as exc:
        errors.append(f"verify gate trace is not valid JSON: {exc}")
        return None
    events = data.get("review_verify_gate_trace") if isinstance(data, dict) else None
    if not isinstance(events, list):
        errors.append("verify gate trace missing review_verify_gate_trace event list")
        return None
    return events

def _verify_gate_event(events: list[Any], name: str) -> dict[str, Any] | None:
    for event in events:
        if isinstance(event, dict) and event.get("event") == name:
            return event
    return None

def _validate_verify_gate_binding(
    receipt: dict[str, Any], events: list[Any], receipt_record_ids: set[str], errors: list[str]
) -> None:
    """docauth#348: digest로 결속된 §6 진입 trace가 receipt와 같은 실행을 말하는가.

    `_validate_front_gate_binding`과 같은 성격 — trace가 진짜인지(그건 재계산의
    몫)가 아니라, **receipt가 가리키는 trace가 이 실행의 것인가**를 본다.
    """
    validated = _verify_gate_event(events, "classification_ledger_validated")
    opened = _verify_gate_event(events, "verification_phase_opened")
    if validated is None or opened is None:
        errors.append(
            "verify gate trace must contain both classification_ledger_validated and "
            "verification_phase_opened events (#348)"
        )
        return
    if validated.get("snapshot_id") != receipt.get("snapshot_id"):
        errors.append("verify gate trace snapshot_id must match receipt snapshot_id")
    if validated.get("target") != receipt.get("target"):
        errors.append("verify gate trace target must match receipt target")
    # Codex 피어리뷰 r7-02: 진입 원장이 이미 종결이었다면 그 게이트는 §6 **뒤에** 불린
    # 것이다. 게이트 쪽 거부와 함께 done 쪽에서도 결속한다(옛 trace 재사용 차단).
    if validated.get("ledger_state") != "open":
        # Codex 피어리뷰 r9-03: 게이트 쪽 주석·CONTRACT는 이 검사를 "문자 그대로 closed인
        # 입력의 거부"로 낮췄는데 여기 메시지만 "§6 뒤에 열렸다"는 **시간 사실**을 단정했다.
        # 보장 수준을 문구까지 맞춘다 — 계보/epoch는 #360·#357.
        errors.append(
            "verify gate trace records a non-open entry ledger "
            f"({validated.get('ledger_state')!r}) -- this violates the entry contract, "
            "though on its own it does not prove when the gate ran"
        )
    gate_ids = opened.get("public_record_ids")
    if not isinstance(gate_ids, list) or not all(isinstance(item, str) for item in gate_ids):
        errors.append("verify gate trace public_record_ids must be a list of strings")
        return
    # §6은 §4가 낸 것을 검증한다. 검증이 **시작된 뒤에** 처음 나타난 공개 기록은
    # 그 검증을 받은 적이 없다 — 오늘(2026-09-07) 사고의 순서 뒤집기가 정확히
    # 이 방향이었다(receipt 먼저, 원장 나중).
    unseen = sorted(receipt_record_ids.difference(gate_ids))
    if unseen:
        errors.append(
            "receipt public records were absent when the §6 verification gate opened: "
            + ", ".join(unseen)
            # docauth#360: §6 중 질문 해소가 finding·drift를 파생하면 정상 실행도 여기
            # 걸린다 — 그때의 정규 절차는 갱신된 원장으로 게이트를 다시 여는 것이고,
            # 그 재실행이 계보를 어떻게 보존할지는 별건으로 열려 있다.
            + " (§6 중 질문 해소로 새 record가 파생됐다면 갱신된 원장으로 게이트를 다시 "
            "열어라 — 계보 보존은 #360)"
        )
    # 반대 방향도 막는다: 게이트가 본 기록이 receipt에서 사라졌다면, 그 trace는 이
    # receipt의 것이 아니거나 §6 도중 원장이 바뀐 것이다. 후자라면 절차는 게이트
    # 재실행이며(그러면 trace가 다시 쓰인다), 그것이 바로 이 계약이 강제하려는
    # 순서다. `final receipt omits ledger public records`는 **종결 원장** 기준이라
    # 이 축(진입 시점 원장)을 대신 보지 못한다.
    vanished = sorted(set(gate_ids).difference(receipt_record_ids))
    if vanished:
        errors.append(
            "records present when the §6 verification gate opened are missing from the receipt: "
            + ", ".join(vanished)
        )
    # Codex 피어리뷰 r1-01: id 집합만으로는 **같은 대상을 두 번 리뷰한 다른 실행**의
    # trace를 이 실행의 것과 구별하지 못한다(snapshot·target·id가 모두 같을 수 있다).
    # 기록 내용까지 대조해야 결속이 성립한다. 비교 대상은 §6이 바꾸지 않는 범주만
    # (finding·drift) — question의 immutable projection은 §6이 확정하는
    # `classification_verification_result`를 포함해, 결속하면 정상 실행이 거짓 FAIL한다.
    gate_digests = opened.get("stable_record_digests")
    pre_digests = opened.get("pre_verification_digests")
    entry_results = opened.get("question_entry_results")
    if (
        not isinstance(gate_digests, dict)
        or not isinstance(pre_digests, dict)
        or not isinstance(entry_results, dict)
    ):
        errors.append(
            "verify gate trace must carry stable_record_digests, pre_verification_digests "
            "and question_entry_results as mappings"
        )
        return
    try:
        from .review_verify_gate import (pre_verification_digest)
    except ImportError:
        from review_verify_gate import (pre_verification_digest)

    for collection_name, category in (
        ("findings", "finding"), ("drifts", "drift"), ("questions", "question"),
    ):
        collection = receipt.get(collection_name)
        if not isinstance(collection, list):
            continue
        for record in collection:
            if not isinstance(record, dict):
                continue
            record_id = record.get("record_id")
            if not isinstance(record_id, str) or record_id not in gate_ids:
                continue
            # Codex 피어리뷰 r2-06: question은 §6이 확정하는 판정 필드만 빼고 결속한다.
            # 통째로 빼면 같은 record_id만 유지하면 authority·source·계보가 다른 실행의
            # trace도 통과한다(r1-01의 구멍이 question 축에 그대로 남는다).
            if category == "question":
                expected = pre_digests.get(record_id)
                # Codex 피어리뷰 r5-02: volatile 집합은 **진입 상태**에 달렸다. done 시점의
                # 상태(`resolved`)로 재계산하면 진입이 `open`이었던 정상 질문이 어긋난다.
                # Codex 피어리뷰 r7-01: 진입 **status만** 복원하고 verdict는 최종값으로
                # 두면 `_volatile_keys()`의 `open + unresolved` 조건이 성립하지 않아,
                # 정상 `open+unresolved → resolved+pass` 전이가 불일치로 거부된다.
                # **진입 상태를 통째로 복원**해 게이트가 쓴 것과 같은 mask로 재계산한다.
                entry = entry_results.get(record_id)
                if isinstance(entry, dict):
                    entry_view = dict(record)
                    entry_view["status"] = entry.get("status", record.get("status"))
                    verification = record.get("classification_verification")
                    entry_view["classification_verification"] = {
                        **(verification if isinstance(verification, dict) else {}),
                        "result": entry.get("result"),
                    }
                else:
                    entry_view = record
                actual = pre_verification_digest(category, entry_view)
                label = "pre_verification_digest"
            else:
                expected = gate_digests.get(record_id)
                actual = record.get("public_record_digest")
                label = "public_record_digest"
            if expected is None:
                errors.append(
                    f"verify gate trace records no {category} digest for {record_id}"
                )
            elif expected != actual:
                errors.append(
                    f"{record_id} changed between the §6 verification gate and the receipt: "
                    f"{label} does not match what the gate recorded"
                )
            if category == "question":
                # Codex 피어리뷰 r3-05: verdict를 digest에서 뺀 대가로 `pass ↔ kill`
                # 변조까지 허용됐다 — 계약 §6이 감사 결과 변조로 금지한 전환이다.
                # 허용 전이는 `unresolved → 무엇이든`과 `이미 terminal이면 그대로`뿐이다.
                entry = entry_results.get(record_id)
                entry_result = entry.get("result") if isinstance(entry, dict) else entry
                verification = record.get("classification_verification")
                final = (
                    verification.get("result") if isinstance(verification, dict) else None
                )
                if entry_result in {"pass", "kill"} and final != entry_result:
                    errors.append(
                        f"{record_id}.classification_verification.result was already "
                        f"{entry_result!r} when the §6 gate opened and may not be rewritten "
                        f"to {final!r} (§6 terminal verdicts are audit outcomes, not logs)"
                    )

def _validate_verification_unit_package(
    events: list[Any], run_root: Path, errors: list[str]
) -> None:
    """docauth#353 · Codex 피어리뷰 r3-06: **패키지 자체**가 이 실행의 것인가.

    trace가 unit id만 결속하면 다른 실행의 단위 파일을 복사해 넣어도 done이 관측하지
    못한다 — "패키지 재활용 불가"라는 주장이 실제 보증보다 강했다. 파일명과 내용
    digest를 대조해 그 축을 닫는다. 단위가 하나도 없으면 §6에 줄 입력이 없었던 것이므로
    fail-closed한다(게이트를 부르지 않은 실행이 여기서 걸린다).
    """
    opened = _verify_gate_event(events, "verification_phase_opened")
    expected = opened.get("verification_unit_digests") if isinstance(opened, dict) else None
    if not isinstance(expected, dict):
        errors.append("verify gate trace must carry verification_unit_digests as a mapping")
        return
    units_dir = run_root / VERIFY_UNITS_CANONICAL_RELDIR
    # Codex 피어리뷰 r4-03: `is_dir()`·`iterdir()`는 **디렉터리 심링크를 따라간다** —
    # run_root 밖의 패키지를 심링크로 연결해도 파일명·digest만 맞으면 통과했다.
    # 게이트가 소유한 **실제** 디렉터리만 인정한다(r3-07이 생산자 쪽에 세운 원칙과 동일).
    try:
        dir_stat = os.lstat(units_dir)
    except FileNotFoundError:
        errors.append(
            f"{VERIFY_UNITS_CANONICAL_RELDIR}/ is missing from run_root -- the §6 gate "
            "publishes the verifier input package on every run, so its absence means the "
            "verification phase was never opened through the gate (#353)"
        )
        return
    except OSError as exc:
        errors.append(f"cannot inspect {VERIFY_UNITS_CANONICAL_RELDIR}/: {exc}")
        return
    if not stat.S_ISDIR(dir_stat.st_mode) or stat.S_ISLNK(dir_stat.st_mode):
        errors.append(
            f"{VERIFY_UNITS_CANONICAL_RELDIR} must be a real directory in run_root, not a "
            "symlink or other file -- a link can point the package outside this run"
        )
        return
    present = set()
    for entry in units_dir.iterdir():
        entry_stat = os.lstat(entry)
        if not stat.S_ISREG(entry_stat.st_mode) or stat.S_ISLNK(entry_stat.st_mode):
            # 조용히 건너뛰면 예상 밖 항목이 검사 대상에서 빠진다(r4-03).
            errors.append(
                f"verification unit package contains a non-regular entry: {entry.name}"
            )
            continue
        present.add(entry.name)
    missing = sorted(set(expected).difference(present))
    extra = sorted(present.difference(expected))
    if missing:
        errors.append(f"verification unit package is missing files: {', '.join(missing)}")
    if extra:
        errors.append(f"verification unit package has files the gate never wrote: {', '.join(extra)}")
    for filename in sorted(set(expected).intersection(present)):
        payload, read_error = _read_packet_file_bytes(units_dir / filename, f"verification unit {filename}")
        if read_error is not None:
            errors.append(read_error)
            continue
        assert payload is not None
        if expected[filename] != "sha256:" + hashlib.sha256(payload).hexdigest():
            errors.append(
                f"verification unit {filename} does not match what the §6 gate wrote "
                "-- it was edited or copied from another run"
            )

def _validate_verify_gate_recomputation(
    events: list[Any], run_root: Path, errors: list[str], *, repo_root: Path | None = None
) -> None:
    """docauth#348: trace가 자기 아카이브에서 재생되는가 (#299와 같은 형태).

    아카이브가 없으면(옛 실행, `--run-root` 미사용) 건너뛴다 — 소급 무효화
    없음, 이 파일의 모든 결속이 따르는 같은 정책이다.
    """
    _validate_verification_unit_package(events, run_root, errors)
    registry, registry_error = _load_run_registry(repo_root)
    if registry_error is not None:
        errors.append(registry_error)
        return
    ledger_archive_path = run_root / VERIFY_GATE_LEDGER_CANONICAL_RELPATH
    if not ledger_archive_path.is_file():
        # Codex 피어리뷰 r1-02: 여기서 조용히 건너뛰면 "게이트를 부르지 않고 손으로
        # 맞춰 쓴 trace"가 재계산 없이 통과한다. front gate(#299)는 아카이브 이전에
        # 발행된 **실제 옛 실행**이 있어 건너뛰기가 필요했지만, `verify_gate_ref`는
        # 이번에 새로 생긴 필드다 — 이 필드를 단 receipt는 예외 없이 이 변경 이후의
        # 실행이므로, 아카이브가 없다는 것은 "옛 실행"이 아니라 **게이트를 거치지
        # 않았다**는 뜻이다. 소급 보호를 잃지 않으면서 fail-closed할 수 있는 자리다.
        errors.append(
            f"verify_gate_ref is present but {VERIFY_GATE_LEDGER_CANONICAL_RELPATH!r} is "
            "missing from run_root -- the §6 gate archives the ledger it read on every run, "
            "so its absence means the trace was not produced by the gate (#348)"
        )
        return
    # 지연 import: review_verify_gate.py가 이 모듈의 상수를 쓰지는 않지만,
    # `validate_review_intermediate`를 거쳐 같은 sys.path에 산다. 호출 시점에는
    # 두 모듈이 모두 로드돼 있으므로 __main__/import 어느 쪽이든 해결된다.
    try:
        from .review_verify_gate import (build_verify_trace)
    except ImportError:
        from review_verify_gate import (build_verify_trace)

    payload, read_error = _read_packet_file_bytes(ledger_archive_path, "verify gate ledger archive")
    if read_error is not None:
        errors.append(read_error)
        return
    assert payload is not None
    source_snapshot: bytes | None = None
    opened = _verify_gate_event(events, "verification_phase_opened")
    evidence_inputs = opened.get("evidence_inputs") if isinstance(opened, dict) else None
    if evidence_inputs is None:
        errors.append("verify gate evidence_inputs.source_snapshot is required for schema-2 packets")
        return
    if evidence_inputs is not None:
        source_ref = evidence_inputs.get("source_snapshot") if isinstance(evidence_inputs, dict) else None
        source_path = run_root / VERIFY_GATE_SOURCE_CANONICAL_RELPATH
        if not isinstance(source_ref, dict) or source_ref.get("path") != VERIFY_GATE_SOURCE_CANONICAL_RELPATH:
            errors.append("verify gate evidence_inputs.source_snapshot must name the canonical source archive")
        else:
            source_snapshot, source_error = _read_packet_file_bytes(source_path, "verify gate source snapshot")
            if source_error is not None:
                errors.append(source_error)
            elif source_ref.get("sha256") != "sha256:" + hashlib.sha256(source_snapshot).hexdigest():
                errors.append("verify gate source snapshot does not match its trace sha256")
    if source_snapshot is not None and repo_root is not None:
        frozen, error = _read_packet_file_bytes(repo_root / "frozen/target.txt", "prepared source")
        if error or frozen != source_snapshot:
            errors.append("verify gate source differs from prepared target")
    try:
        archived = load_yaml_text(payload.decode("utf-8"))
        recomputed = build_verify_trace(archived, source_snapshot=source_snapshot)
    except (UnicodeDecodeError, ValueError, yaml.YAMLError) as exc:
        errors.append(f"cannot recompute verify gate trace from the archived ledger: {exc}")
        return
    # docauth#369 (Codex 구현 r1-01): trace 재계산은 아카이브의 **구조**를 다시 보지 않았다 —
    # 실제 게이트라면 거부할 진입 원장(예: `discovered`인데 judgment_unavailable 블록을 단
    # finding)을 아카이브로 두고 trace·단위·해시만 맞추면 통과했다. 게이트와 같은 구조 검사를
    # 여기서 다시 돌린다(외부 상태 무관한 검사 — 게이트가 요구하는 `require_closed=False`).
    body = archived.get("review_intermediate") if isinstance(archived, dict) else None
    if isinstance(body, dict):
        errors.extend(
            f"verify gate ledger archive: {error}"
            for error in validate_intermediate_data(body, require_closed=False, packet_root=repo_root, registry=registry)
        )
    if recomputed != events:
        errors.append(
            "recomputed verify gate trace does not match the archived trace -- the stored "
            f"{VERIFY_GATE_TRACE_CANONICAL_RELPATH} does not match what its own archived "
            "ledger produces (#348)"
        )

def _public_statuses(receipt: dict[str, Any]) -> tuple[list[Any], list[Any]]:
    findings = receipt.get("findings")
    questions = receipt.get("questions")
    finding_statuses = [
        r.get("status") for r in findings if isinstance(r, dict)
    ] if isinstance(findings, list) else []
    question_statuses = [
        r.get("status") for r in questions if isinstance(r, dict)
    ] if isinstance(questions, list) else []
    return finding_statuses, question_statuses

def has_judgment_unavailable(receipt: dict[str, Any]) -> bool:
    finding_statuses, question_statuses = _public_statuses(receipt)
    return JUDGMENT_UNAVAILABLE in finding_statuses or JUDGMENT_UNAVAILABLE in question_statuses

def recompute_execution_axes(receipt: dict[str, Any], *, deferred: bool) -> tuple[str, str]:
    """docauth#369 — 공개 record에서 (execution_status, document_clearance)를 재계산한다.

    * `incomplete` ⇔ §7 검증 유예 receipt 이거나 terminal이 아닌 finding/question이 있다.
      (유예가 아닌데 terminal 아닌 항목이 있으면 이미 exit 1이므로, 실제로 `incomplete`가
      유효한 receipt는 유예본뿐이다.)
    * clearance: 실행이 `incomplete`면 `indeterminate`(검증이 돌지 않았는데 clear를 말할 수
      없다) · judgment_unavailable이 1건이라도 있으면 `indeterminate` · 아니면 `rejected`가
      아닌 finding이 1건이라도 있으면 `findings_present` · 아니면 `clear`. clearance는 **기록에
      확인된 지적이 있는가**이지 remediation 여부가 아니다(#240 경계 그대로).
    """
    finding_statuses, question_statuses = _public_statuses(receipt)
    incomplete = deferred or any(
        status not in TERMINAL_FINDING_STATUSES for status in finding_statuses
    ) or any(status not in TERMINAL_QUESTION_STATUSES for status in question_statuses)
    execution = "incomplete" if incomplete else "complete"
    if incomplete or JUDGMENT_UNAVAILABLE in finding_statuses or JUDGMENT_UNAVAILABLE in question_statuses:
        clearance = "indeterminate"
    elif any(status != "rejected" for status in finding_statuses):
        clearance = "findings_present"
    else:
        clearance = "clear"
    return execution, clearance

def _validate_execution_axes(receipt: dict[str, Any], errors: list[str], *, deferred: bool) -> None:
    declared_execution = receipt.get("execution_status")
    declared_clearance = receipt.get("document_clearance")
    if declared_execution not in EXECUTION_STATUSES:
        errors.append("execution_status must be complete or incomplete (#369)")
    if declared_clearance not in DOCUMENT_CLEARANCES:
        errors.append("document_clearance must be clear, findings_present, or indeterminate (#369)")
    if declared_execution not in EXECUTION_STATUSES or declared_clearance not in DOCUMENT_CLEARANCES:
        return
    execution, clearance = recompute_execution_axes(receipt, deferred=deferred)
    if declared_execution != execution:
        errors.append(
            f"execution_status declares {declared_execution!r} but the public records recompute to "
            f"{execution!r} (#369 -- the declaration is bound to observation)"
        )
    if declared_clearance != clearance:
        errors.append(
            f"document_clearance declares {declared_clearance!r} but the public records recompute to "
            f"{clearance!r} (#369 -- judgment_unavailable forces indeterminate; no approval, unassured "
            "acceptance, or suppression changes that)"
        )

def _validate_judgment_unavailable_entry_status(
    receipt: dict[str, Any], run_root: Path, errors: list[str]
) -> None:
    """docauth#369 (설계 r2-01): finding의 judgment_unavailable은 **최초 검증**의 결말이다.

    §7 델타 검증(`applied` 뒤 해소 확인)의 `unresolved`는 종전대로 `applied` 유지·blocking
    이며, 단일 snapshot의 attempts로 그것을 terminal로 바꿀 수 없다. §6 진입 원장 아카이브
    (`verify_gate_ledger.yaml`)에서 같은 record의 진입 status가 `discovered`였는지 대조한다.
    아카이브 부재는 이미 #348 규칙(`_validate_verify_gate_recomputation`)이 FAIL한다.
    """
    findings = receipt.get("findings")
    questions = receipt.get("questions")
    unavailable = {
        r.get("record_id") for r in (findings if isinstance(findings, list) else [])
        if isinstance(r, dict) and r.get("status") == JUDGMENT_UNAVAILABLE
    }
    unavailable_questions = {
        r.get("record_id") for r in (questions if isinstance(questions, list) else [])
        if isinstance(r, dict) and r.get("status") == JUDGMENT_UNAVAILABLE
    }
    if not unavailable and not unavailable_questions:
        return
    archive = run_root / VERIFY_GATE_LEDGER_CANONICAL_RELPATH
    if not archive.is_file():
        return
    payload, read_error = _read_packet_file_bytes(archive, "verify gate ledger archive")
    if read_error is not None or payload is None:
        return
    try:
        entry = load_yaml_text(payload.decode("utf-8"))
    except (UnicodeDecodeError, ValueError, yaml.YAMLError):
        return
    body = entry.get("review_intermediate") if isinstance(entry, dict) else None
    entry_findings = body.get("findings") if isinstance(body, dict) else None
    entry_questions = body.get("questions") if isinstance(body, dict) else None
    # question도 같은 축이다(Codex 구현 r1-01의 대칭): 판단 불가는 §6 산출물이라 진입 시점에는
    # `open`이어야 한다 — 이미 JU인 question으로 게이트를 열면 §6이 그 질문을 본 적이 없다.
    for record in (entry_questions if isinstance(entry_questions, list) else []):
        if not isinstance(record, dict) or record.get("record_id") not in unavailable_questions:
            continue
        if record.get("status") != "open":
            errors.append(
                f"questions {record.get('record_id')}: judgment_unavailable is a §6 outcome, but the §6 "
                f"entry ledger already recorded status {record.get('status')!r} -- the question must "
                "enter verification open"
            )
    if not isinstance(entry_findings, list):
        return
    entry_status = {
        r.get("record_id"): r.get("status") for r in entry_findings if isinstance(r, dict)
    }
    for record_id in sorted(unavailable):
        status = entry_status.get(record_id)
        if status is None:
            continue  # 진입 시점에 없던 record는 verify gate 결속(unseen)이 이미 잡는다
        if status != "discovered":
            errors.append(
                f"findings {record_id}: judgment_unavailable is an initial-verification outcome, but the §6 "
                f"entry ledger recorded status {status!r} -- a delta verification (§7) that cannot decide "
                "stays applied and blocking; it does not terminate as judgment_unavailable"
            )

def _load_run_registry(run_root: Path | None) -> tuple[dict[str, Any] | None, str | None]:
    """docauth#356: run_root의 state 파일이 있으면 고정 합집합(`load_fixed_union`)을 만든다.
    없으면 (None, None) — 레거시(디스크 경로). 로드 실패는 오류 문자열(fail-closed)."""
    try:
        from .validate_review_intermediate import (load_run_registry)
    except ImportError:
        from validate_review_intermediate import (load_run_registry)

    return load_run_registry(run_root)

def _validate_decision_registry_binding(
    receipt: dict[str, Any], events: list[Any] | None, ledger: dict[str, Any], errors: list[str],
    *, registry: dict[str, Any] | None = None,
) -> None:
    """docauth#352 — 결정 레지스트리 3상태를 관측에 결속한다.

    ⓐ receipt `decision_registry_state` ∈ {checked, absent_unassured, present_unchecked}.
    ⓑ front gate trace에 `decision_registry_recorded` 이벤트가 **정확히 하나** 있고 그 `state`가
       receipt 선언과 일치한다(checked↔checked · absent_unassured↔absent · present_unchecked↔unchecked).
    ⓒ `unassured_mode`는 파생값이다: `checked`면 false, 그 밖이면 true(사람 수용 `unassured_accepted_by`는
       기존 규칙이 요구한다). 불일치는 FAIL — 선언이 관측과 어긋나는 그 자리가 #352의 관측이었다.
    ⓓ `checked`가 아니면 원장 `suppressed`는 비어 있어야 한다(§1 ② ⓐ 억제 금지). `checked`면 모든
       `suppressed[].authority_ref`(kind decision_registry)의 sha256이 게이트가 검증한 레지스트리 바이트와
       같아야 한다 — 다른 판본의 레지스트리로 억제하고 게이트에는 이 판본을 낸 조합을 막는다.
    """
    state = receipt.get("decision_registry_state")
    if state not in DECISION_REGISTRY_STATES:
        errors.append(
            "decision_registry_state must be checked, absent_unassured, or present_unchecked (#352)"
        )
        return
    unassured = receipt.get("unassured_mode", False)
    expected_unassured = state != "checked"
    if isinstance(unassured, bool) and unassured != expected_unassured:
        errors.append(
            f"unassured_mode declares {unassured} but decision_registry_state {state!r} derives "
            f"{expected_unassured} -- unassured_mode is bound to the registry state, not self-declared (#352)"
        )
    suppressed = ledger.get("suppressed") if isinstance(ledger.get("suppressed"), list) else []
    if events is None:
        return  # trace 자체의 문제는 이미 보고됐다
    registry_events = [
        e for e in events if isinstance(e, dict) and e.get("event") == DECISION_REGISTRY_EVENT
    ]
    if len(registry_events) != 1:
        errors.append(
            f"front gate trace must record the decision registry state exactly once "
            f"(found {len(registry_events)}) -- run review_front_gate.py with --decisions | "
            "--decisions-absent | --decisions-unchecked (#352)"
        )
        return
    event = registry_events[0]
    if event.get("phase") != "pre_lens":
        errors.append("decision registry must be recorded pre_lens (#352)")
    # Codex 구현 r1-01: 이벤트가 **입력 게이트 뒤·첫 렌즈 앞**에 있어야 한다 — trace 끝에 붙인 이벤트를
    # 받으면 재계산 prefix 비교가 그 이벤트를 보지 못한다.
    names = [e.get("event") if isinstance(e, dict) else None for e in events]
    gate_index = names.index("input_gate_recorded") if "input_gate_recorded" in names else -1
    registry_index = names.index(DECISION_REGISTRY_EVENT)
    lens_indexes = [i for i, name in enumerate(names) if name == "lens_started"]
    if gate_index < 0 or registry_index < gate_index or (lens_indexes and registry_index > min(lens_indexes)):
        errors.append(
            "decision registry event must sit after input_gate_recorded and before the first lens_started "
            "(#352 -- an event appended later is outside what the gate recorded pre-lens)"
        )
    if event.get("state") != DECISION_REGISTRY_STATES[state]:
        errors.append(
            f"decision_registry_state {state!r} contradicts the front gate trace "
            f"({event.get('state')!r}) -- the declaration is bound to what the gate recorded (#352)"
        )
        return
    if state != "checked":
        if suppressed:
            errors.append(
                f"decision_registry_state {state!r} forbids suppression, but the ledger carries "
                f"{len(suppressed)} suppressed record(s) (§1 ② ⓐ, #352)"
            )
        return
    if event.get("validated") is not True or not isinstance(event.get("sha256"), str):
        errors.append("checked registry event must carry validated: true and the registry sha256 (#352)")
        return
    # docauth#356 §1-4: 고정 합집합의 재계산 결속 — 이벤트가 찍은 union_sha256 == 아카이브에서 로더가 다시 계산한 값.
    # 구형(0.32) 이벤트에는 union_sha256가 없다 — 그 실행은 루트 sha 결속(아래)만으로 검증한다(r1-04).
    if registry is not None and registry.get("files") and "union_sha256" in event:
        if event.get("union_sha256") != registry.get("union_sha256"):
            errors.append(
                "decision_registry_recorded.union_sha256 does not match the union recomputed from the archived "
                "registry files -- the gate-fixed union is not what the trace claims (#356)"
            )
    for index, record in enumerate(suppressed):
        ref = record.get("authority_ref") if isinstance(record, dict) else None
        if not isinstance(ref, dict) or ref.get("kind") != "decision_registry":
            continue  # approved_docmodel 억제는 §2의 다른 권위(자체 검사는 원장 검증이 한다)
        # docauth#356: 합집합이면 인용 sha는 이벤트 `files[]`의 어느 파일 sha와 같아야 한다(포함 파일의 결정으로
        # 억제 가능). 파일↔경로↔decision_id의 정확한 결속은 원장 검증(`_validate_authority_ref`, registry=)이 한다.
        gate_shas = {f.get("sha256") for f in (event.get("files") or []) if isinstance(f, dict)} or {event.get("sha256")}
        if ref.get("sha256") not in gate_shas:
            errors.append(
                f"suppressed[{index}].authority_ref.sha256 does not match the registry the front gate "
                "validated -- suppression must lean on the exact registry version this run checked (#352)"
            )

def _validate_v2_modern(receipt: dict[str, Any], packet_root: Path, expected_packet_binding: dict[str, Any]) -> list[str]:
    errors: list[str] = []
    required = {
        "schema_version", "route_id", "route_trace", "snapshot_id", "target", "verifiers",
        "input_gate", "classification_ledger_ref", "packet_binding", "docloop_contract_version", "findings", "questions", "drifts",
        # #228②: pre-lens front gate trace, digest-bound. #233: structure axis judged
        # or explicitly not. #238: §0.2's confirmed N and its reason. #229②: §0.2's
        # pre-execution scale disclosure, machine-bound to the executed configuration.
        # #208 제안3: §1 ⑨ prior-round declaration, bound to actual round-comparison proof.
        "front_gate_ref", "structure_axis", "execution", "scale_disclosure", "round_context",
        # docauth#349 ④: 앵커 가드를 돌린 대상(계약 §3 — 전달본). 무엇에 돌렸는지를
        # 실행자가 고를 수 있으면서 그 선택이 판정문에 남지 않던 자리를 닫는다.
        "anchor_guard_ref",
        # docauth#369: 실행 완료 / 문서 승인 가능 축의 선언(검증기가 재계산해 대조).
        "execution_status", "document_clearance",
        # docauth#352: 결정 레지스트리 3상태 선언(front gate trace의 이벤트에 결속).
        "decision_registry_state",
    }
    optional = {
        "unassured_mode", "unassured_accepted_by",
        # required only conditionally — see _validate_structure_axis / _validate_revision_during_run
        "structure_axis_reason", "revision_during_run",
        # docauth#348: §6 진입 게이트의 trace 결속. §7 검증 유예 실행에서는 §6이
        # 아예 열리지 않으므로 **있으면 안 되고**, 그 밖의 모든 실행에서는
        # **필수**다 — 조건부 판정은 _validate_verify_gate_presence가 한다.
        "verify_gate_ref",
        # docauth#315: present only when structure_axis was judged via direct docmodel
        # lookup (§1 input⑤) — see _validate_structure_axis / _validate_front_gate_binding.
        "structure_axis_docmodel_ref",
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
    repo_root = packet_root
    path = packet_root
    _validate_packet_binding(receipt, expected_packet_binding, errors)
    if type(receipt.get("docloop_contract_version")) is not int or receipt["docloop_contract_version"] != 2:
        errors.append("new packets require docloop_contract_version integer 2")
    if receipt.get("snapshot_id") != expected_packet_binding.get("target_snapshot") or receipt.get("target") != expected_packet_binding.get("target_source"):
        errors.append("receipt target/snapshot must match prepared packet")
    if not isinstance(input_gate, dict) or input_gate.get("run_root") != ".":
        errors.append("input_gate.run_root must be the prepared packet itself")

    # #228③: the receipt no longer just carries the ⑧ source_copy hash as a claim —
    # it re-hashes the archived bytes in the run folder it points to (`run_root`).
    run_root_path: Path | None = None
    if isinstance(input_gate, dict):
        if repo_root is None:
            errors.append("cannot locate repository root for input_gate.run_root")
        else:
            run_root_path, run_root_errors = _resolve_packet_relative(
                repo_root, input_gate.get("run_root"), "input_gate.run_root"
            )
            errors.extend(run_root_errors)
            if run_root_path is not None:
                errors.extend(verify_source_copy_bytes(input_gate, run_root_path, label="input_gate"))

    # #228②: bind the receipt's own editing_state/target_maturity/source_copy
    # declaration to the digest-verified pre-lens trace, so a receipt cannot declare
    # something the front gate never actually recorded before lenses ran. Fenced to
    # the same run_root as the archived copy (Codex r2-01) when run_root resolved.
    events = _resolve_front_gate_trace(
        path, receipt.get("front_gate_ref"), errors, run_root=run_root_path
    )
    if events is not None:
        _validate_front_gate_binding(receipt, events, errors)
        # docauth#299: the binding above only proves the receipt agrees with the
        # trace file -- not that the trace file is real. If the gate archived its
        # own inputs, replay them and require the same result.
        if run_root_path is not None:
            _validate_front_gate_recomputation(events, run_root_path, errors)

    # docauth#290: prior_round_output_round_no is digest-bound above (Codex r3-01), so
    # the *number* cannot be rewritten after the gate recorded it -- but nothing checked
    # that output_ref.path/.sha256 actually name a real file until now. Without this, a
    # receipt could reference a prior-round output that does not exist, or whose bytes
    # were tampered with, and pass. Same evidentiary standard classification_ledger_ref/
    # front_gate_ref/round_context.comparison_ref already get.
    if isinstance(input_gate, dict):
        prior_round_for_output = input_gate.get("prior_round")
        output_ref = (
            prior_round_for_output.get("output_ref")
            if isinstance(prior_round_for_output, dict)
            else None
        )
        if isinstance(output_ref, dict):
            if repo_root is None:
                errors.append(
                    "cannot locate repository root for input_gate.prior_round.output_ref"
                )
            else:
                output_path, output_path_errors = _resolve_packet_relative(
                    repo_root,
                    output_ref.get("path"),
                    "input_gate.prior_round.output_ref.path",
                )
                errors.extend(output_path_errors)
                if output_path is not None:
                    output_payload, read_error = _read_packet_file_bytes(
                        output_path, "input_gate.prior_round.output_ref"
                    )
                    if read_error is not None:
                        errors.append(read_error)
                    else:
                        if hashlib.sha256(output_payload).hexdigest() != output_ref.get("sha256"):
                            errors.append(
                                "input_gate.prior_round.output_ref.sha256 does not match "
                                "the referenced file's bytes"
                            )

    _validate_structure_axis(receipt, errors)
    _validate_execution(receipt.get("execution"), errors)
    _validate_scale_disclosure(receipt, errors)
    _validate_round_context(receipt, path, errors)

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

    # docauth#348: 같은 결속을 §4 → §6 경계에도 건다. 이 블록이 없으면
    # `review_verify_gate.py`는 "부르면 좋은 스크립트"에 그치고, 부르지 않은
    # 실행과 부른 실행이 done 시점에 구별되지 않는다 — 계약이 여러 번 경고한
    # "무발화는 고지의 증명이 아니다"가 게이트 자신에게서 발화하는 자리다.
    verify_gate_ref = receipt.get("verify_gate_ref")
    verify_root = None
    if isinstance(verify_gate_ref, dict):
        verify_root = _result_attempt(packet_root, verify_gate_ref.get("path"), VERIFY_GATE_TRACE_CANONICAL_RELPATH, errors)
    if deferred:
        if "verify_gate_ref" in receipt:
            errors.append(
                "verification is deferred (§7): verify_gate_ref must be omitted until "
                "the §6 verification phase actually opens (#348)"
            )
    elif "verify_gate_ref" not in receipt:
        errors.append("missing fields: verify_gate_ref")
    else:
        verify_events = _resolve_verify_gate_trace(
            path, verify_gate_ref, errors, run_root=verify_root
        )
        if verify_events is not None:
            _validate_verify_gate_binding(receipt, verify_events, receipt_record_ids, errors)
            if verify_root is not None:
                _validate_verify_gate_recomputation(
                    verify_events, verify_root, errors, repo_root=repo_root
                )

    ref = receipt.get("classification_ledger_ref")
    ledger = _resolve_ledger(path, ref, errors)
    if isinstance(ref, dict) and ledger is not None:
        if type(ref.get("schema_version")) is not int or ref["schema_version"] not in (1, 2) or ref["schema_version"] != ledger.get("schema_version"):
            errors.append("classification_ledger_ref.schema_version must match the ledger's integer schema version")
    resolved_ledger_path = resolve_packet_file(packet_root, ref.get("path"), "ledger", errors) if isinstance(ref, dict) else None
    # docauth#349 ④ r2-02: 앵커 가드 trace를 **이 receipt가 결속한 원장**과 대조해야 하므로
    # 원장 해결 뒤에 부른다.
    _validate_anchor_guard_ref(
        receipt,
        errors,
        repo_root=repo_root,
        run_root=run_root_path,
        ledger_path=resolved_ledger_path,
        ledger_sha256=(ref.get("sha256") if isinstance(ref, dict) and ledger is not None else None),
    )
    if isinstance(ref, dict) and ref.get("snapshot_id") != receipt.get("snapshot_id"):
        errors.append("classification_ledger_ref.snapshot_id must match receipt snapshot_id")
    if ledger is None:
        return errors
    # docauth#356 §1-4 (설계 r3-04): 최종 원장 검증도 게이트가 고정한 합집합을 받는다 — verify 원장 아카이브
    # 재검증(위)과 **같은** registry. state 파일이 있는 실행에서 디스크 레지스트리는 읽지 않는다.
    registry, registry_error = _load_run_registry(run_root_path)
    if registry_error is not None:
        errors.append(registry_error)
    errors.extend(
        f"ledger: {error}"
        for error in validate_intermediate_data(
            ledger, require_closed=not deferred, packet_root=repo_root, registry=registry
        )
    )
    if not deferred and verify_root is not None:
        _validate_verified_candidates(ledger, verify_root, errors)
    if ledger.get("snapshot_id") != receipt.get("snapshot_id"):
        errors.append("ledger snapshot_id must match receipt snapshot_id")
    if ledger.get("target") != receipt.get("target") or not _nonempty(receipt.get("target")):
        errors.append("ledger target must match nonempty receipt target")

    ledger_records: dict[str, tuple[str, dict[str, Any]]] = {}
    for category, collection_name in PUBLIC_COLLECTIONS.items():
        for record in ledger.get(collection_name, []):
            if isinstance(record, dict) and _nonempty(record.get("record_id")):
                ledger_records[record["record_id"]] = (category, record)
    _validate_decision_registry_binding(receipt, events, ledger, errors, registry=registry)

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
            # docauth#349 ①: **status는 immutable projection에 없다** — finding의
            # projection이 `status`를 일부러 뺀 것은 처분이 리뷰 진행 중 정당하게
            # 바뀌기 때문이고(§8 생애주기), 그 결과 receipt와 원장이 서로 다른 처분을
            # 적어도 digest가 일치해 통과했다. 2026-09-07 실행에서 원장은 23건을
            # `verified`로, 전달본은 같은 23건을 `discovered`로 적고 있었다 — 그 원장을
            # receipt에 결속했다면 **미처분 결함 23건이 done으로 집계**됐을 것이고,
            # 그 방향은 §8 위반의 반대편(막혀야 할 것이 통과)이다. 기계가 하나도 잡지
            # 못했고 사람 검증자 3인만 잡았다. 두 산출물이 같은 실행에 대해 서로 다른
            # 사실을 말하는 것은 어느 쪽이 맞든 결함이다.
            if record.get("status") != ledger_entry[1].get("status"):
                errors.append(
                    f"{prefix}.status contradicts the closed ledger "
                    f"({record.get('status')!r} vs {ledger_entry[1].get('status')!r})"
                )
            if category == "question":
                receipt_verification = record.get("classification_verification")
                ledger_verification = ledger_entry[1].get("classification_verification")
                receipt_result = (
                    receipt_verification.get("result")
                    if isinstance(receipt_verification, dict)
                    else None
                )
                ledger_result = (
                    ledger_verification.get("result")
                    if isinstance(ledger_verification, dict)
                    else None
                )
                if receipt_result != ledger_result:
                    errors.append(
                        f"{prefix}.classification_verification.result contradicts the closed "
                        f"ledger ({receipt_result!r} vs {ledger_result!r})"
                    )
            # docauth#369: done(exit 0)은 여전히 `rejected|verified` / `resolved`뿐이지만,
            # 구조 검사는 terminal 집합(judgment_unavailable 포함)으로 통과시킨다 — 그 receipt는
            # exit 0이 아니라 exit 5(COMPLETE-INDETERMINATE)로 종결한다(_evaluate 참고).
            if not deferred and category == "finding" and record.get("status") not in TERMINAL_FINDING_STATUSES:
                errors.append(
                    f"{prefix}.status must be terminal (rejected, verified, or judgment_unavailable) "
                    "for a complete execution"
                )
            if not deferred and category == "question" and record.get("status") not in TERMINAL_QUESTION_STATUSES:
                errors.append(
                    f"{prefix}.status must be terminal (resolved or judgment_unavailable) for a complete execution"
                )
            if deferred and record.get("status") == JUDGMENT_UNAVAILABLE:
                errors.append(
                    f"{prefix}.status judgment_unavailable requires the §6 verification phase, which a "
                    "deferred (§7) receipt has not opened -- no attempt can have been made"
                )
            # docauth#369: 하위 블록은 immutable projection 밖(§6 산출물)이라 digest가 잡지 않는다 —
            # `status`와 같은 이유로 receipt↔원장 교차 검사를 직접 한다(#349 ①의 확장).
            if record.get("judgment_unavailable") != ledger_entry[1].get("judgment_unavailable"):
                errors.append(
                    f"{prefix}.judgment_unavailable contradicts the closed ledger record"
                )
            if category == "question":
                verification = record.get("classification_verification")
                if not isinstance(verification, dict) or set(verification) != {"result", "verifier_id", "evidence"}:
                    errors.append(f"{prefix}.classification_verification has invalid shape")
                else:
                    if not deferred and record.get("status") == JUDGMENT_UNAVAILABLE:
                        if verification.get("result") != "unresolved":
                            errors.append(
                                f"{prefix}.classification_verification.result must stay unresolved for a "
                                "judgment_unavailable question"
                            )
                        # Codex 구현 r1-02: 원장은 attempts[0] ≡ classification_verification을 강제하지만
                        # verifier_id·evidence는 digest 밖이라 receipt 사본만 다르게 적을 수 있었다.
                        block = record.get("judgment_unavailable")
                        attempts = block.get("attempts") if isinstance(block, dict) else None
                        first = attempts[0] if isinstance(attempts, list) and attempts and isinstance(attempts[0], dict) else {}
                        if any(verification.get(k) != first.get(k) for k in ("verifier_id", "result", "evidence")):
                            errors.append(
                                f"{prefix}.classification_verification must equal judgment_unavailable."
                                "attempts[0] (verifier_id, result, evidence) -- one verification fact, not two"
                            )
                    elif not deferred and verification.get("result") not in {"pass", "kill"}:
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
        _validate_open_item_classification(receipt, input_gate, ledger_records, repo_root)
    )
    _validate_execution_axes(receipt, errors, deferred=deferred)
    if not deferred and verify_root is not None:
        _validate_judgment_unavailable_entry_status(receipt, verify_root, errors)
    return errors

def _validate_verified_candidates(final: dict[str, Any], verify_root: Path, errors: list[str]) -> None:
    """Preserve verified claims, counter-evidence and nonpublic dispositions."""
    raw, read_error = _read_packet_file_bytes(
        verify_root / VERIFY_GATE_LEDGER_CANONICAL_RELPATH, "verified candidate archive"
    )
    if read_error:
        errors.append(read_error)
        return
    try:
        archive = load_yaml_text(raw)
    except (ValueError, yaml.YAMLError) as exc:
        errors.append(f"cannot read verified candidate archive: {exc}")
        return
    entry = archive.get(ROOT_KEY) if isinstance(archive, dict) else None
    if not isinstance(entry, dict):
        errors.append("verified candidate archive is missing its ledger")
        return
    if type(entry.get("schema_version")) is not type(final.get("schema_version")) or entry.get("schema_version") != final.get("schema_version"):
        errors.append("ledger schema_version differs from the verified entry")
    for collection, identifier in (("source_candidate_inventory", "source_candidate_id"),
                                   ("candidate_atoms", "candidate_atom_id"),
                                   ("counter_evidence", "record_id"),
                                   ("suppressed", "record_id"),
                                   ("nonissues", "record_id"),
                                   ("classification_ledger", "candidate_atom_id")):
        indexes = []
        for ledger in (entry, final):
            rows = ledger.get(collection, [])
            if not isinstance(rows, list):
                indexes.append(None)
                continue
            indexed = {row[identifier]: row for row in rows
                       if isinstance(row, dict) and isinstance(row.get(identifier), str)}
            indexes.append(indexed if len(indexed) == len(rows) else None)
        if None in indexes or indexes[0] != indexes[1]:
            errors.append(f"{collection} differs from the verified entry claims; run verification again")


def _result_attempt(packet_root: Path, relative: Any, filename: str, errors: list[str]) -> Path | None:
    path = resolve_packet_file(packet_root, relative, "result attempt", errors)
    if path is None:
        return None
    parts = Path(relative).parts
    if len(parts) != 3 or parts[0] != "results" or parts[-1] != filename:
        errors.append("result ref must name results/<attempt>/" + filename)
        return None
    marker = resolve_packet_file(packet_root, str(Path(relative).parent / "COMPLETE.json"), "attempt completion", errors)
    if marker is None:
        return None
    raw, error = _read_packet_file_bytes(marker, "attempt completion")
    if error:
        errors.append(error)
        return None
    try:
        complete = json.loads(raw, object_pairs_hook=_reject_duplicate_json)
    except (ValueError, TypeError):
        errors.append("invalid attempt completion marker")
        return None
    if complete.get("state") != "complete" or complete.get("artifact") != filename:
        errors.append("incomplete or wrong-kind result attempt")
        return None
    return path.parent


def _frontmatter(path: Path) -> dict[str, Any]:
    return parse_receipt_bytes(path.read_bytes())


def parse_receipt_bytes(raw: bytes) -> dict[str, Any]:
    """Parse the same captured receipt bytes used for validation and import."""
    text = raw.decode("utf-8")
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
    if not isinstance(ref, dict) or set(ref) not in ({"path", "sha256", "snapshot_id"}, {"path", "sha256", "snapshot_id", "schema_version"}):
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
        payload, read_error = _read_packet_file_bytes(ledger_path, "classification ledger")
        if read_error:
            raise ValueError(read_error)
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


def _read_packet_file_bytes(path: Path, label: str) -> tuple[bytes | None, str | None]:
    """Read ``path`` through a single O_NOFOLLOW|O_NONBLOCK fd (docauth#290 r1-01, Codex).

    Mirrors validate_docmodel_approvals.py's ``_read_verified_docmodel``: fstat and
    read both run on the one descriptor a successful open() returned, so nothing
    after that open() can substitute the bytes being hashed, and O_NONBLOCK keeps
    a FIFO from hanging the open() itself instead of being rejected by the
    S_ISREG check right after.
    """
    try:
        fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    except OSError as exc:
        return None, f"cannot read {label}: {exc}"
    try:
        fd_stat = os.fstat(fd)
        if not stat.S_ISREG(fd_stat.st_mode):
            return None, f"{label} must be a regular file"
        chunks: list[bytes] = []
        while True:
            chunk = os.read(fd, 1024 * 1024)
            if not chunk:
                break
            chunks.append(chunk)
        return b"".join(chunks), None
    except OSError as exc:
        return None, f"cannot read {label}: {exc}"
    finally:
        os.close(fd)


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


#: docauth#296: front_gate_ref must always name this one file, relative to
#: input_gate.run_root -- matches what runner.py's `prepare` already writes.
FRONT_GATE_TRACE_CANONICAL_RELPATH = "deterministic/FRONT_GATE_TRACE.json"


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
        # docauth#296: "inside run_root" alone still lets a receipt point at ANY
        # file within run_root -- including one an attacker (or a stale trace left
        # over from mid-run tooling) planted there -- since nothing besides digest
        # self-consistency was ever checked. Pinning the path to one fixed,
        # non-declarable location closes the "point elsewhere in run_root" evasion:
        # every producer of a real trace (runner.py's prepare) writes to the same
        # conventional filename, so a validator reading front_gate_ref no longer
        # trusts the receipt's own choice of filename. This does NOT defend against
        # an attacker who overwrites that one canonical file with forged content --
        # the same class of risk the receipt itself is already exposed to, and out
        # of scope for this fix (see docauth#296's own scope note: it needs a real
        # root-of-trust mechanism, which this is not).
        if trace_path != run_root / FRONT_GATE_TRACE_CANONICAL_RELPATH:
            errors.append(
                f"front_gate_ref.path must be {FRONT_GATE_TRACE_CANONICAL_RELPATH!r} "
                "relative to input_gate.run_root"
            )
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

    Closes the path #223 named as a residual: an execution could record the front
    gate's pre-lens ``editing_state``/``target_maturity`` honestly and then have its
    done receipt independently declare something more convenient (``frozen`` /
    ``complete``) with nothing to check the two against each other.
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
    # Codex r1-01: a trace whose sole `input_gate_recorded` event lacks the shape
    # `review_front_gate.py` always emits (pre-lens phase, a byte-verified copy) is
    # not the record #228② means to bind to — check the event's own shape, not just
    # its agreement with the receipt (which a fabricated event could trivially match).
    if event.get("phase") != "pre_lens":
        errors.append("front gate trace input_gate_recorded event must have phase pre_lens")
    if event.get("source_copy_verified") is not True:
        errors.append("front gate trace input_gate_recorded event must have source_copy_verified: true")
    input_gate = receipt.get("input_gate") if isinstance(receipt.get("input_gate"), dict) else {}
    source_copy = input_gate.get("source_copy") if isinstance(input_gate.get("source_copy"), dict) else {}
    prior_round = input_gate.get("prior_round") if isinstance(input_gate.get("prior_round"), dict) else {}
    prior_round_output_ref = prior_round.get("output_ref") if isinstance(prior_round.get("output_ref"), dict) else {}
    open_items = input_gate.get("open_items") if isinstance(input_gate.get("open_items"), dict) else {}
    bound = (
        ("editing_state", input_gate.get("editing_state")),
        ("target_maturity", input_gate.get("target_maturity")),
        ("source_copy_sha256", source_copy.get("sha256")),
        # #208 제안3 (Codex r1-02): without this, a receipt could flip
        # prior_round.exists after the front gate recorded it — false→true to
        # fabricate a comparison_ref requirement nobody checked at gate time, or
        # true→false to shed the comparison_ref obligation entirely. Same digest
        # protection editing_state/target_maturity already get.
        ("prior_round_exists", prior_round.get("exists")),
        # Codex r3-01: exists alone isn't enough — round_context.round_label's
        # arithmetic depends on output_ref.round_no, so a receipt that rewrote
        # round_no after the gate recorded it could pick any round_label to match
        # (e.g. round 1 → round 99, round_label r100). Bind the number the
        # arithmetic actually depends on.
        ("prior_round_output_round_no", prior_round_output_ref.get("round_no")),
        # docauth#293: round_no digest-binding (above) only stops round_label forgery —
        # nothing bound *which file* output_ref.path/.sha256 name to what the front gate
        # recorded. #292's file-existence/hash check below only proves the referenced
        # file is real and internally self-consistent, not that it is the file the gate
        # actually pointed at; a receipt could swap in any other real, correctly-hashed
        # file from the packet after the gate ran. Same minimal-binding precedent as
        # prior_round_output_round_no, applied to the two fields that check verifies.
        ("prior_round_output_ref_path", prior_round_output_ref.get("path")),
        ("prior_round_output_ref_sha256", prior_round_output_ref.get("sha256")),
        # docauth#290: the trace already emits open_items_ledger_ref (#206), but
        # nothing compared it to the receipt's own declaration — a receipt could
        # rewrite input_gate.open_items.ledger_ref to `none` (or a different file)
        # after the gate recorded it, silently dropping the ledger the
        # open-items-cannot-be-suppression-authority check
        # (_validate_open_item_classification) depends on to identify a match.
        ("open_items_ledger_ref", open_items.get("ledger_ref")),
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
    # docauth#315 (PLAN §3.6, Codex r1-01 "symmetric closure"): #233 above closes one
    # direction -- trace says undetermined, receipt cannot claim otherwise. The new
    # direct-docmodel path opens a second one that needs the SAME closure: a receipt
    # cannot claim structure_axis_docmodel_ref backing unless the trace actually carries
    # the matching judged-via-direct-docmodel event. Without this, #233's own event
    # (convention_profile_not_applicable) not firing would leave the receipt free to
    # self-report a docmodel_ref with no trace evidence behind it at all -- exactly the
    # "trace honestly says one thing, receipt claims another" attack #233 was built to
    # close, just via a path #233 itself doesn't gate.
    # Codex 피어리뷰 r1-02: look ONLY at the first event, not "anywhere in the
    # trace." preflight() always emits its own event as events[0] (before
    # record_input_gate/lens events) in every legitimate run this repo produces --
    # and #299's recomputation only ever verifies a PREFIX of the stored trace
    # (`_validate_front_gate_recomputation`'s `recomputed != events[:len(recomputed)]`),
    # never the tail (that's where record_candidate_questions' intermediate-derived
    # events live, out of #299's scope by design). An unscoped `any()`/`next()` search
    # here would let a forged structure_axis_judged_via_direct_docmodel event appended
    # AFTER a legitimately-reproducible undetermined prefix satisfy this binding check
    # without ever being covered by recomputation -- unlike #233's own unscoped search
    # just above (which only ever RESTRICTS a receipt to undetermined, never permits
    # judged), this check's job is exactly the permissive direction, so it cannot
    # afford to trust an event's mere presence anywhere in the list.
    first_event = events[0] if events and isinstance(events[0], dict) else None
    judged_via_docmodel = (
        first_event
        if first_event is not None
        and first_event.get("event") == "structure_axis_judged_via_direct_docmodel"
        else None
    )
    receipt_docmodel_ref = receipt.get("structure_axis_docmodel_ref")
    # A receipt that stays conservative (structure_axis: undetermined, no
    # structure_axis_docmodel_ref) despite a judged-eligible trace event is not an
    # error -- §3.2/§3.6 make "judged" available, never mandatory. Binding only fires
    # when the receipt actually claims the ref.
    if receipt_docmodel_ref is not None:
        if judged_via_docmodel is None:
            errors.append(
                "receipt declares structure_axis_docmodel_ref but front gate trace has no "
                "structure_axis_judged_via_direct_docmodel event (#315)"
            )
        else:
            # Only sha256 is trace-bound. The trace event has no `path` (a repo path
            # would differ between the live original-run location and the run_root
            # archive location #299 recomputation replays from — see
            # review_front_gate.py's preflight() docstring). A receipt MAY still cite
            # `path` for human readability; that field is informational, not checked
            # against the trace.
            trace_ref = judged_via_docmodel.get("structure_axis_docmodel_ref")
            trace_ref = trace_ref if isinstance(trace_ref, dict) else {}
            receipt_ref = receipt_docmodel_ref if isinstance(receipt_docmodel_ref, dict) else {}
            trace_sha256 = trace_ref.get("sha256")
            receipt_sha256 = receipt_ref.get("sha256")
            # Codex 피어리뷰 r4-01: equality alone lets two malformed sides agree --
            # `{}`/`{"sha256": null}` on both sides satisfies `None == None` while
            # asserting nothing. Judged evidence is only meaningful if the value is
            # an actual content hash, so require the SHAPE to be a real sha256
            # before the equality check can mean anything.
            if not (isinstance(trace_sha256, str) and RE_SHA256.fullmatch(trace_sha256)):
                errors.append(
                    "front gate trace structure_axis_docmodel_ref.sha256 must be a "
                    "sha256 hex digest (#315)"
                )
            if not (isinstance(receipt_sha256, str) and RE_SHA256.fullmatch(receipt_sha256)):
                errors.append(
                    "receipt structure_axis_docmodel_ref.sha256 must be a sha256 hex "
                    "digest (#315)"
                )
            elif trace_sha256 != receipt_sha256:
                errors.append(
                    f"front gate trace structure_axis_docmodel_ref.sha256="
                    f"{trace_sha256!r} does not match receipt "
                    f"structure_axis_docmodel_ref.sha256 ({receipt_sha256!r}) "
                    "(#315)"
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
        # docauth#315: a receipt cannot claim direct-docmodel backing while also
        # declaring the axis undetermined -- that combination is nonsensical (the ref
        # only means anything as the evidence for "judged"). Trace-binding of the ref
        # itself (does it match a real event) is a separate check in
        # _validate_front_gate_binding; this is purely receipt-internal consistency.
        if "structure_axis_docmodel_ref" in receipt:
            errors.append(
                "structure_axis_docmodel_ref must be omitted when structure_axis is undetermined (#315)"
            )
    else:
        if "structure_axis_reason" in receipt:
            errors.append("structure_axis_reason must be omitted when structure_axis is judged")
        # Codex 피어리뷰 r3-02: a bare presence check (`in receipt`) lets an explicit
        # `structure_axis_docmodel_ref: null` slip through as "present" -- the field
        # would then read as malformed evidence (a claim with no content) rather than
        # cleanly absent. When present at all, it must actually be a mapping.
        if "structure_axis_docmodel_ref" in receipt and not isinstance(
            receipt.get("structure_axis_docmodel_ref"), dict
        ):
            errors.append("structure_axis_docmodel_ref must be a mapping when present (#315)")


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

    # docauth#290: prior_round_output_round_no is digest-bound above (front gate
    # binding), so the *number* cannot be rewritten after the gate recorded it --
    # but nothing checked that output_ref.path/.sha256 actually name a real file
    # until now. Without this, a receipt could reference a prior-round output that
    # does not exist, or whose bytes were tampered with, and pass. Same evidentiary
    # standard classification_ledger_ref/front_gate_ref/round_context.comparison_ref
    # already get. Reads through the fd-anchored helper (Codex r1-01) so a FIFO in
    # place of the file cannot hang validation the way a plain read_bytes() would.
    if isinstance(input_gate, dict):
        prior_round_for_output = input_gate.get("prior_round")
        output_ref = (
            prior_round_for_output.get("output_ref")
            if isinstance(prior_round_for_output, dict)
            else None
        )
        if isinstance(output_ref, dict):
            output_path, output_path_errors = _resolve_packet_relative(
                packet_root,
                output_ref.get("path"),
                "input_gate.prior_round.output_ref.path",
            )
            errors.extend(output_path_errors)
            if output_path is not None:
                output_payload, read_error = _read_packet_file_bytes(
                    output_path, "input_gate.prior_round.output_ref"
                )
                if read_error is not None:
                    errors.append(read_error)
                else:
                    if hashlib.sha256(output_payload).hexdigest() != output_ref.get("sha256"):
                        errors.append(
                            "input_gate.prior_round.output_ref.sha256 does not match "
                            "the referenced file's bytes"
                        )

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
    *,
    receipt_bytes: bytes | None = None,
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
        receipt = parse_receipt_bytes(receipt_bytes) if receipt_bytes is not None else _frontmatter(receipt_path)
    except (OSError, ValueError, yaml.YAMLError) as exc:
        return [str(exc)]
    version = receipt.get("schema_version")
    if type(version) is int and version == 1:
        return [LEGACY_MESSAGE]
    if type(version) is not int or version != 2:
        return ["schema_version must be 1 or 2"]
    raw_run, run_error = _read_packet_file_bytes(packet_root / "RUN.yaml", "RUN manifest")
    if run_error:
        return [run_error]
    try:
        run = load_yaml_text(raw_run)
    except (ValueError, yaml.YAMLError) as exc:
        return [str(exc)]
    contract = run.get("schema_version") if isinstance(run, dict) else None
    if type(contract) is not int or contract not in (1, 2):
        return ["unsupported prepared RUN schema version"]
    if contract == 2:
        try:
            from .runner import _validate_prepared_packet, GateError
        except ImportError:
            from runner import _validate_prepared_packet, GateError
        try:
            checked_root, run, complete = _validate_prepared_packet(packet_root)
        except (GateError, OSError, ValueError, RuntimeError) as exc:
            return [f"prepared packet validation failed: {exc}"]
        errors = _validate_v2_modern(receipt, checked_root, expected_packet_binding)
    else:
        if "docloop_contract_version" in receipt:
            return ["legacy packet cannot claim a modern contract"]
        errors = _validate_v2(receipt, packet_root, expected_packet_binding)
    deferred = defers_verification(receipt.get("input_gate"))
    if deferred and not errors:
        return [DEFERRED_MESSAGE]
    if contract == 2 and has_judgment_unavailable(receipt) and not errors:
        return [INDETERMINATE_MESSAGE]
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
        run_path = args.packet_root / "RUN.yaml"
        if run_path.exists():
            try:
                run = load_yaml_text(run_path.read_bytes())
            except (OSError, ValueError, yaml.YAMLError):
                print("FAIL: cannot read legacy packet contract", file=sys.stderr)
                return 1
            if not isinstance(run, dict) or type(run.get("schema_version")) is not int or run["schema_version"] != 1:
                print("FAIL: --legacy requires a schema-1 packet", file=sys.stderr)
                return 1
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
    if errors == [INDETERMINATE_MESSAGE]:
        print(f"COMPLETE-INDETERMINATE: {INDETERMINATE_MESSAGE}")
        return 5
    if errors:
        for error in errors:
            print(f"FAIL: {error}", file=sys.stderr)
        return 1
    print("OK: review-gate done receipt is current and complete")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
