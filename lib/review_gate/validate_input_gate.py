#!/usr/bin/env python3
"""Validate the CONTRACT §1 input gate block (#196 · #206 · #202).

The gate asks three questions the earlier table never asked:

* ⑥ ``editing_state`` — is somebody editing the target right now (#196)? A target
  that is being edited, or whose state is unknown, defers §6 verification (§7).
  Deferral is never a shortcut to done: it produces an intermediate, not a receipt.
* ⑦ ``target_maturity`` — is the target a finished document or a working draft
  (#206)? Declaring ``draft`` only *adds* an obligation: the document's own
  registered open items must be supplied so findings landing on them can be
  **classified**. The open-item ledger is never suppression authority (§2).
* ⑧ ``source_copy`` — was the full target archived into the run folder before the
  lenses read it (#202 ④)? Its hash binds the output to the snapshot it read, so a
  revision mid-run cannot silently move the anchors.
* ⑨ ``prior_round`` — does a prior round's output exist for this target (#208 제안
  3)? ``exists: false`` requires nothing further at this gate; ``exists: true``
  requires naming that prior output (``output_ref``) so the done receipt can bind
  to actual proof that §13's ``match_review_rounds.py`` ran against it.

The done receipt additionally carries ``run_root`` (#228③) — a repository-relative
pointer to the run folder holding ``source_copy``, so the done validator can re-hash
the archived bytes instead of trusting the receipt's declaration a second time. The
pre-lens file never needs it: the front gate already receives the run folder out of
band (``--run-root``), before any receipt exists to point back at it.

This module owns the shape and the enums so the pre-lens front gate
(``review_front_gate.py``) and the done receipt (``validate_review_result.py``)
cannot drift apart.
"""

from __future__ import annotations

import argparse
import hashlib
from pathlib import Path
import re
import sys
from typing import Any

import yaml

if __package__:
    from .validate_convention_profile import (
        DuplicateKeyError,
        _exact_keys,
        _nonempty,
        _validate_string_list,
        load_yaml,
    )
else:
    from validate_convention_profile import (
        DuplicateKeyError,
        _exact_keys,
        _nonempty,
        _validate_string_list,
        load_yaml,
    )


SCHEMA_VERSION = 1
EDITING_STATES = {"frozen", "in_progress", "unknown"}
#: §7 defers verification for both of these — "unknown" is the conservative branch
#: the issue asked for, so a gate that simply never asked cannot reach done either.
DEFERRING_EDITING_STATES = {"in_progress", "unknown"}
TARGET_MATURITIES = {"complete", "draft", "unknown"}
#: r1-05: "unknown" must not be cheaper than the honest "draft".
OPEN_ITEMS_REQUIRED_MATURITIES = {"draft", "unknown"}
# fullmatch()로 검사한다 — `$`는 문자열 끝 개행 앞에서도 맞으므로 `.match()`와
# 함께 쓰면 끝에 개행이 붙은 해시 값이 통과한다. §1 입력 게이트가 원문 스냅샷
# 결속을 검증하는 지점이라 이 해시는 등가 비교로 쓰인다(#231).
HEX64 = re.compile(r"^[0-9a-f]{64}$")
SNAPSHOT_SHA256 = re.compile(r"^sha256:([0-9a-f]{64})$")
NO_LEDGER = "none"


def defers_verification(block: Any) -> bool:
    """True when §7 defers §6 verification for this target (#196)."""
    return isinstance(block, dict) and block.get("editing_state") in DEFERRING_EDITING_STATES


def open_items_ledger_path(block: Any) -> str | None:
    """Path of the document's registered open-item ledger, if one was supplied."""
    if not isinstance(block, dict):
        return None
    open_items = block.get("open_items")
    if not isinstance(open_items, dict):
        return None
    ref = open_items.get("ledger_ref")
    if isinstance(ref, dict) and _nonempty(ref.get("path")):
        return str(ref["path"])
    return None


