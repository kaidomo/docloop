#!/usr/bin/env python3
"""Regression tests for the human-readable summary renderer (docauth#314).

Fidelity here is a check, not a claim: the frozen fixture is what upstream's own renderer
produced, and the port has to reproduce it byte for byte. That test never skips, because
docauth is private and a check that only runs where upstream exists is absent exactly
where nobody can notice. A second test re-derives the fixture from a live checkout when
one is present, which answers "is the fixture stale" -- a different question.

What this suite does NOT check (disclosed rather than chased):
- the tagging step (`stage_summary_tags.py`), which is a separate opt-in tool
- the independent auditor (`audit_summary_traceability.py`), the second line of defence,
  which lands in its own slice
- entailment: whether a tag actually follows from the quotation it cites. Upstream states
  this as a machine-unverifiable limit covered by human sampling, and porting does not
  change that
- the policy-table harness that upstream drives its field rules with
"""

from __future__ import annotations

import contextlib
import hashlib
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

import render_human_summary as rhs  # noqa: E402
import summary_projection as proj  # noqa: E402


# The renderer records the paths it was given, verbatim, so the fixture is only
# machine-independent when every invocation uses the same repo-relative paths. That is a
# property of the tool, not of the test: an absolute path would bind the summary to one
# checkout. Tests therefore run from the repository root with relative paths.
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


def render_fixture(out: Path, *extra: str, target: str = REL_TARGET) -> int:
    with in_repo_root(), contextlib.redirect_stdout(io.StringIO()):
        return rhs.main([REL_RECEIPT, "--target-doc", target, "--output", str(out), *extra])


class UpstreamEquivalence(unittest.TestCase):
    def test_render_matches_the_frozen_upstream_output(self):
        """Runs unconditionally -- a contributor without docauth gets this check too."""
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp) / "summary.md"
            self.assertEqual(0, render_fixture(out))
            self.assertEqual((FIXTURES / "upstream-summary.md").read_text(encoding="utf-8"),
                             out.read_text(encoding="utf-8"))

    def test_reachable_through_the_docloop_cli(self):
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp) / "summary.md"
            proc = subprocess.run(
                [str(ROOT / "bin" / "docloop"), "review-gate", "render-summary",
                 REL_RECEIPT, "--target-doc", REL_TARGET, "--output", str(out)],
                capture_output=True, text=True, cwd=ROOT,
            )
            self.assertEqual(0, proc.returncode, proc.stderr)
            self.assertEqual((FIXTURES / "upstream-summary.md").read_text(encoding="utf-8"),
                             out.read_text(encoding="utf-8"))


class FixtureCurrency(unittest.TestCase):
    """Is the frozen fixture still what upstream produces? The one test allowed to skip."""

    UPSTREAM = Path(
        os.environ.get("DOCUAUTHRING_ROOT", str(Path.home() / "GitHub" / "docauth"))
    ) / "skills" / "review-gate" / "scripts" / "render_human_summary.py"

    def test_fixture_still_matches_live_upstream(self):
        if not self.UPSTREAM.is_file():
            self.skipTest(f"upstream checkout not found at {self.UPSTREAM} -- fixture "
                          "currency unchecked; equivalence itself still ran")
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp) / "up.md"
            proc = subprocess.run(
                [sys.executable, str(self.UPSTREAM), REL_RECEIPT,
                 "--target-doc", REL_TARGET, "--output", str(out)],
                capture_output=True, text=True, cwd=ROOT,
            )
            self.assertEqual(0, proc.returncode, proc.stderr)
            self.assertEqual((FIXTURES / "upstream-summary.md").read_text(encoding="utf-8"),
                             out.read_text(encoding="utf-8"),
                             "the frozen fixture no longer matches upstream -- re-capture "
                             "it and re-check the port rather than editing this test")


