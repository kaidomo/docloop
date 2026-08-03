#!/usr/bin/env python3
"""Focused regression tests for the generic #161 convention front gate."""

from __future__ import annotations

from copy import deepcopy
import hashlib
from pathlib import Path
import subprocess
import sys
import tempfile
import threading
import unittest

import yaml


ROOT = Path(__file__).resolve().parents[1]
LIB = ROOT / "lib" / "review_gate"
FIXTURES = ROOT / "tests" / "fixtures" / "review-gate" / "convention"
sys.path.insert(0, str(LIB))

from front_gate import FrontGateTrace, LENSES  # noqa: E402
from materialize_docmodel import materialize  # noqa: E402
from validate_convention_intake import validate as validate_intake_file  # noqa: E402
from validate_convention_intake import validate_data as validate_intake  # noqa: E402
from validate_convention_profile import load_yaml  # noqa: E402
from validate_convention_profile import validate as validate_profile_file  # noqa: E402
from validate_convention_profile import validate_data as validate_profile  # noqa: E402


class ConventionFixture(unittest.TestCase):
    def setUp(self) -> None:
        self.profile = load_yaml(FIXTURES / "synthetic-profile.yaml")
        self.intake = load_yaml(FIXTURES / "intake-all-states.yaml")


class ConventionProfileTests(ConventionFixture):
    def test_neutral_profile_is_valid_and_non_universal(self) -> None:
        self.assertEqual(validate_profile(self.profile), [])
        self.assertEqual(self.profile["status"], "provisional")
        self.assertIs(self.profile["transferability"]["universal"], False)

    def test_question_ids_and_declaration_identities_are_strict(self) -> None:
        duplicate = deepcopy(self.profile)
        duplicate["questions"][1]["id"] = duplicate["questions"][0]["id"]
        self.assertTrue(any("duplicate question id" in e for e in validate_profile(duplicate)))

        mismatch = deepcopy(self.profile)
        mismatch["questions"][0]["declaration_targets"] = ["sections[OTHER]"]
        self.assertTrue(any("exactly match" in e for e in validate_profile(mismatch)))

        extra = deepcopy(self.profile)
        extra["questions"][0]["options"][0]["declaration"]["precedence"] = [
            {"wins": "U", "over": ["A"]}
        ]
        self.assertTrue(any("undeclared precedence[U]" in e for e in validate_profile(extra)))

    def test_applicability_scope_provenance_and_transferability_fail_closed(self) -> None:
        invalid = deepcopy(self.profile)
        invalid["transferability"]["universal"] = True
        invalid["questions"][0]["applicability"]["all_sections_present"] = []
        invalid["questions"][0]["allowed_scopes"] = ["organization"]
        invalid["questions"][0]["provenance_required"] = False
        errors = validate_profile(invalid)
        for fragment in ("universal=false", "all_sections_present", "document or template", "provenance_required"):
            self.assertTrue(any(fragment in e for e in errors), errors)

    def test_profile_duplicate_yaml_key_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / "profile.yaml"
            path.write_text("schema_version: 1\nschema_version: 1\n", encoding="utf-8")
            self.assertTrue(any("duplicate YAML key" in e for e in validate_profile_file(path)))


