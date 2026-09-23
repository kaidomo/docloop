#!/usr/bin/env python3
"""Import a validated packet-bound review receipt into an as-is/to-be manifest.

Usage: docloop atb-import-review PACKET RECEIPT_REL --manifest FILE
       (--live FILE | --no-live) [--contract FILE] [--report FILE] [--dry-run]

Prepared integrity and final result validation precede any writes. Only done or
complete-indeterminate receipts qualify; malformed/thin packets are rejected.
Verified findings become observations; unavailable judgments become pending issues.
Identity is the canonical review folder plus target (stable across run snapshots).
Human changes persist; imported safety flags can only downgrade overridden records.
Exit 0: success; 1: invalid input/IO before commit; 2: usage or identity collision;
6: manifest committed but report failed. Dry-run writes neither output.
"""
from __future__ import annotations

import argparse
import copy
import difflib
import hashlib
import os
import re
import sys
import tempfile
import stat
from datetime import datetime, timezone, timedelta
from pathlib import Path

import yaml

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from validate_manifest import validate as validate_manifest  # noqa: E402

KST = timezone(timedelta(hours=9))
DEFAULT_CONTRACT = Path(__file__).resolve().parent.parent / "templates" / "review-import.yaml"
SHA256_SNAPSHOT = re.compile(r"^sha256:([0-9a-f]{64})$")
LINE_ANCHOR = re.compile(r"^L(\d+)$")
#: review-gate `audit_quotes`는 범위 앵커(`L10-L12`·`L10~L12`·`L10–L12`)도 받는다(구현 r1-01).
LINE_RANGE_ANCHOR = re.compile(r"^L(\d+)\s*[~\-–]\s*L?(\d+)$")
#: review-gate `audit_quotes.anchor_hash`와 같은 규약(#207 2안): "A" + sha256(정규화 행)[:12].
#: 스킬 간 import를 하지 않으므로(각 스킬은 자족) 여기 최소 복제한다 — 규약이 바뀌면 함께 바꾼다.
HASH_ANCHOR = re.compile(r"^A([0-9a-f]{12})$")
WS = re.compile(r"\s+")
EXCERPT_MAX = 60
#: 어댑터가 소유하는 관찰 필드. 내용 필드는 사람이 고치면 그 값이 이기고(구현 r1-02 — 직전 import
#: 값과 다르면 사람 수정으로 보고 보존), 안전 플래그(verified·needs_revalidation·thin_source)는
#: **강등만** 어댑터가 한다(사람이 내린 verified=false를 어댑터가 올리지 않는다).
CONTENT_FIELDS = ("what", "sources", "kind", "kind_confidence", "intent_gap")
SAFETY_FIELDS = ("verified", "needs_revalidation", "thin_source")
ADAPTER_FIELDS = CONTENT_FIELDS + SAFETY_FIELDS + ("review_ref",)
PENDING_FIELDS = ("kind", "text", "reason", "needed_input", "review_ref")


class UsageError(Exception):
    pass


class InputError(Exception):
    pass


def _norm(text: str) -> str:
    return WS.sub(" ", text).strip()


def _anchor_hash(line: str) -> str:
    return "A" + hashlib.sha256(_norm(line).encode("utf-8")).hexdigest()[:12]


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _frontmatter(path: Path) -> dict:
    text = path.read_text(encoding="utf-8")
    if not text.startswith("---\n"):
        raise InputError(f"{path}: receipt에 YAML frontmatter가 없다")
    end = text.find("\n---", 4)
    if end < 0:
        raise InputError(f"{path}: frontmatter가 닫히지 않았다")
    data = yaml.safe_load(text[4:end])
    if not isinstance(data, dict) or set(data) != {"doc_review_result"}:
        raise InputError(f"{path}: frontmatter 최상위는 doc_review_result 하나여야 한다")
    receipt = data["doc_review_result"]
    if not isinstance(receipt, dict):
        raise InputError(f"{path}: doc_review_result가 매핑이 아니다")
    if receipt.get("schema_version") != 2:
        raise InputError(
            f"{path}: schema_version 2 receipt만 옮긴다 (got {receipt.get('schema_version')!r}) — "
            "v1은 §1 입력 게이트 이전 형식이라 source_copy·run_root가 없다"
        )
    for key in ("findings", "questions", "drifts"):
        if not isinstance(receipt.get(key), list):
            raise InputError(f"{path}: {key}가 리스트가 아니다")
    if not isinstance(receipt.get("snapshot_id"), str) or not receipt["snapshot_id"]:
        raise InputError(f"{path}: snapshot_id가 없다")
    return receipt


def _candidates(bases: list, raw) -> list:
    from review_gate.validate_review_intermediate import resolve_packet_file
    if not bases or bases[0] is None:
        return []
    errors = []
    path = resolve_packet_file(bases[0], raw, "import reference", errors)
    if path is None:
        raise InputError("; ".join(errors))
    return [path]


