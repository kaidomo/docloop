#!/usr/bin/env python3
"""review-gate 결정 레지스트리(decisions.yaml) 검증기 — fail-closed.

통과해야만 레지스트리를 finding 억제(suppression)에 쓸 수 있다(CONTRACT §2).
검사: 스키마(필수 필드·타입·형식·enum·id 유일성·YAML 중복 키) + supersede 참조
무결성·비순환 + 원 결정문서 content hash 일치(신선도, meta·항목 레벨)
+ 동일 `subject` 슬롯의 현행 확정 결정 중복(#199) + 대체 관계 날짜 역전
+ 억제 발동(재론금지 존재) 시 subject 미표기 fail-closed 승격(#217).

종료 코드 (r1-01: 구조 통과와 억제 적격을 기계적으로 구분):
  0 = 완전 통과 — 억제 적격(suppression eligible)
  1 = 검증 실패 — 억제 사용 금지(fail-closed)
  2 = 환경 오류(PyYAML 부재 등)
  3 = 구조 통과이나 hash 미검증(--skip-hash) — 억제 부적격

사용: python3 validate_decisions.py <decisions.yaml> [--skip-hash]
"""
import argparse
import datetime
import hashlib
import os
import re
import sys
import unicodedata

try:
    import yaml
except ImportError:  # PyYAML은 하우스 표준 의존성
    print("PyYAML 필요: pip install pyyaml", file=sys.stderr)
    sys.exit(2)

REQUIRED_META = ("target", "source_ref", "source_version_hash", "updated_at")
REQUIRED_DECISION = ("id", "decision", "status", "date", "evidence")
OPTIONAL_STR_DECISION = (
    "scope",
    "subject",
    "supersedes",
    "superseded_by",
    "source_ref",
    "source_hash",
)
STATUS_ENUM = {"확정", "기각", "재론금지"}
DATE_FIELDS = ("date", "updated_at")  # 스키마상 YYYY-MM-DD 문자열 필드
# fullmatch()로 검사한다 — `$`는 문자열 끝 개행 앞에서도 맞으므로 `.match()`와
# 함께 쓰면 "2026-08-10\n"(YAML block scalar가 흔히 붙이는 형태) 같은 값이
# 통과한다. 이 값들은 동등 비교로 쓰이는 식별자(날짜·해시)라 한 identity가
# 두 표기로 통과하면 신선도·유일성 검사가 조용히 뚫린다(#231, PR #227의
# is_stable_id/fullmatch 수정과 같은 클래스).
RE_DATE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
RE_SHA256 = re.compile(r"^[0-9a-f]{64}$")


class DupKeyError(Exception):
    pass


class _StrictLoader(yaml.SafeLoader):
    """YAML 중복 키를 오류로 처리 (r1-09)."""


def _strict_map(loader, node, deep=False):
    seen = set()
    for k_node, _ in node.value:
        k = loader.construct_object(k_node, deep=deep)
        if k in seen:
            raise DupKeyError(f"YAML 중복 키: {k!r} (line {k_node.start_mark.line + 1})")
        seen.add(k)
    return yaml.SafeLoader.construct_mapping(loader, node, deep)


_StrictLoader.add_constructor(
    yaml.resolver.BaseResolver.DEFAULT_MAPPING_TAG, _strict_map
)


