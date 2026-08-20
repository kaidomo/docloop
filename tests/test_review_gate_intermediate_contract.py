#!/usr/bin/env python3
from __future__ import annotations

import copy
from pathlib import Path
import sys

ROOT_REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT_REPO))
from lib.review_gate.validate_review_intermediate import (  # noqa: E402
    DuplicateKeyError,
    load_yaml_text,
    record_digest,
    validate_data as _validate_data,
)
from review_gate_output_helpers import approved_docmodel_ref, base_ledger, basis, close_digests, docmodel_ref  # noqa: E402


ROOT = Path(__file__).resolve().parent / "fixtures" / "review-gate"


def validate_data(data, *, require_closed=False):
    return _validate_data(data, require_closed=require_closed, packet_root=ROOT)


passed = failed = 0


def check(name: str, condition: bool) -> None:
    global passed, failed
    if condition:
        passed += 1
        print(f"ok   {name}")
    else:
        failed += 1
        print(f"FAIL {name}")


valid = close_digests(base_ledger())
check("closed source→atom→terminal ledger passes", validate_data(valid, require_closed=True) == [])

try:
    load_yaml_text("review_intermediate:\n  state: open\n  state: closed\n")
except DuplicateKeyError:
    duplicate_intermediate_rejected = True
else:
    duplicate_intermediate_rejected = False
check("duplicate YAML keys in intermediate fail closed", duplicate_intermediate_rejected)

open_ledger = copy.deepcopy(valid)
open_ledger["state"] = "open"
open_ledger["questions"][0].update({"status": "open", "resolution_derived_atom_refs": []})
open_ledger["questions"][0]["classification_verification"]["result"] = "unresolved"
open_ledger["questions"][0].pop("authority")
open_ledger["questions"][0].pop("scope")
open_ledger["questions"][0].pop("source")
open_ledger["questions"][0]["public_record_digest"] = record_digest("question", open_ledger["questions"][0])
open_ledger["candidate_atoms"] = [row for row in open_ledger["candidate_atoms"] if row["candidate_atom_id"] != "ATOM-F"]
open_ledger["findings"] = []
open_ledger["classification_ledger"] = [row for row in open_ledger["classification_ledger"] if row["candidate_atom_id"] != "ATOM-F"]
check("open intermediate with candidate-dependent unresolved question is structurally valid", validate_data(open_ledger) == [])
check("open intermediate cannot be used as closed ledger", any("closed" in e for e in validate_data(open_ledger, require_closed=True)))

bad = copy.deepcopy(valid)
bad["findings"][0]["status"] = "unresolved"
check("unresolved is rejected as finding status", any("unresolved is verifier-only" in e for e in validate_data(bad)))

bad = copy.deepcopy(valid)
bad["drifts"][0]["severity"] = "P3"
check("drift cannot masquerade as finding", any("drift cannot contain" in e for e in validate_data(bad)))

relabel = copy.deepcopy(valid)
relabel["drifts"] = []
relabel["findings"].append({
    "record_id": "REC-DF", "finding_id": "F-DRIFT", "candidate_atom_refs": ["ATOM-D"],
    "source_candidate_refs": ["SC-D"], "snapshot_id": relabel["snapshot_id"],
    "evidence_anchors": ["L20", "L30"], "severity": "P3",
    "judgment_provenance": "same values, notation only", "status": "verified",
    "public_record_digest": "",
})
relabel["findings"][-1]["public_record_digest"] = record_digest("finding", relabel["findings"][-1])
row = next(item for item in relabel["classification_ledger"] if item["candidate_atom_id"] == "ATOM-D")
row.update({"outcome": "finding", "target_record_id": "REC-DF"})
check("drift atom cannot be relabeled as a finding", any("contradicts atom classification_basis" in e for e in validate_data(relabel)))

bad = copy.deepcopy(valid)
bad["drifts"][0]["mystery_field"] = "silent extension"
check("unknown record fields fail closed", any("unknown fields" in e for e in validate_data(bad)))