class ConventionIntakeTests(ConventionFixture):
    def test_fixture_covers_all_four_states_and_both_scopes(self) -> None:
        self.assertEqual(validate_intake(self.intake, self.profile), [])
        self.assertEqual(
            {r["approval"] for r in self.intake["records"]},
            {"unanswered", "inapplicable", "unapproved", "approved_to_draft"},
        )
        self.assertEqual(
            {r["scope"] for r in self.intake["records"] if r["scope"]},
            {"document", "template"},
        )

    def test_exactly_once_profile_coverage_is_required(self) -> None:
        missing = deepcopy(self.intake)
        missing["records"].pop()
        self.assertTrue(any("missing intake records" in e for e in validate_intake(missing, self.profile)))

        duplicate = deepcopy(self.intake)
        duplicate["records"].append(deepcopy(duplicate["records"][0]))
        self.assertTrue(any("duplicate intake record" in e for e in validate_intake(duplicate, self.profile)))

        unknown = deepcopy(self.intake)
        unknown["records"][0]["question_id"] = "unknown"
        errors = validate_intake(unknown, self.profile)
        self.assertTrue(any("not in profile" in e for e in errors), errors)

    def test_profile_binding_phase_and_snapshot_are_strict(self) -> None:
        invalid = deepcopy(self.intake)
        invalid["phase"] = "post_lens"
        invalid["profile_id"] = "other"
        invalid["template_id"] = "other"
        invalid["target_snapshot"] = "untyped"
        errors = validate_intake(invalid, self.profile)
        for fragment in ("pre_lens", "profile_id", "template_id", "stable nonempty"):
            self.assertTrue(any(fragment in e for e in errors), errors)

    def test_answer_state_scope_source_and_asked_matrices_are_strict(self) -> None:
        invalid = deepcopy(self.intake)
        unanswered = next(r for r in invalid["records"] if r["approval"] == "unanswered")
        unanswered["response"] = "canonical"
        inapplicable = next(r for r in invalid["records"] if r["approval"] == "inapplicable")
        inapplicable["asked"] = True
        answered = next(r for r in invalid["records"] if r["question_id"] == "q-template")
        answered["asked"] = False
        answered["response"] = "not-an-option"
        answered["scope"] = "document"
        answered["source"] = {"type": "", "ref": ""}
        errors = validate_intake(invalid, self.profile)
        for fragment in (
            "unanswered question",
            "inapplicable question must have asked=false",
            "answered question must have asked=true",
            "select a declared profile option",
            "scope is not allowed",
            "source.type",
        ):
            self.assertTrue(any(fragment in e for e in errors), errors)

    def test_document_scope_requires_target_document(self) -> None:
        invalid = deepcopy(self.intake)
        del invalid["target_document"]
        self.assertTrue(any("document scope requires" in e for e in validate_intake(invalid, self.profile)))

    def test_snapshot_formats_are_accepted_but_untyped_values_are_not(self) -> None:
        for snapshot in (
            "sha256:" + "a" * 64,
            "commit:5025561dcad0",
            "export:neutral-example-2026-08-04",
        ):
            intake = deepcopy(self.intake)
            intake["target_snapshot"] = snapshot
            self.assertEqual(validate_intake(intake, self.profile), [])

    def test_intake_duplicate_yaml_key_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / "intake.yaml"
            path.write_text("schema_version: 1\nschema_version: 1\n", encoding="utf-8")
            errors = validate_intake_file(path, FIXTURES / "synthetic-profile.yaml")
            self.assertTrue(any("duplicate YAML key" in e for e in errors), errors)


