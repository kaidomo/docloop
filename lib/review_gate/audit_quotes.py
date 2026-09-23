#!/usr/bin/env python3
"""audit_quotes.py — 인용 전건 재대조 검산기 (오귀속 검출 fail-closed 가드).

합성 산출물이 finding 근거로 실은 인용이 **대상 문서 원문에 실제로 존재하는지**,
그리고 **적힌 앵커 행에 있는지**를 기계 대조한다.

지위(CONTRACT §3): 이것은 **오귀속 검출 가드**다 — 렌즈·병합이 인용을 옮기며
볼드를 덧붙이거나, 말줄임으로 자르거나, 없는 자리를 지목한 것을 잡는다.
인용이 '존재'하는지만 본다(그 인용이 지적을 뒷받침하는지는 사람 판단이다).

**왜 필요한가**: 확신도는 정확성을 보증하지 않는다 — 독립 3회가 **같은 방향으로**
오인용한 실측이 있다(docauth#197). 실행마다 임시 스크립트로 대조하면 검사기 자체의
오탐과 산출물 결함을 구분할 수 없다(docauth#201: 자작 감사기 오탐 22/24 실측).

**두 가지 실패가 모두 치명적이다**:
- 거짓 MISS — 멀쩡한 인용을 오귀속이라 부른다. 이 도구의 존재 이유가 이걸 없애는 것이다.
- 거짓 OK — 날조된 인용을 통과시킨다. 검산기가 있다는 사실이 오히려 신뢰를 준다.
그래서 파서는 **인용 형태를 하나도 버리지 않는다**(피어리뷰 r1-03·r1-04).

**앵커 형식(docauth#207 2안)**: 새 실행은 행 번호(`L37`) 대신 **행 내용의 안정
식별자**(`A` + 그 행 정규화 텍스트의 sha256 앞 12자, 예: `Ae3b0c44298f`)를 쓴다 —
줄 번호는 문서가 편집돼 다른 줄이 삽입·삭제되면 그 줄이 밀리는 순간 의미를 잃지만,
내용 해시는 **그 줄 자체가 편집되지 않는 한** 몇 줄이 밀리든 그대로 찾힌다. 레거시
`L\\d+` 앵커는 이미 종결된 기록에서 계속 유효하며(소급 무효화 없음), 이 도구는 두
형식을 **동시에** 파싱한다. 새 앵커 값을 구할 때는 `--emit-anchors`를 쓴다.

사용:
  audit_quotes.py SYNTH.md --source 대상문서.md [--source-alt 다른판.md]
                  [--item-re '^### (F-\\S+)'] [--quote-field 인용] [--anchor-field 앵커]
  audit_quotes.py --emit-anchors 대상문서.md   # 각 행의 A<hash> 앵커 값을 조회(저작용)

대조 규칙:
  - 공백만 정규화하고 **볼드·문장부호는 보존**한다 — 표기 자체가 판정 대상이라서다.
  - **여러 줄에 걸친 인용도 찾는다** — 원문을 줄 단위로만 보면 줄바꿈을 넘는 인용이
    전부 MISS 가 된다(r1-01).
  - 한 항목에 여러 인용을 묶은 형태(`- L12: "..." - L20: "..."`)를 분해하되,
    **인용 안에 있는 `- L12:` 문자열은 건드리지 않는다**(r1-02).
  - ASCII·한글 따옴표·라벨 구분 조각을 **전부** 대조한다. 한 형태가 다른 형태를
    덮지 않는다(r1-03).
  - 짧은 인용도 버리지 않는다. 인용 필드가 있는데 대조 단위를 못 만들면 **사용 오류**로
    끝낸다 — 조용히 통과시키지 않는다(r1-04).
  - 앵커의 범위 표기(`L106~L111`·`L106-L111`)를 전개하되 **역순·비양수 범위는 거절**한다(r1-06).
  - `--source-alt` 를 주면 원문이 개정된 경우(**판본 불일치**) 다른 판본에서도 찾아
    `개정본에만 있음`으로 구분한다(CONTRACT §7 계열, docauth#202).
    ⚠️ 계약 §4.1 의 `drift`(표기 드리프트 — 값은 같은데 표기가 갈리는 **출력 범주**.
    finding 과 나란한 별개 outcome 이라 severity·status·blocking 이 없다)와
    **다른 개념이다.** 이 검산기는 그 낱말을 쓰지 않는다.

**판정 범위(Codex 피어리뷰 r9-02)**: 모든 계수의 분모는 `--item-re`가 **추출한 항목**이다.
정규식이 못 잡은 항목은 애초에 보이지 않으므로, exit 0은 "이 산출물 전체가 검사됐다"가
아니라 "추출된 항목 전건이 검사됐다"를 뜻한다. 문서 전체 커버리지를 기계로 보장하려면
호출자가 기대 항목 수·id를 함께 넘겨 대조해야 하고, 그건 이 도구의 계약 밖이다.

종료 코드: **0 = 가드 충족**(판정문은 `QUOTE-OK` 또는 `QUOTE-N/A`) ·
1=QUOTE-FAIL(원문 미존재) · 2=사용 오류(인용 파싱 실패 포함)
· **3=QUOTE-NOTRUN(검사되지 않은 항목이 있다, docauth#347)**.
3은 대조 0건뿐 아니라 **일부만 대조된 경우**도 포함한다: `compared > 0`이면
통과시키던 이전 동작은 33종 중 한 곳만 우연히 필드 문법이 맞아도 나머지 32종을
검사하지 않고 QUOTE-OK를 냈다. 계약 §4가 finding에 근거 인용을 의무 필드로
두므로 기본은 **전건 대조**이며, 인용이 없는 것이 정상인 question·drift 항목은
`--no-quote-ok <id>`로 **id 하나씩** 선언하고 그 id가 판정문에 전부 남는다
(전역 플래그면 정상 question 때문에 켜는 순간 finding의 무인용까지 통과한다).
모든 항목이 그렇게 선언되면 대조할 인용이 아예 없으므로 **exit 0 · QUOTE-N/A** —
통과가 아니라 '해당 없음'이다(계약이 허용하는 drift-only 산출물 등).
**호출자 계약**(Codex 피어리뷰 r3-04): 종료 코드 0은 "가드 충족"이고 판정 토큰은
`QUOTE-OK`와 `QUOTE-N/A` **둘 다**다 — 토큰으로 `QUOTE-OK`만 찾는 호출자는 정상
N/A를 거부하고, 종료 코드만 보는 호출자는 N/A를 통과와 구별하지 못한다. 두 상태를
모두 명시적으로 처리하라. `QUOTE-NOTRUN`(3)은 전달 금지다.
**면제 선언의 지위**: `--no-quote-ok`는 **호출자 자기선언이며 이 도구가 항목 유형을
검증하지 않는다** — 이 도구는 `--item-re`로 나눈 항목만 볼 뿐 무엇이 finding이고
무엇이 question·drift인지 모른다. 유형까지 기계로 막으려면 원장/범주 map을 입력으로
받아야 하고, 그건 별건이다.

판정 순서: 파싱 실패(2) → 미검사 항목 존재(3) → 순수 오귀속(1) → 통과(0).
오귀속과 미검사가 함께 있으면 **미검사가 이긴다** — 호출을 고쳐 다시 돌리는 것이
먼저이고, 인용 수정 루프는 그 다음이다.
3은 "대조했는데 틀렸다"(1)와 구분된다: 항목은 읽었으나 **추출된 항목 중 하나 이상이**
원문과 대조되지 않은 상태이며(전량 미대조만이 아니다 — Codex 피어리뷰 r10-03),
처방이 다르다(1=산출물 수정, 3=selector 불일치나 필수 필드 누락 — 즉
`--item-re`/`--quote-field` 인자가 산출물 표기와 맞는지, 항목에 인용 필드가 실제로
있는지 확인). 0건 검사에 OK를 내면 fail-closed 가드가
**아무 근거 없이 통과 판정을 내는 구멍**이 된다 — 2026-09-07 실행에서 세 번
발생했고 세 번 다 사람이 항목 수를 눈으로 보고 잡았다. 통과·실패 문구는
**언제나 실제 검사 건수를 싣는다**(같은 이슈의 공통 조항).
앵커 불일치·앵커 소멸·앵커 모호는 FAIL 이 아니라 경고다 — 인용은 존재하되 위치
표기만 어긋나거나(불일치) 그 앵커가 가리키던 행 자체가 더는 원문에 없거나(소멸)
같은 내용의 행이 여럿이라 특정 행을 못 정한(모호) 것이라 수정 대상이 다르다.
**알려진 한계**: 안정 식별자도 그 행 내용이 실제로 편집되면
당연히 못 찾는다 — 그 정도 변경은 대조 대상이 사실상 새 문서라는 뜻이며, 이 도구는
그런 대규모 편집을 억지로 따라잡으려 하지 않는다.
"""
from __future__ import annotations

