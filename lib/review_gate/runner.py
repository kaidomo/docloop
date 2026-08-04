#!/usr/bin/env python3
"""Prepare an explicit, append-only docloop review-gate protocol packet.

The packet fixes input bytes and declared lens visibility.  It does not run models,
provide a filesystem security boundary, prove agent independence, or mark a review
passed/done.
"""

from __future__ import annotations

import argparse
import copy
import errno
import hashlib
import json
import os
import posixpath
import re
import stat
import subprocess
import sys
import uuid
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath
from typing import Any, Callable

import yaml

import scan_terms
import validate_decisions


SCHEMA_VERSION = 1
TOOL_VERSION = "0.13.0"
UPSTREAM_REPOSITORY = "kaidomo/docuauthring"
UPSTREAM_COMMIT = "44604347e95067fe93a9b62280b76d16f516d5b4"
UPSTREAM_CONTRACT_VERSION = "0.12"
RUN_ID_RE = re.compile(r"^[a-z0-9][a-z0-9-]{0,63}$")
SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
MAX_TARGET_BYTES = 10 * 1024 * 1024
MAX_SIDECAR_BYTES = 2 * 1024 * 1024
MAX_PROVENANCE_BYTES = 10 * 1024 * 1024
MAX_CAPTURE_BYTES = 50 * 1024 * 1024
MAX_TARGET_LINES = 99_999
MAX_PROVENANCE_FILES = 64
FAIL_PHASES = {"reserve", "freeze", "validate", "prompts", "complete-write"}
ROOT = Path(__file__).resolve().parents[2]
DEFAULT_AXES = ROOT / "templates" / "review-gate" / "default-axes.md"
TOOL_DIR = Path(__file__).resolve().parent

# Unit tests may replace this hook to mutate a source after its bytes were read but
# before the descriptor fingerprint is checked.
_READ_TEST_HOOK: Callable[[str], None] | None = None


class GateError(Exception):
    """Expected fail-closed preparation error."""


class MissingInput(GateError):
    """A relative input does not exist."""


def _utc_now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _json_bytes(value: Any) -> bytes:
    return (json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n").encode("utf-8")


def _yaml_bytes(value: Any) -> bytes:
    return (yaml.safe_dump(value, sort_keys=False, allow_unicode=True, width=10**9).rstrip("\n") + "\n").encode("utf-8")


def _normalize_rel(value: str, label: str) -> PurePosixPath:
    if not value or "\x00" in value or "\\" in value:
        raise GateError(f"{label}: invalid relative path")
    p = PurePosixPath(value)
    if p.is_absolute() or any(part in ("", ".", "..") for part in p.parts):
        raise GateError(f"{label}: must be a normalized path below the review folder")
    if p.parts[0] == "review-gate":
        raise GateError(f"{label}: review-gate output tree cannot be an input")
    return p


def _resolve_ref(sidecar: PurePosixPath, ref: str, label: str) -> PurePosixPath:
    if not isinstance(ref, str) or not ref.strip() or "\x00" in ref or "\\" in ref:
        raise GateError(f"{label}: source_ref must be a non-empty POSIX path")
    if ref.startswith("/") or ref.startswith("~"):
        raise GateError(f"{label}: absolute/external source_ref is not allowed; stage it first")
    joined = posixpath.normpath(posixpath.join(sidecar.parent.as_posix(), ref))
    if joined in ("", ".", "..") or joined.startswith("../"):
        raise GateError(f"{label}: source_ref resolves outside the review folder")
    return _normalize_rel(joined, label)


def _open_directory(path: Path, label: str) -> int:
    absolute = path if path.is_absolute() else Path.cwd() / path
    flags = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0)
    nofollow = getattr(os, "O_NOFOLLOW", 0)
    fd = os.open(absolute.anchor, flags)
    try:
        for part in absolute.parts[1:]:
            try:
                nxt = os.open(part, flags | nofollow, dir_fd=fd)
            except OSError as exc:
                raise GateError(f"{label}: every path component must be a real directory: {absolute}") from exc
            os.close(fd)
            fd = nxt
        return fd
    except BaseException:
        os.close(fd)
        raise


def _directory_argument(value: str, label: str) -> Path:
    """Reject a symlink/non-directory at the user-named leaf, then canonicalize it.

    macOS exposes /var as a system symlink to /private/var, so rejecting every
    ancestor would make ordinary tempfile paths unusable.  All later access is
    anchored to the opened canonical directory descriptor.
    """
    given = Path(value).absolute()
    try:
        st = given.lstat()
    except FileNotFoundError as exc:
        raise GateError(f"{label}: directory missing: {given}") from exc
    if stat.S_ISLNK(st.st_mode) or not stat.S_ISDIR(st.st_mode):
        raise GateError(f"{label}: must be a real non-symlink directory: {given}")
    return given.resolve()


def _open_rel_parent(base_fd: int, rel: PurePosixPath, create: bool) -> int:
    fd = os.dup(base_fd)
    flags = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0) | getattr(os, "O_NOFOLLOW", 0)
    try:
        for part in rel.parts[:-1]:
            if create:
                try:
                    os.mkdir(part, 0o700, dir_fd=fd)
                except FileExistsError:
                    pass
            try:
                nxt = os.open(part, flags, dir_fd=fd)
            except OSError as exc:
                raise GateError(f"unsafe or missing directory component in {rel.as_posix()}") from exc
            os.close(fd)
            fd = nxt
        return fd
    except BaseException:
        os.close(fd)
        raise


def _fingerprint(st: os.stat_result) -> tuple[int, int, int, int, int]:
    return st.st_dev, st.st_ino, st.st_size, st.st_mtime_ns, st.st_ctime_ns


