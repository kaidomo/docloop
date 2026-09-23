#!/usr/bin/env python3
"""Executable review-gate startup ordering guard and audit trace scaffold."""

from __future__ import annotations

import argparse
from copy import deepcopy
import hashlib
import json
import os
from pathlib import Path
import stat
import sys
import tempfile
from typing import Any

import yaml

try:
    from .validate_convention_intake import (declares_profile_not_applicable, validate_data as validate_intake_data)
except ImportError:
    from validate_convention_intake import (declares_profile_not_applicable, validate_data as validate_intake_data)
try:
    from .validate_convention_profile import (StrictLoader, load_yaml)
except ImportError:
    from validate_convention_profile import (StrictLoader, load_yaml)
try:
    from .validate_input_gate import (defers_verification, validate_block as validate_input_gate_block, verify_source_copy_bytes)
except ImportError:
    from validate_input_gate import (defers_verification, validate_block as validate_input_gate_block, verify_source_copy_bytes)
try:
    from .validate_review_intermediate import (_load as load_intermediate, validate_data as validate_intermediate_data)
except ImportError:
    from validate_review_intermediate import (_load as load_intermediate, validate_data as validate_intermediate_data)
try:
    from .validate_docmodel import (load as load_docmodel_yaml, validate as validate_docmodel)
except ImportError:
    from validate_docmodel import (load as load_docmodel_yaml, validate as validate_docmodel)


def _intermediate_validator():
    """Legacy internal import adapter retained for package callers."""
    return validate_intermediate_data


LENSES = ("L1", "L2", "L3")

#: Codex 피어리뷰 r1-04: `build_trace()`'s `docmodel_path` needs to distinguish "the
#: caller hasn't resolved this yet, please do live resolution" from "the caller
#: already resolved this and got no candidate" -- both would otherwise be `None`.
#: Only the true default (this sentinel) triggers live resolution; an explicit
#: `None` from a caller that already resolved (main(), or recomputation replay when
#: no archive exists) is respected as-is and never re-resolved.
_DOCMODEL_PATH_UNRESOLVED = object()


def _read_verified_docmodel_bytes(path: Path) -> bytes:
    """단일 검증된 읽기(fd-anchored, Codex 피어리뷰 r1-01) — 실패하면 명시적으로
    ``ValueError``를 낸다(호출부가 이미 잡는 예외 종류라 raw traceback으로 새지
    않는다). ``docmodel_match.py``의 ``_read_verified_docmodel``과 같은 헬퍼를 이
    파일에도 복제한다 — 이 레포는 언더스코어 이름을 모듈 간 import하지 않고
    모듈마다 이 작은 헬퍼를 복제하는 관례를 이미 쓰고 있다(``validate_review_result.py``의
    ``_read_repo_file_bytes``가 그 첫 번째 사례).
    """
    try:
        fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    except OSError as exc:
        raise ValueError(f"cannot read docmodel {path}: {exc}") from exc
    try:
        fd_stat = os.fstat(fd)
        if not stat.S_ISREG(fd_stat.st_mode):
            raise ValueError(f"docmodel {path} must be a regular file")
        if fd_stat.st_nlink > 1:
            raise ValueError(f"docmodel {path} must not be a hard link (st_nlink={fd_stat.st_nlink})")
        chunks: list[bytes] = []
        while True:
            chunk = os.read(fd, 1024 * 1024)
            if not chunk:
                break
            chunks.append(chunk)
        return b"".join(chunks)
    except OSError as exc:
        raise ValueError(f"cannot read docmodel {path}: {exc}") from exc
    finally:
        os.close(fd)


