#!/usr/bin/env python3
"""audit_anchors.py — 합성 앵커 계수 검산기 (누락 검출 fail-closed 가드).

렌즈 출력이 결함 후보의 근거로 인용한 위치 앵커(L<행번호>)가 합성 산출물
(finding 인용 ∪ 억제 목록 ∪ 비승격/참고 기록)에 전부 나타나는지 기계 검산한다.

추가로 **승격 앵커 승계**(CONTRACT §3, v0.9)를 부분 검사한다: 비승격 기록이
"<finding_id>로 승격"이라 적은 줄은 그 줄의 앵커 중 최소 1종이 승격 대상
finding에 나타나야 한다. 앵커가 기록 쪽에만 남고 승격분이 빈손이면
'존재' 검산은 통과하지만 실행 목록에서는 발현 지점이 사라진다(골든 r9 V8).

지위(CONTRACT §3): 이것은 **누락 검출 가드**다 — 합성이 옮기다 흘린 앵커를
실행 시점에 잡는다(골든 r1·r3·r7의 V8 유형). 합성 변동의 원인을 제거하지
않으며, 앵커가 '존재'하는지만 본다(내용 타당성 검사가 아님). 렌즈가 아예
못 잡은 결함(순수 탐지 실패)에는 무력하다. 승계 검사도 **문형 의존**이라
다른 표현으로 승격하면 발화하지 않는다 — **무발화는 승계의 증명이 아니다**.

사용:
  audit_anchors.py SYNTH.md --lens L1.md L3.md [--l2 L2.md] [--scan SCAN.md] \
      [--extra-re 'F-\\d{2}' ...]

앵커 수집 규칙:
  --lens : 파일 전체에서 앵커 토큰 전부 수집(콜드/축 렌즈 — 전부 후보 근거).
  --l2   : "신규 쟁점" 표제 이후 구간에서만 수집(위치 목록은 대조 자료 —
           CONTRACT §3: 후보가 아니므로 합성 출력 의무 없음).
  --scan : `HIT line <번호>` 패턴에서 수집(결정론 스캔 히트).
  --extra-re : 행 번호 외 문서 ID 앵커 패턴(반복 지정 가능, 예: 'F-\\d{2}').
           렌즈가 위치를 행 번호가 아니라 문서 ID(F-01 등)로 인용하는 경우를
           잡는다 — 골든 r7 V8 실측: L3 렌즈는 F-01로만 인용했고 행 번호
           전용 검산은 이 누락을 보지 못했다. 대상 문서의 ID 체계를 아는
           쪽(오케스트레이터)이 패턴을 지정한다.

앵커 토큰 = L<2~5자리 숫자>(하이픈·숫자 비후행 — 렌즈 후보 ID `L1-24`류 오인 방지;
1자리 행 번호는 렌즈 이름 L1·L2·L3과 충돌해 미수집, 한계로 명시) + --extra-re 매치.

판정: SYNTH 텍스트에 L<번호> 토큰이 없는 앵커가 하나라도 있으면 누락 —
exit 1 (fail-closed: 합성은 미완성이며, 누락 앵커를 인용·억제·비승격 기록
중 하나로 처리한 수정본을 낼 때까지 전달 금지). 승계 검사 위반(승격 대상
finding 부재 = 매달린 참조 포함)도 같은 exit 1. 전부 통과하면 exit 0.
사용 오류/입력 파일 없음은 exit 2.

감사 헤더: 대상 파일 sha256과 앵커 계수를 출력에 보존한다(scan_terms 관례).
"""

import argparse
import hashlib
import re
import sys
from pathlib import Path