import argparse
import bisect
import hashlib
import re
import sys
from pathlib import Path

ANCHOR_HASH_LEN = 12                    # digest() 앞 12자 관례와 동일 길이
HASH_TOKEN = rf"A[0-9a-f]{{{ANCHOR_HASH_LEN}}}"
# Codex r1-01: 경계 없이 A[0-9a-f]{12}만 쓰면 13자리 이상 16진수 잇단 문자열이나
# 다른 식별자에 파묻힌 조각(`XA0123...`)의 앞 12자를 유효 토큰으로 잘못 집는다.
# 오른쪽 경계는 **16진수뿐 아니라 식별자를 이루는 문자 전부**를 막는다(Codex
# r2-01) — `(?![0-9a-f])`만으로는 "A0123456789abG"의 G(비16진수 글자)가 경계를
# 통과시켜 앞 12자를 완결된 토큰으로 오인했다. 하이픈은 오른쪽에서도 막는다 —
# `HASH_TOKEN_BOUNDED`는 **독립된 단일 토큰**(HASH_SINGLE)에만 쓰고, 범위
# 표기에는 아래처럼 **바깥쪽 끝에만** 경계를 건다.
# Codex r6-01 최초 시도(정규식 lookbehind로 물결·en-dash도 막기)는 r7-01에서
# 재발했다: 구분자와 토큰 사이에 공백이 끼면(`\s*`, 가변 길이) Python `re`의
# **고정폭** lookbehind로는 "구분자 뒤 임의 개수 공백"을 표현할 수 없다 — 정규식
# 문제가 아니라 정규식 엔진 자체의 근본 제약이다. 그래서 "이 위치 바로 앞이
# 구분자(공백 있어도 됨)로 끝나는가"는 정규식이 아니라 **파이썬 코드로 직접
# 뒤로 훑어** 판정한다(아래 `_separator_precedes`). LEFT_BOUND는 원래 목적
# (Codex r1-01 — 긴 식별자에 파묻힌 조각을 토큰으로 오인하지 않기)로만 남기고
# 식별자 문자만 막는다.
LEFT_BOUND = r"(?<![A-Za-z0-9_])"
RIGHT_BOUND = r"(?![A-Za-z0-9_])"
HASH_TOKEN_BOUNDED = rf"{LEFT_BOUND}{HASH_TOKEN}{RIGHT_BOUND}"
SEPARATOR_CHARS = "~-–"


def _separator_precedes(text: str, pos: int) -> bool:
    """Codex r6-01/r7-01/r8-01: `pos` 바로 앞의 공백을 건너뛰어, 그 자리가 범위
    구분자(~·-·–)로 끝나는지 본다. 구분자와 토큰 사이 공백은 `\\s*`로 가변
    길이라 정규식 고정폭 lookbehind로 이 조건을 표현할 수 없어(Python `re`의
    근본 제약) 파이썬으로 직접 뒤로 훑는다. **건너뛰는 "공백"은 `str.isspace()`
    기준이다**(r8-01) — 정규식 쪽 `\\s*`가 스페이스·탭·개행뿐 아니라 NBSP
    (U+00A0)·수직 탭·form feed 등 유니코드 공백 전부를 인정하는데, 처음엔 이
    함수가 스페이스·탭만 건너뛰어 그 외 공백이 낀 변형이 경계를 우회했다 —
    `str.isspace()`가 정규식 `\\s`와 같은 문자 집합을 인정하므로 둘을 맞췄다.
    이 검사가 막는 것은 "이 해시가 실은 앞선(어쩌면 깨진) 표현의 연속일 수
    있는 자리에서 새 범위·단일 앵커를 독립적으로 다시 찾아내는 것" — 무관한
    내용을 통째로 span에 끌어들이는 거짓 넓은 span 경로다. `consumed`가 이미
    다른 매치로 공백 처리된 자리는 당연히 구분자로 안 잡힌다(그 앞선 표현은
    이미 처리·거절이 끝난 것이므로 더는 "연속"이 아니다).
    """
    i = pos - 1
    while i >= 0 and text[i].isspace():
        i -= 1
    return i >= 0 and text[i] in SEPARATOR_CHARS
WS = re.compile(r"\s+")
# Codex r3-01: 범위(`A<hash1>-A<hash2>`, 하이픈 무공백)에서 두 끝점 각각에
# HASH_TOKEN_BOUNDED(양쪽 경계 포함)를 그대로 썼더니, 두 번째 끝점 **바로 앞
# 글자가 구분자 하이픈 자신**이라 그 왼쪽 경계가 항상 실패해 두 번째 끝점이
# 아예 매치되지 않았다(범위 전체가 거절되지 않고 첫 끝점만 단일 앵커로 조용히
# 살아남는 조용한 오귀속). 경계는 **범위 표현 전체의 바깥쪽 끝**(첫 끝점 앞·
# 마지막 끝점 뒤)에만 걸고, 두 끝점 사이의 구분자는 그 자체로 이미 경계
# 역할을 한다(구분자 뒤에 다른 유효한 시작 문자 'A'가 오지 않으면 전체 매치가
# 그 자리에서 실패하므로 별도 lookaround 없이도 잘못된 접두 매치가 새지 않는다).
#   Codex r4-02: 라벨의 여는 `-\s*`는 이미 그 자체로 왼쪽 경계 역할을 한다(레거시
#   `L\d+` 가지도 별도 lookbehind 없이 이 접두만으로 충분한 것과 같은 이유) — 첫
#   해시 토큰에 LEFT_BOUND를 또 씌우면, 공백 없는 라벨("-A<hash>:")에서 해시
#   바로 앞 글자가 그 라벨 자신의 여는 하이픈이라 왼쪽 경계가 항상 실패해 공백
#   없는 형태가 레거시와 달리 통째로 안 갈라진다. 첫 토큰에는 LEFT_BOUND를
#   붙이지 않고, 범위 끝(오른쪽)에만 RIGHT_BOUND를 건다.
LABEL = re.compile(
    rf"-\s*(?:L\d+(?:\s*[~\-–]\s*L?\d+)?"
    rf"|{HASH_TOKEN}(?:\s*[~\-–]\s*{HASH_TOKEN})?{RIGHT_BOUND})\s*:"
)
RANGE = re.compile(r"L(\d+)\s*[~\-–]\s*L?(\d+)")
SINGLE = re.compile(r"L(\d+)")
HASH_RANGE = re.compile(rf"{LEFT_BOUND}({HASH_TOKEN})\s*[~\-–]\s*({HASH_TOKEN}){RIGHT_BOUND}")
# Codex r4-01: HASH_RANGE 전체가 실패하면(둘째 끝점이 자리수 미달·비16진수 접미
# 등으로 유효하지 않으면) 첫 끝점만 남아 있다가 뒤이은 HASH_SINGLE 패스에서
# "독립 단일 앵커"로 조용히 되살아난다 — 범위를 쓰려던 의도가 첫 줄만 지목한
# 것으로 조용히 축소된다. HASH_RANGE 처리 후 남은 "해시+구분자" 잔재를 여기서
# 걷어내 명시적으로 거절한다(뒤 HASH_SINGLE 패스가 못 줍게 소비도 같이 한다).
HASH_RANGE_LEAD = re.compile(rf"{LEFT_BOUND}{HASH_TOKEN}\s*[~\-–]\s*")
HASH_SINGLE = re.compile(HASH_TOKEN_BOUNDED)
ELLIPSIS = re.compile(r"…|\.\.\.")
SEP = "\x00"


def anchor_hash(text: str) -> str:
    """행 내용 → 안정 식별자(#207 2안). `norm()`으로 정규화한 텍스트에 대해서만 쓴다 —
    같은 행이 공백만 다르게 적혀도 같은 앵커를 내야 편집 도구·수기 표기 차이로
    거짓 소멸이 나지 않는다."""
    return "A" + hashlib.sha256(text.encode("utf-8")).hexdigest()[:ANCHOR_HASH_LEN]


def norm(text: str) -> str:
    """비교용 정규화 — 공백만 정리(볼드·문장부호 보존)."""
    return WS.sub(" ", text).strip()


#: Codex 피어리뷰 r8-02 → r9-04: 입력을 **실행당 한 번만** 읽고 그 바이트로 해시·decode·
#: 파싱을 전부 한다. 이전에는 `Source`가 `read_bytes()`로 해시한 뒤 `read_text()`로 다시
#: 읽고, synth도 파싱한 뒤 `digest(synth)`에서 재독했다 — 그 사이 파일이 바뀌면 **판본 A의
#: 해시로 판본 B를 대조**했다.
#: r9-04: 캐시가 **모듈 수명**이면 같은 프로세스의 재검산이 수정 전 바이트를 계속 쓰고,
#: 키를 매번 `resolve()`하면 symlink 재지정에 키가 갈려 두 번 읽는다. 캐시는 **실행 단위**로
#: 비우고, 키는 **호출자가 준 경로 문자열 그대로**(재해석 없음) 쓴다.
_BYTES_CACHE: dict = {}