#: docauth#352 — §1 ② 결정 레지스트리의 세 상태. 실행자가 게이트에 **선언**하고(플래그) 게이트가
#: 관측(파일 해시·검증기 결과)과 함께 trace에 찍는다. receipt의 `decision_registry_state`·
#: `unassured_mode`는 done 시점에 이 이벤트에 결속된다 — "있는데 안 봄"이 계약에 없어
#: `unassured_mode: false`가 형식상 거짓이 아니던 자리를 닫는다.
DECISION_REGISTRY_STATES = ("checked", "absent", "unchecked")
FRONT_GATE_DECISIONS_CANONICAL_RELPATH = "front_gate_decisions.yaml"
FRONT_GATE_DECISIONS_STATE_CANONICAL_RELPATH = "front_gate_decisions_state.json"
#: docauth#356: 루트+전이적 includes 전부를 파일별로 아카이브하는 폴더(`<n>.yaml`, 0 = 루트). state에
#: `files: [{declared_path, archive, sha256}]`·`union_sha256`가 실린다. 루트 바이트는 종전 위치에도 남긴다.
FRONT_GATE_DECISIONS_CANONICAL_RELDIR = "front_gate_decisions"
DECISION_REGISTRY_EVENT = "decision_registry_recorded"


class FrontGateTrace:
    """Fail-closed state machine at the intake → lens → candidate-question seam."""

    def __init__(self) -> None:
        self.events: list[dict[str, Any]] = []
        self._validated = False
        self._intake_validated = False
        self._input_gate: dict[str, Any] | None = None
        self._run_root: Path | None = None
        self._started_lenses: set[str] = set()
        self._decision_registry_recorded = False

    def _emit(self, event: str, **payload: Any) -> None:
        self.events.append({"sequence": len(self.events) + 1, "event": event, **payload})

    def preflight(
        self,
        intake: Any,
        profile: Any,
        *,
        docmodel_path: Path | None = None,
        docmodel_bytes: bytes | None = None,
    ) -> None:
        """``docmodel_path`` (docauth#315, PLAN §3.1-3.7): an ALREADY-RESOLVED single
        candidate for input ⑤ (direct docmodel lookup), or ``None``. This method never
        scans ``contracts/docmodel.*.yaml`` or the approvals registry itself -- that
        live, repo-state-dependent resolution happens once in ``build_trace()``/
        ``main()`` (original run) via ``resolve_structure_axis_docmodel()``, or is
        replayed from an archived file (done-time recomputation, #299). Kept out of
        this method so its own logic stays a pure function of its two archived inputs
        (this file's bytes + intake's observed_sections) — see the module docstring
        of ``docmodel_match.py`` for why that determinism boundary matters.

        ``docmodel_bytes`` (Codex 피어리뷰 r1-05, optional): when the caller already
        read this file's bytes as part of resolving/approving it (``main()``'s live
        resolution via ``resolve_structure_axis_docmodel()``), pass them here so this
        method hashes the SAME bytes the approval/matching decision was made against
        instead of opening the path a second time -- a second independent open() is a
        TOCTOU window the file could be swapped through between the two reads. When
        omitted (#299 recomputation replay, which has no live candidate object -- only
        an archived path), this method does its own single verified read.
        """
        if self._validated:
            raise RuntimeError("convention intake can run only once")
        errors = validate_intake_data(intake, profile)
        if errors:
            raise ValueError("invalid convention intake: " + "; ".join(errors))
        self._validated = True
        if self.events:
            raise RuntimeError("front-gate preflight may run only once")
        self._intake_validated = True
        if declares_profile_not_applicable(intake):
            # Lenses may start, but the non-applicability and its cost are published:
            # no convention authority exists for this template, so the structure axis
            # (CONTRACT §1 optional input ⑤) is undetermined rather than passed --
            # UNLESS a direct docmodel match was already resolved (below).
            declaration = intake["profile_applicability"]
            observed_sections = list(declaration["observed_sections"])
            if docmodel_path is not None:
                # No `path` field here on purpose: `docmodel_path` points at the LIVE
                # contracts/ file on the original run but at the run_root ARCHIVE copy
                # during #299 recomputation replay -- those are different filesystem
                # locations for the same logical file, so a path string derived from
                # this Path would differ between the two calls and break the replay
                # comparison. `sha256` is the only identity this event needs: it is a
                # strong content fingerprint, and a human wanting the live repo path
                # can cross-reference it against docmodel-approvals.yaml. The receipt
                # (§9, human-authored) MAY still cite a path for readability, but that
                # field is not trace-bound (only sha256 is — see
                # _validate_front_gate_binding).
                raw_bytes = docmodel_bytes if docmodel_bytes is not None else _read_verified_docmodel_bytes(docmodel_path)
                # Codex 피어리뷰 r1-08: a malformed archived/live docmodel (tampered,
                # or a shape validate_docmodel() would reject) must fail closed with a
                # clean error here, not raise a raw AttributeError/TypeError/KeyError
                # later while pulling `section["title"]` out of unexpected shapes --
                # every caller of preflight()/build_trace() only catches
                # (OSError, RuntimeError, ValueError, yaml.YAMLError).
                docmodel_errors = validate_docmodel(docmodel_path, content=raw_bytes)
                if docmodel_errors:
                    raise ValueError(
                        f"invalid docmodel at {docmodel_path}: " + "; ".join(docmodel_errors)
                    )
                docmodel_data = load_docmodel_yaml(docmodel_path, content=raw_bytes)
                titles = sorted(section["title"] for section in docmodel_data["sections"])
                if titles and set(titles).issubset(set(observed_sections)):
                    self._emit(
                        "structure_axis_judged_via_direct_docmodel",
                        phase="pre_lens",
                        profile_id=intake["profile_id"],
                        profile_template_id=profile["template_id"],
                        target_template_id=intake["template_id"],
                        target_snapshot=intake["target_snapshot"],
                        reason=declaration["reason"],
                        observed_sections=observed_sections,
                        structure_axis="judged",
                        docmodel_materialization="refused",
                        structure_axis_docmodel_ref={
                            "sha256": hashlib.sha256(raw_bytes).hexdigest(),
                            # r1-02-style copy discipline: values pinned at emission,
                            # not a live view of the archived file.
                            "matched_section_titles": list(titles),
                        },
                    )
                    return
            self._emit(
                "convention_profile_not_applicable",
                phase="pre_lens",
                profile_id=intake["profile_id"],
                profile_template_id=profile["template_id"],
                target_template_id=intake["template_id"],
                target_snapshot=intake["target_snapshot"],
                reason=declaration["reason"],
                # r1-02: copy, so later mutation of the intake cannot retroactively
                # rewrite an already-emitted declaration in the audit trace.
                observed_sections=list(declaration["observed_sections"]),
                structure_axis="undetermined",
                docmodel_materialization="refused",
            )
            return
        unanswered = [
            record["question_id"]
            for record in intake["records"]
            if record["approval"] == "unanswered"
        ]
        self._emit(
            "convention_intake_validated",
            phase="pre_lens",
            profile_id=intake["profile_id"],
            target_snapshot=intake["target_snapshot"],
            unanswered_question_ids=unanswered,
        )

    def record_input_gate(
        self, block: Any, run_root: Path, *, target_snapshot: Any = None
    ) -> None:
        """Record the CONTRACT §1 input gate before any lens reads the target.

        This is where the three questions of #196 · #206 · #202 ④ actually fire:
        the run cannot start a lens until the editing state, the target maturity and
        the archived source copy are on record, and the copy's hash is checked
        against the snapshot the run claims to be reading.
        """
        if not self._intake_validated:
            raise RuntimeError("cannot record the input gate before validated convention intake")
        if self._input_gate is not None:
            raise RuntimeError("front-gate input gate may be recorded only once")
        errors = validate_input_gate_block(
            block, label="review_input_gate", with_schema_version=True, snapshot_id=target_snapshot
        )
        # r1-02 · r2-01: the executable path proves the archive exists and hashes right,
        # instead of trusting the declaration. This is NOT optional — an optional byte
        # check is a declaration again, and `start_lens` would have accepted it.
        errors = errors + verify_source_copy_bytes(block, run_root)
        if errors:
            raise ValueError("invalid review input gate: " + "; ".join(errors))
        # r3: pin the validated answer. Keeping the caller's mapping by reference meant a
        # caller could rewrite `source_copy` AFTER the gate was recorded and emitted, and
        # the per-lens recheck — which exists to catch exactly this — would dutifully
        # re-verify the *rewritten* declaration. What is verified must be what is kept.
        self._input_gate = deepcopy(block)
        self._run_root = run_root.resolve()
        open_items = block.get("open_items")
        self._emit(
            "input_gate_recorded",
            phase="pre_lens",
            editing_state=block["editing_state"],
            target_maturity=block["target_maturity"],
            source_copy_sha256=block["source_copy"]["sha256"],
            source_copy_verified=True,
            # #196: an in-progress or unknown editing state defers §6 verification.
            # The run stays valid; it just cannot reach done (§7).
            verification_deferred=defers_verification(block),
            # #206: registered open items are a CLASSIFICATION baseline. §2 remains the
            # only suppression authority, so this reference never kills a finding.
            open_items_ledger_ref=(open_items or {}).get("ledger_ref"),
            open_items_use="classification_only",
            # #208 제안3: whether a prior round's output exists for this target.
            # Codex r1-02: this IS part of the digest-bound field tuple
            # (_validate_front_gate_binding) — a receipt cannot flip exists after
            # the gate recorded it. What is NOT checked here (or there) is the
            # actual match_review_rounds.py output; that obligation is enforced at
            # receipt time via round_context.comparison_ref.
            prior_round_exists=(block.get("prior_round") or {}).get("exists"),
            # Codex r3-01: exists alone isn't enough — round_no drives round_label's
            # arithmetic (round_context.round_label must equal round_no + 1), so a
            # receipt that rewrote round_no after the gate recorded it could pick any
            # round_label to match. Bind the number the arithmetic actually depends on
            # (same minimal-binding precedent as source_copy_sha256, not the whole dict).
            prior_round_output_round_no=(
                ((block.get("prior_round") or {}).get("output_ref") or {}).get("round_no")
            ),
            # docauth#293: round_no alone only stops round_label forgery — #292's
            # _validate_v2 checks that output_ref.path/.sha256 name a real, internally
            # matching file, but nothing bound *which* file that is to what the gate
            # recorded. Without this a receipt could swap output_ref for any other real,
            # correctly-hashed file in the packet after the gate ran. Same minimal-binding
            # precedent as round_no above, applied to the two fields _validate_v2 verifies.
            prior_round_output_ref_path=(
                ((block.get("prior_round") or {}).get("output_ref") or {}).get("path")
            ),
            prior_round_output_ref_sha256=(
                ((block.get("prior_round") or {}).get("output_ref") or {}).get("sha256")
            ),
        )

    def record_decision_registry(
        self, state: str, *, path: Any = None, content: bytes | None = None,
        files: dict[str, bytes] | None = None,
    ) -> None:
        """docauth#352: record the §1 ② decision-registry state BEFORE any lens runs.

        ``checked`` requires the registry bytes and runs ``validate_decisions`` on them --
        a registry that fails validation is not suppression-eligible, so the gate refuses
        (fail-closed) rather than recording a `checked` the receipt could lean on.
        ``absent`` = no registry and no prior-decision source (§1 ② 무보증).
        ``unchecked`` = a registry exists but this run did not compare against it (the
        state #352 found missing from the contract): same duties as absent, plus a
        summary marker. The event carries the declared path and the bytes' sha256 so a
        receipt claiming `checked` can be bound to the exact registry the run validated.
        """
        if self._input_gate is None:
            raise RuntimeError("cannot record the decision registry before the input gate")
        if self._started_lenses:
            raise RuntimeError("cannot record the decision registry after a lens has started")
        if self._decision_registry_recorded:
            raise RuntimeError("front-gate decision registry may be recorded only once")
        if state not in DECISION_REGISTRY_STATES:
            raise ValueError(f"decision registry state must be one of {DECISION_REGISTRY_STATES}, got {state!r}")
        sha256 = None
        validated = False
        union_sha256 = None
        union_files: list[dict[str, Any]] | None = None
        if state == "absent":
            if path is not None or content is not None:
                raise ValueError("decision registry declared absent must not carry a path")
        else:
            if content is None:
                raise ValueError(f"decision registry state {state!r} requires the registry bytes")
            sha256 = hashlib.sha256(content).hexdigest()
            if state == "checked":
                try:
                    from .validate_decisions import (validate_union)
                except ImportError:
                    from validate_decisions import (validate_union)

                # docauth#356: `checked`는 합집합 단위다 — 루트+전이적 includes를 **한 번 읽은 바이트**
                # (`files`)로 검증하고, 합집합 authority 적격(공유 = 서명 필수)까지 본다. 부적격이면 거부.
                declared = str(path) if path is not None else FRONT_GATE_DECISIONS_CANONICAL_RELPATH
                try:
                    from .validate_decisions import (_norm_abs)
                except ImportError:
                    from validate_decisions import (_norm_abs)

                # 매핑이 없으면 준 바이트 하나만이 합집합이다 — includes가 있으면 "include not archived"로
                # 거부된다(공유 레지스트리는 전이적 바이트 매핑 없이 checked가 될 수 없다).
                mapping = dict(files) if files is not None else {_norm_abs(declared): content}
                errors, _warnings, _info, union = validate_union(declared, files=mapping)
                if errors:
                    raise ValueError(
                        "decision registry is not suppression-eligible, so it cannot be recorded as "
                        "checked (declare --decisions-unchecked or fix it): " + "; ".join(errors)
                    )
                if not union["authority_eligible"]:
                    raise ValueError(
                        "decision registry is not authority-eligible for suppression (shared registries need "
                        "registration_policy + signoff on every file/entry, #356) -- declare --decisions-unchecked "
                        f"or fix it: {union['authority_reason']}"
                    )
                validated = True
                if files is not None:  # 구현 r1-04: 구형(0.32) 단일 아카이브 재생은 옛 이벤트 형상을 그대로 낸다
                    union_sha256 = union["union_sha256"]
                    union_files = [
                        {"declared_path": f["declared_path"], "rel_path": f["rel_path"], "sha256": f["sha256"]}
                        for f in union["files"]
                    ]
        self._decision_registry_recorded = True
        extra: dict[str, Any] = {}
        if union_files is not None:
            extra = {"union_sha256": union_sha256, "files": union_files}
        self._emit(
            DECISION_REGISTRY_EVENT,
            phase="pre_lens",
            state=state,
            path=(str(path) if path is not None else None),
            sha256=sha256,
            validated=validated,
            **extra,
        )

    def start_lens(self, lens_id: str) -> None:
        if not self._intake_validated:
            raise RuntimeError("cannot start a lens before validated convention intake")
        if self._input_gate is None:
            raise RuntimeError("cannot start a lens before the recorded CONTRACT §1 input gate")
        # r2-04: the archive was verified once, before any lens ran. Between that check
        # and this lens the bytes could have been replaced, and every anchor this lens
        # produces is bound to the snapshot it actually reads. Re-verify per lens so the
        # window shrinks to nothing a lens can observe.
        if self._run_root is not None:
            drift = verify_source_copy_bytes(self._input_gate, self._run_root)
            if drift:
                raise ValueError(
                    "archived source copy changed after the input gate was recorded: "
                    + "; ".join(drift)
                )
        if lens_id not in LENSES:
            raise ValueError(f"unknown lens: {lens_id}")
        if lens_id in self._started_lenses:
            raise RuntimeError(f"lens already started: {lens_id}")
        self._started_lenses.add(lens_id)
        self._emit("lens_started", lens_id=lens_id)

    def record_candidate_questions(
        self,
        intermediate: Any,
        *,
        repo_root: Path | None = None,
    ) -> None:
        if self._started_lenses != set(LENSES):
            raise RuntimeError("candidate inventory requires L1, L2, and L3 to start first")
        errors = validate_intermediate_data(intermediate, packet_root=repo_root)
        if errors:
            raise ValueError("invalid review intermediate: " + "; ".join(errors))
        self._emit(
            "candidate_inventory_validated",
            source_candidate_count=len(intermediate["source_candidate_inventory"]),
            candidate_atom_count=len(intermediate["candidate_atoms"]),
        )
        for question in intermediate["questions"]:
            if question["status"] == "open":
                self._emit(
                    "candidate_question_opened",
                    record_id=question["record_id"],
                    convention_slot=question["convention_slot"],
                    # r1-02 again: the same emit-by-reference shape. An emitted event is
                    # a record of what was true at emission, not a live view of its source.
                    dependent_atom_refs=list(question["dependent_atom_refs"]),
                )


