#!/usr/bin/env python3
"""audit_quotes.py 양성·음성 fixture 테스트 (audit_anchors 테스트 관례).

양성 = 오귀속을 실제로 잡는다 / 음성 = 형식 변형에 오탐하지 않는다.
**음성 케이스가 이 검산기의 존재 이유**다 — 자작 감사기가 오탐 22/24 를 낸
실측(docauth#201)이 fixture 로 고정돼 있다.

피어리뷰 r1 지적(r1-01~06)에 대응하는 경계 fixture 를 함께 둔다(r1-07):
여러 줄 인용 · 인용 안의 라벨 문자열 · 혼합 따옴표 · 짧은 인용 ·
그룹 없는 `--item-re` · 하이픈 범위 · 역순 범위.
"""

from __future__ import annotations   # 3.9 호환(`str | None` 런타임 평가 회피)

import subprocess
import sys
import tempfile
from pathlib import Path

SCRIPT = Path(__file__).resolve().parent.parent / "lib" / "review_gate" / "audit_quotes.py"
sys.path.insert(0, str(SCRIPT.parent))
from audit_quotes import anchor_hash, norm  # noqa: E402

PASS_COUNT = 0
FAIL_COUNT = 0

SOURCE = """# 대상 문서
Org 는 유일한 운영 주체이며 시스템 상태를 가집니다.
- **데이터 격리** — 모든 데이터는 Org 식별자 기준 논리 분리.
  | 데이터 | Main으로 이동 | 고객에게 반출 후 삭제 |
'Tenant/태넌트'는 이력 기록을 제외하고 고객 대면에서 'Org'로 통일합니다.
반출 산출물 사양 미확정(mrq4) 상태에서는 실행 불가입니다.
이 문장은 여기서 끊기고
다음 줄로 이어집니다.
예시 표기는 - L12: 값 처럼 적는다.
"""


def check(name: str, ok: bool, detail: str = "") -> None:
    global PASS_COUNT, FAIL_COUNT
    if ok:
        PASS_COUNT += 1
        print(f"  ok  {name}")
    else:
        FAIL_COUNT += 1
        print(f"  FAIL {name} {detail}")


def run(synth_text: str, source_text: str = SOURCE, alt_text: str | None = None,
        extra: list[str] | None = None):
    with tempfile.TemporaryDirectory() as d:
        d = Path(d)
        (d / "synth.md").write_text(synth_text, encoding="utf-8")
        (d / "src.md").write_text(source_text, encoding="utf-8")
        cmd = [sys.executable, str(SCRIPT), str(d / "synth.md"), "--source", str(d / "src.md")]
        if alt_text is not None:
            (d / "alt.md").write_text(alt_text, encoding="utf-8")
            cmd += ["--source-alt", str(d / "alt.md")]
        cmd += extra or []
        p = subprocess.run(cmd, capture_output=True, text=True)
        return p.returncode, p.stdout + p.stderr


def item(iid: str, quote: str, anchor: str = "") -> str:
    block = f"### {iid}\n- 인용: {quote}\n"
    if anchor:
        block += f"- 앵커: {anchor}\n"
    return block + "\n"


# ── 양성: 오귀속을 잡는다 ────────────────────────────────────────────
rc, out = run(item("F-01", '"Org 는 유일한 운영 주체이며 최고 권한을 가집니다."'))
check("양성 — 원문에 없는 문장을 MISS 로 잡는다", rc == 1 and "QUOTE-FAIL" in out, out)

rc, out = run(item("F-02", '"모든 데이터는 **Org 식별자** 기준 논리 분리."'))
check("양성 — 원문에 없는 볼드를 덧붙인 인용을 잡는다", rc == 1, out)

rc, out = run(item("F-03", '"Org 는 유일한 운영 주체이며 시스템 상태를 가집니다."', "L99"))
check("양성 — 인용은 있으나 앵커가 어긋나면 경고(FAIL 아님)",
      rc == 0 and "앵커 불일치 1" in out, out)

# r1-03: 한 따옴표 형태가 다른 형태를 덮어 날조 인용이 통과하면 안 된다
rc, out = run(item("F-04", '- L2: "Org 는 유일한 운영 주체이며 시스템 상태를 가집니다." '
                           '- L5: “문서에 없는 날조된 인용입니다”'))
check("양성(r1-03) — ASCII·한글 따옴표 혼합에서 날조분을 놓치지 않는다", rc == 1, out)

# r1-04: 짧은 인용이 조용히 버려져 QUOTE-OK 가 되면 안 된다
rc, out = run(item("F-05", '"없는말"'))
check("양성(r1-04) — 짧은 날조 인용도 MISS 로 잡는다", rc == 1, out)

# ── 음성: 형식 변형에 오탐하지 않는다 ─────────────────────────────────
rc, out = run(item("F-06", '"Org 는 유일한 운영 주체이며 시스템 상태를 가집니다."', "L2"))
check("음성 — 정상 인용+정확한 앵커는 통과", rc == 0 and "QUOTE-OK" in out, out)

rc, out = run(item("F-07",
                   '- L2: "Org 는 유일한 운영 주체이며 시스템 상태를 가집니다." '
                   '- L5: "\'Tenant/태넌트\'는 이력 기록을 제외하고" ', "L2, L5"))
check("음성 — 한 항목에 라벨로 묶인 복수 인용을 분해한다", rc == 0, out)

rc, out = run(item("F-08", '"모든 데이터는 … 논리 분리."', "L3"))
check("음성 — 말줄임 인용을 단편으로 대조한다", rc == 0, out)

rc, out = run(item("F-09", '"| 데이터 | Main으로 이동 | 고객에게 반출 후 삭제 |"', "L3~L5"))
check("음성 — 범위 앵커 표기(L3~L5)를 전개한다", rc == 0 and "앵커 불일치 0" in out, out)

rc, out = run(item("F-10", '\\"반출 산출물 사양 미확정(mrq4) 상태에서는 실행 불가입니다.\\"'))
check("음성 — 이스케이프된 따옴표를 처리한다", rc == 0, out)

# r1-01: 원문에서 줄바꿈을 넘는 인용
rc, out = run(item("F-11", '"이 문장은 여기서 끊기고 다음 줄로 이어집니다."', "L7~L8"))
check("음성(r1-01) — 여러 줄에 걸친 인용을 찾는다", rc == 0 and "QUOTE-OK" in out, out)

# r1-02: 인용 본문에 라벨 모양 문자열이 들어 있는 경우
rc, out = run(item("F-12", '"예시 표기는 - L12: 값 처럼 적는다."', "L9"))
check("음성(r1-02) — 인용 안의 `- L12:` 를 라벨로 오인하지 않는다", rc == 0, out)

# r1-03: 혼합 따옴표 — 둘 다 원문에 있으면 통과해야 한다
rc, out = run(item("F-13", '- L2: "Org 는 유일한 운영 주체이며 시스템 상태를 가집니다." '
                           '- L6: “반출 산출물 사양 미확정(mrq4) 상태에서는 실행 불가입니다.”'))
check("음성(r1-03) — 혼합 따옴표 인용을 둘 다 대조한다", rc == 0, out)

# r1-04: 원문에 있는 짧은 인용은 통과
rc, out = run(item("F-14", '"Org"', "L2"))
check("음성(r1-04) — 원문에 있는 짧은 인용은 통과", rc == 0, out)

# r1-06: 하이픈 범위
rc, out = run(item("F-15", '"| 데이터 | Main으로 이동 | 고객에게 반출 후 삭제 |"', "L3-L5"))
check("음성(r1-06) — 하이픈 범위 표기도 전개한다", rc == 0 and "앵커 불일치 0" in out, out)

# ── 사용 오류·거절 ────────────────────────────────────────────────
# r1-04: 인용 필드는 있는데 대조 단위를 못 만들면 조용히 통과시키지 않는다
rc, out = run(item("F-16", '"..."'))
check("사용 오류(r1-04) — 인용 파싱 실패는 exit 2, QUOTE-OK 아님",
      rc == 2 and "인용 파싱 실패" in out, out)