def open_items_ledger_sha256(block: Any) -> str | None:
    """Declared content hash of the open-item ledger, if one was supplied."""
    if not isinstance(block, dict):
        return None
    open_items = block.get("open_items")
    if not isinstance(open_items, dict):
        return None
    ref = open_items.get("ledger_ref")
    if isinstance(ref, dict) and isinstance(ref.get("sha256"), str):
        return ref["sha256"]
    return None


def classified_record_ids(block: Any) -> list[str]:
    """Receipt record ids marked as landing on a registered open item (#206)."""
    if not isinstance(block, dict):
        return []
    open_items = block.get("open_items")
    if not isinstance(open_items, dict):
        return []
    ids = open_items.get("classified_record_ids")
    return list(ids) if isinstance(ids, list) else []


def _validate_source_copy(value: Any, label: str, snapshot_id: Any) -> list[str]:
    errors = _exact_keys(value, {"path", "sha256"}, set(), label)
    if errors:
        return errors
    if not _nonempty(value.get("path")):
        errors.append(f"{label}.path must be nonempty")
    digest = value.get("sha256")
    if not isinstance(digest, str) or not HEX64.fullmatch(digest):
        errors.append(f"{label}.sha256 must be 64 lowercase hex characters")
        return errors
    # #202 ①②: the output is bound to the snapshot it read. When the snapshot id is
    # itself a content hash the archived copy must BE that snapshot — otherwise the
    # run archived some other revision and the binding is a claim without a referent.
    # Not every snapshot_id is hash-shaped (symbolic ids like "sha256:target-snapshot"
    # are a real, established fixture convention elsewhere) -- this binding check
    # applies only when it is.
    if isinstance(snapshot_id, str):
        # r1-01 (PR #231 r1): matching the raw string with fullmatch() correctly
        # rejects a trailing-newline snapshot_id where match() used to accept it --
        # but `if match and ...` then silently SKIPPED the digest-equality check on
        # that same rejection, turning a fixed bypass into a new one (a mismatched
        # digest paired with a newline-corrupted snapshot_id passed with zero
        # errors).
        #
        # r2-01 (PR #231 r2): the first fix used rstrip("\n"), which removes ALL
        # trailing LFs and only LFs -- "sha256:<hex>\n\n" and "sha256:<hex>\r\n"
        # both still ended up matching, reintroducing the same class of bypass one
        # level down. A legitimate YAML block scalar appends exactly one trailing
        # "\n" and nothing else, so normalize exactly that -- strip one trailing
        # "\n" by slicing (not rstrip), never touch "\r", and let fullmatch() do
        # the strict comparison on whatever remains. "\r\n", "\n\n", and mid-string
        # whitespace all still fail fullmatch() after this and correctly fall back
        # to "not hash-shaped, skip" -- verified directly, not asserted.
        normalized = snapshot_id[:-1] if snapshot_id.endswith("\n") else snapshot_id
        match = SNAPSHOT_SHA256.fullmatch(normalized)
        if match and digest != match.group(1):
            errors.append(f"{label}.sha256 must equal the snapshot_id content hash")
    return errors


def _validate_open_items(value: Any, label: str, *, allow_classification: bool) -> list[str]:
    optional = {"classified_record_ids"} if allow_classification else set()
    errors = _exact_keys(value, {"ledger_ref"}, optional, label)
    if errors:
        return errors
    ref = value.get("ledger_ref")
    has_ledger = False
    if ref == NO_LEDGER:
        has_ledger = False
    elif isinstance(ref, dict):
        ref_errors = _exact_keys(ref, {"path", "sha256"}, set(), f"{label}.ledger_ref")
        if ref_errors:
            errors.extend(ref_errors)
        else:
            has_ledger = True
            if not _nonempty(ref.get("path")):
                errors.append(f"{label}.ledger_ref.path must be nonempty")
            if not isinstance(ref.get("sha256"), str) or not HEX64.fullmatch(ref["sha256"]):
                errors.append(f"{label}.ledger_ref.sha256 must be 64 lowercase hex characters")
    else:
        errors.append(f"{label}.ledger_ref must be 'none' or a path/sha256 mapping")
        return errors

    if "classified_record_ids" in value:
        ids = value["classified_record_ids"]
        errors.extend(
            _validate_string_list(ids, f"{label}.classified_record_ids", nonempty=False)
        )
        if not has_ledger and isinstance(ids, list) and ids:
            errors.append(
                f"{label}.classified_record_ids requires a registered open-item ledger"
            )
    return errors