def build_trace(
    profile_path: Path,
    intake_path: Path,
    input_gate_path: Path,
    intermediate_path: Path | None = None,
    *,
    repo_root: Path | None = None,
    run_root: Path | None = None,
    docmodel_path: Any = _DOCMODEL_PATH_UNRESOLVED,
    docmodel_bytes: bytes | None = None,
    intake_bytes: bytes | None = None,
    profile_bytes: bytes | None = None,
    input_gate_bytes: bytes | None = None,
    decisions_state: str | None = None,
    decisions_path: Any = None,
    decisions_bytes: bytes | None = None,
    decisions_files: dict[str, bytes] | None = None,
) -> list[dict[str, Any]]:
    """``docmodel_path`` (docauth#315): pass an explicit resolved path (or ``None``)
    to bypass live resolution entirely -- this is what #299 recomputation replay
    does, pointing at the archived ``front_gate_docmodel.yaml`` copy (or passing
    ``None`` when that archive doesn't exist). Leave this at its default (an
    internal sentinel, distinct from ``None``) and the ORIGINAL run resolves it live
    from repo state (`resolve_structure_axis_docmodel`) when ``repo_root`` is given
    -- this is the one live, non-deterministic step in the whole pipeline, and it is
    intentionally never reached during recomputation (which never passes repo_root
    for this purpose; see validate_review_result.py's `_validate_front_gate_recomputation`).

    Codex 피어리뷰 r1-04: an explicit ``docmodel_path=None`` (e.g. from `main()`,
    which already ran its own resolution and got no candidate) must be honored
    as-is, NOT treated the same as "please resolve this yourself" -- re-resolving
    here could observe a DIFFERENT repo state than the caller's own resolution did
    (a file added/removed between the two calls), producing a judged trace event
    the caller never archived a docmodel for (or vice versa). Only the sentinel
    default triggers live resolution; a caller that already resolved -- to a path
    OR to nothing -- always wins.
    """
    trace = FrontGateTrace()
    # Codex 피어리뷰 r6-01: read intake ONCE if the caller already captured bytes
    # (main()'s pre-resolution step) instead of a second independent read here --
    # otherwise a concurrent intake edit between the caller's read and this one
    # could resolve the docmodel candidate against different observed_sections than
    # what preflight() actually judges against.
    intake = (
        yaml.load(intake_bytes.decode("utf-8"), Loader=StrictLoader)
        if intake_bytes is not None
        else load_yaml(intake_path)
    )
    resolved_docmodel_path: Path | None
    resolved_docmodel_bytes: bytes | None
    if docmodel_path is _DOCMODEL_PATH_UNRESOLVED:
        raise ValueError("docloop build_trace requires explicit docmodel_path (None is allowed)")
    resolved_docmodel_path = docmodel_path
    resolved_docmodel_bytes = docmodel_bytes
    # docauth#318: same single-read-and-thread treatment r6-01 gave intake --
    # profile and input-gate were still independently reread during archival
    # (build_trace()'s own load_yaml() here, then main()'s separate
    # source_path.read_bytes() at archive time). Recomputation replay never
    # passes these (it reads each archived file exactly once, nothing else
    # touches it during that call), so this only changes the ORIGINAL run's path.
    profile = (
        yaml.load(profile_bytes.decode("utf-8"), Loader=StrictLoader)
        if profile_bytes is not None
        else load_yaml(profile_path)
    )
    input_gate_block = (
        yaml.load(input_gate_bytes.decode("utf-8"), Loader=StrictLoader)
        if input_gate_bytes is not None
        else load_yaml(input_gate_path)
    )
    trace.preflight(
        intake,
        profile,
        docmodel_path=resolved_docmodel_path,
        docmodel_bytes=resolved_docmodel_bytes,
    )
    trace.record_input_gate(
        input_gate_block,
        run_root if run_root is not None else input_gate_path.resolve().parent,
        target_snapshot=intake.get("target_snapshot") if isinstance(intake, dict) else None,
    )
    # docauth#352: the registry declaration sits between the input gate and the lenses
    # (§1 ② is a pre-lens input). ``None`` = the caller did not declare (old runs; the
    # done-time validator refuses a v2 receipt whose trace has no registry event).
    if decisions_state is not None:
        trace.record_decision_registry(
            decisions_state, path=decisions_path, content=decisions_bytes, files=decisions_files
        )
    for lens_id in LENSES:
        trace.start_lens(lens_id)
    if intermediate_path is not None:
        trace.record_candidate_questions(load_intermediate(intermediate_path), repo_root=repo_root)
    return trace.events