# r1-05: 캡처 그룹이 없는 --item-re 는 거절한다(페어링이 밀려 항목이 누락되는 것 방지)
rc, out = run(item("F-17", '"Org 는 유일한 운영 주체이며 시스템 상태를 가집니다."'),
              extra=["--item-re", r"^### F-\S+"])
check("사용 오류(r1-05) — 캡처 그룹 없는 --item-re 를 거절한다",
      rc == 2 and "캡처 그룹" in out, out)

# r1-06: 역순 범위는 거절하고 경고한다
rc, out = run(item("F-18", '"Org 는 유일한 운영 주체이며 시스템 상태를 가집니다."', "L8~L3"))
check("경고(r1-06) — 역순 범위를 거절하고 알린다", "앵커 표기 거절" in out, out)

# ── 판본 불일치: 다른 판본에만 있는 인용은 MISS 와 구분한다 ─────────────
rc, out = run(item("F-19", '"이관 이벤트와 완전 폐쇄 이벤트를 분리합니다."'),
              alt_text="이관 이벤트와 완전 폐쇄 이벤트를 분리합니다.\n")
check("판본 불일치 — 구판에만 있는 인용을 SNAPSHOT-MISMATCH 로 분류(FAIL 아님)",
      # 라벨은 형제 라벨 `[MISS — …]` 과 같은 형식이다. 느슨한 부분문자열이 아니라
      # 실제로 찍히는 토큰 전체를 본다(r2-01).
      rc == 0 and "판본 불일치 1" in out
      and "[SNAPSHOT-MISMATCH — 다른 판본에만 있는 인용]" in out, out)

# 계약 §4.1 의 출력 범주 `drift`(표기 드리프트 — finding 과 나란한 별개 outcome)와
# 낱말이 겹치지 않는다.
# 이 검산기가 말하는 것은 판본 대조이지 표기 divergence 가 아니다.
check("용어 분리 — 판본 불일치 출력에 계약 용어 '드리프트'/'DRIFT' 가 없다",
      "드리프트" not in out and "DRIFT" not in out, out)

rc, out = run(item("F-20", '"어느 판본에도 없는 문장입니다."'), alt_text="다른 내용\n")
check("판본 불일치 — 어느 판본에도 없으면 여전히 MISS", rc == 1, out)

# ── Greptile P1 대응 ─────────────────────────────────────────────
# G-1: 항목에 인용 필드가 여러 개면 뒤쪽 필드도 검사해야 한다(첫 필드만 보면 거짓 OK)
multi = ('### F-21\n'
         '- 인용: "Org 는 유일한 운영 주체이며 시스템 상태를 가집니다."\n'
         '- 인용(보조): "문서에 없는 두 번째 날조 인용"\n\n')
rc, out = run(multi)
check("양성(G-1) — 두 번째 인용 필드의 날조도 잡는다", rc == 1, out)

multi_ok = ('### F-22\n'
            '- 인용: "Org 는 유일한 운영 주체이며 시스템 상태를 가집니다."\n'
            '- 인용(보조): "반출 산출물 사양 미확정(mrq4) 상태에서는 실행 불가입니다."\n\n')
rc, out = run(multi_ok)
check("음성(G-1) — 두 인용 필드가 모두 원문에 있으면 통과", rc == 0, out)

# G-2: 조각별로 판본이 갈리면 판본 불일치가 아니라 MISS 다
rc, out = run(item("F-23", '- L2: "Org 는 유일한 운영 주체이며 시스템 상태를 가집니다." '
                           '- L9: "구판에만 있던 문장입니다."'),
              alt_text="구판에만 있던 문장입니다.\n")
check("양성(G-2) — 조각이 두 판본에 흩어지면 SNAPSHOT-MISMATCH 가 아니라 MISS",
      rc == 1 and "판본 불일치 0" in out, out)

rc, out = run(item("F-24", '"구판 문장 하나" 그리고 "구판 문장 둘"'),
              alt_text="구판 문장 하나 이고 구판 문장 둘 이다.\n")
check("음성(G-2) — 대조본이 인용 전체를 담으면 SNAPSHOT-MISMATCH",
      rc == 0 and "판본 불일치 1" in out, out)

# 스냅샷 식별 — 어느 판본에 대한 질의였는지 출력에 남는다(#202)
rc, out = run(item("F-25", '"Org 는 유일한 운영 주체이며 시스템 상태를 가집니다."'))
check("스냅샷 식별 — SYNTH·SRC 의 sha256 을 출력한다",
      out.count("sha256") >= 2, out)

# ── #207 2안: 안정 식별자(A<hash>) 앵커 ──────────────────────────────
# SOURCE 행 2 · 5의 실제 안정 식별자(audit_quotes.anchor_hash로 계산, 고정값).
LINE2_HASH = "A85e37a833bbf"
LINE5_HASH = "Aad24bd3874bb"
LINE4_HASH = "A26e53b8cc23d"

rc, out = run(item("F-26", '"Org 는 유일한 운영 주체이며 시스템 상태를 가집니다."', LINE2_HASH))
check("음성(#207) — 정확한 안정 식별자 앵커는 통과(앵커 불일치·소멸 0)",
      rc == 0 and "QUOTE-OK" in out and "앵커 불일치 0" in out and "앵커 소멸 0" in out, out)

rc, out = run(item("F-27", '"Org 는 유일한 운영 주체이며 시스템 상태를 가집니다."', "Adeadbeefdead"))
check("음성(#207) — 원문에 없는 해시(그 행이 편집·삭제됨)는 앵커 소멸, FAIL 아님",
      rc == 0 and "QUOTE-OK" in out and "앵커 소멸 1" in out and "[앵커 소멸" in out, out)

rc, out = run(item("F-28", '"Org 는 유일한 운영 주체이며 시스템 상태를 가집니다."', LINE5_HASH))
check("음성(#207) — 인용은 있으나 다른 행의 안정 식별자를 적으면 앵커 불일치(소멸 아님)",
      rc == 0 and "앵커 불일치 1" in out and "앵커 소멸 0" in out, out)

rc, out = run(item(
    "F-29",
    f'- {LINE2_HASH}: "Org 는 유일한 운영 주체이며 시스템 상태를 가집니다." '
    f'- L5: "\'Tenant/태넌트\'는 이력 기록을 제외하고" ',
))
check("음성(#207) — 레거시 L 라벨과 신규 A<hash> 라벨을 한 필드에 섞어도 둘 다 분해",
      rc == 0 and "QUOTE-OK" in out, out)

# Codex r4-02: 라벨 여는 하이픈 바로 뒤에 공백 없이 붙는 형식(레거시 "-L2:"는 이미
# 지원)도 신규 해시 라벨에서 똑같이 갈라져야 한다.
rc, out = run(item(
    "F-29b",
    f'-{LINE2_HASH}: "Org 는 유일한 운영 주체이며 시스템 상태를 가집니다." '
    f'-L5: "\'Tenant/태넌트\'는 이력 기록을 제외하고" ',
))
check("음성(Codex r4-02) — 공백 없는 라벨(-A<hash>:)도 레거시(-L2:)처럼 분해된다",
      rc == 0 and "QUOTE-OK" in out, out)

rc, out = run(item("F-30", '"| 데이터 | Main으로 이동 | 고객에게 반출 후 삭제 |"', f"{LINE4_HASH}~{LINE4_HASH}"))
check("음성(#207) — 안정 식별자 범위(같은 끝점)도 전개해 대조", rc == 0 and "앵커 불일치 0" in out, out)

