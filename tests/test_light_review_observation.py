"""Observable journal/feedback regressions; synthetic calls are not live review proof."""
import json
import html
from pathlib import Path
import subprocess
import unittest

import test_light_review as fixtures
lr, ROOT = fixtures.lr, fixtures.ROOT


class ObservationTests(unittest.TestCase):
    def setUp(self):
        self.fixture = fixtures.LightReviewTests()
        self.fixture.setUp()
        self.out = self.fixture.out
        self.root = self.fixture.root
        self.packet = self.fixture.packet
        self.seq = 0

    def cli(self, command, record=None):
        args = ["bash", str(ROOT / "bin/docloop"), "light-review", command, "--out", str(self.out)]
        if record is not None:
            self.seq += 1
            p = self.root / f'request-{self.seq}.json'
            p.write_text(json.dumps(record))
            args += ['--record', str(p)]
        return subprocess.run(args, capture_output=True, text=True)

    def complete(self, empty=False):
        for lane in lr.LANES:
            self.fixture.save(lane, candidates=[] if empty else [self.fixture.item(lane)])
        lr.falsify(self.out)
        self.fixture.save('falsifier', decisions=[] if empty else [dict(id=lane+'-1', disposition='question', reason='Missing external policy', resolution=None) for lane in lr.LANES])
        self.fixture.calls()
        return lr.finalize(self.out)

    def feedback(self, **updates):
        value = dict(schema_version=1, feedback_id='feedback-1', packet_sha256=self.packet['packet_sha256'], result_sha256=lr.digest((self.out/'result.json').read_bytes()), candidate_ids=['structure-1'], question_id='question-1', question_text='Which timing applies?', answer_text='Keep the existing policy; do not add a new requirement.', answer_source='synthetic fixture, not a real user', received_at=None, interpretation='Existing policy clarification; no document change verified.', interpretation_confirmed=False, change_status='not_applied', change_refs=[], supersedes=None)
        value.update(updates)
        return value

    def test_feedback_timeline_preserves_original_and_correction(self):
        self.complete()
        before = {p: (self.out/p).read_bytes() for p in ['result.json','result.md','structure.json','behavior.json','falsifier.json']}
        self.assertEqual(self.cli('feedback', self.feedback()).returncode, 0)
        self.assertEqual(self.cli('feedback', self.feedback(feedback_id='feedback-2', answer_text='Correction: use the linked policy.', supersedes='feedback-1')).returncode, 0)
        self.assertEqual(self.cli('timeline').returncode, 0)
        timeline = (self.out/'timeline.md').read_text()
        self.assertIn('Keep the existing policy', timeline)
        self.assertIn('Correction: use the linked policy', html.unescape(timeline))
        for p, raw in before.items(): self.assertEqual((self.out/p).read_bytes(), raw)
        self.assertTrue((self.out/'feedback'/'feedback-1.json').is_file())
        self.assertTrue((self.out/'feedback'/'feedback-2.json').is_file())

    def test_rejects_cross_run_stale_unknown_duplicate_and_traversal(self):
        self.complete()
        for updates in [dict(packet_sha256='0'*64), dict(result_sha256='0'*64), dict(candidate_ids=['missing-1']), dict(feedback_id='../escape'), dict(supersedes='missing-feedback')]:
            with self.subTest(updates=updates): self.assertNotEqual(self.cli('feedback', self.feedback(**updates)).returncode, 0)
        self.assertEqual(self.cli('feedback', self.feedback()).returncode, 0)
        raw = (self.out/'feedback'/'feedback-1.json').read_bytes()
        self.assertNotEqual(self.cli('feedback', self.feedback(answer_text='overwrite')).returncode, 0)
        self.assertEqual((self.out/'feedback'/'feedback-1.json').read_bytes(), raw)

    def test_cli_stage_exception_is_recorded_as_failure(self):
        self.assertEqual(self.cli('falsify').returncode, 3)
        before = len((self.out/'events.jsonl').read_text().splitlines())
        self.assertEqual(self.cli('falsify').returncode, 2)
        events = [json.loads(x) for x in (self.out/'events.jsonl').read_text().splitlines()]
        self.assertEqual(len(events), before+1)
        self.assertEqual(events[-1]['event_type'], 'stage_failed')
        self.assertEqual(events[-1]['outcome'], 'failed')

    def test_failed_final_markdown_never_logs_completion(self):
        for lane in lr.LANES:
            self.fixture.save(lane, candidates=[])
        lr.falsify(self.out)
        self.fixture.save('falsifier', decisions=[])
        self.fixture.calls()
        (self.out/'result.md').write_text('EXISTING')
        self.assertEqual(self.cli('finalize').returncode, 2)
        events = [json.loads(x) for x in (self.out/'events.jsonl').read_text().splitlines()]
        self.assertFalse(any(x['stage']=='finalize' and x['event_type']=='stage_completed' for x in events))
        self.assertEqual(events[-1]['event_type'], 'stage_failed')
        self.assertEqual((self.out/'result.md').read_text(), 'EXISTING')

    def test_unrecordable_failure_is_disclosed(self):
        self.fixture.source.write_text('changed')
        result = self.cli('finalize')
        self.assertEqual(result.returncode, 2)
        self.assertIn('observation recording failed', result.stderr)

    def test_empty_host_id_and_multiline_feedback_boundaries(self):
        event = dict(event_id='empty-host', packet_sha256=self.packet['packet_sha256'], event_type='call_started', stage='structure', host_call_id='', outcome='running', evidence_ref='synthetic')
        self.assertNotEqual(self.cli('event', event).returncode, 0)
        self.complete()
        answer = 'hello\n## INJECT\n[evil](javascript:alert(1))'
        self.assertEqual(self.cli('feedback', self.feedback(answer_text=answer)).returncode, 0)
        self.assertNotIn('\n## INJECT', (self.out/'timeline.md').read_text())
        self.assertEqual(json.loads((self.out/'feedback/feedback-1.json').read_text())['answer_text'], answer)

    def test_timeline_discloses_orphan_and_conflicting_call_record(self):
        self.complete()
        event = dict(event_id='orphan', packet_sha256=self.packet['packet_sha256'], event_type='call_completed', stage='structure', host_call_id='different-host', outcome='completed', evidence_ref='synthetic')
        self.assertEqual(self.cli('event', event).returncode, 0)
        view = html.unescape((self.out/'timeline.md').read_text())
        self.assertIn('orphan terminal event', view)
        self.assertIn('calls.json mismatch', view)
        self.assertIn('caller observations; not verified execution', view)

    def test_refresh_failure_does_not_append_conflicting_stage_outcomes(self):
        for lane in lr.LANES:
            self.fixture.save(lane, candidates=[])
        lr.falsify(self.out)
        self.fixture.save('falsifier', decisions=[])
        self.fixture.calls()
        target = self.root/'preserved-view'; target.write_text('KEEP')
        (self.out/'timeline.md').unlink(); (self.out/'timeline.md').symlink_to(target)
        response = self.cli('finalize')
        self.assertEqual(response.returncode, 0)
        self.assertIn('timeline refresh failed', response.stderr)
        records = [json.loads(x) for x in (self.out/'events.jsonl').read_text().splitlines()]
        self.assertEqual([x['event_type'] for x in records if x['stage']=='finalize'], ['stage_completed'])
        self.assertEqual(target.read_text(), 'KEEP')

    def test_falsify_exposes_rejected_candidates_and_partial_exit(self):
        invalid = self.fixture.item()
        invalid['evidence'][0]['quote'] = 'invented'
        self.fixture.save('structure', candidates=[invalid])
        self.fixture.save('behavior', candidates=[])
        result = self.cli('falsify')
        self.assertEqual(result.returncode, 3)
        output = json.loads(result.stdout)
        self.assertEqual(output['status'], 'partial')
        self.assertEqual(len(output['rejected']), 1)

    def test_call_mismatch_prevents_consistent_final_provenance(self):
        for lane in lr.LANES:
            self.fixture.save(lane, candidates=[])
        lr.falsify(self.out)
        self.fixture.save('falsifier', decisions=[])
        self.fixture.calls()
        event = dict(event_id='foreign-host', packet_sha256=self.packet['packet_sha256'], event_type='call_completed', stage='structure', host_call_id='other', outcome='completed', evidence_ref='synthetic')
        self.assertEqual(self.cli('event', event).returncode, 0)
        result = lr.finalize(self.out)
        self.assertEqual(result['status'], 'partial')
        self.assertEqual(result['call_record_status'], 'provenance_unverified')

    def test_renamed_feedback_is_rejected_instead_of_broken_link(self):
        self.complete()
        self.assertEqual(self.cli('feedback', self.feedback()).returncode, 0)
        (self.out/'feedback/feedback-1.json').rename(self.out/'feedback/renamed.json')
        self.assertNotEqual(self.cli('timeline').returncode, 0)

    def test_duplicate_terminal_calls_are_disclosed(self):
        self.complete()
        event = dict(event_id='start-1', packet_sha256=self.packet['packet_sha256'], event_type='call_started', stage='structure', host_call_id='synthetic-structure', outcome='running', evidence_ref='synthetic')
        self.assertEqual(self.cli('event', event).returncode, 0)
        event.update(event_id='end-1', event_type='call_completed', outcome='completed')
        self.assertEqual(self.cli('event', event).returncode, 0)
        event['event_id'] = 'end-2'
        self.assertEqual(self.cli('event', event).returncode, 0)
        self.assertIn('duplicate or conflicting terminal event', html.unescape((self.out/'timeline.md').read_text()))

    def test_empty_candidates_can_record_independent_question(self):
        self.complete(empty=True)
        self.assertEqual(self.cli('feedback', self.feedback(candidate_ids=[])).returncode, 0)
        self.assertEqual(self.cli('timeline').returncode, 0)
        self.assertEqual(json.loads((self.out/'result.json').read_text())['groups'], [])

    def test_call_events_and_failure_are_not_success_or_cross_run(self):
        base = dict(schema_version=1, event_id='call-1', packet_sha256=self.packet['packet_sha256'], event_type='call_started', stage='structure', host_call_id='/root/real-task-format', outcome='running', evidence_ref='synthetic caller evidence')
        self.assertEqual(self.cli('event', base).returncode, 0)
        self.assertNotEqual(self.cli('event', base).returncode, 0)
        wrong = dict(base, event_id='call-2', packet_sha256='0'*64)
        self.assertNotEqual(self.cli('event', wrong).returncode, 0)
        failed = dict(base, event_id='call-3', event_type='call_failed', outcome='failed')
        self.assertEqual(self.cli('event', failed).returncode, 0)
        records = [json.loads(x) for x in (self.out/'events.jsonl').read_text().splitlines()]
        self.assertFalse(any(x['event_type']=='call_completed' for x in records))
        self.assertEqual([x['outcome'] for x in records if x['stage']=='structure'], ['running','failed'])
        self.assertFalse((self.out/'result.json').exists())

    def test_feedback_requires_actual_change_reference_and_escapes_html(self):
        self.complete()
        self.assertNotEqual(self.cli('feedback', self.feedback(change_status='applied')).returncode, 0)
        self.assertEqual(self.cli('feedback', self.feedback(answer_text='<script>alert(1)</script>')).returncode, 0)
        self.assertNotIn('<script>', (self.out/'timeline.md').read_text())
        raw = json.loads((self.out/'feedback'/'feedback-1.json').read_text())
        self.assertEqual(raw['answer_text'], '<script>alert(1)</script>')
        self.assertFalse(raw['interpretation_confirmed'])
        self.assertNotEqual(self.cli('feedback', self.feedback(feedback_id='feedback-2', question_id='other-question', supersedes='feedback-1')).returncode, 0)

    def test_journal_corruption_and_symlink_do_not_overwrite_target(self):
        journal = self.out/'events.jsonl'
        journal.write_text('{broken\n')
        self.assertNotEqual(self.cli('timeline').returncode, 0)
        self.assertEqual(journal.read_text(), '{broken\n')
        # Independent run for a symlinked journal; never alter the target.
        other = self.root/'other'; lr.prepare(self.fixture.source, lr.digest(self.fixture.source.read_bytes()), other)
        target = self.root/'untouched'; target.write_text('KEEP')
        (other/'events.jsonl').unlink(); (other/'events.jsonl').symlink_to(target)
        self.out = other
        self.assertNotEqual(self.cli('timeline').returncode, 0)
        self.assertEqual(target.read_text(), 'KEEP')


if __name__ == '__main__':
    unittest.main()
