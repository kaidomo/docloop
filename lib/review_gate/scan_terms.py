#!/usr/bin/env python3
"""review-gate 용어 결정론 스캔 — terms.yaml(대응 사전) 기반, fail-closed.

사전(canonical↔금지 변형)을 입력으로 고정하고 대상 전문에서 금지 변형의
literal 출현을 전수 열거한다(CONTRACT §3 — 골든 r1 개선 후보 1). 즉석 grep과
달리 대응 관계가 사전으로 고정돼 있어 같은 사전·같은 전문이면 결과가 결정론적이다.

히트는 '후보'다 — finding 확정은 합성 단계의 §4 출력 계약·레지스트리 대조 몫.

종료 코드:
  0 = 스캔 성공 (히트 0건 포함 — 히트 유무는 실패가 아님)
  1 = 입력 오류(사전 스키마 위반·파일 없음 등) — 스캔 결과 무효(fail-closed)
  2 = 환경 오류(PyYAML 부재 등)

사용: python3 scan_terms.py <terms.yaml> <대상 전문 파일>
"""
import argparse
import datetime
import hashlib
import os
import re
import sys

try:
    import yaml
except ImportError:  # PyYAML은 하우스 표준 의존성
    print("PyYAML 필요: pip install pyyaml", file=sys.stderr)
    sys.exit(2)

# fullmatch()로 검사한다 — `$`는 문자열 끝 개행 앞에서도 맞으므로 `.match()`와
# 함께 쓰면 끝에 개행이 붙은 값(예: YAML block scalar)이 통과한다. 이 값들은
# 신선도 비교(source_hash)·형식 검증에 쓰이는 식별자다(#231).
RE_DATE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
RE_SHA256 = re.compile(r"^[0-9a-f]{64}$")