# 하이픈 범위(L\d+ 관례와 동일 구분자)도 여전히 통과해야 한다 — Codex r2-01 수정이
# 하이픈 자체를 경계로 막지 않는다는 것을 고정한다.
rc, out = run(item("F-30b", '"| 데이터 | Main으로 이동 | 고객에게 반출 후 삭제 |"', f"{LINE4_HASH}-{LINE4_HASH}"))
check("음성(#207) — 하이픈으로 구분한 안정 식별자 범위도 통과(Codex r2-01 이후에도 유지)",
      rc == 0 and "앵커 불일치 0" in out, out)

# Codex r3-01: 서로 다른 두 끝점을 하이픈으로 묶은 범위(축약형, 공백 없음)가 조용히
# 첫 끝점만 단일 앵커로 살아남지 않고 실제로 span 전체(행 2~5)로 해석되는지 —
# span 안의 행 4 인용이 불일치 없이 통과해야 한다.
rc, out = run(item("F-30c", '"| 데이터 | Main으로 이동 | 고객에게 반출 후 삭제 |"', f"{LINE2_HASH}-{LINE5_HASH}"))
check("음성(Codex r3-01) — 서로 다른 끝점의 하이픈 범위가 span 전체로 정상 해석된다",
      rc == 0 and "앵커 불일치 0" in out and "앵커 소멸 0" in out, out)

# Codex r4-01: 둘째 끝점이 깨진 범위(A<h1>-A<h2>d)는 첫 끝점만 단일 앵커로 조용히
# 살아남으면 안 된다 — 범위 표기 자체를 거절해야 한다. 인용은 원문에 있으나 앵커
# 필드가 통째로 거절되면 anchors가 비어 앵커 판정(불일치·소멸) 자체가 발화하지
# 않는다 — "앵커 표기 거절" 목록에 남는지로 확인한다.
rc, out = run(item("F-30d", '"Org 는 유일한 운영 주체이며 시스템 상태를 가집니다."', f"{LINE2_HASH}-{LINE5_HASH}d"))
check("음성(Codex r4-01) — 둘째 끝점이 깨진 범위는 첫 끝점 단독 앵커로 새지 않고 거절된다",
      rc == 0 and "QUOTE-OK" in out and "앵커 표기 거절" in out, out)

# Codex r5-02: consumed.replace(m.group(0), " ", 1)가 문자열 검색으로 첫 등장을
# 지우면, 같은 리터럴(LINE2_HASH)이 먼저 나오는 깨진 범위와 나중에 나오는 정상
# 범위가 같은 필드에 있을 때 정상 범위가 아니라 깨진 범위 자리가(우연히 먼저
# 나온 것을 텍스트로 찾아) 지워질 위험이 있었다 — 위치 기반 소비로 고쳤는지 확인.
rc, out = run(item(
    "F-30e",
    '"Org 는 유일한 운영 주체이며 시스템 상태를 가집니다."',
    f"{LINE2_HASH}-Azzzzzzzzzzzz {LINE2_HASH}~{LINE5_HASH}",
))
check("음성(Codex r5-02) — 같은 해시가 깨진 범위·정상 범위에 중복 등장해도 위치 기준으로 정확히 소비된다",
      rc == 0 and "QUOTE-OK" in out and "앵커 표기 거절" in out and "앵커 불일치 0" in out, out)

# Codex r6-01: 깨진 첫 끝점(A<bad>d~) 뒤에 **완전히 무관한 다른 유효 범위**
# (A<h2>-A<h10>)가 우연히 인접하면, 문자열 아무 데서나 스캔하는 HASH_RANGE가
# 그 무관한 범위를 독립적으로 찾아내 min..max span(무관한 중간 행 전부)을
# 앵커로 삼을 위험이 있었다 — r1-02·r4-01이 막으려던 "거짓 넓은 span"이 조합을
# 통해 되살아난 것. LEFT_BOUND에 구분자 문자(~·–)를 추가해 "이 해시 바로 앞이
# 구분자로 시작하는 자리"는 첫 끝점 후보에서 아예 제외한다 — parse_anchors를
# 직접 호출해 anchors가 완전히 비는지(무관한 span이 전혀 생기지 않는지) 확인한다.
from audit_quotes import parse_anchors as _parse_anchors_direct  # noqa: E402

with tempfile.TemporaryDirectory() as _d:
    _d = Path(_d)
    _lines = ["header"] + [f"line {i} content unique-{i}" for i in range(2, 11)]
    _src_path = _d / "src10.md"
    _src_path.write_text("\n".join(_lines) + "\n", encoding="utf-8")
    from audit_quotes import Source as _Source  # noqa: E402
    _source = _Source(_src_path)
    _h2 = anchor_hash(norm("line 2 content unique-2"))
    _h10 = anchor_hash(norm("line 10 content unique-10"))
    _anchors, _rejected, _vanished, _ambiguous = _parse_anchors_direct(
        f"A000000000001d~{_h2}-{_h10}", _source
    )
    check(
        "음성(Codex r6-01) — 깨진 첫 끝점에 우연히 인접한 무관 범위가 거짓 넓은 span을 만들지 않는다",
        _anchors == set(),
        f"anchors={sorted(_anchors)} (2~10 전체가 나오면 거짓 넓은 span 재발)",
    )

    # Codex r7-01: r6-01과 같은 뿌리지만 구분자 뒤에 공백이 끼는 변형 — 정규식
    # 고정폭 lookbehind로는 "구분자 뒤 임의 개수 공백"을 표현할 수 없어(r6-01의
    # 정규식 전용 수정으로는 안 닫혔다) 파이썬 코드로 직접 뒤로 훑는 검사로
    # 바꿨다. 세 구분자(~·-·–) 전부 공백 변형을 확인한다.
    for _sep in ("~", "-", "–"):
        _raw = f"A000000000001d{_sep} {_h2}-{_h10}"
        _anchors, *_ = _parse_anchors_direct(_raw, _source)
        check(
            f"음성(Codex r7-01) — 구분자({_sep!r}) 뒤 공백이 있어도 거짓 넓은 span을 만들지 않는다",
            _anchors == set(),
            f"raw={_raw!r} anchors={sorted(_anchors)}",
        )

    # Codex r7-02: 셋 이상 잇는 연쇄에서 마지막 구분자 뒤에 공백이 있으면 그
    # 셋째 항목이 (구분자 바로 앞이 아니라는 이유로) HASH_SINGLE에 독립적으로
    # 다시 주워지던 것 — 공백 유무와 무관하게 일관되게 빠져야 한다.
    _h3 = anchor_hash(norm("line 3 content unique-3"))
    _anchors, *_ = _parse_anchors_direct(f"{_h2}~{_h3}~ {_h10}", _source)
    check(
        "음성(Codex r7-02) — 구분자 뒤 공백이 있는 셋째 연쇄 항목도 일관되게 빠진다",
        _anchors == {2, 3},
        f"anchors={sorted(_anchors)} (10이 섞이면 공백-비공백 비일관성 재발)",
    )

    # Codex r8-01: 정규식 \s*는 스페이스·탭뿐 아니라 NBSP(U+00A0)·수직 탭·
    # form feed 등 유니코드 공백 전부를 인정하는데, _separator_precedes가
    # 스페이스·탭만 건너뛰던 최초 구현은 그 외 공백이 낀 변형을 놓쳤다.
    for _name, _ws in (("NBSP", " "), ("수직 탭", "\x0b"), ("form feed", "\x0c")):
        _raw = f"A000000000001d~{_ws}{_h2}-{_h10}"
        _anchors, *_ = _parse_anchors_direct(_raw, _source)
        check(
            f"음성(Codex r8-01) — 구분자 뒤 {_name}도 정규식 \\s와 동일하게 건너뛴다",
            _anchors == set(),
            f"raw={_raw!r} anchors={sorted(_anchors)}",
        )

