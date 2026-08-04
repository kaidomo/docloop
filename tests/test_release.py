#!/usr/bin/env python3
"""Focused release/version contract tests."""

from __future__ import annotations

import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

import yaml


ROOT = Path(__file__).resolve().parents[1]
BIN = ROOT / "bin" / "docloop"
CHECK_RELEASE = ROOT / "tools" / "check_release.py"

passed = failed = 0


def check(name: str, condition: bool) -> None:
    global passed, failed
    if condition:
        passed += 1
        print(f"ok   {name}")
    else:
        failed += 1
        print(f"FAIL {name}")


def run(*args: str, cwd: Path | None = None) -> subprocess.CompletedProcess[str]:
    return subprocess.run(args, cwd=cwd, capture_output=True, text=True)


def run_validator(repo: Path, *args: str) -> subprocess.CompletedProcess[str]:
    return run(sys.executable, str(CHECK_RELEASE), "--repo-root", str(repo), *args)


def write_contract(repo: Path, version: bytes = b"0.13.0\n", changelog: str | None = None) -> None:
    repo.mkdir(parents=True, exist_ok=True)
    (repo / "VERSION").write_bytes(version)
    (repo / "CHANGELOG.md").write_text(
        changelog or "# Changelog\n\n## [0.13.0] — test\n",
        encoding="utf-8",
    )


def git(repo: Path, *args: str) -> subprocess.CompletedProcess[str]:
    return run("git", *args, cwd=repo)


def make_git_repo(*, tag_kind: str = "annotated", off_main: bool = False) -> Path:
    repo = Path(tempfile.mkdtemp(prefix="docloop-release-git-"))
    write_contract(repo)
    git(repo, "init", "-b", "main")
    git(repo, "config", "user.name", "Release Test")
    git(repo, "config", "user.email", "release-test@example.invalid")
    git(repo, "add", "VERSION", "CHANGELOG.md")
    git(repo, "commit", "-m", "base")
    if off_main:
        git(repo, "switch", "-c", "release-candidate")
        git(repo, "commit", "--allow-empty", "-m", "off main")
    if tag_kind == "annotated":
        git(repo, "tag", "-a", "v0.13.0", "-m", "v0.13.0")
    elif tag_kind == "lightweight":
        git(repo, "tag", "v0.13.0")
    if off_main:
        git(repo, "switch", "main")
    return repo


# Real checkout and CLI behavior.
current_version = (ROOT / "VERSION").read_text(encoding="ascii").strip()
for args in (("--version",), ("version",)):
    result = run(str(BIN), *args, cwd=Path(tempfile.gettempdir()))
    check(f"CLI {' '.join(args)} prints product version", result.returncode == 0 and result.stdout == f"docloop {current_version}\n")

help_result = run(str(BIN), "--help")
check("CLI help remains available and lists version", help_result.returncode == 0 and "docloop version" in help_result.stdout)
unknown = run(str(BIN), "definitely-unknown")
check("unknown command remains nonzero and actionable", unknown.returncode != 0 and "unknown command 'definitely-unknown'" in unknown.stderr)

real_validation = run_validator(ROOT)
check("real checkout passes tagless release validation", real_validation.returncode == 0 and f"VERSION {current_version}" in real_validation.stdout)

# VERSION and CHANGELOG format contract.
valid_versions = (b"0.13.0\n", b"0.13.0")
invalid_versions = (
    b"", b" 0.13.0\n", b"0.13.0 \n", b"v0.13.0\n", b"01.2.3\n",
    b"1.02.3\n", b"1.2.03\n", b"0.13\n", b"0.13.0-rc.1\n",
    b"0.13.0+build\n", b"0.13.0\n\n",
)
for index, raw in enumerate(valid_versions):
    with tempfile.TemporaryDirectory(prefix="docloop-release-version-") as tmp:
        repo = Path(tmp)
        write_contract(repo, raw)
        check(f"VERSION valid form {index + 1}", run_validator(repo).returncode == 0)
for index, raw in enumerate(invalid_versions):
    with tempfile.TemporaryDirectory(prefix="docloop-release-version-") as tmp:
        repo = Path(tmp)
        write_contract(repo, raw)
        result = run_validator(repo)
        check(f"VERSION invalid form {index + 1}", result.returncode != 0 and "VERSION" in result.stderr)

with tempfile.TemporaryDirectory(prefix="docloop-release-changelog-") as tmp:
    repo = Path(tmp)
    write_contract(repo, changelog="# Changelog\n\n## [0.12.0]\n\n## [0.13.0]\n")
    result = run_validator(repo)
    check("stale first CHANGELOG version is rejected", result.returncode != 0 and "CHANGELOG" in result.stderr and "0.12.0" in result.stderr)

