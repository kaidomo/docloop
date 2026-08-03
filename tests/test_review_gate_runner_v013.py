#!/usr/bin/env python3
"""Runner-facing regression tests for the additive review-gate v0.13 surface."""

from __future__ import annotations

import hashlib
import json
import subprocess
import sys
from pathlib import Path, PurePosixPath
import tempfile

import yaml


ROOT = Path(__file__).resolve().parents[1]
BIN = ROOT / "bin" / "docloop"
CONVENTION_FIXTURES = ROOT / "tests" / "fixtures" / "review-gate" / "convention"
sys.path.insert(0, str(ROOT / "lib" / "review_gate"))
import runner as RG  # noqa: E402


passed = failed = 0


def check(name: str, ok: bool) -> None:
    global passed, failed
    if ok:
        passed += 1
        print(f"ok   {name}")
    else:
        failed += 1
        print(f"FAIL {name}")


help_text = RG.usage()
for command in (
    "validate-intermediate",
    "validate-result",
    "validate-convention-profile",
    "validate-convention-intake",
    "materialize-docmodel",
):
    check(f"help exposes {command}", f"docloop review-gate {command}" in help_text)
check("help does not expose internal front gate", "docloop review-gate front-gate" not in help_text)

for command in (
    "validate-intermediate",
    "validate-result",
    "validate-convention-profile",
    "validate-convention-intake",
    "materialize-docmodel",
):
    proc = subprocess.run(
        [str(BIN), "review-gate", command, "--help"],
        capture_output=True,
        text=True,
    )
    check(f"dispatch reaches {command} CLI", proc.returncode == 0)

profile = yaml.safe_load(
    (CONVENTION_FIXTURES / "synthetic-profile.yaml").read_text(encoding="utf-8")
)
intake = yaml.safe_load(
    (CONVENTION_FIXTURES / "intake-all-states.yaml").read_text(encoding="utf-8")
)
profile_raw = yaml.safe_dump(profile).encode()
intake_raw = yaml.safe_dump(intake).encode()
preflight = RG._validate_convention_pair(
    profile_raw,
    intake_raw,
    target_rel=PurePosixPath("docs/example.md"),
    target_sha="a" * 64,
)
check(
    "preflight is readiness-only and hash-bound",
    preflight["phase"] == "pre_lens"
    and preflight["target_snapshot"] == "sha256:" + "a" * 64
    and "lens_started" not in preflight,
)
invalid_profile = dict(profile)
invalid_profile["schema_version"] = 2
try:
    RG._validate_convention_pair(
        yaml.safe_dump(invalid_profile).encode(),
        intake_raw,
        target_rel=PurePosixPath("docs/example.md"),
        target_sha="a" * 64,
    )
except RG.GateError as exc:
    profile_validated_once = str(exc).count("schema_version must be 1") == 1
else:
    profile_validated_once = False
check("convention profile is validated exactly once", profile_validated_once)
try:
    RG._validate_convention_pair(
        profile_raw,
        intake_raw,
        target_rel=PurePosixPath("other.md"),
        target_sha="a" * 64,
    )
except RG.GateError as exc:
    mismatch_closed = "target_document" in str(exc)
else:
    mismatch_closed = False
check("preflight rejects target-document mismatch", mismatch_closed)