# Codex r1-01: 경계 없는 해시 토큰은 13자리 이상 16진수의 앞 12자를 잘못 잘라
# 유효 토큰으로 오인한다 — LINE2_HASH 뒤에 16진수 한 글자를 더 붙이면 매치가 아예
# 안 되어야 한다(잘린 접두를 진짜 앵커로 오인하지 않음).
rc, out = run(item("F-31", '"Org 는 유일한 운영 주체이며 시스템 상태를 가집니다."', LINE2_HASH + "a"))
check("음성(Codex r1-01) — 13자리 16진수는 앞 12자로 잘려 매치되지 않는다(경계 보호)",
      rc == 0 and "앵커 불일치 0" in out and "앵커 소멸 0" in out, out)

# Codex r2-01: 16진수가 아닌 식별자 문자(대문자 G 등)가 바로 뒤에 붙어도 앞 12자를
# 완결된 토큰으로 오인하면 안 된다 — (?![0-9a-f])만으로는 못 잡는다.
rc, out = run(item("F-31b", '"Org 는 유일한 운영 주체이며 시스템 상태를 가집니다."', LINE2_HASH + "G"))
check("음성(Codex r2-01) — 비16진수 식별자 문자가 바로 붙어도 앞 12자를 토큰으로 오인하지 않는다",
      rc == 0 and "앵커 불일치 0" in out and "앵커 소멸 0" in out, out)

# Codex r1-02: 동일 내용의 중복 행 — 단일 앵커는 후보 전부를 넣고 "모호"로 공시,
# 범위는 min..max span이 무관한 중복 행까지 끌고 올 위험이 있어 계산을 거부한다.
DUP_SOURCE = ("헤더\n반복되는 문구입니다.\n중간 내용 A\n중간 내용 B\n중간 내용 C\n"
              "반복되는 문구입니다.\n끝\n")
DUP_HASH = anchor_hash(norm("반복되는 문구입니다."))
LINE3_HASH_DUP = anchor_hash(norm("중간 내용 A"))

rc, out = run(item("F-32", '"반복되는 문구입니다."', DUP_HASH), source_text=DUP_SOURCE)
check("음성(Codex r1-02) — 중복 행 해시는 앵커 모호로 공시되지 FAIL 아님",
      rc == 0 and "QUOTE-OK" in out and "앵커 모호 1" in out, out)

rc, out = run(item("F-33", '"중간 내용 A"', f"{DUP_HASH}~{LINE3_HASH_DUP}"), source_text=DUP_SOURCE)
check("음성(Codex r1-02) — 끝점이 중복 행이면 범위 span 계산을 거부하고 소멸로 남긴다",
      rc == 0 and "앵커 소멸 1" in out and "범위 계산 거부" in out, out)

# --emit-anchors: 저작 시점 조회 도우미가 실제 계산값과 일치하는지
with tempfile.TemporaryDirectory() as d:
    d = Path(d)
    src_path = d / "src.md"
    src_path.write_text(SOURCE, encoding="utf-8")
    p = subprocess.run(
        [sys.executable, str(SCRIPT), "--emit-anchors", str(src_path)],
        capture_output=True, text=True,
    )
    check("--emit-anchors — exit 0", p.returncode == 0, p.stdout + p.stderr)
    check("--emit-anchors — 행 2의 안정 식별자가 검산 결과와 일치",
          f"L2\t{LINE2_HASH}\t" in p.stdout, p.stdout)

# ── docauth#347: 0건 검사에 통과를 내지 않는다 ─────────────────────────
# 2026-09-07 실측 재현. 산출물은 `- **근거 인용**:`(볼드)를 쓰는데 기본
# `--quote-field`는 `- 근거 인용:`만 잡는다 → 인용 파싱 0건인데 QUOTE-OK 였다.
BOLD_FIELD_SYNTH = (
    "## F-001\n- **근거 인용**: \"Org 는 유일한 운영 주체이며 시스템 상태를 가집니다.\"\n\n"
    "## F-002\n- **근거 인용**: \"반출 산출물 사양 미확정(mrq4) 상태에서는 실행 불가입니다.\"\n\n"
)

rc, out = run(BOLD_FIELD_SYNTH, extra=["--item-re", r"^## (F-[0-9]+)", "--quote-field", "근거 인용"])
check("양성(#347) — 항목은 읽었으나 대조 0건이면 exit 3 · QUOTE-NOTRUN(통과 아님)",
      rc == 3 and "QUOTE-NOTRUN" in out, out)
check("양성(#347) — NOTRUN 문구가 항목 수와 미검사 항목 id를 함께 싣는다",
      "항목 2종" in out and "2종은 인용 필드가 없어" in out and "F-001" in out and "F-002" in out, out)
check("양성(#347) — NOTRUN 문구가 고칠 대상(호출 인자)을 지목한다",
      "--quote-field" in out, out)

rc, out = run(
    BOLD_FIELD_SYNTH,
    extra=["--item-re", r"^## (F-[0-9]+)", "--quote-field", r"\*\*근거 인용\*\*"],
)
check("음성(#347) — 인자를 맞추면 같은 산출물이 정상 대조되어 QUOTE-OK",
      rc == 0 and "QUOTE-OK" in out, out)
check("음성(#347) — 통과 문구에 실제 검사 건수가 실린다",
      "대조 2건" in out and "항목 2종" in out, out)

# Codex 피어리뷰 r1-02: **일부만** 대조된 것도 통과가 아니다 — 33종 중 한 곳만
# 우연히 필드 문법이 맞아도 나머지를 검사하지 않고 QUOTE-OK가 나오던 자리.
PARTIAL_SYNTH = (
    item("F-06", '"Org 는 유일한 운영 주체이며 시스템 상태를 가집니다."')
    + "### F-07\n- 설명만 있고 인용 필드가 없다\n\n"
)
rc, out = run(PARTIAL_SYNTH)
check("양성(#347 r1-02) — 2종 중 1종만 대조되면 exit 3(나머지는 검사되지 않았다)",
      rc == 3 and "QUOTE-NOTRUN" in out and "1종은 인용 필드가 없어" in out, out)

# 검산 헤더 줄도 대조 건수를 싣는다(사람이 눈으로 잡던 단서를 기계가 먼저 낸다).
check("음성(#347) — 검산 헤더가 대조 건수 / 항목 수를 함께 낸다", "검산: 대조 1건 / 항목 2종" in out, out)

# 인용 없는 항목이 정상이면 **그 id를 선언**하면 통과하고, 선언이 판정문에 남는다.
rc, out = run(PARTIAL_SYNTH, extra=["--no-quote-ok", "F-07"])
check("음성(#347 r2-01) — --no-quote-ok <id> 선언 시 통과", rc == 0 and "QUOTE-OK" in out, out)
check("음성(#347 r2-01) — 통과 문구가 선언된 id를 남긴다",
      "검사되지 않았다" in out and "F-07" in out, out)

# docauth#367: 같은 ID가 두 번 나오면 --no-quote-ok가 무인용 항목뿐 아니라 같은 ID의
# 인용 있는 항목에도 걸린 것처럼 합쳐져 거짓 QUOTE-OK가 났다. ID 유일성 위반은 호출자가
# 산출물을 고쳐야 하는 사용 오류이며, 이 조기 종료에서도 면제 선언을 공시한다.
DUPLICATE_ID_SYNTH = (
    '### Q-1\n- 인용: "safe prefix"\n\n'
    "### Q-1\n- 표기: 인용 없음\n\n"
)
rc, out = run(
    DUPLICATE_ID_SYNTH,
    source_text="safe prefix\n",
    extra=["--item-re", r"^### (.+)$", "--no-quote-ok", "Q-1"],
)
check("양성(#367) — 중복 item ID는 면제 선언과 함께 exit 2로 거부",
      rc == 2 and "중복" in out and "Q-1" in out and "면제 선언" in out, out)