def _validate_prior_round(value: Any, label: str) -> list[str]:
    """⑨ Does a prior round's output exist (#208 제안 3)?

    ``exists: false`` requires nothing further here — the obligation it creates
    (the output's ``round_context.round_label`` must literally say ``r1``) lives in
    the done receipt (``validate_review_result.py``), not in this pre-lens shape
    check, because "1라운드" is a claim about the *output*, which does not exist yet
    when the input gate is recorded. ``exists: true`` requires ``output_ref`` naming
    the prior round's output file and its round number, so the receipt-side check can
    demand ``round_context.comparison_ref`` — proof that ``match_review_rounds.py``
    (§13) was actually run against it, not just a self-report that it was.
    """
    errors = _exact_keys(value, {"exists"}, {"output_ref"}, label)
    if errors:
        return errors
    exists = value.get("exists")
    if not isinstance(exists, bool):
        errors.append(f"{label}.exists must be boolean")
        return errors
    has_ref = "output_ref" in value
    if exists and not has_ref:
        errors.append(f"{label}.output_ref is required when {label}.exists is true")
    elif not exists and has_ref:
        errors.append(f"{label}.output_ref must be omitted when {label}.exists is false")
    if has_ref:
        ref = value["output_ref"]
        ref_errors = _exact_keys(ref, {"path", "sha256", "round_no"}, set(), f"{label}.output_ref")
        if ref_errors:
            errors.extend(ref_errors)
        else:
            if not _nonempty(ref.get("path")):
                errors.append(f"{label}.output_ref.path must be nonempty")
            else:
                candidate = Path(ref["path"])
                if candidate.is_absolute() or ".." in candidate.parts:
                    errors.append(f"{label}.output_ref.path must be a safe repository-relative path")
            digest = ref.get("sha256")
            if not isinstance(digest, str) or not HEX64.fullmatch(digest):
                errors.append(f"{label}.output_ref.sha256 must be 64 lowercase hex characters")
            round_no = ref.get("round_no")
            if not isinstance(round_no, int) or isinstance(round_no, bool) or round_no < 1:
                errors.append(f"{label}.output_ref.round_no must be a positive integer")
    return errors


def _validate_run_root(value: Any, label: str) -> list[str]:
    """#228③: the receipt-side pointer to the run folder holding ``source_copy``.

    Only a syntactic check lives here — the pre-lens file has no repository root to
    resolve against. The done receipt validator (which does know the repository root)
    resolves this path and re-hashes the archived bytes at done time.
    """
    if not _nonempty(value):
        return [f"{label} must be a nonempty repository-relative path"]
    candidate = Path(value)
    if candidate.is_absolute() or ".." in candidate.parts:
        return [f"{label} must be a safe repository-relative path"]
    return []


