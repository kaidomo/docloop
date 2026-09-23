#!/usr/bin/env python3
"""Packet-bound import behavior and atomic-commit fault boundaries, no model calls."""
from contextlib import redirect_stdout, redirect_stderr
from pathlib import Path
import copy
import hashlib
import io
import json
import os
import subprocess
import sys
import unittest
from unittest.mock import patch

import yaml

from test_review_gate_port_core import ModernPacketFixture, ROOT, SOURCE, digest, dump, record_digest
from lib import import_review_receipt as importer


class ImportReceiptTests(ModernPacketFixture, unittest.TestCase):
    def setUp(self):
        super().setUp()
        self.manifest = self.root / "manifest.yaml"
        dump(self.manifest, {"project": {"product": "Synthetic product", "ssot": "plan.md"},
                             "observations": [], "chunks": []})
        self.report = self.root / "reports/import.md"
        self.live = self.root / "live.md"
        self.live.write_text(SOURCE)
        self.round_number = 0

    def prepare(self, *extra):
        self.round_number += 1
        run_id = "modern" if self.round_number == 1 else f"modern-{self.round_number}"
        self.packet = self.review / "review-gate" / run_id
        self.assert_cli(0, "prepare", self.review, run_id, "target.md", "--unassured", "--no-terms",
                        "--no-docmodel", "--editing-state", "frozen", "--target-maturity", "complete", *extra)
        self.run = yaml.safe_load((self.packet / "RUN.yaml").read_text())
        self.snapshot = "sha256:" + digest(self.packet / "frozen/target.txt")
        self.prepared_marker = (self.packet / "COMPLETE.json").read_bytes()

    def build_flow(self, final_status="verified"):
        receipt = super().build_flow(final_status)
        receipt["packet_binding"]["run_id"] = self.run["run_id"]
        if final_status == "rejected":
            receipt["document_clearance"] = "clear"
        if getattr(self, "include_question", False):
            final = yaml.safe_load((self.packet / "results/final.yaml").read_text())["review_intermediate"]
            receipt["questions"] = copy.deepcopy(final["questions"])
            receipt["document_clearance"] = "indeterminate"
        self.write_receipt(receipt)
        self.assert_cli(5 if final_status == "judgment_unavailable" or getattr(self, "include_question", False) else 0,
                        "validate-result", self.packet, "results/DONE.md")
        return receipt

    def ledger(self, status):
        ledger = super().ledger(status)
        if not getattr(self, "include_question", False):
            return ledger
        question = {"record_id": "REC-Q", "status": "open" if status == "discovered" else "judgment_unavailable",
            "convention_slot": "external.retry-policy", "dependent_atom_refs": ["ATOM-Q"],
            "resolution_derived_atom_refs": [], "snapshot_id": self.snapshot, "evidence_anchors": ["L2"],
            "classification_verification": {"result": "unresolved", "verifier_id": "synthetic-question",
                                             "evidence": "results/verification.md"}}
        if status != "discovered":
            question["judgment_unavailable"] = {"reason": "authority_absent", "basis": "Policy owner not available.",
                "needed_input": "Ask the policy owner.", "attempts": [{"verifier_id": "synthetic-question",
                    "result": "unresolved", "snapshot_id": self.snapshot, "evidence": "results/verification.md"}]}
        question["public_record_digest"] = record_digest("question", question)
        ledger["questions"] = [question]
        ledger["source_candidate_inventory"].append({"source_candidate_id": "SC-Q", "lens": "L3",
            "statement": "Which external policy applies?", "evidence_anchors": ["L2"]})
        ledger["candidate_atoms"].append({"candidate_atom_id": "ATOM-Q", "source_candidate_refs": ["SC-Q"],
            "statement": "Which external policy applies?", "evidence_anchors": ["L2"],
            "classification_basis": {"co_reference": "unknown", "semantic_values": "unknown",
                "presentation_rule": "unknown", "evidence": "Need external policy owner."}})
        ledger["classification_ledger"].append({"candidate_atom_id": "ATOM-Q", "outcome": "question",
            "target_record_id": "REC-Q", "evidence_anchors": ["L2"]})
        return ledger

    def import_args(self, *extra, live=True, packet=None):
        return [str(packet or self.packet), "results/DONE.md", "--manifest", str(self.manifest),
                "--report", str(self.report), *( ["--live", str(self.live)] if live else ["--no-live"]),
                *map(str, extra)]

    def run_import(self, *extra, expected=0, live=True, packet=None):
        proc = subprocess.run([str(ROOT / "bin/docloop"), "atb-import-review",
                               *self.import_args(*extra, live=live, packet=packet)],
                              capture_output=True, text=True)
        self.assertEqual(proc.returncode, expected, proc.stdout + proc.stderr)
        return proc

    def manifest_data(self):
        return yaml.safe_load(self.manifest.read_text())

    def test_valid_packet_imports_grounded_observation_and_passes_manifest_cli(self):
        self.build_flow()
        self.run_import()
        data = self.manifest_data()
        obs, = data["observations"]
        self.assertEqual(obs["id"], "RG-F-01")
        self.assertEqual(obs["what"], "Retry limits differ.")
        self.assertTrue(obs["verified"])
        self.assertFalse(obs["needs_revalidation"])
        self.assertFalse(obs["thin_source"])
        self.assertTrue(any("The retry limit is three." in src for src in obs["sources"]))
        self.assertEqual(obs["review_ref"]["source_identity"], [str(self.review), "target.md"])
        proc = subprocess.run([sys.executable, str(ROOT / "lib/validate_manifest.py"), str(self.manifest)],
                              capture_output=True, text=True)
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
        self.assertTrue(self.report.is_file())

    def test_repeat_import_preserves_manifest_bytes(self):
        self.build_flow()
        self.run_import()
        before = self.manifest.read_bytes()
        self.run_import()
        self.assertEqual(self.manifest.read_bytes(), before)
        self.assertEqual(len(self.manifest_data()["observations"]), 1)

    def test_human_content_override_and_demotion_survive_reimport(self):
        self.build_flow()
        self.run_import()
        data = self.manifest_data()
        data["observations"][0].update(what="Human correction", verified=False, needs_revalidation=True)
        dump(self.manifest, data)
        self.run_import()
        obs = self.manifest_data()["observations"][0]
        self.assertEqual(obs["what"], "Human correction")
        self.assertFalse(obs["verified"])
        self.assertTrue(obs["needs_revalidation"])

    def test_no_live_demotes_every_observation(self):
        self.build_flow()
        self.run_import(live=False)
        obs = self.manifest_data()["observations"][0]
        self.assertFalse(obs["verified"])
        self.assertTrue(obs["needs_revalidation"])

    def test_line_movement_does_not_demote_unchanged_anchored_text(self):
        self.build_flow()
        self.live.write_text("Introductory line.\n" + SOURCE)
        self.run_import()
        obs = self.manifest_data()["observations"][0]
        self.assertTrue(obs["verified"])
        self.assertFalse(obs["needs_revalidation"])

    def test_changed_anchored_line_demotes_observation(self):
        self.build_flow()
        self.live.write_text(SOURCE.replace("limit is three", "limit is two"))
        self.run_import()
        obs = self.manifest_data()["observations"][0]
        self.assertFalse(obs["verified"])
        self.assertTrue(obs["needs_revalidation"])

    def test_unavailable_result_becomes_pending_not_verified_observation(self):
        self.build_flow("judgment_unavailable")
        self.run_import()
        data = self.manifest_data()
        self.assertEqual(data["observations"], [])
        pending, = data["pending_issues"]
        self.assertEqual(pending["id"], "RGQ-F-01")
        self.assertEqual(pending["kind"], "judgment_unavailable")
        self.assertEqual(pending["needed_input"], "Provide the authoritative retry policy.")
        self.assertEqual(pending["review_ref"]["source_identity"], [str(self.review), "target.md"])

    def test_broken_receipt_leaves_manifest_and_existing_report_unchanged(self):
        receipt = self.build_flow()
        self.report.parent.mkdir()
        self.report.write_text("Existing report.\n")
        before = self.manifest.read_bytes(), self.report.read_bytes()
        receipt["findings"][0]["status"] = "discovered"
        self.write_receipt(receipt)
        self.run_import(expected=1)
        self.assertEqual((self.manifest.read_bytes(), self.report.read_bytes()), before)

    def test_invalid_manifest_is_rejected_without_any_output(self):
        self.build_flow()
        self.manifest.write_text("project: {}\nobservations: invalid\n")
        before = self.manifest.read_bytes()
        self.run_import(expected=1)
        self.assertEqual(self.manifest.read_bytes(), before)
        self.assertFalse(self.report.exists())

    def test_dry_run_writes_neither_manifest_nor_report(self):
        self.build_flow()
        before = self.manifest.read_bytes()
        self.run_import("--dry-run")
        self.assertEqual(self.manifest.read_bytes(), before)
        self.assertFalse(self.report.parent.exists())
        self.report.parent.mkdir()
        self.report.write_text("Keep existing report.\n")
        report_before = self.report.read_bytes()
        self.run_import("--dry-run")
        self.assertEqual(self.manifest.read_bytes(), before)
        self.assertEqual(self.report.read_bytes(), report_before)


    def switch_source_folder(self):
        self.review = self.root / "other-review"
        self.review.mkdir()
        (self.review / "target.md").write_text(SOURCE)

    def test_different_review_folders_cannot_share_a_finding_id(self):
        self.build_flow()
        self.run_import()
        before = self.manifest.read_bytes(), self.report.read_bytes()
        self.switch_source_folder()
        self.build_flow()
        self.run_import(expected=2)
        self.assertEqual((self.manifest.read_bytes(), self.report.read_bytes()), before)

    def test_same_source_followup_snapshot_updates_existing_observation(self):
        self.build_flow()
        self.run_import()
        old = self.manifest_data()["observations"][0]["review_ref"]["snapshot_id"]
        changed = SOURCE.replace("four retries", "five retries")
        (self.review / "target.md").write_text(changed)
        self.live.write_text(changed)
        self.build_flow()
        self.run_import()
        obs, = self.manifest_data()["observations"]
        self.assertNotEqual(obs["review_ref"]["snapshot_id"], old)
        self.assertEqual(obs["review_ref"]["source_identity"], [str(self.review), "target.md"])
        self.assertTrue(obs["verified"])

    def test_missing_legacy_source_identity_is_a_collision(self):
        self.build_flow()
        self.run_import()
        data = self.manifest_data()
        data["observations"][0]["review_ref"].pop("source_identity")
        dump(self.manifest, data)
        before = self.manifest.read_bytes()
        self.run_import(expected=2)
        self.assertEqual(self.manifest.read_bytes(), before)

    def test_different_source_pending_id_cannot_overwrite_existing_pending(self):
        self.build_flow("judgment_unavailable")
        self.run_import()
        before = self.manifest.read_bytes()
        self.switch_source_folder()
        self.build_flow("judgment_unavailable")
        self.run_import(expected=2)
        self.assertEqual(self.manifest.read_bytes(), before)

    def test_other_source_rejected_finding_is_a_collision_not_a_demotion(self):
        self.build_flow()
        self.run_import()
        before = self.manifest.read_bytes(), self.report.read_bytes()
        self.switch_source_folder()
        self.build_flow("rejected")
        self.run_import(expected=1)
        self.assertEqual((self.manifest.read_bytes(), self.report.read_bytes()), before)

    def test_report_aliases_to_manifest_and_inputs_leave_everything_unchanged(self):
        self.build_flow()
        for target in (self.manifest, self.live, self.packet / "results/DONE.md",
                       self.packet / "results/final.yaml", self.packet / "frozen/target.txt"):
            with self.subTest(alias=target.name):
                before = {p: p.read_bytes() for p in (self.manifest, self.live, target)}
                self.run_import("--report", target, expected=1)
                self.assertEqual({p: p.read_bytes() for p in before}, before)

    def test_hardlink_output_alias_is_rejected_without_mutation(self):
        self.build_flow()
        alias = self.root / "hardlink-report.md"
        os.link(self.live, alias)
        before = self.manifest.read_bytes(), self.live.read_bytes()
        self.run_import("--report", alias, expected=1)
        self.assertEqual((self.manifest.read_bytes(), self.live.read_bytes()), before)

    def test_symlink_parent_output_is_rejected_without_external_write(self):
        self.build_flow()
        target = self.root / "external"
        target.mkdir()
        link = self.root / "linked-output"
        link.symlink_to(target, target_is_directory=True)
        before = self.manifest.read_bytes()
        self.run_import("--report", link / "report.md", expected=1)
        self.assertEqual(self.manifest.read_bytes(), before)
        self.assertEqual(list(target.iterdir()), [])

    def test_explicit_mapping_contract_changes_imported_kind(self):
        self.build_flow()
        contract = yaml.safe_load((ROOT / "templates/review-import.yaml").read_text())
        contract["kind_by_severity"]["P2"] = "improvement"
        chosen = self.root / "chosen-contract.yaml"
        dump(chosen, contract)
        self.run_import("--contract", chosen)
        self.assertEqual(self.manifest_data()["observations"][0]["kind"], "improvement")

    def test_precommit_replace_failure_preserves_manifest_and_report(self):
        self.build_flow()
        self.report.parent.mkdir()
        self.report.write_text("Previous report.\n")
        before = self.manifest.read_bytes(), self.report.read_bytes()
        with patch.object(importer.os, "replace", side_effect=OSError("injected manifest commit failure")):
            with redirect_stdout(io.StringIO()), redirect_stderr(io.StringIO()):
                rc = importer.main(self.import_args())
        self.assertEqual(rc, 1)
        self.assertEqual((self.manifest.read_bytes(), self.report.read_bytes()), before)

    def test_postcommit_report_failure_discloses_committed_manifest_exit_six(self):
        self.build_flow()
        self.report.parent.mkdir()
        self.report.write_text("Previous report.\n")
        old_report = self.report.read_bytes()
        actual_write = importer._atomic_write

        def fail_report(path, raw):
            if path == self.report:
                raise OSError("injected report failure")
            return actual_write(path, raw)

        stderr = io.StringIO()
        with patch.object(importer, "_atomic_write", side_effect=fail_report):
            with redirect_stdout(io.StringIO()), redirect_stderr(stderr):
                rc = importer.main(self.import_args())
        self.assertEqual(rc, 6)
        self.assertIn("IMPORT-COMMITTED-REPORT-FAILED", stderr.getvalue())
        self.assertEqual(len(self.manifest_data()["observations"]), 1)
        self.assertEqual(self.report.read_bytes(), old_report)

    def test_concurrent_manifest_edit_is_preserved_before_commit(self):
        self.build_flow()
        actual_check = importer._check_outputs
        checks = 0
        external = self.manifest.read_bytes() + b"# concurrent human note\n"

        def human_changes_manifest(*args):
            nonlocal checks
            actual_check(*args)
            checks += 1
            if checks == 2:
                self.manifest.write_bytes(external)

        with patch.object(importer, "_check_outputs", side_effect=human_changes_manifest):
            with redirect_stdout(io.StringIO()), redirect_stderr(io.StringIO()):
                rc = importer.main(self.import_args())
        self.assertEqual(rc, 1)
        self.assertEqual(self.manifest.read_bytes(), external)
        self.assertFalse(self.report.exists())


    def test_unavailable_question_is_pending_and_foreign_question_id_is_rejected(self):
        self.include_question = True
        self.build_flow()
        self.run_import()
        data = self.manifest_data()
        question, = data["pending_issues"]
        self.assertEqual(question["id"], "RGQ-REC-Q")
        self.assertEqual(question["needed_input"], "Ask the policy owner.")
        data["observations"] = []  # Isolate the question collision from the finding collision.
        dump(self.manifest, data)
        before = self.manifest.read_bytes(), self.report.read_bytes()
        self.switch_source_folder()
        self.build_flow()
        self.run_import(expected=2)
        self.assertEqual((self.manifest.read_bytes(), self.report.read_bytes()), before)

    def test_external_ledger_reference_is_rejected_before_import_writes(self):
        receipt = self.build_flow()
        receipt["classification_ledger_ref"]["path"] = "../../outside.yaml"
        self.write_receipt(receipt)
        before = self.manifest.read_bytes()
        self.run_import(expected=1)
        self.assertEqual(self.manifest.read_bytes(), before)
        self.assertFalse(self.report.exists())


    def test_symlink_then_parent_output_cannot_alias_protected_packet_source(self):
        self.build_flow()
        link = self.root / "result-link"
        link.symlink_to(self.packet / "results", target_is_directory=True)
        protected = self.packet / "frozen/target.txt"
        bypass = link / ".." / "frozen/target.txt"
        self.assertEqual(bypass.resolve(), protected)
        before = self.manifest.read_bytes(), protected.read_bytes()
        for output_flag in ("--report", "--manifest"):
            with self.subTest(output=output_flag):
                proc = self.run_import(output_flag, bypass, expected=1)
                self.assertIn("parent traversal", proc.stdout + proc.stderr)
                self.assertEqual((self.manifest.read_bytes(), protected.read_bytes()), before)
                self.assertFalse(self.report.exists())
        self.assert_cli(0, "check", self.packet)

    def test_receipt_aba_between_validation_and_conversion_uses_validated_bytes(self):
        from review_gate import validate_review_result as validator
        receipt = self.build_flow()
        receipt_path = self.packet / "results/DONE.md"
        original = receipt_path.read_bytes()
        poison = copy.deepcopy(receipt)
        poison["findings"][0]["severity"] = "P1"
        self.write_receipt(poison)
        poison_bytes = receipt_path.read_bytes()
        receipt_path.write_bytes(original)
        original_validate = validator.validate
        original_build = importer.build_observations
        validated = []
        converted = []

        def validate_captured(*args, **kwargs):
            raw = kwargs.get("receipt_bytes")
            self.assertIsInstance(raw, bytes, "import must validate its captured receipt bytes")
            validated.append(raw)
            errors = original_validate(*args, **kwargs)
            if len(validated) == 1:
                receipt_path.write_bytes(poison_bytes)  # B appears only between validation and conversion.
            return errors

        def convert_captured(record, *args, **kwargs):
            actual = {key: value for key, value in record.items() if key != "_source_identity"}
            expected = yaml.safe_load(original.decode().split("---", 2)[1])["doc_review_result"]
            self.assertEqual(actual, expected, "conversion must consume the validated A, never substituted B")
            converted.append(actual)
            receipt_path.write_bytes(original)  # Return to A before the commit recheck.
            return original_build(record, *args, **kwargs)

        with patch.object(validator, "validate", side_effect=validate_captured), patch.object(
                importer, "build_observations", side_effect=convert_captured):
            with redirect_stdout(io.StringIO()), redirect_stderr(io.StringIO()):
                rc = importer.main(self.import_args())
        self.assertEqual(rc, 0)
        self.assertTrue(converted)
        self.assertTrue(validated)
        self.assertTrue(all(raw == original for raw in validated))
        observation, = self.manifest_data()["observations"]
        self.assertEqual(observation["kind"], "bug")  # Poisoned P1 would map to intent_gap.
        self.assertEqual(observation["review_ref"]["sha256"], hashlib.sha256(original).hexdigest())

    def test_receipt_changed_after_conversion_leaves_outputs_unchanged(self):
        self.build_flow()
        self.report.parent.mkdir()
        self.report.write_text("Existing report.\n")
        before = self.manifest.read_bytes(), self.report.read_bytes()
        receipt_path = self.packet / "results/DONE.md"
        original_build = importer.build_observations

        def change_after_conversion(*args, **kwargs):
            result = original_build(*args, **kwargs)
            # Valid body prose still changes receipt identity even when the ledger remains valid.
            receipt_path.write_bytes(receipt_path.read_bytes() + b"\nConcurrent receipt amendment.\n")
            return result

        with patch.object(importer, "build_observations", side_effect=change_after_conversion):
            with redirect_stdout(io.StringIO()), redirect_stderr(io.StringIO()):
                rc = importer.main(self.import_args())
        self.assertEqual(rc, 1)
        self.assertEqual((self.manifest.read_bytes(), self.report.read_bytes()), before)


    def mapping_contract(self, prefix):
        contract = yaml.safe_load((ROOT / "templates/review-import.yaml").read_text())
        contract["observation_id_prefix"] = prefix
        path = self.root / f"contract-{prefix}.yaml"
        dump(path, contract)
        return path

    def test_different_source_same_raw_finding_id_can_use_distinct_effective_prefixes(self):
        self.build_flow()
        first = self.mapping_contract("A-")
        self.run_import("--contract", first)
        self.switch_source_folder()
        self.build_flow()
        second = self.mapping_contract("B-")
        self.run_import("--contract", second)
        observations = self.manifest_data()["observations"]
        self.assertEqual([record["id"] for record in observations], ["A-F-01", "B-F-01"])
        self.assertNotEqual(observations[0]["review_ref"]["source_identity"],
                            observations[1]["review_ref"]["source_identity"])
        self.assertTrue(all(record["verified"] for record in observations))

    def test_same_custom_effective_id_rejects_foreign_rejected_finding_without_writes(self):
        self.build_flow()
        contract = self.mapping_contract("A-")
        self.run_import("--contract", contract)
        before = self.manifest.read_bytes(), self.report.read_bytes()
        self.switch_source_folder()
        self.build_flow("rejected")
        self.run_import("--contract", contract, expected=1)
        self.assertEqual((self.manifest.read_bytes(), self.report.read_bytes()), before)

    def test_same_custom_effective_id_rejects_foreign_verified_finding_without_writes(self):
        self.build_flow()
        contract = self.mapping_contract("A-")
        self.run_import("--contract", contract)
        before = self.manifest.read_bytes(), self.report.read_bytes()
        self.switch_source_folder()
        self.build_flow()
        proc = subprocess.run([str(ROOT / "bin/docloop"), "atb-import-review",
            *self.import_args("--contract", contract)], capture_output=True, text=True)
        self.assertIn(proc.returncode, (1, 2), proc.stdout + proc.stderr)
        self.assertEqual((self.manifest.read_bytes(), self.report.read_bytes()), before)


    def test_same_source_prefix_change_preserves_prior_verified_observation(self):
        self.build_flow()
        first = self.mapping_contract("A-")
        second = self.mapping_contract("B-")
        self.run_import("--contract", first)
        old, = self.manifest_data()["observations"]
        self.run_import("--contract", second)
        observations = {row["id"]: row for row in self.manifest_data()["observations"]}
        self.assertEqual(set(observations), {"A-F-01", "B-F-01"})
        self.assertEqual(observations["A-F-01"], old)
        self.assertTrue(observations["A-F-01"]["verified"])
        self.assertTrue(observations["B-F-01"]["verified"])
        self.assertNotIn("withdrawn", observations["A-F-01"]["review_ref"])

    def test_reaudited_postverification_statement_changes_cannot_mutate_import_outputs(self):
        for collection in ("candidate_atoms", "source_candidate_inventory"):
            with self.subTest(changed_collection=collection):
                self.setUp()
                receipt = self.build_flow()
                self.run_import()
                before = self.manifest.read_bytes(), self.report.read_bytes()
                self.reaudit_final_ledger(receipt, lambda final: final[collection][0].update(
                    statement="Unverified replacement statement after the verification attempt."))
                self.assert_cli(1, "validate-result", self.packet, "results/DONE.md")
                self.run_import(expected=1)
                self.assertEqual((self.manifest.read_bytes(), self.report.read_bytes()), before)


    def test_rejected_receipt_in_another_same_source_namespace_preserves_existing_observation(self):
        self.build_flow()
        first = self.mapping_contract("A-")
        second = self.mapping_contract("B-")
        self.run_import("--contract", first)
        original, = self.manifest_data()["observations"]
        self.build_flow("rejected")
        self.run_import("--contract", second)
        remaining, = self.manifest_data()["observations"]
        self.assertEqual(remaining, original)
        self.assertTrue(remaining["verified"])
        self.assertNotIn("withdrawn", remaining["review_ref"])

    def test_rejected_receipt_in_same_source_and_namespace_still_demotes_observation(self):
        self.build_flow()
        contract = self.mapping_contract("A-")
        self.run_import("--contract", contract)
        self.build_flow("rejected")
        self.run_import("--contract", contract)
        observation, = self.manifest_data()["observations"]
        self.assertEqual(observation["id"], "A-F-01")
        self.assertFalse(observation["verified"])
        self.assertIn("rejected", observation["review_ref"]["withdrawn"]["why"])


if __name__ == "__main__":
    unittest.main()