def _prepare_fixture(root: Path, run_id: str, *, target_document: str = "draft.md"):
    root.mkdir()
    target = root / "draft.md"
    target.write_text("# Demo\n\nPrepared target.\n", encoding="utf-8")
    profile = yaml.safe_load(
        (CONVENTION_FIXTURES / "synthetic-profile.yaml").read_text(encoding="utf-8")
    )
    intake = yaml.safe_load(
        (CONVENTION_FIXTURES / "intake-all-states.yaml").read_text(encoding="utf-8")
    )
    intake["target_snapshot"] = "sha256:" + hashlib.sha256(target.read_bytes()).hexdigest()
    intake["target_document"] = target_document
    (root / "profile.yaml").write_text(
        yaml.safe_dump(profile, allow_unicode=True, sort_keys=False), encoding="utf-8"
    )
    (root / "intake.yaml").write_text(
        yaml.safe_dump(intake, allow_unicode=True, sort_keys=False), encoding="utf-8"
    )
    args = [
        str(BIN), "review-gate", "prepare", str(root), run_id, "draft.md",
        "--unassured", "--no-terms", "--no-docmodel",
        "--convention-profile", "profile.yaml",
        "--convention-intake", "intake.yaml",
    ]
    return subprocess.run(args, capture_output=True, text=True)


with tempfile.TemporaryDirectory() as td:
    review = Path(td) / "review"
    proc = _prepare_fixture(review, "convention-ok")
    run = review / "review-gate" / "convention-ok"
    check("convention prepare succeeds end-to-end", proc.returncode == 0)
    manifest = yaml.safe_load((run / "RUN.yaml").read_text(encoding="utf-8"))
    preflight = json.loads(
        (run / "deterministic" / "CONVENTION_PREFLIGHT.json").read_text(encoding="utf-8")
    )
    check(
        "convention inputs are frozen and manifest-bound",
        (run / "frozen" / "convention-profile.yaml").read_bytes()
        == (review / "profile.yaml").read_bytes()
        and (run / "frozen" / "convention-intake.yaml").read_bytes()
        == (review / "intake.yaml").read_bytes()
        and manifest["sidecars"]["convention"]["profile_source"] == "profile.yaml",
    )
    check(
        "prepared convention evidence is readiness-only",
        preflight["phase"] == "pre_lens"
        and "lens_started" not in json.dumps(preflight)
        and all("lens_started" not in path.read_text(encoding="utf-8")
                for path in run.rglob("*") if path.is_file()),
    )
    try:
        RG._validate_prepared_packet(run)
    except RG.GateError:
        prepared_valid = False
    else:
        prepared_valid = True
    check("convention packet passes prepared integrity", prepared_valid)

    bad_receipt = run / "results" / "BAD.md"
    bad_receipt.write_text("not frontmatter\n", encoding="utf-8")
    (run / "frozen" / "target.txt").write_text("tampered\n", encoding="utf-8")
    result_proc = subprocess.run(
        [str(BIN), "review-gate", "validate-result", str(run), "results/BAD.md"],
        capture_output=True,
        text=True,
    )
    check(
        "validate-result rejects prepared tamper before receipt parsing",
        result_proc.returncode != 0
        and "prepared packet validation failed" in result_proc.stderr
        and "frontmatter" not in result_proc.stderr,
    )

with tempfile.TemporaryDirectory() as td:
    review = Path(td) / "review"
    proc = _prepare_fixture(review, "target-mismatch", target_document="other.md")
    check(
        "convention target mismatch fails before reservation",
        proc.returncode != 0 and not (review / "review-gate" / "target-mismatch").exists(),
    )

with tempfile.TemporaryDirectory() as td:
    review = Path(td) / "review"
    review.mkdir()
    (review / "draft.md").write_text("# Demo\n", encoding="utf-8")
    (review / "profile.yaml").write_bytes(
        (CONVENTION_FIXTURES / "synthetic-profile.yaml").read_bytes()
    )
    proc = subprocess.run(
        [
            str(BIN), "review-gate", "prepare", str(review), "half-pair", "draft.md",
            "--unassured", "--no-terms", "--no-docmodel",
            "--convention-profile", "profile.yaml",
        ],
        capture_output=True,
        text=True,
    )
    check(
        "partial convention pair fails before reservation",
        proc.returncode != 0 and not (review / "review-gate" / "half-pair").exists(),
    )

print(f"\n=== review-gate runner v0.13: {passed} passed, {failed} failed ===")
raise SystemExit(1 if failed else 0)
