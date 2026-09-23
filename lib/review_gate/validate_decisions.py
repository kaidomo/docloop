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
import pathlib

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


#: docauth#356: 공유 레지스트리(meta.includes)·등록 정책·signoff 매핑.
INCLUDE_DEPTH_MAX = 3
REGISTRATION_POLICIES = {"human_signoff_required"}
SIGNOFF_FIELDS = ("kind", "ref", "sha256", "chunk_id", "issue_index")
SIGNOFF_KINDS = {"asistobe_manifest_snapshot"}
SUPPRESSIBLE_STATUS = {"확정", "재론금지"}  # 현행 소비자(validate_review_intermediate)의 구분 그대로


def _load_data(path, content):
    """(data, error) — YAML 로드(중복 키 거부)."""
    try:
        if content is None:
            with open(path, encoding="utf-8") as f:
                return yaml.load(f, Loader=_StrictLoader), None
        return yaml.load(content, Loader=_StrictLoader), None
    except FileNotFoundError:
        return None, f"파일 없음: {path}"
    except DupKeyError as e:
        return None, str(e)
    except yaml.YAMLError as e:
        return None, f"YAML 파싱 실패: {e}"


def _file_checks(path, data, skip_hash, E, W, I):
    """파일 단위 검사(docauth#356 §1-1) — 형식·enum·날짜·id 중복·신선도·includes/정책/signoff 형식.

    참조 실재성·순환·대체 인정·슬롯 배타·#217은 여기서 하지 않는다(`_graph_checks`, 합집합 단위).
    반환 (meta, decisions).
    """
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

    # meta 신선도(fail-closed 핵심)
    if _is_str(meta.get("source_ref")) and _is_str(meta.get("source_version_hash")) and RE_SHA256.fullmatch(meta["source_version_hash"]):
        _check_hash(path, meta["source_ref"], meta["source_version_hash"], "meta", E, skip_hash, W)

    for did, c in ids.items():
        if c > 1:
            E.append(f"id 중복: '{did}' ×{c}")
    # docauth#356: includes / registration_policy / signoff 형식
    inc = meta.get("includes")
    if inc is not None:
        if not isinstance(inc, list) or not inc or not all(_is_str(x) for x in inc):
            E.append("meta.includes: 비어있지 않은 문자열(상대 경로) 목록이어야 함")
        elif len(set(inc)) != len(inc):
            E.append("meta.includes: 중복 경로")
    pol = meta.get("registration_policy")
    if pol is not None and (not _is_str(pol) or pol not in REGISTRATION_POLICIES):
        E.append(f"meta.registration_policy '{pol}' 무효(허용 {sorted(REGISTRATION_POLICIES)})")
    for i, d in enumerate(decisions):
        if not isinstance(d, dict):
            continue
        tag = f"decision '{d['id']}'" if _is_str(d.get("id")) else f"decisions[{i}]"
        so = d.get("signoff")
        if so is None:
            continue
        if not isinstance(so, dict) or tuple(sorted(so)) != tuple(sorted(SIGNOFF_FIELDS)):
            E.append(f"{tag}: signoff는 {{{', '.join(SIGNOFF_FIELDS)}}} 매핑이어야 함")
            continue
        if not _is_str(so.get("kind")) or so["kind"] not in SIGNOFF_KINDS:
            E.append(f"{tag}: signoff.kind '{so.get('kind')}' 미지원(허용 {sorted(SIGNOFF_KINDS)})")
        if not _is_str(so.get("ref")):
            E.append(f"{tag}: signoff.ref는 비어있지 않은 문자열(스냅샷 경로)이어야 함")
        if not _is_str(so.get("sha256")) or not RE_SHA256.fullmatch(so["sha256"]):
            E.append(f"{tag}: signoff.sha256: sha256 hex(64자 소문자) 형식이 아님")
        if not _is_str(so.get("chunk_id")):
            E.append(f"{tag}: signoff.chunk_id는 비어있지 않은 문자열이어야 함")
        ii = so.get("issue_index")
        if type(ii) is not int or ii < 0:
            E.append(f"{tag}: signoff.issue_index는 0 이상의 정수여야 함")
    return meta, decisions