class MaterializerTests(ConventionFixture):
    def test_only_approved_answers_materialize_in_profile_order(self) -> None:
        expected = materialize(self.profile, self.intake, "b" * 64)
        reordered = deepcopy(self.intake)
        reordered["records"].reverse()
        actual = materialize(self.profile, reordered, "b" * 64)
        self.assertEqual(actual, expected)
        self.assertEqual([row["id"] for row in actual["sections"]], ["D", "T", "A"])
        self.assertEqual(actual["meta"]["materialized_question_ids"], [
            "q-document", "q-template", "q-approved"
        ])

    def test_draft_is_explicitly_non_authoritative(self) -> None:
        draft = materialize(self.profile, self.intake, hashlib.sha256(b"intake").hexdigest())
        self.assertEqual(draft["meta"]["approval_state"], "draft")
        self.assertIsNone(draft["meta"]["approved_by"])
        self.assertIs(draft["meta"]["suppression_eligible"], False)
        self.assertEqual(draft["meta"]["answer_scopes"], ["document", "template"])

    def test_empty_and_conflicting_materialization_fail_closed(self) -> None:
        empty = deepcopy(self.intake)
        for record in empty["records"]:
            if record["approval"] == "approved_to_draft":
                record["approval"] = "unapproved"
        with self.assertRaisesRegex(ValueError, "no approved_to_draft answers"):
            materialize(self.profile, empty, "c" * 64)

        conflict_profile = deepcopy(self.profile)
        question = next(q for q in conflict_profile["questions"] if q["id"] == "q-template")
        question["declaration_targets"] = ["sections[D]"]
        question["options"][0]["declaration"]["sections"] = [
            {"id": "D", "title": "Document", "role": "canonical"}
        ]
        with self.assertRaisesRegex(ValueError, r"conflicting approved declarations for sections\[D\]"):
            materialize(conflict_profile, self.intake, "d" * 64)

    def _command(self, output: Path) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            [
                sys.executable,
                str(LIB / "materialize_docmodel.py"),
                str(FIXTURES / "intake-all-states.yaml"),
                "--profile",
                str(FIXTURES / "synthetic-profile.yaml"),
                "--output",
                str(output),
            ],
            capture_output=True,
            text=True,
        )

    def test_existing_file_and_symlink_are_rejected_without_changing_bytes(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            existing = root / "existing.yaml"
            existing.write_bytes(b"keep-existing\n")
            result = self._command(existing)
            self.assertNotEqual(result.returncode, 0)
            self.assertEqual(existing.read_bytes(), b"keep-existing\n")

            target = root / "target.yaml"
            target.write_bytes(b"keep-target\n")
            link = root / "link.yaml"
            link.symlink_to(target)
            result = self._command(link)
            self.assertNotEqual(result.returncode, 0)
            self.assertTrue(link.is_symlink())
            self.assertEqual(target.read_bytes(), b"keep-target\n")

    def test_concurrent_same_output_admits_exactly_one_success(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            output = Path(td) / "draft.yaml"
            barrier = threading.Barrier(3)
            results: list[subprocess.CompletedProcess[str]] = []

            def launch() -> None:
                barrier.wait()
                results.append(self._command(output))

            threads = [threading.Thread(target=launch) for _ in range(2)]
            for thread in threads:
                thread.start()
            barrier.wait()
            for thread in threads:
                thread.join()
            self.assertEqual(sorted(result.returncode for result in results), [0, 1])
            draft = yaml.safe_load(output.read_text(encoding="utf-8"))
            self.assertEqual(draft["meta"]["approval_state"], "draft")


class FrontGateTests(ConventionFixture):
    def test_preflight_must_precede_each_lens_and_runs_once(self) -> None:
        trace = FrontGateTrace()
        with self.assertRaisesRegex(RuntimeError, "before validated convention intake"):
            trace.start_lens("L1")
        trace.preflight(self.intake, self.profile)
        with self.assertRaisesRegex(RuntimeError, "only once"):
            trace.preflight(self.intake, self.profile)
        for lens in LENSES:
            trace.start_lens(lens)
        self.assertEqual(
            [event["lens_id"] for event in trace.events if event["event"] == "lens_started"],
            list(LENSES),
        )

    def test_candidate_inventory_is_rejected_until_all_lenses_start(self) -> None:
        trace = FrontGateTrace()
        trace.preflight(self.intake, self.profile)
        trace.start_lens("L1")
        trace.start_lens("L2")
        with self.assertRaisesRegex(RuntimeError, "L1, L2, and L3"):
            trace.record_candidate_questions({})

    def test_invalid_intake_never_admits_lens_execution(self) -> None:
        invalid = deepcopy(self.intake)
        invalid["phase"] = "post_lens"
        trace = FrontGateTrace()
        with self.assertRaisesRegex(ValueError, "pre_lens"):
            trace.preflight(invalid, self.profile)
        with self.assertRaisesRegex(RuntimeError, "before validated convention intake"):
            trace.start_lens("L1")


if __name__ == "__main__":
    unittest.main()
