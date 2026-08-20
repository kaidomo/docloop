#!/usr/bin/env python3
"""review-gate 문서모델 승인 레지스트리(docmodel-approvals.yaml) 검증기 — fail-closed.

closes docauth#242: `approved_docmodel` authority의 승인 여부를 그 파일 **자신의**
메타데이터(`meta.approval_state` 등)가 아니라 **독립 레지스트리 항목**에 결속한다 —
`decision_registry`가 이미 위조되지 않는 것으로 확인된 것과 같은 모델(CONTRACT §2).
파일은 더 이상 "나는 승인됐다"고 스스로 선언할 수 없다: 미결 원장을 복사해 승인
메타데이터만 붙여도 이 레지스트리에 독립 항목이 없으면 authority로 성립하지 않는다.

검사: 스키마(필수 필드·타입·형식·enum·id 유일성·YAML 중복 키) + 각 항목이 가리키는
docmodel의 **현재 바이트**가 승인 시점에 기록한 `docmodel_sha256`과 일치하는지(신선도 —
승인 후 문서가 바뀌면 그 승인은 더 이상 그 문서를 승인한 것이 아니다. fail-closed).

`docmodel_path`는 **packet-root 상대 경로**다(decisions.yaml의 `source_ref`가 그 파일
자신의 디렉터리 기준인 것과 다른 관례 — 이 스키마의 다른 모든 경로(authority_ref.path,
question.source.path 등)가 이미 packet-root 상대이므로 그것과 맞춘다. upstream docauth는
git repo-root 기준이지만, docloop은 git repo를 요구하지 않으므로 packet-root로 대체한다 —
audit_anchors.py/validate_review_intermediate.py와 동일한 로컬 관례).

사용: python3 validate_docmodel_approvals.py <docmodel-approvals.yaml> --packet-root <경로>
"""
from __future__ import annotations

import argparse
import hashlib
import re
import sys
from pathlib import Path
from typing import Any

import yaml

REQUIRED_META = ("target", "updated_at")
REQUIRED_APPROVAL = (
    "id", "docmodel_path", "docmodel_sha256", "status", "approved_by", "approved_at", "evidence",
)
OPTIONAL_APPROVAL = ("template",)
STATUS_ENUM = {"approved", "revoked"}
RE_DATE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
RE_SHA256 = re.compile(r"^[0-9a-f]{64}$")


class DupKeyError(Exception):
    pass


class _StrictLoader(yaml.SafeLoader):
    """YAML 중복 키를 오류로 처리."""


def _strict_map(loader, node, deep=False):
    seen = set()
    for k_node, _ in node.value:
        k = loader.construct_object(k_node, deep=deep)
        if k in seen:
            raise DupKeyError(f"YAML 중복 키: {k!r} (line {k_node.start_mark.line + 1})")
        seen.add(k)
    return yaml.SafeLoader.construct_mapping(loader, node, deep)


_StrictLoader.add_constructor(yaml.resolver.BaseResolver.DEFAULT_MAPPING_TAG, _strict_map)


def _is_str(v: Any) -> bool:
    return isinstance(v, str) and v.strip() != ""


def load(path: Path, content: bytes | None = None) -> Any:
    if content is not None:
        return yaml.load(content, Loader=_StrictLoader)
    with open(path, encoding="utf-8") as f:
        return yaml.load(f, Loader=_StrictLoader)