def load_contract(path: Path) -> dict:
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict) or data.get("schema_version") != 1:
        raise InputError(f"{path}: review_import 계약 파일 형식 오류(schema_version 1 매핑이어야 한다)")
    for key in ("kind_by_severity", "kind_confidence_by_counter_citation_verdict"):
        if not isinstance(data.get(key), dict):
            raise InputError(f"{path}: {key} 매핑이 없다")
    data.setdefault("observation_id_prefix", "RG-")
    data.setdefault("pending_issue_id_prefix", "RGQ-")
    for key in ("observation_id_prefix", "pending_issue_id_prefix"):
        if not isinstance(data[key], str) or not data[key].strip():
            raise InputError(f"{key} must be a nonempty string")
    return data


def load_ledger(receipt: dict, receipt_path: Path, repo_root: Path | None) -> tuple[dict | None, str | None]:
    """원장을 읽어 (body, 문제) 를 돌려준다. 문제가 있으면 body는 None — 그 receipt는 '얇다'."""
    ref = receipt.get("classification_ledger_ref")
    if not isinstance(ref, dict):
        return None, "classification_ledger_ref 없음"
    raw = None
    seen = False
    for path in _candidates([repo_root, receipt_path.parent], ref.get("path")):
        if not path.is_file():
            continue
        seen = True
        from review_gate.validate_review_result import _read_packet_file_bytes
        data_bytes, error = _read_packet_file_bytes(path, "import ledger")
        if error or data_bytes is None:
            raise InputError(error or "cannot read import ledger")
        if ref.get("sha256") == _sha256(data_bytes):
            raw = data_bytes
            break
    if raw is None:
        return None, (f"원장 해시 불일치: {ref.get('path')!r}" if seen else f"원장 파일 없음: {ref.get('path')!r}")
    try:
        data = yaml.safe_load(raw.decode("utf-8"))
    except (UnicodeDecodeError, yaml.YAMLError) as exc:
        return None, f"원장 파싱 실패: {exc}"
    body = data.get("review_intermediate") if isinstance(data, dict) else None
    if not isinstance(body, dict):
        return None, "원장 최상위 키가 review_intermediate가 아님"
    return body, None


def _atom_statements(ledger: dict | None) -> dict[str, str]:
    out: dict[str, str] = {}
    for atom in (ledger or {}).get("candidate_atoms", []) or []:
        if isinstance(atom, dict) and isinstance(atom.get("candidate_atom_id"), str):
            out[atom["candidate_atom_id"]] = str(atom.get("statement") or "")
    return out


def load_source_copy(receipt: dict, repo_root: Path, receipt_path: Path):
    from review_gate.validate_review_result import _read_packet_file_bytes
    ref = receipt["input_gate"]["source_copy"]
    path = _candidates([repo_root], ref["path"])[0]
    raw, error = _read_packet_file_bytes(path, "source copy")
    if error or raw is None or _sha256(raw) != ref["sha256"]:
        raise InputError(error or "source copy hash mismatch")
    return raw.decode("utf-8").splitlines(), None


def changed_lines(old: list[str], new: list[str]) -> tuple[set[int], set[str]]:
    """(변경·삭제된 옛 행 번호 집합(1-based), 라이브 정규화 행 집합)."""
    changed: set[int] = set()
    matcher = difflib.SequenceMatcher(a=old, b=new, autojunk=False)
    for tag, i1, i2, _, _ in matcher.get_opcodes():
        if tag != "equal":
            changed.update(range(i1 + 1, i2 + 1))
    return changed, {_norm(line) for line in new}


def _excerpt(line: str) -> str:
    text = _norm(line)
    return text if len(text) <= EXCERPT_MAX else text[:EXCERPT_MAX].rstrip() + "…"


def _anchor_line(anchor: str, source_lines: list[str] | None) -> tuple[int | None, str | None]:
    if source_lines is None:
        return None, None
    m = LINE_ANCHOR.match(anchor)
    if m:
        n = int(m.group(1))
        if 1 <= n <= len(source_lines):
            return n, source_lines[n - 1]
        return n, None
    m = HASH_ANCHOR.match(anchor)
    if m:
        for index, line in enumerate(source_lines, start=1):
            if _anchor_hash(line) == anchor:
                return index, line
    return None, None