def _graph_checks(decisions, E, W, I, *, file_of=None, includers=None):
    """합집합 단위 검사(docauth#356 §1-1): 참조 실재성·자기참조·순환·대체 인정·슬롯 배타·#217·날짜 역전.

    단일 파일이면 합집합 = 그 파일 하나(회귀 없음). `file_of`(id→파일)·`includers`(파일→그 파일을
    전이적으로 포함하는 파일 집합)가 주어지면 대체 인정에 방향 규칙을 더한다.
    반환 (retired, honored, by_id).
    """
    ids = {}
    for d in decisions:
        if isinstance(d, dict) and _is_str(d.get("id")):
            ids[d["id"]] = ids.get(d["id"], 0) + 1
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
                where = "합집합" if file_of is not None else "본 파일"
                E.append(f"decision '{did}': {ref_key} '{ref}'가 {where}에 없음(dangling)")
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

    def _direction_reject_reason(old, new):
        """docauth#356 §1-1: 합집합에서 인정되는 대체 간선은 (a) 같은 파일 안 (b) 포함하는 쪽(자손) →
        포함되는 쪽(조상) 뿐이다. 조상→자손·형제 간 간선은 WARN이고 그 간선으로는 어떤 항목도
        슬롯에서 빼지 않는다 — 공유 파일이 문서 폴더의 결정을 조용히 퇴역시키지 못한다."""
        if file_of is None:
            return None
        f_old, f_new = file_of.get(old), file_of.get(new)
        if f_old == f_new or f_new in (includers or {}).get(f_old, set()):  # new가 old의 파일을 포함한다
            return None
        return (
            f"방향 비인정 대체: '{new}'({f_new})가 '{old}'({f_old})를 대체 — 대체는 같은 파일 또는 "
            "포함하는 쪽→포함되는 쪽만 인정된다(docauth#356). 대체로 인정하지 않았다(피대체 항목이 슬롯에 남음)"
        )

    retired, honored = set(), {}
    for old in sorted(edges):
        for new in sorted(edges[old]):
            reason = _direction_reject_reason(old, new) or _edge_reject_reason(old, new)
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

    return retired, honored, by_id


def validate(path, skip_hash=False, content=None):
    """(errors, warnings, info) 반환. `meta.includes`가 있으면 합집합 검사(`validate_union`)로 간다."""
    data, err = _load_data(path, content)
    if err is not None:
        return [err], [], []
    if not isinstance(data, dict):
        return ["최상위가 매핑이 아님"], [], []
    if isinstance(data.get("meta"), dict) and data["meta"].get("includes") is not None:
        E, W, I, _union = validate_union(path, skip_hash=skip_hash, content=content)
        return E, W, I
    E, W, I = [], [], []
    _meta, decisions = _file_checks(path, data, skip_hash, E, W, I)
    _graph_checks(decisions, E, W, I)
    return E, W, I


# ── docauth#356: 합집합 · signoff · 고정 합집합 ──────────────────────────────────────

def _norm_abs(path):
    return os.path.normpath(os.path.abspath(os.path.expanduser(str(path))))


def _rel_posix(root_abs, file_abs):
    return pathlib.PurePath(os.path.relpath(file_abs, os.path.dirname(root_abs))).as_posix()


