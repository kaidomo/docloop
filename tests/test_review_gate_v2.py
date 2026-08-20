#!/usr/bin/env python3
"""Focused deterministic #160 ledger, receipt, and anchor-audit regressions."""

from __future__ import annotations

import copy
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import tempfile
import unittest

import yaml

ROOT = Path(__file__).resolve().parents[1]
BIN = ROOT / "bin" / "docloop"
FIXTURES = ROOT / "tests" / "fixtures" / "review-gate"
sys.path.insert(0, str(ROOT))

from lib.review_gate import front_gate  # noqa: E402
from lib.review_gate import runner as RG  # noqa: E402
from lib.review_gate import validate_docmodel_approvals as docmodel_approvals  # noqa: E402
from lib.review_gate import validate_review_intermediate as intermediate  # noqa: E402
from lib.review_gate import validate_review_result as result  # noqa: E402

TESTS_ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(TESTS_ROOT))
from review_gate_output_helpers import write_docmodel_approval  # noqa: E402


def _write_front_gate_trace(
    root: Path, target_path: Path, target_snapshot: str, *, extra_input_gate: dict | None = None
) -> dict:
    """Record a real input gate + all three lenses, mirroring what `docloop
    review-gate prepare` does internally, and return the receipt fields a done
    receipt must carry to bind to it (front_gate_ref/input_gate/structure_axis/
    execution/scale_disclosure/round_context).

    `extra_input_gate` merges extra fields (e.g. `open_items`, a real `prior_round`)
    into the input gate BEFORE the trace records it, so trace and receipt start
    from the same gate-recorded declaration (docauth#290 fixture parity)."""
    trace = front_gate.FrontGateTrace()
    trace.preflight(
        RG._synthetic_convention_intake(target_snapshot=target_snapshot, recorded_at="2026-08-20"),
        RG._synthetic_convention_profile(),
    )
    input_gate = {
        "schema_version": 1,
        "editing_state": "frozen",
        "target_maturity": "complete",
        "source_copy": {"path": "frozen/target.txt", "sha256": hashlib.sha256(target_path.read_bytes()).hexdigest()},
        "prior_round": {"exists": False},
        "run_root": ".",
    }
    if extra_input_gate:
        input_gate.update(extra_input_gate)
    trace.record_input_gate(input_gate, root, target_snapshot=target_snapshot)
    for lens_id in front_gate.LENSES:
        trace.start_lens(lens_id)
    trace_bytes = json.dumps({"review_front_gate_trace": trace.events}, ensure_ascii=False).encode("utf-8")
    trace_path = root / "deterministic" / "FRONT_GATE_TRACE.json"
    trace_path.parent.mkdir(parents=True, exist_ok=True)
    trace_path.write_bytes(trace_bytes)
    # The pre-lens front gate reads input_gate WITH schema_version; the done receipt
    # embeds it WITHOUT (validate_review_result.py's _validate_v2 call omits
    # with_schema_version, matching front_gate.py's own module contract).
    receipt_input_gate = {k: v for k, v in input_gate.items() if k != "schema_version"}
    return {
        "input_gate": receipt_input_gate,
        "front_gate_ref": {"path": "deterministic/FRONT_GATE_TRACE.json", "sha256": hashlib.sha256(trace_bytes).hexdigest()},
        "structure_axis": "undetermined",
        "structure_axis_reason": "no real convention profile supplied for this fixture packet",
        "execution": {"run_ids": [], "lens_rounds": 1, "lens_rounds_reason": "single-round fixture run"},
        "scale_disclosure": {
            "target_volume": {"lines": 1, "snapshot_id": target_snapshot},
            "planned_lens_rounds": 1,
            "configuration": [{"name": "fixture", "count": 1}],
            "derived_total_agents": 1,
        },
        "round_context": {"round_label": "r1"},
    }


def _write_assured_inputs(review: Path) -> None:
    review.mkdir()
    target = review / "draft.md"
    target.write_text("# Demo\n\nState is open.\n", encoding="utf-8")
    decision_source = review / "decision-source.md"
    decision_source.write_text("D-01 remains authoritative\n", encoding="utf-8")
    (review / "decisions.yaml").write_text(
        yaml.safe_dump(
            {
                "meta": {
                    "target": "demo",
                    "source_ref": "decision-source.md",
                    "source_version_hash": hashlib.sha256(decision_source.read_bytes()).hexdigest(),
                    "updated_at": "2026-08-03",
                },
                "decisions": [{
                    "id": "D-01", "decision": "retain the established rule", "status": "재론금지",
                    "date": "2026-08-03", "evidence": "D-01",
                }],
            },
            allow_unicode=True,
            sort_keys=False,
        ),
        encoding="utf-8",
    )
    terms_source = review / "terms-source.md"
    terms_source.write_text("Receiver is canonical.\n", encoding="utf-8")
    (review / "terms.yaml").write_text(
        yaml.safe_dump({
            "meta": {
                "target": "demo", "updated_at": "2026-08-03", "source_ref": "terms-source.md",
                "source_hash": hashlib.sha256(terms_source.read_bytes()).hexdigest(),
            },
            "terms": [{"canonical": "Receiver", "forbidden": ["리시버"]}],
        }, allow_unicode=True, sort_keys=False),
        encoding="utf-8",
    )
    model_source = review / "model-source.md"
    model_source.write_text("Section one is canonical.\n", encoding="utf-8")
    (review / "docmodel.yaml").write_text(
        yaml.safe_dump({
            "meta": {
                "template": "demo", "updated_at": "2026-08-03", "approved_by": "owner",
                "approval_state": "approved", "suppression_eligible": True,
                "source_ref": "model-source.md",
                "source_hash": hashlib.sha256(model_source.read_bytes()).hexdigest(),
            },
            "sections": [{"id": "1", "title": "Demo", "role": "canonical"}],
        }, allow_unicode=True, sort_keys=False),
        encoding="utf-8",
    )


