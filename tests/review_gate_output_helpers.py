from __future__ import annotations

import copy
import hashlib
from pathlib import Path

import yaml


import sys
ROOT_REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT_REPO))
from lib.review_gate.validate_review_intermediate import PUBLIC_COLLECTIONS, record_digest  # noqa: E402


SNAPSHOT = "sha256:target-snapshot"
ROOT = Path(__file__).resolve().parent / "fixtures" / "review-gate"
AUTHORITY_RELPATH = "approved-docmodel.yaml"


def approved_docmodel_ref() -> dict:
    return docmodel_ref(AUTHORITY_RELPATH)


def docmodel_ref(relative_path: str) -> dict:
    authority_path = ROOT / relative_path
    return {
        "kind": "approved_docmodel",
        "path": relative_path,
        "sha256": hashlib.sha256(authority_path.read_bytes()).hexdigest(),
    }


def basis(co_reference: str, semantic_values: str, presentation_rule: str, evidence: str) -> dict:
    return {
        "co_reference": co_reference,
        "semantic_values": semantic_values,
        "presentation_rule": presentation_rule,
        "evidence": evidence,
    }


def base_ledger() -> dict:
    return {
        "schema_version": 1,
        "snapshot_id": SNAPSHOT,
        "target": "docs/target.md",
        "state": "closed",
        "source_candidate_inventory": [
            {"source_candidate_id": "SC-Q", "lens": "L3", "statement": "규약이 필요함", "evidence_anchors": ["L10"]},
            {"source_candidate_id": "SC-D", "lens": "L3", "statement": "표기가 다름", "evidence_anchors": ["L20", "L30"]},
            {"source_candidate_id": "SC-N", "lens": "L1", "statement": "맥락별 표현", "evidence_anchors": ["L40"]},
        ],
        "candidate_atoms": [
            {
                "candidate_atom_id": "ATOM-Q", "source_candidate_refs": ["SC-Q"],
                "statement": "표현 규약 질문", "evidence_anchors": ["L10"],
                "classification_basis": basis("unknown", "unknown", "unknown", "co-reference convention is unanswered"),
            },
            {
                "candidate_atom_id": "ATOM-F",
                "source_candidate_refs": ["SC-Q"],
                "statement": "답변 후 확인된 결함",
                "evidence_anchors": ["L10"],
                "classification_basis": basis("proven", "different", "not_applicable", "approved docmodel resolves the comparison"),
                "derived_from_question_atom_refs": ["ATOM-Q"],
            },
            {
                "candidate_atom_id": "ATOM-D", "source_candidate_refs": ["SC-D"],
                "statement": "비결함 표기 차이", "evidence_anchors": ["L20", "L30"],
                "classification_basis": basis("proven", "equal", "not_violated", "declared mirror values normalize equally"),
            },
            {
                "candidate_atom_id": "ATOM-N", "source_candidate_refs": ["SC-N"],
                "statement": "비교 대상 아님", "evidence_anchors": ["L40"],
                "classification_basis": basis("not_coreferential", "not_applicable", "not_applicable", "contexts identify different facts"),
            },
        ],
        "findings": [
            {
                "record_id": "REC-F",
                "finding_id": "F-01",
                "candidate_atom_refs": ["ATOM-F"],
                "source_candidate_refs": ["SC-Q"],
                "snapshot_id": SNAPSHOT,
                "evidence_anchors": ["L10"],
                "severity": "P2",
                "judgment_provenance": "approved convention says the value is required",
                "status": "verified",
            }
        ],
        "questions": [
            {
                "record_id": "REC-Q",
                "status": "resolved",
                "convention_slot": "ownership.filters",
                "dependent_atom_refs": ["ATOM-Q"],
                "resolution_derived_atom_refs": ["ATOM-F"],
                "authority": "document owner",
                "scope": "document",
                "source": approved_docmodel_ref(),
                "snapshot_id": SNAPSHOT,
                "evidence_anchors": ["L10"],
                "classification_verification": {"result": "pass", "verifier_id": "V-Q", "evidence": "approved docmodel"},
            }
        ],
        "drifts": [
            {
                "record_id": "REC-D",
                "candidate_atom_refs": ["ATOM-D"],
                "source_candidate_refs": ["SC-D"],
                "snapshot_id": SNAPSHOT,
                "evidence_anchors": ["L20", "L30"],
                "detail": "같은 상한값의 조사 차이",
                "variants": [
                    {"notation": "200개", "anchors": ["L20"]},
                    {"notation": "200건", "anchors": ["L30"]},
                ],
                "co_reference_basis": "docmodel ownership[상한값].mirrors_ok",
                "comparison_ref": "ownership:상한값",
            }
        ],
        "suppressed": [],
        "nonissues": [
            {
                "record_id": "REC-N",
                "candidate_atom_refs": ["ATOM-N"],
                "source_candidate_refs": ["SC-N"],
                "snapshot_id": SNAPSHOT,
                "evidence_anchors": ["L40"],
                "rationale": "서로 다른 맥락의 표현이라 co-reference가 아님",
            }
        ],
        "classification_ledger": [
            {"candidate_atom_id": "ATOM-Q", "outcome": "question", "target_record_id": "REC-Q", "evidence_anchors": ["L10"]},
            {"candidate_atom_id": "ATOM-F", "outcome": "finding", "target_record_id": "REC-F", "evidence_anchors": ["L10"]},
            {"candidate_atom_id": "ATOM-D", "outcome": "drift", "target_record_id": "REC-D", "evidence_anchors": ["L20", "L30"]},
            {"candidate_atom_id": "ATOM-N", "outcome": "nonissue", "target_record_id": "REC-N", "evidence_anchors": ["L40"]},
        ],
    }


def close_digests(ledger: dict) -> dict:
    ledger = copy.deepcopy(ledger)
    for category, collection in PUBLIC_COLLECTIONS.items():
        for record in ledger[collection]:
            record["public_record_digest"] = record_digest(category, record)
    return ledger


def write_ledger(path: Path, ledger: dict) -> str:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(yaml.safe_dump({"review_intermediate": ledger}, sort_keys=False, allow_unicode=True), encoding="utf-8")
    return hashlib.sha256(path.read_bytes()).hexdigest()


def base_receipt(ledger: dict, ledger_relpath: str, ledger_hash: str) -> dict:
    return {
        "schema_version": 2,
        "route_id": "review-gate",
        "route_trace": "direct independent source inspection",
        "snapshot_id": ledger["snapshot_id"],
        "target": ledger["target"],
        "verifiers": [
            {
                "verifier_id": f"independent-final-{index}",
                "result": "pass",
                "snapshot_id": ledger["snapshot_id"],
                "evidence": f"reports/final-{index}.md",
            }
            for index in range(1, 4)
        ],
        "classification_ledger_ref": {
            "path": ledger_relpath,
            "sha256": ledger_hash,
            "snapshot_id": ledger["snapshot_id"],
        },
        "findings": copy.deepcopy(ledger["findings"]),
        "questions": copy.deepcopy(ledger["questions"]),
        "drifts": copy.deepcopy(ledger["drifts"]),
        "unassured_mode": False,
    }


def write_receipt(path: Path, receipt: dict) -> None:
    path.write_text(
        "---\n" + yaml.safe_dump({"doc_review_result": receipt}, sort_keys=False, allow_unicode=True) + "---\n# Review\n",
        encoding="utf-8",
    )
