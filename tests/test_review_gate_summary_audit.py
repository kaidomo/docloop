#!/usr/bin/env python3
"""Regression tests for the independent summary auditor (docauth#314).

This tool exists because the first line of defence cannot audit itself. So the tests are
about the second line actually being independent: it re-reads the rendered body, not the
manifest the renderer wrote, and it refuses what does not match the receipt.

Fidelity to upstream is a check: the frozen fixture is what upstream's auditor produced,
and it never skips, because docauth is private and a check that only runs where upstream
exists is absent exactly where nobody notices.

Known gaps in the auditor itself, all present upstream and therefore not fixed here (a
downstream fix would fork the port rather than improve it). Reproduced against upstream
at e59c32f, each pinned below so the port cannot drift away from the upstream behaviour
without a test saying so:

- **entailment** -- whether a tag follows from the quotation it cites. Upstream states
  this as outside machine verification, covers it by human sampling, and the tool prints
  the limit on every run.
- **`match_strength` has no source binding.** The policy table declares it an EXACT match
  against the tags file, but the auditor never receives that file and the manifest does
  not carry the value, so a `match_strength` added to the body alone is not contradicted.
- **Ordering inside a severity/tag group is not compared** with the receipt's order, and
  drift rows are compared by membership and multiplicity rather than sequence.
- **Paths are compared by basename only.** The policy table calls this EXACT while the
  implementation comment calls it a weak check; the runtime NOTE discloses only
  entailment. Content hashes are bound, so a wrong path cannot smuggle wrong content --
  but the path itself is display information, not a guarantee.

What this suite does not attempt: the tagging step and the policy-table harness, which
are separate slices.
"""

from __future__ import annotations

import contextlib
import io
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
LIB = ROOT / "lib" / "review_gate"
FIXTURES = ROOT / "tests" / "fixtures" / "review-gate" / "human-summary"
sys.path.insert(0, str(LIB))

import audit_summary_traceability as audit  # noqa: E402

REL_SUMMARY = "tests/fixtures/review-gate/human-summary/upstream-summary.md"
REL_RECEIPT = "tests/fixtures/review-gate/human-summary/receipt.md"
REL_TARGET = "tests/fixtures/review-gate/human-summary/target.md"


@contextlib.contextmanager
def in_repo_root():
    previous = os.getcwd()
    os.chdir(ROOT)
    try:
        yield
    finally:
        os.chdir(previous)


def run_audit(summary: str = REL_SUMMARY) -> tuple[int, str]:
    out = io.StringIO()
    with in_repo_root(), contextlib.redirect_stdout(out), contextlib.redirect_stderr(out):
        rc = audit.main([summary, "--source", REL_RECEIPT, "--target-doc", REL_TARGET])
    return rc, out.getvalue()


def tampered(tmp: str, old: str, new: str, count: int = 1) -> str:
    """A copy of the good summary with a substitution -- the edit this tool answers.

    `count` matters: replacing one occurrence of something the summary states twice (the
    preamble and the manifest) leaves the two disagreeing, and the auditor catches that
    disagreement rather than the edit itself. Passing -1 changes both, which is what a
    real edit would do.
    """
    text = (FIXTURES / "upstream-summary.md").read_text(encoding="utf-8")
    assert old in text, f"fixture no longer contains {old!r}"
    path = Path(tmp) / "tampered.md"
    path.write_text(text.replace(old, new, count), encoding="utf-8")
    return str(path)


class CleanSummary(unittest.TestCase):
    def test_the_rendered_summary_passes_its_own_audit(self):
        rc, out = run_audit()
        self.assertEqual(0, rc, out)
        self.assertIn("TRACE-OK", out)

    def test_the_entailment_limit_is_stated_in_the_output(self):
        """The tool says what it does not check, every run -- not only in a doc."""
        _, out = run_audit()
        self.assertIn("entailment", out)

    def test_reachable_through_the_docloop_cli(self):
        proc = subprocess.run(
            [str(ROOT / "bin" / "docloop"), "review-gate", "audit-summary",
             REL_SUMMARY, "--source", REL_RECEIPT, "--target-doc", REL_TARGET],
            capture_output=True, text=True, cwd=ROOT,
        )
        self.assertEqual(0, proc.returncode, proc.stderr)
        self.assertIn("TRACE-OK", proc.stdout)


class TamperDetection(unittest.TestCase):
    """Each case edits the body a human reads. All were compared against upstream."""

    def _refuses(self, old: str, new: str, what: str):
        with tempfile.TemporaryDirectory() as tmp:
            rc, out = run_audit(tampered(tmp, old, new))
        self.assertNotEqual(0, rc, f"{what} was not caught:\n{out}")

    def test_an_invented_finding_id_is_caught(self):
        self._refuses("F-01", "F-99", "a finding id that is not in the receipt")

    def test_a_changed_severity_is_caught(self):
        self._refuses("P2", "P1", "a severity re-declared in the summary")

    def test_a_promoted_tag_is_caught(self):
        self._refuses("미분류", "미정의", "a tag upgraded in the body")

    def test_an_edited_locator_line_is_caught(self):
        self._refuses("Section B", "Section Z", "a source locator that does not match the target")