rc, out = run(
    '### Q-1\n- 인용: "safe prefix"\n\n### Q-2\n- 표기: 인용 없음\n\n',
    source_text="safe prefix\n",
    extra=["--item-re", r"^### (.+)$", "--no-quote-ok", "Q-2"],
)
check("음성(#367) — 서로 다른 ID와 개별 무인용 면제는 QUOTE-OK 유지",
      rc == 0 and "QUOTE-OK" in out, out)

# 양성(r2-01): 다른 항목을 선언해도 **그 항목**의 무인용은 여전히 막힌다 —
# 전역 플래그였다면 정상 question 하나 때문에 finding의 무인용까지 통과했다.
rc, out = run(
    PARTIAL_SYNTH + "### F-08\n- 설명만 있고 인용 필드가 없다\n\n",
    extra=["--no-quote-ok", "F-07"],
)
check("양성(#347 r2-01) — 선언되지 않은 항목의 무인용은 여전히 exit 3",
      rc == 3 and "F-08" in out and "F-07" not in out.split("결과:")[-1].split("--no-quote-ok")[0], out)

# 양성(r2-03): 전부 선언된 무인용 산출물(계약이 허용하는 drift-only)은 QUOTE-N/A.
rc, out = run(
    "### D-01\n- 설명 A\n\n### D-02\n- 설명 B\n\n",
    extra=["--no-quote-ok", "D-01", "--no-quote-ok", "D-02"],
)
check("음성(#347 r2-03) — 전건 선언된 무인용 산출물은 QUOTE-N/A(exit 0)",
      rc == 0 and "QUOTE-N/A" in out and "통과가 아니라 해당 없음" in out, out)

# 양성(r2-04): 오귀속과 미검사가 함께 있으면 **미검사가 이긴다**(호출을 먼저 고친다).
rc, out = run(
    item("F-10", '"원문에 전혀 없는 날조 인용입니다."')
    + "### F-11\n- 설명만 있고 인용 필드가 없다\n\n"
)
check("양성(#347 r2-04) — 오귀속 + 미검사 혼합은 exit 3(exit 1이 아니다)",
      rc == 3 and "QUOTE-NOTRUN" in out, out)

# 양성(r2-02): 빈 보조 필드가 정상 필드보다 **앞에** 있어도 삼켜지지 않는다.
rc, out = run(
    "### F-12\n"
    "- 인용(보조): \n"
    '- 인용: "Org 는 유일한 운영 주체이며 시스템 상태를 가집니다."\n\n'
)
check("양성(#347 r2-02) — 빈 필드 선행도 필드 단위로 잡힌다", rc == 2 and "인용 파싱 실패" in out, out)

# 양성(r2-02): 파일 끝에 개행 없이 빈 필드가 있어도 사라지지 않는다.
rc, out = run(
    "### F-13\n"
    '- 인용: "Org 는 유일한 운영 주체이며 시스템 상태를 가집니다."\n'
    "- 인용(보조):"
)
check("양성(#347 r2-02) — EOF 무개행 빈 필드도 잡힌다", rc == 2 and "인용 파싱 실패" in out, out)

# 음성(r2-02): 정상 다중 인용 필드는 그대로 통과한다.
rc, out = run(
    "### F-14\n"
    '- 인용: "Org 는 유일한 운영 주체이며 시스템 상태를 가집니다."\n'
    '- 인용(보조): "반출 산출물 사양 미확정(mrq4) 상태에서는 실행 불가입니다."\n\n'
)
check("음성(#347 r2-02) — 정상 다중 인용 필드는 통과", rc == 0 and "QUOTE-OK" in out, out)

# Codex 피어리뷰 r1-03: 정상 인용 필드 하나가 파싱 불가능한 다른 필드를 가리면 안 된다.
rc, out = run(
    "### F-09\n"
    '- 인용: "Org 는 유일한 운영 주체이며 시스템 상태를 가집니다."\n'
    "- 인용(보조): \n\n"
)
check("양성(#347 r1-03) — 필드 하나가 대조 단위를 못 만들면 항목 전체가 exit 2",
      rc == 2 and "인용 파싱 실패" in out, out)

# QUOTE-FAIL 문구에도 건수가 실린다.
rc, out = run(item("F-08", '"원문에 전혀 없는 날조 인용입니다."'))
check("양성(#347) — FAIL 문구도 대조 건수·오귀속 건수를 싣는다",
      rc == 1 and "대조 1건" in out and "오귀속 1건" in out, out)

# Codex 피어리뷰 r4-01: 면제 선언은 **이른 exit 2 경로에서도** 보여야 한다.
for label, extra_args, synth in (
    ("항목 0건", ["--item-re", r"^ZZZ (x)"], item("F-20", '"Org 는 유일한 운영 주체이며 시스템 상태를 가집니다."')),
    ("잘못된 --item-re", ["--item-re", r"^### (\S+"], item("F-21", '"Org 는 유일한 운영 주체이며 시스템 상태를 가집니다."')),
):
    rc, out = run(synth, extra=extra_args + ["--no-quote-ok", "D-01"])
    check(f"양성(#347 r4-01) — {label} exit 2에서도 면제 선언이 남는다",
          rc == 2 and "면제 선언" in out and "적용 판정 미수행" in out, out)

# Codex 피어리뷰 r5-03: 사용자 정규식 조각은 **각각 독립적으로** 검증돼야 한다 —
# 조립된 바깥 패턴만 컴파일하면 `[`가 뒤쪽 문자 클래스와 결합해 살아남는다.
for _opt in ("--quote-field", "--anchor-field"):
    for _frag in ("[", "("):
        rc, out = run(item("F-30", '"Org 는 유일한 운영 주체이며 시스템 상태를 가집니다."'),
                      extra=[_opt, _frag])
        check(f"양성(#347 r5-03) — {_opt} {_frag!r}는 사용 오류(exit 2)",
              rc == 2 and f"{_opt} 정규식 오류" in out, out)

# Codex 피어리뷰 r6-01: **문법이 유효한** 조각도 바깥 패턴을 침범한다.
# `(인용)`의 캡처가 group(1)을 차지하면 실제 인용문 대신 **필드명**이 대조돼,
# 원문에 "인용"이라는 낱말만 있으면 날조 인용이 QUOTE-OK로 통과했다.
CAPTURE_SOURCE = "여기에 인용 이라는 낱말과 앵커 라는 낱말이 있다.\n"
rc, out = run(
    item("F-40", '"원문에 전혀 없는 날조 인용입니다."'),
    source_text=CAPTURE_SOURCE,
    extra=["--quote-field", "(인용)"],
)
check("양성(#347 r6-01) — 캡처 그룹 필드가 날조 인용을 통과시키지 않는다",
      rc == 1 and "QUOTE-FAIL" in out, out)

# Codex 피어리뷰 r7-03: 인용이 먼저 MISS면 앵커 파서에 **도달하지 못한다** —
# 그러면 앵커 쪽 회귀는 보호되지 않는다. 원문에 있는 정상 인용 + 어긋난 앵커를 써서
# `parse_anchors()`까지 실제로 가게 한다.
rc, out = run(
    item("F-41", '"Org 는 유일한 운영 주체이며 시스템 상태를 가집니다."', "L999"),
    extra=["--anchor-field", "(앵커)"],
)
check("양성(#347 r6-01·r7-03) — 캡처 그룹 앵커 필드가 실제 앵커 파서까지 가서 동작한다",
      rc == 0 and "앵커 불일치 1" in out, out)

# Codex 피어리뷰 r9-01: 종료 경계가 라벨·콜론을 안 보면, 여러 줄 인용 안의 비필드
# `- ...` 행 뒤 내용(닫는 따옴표 포함)이 잘려 앞부분만 대조되고 exit 0이 났다.
rc, out = run(
    '### F-60\n- 인용: "Org 는 유일한 운영 주체이며\n- not-a-field 날조 후속"\n\n'
)
check("양성(#347 r9-01) — 비필드 하이픈 행이 인용을 자르지 않는다",
      rc == 1 and "QUOTE-FAIL" in out, out)