# 왼쪽 경계도 ASCII 기준(--extra-re와 동일 규칙). 현 골든 코퍼스에서는 no-op이나
# 한글 바로 뒤 인용("행L129")에서 갈리므로 규칙을 통일해 둔다.
ANCHOR_RE = re.compile(r"(?<![A-Za-z0-9_-])L(\d{2,5})(?![\d-])")
SCAN_HIT_RE = re.compile(r"\bHIT line (\d{1,5})\b")
L2_SECTION_RE = re.compile(r"신규\s*쟁점")
# finding_id 문법. CONTRACT §4는 id 문자열 형식을 규정하지 않으므로(Codex c1-05)
# 기본값은 골든 관례(G12-42·S-01 등 대문자 접두어)로 두되 --id-re로 교체 가능하게 한다.
# 기본값을 넓히지 않는 이유: 소문자까지 열면 본문의 평범한 낱말-숫자 조합("r1-01"을
# 인용한 서술 등)이 승격 참조로 오인돼 거짓 FAIL을 만든다. 대상 문서의 채번 관례를
# 아는 쪽(오케스트레이터)이 지정한다.
DEFAULT_ID_RE = r"[A-Z]+\d*-\d+"


def promotion_re(id_pattern: str) -> re.Pattern:
    """"…는 G9-37로 승격" / "G9-26으로 별도 승격됨" 문형.

    id_pattern을 **비캡처 그룹으로만** 감싼다(Codex c1-05). 캡처 그룹을 덧씌우면
    ⓐ `findall`이 튜플을 돌려줘 `c(\\d+)-\\d+`가 `('c1-7','1')`로 깨지고
    ⓑ 사용자 정규식의 숫자 역참조가 밀려 `(G\\d+)-\\1`이 "cannot refer to an
    open group"으로 **가드를 죽인다**. 비캡처면 그룹 번호가 보존돼 둘 다 안전하다.
    id 문자열은 매치 시작 위치에서 id_pattern을 다시 매치해 얻는다(아래 호출부).
    """
    return re.compile(rf"(?:{id_pattern})\s*(?:으로|로)\s*(?:별도\s*)?승격")


