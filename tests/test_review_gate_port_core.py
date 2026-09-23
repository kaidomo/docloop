#!/usr/bin/env python3
"""Public contract-2 packet flow with synthetic review records, no model execution.

The fixture uses the upstream ledger/receipt shapes from test_verify_gate.py and
review_output_helpers.py at docauth 93b256a, with only one grounded finding. All
prepared artifacts and verification/delivery traces come from the public CLI.
"""
from pathlib import Path
import copy
import hashlib
import json
import subprocess
import sys
import tempfile
import unittest

import yaml

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from lib.review_gate.validate_review_intermediate import record_digest

BIN = ROOT / "bin/docloop"
SOURCE = "# Requirements\nThe retry limit is three.\nThe summary says four retries.\n"


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def dump(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(yaml.safe_dump(value, allow_unicode=True, sort_keys=False), encoding="utf-8")


class ModernPacketFixture:
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="docloop-port-core-")
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name).resolve()
        self.review = self.root / "review"
        self.review.mkdir()
        (self.review / "target.md").write_text(SOURCE, encoding="utf-8")
        self.packet = self.review / "review-gate" / "modern"

    def cli(self, *args):
        return subprocess.run([str(BIN), "review-gate", *map(str, args)],
                              capture_output=True, text=True)

    def assert_cli(self, expected, *args):
        proc = self.cli(*args)
        self.assertEqual(proc.returncode, expected,
                         f"{' '.join(map(str, args))}\n{proc.stdout}\n{proc.stderr}")
        return proc

    def prepare(self, *extra):
        self.assert_cli(0, "prepare", self.review, "modern", "target.md",
                        "--unassured", "--no-terms", "--no-docmodel", "--editing-state", "frozen",
                        "--target-maturity", "complete", *extra)
        self.run = yaml.safe_load((self.packet / "RUN.yaml").read_text())
        self.snapshot = "sha256:" + digest(self.packet / "frozen/target.txt")
        self.prepared_marker = (self.packet / "COMPLETE.json").read_bytes()

    def ledger(self, status):
        finding = {
            "record_id": "REC-F", "finding_id": "F-01",
            "candidate_atom_refs": ["ATOM-F"], "source_candidate_refs": ["SC-F"],
            "snapshot_id": self.snapshot, "evidence_anchors": ["L2", "L3"],
            "severity": "P2", "judgment_provenance": "Retry limits differ in L2 and L3.",
            "status": status, "counter_citation_verdict": "none",
        }
        if status == "judgment_unavailable":
            finding["judgment_unavailable"] = {
                "reason": "evidence_insufficient", "basis": "External policy is unavailable.",
                "needed_input": "Provide the authoritative retry policy.",
                "attempts": [{"verifier_id": "synthetic-finding-verifier", "result": "unresolved",
                              "snapshot_id": self.snapshot, "evidence": "results/verification.md"}],
            }
        finding["public_record_digest"] = record_digest("finding", finding)
        return {
            "schema_version": 2, "snapshot_id": self.snapshot, "target": "target.md",
            "state": "open" if status == "discovered" else "closed",
            "source_candidate_inventory": [{"source_candidate_id": "SC-F", "lens": "L1",
                "statement": "Retry limits differ.", "evidence_anchors": ["L2", "L3"]}],
            "candidate_atoms": [{"candidate_atom_id": "ATOM-F", "source_candidate_refs": ["SC-F"],
                "statement": "Retry limits differ.", "evidence_anchors": ["L2", "L3"],
                "classification_basis": {"co_reference": "proven", "semantic_values": "different",
                    "presentation_rule": "not_applicable", "evidence": "Same retry limit at L2 and L3."}}],
            "findings": [finding], "questions": [], "drifts": [], "suppressed": [], "nonissues": [],
            "counter_evidence": [],
            "classification_ledger": [{"candidate_atom_id": "ATOM-F", "outcome": "finding",
                "target_record_id": "REC-F", "evidence_anchors": ["L2", "L3"]}],
        }

    def build_flow(self, final_status="verified"):
        self.prepare()
        dump(self.packet / "results/entry.yaml", {"review_intermediate": self.ledger("discovered")})
        self.assert_cli(0, "validate-intermediate", self.packet, "results/entry.yaml")
        self.assert_cli(0, "verify-gate", self.packet, "verify", "results/entry.yaml")
        final = self.ledger(final_status)
        dump(self.packet / "results/final.yaml", {"review_intermediate": final})
        delivery = "# Findings\n\nF-01: Retry limits differ at L2 and L3.\n"
        (self.packet / "results/findings.md").write_text(delivery)
        (self.packet / "results/lens.md").write_text(delivery)
        (self.packet / "results/verification.md").write_text("Synthetic verification evidence, not a real model call.\n")
        self.assert_cli(0, "audit-delivery", self.packet, "delivery", "results/final.yaml",
                        "results/findings.md", "--lens", "results/lens.md")
        receipt = json.loads((self.packet / "deterministic/RECEIPT_SCAFFOLD.json").read_text())
        receipt.update({
            "schema_version": 2, "route_id": "review-gate", "route_trace": "Synthetic CLI contract fixture.",
            "snapshot_id": self.snapshot, "target": "target.md",
            "verifiers": [{"verifier_id": f"synthetic-final-{i}", "result": "pass",
                           "snapshot_id": self.snapshot, "evidence": "results/verification.md"} for i in range(3)],
            "findings": copy.deepcopy(final["findings"]), "questions": [], "drifts": [],
            "unassured_mode": True, "unassured_accepted_by": "synthetic fixture owner",
            "structure_axis_reason": "No convention profile supplied.",
            "execution": {"run_ids": [], "lens_rounds": 1, "lens_rounds_reason": "Synthetic single round."},
            "scale_disclosure": {"target_volume": {"lines": 3, "snapshot_id": self.snapshot},
                "planned_lens_rounds": 1, "configuration": [{"name": "fixture", "count": 1}],
                "derived_total_agents": 1},
            "execution_status": "complete", "document_clearance": "indeterminate" if final_status == "judgment_unavailable" else "findings_present",
            "packet_binding": {"run_id": "modern", "target_source": "target.md", "target_snapshot": self.snapshot,
                "prepared_payload_digest_sha256": json.loads(self.prepared_marker)["payload_digest_sha256"],
                "receipt_path": "results/DONE.md"},
            "classification_ledger_ref": {"path": "results/final.yaml", "sha256": digest(self.packet / "results/final.yaml"),
                "snapshot_id": self.snapshot, "schema_version": 2},
            "verify_gate_ref": {"path": "results/verify/verify_gate_trace.json", "sha256": digest(self.packet / "results/verify/verify_gate_trace.json")},
            "anchor_guard_ref": {"path": "results/findings.md", "sha256": digest(self.packet / "results/findings.md"),
                "trace_path": "results/delivery/anchor_guard_trace.json", "trace_sha256": digest(self.packet / "results/delivery/anchor_guard_trace.json")},
        })
        self.write_receipt(receipt)
        return receipt

    def reaudit_final_ledger(self, receipt, mutate):
        """Change only final ledger/delivery artifacts, preserving prior verification."""
        protected = {path: path.read_bytes() for path in (self.packet / "results/verify").rglob("*")
                     if path.is_file()}
        protected[self.packet / "results/entry.yaml"] = (self.packet / "results/entry.yaml").read_bytes()
        protected[self.packet / "COMPLETE.json"] = self.prepared_marker
        final_path = self.packet / "results/final.yaml"
        final = yaml.safe_load(final_path.read_text())["review_intermediate"]
        mutate(final)
        dump(final_path, {"review_intermediate": final})
        self.assert_cli(0, "validate-intermediate", self.packet, "results/final.yaml", "--closed")
        self.assert_cli(0, "audit-delivery", self.packet, "delivery-updated", "results/final.yaml",
                        "results/findings.md", "--lens", "results/lens.md")
        receipt["classification_ledger_ref"]["sha256"] = digest(final_path)
        receipt["anchor_guard_ref"]["trace_path"] = "results/delivery-updated/anchor_guard_trace.json"
        receipt["anchor_guard_ref"]["trace_sha256"] = digest(self.packet / receipt["anchor_guard_ref"]["trace_path"])
        self.write_receipt(receipt)
        self.assertEqual({path: path.read_bytes() for path in protected}, protected)
        self.assert_cli(0, "check", self.packet)

    def write_receipt(self, receipt):
        (self.packet / "results/DONE.md").write_text("---\n" + yaml.safe_dump(
            {"doc_review_result": receipt}, allow_unicode=True, sort_keys=False) + "---\n# Review result\n")