bad = copy.deepcopy(valid)
bad["classification_ledger"].pop()
check("atom omission fails", any("without terminal classification" in e for e in validate_data(bad)))

bad = copy.deepcopy(valid)
bad["classification_ledger"].append(copy.deepcopy(bad["classification_ledger"][0]))
check("multiple terminal classifications fail", any("multiple terminal classifications" in e for e in validate_data(bad)))

erase_question = copy.deepcopy(valid)
erase_question["questions"] = []
erase_question["candidate_atoms"] = [item for item in erase_question["candidate_atoms"] if item["candidate_atom_id"] != "ATOM-F"]
erase_question["findings"] = []
erase_question["classification_ledger"] = [
    item for item in erase_question["classification_ledger"] if item["candidate_atom_id"] != "ATOM-F"
]
erase_question["findings"].append({
    "record_id": "REC-QF", "finding_id": "F-Q", "candidate_atom_refs": ["ATOM-Q"],
    "source_candidate_refs": ["SC-Q"], "snapshot_id": erase_question["snapshot_id"],
    "evidence_anchors": ["L10"], "severity": "P3", "judgment_provenance": "question relabeled",
    "status": "verified", "public_record_digest": "",
})
erase_question["findings"][0]["public_record_digest"] = record_digest("finding", erase_question["findings"][0])
row = next(item for item in erase_question["classification_ledger"] if item["candidate_atom_id"] == "ATOM-Q")
row.update({"outcome": "finding", "target_record_id": "REC-QF"})
check("original question atom cannot be relabeled as finding", any("contradicts atom classification_basis" in e for e in validate_data(erase_question)))

draft_question = copy.deepcopy(valid)
draft_question["questions"][0]["source"] = docmodel_ref(
    "docmodel.fixture-template-v1.draft.yaml"
)
draft_question["questions"][0]["public_record_digest"] = record_digest("question", draft_question["questions"][0])
check("draft docmodel cannot resolve a question", any("draft docmodel" in e or "approval_state" in e for e in validate_data(draft_question)))

bad = copy.deepcopy(valid)
bad["findings"][0]["candidate_atom_refs"].append("ATOM-D")
bad["findings"][0]["source_candidate_refs"].append("SC-D")
bad["findings"][0]["public_record_digest"] = record_digest("finding", bad["findings"][0])
check("public record cannot claim an atom owned by another ledger target", any("exactly match ledger rows" in e for e in validate_data(bad)))

bad = copy.deepcopy(valid)
bad["classification_ledger"][2]["evidence_anchors"] = ["L20", "L99"]
check("terminal ledger anchors must exist in target record", any("terminal target record" in e for e in validate_data(bad)))

split = copy.deepcopy(valid)
check("one source candidate may split across question and finding atoms", validate_data(split) == [])

merged = copy.deepcopy(valid)
merged["candidate_atoms"][2]["source_candidate_refs"].append("SC-N")
merged["drifts"][0]["source_candidate_refs"].append("SC-N")
merged["drifts"][0]["public_record_digest"] = record_digest("drift", merged["drifts"][0])
check("multiple source candidates may merge into one atom", validate_data(merged) == [])

bad = copy.deepcopy(valid)
bad["drifts"][0]["source_candidate_refs"].append("SC-N")
bad["drifts"][0]["public_record_digest"] = record_digest("drift", bad["drifts"][0])
check("record provenance cannot add sources absent from its atoms", any("union of its atoms" in e for e in validate_data(bad)))

