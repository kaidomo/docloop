#!/usr/bin/env python3
"""Regression tests for the CONTRACT §13 round comparison table generator.

The point of this suite is the binding at the top: the file this generator writes is
the file `validate_review_result.py` will later refuse or accept, so the signature is
tested against the validator's own constant rather than a copy of the string.
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
sys.path.insert(0, str(LIB))

import match_review_rounds as mrr  # noqa: E402
from validate_review_result import COMPARISON_TABLE_SIGNATURE  # noqa: E402


def render(prev_text: str, curr_text: str, prev_round: int = 1, curr_round: int = 2,
           lang: str = "ko") -> str:
    rows, curr_ids, new_ids = mrr.build_table(prev_text, curr_text, prev_round, curr_round)
    return mrr.render_table(rows, curr_ids, new_ids, prev_round, curr_round, lang)


def verdict_for(table: str, id_token: str) -> str:
    for line in table.splitlines():
        if line.startswith(f"| `{id_token}` |"):
            return line.split("|")[2].strip()
    raise AssertionError(f"{id_token} missing from table:\n{table}")


class SignatureBinding(unittest.TestCase):
    def test_header_matches_the_validator_constant(self):
        """The generator's header is what the validator matches -- not a lookalike.

        If either side is edited alone, a receipt that passes validation can no longer
        be produced by this tool, which is exactly the gap this port closed.
        """
        table = render("- r1-01 x", "- r1-01 remains open")
        self.assertTrue(
            table.startswith(COMPARISON_TABLE_SIGNATURE),
            f"table header {table.splitlines()[0]!r} does not start with the validator's "
            f"signature {COMPARISON_TABLE_SIGNATURE!r}",
        )

    def test_signature_survives_a_written_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp) / "TABLE.md"
            prev, curr = Path(tmp) / "p.md", Path(tmp) / "c.md"
            prev.write_text("- r1-01 x\n", encoding="utf-8")
            curr.write_text("- r1-01 remains open\n", encoding="utf-8")
            rc = mrr.main([str(prev), str(curr), "--prev-round", "1", "--curr-round", "2",
                           "--out", str(out)])
            self.assertEqual(rc, 0)
            self.assertTrue(out.read_text(encoding="utf-8").startswith(COMPARISON_TABLE_SIGNATURE))


class Classification(unittest.TestCase):
    def test_open_words_alone_mean_carried_open(self):
        table = render("- r1-01 x", "- r1-01 remains a problem")
        self.assertIn("carried(open)", verdict_for(table, "r1-01"))

    def test_closed_words_alone_mean_carried_closed_by_self_report(self):
        table = render("- r1-01 x", "- r1-01 resolved in this patch")
        self.assertIn("closed 자기언급", verdict_for(table, "r1-01"))

    def test_no_verdict_vocabulary_stays_unknown(self):
        table = render("- r1-01 x", "- r1-01 was discussed at length")
        self.assertEqual("불명", verdict_for(table, "r1-01"))

    def test_absent_id_is_resolved_meaning_not_mentioned(self):
        table = render("- r1-01 x", "- nothing about the first one")
        self.assertIn("미언급", verdict_for(table, "r1-01"))

    def test_negation_flips_a_closed_word_to_open(self):
        """'not resolved' must not read as closed -- the whole reason for the lookback."""
        table = render("- r1-01 x", "- r1-01 was never resolved")
        self.assertIn("carried(open)", verdict_for(table, "r1-01"))

    def test_open_and_closed_words_together_stay_open_and_ask_for_a_human(self):
        """The mixed branch is the one that must not quietly resolve either way."""
        table = render("- r1-01 x", "- r1-01 remains open but was resolved elsewhere")
        verdict = verdict_for(table, "r1-01")
        self.assertIn("carried(open)", verdict)
        self.assertIn("사람 재확인 권장", verdict)

    def test_negation_with_words_between_trigger_and_stem(self):
        table = render("- r1-01 x", "- r1-01 cannot be resolved yet")
        self.assertIn("carried(open)", verdict_for(table, "r1-01"))


CASES = {
    "open_only": "- r1-01 remains a problem",
    "closed_only": "- r1-01 resolved in this patch",
    "mixed": "- r1-01 remains open but was resolved elsewhere",
    "silent": "- r1-01 was discussed at length",
    "absent": "- nothing about the first one",
}


class VerdictVocabulary(unittest.TestCase):
    """The verdict strings are what a human reads, so both languages are pinned.

    Nothing machine-checks them -- `validate_review_result.py` matches the header
    signature and the file hash, never the table body -- which is exactly why they can
    drift without anyone noticing. These goldens make a change to them deliberate.
    """

    GOLDEN_KO = {
        "open_only": "carried(open)",
        "closed_only": "carried(closed 자기언급 — 실제 종결은 신규 id 부재로 재확인)",
        "mixed": "carried(open) — 열림·닫힘 어휘 동시 등장, 사람 재확인 권장",
        "silent": "불명",
        "absent": "resolved(미언급 — 사람 확인 필요)",
    }
    GOLDEN_EN = {
        "open_only": "carried(open)",
        "closed_only": "carried(closed by self-report -- confirm the close by the id being "
                       "absent next round)",
        "mixed": "carried(open) -- open and closed words both present; a human should re-check",
        "silent": "unknown",
        "absent": "resolved(not mentioned -- needs human confirmation)",
    }

    def test_default_language_renders_the_exact_korean_strings(self):
        for key, curr in CASES.items():
            with self.subTest(verdict=key):
                self.assertEqual(self.GOLDEN_KO[key],
                                 verdict_for(render("- r1-01 x", curr), "r1-01"))

    def test_english_renders_the_exact_english_strings(self):
        for key, curr in CASES.items():
            with self.subTest(verdict=key):
                self.assertEqual(self.GOLDEN_EN[key],
                                 verdict_for(render("- r1-01 x", curr, lang="en"), "r1-01"))

    def test_both_languages_cover_every_verdict_key(self):
        """A key added to one language and forgotten in the other renders a KeyError."""
        self.assertEqual(set(mrr.VERDICTS["ko"]), set(mrr.VERDICTS["en"]))
        self.assertEqual(set(mrr.LABELS["ko"]), set(mrr.LABELS["en"]))

    def test_unknown_language_is_refused(self):
        with self.assertRaises(ValueError):
            mrr.render_table([], [], [], 1, 2, lang="fr")


class UpstreamEquivalence(unittest.TestCase):
    """The default output is upstream's, byte for byte.

    This is the whole reason `ko` is the default: a docloop table and a docauth table of
    the same two rounds must be directly comparable. Skipped, not failed, when the
    upstream checkout is absent -- contributors without it still get a green suite.
    """

    UPSTREAM = Path(
        os.environ.get("DOCUAUTHRING_ROOT", str(Path.home() / "GitHub" / "docauth"))
    ) / "skills" / "review-gate" / "scripts" / "match_review_rounds.py"

    def test_default_output_matches_the_upstream_generator(self):
        if not self.UPSTREAM.is_file():
            self.skipTest(f"upstream checkout not found at {self.UPSTREAM}")
        prev = "- r1-01 first\n- r1-02 second\n- r1-03 third\n"
        curr = ("- r1-01 remains open\n- r1-02 resolved in the patch\n"
                "- r1-04 was discussed\n- r2-01 brand new far away " + "filler " * 40 + "\n")
        with tempfile.TemporaryDirectory() as tmp:
            p, c = Path(tmp) / "p.md", Path(tmp) / "c.md"
            p.write_text(prev, encoding="utf-8")
            c.write_text(curr, encoding="utf-8")
            upstream = subprocess.run(
                [sys.executable, str(self.UPSTREAM), str(p), str(c),
                 "--prev-round", "1", "--curr-round", "2"],
                capture_output=True, text=True,
            )
            self.assertEqual(0, upstream.returncode, upstream.stderr)
            ours = render(prev, curr)
        self.assertEqual(upstream.stdout.rstrip("\n"), ours.rstrip("\n"))


class TokenBoundaries(unittest.TestCase):
    def test_a_longer_id_is_not_mistaken_for_a_shorter_one(self):
        """`r1-01` must not match inside `r1-010`; they are different findings."""
        table = render("- r1-01 x", "- r1-010 remains open")
        self.assertIn("미언급", verdict_for(table, "r1-01"))

    def test_korean_particle_directly_after_an_id_still_extracts(self):
        """`r1-01은` gave no word boundary, so extraction used to fail outright."""
        self.assertEqual(["r1-01"], mrr._extract_ids("- r1-01은 문제다", 1))


class NewCandidates(unittest.TestCase):
    def test_id_far_from_any_previous_id_is_a_new_candidate(self):
        curr = "- r1-01 remains open\n" + ("filler. " * 40) + "\n- r2-07 an unrelated finding"
        table = render("- r1-01 x", curr)
        self.assertIn("신규 후보", verdict_for(table, "r2-07"))

    def test_id_next_to_a_previous_id_is_flagged_for_recheck(self):
        table = render("- r1-01 x", "- r2-07 supersedes r1-01")
        self.assertIn("인접 언급", verdict_for(table, "r2-07"))


class RoundAdjacency(unittest.TestCase):
    def _run(self, *extra):
        with tempfile.TemporaryDirectory() as tmp:
            prev, curr = Path(tmp) / "p.md", Path(tmp) / "c.md"
            prev.write_text("- r1-01 x\n", encoding="utf-8")
            curr.write_text("- r3-01 y\n", encoding="utf-8")
            # the tool prints the table on success; keep the suite's own output readable
            with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
                return mrr.main([str(prev), str(curr), "--prev-round", "1",
                                 "--curr-round", "3", *extra])

    def test_non_adjacent_rounds_fail_closed(self):
        self.assertEqual(1, self._run())

    def test_non_adjacent_rounds_proceed_when_explicitly_allowed(self):
        self.assertEqual(0, self._run("--allow-non-adjacent"))

    def test_missing_input_file_is_an_input_error(self):
        with tempfile.TemporaryDirectory() as tmp:
            curr = Path(tmp) / "c.md"; curr.write_text("- r2-01 y\n", encoding="utf-8")
            with contextlib.redirect_stderr(io.StringIO()):
                rc = mrr.main([str(Path(tmp) / "nope.md"), str(curr),
                               "--prev-round", "1", "--curr-round", "2"])
        self.assertEqual(1, rc)


class CliSurface(unittest.TestCase):
    def test_reachable_through_the_docloop_review_gate_dispatch(self):
        with tempfile.TemporaryDirectory() as tmp:
            prev, curr = Path(tmp) / "p.md", Path(tmp) / "c.md"
            prev.write_text("- r1-01 x\n", encoding="utf-8")
            curr.write_text("- r1-01 remains open\n", encoding="utf-8")
            proc = subprocess.run(
                [str(ROOT / "bin" / "docloop"), "review-gate", "match-rounds",
                 str(prev), str(curr), "--prev-round", "1", "--curr-round", "2"],
                capture_output=True, text=True,
            )
        self.assertEqual(0, proc.returncode, proc.stderr)
        self.assertTrue(proc.stdout.startswith(COMPARISON_TABLE_SIGNATURE))

    def test_lang_flag_selects_the_english_wording(self):
        with tempfile.TemporaryDirectory() as tmp:
            prev, curr = Path(tmp) / "p.md", Path(tmp) / "c.md"
            prev.write_text("- r1-01 x\n", encoding="utf-8")
            curr.write_text("- r1-01 remains open\n", encoding="utf-8")
            proc = subprocess.run(
                [str(ROOT / "bin" / "docloop"), "review-gate", "match-rounds",
                 str(prev), str(curr), "--prev-round", "1", "--curr-round", "2", "--lang", "en"],
                capture_output=True, text=True,
            )
        self.assertEqual(0, proc.returncode, proc.stderr)
        self.assertTrue(proc.stdout.startswith(COMPARISON_TABLE_SIGNATURE),
                        "the signature must not follow the language choice")
        self.assertIn("verdict", proc.stdout)
        self.assertNotIn("판정", proc.stdout)

    def test_listed_in_the_help_text(self):
        proc = subprocess.run([str(ROOT / "bin" / "docloop"), "review-gate", "--help"],
                              capture_output=True, text=True)
        self.assertIn("match-rounds", proc.stdout)


if __name__ == "__main__":
    unittest.main(verbosity=2)