def reset_read_cache() -> None:
    _BYTES_CACHE.clear()


def read_input_bytes(path: Path) -> bytes:
    key = str(path)
    if key not in _BYTES_CACHE:
        _BYTES_CACHE[key] = Path(path).read_bytes()
    return _BYTES_CACHE[key]


def read_input_text(path: Path) -> str:
    """해시는 **원시 바이트**로, 파싱은 **universal-newline 정규화 텍스트**로.

    Codex 피어리뷰 r9-03: 기존 `Path.read_text()`는 lone `\r`도 줄바꿈으로 바꿨는데
    `bytes.decode()`는 그대로 남긴다. `re.M`과 필드 경계는 `\n`만 인식하므로, CR 산출물이
    항목·필드를 통째로 놓쳐 exit 2/3으로 퇴행한다.
    """
    return read_input_bytes(path).decode("utf-8").replace("\r\n", "\n").replace("\r", "\n")


def digest(path: Path) -> str:
    """파일 content hash 앞 12자 — 이 검산이 어느 판본에 대한 질의였는지 남긴다."""
    return hashlib.sha256(read_input_bytes(path)).hexdigest()[:12]


class Source:
    """원문 색인 — 줄 경계를 넘는 인용도 찾을 수 있도록 통짜 텍스트 + 행 매핑을 든다(r1-01)."""

    def __init__(self, path: Path):
        self.path = path
        self.digest = digest(path)
        raw_lines = read_input_text(path).splitlines()
        self.lines = [norm(l) for l in raw_lines]
        parts, starts, pos = [], [], 0
        for idx, line in enumerate(self.lines):
            starts.append(pos)
            parts.append(line)
            pos += len(line) + 1          # 구분 공백 1칸
        self.joined = " ".join(parts)
        self._starts = starts
        self._cache: dict[str, set[int]] = {}
        # #207 2안: 행 내용 해시 → 그 내용을 가진 현재 행 번호(들). 안정 식별자
        # 앵커를 조회할 때 쓴다 — 여러 행이 우연히 같은 정규화 텍스트면(예: 짧은
        # 반복 문구) 집합으로 모아 둔다.
        self.hash_index: dict[str, set[int]] = {}
        for i, line in enumerate(self.lines, start=1):
            self.hash_index.setdefault(anchor_hash(line), set()).add(i)

    def __len__(self) -> int:
        return len(self.lines)

    def _line_of(self, offset: int) -> int:
        return bisect.bisect_right(self._starts, offset)   # 1-based

    def hits(self, fragment: str) -> set[int]:
        """fragment 가 걸치는 모든 행 번호. 줄바꿈을 넘는 인용은 걸친 행 전부를 돌려준다."""
        if fragment in self._cache:
            return self._cache[fragment]
        found: set[int] = set()
        start = self.joined.find(fragment)
        while start != -1:
            end = start + len(fragment) - 1
            found |= set(range(self._line_of(start), self._line_of(end) + 1))
            start = self.joined.find(fragment, start + 1)
        self._cache[fragment] = found
        return found


class QuoteState:
    """인용 상태(docauth#364 — 인용 필드 payload 시작에서 리셋). unescaped ASCII `"`는 토글,
    `“`는 depth+1, `”`는 depth-1(0 하한). 휴리스틱 없음(설계 r1-01: 공백 조건은 새 거짓 QUOTE-OK를 연다)."""

    __slots__ = ("ascii_open", "hangul_depth")

    def __init__(self) -> None:
        self.ascii_open = False
        self.hangul_depth = 0

    @property
    def inside(self) -> bool:
        return self.ascii_open or self.hangul_depth > 0

    def feed(self, text: str) -> None:
        backslashes = 0
        for ch in text:
            if ch == "\\":
                backslashes += 1
                continue
            if ch == '"' and backslashes % 2 == 0:
                self.ascii_open = not self.ascii_open
            elif ch == "\u201c":
                self.hangul_depth += 1
            elif ch == "\u201d":
                self.hangul_depth = max(0, self.hangul_depth - 1)
            backslashes = 0


class Field:
    """스캐너가 낸 필드(옛 `FIELD_LINE_RE` 매치 객체 대체 — `group("label"/"payload")` 호환)."""

    __slots__ = ("label", "payload", "is_quote", "unterminated")

    def __init__(self, label: str, payload: str, is_quote: bool, unterminated: bool) -> None:
        self.label = label
        self.payload = payload
        self.is_quote = is_quote
        self.unterminated = unterminated

    def group(self, name: str) -> str:
        return self.label if name == "label" else self.payload


def iter_fields(body: str, quote_matcher) -> list:
    """항목 본문 → 필드 목록 (docauth#364 — 인용 필드별 **흡수** 스캐너, 설계 v3).

    - 후보 경계는 현행 문법 그대로(`-[ \t]*<label>:` 행, `#` 행).
    - **인용 필드**(라벨이 `quote_matcher`에 접두 매치)만, 자기 payload 안에서 열린 인용이 닫히지
      않은 동안 뒤따르는 **비인용** 후보 행을 흡수한다(그 행은 인용 본문이 되어 대조된다).
    - **인용 후보 행은 절대 흡수하지 않는다**(설계 r2-01): 열린 상태에서 그런 행을 만나면 흡수를 멈추고
      그 필드를 `unterminated`로 확정한다 — 잘린 인용은 통과하지 않는다(exit 2).
    - 비인용 필드는 흡수하지 않는다(설계 r1-02): 그 안의 따옴표는 다음 필드에 영향이 없다.
    - 항목 끝에서 열린 인용 필드도 `unterminated`(inch 표기 `12"`의 홀수 따옴표 포함 — 현행 fail-closed 유지, 공시).
    """
    lines = body.split("\n")
    fields: list = []
    i = 0
    n = len(lines)
    while i < n:
        m = _CANDIDATE_RE.match(lines[i])
        if m is None:
            i += 1
            continue
        label = m.group("label")
        is_quote = _label_matches(quote_matcher, m)
        payload_lines = [m.group("rest")]
        state = QuoteState()
        unterminated = False
        if is_quote:
            state.feed(m.group("rest"))
        i += 1
        while i < n:
            line = lines[i]
            cm = _CANDIDATE_RE.match(line)
            if cm is not None or line.startswith("#"):
                if not (is_quote and state.inside):
                    break  # 인용 밖의 후보 경계 — 이 필드 끝
                if line.startswith("#") or _label_matches(quote_matcher, cm):
                    unterminated = True  # 인용 후보 행·절 경계는 흡수하지 않는다 — 잘린 인용
                    break
            payload_lines.append(line)
            if is_quote:
                state.feed(line)
            i += 1
        if is_quote and state.inside:
            unterminated = True
        fields.append(Field(label, "\n".join(payload_lines), is_quote, unterminated))
    return fields


def _unterminated_quote(payload: str) -> bool:
    """payload에 **닫히지 않은 따옴표**가 남았는가 (Codex 피어리뷰 r10-01 → docauth#364).

    r10-01 시점에는 정규식 경계(`_FIELD_END`)가 "지금 인용 안인가"를 몰라 여러 줄 인용
    한가운데의 `- key: ...` 행에서 payload가 끊겼고, 이 함수는 그 절단 결과를 exit 2로
    거부하는 **임시 방어**였다. #364가 경계 계산을 `iter_fields`의 인용 상태 추적으로
    바꿨으므로(인용 필드가 열린 인용 안의 비인용 후보 행을 흡수), 이제 여기 남는
    미종결은 진짜 미종결이다 — 인용 후보 행·절 경계에서 멈춘 잘린 인용, 항목 끝까지
    닫히지 않은 인용, 홀수 따옴표.

    한계(공시): inch 표기(`12"`)처럼 정당한 홀수 `"`도 거부된다 — 어휘 휴리스틱은 새
    거짓 QUOTE-OK를 열기 때문에 두지 않는다(설계 r1-01). 그 필드는 `--no-quote-ok`가
    아니라 표기를 고쳐 푼다(단위는 인용 밖에 두거나 `＂`로). 역순 한글 따옴표(`”Org“`)는
    개수가 같아도 depth 추적상 열린 것이라 거부한다(0.33 「#364 닫힘」의 신규 거부 1종).
    """
    # docauth#364: 경계 계산은 `iter_fields`가 상태 추적으로 한다. 이 함수는 그 결과(흡수 뒤 payload)에
    # 대한 **같은 상태 기계의 종료 검사**로 남는다 — 역순 한글 따옴표(`”Org“`)는 depth 추적상 열린 것이라
    # 거부한다(0.33 「#364 닫힘」에 기록된 신규 거부 1종).
    state = QuoteState()
    state.feed(payload)
    return state.inside