class ModernPacketTests(ModernPacketFixture, unittest.TestCase):
    def test_prepare_verify_delivery_and_receipt_complete(self):
        self.build_flow()
        self.assert_cli(0, "check", self.packet)
        self.assertEqual((self.packet / "COMPLETE.json").read_bytes(), self.prepared_marker)
        self.assert_cli(0, "validate-result", self.packet, "results/DONE.md")

    def test_terminal_unavailable_is_indeterminate_exit_five(self):
        self.build_flow("judgment_unavailable")
        proc = self.assert_cli(5, "validate-result", self.packet, "results/DONE.md")
        self.assertIn("COMPLETE-INDETERMINATE", proc.stdout)


    def test_archived_input_removal_cannot_downgrade_a_new_packet(self):
        for name in ("front_gate_profile.yaml", "front_gate_intake.yaml", "front_gate_input_gate.yaml",
                     "front_gate_decisions_state.json"):
            with self.subTest(archive=name):
                self.setUp()
                self.prepare()
                self.assert_cli(0, "check", self.packet)
                (self.packet / name).unlink()
                proc = self.assert_cli(1, "check", self.packet)
                self.assertNotIn("Traceback", proc.stderr)

    def test_wrong_receipt_contract_version_never_uses_legacy_fallback(self):
        receipt = self.build_flow()
        self.assert_cli(0, "validate-result", self.packet, "results/DONE.md")
        for value in (None, 1, True, 999):
            with self.subTest(contract_version=value):
                altered = copy.deepcopy(receipt)
                if value is None:
                    altered.pop("docloop_contract_version")
                else:
                    altered["docloop_contract_version"] = value
                self.write_receipt(altered)
                proc = self.assert_cli(1, "validate-result", self.packet, "results/DONE.md")
                self.assertIn("docloop_contract_version", proc.stdout + proc.stderr)

    def test_receipt_cannot_claim_clear_for_terminal_unavailable(self):
        receipt = self.build_flow("judgment_unavailable")
        self.assert_cli(5, "validate-result", self.packet, "results/DONE.md")
        receipt["document_clearance"] = "clear"
        self.write_receipt(receipt)
        proc = self.assert_cli(1, "validate-result", self.packet, "results/DONE.md")
        self.assertIn("document_clearance", proc.stdout + proc.stderr)

    def test_verification_unit_tamper_blocks_receipt(self):
        self.build_flow()
        self.assert_cli(0, "validate-result", self.packet, "results/DONE.md")
        unit = next((self.packet / "results/verify/verify_units").glob("*.md"))
        unit.write_text(unit.read_text() + "\nInjected unsupported evidence.\n")
        self.assert_cli(1, "validate-result", self.packet, "results/DONE.md")
        self.assert_cli(0, "check", self.packet)

    def test_incomplete_result_attempts_cannot_support_done(self):
        self.build_flow()
        self.assert_cli(0, "validate-result", self.packet, "results/DONE.md")
        for attempt in ("verify", "delivery"):
            with self.subTest(attempt=attempt):
                marker = self.packet / "results" / attempt / "COMPLETE.json"
                before = marker.read_bytes()
                marker.unlink()
                proc = self.assert_cli(1, "validate-result", self.packet, "results/DONE.md")
                self.assertNotIn("Traceback", proc.stderr)
                marker.write_bytes(before)

    def test_result_attempt_reuse_preserves_existing_artifacts(self):
        self.build_flow()
        before = {str(p.relative_to(self.packet)): p.read_bytes()
                  for p in (self.packet / "results").rglob("*") if p.is_file()}
        self.assert_cli(1, "verify-gate", self.packet, "verify", "results/entry.yaml")
        self.assert_cli(1, "audit-delivery", self.packet, "delivery", "results/final.yaml",
                        "results/findings.md", "--lens", "results/lens.md")
        after = {str(p.relative_to(self.packet)): p.read_bytes()
                 for p in (self.packet / "results").rglob("*") if p.is_file()}
        self.assertEqual(after, before)

    def test_same_bytes_in_another_delivery_file_do_not_reuse_audit(self):
        receipt = self.build_flow()
        self.assert_cli(0, "validate-result", self.packet, "results/DONE.md")
        (self.packet / "results/other-findings.md").write_bytes((self.packet / "results/findings.md").read_bytes())
        receipt["anchor_guard_ref"]["path"] = "results/other-findings.md"
        self.write_receipt(receipt)
        proc = self.assert_cli(1, "validate-result", self.packet, "results/DONE.md")
        self.assertIn("synth.path", proc.stdout + proc.stderr)


    def test_receipt_schema_version_requires_supported_integer_through_public_cli(self):
        receipt = self.build_flow()
        self.assert_cli(0, "validate-result", self.packet, "results/DONE.md")
        for version in ("bogus", None, True, 2.5):
            with self.subTest(schema_version=version):
                changed = copy.deepcopy(receipt)
                changed["schema_version"] = version
                self.write_receipt(changed)
                proc = self.assert_cli(1, "validate-result", self.packet, "results/DONE.md")
                self.assertIn("schema_version", proc.stdout + proc.stderr)
                self.assertNotIn("Traceback", proc.stderr)

    def test_rebuilt_sourceless_verify_artifacts_cannot_support_modern_receipt(self):
        from lib.review_gate.review_verify_gate import build_verify_trace, build_verification_units
        receipt = self.build_flow()
        self.assert_cli(0, "validate-result", self.packet, "results/DONE.md")
        entry = yaml.safe_load((self.packet / "results/verify/verify_gate_ledger.yaml").read_text())
        attempt = self.packet / "results/verify"
        # Recompute a mutually consistent older-style package, not a trivial stale hash.
        events = build_verify_trace(entry, source_snapshot=None)
        units = build_verification_units(entry, source_snapshot=None)
        self.assertTrue(units)
        for previous in (attempt / "verify_units").iterdir():
            previous.unlink()
        for name, content in units:
            (attempt / "verify_units" / name).write_text(content, encoding="utf-8")
        trace = attempt / "verify_gate_trace.json"
        trace.write_text(json.dumps({"review_verify_gate_trace": events}), encoding="utf-8")
        receipt["verify_gate_ref"]["sha256"] = digest(trace)
        self.write_receipt(receipt)
        self.assert_cli(0, "check", self.packet)
        self.assertEqual((self.packet / "COMPLETE.json").read_bytes(), self.prepared_marker)
        proc = self.assert_cli(1, "validate-result", self.packet, "results/DONE.md")
        self.assertIn("source", proc.stdout + proc.stderr)
        self.assertNotIn("Traceback", proc.stderr)


    def test_delivery_source_changed_after_audit_invalidates_unchanged_receipt(self):
        self.build_flow()
        self.assert_cli(0, "validate-result", self.packet, "results/DONE.md")
        source = self.packet / "results/lens.md"
        source.write_text(source.read_text() + "\nOmitted candidate anchored at L999.\n")
        self.assert_cli(1, "validate-result", self.packet, "results/DONE.md")
        self.assert_cli(0, "check", self.packet)

    def test_empty_audit_sources_cannot_be_hidden_by_updated_trace_hash(self):
        receipt = self.build_flow()
        self.assert_cli(0, "validate-result", self.packet, "results/DONE.md")
        path = self.packet / "results/delivery/anchor_guard_trace.json"
        trace = json.loads(path.read_text())
        self.assertTrue(trace["review_anchor_guard_trace"]["sources"])
        trace["review_anchor_guard_trace"]["sources"] = []
        path.write_text(json.dumps(trace))
        receipt["anchor_guard_ref"]["trace_sha256"] = digest(path)
        self.write_receipt(receipt)
        self.assert_cli(1, "validate-result", self.packet, "results/DONE.md")


    def test_reaudited_final_atom_statement_cannot_replace_verified_entry_semantics(self):
        receipt = self.build_flow()
        self.assert_cli(0, "validate-result", self.packet, "results/DONE.md")
        self.reaudit_final_ledger(receipt, lambda ledger: ledger["candidate_atoms"][0].update(
            statement="Invented final claim that no verifier received."))
        self.assert_cli(1, "validate-result", self.packet, "results/DONE.md")

    def test_reaudited_source_inventory_statement_cannot_replace_verified_candidate(self):
        receipt = self.build_flow()
        self.assert_cli(0, "validate-result", self.packet, "results/DONE.md")
        self.reaudit_final_ledger(receipt, lambda ledger: ledger["source_candidate_inventory"][0].update(
            statement="A different source candidate introduced after verification."))
        self.assert_cli(1, "validate-result", self.packet, "results/DONE.md")