def _prepare_assured(root: Path) -> Path:
    review = root / "review"
    _write_assured_inputs(review)
    proc = subprocess.run(
        [
            str(BIN), "review-gate", "prepare", str(review), "assured-registry", "draft.md",
            "--decisions", "decisions.yaml", "--terms", "terms.yaml", "--docmodel", "docmodel.yaml",
            "--editing-state", "frozen", "--target-maturity", "complete",
        ],
        capture_output=True,
        text=True,
    )
    if proc.returncode != 0:
        raise AssertionError(proc.stdout + proc.stderr)
    return review / "review-gate" / "assured-registry"


def _receipt_fixture() -> dict:
    text = (FIXTURES / "v2-done.md").read_text(encoding="utf-8")
    match = re.match(r"\A---\n(.*?)\n---(?:\n|\Z)", text, re.S)
    assert match
    return yaml.safe_load(match.group(1))["doc_review_result"]


def _write_receipt(path: Path, receipt: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        "---\n"
        + yaml.safe_dump(
            {"doc_review_result": receipt}, allow_unicode=True, sort_keys=False
        ).rstrip()
        + "\n---\n# done\n",
        encoding="utf-8",
    )


def _packet(root: Path, *, extra_input_gate: dict | None = None) -> tuple[dict, dict, Path, Path]:
    (root / "frozen").mkdir(parents=True, exist_ok=True)
    (root / "results").mkdir(exist_ok=True)
    authority = root / "frozen" / "approved-docmodel.yaml"
    authority.write_bytes((FIXTURES / "approved-docmodel.yaml").read_bytes())
    target = b"target\n"
    target_sha = hashlib.sha256(target).hexdigest()
    snapshot = f"sha256:{target_sha}"
    target_source = "docs/target.md"
    target_path = root / "frozen" / "target.txt"
    target_path.write_bytes(target)
    front_gate_fields = _write_front_gate_trace(root, target_path, snapshot, extra_input_gate=extra_input_gate)

    envelope = yaml.safe_load(
        (FIXTURES / "v2-ledger.yaml").read_text(encoding="utf-8")
    )[intermediate.ROOT_KEY]
    envelope["snapshot_id"] = snapshot
    envelope["target"] = target_source
    envelope["questions"][0]["source"] = write_docmodel_approval(root, "frozen/approved-docmodel.yaml")
    for category, collection in intermediate.PUBLIC_COLLECTIONS.items():
        for record in envelope[collection]:
            record["snapshot_id"] = snapshot
            record["public_record_digest"] = intermediate.record_digest(category, record)

    ledger_path = root / "results" / "INTERMEDIATE.yaml"
    ledger_path.write_text(
        yaml.safe_dump(
            {intermediate.ROOT_KEY: envelope}, allow_unicode=True, sort_keys=False
        ),
        encoding="utf-8",
    )
    binding = {
        "run_id": "run-one",
        "target_source": target_source,
        "target_snapshot": snapshot,
        "prepared_payload_digest_sha256": "a" * 64,
        "receipt_path": "results/DONE.md",
    }
    receipt = _receipt_fixture()
    receipt["snapshot_id"] = snapshot
    receipt["target"] = target_source
    for verifier in receipt["verifiers"]:
        verifier["snapshot_id"] = snapshot
    receipt["classification_ledger_ref"] = {
        "path": "results/INTERMEDIATE.yaml",
        "sha256": hashlib.sha256(ledger_path.read_bytes()).hexdigest(),
        "snapshot_id": snapshot,
    }
    receipt["findings"] = copy.deepcopy(envelope["findings"])
    receipt["questions"] = copy.deepcopy(envelope["questions"])
    receipt["drifts"] = copy.deepcopy(envelope["drifts"])
    receipt["packet_binding"] = copy.deepcopy(binding)
    receipt.update(copy.deepcopy(front_gate_fields))
    receipt_path = root / "results" / "DONE.md"
    _write_receipt(receipt_path, receipt)
    (root / "RUN.yaml").write_text(
        yaml.safe_dump(
            {
                "run_id": binding["run_id"],
                "target": {"source": target_source, "sha256": target_sha},
            },
            sort_keys=False,
        ),
        encoding="utf-8",
    )
    (root / "COMPLETE.json").write_text(
        json.dumps({"payload_digest_sha256": binding["prepared_payload_digest_sha256"]}),
        encoding="utf-8",
    )
    return envelope, receipt, ledger_path, receipt_path