def sha256_of(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def read(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8")
    except OSError as e:
        print(f"ERROR: 파일을 읽을 수 없음: {path} ({e})", file=sys.stderr)
        sys.exit(2)


def anchors_full(text: str, extra_res: list[re.Pattern]) -> set[str]:
    found = {f"L{m}" for m in ANCHOR_RE.findall(text)}
    for rx in extra_res:
        found |= {m.group(0) for m in rx.finditer(text)}
    return found


def anchors_l2(text: str, extra_res: list[re.Pattern]) -> set[str]:
    """L2는 '신규 쟁점' 표제 이후 구간만 후보 — 그 앞(위치 목록)은 제외."""
    lines = text.splitlines()
    start = None
    for i, ln in enumerate(lines):
        if ln.lstrip().startswith("#") and L2_SECTION_RE.search(ln):
            start = i
            break
    if start is None:
        # 표제를 못 찾으면 fail-closed: 전체를 후보로 취급(과잉 검산이 과소보다 안전)
        return anchors_full(text, extra_res)
    return anchors_full("\n".join(lines[start:]), extra_res)


def anchors_scan(text: str) -> set[str]:
    # 1자리 행 번호는 제외 — SYNTH 쪽 인식 규칙(2~5자리)과 정합(Codex r7-01:
    # 비대칭이면 'HIT line 7'이 수리 불가능한 FAIL을 만든다). 한계는 모듈
    # docstring의 1자리 미수집 한계와 동일하게 명시.
    return {f"L{m}" for m in SCAN_HIT_RE.findall(text) if len(m) >= 2}


def finding_rows(text: str, id_pattern: str = DEFAULT_ID_RE) -> dict[str, str]:
    """SYNTH의 finding 표에서 `| <id> | …` 행을 id→행 본문으로 수집."""
    rows: dict[str, str] = {}
    for ln in text.splitlines():
        s = ln.strip()
        if not s.startswith("|"):
            continue
        cells = s.split("|")
        if len(cells) < 3:
            continue
        head = cells[1].strip().strip("*` ")
        if re.fullmatch(id_pattern, head):
            rows.setdefault(head, "")
            rows[head] += "\n" + s
    return rows


def check_promotions(
    synth_text: str, extra_res: list[re.Pattern], id_pattern: str = DEFAULT_ID_RE
) -> tuple[int, int, int, list[str], int]:
    """승격 앵커 승계 부분 검사(CONTRACT §3 v0.9).

    비승격 기록 줄이 "<id>로 승격"이라 적었으면 그 줄의 앵커 중 최소 1종이
    승격 대상 finding 행에 나타나야 한다. 반환: (참조 수, 중첩 수, 검사 대상 밖
    수, 위반 목록, 다중 승격 줄 수). **문형 의존** — 다른 표현의 승격은
    검사되지 않는다.

    한계(Codex c1-04): 이것은 **줄 단위 앵커 중첩 감지**이지 결함별 대응 검증이
    아니다. 한 줄에 승격이 여럿이면 A 결함의 앵커가 B 승격분에 있어도 중첩이
    성립해 과대 통과할 수 있다 — 그런 줄 수를 따로 세어 호출자가 공시한다.
    근본 해결은 비승격 기록을 `승격 하나당 target_finding + inherited_anchors`로
    구조화하는 것이고, 그건 산출물 형식 변경이라 계약 개정 사안이다.
    """
    rows = finding_rows(synth_text, id_pattern)
    promo_re = promotion_re(id_pattern)
    id_re = re.compile(id_pattern)
    total = ok = no_anchor = multi_lines = 0
    violations: list[str] = []
    for ln in synth_text.splitlines():
        # 문형 매치는 id로 시작하므로, 매치 시작 위치에서 id만 다시 떠낸다.
        ids = [m0.group(0) for m0 in
               (id_re.match(ln, m.start()) for m in promo_re.finditer(ln)) if m0]
        if not ids:
            continue
        uniq = list(dict.fromkeys(ids))
        if len(uniq) > 1:
            multi_lines += 1
        line_anchors = anchors_full(ln, extra_res)
        for fid in uniq:
            total += 1
            row = rows.get(fid)
            if row is None:
                violations.append(f"{fid}: 승격 대상 finding 행이 없음(매달린 승격 참조)")
                continue
            # 기록 줄이 곧 그 finding의 행이면 승계 관계가 성립하지 않는다(퇴화 사례).
            # 승계할 앵커가 줄에 없는 경우도 검사 대상이 아니다.
            if not line_anchors or ln.strip() in row:
                no_anchor += 1
                continue
            if anchors_full(row, extra_res) & line_anchors:
                ok += 1
            else:
                violations.append(
                    f"{fid}: 기록 줄 앵커 {', '.join(sorted(line_anchors))} 중 "
                    f"승격분에 승계된 것 없음"
                )
    return total, ok, no_anchor, violations, multi_lines


def main() -> int:
    ap = argparse.ArgumentParser(description="합성 앵커 계수 검산기")
    ap.add_argument("synth", help="합성 산출물(SYNTH.md)")
    ap.add_argument("--lens", nargs="*", default=[], help="전체 수집 렌즈 파일(L1·L3)")
    ap.add_argument("--l2", help="L2 파일(신규 쟁점 구간만 수집)")
    ap.add_argument("--scan", help="용어 스캔 파일(HIT line 패턴 수집)")
    ap.add_argument(
        "--extra-re", action="append", default=[],
        help="행 번호 외 문서 ID 앵커 정규식(반복 가능, 예: 'F-\\d{2}')",
    )
    ap.add_argument(
        "--id-re", default=DEFAULT_ID_RE,
        help=f"finding_id 문법 정규식(승계 검사용, 기본 {DEFAULT_ID_RE!r})",
    )
    args = ap.parse_args()

    try:
        re.compile(args.id_re)
    except re.error as e:
        print(f"ERROR: --id-re 정규식 오류: {e}", file=sys.stderr)
        return 2

    try:
        # 경계는 **ASCII 기준**으로 준다 — `\b`는 한글을 \w로 보므로 "F-01이"처럼
        # 조사가 붙은 인용에서 경계가 서지 않아 앵커를 통째로 놓친다(골든 r9 V8
        # 실측: 비승격 기록의 "F-01이 …"가 수집되지 않아 승계 검사가 무발화).
        extra_res = [
            re.compile(rf"(?<![A-Za-z0-9_-])(?:{p})(?![A-Za-z0-9_-])") for p in args.extra_re
        ]
    except re.error as e:
        print(f"ERROR: --extra-re 정규식 오류: {e}", file=sys.stderr)
        return 2

    if not args.lens and not args.l2 and not args.scan:
        print("ERROR: 검산할 입력이 없음 (--lens/--l2/--scan 중 하나 필수)", file=sys.stderr)
        return 2

    synth_path = Path(args.synth)
    if not synth_path.is_file():
        print(f"ERROR: SYNTH 파일 없음: {synth_path}", file=sys.stderr)
        return 2
    synth_text = read(synth_path)
    synth_anchors = anchors_full(synth_text, extra_res)

    sources: list[tuple[str, Path, set[str]]] = []
    for f in args.lens:
        p = Path(f)
        if not p.is_file():
            print(f"ERROR: 렌즈 파일 없음: {p}", file=sys.stderr)
            return 2
        sources.append(("lens", p, anchors_full(read(p), extra_res)))
    if args.l2:
        p = Path(args.l2)
        if not p.is_file():
            print(f"ERROR: L2 파일 없음: {p}", file=sys.stderr)
            return 2
        sources.append(("l2:신규쟁점", p, anchors_l2(read(p), extra_res)))
    if args.scan:
        p = Path(args.scan)
        if not p.is_file():
            print(f"ERROR: 스캔 파일 없음: {p}", file=sys.stderr)
            return 2
        sources.append(("scan:HIT", p, anchors_scan(read(p))))

    print(f"SYNTH: {synth_path} sha256 {sha256_of(synth_path)} (앵커 {len(synth_anchors)}종)")
    required: set[str] = set()
    for kind, p, anc in sources:
        print(f"SRC[{kind}]: {p} sha256 {sha256_of(p)} (앵커 {len(anc)}종)")
        required |= anc

    def sort_key(a: str):
        m = re.fullmatch(r"L(\d+)", a)
        return (0, int(m.group(1)), a) if m else (1, 0, a)

    missing = sorted(required - synth_anchors, key=sort_key)
    print(f"검산: 요구 앵커 {len(required)}종 → SYNTH 존재 {len(required) - len(missing)}종 · 누락 {len(missing)}종")

    promo_total, promo_ok, promo_skip, promo_bad, promo_multi = check_promotions(
        synth_text, extra_res, args.id_re
    )
    # 표현 주의(Codex c1-04): 이 검사는 "줄 앵커 ∩ 승격분 앵커 ≠ ∅"이라는 **패턴 기반
    # 중첩 감지**이지 의미 수준의 승계 검증이 아니다. 한 줄에 승격이 여럿이면 다른 결함의
    # 앵커로도 중첩이 성립할 수 있으므로 "승계 확인"이라 부르지 않는다.
    print(
        f"승계 중첩 감지(§3 승격 앵커 승계 근사·문형 의존): 승격 참조 {promo_total}건 → "
        f"앵커 중첩 {promo_ok}건 · 검사 대상 밖 {promo_skip}건 · 중첩 없음(위반) {len(promo_bad)}건"
    )
    if promo_multi:
        print(
            f"  주의: 한 줄에 승격 참조가 여럿인 기록 {promo_multi}줄 — 그 줄의 중첩 판정은 "
            f"결함별 대응을 구분하지 못한다(과대 통과 가능)."
        )

    failed = False
    if missing:
        print("결과: ANCHOR-FAIL — 누락 앵커(합성 미완성, 인용·억제·비승격 기록 중 하나로 처리 후 재제출):")
        print("  " + ", ".join(missing))
        failed = True
    if promo_bad:
        print("결과: PROMO-FAIL — 승격 앵커 미승계(승격된 finding이 그 결함의 근거 앵커를 가져가야 함):")
        for v in promo_bad:
            print("  " + v)
        failed = True
    if failed:
        return 1
    print("결과: ANCHOR-OK — 렌즈·스캔 후보 앵커 전부가 합성 산출물에 존재, 승격 앵커 중첩 위반 없음")
    return 0


if __name__ == "__main__":
    sys.exit(main())