def validate_block(
    block: Any,
    *,
    label: str = "input_gate",
    with_schema_version: bool = False,
    allow_classification: bool = False,
    require_run_root: bool = False,
    snapshot_id: Any = None,
) -> list[str]:
    """Validate one §1 input-gate block. Returns human-readable errors."""
    required = {"editing_state", "target_maturity", "source_copy", "prior_round"}
    if with_schema_version:
        required = required | {"schema_version"}
    if require_run_root:
        required = required | {"run_root"}
    errors = _exact_keys(block, required, {"open_items", "run_root"}, label)
    if not isinstance(block, dict):
        return errors
    if with_schema_version and block.get("schema_version") != SCHEMA_VERSION:
        errors.append(f"{label}.schema_version must be {SCHEMA_VERSION}")
    if "run_root" in block:
        errors.extend(_validate_run_root(block["run_root"], f"{label}.run_root"))
    editing_state = block.get("editing_state")
    if editing_state not in EDITING_STATES:
        errors.append(f"{label}.editing_state must be one of {sorted(EDITING_STATES)}")
    maturity = block.get("target_maturity")
    if maturity not in TARGET_MATURITIES:
        errors.append(f"{label}.target_maturity must be one of {sorted(TARGET_MATURITIES)}")
    if "source_copy" in block:
        errors.extend(_validate_source_copy(block["source_copy"], f"{label}.source_copy", snapshot_id))
    if "prior_round" in block:
        errors.extend(_validate_prior_round(block["prior_round"], f"{label}.prior_round"))
    if "open_items" in block:
        errors.extend(
            _validate_open_items(
                block["open_items"],
                f"{label}.open_items",
                allow_classification=allow_classification,
            )
        )
    elif maturity in OPEN_ITEMS_REQUIRED_MATURITIES:
        # #206: declaring a draft adds an obligation, it never removes one. `unknown`
        # carries the same obligation (peer review r1-05) — otherwise "I didn't check"
        # would be the cheap path that the honest `draft` answer is denied.
        errors.append(
            f"{label}.open_items is required when target_maturity is {maturity} "
            "(the document's registered open items are the classification baseline, "
            "not a suppression baseline)"
        )
    return errors


def verify_source_copy_bytes(block: Any, run_root: Path, label: str = "review_input_gate") -> list[str]:
    """#202 ④: the archived copy must actually exist and hash to what was declared.

    Without this the gate only records a *claim* that the target was archived, and the
    whole point of ④ is that the claim was false in the 2026-08-10 run — the A revision
    was gone and no real diff could be taken. Path traversal out of the run folder is
    refused so the "archive" cannot be some unrelated file elsewhere on disk.
    """
    if not isinstance(block, dict) or not isinstance(block.get("source_copy"), dict):
        return []
    source_copy = block["source_copy"]
    raw_path = source_copy.get("path")
    declared = source_copy.get("sha256")
    if not _nonempty(raw_path) or not isinstance(declared, str) or not HEX64.fullmatch(declared):
        return []
    candidate = Path(raw_path)
    if candidate.is_absolute() or ".." in candidate.parts:
        return [f"{label}.source_copy.path must stay inside the run folder"]
    raw_copy_path = run_root / candidate
    # r2-04: a symlink is not an archive — it is a pointer at something that can be
    # repointed or edited elsewhere. The archive must be a real, private regular file.
    if raw_copy_path.is_symlink():
        return [f"{label}.source_copy.path must be a regular file, not a symlink"]
    copy_path = raw_copy_path.resolve()
    try:
        copy_path.relative_to(run_root.resolve())
    except ValueError:
        return [f"{label}.source_copy.path escapes the run folder"]
    try:
        stat = copy_path.stat()
    except OSError as exc:
        return [f"{label}.source_copy was not archived in the run folder: {exc}"]
    if not copy_path.is_file():
        return [f"{label}.source_copy.path must be a regular file"]
    # r2-04: a hard link shares bytes with a name outside the run folder, so a later
    # edit through that other name silently mutates the "archive" this run is bound to.
    if stat.st_nlink > 1:
        return [
            f"{label}.source_copy must not be a hard link (st_nlink={stat.st_nlink}); "
            "the archive must be a private copy that nothing else can edit"
        ]
    try:
        payload = copy_path.read_bytes()
    except OSError as exc:
        return [f"{label}.source_copy was not archived in the run folder: {exc}"]
    if hashlib.sha256(payload).hexdigest() != declared:
        return [f"{label}.source_copy.sha256 does not match the archived bytes"]
    return []


def validate(path: Path) -> list[str]:
    try:
        data = load_yaml(path)
    except (OSError, DuplicateKeyError, yaml.YAMLError) as exc:
        return [str(exc)]
    return validate_block(data, label="review_input_gate", with_schema_version=True)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Validate a review-gate §1 input gate file.")
    parser.add_argument("input_gate", type=Path)
    args = parser.parse_args(argv)
    errors = validate(args.input_gate)
    if errors:
        for error in errors:
            print(f"FAIL: {error}", file=sys.stderr)
        return 1
    print("OK: review-gate input gate is complete")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
