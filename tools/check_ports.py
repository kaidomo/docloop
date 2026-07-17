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


def lint_rows(text):
    """기형 행 fail-closed(impl r2-02): lib/·prompts/로 시작하는 표 행은 반드시
    유효한 ROW여야 한다 — 조용한 탈락(secondary 소스 무오류 소실) 차단."""
    errors = []
    for line in text.splitlines():
        if re.match(r"^\|\s*(lib/|prompts/)", line) and not ROW.match(line):
            errors.append(f"malformed PORTS row (fail-closed): {line.strip()[:80]}")
    return errors


def blob_of(path):
    r = subprocess.run(["git", "hash-object", os.path.join(ROOT, path)],
                       capture_output=True, text=True)
    return r.stdout.strip() if r.returncode == 0 else None


def compare(rows, upstream_blob_fn, downstream_blob_fn, tracked_files):
    """비교 코어(주입식 — selftest가 실제 로직을 실행할 수 있게 분리)."""
    errors, covered = [], set()
    for downstream, cls, src, blob, down_blob in rows:
        covered.add(downstream)
        if cls != "blob":
            continue
        if down_blob in ("-", "(auto)") or len(down_blob) != 40:   # blob 행은 다운스트림 해시 필수
            errors.append(f"row format: {downstream} blob row lacks a recorded downstream hash")
        up = upstream_blob_fn(src)
        if up is None:
            errors.append(f"missing upstream source: {src}"); continue
        if up != blob:
            errors.append(f"STALE-upstream: {downstream} ← {src} ({blob[:9]}→{up[:9]})")
        if len(down_blob) == 40:
            cur = downstream_blob_fn(downstream)
            if cur != down_blob:
                errors.append(f"STALE-downstream: {downstream} edited without row update "
                              f"({down_blob[:9]}→{(cur or 'missing')[:9]})")
    for f in tracked_files:                       # 커버리지: lib/ + prompts/ 전 파일
        if f not in covered:
            errors.append(f"coverage: {f} has no PORTS.md row")
    return errors


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
    ports_text = open(PORTS, encoding="utf-8").read()
    lint = lint_rows(ports_text)
    if lint:
        for e in lint:
            print(f"FAIL {e}")
        print(f"=== {len(lint)} failures (row lint) ===")
        return 1
    rows = parse_rows(ports_text)

    def up_blob(src):
        r = subprocess.run(["git", "-C", UP, "rev-parse", f"{upref}:{src}"],
                           capture_output=True, text=True)
        return r.stdout.strip() if r.returncode == 0 else None

    tracked = [f"lib/{f}" for f in sorted(os.listdir(os.path.join(ROOT, "lib")))]
    tracked += [f"prompts/{f}" for f in sorted(os.listdir(os.path.join(ROOT, "prompts")))]
    errors = compare(rows, up_blob, blob_of, tracked)
    for e in errors:
        print(f"FAIL {e}")
    print(f"=== {len(errors)} failures ===")
    return 1 if errors else 0


def selftest():
    """실제 비교 로직에 주입 픽스처로 5개 실패 모드를 각각 발화시켜 증명(r1-03)."""
    H = lambda c: c * 40
    up = {"src/a": H("a"), "src/guards": H("b")}
    down = {"lib/x.py": H("c")}
    ok_rows = [("lib/x.py", "blob", "src/a", H("a"), H("c")),
               ("lib/x.py", "blob", "src/guards", H("b"), H("c"))]
    base = compare(ok_rows, up.get, down.get, ["lib/x.py"])
    assert base == [], f"기준 픽스처가 실패함: {base}"
    cases = {
        "changed-upstream": ([("lib/x.py", "blob", "src/a", H("0"), H("c"))], up.get, down.get, ["lib/x.py"]),
        "changed-downstream": ([("lib/x.py", "blob", "src/a", H("a"), H("9"))], up.get, down.get, ["lib/x.py"]),
        "missing-source": ([("lib/x.py", "blob", "src/none", H("a"), H("c"))], up.get, down.get, ["lib/x.py"]),
        "incomplete-coverage": (ok_rows, up.get, down.get, ["lib/x.py", "prompts/new.md"]),
        "secondary-source-only": ([("lib/x.py", "blob", "src/a", H("a"), H("c")),
                                   ("lib/x.py", "blob", "src/guards", H("0"), H("c"))],
                                  up.get, down.get, ["lib/x.py"]),
        "auto-downstream-disallowed": ([("lib/x.py", "blob", "src/a", H("a"), "(auto)")],
                                       up.get, down.get, ["lib/x.py"]),
    }
    for name, (rows, u, d, tr) in cases.items():
        errs = compare(rows, u, d, tr)
        assert errs, f"실패 모드 미발화: {name}"
        print(f"selftest: {name} → FAIL 발화 ok ({errs[0][:60]}…)")
    # r2-02: 기형 secondary 행이 파서에서 조용히 탈락하지 않고 lint로 실패
    raw = ("| lib/split.py | blob | src/a | " + H("a") + " | " + H("c") + " |\n"
           "| lib/split.py | blob | src/guards | " + H("b") + " | (AUTO) |\n")
    lint = lint_rows(raw)
    assert lint, "기형 secondary 행이 lint를 통과함(fail-open)"
    print(f"selftest: malformed-secondary-row → lint FAIL 발화 ok ({lint[0][:60]}…)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