def _anchor_stale(anchor: str, index, line, freshness: dict) -> bool:
    """라이브 판본에서 이 앵커가 가리키던 행이 바뀌었는가. **해석하지 못하는 앵커는 재판정**(구현 r1-01 —
    모르는 형식을 '안 바뀜'으로 세면 verified가 새어 나간다)."""
    m = LINE_RANGE_ANCHOR.match(anchor)
    if m:
        lo, hi = sorted((int(m.group(1)), int(m.group(2))))
        return any(n in freshness["changed"] for n in range(lo, hi + 1))
    if LINE_ANCHOR.match(anchor):
        return index is None or index in freshness["changed"]
    if HASH_ANCHOR.match(anchor):
        return line is None or _norm(line) not in freshness["live_norm"]
    return True


def build_observations(
    receipt: dict,
    receipt_path: Path,
    receipt_sha256: str,
    ledger: dict | None,
    thin_reason: str | None,
    contract: dict,
    source_lines: list[str] | None,
    freshness: dict,
) -> tuple[list[dict], list[dict], list[dict], int]:
    """(observations, pending_issues, excluded, drift_count)."""
    statements = _atom_statements(ledger)
    ledger_records = {
        r.get("record_id"): r for r in (ledger or {}).get("findings", []) or [] if isinstance(r, dict)
    }
    target = receipt.get("target") or "<target>"
    observations: list[dict] = []
    pending: list[dict] = []
    excluded: list[dict] = []
    prefix = contract["observation_id_prefix"]
    qprefix = contract["pending_issue_id_prefix"]
    review_ref_base = {
        "receipt": str(receipt_path),
        "sha256": receipt_sha256,
        "snapshot_id": receipt["snapshot_id"],
        "target": target,
        "source_identity": receipt.get("_source_identity"),
    }
    for finding in receipt["findings"]:
        if not isinstance(finding, dict):
            excluded.append({"id": "?", "why": "finding이 매핑이 아님"})
            continue
        fid = finding.get("finding_id") or finding.get("record_id") or "?"
        status = finding.get("status")
        if status == "judgment_unavailable":
            block = finding.get("judgment_unavailable") if isinstance(finding.get("judgment_unavailable"), dict) else {}
            pending.append({
                "id": f"{qprefix}{fid}",
                "kind": "judgment_unavailable",
                "text": str(finding.get("judgment_provenance") or ""),
                "reason": block.get("reason"),
                "needed_input": block.get("needed_input"),
                "review_ref": {**review_ref_base, "record_id": finding.get("record_id"), "finding_id": fid},
            })
            continue
        if status != "verified":
            excluded.append({"id": fid, "why": f"status={status!r} (verified만 옮긴다)"})
            continue
        anchors = [a for a in (finding.get("evidence_anchors") or []) if isinstance(a, str)]
        sources: list[str] = []
        stale = False
        for anchor in anchors:
            index, line = _anchor_line(anchor, source_lines)
            excerpt = f' "{_excerpt(line)}"' if line is not None else ""
            sources.append(f"{target}:{anchor}{excerpt}")
            if freshness["mode"] == "live" and freshness["comparable"]:
                stale = stale or _anchor_stale(anchor, index, line, freshness)
        thin = ledger is None or finding.get("record_id") not in ledger_records
        what = ""
        if not thin:
            refs = ledger_records[finding["record_id"]].get("candidate_atom_refs") or []
            what = " / ".join(statements.get(ref, "") for ref in refs if statements.get(ref))
        if not what:
            what = str(finding.get("judgment_provenance") or "")
        severity = finding.get("severity")
        kind = contract["kind_by_severity"].get(severity)
        confidence = contract["kind_confidence_by_counter_citation_verdict"].get(
            finding.get("counter_citation_verdict", "none"), "high"
        )
        needs_revalidation = (
            freshness["mode"] == "no-live"
            or (freshness["mode"] == "live" and (not freshness["comparable"] or stale))
        )
        verified = not needs_revalidation and not thin
        observation = {
            "id": f"{prefix}{fid}",
            "what": what,
            "sources": sources,
            "kind": kind if kind else "new",
            "kind_confidence": confidence,
            "intent_gap": str(finding.get("judgment_provenance") or ""),
            "verified": verified,
            "needs_revalidation": needs_revalidation,
            "thin_source": thin,
            "review_ref": {
                **review_ref_base,
                "record_id": finding.get("record_id"),
                "finding_id": fid,
                "severity": severity,
            },
        }
        if thin and thin_reason:
            observation["review_ref"]["thin_reason"] = thin_reason
        elif thin:
            observation["review_ref"]["thin_reason"] = "원장에 이 record가 없음"
        if stale:
            observation["review_ref"]["stale_reason"] = "앵커 행이 라이브 판본에서 바뀜"
        observations.append(observation)
    for question in receipt["questions"]:
        if not isinstance(question, dict):
            continue
        status = question.get("status")
        if status in ("open", "judgment_unavailable"):
            block = question.get("judgment_unavailable") if isinstance(question.get("judgment_unavailable"), dict) else {}
            pending.append({
                "id": f"{qprefix}{question.get('record_id')}",
                "kind": "open_question" if status == "open" else "judgment_unavailable",
                "text": str(question.get("convention_slot") or ""),
                "reason": block.get("reason"),
                "needed_input": block.get("needed_input"),
                "review_ref": {**review_ref_base, "record_id": question.get("record_id")},
            })
    drift_count = sum(1 for d in receipt["drifts"] if isinstance(d, dict))
    return observations, pending, excluded, drift_count