def _read_relative(base_fd: int, rel: PurePosixPath, label: str, max_bytes: int) -> bytes:
    parent_fd = _open_rel_parent(base_fd, rel, False)
    flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
    try:
        try:
            fd = os.open(rel.name, flags, dir_fd=parent_fd)
        except FileNotFoundError as exc:
            raise MissingInput(f"{label}: missing: {rel.as_posix()}") from exc
        except OSError as exc:
            raise GateError(f"{label}: must be a regular non-symlink file: {rel.as_posix()}") from exc
    finally:
        os.close(parent_fd)
    try:
        before = os.fstat(fd)
        if not stat.S_ISREG(before.st_mode):
            raise GateError(f"{label}: must be a regular file: {rel.as_posix()}")
        if before.st_size > max_bytes:
            raise GateError(f"{label}: file cap exceeded ({before.st_size} > {max_bytes})")
        chunks: list[bytes] = []
        total = 0
        while True:
            chunk = os.read(fd, min(65_536, max_bytes + 1 - total))
            if not chunk:
                break
            chunks.append(chunk)
            total += len(chunk)
            if total > max_bytes:
                raise GateError(f"{label}: file cap exceeded")
        if _READ_TEST_HOOK is not None:
            _READ_TEST_HOOK(label)
        after = os.fstat(fd)
        data = b"".join(chunks)
        if _fingerprint(before) != _fingerprint(after) or len(data) != after.st_size:
            raise GateError(f"{label}: changed while being captured")
        return data
    finally:
        os.close(fd)


def _load_strict(raw: bytes, label: str) -> Any:
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise GateError(f"{label}: not valid UTF-8") from exc
    try:
        return yaml.load(text, Loader=validate_decisions._StrictLoader)
    except (yaml.YAMLError, validate_decisions.DupKeyError) as exc:
        raise GateError(f"{label}: malformed or duplicate-key YAML: {exc}") from exc


def _write_relative(base_fd: int, rel: PurePosixPath, data: bytes, mode: int = 0o600) -> None:
    parent_fd = _open_rel_parent(base_fd, rel, True)
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0)
    try:
        try:
            fd = os.open(rel.name, flags, mode, dir_fd=parent_fd)
        except OSError as exc:
            if exc.errno == errno.EEXIST:
                raise GateError(f"no-clobber: output already exists: {rel.as_posix()}") from exc
            raise GateError(f"could not create output safely: {rel.as_posix()}") from exc
    finally:
        os.close(parent_fd)
    try:
        with os.fdopen(fd, "wb") as fh:
            fh.write(data)
            fh.flush()
            os.fsync(fh.fileno())
    except BaseException:
        try:
            parent_fd = _open_rel_parent(base_fd, rel, False)
            try:
                os.unlink(rel.name, dir_fd=parent_fd)
            finally:
                os.close(parent_fd)
        except OSError:
            pass
        raise


def _mkdir_open(parent_fd: int, name: str, exclusive: bool) -> int:
    if exclusive:
        try:
            os.mkdir(name, 0o700, dir_fd=parent_fd)
        except FileExistsError as exc:
            raise GateError(f"run ID already exists; use a new ID: {name}") from exc
    else:
        try:
            os.mkdir(name, 0o700, dir_fd=parent_fd)
        except FileExistsError:
            pass
    flags = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0) | getattr(os, "O_NOFOLLOW", 0)
    try:
        return os.open(name, flags, dir_fd=parent_fd)
    except OSError as exc:
        raise GateError(f"output path is not a real directory: {name}") from exc


def _phase(name: str) -> None:
    requested = os.environ.get("DOCLOOP_REVIEW_GATE_FAIL_AFTER")
    if requested and requested not in FAIL_PHASES:
        raise GateError(f"unknown DOCLOOP_REVIEW_GATE_FAIL_AFTER phase: {requested}")
    if requested == name:
        raise GateError(f"injected failure after phase: {name}")


def _provenance_name(kind: str, slot: str, index: int) -> str:
    clean = re.sub(r"[^a-z0-9-]+", "-", slot.lower()).strip("-") or "source"
    return f"{kind}-{index:02d}-{clean}.source"


def _freeze_ref(
    review_fd: int,
    sidecar_rel: PurePosixPath,
    obj: dict[str, Any],
    ref_key: str,
    hash_key: str,
    kind: str,
    slot: str,
    index: int,
    provenance: dict[str, bytes],
    provenance_rows: list[dict[str, Any]],
) -> None:
    ref, want = obj.get(ref_key), obj.get(hash_key)
    if ref is None and want is None:
        return
    if not isinstance(ref, str) or not ref.strip() or not isinstance(want, str):
        raise GateError(f"{kind}.{slot}: {ref_key}/{hash_key} must be a complete string pair")
    if not SHA256_RE.fullmatch(want):
        raise GateError(f"{kind}.{slot}: {hash_key} must be lowercase sha256")
    source_rel = _resolve_ref(sidecar_rel, ref, f"{kind}.{slot}")
    raw = _read_relative(review_fd, source_rel, f"{kind}.{slot} provenance", MAX_PROVENANCE_BYTES)
    got = _sha(raw)
    if got != want:
        raise GateError(f"{kind}.{slot}: STALE provenance hash mismatch")
    frozen_name = _provenance_name(kind, slot, index)
    provenance[frozen_name] = raw
    provenance_rows.append({
        "kind": kind,
        "slot": slot,
        "source": source_rel.as_posix(),
        "frozen": f"frozen/provenance/{frozen_name}",
        "bytes": len(raw),
        "sha256": got,
    })
    obj[ref_key] = f"provenance/{frozen_name}"


def _freeze_decisions(review_fd: int, rel: PurePosixPath) -> tuple[bytes, list[dict[str, Any]], dict[str, bytes]]:
    raw = _read_relative(review_fd, rel, "decisions", MAX_SIDECAR_BYTES)
    data = _load_strict(raw, "decisions")
    if not isinstance(data, dict):
        raise GateError("decisions: top level must be a mapping")
    frozen = copy.deepcopy(data)
    provenance: dict[str, bytes] = {}
    rows: list[dict[str, Any]] = []
    meta = frozen.get("meta")
    if isinstance(meta, dict):
        _freeze_ref(review_fd, rel, meta, "source_ref", "source_version_hash", "decisions", "meta", 1, provenance, rows)
    decisions = frozen.get("decisions")
    if isinstance(decisions, list):
        pidx = 2
        for i, item in enumerate(decisions):
            if isinstance(item, dict) and ("source_ref" in item or "source_hash" in item):
                _freeze_ref(review_fd, rel, item, "source_ref", "source_hash", "decisions", f"item-{i+1}", pidx, provenance, rows)
                pidx += 1
    return _yaml_bytes(frozen), rows, provenance