class AntiFabrication(unittest.TestCase):
    """The refusals that exist because a summary once carried a sentence nobody wrote."""

    def test_target_document_must_be_bound_to_the_receipt(self):
        """An unbound target lets the same decoy be fed to renderer and auditor alike."""
        with tempfile.TemporaryDirectory() as tmp:
            decoy = Path(tmp) / "decoy.md"
            decoy.write_text("# not the reviewed document\n", encoding="utf-8")
            out = Path(tmp) / "s.md"
            with contextlib.redirect_stderr(io.StringIO()) as err:
                rc = render_fixture(out, target=str(decoy))
            self.assertNotEqual(0, rc)
            self.assertIn("not the document that was reviewed", err.getvalue())
            self.assertFalse(out.exists(), "a refused render must not leave a file behind")

    def test_a_fabricated_quotation_refuses_the_whole_render(self):
        """A tag's quotation must be verbatim in the receipt's own judgment_provenance.

        Fail-closed, and deliberately not fail-safe: a fabricated citation does not get
        downgraded to `미분류`, it stops the render. Verified identical to upstream.
        """
        with tempfile.TemporaryDirectory() as tmp:
            tags = Path(tmp) / "tags.yaml"
            tags.write_text(
                "tags:\n  F-01:\n    tag: 미정의\n    quote: this sentence exists nowhere\n"
                "    match_strength: 강\n", encoding="utf-8")
            out = Path(tmp) / "s.md"
            with contextlib.redirect_stderr(io.StringIO()) as err:
                rc = render_fixture(out, "--tags", str(tags))
            self.assertEqual(1, rc)
            self.assertIn("fabricated citation", err.getvalue())
            self.assertFalse(out.exists(), "a refused render must not leave a file behind")

    def test_existing_output_is_not_overwritten_without_force(self):
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp) / "summary.md"
            out.write_text("previous render\n", encoding="utf-8")
            with contextlib.redirect_stdout(io.StringIO()), \
                 contextlib.redirect_stderr(io.StringIO()):
                rc = render_fixture(out)
            self.assertEqual(2, rc)
            self.assertEqual("previous render\n", out.read_text(encoding="utf-8"))

    def test_a_quotation_present_in_the_provenance_does_carry_its_tag(self):
        """The counterpart: a real quotation classifies, or the check above proves nothing."""
        with tempfile.TemporaryDirectory() as tmp:
            tags = Path(tmp) / "tags.yaml"
            tags.write_text("tags:\n  F-01:\n    tag: 미정의\n    quote: approved convention "
                            "says the value is required\n    match_strength: 강\n",
                            encoding="utf-8")
            out = Path(tmp) / "s.md"
            self.assertEqual(0, render_fixture(out, "--tags", str(tags)))
            rendered = out.read_text(encoding="utf-8")
        self.assertIn("미정의", rendered)
        self.assertNotIn("#### 미분류", rendered)

    def test_force_re_render_says_so_in_the_output(self):
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp) / "summary.md"
            self.assertEqual(0, render_fixture(out))
            first = out.read_text(encoding="utf-8")
            self.assertEqual(0, render_fixture(out, "--force"))
            self.assertNotEqual(first, out.read_text(encoding="utf-8"),
                                "a re-render must be distinguishable from a first render")


class ControlledVocabulary(unittest.TestCase):
    """The vocabulary is pinned, and a tag outside it stops the render.

    Pinning matters because the vocabulary is what a tagging agent is allowed to say. If
    an entry were dropped here, every finding carrying it would stop rendering; if one
    were added, an agent could introduce a category no reviewer agreed to.
    """

    EXPECTED = ("정합 안 맞음", "미정의", "모호함", "표기 불일치",
                "출처 불명", "중복", "근거 약함", "근거 없음")

    def test_vocabulary_matches_its_golden(self):
        self.assertEqual(self.EXPECTED, proj.CONTROLLED_VOCAB)

    def test_a_tag_outside_the_vocabulary_refuses_the_render(self):
        with tempfile.TemporaryDirectory() as tmp:
            tags = Path(tmp) / "tags.yaml"
            tags.write_text("tags:\n  F-01:\n    tag: not-in-the-vocabulary\n"
                            "    quote: approved convention says the value is required\n"
                            "    match_strength: 강\n", encoding="utf-8")
            out = Path(tmp) / "s.md"
            with contextlib.redirect_stderr(io.StringIO()) as err:
                rc = render_fixture(out, "--tags", str(tags))
            self.assertEqual(1, rc, "a tag outside the vocabulary is refused, not silently dropped")
            self.assertIn("not in the controlled vocabulary", err.getvalue())

    def test_low_confidence_is_downgraded_whatever_the_tag_said(self):
        with tempfile.TemporaryDirectory() as tmp:
            tags = Path(tmp) / "tags.yaml"
            tags.write_text("tags:\n  F-01:\n    tag: 미정의\n"
                            "    quote: approved convention says the value is required\n"
                            "    match_strength: 약\n", encoding="utf-8")
            out = Path(tmp) / "s.md"
            self.assertEqual(0, render_fixture(out, "--tags", str(tags)))
            rendered = out.read_text(encoding="utf-8")
        self.assertIn("#### 미분류", rendered)


class SharedDefinitions(unittest.TestCase):
    """The projection owns the definitions both consumers must agree on."""

    def test_enums_come_from_the_schema_not_a_local_copy(self):
        sys.path.insert(0, str(LIB))
        from validate_review_intermediate import QUESTION_STATUSES, VERIFY_RESULTS
        self.assertIs(proj.QUESTION_STATUSES, QUESTION_STATUSES)
        self.assertIs(proj.VERIFY_RESULTS, VERIFY_RESULTS)

    def test_anchor_semantics_come_from_the_shared_module(self):
        from anchor_semantics import anchor_hash, norm
        self.assertIs(proj.anchor_hash, anchor_hash)
        self.assertEqual(proj._norm_line, norm)

    def test_the_strict_loader_rejects_a_duplicated_findings_key(self):
        """`yaml.safe_load` would let the last one win and render zero findings."""
        raw = (FIXTURES / "receipt.md").read_bytes()
        tampered = raw.replace(b"\n---\n", b"\n  findings: []\n---\n", 1)
        with tempfile.TemporaryDirectory() as tmp:
            bad = Path(tmp) / "bad.md"
            bad.write_bytes(tampered)
            with self.assertRaises(Exception):
                proj.load_receipt_from_bytes(bad, tampered)


if __name__ == "__main__":
    unittest.main(verbosity=2)
