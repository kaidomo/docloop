#!/usr/bin/env python3
"""Validate docloop's release version and, optionally, a release tag."""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from pathlib import Path


STABLE_SEMVER = re.compile(r"(?:0|[1-9][0-9]*)\.(?:0|[1-9][0-9]*)\.(?:0|[1-9][0-9]*)")
CHANGELOG_VERSION = re.compile(r"^## \[([^]]+)]", re.MULTILINE)
ALLOWED_SIGNERS = Path(".github/release_allowed_signers")
RELEASE_HEADING = re.compile(
    r"^##\s*\[(?P<version>[^]]+)\]\s+[—-]\s+"
    r"(?P<date>[0-9]{4}-[0-9]{2}-[0-9]{2})\s*$",
    re.MULTILINE,
)


class ReleaseError(RuntimeError):
    """A release contract violation with an actionable diagnostic."""


def read_version(root: Path) -> str:
    path = root / "VERSION"
    try:
        raw = path.read_bytes()
    except OSError as exc:
        raise ReleaseError(f"VERSION cannot be read at {path}: {exc}") from exc

    match = re.fullmatch(rb"(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)(?:\n)?", raw)
    if not match:
        raise ReleaseError(
            "VERSION must contain exactly one stable SemVer value (X.Y.Z), "
            "with at most one trailing newline"
        )
    return raw.rstrip(b"\n").decode("ascii")


def read_changelog_version(root: Path) -> str:
    path = root / "CHANGELOG.md"
    try:
        text = path.read_text(encoding="utf-8")
    except (OSError, UnicodeError) as exc:
        raise ReleaseError(f"CHANGELOG cannot be read as UTF-8 at {path}: {exc}") from exc

    match = CHANGELOG_VERSION.search(text)
    if not match:
        raise ReleaseError("CHANGELOG has no release header in the form '## [X.Y.Z]'")
    return match.group(1)


def read_release_notes(root: Path, version: str) -> str:
    text = (root / "CHANGELOG.md").read_text(encoding="utf-8")
    first = CHANGELOG_VERSION.search(text)
    match = RELEASE_HEADING.match(text, first.start()) if first else None
    if match is None or match.group("version") != version:
        raise ReleaseError(f"CHANGELOG has no dated release section [{version}]")
    following = re.search(r"^##\s*\[", text[match.end():], re.MULTILINE)
    end = match.end() + following.start() if following else len(text)
    notes = text[match.end():end].strip("\n")
    if not notes.strip():
        raise ReleaseError(f"CHANGELOG release section [{version}] is empty")
    return notes + "\n"


def git(root: Path, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["git", *args],
        cwd=root,
        capture_output=True,
        text=True,
    )


