#!/usr/bin/env python3
"""Authoring CLI transport and review staging contracts; no live model calls.

Semantic requirement preservation remains a prompt/example review obligation.
A capture stub proves request transport, not editing quality or model confinement.
"""
from pathlib import Path
import json
import os
import shlex
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
BIN = ROOT / "bin/docloop"


class HumanEditTransport(unittest.TestCase):
    def test_request_and_prompt_reach_each_configured_model_without_shell_expansion(self):
        for model in ("codex", "claude"):
            with self.subTest(model=model), tempfile.TemporaryDirectory() as td:
                root = Path(td).resolve()
                work = root / "author's work folder"
                work.mkdir()
                doc = work / "owner's draft.md"
                doc.write_text("Original requirement.\n", encoding="utf-8")
                (work / "manifest.yaml").write_text("project: {product: synthetic}\n")
                stub_dir = root / "stubs"
                stub_dir.mkdir()
                capture = root / "captured.json"
                stub = stub_dir / model
                stub.write_text(
                    f"#!{sys.executable}\n"
                    "import json, os, pathlib, sys\n"
                    "pathlib.Path(os.environ['AUTHORING_CAPTURE']).write_text("
                    "json.dumps({'argv': sys.argv[1:], 'cwd': os.getcwd()}))\n",
                    encoding="utf-8",
                )
                stub.chmod(0o700)
                request = [str(doc), 'Keep "quoted text" and exceptions',
                           "literal $(touch SHOULD_NOT_EXIST) and `touch ALSO_NOT_CREATED`"]
                proc = subprocess.run(
                    ["bash", str(BIN), "human-edit", *request], cwd=work,
                    env={**os.environ, "DOCLOOP_MODEL": model,
                         "PATH": str(stub_dir) + os.pathsep + os.environ["PATH"],
                         "AUTHORING_CAPTURE": str(capture)},
                    capture_output=True, text=True,
                )
                self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
                observed = json.loads(capture.read_text())
                expected_prompt = (ROOT / "prompts/human-edit.md").read_text()
                expected_prompt += "\n\n---\n## Run context\n"
                expected_prompt += f"- Work folder: {work}\n"
                expected_prompt += f"- docloop lib (scripts): {ROOT / 'lib'}\n"
                expected_prompt += f"- Manifest: {work / 'manifest.yaml'}\n"
                expected_prompt += "- User request: " + " ".join(request) + "\n"
                expected_args = (["exec", "--skip-git-repo-check"] if model == "codex" else ["-p"])
                self.assertEqual(observed["argv"], expected_args + [expected_prompt.rstrip("\n")])
                self.assertEqual(Path(observed["cwd"]), work)
                self.assertEqual(doc.read_text(), "Original requirement.\n")
                self.assertFalse((work / "SHOULD_NOT_EXIST").exists())
                self.assertFalse((work / "ALSO_NOT_CREATED").exists())


class PeerReviewStaging(unittest.TestCase):
    def stage(self, work, dest, name, source):
        return subprocess.run(
            ["bash", str(BIN), "review", name, str(source)], cwd=work,
            env={**os.environ, "DOCLOOP_REVIEW_DIR": str(dest),
                 "DOCLOOP_MODEL": "no-model-may-run"},
            capture_output=True, text=True,
        )

    def command_tokens(self, proc):
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
        command = next(line.strip()[3:] for line in proc.stdout.splitlines()
                       if line.strip().startswith("2) cd "))
        return shlex.split(command)

    def test_quoted_review_path_and_high_effort_survive_generated_shell_argv(self):
        with tempfile.TemporaryDirectory() as td:
            work = Path(td).resolve()
            dest = work / "reviewer's folders"
            source = work / "author's source.md"
            source.write_text("Review this copy.\n")
            name = "release owner's review"
            proc = self.stage(work, dest, name, source)
            tokens = self.command_tokens(proc)
            self.assertEqual(tokens, ["cd", str(dest / name), "&&", "codex", "exec",
                                     "--skip-git-repo-check", "--sandbox", "read-only",
                                     "-c", "model_reasoning_effort=high", "-", ">", "REVIEW_r1.md"])
            self.assertEqual((dest / name / source.name).read_bytes(), source.read_bytes())
            self.assertIn("observed_reasoning_effort", proc.stdout)
            self.assertIn("unknown", proc.stdout)

    def test_existing_brief_and_lens_artifacts_survive_restage(self):
        with tempfile.TemporaryDirectory() as td:
            work = Path(td).resolve()
            dest = work / "review root"
            source = work / "draft.md"
            source.write_text("Version one.\n")
            name = "fixture"
            self.command_tokens(self.stage(work, dest, name, source))
            review = dest / name
            brief = review / "REVIEW_BRIEF.md"
            original = b"Human-owned brief, no new observation inferred.\n"
            brief.write_bytes(original)
            lens = review / "L2_REVIEW_r1.md.log"
            lens.write_text("Existing lens evidence.\n")
            source.write_text("Version two.\n")
            proc = self.stage(work, dest, name, source)
            tokens = self.command_tokens(proc)
            self.assertEqual(tokens[-1], "REVIEW_r2.md")
            self.assertEqual(brief.read_bytes(), original)
            self.assertEqual(lens.read_text(), "Existing lens evidence.\n")
            self.assertEqual((review / source.name).read_bytes(), source.read_bytes())
            self.assertIn("kept existing", proc.stdout)
            self.assertIn("observed_reasoning_effort", proc.stdout)


if __name__ == "__main__":
    unittest.main()