def _extract_quoted(raw: str) -> tuple[list[str], str]:
    """`QuoteState` 규칙으로 인용 span을 떼어낸다 → (인용 본문 목록, 인용을 SEP로 치환한 나머지).

    밖→안 전이에서 span이 열리고 안→밖 전이에서 닫힌다(여는·닫는 따옴표는 본문에 넣지 않는다).
    중첩 한글 따옴표는 depth가 0으로 돌아올 때까지 한 span이라 안쪽 `“…”`는 본문에 남는다.
    escape된 `\"`는 본문에 `"`로 들어간다(escape 백슬래시는 소비). 열린 채 끝나는 span은
    호출자가 `_unterminated_quote`로 이미 거부했으므로 여기서는 버린다.
    """
    state = QuoteState()
    quoted: list[str] = []
    outside: list[str] = []
    current: list[str] = []
    backslashes = 0
    for ch in raw:
        if ch == "\\":
            backslashes += 1
            continue
        was_inside = state.inside
        is_quote_char = ch == '"' and backslashes % 2 == 0
        if ch == '"' and not is_quote_char:
            backslashes -= 1  # escape 백슬래시 소비 — 나머지는 본문
        pending = "\\" * backslashes
        backslashes = 0
        if is_quote_char:
            state.ascii_open = not state.ascii_open
        elif ch == "\u201c":
            state.hangul_depth += 1
        elif ch == "\u201d":
            state.hangul_depth = max(0, state.hangul_depth - 1)
        transition_char = is_quote_char or ch in "\u201c\u201d"
        if not was_inside and state.inside:
            outside.append(pending)
            current = []
            continue
        if was_inside and not state.inside:
            current.append(pending)
            quoted.append("".join(current))
            outside.append(SEP)
            current = []
            continue
        (current if state.inside else outside).append(pending + ch)
    outside.append("\\" * backslashes)
    return quoted, "".join(outside)


def split_quotes(raw: str) -> list[str]:
    """인용 필드 → 대조 단위 리스트.

    ① 따옴표로 묶인 조각을 **먼저** 떼어낸다 — 그래야 인용 본문 안의 `- L12:` 가
       라벨로 오인되지 않는다(r1-02).
    ② **ASCII·한글 따옴표를 함께** 수집한다 — 한 형태가 다른 형태를 덮지 않는다(r1-03).
    ③ 따옴표 조각이 하나라도 있으면 **그것만** 대조 단위로 쓴다. 따옴표 밖의 연결어
       ("그리고" 등)는 인용이 아니라 서술이라, 대조 단위로 삼으면 거짓 MISS 가 난다.
    ④ 따옴표가 하나도 없을 때만 라벨 구분 조각으로 넘어간다.
    ⑤ 길이 하한을 두지 않는다 — 짧은 인용도 대조 대상이다(r1-04).
    """
    # docauth#364 구현 r1-01: 추출도 경계와 **같은 상태 기계**(`QuoteState` — escape 홀짝·한글 depth)를
    # 쓴다. 정규식 `QUOTED`는 `\"`를 먼저 지워 흡수된 뒷부분(`"Org\"` 뒤 `- key: 날조"`)을 버렸고,
    # 중첩 `“…”`에서 첫 닫힘 뒤를 놓쳤다 — 경계가 "인용 안"으로 본 문자는 전부 대조 단위에 들어간다.
    quoted, remainder = _extract_quoted(raw)
    remainder = LABEL.sub(SEP, remainder)

    pieces: list[str] = []
    for chunk in (quoted if quoted else remainder.split(SEP)):
        for piece in ELLIPSIS.split(chunk):
            piece = norm(piece.strip(' "“”'))
            piece = piece.strip("-–— \t")
            if piece and not re.fullmatch(r"[\W_]+", piece):
                pieces.append(piece)
    # 중복 제거(순서 보존)
    return list(dict.fromkeys(pieces))


def parse_anchors(
    raw: str, source: Source
) -> tuple[set[int], list[str], list[str], list[str]]:
    """앵커 필드 → (행번호 집합, 거절 사유 목록, 소멸된 안정 식별자 목록, 모호한 식별자 목록).

    레거시 `L\\d+`는 그대로 행 번호로 쓴다(역순·비양수 범위는 거절, r1-06). 신규
    `A<hash>`는 `source.hash_index`에서 **현재** 행 번호로 조회한다 — 그 행 내용이
    편집·삭제돼 더는 존재하지 않으면 앵커 집합에 넣지 않고 "소멸"로 남긴다(#207 2안).
    범위(`A<hash1>~A<hash2>`)는 두 끝점을 각각 조회한 뒤 그 **현재** 행 번호 사이를
    span으로 잡는다 — 원 저작 시점의 거리가 아니라 현재 문서에서의 실제 위치로
    다시 계산한다. **동일 내용의 중복 행**(Codex r1-02)은 단일 앵커에서는 후보 전부를
    앵커에 넣고 "모호"로 공시하지만, 범위 끝점이 중복이면 min..max span이 우연히 먼
    중복 행까지 끌고 와 무관한 내용을 통째로 삼킬 수 있으므로 그 범위는 **계산을
    거부**하고 "소멸"로 남긴다.

    **연속 자리 방어(Codex r6-01·r7-01·r8-01)**: 어떤 범위·단일 앵커 후보든, 그 시작
    위치 바로 앞이 (공백을 사이에 두고도) 구분자(`~`·`-`·`–`)로 끝나면 무시한다
    (`_separator_precedes`). 그렇지 않으면 "A<깨진 해시>d~A<h2>-A<h10>" 같은
    입력에서 첫 끝점이 깨져 앞쪽 시도가 실패해도, `HASH_RANGE`가 문자열 아무
    데서나 다시 스캔하다가 h2 바로 앞의 '~'를 경계로 통과시켜 "A<h2>-A<h10>"를
    **완전히 무관한 별개의 유효 범위**로 우연히 찾아내 min..max span(무관한 중간
    행 전부)을 앵커로 삼는다 — r1-02·r4-01이 막으려던 "거짓 넓은 span" 실패가
    조합을 통해 되살아나는 것이다. **처음엔 정규식 `LEFT_BOUND`에 구분자를
    추가해 막으려 했으나(r6-01) 구분자·토큰 사이 공백은 `\\s*`로 가변 길이라
    Python `re`의 고정폭 lookbehind로는 이 조건 자체를 표현할 수 없어 공백이 낀
    변형에서 재발했다(r7-01)** — 그래서 이 검사는 정규식이 아니라 파이썬 코드로
    뒤로 훑는다. 부수 효과: 해시가 **어떤 구분자로도(공백 있어도) 바로 앞이
    시작되는 자리**(예: 셋 이상 잇는 연쇄 `A<h1>~A<h2>~A<h3>`의 `A<h3>`)는 범위
    첫 끝점 후보에서도 독립 단일 앵커 후보에서도 빠진다 — 애매한 경우를 통째로
    무시하는 쪽이 무관한 넓은 span에 섞어 넣는 쪽보다 항상 안전하다.

    **알려진 한계(더 패치하지 않고 공시 — CONTRACT §12.6 ⓔ의 "공시 + 확장 중단"
    처방. 잔여 실패의 방향이
    안전하다: 최악이 "의도보다 적게 인정"이지 무관한 내용을 끌어들이는 것이
    아니다. match_review_rounds.py와 같은 갈래)**: 이 함수는 순차적으로
    조각을 "소비"해 가는 여러 정규식 패스의 나열이지, 앵커 표기 전체를 하나의
    닫힌 문법으로 파싱하는 것이 아니다. **셋 이상을 잇는 연쇄 표현**(`A<h1>~A<h2>~
    A<h3>`, `A<h1>-A<h2>-A<h3>` 등, 구분자 앞뒤 공백 유무 무관)은 위 연속 자리
    방어 덕에 **일관되게** 첫 번째 쌍만 범위로 인정하고 그 뒤 이어지는 항목은
    조용히 빠진다(r6-01 이전에는 구분자 종류·공백 유무에 따라 결과가 갈렸다 —
    지금은 항상 같은 축소다). **거짓 넓은 span을 만들지는 않는다** — 최악의
    결과는 "의도한 것보다 적게 인정됨"이지 "무관한 내용을 끌어들임"이 아니다.
    셋 이상 잇는 표기 자체를 정식 지원하려면 조각별 정규식 스캐빈징이 아니라
    앵커 표기를 하나의 문법으로 보는 파서가 필요하며, 그건 이 PR(#207 2안)의
    범위를 넘는 별도 재설계 사안이다 — 실제로 문제가 되면(혼합·연쇄 표기가 실사용에서
    관측되면) 그때 후속 이슈로 등록해 다룬다.
    """
    anchors: set[int] = set()
    rejected: list[str] = []
    vanished: list[str] = []
    ambiguous: list[str] = []
    consumed = raw
    # Codex r5-02: `consumed.replace(m.group(0), " ", 1)`는 **텍스트 검색**으로 첫
    # 등장을 지운다 — `m`이 두 번째(또는 그 이후) 등장을 가리켜도, 같은 리터럴
    # 문자열이 앞쪽에 한 번 더 있으면 엉뚱한(더 앞선) 자리를 지워버린다. 매치가
    # 실제로 위치한 구간(`m.start()`~`m.end()`)만 공백으로 지운다 — 문자열 동일성이
    # 아니라 **위치**로 소비한다.
    def _blank(text: str, match: re.Match) -> str:
        return text[: match.start()] + " " * (match.end() - match.start()) + text[match.end() :]

    for m in RANGE.finditer(raw):
        lo, hi = int(m.group(1)), int(m.group(2))
        if lo <= 0 or hi <= 0 or hi < lo:
            rejected.append(m.group(0))
        else:
            anchors |= set(range(lo, hi + 1))
        consumed = _blank(consumed, m)
    for m in HASH_RANGE.finditer(consumed):
        if _separator_precedes(consumed, m.start()):
            # Codex r6-01/r7-01: 이 범위의 시작이 (공백을 사이에 두고도) 구분자로
            # 끝나는 자리라면, 앞선 표현의 연속일 수 있는 자리에서 독립적인 범위를
            # 새로 찾아낸 것이다 — 무관한 span을 만들 위험이 있으니 통째로 무시한다.
            consumed = _blank(consumed, m)
            continue
        lines_lo = source.hash_index.get(m.group(1))
        lines_hi = source.hash_index.get(m.group(2))
        if not lines_lo or not lines_hi:
            vanished.append(m.group(0))
        elif len(lines_lo) > 1 or len(lines_hi) > 1:
            # Codex r1-02: 끝점 중 하나가 중복 내용(동일 정규화 텍스트를 가진 행이
            # 여럿)이면 min..max span이 그 중복이 우연히 멀리 있는 행까지 끌고 와
            # 무관한 내용을 통째로 앵커로 삼을 수 있다 — 계산을 아예 거부한다.
            vanished.append(f"{m.group(0)}(양끝 중 하나가 중복 행이라 범위 계산 거부)")
        else:
            span = lines_lo | lines_hi
            anchors |= set(range(min(span), max(span) + 1))
        consumed = _blank(consumed, m)
    for m in HASH_RANGE_LEAD.finditer(consumed):
        if _separator_precedes(consumed, m.start()):
            # r6-01/r7-01과 같은 이유 — 이 lead 자신이 앞선 표현의 연속 자리일
            # 수 있으므로 통째로 무시한다(거절 사유도 남기지 않는다 — 이미 앞선
            # 표현 쪽에서 처리·거절됐거나 될 자리다).
            consumed = _blank(consumed, m)
            continue
        # 온전한 HASH_RANGE로 이미 소비되지 않고 남은 "해시+구분자"는 둘째 끝점이
        # 깨진 범위 표기다 — 첫 끝점이 뒤이은 HASH_SINGLE에 홀로 주워지지 않도록
        # 여기서 거절하고 소비한다(r4-01).
        rejected.append(f"{m.group(0).strip()}...(범위처럼 시작했으나 둘째 끝점이 유효한 안정 식별자가 아님)")
        consumed = _blank(consumed, m)
    for m in SINGLE.finditer(consumed):          # 범위로 소비된 부분은 제외하고 단일 앵커 수집
        value = int(m.group(1))
        if value > 0:
            anchors.add(value)
        else:
            rejected.append(m.group(0))
    for m in HASH_SINGLE.finditer(consumed):
        if _separator_precedes(consumed, m.start()):
            # r6-01/r7-01: 이 해시 바로 앞이 (공백 있어도) 구분자로 끝나면 앞선
            # (어쩌면 깨진) 표현의 연속 자리일 수 있다 — 독립 단일 앵커로 주워
            # 가지 않는다. 애매한 자리를 통째로 무시하는 쪽이 무관한 행을 섞어
            # 넣는 쪽보다 항상 안전하다.
            continue
        lines = source.hash_index.get(m.group(0))
        if lines:
            anchors |= lines
            if len(lines) > 1:
                # 여러 행이 같은 내용이라 이 앵커 하나로는 어느 행인지 못 정한다.
                # 후보 전부를 앵커에 넣어 두되(각 행이 정말 그 내용을 담고 있으므로
                # anchors∩hits 판정을 거짓 불일치로 몰지는 않는다) 모호함은 공시한다.
                ambiguous.append(f"{m.group(0)} → 행 {sorted(lines)}(동일 내용 중복 — 특정 행 미확정)")
        else:
            vanished.append(m.group(0))
    return anchors, rejected, vanished, ambiguous