class CandidatePreparationTests(ModernPacketFixture, unittest.TestCase):
    def candidate_inputs(self, titles, *, approved=True, selection=None):
        profile = yaml.safe_load((ROOT / "tests/fixtures/review-gate/convention/synthetic-profile.yaml").read_text())
        dump(self.review / "profile.yaml", profile)
        intake = {"schema_version": 1, "phase": "pre_lens", "profile_id": profile["profile_id"],
                  "template_id": "candidate-target-v1", "target_document": "target.md",
                  "target_snapshot": "sha256:" + digest(self.review / "target.md"),
                  "recorded_at": "2026-09-23", "records": [],
                  "profile_applicability": {"result": "inapplicable", "reason": "Different target template.",
                                             "observed_sections": ["Requirements"]}}
        if selection:
            intake["docmodel_selection"] = {"approval": "answered", "response": selection}
        dump(self.review / "intake.yaml", intake)
        approvals = []
        args = []
        for i, sections in enumerate(titles):
            name = f"candidate-{i}.yaml"
            model = {"meta": {"template": "candidate-target-v1", "updated_at": "2026-09-23",
                              "approval_state": "approved", "approved_by": "synthetic owner",
                              "suppression_eligible": True},
                     "sections": [{"id": str(j), "title": title, "role": "canonical"}
                                  for j, title in enumerate(sections)]}
            dump(self.review / name, model)
            approvals.append({"id": f"APR-{i}", "docmodel_path": name,
                "docmodel_sha256": digest(self.review / name), "status": "approved" if approved else "revoked",
                "approved_by": "synthetic owner", "approved_at": "2026-09-23", "evidence": "Synthetic approval fixture."})
            args += ["--docmodel-candidate", name]
        dump(self.review / "approvals.yaml", {"meta": {"target": "synthetic", "updated_at": "2026-09-23"},
                                                "approvals": approvals})
        return args + ["--docmodel-approvals", "approvals.yaml", "--convention-profile", "profile.yaml",
                       "--convention-intake", "intake.yaml"]

    def candidate_prepare(self, args, expected=0):
        return self.assert_cli(expected, "prepare", self.review, "modern", "target.md", "--unassured",
                               "--no-terms", "--editing-state", "frozen", "--target-maturity", "complete", *args)

    def test_zero_matching_candidates_leaves_structure_undetermined(self):
        self.candidate_prepare(self.candidate_inputs([["Other section"]]))
        scaffold = json.loads((self.packet / "deterministic/RECEIPT_SCAFFOLD.json").read_text())
        self.assertEqual(scaffold["structure_axis"], "undetermined")
        self.assertFalse((self.packet / "lens/L3/DOCMODEL.yaml").exists())

    def test_single_approved_match_freezes_identical_lens_and_trace_input(self):
        self.candidate_prepare(self.candidate_inputs([["Requirements"]]))
        scaffold = json.loads((self.packet / "deterministic/RECEIPT_SCAFFOLD.json").read_text())
        self.assertEqual(scaffold["structure_axis"], "judged")
        selected = (self.review / "candidate-0.yaml").read_bytes()
        self.assertEqual((self.packet / "lens/L3/DOCMODEL.yaml").read_bytes(), selected)
        self.assertEqual((self.packet / "front_gate_docmodel.yaml").read_bytes(), selected)
        (self.review / "approvals.yaml").write_text("later external change")
        self.assert_cli(0, "check", self.packet)

    def test_multiple_matches_without_selection_are_undetermined(self):
        self.candidate_prepare(self.candidate_inputs([["Requirements"], ["Requirements"]]))
        scaffold = json.loads((self.packet / "deterministic/RECEIPT_SCAFFOLD.json").read_text())
        self.assertEqual(scaffold["structure_axis"], "undetermined")
        self.assertFalse((self.packet / "lens/L3/DOCMODEL.yaml").exists())

    def test_explicit_selection_among_multiple_matches_is_frozen(self):
        self.candidate_prepare(self.candidate_inputs([["Requirements"], ["Requirements"]], selection="candidate-1.yaml"))
        self.assertEqual((self.packet / "lens/L3/DOCMODEL.yaml").read_bytes(),
                         (self.review / "candidate-1.yaml").read_bytes())

    def test_invalid_explicit_selection_is_rejected_before_packet_creation(self):
        args = self.candidate_inputs([["Other section"]], selection="candidate-0.yaml")
        self.candidate_prepare(args, expected=1)
        self.assertFalse(self.packet.exists())

    def test_empty_sections_fail_before_packet_creation(self):
        self.candidate_prepare(self.candidate_inputs([[]]), expected=1)
        self.assertFalse(self.packet.exists())


    def test_revoked_candidate_is_not_selected(self):
        self.candidate_prepare(self.candidate_inputs([["Requirements"]], approved=False))
        scaffold = json.loads((self.packet / "deterministic/RECEIPT_SCAFFOLD.json").read_text())
        self.assertEqual(scaffold["structure_axis"], "undetermined")
        self.assertFalse((self.packet / "lens/L3/DOCMODEL.yaml").exists())

    def test_frozen_approval_changes_fail_packet_integrity(self):
        self.candidate_prepare(self.candidate_inputs([["Requirements"]]))
        self.assert_cli(0, "check", self.packet)
        frozen = self.packet / "frozen/docmodel-approvals.yaml"
        self.assertTrue(frozen.is_file())
        frozen.write_text("tampered approval")
        self.assert_cli(1, "check", self.packet)