class Independence(unittest.TestCase):
    """The point of a second line of defence is not sharing the first one's blind spot."""

    def test_audit_reads_the_body_not_only_the_manifest(self):
        """Editing the body while leaving the manifest intact must still fail.

        This is the failure that motivated the tool: an earlier design audited the
        trailer alone, so a hand-edited body passed.
        """
        text = (FIXTURES / "upstream-summary.md").read_text(encoding="utf-8")
        body, _, manifest = text.partition("<!--")
        self.assertTrue(manifest, "fixture has no manifest trailer to leave untouched")
        self.assertIn("F-01", body, "the finding id must appear in the visible body")
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "body-only-edit.md"
            path.write_text(body.replace("F-01", "F-42", 1) + "<!--" + manifest, encoding="utf-8")
            rc, out = run_audit(str(path))
        self.assertNotEqual(0, rc, f"a body-only edit passed the audit:\n{out}")

    def test_a_decoy_target_document_is_refused(self):
        with tempfile.TemporaryDirectory() as tmp:
            decoy = Path(tmp) / "decoy.md"
            decoy.write_text("# not the reviewed document\n", encoding="utf-8")
            out = io.StringIO()
            with in_repo_root(), contextlib.redirect_stdout(out), \
                 contextlib.redirect_stderr(out):
                rc = audit.main([REL_SUMMARY, "--source", REL_RECEIPT,
                                 "--target-doc", str(decoy)])
        self.assertNotEqual(0, rc, f"a decoy target document passed:\n{out.getvalue()}")


class KnownGapsPinned(unittest.TestCase):
    """The gaps above are pinned as behaviour, so a silent change is caught either way.

    These assert what the tool does *not* catch. That reads strange until you consider the
    alternative: an undocumented gap that quietly closes or widens with the next re-port,
    with nothing to say which. If one of these starts failing, upstream changed and this
    port needs re-checking -- that is the signal, not a defect in the test.
    """

    def test_a_match_strength_in_the_body_alone_is_not_contradicted(self):
        with tempfile.TemporaryDirectory() as tmp:
            rc, _ = run_audit(tampered(tmp, "- **F-01**", "- **F-01** · 매칭 강"))
        self.assertEqual(0, rc, "upstream tolerates this; a downstream fix would fork the port")

    def test_a_fabricated_directory_with_the_same_basename_passes(self):
        with tempfile.TemporaryDirectory() as tmp:
            rc, _ = run_audit(tampered(
                tmp, "tests/fixtures/review-gate/human-summary/receipt.md",
                "/fabricated/location/receipt.md", count=-1))
        self.assertEqual(0, rc, "paths are compared by basename upstream; content hashes still bind")

    def test_changing_only_one_of_the_two_stated_paths_is_caught(self):
        """The counterpart: preamble and manifest must agree, and that much is checked."""
        with tempfile.TemporaryDirectory() as tmp:
            rc, _ = run_audit(tampered(
                tmp, "tests/fixtures/review-gate/human-summary/receipt.md",
                "/fabricated/location/receipt.md", count=1))
        self.assertNotEqual(0, rc)

    def test_the_runtime_note_discloses_entailment_only(self):
        """Pins the disclosure gap itself: the other three limits are not printed."""
        _, out = run_audit()
        self.assertIn("entailment", out)
        self.assertNotIn("match_strength", out)
        self.assertNotIn("basename", out)


class UpstreamEquivalence(unittest.TestCase):
    UPSTREAM = Path(
        os.environ.get("DOCUAUTHRING_ROOT", str(Path.home() / "GitHub" / "docauth"))
    ) / "skills" / "review-gate" / "scripts" / "audit_summary_traceability.py"

    def test_verdict_matches_upstream_on_the_clean_summary(self):
        if not self.UPSTREAM.is_file():
            self.skipTest(f"upstream checkout not found at {self.UPSTREAM}")
        proc = subprocess.run(
            [sys.executable, str(self.UPSTREAM), REL_SUMMARY,
             "--source", REL_RECEIPT, "--target-doc", REL_TARGET],
            capture_output=True, text=True, cwd=ROOT,
        )
        rc, out = run_audit()
        self.assertEqual(proc.returncode, rc)
        self.assertEqual(proc.stdout + proc.stderr, out)

    def test_verdicts_match_upstream_on_every_tamper_case(self):
        if not self.UPSTREAM.is_file():
            self.skipTest(f"upstream checkout not found at {self.UPSTREAM}")
        for old, new in (("F-01", "F-99"), ("P2", "P1"), ("미분류", "미정의"),
                         ("Section B", "Section Z")):
            with self.subTest(tamper=f"{old}->{new}"), tempfile.TemporaryDirectory() as tmp:
                path = tampered(tmp, old, new)
                proc = subprocess.run(
                    [sys.executable, str(self.UPSTREAM), path,
                     "--source", REL_RECEIPT, "--target-doc", REL_TARGET],
                    capture_output=True, text=True, cwd=ROOT,
                )
                rc, _ = run_audit(path)
                self.assertEqual(proc.returncode, rc)


if __name__ == "__main__":
    unittest.main(verbosity=2)