def collect_union_files(root, *, files=None, content=None):
    """루트 + 전이적 includes → ([entry], errors). entry = {abs, rel, bytes, depth, includes: [abs]}.

    - 경로 정규형 = 절대 정규화 경로(`declared_path`). includes는 **선언 파일의 부모** 기준으로 해석한다.
    - `files`(declared_path→bytes 매핑, 고정 합집합)가 주어지면 디스크를 읽지 않는다 — 매핑에 없는
      include는 FAIL("include not archived").
    - 깊이 상한 INCLUDE_DEPTH_MAX, 순환·중복(정규화 후 같은 경로) FAIL.
    """
    root_abs = _norm_abs(root)
    errors = []
    order = []
    seen = {}

    def _read(abs_path, is_root):
        if files is not None:
            if abs_path not in files:
                errors.append(f"include not archived: {abs_path} (고정 합집합 밖의 파일)")
                return None
            return files[abs_path]
        if is_root and content is not None:
            return content.encode("utf-8") if isinstance(content, str) else content
        try:
            with open(abs_path, "rb") as f:
                return f.read()
        except OSError as exc:
            errors.append(f"include 읽기 실패: {abs_path} ({exc})")
            return None

    def _walk(abs_path, depth, stack):
        if abs_path in stack:
            errors.append(f"includes 순환: {' → '.join(stack + [abs_path])}")
            return
        if abs_path in seen:
            errors.append(f"includes 중복: {abs_path} 가 두 경로로 포함됨")
            return
        if depth > INCLUDE_DEPTH_MAX:
            errors.append(f"includes 깊이 초과({INCLUDE_DEPTH_MAX}): {abs_path}")
            return
        raw = _read(abs_path, depth == 0)
        if raw is None:
            return
        entry = {"abs": abs_path, "rel": _rel_posix(root_abs, abs_path), "bytes": raw, "depth": depth, "includes": []}
        seen[abs_path] = entry
        order.append(entry)
        data, err = _load_data(abs_path, raw.decode("utf-8", errors="replace"))
        if err is not None or not isinstance(data, dict):
            return  # 형식 오류는 _file_checks가 보고한다
        inc = data.get("meta", {}).get("includes") if isinstance(data.get("meta"), dict) else None
        if not isinstance(inc, list):
            return
        for ref in inc:
            if not _is_str(ref):
                continue
            child = _norm_abs(os.path.join(os.path.dirname(abs_path), os.path.expanduser(ref)))
            entry["includes"].append(child)
            _walk(child, depth + 1, stack + [abs_path])

    _walk(root_abs, 0, [])
    return order, errors


