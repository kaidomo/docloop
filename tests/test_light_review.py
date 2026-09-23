"""Structural regressions with synthetic responses; not a model quality benchmark."""
import copy
import html
import importlib.util
import json
import os
import re
import subprocess
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("light_review", ROOT / "lib/light_review.py")
lr = importlib.util.module_from_spec(spec)
spec.loader.exec_module(lr)


class LightReviewTests(unittest.TestCase):
    def setUp(self):
        # Preserve synthetic test artifacts for failure inspection.
        self.root = Path(tempfile.mkdtemp(prefix="docloop-light-test-"))
        self.source = self.root / "source.md"
        self.source.write_text("# Requirement\nPrevent duplicate records.\nRetry until recorded.\nTiming is delegated to policy B.\n", encoding="utf-8")
        self.out = self.root / "run"
        self.packet = lr.prepare(self.source, lr.digest(self.source.read_bytes()), self.out)

    def item(self, lane="structure", **changes):
        value = {"id": lane + "-1", "kind": "question", "problem": "Which policy timing applies?",
                 "evidence": [{"start": 4, "end": 4, "quote": "Timing is delegated to policy B."}],
                 "scenario": "Policy B was not supplied; timing cannot be confirmed.",
                 "action": "Confirm the applicable policy; do not repeat it here."}
        value.update(changes)
        return value

    def save(self, lane, **values):
        lr.write_new(self.out / (lane + ".json"), lr.encoded(dict(
            packet_sha256=self.packet["packet_sha256"], lane=lane, read_complete=True, **values)))

    def calls(self):
        records = []
        for lane in (*lr.LANES, "falsifier"):
            records.append(dict(lane=lane, host_call_id="synthetic-" + lane,
                started_at=self.packet["started_at"], ended_at=self.packet["started_at"],
                model="synthetic test response, no model execution", fresh_context=True,
                host_evidence_ref="synthetic fixture, not a host receipt", effective_tool_policy="synthetic no tools",
                outcome="completed", prompt_sha256=lr.digest((self.out / (lane + ".prompt.txt")).read_bytes()),
                response_sha256=lr.digest((self.out / (lane + ".json")).read_bytes())))
        manifest = dict(schema_version=1, packet_sha256=self.packet["packet_sha256"], calls=records)
        lr.write_new(self.out / "calls.json", lr.encoded(manifest))
        return manifest

    def test_full_flow_questions_complete_without_glossary(self):
        for lane in lr.LANES:
            self.save(lane, candidates=[self.item(lane)])
        lr.falsify(self.out)
        self.save("falsifier", decisions=[dict(id=lane + "-1", disposition="question",
                  reason="External policy not inspected; not a local defect", resolution=None) for lane in lr.LANES])
        self.calls()
        result = lr.finalize(self.out)
        self.assertEqual(result["status"], "completed")
        self.assertFalse(result["document_approved"])
        self.assertEqual(len(result["groups"]), 1)
        self.assertEqual(len(result["groups"][0]["members"]), 2)
        self.assertEqual(result["inputs"]["glossary"], "not supplied / not checked")
        self.assertIn("External policy", (self.out / "result.md").read_text())
        self.assertEqual(result["call_record_status"], "consistent_caller_record")
        self.assertEqual(result["calls_sha256"], lr.digest((self.out / "calls.json").read_bytes()))

    def test_experimental_profile_roundtrip_preserves_questions_and_binding(self):
        self.out = self.root / "profile"
        self.packet = lr.prepare(self.source, lr.digest(self.source.read_bytes()),
                                 self.out, profile="decision-aware-v1")
        self.assertEqual(lr.load_packet(self.out), self.packet)
        for lane in lr.LANES:
            self.save(lane, candidates=[self.item(lane)])
        lr.falsify(self.out)
        for lane in (*lr.LANES, "falsifier"):
            prompt_text = (self.out / (lane + ".prompt.txt")).read_text()
            self.assertEqual(prompt_text.count("Experimental decision-aware-v1 review instructions:"), 1)
            self.assertIn("Preserve explicit incompatible rules", prompt_text)
            self.assertIn("state the unverified premise", prompt_text)
        self.save("falsifier", decisions=[dict(id=lane + "-1", disposition="question",
                  reason="External premise still needs confirmation", resolution=None) for lane in lr.LANES])
        self.calls()
        result = lr.finalize(self.out)
        self.assertEqual(result["status"], "completed")
        self.assertEqual(result["review_profile"], "decision-aware-v1")
        self.assertIn("decision&#45;aware&#45;v1", (self.out / "result.md").read_text())
        self.assertIn("실험 검토 프로필", (self.out / "result.md").read_text())
        self.assertEqual(sum(len(g["members"]) for g in result["groups"]), 2)
        self.assertFalse(result["document_approved"])

    def test_profile_cannot_be_added_removed_or_changed_without_invalidating_hash(self):
        original = copy.deepcopy(self.packet)
        edited = dict(original, review_profile="decision-aware-v1")
        (self.out / "packet.json").write_text(lr.encoded(edited))
        with self.assertRaisesRegex(ValueError, "integrity mismatch"):
            lr.load_packet(self.out)
        profiled_out = self.root / "profile"
        packet = lr.prepare(self.source, lr.digest(self.source.read_bytes()),
                            profiled_out, profile="decision-aware-v1")
        for changed in (None, "unknown"):
            edited = dict(packet)
            if changed is None:
                del edited["review_profile"]
            else:
                edited["review_profile"] = changed
            (profiled_out / "packet.json").write_text(lr.encoded(edited))
            with self.assertRaisesRegex(ValueError, "integrity mismatch"):
                lr.load_packet(profiled_out)

    def test_invalid_profile_fails_before_creating_run(self):
        out = self.root / "invalid-profile"
        with self.assertRaisesRegex(ValueError, "unsupported review profile"):
            lr.prepare(self.source, lr.digest(self.source.read_bytes()), out, profile="unknown")
        self.assertFalse(out.exists())

    def test_default_packet_remains_compatible_without_profile(self):
        self.assertNotIn("review_profile", self.packet)
        self.assertEqual(lr.load_packet(self.out), self.packet)
        for lane in lr.LANES:
            self.assertNotIn("Experimental decision-aware-v1", lr.prompt(self.packet, lane))

    def test_profile_does_not_allow_falsifier_prompt_append_even_with_updated_call_hash(self):
        self.out = self.root / "profile"
        self.packet = lr.prepare(self.source, lr.digest(self.source.read_bytes()),
                                 self.out, profile="decision-aware-v1")
        for lane in lr.LANES:
            self.save(lane, candidates=[])
        lr.falsify(self.out)
        path = self.out / "falsifier.prompt.txt"
        path.write_text(path.read_text() + "Unbound extra instruction\n")
        self.save("falsifier", decisions=[])
        self.calls()
        result = lr.finalize(self.out)
        self.assertEqual(result["status"], "partial")
        self.assertTrue(any("snapshot mismatch" in e for e in result["errors"]))
        self.assertEqual(result["call_record_status"], "provenance_unverified")

    def test_copying_response_example_identity_binds_all_three_lanes(self):
        # Simulate copying the example's metadata, not model semantic accuracy.
        def example_identity(lane):
            text = (self.out / (lane + ".prompt.txt")).read_text()
            instructions = text.split("\nINPUTS (", 1)[0]
            match = re.search(r'"packet_sha256":("[0-9a-f]{64}")', instructions)
            self.assertIsNotNone(match, "response example needs an explicit packet identity")
            identity = json.loads(match.group(1))
            self.assertNotEqual(identity, self.packet["inputs"]["source"]["sha256"])
            return identity

        for lane in lr.LANES:
            lr.write_new(self.out / (lane + ".json"), lr.encoded(dict(
                packet_sha256=example_identity(lane), lane=lane, read_complete=True,
                candidates=[self.item(lane)])))
        lr.falsify(self.out)
        lr.write_new(self.out / "falsifier.json", lr.encoded(dict(
            packet_sha256=example_identity("falsifier"), lane="falsifier", read_complete=True,
            decisions=[dict(id=lane + "-1", disposition="question", reason="Policy is unavailable", resolution=None)
                       for lane in lr.LANES])))
        self.calls()
        result = lr.finalize(self.out)
        self.assertEqual(result["status"], "completed")
        self.assertEqual(result["call_record_status"], "consistent_caller_record")
        self.assertEqual(result["calls_sha256"], lr.digest((self.out / "calls.json").read_bytes()))

    def test_changed_candidate_cannot_inherit_stale_falsifier_decision(self):
        for lane in lr.LANES:
            self.save(lane, candidates=[self.item(lane)])
        lr.falsify(self.out)
        self.save("falsifier", decisions=[dict(id=lane + "-1", disposition="keep",
                  reason="Reviewed original claim", resolution=None) for lane in lr.LANES])
        path = self.out / "structure.json"
        changed = lr.read_json(path)
        changed["candidates"][0]["problem"] = "A different claim never reviewed"
        path.write_text(lr.encoded(changed), encoding="utf-8")
        self.calls()  # Even updated file hashes cannot bind an old prompt to new claims.
        result = lr.finalize(self.out)
        self.assertEqual(result["status"], "partial")
        self.assertEqual(result["call_record_status"], "provenance_unverified")
        self.assertTrue(all(member["decision"] is None
                            for group in result["groups"] for member in group["members"]))
        self.assertTrue(any("candidate snapshot" in error for error in result["errors"]))

    def test_report_keeps_untrusted_markup_literal_without_mutating_records(self):
        attack = '<script>alert(1)</script>\n# forged\n[click](https://example.invalid)\n```\n'
        candidate = self.item(problem=attack, scenario=attack, action=attack)
        candidate['evidence'][0]['quote'] = attack
        member = dict(candidate=candidate, status='question', decision=dict(
            reason=attack, resolution=candidate['evidence'][0]))
        result = dict(status='completed', inputs={'source':attack}, elapsed_seconds=1,
                      groups=[dict(claim=candidate, members=[member])],
                      excluded=[dict(raw=attack)], rejected=[dict(raw={'id':attack})], errors=[attack])
        original = copy.deepcopy(result)
        report = lr.render(result)
        outside = []
        fence = None
        for line in report.splitlines():
            if fence:
                if line == fence:
                    fence = None
                continue
            if line.startswith('```'):
                fence = line.removesuffix('json')
                continue
            outside.append(line)
        self.assertIsNone(fence)
        visible = '\n'.join(outside)
        self.assertNotIn('<script>', visible)
        self.assertNotIn('[click](', visible)
        self.assertNotIn('# forged', visible)
        self.assertEqual(result, original)

    def test_missing_or_corrupt_call_provenance_cannot_complete(self):
        for lane in lr.LANES:
            self.save(lane, candidates=[])
        lr.falsify(self.out)
        self.save("falsifier", decisions=[])
        self.assertIsNone(lr.check_calls(self.out, self.packet)[0])
        manifest = self.calls()
        self.assertEqual(lr.check_calls(self.out, self.packet)[1], [])
        for field, value in (("host_call_id", ""), ("started_at", None),
                             ("fresh_context", False), ("effective_tool_policy", ""),
                             ("response_sha256", "wrong"), ("outcome", "failed")):
            broken = copy.deepcopy(manifest)
            broken["calls"][0][field] = value
            (self.out / "calls.json").write_text(lr.encoded(broken))
            self.assertIsNone(lr.check_calls(self.out, self.packet)[0], field)
        result = lr.finalize(self.out)
        self.assertEqual(result["status"], "partial")
        self.assertEqual(result["call_record_status"], "provenance_unverified")

    def test_duplicate_context_and_early_falsifier_rejected(self):
        for lane in lr.LANES:
            self.save(lane, candidates=[])
        lr.falsify(self.out)
        self.save("falsifier", decisions=[])
        manifest = self.calls()
        manifest["calls"][1]["host_call_id"] = manifest["calls"][0]["host_call_id"]
        (self.out / "calls.json").write_text(lr.encoded(manifest))
        self.assertIsNone(lr.check_calls(self.out, self.packet)[0])
        manifest["calls"][1]["host_call_id"] = "different"
        manifest["calls"][0]["ended_at"] += 1
        (self.out / "calls.json").write_text(lr.encoded(manifest))
        self.assertIsNone(lr.check_calls(self.out, self.packet)[0])

    def test_run_artifacts_are_private_under_permissive_umask(self):
        old = os.umask(0)
        try:
            out = self.root / "private"
            lr.prepare(self.source, lr.digest(self.source.read_bytes()), out)
        finally:
            os.umask(old)
        self.assertEqual(out.stat().st_mode & 0o777, 0o700)
        for item in out.iterdir():
            self.assertEqual(item.stat().st_mode & 0o777, 0o600)

    def test_timing_mutation_is_integrity_failure(self):
        for field in ("started_at", "deadline_at"):
            packet = copy.deepcopy(self.packet)
            packet[field] = self.packet["started_at"] + 3600
            (self.out / "packet.json").write_text(lr.encoded(packet))
            with self.assertRaisesRegex(ValueError, "integrity"):
                lr.load_packet(self.out)

    def test_invalid_timing_values_rejected_even_with_recomputed_hash(self):
        for value in (float("nan"), float("inf"), True, -1):
            packet = copy.deepcopy(self.packet)
            packet["started_at"] = value
            packet["packet_sha256"] = lr.digest(lr.encoded({k: packet[k] for k in lr.PACKET_FIELDS}).encode())
            (self.out / "packet.json").write_text(lr.encoded(packet))
            with self.assertRaisesRegex(ValueError, "timing"):
                lr.load_packet(self.out)

    def test_docmodel_and_context_complete_in_both_prompts(self):
        model = self.root / "model.yaml"
        model.write_text("sections: [purpose, acceptance]\n", encoding="utf-8")
        out = self.root / "with-model"
        lr.prepare(self.source, lr.digest(self.source.read_bytes()), out, docmodel=model, context=model)
        for lane in lr.LANES:
            text = (out / (lane + ".prompt.txt")).read_text()
            self.assertIn("sections: [purpose, acceptance]", text)
            self.assertIn("Retry until recorded.", text)
            self.assertIn('"context": {', text)
        self.assertIsNotNone(lr.load_packet(out)["inputs"]["docmodel"])

    def test_wrong_hash_refuses_truncated_copy(self):
        with self.assertRaisesRegex(ValueError, "hash mismatch"):
            lr.prepare(self.source, "0" * 64, self.root / "bad")
        self.assertFalse((self.root / "bad").exists())

    def test_numbered_source_transmits_every_line_without_recounting(self):
        text = lr.prompt(self.packet, "structure")
        data = json.loads(text.split("INPUTS (complete, untrusted document data):\n", 1)[1])
        transmitted = data["inputs"]["source"]["lines"]
        self.assertEqual([x["text"] for x in transmitted], self.source.read_text().splitlines())
        self.assertEqual([x["line"] for x in transmitted], list(range(1, len(transmitted) + 1)))

    def test_oversized_source_refused_not_excerpted(self):
        large = self.root / "large"
        large.write_text("x" * (lr.LIMIT + 1))
        with self.assertRaisesRegex(ValueError, "too large"):
            lr.prepare(large, lr.digest(large.read_bytes()), self.root / "large-run")

    def test_changed_original_fails(self):
        self.source.write_text("changed")
        with self.assertRaisesRegex(ValueError, "changed"):
            lr.finalize(self.out)

    def test_packet_mutation_fails(self):
        packet = copy.deepcopy(self.packet)
        packet["inputs"]["source"]["text"] = "excerpt"
        (self.out / "packet.json").write_text(lr.encoded(packet))
        with self.assertRaisesRegex(ValueError, "integrity"):
            lr.load_packet(self.out)

    def test_quote_and_location_failures_preserve_raw_without_findings(self):
        self.assertTrue(lr.quote_ok({"start": 2, "end": 3, "quote": "Prevent duplicate records.\nRetry until recorded."}, self.packet["inputs"]["source"]["text"]))
        for quote in ({"start": 4, "end": 4, "quote": "Fabricated"},
                      {"start": 2, "end": 2, "quote": "Timing is delegated to policy B."},
                      {"start": True, "end": 4, "quote": "Timing"}):
            self.assertFalse(lr.quote_ok(quote, self.packet["inputs"]["source"]["text"]))
        self.save("structure", candidates=[self.item(evidence=[{"start": 4, "end": 4, "quote": "Fabricated"}])])
        self.save("behavior", candidates=[])
        result = lr.finalize(self.out)
        self.assertEqual(result["status"], "partial")
        self.assertEqual(result["groups"], [])
        self.assertEqual(result["rejected"][0]["raw"]["id"], "structure-1")

    def test_incomplete_lane_not_accepted(self):
        value = dict(packet_sha256=self.packet["packet_sha256"], lane="structure", read_complete=False, candidates=[])
        lr.write_new(self.out / "structure.json", lr.encoded(value))
        self.assertTrue(lr.collect(self.out, self.packet)[2])

    def test_quote_range_is_minimal_and_duplicate_occurrences_disambiguated(self):
        source = "alpha\nbeta\ngamma\nbeta"
        for start, end in ((1, 4), (1, 3), (2, 4)):
            self.assertFalse(lr.quote_ok(dict(start=start, end=end, quote="beta"), source))
        for n in (2, 4):
            self.assertTrue(lr.quote_ok(dict(start=n, end=n, quote="beta"), source))
        self.assertTrue(lr.quote_ok(dict(start=2, end=3, quote="eta\ngam"), source))
        self.assertFalse(lr.quote_ok(dict(start=2, end=4, quote="eta\ngam"), source))

    def test_different_behaviors_never_merge(self):
        items = [self.item(), self.item("behavior", scenario="Different user-observable behavior")]
        result = lr.assemble(self.packet, items, [], [], None)
        self.assertEqual(len(result["groups"]), 2)
        self.assertEqual(result["groups"][0]["members"][0]["status"], "unconfirmed")

    def test_shared_evidence_places_related_claims_together_without_merging(self):
        items = [self.item(), self.item("behavior", scenario="Different behavior", problem="Different wording")]
        result = lr.assemble(self.packet, items, [], [], None)
        self.assertEqual(len(result["groups"]), 2)
        clusters = lr.evidence_clusters(result["groups"])
        self.assertEqual(len(clusters), 1)
        self.assertEqual(len(clusters[0]), 2)
        output = html.unescape(lr.render(result))
        self.assertIn("structure-1", output)
        self.assertIn("behavior-1", output)
        self.assertIn("Different behavior", output)
        self.assertIn("Different wording", output)

    def test_exclusion_requires_resolution_and_preserves_original(self):
        item = self.item(problem="Retry termination is missing", evidence=[{"start": 3, "end": 3, "quote": "Retry until recorded."}])
        decision = dict(id=item["id"], disposition="exclude", reason="Source defines termination", resolution=None)
        result = lr.assemble(self.packet, [item], [], [], {"decisions": [decision]})
        self.assertEqual(len(result["groups"]), 1)
        self.assertEqual(result["status"], "partial")
        decision["resolution"] = item["evidence"][0]
        result = lr.assemble(self.packet, [item], [], [], {"decisions": [decision]})
        self.assertEqual(result["excluded"][0]["candidate"], item)
        self.assertEqual(result["groups"], [])

    def test_falsifier_cannot_rewrite_claim(self):
        item = self.item()
        decision = dict(id=item["id"], disposition="keep", reason="keep", resolution=None, problem="new claim")
        result = lr.assemble(self.packet, [item], [], [], {"decisions": [decision]})
        self.assertEqual(result["groups"][0]["members"][0]["candidate"], item)
        self.assertEqual(result["status"], "partial")

    def test_malformed_falsifier_is_partial_not_a_crash(self):
        item = self.item()
        decision = dict(id=item["id"], disposition=[], reason="malformed model response", resolution=None)
        result = lr.assemble(self.packet, [item], [], [], {"decisions": [decision]})
        self.assertEqual(result["status"], "partial")
        self.assertEqual(result["groups"][0]["members"][0]["status"], "unconfirmed")

    def test_long_runs_complete_including_legacy_deadline_packets(self):
        for legacy in (False, True):
            with self.subTest(legacy=legacy):
                self.out = self.root / ("legacy" if legacy else "unlimited")
                self.packet = lr.prepare(self.source, lr.digest(self.source.read_bytes()), self.out)
                if legacy:
                    # Old packets predate observation journals. Remove only these
                    # generated fixture views before constructing the legacy input.
                    (self.out / "events.jsonl").unlink()
                    (self.out / "timeline.md").unlink()
                    self.packet["deadline_at"] = self.packet["started_at"] + 600
                    self.packet["packet_sha256"] = lr.digest(lr.encoded(
                        {k: self.packet[k] for k in lr.PACKET_FIELDS}).encode())
                    (self.out / "packet.json").write_text(lr.encoded(self.packet))
                    for lane in lr.LANES:
                        (self.out / (lane + ".prompt.txt")).write_text(lr.prompt(self.packet, lane) + "\n")
                for lane in lr.LANES:
                    self.save(lane, candidates=[self.item(lane)])
                late = self.packet["started_at"] + 86400
                with patch.object(lr.time, "time", return_value=late):
                    lr.falsify(self.out)
                    self.save("falsifier", decisions=[dict(id=lane + "-1", disposition="question",
                        reason="External policy not supplied", resolution=None) for lane in lr.LANES])
                    manifest = self.calls()
                    for index, call in enumerate(manifest["calls"]):
                        call["started_at"] = late - 30 + index * 10
                        call["ended_at"] = late - 25 + index * 10
                    (self.out / "calls.json").write_text(lr.encoded(manifest))
                    result = lr.finalize(self.out)
                self.assertEqual(result["status"], "completed")
                self.assertEqual(result["call_record_status"], "consistent_caller_record")
                self.assertFalse(result["document_approved"])

    def test_falsifier_failure_still_delivers_partial_after_long_wait(self):
        self.save("structure", candidates=[self.item()])
        self.save("behavior", candidates=[])
        with patch.object(lr.time, "time", return_value=self.packet["started_at"] + 86400):
            lr.falsify(self.out)
            result = lr.finalize(self.out)
        self.assertEqual(result["status"], "partial")
        self.assertTrue(result["groups"])
        self.assertIn("falsifier unavailable", " ".join(result["errors"]))

    def test_existing_outputs_not_overwritten(self):
        lr.finalize(self.out)
        before = (self.out / "result.json").read_bytes()
        with self.assertRaises(FileExistsError):
            lr.finalize(self.out)
        self.assertEqual(before, (self.out / "result.json").read_bytes())

    def _completed_result_with_candidate(self):
        for lane in lr.LANES:
            self.save(lane, candidates=[self.item(lane)])
        lr.falsify(self.out)
        self.save("falsifier", decisions=[dict(id=lane + "-1", disposition="question",
                  reason="Still bounded", resolution=None) for lane in lr.LANES])
        self.calls()
        return lr.finalize(self.out)

    def _assessment(self, result, **changes):
        entries = []
        for candidate_id in lr._result_candidate_ids(result):
            entry = dict(candidate_id=candidate_id,
                         candidate_assessment="known_error",
                         proposal_assessment="known_error",
                         evidence_coverage="external_source_unavailable",
                         rationale="원문 대조에서 이 후보와 제안의 오류를 확인했다.")
            entry.update(changes)
            entries.append(entry)
        value = dict(schema_version=1, packet_sha256=result["packet_sha256"],
                     result_sha256=lr.digest((self.out / "result.json").read_bytes()),
                     annotations=entries)
        path = self.root / "assessment.json"
        path.write_text(lr.encoded(value), encoding="utf-8")
        return path

    def test_report_labels_known_errors_adjacent_and_preserves_original(self):
        result = self._completed_result_with_candidate()
        original_result = (self.out / "result.json").read_bytes()
        assessment = self._assessment(result)
        report_dir = self.root / "derived"
        derived = lr.report(self.out, assessment, report_dir)
        text = html.unescape((report_dir / "result.md").read_text(encoding="utf-8"))
        self.assertEqual(derived["status"], result["status"])
        self.assertEqual(derived["quality_assessment"]["semantic_status"], "known_errors_disclosed")
        self.assertIn("확인된 오류입니다", text)
        self.assertIn("수정안으로 사용하지 마십시오", text)
        self.assertIn("외부 원문은 제공되지 않았습니다", text)
        self.assertLess(text.index("확인된 오류입니다"), text.index("Which policy timing applies?"))
        self.assertEqual(original_result, (self.out / "result.json").read_bytes())
        self.assertTrue(derived["quality_assessment"]["caller_authored"])
        self.assertFalse(derived["quality_assessment"]["automated_error_detection"])

    def test_report_fails_closed_for_stale_unknown_duplicate_and_invalid_assessment(self):
        result = self._completed_result_with_candidate()
        assessment = self._assessment(result)
        base = json.loads(assessment.read_text(encoding="utf-8"))
        cases = []
        stale = copy.deepcopy(base)
        stale["result_sha256"] = "0" * 64
        cases.append(stale)
        unknown = copy.deepcopy(base)
        unknown["annotations"][0]["candidate_id"] = "unknown-1"
        cases.append(unknown)
        duplicate = copy.deepcopy(base)
        duplicate["annotations"].append(copy.deepcopy(duplicate["annotations"][0]))
        cases.append(duplicate)
        invalid = copy.deepcopy(base)
        invalid["annotations"][0]["candidate_assessment"] = "approved"
        cases.append(invalid)
        for index, value in enumerate(cases):
            path = self.root / ("bad-%d.json" % index)
            path.write_text(lr.encoded(value), encoding="utf-8")
            with self.assertRaises(ValueError):
                lr.report(self.out, path, self.root / ("derived-bad-%d" % index))

    def test_report_preserves_partial_status_and_supports_unresolved_supplied_annotation(self):
        result = self._completed_result_with_candidate()
        result_path = self.out / "result.json"
        original = result_path.read_bytes()
        partial = json.loads(original.decode("utf-8"))
        partial["status"] = "partial"
        result_path.write_bytes(lr.encoded(partial).encode("utf-8") + b"\n")
        assessment = self._assessment(partial, candidate_assessment="evaluated_unresolved",
                                       proposal_assessment="unverified_proposal",
                                       evidence_coverage="supplied_source_evaluated",
                                       rationale="<unresolved> & remains under review")
        report_dir = self.root / "derived-partial"
        derived = lr.report(self.out, assessment, report_dir)
        self.assertEqual(derived["status"], "partial")
        self.assertEqual(derived["quality_assessment"]["semantic_status"], "unverified")
        text = html.unescape((report_dir / "result.md").read_text(encoding="utf-8"))
        self.assertIn("제공된 원문은 평가했지만", text)
        self.assertIn("&#38;", (report_dir / "result.md").read_text(encoding="utf-8"))
        with self.assertRaises(FileExistsError):
            lr.report(self.out, assessment, report_dir)

    def test_report_allows_unannotated_candidates_without_default(self):
        result = self._completed_result_with_candidate()
        value = dict(schema_version=1, packet_sha256=result["packet_sha256"],
                     result_sha256=lr.digest((self.out / "result.json").read_bytes()), annotations=[])
        path = self.root / "empty-assessment.json"
        path.write_text(lr.encoded(value), encoding="utf-8")
        derived = lr.report(self.out, path, self.root / "derived-empty")
        self.assertEqual(derived["quality_assessment"]["annotations"], {})
        self.assertEqual(derived["quality_assessment"]["semantic_status"], "unverified")
        self.assertIn("의미 평가 상태: unverified", (self.root / "derived-empty" / "result.md").read_text())

    def test_report_discloses_excluded_candidate_notes_without_restoring_it(self):
        item = self.item()
        decision = dict(id=item["id"], disposition="exclude", reason="Model exclusion",
                        resolution=item["evidence"][0])
        result = lr.assemble(self.packet, [item], [], [], {"decisions": [decision]})
        lr.write_new(self.out / "result.json", lr.encoded(result))
        assessment = self._assessment(result, rationale="Preserve <raw> judgment; proposal is unsafe.")
        derived = lr.report(self.out, assessment, self.root / "excluded-report")
        text = (self.root / "excluded-report/result.md").read_text(encoding="utf-8")
        self.assertEqual(derived["excluded"], result["excluded"])
        self.assertEqual(derived["groups"], [])
        self.assertIn("수정안으로 사용하지 마십시오", text)
        self.assertIn("Preserve", text)
        self.assertNotIn("<raw>", text)
        self.assertLess(text.index("평가 메모"), text.index("```json"))

    def test_report_rejects_original_approval_or_forged_runtime_state(self):
        result = self._completed_result_with_candidate()
        for changes in ({"document_approved": True}, {"status": "approved"}):
            invalid = dict(result, **changes)
            (self.out / "result.json").write_text(lr.encoded(invalid), encoding="utf-8")
            assessment = self._assessment(invalid)
            with self.assertRaisesRegex(ValueError, "status or approval"):
                lr.report(self.out, assessment, self.root / "invalid-report")