def _same_source(existing_ref, new_ref: dict) -> bool:
    """같은 출처(대상 문서)의 receipt인가 — 후속 라운드는 같은 target, 다른 snapshot."""
    return (isinstance(existing_ref, dict) and bool(existing_ref.get("source_identity"))
            and existing_ref.get("source_identity") == new_ref.get("source_identity"))


def upsert(manifest: dict, observations: list[dict], pending: list[dict], receipt: dict, *, observation_prefix: str = "RG-") -> dict:
    """manifest를 제자리에서 갱신하고 집계를 돌려준다.

    규칙(구현 r1-02·03·04·05):
    - 신규 id → 추가. 기존 관찰에 `review_ref`가 없으면 사람 관찰과의 충돌 → UsageError.
    - 기존 `review_ref.target` ≠ 새 receipt target → 다른 문서의 같은 finding_id → UsageError(덮어쓰지 않는다).
    - 내용 필드(CONTENT_FIELDS)는 **직전 import 값**(`review_ref.imported`)과 현재 값이 같을 때만 갱신 —
      다르면 사람이 고친 것이라 보존하고 리포트에 남긴다.
    - 안전 플래그는 강등만: 사람이 `verified: false`로 내렸으면 어댑터가 올리지 않는다.
    - 같은 출처의 후속 receipt에서 verified가 아니게 된(또는 사라진) 기존 어댑터 관찰은
      `verified: false`로 강등하고 `review_ref.withdrawn`에 사유를 적는다(사람 작성 내용은 보존).
    - pending은 어댑터 소유 키만 갱신, 사람이 더한 키는 보존. `review_ref` 없는 같은 id는 충돌.
    """
    obs_list = manifest.setdefault("observations", [])
    if not isinstance(obs_list, list):
        raise InputError("manifest.observations가 리스트가 아니다")
    by_id = {o.get("id"): o for o in obs_list if isinstance(o, dict)}
    stats = {"added": 0, "updated": 0, "unchanged": 0, "preserved": [], "withdrawn": [], "absent": [],
             "p_added": 0, "p_updated": 0}
    new_ids = set()
    for observation in observations:
        new_ids.add(observation["id"])
        existing = by_id.get(observation["id"])
        imported = {field: copy.deepcopy(observation[field]) for field in CONTENT_FIELDS + SAFETY_FIELDS}
        observation["review_ref"]["imported"] = imported
        if existing is None:
            obs_list.append(observation)
            stats["added"] += 1
            continue
        existing_ref = existing.get("review_ref")
        if not isinstance(existing_ref, dict):
            raise UsageError(
                f"관찰 id {observation['id']!r}가 이미 있는데 review_ref가 없다 — 사람이 만든 관찰이라 "
                "어댑터가 덮어쓰지 않는다(id를 바꾸거나 그 관찰을 옮겨라)"
            )
        if not _same_source(existing_ref, observation["review_ref"]):
            raise UsageError(
                f"관찰 id {observation['id']!r}는 다른 대상({existing_ref.get('target')!r})의 receipt에서 왔다 — "
                f"이 receipt의 대상은 {observation['review_ref'].get('target')!r}. 같은 manifest에 두 문서의 "
                "같은 finding_id를 섞을 수 없다(다른 manifest를 쓰거나 id 접두를 바꿔라)"
            )
        before = copy.deepcopy(existing)
        previous = existing_ref.get("imported") if isinstance(existing_ref.get("imported"), dict) else {}
        # Codex 구현 r1-02 → r2-01 → r3-01/02(같은 뿌리 3회) — 기제 판정: 어댑터 출력과 사람 편집이 한
        # 관찰에 섞이는 3-way merge라 필드별 규칙을 더할수록 구석이 남는다. fail-closed 최소분으로 닫는다:
        #   ⓐ 사람이 **한 번이라도 손댄** 관찰(어느 어댑터 필드든 직전 import 값과 다르거나 `human_overrides`가
        #      있으면)은 어댑터가 내용 필드를 쓰지 않고, 안전 플래그는 **단조 강등만** 한다
        #      (verified는 내리기만, needs_revalidation·thin_source는 올리기만). 승격은 사람이 표식을 지운 뒤에만.
        #   ⓑ 손대지 않은 관찰은 어댑터가 전부 소유한다(계산값 그대로).
        #   ⓒ 어댑터가 **쓴** 값만 `imported` 기준선에 반영한다 — 보존한 필드의 기준선은 그대로(r2-01).
        overrides = set(existing_ref.get("human_overrides") or [])
        for field in CONTENT_FIELDS + SAFETY_FIELDS:
            if field in previous and existing.get(field) != previous.get(field):
                overrides.add(field)
        new_imported = dict(previous) if previous else {}
        computed = observation["review_ref"]["imported"]
        # Codex 구현 r4-01(같은 뿌리 4회 → 분리 #TBD + fail-closed 최소분): **강등 이력이 있는 관찰은 자동으로
        # 승격하지 않는다.** withdrawn 표식이 남아 있으면 사람이 손댄 것과 같이 다룬다(강등만) — 사람이
        # `review_ref.withdrawn`을 지워야 어댑터가 다시 올릴 수 있다. 자동 복귀(r3-03)는 이 규칙에 양보한다.
        withdrawn = existing_ref.get("withdrawn")
        if withdrawn:
            observation["review_ref"]["withdrawn"] = withdrawn
        if overrides or withdrawn:
            stats["preserved"].append((observation["id"], "+".join(sorted(overrides)) or "withdrawn"))
            # 단조 강등만
            existing["needs_revalidation"] = bool(existing.get("needs_revalidation")) or computed["needs_revalidation"]
            existing["thin_source"] = bool(existing.get("thin_source")) or computed["thin_source"]
            existing["verified"] = bool(existing.get("verified")) and computed["verified"] \
                and not existing["needs_revalidation"] and not existing["thin_source"]
            for field in SAFETY_FIELDS:
                new_imported[field] = existing[field]  # 어댑터가 쓴 값
            if overrides:
                observation["review_ref"]["human_overrides"] = sorted(overrides)
        else:
            for field in CONTENT_FIELDS + SAFETY_FIELDS:
                existing[field] = copy.deepcopy(computed[field])
                new_imported[field] = copy.deepcopy(computed[field])  # 같은 객체를 공유하면 YAML anchor(&id)가 생긴다
        observation["review_ref"]["imported"] = new_imported
        existing["review_ref"] = observation["review_ref"]
        if existing != before:
            stats["updated"] += 1
        else:
            stats["unchanged"] += 1
    # r1-04: 같은 출처의 기존 어댑터 관찰 중 이번 receipt에서 verified가 아닌 것 → 강등
    receipt_status = {}
    for finding in receipt.get("findings", []):
        if isinstance(finding, dict):
            receipt_status[finding.get("finding_id") or finding.get("record_id")] = finding.get("status")
    for existing in obs_list:
        if not isinstance(existing, dict) or existing.get("id") in new_ids:
            continue
        ref = existing.get("review_ref")
        if isinstance(ref, dict) and existing.get("id") != observation_prefix + str(ref.get("finding_id")):
            continue
        if not _same_source(ref, {"source_identity": receipt.get("_source_identity")}):
            if isinstance(ref, dict) and existing.get("id") in {observation_prefix + fid for fid in receipt_status if isinstance(fid, str)}:
                raise InputError(f"source identity collision: {existing.get('id')}")
            continue
        fid = ref.get("finding_id")
        status = receipt_status.get(fid)
        if status is None:
            # 관측 노트(2026-09-08 12:40) 3: 후속 판본에 **없는** finding은 건드리지 않는다 — 리포트에만 남긴다
            # (§4.2 결번은 공시 대상이지 철회의 증거가 아니다).
            stats["absent"].append(existing["id"])
            continue
        if status == "verified":
            # Another configured prefix may have created a distinct observation.
            # Still-verified evidence is not a withdrawal instruction for that ID.
            continue
        why = f"후속 receipt에서 status={status!r}"
        before = copy.deepcopy(existing)
        # r3-02: withdrawn은 강등만 — 사람이 올린 needs_revalidation·thin_source는 내리지 않는다.
        # r4-01: 쓰기 **전에** 사람 수정 흔적을 영속화한다(기준선과 다른 필드 → human_overrides). 기준선은
        # 손대지 않는다 — withdrawn 표식 자체가 "자동 승격 금지"를 뜻하므로(위 update 경로) r3-03의
        # 기준선 갱신은 더 이상 필요 없다.
        imported = ref.get("imported") if isinstance(ref.get("imported"), dict) else {}
        overrides = set(ref.get("human_overrides") or [])
        for field in CONTENT_FIELDS + SAFETY_FIELDS:
            if field in imported and existing.get(field) != imported.get(field):
                overrides.add(field)
        if overrides:
            ref["human_overrides"] = sorted(overrides)
        existing["verified"] = False
        imported["verified"] = False  # 어댑터가 쓴 값은 기준선에 — 표식을 지운 뒤 정당한 복귀가 사람 처분으로 오인되지 않게(r3-03)
        ref["imported"] = imported
        ref["withdrawn"] = {"snapshot_id": receipt.get("snapshot_id"), "why": why}
        if existing != before:
            stats["withdrawn"].append((existing["id"], why))
            stats["updated"] += 1
    pending_list = manifest.setdefault("pending_issues", [])
    if not isinstance(pending_list, list):
        raise InputError("manifest.pending_issues가 리스트가 아니다")
    pending_by_id = {p.get("id"): p for p in pending_list if isinstance(p, dict)}
    for issue in pending:
        existing = pending_by_id.get(issue["id"])
        if existing is None:
            pending_list.append(issue)
            stats["p_added"] += 1
            continue
        if not isinstance(existing.get("review_ref"), dict):
            raise UsageError(f"pending id {issue['id']!r}가 이미 있는데 review_ref가 없다 — 사람이 만든 항목이라 덮어쓰지 않는다")
        if not _same_source(existing.get("review_ref"), issue["review_ref"]):
            # Codex 구현 r2-02: 관찰과 같은 출처 결속 — 다른 문서의 같은 question id로 사람 처분이 섞이지 않게.
            raise UsageError(
                f"pending id {issue['id']!r}는 다른 대상({existing['review_ref'].get('target')!r})의 receipt에서 왔다 — "
                f"이 receipt의 대상은 {issue['review_ref'].get('target')!r}"
            )
        before = copy.deepcopy(existing)
        for field in PENDING_FIELDS:
            existing[field] = issue[field]
        if existing != before:
            stats["p_updated"] += 1
    return stats