def verify_signoff(entry, base_dir):
    """docauth#356 §3-4 — 승격 도구와 `validate_union`이 **같이** 부르는 signoff 재검증. errors 반환.

    스냅샷 파일 존재 · sha 일치 · kind 지원 · 청크 approved · issue_index 존재 · recommendation == decision
    원문 · subject 동치(둘 다 존재).
    """
    errors = []
    so = entry.get("signoff") if isinstance(entry, dict) else None
    tag = f"decision '{entry.get('id')}'" if isinstance(entry, dict) and _is_str(entry.get("id")) else "decision"
    if not isinstance(so, dict) or tuple(sorted(so)) != tuple(sorted(SIGNOFF_FIELDS)):
        return [f"{tag}: signoff 매핑 없음/형식 오류"]
    if so.get("kind") not in SIGNOFF_KINDS:
        return [f"{tag}: signoff.kind '{so.get('kind')}' 미지원"]
    ref = so.get("ref")
    if not _is_str(ref):
        return [f"{tag}: signoff.ref 없음"]
    snap_path = _norm_abs(os.path.join(base_dir, os.path.expanduser(ref)))
    if not os.path.isfile(snap_path):
        return [f"{tag}: signoff 스냅샷 없음: {snap_path}"]
    with open(snap_path, "rb") as f:
        raw = f.read()
    if hashlib.sha256(raw).hexdigest() != so.get("sha256"):
        return [f"{tag}: signoff.sha256가 스냅샷 바이트와 불일치(스냅샷은 불변이어야 한다)"]
    snap, err = _load_data(snap_path, raw.decode("utf-8", errors="replace"))
    if err is not None or not isinstance(snap, dict):
        return [f"{tag}: signoff 스냅샷 YAML 오류: {err or '매핑 아님'}"]
    if snap.get("kind") != "asistobe_manifest_snapshot":
        return [f"{tag}: 스냅샷 kind가 asistobe_manifest_snapshot이 아님"]
    chunk = snap.get("chunk")
    if not isinstance(chunk, dict) or chunk.get("id") != so.get("chunk_id"):
        return [f"{tag}: 스냅샷의 chunk.id가 signoff.chunk_id '{so.get('chunk_id')}'와 다름"]
    if chunk.get("status") != "approved":
        return [f"{tag}: 스냅샷 청크 status '{chunk.get('status')}' — approved만 승인 증거"]
    issues = chunk.get("issues")
    idx = so.get("issue_index")
    if not isinstance(issues, list) or type(idx) is not int or not (0 <= idx < len(issues)):
        return [f"{tag}: 스냅샷 청크에 issues[{idx}] 없음"]
    issue = issues[idx]
    if not isinstance(issue, dict):
        return [f"{tag}: 스냅샷 issues[{idx}]가 구조 쟁점(v2)이 아님"]
    if not _is_str(issue.get("recommendation")) or issue["recommendation"] != entry.get("decision"):
        errors.append(f"{tag}: decision 원문이 승인 쟁점 recommendation과 문자열 동일하지 않음")
    # 구현 r1-02: 쟁점이 이미 다른 결정을 가리키면 이 항목은 그 승인의 산물이 아니다 — 승격·소비가 같이 본다(불변식 vi).
    applied = issue.get("decision_applied")
    if applied is not None and applied != entry.get("id"):
        errors.append(f"{tag}: 승인 쟁점의 decision_applied '{applied}'가 이 결정 id와 다름")
    subj_i, subj_d = issue.get("subject"), entry.get("subject")
    if not (_is_str(subj_i) and _is_str(subj_d)):
        errors.append(f"{tag}: subject가 승인 쟁점·결정 양쪽에 있어야 함(d2-02)")
    elif _subject_key(subj_i) != _subject_key(subj_d):
        errors.append(f"{tag}: subject 불일치 — 승인 쟁점 '{subj_i}' vs 결정 '{subj_d}'")
    return errors


def union_sha256_of(entries):
    """파일 목록(rel_path+sha 정렬)의 sha256 — 절대 경로를 넣지 않아 기계 간 이식된다."""
    rows = sorted(f"{e['rel']}\t{hashlib.sha256(e['bytes']).hexdigest()}" for e in entries)
    return hashlib.sha256("\n".join(rows).encode("utf-8")).hexdigest()