def _freeze_terms(review_fd: int, rel: PurePosixPath) -> tuple[bytes, list[dict[str, Any]], dict[str, bytes]]:
    raw = _read_relative(review_fd, rel, "terms", MAX_SIDECAR_BYTES)
    data = _load_strict(raw, "terms")
    if not isinstance(data, dict):
        raise GateError("terms: top level must be a mapping")
    frozen = copy.deepcopy(data)
    provenance: dict[str, bytes] = {}
    rows: list[dict[str, Any]] = []
    meta = frozen.get("meta")
    if isinstance(meta, dict):
        _freeze_ref(review_fd, rel, meta, "source_ref", "source_hash", "terms", "meta", 1, provenance, rows)
    return _yaml_bytes(frozen), rows, provenance


def _freeze_docmodel(review_fd: int, rel: PurePosixPath) -> tuple[bytes, list[dict[str, Any]], dict[str, bytes]]:
    raw = _read_relative(review_fd, rel, "docmodel", MAX_SIDECAR_BYTES)
    data = _load_strict(raw, "docmodel")
    if not isinstance(data, dict) or not isinstance(data.get("meta"), dict):
        raise GateError("docmodel: top level and meta must be mappings")
    frozen = copy.deepcopy(data)
    meta = frozen["meta"]
    if not isinstance(meta.get("approved_by"), str) or not meta["approved_by"].strip():
        raise GateError("docmodel: meta.approved_by must be a non-empty string")
    provenance: dict[str, bytes] = {}
    rows: list[dict[str, Any]] = []
    _freeze_ref(review_fd, rel, meta, "source_ref", "source_hash", "docmodel", "meta", 1, provenance, rows)
    return _yaml_bytes(frozen), rows, provenance


def _numbered_target(text: str, source_rel: str, digest: str) -> bytes:
    lines = text.splitlines()
    body = [
        f"# Frozen target: `{source_rel}`",
        f"- sha256: `{digest}`",
        "- Cite anchors exactly as `L01`, `L02`, ...; quote the text after `|` verbatim.",
        "",
    ]
    body.extend(f"L{i:02d} | {line}" for i, line in enumerate(lines, 1))
    return ("\n".join(body) + "\n").encode("utf-8")


def _prompt_l1(target_sha: str) -> bytes:
    return f"""# L1 — cold read

Use a fresh model context. Read only `TARGET.md` in this directory. Do not inspect the
review brief, decisions, axes, docmodel, sibling lenses, or earlier outputs.

Target snapshot sha256: `{target_sha}`.

Find candidate defects a cold reader can establish from the complete target. Every
candidate must be self-contained and quote at least one exact `L<number>` anchor. Do not
assign canonical finding IDs; synthesis owns IDs. Do not modify files.
""".encode("utf-8")


def _prompt_l2(target_sha: str, assured: bool) -> bytes:
    decision_line = "Read `DECISIONS.yaml`; only its validated, non-superseded entries may support suppression." if assured else "Read `UNASSURED.md`; no candidate may be suppressed as already decided."
    return f"""# L2 — decision comparison

Use a fresh model context. Read only files in this directory. Do not inspect axes,
docmodel, sibling lenses, or earlier outputs.

Target snapshot sha256: `{target_sha}`. {decision_line}

First emit `## Decision-related locations`, then `## 신규 쟁점`. In the latter, list
only conflicts/new issues established by the target and decision input. Quote exact
`L<number>` anchors. Do not assign canonical finding IDs; synthesis owns IDs. Do not
modify files.
""".encode("utf-8")


def _prompt_l3(target_sha: str, has_docmodel: bool) -> bytes:
    dm = "Read `DOCMODEL.yaml` as a human-approved but only partially preflighted declaration." if has_docmodel else "No docmodel is supplied; do not infer structural ownership or suppress a candidate because a declaration is absent."
    return f"""# L3 — axis sweep

Use a fresh model context. Read only files in this directory. Do not inspect decisions,
sibling lenses, or earlier outputs.

Target snapshot sha256: `{target_sha}`. Read `AXES.md`. {dm}

Sweep every axis and enumerate cross-section/table/list mismatches. Quote exact
`L<number>` anchors. If a relevant structural rule is undetermined, keep the candidate and
mark `규약 미확정` / `unresolved`; absence is not suppression evidence. Do not assign
canonical finding IDs; synthesis owns IDs. Do not modify files.
""".encode("utf-8")