def emit_anchors(source: Source) -> str:
    """각 행의 안정 식별자를 조회용으로 출력한다(#207 2안 저작 도우미).

    앵커 값은 사람이 암산으로 못 구하므로, 저작 시점에 이 표를 보고 인용하려는
    행의 `A<hash>` 값을 그대로 옮겨 적는다."""
    lines = [f"SRC: {source.path}  {len(source)}행  sha256 {source.digest}"]
    for i, norm_line in enumerate(source.lines, start=1):
        preview = norm_line if len(norm_line) <= 60 else norm_line[:57] + "..."
        lines.append(f"L{i}\t{anchor_hash(norm_line)}\t{preview}")
    return "\n".join(lines)


def split_items(text: str, item_re: str) -> list[tuple[str, str]]:
    """항목 헤더 정규식으로 (id, 본문) 쌍을 만든다.

    `re.split` 의 고정 stride 는 그룹 수·헤더 형식이 조금만 달라도 페어링이 밀린다(r1-05).
    `finditer` 로 각 항목의 구간을 직접 잡고, 캡처 그룹이 정확히 1개인지 검사한다.
    """
    # 잘못된 정규식은 raw traceback이 아니라 원인을 짚는 사용 오류로 끝난다 — 이 도구의
    # 다른 인자 오류와 같은 지위다(#347 r4-01에서 이 경로가 exit 2 계약에 들어왔다).
    try:
        pattern = re.compile(item_re, re.M)
    except re.error as exc:
        raise ValueError(f"--item-re 정규식 오류: {exc} — {item_re!r}") from exc
    if pattern.groups != 1:
        raise ValueError(f"--item-re 는 캡처 그룹이 정확히 1개여야 한다 (현재 {pattern.groups}개): {item_re}")
    matches = list(pattern.finditer(text))
    items: list[tuple[str, str]] = []
    for i, m in enumerate(matches):
        end = matches[i + 1].start() if i + 1 < len(matches) else len(text)
        items.append((m.group(1), text[m.end():end]))
    return items


def _print_exemption_disclosure(no_quote_ok, exempted_ids=None) -> None:
    """면제 선언을 **모든 종료 경로에서** 출력한다 (Codex 피어리뷰 r3-03 → r4-01).

    `QUOTE-OK`/`QUOTE-N/A`에서만 출력하면 파싱 실패나 NOTRUN이 이길 때 어떤 면제가
    걸려 있었는지가 사라진다. 더 이른 exit 2 경로(잘못된 `--item-re`, 항목 0건, 파일
    부재, 필수 인자 누락)에서도 마찬가지다 — 항목 분석에 닿지도 못한 실행이라
    `exempted_ids`가 없으면 **적용 판정 미수행**으로 표시한다.

    **범위(Codex 피어리뷰 r6-02)**: 이 공시는 **인자 파싱에 성공한** 판정 경로에 건다.
    argparse 자체가 거부한 호출(`--bogus`)은 요청이 성립하지 않은 것이라 대상이 아니다 —
    그 경계를 계약과 `--help`에도 같은 문구로 적었다.
    """
    if not no_quote_ok:
        return
    requested = sorted(no_quote_ok)
    header = "면제 선언(--no-quote-ok, **호출자 자기선언 · 항목 유형/정당성 미검증**): "
    if exempted_ids is None:
        print(f"{header}요청 {len(requested)}건 {requested} · **적용 판정 미수행**"
              "(항목 분석 전에 종료됐다)")
        return
    applied = sorted(exempted_ids)
    unapplied = [i for i in requested if i not in set(applied)]
    print(f"{header}요청 {len(requested)}건 {requested} · 적용 {len(applied)}건 {applied}")
    if unapplied:
        print(f"  주의: 적용되지 않은 선언 {len(unapplied)}건 {unapplied} — 그 항목이 "
              "산출물에 없거나 인용을 실제로 갖고 있다. 선언이 대상과 어긋난 것이므로 "
              "확인하라(이 도구는 항목 유형을 모르므로 선언의 정당성을 검사하지 않는다).")