# Git-aware checks are isolated from the project repository.
annotated = make_git_repo()
try:
    result = run_validator(annotated, "--tag", "v0.13.0", "--main-ref", "main")
    check("annotated matching on-main tag passes", result.returncode == 0 and "tag v0.13.0" in result.stdout and "signature=" in result.stdout)
    missing = run_validator(annotated, "--tag", "v0.13.1", "--main-ref", "main")
    check("mismatched tag fails before publication", missing.returncode != 0 and "tag" in missing.stderr and "VERSION" in missing.stderr)
    absent_main = run_validator(annotated, "--tag", "v0.13.0", "--main-ref", "origin/main")
    check("missing main ref gives retrieval guidance", absent_main.returncode != 0 and "origin/main" in absent_main.stderr and "fetch" in absent_main.stderr.lower())
finally:
    shutil.rmtree(annotated)

absent = make_git_repo(tag_kind="none")
try:
    result = run_validator(absent, "--tag", "v0.13.0", "--main-ref", "main")
    check("absent matching tag gives retrieval guidance", result.returncode != 0 and "tag object" in result.stderr and "fetch" in result.stderr.lower())
finally:
    shutil.rmtree(absent)

lightweight = make_git_repo(tag_kind="lightweight")
try:
    result = run_validator(lightweight, "--tag", "v0.13.0", "--main-ref", "main")
    check("lightweight tag is rejected", result.returncode != 0 and "annotated" in result.stderr.lower())
finally:
    shutil.rmtree(lightweight)

off_main = make_git_repo(off_main=True)
try:
    result = run_validator(off_main, "--tag", "v0.13.0", "--main-ref", "main")
    check("off-main annotated tag is rejected", result.returncode != 0 and "reachable" in result.stderr.lower())
finally:
    shutil.rmtree(off_main)

# Workflow permissions and publication shape.
ci_path = ROOT / ".github" / "workflows" / "ci.yml"
release_path = ROOT / ".github" / "workflows" / "release.yml"
if ci_path.exists() and release_path.exists():
    ci_raw = ci_path.read_text(encoding="utf-8")
    release_raw = release_path.read_text(encoding="utf-8")
    ci = yaml.safe_load(ci_raw)
    release = yaml.safe_load(release_raw)
    ci_on = ci.get("on", ci.get(True, {}))
    release_on = release.get("on", release.get(True, {}))
    check("CI triggers on PR and main push", "pull_request" in ci_on and ci_on.get("push", {}).get("branches") == ["main"])
    check("CI grants contents read only", ci.get("permissions") == {"contents": "read"})
    check("CI runs full suite and release validator", "python3 tests/run_tests.py" in ci_raw and "python3 tools/check_release.py" in ci_raw)
    check("release triggers only on v* tag pushes", release_on == {"push": {"tags": ["v*"]}})
    verify = release["jobs"]["verify"]
    publish = release["jobs"]["publish"]
    check("release verify has read-only contents", verify.get("permissions") == {"contents": "read"})
    check("publish depends on verify and alone has write", publish.get("needs") == "verify" and publish.get("permissions") == {"contents": "write"})
    check("release checkout fetches full history", "fetch-depth: 0" in release_raw and "ref: ${{ github.ref }}" in release_raw)
    check("release concurrency is tag-keyed and non-cancelling", release.get("concurrency", {}).get("group") == "release-${{ github.ref_name }}" and release.get("concurrency", {}).get("cancel-in-progress") is False)
    check("publish has explicit no-checkout GitHub context", "actions/checkout" not in str(publish) and publish.get("env") == {"GH_REPO": "${{ github.repository }}", "GH_TOKEN": "${{ github.token }}"})
    check("release creation verifies existing tag and generates notes", all(flag in release_raw for flag in ("gh release create \"$GITHUB_REF_NAME\"", "--title \"$GITHUB_REF_NAME\"", "--verify-tag", "--generate-notes", "--fail-on-no-commits")))
    check("release validation preserves failure status and summary diagnostics", "set -o pipefail" in release_raw and "2>&1 | tee -a \"$GITHUB_STEP_SUMMARY\"" in release_raw)
    check("first-party actions are SHA-pinned", all(value in ci_raw + release_raw for value in ("3d3c42e5aac5ba805825da76410c181273ba90b1", "5fda3b95a4ea91299a34e894583c3862153e4b97")))
    uses_lines = [line.strip() for line in (ci_raw + release_raw).splitlines() if line.strip().startswith("uses:")]
    check("workflows use no third-party actions", all("actions/checkout@" in line or "actions/setup-python@" in line for line in uses_lines))
    check("release publishes no package or redundant assets", all(term not in release_raw.lower() for term in ("upload-artifact", "checksum", "gh release upload", "twine", "npm publish")))
else:
    check("workflow files exist", False)

# Product VERSION must not absorb subsystem protocol versions.
contribution = (ROOT / "lib" / "contribution_flow.py").read_text(encoding="utf-8")
review_gate = (ROOT / "lib" / "review_gate" / "runner.py").read_text(encoding="utf-8")
check("subsystem TOOL_VERSION values remain independent", 'TOOL_VERSION = "0.11.0"' in contribution and 'TOOL_VERSION = "0.13.0"' in review_gate)

print(f"\n=== {passed} passed, {failed} failed ===")
sys.exit(1 if failed else 0)