def render_report(
    receipt_path: Path, receipt_sha256: str, snapshot_id: str, freshness: dict, thin_reason: str | None,
    observations: list[dict], pending: list[dict], excluded: list[dict], drift_count: int,
    stats: dict, contract_path: Path, contract: dict,
) -> str:
    now = datetime.now(KST).strftime("%Y-%m-%d %H:%M %Z")
    revalidate = [o for o in observations if o["needs_revalidation"]]
    thin = [o for o in observations if o["thin_source"]]
    lines = [
        "# review-gate receipt import 리포트 (asistobe-auth 1단계 입력 어댑터, docauth#354)", "",
        f"- 생성: {now}", f"- receipt: `{receipt_path}` (sha256:{receipt_sha256})",
        f"- receipt snapshot_id: `{snapshot_id}`",
        f"- 판본 대조: **{freshness['summary']}**",
        f"- kind 매핑 계약: `{contract_path}` (severity → kind: "
        + ", ".join(f"{k}→{v}" for k, v in contract["kind_by_severity"].items()) + ")",
        f"- 원장: {'읽음' if thin_reason is None else '얇은 receipt — ' + thin_reason}",
        "", "## 집계", "",
        f"- 관찰 upsert: 신규 {stats['added']} · 갱신 {stats['updated']} · 무변경 {stats['unchanged']}",
        f"- 사람 수정 보존(직전 import 값과 달라 덮어쓰지 않음): {len(stats['preserved'])}건"
        + (" — " + ", ".join(f"{i}.{f}" for i, f in stats["preserved"]) if stats["preserved"] else ""),
        f"- 후속 receipt로 강등(withdrawn): {len(stats['withdrawn'])}건"
        + (" — " + ", ".join(f"{i}({w})" for i, w in stats["withdrawn"]) if stats["withdrawn"] else ""),
        f"- 이번 receipt에 없는 기존 관찰(건드리지 않음): {len(stats['absent'])}건"
        + (" — " + ", ".join(stats["absent"]) if stats["absent"] else ""),
        f"- pending_issues: 신규 {stats['p_added']} · 갱신 {stats['p_updated']} (총 {len(pending)})",
        f"- 제외: {len(excluded)}건 · drift(제외, 건수만): {drift_count}건",
        f"- 재판정 필요(needs_revalidation): {len(revalidate)}건 · 얇은 원천(thin_source): {len(thin)}건",
        "", "## 재판정 필요 관찰", "",
    ]
    if revalidate:
        lines += ["| id | 사유 | 앵커 |", "|---|---|---|"]
        for o in revalidate:
            why = o["review_ref"].get("stale_reason") or freshness["summary"]
            lines.append(f"| {o['id']} | {why} | {'; '.join(o['sources'])} |")
    else:
        lines.append("없음.")
    lines += ["", "## 제외 (verified가 아닌 finding)", ""]
    if excluded:
        lines += ["| id | 사유 |", "|---|---|"] + [f"| {e['id']} | {e['why']} |" for e in excluded]
    else:
        lines.append("없음.")
    lines += ["", "## pending_issues (관찰 아님 — 청크 issues 후보)", ""]
    if pending:
        lines += ["| id | 종류 | reason | 필요 입력 |", "|---|---|---|---|"] + [
            f"| {p['id']} | {p['kind']} | {p.get('reason') or ''} | {p.get('needed_input') or ''} |" for p in pending
        ]
    else:
        lines.append("없음.")
    lines += ["", "> 판단 불가(judgment_unavailable)·open question은 승인·억제·to-be의 근거가 아니다 — "
              "사람 처분 뒤 다음 라운드 receipt에서 verified로 돌아오면 그때 관찰이 된다.", ""]
    return "\n".join(lines)


