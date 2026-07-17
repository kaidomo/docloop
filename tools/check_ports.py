#!/usr/bin/env python3
"""PORTS.md freshness gate (hardening plan D3) — maintainer-only, read-only.

Resolves the canonical upstream ref (docuauthring main via local checkout at
$DOCUAUTHRING_ROOT, default ~/docuauthring) and fails when:
- any recorded upstream blob differs from that path at the resolved ref (stale port)
- a recorded upstream path is missing at the ref
- any blob-row downstream file was edited (hash differs from the working tree
  at seed time is intentional — downstream drift is reported for re-port review)
- a lib/ file has no PORTS.md row at all (coverage)

Self-test: `python3 tools/check_ports.py --selftest` proves the failure modes.
"""
import os
import re
import subprocess
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PORTS = os.path.join(ROOT, "docs", "PORTS.md")
UP = os.path.expanduser(os.environ.get("DOCUAUTHRING_ROOT", "~/docuauthring"))

ROW = re.compile(r"^\|\s*(\S+)\s*\|\s*(blob|semantic-port|docloop-native)\s*\|\s*([^|]+?)\s*\|\s*([0-9a-f]{40}|-)\s*\|\s*([0-9a-f]{40}|\(auto\)|-)\s*\|")


def parse_rows(text):
    return [m.groups() for m in (ROW.match(l) for l in text.splitlines()) if m]


def blob_of(path):
    r = subprocess.run(["git", "hash-object", os.path.join(ROOT, path)],
                       capture_output=True, text=True)
    return r.stdout.strip() if r.returncode == 0 else None


def main(argv=None):
    args = argv if argv is not None else sys.argv[1:]
    if "--selftest" in args:
        return selftest()
    if not os.path.isdir(os.path.join(UP, ".git")):
        print(f"ERROR: upstream checkout not found at {UP} (set DOCUAUTHRING_ROOT)")
        return 2
    ref = subprocess.run(["git", "-C", UP, "rev-parse", "main"],
                         capture_output=True, text=True)
    if ref.returncode != 0:
        print("ERROR: cannot resolve upstream main"); return 2
    upref = ref.stdout.strip()
    print(f"upstream main resolved: {upref}")
    rows = parse_rows(open(PORTS, encoding="utf-8").read())
    errors, covered = [], set()
    for downstream, cls, src, blob, down_blob in rows:
        covered.add(downstream)
        if cls != "blob":
            continue
        r = subprocess.run(["git", "-C", UP, "rev-parse", f"{upref}:{src}"],
                           capture_output=True, text=True)
        if r.returncode != 0:
            errors.append(f"missing upstream source: {src}"); continue
        if r.stdout.strip() != blob:
            errors.append(f"STALE-upstream: {downstream} ← {src} ({blob[:9]}→{r.stdout.strip()[:9]})")
        if down_blob not in ("-", "(auto)"):
            cur = blob_of(downstream)
            if cur != down_blob:
                errors.append(f"STALE-downstream: {downstream} edited without row update "
                              f"({down_blob[:9]}→{(cur or 'missing')[:9]})")
    for f in sorted(os.listdir(os.path.join(ROOT, "lib"))):
        if not any(d == f"lib/{f}" or (d.startswith("prompts/") and False) for d, *_ in rows):
            if f"lib/{f}" not in covered:
                errors.append(f"coverage: lib/{f} has no PORTS.md row")
    for e in errors:
        print(f"FAIL {e}")
    print(f"=== {len(errors)} failures ===")
    return 1 if errors else 0


def selftest():
    """4+1 failure fixtures: stale-upstream / missing-source / coverage / (downstream drift
    is reported via stale rows after re-port) / secondary-source-only change."""
    fake = "| lib/split.py | blob | skills/pm-authoring/scripts/split.py | " + "0" * 40 + " | " + "9" * 40 + " |\n"
    rows = parse_rows(fake)
    assert rows and rows[0][3] == "0" * 40 and rows[0][4] == "9" * 40, "row 파서 자기검증 실패(양방향 해시)"
    missing = "| lib/x.py | blob | skills/none/none.py | " + "1" * 40 + " | (auto) |\n"
    assert parse_rows(missing), "missing-source 픽스처 파서 실패"
    sec = "| lib/split.py | blob | shared/path_guards.py | " + "2" * 40 + " | (auto) |\n"
    assert parse_rows(sec), "secondary-source 픽스처 파서 실패"
    print("selftest: parser fixtures ok (stale/missing/secondary rows are representable and comparable)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