partial = copy.deepcopy(valid)
partial["source_candidate_inventory"].append({"source_candidate_id": "SC-S", "lens": "L1", "statement": "일부만 억제", "evidence_anchors": ["L50", "L51"]})
partial["candidate_atoms"].extend([
    {
        "candidate_atom_id": "ATOM-SF", "source_candidate_refs": ["SC-S"],
        "statement": "유효 결함", "evidence_anchors": ["L50"],
        "classification_basis": basis("proven", "different", "not_applicable", "declared values differ"),
    },
    {
        "candidate_atom_id": "ATOM-SS", "source_candidate_refs": ["SC-S"],
        "statement": "기결정 억제", "evidence_anchors": ["L51"],
        "classification_basis": basis("proven", "different", "not_applicable", "declared values differ but authority suppresses"),
    },
])
partial["findings"].append({
    "record_id": "REC-SF", "finding_id": "F-02", "candidate_atom_refs": ["ATOM-SF"], "source_candidate_refs": ["SC-S"],
    "snapshot_id": partial["snapshot_id"], "evidence_anchors": ["L50"], "severity": "P3",
    "judgment_provenance": "independent review", "status": "rejected",
})
partial["suppressed"].append({
    "record_id": "REC-SS", "candidate_atom_refs": ["ATOM-SS"], "source_candidate_refs": ["SC-S"],
    "snapshot_id": partial["snapshot_id"], "evidence_anchors": ["L51"], "rationale": "approved decision",
    "authority_ref": approved_docmodel_ref(),
})
partial["findings"][-1]["public_record_digest"] = record_digest("finding", partial["findings"][-1])
partial["suppressed"][-1]["public_record_digest"] = record_digest("suppressed", partial["suppressed"][-1])
partial["classification_ledger"].extend([
    {"candidate_atom_id": "ATOM-SF", "outcome": "finding", "target_record_id": "REC-SF", "evidence_anchors": ["L50"]},
    {"candidate_atom_id": "ATOM-SS", "outcome": "suppressed", "target_record_id": "REC-SS", "evidence_anchors": ["L51"]},
])
check("source split with partial suppression passes", validate_data(partial) == [])

draft_suppression = copy.deepcopy(partial)
draft_suppression["suppressed"][-1]["authority_ref"] = docmodel_ref(
    "docmodel.fixture-template-v1.draft.yaml"
)
draft_suppression["suppressed"][-1]["public_record_digest"] = record_digest("suppressed", draft_suppression["suppressed"][-1])
check("draft docmodel cannot suppress a candidate", any("draft docmodel" in e or "approval_state" in e for e in validate_data(draft_suppression)))

scratch_only = copy.deepcopy(valid)
scratch_only["classification_ledger"][2]["evidence_anchors"] = ["SCRATCH-ONLY"]
check("scratch prose anchor cannot satisfy terminal ledger anchor", any("exactly preserve" in e for e in validate_data(scratch_only)))

fabricated_atom = copy.deepcopy(valid)
atom = next(item for item in fabricated_atom["candidate_atoms"] if item["candidate_atom_id"] == "ATOM-D")
atom["evidence_anchors"] = ["L999"]
fabricated_atom["drifts"][0]["evidence_anchors"] = ["L999"]
for variant in fabricated_atom["drifts"][0]["variants"]:
    variant["anchors"] = ["L999"]
fabricated_atom["drifts"][0]["public_record_digest"] = record_digest("drift", fabricated_atom["drifts"][0])
row = next(item for item in fabricated_atom["classification_ledger"] if item["candidate_atom_id"] == "ATOM-D")
row["evidence_anchors"] = ["L999"]
check("atom anchors cannot be fabricated outside source candidate evidence", any("referenced source candidates" in e for e in validate_data(fabricated_atom)))

dropped_source_anchor = copy.deepcopy(valid)
atom = next(item for item in dropped_source_anchor["candidate_atoms"] if item["candidate_atom_id"] == "ATOM-D")
atom["evidence_anchors"] = ["L20"]
dropped_source_anchor["drifts"][0]["evidence_anchors"] = ["L20"]
dropped_source_anchor["drifts"][0]["variants"] = [
    {"notation": "200개", "anchors": ["L20"]},
    {"notation": "200건", "anchors": ["L20"]},
]
dropped_source_anchor["classification_ledger"][2]["evidence_anchors"] = ["L20"]
dropped_source_anchor["drifts"][0]["public_record_digest"] = record_digest(
    "drift", dropped_source_anchor["drifts"][0]
)
check(
    "atomization cannot drop a source candidate anchor",
    any("anchors omitted during atomization" in error for error in validate_data(dropped_source_anchor)),
)