class RegistryPacketTests(ModernPacketFixture, unittest.TestCase):
    """Shared decisions/signoff remain frozen authority through all result producers."""

    def write_registries(self):
        (self.review / "decision-source.md").write_text("Synthetic approved decision source.\n")
        snapshot = {"kind": "asistobe_manifest_snapshot", "source_manifest": "manifest.yaml",
            "source_sha256": "0" * 64, "created": "2026-09-23",
            "chunk": {"id": "c1", "status": "approved", "issues": [{"issue": "Retry policy",
                "recommendation": "Keep the declared policy.", "basis": "Fixture decision source",
                "owner": "synthetic owner", "subject": "retry"}]}}
        dump(self.review / "signoff.yaml", snapshot)
        for name, decision_id in (("decisions.yaml", "LOCAL-1"), ("shared.yaml", "SHARED-1")):
            meta = {"target": "synthetic", "source_ref": "decision-source.md",
                "source_version_hash": digest(self.review / "decision-source.md"), "updated_at": "2026-09-23",
                "registration_policy": "human_signoff_required"}
            if name == "decisions.yaml":
                meta["includes"] = ["shared.yaml"]
            dump(self.review / name, {"meta": meta, "decisions": [{"id": decision_id,
                "decision": "Keep the declared policy.", "status": "재론금지", "date": "2026-09-23",
                "evidence": "Synthetic source", "subject": "retry", "signoff": {
                    "kind": "asistobe_manifest_snapshot", "ref": "signoff.yaml",
                    "sha256": digest(self.review / "signoff.yaml"), "chunk_id": "c1", "issue_index": 0}}]})

    def prepare(self, *extra):
        self.write_registries()
        mode = "--decisions-unchecked" if getattr(self, "unchecked", False) else "--decisions"
        self.assert_cli(0, "prepare", self.review, "modern", "target.md", mode, "decisions.yaml",
                        "--no-terms", "--no-docmodel", "--editing-state", "frozen", "--target-maturity", "complete")
        self.run = yaml.safe_load((self.packet / "RUN.yaml").read_text())
        self.snapshot = "sha256:" + digest(self.packet / "frozen/target.txt")
        self.prepared_marker = (self.packet / "COMPLETE.json").read_bytes()

    def ledger(self, status):
        ledger = super().ledger(status)
        if getattr(self, "unchecked", False):
            return ledger
        shared = "frozen/decisions.yaml" if getattr(self, "use_local_authority", False) else "frozen/provenance/registry-2.yaml"
        authority = {"kind": "decision_registry", "path": shared,
                     "sha256": digest(self.packet / shared),
                     "decision_id": "LOCAL-1" if getattr(self, "use_local_authority", False) else "SHARED-1"}
        suppressed = {"record_id": "REC-S", "candidate_atom_refs": ["ATOM-S"], "source_candidate_refs": ["SC-S"],
                      "snapshot_id": self.snapshot, "evidence_anchors": ["L2"],
                      "rationale": "Existing approved retry decision.", "authority_ref": authority}
        suppressed["public_record_digest"] = record_digest("suppressed", suppressed)
        ledger["suppressed"] = [suppressed]
        ledger["source_candidate_inventory"].append({"source_candidate_id": "SC-S", "lens": "L2",
            "statement": "Existing retry decision.", "evidence_anchors": ["L2"]})
        ledger["candidate_atoms"].append({"candidate_atom_id": "ATOM-S", "source_candidate_refs": ["SC-S"],
            "statement": "Existing retry decision.", "evidence_anchors": ["L2"],
            "classification_basis": {"co_reference": "proven", "semantic_values": "different",
                "presentation_rule": "not_applicable", "evidence": "SHARED-1 decides the rule."}})
        ledger["classification_ledger"].append({"candidate_atom_id": "ATOM-S", "outcome": "suppressed",
                                              "target_record_id": "REC-S", "evidence_anchors": ["L2"]})
        return ledger

    def build_flow(self, final_status="verified"):
        receipt = super().build_flow(final_status)
        if not getattr(self, "unchecked", False):
            receipt["unassured_mode"] = False
            receipt.pop("unassured_accepted_by", None)
        self.write_receipt(receipt)
        return receipt

    def test_final_candidate_row_reordering_preserves_verified_semantics(self):
        receipt = self.build_flow()
        self.assert_cli(0, "validate-result", self.packet, "results/DONE.md")

        def reorder(final):
            self.assertGreater(len(final["candidate_atoms"]), 1)
            self.assertGreater(len(final["source_candidate_inventory"]), 1)
            final["candidate_atoms"].reverse()
            final["source_candidate_inventory"].reverse()

        self.reaudit_final_ledger(receipt, reorder)
        self.assert_cli(0, "validate-result", self.packet, "results/DONE.md")

    def test_reaudited_suppression_payload_change_cannot_replace_verified_record(self):
        receipt = self.build_flow()
        self.assert_cli(0, "validate-result", self.packet, "results/DONE.md")

        def change_suppression(final):
            record = final["suppressed"][0]
            record["rationale"] = "A different unverified reason for suppressing this candidate."
            record["public_record_digest"] = record_digest("suppressed", record)

        self.reaudit_final_ledger(receipt, change_suppression)
        self.assert_cli(1, "validate-result", self.packet, "results/DONE.md")

    def test_root_decision_of_shared_union_passes_default_intermediate_and_full_flow(self):
        self.use_local_authority = True
        self.build_flow()
        # build_flow invokes public validate-intermediate with no optional --run-root.
        self.assert_cli(0, "validate-intermediate", self.packet, "results/final.yaml", "--closed")
        self.assert_cli(0, "validate-result", self.packet, "results/DONE.md")

    def test_shared_signoff_authority_survives_original_registry_changes(self):
        receipt = self.build_flow()
        self.assertEqual(receipt["decision_registry_state"], "checked")
        self.assert_cli(0, "validate-result", self.packet, "results/DONE.md")
        for name in ("decisions.yaml", "shared.yaml", "signoff.yaml", "decision-source.md"):
            (self.review / name).write_text("Later external change.\n")
        self.assert_cli(0, "check", self.packet)
        self.assert_cli(0, "validate-result", self.packet, "results/DONE.md")
        self.assertEqual((self.packet / "COMPLETE.json").read_bytes(), self.prepared_marker)

    def test_unchecked_registry_stays_unassured_and_cannot_self_declare_checked(self):
        self.unchecked = True
        receipt = self.build_flow()
        self.assertEqual(receipt["decision_registry_state"], "present_unchecked")
        self.assert_cli(0, "validate-result", self.packet, "results/DONE.md")
        receipt["decision_registry_state"] = "checked"
        self.write_receipt(receipt)
        proc = self.assert_cli(1, "validate-result", self.packet, "results/DONE.md")
        self.assertIn("decision_registry_state", proc.stdout + proc.stderr)

    def test_invalid_signoff_bytes_fail_before_prepared_packet_creation(self):
        self.write_registries()
        (self.review / "signoff.yaml").write_text("changed after signoff reference")
        self.assert_cli(1, "prepare", self.review, "modern", "target.md", "--decisions", "decisions.yaml",
                        "--no-terms", "--no-docmodel", "--editing-state", "frozen", "--target-maturity", "complete")
        self.assertFalse(self.packet.exists())

    def test_cyclic_shared_includes_fail_before_prepared_packet_creation(self):
        self.write_registries()
        shared = yaml.safe_load((self.review / "shared.yaml").read_text())
        shared["meta"]["includes"] = ["decisions.yaml"]
        dump(self.review / "shared.yaml", shared)
        self.assert_cli(1, "prepare", self.review, "modern", "target.md", "--decisions", "decisions.yaml",
                        "--no-terms", "--no-docmodel", "--editing-state", "frozen", "--target-maturity", "complete")
        self.assertFalse(self.packet.exists())


