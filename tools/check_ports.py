#!/usr/bin/env python3
"""PORTS.md freshness gate (hardening plan D3) — maintainer-only, read-only.

Resolves the canonical upstream ref (docuauthring main via local checkout at
$DOCUAUTHRING_ROOT, default ~/docuauthring) and fails when:
- any recorded upstream blob differs from that path at the resolved ref (stale port)
- a recorded upstream path is missing at the ref
- any blob-row downstream file was edited (hash differs from the working tree
  at seed time is intentional — downstream drift is reported for re-port review)
- a lib/ file has no PORTS.md row at all (coverage)

semantic-port rows are prose re-writes, so their downstream file can never be blob-compared.
What is recorded instead is the upstream blob the row was last reviewed against; when that
source moves, this gate emits a WARNING, not a failure. Whether the change has to reach the
downstream prose is a human call — the machine only guarantees you are told the source moved.
A row whose source resolves to a file may not record `-`: that would switch detection off
silently, so it fails. `-` is reserved for a source that is not a file at all (a skill name,
say), and such a row must disclose in its notes that drift is undetectable there. As of
2026-09-07 no row needs it -- the two that once did were re-pointed at the real files.

What this gate does not guarantee: bumping a baseline is an unverifiable human act. Anyone
can silence a warning by writing the current upstream SHA into the row without reading a
line of the source. No machine check can close that -- the gate is human-assistive here, so
the limit is disclosed rather than papered over with ceremony that would be just as easy to
fake. What the gate does guarantee is that the drift is never invisible: the warning appears
until someone acts on it, and the row's notes are where that act is recorded for review.

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
    # lint_rows와 동일한 정규화(r4-01): 들여쓴 유효 행이 lint는 통과하고
    # 파싱에선 탈락하는 비대칭 차단
    return [m.groups() for m in (ROW.match(l.lstrip()) for l in text.splitlines()) if m]


def lint_rows(text):
    """기형 행 fail-closed(impl r2-02): lib/·prompts/로 시작하는 표 행은 반드시
    유효한 ROW여야 한다 — 조용한 탈락(secondary 소스 무오류 소실) 차단."""
    errors = []
    for line in text.splitlines():
        if re.match(r"^\s*\|\s*(lib/|prompts/)", line) and not ROW.match(line.lstrip()):
            errors.append(f"malformed PORTS row (fail-closed): {line.strip()[:80]}")
    return errors


def blob_of(path):
    r = subprocess.run(["git", "hash-object", os.path.join(ROOT, path)],
                       capture_output=True, text=True)
    return r.stdout.strip() if r.returncode == 0 else None


def compare(rows, upstream_blob_fn, downstream_blob_fn, tracked_files):
    """비교 코어(주입식 — selftest가 실제 로직을 실행할 수 있게 분리).

    반환: (errors, warnings). semantic-port 드리프트는 경고이지 실패가 아니다 —
    downstream 산문에 반영해야 하는지는 사람이 판단한다."""
    errors, warnings, covered = [], [], set()
    for downstream, cls, src, blob, down_blob in rows:
        covered.add(downstream)
        if cls == "semantic-port":
            up = upstream_blob_fn(src)
            if blob == "-":
                # 원천이 실제 파일로 풀리는데 baseline 이 없으면 검출이 조용히 꺼진다(fail-open).
                # 파일이 아닌 원천(스킬 이름 등)만 baseline 없이 허용한다.
                if up is not None:
                    errors.append(f"semantic-port row without a baseline: {downstream} ← {src} "
                                  f"(the source resolves to a file, so drift is detectable and a "
                                  f"baseline is required; only non-file sources may record `-`)")
                continue
            if up is None:
                errors.append(f"missing upstream source: {src}")
            elif up != blob:
                warnings.append(f"semantic-port source moved: {downstream} ← {src} "
                                f"({blob[:9]}→{up[:9]}) — review whether the prose must follow, "
                                f"then update the row (reflected or deliberately not)")
            continue
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
    return errors, warnings


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
    errors, warnings = compare(rows, up_blob, blob_of, tracked)
    for w in warnings:
        print(f"WARN {w}")
    for e in errors:
        print(f"FAIL {e}")
    suffix = f", {len(warnings)} warnings" if warnings else ""
    print(f"=== {len(errors)} failures{suffix} ===")
    return 1 if errors else 0


def selftest():
    """실제 비교 로직에 주입 픽스처로 각 실패·경고 모드를 발화시켜 증명(r1-03)."""
    H = lambda c: c * 40
    up = {"src/a": H("a"), "src/guards": H("b")}
    down = {"lib/x.py": H("c")}
    ok_rows = [("lib/x.py", "blob", "src/a", H("a"), H("c")),
               ("lib/x.py", "blob", "src/guards", H("b"), H("c"))]
    base, base_warn = compare(ok_rows, up.get, down.get, ["lib/x.py"])
    assert base == [], f"기준 픽스처가 실패함: {base}"
    assert base_warn == [], f"기준 픽스처가 경고를 냄: {base_warn}"
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
        errs, _ = compare(rows, u, d, tr)
        assert errs, f"실패 모드 미발화: {name}"
        print(f"selftest: {name} → FAIL 발화 ok ({errs[0][:60]}…)")

    # semantic-port 드리프트: 경고로 발화하되 게이트를 실패시키지 않는다
    sem_moved = [("prompts/p.md", "semantic-port", "src/a", H("0"), "-")]
    errs, warns = compare(sem_moved, up.get, down.get, [])
    assert not errs, f"semantic 드리프트가 FAIL 로 샜다: {errs}"
    assert any("semantic-port source moved" in w for w in warns), f"semantic 드리프트 미발화: {warns}"
    print(f"selftest: semantic-drift → WARN 발화 ok, FAIL 0 ({warns[0][:60]}…)")

    # 같은 값이면 조용하다(경고 오탐 차단)
    errs, warns = compare([("prompts/p.md", "semantic-port", "src/a", H("a"), "-")], up.get, down.get, [])
    assert not errs and not warns, f"동일 baseline 에서 오탐: {errs} {warns}"
    print("selftest: semantic-unchanged → 무발화 ok")

    # baseline 이 '-' 인 semantic 행(원천이 파일이 아님)은 검사 대상이 아니다
    errs, warns = compare([("lib/b.py", "semantic-port", "some-skill", "-", "-")], up.get, down.get, [])
    assert not errs and not warns, f"baseline 없는 semantic 행에서 발화: {errs} {warns}"
    print("selftest: semantic-no-baseline → 무발화 ok(검출 불가 공시 행)")

    # 원천이 파일로 풀리는데 baseline 이 '-' 이면 검출이 꺼지므로 실패다(fail-open 차단)
    errs, warns = compare([("prompts/p.md", "semantic-port", "src/a", "-", "-")], up.get, down.get, [])
    assert any("semantic-port row without a baseline" in e for e in errs), \
        f"파일 원천 + baseline 없음이 조용히 통과함(fail-open): {errs} {warns}"
    print("selftest: semantic-file-source-without-baseline → FAIL 발화 ok")

    # semantic 행의 upstream 경로가 사라지면 그건 경고가 아니라 실패다
    errs, warns = compare([("prompts/p.md", "semantic-port", "src/none", H("a"), "-")], up.get, down.get, [])
    assert any("missing upstream source" in e for e in errs), f"semantic missing-source 미발화: {errs}"
    print("selftest: semantic-missing-source → FAIL 발화 ok")
    # r2-02: 기형 secondary 행이 파서에서 조용히 탈락하지 않고 lint로 실패
    raw = ("| lib/split.py | blob | src/a | " + H("a") + " | " + H("c") + " |\n"
           "| lib/split.py | blob | src/guards | " + H("b") + " | (AUTO) |\n")
    lint = lint_rows(raw)
    assert lint, "기형 secondary 행이 lint를 통과함(fail-open)"
    print(f"selftest: malformed-secondary-row → lint FAIL 발화 ok ({lint[0][:60]}…)")
    indented = " | lib/split.py | blob | src/guards | " + H("b") + " | (AUTO) |\n"
    lint2 = lint_rows(indented)
    assert lint2, "들여쓴 기형 행이 lint를 통과함(r3-02)"
    print(f"selftest: indented-malformed-row → lint FAIL 발화 ok")
    # r4-01 end-to-end: 들여쓴 '유효' secondary 행 — lint 통과 + 파싱 포함 + stale 발화
    raw2 = ("| lib/x.py | blob | src/a | " + H("a") + " | " + H("c") + " |\n"
            "  | lib/x.py | blob | src/guards | " + H("0") + " | " + H("c") + " |\n")
    assert lint_rows(raw2) == [], "들여쓴 유효 행이 lint에서 오탐"
    rows2 = parse_rows(raw2)
    assert len(rows2) == 2, f"들여쓴 유효 행이 파싱에서 탈락: {len(rows2)}행"
    errs2, _ = compare(rows2, up.get, down.get, ["lib/x.py"])
    assert any("STALE-upstream" in e and "src/guards" in e for e in errs2), \
        f"들여쓴 stale secondary 미발화: {errs2}"
    print("selftest: indented-valid-stale-secondary → end-to-end STALE 발화 ok")
    return 0


if __name__ == "__main__":
    sys.exit(main())