def sha256_file(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


def _is_str(v):
    return isinstance(v, str) and v.strip() != ""


def _quote_hint(key, value):
    """따옴표 누락으로 날짜가 date 객체가 된 경우 해법을 덧붙인다 (#194).

    판정은 바꾸지 않는다 — 문자열 요구는 그대로고, 메시지만 고친 방법을 알려준다.
    (datetime.datetime은 datetime.date의 하위 클래스라 함께 걸린다.)
    """
    if key in DATE_FIELDS and isinstance(value, datetime.date):
        return (
            f' — 따옴표로 감싸라(예: {key}: "{value.isoformat()[:10]}"). '
            "따옴표가 없으면 YAML이 date 객체로 파싱한다"
        )
    return ""


_ASCII_LOWER = str.maketrans(
    "ABCDEFGHIJKLMNOPQRSTUVWXYZ", "abcdefghijklmnopqrstuvwxyz"
)


def _subject_key(s):
    """subject 슬롯 키 정규화 — 결정론적 문자열 동치만 쓴다(유사도·의미 판정 없음).

    NFC 정규화 + 공백 런 축약 + 앞뒤 공백 제거 + **ASCII 대문자만** 소문자화.
    보이지 않는 차이(앞뒤 공백·중복 공백·ASCII 대소문자)만 흡수하고 그 밖에는
    글자 그대로 비교한다. `str.casefold()`를 쓰지 않는 이유(피어리뷰 r1-03):
    casefold는 'Maße'와 'Masse'처럼 **서로 다른 슬롯 이름을 같은 키로 뭉갠다** —
    fail-closed 게이트에서 그 충돌은 거짓 FAIL이 되어 정당한 억제를 통째로 막는다.
    """
    t = re.sub(r"\s+", " ", unicodedata.normalize("NFC", s)).strip()
    return t.translate(_ASCII_LOWER)


def _resolve(base_yaml_path, ref):
    p = os.path.expanduser(ref)
    if not os.path.isabs(p):
        p = os.path.join(os.path.dirname(os.path.abspath(base_yaml_path)), p)
    return p


def _check_hash(base_yaml_path, ref, want, label, E, skip_hash, W):
    """source_ref/hash 신선도 검사 — fail-closed (meta·항목 공용)."""
    if skip_hash:
        W.append(f"{label}: hash 미검증(--skip-hash) — 신선도 무보증")
        return
    src_path = _resolve(base_yaml_path, ref)
    if not os.path.exists(src_path):
        E.append(f"{label}: source_ref 원본 없음: {src_path} (신선도 검증 불가 — fail-closed)")
        return
    got = sha256_file(src_path)
    if got != want:
        E.append(
            f"{label}: STALE — hash 불일치, 원 결정문서가 변경됨. 재시드 필요 "
            f"(기록 {str(want)[:12]}… vs 현재 {got[:12]}…). 억제 금지"
        )


def validate(path, skip_hash=False, content=None):
    """(errors, warnings, info) 반환."""
    E, W, I = [], [], []
    try:
        if content is None:
            with open(path, encoding="utf-8") as f:
                data = yaml.load(f, Loader=_StrictLoader)
        else:
            data = yaml.load(content, Loader=_StrictLoader)
    except FileNotFoundError:
        return [f"파일 없음: {path}"], [], []
    except DupKeyError as e:
        return [str(e)], [], []
    except yaml.YAMLError as e:
        return [f"YAML 파싱 실패: {e}"], [], []

    if not isinstance(data, dict):
        return ["최상위가 매핑이 아님"], [], []

    meta = data.get("meta")
    if not isinstance(meta, dict):
        E.append("meta 블록 누락")
        meta = {}
    for k in REQUIRED_META:
        if k not in meta or meta.get(k) is None:
            E.append(f"meta.{k} 누락")
        elif not _is_str(meta[k]):
            E.append(
                f"meta.{k}: 비어있지 않은 문자열이어야 함 (현재 {type(meta[k]).__name__})"
                + _quote_hint(k, meta[k])
            )
    if _is_str(meta.get("source_version_hash")) and not RE_SHA256.fullmatch(meta["source_version_hash"]):
        E.append("meta.source_version_hash: sha256 hex(64자 소문자) 형식이 아님")
    if _is_str(meta.get("updated_at")) and not RE_DATE.fullmatch(meta["updated_at"]):
        E.append("meta.updated_at: YYYY-MM-DD 형식이 아님")

    decisions = data.get("decisions")
    if not isinstance(decisions, list) or not decisions:
        E.append("decisions가 비어있거나 리스트가 아님")
        decisions = []

    ids = {}
    for i, d in enumerate(decisions):
        tag = f"decisions[{i}]"
        if not isinstance(d, dict):
            E.append(f"{tag}: 매핑이 아님")
            continue
        did = d.get("id")
        if _is_str(did):
            tag = f"decision '{did}'"
            ids[did] = ids.get(did, 0) + 1
        for k in REQUIRED_DECISION:
            v = d.get(k)
            if v is None:
                E.append(f"{tag}: {k} 누락")
            elif not _is_str(v):
                E.append(
                    f"{tag}: {k}는 비어있지 않은 문자열이어야 함 (현재 {type(v).__name__})"
                    + _quote_hint(k, v)
                )
        for k in OPTIONAL_STR_DECISION:
            v = d.get(k)
            if v is not None and not _is_str(v):
                E.append(f"{tag}: {k}는 문자열이어야 함 (현재 {type(v).__name__})")
        st = d.get("status")
        if _is_str(st) and st not in STATUS_ENUM:
            E.append(f"{tag}: status '{st}' 무효(허용 {sorted(STATUS_ENUM)})")
        dt = d.get("date")
        if _is_str(dt) and not RE_DATE.fullmatch(dt):
            E.append(f"{tag}: date는 YYYY-MM-DD 형식이어야 함")
        sh = d.get("source_hash")
        if _is_str(sh) and not RE_SHA256.fullmatch(sh):
            E.append(f"{tag}: source_hash: sha256 hex(64자 소문자) 형식이 아님")
        # 항목 레벨 provenance (r1-10): source_ref가 있으면 source_hash 필수 + 신선도 검사
        if _is_str(d.get("source_ref")):
            if not _is_str(sh):
                E.append(f"{tag}: source_ref가 있으면 source_hash 필수(외부 provenance 해시 보호)")
            elif RE_SHA256.fullmatch(sh):
                _check_hash(path, d["source_ref"], sh, tag, E, skip_hash, W)

    for did, c in ids.items():
        if c > 1:
            E.append(f"id 중복: '{did}' ×{c}")
    id_set = set(ids)

    # supersede 참조 무결성 + 자기참조 + 순환 (r1-09)
    edges = {}
    for d in decisions:
        if not isinstance(d, dict) or not _is_str(d.get("id")):
            continue
        did = d["id"]
        for ref_key in ("supersedes", "superseded_by"):
            ref = d.get(ref_key)
            if not _is_str(ref):
                continue
            if ref == did:
                E.append(f"decision '{did}': {ref_key}가 자기 자신을 참조")
                continue
            if ref not in id_set:
                E.append(f"decision '{did}': {ref_key} '{ref}'가 본 파일에 없음(dangling)")
                continue
            if ref_key == "superseded_by":
                edges.setdefault(did, set()).add(ref)
            else:  # supersedes: 대체 방향으로 정규화(old → new)
                edges.setdefault(ref, set()).add(did)
    # (자기 선언 superseded_by에 대한 억제 부적격 통지는 대체 인정 판정 뒤에 낸다 — 아래)

    def _has_cycle():
        WHITE, GRAY, BLACK = 0, 1, 2
        color = {n: WHITE for n in id_set}

        def dfs(n):
            color[n] = GRAY
            for m in edges.get(n, ()):
                if color.get(m) == GRAY:
                    return True
                if color.get(m) == WHITE and dfs(m):
                    return True
            color[n] = BLACK
            return False

        return any(color[n] == WHITE and dfs(n) for n in list(id_set))

    if _has_cycle():
        E.append("supersede 관계에 순환 존재 — 대체 사슬이 닫힘(비순환이어야 함)")

    # ── #199: 동일 subject 슬롯 모순 + 날짜 역전 ──────────────────────────────
    # subject는 "이 결정이 채우는 슬롯"의 키다(스키마 참조). 같은 슬롯에 현행(=대체되지
    # 않은) 확정 결정이 둘 이상 남아 있으면 어느 것이 억제 baseline인지 기계적으로
    # 판정할 수 없다 → fail-closed(ERROR). 판정은 정규화된 문자열 동치로만 하며,
    # 결정문 유사도 같은 확률적 수단은 쓰지 않는다(의미 충돌 탐지는 범위 밖).
    by_id = {}
    for d in decisions:
        if isinstance(d, dict) and _is_str(d.get("id")):
            by_id.setdefault(d["id"], d)

    def _subj(did):
        v = by_id.get(did, {}).get("subject")
        return _subject_key(v) if _is_str(v) else None

    def _edge_reject_reason(old, new):
        """대체 관계를 '현행에서 빼기'에 쓸 수 있는가 — 못 쓰면 사유(WARN 문구)를 준다.

        대체 한 줄이 피대체 항목을 슬롯에서 빼주므로, **검증되지 않은 대체는 같은 슬롯의
        진짜 모순을 은폐한다**(fail-open — #199가 고치려던 바로 그 실패). 그래서 아래
        세 경우에는 대체를 인정하지 않는다. 인정하지 않아도 ERROR가 아니라 WARN이다:
        해소법(후속 결정을 확정으로 두거나 같은 subject를 적기)이 한 줄이고, 슬롯에
        현행 확정이 하나뿐이면 그대로 PASS라 거짓 FAIL로 정당한 억제를 막지 않는다.
        """
        st_new = by_id.get(new, {}).get("status")
        if st_new != "확정":  # 피어리뷰 r2-02: 기각·재론금지 결정은 슬롯을 넘겨받지 못한다
            return (
                f"미확정 대체: '{new}'(status '{st_new}')가 '{old}'를 대체 — 확정 결정만 "
                "슬롯의 현행을 넘겨받는다. 대체로 인정하지 않았다(피대체 항목이 슬롯에 남음)"
            )
        s_old, s_new = _subj(old), _subj(new)
        if s_old is None:  # 피대체 항목이 슬롯 미표기 → 애초에 모순 검사 대상이 아니다
            return None
        if s_new is None:  # 피어리뷰 r2-01: 후속이 미표기면 같은 슬롯인지 검증할 수 없다
            return (
                f"슬롯 검증 불가 대체: '{new}'에 subject가 없어 "
                f"'{old}'(subject '{by_id[old]['subject']}')와 같은 슬롯인지 확인할 수 없다 "
                "— 후속 결정에 같은 subject를 적어야 대체로 인정된다"
            )
        if s_old != s_new:  # 피어리뷰 r1-01: 다른 슬롯을 가리키는 오기로 모순을 덮을 수 없다
            return (
                f"슬롯 불일치 대체: '{new}'(subject '{by_id[new]['subject']}')가 "
                f"'{old}'(subject '{by_id[old]['subject']}')를 대체 — 표기가 맞다면 "
                "두 항목의 subject를 같은 슬롯으로 정렬해야 대체로 인정된다"
            )
        return None

    retired, honored = set(), {}
    for old in sorted(edges):
        for new in sorted(edges[old]):
            reason = _edge_reject_reason(old, new)
            if reason:
                W.append(reason)
                continue
            retired.add(old)
            honored.setdefault(old, set()).add(new)

    # 억제 부적격 통지 — 두 방향의 근거가 다르므로 판정도 다르다(피어리뷰 r1-02·r3-01).
    #
    # ① 자기 선언 `superseded_by`: 작성자가 스스로 "나는 대체됐다"고 적은 것이다.
    #    CONTRACT §2가 무조건 억제 금지로 규정하므로 대체가 인정되지 않아도 금지는 유지한다
    #    — 대체를 인정하지 않는 것은 "이 항목이 현행임을 확인했다"는 뜻이 아니라 "현행 여부를
    #    판정할 수 없다"는 뜻이고, 스스로 이력이라 적은 항목으로 finding을 죽이는 쪽이
    #    §2가 막으려는 실패다. 다만 문구는 그 미정 상태를 그대로 말한다(단정 금지).
    # ② 남이 적은 `supersedes`: 제3자의 주장이라 **인정된 대체일 때만** 부적격으로 본다.
    #    인정하지 않은 주장으로 슬롯에 남겨둔 항목을 동시에 억제 부적격이라 선언하면
    #    검증기가 스스로 모순되고, 정당한 억제 근거를 근거 없이 죽인다.
    for d in decisions:
        if not isinstance(d, dict) or not _is_str(d.get("id")):
            continue
        did, ref = d["id"], d.get("superseded_by")
        if not _is_str(ref):
            continue
        if honored.get(did):  # 인정된 대체가 하나라도 있으면 실제로 퇴역한 항목이다
            I.append(f"decision '{did}': superseded_by 존재 — 억제 근거 사용 금지(이력 보존용)")
        else:
            I.append(
                f"decision '{did}': superseded_by '{ref}'를 스스로 적었으나 그 대체는 "
                "인정되지 않았다(위 WARN·ERROR 참조) — 현행 여부 미정이라 슬롯에는 남기되 "
                "억제 근거 사용 금지(CONTRACT §2). 표기를 고쳐 상태를 확정하라"
            )
    for old in sorted(honored):
        if _is_str(by_id.get(old, {}).get("superseded_by")):
            continue  # 위에서 이미 통지됨
        srcs = ", ".join(f"'{n}'" for n in sorted(honored[old]))
        I.append(f"decision '{old}': {srcs}의 supersedes 대상 — 억제 근거 사용 금지(이력 보존용)")

    # ── #218: 검사 대상은 여전히 `확정`만이다(범위 고정 — 사람 결정, 넓히지 않는다) ──
    # 넓히려면 `재론금지`끼리의 "같은 슬롯 현행" 판정 규칙(재론금지가 다른 재론금지를
    # 대체할 수 있는가·복수 재론금지가 같은 슬롯에 있을 때 무엇이 모순인가, `확정`과
    # `재론금지`가 같은 슬롯에서 서로 모순되면 어느 쪽을 우선하는가)을 새로 정의해야
    # 하는데, 그 규칙은 아직 사람이 판정하지 않았다(#218 이슈 본문 — 별도 논점으로 명시
    # 유보). 위 _edge_reject_reason도 같은 이유로 재론금지가 슬롯을 넘겨받는 것을 인정하지
    # 않는다("미확정 대체"). 대신 이 검사가 놓치는 실제 사고 경로 — 레지스트리에
    # `재론금지`가 있어 억제가 실제로 발동하는데 subject 미표기로 이 검사 자체가 발화하지
    # 않는 경우 — 는 아래 subject 미표기 처리에서 억제 발동 시점 fail-closed로 막는다(#217).
    has_suppressing = any(
        isinstance(d, dict) and d.get("status") == "재론금지" for d in decisions
    )

    groups, unlabeled = {}, []
    for d in decisions:
        if not isinstance(d, dict) or not _is_str(d.get("id")):
            continue
        if d.get("status") != "확정":  # 기각·재론금지는 이 검사의 대상이 아니다(범위 고정, 위 설명 참조)
            continue
        if d["id"] in retired:
            continue
        subj = d.get("subject")
        if _is_str(subj):
            groups.setdefault(_subject_key(subj), []).append((d["id"], subj))
        else:
            unlabeled.append(d["id"])

    for _key, members in sorted(groups.items()):
        if len(members) > 1:
            ids_txt = ", ".join(f"'{i}'" for i, _ in members)
            E.append(
                f"subject '{members[0][1]}' 슬롯에 현행 확정 결정 {len(members)}건({ids_txt}) — "
                "supersedes/superseded_by로 현행 결정을 명시해야 함. 어느 결정이 baseline인지 "
                "판정 불가라 억제 금지(fail-closed)"
            )
    if len(unlabeled) > 1:
        msg = (
            f"subject 미표기 확정 결정 {len(unlabeled)}건({', '.join(sorted(unlabeled))}) — "
            "동일 슬롯 모순 검사가 이 항목들에는 수행되지 않았다(검사 공백)"
        )
        if has_suppressing:  # #217 1안: 억제 발동 시점에만 fail-closed
            E.append(
                msg + " — 이 레지스트리에 재론금지 결정이 존재해 억제가 실제로 발동한다: "
                "이 공백이 남으면 억제 baseline의 모순 여부를 판정할 수 없는 채로 억제가 "
                "발동한다(#217). subject를 채우거나 supersedes/superseded_by로 현행을 "
                "정렬해야 억제에 쓸 수 있다"
            )
        else:
            W.append(msg)

    # 날짜 역전: 대체 결정이 피대체 결정보다 이른 날짜면 대체 방향이 의심스럽다(경고).
    for old in sorted(edges):
        for new in sorted(edges[old]):
            d_old, d_new = by_id.get(old, {}).get("date"), by_id.get(new, {}).get("date")
            if not (_is_str(d_old) and _is_str(d_new)):
                continue
            if not (RE_DATE.fullmatch(d_old) and RE_DATE.fullmatch(d_new)):
                continue
            if d_new < d_old:
                W.append(
                    f"날짜 역전: '{new}'({d_new})가 '{old}'({d_old})를 대체하는데 날짜가 더 이르다 "
                    "— 대체 방향 또는 date 확인 필요"
                )

    # meta 신선도(fail-closed 핵심)
    if _is_str(meta.get("source_ref")) and _is_str(meta.get("source_version_hash")) and RE_SHA256.fullmatch(meta["source_version_hash"]):
        _check_hash(path, meta["source_ref"], meta["source_version_hash"], "meta", E, skip_hash, W)

    return E, W, I


def main():
    ap = argparse.ArgumentParser(description="review-gate decisions.yaml 검증(fail-closed)")
    ap.add_argument("path")
    ap.add_argument("--skip-hash", action="store_true")
    args = ap.parse_args()
    E, W, I = validate(args.path, skip_hash=args.skip_hash)
    for m in E:
        print(f"ERROR: {m}")
    for m in W:
        print(f"WARN:  {m}")
    for m in I:
        print(f"INFO:  {m}")
    if E:
        print("결과: FAIL — 이 레지스트리는 finding 억제에 사용할 수 없다(fail-closed)")
        sys.exit(1)
    if args.skip_hash:
        print("결과: STRUCT-PASS — 구조 통과이나 신선도 무보증: 억제 부적격(exit 3)")
        sys.exit(3)
    print("결과: PASS — 억제 적격")
    sys.exit(0)


if __name__ == "__main__":
    main()