# Codex 피어리뷰 r10-01 → docauth#364: 필드 문법을 만족하는 `- key: ...` 행이 여러 줄 인용
# 한가운데 있으면 **인용 필드가 그 행을 흡수**해 전체를 대조한다 — 날조는 exit 2(파싱 실패)가
# 아니라 exit 1(QUOTE-FAIL)로 잡힌다(거부 유지·코드 변경).
rc, out = run(
    '### F-64\n- 인용: "Org 는 유일한 운영 주체이며\n- key: 날조 후속"\n\n'
)
check("양성(#364) — 인용 안의 필드형 행은 흡수돼 날조가 MISS로 잡힌다(exit 1)",
      rc == 1 and "QUOTE-FAIL" in out and "원문 미존재 1" in out, out)

# docauth#364 양성 짝: 인용 안에 `- key:` 형식 행이 있어도 전체가 원문에 있으면 통과(신규 0).
rc, out = run(
    '### F-66\n- 인용: "예시 표기는\n- L12: 값 처럼 적는다."\n\n'
)
check("음성(#364) — `- key:` 행을 품은 여러 줄 인용 전체가 원문에 있으면 QUOTE-OK",
      rc == 0 and "QUOTE-OK" in out, out)

# docauth#364 설계 r1-01 반례: `" Org`(여는 따옴표 뒤 공백)도 열린 인용이다 — 휴리스틱 없이 토글.
rc, out = run(
    '### F-67\n- 인용: " Org 는 유일한 운영 주체이며\n- key: 날조 후속"\n\n'
)
check("양성(#364 설계 r1-01) — 따옴표 뒤 공백도 열림으로 세어 흡수·MISS(exit 1)",
      rc == 1 and "QUOTE-FAIL" in out, out)

# docauth#364 설계 r1-02 반례: **비인용** 필드의 열린 `"`는 다음 필드에 영향이 없다 —
# `설명: "메모` 뒤의 `인용(보조): "날조"`는 온전한 인용 필드로 대조된다.
rc, out = run(
    '### F-68\n- 설명: "메모\n- 인용(보조): "Org 는 유일한 날조 주체입니다."\n\n'
)
check("양성(#364 설계 r1-02) — 비인용 필드의 열린 따옴표가 뒤 인용 필드를 숨기지 않는다",
      rc == 1 and "QUOTE-FAIL" in out and "원문 미존재 1" in out, out)

# docauth#364 설계 r2-01: 인용 필드는 **인용 후보 행**을 절대 흡수하지 않는다 — 열린 인용
# 뒤의 `- 인용(보조): ...` 행은 흡수 대상이 아니라 잘린 인용의 증거(exit 2).
# (구현 r1 보완: 흡수 금지를 빼면 따옴표가 **닫혀** 대조로 가는 입력 — 금지가 있어야 exit 2다)
rc, out = run(
    '### F-69\n- 인용: "Org 는 유일한 운영 주체이며\n- 인용(보조): 시스템 상태를 가집니다."\n\n'
)
check("양성(#364 설계 r2-01) — 열린 인용 뒤의 인용 후보 행은 흡수하지 않고 exit 2",
      rc == 2 and "인용 파싱 실패" in out, out)

# docauth#364: 절 경계(`#` 행)도 흡수하지 않는다 — 항목 **안**의 `## 절` 행(split_items가 자르지 않는
# 수준)이라야 이 분기를 검증한다(구현 r1 보완). 흡수 금지를 빼면 닫혀서 대조(MISS)로 간다.
rc, out = run(
    '### F-70\n- 인용: "Org 는 유일한 운영 주체이며\n## 절\n시스템 상태를 가집니다."\n\n'
)
check("양성(#364) — 항목 안의 절 경계는 흡수하지 않고 열린 인용은 exit 2",
      rc == 2 and "인용 파싱 실패" in out, out)
rc, out = run(
    '### F-70\n- 인용: "Org 는 유일한 운영 주체이며\n\n### F-71\n- 인용: "시스템 상태를 가집니다."\n\n'
)
check("양성(#364) — 항목 경계에서 열린 채 끝난 인용은 exit 2",
      rc == 2 and "인용 파싱 실패" in out, out)

# docauth#364 구현 r1-01 반례: 흡수한 뒷부분이 추출에서 빠지면 거짓 QUOTE-OK — 추출도 같은 상태 기계.
rc, out = run('### F-80\n- 인용: "Org 는 유일한 운영 주체이며\\"\n- key: 날조"\n\n')
check("양성(#364 구현 r1-01) — escape 뒤 흡수한 날조 행이 추출에 포함돼 MISS(exit 1)",
      rc == 1 and "QUOTE-FAIL" in out, out)
rc, out = run('### F-81\n- 인용: “Org 는 유일한 운영 주체이며 “시스템” 상태를\n- key: 날조”\n\n')
check("양성(#364 구현 r1-01) — 중첩 한글 따옴표 첫 닫힘 뒤의 흡수 내용도 추출에 포함돼 MISS",
      rc == 1 and "QUOTE-FAIL" in out, out)
from audit_quotes import _extract_quoted  # noqa: E402
check("추출(#364) — 상태 기계 추출: escape·중첩·혼합",
      _extract_quoted('a "x\\"y" b “c “d” e” f "g"')[0] == ['x"y', "c “d” e", "g"]
      and _extract_quoted('a "x" b')[1] == "a \x00 b",
      str(_extract_quoted('a "x\\"y" b “c “d” e” f "g"')))

# docauth#364 구현 r1-02 반례: 흡수된 앵커형 행이 실제 앵커 필드를 가리면 안 된다.
_SRC_ANCHOR = "예시\n- 앵커: L1\n끝\n무관\n"
rc, out = run('### F-82\n- 인용: "예시\n- 앵커: L1\n끝"\n- 앵커: L4\n\n', source_text=_SRC_ANCHOR)
check("양성(#364 구현 r1-02) — 흡수된 앵커형 행이 아니라 남은 앵커 필드(L4)로 대조해 앵커 불일치 1",
      rc == 0 and "앵커 불일치 1" in out, out)
rc, out = run('### F-83\n- 인용: "예시\n- 앵커: L1\n끝"\n- 앵커: L1\n\n', source_text=_SRC_ANCHOR)
check("음성(#364 구현 r1-02) — 남은 앵커 필드가 맞으면 앵커 불일치 0",
      rc == 0 and "앵커 불일치 0" in out, out)

# docauth#364: 공백을 품은 인용(`"Org "`·`" Org"`)은 토글 규칙상 정상(현행과 동일).
for _q in ('"Org "', '" Org"'):
    rc, out = run(f"### F-72\n- 인용: {_q}\n\n")
    check(f"음성(#364) — 공백 포함 인용 {_q} 는 정상 대조", rc == 0 and "QUOTE-OK" in out, out)

# docauth#364 공시: inch 표기 `12"`의 홀수 따옴표는 계속 exit 2(현행 유지 — 어휘 휴리스틱 없음).
rc, out = run('### F-73\n- 인용: 12"\n\n')
check("양성(#364 공시) — 단독 홀수 따옴표(inch 표기)는 현행대로 exit 2",
      rc == 2 and "인용 파싱 실패" in out, out)

# docauth#364: escape는 연속 백슬래시의 홀짝으로 — `\\"`는 닫힌 escape + 진짜 따옴표.
rc, out = run('### F-74\n- 인용: "Org 는 유일한 운영 주체이며 \\\\"\n\n')
check("양성(#364) — `\\\\\"` 는 escape가 아니라 닫는 따옴표(열린 인용 없음 → 대조 → MISS)",
      rc == 1 and "QUOTE-FAIL" in out, out)
rc, out = run('### F-75\n- 인용: "Org 는 유일한 운영 주체이며 \\" 시스템\n\n')
check("양성(#364) — `\\\"` 는 escape라 인용이 열린 채 끝난다(exit 2)",
      rc == 2 and "인용 파싱 실패" in out, out)