def validate_union(root, *, files=None, skip_hash=False, content=None):
    """docauth#356 — 루트+전이적 includes 합집합 검사 → (E, W, I, union).

    union = {root, files: [{declared_path, rel_path, sha256, depth}], decisions: {id: {file, sha256, current,
    suppression_eligible, reason}}, union_sha256, shared: bool, authority_eligible: bool, authority_reason}.
    소비 조건은 `suppression_eligible`이다(현행 ∧ 자기 superseded_by 없음 ∧ 억제 가능 status ∧ (공유면)
    signoff 유효 ∧ 합집합 authority 적격). 파일 하나라도 signoff 검증에 실패하면 합집합 전체가 부적격이다.
    """
    E, W, I = [], [], []
    entries, errs = collect_union_files(root, files=files, content=content)
    E.extend(errs)
    union = {
        "root": _norm_abs(root), "files": [], "decisions": {}, "union_sha256": None,
        "shared": False, "authority_eligible": False, "authority_reason": None,
    }
    if not entries:
        return E, W, I, union
    per_file = []
    for e in entries:
        data, err = _load_data(e["abs"], e["bytes"].decode("utf-8", errors="replace"))
        prefix = "" if e["depth"] == 0 else f"[{e['rel']}] "
        if err is not None:
            E.append(prefix + err)
            continue
        if not isinstance(data, dict):
            E.append(prefix + "최상위가 매핑이 아님")
            continue
        fe, fw, fi = [], [], []
        meta, decisions = _file_checks(e["abs"], data, skip_hash, fe, fw, fi)
        E.extend(prefix + m for m in fe)
        W.extend(prefix + m for m in fw)
        I.extend(prefix + m for m in fi)
        per_file.append((e, meta, decisions))
        union["files"].append({
            "declared_path": e["abs"], "rel_path": e["rel"],
            "sha256": hashlib.sha256(e["bytes"]).hexdigest(), "depth": e["depth"],
        })
    union["union_sha256"] = union_sha256_of(entries)
    shared = len(entries) > 1 or any(
        isinstance(m, dict) and m.get("includes") is not None for _e, m, _d in per_file
    )
    union["shared"] = shared
    # includers: 파일 → 그 파일을 전이적으로 포함하는 파일 집합
    includers = {e["abs"]: set() for e in entries}
    parents = {}
    for e in entries:
        for child in e["includes"]:
            parents.setdefault(child, set()).add(e["abs"])
    for f in includers:
        stack, seen = list(parents.get(f, ())), set()
        while stack:
            q = stack.pop()
            if q in seen:
                continue
            seen.add(q)
            stack.extend(parents.get(q, ()))
        includers[f] = seen
    merged, file_of = [], {}
    for e, _meta, decisions in per_file:
        for d in decisions:
            if isinstance(d, dict) and _is_str(d.get("id")):
                if d["id"] in file_of and file_of[d["id"]] != e["abs"]:
                    E.append(f"id 충돌: '{d['id']}' 가 {file_of[d['id']]} 와 {e['abs']} 양쪽에 있음")
                    continue
                file_of[d["id"]] = e["abs"]
            merged.append(d)
    retired, honored, by_id = _graph_checks(merged, E, W, I, file_of=file_of, includers=includers)
    # authority 적격(§3-1): 공유면 전 파일 등록 정책 + 전 항목 signoff(verify_signoff PASS — 비현행 포함, 불변식 iii)
    policy_declared = any(isinstance(m, dict) and m.get("registration_policy") for _e, m, _d in per_file)
    signoff_required = shared or policy_declared
    reason = None
    signoff_errors = {}
    if signoff_required:
        for e, meta, decisions in per_file:
            if not isinstance(meta, dict) or meta.get("registration_policy") != "human_signoff_required":
                reason = reason or f"공유 레지스트리의 모든 파일에 meta.registration_policy: human_signoff_required 필요 — 없음: {e['rel']}"
            for d in decisions:
                if not isinstance(d, dict) or not _is_str(d.get("id")):
                    continue
                errs = verify_signoff(d, os.path.dirname(e["abs"]))
                if errs:
                    signoff_errors[d["id"]] = errs[0]
                    reason = reason or f"signoff 검증 실패: {errs[0]}"
    if reason:
        I.append(f"authority_ineligible({reason})")
    union["authority_eligible"] = not reason
    union["authority_reason"] = reason
    for d in merged:
        if not isinstance(d, dict) or not _is_str(d.get("id")):
            continue
        did = d["id"]
        current = did not in retired
        why = None
        if not current:
            why = "인정된 대체로 퇴역"
        elif _is_str(d.get("superseded_by")):
            why = "자기 선언 superseded_by"
        elif d.get("status") not in SUPPRESSIBLE_STATUS:
            why = f"status '{d.get('status')}'는 억제 근거가 아님"
        elif did in signoff_errors:
            why = signoff_errors[did]
        elif reason:
            why = f"합집합 authority 부적격: {reason}"
        f_abs = file_of.get(did)
        union["decisions"][did] = {
            "file": f_abs,
            "sha256": next((f["sha256"] for f in union["files"] if f["declared_path"] == f_abs), None),
            "current": current,
            "suppression_eligible": why is None and not E,
            "reason": why if why else ("합집합 FAIL" if E else None),
        }
    return E, W, I, union


