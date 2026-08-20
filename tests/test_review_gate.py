#!/usr/bin/env python3
"""Focused tests for the opt-in review-gate packet port."""

import hashlib
import json
import os
import shutil
import subprocess
import sys
import tempfile
import threading
from pathlib import Path, PurePosixPath

import yaml


ROOT = Path(__file__).resolve().parents[1]
BIN = ROOT / "bin" / "docloop"
RUNNER = ROOT / "lib" / "review_gate" / "runner.py"
sys.path.insert(0, str(ROOT / "lib" / "review_gate"))
import runner as RG  # noqa: E402

PASSED = FAILED = 0


def check(name, ok, detail=""):
    global PASSED, FAILED
    if ok:
        PASSED += 1
        print(f"ok   {name}")
    else:
        FAILED += 1
        print(f"FAIL {name} {detail}")


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def write(path, data):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    if isinstance(data, str):
        data = data.encode("utf-8")
    path.write_bytes(data)
    return path


def command(*args, env=None):
    args = list(map(str, args))
    if args and args[0] == "prepare" and "--editing-state" not in args:
        args += ["--editing-state", "frozen", "--target-maturity", "complete"]
    return subprocess.run([str(BIN), "review-gate", *args], capture_output=True, text=True, env=env)


def fixture(root):
    root = Path(root)
    target = write(root / "draft.md", "# Demo\n\nState is open.\nState is closed.\n리시버를 선택한다.\n")
    decision_source = write(root / "decision-source.md", "D-01: open and closed wording stays\n")
    write(root / "decisions.yaml", f"""meta:
  target: demo
  source_ref: decision-source.md
  source_version_hash: {sha(decision_source)}
  updated_at: '2026-08-03'
decisions:
  - id: D-01
    decision: open and closed wording stays
    status: 재론금지
    date: '2026-08-03'
    evidence: D-01
""")
    glossary = write(root / "glossary.md", "Receiver is canonical.\n")
    write(root / "terms.yaml", f"""meta:
  target: demo
  updated_at: '2026-08-03'
  source_ref: glossary.md
  source_hash: {sha(glossary)}
terms:
  - canonical: Receiver
    forbidden: [리시버]
""")
    model_source = write(root / "docmodel-source.md", "approved template structure\n")
    write(root / "docmodel.yaml", f"""meta:
  template: demo
  updated_at: '2026-08-03'
  approved_by: human-owner
  source_ref: docmodel-source.md
  source_hash: {sha(model_source)}
sections:
  - id: '1'
    title: Demo
    role: canonical
""")
    return target


def assured_args(root, run_id):
    return (
        "prepare", root, run_id, "draft.md",
        "--decisions", "decisions.yaml",
        "--terms", "terms.yaml",
        "--docmodel", "docmodel.yaml",
    )


def packet(root, run_id):
    return Path(root) / "review-gate" / run_id


def aggregate(rows):
    h = hashlib.sha256()
    for row in rows:
        h.update(row["path"].encode() + b"\0")
        h.update(str(row["bytes"]).encode() + b"\0")
        h.update(row["sha256"].encode() + b"\n")
    return h.hexdigest()