def sha256_file(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


def _is_str(v):
    return isinstance(v, str) and v.strip() != ""


def _has_newline(v):
    return isinstance(v, str) and ("\n" in v or "\r" in v)


def _is_str_list(v):
    return isinstance(v, list) and all(_is_str(x) for x in v)


def _quote_hint(key, value):
    """따옴표 누락으로 날짜가 date 객체가 된 경우 해법을 덧붙인다 (#194·#226).

    판정은 바꾸지 않는다 — 문자열 요구는 그대로고, 메시지만 고친 방법을 알려준다.
    (datetime.datetime은 datetime.date의 하위 클래스라 함께 걸린다.)
    """
    if key == "updated_at" and isinstance(value, datetime.date):
        return (
            f' — 따옴표로 감싸라(예: {key}: "{value.isoformat()[:10]}"). '
            "따옴표가 없으면 YAML이 date 객체로 파싱한다"
        )
    return ""


def validate_terms(data):
    """스키마 검사 — 오류 문자열 리스트 반환(비면 통과)."""
    E = []
    if not isinstance(data, dict):
        return ["최상위가 매핑이 아님"]
    meta = data.get("meta")
    if not isinstance(meta, dict):
        E.append("meta 블록 누락")
        meta = {}
    if not _is_str(meta.get("target")):
        E.append("meta.target 누락/비문자열")
    if not _is_str(meta.get("updated_at")) or not RE_DATE.fullmatch(meta.get("updated_at", "")):
        E.append(
            "meta.updated_at: YYYY-MM-DD 필수" + _quote_hint("updated_at", meta.get("updated_at"))
        )
    terms = data.get("terms")
    if not isinstance(terms, list) or not terms:
        E.append("terms가 비어있거나 리스트가 아님")
        terms = []
    seen_forbidden = {}  # forbidden 값 → term tag (전역 유일성, r1-04)
    for i, t in enumerate(terms):
        tag = f"terms[{i}]"
        if not isinstance(t, dict):
            E.append(f"{tag}: 매핑이 아님")
            continue
        if _is_str(t.get("canonical")):
            tag = f"term '{t['canonical']}'"
        else:
            E.append(f"{tag}: canonical 누락/비문자열")
        fb = t.get("forbidden")
        ex = t.get("except")
        # 개행 금지 (r1-02): 스캔이 줄 단위라 개행 포함 값은 영원히 매치 불가
        for label, vals in (("canonical", [t.get("canonical")]), ("forbidden", fb), ("except", ex)):
            for v in vals if isinstance(vals, list) else vals if vals else []:
                if _has_newline(v):
                    E.append(f"{tag}: {label} 값에 개행(CR/LF) 포함 — 줄 단위 스캔에서 매치 불가(fail-closed): {v!r}")
        if not _is_str_list(fb) or not fb:
            E.append(f"{tag}: forbidden은 비어있지 않은 문자열 리스트여야 함")
        else:
            can = t.get("canonical")
            for v in fb:
                if _is_str(can) and v == can:
                    E.append(f"{tag}: forbidden에 canonical 자신이 포함됨: {v!r}")
                if v in seen_forbidden:
                    E.append(f"{tag}: forbidden {v!r}가 {seen_forbidden[v]}와 중복 — canonical 매핑이 갈린다(전역 유일 필수)")
                else:
                    seen_forbidden[v] = tag
        if ex is not None:
            if not _is_str_list(ex):
                E.append(f"{tag}: except는 문자열 리스트여야 함")
            elif _is_str_list(fb):
                for e in ex:
                    if e in fb:
                        E.append(f"{tag}: except {e!r}가 같은 항목의 forbidden과 동일 — 그 변형이 영구 침묵된다(r1-04)")
                    elif not any(v in e and v != e for v in fb):
                        E.append(
                            f"{tag}: except {e!r}는 어떤 forbidden 변형도 진부분 문자열로 포함하지 않음(무효 예외)"
                        )
        nt = t.get("note")
        if nt is not None and not _is_str(nt):
            E.append(f"{tag}: note는 문자열이어야 함")
    # canonical ⊇ forbidden 포함 관계 (r1-04): 정본 표기 자체가 히트되는 구성은
    # 해당 forbidden 항목의 except에 그 canonical이 등재된 경우에만 허용
    canonicals = [t.get("canonical") for t in terms if isinstance(t, dict) and _is_str(t.get("canonical"))]
    for t in terms:
        if not isinstance(t, dict) or not _is_str_list(t.get("forbidden")):
            continue
        ex = t.get("except") if _is_str_list(t.get("except")) else []
        ttag = f"term '{t['canonical']}'" if _is_str(t.get("canonical")) else "terms[?]"
        for v in t["forbidden"]:
            for c in canonicals:
                if v != c and v in c and c not in ex:
                    E.append(
                        f"{ttag}: forbidden {v!r}가 canonical {c!r}의 부분 문자열 — 정본 표기가 히트된다. "
                        f"의도라면 except에 {c!r}를 등재, 아니면 사전 수정(fail-closed)"
                    )
    return E


def check_provenance(data, base_path):
    """meta.source_ref/source_hash 선택 provenance — 있으면 신선도 강제(fail-closed). 오류 리스트 반환."""
    E = []
    meta = data.get("meta") if isinstance(data, dict) else None
    if not isinstance(meta, dict):
        return E
    ref, want = meta.get("source_ref"), meta.get("source_hash")
    if ref is None and want is None:
        return E
    if not _is_str(ref) or not _is_str(want):
        return ["meta.source_ref/source_hash는 쌍으로 있어야 함(하나만 있으면 provenance 검증 불가 — fail-closed)"]
    if not RE_SHA256.fullmatch(want):
        return ["meta.source_hash: sha256 hex(64자 소문자) 형식이 아님"]
    p = os.path.expanduser(ref)
    if not os.path.isabs(p):
        p = os.path.join(os.path.dirname(os.path.abspath(base_path)), p)
    if not os.path.exists(p):
        return [f"meta.source_ref 원본 없음: {p} (신선도 검증 불가 — fail-closed)"]
    got = sha256_file(p)
    if got != want:
        E.append(f"meta: STALE — 사전 출처 hash 불일치(기록 {want[:12]}… vs 현재 {got[:12]}…). 재시드 필요")
    return E


def _spans(line, needle):
    """line 내 needle의 모든 (start, end) 구간."""
    out, i = [], line.find(needle)
    while i != -1:
        out.append((i, i + len(needle)))
        i = line.find(needle, i + 1)
    return out


def scan(terms, text):
    """히트 리스트 반환: (line_no, variant, canonical, line). except 구간 내 출현은 제외."""
    hits = []
    for line_no, line in enumerate(text.splitlines(), 1):
        for t in terms:
            allowed = []
            for e in t.get("except") or []:
                allowed.extend(_spans(line, e))
            for v in t["forbidden"]:
                for s, epos in _spans(line, v):
                    if any(a <= s and epos <= b for a, b in allowed):
                        continue
                    hits.append((line_no, v, t["canonical"], line.strip()))
    return hits


def main():
    ap = argparse.ArgumentParser(description="review-gate 용어 결정론 스캔(fail-closed)")
    ap.add_argument("terms_yaml")
    ap.add_argument("target")
    args = ap.parse_args()

    try:
        with open(args.terms_yaml, encoding="utf-8") as f:
            data = yaml.safe_load(f)
    except FileNotFoundError:
        print(f"ERROR: 사전 파일 없음: {args.terms_yaml}")
        sys.exit(1)
    except yaml.YAMLError as e:
        print(f"ERROR: 사전 YAML 파싱 실패: {e}")
        sys.exit(1)

    E = validate_terms(data) + check_provenance(data, args.terms_yaml)
    if E:
        for m in E:
            print(f"ERROR: {m}")
        print("결과: FAIL — 사전 무효, 스캔 결과를 사용할 수 없다(fail-closed)")
        sys.exit(1)

    try:
        with open(args.target, encoding="utf-8") as f:
            text = f.read()
    except FileNotFoundError:
        print(f"ERROR: 대상 파일 없음: {args.target}")
        sys.exit(1)

    # 감사 메타데이터 (r1-03): 어느 사전·어느 대상으로 스캔했는지 산출물에 남긴다
    print(f"DICT: {args.terms_yaml} sha256 {sha256_file(args.terms_yaml)} (항목 {len(data['terms'])}종)")
    print(f"TARGET: {args.target} sha256 {sha256_file(args.target)}")

    hits = scan(data["terms"], text)
    for line_no, v, can, line in hits:
        excerpt = line if len(line) <= 120 else line[:117] + "…"
        print(f"HIT line {line_no}: {v!r} → canonical {can!r} | {excerpt}")
    print(f"결과: SCAN-OK — 히트 {len(hits)}건 (항목 {len(data['terms'])}종 대조)")
    sys.exit(0)


if __name__ == "__main__":
    main()