def load_fixed_union(state_json_path, *, skip_hash=False):
    """docauth#356 §1-3 — front gate 스냅샷(`front_gate_decisions_state.json`)에서 고정 합집합을 만든다.
    → (union|None, errors). 디스크의 현재 레지스트리는 읽지 않는다(아카이브 바이트로 `validate_union`).

    state = {state, path, files: [{declared_path, archive, sha256}], union_sha256}. 구형(0.32) state
    (`files` 없음)는 단일 아카이브 `front_gate_decisions.yaml`로 재생하고 includes는 거부한다.
    state가 checked가 아니면 union은 빈 합집합(파일·결정 없음 — authority 없음)이다.
    """
    import json

    state_path = _norm_abs(state_json_path)
    base = os.path.dirname(state_path)
    try:
        with open(state_path, encoding="utf-8") as f:
            state = json.load(f)
    except (OSError, ValueError) as exc:
        return None, [f"decision registry state 읽기 실패: {exc}"]
    if not isinstance(state, dict) or state.get("state") not in {"checked", "absent", "unchecked"}:
        return None, ["decision registry state 형식 오류"]
    empty = {"root": None, "files": [], "decisions": {}, "union_sha256": None, "shared": False,
             "authority_eligible": False, "authority_reason": f"registry state {state.get('state')}"}
    if state["state"] != "checked":
        return empty, []
    root = state.get("path")
    if not _is_str(root):
        return None, ["checked state에 path 없음"]
    rows = state.get("files")
    mapping = {}
    if "files" not in state:  # 구형 단일 아카이브(키 자체가 없음 — 명시적 null·빈 목록은 형식 오류)
        archive = os.path.join(base, "front_gate_decisions.yaml")
        try:
            with open(archive, "rb") as f:
                mapping[_norm_abs(root)] = f.read()
        except OSError as exc:
            return None, [f"레지스트리 아카이브 읽기 실패: {exc}"]
        data, err = _load_data(archive, mapping[_norm_abs(root)].decode("utf-8", errors="replace"))
        if isinstance(data, dict) and isinstance(data.get("meta"), dict) and data["meta"].get("includes") is not None:
            return None, ["구형 단일 아카이브 state는 includes를 가진 레지스트리를 재생할 수 없다(공유 = 스냅샷+서명 필수)"]
    else:
        if not isinstance(rows, list) or not rows:
            return None, ["state.files 형식 오류"]
        for row in rows:
            if not isinstance(row, dict) or not _is_str(row.get("declared_path")) or not _is_str(row.get("archive")):
                return None, ["state.files 항목 형식 오류"]
            key = _norm_abs(row["declared_path"])
            if key in mapping:
                return None, [f"state.files 중복 declared_path: {key}"]
            archive = _norm_abs(os.path.join(base, row["archive"]))
            if os.path.dirname(archive) != _norm_abs(base) and not archive.startswith(_norm_abs(base) + os.sep):
                return None, [f"archive 경로가 run_root 밖: {row['archive']}"]
            try:
                with open(archive, "rb") as f:
                    raw = f.read()
            except OSError as exc:
                return None, [f"레지스트리 아카이브 읽기 실패: {exc}"]
            if hashlib.sha256(raw).hexdigest() != row.get("sha256"):
                return None, [f"아카이브 sha256 불일치: {row['archive']} (state 기록과 바이트가 다름)"]
            mapping[key] = raw
        if _norm_abs(root) not in mapping:
            return None, ["state.files에 루트 레지스트리가 없음"]
    E, W, I, union = validate_union(root, files=mapping, skip_hash=skip_hash)
    if rows is not None and state.get("union_sha256") != union["union_sha256"]:
        E.append("state.union_sha256가 아카이브에서 재계산한 값과 다름")
    if len(union["files"]) != len(mapping):
        E.append("아카이브된 파일 중 합집합에 도달하지 않은 파일이 있음(state.files ≠ 전이적 includes)")
    union["errors"] = E
    union["warnings"] = W
    union["info"] = I
    return union, E


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