#: docauth#347 r7-01: **고정된** 호스트 정규식. 사용자 조각은 여기 삽입되지 않고,
#: 아래 `_label_matches()`가 잘라 낸 `label` 문자열에만 따로 적용된다. 그래서 조각의
#: 플래그·lookaround·역참조가 payload 경계나 줄 경계를 침범할 수 없다.
#: Codex 피어리뷰 r8-01: `^-\s*`의 `\s*`가 **개행까지** 소비해 `-\n인용: "..."`처럼 실제
#: 필드 형식이 아닌 입력도 인용 필드가 됐다(실측 거짓 `QUOTE-OK`). 또 시작은 `-인용:`을
#: 허용하는데 종료 경계 `\n-\s`는 그것을 새 필드로 보지 않아, 뒤따르는 빈 `-인용(보조):`가
#: 앞 payload에 합쳐져 필드별 파싱 실패 검사가 무뎌졌다. 시작·종료의 공백 문법을 같게 맞춘다.
_FIELD_START = r"^-[ \t]*"
#: Codex 피어리뷰 r9-01: 종료 경계가 **라벨과 콜론을 확인하지 않고** 아무 `\n-...` 행에서
#: payload를 끊으면, 여러 줄 인용 안의 `- not-a-field` 행 뒤 내용(닫는 따옴표 포함)이
#: 잘려 앞부분만 대조되고 exit 0이 난다(실측 거짓 QUOTE-OK). 후보 경계는 **전체 필드 문법**
#: 행만이다 — docauth#364: 그 후보 행이 실제 경계인지는 정규식이 아니라 `iter_fields`가
#: 인용 상태로 정한다(옛 `FIELD_LINE_RE`/`_FIELD_END`는 폐기).
_CANDIDATE_RE = re.compile(_FIELD_START + r"(?P<label>[^:\n]*):[ \t]*(?P<rest>.*)$")




def _label_matches(matcher, m) -> bool:
    """필드 라벨이 사용자 조각으로 시작하는가 (docauth#347 r7-01).

    원래 호스트가 `{field}[^:\n]*:` 였으므로 **접두 일치**가 계약이다(예: `근거 인용`에
    `인용`은 매치하지 않지만 `근거 인용(보조)`에 `근거 인용`은 매치한다). 소비 길이 0은
    거부한다 — `(?=.)` 같은 0폭 조각이 **아무 라벨이나** 통과시키던 자리다.
    """
    hit = matcher.match(m.group("label"))
    return bool(hit) and hit.end() > 0