def validate(
    path: Path,
    *,
    packet_root: Path | None = None,
    content: bytes | None = None,
) -> list[str]:
    """레지스트리 구조 + (packet_root가 있으면) 각 항목의 docmodel 신선도를 검사한다.

    `packet_root`를 생략하면 파일시스템 접근 없이 구조·형식만 검사한다. 실제로 이
    레지스트리를 억제(suppression)/질문 해소 authority로 쓰려면 `_validate_authority_ref`가
    항상 packet_root를 넘겨 신선도까지 검사한다 — 구조만 통과하고 신선도 미검증인 레지스트리는
    fail-closed로 authority가 될 수 없다(호출부가 강제).
    """
    E: list[str] = []
    try:
        data = load(path, content=content)
    except FileNotFoundError:
        return [f"파일 없음: {path}"]
    except DupKeyError as exc:
        return [str(exc)]
    except yaml.YAMLError as exc:
        return [f"YAML 파싱 실패: {exc}"]
    except TypeError as exc:
        # A mapping/sequence used as a YAML key is unhashable and `_strict_map`'s
        # duplicate-key check raises before PyYAML's own error path runs. Fail
        # closed with a clean error instead of an uncaught traceback escaping
        # through `_validate_authority_ref` (Codex r1-01).
        return [f"YAML 파싱 실패(해시 불가능한 키): {exc}"]

    if not isinstance(data, dict):
        return ["최상위가 매핑이 아님"]

    meta = data.get("meta")
    if not isinstance(meta, dict):
        E.append("meta 블록 누락")
        meta = {}
    for key in REQUIRED_META:
        if not _is_str(meta.get(key)):
            E.append(f"meta.{key}: 비어있지 않은 문자열이어야 함 (현재 {type(meta.get(key)).__name__})")
    if _is_str(meta.get("updated_at")) and not RE_DATE.fullmatch(meta["updated_at"]):
        E.append("meta.updated_at: YYYY-MM-DD 형식이 아님")

    approvals = data.get("approvals")
    if not isinstance(approvals, list) or not approvals:
        E.append("approvals가 비어있거나 리스트가 아님")
        approvals = []

    ids: dict[str, int] = {}
    for index, entry in enumerate(approvals):
        tag = f"approvals[{index}]"
        if not isinstance(entry, dict):
            E.append(f"{tag}: 매핑이 아님")
            continue
        entry_id = entry.get("id")
        if _is_str(entry_id):
            tag = f"approval '{entry_id}'"
            ids[entry_id] = ids.get(entry_id, 0) + 1
        unknown = set(entry) - set(REQUIRED_APPROVAL) - set(OPTIONAL_APPROVAL)
        if unknown:
            # Codex r2-01: YAML mapping keys need not be strings (e.g. `42: value`),
            # and `', '.join(...)` requires str items regardless of whether sorted()
            # would also choke on mixed types -- stringify before formatting so a
            # non-string unknown key becomes a clean error instead of a crash.
            E.append(f"{tag}: 알 수 없는 필드: {', '.join(sorted(str(key) for key in unknown))}")
        for key in REQUIRED_APPROVAL:
            value = entry.get(key)
            if not _is_str(value):
                E.append(f"{tag}: {key}는 비어있지 않은 문자열이어야 함 (현재 {type(value).__name__})")
        # Codex r2-02/r3-02: `template` is documented as an optional string
        # (docmodel-approvals-schema.yaml) but was previously accepted as any type,
        # including an explicit `null` (`entry.get("template")` cannot tell "key
        # present with null value" from "key absent" -- checking key membership can).
        if "template" in entry and not _is_str(entry.get("template")):
            E.append(f"{tag}: template은 문자열이어야 함 (현재 {type(entry.get('template')).__name__})")
        status = entry.get("status")
        if _is_str(status) and status not in STATUS_ENUM:
            E.append(f"{tag}: status '{status}' 무효(허용 {sorted(STATUS_ENUM)})")
        docmodel_sha256 = entry.get("docmodel_sha256")
        if _is_str(docmodel_sha256) and not RE_SHA256.fullmatch(docmodel_sha256):
            E.append(f"{tag}: docmodel_sha256: sha256 hex(64자 소문자) 형식이 아님")
        approved_at = entry.get("approved_at")
        if _is_str(approved_at) and not RE_DATE.fullmatch(approved_at):
            E.append(f"{tag}: approved_at는 YYYY-MM-DD 형식이어야 함")

        raw_docmodel_path = entry.get("docmodel_path")
        if (
            packet_root is not None
            and _is_str(raw_docmodel_path)
            and _is_str(docmodel_sha256)
            and RE_SHA256.fullmatch(docmodel_sha256)
        ):
            if Path(raw_docmodel_path).is_absolute() or ".." in Path(raw_docmodel_path).parts:
                E.append(f"{tag}: docmodel_path must be a safe packet-relative path")
                continue
            # Resolve the root itself before comparing -- on macOS, TemporaryDirectory()
            # and other /tmp,/var paths are symlinks into /private, so an unresolved
            # `packet_root` compares unequal to the (always-resolved) `docmodel_path`
            # below even when they name the same real location.
            resolved_packet_root = packet_root.resolve()
            raw_docmodel_target = resolved_packet_root / raw_docmodel_path
            # Codex r3-01: a symlink can have a non-draft-looking declared name while
            # its target is `something.draft.yaml` -- `_validate_authority_ref`'s
            # `.draft.` guard only ever inspects this lexical `docmodel_path` name, so
            # a symlink would let a draft docmodel through under a clean-looking alias.
            # Match the existing r2-04 precedent (validate_input_gate.py's source-copy
            # check): a hash-bound path here must be a real, private regular file, not
            # a pointer that can be repointed or aliased elsewhere.
            if raw_docmodel_target.is_symlink():
                E.append(f"{tag}: docmodel_path must be a regular file, not a symlink")
                continue
            docmodel_path = raw_docmodel_target.resolve()
            try:
                docmodel_path.relative_to(resolved_packet_root)
                stat = docmodel_path.stat()
            except (ValueError, OSError) as exc:
                E.append(f"{tag}: docmodel_path 로드 실패: {exc}")
                continue
            # Codex r5-01: stat() alone doesn't require a REGULAR file -- a FIFO can
            # pass the symlink/hardlink checks below and then block read_bytes()
            # indefinitely. Match validate_input_gate.py's existing is_file() check
            # (which sits in the same position, right after stat()).
            if not docmodel_path.is_file():
                E.append(f"{tag}: docmodel_path must be a regular file")
                continue
            # Codex r4-01: `is_symlink()` alone misses a HARD link -- a second directory
            # entry for the same inode, indistinguishable from "a real file" by that
            # check, that can carry a clean, non-draft-looking name while its bytes (and
            # any later edits through the OTHER name) are the draft's. Same r2-04
            # precedent as the symlink guard above: st_nlink > 1 means this path is not
            # a private, dedicated file.
            if stat.st_nlink > 1:
                E.append(
                    f"{tag}: docmodel_path must not be a hard link (st_nlink={stat.st_nlink}); "
                    "it must be a private file nothing else can edit"
                )
                continue
            try:
                current_bytes = docmodel_path.read_bytes()
            except OSError as exc:
                E.append(f"{tag}: docmodel_path 로드 실패: {exc}")
                continue
            current_hash = hashlib.sha256(current_bytes).hexdigest()
            if current_hash != docmodel_sha256:
                E.append(
                    f"{tag}: STALE — docmodel_sha256 불일치, 승인 후 문서가 변경됨 "
                    f"(기록 {docmodel_sha256[:12]}… vs 현재 {current_hash[:12]}…). 재승인 필요 — 억제 금지"
                )

    for entry_id, count in ids.items():
        if count > 1:
            E.append(f"id 중복: '{entry_id}' ×{count}")

    return E


def main() -> None:
    parser = argparse.ArgumentParser(description="review-gate docmodel-approvals.yaml 검증(fail-closed)")
    parser.add_argument("path", type=Path)
    parser.add_argument("--packet-root", type=Path, default=None)
    args = parser.parse_args()
    packet_root = args.packet_root.resolve() if args.packet_root is not None else None
    errors = validate(args.path, packet_root=packet_root)
    for message in errors:
        print(f"ERROR: {message}")
    if errors:
        print("결과: FAIL — 이 레지스트리는 docmodel 승인 근거로 사용할 수 없다(fail-closed)")
        sys.exit(1)
    print("결과: PASS")
    sys.exit(0)


if __name__ == "__main__":
    main()