class LightReviewCliTests(unittest.TestCase):
    """Public staged CLI contracts using synthetic caller evidence, no model calls."""

    def setUp(self):
        self.fixture = LightReviewTests()
        self.fixture.setUp()
        self.root = self.fixture.root
        self.out = self.fixture.out

    def cli(self, command, *args, out=None):
        return subprocess.run(
            ["bash", str(ROOT / "bin/docloop"), "light-review", command,
             *map(str, args), "--out", str(out or self.out)],
            cwd=self.root, capture_output=True, text=True,
            env={**os.environ, "DOCLOOP_MODEL": "no-model-may-run"},
        )

    def assert_exit(self, proc, expected):
        self.assertEqual(proc.returncode, expected, proc.stdout + proc.stderr)

    def prepare_lanes(self):
        for lane in lr.LANES:
            self.fixture.save(lane, candidates=[self.fixture.item(lane)])
        self.assert_exit(self.cli("falsify"), 0)
        self.fixture.save("falsifier", decisions=[
            dict(id=lane + "-1", disposition="question",
                 reason="Synthetic unresolved external policy", resolution=None)
            for lane in lr.LANES
        ])

    def test_staged_cli_roundtrip_preserves_questions_and_report_binding(self):
        source = self.root / "author's source file.md"
        source.write_bytes(self.fixture.source.read_bytes())
        self.out = self.root / "reviewer's run folder"
        self.assert_exit(self.cli("prepare", source, "--source-sha256",
                                  lr.digest(source.read_bytes())), 0)
        self.fixture.out = self.out
        self.fixture.packet = lr.load_packet(self.out)
        self.assertFalse((self.out / "calls.json").exists())
        self.prepare_lanes()
        self.fixture.calls()
        self.assert_exit(self.cli("finalize"), 0)
        result_bytes = (self.out / "result.json").read_bytes()
        result = json.loads(result_bytes)
        self.assertEqual(result["status"], "completed")
        self.assertFalse(result["document_approved"])
        self.assertEqual(sum(len(g["members"]) for g in result["groups"]), 2)
        assessment = self.fixture._assessment(result)
        report_dir = self.root / "reader's report"
        self.assert_exit(self.cli("report", "--assessment", assessment,
                                  "--report-dir", report_dir), 0)
        self.assertEqual((self.out / "result.json").read_bytes(), result_bytes)
        report = json.loads((report_dir / "result.json").read_text())
        self.assertEqual(report["status"], "completed")
        self.assertFalse(report["document_approved"])
        self.assertTrue((report_dir / "result.md").is_file())
        self.assert_exit(self.cli("timeline"), 0)
        self.assertTrue((self.out / "timeline.md").is_file())

    def test_missing_calls_returns_partial_and_preserves_candidates(self):
        self.prepare_lanes()
        self.assert_exit(self.cli("finalize"), 3)
        result = json.loads((self.out / "result.json").read_text())
        self.assertEqual(result["status"], "partial")
        self.assertEqual(result["call_record_status"], "provenance_unverified")
        self.assertEqual(sum(len(g["members"]) for g in result["groups"]), 2)
        self.assertFalse(result["document_approved"])
        self.assertFalse((self.out / "calls.json").exists())

    def test_invalid_calls_return_partial_instead_of_success(self):
        for corruption in ("duplicate-lane", "response-hash", "failed-call"):
            with self.subTest(corruption=corruption):
                self.setUp()
                self.prepare_lanes()
                calls = self.fixture.calls()
                if corruption == "duplicate-lane":
                    calls["calls"].append(dict(calls["calls"][0]))
                elif corruption == "response-hash":
                    calls["calls"][0]["response_sha256"] = "0" * 64
                else:
                    calls["calls"][0]["outcome"] = "failed"
                (self.out / "calls.json").write_text(lr.encoded(calls))
                self.assert_exit(self.cli("finalize"), 3)
                result = json.loads((self.out / "result.json").read_text())
                self.assertEqual(result["call_record_status"], "provenance_unverified")
                self.assertEqual(sum(len(g["members"]) for g in result["groups"]), 2)

    def test_prepare_wrong_hash_does_not_create_run(self):
        out = self.root / "wrong-hash"
        self.assert_exit(self.cli("prepare", self.fixture.source,
                                  "--source-sha256", "0" * 64, out=out), 2)
        self.assertFalse(out.exists())

    def test_prepare_oversized_source_does_not_create_run(self):
        source = self.root / "oversized.md"
        source.write_text("x" * 120001)
        out = self.root / "oversized-run"
        self.assert_exit(self.cli("prepare", source, "--source-sha256",
                                  lr.digest(source.read_bytes()), out=out), 2)
        self.assertFalse(out.exists())

    def test_prepare_existing_run_preserves_all_file_bytes(self):
        before = {p.relative_to(self.out): p.read_bytes()
                  for p in self.out.rglob("*") if p.is_file()}
        self.assert_exit(self.cli("prepare", self.fixture.source, "--source-sha256",
                                  lr.digest(self.fixture.source.read_bytes())), 2)
        after = {p.relative_to(self.out): p.read_bytes()
                 for p in self.out.rglob("*") if p.is_file()}
        self.assertEqual(after, before)


if __name__ == "__main__":
    unittest.main()