def audit(synth: Path, source: Source, alt: Source | None,
          item_re: str, quote_field: str, anchor_field: str,
          no_quote_ok: frozenset[str] = frozenset()) -> int:
    # Codex 피어리뷰 r2-02(#347): `:\s*`가 개행까지 먹어서, **빈 보조 필드가 정상 필드보다
    # 앞에 있으면** 다음 필드를 한 매치로 삼켜 빈 필드가 사라졌다. 반대로 파일 끝에 개행
    # 없이 빈 필드가 있으면 `(.+?)`가 아예 매치하지 않아 역시 사라졌다. 라벨 뒤 공백은
    # 같은 줄로 한정하고(`[ \t]*`), 본문은 **비어도 매치**하게(`(.*?)`) 바꾼다 —
    # 그래야 "필드는 있는데 대조 단위가 없다"가 필드 단위로 관측된다.
    # Codex 피어리뷰 r5-03 → r6-01 → r7-01(#347): 사용자 조각을 **호스트 정규식에 삽입**하는
    # 구조 자체가 문제였다. 독립 컴파일도, `(?:...)` 격리도 그 구조를 유지한 채여서
    # ⓐ `(?=.)` 같은 문맥 의존 0폭 조각이 `match("")` 검사를 통과해 **아무 필드나** 인용
    #    필드로 만들고, ⓑ 바깥 `re.S`가 조각에도 적용돼 `설명.*인용`이 줄을 넘어 이웃
    #    필드를 삼켰다 — 둘 다 실측된 거짓 `QUOTE-OK`다.
    # 그래서 **삽입을 없앤다**: 고정된 호스트 정규식이 `label`과 `payload`를 먼저 나누고,
    # 독립 컴파일된 조각은 **label 문자열에만** 적용한다. 조각은 이제 자기 플래그·
    # lookaround·역참조를 가져도 payload 경계나 줄 경계를 건드릴 수 없다.
    field_matchers = {}
    for label, fragment in (("quote", quote_field), ("anchor", anchor_field)):
        option = "--quote-field" if label == "quote" else "--anchor-field"
        if not fragment:
            print(f"ERROR: {option} 는 비어 있을 수 없다 — 빈 조각은 다른 필드를 "
                  "인용 필드로 오인한다", file=sys.stderr)
            _print_exemption_disclosure(no_quote_ok)
            return 2
        # docauth#364(설계 r1-03·r2): r8-04의 콜론 조기 검사(`_GROUP_SYNTAX_RE` 텍스트 치환)는 삭제했다 —
        # 문자 클래스·주석·escape·assertion·분기·반복 하한을 텍스트로도 파서 트리로도 바르게 판정할 수 없다.
        # 안전망은 관측이다: 매치 0 → QUOTE-NOTRUN(exit 3, 아래 힌트) / anchor 0건 → ANCHOR-NOTRUN 경고.
        try:
            compiled = re.compile(fragment)
        except re.error as exc:
            print(f"ERROR: {option} 정규식 오류: {exc} — {fragment!r}", file=sys.stderr)
            _print_exemption_disclosure(no_quote_ok)
            return 2
        # 빈 문자열에 매치되는 조각(`x?`)은 **아무 라벨이나** 통과시킨다. 아래
        # `_label_matches()`의 "소비 길이 0 거부"가 구조적으로 같은 것을 막지만,
        # 여기서 미리 거절하면 조용한 NOTRUN 대신 원인을 짚는 사용 오류가 나온다.
        if compiled.match(""):
            print(f"ERROR: {option} 는 빈 문자열에 매치될 수 있다 — 다른 필드를 인용 "
                  f"필드로 오인한다: {fragment!r}", file=sys.stderr)
            _print_exemption_disclosure(no_quote_ok)
            return 2
        field_matchers[label] = compiled
    quote_matcher = field_matchers["quote"]
    anchor_matcher = field_matchers["anchor"]

    try:
        items = split_items(read_input_text(synth), item_re)
    except (OSError, UnicodeError) as exc:
        print(f"ERROR: 합성 산출물을 읽을 수 없음: {synth} ({exc})", file=sys.stderr)
        _print_exemption_disclosure(no_quote_ok)
        return 2
    except ValueError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        _print_exemption_disclosure(no_quote_ok)
        return 2
    if not items:
        print(f"ERROR: 항목을 찾지 못함 — --item-re {item_re!r} 확인", file=sys.stderr)
        _print_exemption_disclosure(no_quote_ok)
        return 2
    seen_ids: set[str] = set()
    duplicate_ids: set[str] = set()
    for iid, _ in items:
        if iid in seen_ids:
            duplicate_ids.add(iid)
        seen_ids.add(iid)
    if duplicate_ids:
        duplicates = ", ".join(sorted(duplicate_ids))
        print(f"ERROR: 중복 item ID — 각 항목 ID는 유일해야 한다: {duplicates}", file=sys.stderr)
        _print_exemption_disclosure(no_quote_ok)
        return 2

    ok = miss = no_quote = unparsed = anchor_off = anchor_vanished = anchor_ambiguous = snap_mismatch = 0
    anchor_scope = anchor_hit = 0  # docauth#364: 인용 필드가 있는 항목 수 / 그중 anchor selector 매치 수
    misses: list[str] = []
    offs: list[str] = []
    # 계약 §4.1 의 `drifts` 레코드와 무관한 값이다 — 낱말을 빌려 쓰지 않는다.
    snap_mismatches: list[str] = []
    bad_anchor: list[str] = []
    vanished_anchor: list[str] = []
    ambiguous_anchor: list[str] = []
    unparsed_ids: list[str] = []
    exempted_ids: list[str] = []
    unexempt_no_quote_ids: list[str] = []

    for iid, body in items:
        # 한 항목에 인용 필드가 여러 개면 **전부** 대조한다 — 첫 필드만 보면 뒤쪽
        # 날조 인용이 조용히 통과한다(Greptile G-1, r1-03·r1-04 와 같은 거짓 OK 계열).
        # docauth#364: 정규식 대신 인용 필드별 흡수 스캐너로 경계를 계산한다.
        item_fields = iter_fields(body, quote_matcher)
        matches = [f for f in item_fields if f.is_quote]
        # docauth#364 구현 r1-02·r1-03: 앵커 필드도 **스캐너가 남긴 필드**에서 고른다(흡수된 앵커형 행은
        # 인용 본문이지 필드가 아니다). 한 줄 payload 문법은 유지(첫 줄만). 매치 여부는 대조 결과와
        # 무관하게 여기서 관측한다 — MISS·판본 불일치로 continue한 항목도 분모에 든다.
        anchor_fld = next(
            (f for f in item_fields
             if _label_matches(anchor_matcher, f) and f.payload.split("\n", 1)[0].strip()),
            None,
        )
        if matches:
            anchor_scope += 1
            if anchor_fld is not None:
                anchor_hit += 1
        if not matches:
            no_quote += 1
            # Codex 피어리뷰 r2-01(#347): 전역 boolean은 **항목 유형을 구분하지 않아**,
            # 정상 question/drift 때문에 플래그를 켜면 계약상 인용이 필수인 finding의
            # 무인용까지 함께 조용히 통과한다. 면제는 **id 단위 선언**이어야 한다.
            (exempted_ids if iid in no_quote_ok else unexempt_no_quote_ids).append(iid)
            continue
        # Codex 피어리뷰 r1-03(#347): 여러 인용 필드를 **합친 뒤** 전체가 비었는지만
        # 보면, 정상 필드 하나가 파싱 불가능한 다른 필드를 가린다 — 후자는 조용히
        # 버려지고 QUOTE-OK에 도달한다. "인용 필드가 있는데 대조 단위를 못 만들면
        # 통과시키지 않는다"(r1-04)는 자체 계약이 **필드 단위로** 지켜져야 한다.
        fragments: list[str] = []
        field_unparsed = False
        for qm in matches:
            payload = qm.group("payload").strip()
            # Codex 피어리뷰 r10-01 → docauth#364: 스캐너가 열린 인용 안의 비인용 경계를 흡수했으므로
            # 여기 남는 미종결은 진짜 미종결(인용 후보 행·절 경계에서 멈춤, 항목 끝, inch 홀수 따옴표)이다.
            if qm.unterminated or _unterminated_quote(payload):
                field_unparsed = True
                continue
            field_fragments = split_quotes(payload)
            if not field_fragments:
                field_unparsed = True
            fragments += field_fragments
        fragments = list(dict.fromkeys(fragments))
        if field_unparsed or not fragments:
            unparsed += 1
            unparsed_ids.append(iid)
            continue
        absent = [f for f in fragments if not source.hits(f)]
        if absent:
            # 판본 불일치는 **대조본이 인용 전체를 담을 때만** 성립한다 — 조각별로 판본이
            # 갈리면 어느 판본에도 그 인용이 없는 것이므로 MISS 다(Greptile G-2).
            if alt and all(alt.hits(f) for f in fragments):
                snap_mismatch += 1
                snap_mismatches.append(
                    f"  {iid}: 대조본에 없고 --source-alt 에만 있음 — 판본 불일치")
            else:
                miss += 1
                misses.append(f"  {iid}: {absent[0][:70]}")
            continue
        ok += 1
        if anchor_fld is None:
            continue
        anchors, rejected, vanished, ambiguous = parse_anchors(
            anchor_fld.payload.split("\n", 1)[0], source
        )
        if rejected:
            bad_anchor.append(f"  {iid}: 잘못된 범위 표기 {rejected}")
        if vanished:
            anchor_vanished += 1
            vanished_anchor.append(
                f"  {iid}: 안정 식별자 {vanished} — 그 행 내용이 더는 원문에 없음(편집됨)"
            )
        if ambiguous:
            anchor_ambiguous += 1
            ambiguous_anchor.append(f"  {iid}: {ambiguous}")
        hits = {h for f in fragments for h in source.hits(f)}
        if anchors and hits and not (anchors & hits):
            anchor_off += 1
            offs.append(f"  {iid}: 적힌 앵커 {sorted(anchors)[:6]} / 실제 출현 {sorted(hits)[:6]}")

    # 이 검산은 **어느 판본에 대한 질의였는지**가 결과의 일부다 — 대상이 편집 중이면
    # 같은 산출물에 대해서도 실행할 때마다 값이 달라진다(docauth#202).
    print(f"SYNTH: {synth}  항목 {len(items)}종  sha256 {digest(synth)}")
    print(f"SRC  : {source.path}  {len(source)}행  sha256 {source.digest}")
    if alt:
        print(f"ALT  : {alt.path}  {len(alt)}행  sha256 {alt.digest} (판본 불일치 판정용)")
    # docauth#347: 실제로 **대조가 이뤄진** 항목 수. `인용 없음`은 여기 들어가지
    # 않는다 — 그것은 검사한 항목이 아니라 검사할 단위를 못 만든 항목이다.
    compared = ok + miss + snap_mismatch
    print(f"검산: 대조 {compared}건 / 항목 {len(items)}종 · "
          f"원문 일치 {ok} · 원문 미존재 {miss} · 판본 불일치 {snap_mismatch} · "
          f"인용 없음 {no_quote} · 인용 파싱 실패 {unparsed} · 앵커 불일치 {anchor_off} · "
          f"앵커 소멸 {anchor_vanished} · 앵커 모호 {anchor_ambiguous}")
    for label, rows in (("[MISS — 원문에 없는 인용]", misses),
                        ("[SNAPSHOT-MISMATCH — 다른 판본에만 있는 인용]", snap_mismatches),
                        ("[앵커 불일치 — 인용은 있으나 적힌 행에 없음(경고)]", offs),
                        ("[앵커 소멸 — 안정 식별자가 가리키던 행이 더는 없음(경고)]", vanished_anchor),
                        ("[앵커 모호 — 안정 식별자가 중복 행에 걸침, 특정 행 미확정(경고)]", ambiguous_anchor),
                        ("[앵커 표기 거절 — 역순·비양수 범위(경고)]", bad_anchor)):
        if rows:
            print(label)
            print("\n".join(rows))
    # Codex 피어리뷰 r2-04(#347): 판정 순서가 계약과 어긋나 있었다 — `miss`가 먼저
    # return해서 "오귀속 1건 + 미검사 1종"이 exit 1로 나갔고, 호출자는 **호출을 고쳐야
    # 하는데 인용 수정 루프를 탔다.** 진단은 전부 출력하되 종료 코드는
    # ① 파싱 실패(exit 2) → ② 미검사 항목 존재(exit 3) → ③ 순수 오귀속(exit 1) 순으로 정한다.
    _print_exemption_disclosure(no_quote_ok, exempted_ids)
    if unparsed:
        print(f"[인용 파싱 실패] {', '.join(unparsed_ids)}")
        print("결과: 사용 오류 — 인용 필드는 있는데 대조 단위를 만들지 못했다. "
              "검산되지 않은 항목을 통과로 셈하지 않는다")
        return 2
    if unexempt_no_quote_ids:
        # Codex 피어리뷰 r8-03: 이 판정은 **원인 중립**이다 — selector 불일치와 산출물의
        # 필수 필드 누락이 같은 상태로 합쳐진다. 처방을 하나로 단정하면(호출만 고쳐라)
        # 실제로 필드가 빠진 finding에서 selector를 넓혀 다른 라벨을 오인할 유인이 생긴다.
        print(f"결과: QUOTE-NOTRUN — 항목 {len(items)}종 중 대조 {compared}건이고 "
              f"{len(unexempt_no_quote_ids)}종은 인용 필드가 없어 **검사되지 않았다**: "
              f"{', '.join(unexempt_no_quote_ids)}. 원인은 둘 중 하나이고 **둘 다 확인해야 "
              f"한다** — ① `--quote-field` {quote_field!r}가 이 산출물의 표기와 맞지 않다"
              "(호출을 고친다; 볼드 표기 산출물에는 '\\*\\*근거 인용\\*\\*'). "
              "② 계약 §4가 finding에 의무로 요구하는 근거 인용이 산출물에서 실제로 빠졌다"
              "(산출물을 고친다 — selector를 넓히면 다른 라벨을 인용으로 오인하게 된다). "
              "인용이 없는 것이 정상인 question·drift 항목만 `--no-quote-ok <id>`로 개별 "
              "선언한다(이 도구는 항목 유형을 모른다 — 선언은 **호출자 자기선언이며 유형이 "
              "검증되지 않는다**). "
              "힌트(docauth#364): 라벨은 첫 콜론에서 끝난다 — selector가 라벨 안의 실제 콜론을 "
              "요구하는지 확인하라.")
        return 3
    # docauth#364: anchor selector가 인용 필드는 있는 항목 전부에서 0건이면 관측만 남긴다(종료 코드 불변 —
    # 앵커 대조는 advisory). 조용히 넘어가던 자리를 닫는다(설계 r1-03).
    if anchor_scope and not anchor_hit:
        print(f"ANCHOR-NOTRUN — --anchor-field {anchor_field!r}로 인용 필드를 가진 항목 {anchor_scope}종 어디서도 "
              "첫 줄 payload가 비어 있지 않은 앵커 필드를 찾지 못해 앵커 대조가 실행되지 않았다"
              "(경고 — 라벨은 첫 콜론에서 끝난다).")
    if miss:
        print(f"결과: QUOTE-FAIL — 항목 {len(items)}종 중 대조 {compared}건, "
              f"그중 오귀속 {miss}건. 원문 기준으로 고치고 정정 건수를 기록할 것")
        return 1
    # docauth#347: **0건을 검사하고 통과를 내지 않는다.** 항목을 읽었어도 그중
    # 어느 것도 원문과 대조되지 않았다면(전부 `인용 없음`), 이 검산기는 아무 근거
    # 없이 통과 판정을 낸 것이다 — 계약이 여러 곳에서 경고한 "무발화는 고지의
    # 증명이 아니다"가 검산기 자신에게서 발화하는 자리이며, §0.2가 `sha256` 형식
    # 검사로 닫은 것(빈 `{}`로 양쪽이 "합의"해도 통과하던 문제)과 같은 종류의 구멍이다.
    # 2026-09-07 실행에서 세 번 발생했고, 세 번 다 사람이 출력의 항목 수를 눈으로
    # 보고 잡았다. 실패(QUOTE-FAIL)와 구분되는 별도 종료 코드를 쓰는 이유는
    # "대조했는데 틀렸다"와 "아예 대조하지 않았다"가 호출측에 다른 처방이기 때문이다
    # (전자는 산출물 수정, 후자는 `--item-re`/`--quote-field` 인자 수정).
    # Codex 피어리뷰 r2-03(#347): 계약은 `findings: []`인 drift-only 산출물을 허용한다.
    # 그런데 무조건 `compared == 0 → exit 3`이면 그 정상 산출물은 **선언해도 완료할 수
    # 없다.** 단순 파싱 실패(0건 대조 + 미선언 항목 존재)와, 모든 항목이 개별 선언으로
    # 면제된 경우를 구분한다 — 후자는 통과 문구가 아니라 `QUOTE-N/A`로 낸다.
    if compared == 0:
        print(f"결과: QUOTE-N/A — 항목 {len(items)}종 전부가 `--no-quote-ok`로 개별 선언된 "
              f"무인용 항목이라 대조할 인용이 없다: {', '.join(exempted_ids)}. "
              "**통과가 아니라 해당 없음**이며, `--item-re`가 **추출한 항목**에는 대조할 인용이 "
              "없다는 뜻이다 — 추출되지 않은 항목은 판정 범위 밖이라 이 문구가 산출물 전체를 "
              "주장하지 않는다(Codex 피어리뷰 r10-02). 계약이 허용하는 drift-only 산출물 등. "
              "그 선언은 **호출자 자기선언이며 "
              "이 도구가 항목 유형을 검증하지 않는다**.")
        return 0
    extra = []
    if anchor_off:
        extra.append(f"앵커 불일치 {anchor_off}종은 위치 표기 수정 대상")
    if anchor_vanished:
        extra.append(f"앵커 소멸 {anchor_vanished}종은 그 행이 편집된 것 — 재인용 대상")
    if anchor_ambiguous:
        extra.append(f"앵커 모호 {anchor_ambiguous}종은 중복 행이라 특정 행 미확정 — 재확인 권장")
    # docauth#347 공통 조항: 통과 문구는 **실제 검사 건수**를 항상 싣는다. 건수 없는
    # "QUOTE-OK"는 3종을 봤는지 33종을 봤는지 구별되지 않아, 사람이 눈으로 잡을
    # 단서마저 없앤다.
    # Codex 피어리뷰 r9-02: 이 판정의 **분모는 `--item-re`가 추출한 항목**이다 —
    # 정규식이 못 잡은 항목은 애초에 보이지 않으므로 "전건"이라는 말이 문서 전체를
    # 뜻하지 않는다. exit 0만으로 전수 검사를 주장하지 않도록 통과 문구에 범위를 붙인다.
    print(f"결과: QUOTE-OK — `--item-re`가 추출한 항목 {len(items)}종 중 대조 {compared}건 "
          "전건이 원문에 존재(추출되지 않은 항목은 판정 범위 밖)"
          + (f"; **인용 없음 {len(exempted_ids)}종은 검사되지 않았다**"
             f"(--no-quote-ok 호출자 자기선언 · 유형 미검증: {', '.join(exempted_ids)})"
             if exempted_ids else "")
          + (f" ({'· '.join(extra)})" if extra else ""))
    return 0