class DeferredPacketTests(ModernPacketFixture, unittest.TestCase):
    def open_ledger(self):
        ledger = self.ledger("discovered")
        question = {"record_id": "REC-Q", "status": "open", "convention_slot": "retry-policy",
            "dependent_atom_refs": ["ATOM-Q"], "resolution_derived_atom_refs": [],
            "snapshot_id": self.snapshot, "evidence_anchors": ["L2"],
            "classification_verification": {"result": "unresolved", "verifier_id": "pending",
                "evidence": "Verification has not started."}}
        question["public_record_digest"] = record_digest("question", question)
        ledger["questions"] = [question]
        ledger["source_candidate_inventory"].append({"source_candidate_id": "SC-Q", "lens": "L3",
            "statement": "Which retry policy applies?", "evidence_anchors": ["L2"]})
        ledger["candidate_atoms"].append({"candidate_atom_id": "ATOM-Q", "source_candidate_refs": ["SC-Q"],
            "statement": "Which retry policy applies?", "evidence_anchors": ["L2"],
            "classification_basis": {"co_reference": "unknown", "semantic_values": "unknown",
                "presentation_rule": "unknown", "evidence": "Requires owner input."}})
        ledger["classification_ledger"].append({"candidate_atom_id": "ATOM-Q", "outcome": "question",
            "target_record_id": "REC-Q", "evidence_anchors": ["L2"]})
        return ledger

    def write_open_delivery(self):
        final = self.open_ledger()
        dump(self.packet / "results/final.yaml", {"review_intermediate": final})
        for name in ("findings.md", "lens.md"):
            (self.packet / "results" / name).write_text("# Open review\n\nF-01 at L2 and L3, question at L2.\n")
        return final

    def test_prepared_in_progress_review_can_deliver_open_questions_as_deferred(self):
        self.prepare("--editing-state", "in_progress")
        final = self.write_open_delivery()
        self.assert_cli(0, "validate-intermediate", self.packet, "results/final.yaml")
        self.assert_cli(0, "audit-delivery", self.packet, "delivery", "results/final.yaml",
                        "results/findings.md", "--lens", "results/lens.md")
        receipt = json.loads((self.packet / "deterministic/RECEIPT_SCAFFOLD.json").read_text())
        receipt.update({"schema_version": 2, "route_id": "review-gate", "route_trace": "Synthetic deferred review.",
            "snapshot_id": self.snapshot, "target": "target.md", "verifiers": [],
            "findings": copy.deepcopy(final["findings"]), "questions": copy.deepcopy(final["questions"]), "drifts": [],
            "unassured_mode": True, "unassured_accepted_by": "synthetic owner",
            "structure_axis_reason": "No convention profile supplied.",
            "execution": {"run_ids": [], "lens_rounds": 1, "lens_rounds_reason": "Synthetic single round."},
            "scale_disclosure": {"target_volume": {"lines": 3, "snapshot_id": self.snapshot},
                "planned_lens_rounds": 1, "configuration": [{"name": "fixture", "count": 1}], "derived_total_agents": 1},
            "execution_status": "incomplete", "document_clearance": "indeterminate",
            "packet_binding": {"run_id": "modern", "target_source": "target.md", "target_snapshot": self.snapshot,
                "prepared_payload_digest_sha256": json.loads(self.prepared_marker)["payload_digest_sha256"],
                "receipt_path": "results/DONE.md"},
            "classification_ledger_ref": {"path": "results/final.yaml", "sha256": digest(self.packet / "results/final.yaml"),
                "snapshot_id": self.snapshot, "schema_version": 2},
            "anchor_guard_ref": {"path": "results/findings.md", "sha256": digest(self.packet / "results/findings.md"),
                "trace_path": "results/delivery/anchor_guard_trace.json", "trace_sha256": digest(self.packet / "results/delivery/anchor_guard_trace.json")}})
        self.assertNotIn("verify_gate_ref", receipt)
        self.write_receipt(receipt)
        self.assert_cli(3, "validate-result", self.packet, "results/DONE.md")
        self.assert_cli(0, "check", self.packet)
        self.assertEqual((self.packet / "COMPLETE.json").read_bytes(), self.prepared_marker)
        receipt["input_gate"]["editing_state"] = "frozen"
        self.write_receipt(receipt)
        self.assert_cli(1, "validate-result", self.packet, "results/DONE.md")

    def test_frozen_complete_packet_still_rejects_open_ledger_delivery(self):
        self.prepare()
        self.write_open_delivery()
        self.assert_cli(1, "audit-delivery", self.packet, "delivery", "results/final.yaml",
                        "results/findings.md", "--lens", "results/lens.md")
        self.assertFalse((self.packet / "results/delivery/COMPLETE.json").exists())