def _synthesis_guide(has_terms: bool, extra_unassured: bool) -> bytes:
    scan = "Include every hit in `deterministic/TERM_SCAN.md` in the candidate union and preserve its audit header." if has_terms else "No deterministic term dictionary was selected; do not claim exhaustive terminology coverage."
    unassured = "No finding may be suppressed as previously decided; the final human must explicitly accept unassured decision history." if extra_unassured else "Apply suppression only with a validated decision ID; preserve stale/superseded items as non-suppression context."
    return f"""# Synthesis handoff — manual step

This packet is prepared, not reviewed. Run the three lens prompts in fresh contexts and
save outputs as `results/L1.md`, `results/L2.md`, and `results/L3.md`. Then run synthesis
in another context over exactly those three files plus any term scan.

{scan}

Candidate set = L1 union L2 `신규 쟁점` union L3 union term-scan hits. Do not filter a
single-lens candidate. Atomize by independent fix/verification action, merge only the
same defect, and preserve the union of all anchors in findings, suppressions, or
non-promoted records. {unassured}

Every canonical finding must contain: (1) finding_id, (2) verbatim evidence + location,
(3) contrary-quote search result, (4) registry comparison result, (5) P1/P2/P3 plus
decision path, and (6) state. Severity is a claim, not a trusted priority signal.

Save the auditable candidate inventory and terminal dispositions as
`results/INTERMEDIATE.yaml`, then validate the closed ledger:

```text
docloop review-gate validate-intermediate . results/INTERMEDIATE.yaml --closed
```

Save synthesis as `results/SYNTHESIS.md`, then run:

```text
docloop review-gate audit-anchors results/SYNTHESIS.md \\
  --lens results/L1.md results/L3.md --l2 results/L2.md \\
  [--scan deterministic/TERM_SCAN.md] [--extra-re '<document-id-regex>'] \
  --ledger results/INTERMEDIATE.yaml --packet-root .
```

ANCHOR-FAIL or PROMO-FAIL means incomplete synthesis. Repair at most twice, preserving
each attempt; after two failures, hand the run to a human instead of optimizing for the
checker.
""".encode("utf-8")


def _verification_guide() -> bytes:
    return """# Independent verification handoff — manual step

Exclude the writing/synthesis context. Give each verifier the frozen target and one
finding, with the mandate: try to kill this finding by locating contrary evidence.

- Result values are `pass | kill | unresolved`, separate from finding state.
- P1 findings require three fresh reviewers; other findings require one.
- Three-reviewer aggregation is unanimity only: all pass -> pass, all kill -> rejected,
  any mixture -> blocking unresolved. No majority vote.
- Individual kill is a normal successful refutation and transitions to `rejected`.
- Before declaring done, three fresh reviewers try to kill the whole review conclusion.
  Any kill/unresolved returns to synthesis as a new or reopened finding.
- After an accepted fix is applied, delta verification asks whether the candidate
  snapshot actually resolves it: pass -> verified; kill -> applied/rework; unresolved
  blocks.

Separate model invocations are a protocol requirement. docloop does not prove that the
contexts or agents are independent.
""".encode("utf-8")


def _human_guide(unassured: bool) -> bytes:
    assurance = "Because this packet is unassured, record explicit acceptance of missing decision history before any done claim." if unassured else "Record every suppression with the validated decision ID."
    return f"""# Human decision record — append-only

{assurance}

Do not edit this immutable guidance file. Create `results/HUMAN_DECISION.md`; append
the evidence, verification result, and state transition for each finding. The accepted
path is `discovered -> accepted -> planned -> applied -> verified`; the refuted path is
`discovered -> rejected`. `rejected` and `verified` are terminal. Human resolution of an unresolved item must end in accepted or
rejected; do not invent a terminal enum.

The review is not done unless every candidate atom has exactly one terminal disposition,
every finding is verified or rejected, no verification is missing/unresolved, the
three-reviewer done-preflight is pass, and the unassured-mode acceptance (if applicable)
is explicit. Record the final v2 receipt as `results/DONE.md`, bound to this packet's
run ID, target, snapshot, prepared-payload digest, and ledger. Then run
`docloop review-gate validate-result . results/DONE.md`. This file records a human
judgment; it does not turn model output into ground truth.
""".encode("utf-8")


def _normalize_term_scan_anchors(raw: bytes) -> bytes:
    """Use the audit tool's two-digit-minimum anchor namespace inside packets.

    The vendored scanner intentionally prints `HIT line 1`, while the vendored
    anchor audit intentionally ignores one-digit L anchors to avoid confusing L1/L2/L3
    lens names.  Preserve the scanner's exact bytes separately and adapt only the
    packet copy consumed by synthesis/audit.
    """
    text = raw.decode("utf-8")
    return re.sub(
        r"(?m)^HIT line (\d{1,5}):",
        lambda m: f"HIT line {int(m.group(1)):02d}:",
        text,
    ).encode("utf-8")


