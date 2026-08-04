#!/usr/bin/env python3
"""Validate docloop's release version and, optionally, a release tag."""

from __future__ import annotations

import argparse
import re
import subprocess
import sys
from pathlib import Path


STABLE_SEMVER = re.compile(r"(?:0|[1-9][0-9]*)\.(?:0|[1-9][0-9]*)\.(?:0|[1-9][0-9]*)")
CHANGELOG_VERSION = re.compile(r"^## \[([^]]+)]", re.MULTILINE)


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

    signature = git(root, "verify-tag", "--raw", tag)
    signature_status = "verified" if signature.returncode == 0 else "unverified"
    return commit, signature_status


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
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    root = args.repo_root.resolve()
    try:
        version = read_version(root)
        changelog_version = read_changelog_version(root)
        if changelog_version != version:
            raise ReleaseError(
                f"CHANGELOG first version {changelog_version!r} does not match VERSION {version!r}"
            )

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
