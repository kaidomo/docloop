#!/usr/bin/env python3
"""Internal review-gate startup ordering guard and audit trace scaffold."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from validate_convention_intake import validate_data as validate_intake_data
LENSES = ("L1", "L2", "L3")


def _intermediate_validator() -> Any:
    """Load the optional #160 dependency only when candidate inventory is used."""
    from validate_review_intermediate import validate_data

    return validate_data


class FrontGateTrace:
    """Fail-closed state machine at the intake → lens → candidate-question seam."""

    def __init__(self) -> None:
        self.events: list[dict[str, Any]] = []
        self._intake_validated = False
        self._started_lenses: set[str] = set()

    def _emit(self, event: str, **payload: Any) -> None:
        self.events.append({"sequence": len(self.events) + 1, "event": event, **payload})

    def preflight(self, intake: Any, profile: Any) -> None:
        errors = validate_intake_data(intake, profile)
        if errors:
            raise ValueError("invalid convention intake: " + "; ".join(errors))
        if self.events:
            raise RuntimeError("front-gate preflight may run only once")
        self._intake_validated = True
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

    def start_lens(self, lens_id: str) -> None:
        if not self._intake_validated:
            raise RuntimeError("cannot start a lens before validated convention intake")
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
                    dependent_atom_refs=question["dependent_atom_refs"],
                )