with tempfile.TemporaryDirectory() as td:
    review = Path(td) / "review"
    review.mkdir()
    fixture(review)

    # Full assured preparation.
    r = command(*assured_args(review, "assured-1"))
    run = packet(review, "assured-1")
    check("prepare/assured succeeds and says packet-only", r.returncode == 0 and "not reviewed, passed, or done" in r.stdout, r.stderr)
    check("prepare/commit markers are mutually exclusive", (run / "COMPLETE.json").is_file() and not (run / "INCOMPLETE.json").exists())
    manifest = yaml.safe_load((run / "RUN.yaml").read_text())
    check("prepare/RUN records exact upstream and assured mode",
          manifest["mode"] == "assured" and manifest["upstream"]["commit"] == RG.UPSTREAM_COMMIT
          and manifest["state"] == "prepared")
    check("prepare/lens visibility manifest is exact",
          manifest["visibility"] == {"L1": ["target"], "L2": ["target", "decisions"], "L3": ["target", "axes", "docmodel"]})
    check("prepare/lens directories contain only declared envelope files",
          sorted(p.name for p in (run / "lens" / "L1").iterdir()) == ["PROMPT.md", "TARGET.md"]
          and sorted(p.name for p in (run / "lens" / "L2").iterdir()) == ["DECISIONS.yaml", "PROMPT.md", "TARGET.md"]
          and sorted(p.name for p in (run / "lens" / "L3").iterdir()) == ["AXES.md", "DOCMODEL.yaml", "PROMPT.md", "TARGET.md"])
    check("prepare/numbered target carries stable anchors and hash",
          "L03 | State is open." in (run / "frozen" / "target.numbered.md").read_text()
          and manifest["target"]["sha256"] == sha(review / "draft.md"))
    frozen_decisions = yaml.safe_load((run / "frozen" / "decisions.yaml").read_text())
    frozen_terms = yaml.safe_load((run / "frozen" / "terms.yaml").read_text())
    frozen_docmodel = yaml.safe_load((run / "frozen" / "docmodel.yaml").read_text())
    check("prepare/all provenance refs rewritten inside frozen tree",
          frozen_decisions["meta"]["source_ref"].startswith("provenance/")
          and frozen_terms["meta"]["source_ref"].startswith("provenance/")
          and frozen_docmodel["meta"]["source_ref"].startswith("provenance/")
          and len(manifest["provenance"]) == 3)
    scan = (run / "deterministic" / "TERM_SCAN.md").read_text()
    raw_scan = (run / "deterministic" / "TERM_SCAN_RAW.md").read_text()
    check("prepare/term scan preserves raw audit and adapts one-digit anchors",
          "DICT:" in scan and "TARGET:" in scan and "HIT line 05" in scan
          and "HIT line 5" in raw_scan)
    check("prepare/decision validation preserved", "억제 적격" in (run / "deterministic" / "DECISIONS_VALIDATION.txt").read_text())
    check("prepare/non-guarantees are explicit false claims",
          all(value is False for value in manifest["non_guarantees"].values()))

    # Prepared-state checker, mutable results exclusion, and immutable tamper detection.
    r = command("check", run)
    check("check/valid prepared packet passes", r.returncode == 0 and "verified" in r.stdout, r.stderr)
    complete = json.loads((run / "COMPLETE.json").read_text())
    check("check/marker digest matches recorded immutable inventory",
          complete["payload_digest_sha256"] == aggregate(complete["payload_files"])
          and all(not row["path"].startswith("results/") for row in complete["payload_files"]))
    write(run / "results" / "L1.md", "candidate L00003\n")
    check("check/append-only result does not invalidate prepared inputs", command("check", run).returncode == 0)
    original_prompt = (run / "lens" / "L1" / "PROMPT.md").read_bytes()
    write(run / "lens" / "L1" / "PROMPT.md", original_prompt + b"tamper\n")
    check("check/immutable prompt tamper fails", command("check", run).returncode != 0)
    write(run / "lens" / "L1" / "PROMPT.md", original_prompt)
    check("check/restored immutable prompt passes", command("check", run).returncode == 0)
    os.symlink(Path(td), run / "frozen" / "results")
    check("check/nested results-named symlink is not excluded", command("check", run).returncode != 0)
    (run / "frozen" / "results").unlink()
    write(run / "INCOMPLETE.json", "{}\n")
    check("check/both state markers are non-consumable", command("check", run).returncode != 0)
    (run / "INCOMPLETE.json").unlink()

    # Same-ID no-clobber and concurrency.
    before = (run / "COMPLETE.json").read_bytes()
    r = command(*assured_args(review, "assured-1"))
    check("prepare/same run ID never overwrites", r.returncode != 0 and (run / "COMPLETE.json").read_bytes() == before)
    concurrent_args = assured_args(review, "race-1")
    barrier = threading.Barrier(3)
    results = []
    def launch():
        barrier.wait(); results.append(command(*concurrent_args).returncode)
    threads = [threading.Thread(target=launch) for _ in range(2)]
    for t in threads: t.start()
    barrier.wait()
    for t in threads: t.join()
    check("prepare/same-ID concurrency admits exactly one winner", sorted(results) == [0, 1], str(results))
    check("prepare/concurrency winner is a valid packet", command("check", packet(review, "race-1")).returncode == 0)

    # Explicit unassured mode with no optional sidecars.
    un = Path(td) / "unassured"; un.mkdir(); write(un / "draft.md", "# Only target\n")
    r = command("prepare", un, "un-1", "draft.md", "--unassured", "--no-terms", "--no-docmodel")
    unrun = packet(un, "un-1")
    unmanifest = yaml.safe_load((unrun / "RUN.yaml").read_text()) if r.returncode == 0 else {}
    check("prepare/explicit unassured succeeds and forbids suppression",
          r.returncode == 0 and unmanifest.get("mode") == "unassured"
          and "Suppression" in (unrun / "lens" / "L2" / "UNASSURED.md").read_text())
    check("prepare/unassured human acceptance is explicit",
          "explicit acceptance" in (unrun / "handoff" / "HUMAN_DECISION.md").read_text())
    r = command("prepare", un, "un-missing", "draft.md", "--no-terms", "--no-docmodel")
    check("prepare/missing decisions choice fails before reservation", r.returncode != 0 and not packet(un, "un-missing").exists())

    # Omission, path, type, encoding, size, and schema failures occur before reservation.
    r = command("prepare", review, "omit-terms", "draft.md", "--decisions", "decisions.yaml", "--no-terms", "--docmodel", "docmodel.yaml")
    check("prepare/adjacent terms cannot be silently omitted", r.returncode != 0 and not packet(review, "omit-terms").exists())
    r = command("prepare", review, "omit-model", "draft.md", "--decisions", "decisions.yaml", "--terms", "terms.yaml", "--no-docmodel")
    check("prepare/adjacent docmodel cannot be silently omitted", r.returncode != 0 and not packet(review, "omit-model").exists())
    r = command("prepare", un, "escape", "../outside.md", "--unassured", "--no-terms", "--no-docmodel")
    check("prepare/parent traversal target rejected", r.returncode != 0 and not packet(un, "escape").exists())
    r = command("prepare", un, "overlap", "review-gate/x", "--unassured", "--no-terms", "--no-docmodel")
    check("prepare/output-tree overlap rejected", r.returncode != 0 and not packet(un, "overlap").exists())
    outside = write(Path(td) / "outside.md", "outside\n")
    os.symlink(outside, un / "link.md")
    r = command("prepare", un, "symlink", "link.md", "--unassured", "--no-terms", "--no-docmodel")
    check("prepare/symlink target rejected", r.returncode != 0 and not packet(un, "symlink").exists())
    write(un / "binary.md", b"\xff\xfe")
    r = command("prepare", un, "binary", "binary.md", "--unassured", "--no-terms", "--no-docmodel")
    check("prepare/non-UTF8 target rejected", r.returncode != 0 and not packet(un, "binary").exists())
    with open(un / "huge.md", "wb") as fh:
        fh.truncate(RG.MAX_TARGET_BYTES + 1)
    r = command("prepare", un, "huge", "huge.md", "--unassured", "--no-terms", "--no-docmodel")
    check("prepare/target byte cap fails closed", r.returncode != 0 and not packet(un, "huge").exists())

    dup = Path(td) / "duplicate"; shutil.copytree(review, dup)
    write(dup / "decisions.yaml", "meta:\n  target: one\n  target: two\ndecisions: []\n")
    r = command(*assured_args(dup, "duplicate"))
    check("prepare/duplicate-key decisions rejected", r.returncode != 0 and not packet(dup, "duplicate").exists())
    stale = Path(td) / "stale"; shutil.copytree(review, stale)
    write(stale / "decision-source.md", "changed after registry\n")
    r = command(*assured_args(stale, "stale"))
    check("prepare/stale decisions rejected before reservation", r.returncode != 0 and not packet(stale, "stale").exists())
    badmodel = Path(td) / "badmodel"; shutil.copytree(review, badmodel)
    model = yaml.safe_load((badmodel / "docmodel.yaml").read_text()); model["meta"]["source_hash"] = "0" * 64
    write(badmodel / "docmodel.yaml", yaml.safe_dump(model, allow_unicode=True, sort_keys=False))
    r = command(*assured_args(badmodel, "badmodel"))
    check("prepare/docmodel provenance mismatch rejected", r.returncode != 0 and not packet(badmodel, "badmodel").exists())
    external = Path(td) / "external"; shutil.copytree(review, external)
    decisions = yaml.safe_load((external / "decisions.yaml").read_text())
    decisions["meta"]["source_ref"] = str(outside); decisions["meta"]["source_version_hash"] = sha(outside)
    write(external / "decisions.yaml", yaml.safe_dump(decisions, allow_unicode=True, sort_keys=False))
    r = command(*assured_args(external, "external"))
    check("prepare/external provenance rejected until staged", r.returncode != 0 and not packet(external, "external").exists())

    # Descriptor fingerprint detects mutation for target, sidecar, and provenance reads.
    mutation = Path(td) / "mutation"; mutation.mkdir()
    mfd = RG._open_directory(RG._directory_argument(str(mutation), "mutation fixture"), "mutation fixture")
    try:
        for label in ("target", "decisions", "decisions.meta provenance"):
            p = write(mutation / (label.split()[0] + ".txt"), "before\n")
            def hook(_label, path=p):
                with open(path, "ab") as fh: fh.write(b"changed\n")
            RG._READ_TEST_HOOK = hook
            try:
                RG._read_relative(mfd, PurePosixPath(p.name), label, 1024)
                caught = False
            except RG.GateError as exc:
                caught = "changed while" in str(exc)
            check(f"capture/mutation fails closed for {label}", caught)
    finally:
        RG._READ_TEST_HOOK = None
        os.close(mfd)

    # Every named fault phase leaves a non-consumable reservation; only the final
    # phase may have both markers.
    for phase in sorted(RG.FAIL_PHASES):
        run_id = "fault-" + phase
        env = dict(os.environ, DOCLOOP_REVIEW_GATE_FAIL_AFTER=phase)
        r = command(*assured_args(review, run_id), env=env)
        frun = packet(review, run_id)
        incomplete = (frun / "INCOMPLETE.json").is_file()
        complete_exists = (frun / "COMPLETE.json").exists()
        expected_complete = phase == "complete-write"
        check(f"fault/{phase} retains non-consumable owned run",
              r.returncode != 0 and incomplete and complete_exists == expected_complete and command("check", frun).returncode != 0)

    # Thin deterministic-tool dispatch preserves upstream exit semantics.
    r = command("validate-decisions", review / "decisions.yaml")
    check("tools/validate-decisions pass exit 0", r.returncode == 0 and "억제 적격" in r.stdout)
    r = command("validate-decisions", review / "decisions.yaml", "--skip-hash")
    check("tools/validate-decisions skip-hash exit 3", r.returncode == 3)
    r = command("scan-terms", review / "terms.yaml", review / "draft.md")
    check("tools/scan-terms preserves success/audit output", r.returncode == 0 and "DICT:" in r.stdout and "HIT line 5" in r.stdout)
    anchors = Path(td) / "anchors"; anchors.mkdir()
    write(anchors / "L1.md", "candidate L10 L20\n")
    write(anchors / "S-ok.md", "| G-1 | evidence L10 L20 |\n")
    write(anchors / "S-bad.md", "| G-1 | evidence L10 |\n")
    r = command("audit-anchors", anchors / "S-ok.md", "--lens", anchors / "L1.md")
    check("tools/audit-anchors pass exit 0", r.returncode == 0 and "ANCHOR-OK" in r.stdout)
    r = command("audit-anchors", anchors / "S-bad.md", "--lens", anchors / "L1.md")
    check("tools/audit-anchors missing anchor exit 1", r.returncode == 1 and "ANCHOR-FAIL" in r.stdout)
    write(anchors / "L-first.md", "candidate at L01 and another defect at L03\n")
    write(anchors / "S-first-missing.md", "| G-early | no source anchor |\n")
    r = command("audit-anchors", anchors / "S-first-missing.md", "--lens", anchors / "L-first.md")
    check("anchors/lines 1 and 3 cannot disappear through one-digit blind spot",
          r.returncode == 1 and "L01" in r.stdout and "L03" in r.stdout and "ANCHOR-FAIL" in r.stdout)
    # Unpadded line anchors keep lens citations compatible with scan_terms/audit_anchors.
    ten_lines = "\n".join(["clean"] * 9 + ["리시버"]) + "\n"
    numbered = RG._numbered_target(ten_lines, "ten.md", hashlib.sha256(ten_lines.encode()).hexdigest()).decode()
    write(anchors / "scan10.md", "HIT line 10: '리시버' -> canonical 'Receiver'\n")
    write(anchors / "S-10.md", "| G-2 | evidence L10 |\n")
    r = command("audit-anchors", anchors / "S-10.md", "--scan", anchors / "scan10.md")
    check("anchors/numbered target and term scan agree on L10",
          "L10 | 리시버" in numbered and r.returncode == 0 and "ANCHOR-OK" in r.stdout)

    # front-gate/input-gate wiring (docauth#196/#206/#202④/#228②) -----------------
    fg = Path(td) / "front-gate"
    write(fg / "draft.md", "# Demo\n\nBody.\n")
    r = subprocess.run(
        [str(BIN), "review-gate", "prepare", str(fg), "no-flags", "draft.md",
         "--unassured", "--no-terms", "--no-docmodel"],
        capture_output=True, text=True,
    )
    check(
        "front-gate/editing-state and target-maturity are required",
        r.returncode != 0 and "--editing-state" in (r.stdout + r.stderr),
    )
    r = command(
        "prepare", fg, "draft-no-ledger", "draft.md", "--unassured", "--no-terms", "--no-docmodel",
        "--editing-state", "frozen", "--target-maturity", "draft",
    )
    check(
        "front-gate/draft maturity requires an open-items ledger",
        r.returncode != 0 and "open-items-ledger" in (r.stdout + r.stderr)
        and not (fg / "review-gate" / "draft-no-ledger").exists(),
    )
    r = command(
        "prepare", fg, "no-pair-round", "draft.md", "--unassured", "--no-terms", "--no-docmodel",
        "--editing-state", "frozen", "--target-maturity", "complete",
        "--prior-round-output", "draft.md",
    )
    check(
        "front-gate/prior-round-output requires prior-round-no",
        r.returncode != 0 and "--prior-round-no" in (r.stdout + r.stderr),
    )
    r = command(
        "prepare", fg, "happy", "draft.md", "--unassured", "--no-terms", "--no-docmodel",
        "--editing-state", "frozen", "--target-maturity", "complete",
    )
    happy_run = fg / "review-gate" / "happy"
    trace_path = happy_run / "deterministic" / "FRONT_GATE_TRACE.json"
    trace = json.loads(trace_path.read_text(encoding="utf-8"))["review_front_gate_trace"] if trace_path.is_file() else []
    events = [e.get("event") for e in trace]
    input_gate_event = next((e for e in trace if e.get("event") == "input_gate_recorded"), None)
    check(
        "front-gate/happy path records input gate then starts all three lenses",
        r.returncode == 0
        and events == ["convention_profile_not_applicable", "input_gate_recorded", "lens_started", "lens_started", "lens_started"]
        and input_gate_event is not None
        and input_gate_event.get("editing_state") == "frozen"
        and input_gate_event.get("source_copy_verified") is True
        and input_gate_event.get("prior_round_exists") is False,
    )
    scaffold_path = happy_run / "deterministic" / "RECEIPT_SCAFFOLD.json"
    scaffold = json.loads(scaffold_path.read_text(encoding="utf-8")) if scaffold_path.is_file() else {}
    check(
        "front-gate/receipt scaffold names the frozen trace and round r1",
        scaffold.get("front_gate_ref", {}).get("path") == "deterministic/FRONT_GATE_TRACE.json"
        and scaffold.get("front_gate_ref", {}).get("sha256") == hashlib.sha256(trace_path.read_bytes()).hexdigest()
        and scaffold.get("round_context") == {"round_label": "r1"},
    )
    write(fg / "open-items.yaml", "meta:\n  target: demo\nitems: []\n")
    r = command(
        "prepare", fg, "draft-ok", "draft.md", "--unassured", "--no-terms", "--no-docmodel",
        "--editing-state", "in_progress", "--target-maturity", "draft",
        "--open-items-ledger", "open-items.yaml",
    )
    draft_run = fg / "review-gate" / "draft-ok"
    draft_scaffold = json.loads(
        (draft_run / "deterministic" / "RECEIPT_SCAFFOLD.json").read_text(encoding="utf-8")
    ) if r.returncode == 0 else {}
    check(
        "front-gate/in-progress editing state with an open-items ledger succeeds",
        r.returncode == 0
        and draft_scaffold.get("input_gate", {}).get("open_items", {}).get("ledger_ref", {}).get("path")
        == "frozen/open-items.yaml",
    )

print(f"\n=== review-gate: {PASSED} passed, {FAILED} failed ===")
sys.exit(1 if FAILED else 0)