def _inventory(run_path: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    root_fd = _open_directory(run_path, "prepared payload")
    try:
        for dirpath, dirnames, filenames in os.walk(run_path, followlinks=False):
            dp = Path(dirpath)
            if dp == run_path:
                dirnames[:] = [d for d in dirnames if d != "results"]
            for name in dirnames:
                child = dp / name
                st = child.lstat()
                if stat.S_ISLNK(st.st_mode) or not stat.S_ISDIR(st.st_mode):
                    raise GateError(f"prepared payload contains unsafe directory: {child}")
            for name in filenames:
                if dp == run_path and name in {"INCOMPLETE.json", "COMPLETE.json"}:
                    continue
                p = dp / name
                rel = PurePosixPath(p.relative_to(run_path).as_posix())
                data = _read_relative(root_fd, rel, f"prepared payload {rel.as_posix()}", MAX_CAPTURE_BYTES)
                rows.append({"path": rel.as_posix(), "bytes": len(data), "sha256": _sha(data)})
    finally:
        os.close(root_fd)
    return sorted(rows, key=lambda row: row["path"].encode("utf-8"))


def _aggregate(rows: list[dict[str, Any]]) -> str:
    h = hashlib.sha256()
    for row in rows:
        h.update(row["path"].encode("utf-8") + b"\0")
        h.update(str(row["bytes"]).encode("ascii") + b"\0")
        h.update(row["sha256"].encode("ascii") + b"\n")
    return h.hexdigest()


def _json_load_unique(raw: bytes, label: str) -> Any:
    def pairs(items: list[tuple[str, Any]]) -> dict[str, Any]:
        out: dict[str, Any] = {}
        for key, value in items:
            if key in out:
                raise GateError(f"{label}: duplicate JSON key {key!r}")
            out[key] = value
        return out
    try:
        return json.loads(raw.decode("utf-8"), object_pairs_hook=pairs)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise GateError(f"{label}: malformed JSON") from exc


def _validate_results_tree(run_path: Path) -> None:
    results = run_path / "results"
    try:
        root_st = results.lstat()
    except FileNotFoundError as exc:
        raise GateError("prepared packet: results directory missing") from exc
    if stat.S_ISLNK(root_st.st_mode) or not stat.S_ISDIR(root_st.st_mode):
        raise GateError("prepared packet: results must be a real directory")
    for dirpath, dirnames, filenames in os.walk(results, followlinks=False):
        dp = Path(dirpath)
        for name in dirnames:
            st = (dp / name).lstat()
            if stat.S_ISLNK(st.st_mode) or not stat.S_ISDIR(st.st_mode):
                raise GateError(f"prepared packet: unsafe mutable result directory: {dp / name}")
        for name in filenames:
            st = (dp / name).lstat()
            if stat.S_ISLNK(st.st_mode) or not stat.S_ISREG(st.st_mode):
                raise GateError(f"prepared packet: unsafe mutable result file: {dp / name}")


def _validate_prepared_packet(run_folder: str | Path) -> tuple[Path, dict[str, Any], dict[str, Any]]:
    """Validate immutable packet state without printing or inspecting a receipt."""
    run_path = _directory_argument(run_folder, "run folder")
    run_fd = _open_directory(run_path, "run folder")
    try:
        try:
            _read_relative(run_fd, PurePosixPath("INCOMPLETE.json"), "incomplete marker", MAX_SIDECAR_BYTES)
        except MissingInput:
            pass
        else:
            raise GateError("packet is incomplete: INCOMPLETE.json is present")
        complete_raw = _read_relative(run_fd, PurePosixPath("COMPLETE.json"), "complete marker", MAX_SIDECAR_BYTES)
        complete = _json_load_unique(complete_raw, "complete marker")
        if not isinstance(complete, dict):
            raise GateError("complete marker: top level must be an object")
        if type(complete.get("schema_version")) is not int or complete["schema_version"] != 1:
            raise GateError("complete marker: schema_version must be integer 1")
        if complete.get("state") != "prepared":
            raise GateError("complete marker: state must be prepared")
        if complete.get("run_id") != run_path.name or not RUN_ID_RE.fullmatch(run_path.name):
            raise GateError("complete marker: run_id does not match the run directory")
        if complete.get("excluded_mutable_prefixes") != ["results/"]:
            raise GateError("complete marker: mutable-prefix contract mismatch")
        recorded = complete.get("payload_files")
        if not isinstance(recorded, list) or any(not isinstance(row, dict) for row in recorded):
            raise GateError("complete marker: payload_files must be a list of objects")
        current = _inventory(run_path)
        if recorded != current:
            raise GateError("prepared packet: payload inventory mismatch")
        if complete.get("payload_digest_sha256") != _aggregate(current):
            raise GateError("prepared packet: aggregate digest mismatch")
        run_raw = _read_relative(run_fd, PurePosixPath("RUN.yaml"), "run manifest", MAX_SIDECAR_BYTES)
        run = _load_strict(run_raw, "run manifest")
        if not isinstance(run, dict) or run.get("state") != "prepared" or run.get("run_id") != run_path.name:
            raise GateError("run manifest: prepared state/run_id mismatch")
        _validate_results_tree(run_path)
    finally:
        os.close(run_fd)
    return run_path, run, complete


def _check_prepared(run_folder: str) -> int:
    run_path, _run, _complete = _validate_prepared_packet(run_folder)
    print(f"prepared packet verified: {run_path}")
    return 0


def _fsync_tree(path: Path) -> None:
    dirs: list[Path] = []
    for dirpath, _dirnames, filenames in os.walk(path):
        dp = Path(dirpath)
        dirs.append(dp)
        for name in filenames:
            fd = os.open(dp / name, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
            try:
                os.fsync(fd)
            finally:
                os.close(fd)
    for dp in reversed(dirs):
        fd = os.open(dp, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0) | getattr(os, "O_NOFOLLOW", 0))
        try:
            os.fsync(fd)
        finally:
            os.close(fd)


def _run_tool(script: Path, args: list[str], label: str) -> bytes:
    proc = subprocess.run([sys.executable, str(script), *args], capture_output=True)
    output = proc.stdout + proc.stderr
    if len(output) > 1024 * 1024:
        raise GateError(f"{label}: tool output cap exceeded")
    if proc.returncode != 0:
        excerpt = output.decode("utf-8", "replace")[-800:]
        raise GateError(f"{label}: failed closed (exit {proc.returncode})\n{excerpt}")
    return output


def _validate_convention_pair(
    profile_raw: bytes,
    intake_raw: bytes,
    *,
    target_rel: PurePosixPath,
    target_sha: str,
) -> dict[str, Any]:
    """Validate captured convention bytes before reserving a packet directory."""
    try:
        import validate_convention_intake
    except ImportError as exc:
        raise GateError("convention validators are unavailable") from exc

    profile = _load_strict(profile_raw, "convention profile")
    intake = _load_strict(intake_raw, "convention intake")
    errors = validate_convention_intake.validate_data(intake, profile)
    expected_snapshot = f"sha256:{target_sha}"
    if not isinstance(intake, dict):
        errors.append("convention intake: top level must be a mapping")
    else:
        if intake.get("target_snapshot") != expected_snapshot:
            errors.append("convention intake target_snapshot must match the frozen target")
        if (
            "target_document" in intake
            and intake.get("target_document") != target_rel.as_posix()
        ):
            errors.append("convention intake target_document must match the selected target source")
    if errors:
        raise GateError("convention preflight failed:\n" + "\n".join(f"- {error}" for error in errors))
    return {
        "schema_version": 1,
        "phase": "pre_lens",
        "profile_sha256": _sha(profile_raw),
        "intake_sha256": _sha(intake_raw),
        "target_snapshot": expected_snapshot,
        "target_document": target_rel.as_posix(),
        "validation_result": "pass",
    }


def _prepare(args: argparse.Namespace) -> int:
    if not RUN_ID_RE.fullmatch(args.run_id):
        raise GateError("run-id must match [a-z0-9][a-z0-9-]{0,63}")
    if bool(args.decisions) == bool(args.unassured):
        raise GateError("choose exactly one of --decisions or --unassured")
    if bool(args.terms) == bool(args.no_terms):
        raise GateError("choose exactly one of --terms or --no-terms")
    if bool(args.docmodel) == bool(args.no_docmodel):
        raise GateError("choose exactly one of --docmodel or --no-docmodel")
    if bool(args.convention_profile) != bool(args.convention_intake):
        raise GateError("--convention-profile and --convention-intake must be supplied together")

    target_rel = _normalize_rel(args.target, "target")
    decisions_rel = _normalize_rel(args.decisions, "decisions") if args.decisions else None
    axes_rel = _normalize_rel(args.axes, "axes") if args.axes else None
    terms_rel = _normalize_rel(args.terms, "terms") if args.terms else None
    docmodel_rel = _normalize_rel(args.docmodel, "docmodel") if args.docmodel else None
    convention_profile_rel = _normalize_rel(args.convention_profile, "convention profile") if args.convention_profile else None
    convention_intake_rel = _normalize_rel(args.convention_intake, "convention intake") if args.convention_intake else None
    review_path = _directory_argument(args.review_folder, "review folder")
    review_fd = _open_directory(review_path, "review folder")
    try:
        for no_flag, selected, conventional, label in (
            (args.no_terms, terms_rel, target_rel.parent / "terms.yaml", "terms"),
            (args.no_docmodel, docmodel_rel, target_rel.parent / "docmodel.yaml", "docmodel"),
        ):
            if no_flag and selected is None:
                try:
                    _read_relative(review_fd, conventional, f"adjacent {label}", MAX_SIDECAR_BYTES)
                except MissingInput:
                    pass
                else:
                    raise GateError(f"--no-{label} conflicts with adjacent {conventional.as_posix()}; select it explicitly")

        target_raw = _read_relative(review_fd, target_rel, "target", MAX_TARGET_BYTES)
        try:
            target_text = target_raw.decode("utf-8")
        except UnicodeDecodeError as exc:
            raise GateError("target: not valid UTF-8") from exc
        line_count = len(target_text.splitlines())
        if line_count > MAX_TARGET_LINES:
            raise GateError(f"target: line cap exceeded ({line_count} > {MAX_TARGET_LINES})")
        target_sha = _sha(target_raw)
        target_numbered = _numbered_target(target_text, target_rel.as_posix(), target_sha)

        convention_profile_raw = None
        convention_intake_raw = None
        convention_preflight = None
        if convention_profile_rel is not None and convention_intake_rel is not None:
            convention_profile_raw = _read_relative(
                review_fd, convention_profile_rel, "convention profile", MAX_SIDECAR_BYTES
            )
            convention_intake_raw = _read_relative(
                review_fd, convention_intake_rel, "convention intake", MAX_SIDECAR_BYTES
            )
            convention_preflight = _validate_convention_pair(
                convention_profile_raw,
                convention_intake_raw,
                target_rel=target_rel,
                target_sha=target_sha,
            )

        if axes_rel:
            axes_raw = _read_relative(review_fd, axes_rel, "axes", MAX_SIDECAR_BYTES)
            try:
                axes_raw.decode("utf-8")
            except UnicodeDecodeError as exc:
                raise GateError("axes: not valid UTF-8") from exc
            axes_source = axes_rel.as_posix()
        else:
            axes_raw = DEFAULT_AXES.read_bytes()
            axes_source = "docloop:templates/review-gate/default-axes.md"

        provenance: dict[str, bytes] = {}
        provenance_rows: list[dict[str, Any]] = []
        decisions_raw = None
        if decisions_rel:
            decisions_raw, rows, files = _freeze_decisions(review_fd, decisions_rel)
            provenance_rows.extend(rows); provenance.update(files)
        terms_raw = None
        if terms_rel:
            terms_raw, rows, files = _freeze_terms(review_fd, terms_rel)
            provenance_rows.extend(rows); provenance.update(files)
        docmodel_raw = None
        if docmodel_rel:
            docmodel_raw, rows, files = _freeze_docmodel(review_fd, docmodel_rel)
            provenance_rows.extend(rows); provenance.update(files)

        if len(provenance) > MAX_PROVENANCE_FILES:
            raise GateError(f"provenance file cap exceeded ({len(provenance)} > {MAX_PROVENANCE_FILES})")
        captured_total = sum(len(x) for x in (
            target_raw,
            axes_raw,
            decisions_raw or b"",
            terms_raw or b"",
            docmodel_raw or b"",
            convention_profile_raw or b"",
            convention_intake_raw or b"",
        )) + sum(map(len, provenance.values()))
        if captured_total > MAX_CAPTURE_BYTES:
            raise GateError(f"capture byte cap exceeded ({captured_total} > {MAX_CAPTURE_BYTES})")

        gate_fd = _mkdir_open(review_fd, "review-gate", False)
        try:
            run_fd = _mkdir_open(gate_fd, args.run_id, True)
        finally:
            os.close(gate_fd)
        run_path = review_path / "review-gate" / args.run_id
        token = uuid.uuid4().hex
        try:
            _write_relative(run_fd, PurePosixPath("INCOMPLETE.json"), _json_bytes({"schema_version": 1, "run_id": args.run_id, "ownership_token": token, "reserved_at": _utc_now()}))
            _phase("reserve")

            _write_relative(run_fd, PurePosixPath("frozen/target.txt"), target_raw)
            _write_relative(run_fd, PurePosixPath("frozen/target.numbered.md"), target_numbered)
            _write_relative(run_fd, PurePosixPath("frozen/axes.md"), axes_raw)
            if decisions_raw is not None:
                _write_relative(run_fd, PurePosixPath("frozen/decisions.yaml"), decisions_raw)
            if terms_raw is not None:
                _write_relative(run_fd, PurePosixPath("frozen/terms.yaml"), terms_raw)
            if docmodel_raw is not None:
                _write_relative(run_fd, PurePosixPath("frozen/docmodel.yaml"), docmodel_raw)
            if convention_profile_raw is not None and convention_intake_raw is not None:
                _write_relative(run_fd, PurePosixPath("frozen/convention-profile.yaml"), convention_profile_raw)
                _write_relative(run_fd, PurePosixPath("frozen/convention-intake.yaml"), convention_intake_raw)
            for name, raw in provenance.items():
                _write_relative(run_fd, PurePosixPath(f"frozen/provenance/{name}"), raw)
            _phase("freeze")

            decisions_audit = None
            if decisions_raw is not None:
                decisions_audit = _run_tool(TOOL_DIR / "validate_decisions.py", [str(run_path / "frozen" / "decisions.yaml")], "decision registry")
                _write_relative(run_fd, PurePosixPath("deterministic/DECISIONS_VALIDATION.txt"), decisions_audit)
            term_audit = None
            term_audit_raw = None
            if terms_raw is not None:
                term_audit_raw = _run_tool(TOOL_DIR / "scan_terms.py", [str(run_path / "frozen" / "terms.yaml"), str(run_path / "frozen" / "target.txt")], "term scan")
                term_audit = _normalize_term_scan_anchors(term_audit_raw)
                _write_relative(run_fd, PurePosixPath("deterministic/TERM_SCAN_RAW.md"), term_audit_raw)
                _write_relative(run_fd, PurePosixPath("deterministic/TERM_SCAN.md"), term_audit)
            if convention_preflight is not None:
                _write_relative(
                    run_fd,
                    PurePosixPath("deterministic/CONVENTION_PREFLIGHT.json"),
                    _json_bytes(convention_preflight),
                )
            _phase("validate")

            _write_relative(run_fd, PurePosixPath("lens/L1/PROMPT.md"), _prompt_l1(target_sha))
            _write_relative(run_fd, PurePosixPath("lens/L1/TARGET.md"), target_numbered)
            _write_relative(run_fd, PurePosixPath("lens/L2/PROMPT.md"), _prompt_l2(target_sha, decisions_raw is not None))
            _write_relative(run_fd, PurePosixPath("lens/L2/TARGET.md"), target_numbered)
            if decisions_raw is not None:
                _write_relative(run_fd, PurePosixPath("lens/L2/DECISIONS.yaml"), decisions_raw)
            else:
                _write_relative(run_fd, PurePosixPath("lens/L2/UNASSURED.md"), b"# Unassured decision history\n\nNo validated decision registry was supplied. Suppression as already decided is forbidden. Human acceptance is required before done.\n")
            _write_relative(run_fd, PurePosixPath("lens/L3/PROMPT.md"), _prompt_l3(target_sha, docmodel_raw is not None))
            _write_relative(run_fd, PurePosixPath("lens/L3/TARGET.md"), target_numbered)
            _write_relative(run_fd, PurePosixPath("lens/L3/AXES.md"), axes_raw)
            if docmodel_raw is not None:
                _write_relative(run_fd, PurePosixPath("lens/L3/DOCMODEL.yaml"), docmodel_raw)
            _write_relative(run_fd, PurePosixPath("handoff/SYNTHESIS.md"), _synthesis_guide(terms_raw is not None, args.unassured))
            _write_relative(run_fd, PurePosixPath("handoff/ANCHOR_AUDIT.md"), b"# Anchor audit\n\nRun the command in SYNTHESIS.md. Preserve the complete stdout audit header. ANCHOR-FAIL or PROMO-FAIL blocks delivery; this check detects anchor loss, not truth or missed defects.\n")
            _write_relative(run_fd, PurePosixPath("handoff/VERIFICATION.md"), _verification_guide())
            _write_relative(run_fd, PurePosixPath("handoff/HUMAN_DECISION.md"), _human_guide(args.unassured))
            _write_relative(run_fd, PurePosixPath("results/README.md"), b"# Append-only result slots\n\nWrite fresh-context outputs as L1.md, L2.md, L3.md, INTERMEDIATE.yaml, SYNTHESIS.md, ANCHOR_AUDIT.txt, VERIFICATION-*.md, HUMAN_DECISION.md, and the v2 final receipt DONE.md. Never overwrite an earlier attempt; add a numeric suffix. Results are excluded from the prepared-input digest. A prepared packet is not reviewed, passed, or done; validate DONE.md separately with `docloop review-gate validate-result`.\n")
            _phase("prompts")

            sidecars = {
                "decisions": None if decisions_rel is None else {"source": decisions_rel.as_posix(), "frozen_sha256": _sha(decisions_raw or b"")},
                "axes": {"source": axes_source, "frozen_sha256": _sha(axes_raw)},
                "terms": None if terms_rel is None else {"source": terms_rel.as_posix(), "frozen_sha256": _sha(terms_raw or b"")},
                "docmodel": None if docmodel_rel is None else {"source": docmodel_rel.as_posix(), "frozen_sha256": _sha(docmodel_raw or b""), "validation": "limited packet preflight; human-approved semantics"},
                "convention": None if convention_profile_rel is None else {
                    "profile_source": convention_profile_rel.as_posix(),
                    "profile_frozen_sha256": _sha(convention_profile_raw or b""),
                    "intake_source": convention_intake_rel.as_posix() if convention_intake_rel is not None else None,
                    "intake_frozen_sha256": _sha(convention_intake_raw or b""),
                    "phase": "pre_lens",
                    "validation": "pass",
                },
            }
            run_manifest = {
                "schema_version": SCHEMA_VERSION,
                "state": "prepared",
                "run_id": args.run_id,
                "prepared_at": _utc_now(),
                "tool_version": TOOL_VERSION,
                "upstream": {"repository": UPSTREAM_REPOSITORY, "commit": UPSTREAM_COMMIT, "contract_version": UPSTREAM_CONTRACT_VERSION},
                "mode": "unassured" if args.unassured else "assured",
                "target": {"source": target_rel.as_posix(), "bytes": len(target_raw), "lines": line_count, "sha256": target_sha},
                "sidecars": sidecars,
                "provenance": provenance_rows,
                "term_scan_sha256": None if term_audit is None else _sha(term_audit),
                "term_scan_raw_sha256": None if term_audit_raw is None else _sha(term_audit_raw),
                "visibility": {"L1": ["target"], "L2": ["target", "decisions" if decisions_raw is not None else "unassured-notice"], "L3": ["target", "axes"] + (["docmodel"] if docmodel_raw is not None else [])},
                "non_guarantees": {"filesystem_isolation": False, "independent_agents": False, "complete_detection": False, "severity_reliable": False, "justified_repeat_count": False, "review_passed": False},
                "limits": {"single_utf8_target": True, "max_target_bytes": MAX_TARGET_BYTES, "max_capture_bytes": MAX_CAPTURE_BYTES, "max_target_lines": MAX_TARGET_LINES},
            }
            _write_relative(run_fd, PurePosixPath("RUN.yaml"), _yaml_bytes(run_manifest))
            _fsync_tree(run_path)
            rows = _inventory(run_path)
            complete = {"schema_version": 1, "state": "prepared", "run_id": args.run_id, "ownership_token": token, "prepared_at": _utc_now(), "payload_digest_sha256": _aggregate(rows), "payload_files": rows, "excluded_mutable_prefixes": ["results/"]}
            _write_relative(run_fd, PurePosixPath("COMPLETE.json"), _json_bytes(complete))
            os.fsync(run_fd)
            _phase("complete-write")
            marker_raw = _read_relative(run_fd, PurePosixPath("INCOMPLETE.json"), "ownership marker", MAX_SIDECAR_BYTES)
            marker = _json_load_unique(marker_raw, "ownership marker")
            if not isinstance(marker, dict) or marker.get("ownership_token") != token:
                raise GateError("ownership marker changed; refusing to finalize")
            try:
                os.unlink("INCOMPLETE.json", dir_fd=run_fd)
            except OSError as exc:
                raise GateError("could not remove the ownership marker; packet remains incomplete") from exc
            # COMPLETE and its directory entry were already fsynced.  Not fsyncing
            # again after unlink intentionally prefers a crash-time false negative
            # (INCOMPLETE may reappear) over a false prepared packet.
        finally:
            os.close(run_fd)
    finally:
        os.close(review_fd)

    print(f"review-gate packet prepared: {run_path}")
    print("state: prepared (packet only; not reviewed, passed, or done)")
    print("next: run lens prompts in fresh contexts, synthesize, audit anchors, independently verify, then record the human decision")
    return 0


def _prepare_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(prog="docloop review-gate prepare", description="prepare a fixed-input review-gate packet")
    ap.add_argument("review_folder")
    ap.add_argument("run_id")
    ap.add_argument("target", help="one UTF-8 file, relative to review-folder")
    g = ap.add_mutually_exclusive_group(required=True)
    g.add_argument("--decisions")
    g.add_argument("--unassured", action="store_true")
    ap.add_argument("--axes")
    g = ap.add_mutually_exclusive_group(required=True)
    g.add_argument("--terms")
    g.add_argument("--no-terms", action="store_true")
    g = ap.add_mutually_exclusive_group(required=True)
    g.add_argument("--docmodel")
    g.add_argument("--no-docmodel", action="store_true")
    ap.add_argument("--convention-profile")
    ap.add_argument("--convention-intake")
    return ap


def usage() -> str:
    return """docloop review-gate — explicit packet preparation and deterministic tools

  docloop review-gate prepare <review-folder> <run-id> <target-relative-path>
      (--decisions FILE | --unassured) [--axes FILE]
      (--terms FILE | --no-terms) (--docmodel FILE | --no-docmodel)
      [--convention-profile FILE --convention-intake FILE]
  docloop review-gate check <review-gate-run-folder>
  docloop review-gate validate-decisions <decisions.yaml> [--skip-hash]
  docloop review-gate validate-intermediate <run-folder> <ledger-relative-path> [--closed]
  docloop review-gate validate-result <run-folder> <receipt-relative-path>
  docloop review-gate validate-convention-profile <profile.yaml>
  docloop review-gate validate-convention-intake <intake.yaml> --profile <profile.yaml>
  docloop review-gate materialize-docmodel <intake.yaml> --profile <profile.yaml> [--output FILE]
  docloop review-gate scan-terms <terms.yaml> <target>
  docloop review-gate audit-anchors <synthesis> [upstream-compatible options]

prepare creates a packet only. It does not run models or declare pass/done.
"""


def main(argv: list[str] | None = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    if not args or args[0] in {"-h", "--help", "help"}:
        print(usage(), end="")
        return 0
    command, rest = args[0], args[1:]
    if command == "prepare":
        return _prepare(_prepare_parser().parse_args(rest))
    if command == "check":
        if len(rest) != 1:
            raise GateError("usage: docloop review-gate check <review-gate-run-folder>")
        return _check_prepared(rest[0])
    scripts = {
        "validate-decisions": TOOL_DIR / "validate_decisions.py",
        "validate-intermediate": TOOL_DIR / "validate_review_intermediate.py",
        "validate-result": TOOL_DIR / "validate_review_result.py",
        "validate-convention-profile": TOOL_DIR / "validate_convention_profile.py",
        "validate-convention-intake": TOOL_DIR / "validate_convention_intake.py",
        "materialize-docmodel": TOOL_DIR / "materialize_docmodel.py",
        "scan-terms": TOOL_DIR / "scan_terms.py",
        "audit-anchors": TOOL_DIR / "audit_anchors.py",
    }
    if command in scripts:
        os.execv(sys.executable, [sys.executable, str(scripts[command]), *rest])
    raise GateError(f"unknown review-gate command {command!r}\n\n{usage()}")


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except GateError as exc:
        print(f"docloop review-gate: {exc}", file=sys.stderr)
        raise SystemExit(1)
