#!/usr/bin/env python3
"""review-gate 결정 레지스트리(decisions.yaml) 검증기 — fail-closed.

통과해야만 레지스트리를 finding 억제(suppression)에 쓸 수 있다(CONTRACT §2).
검사: 스키마(필수 필드·타입·형식·enum·id 유일성·YAML 중복 키) + supersede 참조
무결성·비순환 + 원 결정문서 content hash 일치(신선도, meta·항목 레벨).

종료 코드 (r1-01: 구조 통과와 억제 적격을 기계적으로 구분):
  0 = 완전 통과 — 억제 적격(suppression eligible)
  1 = 검증 실패 — 억제 사용 금지(fail-closed)
  2 = 환경 오류(PyYAML 부재 등)
  3 = 구조 통과이나 hash 미검증(--skip-hash) — 억제 부적격

사용: python3 validate_decisions.py <decisions.yaml> [--skip-hash]
"""
import argparse
import hashlib
import os
import re
import sys

try:
    import yaml
except ImportError:  # PyYAML은 하우스 표준 의존성
    print("PyYAML 필요: pip install pyyaml", file=sys.stderr)
    sys.exit(2)

REQUIRED_META = ("target", "source_ref", "source_version_hash", "updated_at")
REQUIRED_DECISION = ("id", "decision", "status", "date", "evidence")
OPTIONAL_STR_DECISION = ("scope", "supersedes", "superseded_by", "source_ref", "source_hash")
STATUS_ENUM = {"확정", "기각", "재론금지"}
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


def validate(path, skip_hash=False):
    """(errors, warnings, info) 반환."""
    E, W, I = [], [], []
    try:
        with open(path, encoding="utf-8") as f:
            data = yaml.load(f, Loader=_StrictLoader)
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
            E.append(f"meta.{k}: 비어있지 않은 문자열이어야 함 (현재 {type(meta[k]).__name__})")
    if _is_str(meta.get("source_version_hash")) and not RE_SHA256.match(meta["source_version_hash"]):
        E.append("meta.source_version_hash: sha256 hex(64자 소문자) 형식이 아님")
    if _is_str(meta.get("updated_at")) and not RE_DATE.match(meta["updated_at"]):
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
                E.append(f"{tag}: {k}는 비어있지 않은 문자열이어야 함 (현재 {type(v).__name__})")
        for k in OPTIONAL_STR_DECISION:
            v = d.get(k)
            if v is not None and not _is_str(v):
                E.append(f"{tag}: {k}는 문자열이어야 함 (현재 {type(v).__name__})")
        st = d.get("status")
        if _is_str(st) and st not in STATUS_ENUM:
            E.append(f"{tag}: status '{st}' 무효(허용 {sorted(STATUS_ENUM)})")
        dt = d.get("date")
        if _is_str(dt) and not RE_DATE.match(dt):
            E.append(f"{tag}: date는 YYYY-MM-DD 형식이어야 함")
        sh = d.get("source_hash")
        if _is_str(sh) and not RE_SHA256.match(sh):
            E.append(f"{tag}: source_hash: sha256 hex(64자 소문자) 형식이 아님")
        # 항목 레벨 provenance (r1-10): source_ref가 있으면 source_hash 필수 + 신선도 검사
        if _is_str(d.get("source_ref")):
            if not _is_str(sh):
                E.append(f"{tag}: source_ref가 있으면 source_hash 필수(외부 provenance 해시 보호)")
            elif RE_SHA256.match(sh):
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
        if _is_str(d.get("superseded_by")):
            I.append(f"decision '{did}': superseded_by 존재 — 억제 근거 사용 금지(이력 보존용)")

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

    # meta 신선도(fail-closed 핵심)
    if _is_str(meta.get("source_ref")) and _is_str(meta.get("source_version_hash")) and RE_SHA256.match(meta["source_version_hash"]):
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