# docauth#364: 한글 따옴표 depth + ASCII 혼합 — 경계 판정과 QUOTED 추출을 따로 단언한다.
rc, out = run(
    '### F-76\n- 인용: “Org 는 유일한 운영 주체이며 “시스템” 상태를 가집니다.”\n\n'
)
check("경계(#364) — 한글 중첩 따옴표는 depth로 닫혀 파싱 실패가 아니라 대조로 간다(내용은 원문에 없어 MISS)",
      rc == 1 and "QUOTE-FAIL" in out and "인용 파싱 실패 0" in out, out)
rc, out = run(
    '### F-77\n- 인용: “Org 는 유일한 운영 주체이며 “시스템 상태를\n- key: 가집니다.”\n\n'
)
check("양성(#364) — 한글 depth 1이 남으면 흡수 뒤에도 열린 인용(exit 2)",
      rc == 2 and "인용 파싱 실패" in out, out)
rc, out = run(
    '### F-78\n- 인용: "Org 는 유일한 운영 주체이며" “시스템 상태를 가집니다.”\n\n'
)
check("음성(#364) — ASCII·한글 따옴표 혼합은 각각 닫히면 정상", rc == 0 and "QUOTE-OK" in out, out)
from audit_quotes import iter_fields  # noqa: E402
import re as _re  # noqa: E402
_qf = iter_fields('- 인용: "a\n- key: b"\n- 앵커: L1\n- 설명: "x\n- 인용(보조): "y"', _re.compile("인용"))
check("경계(#364) — 흡수 결과의 필드 수·payload·unterminated가 설계대로",
      [(f.label, f.payload, f.is_quote, f.unterminated) for f in _qf]
      == [("인용", '"a\n- key: b"', True, False), ("앵커", "L1", False, False),
          ("설명", '"x', False, False), ("인용(보조)", '"y"', True, False)],
      str([(f.label, f.payload, f.is_quote, f.unterminated) for f in _qf]))

# docauth#364 신규 거부(0.33 「#364 닫힘」 기록): 역순 한글 따옴표 `”Org“`는 개수는 같지만 depth상 열림.
rc, out = run('### F-79\n- 인용: ”Org 는 유일한 운영 주체이며 시스템 상태를 가집니다.“\n\n')
check("양성(#364 신규 거부) — 역순 한글 따옴표는 exit 2",
      rc == 2 and "인용 파싱 실패" in out, out)

# 같은 방어가 **정상 여러 줄 인용**은 막지 않는다(따옴표가 닫혀 있으면 그대로 대조된다).
rc, out = run(
    '### F-65\n- 인용: "Org 는 유일한 운영 주체이며\n시스템 상태를 가집니다."\n\n'
)
check("음성(#347 r10-01) — 닫힌 여러 줄 인용은 그대로 대조된다",
      rc == 0 and "QUOTE-OK" in out, out)

# Codex 피어리뷰 r10-02: N/A 판정문도 추출 범위를 넘어 산출물 전체를 주장하지 않는다.
rc, out = run(
    "### D-01\n- 표기: 기존 vs 신규\n\n", extra=["--no-quote-ok", "D-01"]
)
check("음성(#347 r10-02) — N/A 문구가 --item-re 추출 범위로 한정된다",
      rc == 0 and "QUOTE-N/A" in out and "판정 범위 밖" in out, out)

# Codex 피어리뷰 r9-03: lone CR 산출물도 파싱된다(해시는 원시 바이트 기준).
rc, out = run('### F-61\r- 인용: "Org 는 유일한 운영 주체이며 시스템 상태를 가집니다."\r')
check("음성(#347 r9-03) — lone CR 산출물이 항목·필드를 놓치지 않는다",
      rc == 0 and "QUOTE-OK" in out, out)

# Codex 피어리뷰 r9-05: 콜론 금지는 **literal 라벨 콜론**만이다 — 정규식 문법은 통과한다.
for _frag in ("(?:근거 인용)", "(?i:근거 인용)"):
    rc, out = run(
        '### F-62\n- 근거 인용: "Org 는 유일한 운영 주체이며 시스템 상태를 가집니다."\n\n',
        extra=["--quote-field", _frag],
    )
    check(f"음성(#347 r9-05) — {_frag} 는 정상 동작한다", rc == 0 and "QUOTE-OK" in out, out)

# Codex 피어리뷰 r9-02: 통과 문구가 판정 **범위**를 밝힌다.
rc, out = run(item("F-63", '"Org 는 유일한 운영 주체이며 시스템 상태를 가집니다."'))
check("음성(#347 r9-02) — 통과 문구가 --item-re 추출 범위를 명시한다",
      rc == 0 and "추출한 항목" in out and "판정 범위 밖" in out, out)

# Codex 피어리뷰 r8-01: 호스트의 `\s*`가 개행을 삼켜 `-\n인용:`이 인용 필드가 됐다.
rc, out = run("### F-50\n-\n인용: \"Org 는 유일한 운영 주체이며 시스템 상태를 가집니다.\"\n\n")
check("양성(#347 r8-01) — 개행으로 갈린 라벨은 인용 필드가 아니다",
      rc == 3 and "QUOTE-NOTRUN" in out, out)

# 시작·종료 공백 문법이 어긋나면 뒤따르는 빈 필드가 앞 payload에 합쳐진다.
rc, out = run(
    "### F-51\n- 인용: \"Org 는 유일한 운영 주체이며 시스템 상태를 가집니다.\"\n-인용(보조):\n\n"
)
check("양성(#347 r8-01) — 공백 없는 두 번째 빈 필드도 필드로 인식된다(파싱 실패 exit 2)",
      rc == 2 and "인용 파싱 실패" in out, out)

# Codex 피어리뷰 r8-04 → docauth#364: 콜론 조기 검사는 **제거**했다. 정말 라벨 안 콜론을 요구하는
# selector(`근거: 인용`)는 매치 0 → QUOTE-NOTRUN(exit 3) + 힌트로 관측된다(fail-closed 유지, 2→3).
rc, out = run(item("F-52", '"Org 는 유일한 운영 주체이며 시스템 상태를 가집니다."'),
              extra=["--quote-field", "근거: 인용"])
check("양성(#364) — 콜론 요구 quote selector는 QUOTE-NOTRUN + 힌트(exit 3)",
      rc == 3 and "QUOTE-NOTRUN" in out and "라벨은 첫 콜론에서 끝난다" in out
      and "둘 중 하나이고" in out, out)
# anchor selector가 0건이면 인용 대조는 정상, `ANCHOR-NOTRUN` 경고만 남는다(종료 코드 불변).
rc, out = run(item("F-52", '"Org 는 유일한 운영 주체이며 시스템 상태를 가집니다."', "L2"),
              extra=["--anchor-field", "근거: 인용"])
check("음성(#364) — 콜론 요구 anchor selector는 ANCHOR-NOTRUN 경고 + QUOTE-OK",
      rc == 0 and "QUOTE-OK" in out and "ANCHOR-NOTRUN" in out, out)
rc, out = run(item("F-52", '"Org 는 유일한 운영 주체이며 시스템 상태를 가집니다."', "L2"))
check("음성(#364) — anchor가 매치되면 ANCHOR-NOTRUN이 없다",
      rc == 0 and "ANCHOR-NOTRUN" not in out, out)
# 구현 r1-03: 관측 분모는 "인용 필드가 있는 항목" — MISS·판본 불일치로 continue한 항목도 센다.
rc, out = run(item("F-52", '"Org 는 유일한 운영 주체이며 시스템 상태를 가집니다."', "L2")
              + item("F-54", '"Org 는 유일한 날조 주체입니다."', "L2"),
              extra=["--anchor-field", "근거: 인용"])