def main() -> int:
    # Codex 피어리뷰 r9-04: 캐시는 **실행 단위**다 — 모듈 수명이면 같은 프로세스의
    # 재검산(테스트·임베딩)이 수정 전 바이트를 계속 쓴다.
    reset_read_cache()
    ap = argparse.ArgumentParser(description="인용 전건 재대조 검산기")
    ap.add_argument("synth", type=Path, nargs="?", help="합성 산출물(리뷰 최종본)")
    ap.add_argument("--source", type=Path, help="대상 문서 전문")
    ap.add_argument("--source-alt", type=Path, default=None,
                    help="다른 판본(개정 전/후) — 판본 불일치 구분용")
    ap.add_argument("--item-re", default=r"^### (\S+)", help="항목 헤더 정규식(캡처 그룹 정확히 1개)")
    ap.add_argument("--quote-field", default="인용", help="인용 필드 이름")
    ap.add_argument("--anchor-field", default="앵커", help="앵커 필드 이름")
    ap.add_argument(
        "--no-quote-ok", action="append", default=[], metavar="ID",
        help=(
            "인용 필드가 없어도 되는 항목의 **id를 하나씩 선언**한다(반복 지정, docauth#347). "
            "**호출자 자기선언 · 항목 유형/정당성 미검증** — 이 도구는 `--item-re`로 나눈 항목만 "
            "볼 뿐 무엇이 finding이고 무엇이 question·drift인지 모르므로, finding id를 선언해도 "
            "막지 못한다. 요청·적용된 선언은 **인자 파싱에 성공한 모든 종료 경로**의 "
            "판정문에 남는다(argparse 자체가 거부한 호출은 요청이 성립하지 않은 것이라 "
            "예외다). "
            "기본값은 전건 대조다 — 계약 §4가 finding에 근거 인용을 의무 필드로 두므로, "
            "일부만 대조된 통과는 나머지를 검사하지 않았다는 사실을 숨긴다. "
            "**전역 플래그가 아니라 id 단위인 이유**(Codex 피어리뷰 r2-01): 항목 유형을 "
            "구분하지 않는 전역 면제는, 정상 question·drift 때문에 켜는 순간 계약상 인용이 "
            "필수인 finding의 무인용까지 함께 통과시킨다. 선언한 id는 판정문에 전부 남는다."
        ),
    )
    ap.add_argument(
        "--emit-anchors", type=Path, default=None,
        help="검산 대신 이 문서의 행별 안정 식별자(A<hash>) 표를 출력하고 종료(#207 저작 도우미)",
    )
    args = ap.parse_args()

    if args.emit_anchors is not None:
        if not args.emit_anchors.is_file():
            # Codex 피어리뷰 r6-02(#347): `exists()`만 보면 디렉터리가 통과해
            # `IsADirectoryError` traceback으로 죽었다. 일반 파일만 받는다.
            print(f"ERROR: 일반 파일이 아님: {args.emit_anchors}", file=sys.stderr)
            _print_exemption_disclosure(args.no_quote_ok)
            return 2
        try:
            rendered = emit_anchors(Source(args.emit_anchors))
        except (OSError, UnicodeError) as exc:
            print(f"ERROR: 파일을 읽을 수 없음: {args.emit_anchors} ({exc})", file=sys.stderr)
            _print_exemption_disclosure(args.no_quote_ok)
            return 2
        print(rendered)
        # Codex 피어리뷰 r5-02(#347): 성공 반환도 "모든 종료 경로" 계약에 든다.
        _print_exemption_disclosure(args.no_quote_ok)
        return 0

    if args.synth is None or args.source is None:
        print("ERROR: synth 인자와 --source 가 필요하다(--emit-anchors 단독 사용이 아니라면)", file=sys.stderr)
        _print_exemption_disclosure(args.no_quote_ok)
        return 2
    # Codex 피어리뷰 r7-02(#347): `exists()`만 보면 디렉터리가 통과해 `IsADirectoryError`
    # **traceback + exit 1**로 죽었다 — exit 1은 `QUOTE-FAIL` 코드라 의미도 충돌한다.
    # 세 입력 전부 일반 파일이어야 하고, 읽기 자체도 경계 안에서 일어나야 한다.
    for label, candidate in (
        ("synth", args.synth), ("--source", args.source), ("--source-alt", args.source_alt)
    ):
        if candidate is None:
            continue
        if not candidate.exists():
            print(f"ERROR: 파일 없음: {candidate}", file=sys.stderr)
            _print_exemption_disclosure(args.no_quote_ok)
            return 2
        if not candidate.is_file():
            print(f"ERROR: {label} 는 일반 파일이어야 한다: {candidate}", file=sys.stderr)
            _print_exemption_disclosure(args.no_quote_ok)
            return 2
    try:
        _source = Source(args.source)
        _alt = Source(args.source_alt) if args.source_alt else None
    except (OSError, UnicodeError) as exc:
        print(f"ERROR: 원문을 읽을 수 없음: {exc}", file=sys.stderr)
        _print_exemption_disclosure(args.no_quote_ok)
        return 2
    return audit(args.synth, _source, _alt,
                 args.item_re, args.quote_field, args.anchor_field,
                 no_quote_ok=frozenset(args.no_quote_ok))


if __name__ == "__main__":
    sys.exit(main())