def validate_tag(root: Path, tag: str, version: str, main_ref: str) -> tuple[str, str]:
    expected = f"v{version}"
    if tag != expected:
        raise ReleaseError(f"tag {tag!r} does not match VERSION {version!r}; expected {expected!r}")
    if not STABLE_SEMVER.fullmatch(tag[1:]):
        raise ReleaseError(f"tag {tag!r} is not a stable vX.Y.Z release tag")

    tag_ref = f"refs/tags/{tag}"
    tag_type_result = git(root, "cat-file", "-t", tag_ref)
    if tag_type_result.returncode != 0:
        detail = tag_type_result.stderr.strip() or "tag object is absent"
        raise ReleaseError(
            f"cannot resolve tag object {tag_ref}: {detail}. "
            "Fetch complete history and tags first (for example: git fetch --tags origin main)."
        )
    object_type = tag_type_result.stdout.strip()
    if object_type != "tag":
        raise ReleaseError(
            f"tag {tag!r} is {object_type!r}, not an annotated tag; "
            "create releases from annotated (or signed annotated) tags"
        )

    commit_result = git(root, "rev-parse", "--verify", f"{tag_ref}^{{commit}}")
    if commit_result.returncode != 0:
        detail = commit_result.stderr.strip() or "tag does not peel to a commit"
        raise ReleaseError(f"tag {tag!r} cannot be peeled to a commit: {detail}")
    commit = commit_result.stdout.strip()
    main_commit_result = git(root, "rev-parse", "--verify", f"{main_ref}^{{commit}}")
    if main_commit_result.returncode != 0:
        detail = main_commit_result.stderr.strip() or "main ref is absent"
        raise ReleaseError(
            f"cannot resolve main ref {main_ref!r}: {detail}. "
            "Fetch complete history and tags first (for example: git fetch --tags origin main)."
        )

    ancestry = git(root, "merge-base", "--is-ancestor", commit, main_commit_result.stdout.strip())
    if ancestry.returncode == 1:
        raise ReleaseError(f"tag {tag!r} peels to {commit}, which is not reachable from {main_ref!r}")
    if ancestry.returncode != 0:
        detail = ancestry.stderr.strip() or "ancestry could not be determined"
        raise ReleaseError(
            f"cannot prove {tag!r} ancestry under {main_ref!r}: {detail}. "
            "Fetch complete history and tags before retrying."
        )

    allowed_signers = root / ALLOWED_SIGNERS
    if not allowed_signers.is_file():
        raise ReleaseError(
            f"trusted signer file is missing: {allowed_signers}; "
            "production signer bytes must be provided by the repository owner"
        )
    tracked = git(root, "ls-files", "--error-unmatch", str(ALLOWED_SIGNERS))
    if tracked.returncode != 0:
        raise ReleaseError(f"trusted signer file is not tracked in the tag commit: {allowed_signers}")
    if any(
        git(root, *args, str(ALLOWED_SIGNERS)).returncode != 0
        for args in (("diff", "--quiet", "HEAD", "--"), ("diff", "--cached", "--quiet", "HEAD", "--"))
    ):
        raise ReleaseError(f"trusted signer file has uncommitted changes: {allowed_signers}")
    status = git(root, "status", "--short", "--untracked-files=all", "--", str(ALLOWED_SIGNERS))
    if status.returncode != 0 or status.stdout.strip():
        raise ReleaseError(f"trusted signer file is not cleanly bound to the tag commit: {allowed_signers}")
    committed_signers = git(root, "show", f"{tag_ref}:{ALLOWED_SIGNERS}")
    if committed_signers.returncode != 0 or committed_signers.stdout.encode() != allowed_signers.read_bytes():
        raise ReleaseError(f"trusted signer file does not match the tag commit: {allowed_signers}")
    signature = git(
        root,
        "-c",
        f"gpg.ssh.allowedSignersFile={allowed_signers}",
        "verify-tag",
        "--raw",
        tag,
    )
    if signature.returncode != 0:
        detail = signature.stderr.strip() or "signature verification failed"
        raise ReleaseError(
            f"tag {tag!r} must carry a valid signature from {allowed_signers}: {detail}"
        )
    return commit, "verified"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--repo-root",
        type=Path,
        default=Path(__file__).resolve().parents[1],
        help="repository root to validate (default: this script's repository)",
    )
    parser.add_argument("--tag", help="existing release tag to validate, for example v0.13.0")
    parser.add_argument(
        "--main-ref",
        default="origin/main",
        help="Git ref that must contain the peeled tag commit (default: origin/main)",
    )
    parser.add_argument("--notes-file", type=Path, help="write validated CHANGELOG notes here")
    parser.add_argument("--github-release-json", type=Path, help="validate an existing Release payload")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    root = args.repo_root.resolve()
    try:
        if args.github_release_json and not args.tag:
            raise ReleaseError("--tag is required when validating an existing Release")
        version = read_version(root)
        changelog_version = read_changelog_version(root)
        if changelog_version != version:
            raise ReleaseError(
                f"CHANGELOG first version {changelog_version!r} does not match VERSION {version!r}"
            )

        notes = read_release_notes(root, version)
        if args.notes_file:
            args.notes_file.parent.mkdir(parents=True, exist_ok=True)
            args.notes_file.write_text(notes, encoding="utf-8")
        if args.github_release_json:
            try:
                payload = json.loads(args.github_release_json.read_text(encoding="utf-8"))
            except (OSError, UnicodeError, json.JSONDecodeError) as exc:
                raise ReleaseError(f"existing Release JSON cannot be read: {exc}") from exc
            expected = {"tagName": args.tag, "name": args.tag, "isDraft": False, "isPrerelease": False}
            if not isinstance(payload, dict):
                raise ReleaseError("existing Release JSON must be an object")
            errors = [f"existing Release {key} must be {value!r}" for key, value in expected.items() if key not in payload or payload[key] != value]
            if "body" not in payload or not isinstance(payload["body"], str) or payload["body"] != notes:
                errors.append("existing Release body does not match CHANGELOG notes")
            if errors:
                raise ReleaseError("; ".join(errors))

        summary = f"release check passed: VERSION {version}, CHANGELOG {changelog_version}"
        if args.tag:
            commit, signature = validate_tag(root, args.tag, version, args.main_ref)
            summary += f", tag {args.tag}, commit {commit}, signature={signature}"
        print(summary)
        return 0
    except ReleaseError as exc:
        print(f"release check failed: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