fabricated_record = copy.deepcopy(valid)
fabricated_record["drifts"][0]["evidence_anchors"] = ["L20", "L30", "L999"]
fabricated_record["drifts"][0]["public_record_digest"] = record_digest("drift", fabricated_record["drifts"][0])
check("record anchors must equal atom anchor union", any("union of its atoms' anchors" in e for e in validate_data(fabricated_record)))

fabricated_variant = copy.deepcopy(valid)
fabricated_variant["drifts"][0]["variants"][0]["anchors"] = ["L888"]
fabricated_variant["drifts"][0]["public_record_digest"] = record_digest("drift", fabricated_variant["drifts"][0])
check("drift variant anchors must be supported by drift evidence", any("must be in drift evidence_anchors" in e for e in validate_data(fabricated_variant)))

bad_lineage = copy.deepcopy(valid)
bad_lineage["candidate_atoms"][1]["derived_from_question_atom_refs"] = []
check("resolved question requires bidirectional derived lineage", any("reverse lineage" in e for e in validate_data(bad_lineage)))

parallel = copy.deepcopy(valid)
parallel["candidate_atoms"].extend([
    {
        "candidate_atom_id": "ATOM-QD",
        "source_candidate_refs": ["SC-Q"],
        "statement": "답 후 drift",
        "evidence_anchors": ["L10"],
        "classification_basis": basis("proven", "equal", "not_violated", "answer proves equal values (L10)"),
        "derived_from_question_atom_refs": ["ATOM-Q"],
    },
    {
        "candidate_atom_id": "ATOM-QN",
        "source_candidate_refs": ["SC-Q"],
        "statement": "답 후 nonissue",
        "evidence_anchors": ["L10"],
        "classification_basis": basis("proven", "equal", "intentional_variant", "answer approves intentional notation (L10)"),
        "derived_from_question_atom_refs": ["ATOM-Q"],
    },
])
parallel["questions"][0]["resolution_derived_atom_refs"].extend(["ATOM-QD", "ATOM-QN"])
parallel["questions"][0]["public_record_digest"] = record_digest("question", parallel["questions"][0])
parallel["drifts"].append({
    "record_id": "REC-QD", "candidate_atom_refs": ["ATOM-QD"], "source_candidate_refs": ["SC-Q"],
    "snapshot_id": parallel["snapshot_id"], "evidence_anchors": ["L10"], "detail": "답변으로 co-reference 확인",
    "variants": [{"notation": "A", "anchors": ["L10"]}, {"notation": "Ａ", "anchors": ["L10"]}],
    "co_reference_basis": "approved answer", "public_record_digest": "",
})
parallel["drifts"][-1]["public_record_digest"] = record_digest("drift", parallel["drifts"][-1])
parallel["nonissues"].append({
    "record_id": "REC-QN", "candidate_atom_refs": ["ATOM-QN"], "source_candidate_refs": ["SC-Q"],
    "snapshot_id": parallel["snapshot_id"], "evidence_anchors": ["L10"], "rationale": "approved intentional variant",
    "public_record_digest": "",
})
parallel["nonissues"][-1]["public_record_digest"] = record_digest("nonissue", parallel["nonissues"][-1])
parallel["classification_ledger"].extend([
    {"candidate_atom_id": "ATOM-QD", "outcome": "drift", "target_record_id": "REC-QD", "evidence_anchors": ["L10"]},
    {"candidate_atom_id": "ATOM-QN", "outcome": "nonissue", "target_record_id": "REC-QN", "evidence_anchors": ["L10"]},
])
check("resolved question may derive parallel drift and nonissue atoms", validate_data(parallel) == [])

print(f"\n=== {passed} passed, {failed} failed ===")
raise SystemExit(1 if failed else 0)
