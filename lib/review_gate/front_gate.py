#!/usr/bin/env python3
"""Internal review-gate startup ordering guard and audit trace scaffold."""

from __future__ import annotations

from copy import deepcopy
from pathlib import Path
from typing import Any

if __package__:
    from .validate_convention_intake import (
        declares_profile_not_applicable,
        validate_data as validate_intake_data,
    )
    from .validate_input_gate import (
        defers_verification,
        validate_block as validate_input_gate_block,
        verify_source_copy_bytes,
    )
else:
    from validate_convention_intake import (
        declares_profile_not_applicable,
        validate_data as validate_intake_data,
    )
    from validate_input_gate import (
        defers_verification,
        validate_block as validate_input_gate_block,
        verify_source_copy_bytes,
    )

LENSES = ("L1", "L2", "L3")


def _intermediate_validator() -> Any:
    """Load the optional ledger validator only when candidate inventory is used."""
    if __package__:
        from .validate_review_intermediate import validate_data
    else:
        from validate_review_intermediate import validate_data

    return validate_data


class FrontGateTrace:
    """Fail-closed state machine at the intake → lens → candidate-question seam."""

    def __init__(self) -> None:
        self.events: list[dict[str, Any]] = []
        self._intake_validated = False
        self._input_gate: dict[str, Any] | None = None
        self._run_root: Path | None = None
        self._started_lenses: set[str] = set()

    def _emit(self, event: str, **payload: Any) -> None:
        self.events.append({"sequence": len(self.events) + 1, "event": event, **payload})

    def preflight(self, intake: Any, profile: Any) -> None:
        if self._intake_validated:
            raise RuntimeError("front-gate preflight may run only once")
        errors = validate_intake_data(intake, profile)
        if errors:
            raise ValueError("invalid convention intake: " + "; ".join(errors))
        self._intake_validated = True
        if declares_profile_not_applicable(intake):
            # Lenses may start, but the non-applicability and its cost are published:
            # no convention authority exists for this template, so the structure axis
            # (CONTRACT §1 optional input ⑤) is undetermined rather than passed.
            declaration = intake["profile_applicability"]
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
            # docauth#293: round_no alone only stops round_label forgery — docauth#290's
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
        packet_root: Path | None = None,
    ) -> None:
        if self._started_lenses != set(LENSES):
            raise RuntimeError("candidate inventory requires L1, L2, and L3 to start first")
        validate_intermediate_data = _intermediate_validator()
        errors = validate_intermediate_data(intermediate, packet_root=packet_root)
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