class ReviewGateV2Tests(unittest.TestCase):
    def test_runner_prepared_decision_registry_can_resolve_and_suppress(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            run = _prepare_assured(Path(td))
            run_meta = yaml.safe_load((run / "RUN.yaml").read_text(encoding="utf-8"))
            snapshot = "sha256:" + run_meta["target"]["sha256"]
            target_source = run_meta["target"]["source"]
            authority = run / "frozen" / "decisions.yaml"
            authority_ref = {
                "kind": "decision_registry",
                "path": "frozen/decisions.yaml",
                "sha256": hashlib.sha256(authority.read_bytes()).hexdigest(),
                "decision_id": "D-01",
            }
            envelope = yaml.safe_load(
                (FIXTURES / "v2-ledger.yaml").read_text(encoding="utf-8")
            )[intermediate.ROOT_KEY]
            envelope["snapshot_id"] = snapshot
            envelope["target"] = target_source
            envelope["questions"][0]["source"] = copy.deepcopy(authority_ref)
            envelope["source_candidate_inventory"].append({
                "source_candidate_id": "SC-S", "lens": "L2",
                "statement": "candidate covered by confirmed decision", "evidence_anchors": ["L50"],
            })
            envelope["candidate_atoms"].append({
                "candidate_atom_id": "ATOM-S", "source_candidate_refs": ["SC-S"],
                "statement": "confirmed prior decision", "evidence_anchors": ["L50"],
                "classification_basis": {
                    "co_reference": "proven", "semantic_values": "different",
                    "presentation_rule": "not_applicable", "evidence": "registry decision D-01",
                },
            })
            envelope["suppressed"] = [{
                "record_id": "REC-S",
                "candidate_atom_refs": ["ATOM-S"],
                "source_candidate_refs": ["SC-S"],
                "snapshot_id": snapshot,
                "evidence_anchors": ["L50"],
                "rationale": "current confirmed decision",
                "authority_ref": copy.deepcopy(authority_ref),
            }]
            envelope["classification_ledger"].append({
                "candidate_atom_id": "ATOM-S", "outcome": "suppressed",
                "target_record_id": "REC-S", "evidence_anchors": ["L50"],
            })
            for category, collection in intermediate.PUBLIC_COLLECTIONS.items():
                for record in envelope[collection]:
                    record["snapshot_id"] = snapshot
                    record["public_record_digest"] = intermediate.record_digest(category, record)
            self.assertEqual(
                intermediate.validate_data(envelope, require_closed=True, packet_root=run),
                [],
            )

            ledger_path = run / "results" / "INTERMEDIATE.yaml"
            ledger_path.write_text(
                yaml.safe_dump({intermediate.ROOT_KEY: envelope}, allow_unicode=True, sort_keys=False),
                encoding="utf-8",
            )
            receipt = _receipt_fixture()
            receipt["snapshot_id"] = snapshot
            receipt["target"] = target_source
            for verifier in receipt["verifiers"]:
                verifier["snapshot_id"] = snapshot
            receipt["classification_ledger_ref"] = {
                "path": "results/INTERMEDIATE.yaml",
                "sha256": hashlib.sha256(ledger_path.read_bytes()).hexdigest(),
                "snapshot_id": snapshot,
            }
            receipt["findings"] = copy.deepcopy(envelope["findings"])
            receipt["questions"] = copy.deepcopy(envelope["questions"])
            receipt["drifts"] = copy.deepcopy(envelope["drifts"])
            # `prepare` already recorded the real input gate/front-gate trace for this
            # packet; its receipt scaffold is copy-ready (docs/review-gate.md).
            scaffold = json.loads(
                (run / "deterministic" / "RECEIPT_SCAFFOLD.json").read_text(encoding="utf-8")
            )
            receipt.update(scaffold)
            receipt["structure_axis"] = "undetermined"
            receipt["structure_axis_reason"] = "no real convention profile supplied for this fixture packet"
            receipt["execution"] = {"run_ids": [], "lens_rounds": 1, "lens_rounds_reason": "single-round fixture run"}
            receipt["scale_disclosure"] = {
                "target_volume": {"lines": 1, "snapshot_id": snapshot},
                "planned_lens_rounds": 1,
                "configuration": [{"name": "fixture", "count": 1}],
                "derived_total_agents": 1,
            }
            binding, binding_errors = result.packet_binding_from_prepared(run, "results/DONE.md")
            self.assertEqual(binding_errors, [])
            receipt["packet_binding"] = binding
            _write_receipt(run / "results" / "DONE.md", receipt)
            proc = subprocess.run(
                [str(BIN), "review-gate", "validate-result", str(run), "results/DONE.md"],
                capture_output=True,
                text=True,
            )
            self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)

    def test_registry_errors_are_relative_safe_and_not_masked(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            envelope, _, _, _ = _packet(root)
            provenance = root / "frozen" / "provenance"
            provenance.mkdir()
            source = provenance / "decision.source"
            source.write_bytes(b"authority\n")
            registry = root / "frozen" / "decisions.yaml"
            registry.write_text(yaml.safe_dump({
                "meta": {
                    "target": "demo", "source_ref": "provenance/decision.source",
                    "source_version_hash": hashlib.sha256(source.read_bytes()).hexdigest(),
                    "updated_at": "2026-08-03",
                },
                "decisions": [{
                    "id": "D-01", "decision": "confirmed", "status": "재론금지",
                    "date": "2026-08-03", "evidence": "D-01",
                }],
            }, allow_unicode=True, sort_keys=False), encoding="utf-8")
            envelope["schema_version"] = 3
            envelope["questions"][0]["source"] = {
                "kind": "decision_registry", "path": "frozen/decisions.yaml",
                "sha256": hashlib.sha256(registry.read_bytes()).hexdigest(), "decision_id": "missing",
            }
            envelope["questions"][0]["public_record_digest"] = intermediate.record_digest(
                "question", envelope["questions"][0]
            )
            errors = intermediate.validate_data(envelope, packet_root=root)
            self.assertIn("schema_version must be 1 or 2", errors)
            self.assertTrue(any("current confirmed decision" in error for error in errors), errors)

            registry_data = yaml.safe_load(registry.read_text(encoding="utf-8"))
            registry_data["meta"]["source_ref"] = "../../outside"
            registry.write_text(yaml.safe_dump(registry_data, allow_unicode=True, sort_keys=False), encoding="utf-8")
            envelope["questions"][0]["source"]["sha256"] = hashlib.sha256(registry.read_bytes()).hexdigest()
            envelope["questions"][0]["public_record_digest"] = intermediate.record_digest(
                "question", envelope["questions"][0]
            )
            errors = intermediate.validate_data(envelope, packet_root=root)
            self.assertTrue(any("outside the packet root" in error for error in errors), errors)

            registry_data["meta"]["source_ref"] = "provenance/link.source"
            link = provenance / "link.source"
            link.symlink_to(source)
            registry.write_text(yaml.safe_dump(registry_data, allow_unicode=True, sort_keys=False), encoding="utf-8")
            envelope["questions"][0]["source"]["sha256"] = hashlib.sha256(registry.read_bytes()).hexdigest()
            envelope["questions"][0]["public_record_digest"] = intermediate.record_digest(
                "question", envelope["questions"][0]
            )
            errors = intermediate.validate_data(envelope, packet_root=root)
            self.assertTrue(any("symlink" in error for error in errors), errors)

    def test_closed_ledger_and_v2_receipt_pass_without_git(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            envelope, _, _, _ = _packet(root)
            self.assertEqual(
                intermediate.validate_data(envelope, require_closed=True, packet_root=root),
                [],
            )
            binding, errors = result.packet_binding_from_prepared(root, "results/DONE.md")
            self.assertEqual(errors, [])
            self.assertEqual(result.validate(root, "results/DONE.md", binding or {}), [])

    def test_legacy_v1_receipt_remains_valid(self) -> None:
        self.assertEqual(result.legacy_field_errors(FIXTURES / "v1-done.md").field_errors, [])

    def test_open_question_blocks_closed_validation(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            envelope, _, _, _ = _packet(root)
            envelope["state"] = "open"
            errors = intermediate.validate_data(envelope, require_closed=True, packet_root=root)
            self.assertTrue(any("requires a closed" in error for error in errors))

    def test_resolved_question_requires_derived_terminal_atom(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            envelope, _, _, _ = _packet(root)
            envelope["questions"][0]["resolution_derived_atom_refs"] = []
            errors = intermediate.validate_data(envelope, packet_root=root)
            self.assertTrue(any("must be nonempty when resolved" in error for error in errors))

    def test_duplicate_yaml_keys_and_boolean_versions_fail(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            duplicate = root / "duplicate.yaml"
            duplicate.write_text(
                "review_intermediate:\n  schema_version: 1\n  schema_version: 1\n",
                encoding="utf-8",
            )
            self.assertTrue(intermediate.validate(duplicate, packet_root=root))
            envelope, _, _, _ = _packet(root)
            envelope["schema_version"] = True
            self.assertIn("schema_version must be 1 or 2", intermediate.validate_data(envelope, packet_root=root))

    def test_drift_is_nonblocking_but_rejects_finding_fields(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            envelope, _, _, _ = _packet(root)
            envelope["drifts"][0]["severity"] = "P3"
            errors = intermediate.validate_data(envelope, require_closed=True, packet_root=root)
            self.assertTrue(any("drift cannot contain" in error for error in errors))

    def test_authority_path_hash_and_symlink_fail_closed(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            envelope, _, _, _ = _packet(root)
            envelope["questions"][0]["source"]["path"] = "../outside.yaml"
            errors = intermediate.validate_data(envelope, packet_root=root)
            self.assertTrue(any("packet-relative" in error for error in errors))
            envelope, _, _, _ = _packet(root)
            envelope["questions"][0]["source"]["sha256"] = "0" * 64
            errors = intermediate.validate_data(envelope, packet_root=root)
            self.assertTrue(any("does not match authority" in error for error in errors))
            link = root / "frozen" / "link.yaml"
            link.symlink_to(root / "frozen" / "approved-docmodel.yaml")
            envelope["questions"][0]["source"]["path"] = "frozen/link.yaml"
            errors = intermediate.validate_data(envelope, packet_root=root)
            self.assertTrue(any("symlink" in error for error in errors))

    def test_each_packet_binding_field_is_exact(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            _, receipt, _, receipt_path = _packet(root)
            expected = copy.deepcopy(receipt["packet_binding"])
            for field in expected:
                changed = copy.deepcopy(receipt)
                changed["packet_binding"][field] = "changed"
                _write_receipt(receipt_path, changed)
                errors = result.validate(root, "results/DONE.md", expected)
                self.assertTrue(
                    any(f"packet_binding.{field}" in error for error in errors),
                    (field, errors),
                )

    def test_front_gate_trace_binding_rejects_rewritten_input_gate(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            _, receipt, _, receipt_path = _packet(root)
            expected = copy.deepcopy(receipt["packet_binding"])
            # Declaring in_progress after the digest-verified pre-lens trace recorded
            # frozen -- the trace, not this rewritten claim, must win (docauth#228②).
            receipt["input_gate"]["editing_state"] = "in_progress"
            _write_receipt(receipt_path, receipt)
            errors = result.validate(root, "results/DONE.md", expected)
            self.assertTrue(
                any("editing_state" in error and "does not match receipt" in error for error in errors),
                errors,
            )

    def test_front_gate_ref_hash_tamper_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            _, receipt, _, receipt_path = _packet(root)
            expected = copy.deepcopy(receipt["packet_binding"])
            receipt["front_gate_ref"]["sha256"] = "0" * 64
            _write_receipt(receipt_path, receipt)
            errors = result.validate(root, "results/DONE.md", expected)
            self.assertTrue(any("front_gate_ref.sha256 does not match" in error for error in errors), errors)

    def test_structure_axis_must_match_trace_non_applicability(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            _, receipt, _, receipt_path = _packet(root)
            expected = copy.deepcopy(receipt["packet_binding"])
            # _packet()'s trace declares the synthetic convention profile
            # not-applicable -- structure_axis must stay undetermined (#233).
            receipt["structure_axis"] = "judged"
            del receipt["structure_axis_reason"]
            _write_receipt(receipt_path, receipt)
            errors = result.validate(root, "results/DONE.md", expected)
            self.assertTrue(
                any("structure_axis must be undetermined" in error for error in errors), errors
            )

    def test_deferred_verification_when_target_still_being_edited(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            _, receipt, _, receipt_path = _packet(root)
            expected = copy.deepcopy(receipt["packet_binding"])
            trace = json.loads(
                (root / "deterministic" / "FRONT_GATE_TRACE.json").read_text(encoding="utf-8")
            )
            for event in trace["review_front_gate_trace"]:
                if event.get("event") == "input_gate_recorded":
                    event["editing_state"] = "in_progress"
                    event["verification_deferred"] = True
            trace_bytes = json.dumps(trace, ensure_ascii=False).encode("utf-8")
            (root / "deterministic" / "FRONT_GATE_TRACE.json").write_bytes(trace_bytes)
            receipt["input_gate"]["editing_state"] = "in_progress"
            receipt["front_gate_ref"]["sha256"] = hashlib.sha256(trace_bytes).hexdigest()
            receipt["verifiers"] = []
            _write_receipt(receipt_path, receipt)
            errors = result.validate(root, "results/DONE.md", expected)
            self.assertEqual(errors, [result.DEFERRED_MESSAGE], errors)

    def test_open_items_ledger_cannot_be_cited_as_suppression_authority(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            envelope, receipt, ledger_path, receipt_path = _packet(root)
            expected = copy.deepcopy(receipt["packet_binding"])
            open_items = root / "frozen" / "open-items.yaml"
            open_items.write_text("meta:\n  target: demo\nitems: []\n", encoding="utf-8")
            open_items_sha = hashlib.sha256(open_items.read_bytes()).hexdigest()
            receipt["input_gate"]["open_items"] = {
                "ledger_ref": {"path": "frozen/open-items.yaml", "sha256": open_items_sha},
            }
            drift = envelope["drifts"][0]
            suppressed_record = {
                "record_id": "REC-OI-SUPPRESS",
                "candidate_atom_refs": drift["candidate_atom_refs"],
                "source_candidate_refs": drift["source_candidate_refs"],
                "snapshot_id": drift["snapshot_id"],
                "evidence_anchors": drift["evidence_anchors"],
                "rationale": "already tracked as an open item",
                "authority_ref": {"path": "frozen/open-items.yaml", "sha256": open_items_sha},
            }
            suppressed_record["public_record_digest"] = intermediate.record_digest("suppressed", suppressed_record)
            envelope["suppressed"] = [suppressed_record]
            envelope["classification_ledger"] = [
                row for row in envelope["classification_ledger"]
                if row["candidate_atom_id"] != drift["candidate_atom_refs"][0]
            ]
            envelope["classification_ledger"].append({
                "candidate_atom_id": drift["candidate_atom_refs"][0], "outcome": "suppressed",
                "target_record_id": "REC-OI-SUPPRESS", "evidence_anchors": drift["evidence_anchors"],
            })
            envelope["drifts"] = []
            ledger_path.write_text(
                yaml.safe_dump({intermediate.ROOT_KEY: envelope}, allow_unicode=True, sort_keys=False),
                encoding="utf-8",
            )
            receipt["classification_ledger_ref"]["sha256"] = hashlib.sha256(ledger_path.read_bytes()).hexdigest()
            receipt["drifts"] = []
            _write_receipt(receipt_path, receipt)
            errors = result.validate(root, "results/DONE.md", expected)
            self.assertTrue(
                any("registered open items classify findings, they never" in error for error in errors),
                errors,
            )

    def test_open_items_ledger_ref_binding_rejects_rewritten_declaration(self) -> None:
        # docauth#290: the front gate trace already records open_items_ledger_ref
        # (#206), but nothing compared it to the receipt's own declaration until
        # now -- a receipt could rewrite input_gate.open_items.ledger_ref to
        # "none" (or a different file) after the gate recorded it.
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            open_items_ref = {"path": "frozen/open-items.yaml", "sha256": "f" * 64}
            _, receipt, _, receipt_path = _packet(
                root, extra_input_gate={"open_items": {"ledger_ref": open_items_ref}}
            )
            expected = copy.deepcopy(receipt["packet_binding"])
            # sanity: gate and receipt agree on the ledger_ref, so this binding
            # raises nothing (other, unrelated errors are not what this test is about).
            errors = result.validate(root, "results/DONE.md", expected)
            self.assertFalse(any("open_items_ledger_ref" in e for e in errors), errors)

            receipt["input_gate"]["open_items"] = {"ledger_ref": "none"}
            _write_receipt(receipt_path, receipt)
            errors = result.validate(root, "results/DONE.md", expected)
            self.assertTrue(
                any(
                    "open_items_ledger_ref" in e and "does not match receipt" in e
                    for e in errors
                ),
                errors,
            )

    def test_prior_round_output_ref_must_point_at_real_matching_bytes(self) -> None:
        # docauth#290: prior_round_output_round_no is digest-bound (front gate
        # binding), so the NUMBER cannot be rewritten after the gate recorded it --
        # but nothing verified output_ref.path/.sha256 actually name a real,
        # matching file until now.
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            output_path = root / "frozen" / "prior-r1.md"
            output_path.parent.mkdir(parents=True, exist_ok=True)
            output_payload = b"prior round output\n"
            output_path.write_bytes(output_payload)
            output_sha = hashlib.sha256(output_payload).hexdigest()
            extra = {
                "prior_round": {
                    "exists": True,
                    "output_ref": {"path": "frozen/prior-r1.md", "sha256": output_sha, "round_no": 1},
                },
            }
            _, receipt, _, receipt_path = _packet(root, extra_input_gate=extra)
            expected = copy.deepcopy(receipt["packet_binding"])
            comparison_path = root / "frozen" / "comparison-r1-r2.md"
            comparison_payload = (result.COMPARISON_TABLE_SIGNATURE + "\nfixture\n").encode("utf-8")
            comparison_path.write_bytes(comparison_payload)
            receipt["round_context"] = {
                "round_label": "r2",
                "comparison_ref": {
                    "path": "frozen/comparison-r1-r2.md",
                    "sha256": hashlib.sha256(comparison_payload).hexdigest(),
                },
            }
            _write_receipt(receipt_path, receipt)
            # Codex r1-02: assert a clean baseline before tampering, so a later
            # rejection actually demonstrates the new check firing (not just some
            # other, unrelated error already present in an unverified fixture).
            errors = result.validate(root, "results/DONE.md", expected)
            self.assertFalse(any("output_ref" in e for e in errors), errors)

            bad = copy.deepcopy(receipt)
            bad["input_gate"]["prior_round"]["output_ref"]["sha256"] = "0" * 64
            _write_receipt(receipt_path, bad)
            errors = result.validate(root, "results/DONE.md", expected)
            self.assertTrue(any("output_ref.sha256 does not match" in e for e in errors), errors)

            missing = copy.deepcopy(receipt)
            missing["input_gate"]["prior_round"]["output_ref"]["path"] = "frozen/does-not-exist.md"
            _write_receipt(receipt_path, missing)
            errors = result.validate(root, "results/DONE.md", expected)
            self.assertTrue(
                any("cannot read input_gate.prior_round.output_ref" in e for e in errors), errors
            )

    def test_receipt_cannot_swap_output_ref_for_another_real_matching_file(self) -> None:
        # docauth#293 (closes the docauth#290/r1-01 known gap documented above):
        # output_ref.path/.sha256 being real and self-consistent was never enough --
        # round_no alone was trace-bound, so a receipt could leave round_no untouched
        # and swap path/sha256 to point at a DIFFERENT real, correctly-hashed file
        # already sitting in the packet. Existence + self-consistency both still
        # passed. Now output_ref.path/.sha256 are trace-bound the same way
        # open_items_ledger_ref already was.
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            original_path = root / "frozen" / "prior-r1-original.md"
            original_path.parent.mkdir(parents=True, exist_ok=True)
            original_payload = b"original prior-round output\n"
            original_path.write_bytes(original_payload)
            original_sha = hashlib.sha256(original_payload).hexdigest()
            extra = {
                "prior_round": {
                    "exists": True,
                    "output_ref": {
                        "path": "frozen/prior-r1-original.md",
                        "sha256": original_sha,
                        "round_no": 1,
                    },
                },
            }
            _, receipt, _, receipt_path = _packet(root, extra_input_gate=extra)
            expected = copy.deepcopy(receipt["packet_binding"])
            comparison_path = root / "frozen" / "comparison-r1-r2-swap.md"
            comparison_payload = (result.COMPARISON_TABLE_SIGNATURE + "\nfixture\n").encode("utf-8")
            comparison_path.write_bytes(comparison_payload)
            receipt["round_context"] = {
                "round_label": "r2",
                "comparison_ref": {
                    "path": "frozen/comparison-r1-r2-swap.md",
                    "sha256": hashlib.sha256(comparison_payload).hexdigest(),
                },
            }
            _write_receipt(receipt_path, receipt)
            errors = result.validate(root, "results/DONE.md", expected)
            self.assertFalse(any("output_ref" in e for e in errors), errors)  # sanity: valid baseline

            # A distinct real file with its OWN self-consistent hash -- not the same
            # payload the original uses, or the two would collide on sha256 and this
            # test would only ever exercise the path binding, never sha256 binding.
            swapped_path = root / "frozen" / "prior-r1-swapped.md"
            swapped_payload = b"a different real prior-round output (swap target)\n"
            swapped_path.write_bytes(swapped_payload)
            swapped_sha = hashlib.sha256(swapped_payload).hexdigest()
            tampered = copy.deepcopy(receipt)
            tampered["input_gate"]["prior_round"]["output_ref"]["path"] = "frozen/prior-r1-swapped.md"
            tampered["input_gate"]["prior_round"]["output_ref"]["sha256"] = swapped_sha
            _write_receipt(receipt_path, tampered)
            errors = result.validate(root, "results/DONE.md", expected)
            self.assertTrue(
                any(
                    "does not match receipt" in e and "prior_round_output_ref_path" in e
                    for e in errors
                ),
                errors,
            )
            self.assertTrue(
                any(
                    "does not match receipt" in e and "prior_round_output_ref_sha256" in e
                    for e in errors
                ),
                errors,
            )

    def test_prior_round_output_ref_fifo_is_rejected_instead_of_hanging(self) -> None:
        # Codex r1-01 (docauth#290): a plain read_bytes() on output_ref.path would
        # block indefinitely open()-ing a FIFO -- the same hang class the docmodel-
        # approvals TOCTOU fix (#290 fix 3) already caught elsewhere. output_ref
        # must go through the same fd-anchored read (_read_packet_file_bytes).
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            fifo_path = root / "frozen" / "prior-r1.fifo"
            fifo_path.parent.mkdir(parents=True, exist_ok=True)
            os.mkfifo(fifo_path)
            extra = {
                "prior_round": {
                    "exists": True,
                    "output_ref": {"path": "frozen/prior-r1.fifo", "sha256": "0" * 64, "round_no": 1},
                },
            }
            _, receipt, _, receipt_path = _packet(root, extra_input_gate=extra)
            receipt["round_context"] = {"round_label": "r2"}
            expected = copy.deepcopy(receipt["packet_binding"])
            _write_receipt(receipt_path, receipt)
            errors = result.validate(root, "results/DONE.md", expected)
            self.assertTrue(
                any(
                    "cannot read input_gate.prior_round.output_ref" in e
                    or "must be a regular file" in e
                    for e in errors
                ),
                f"FIFO output_ref should be rejected, not hang; got: {errors}",
            )

    def test_docmodel_approval_hard_link_and_fifo_rejected(self) -> None:
        # docauth#290 fix 3: docmodel_path was read via separate stat()/is_file()/
        # read_bytes() calls (a TOCTOU window) and a FIFO could hang open() before
        # any of those checks ran. _read_verified_docmodel opens with
        # O_NOFOLLOW|O_NONBLOCK and does every check on that one fd.
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            (root / "frozen").mkdir(parents=True)
            docmodel_path = root / "frozen" / "approved-docmodel.yaml"
            docmodel_path.write_bytes((FIXTURES / "approved-docmodel.yaml").read_bytes())
            write_docmodel_approval(root, "frozen/approved-docmodel.yaml")
            registry_path = root / "frozen" / "docmodel-approvals.yaml"

            hard_link_path = root / "frozen" / "approved-docmodel-alias.yaml"
            os.link(docmodel_path, hard_link_path)
            hardlink_registry = root / "frozen" / "hardlink-docmodel-approvals.yaml"
            hardlink_registry.write_text(
                registry_path.read_text(encoding="utf-8").replace(
                    "docmodel_path: frozen/approved-docmodel.yaml",
                    "docmodel_path: frozen/approved-docmodel-alias.yaml",
                ),
                encoding="utf-8",
            )
            errors = docmodel_approvals.validate(hardlink_registry, packet_root=root)
            self.assertTrue(any("hard link" in e for e in errors), errors)

            fifo_path = root / "frozen" / "docmodel.fifo.yaml"
            os.mkfifo(fifo_path)
            fifo_registry = root / "frozen" / "fifo-docmodel-approvals.yaml"
            fifo_registry.write_text(
                registry_path.read_text(encoding="utf-8").replace(
                    "docmodel_path: frozen/approved-docmodel.yaml",
                    "docmodel_path: frozen/docmodel.fifo.yaml",
                ),
                encoding="utf-8",
            )
            errors = docmodel_approvals.validate(fifo_registry, packet_root=root)
            self.assertTrue(
                any("must be a regular file" in e for e in errors),
                f"FIFO docmodel_path should be rejected, not hang; got: {errors}",
            )

    def test_receipt_and_ledger_bind_to_packet_target(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            _, receipt, _, receipt_path = _packet(root)
            expected = copy.deepcopy(receipt["packet_binding"])
            receipt["snapshot_id"] = "sha256:other"
            _write_receipt(receipt_path, receipt)
            errors = result.validate(root, "results/DONE.md", expected)
            self.assertTrue(any("snapshot_id must match packet_binding" in error for error in errors))

    def test_cross_run_replay_fails(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            _, receipt, _, _ = _packet(root)
            expected = copy.deepcopy(receipt["packet_binding"])
            expected["run_id"] = "run-two"
            self.assertIn(
                "packet_binding.run_id does not match the prepared packet",
                result.validate(root, "results/DONE.md", expected),
            )

    def test_ledger_hash_and_public_payload_tamper_fail(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            _, receipt, ledger_path, receipt_path = _packet(root)
            expected = copy.deepcopy(receipt["packet_binding"])
            ledger_path.write_text(ledger_path.read_text() + "\n", encoding="utf-8")
            self.assertTrue(any("sha256" in error for error in result.validate(root, "results/DONE.md", expected)))
            _, receipt, _, receipt_path = _packet(root)
            receipt["findings"][0]["severity"] = "P1"
            _write_receipt(receipt_path, receipt)
            self.assertTrue(any("immutable payload" in error for error in result.validate(root, "results/DONE.md", expected)))

    def test_malformed_ledger_still_reports_load_failure_after_dead_path_cleanup(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            _, receipt, ledger_path, receipt_path = _packet(root)
            expected = copy.deepcopy(receipt["packet_binding"])
            ledger_path.write_text("not: [valid\n", encoding="utf-8")
            receipt["classification_ledger_ref"]["sha256"] = hashlib.sha256(
                ledger_path.read_bytes()
            ).hexdigest()
            _write_receipt(receipt_path, receipt)
            errors = result.validate(root, "results/DONE.md", expected)
            self.assertTrue(any("cannot load classification ledger" in error for error in errors), errors)

    def test_receipt_and_ledger_paths_reject_backslash_and_symlink(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            _, receipt, ledger_path, receipt_path = _packet(root)
            expected = copy.deepcopy(receipt["packet_binding"])
            self.assertTrue(result.validate(root, r"results\DONE.md", expected))
            receipt["classification_ledger_ref"]["path"] = r"results\INTERMEDIATE.yaml"
            _write_receipt(receipt_path, receipt)
            self.assertTrue(any("packet-relative" in error for error in result.validate(root, "results/DONE.md", expected)))
            alias = root / "results" / "ALIAS.yaml"
            alias.symlink_to(ledger_path)
            receipt["classification_ledger_ref"]["path"] = "results/ALIAS.yaml"
            _write_receipt(receipt_path, receipt)
            self.assertTrue(any("symlink" in error for error in result.validate(root, "results/DONE.md", expected)))

    def test_ledger_aware_audit_uses_terminal_rows_not_scratch_prose(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            _packet(root)
            synth = root / "results" / "SYNTHESIS.md"
            synth.write_text("scratch L99\n", encoding="utf-8")
            lens = root / "results" / "L1.md"
            lens.write_text("candidate L99\n", encoding="utf-8")
            command = [
                sys.executable,
                str(ROOT / "lib" / "review_gate" / "audit_anchors.py"),
                str(synth),
                "--packet-root",
                str(root),
                "--ledger",
                "results/INTERMEDIATE.yaml",
                "--lens",
                str(lens),
            ]
            proc = subprocess.run(command, text=True, capture_output=True)
            self.assertEqual(proc.returncode, 1, proc.stdout + proc.stderr)
            self.assertIn("L99", proc.stdout)
            lens.write_text("candidate L10\n", encoding="utf-8")
            proc = subprocess.run(command, text=True, capture_output=True)
            self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)


if __name__ == "__main__":
    unittest.main(verbosity=2)
