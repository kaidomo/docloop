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
  audit_anchors.py SYNTH.md --lens L1.md L3.md [--l2 L2_r1.md L2_r2.md ...] \
      [--scan SCAN.md] [--extra-re 'F-\\d{2}' ...]
  SYNTH는 **옵션보다 앞**에 둔다 — `--lens`·`--l2`는 여러 파일을 받으므로 뒤따르는
  positional을 인자로 삼킨다(그 호출은 원인을 밝히며 exit 2).

앵커 수집 규칙:
  --lens : 파일 전체에서 앵커 토큰 전부 수집(콜드/축 렌즈 — 전부 후보 근거).
  --l2   : "신규 쟁점" 표제 이후 구간에서만 수집(위치 목록은 대조 자료 —
           CONTRACT §3: 후보가 아니므로 합성 출력 의무 없음).
           **여러 회차를 받는다**(docauth#200) — 회차마다 '신규 쟁점' 구간이
           다르므로 파일별 구간 앵커를 **합집합**으로 요구한다. 한 회차만
           넘기면 나머지 회차 앵커는 검산되지 않는다(실측: r1은 ANCHOR-OK였으나
           r2에 미검산 후보 L16이 남아 있었다).
  --scan : `HIT line <번호>` 패턴에서 수집(결정론 스캔 히트).
  --extra-re : 행 번호 외 문서 ID 앵커 패턴(반복 지정 가능, 예: 'F-\\d{2}').
           렌즈가 위치를 행 번호가 아니라 문서 ID(F-01 등)로 인용하는 경우를
           잡는다 — 골든 r7 V8 실측: L3 렌즈는 F-01로만 인용했고 행 번호
           전용 검산은 이 누락을 보지 못했다. 대상 문서의 ID 체계를 아는
           쪽(오케스트레이터)이 패턴을 지정한다.

앵커 토큰 = L<2~5자리 숫자>(하이픈·숫자 비후행 — 렌즈 후보 ID `L1-24`류 오인 방지;
1자리 행 번호는 렌즈 이름 L1·L2·L3과 충돌해 미수집, 한계로 명시) + `A<12자리 소문자
16진수>`(docauth#207 2안 — 행 내용 안정 식별자, `audit_quotes.py`가 쓰는 것과 같은
형식) + --extra-re 매치. 이 도구는 두 형식 모두 **불투명한 토큰 문자열**로만
다룬다(원문에 대해 재계산·검증하지 않는다) — 내용 검증은 `audit_quotes.py`의 몫이고,
여기서는 "같은 토큰이 후보·합성 양쪽에 나타나는가"만 본다.

판정: v2 실행은 `--ledger`의 `classification_ledger[].evidence_anchors`를 terminal
placement SSOT로 삼는다. SYNTH 본문이나 scratch prose에 앵커가 있어도 ledger에
없으면 누락이다. `--ledger`가 없는 legacy 실행만 SYNTH 텍스트를 사용한다.
필수 앵커가 terminal SSOT에 하나라도 없으면 누락 —
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

import yaml

try:  # Package import in tests; sibling import when executed as a script.
    from .validate_review_intermediate import (
        load_yaml_text,
        resolve_packet_file,
        validate_data as validate_intermediate_data,
    )
except ImportError:  # pragma: no cover - exercised by CLI dispatch
    from validate_review_intermediate import (
        load_yaml_text,
        resolve_packet_file,
        validate_data as validate_intermediate_data,
    )

# 왼쪽 경계도 ASCII 기준(--extra-re와 동일 규칙). 현 골든 코퍼스에서는 no-op이나
# 한글 바로 뒤 인용("행L129")에서 갈리므로 규칙을 통일해 둔다.
# #207 2안: 레거시 행 번호(L\d{2,5})와 신규 안정 식별자(A<12hex>, audit_quotes.py의
# 형식과 동일)를 하나의 정규식으로 함께 인식한다 — 그룹 1이 매치되면 레거시,
# 그룹 2가 매치되면 신규다(아래 anchors_full 참고).
# 해시 토큰의 오른쪽 경계는 16진수뿐 아니라 식별자 문자 전부를 막는다(Codex r2-01,
# audit_quotes.py와 동일 이유) — 이 스크립트는 하이픈 범위 개념이 없으므로(앵커를
# 그저 불투명 토큰으로 전부 긁을 뿐 범위로 전개하지 않는다) 왼쪽 경계와 대칭으로
# 하이픈도 막는다.
ANCHOR_RE = re.compile(r"(?<![A-Za-z0-9_-])(?:L(\d{2,5})(?![\d-])|(A[0-9a-f]{12})(?![A-Za-z0-9_-]))")
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
    found: set[str] = set()
    for m in ANCHOR_RE.finditer(text):
        found.add(f"L{m.group(1)}" if m.group(1) else m.group(2))
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
    ap = argparse.ArgumentParser(
        description="합성 앵커 계수 검산기",
        epilog="SYNTH는 옵션보다 **앞**에 둔다 — --lens/--l2는 nargs='*'라 뒤따르는 "
               "positional을 인자로 삼킨다.",
    )
    # nargs='?'로 두고 부재를 직접 잡는다(Codex r1-01): --l2가 다중 인자가 되면서
    # `--l2 a.md SYNTH.md` 같은 옵션 선행 호출은 SYNTH까지 삼켜 실패한다(--lens는
    # 원래부터 같은 성질이라 문서화된 호출 순서는 SYNTH 선행이다). argparse 기본
    # 오류문("required: synth")은 원인을 못 알려주므로 원인을 짚는 오류로 대체한다.
    ap.add_argument("synth", nargs="?", help="합성 산출물(SYNTH.md) — 옵션보다 앞에 둘 것")
    ap.add_argument(
        "--ledger",
        help="v2 review_intermediate YAML; classification_ledger evidence anchors become the terminal SSOT",
    )
    ap.add_argument(
        "--packet-root",
        help="packet root for ledger and hash-bound authority references",
    )
    ap.add_argument("--lens", nargs="*", default=[], help="전체 수집 렌즈 파일(L1·L3)")
    ap.add_argument(
        "--l2", nargs="*", default=[],
        help="L2 파일(신규 쟁점 구간만 수집). 회차마다 하나씩 전부 넘긴다 — "
             "파일별 구간 앵커를 합집합으로 요구한다",
    )
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

    if args.synth is None:
        # 삼킴 설명은 실제로 삼킬 수 있었을 때만 한다(Codex r2-01) — `--scan`만 준
        # 호출이나 무인자 호출에서 가변 인자를 탓하면 틀린 진단이다.
        if args.lens or args.l2:
            print(
                "ERROR: SYNTH 인자가 없음 — `--lens`/`--l2`는 여러 파일을 받으므로(nargs='*') "
                "옵션 뒤에 둔 SYNTH가 그 인자로 삼켜진다. "
                "호출 순서: audit_anchors.py SYNTH.md --lens L1.md L3.md --l2 L2_r1.md L2_r2.md",
                file=sys.stderr,
            )
        else:
            print(
                "ERROR: SYNTH 인자가 없음 — 첫 인자로 합성 산출물 경로를 준다. "
                "호출 순서: audit_anchors.py SYNTH.md --lens L1.md L3.md --l2 L2_r1.md L2_r2.md",
                file=sys.stderr,
            )
        return 2

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
    ledger_path = None
    if args.ledger:
        if not args.packet_root:
            print("ERROR: --ledger requires --packet-root", file=sys.stderr)
            return 2
        path_errors: list[str] = []
        packet_root = Path(args.packet_root)
        ledger_path = resolve_packet_file(packet_root, args.ledger, "ledger", path_errors)
        if path_errors or ledger_path is None:
            for error in path_errors:
                print(f"ERROR: {error}", file=sys.stderr)
            return 2
        try:
            loaded = load_yaml_text(ledger_path.read_text(encoding="utf-8"))
            envelope = loaded.get("review_intermediate") if isinstance(loaded, dict) else None
        except (OSError, yaml.YAMLError) as e:
            print(f"ERROR: LEDGER를 읽을 수 없음: {e}", file=sys.stderr)
            return 2
        if not isinstance(envelope, dict):
            print("ERROR: LEDGER에 review_intermediate mapping이 없음", file=sys.stderr)
            return 2
        ledger_errors = validate_intermediate_data(envelope, packet_root=packet_root)
        if ledger_errors:
            print(
                "결과: LEDGER-FAIL — intermediate ledger 구조가 유효하지 않음:",
                file=sys.stderr,
            )
            for error in ledger_errors:
                print(f"  {error}", file=sys.stderr)
            return 1
        synth_anchors = {
            anchor
            for row in envelope["classification_ledger"]
            for anchor in row["evidence_anchors"]
        }
    else:
        synth_anchors = anchors_full(synth_text, extra_res)

    sources: list[tuple[str, Path, set[str]]] = []
    for f in args.lens:
        p = Path(f)
        if not p.is_file():
            print(f"ERROR: 렌즈 파일 없음: {p}", file=sys.stderr)
            return 2
        sources.append(("lens", p, anchors_full(read(p), extra_res)))
    # 회차별 L2를 각각 하나의 소스로 등록한다 — required는 아래에서 전 소스의
    # 합집합이므로 파일별 '신규 쟁점' 구간 앵커가 합쳐진다(docauth#200).
    # 파일별로 sha256·앵커 계수를 따로 찍어 어느 회차가 무엇을 요구했는지 감사에 남긴다.
    for f in args.l2:
        p = Path(f)
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

    print(f"SYNTH: {synth_path} sha256 {sha256_of(synth_path)}")
    if ledger_path:
        print(
            f"TERMINAL-LEDGER: {ledger_path} sha256 {sha256_of(ledger_path)} "
            f"(terminal evidence 앵커 {len(synth_anchors)}종)"
        )
    else:
        print(f"LEGACY-SYNTH-ANCHORS: {len(synth_anchors)}종")
    required: set[str] = set()
    for kind, p, anc in sources:
        print(f"SRC[{kind}]: {p} sha256 {sha256_of(p)} (앵커 {len(anc)}종)")
        required |= anc

    # 부분 검산 경고(docauth#200).
    #
    # 판정 근거: 이 도구에 '회차' 개념이 인자로 들어오지 않으므로 회차 수를 직접
    # 알 방법은 없다. 쓸 수 있는 유일한 근거는 **호출 관례**다 — 골든
    # MANIFEST(`golden/MANIFEST_streak_v09.md`)의 표준 호출은 한 회차당 콜드 렌즈
    # 2종(L1·L3)을 --lens로, 그 회차의 L2 1종을 --l2로 넘긴다. 그래서
    # `len(--lens) // 2`를 회차 수의 **관례 기반 어림**으로 쓴다 — 하한 보장이
    # 아니다(Codex r1-02): 한 회차에서 렌즈를 4개 넘기는 정당한 호출이면 과대
    # 추정이고, 렌즈는 2개인데 L2만 3회차면 과소 추정이라 무발화한다. 관례보다
    # 렌즈를 적게 넘긴 호출에서는 어림이 1로 내려가 경고가 꺼진다 — 거짓 경고보다
    # 무발화 쪽으로 보수적이다(경고는 어림일 뿐이라 exit code를 바꾸지 않는다).
    # 무발화가 '전수 검산됨'의 증명이 아니라는 점은 ANCHOR-OK 문장에서 판정 범위를
    # 넘긴 SRC 입력으로 한정해 함께 공시한다.
    #
    # --l2 0개는 경고 대상이 아니다: L2를 아예 넘기지 않는 것은 'L2 축을 검산하지
    # 않는다'는 명시적 선택이고, 1개만 넘긴 부분 검산과 달리 전수 검산으로 오인될
    # 위험이 없다. 경고는 "회차가 여럿인데 그보다 적은 L2만 넘겼다"일 때만 낸다.
    rounds_hint = len(args.lens) // 2
    if rounds_hint >= 2 and 1 <= len(args.l2) < rounds_hint:
        print(
            f"주의: --lens {len(args.lens)}개 — 관례(회차당 콜드 렌즈 L1·L3 2종)로 어림하면 회차 {rounds_hint} 규모인데 "
            f"--l2 는 {len(args.l2)}개뿐 — 넘기지 않은 회차의 '신규 쟁점' 앵커는 검산되지 않는다. "
            f"회차별 L2 산출물을 전부 --l2 에 넘겨라(#200: r1만 넘겨 ANCHOR-OK였으나 r2에 미검산 앵커가 남아 있었다)."
        )

    def sort_key(a: str):
        m = re.fullmatch(r"L(\d+)", a)
        return (0, int(m.group(1)), a) if m else (1, 0, a)

    missing = sorted(required - synth_anchors, key=sort_key)
    print(f"검산: 요구 앵커 {len(required)}종 → SYNTH 존재 {len(required) - len(missing)}종 · 누락 {len(missing)}종")

    if ledger_path:
        promo_total, promo_ok, promo_skip, promo_bad, promo_multi = 0, 0, 0, [], 0
    else:
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
    sink = "atom-level terminal ledger" if ledger_path else "legacy 합성 산출물"
    # 판정 범위를 명시한다(Codex r1-02): 이 도구는 넘겨받은 SRC 입력만 본다.
    # 회차·렌즈를 덜 넘긴 실행에서도 ANCHOR-OK가 나오므로, OK를 '전수 검산됨'으로
    # 읽지 않도록 통과 문장 자체에 범위를 붙인다.
    print(
        f"결과: ANCHOR-OK — 이 실행에 넘긴 SRC 입력({len(sources)}종)의 후보 앵커 전부가 {sink}에 존재, "
        f"승격 앵커 중첩 위반 없음 (넘기지 않은 렌즈·회차는 판정 범위 밖)"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
