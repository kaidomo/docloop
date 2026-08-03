#!/usr/bin/env python3
"""Focused deterministic #160 ledger, receipt, and anchor-audit regressions."""

from __future__ import annotations

import copy
import hashlib
import json
from pathlib import Path
import re
import subprocess
import sys
import tempfile
import unittest

import yaml

ROOT = Path(__file__).resolve().parents[1]
FIXTURES = ROOT / "tests" / "fixtures" / "review-gate"
sys.path.insert(0, str(ROOT))

from lib.review_gate import validate_review_intermediate as intermediate  # noqa: E402
from lib.review_gate import validate_review_result as result  # noqa: E402


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


def _packet(root: Path) -> tuple[dict, dict, Path, Path]:
    (root / "frozen").mkdir(parents=True, exist_ok=True)
    (root / "results").mkdir(exist_ok=True)
    authority = root / "frozen" / "approved-docmodel.yaml"
    authority.write_bytes((FIXTURES / "approved-docmodel.yaml").read_bytes())
    target = b"target\n"
    target_sha = hashlib.sha256(target).hexdigest()
    snapshot = f"sha256:{target_sha}"
    target_source = "docs/target.md"

    envelope = yaml.safe_load(
        (FIXTURES / "v2-ledger.yaml").read_text(encoding="utf-8")
    )[intermediate.ROOT_KEY]
    envelope["snapshot_id"] = snapshot
    envelope["target"] = target_source
    envelope["questions"][0]["source"] = {
        "kind": "approved_docmodel",
        "path": "frozen/approved-docmodel.yaml",
        "sha256": hashlib.sha256(authority.read_bytes()).hexdigest(),
    }
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
        self.assertEqual(result.validate_legacy(FIXTURES / "v1-done.md"), [])

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
            self.assertIn("schema_version must be 1", intermediate.validate_data(envelope, packet_root=root))

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