check("양성(#364 구현 r1-03) — 정상+MISS 섞여도 anchor 0매치면 ANCHOR-NOTRUN(분모 2종)",
      rc == 1 and "ANCHOR-NOTRUN" in out and "항목 2종" in out, out)
rc, out = run(item("F-52", '"Org 는 유일한 운영 주체이며 시스템 상태를 가집니다."', "L2")
              + "### F-55\n- 인용: \"Org 는 유일한 운영 주체이며 시스템 상태를 가집니다.\"\n- 근거 위치: L2\n\n",
              extra=["--anchor-field", "근거 위치"])
check("음성(#364 구현 r1-03) — 일부 항목만 anchor 매치면 경고 없음(전부 0매치일 때만)",
      rc == 0 and "ANCHOR-NOTRUN" not in out, out)
# 이슈의 selector 3형태·`인용\x3a?`·`(?:인용|x\x3ay)` — 종전 exit 2 → 정상 매치·정상 대조.
for _frag in ("인용(?::)?", "인용[:]?", "인용(?!:x)", "인용\\x3a?", "(?:인용|x\\x3ay)"):
    rc, out = run(item("F-52", '"Org 는 유일한 운영 주체이며 시스템 상태를 가집니다."'),
                  extra=["--quote-field", _frag])
    check(f"음성(#364) — 콜론을 품은 정규식 문법 {_frag} 는 정상 동작한다",
          rc == 0 and "QUOTE-OK" in out, out)

# Codex 피어리뷰 r8-03: NOTRUN 처방이 원인을 하나로 단정하지 않는다.
rc, out = run("### F-53\n- 설명만 있다\n\n")
check("음성(#347 r8-03) — NOTRUN이 두 원인을 모두 제시한다",
      rc == 3 and "둘 중 하나이고" in out and "산출물을 고친다" in out, out)

# Codex 피어리뷰 r7-01: 조각을 호스트에 삽입하지 않으므로, 0폭·DOTALL 조각이 다른 필드를
# 인용 필드로 만들 수 없다. 둘 다 실측된 거짓 QUOTE-OK였다.
rc, out = run(
    "### F-45\n- 설명: \"Org 는 유일한 운영 주체이며 시스템 상태를 가집니다.\"\n\n",
    extra=["--quote-field", "(?=.)"],
)
check("양성(#347 r7-01) — 0폭 조각이 아무 필드나 인용 필드로 만들지 못한다",
      rc == 3 and "QUOTE-NOTRUN" in out, out)

rc, out = run(
    "### F-46\n- 설명: x\n- 인용: \"Org 는 유일한 운영 주체이며 시스템 상태를 가집니다.\"\n\n",
    extra=["--quote-field", "설명.*인용"],
)
check("양성(#347 r7-01) — DOTALL이 조각으로 새어 이웃 필드를 삼키지 않는다",
      rc == 3 and "QUOTE-NOTRUN" in out, out)

# 접두 일치 계약은 유지된다: `근거 인용(보조)` 라벨에 `근거 인용`은 매치한다.
rc, out = run(
    "### F-47\n- 근거 인용(보조): \"Org 는 유일한 운영 주체이며 시스템 상태를 가집니다.\"\n\n",
    extra=["--quote-field", "근거 인용"],
)
check("음성(#347 r7-01) — 접두 일치 계약은 그대로", rc == 0 and "QUOTE-OK" in out, out)

# r7-02: 세 입력 전부 일반 파일이어야 하고, 디렉터리는 traceback이 아니라 사용 오류다.
with tempfile.TemporaryDirectory() as _d:
    _dir = Path(_d) / "adir"
    _dir.mkdir()
    _f = Path(_d) / "f.md"
    _f.write_text('### F-01\n- 인용: "x"\n', encoding="utf-8")
    for _label, _args in (
        ("synth", [str(_dir), "--source", str(_f)]),
        ("--source", [str(_f), "--source", str(_dir)]),
        ("--source-alt", [str(_f), "--source", str(_f), "--source-alt", str(_dir)]),
    ):
        _p = subprocess.run(
            [sys.executable, str(SCRIPT), *_args, "--no-quote-ok", "D-01"],
            capture_output=True, text=True,
        )
        check(f"양성(#347 r7-02) — {_label} 에 디렉터리는 사용 오류(traceback 아님)",
              _p.returncode == 2 and "Traceback" not in _p.stderr and "면제 선언" in _p.stdout,
              _p.stdout + _p.stderr)

# 최상위 alternation은 바깥 앵커 범위를 벗어난다 — 격리(`(?:...)`)로 막힌다.
rc, out = run(item("F-42", '"Org 는 유일한 운영 주체이며 시스템 상태를 가집니다."'),
              extra=["--quote-field", "인용|앵커"])
check("음성(#347 r6-01) — alternation 필드도 격리돼 정상 동작", rc == 0 and "QUOTE-OK" in out, out)

for _opt in ("--quote-field", "--anchor-field"):
    rc, out = run(item("F-43", '"Org 는 유일한 운영 주체이며 시스템 상태를 가집니다."'),
                  extra=[_opt, ""])
    check(f"양성(#347 r6-01) — {_opt} 빈 조각은 사용 오류", rc == 2 and "비어 있을 수 없다" in out, out)
    rc, out = run(item("F-44", '"Org 는 유일한 운영 주체이며 시스템 상태를 가집니다."'),
                  extra=[_opt, "x?"])
    check(f"양성(#347 r6-01) — {_opt} 빈 매치 가능 조각은 사용 오류",
          rc == 2 and "빈 문자열에 매치" in out, out)

# Codex 피어리뷰 r6-02: `--emit-anchors`에 디렉터리를 주면 traceback으로 죽었다.
with tempfile.TemporaryDirectory() as _d:
    _p = subprocess.run(
        [sys.executable, str(SCRIPT), "--emit-anchors", _d, "--no-quote-ok", "D-01"],
        capture_output=True, text=True,
    )
    check("양성(#347 r6-02) — --emit-anchors 디렉터리는 사용 오류(traceback 아님)",
          _p.returncode == 2 and "Traceback" not in _p.stderr and "면제 선언" in _p.stdout,
          _p.stdout + _p.stderr)

# 음성(r5-03): 기본값과 escaped-bold 정상 인자는 그대로 통과한다.
rc, out = run(
    "## F-31\n- **근거 인용**: \"Org 는 유일한 운영 주체이며 시스템 상태를 가집니다.\"\n\n",
    extra=["--item-re", r"^## (F-[0-9]+)", "--quote-field", r"\*\*근거 인용\*\*"],
)
check("음성(#347 r5-03) — escaped-bold 정상 인자는 그대로 통과", rc == 0 and "QUOTE-OK" in out, out)

# Codex 피어리뷰 r5-02: --emit-anchors 성공 반환도 면제 공시를 건너뛰지 않는다.
with tempfile.TemporaryDirectory() as _d:
    _src = Path(_d) / "src.md"
    _src.write_text(SOURCE, encoding="utf-8")
    _p = subprocess.run(
        [sys.executable, str(SCRIPT), "--emit-anchors", str(_src), "--no-quote-ok", "D-01"],
        capture_output=True, text=True,
    )
    check("양성(#347 r5-02) — --emit-anchors 성공 경로도 면제 선언을 남긴다",
          _p.returncode == 0 and "면제 선언" in _p.stdout, _p.stdout + _p.stderr)

# Codex 피어리뷰 r4-02: --help가 보장 수준을 실제보다 강하게 읽히지 않게 한다.
_help = subprocess.run([sys.executable, str(SCRIPT), "--help"], capture_output=True, text=True)
check("음성(#347 r4-02) — --help가 자기선언·유형 미검증을 고지한다",
      "자기선언" in _help.stdout and "미검증" in _help.stdout, _help.stdout)

print(f"\n{PASS_COUNT} passed, {FAIL_COUNT} failed")
sys.exit(1 if FAIL_COUNT else 0)