def _check_outputs(manifest, report, protected, packet):
    if any(".." in Path(value).parts for value in (manifest, report)):
        raise InputError("output paths must not contain parent traversal")
    outputs = [Path(manifest).absolute(), Path(report).absolute()]
    for out in outputs:
        for part in [out, *out.parents]:
            if part.is_symlink():
                raise InputError("output paths must not contain symlinks")
        if out.is_relative_to(packet):
            raise InputError("import outputs cannot be inside the packet")
        if out.exists() and (not out.is_file() or out.stat().st_nlink != 1):
            raise InputError("output must be a private regular file")
    for index, out in enumerate(outputs):
        others = protected + outputs[:index]
        for other in others:
            other = Path(other).absolute()
            if out == other or (out.exists() and other.exists() and out.samefile(other)):
                raise InputError("import output aliases an input or the other output")


def _atomic_write(path, raw):
    fd, name = tempfile.mkstemp(prefix=".docloop-import-", dir=path.parent)
    with os.fdopen(fd, "wb") as handle:
        handle.write(raw)
        handle.flush()
        os.fsync(handle.fileno())
    os.replace(name, path)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("packet", type=Path, help="prepared review-gate packet")
    ap.add_argument("review", type=Path, help="review-gate 전달본(review.md, schema_version 2 receipt)")
    ap.add_argument("--manifest", type=Path, required=True, help="asistobe manifest.yaml (제자리 갱신)")
    group = ap.add_mutually_exclusive_group()
    group.add_argument("--live", type=Path, help="대상 문서의 현재 전문(라이브 판본) 파일")
    group.add_argument("--no-live", action="store_true", help="신선도 미확인 선언 — 전건 needs_revalidation")
    ap.add_argument("--contract", type=Path, default=DEFAULT_CONTRACT, help="kind 매핑 계약 파일(review_import.yaml)")
    ap.add_argument("--report", type=Path, default=None, help="리포트 경로(기본 <manifest 폴더>/reports/_import_report.md)")
    ap.add_argument("--dry-run", action="store_true", help="manifest와 report 모두 쓰지 않고 검사")
    args = ap.parse_args(argv)

    if args.live is None and not args.no_live:
        print("USAGE-ERROR: --live <현재 전문> 또는 --no-live 중 하나가 필요하다 — 신선도 미확인 상태로 "
              "verified를 옮기지 않는다(fail-closed)", file=sys.stderr)
        return 2
    try:
        from review_gate.runner import _validate_prepared_packet, GateError
        from review_gate import validate_review_result as validator
        from review_gate.validate_review_intermediate import resolve_packet_file, load_yaml_text
        root, run, complete = _validate_prepared_packet(args.packet)
        receipt_rel = args.review.as_posix()
        binding, errors = validator.packet_binding_from_metadata(run, complete, receipt_rel)
        if errors or binding is None:
            raise InputError("; ".join(errors))
        path_errors = []
        receipt_file = resolve_packet_file(root, receipt_rel, "receipt", path_errors)
        if receipt_file is None:
            raise InputError("; ".join(path_errors))
        args.review = receipt_file
        raw_receipt, read_error = validator._read_packet_file_bytes(args.review, "import receipt")
        if read_error:
            raise InputError(read_error)
        errors = validator.validate(root, receipt_rel, binding, receipt_bytes=raw_receipt)
        if errors and errors != [validator.INDETERMINATE_MESSAGE]:
            raise InputError("receipt did not validate: " + "; ".join(errors))
        receipt = validator.parse_receipt_bytes(raw_receipt)
        receipt["_source_identity"] = [str(root.parent.parent), run["target"]["source"]]
        report_path = args.report or (args.manifest.parent / "reports" / "_import_report.md")
        protected = [args.review, args.contract]
        if args.live is not None:
            protected.append(args.live)
        for ref in [receipt["classification_ledger_ref"]["path"], receipt["input_gate"]["source_copy"]["path"]]:
            protected_path = resolve_packet_file(root, ref, "import reference", path_errors)
            if protected_path is None:
                raise InputError("; ".join(path_errors))
            protected.append(protected_path)
        _check_outputs(args.manifest, report_path, protected, root)
        manifest_before = args.manifest.read_bytes()
        receipt_sha256 = _sha256(raw_receipt)
        contract = load_contract(args.contract)
        repo_root = root
        ledger, thin_reason = load_ledger(receipt, args.review, repo_root)
        source_lines, copy_problem = load_source_copy(receipt, repo_root, args.review)

        freshness: dict = {"mode": "no-live", "comparable": False, "changed": set(), "live_norm": set(),
                           "summary": "--no-live 선언 — 전건 재판정 필요(신선도 미확인)"}
        if args.live is not None:
            live_raw = args.live.read_bytes()
            live_sha = _sha256(live_raw)
            snap = SHA256_SNAPSHOT.match(receipt["snapshot_id"])
            freshness["mode"] = "live"
            if snap is None:
                freshness["summary"] = "receipt snapshot_id가 sha256 형식이 아니라 대조 불가 — 전건 재판정 필요"
            elif source_lines is None:
                freshness["summary"] = f"source_copy를 읽지 못해 대조 불가({copy_problem}) — 전건 재판정 필요"
            elif snap.group(1) == live_sha:
                # 판본 동일 — 바뀐 행 없음, 라이브 정규화 행 = source_copy 행(해시 앵커 검사가 성립하도록).
                freshness.update({"comparable": True, "changed": set(), "live_norm": {_norm(l) for l in source_lines},
                                  "summary": "라이브 판본 == receipt 스냅샷 (앵커 유효)"})
            else:
                live_lines = live_raw.decode("utf-8", errors="replace").splitlines()
                changed, live_norm = changed_lines(source_lines, live_lines)
                freshness.update({"comparable": True, "changed": changed, "live_norm": live_norm,
                                  "summary": f"라이브 판본 ≠ receipt 스냅샷 — 바뀐 옛 행 {len(changed)}개, 그 행에 앵커된 finding만 재판정"})

        manifest = load_yaml_text(manifest_before)
        if not isinstance(manifest, dict):
            raise InputError(f"{args.manifest}: manifest 최상위가 매핑이 아니다")
        pre_errors, _ = validate_manifest(manifest)
        if pre_errors:
            raise InputError("invalid input manifest: " + "; ".join(pre_errors))
        observations, pending, excluded, drift_count = build_observations(
            receipt, args.review, receipt_sha256, ledger, thin_reason, contract, source_lines, freshness
        )
        before = yaml.safe_dump(manifest, allow_unicode=True, sort_keys=False)
        stats = upsert(manifest, observations, pending, receipt, observation_prefix=contract["observation_id_prefix"])
        errors, _ = validate_manifest(manifest)
        if errors:
            raise InputError("갱신된 manifest가 스키마 검증에 실패한다: " + "; ".join(errors))
        after = yaml.safe_dump(manifest, allow_unicode=True, sort_keys=False)
        report_path = args.report or (args.manifest.parent / "reports" / "_import_report.md")
        report = render_report(args.review, receipt_sha256, receipt["snapshot_id"], freshness, thin_reason,
                               observations, pending, excluded, drift_count, stats, args.contract, contract)
        if not args.dry_run:
            _check_outputs(args.manifest, report_path, protected, root)
            if args.manifest.read_bytes() != manifest_before:
                raise InputError("manifest changed during import; no overwrite")
            # Revalidate mutable result inputs immediately before committing.
            re_errors = validator.validate(root, receipt_rel, binding, receipt_bytes=raw_receipt)
            if re_errors and re_errors != [validator.INDETERMINATE_MESSAGE]:
                raise InputError("receipt changed during import: " + "; ".join(re_errors))
            if args.review.read_bytes() != raw_receipt:
                raise InputError("receipt bytes changed during import")
            if after != before:
                _atomic_write(args.manifest, after.encode("utf-8"))
            try:
                report_path.parent.mkdir(parents=True, exist_ok=True)
                _atomic_write(report_path, report.encode("utf-8"))
            except OSError as exc:
                print(f"IMPORT-COMMITTED-REPORT-FAILED: manifest committed; report failed: {exc}", file=sys.stderr)
                return 6
    except UsageError as exc:
        print(f"USAGE-ERROR: {exc}", file=sys.stderr)
        return 2
    except (InputError, GateError, ValueError, yaml.YAMLError) as exc:
        print(f"FAIL: {exc}", file=sys.stderr)
        return 1
    except OSError as exc:
        print(f"FAIL: {exc}", file=sys.stderr)
        return 1
    changed = "변경 없음(멱등)" if after == before else f"신규 {stats['added']} · 갱신 {stats['updated']}"
    print(f"IMPORT-OK: 관찰 {len(observations)}건({changed}) · pending {len(pending)} · 제외 {len(excluded)} · "
          f"drift {drift_count} · {freshness['summary']}" + (" [dry-run]" if args.dry_run else ""))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