class NonpublicVerificationInputTests(ModernPacketFixture, unittest.TestCase):
    def ledger(self, status):
        ledger = super().ledger(status)
        finding = ledger["findings"][0]
        finding["counter_citation_verdict"] = "full"
        finding["public_record_digest"] = record_digest("finding", finding)
        counter = {"record_id": "CE-F", "finding_record_id": "REC-F", "resolution": "full",
                   "anchors": ["L2"], "snapshot_id": self.snapshot}
        counter["public_record_digest"] = record_digest("counter_evidence", counter)
        ledger["counter_evidence"] = [counter]
        nonissue = {"record_id": "REC-N", "candidate_atom_refs": ["ATOM-N"], "source_candidate_refs": ["SC-N"],
                    "snapshot_id": self.snapshot, "evidence_anchors": ["L3"],
                    "rationale": "These phrases describe separate contexts."}
        nonissue["public_record_digest"] = record_digest("nonissue", nonissue)
        ledger["nonissues"] = [nonissue]
        ledger["source_candidate_inventory"].append({"source_candidate_id": "SC-N", "lens": "L1",
            "statement": "Different contexts use different phrases.", "evidence_anchors": ["L3"]})
        ledger["candidate_atoms"].append({"candidate_atom_id": "ATOM-N", "source_candidate_refs": ["SC-N"],
            "statement": "Different contexts use different phrases.", "evidence_anchors": ["L3"],
            "classification_basis": {"co_reference": "not_coreferential", "semantic_values": "not_applicable",
                "presentation_rule": "not_applicable", "evidence": "Separate contexts at L3."}})
        ledger["classification_ledger"].append({"candidate_atom_id": "ATOM-N", "outcome": "nonissue",
            "target_record_id": "REC-N", "evidence_anchors": ["L3"]})
        return ledger

    def rejected_flow(self):
        receipt = self.build_flow("rejected")
        receipt["document_clearance"] = "clear"
        self.write_receipt(receipt)
        self.assert_cli(0, "validate-result", self.packet, "results/DONE.md")
        return receipt

    def test_reaudited_full_counter_anchor_swap_cannot_replace_verification_evidence(self):
        receipt = self.rejected_flow()

        def change_counter(final):
            counter = final["counter_evidence"][0]
            self.assertEqual(counter["anchors"], ["L2"])
            counter["anchors"] = ["L3"]
            counter["public_record_digest"] = record_digest("counter_evidence", counter)

        self.reaudit_final_ledger(receipt, change_counter)
        self.assert_cli(1, "validate-result", self.packet, "results/DONE.md")

    def test_reaudited_nonissue_payload_change_cannot_replace_verified_record(self):
        receipt = self.rejected_flow()

        def change_nonissue(final):
            record = final["nonissues"][0]
            record["rationale"] = "A different postverification reason for dismissing the candidate."
            record["public_record_digest"] = record_digest("nonissue", record)

        self.reaudit_final_ledger(receipt, change_nonissue)
        self.assert_cli(1, "validate-result", self.packet, "results/DONE.md")


if __name__ == "__main__":
    unittest.main()
