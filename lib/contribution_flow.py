#!/usr/bin/env python3
"""Optional append-only contribute -> curate workflow for docloop.

This module deliberately does not modify manifest.yaml or the document SSOT.  It
creates self-contained, validated bundles below work/ and only draft-curated
bridges a validated curation back into the normal draft prompt.
"""

from __future__ import annotations

import argparse
import errno
import hashlib
import json
import os
import re
import selectors
import signal
import stat
import subprocess
import sys
import tempfile
import time
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath
from typing import Any

import yaml


SCHEMA_VERSION = 1
TOOL_VERSION = "0.11.0"
PERSPECTIVES = {"pm", "product-designer", "frontend", "backend", "qa"}
ID_RE = re.compile(r"^[a-z0-9][a-z0-9-]{0,63}$")
MATERIAL_ID_RE = re.compile(r"^[a-z][a-z0-9-]{0,63}$")
SHA_RE = re.compile(r"^[0-9a-f]{64}$")
STATUSES = {"decided", "supported", "open", "carried", "dismissed"}
STATUS_ORDER = {v: i for i, v in enumerate(("decided", "supported", "open", "carried", "dismissed"))}
MAX_PERSPECTIVES = 5
MAX_INPUT_FILES = 1000
MAX_FILE_BYTES = 10 * 1024 * 1024
MAX_INPUT_BYTES = 50 * 1024 * 1024
MAX_MODEL_BYTES = 1024 * 1024
MAX_ITEMS = 50
MAX_MATERIALS = 50
MAX_MATERIAL_BYTES = 50 * 1024 * 1024
MAX_ARTIFACT_BYTES = 2 * 1024 * 1024
MAX_STDERR_BYTES = 64 * 1024
ROOT = Path(__file__).resolve().parent.parent
CLAUDE_CONTRIBUTION_SCHEMA = json.dumps({
    "type": "object",
    "additionalProperties": False,
    "required": ["schema_version", "run_id", "perspective", "model_lineage", "items"],
    "properties": {
        "schema_version": {"type": "integer", "const": SCHEMA_VERSION},
        "run_id": {"type": "string", "minLength": 1},
        "perspective": {"type": "string", "enum": sorted(PERSPECTIVES)},
        "model_lineage": {"type": "string", "const": "claude"},
        "items": {
            "type": "array", "maxItems": MAX_ITEMS,
            "items": {
                "type": "object",
                "additionalProperties": False,
                "required": ["item_id", "consideration", "target_section", "evidence_refs",
                             "human_need", "human_question"],
                "properties": {
                    "item_id": {"type": "string", "minLength": 1},
                    "consideration": {"type": "string", "minLength": 1},
                    "target_section": {"type": "string", "minLength": 1},
                    "evidence_refs": {
                        "type": "array", "uniqueItems": True,
                        "items": {"type": "string"},
                    },
                    "human_need": {"type": "string", "enum": ["decision", "material", "both", "none"]},
                    "human_question": {"type": "string", "minLength": 1},
                },
            },
        },
    },
}, sort_keys=True, separators=(",", ":"))


class FlowError(Exception):
    """Expected, body-safe failure surfaced to the console."""


_interrupted = False
_active_child: subprocess.Popen[bytes] | None = None


class _UniqueKeyLoader(yaml.SafeLoader):
    """Safe YAML loader that rejects ambiguous duplicate mapping keys."""


def _construct_unique_mapping(loader: _UniqueKeyLoader, node: yaml.MappingNode, deep: bool = False) -> dict[Any, Any]:
    loader.flatten_mapping(node)
    mapping: dict[Any, Any] = {}
    for key_node, value_node in node.value:
        key = loader.construct_object(key_node, deep=deep)
        try:
            duplicate = key in mapping
        except TypeError as exc:
            raise yaml.constructor.ConstructorError(
                "while constructing a mapping", node.start_mark,
                "found an unhashable mapping key", key_node.start_mark,
            ) from exc
        if duplicate:
            raise yaml.constructor.ConstructorError(
                "while constructing a mapping", node.start_mark,
                f"found duplicate key {key!r}", key_node.start_mark,
            )
        mapping[key] = loader.construct_object(value_node, deep=deep)
    return mapping


_UniqueKeyLoader.add_constructor(
    yaml.resolver.BaseResolver.DEFAULT_MAPPING_TAG, _construct_unique_mapping,
)


def _signal_handler(signum: int, _frame: Any) -> None:
    global _interrupted
    _interrupted = True
    child = _active_child
    if child is not None and child.poll() is None:
        try:
            os.killpg(child.pid, signal.SIGTERM)
        except ProcessLookupError:
            pass


def _install_signal_handlers() -> None:
    for sig in (signal.SIGINT, signal.SIGTERM, signal.SIGHUP):
        signal.signal(sig, _signal_handler)


def _utc_now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _yaml_bytes(value: Any) -> bytes:
    text = yaml.safe_dump(
        value, sort_keys=False, allow_unicode=True, default_flow_style=False,
        width=10**9, line_break="\n",
    ).rstrip("\n") + "\n"
    return text.encode("utf-8")


def _load_yaml_bytes(data: bytes, label: str) -> Any:
    try:
        text = data.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise FlowError(f"{label}: not valid UTF-8") from exc
    try:
        return yaml.load(text, Loader=_UniqueKeyLoader)
    except yaml.YAMLError as exc:
        raise FlowError(f"{label}: malformed YAML") from exc


def _regular_nosymlink(path: Path, label: str) -> os.stat_result:
    try:
        st = path.lstat()
    except FileNotFoundError as exc:
        raise FlowError(f"{label}: missing: {path}") from exc
    if stat.S_ISLNK(st.st_mode) or not stat.S_ISREG(st.st_mode):
        raise FlowError(f"{label}: must be a regular non-symlink file: {path}")
    return st


def _read_stable(path: Path, label: str, max_bytes: int) -> bytes:
    return _read_stable_record(path, label, max_bytes)[0]


def _read_stable_record(path: Path, label: str, max_bytes: int) -> tuple[bytes, os.stat_result]:
    before = _regular_nosymlink(path, label)
    if before.st_size > max_bytes:
        raise FlowError(f"{label}: file cap exceeded ({before.st_size} > {max_bytes})")
    with path.open("rb") as fh:
        data = fh.read(max_bytes + 1)
    after = _regular_nosymlink(path, label)
    if len(data) > max_bytes:
        raise FlowError(f"{label}: file cap exceeded")
    fingerprint = lambda s: (s.st_dev, s.st_ino, s.st_size, s.st_mtime_ns, s.st_ctime_ns)
    if fingerprint(before) != fingerprint(after) or len(data) != after.st_size:
        raise FlowError(f"{label}: changed while being read")
    return data, after


def _open_real_directory(path: Path, label: str) -> int:
    """Open an absolute directory component-by-component without symlink traversal."""
    absolute = path if path.is_absolute() else Path.cwd() / path
    flags = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0)
    nofollow = getattr(os, "O_NOFOLLOW", 0)
    fd = os.open(absolute.anchor, flags)
    try:
        for part in absolute.parts[1:]:
            try:
                next_fd = os.open(part, flags | nofollow, dir_fd=fd)
            except OSError as exc:
                raise FlowError(f"{label}: ancestor must be a real directory: {absolute}") from exc
            os.close(fd)
            fd = next_fd
        return fd
    except BaseException:
        os.close(fd)
        raise


def _open_child_directory(parent_fd: int, name: str, label: str, create: bool) -> int:
    flags = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0) | getattr(os, "O_NOFOLLOW", 0)
    try:
        return os.open(name, flags, dir_fd=parent_fd)
    except FileNotFoundError:
        if not create:
            raise FlowError(f"{label}: directory missing: {name}")
        try:
            os.mkdir(name, mode=0o700, dir_fd=parent_fd)
        except FileExistsError:
            pass
        try:
            return os.open(name, flags, dir_fd=parent_fd)
        except OSError as exc:
            raise FlowError(f"{label}: created path is not a real directory: {name}") from exc
    except OSError as exc:
        raise FlowError(f"{label}: path is not a real directory: {name}") from exc


def _open_relative_directory(base_fd: int, parts: tuple[str, ...], label: str, create: bool) -> int:
    fd = os.dup(base_fd)
    try:
        for part in parts:
            next_fd = _open_child_directory(fd, part, label, create)
            os.close(fd)
            fd = next_fd
        return fd
    except BaseException:
        os.close(fd)
        raise


def _write_exclusive_at(parent_fd: int, name: str, display_path: Path, data: bytes,
                        mode: int, max_bytes: int | None) -> None:
    if max_bytes is not None and len(data) > max_bytes:
        raise FlowError(f"artifact cap exceeded for {display_path.name}")
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    try:
        fd = os.open(name, flags, mode, dir_fd=parent_fd)
    except OSError as exc:
        if exc.errno == errno.EEXIST:
            raise FlowError(f"no-clobber: destination already exists: {display_path}") from exc
        raise FlowError(f"could not create destination safely: {display_path}") from exc
    try:
        with os.fdopen(fd, "wb") as fh:
            fh.write(data)
            fh.flush()
            os.fsync(fh.fileno())
    except BaseException:
        try:
            os.unlink(name, dir_fd=parent_fd)
        except OSError:
            pass
        raise


def _write_exclusive(path: Path, data: bytes, mode: int = 0o600,
                     max_bytes: int | None = MAX_ARTIFACT_BYTES) -> None:
    parent_fd = _open_real_directory(path.parent, "destination parent")
    try:
        _write_exclusive_at(parent_fd, path.name, path, data, mode, max_bytes)
    finally:
        os.close(parent_fd)


def _write_exclusive_below(base: Path, relative: PurePosixPath, data: bytes,
                           mode: int = 0o600, max_bytes: int | None = MAX_ARTIFACT_BYTES,
                           create_parents: bool = False) -> None:
    rel = _safe_rel(relative.as_posix(), "destination relative path")
    base_fd = _open_real_directory(base, "destination base")
    try:
        parent_fd = _open_relative_directory(base_fd, rel.parts[:-1], "destination parent", create_parents)
        try:
            _write_exclusive_at(parent_fd, rel.name, base / Path(*rel.parts), data, mode, max_bytes)
        finally:
            os.close(parent_fd)
    finally:
        os.close(base_fd)


def _unlink_output(path: Path) -> None:
    """Unlink one output entry without following any parent symlink."""
    parent_fd = _open_real_directory(path.parent, "output cleanup parent")
    try:
        os.unlink(path.name, dir_fd=parent_fd)
    finally:
        os.close(parent_fd)


def _reserve(path: Path) -> None:
    _ensure_dir_chain(path.parents[2], path.parent)
    parent_fd = _open_real_directory(path.parent, "reservation parent")
    try:
        try:
            os.mkdir(path.name, mode=0o700, dir_fd=parent_fd)
        except FileExistsError as exc:
            raise FlowError(f"run ID already exists; use a new ID: {path.name}") from exc
    finally:
        os.close(parent_fd)
    _write_exclusive_below(path, PurePosixPath("INCOMPLETE"), b"incomplete\n")
    _ensure_dir_chain(path, path / "payload")


def _validate_id(value: str, label: str) -> None:
    if not ID_RE.fullmatch(value):
        raise FlowError(f"{label}: must match [a-z0-9][a-z0-9-]{{0,63}}")


def _safe_rel(value: str, label: str) -> PurePosixPath:
    if not isinstance(value, str) or not value or "\x00" in value or "\\" in value:
        raise FlowError(f"{label}: invalid POSIX relative path")
    p = PurePosixPath(value)
    if p.is_absolute() or any(part in ("", ".", "..") for part in p.parts):
        raise FlowError(f"{label}: must be a normalized POSIX relative path without '..'")
    return p


def _inside(path: Path, base: Path) -> bool:
    try:
        path.resolve().relative_to(base.resolve())
        return True
    except (ValueError, OSError):
        return False


def _ensure_dir_chain(base: Path, target: Path) -> None:
    """Create target below base without following destination-parent symlinks."""
    try:
        rel = target.relative_to(base)
    except ValueError as exc:
        raise FlowError(f"destination escapes work root: {target}") from exc
    base_fd = _open_real_directory(base, "destination base")
    try:
        target_fd = _open_relative_directory(base_fd, rel.parts, "destination parent", True)
        os.close(target_fd)
    finally:
        os.close(base_fd)


def _assert_real_chain(base: Path, target: Path) -> None:
    """Reject symlink/non-directory ancestors from base through target's parent."""
    try:
        rel = target.relative_to(base)
    except ValueError as exc:
        raise FlowError(f"bundle path escapes work root: {target}") from exc
    try:
        base_st = base.lstat()
    except FileNotFoundError as exc:
        raise FlowError(f"bundle base missing: {base}") from exc
    if stat.S_ISLNK(base_st.st_mode) or not stat.S_ISDIR(base_st.st_mode):
        raise FlowError(f"bundle base must be a real directory: {base}")
    current = base
    for part in rel.parts[:-1]:
        current = current / part
        try:
            st = current.lstat()
        except FileNotFoundError as exc:
            raise FlowError(f"bundle parent missing: {current}") from exc
        if stat.S_ISLNK(st.st_mode) or not stat.S_ISDIR(st.st_mode):
            raise FlowError(f"bundle parent must be a real directory: {current}")


def _walk_regular(root: Path, rel_prefix: str) -> list[tuple[str, Path, os.stat_result]]:
    if not root.exists():
        return []
    if root.is_symlink() or not root.is_dir():
        raise FlowError(f"snapshot source must be a real directory: {root}")
    rows: list[tuple[str, Path, os.stat_result]] = []
    for dirpath, dirnames, filenames in os.walk(root, followlinks=False):
        dp = Path(dirpath)
        for name in list(dirnames):
            child = dp / name
            st = child.lstat()
            if stat.S_ISLNK(st.st_mode) or not stat.S_ISDIR(st.st_mode):
                raise FlowError(f"snapshot rejects symlink/special path: {child}")
        for name in filenames:
            child = dp / name
            st = child.lstat()
            if stat.S_ISLNK(st.st_mode) or not stat.S_ISREG(st.st_mode):
                raise FlowError(f"snapshot rejects symlink/special file: {child}")
            rel = child.relative_to(root).as_posix()
            rows.append((f"{rel_prefix}/{rel}", child, st))
    return sorted(rows, key=lambda row: row[0].encode("utf-8"))


def _capture_source_state(work: Path) -> tuple[dict[str, Any], bytes, list[tuple[str, str]],
                                                    list[dict[str, Any]], dict[str, bytes]]:
    """Freshly enumerate and read every source used by the snapshot."""
    manifest_path = work / "manifest.yaml"
    manifest_raw, manifest_st = _read_stable_record(manifest_path, "manifest", MAX_FILE_BYTES)
    manifest = _load_yaml_bytes(manifest_raw, "manifest")
    if not isinstance(manifest, dict) or not isinstance(manifest.get("project"), dict):
        raise FlowError("manifest: project mapping missing")
    policy_ref = manifest["project"].get("policy")
    if not isinstance(policy_ref, str) or not policy_ref:
        raise FlowError("manifest: project.policy missing")
    policy_path = Path(os.path.expanduser(policy_ref))
    if not policy_path.is_absolute():
        policy_path = work / policy_path
    policy_raw, policy_st = _read_stable_record(policy_path, "policy", MAX_FILE_BYTES)
    policy = _load_yaml_bytes(policy_raw, "policy")
    if not isinstance(policy, dict):
        raise FlowError("policy: top level must be a mapping")
    sections = _policy_sections(manifest, policy)
    input_rows = _walk_regular(work / "inputs", "inputs")
    if len(input_rows) > MAX_INPUT_FILES:
        raise FlowError(f"input file cap exceeded ({len(input_rows)} > {MAX_INPUT_FILES})")
    rows = [("manifest.yaml", manifest_path, manifest_st),
            ("policy.yaml", policy_path, policy_st), *input_rows]
    inventory: list[dict[str, Any]] = []
    contents: dict[str, bytes] = {}
    for rel, path, _enumerated_st in rows:
        if rel == "manifest.yaml":
            data, stable_st = manifest_raw, manifest_st
        elif rel == "policy.yaml":
            data, stable_st = policy_raw, policy_st
        else:
            data, stable_st = _read_stable_record(path, f"snapshot source {rel}", MAX_FILE_BYTES)
        contents[rel] = data
        inventory.append({
            "path": rel, "bytes": len(data), "sha256": _sha(data),
            "device": stable_st.st_dev, "inode": stable_st.st_ino, "mtime_ns": stable_st.st_mtime_ns,
        })
    total = sum(row["bytes"] for row in inventory)
    if total > MAX_INPUT_BYTES:
        raise FlowError(f"captured input byte cap exceeded ({total} > {MAX_INPUT_BYTES})")
    return manifest, policy_raw, sections, inventory, contents


def _policy_sections(manifest: dict[str, Any], policy: dict[str, Any]) -> list[tuple[str, str]]:
    doc_type = (manifest.get("project") or {}).get("doc_type")
    definitions = (policy.get("doc_types") or {}).get(doc_type) if isinstance(policy, dict) else None
    raw = definitions.get("sections") if isinstance(definitions, dict) else None
    result: list[tuple[str, str]] = []
    seen: set[str] = set()
    for entry in raw or []:
        if not isinstance(entry, dict) or not isinstance(entry.get("id"), str) or not isinstance(entry.get("title"), str):
            raise FlowError("policy: malformed doc_types section entry")
        sid = entry["id"]
        if sid in seen or not ID_RE.fullmatch(sid):
            raise FlowError(f"policy: invalid or duplicate section id: {sid!r}")
        seen.add(sid)
        result.append((sid, entry["title"]))
    if not result:
        # A manifest skeleton is a valid fallback for policies without doc_types.
        for entry in manifest.get("sections") or []:
            if isinstance(entry, dict) and isinstance(entry.get("id"), str) and isinstance(entry.get("title"), str):
                if entry["id"] in seen or not ID_RE.fullmatch(entry["id"]):
                    raise FlowError(f"manifest: invalid or duplicate section id: {entry['id']!r}")
                seen.add(entry["id"]); result.append((entry["id"], entry["title"]))
    if not result:
        raise FlowError("no policy/manifest section definitions available")
    out_title = next((title for sid, title in result if sid == "out-of-scope"), "Out of scope")
    return [(sid, title) for sid, title in result if sid != "out-of-scope"] + [("out-of-scope", out_title)]


def _capture_snapshot(work: Path, payload: Path) -> tuple[str, list[tuple[str, str]], dict[str, Any]]:
    manifest, policy_raw, sections, pre, source_bytes = _capture_source_state(work)
    total = sum(row["bytes"] for row in pre)
    print(f"docloop: capture destination={payload.parent} files={len(pre)} bytes={total}", file=sys.stderr)

    snapshot = payload / "snapshot"
    _ensure_dir_chain(payload, snapshot)
    rewritten = dict(manifest)
    rewritten["project"] = dict(manifest["project"])
    rewritten["project"]["policy"] = "./policy.yaml"
    _write_exclusive_below(payload, PurePosixPath("snapshot/manifest.yaml"), _yaml_bytes(rewritten))
    _write_exclusive_below(payload, PurePosixPath("snapshot/policy.yaml"), policy_raw, max_bytes=MAX_FILE_BYTES)
    input_paths = [row["path"] for row in pre if row["path"].startswith("inputs/")]
    for rel in input_paths:
        _write_exclusive_below(snapshot, PurePosixPath(rel), source_bytes[rel],
                               max_bytes=MAX_FILE_BYTES, create_parents=True)

    _manifest2, _policy_raw2, _sections2, post, _source_bytes2 = _capture_source_state(work)
    if pre != post:
        raise FlowError("snapshot source tree changed during capture")
    captured_rows = []
    for relpath in ("manifest.yaml", "policy.yaml", *input_paths):
        data = _read_stable(snapshot / relpath, f"captured {relpath}", MAX_FILE_BYTES)
        captured_rows.append({"path": relpath, "bytes": len(data), "sha256": _sha(data)})
    inv = {"schema_version": 1, "files": captured_rows}
    inv_bytes = _yaml_bytes(inv)
    _write_exclusive_below(payload, PurePosixPath("snapshot/inventory.yaml"), inv_bytes)
    return _sha(inv_bytes), sections, manifest


def _model_command(model: str, prompt: str, read_only: bool) -> list[str]:
    if model == "codex":
        cmd = ["codex", "exec", "--skip-git-repo-check"]
        cmd += ["--sandbox", "read-only" if read_only else "workspace-write"]
        return cmd + [prompt]
    if model == "claude":
        if read_only:
            return ["claude", "--safe-mode", "--permission-mode", "dontAsk",
                    "--tools", "Read,Glob,Grep", "--no-session-persistence",
                    "--json-schema", CLAUDE_CONTRIBUTION_SCHEMA, "-p", prompt]
        return ["claude", "--permission-mode", "acceptEdits", "--tools",
                "Read,Glob,Grep,Edit,Write", "--no-session-persistence", "-p", prompt]
    raise FlowError(f"unknown DOCLOOP_MODEL {model!r} (use codex or claude)")


def _terminate_child_group(child: subprocess.Popen[bytes]) -> None:
    try:
        os.killpg(child.pid, signal.SIGTERM)
    except ProcessLookupError:
        pass
    if child.poll() is None:
        try:
            child.wait(timeout=2)
        except subprocess.TimeoutExpired:
            try:
                os.killpg(child.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
    child.wait()
    deadline = time.monotonic() + 2
    while time.monotonic() < deadline:
        try:
            os.killpg(child.pid, 0)
        except ProcessLookupError:
            return
        time.sleep(0.02)
    try:
        os.killpg(child.pid, signal.SIGKILL)
    except ProcessLookupError:
        return
    deadline = time.monotonic() + 2
    while time.monotonic() < deadline:
        try:
            os.killpg(child.pid, 0)
        except ProcessLookupError:
            return
        time.sleep(0.02)


def _run_model(prompt: str, model: str, cwd: Path, diagnostics: Path, perspective: str, read_only: bool) -> bytes:
    global _active_child
    if _interrupted:
        raise FlowError("interrupted before model launch")
    _ensure_dir_chain(diagnostics.parent, diagnostics)
    stdout_path = diagnostics / f".{perspective}.stdout"
    stderr_path = diagnostics / f".{perspective}.stderr"
    started = _utc_now()
    stderr_hash = hashlib.sha256()
    stderr_retained = bytearray()
    stderr_size = 0
    stdout_retained = bytearray()
    stdout_size = 0
    child = subprocess.Popen(
        _model_command(model, prompt, read_only), cwd=cwd,
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, start_new_session=True,
    )
    _active_child = child
    assert child.stdout is not None and child.stderr is not None
    selector = selectors.DefaultSelector()
    selector.register(child.stdout, selectors.EVENT_READ, "stdout")
    selector.register(child.stderr, selectors.EVENT_READ, "stderr")
    cleanup_attempted = False
    try:
        while selector.get_map() or child.poll() is None:
            for key, _mask in selector.select(timeout=0.02):
                chunk = os.read(key.fd, 64 * 1024)
                if not chunk:
                    selector.unregister(key.fileobj)
                    continue
                if key.data == "stdout":
                    stdout_size += len(chunk)
                    room = MAX_MODEL_BYTES - len(stdout_retained)
                    if room > 0:
                        stdout_retained.extend(chunk[:room])
                else:
                    stderr_hash.update(chunk)
                    stderr_size += len(chunk)
                    room = MAX_STDERR_BYTES - len(stderr_retained)
                    if room > 0:
                        stderr_retained.extend(chunk[:room])
            leader_exited_with_open_pipe = child.poll() is not None and bool(selector.get_map())
            if not cleanup_attempted and (_interrupted or stdout_size > MAX_MODEL_BYTES or leader_exited_with_open_pipe):
                cleanup_attempted = True
                _terminate_child_group(child)
        rc = child.wait()
    finally:
        selector.close()
        child.stdout.close()
        child.stderr.close()
        if child.poll() is None or (_interrupted and not cleanup_attempted):
            _terminate_child_group(child)
        _active_child = None
    _write_exclusive(stdout_path, bytes(stdout_retained), max_bytes=MAX_MODEL_BYTES)
    _write_exclusive(stderr_path, bytes(stderr_retained), max_bytes=MAX_STDERR_BYTES)
    diag = {
        "schema_version": 1, "perspective": perspective, "model_lineage": model,
        "started_at": started, "finished_at": _utc_now(), "exit_status": rc,
        # Do not persist provider messages: some CLIs echo prompt/environment
        # fragments on stderr.  Length/hash retain a useful diagnostic signal
        # without copying source or secrets into the bundle.
        "stderr_bytes": stderr_size,
        "stderr_sha256": stderr_hash.hexdigest(),
        "stderr_truncated": stderr_size > MAX_STDERR_BYTES,
        "stderr_retained_bytes": len(stderr_retained),
    }
    _write_exclusive(diagnostics / f"{perspective}.yaml", _yaml_bytes(diag))
    raw = bytes(stdout_retained)
    if _interrupted:
        raise FlowError("interrupted; model process group terminated and reaped")
    if stdout_size > MAX_MODEL_BYTES:
        raise FlowError(f"{perspective}: model stdout cap exceeded")
    if rc != 0:
        raise FlowError(f"{perspective}: model exited nonzero ({rc})")
    if not raw:
        raise FlowError(f"{perspective}: no output")
    stripped = raw.decode("utf-8-sig", "replace").strip()
    if not stripped:
        raise FlowError(f"{perspective}: empty output")
    _unlink_output(stderr_path)
    return raw


def _validate_keys(obj: dict[str, Any], expected: set[str], label: str) -> None:
    extra = set(obj) - expected
    missing = expected - set(obj)
    if extra or missing:
        raise FlowError(f"{label}: schema keys mismatch (missing={sorted(missing)}, unknown={sorted(extra)})")


def _schema_one(value: Any, label: str) -> None:
    if type(value) is not int or value != SCHEMA_VERSION:
        raise FlowError(f"{label}: schema_version must be integer {SCHEMA_VERSION}")


def _validate_envelope(raw: bytes, run_id: str, perspective: str, sections: set[str],
                       evidence_paths: set[str], expected_model: str | None = None) -> dict[str, Any]:
    value = _load_yaml_bytes(raw, f"{perspective} output")
    if not isinstance(value, dict):
        raise FlowError(f"{perspective}: output must be a YAML mapping")
    expected = {"schema_version", "run_id", "perspective", "model_lineage", "items"}
    _validate_keys(value, expected, f"{perspective} envelope")
    _schema_one(value["schema_version"], f"{perspective} envelope")
    if value["run_id"] != run_id or value["perspective"] != perspective:
        raise FlowError(f"{perspective}: wrong envelope header")
    if expected_model is None:
        if not isinstance(value["model_lineage"], str) or not value["model_lineage"].strip():
            raise FlowError(f"{perspective}: model_lineage must be non-empty")
    elif value["model_lineage"] != expected_model:
        raise FlowError(f"{perspective}: model_lineage must exactly match configured model {expected_model!r}")
    items = value["items"]
    if not isinstance(items, list) or len(items) > MAX_ITEMS:
        raise FlowError(f"{perspective}: items must be a list with at most {MAX_ITEMS} entries")
    seen: set[str] = set()
    for i, item in enumerate(items, 1):
        if not isinstance(item, dict):
            raise FlowError(f"{perspective}: item {i} must be a mapping")
        keys = {"item_id", "consideration", "target_section", "evidence_refs", "human_need", "human_question"}
        _validate_keys(item, keys, f"{perspective} item {i}")
        expected_id = f"{run_id}/{perspective}/{i:02d}"
        if item["item_id"] != expected_id or item["item_id"] in seen:
            raise FlowError(f"{perspective}: item IDs must be unique sequential generation-qualified IDs")
        seen.add(item["item_id"])
        for key in ("consideration", "human_question"):
            if not isinstance(item[key], str) or not item[key].strip():
                raise FlowError(f"{perspective}: {key} must be non-empty")
        if item["target_section"] not in sections:
            raise FlowError(f"{perspective}: invalid target_section {item['target_section']!r}")
        if item["human_need"] not in {"decision", "material", "both", "none"}:
            raise FlowError(f"{perspective}: invalid human_need")
        refs = item["evidence_refs"]
        if (not isinstance(refs, list) or any(not isinstance(x, str) for x in refs)
                or len(refs) != len(set(refs)) or any(x not in evidence_paths for x in refs)):
            raise FlowError(f"{perspective}: evidence_refs must be unique captured paths")
    return value


def _payload_inventory(payload: Path) -> list[dict[str, Any]]:
    try:
        root_st = payload.lstat()
    except FileNotFoundError as exc:
        raise FlowError(f"payload root missing: {payload}") from exc
    if stat.S_ISLNK(root_st.st_mode) or not stat.S_ISDIR(root_st.st_mode):
        raise FlowError(f"payload root must be a real directory: {payload}")
    rows: list[dict[str, Any]] = []
    for dirpath, dirnames, filenames in os.walk(payload, followlinks=False):
        dp = Path(dirpath)
        for name in dirnames:
            st = (dp / name).lstat()
            if stat.S_ISLNK(st.st_mode) or not stat.S_ISDIR(st.st_mode):
                raise FlowError(f"payload contains symlink/special directory: {dp / name}")
        for name in filenames:
            p = dp / name
            st = p.lstat()
            if stat.S_ISLNK(st.st_mode) or not stat.S_ISREG(st.st_mode):
                raise FlowError(f"payload contains symlink/special file: {p}")
            rel = p.relative_to(payload).as_posix()
            data = _read_stable(p, f"payload {rel}", MAX_ARTIFACT_BYTES if st.st_size <= MAX_ARTIFACT_BYTES else st.st_size)
            rows.append({"path": rel, "bytes": len(data), "sha256": _sha(data)})
    return sorted(rows, key=lambda row: row["path"].encode("utf-8"))


def _aggregate_digest(rows: list[dict[str, Any]]) -> str:
    h = hashlib.sha256()
    for row in rows:
        h.update(row["path"].encode("utf-8") + b"\0")
        h.update(str(row["bytes"]).encode("ascii") + b"\0")
        h.update(row["sha256"].encode("ascii") + b"\n")
    return h.hexdigest()


def _fsync_tree(payload: Path) -> None:
    dirs = []
    for dirpath, _dirnames, filenames in os.walk(payload):
        dp = Path(dirpath); dirs.append(dp)
        for name in filenames:
            fd = os.open(dp / name, os.O_RDONLY)
            try: os.fsync(fd)
            finally: os.close(fd)
    for dp in reversed(dirs):
        fd = os.open(dp, os.O_RDONLY)
        try: os.fsync(fd)
        finally: os.close(fd)


def _finalize(run_dir: Path, stage: str, run_id: str) -> str:
    if _interrupted:
        raise FlowError("interrupted before finalization")
    rows = _payload_inventory(run_dir / "payload")
    digest = _aggregate_digest(rows)
    marker = {
        "schema_version": 1, "stage": stage, "run_id": run_id,
        "tool_version": TOOL_VERSION, "completed_at": _utc_now(),
        "payload_digest_sha256": digest, "payload_files": rows,
    }
    _fsync_tree(run_dir / "payload")
    if _interrupted:
        raise FlowError("interrupted during finalization; incomplete state preserved")
    complete_path = run_dir / "COMPLETE.yaml"
    _write_exclusive(complete_path, _yaml_bytes(marker))
    fd = os.open(run_dir, os.O_RDONLY)
    try:
        os.fsync(fd)
    finally: os.close(fd)
    if _interrupted:
        _unlink_output(complete_path)
        raise FlowError("interrupted during finalization; incomplete state preserved")
    incomplete_path = run_dir / "INCOMPLETE"
    try:
        _unlink_output(incomplete_path)
    except OSError as exc:
        _unlink_output(complete_path)
        raise FlowError(f"could not remove INCOMPLETE; incomplete state preserved: {exc}") from exc
    if _interrupted:
        try:
            _write_exclusive(incomplete_path, b"incomplete\n")
            _unlink_output(complete_path)
        except OSError as exc:
            raise FlowError(f"interrupted during finalization; could not restore incomplete marker: {exc}") from exc
        raise FlowError("interrupted during finalization; incomplete state restored")
    fd = os.open(run_dir, os.O_RDONLY)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)
    if _interrupted:
        try:
            _write_exclusive(incomplete_path, b"incomplete\n")
            _unlink_output(complete_path)
        except OSError as exc:
            raise FlowError(f"interrupted after final fsync; could not restore incomplete marker: {exc}") from exc
        raise FlowError("interrupted after final fsync; incomplete state restored")
    return digest


def _accepted_bundle(run_dir: Path, stage: str, run_id: str) -> tuple[dict[str, Any], Path]:
    # Canonical layout is <work-root>/work/{contributions|curations}/<id>.
    # Deriving the root also keeps this validator independently testable.
    _assert_real_chain(run_dir.parents[2], run_dir)
    if run_dir.is_symlink() or not run_dir.is_dir():
        raise FlowError(f"{stage} bundle missing or not a real directory: {run_dir}")
    if (run_dir / "INCOMPLETE").exists() or (run_dir / "INCOMPLETE").is_symlink():
        raise FlowError(f"{stage} bundle is incomplete: {run_id}")
    marker_path = run_dir / "COMPLETE.yaml"
    raw = _read_stable(marker_path, "COMPLETE marker", MAX_ARTIFACT_BYTES)
    marker = _load_yaml_bytes(raw, "COMPLETE marker")
    if not isinstance(marker, dict):
        raise FlowError("COMPLETE marker must be a mapping")
    keys = {"schema_version", "stage", "run_id", "tool_version", "completed_at", "payload_digest_sha256", "payload_files"}
    _validate_keys(marker, keys, "COMPLETE marker")
    _schema_one(marker["schema_version"], "COMPLETE marker")
    if marker["stage"] != stage or marker["run_id"] != run_id:
        raise FlowError("COMPLETE marker stage/run/schema mismatch")
    if not isinstance(marker["tool_version"], str) or not isinstance(marker["completed_at"], str):
        raise FlowError("COMPLETE marker metadata malformed")
    listed = marker["payload_files"]
    if not isinstance(listed, list):
        raise FlowError("COMPLETE payload_files must be a list")
    normalized = []
    seen = set()
    for row in listed:
        if not isinstance(row, dict): raise FlowError("COMPLETE payload row malformed")
        _validate_keys(row, {"path", "bytes", "sha256"}, "COMPLETE payload row")
        rel = _safe_rel(row["path"], "COMPLETE payload path").as_posix()
        if rel in seen or not isinstance(row["bytes"], int) or isinstance(row["bytes"], bool) or row["bytes"] < 0 or not isinstance(row["sha256"], str) or not SHA_RE.fullmatch(row["sha256"]):
            raise FlowError("COMPLETE payload row values malformed or duplicate")
        seen.add(rel); normalized.append({"path": rel, "bytes": row["bytes"], "sha256": row["sha256"]})
    expected_sorted = sorted(normalized, key=lambda row: row["path"].encode("utf-8"))
    if normalized != expected_sorted:
        raise FlowError("COMPLETE payload_files is not canonically sorted")
    actual = _payload_inventory(run_dir / "payload")
    if actual != normalized:
        raise FlowError("COMPLETE payload inventory/bytes/hash mismatch")
    aggregate = _aggregate_digest(actual)
    if marker["payload_digest_sha256"] != aggregate:
        raise FlowError("COMPLETE aggregate digest mismatch")
    return marker, run_dir / "payload"


def _contribution_prompt(run_id: str, perspective: str, snapshot_digest: str,
                         sections: list[tuple[str, str]], evidence_paths: set[str],
                         payload: Path, model: str) -> str:
    base = (ROOT / "prompts" / "contribute.md").read_text(encoding="utf-8")
    section_ids = ", ".join(sid for sid, _ in sections)
    evidence_ids = ", ".join(sorted(evidence_paths, key=lambda value: value.encode("utf-8")))
    return (base + "\n\n---\n## Invocation contract\n"
            f"- Run ID: {run_id}\n- Perspective: {perspective}\n"
            f"- Required model_lineage value: {model}\n"
            f"- Captured bundle inventory sha256: {snapshot_digest}\n"
            f"- Captured snapshot directory: {payload / 'snapshot'}\n"
            f"- Allowed target_section values: {section_ids}\n"
            f"- Allowed evidence_refs values (exact, unique strings only): {evidence_ids}\n"
            f"- Required item_id prefix and sequence: {run_id}/{perspective}/01, /02, ...\n"
            "- Every item must include a non-empty human_question, including when human_need is none; "
            "for none, ask the operator to confirm or review the consideration.\n")


def contribute(args: argparse.Namespace) -> None:
    run_id, perspectives = args.run_id, args.perspectives
    _validate_id(run_id, "run ID")
    if not (2 <= len(perspectives) <= MAX_PERSPECTIVES):
        raise FlowError("contribute requires 2 to 5 explicit perspectives")
    if len(set(perspectives)) != len(perspectives):
        raise FlowError("duplicate perspective")
    unknown = set(perspectives) - PERSPECTIVES
    if unknown:
        raise FlowError(f"undefined perspective(s): {', '.join(sorted(unknown))}")
    work = Path.cwd().resolve()
    _ensure_dir_chain(work, work / "work" / "contribution-responses")
    run_dir = work / "work" / "contributions" / run_id
    _reserve(run_dir)
    payload = run_dir / "payload"
    snapshot_digest, sections, _manifest = _capture_snapshot(work, payload)
    inventory = _load_yaml_bytes((payload / "snapshot" / "inventory.yaml").read_bytes(), "inventory")
    evidence_paths = {row["path"] for row in inventory["files"]}
    perspectives_dir = payload / "perspectives"; _ensure_dir_chain(payload, perspectives_dir)
    model = os.environ.get("DOCLOOP_MODEL", "codex")
    started = _utc_now()
    envelopes = []
    for perspective in perspectives:
        raw = _run_model(_contribution_prompt(run_id, perspective, snapshot_digest, sections, evidence_paths, payload, model), model, work,
                         payload / "diagnostics", perspective, True)
        envelope = _validate_envelope(raw, run_id, perspective, {x[0] for x in sections}, evidence_paths, model)
        canonical = _yaml_bytes(envelope)
        _write_exclusive(perspectives_dir / f"{perspective}.yaml", canonical)
        # Incomplete runs retain bounded raw stdout for diagnosis. Accepted
        # runs retain only the validated, canonical perspective envelope.
        _unlink_output(payload / "diagnostics" / f".{perspective}.stdout")
        envelopes.append(envelope)
    items = sorted((item for env in envelopes for item in env["items"]), key=lambda x: x["item_id"].encode("utf-8"))
    if len({x["item_id"] for x in items}) != len(items):
        raise FlowError("contribution item IDs are not globally unique")
    index = {"schema_version": 1, "run_id": run_id, "items": items}
    index_bytes = _yaml_bytes(index)
    _write_exclusive(payload / "contribution-index.yaml", index_bytes)
    template = {
        "schema_version": 1, "source_run_id": run_id, "source_index_sha256": _sha(index_bytes),
        "operator_attested": False, "attested_by": "", "attested_at": "", "materials": [],
        "dispositions": [{"item_id": item["item_id"], "status": "open", "group_id": "", "decision": "", "material_refs": [], "rationale": ""} for item in items],
    }
    _write_exclusive(payload / "human-response.template.yaml", _yaml_bytes(template))
    run = {"schema_version": 1, "stage": "contribute", "run_id": run_id, "model_lineage": model,
           "perspectives": perspectives, "started_at": started, "finished_at": _utc_now(),
           "captured_inventory_sha256": snapshot_digest}
    _write_exclusive(payload / "run.yaml", _yaml_bytes(run))
    digest = _finalize(run_dir, "contribute", run_id)
    print(f"docloop: contribute complete id={run_id} items={len(items)} digest={digest}", file=sys.stderr)


def _load_contribution(work: Path, run_id: str) -> tuple[dict[str, Any], dict[str, Any], Path]:
    marker, payload = _accepted_bundle(work / "work" / "contributions" / run_id, "contribute", run_id)
    index_raw = _read_stable(payload / "contribution-index.yaml", "contribution index", MAX_ARTIFACT_BYTES)
    index = _load_yaml_bytes(index_raw, "contribution index")
    if not isinstance(index, dict): raise FlowError("contribution index malformed")
    _validate_keys(index, {"schema_version", "run_id", "items"}, "contribution index")
    _schema_one(index["schema_version"], "contribution index")
    if index["run_id"] != run_id or not isinstance(index["items"], list):
        raise FlowError("contribution index header malformed")
    ids = [x.get("item_id") for x in index["items"] if isinstance(x, dict)]
    if len(ids) != len(index["items"]) or len(ids) != len(set(ids)) or ids != sorted(ids, key=lambda x: x.encode("utf-8")):
        raise FlowError("contribution index does not have sorted one-to-one IDs")
    return marker, index, payload


def _validate_response(raw: bytes, path: Path, source_run_id: str, index: dict[str, Any], work: Path) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    response = _load_yaml_bytes(raw, "human response")
    if not isinstance(response, dict): raise FlowError("human response must be a mapping")
    keys = {"schema_version", "source_run_id", "source_index_sha256", "operator_attested", "attested_by", "attested_at", "materials", "dispositions"}
    _validate_keys(response, keys, "human response")
    index_bytes = _yaml_bytes(index)
    _schema_one(response["schema_version"], "human response")
    if response["source_run_id"] != source_run_id or response["source_index_sha256"] != _sha(index_bytes):
        raise FlowError("human response source binding mismatch")
    if response["operator_attested"] is not True or not isinstance(response["attested_by"], str) or not response["attested_by"].strip() or not isinstance(response["attested_at"], str) or not response["attested_at"].strip():
        raise FlowError("human response requires operator_attested: true, attested_by, and attested_at")
    if not isinstance(response["materials"], list) or len(response["materials"]) > MAX_MATERIALS:
        raise FlowError(f"materials must be a list with at most {MAX_MATERIALS} entries")
    materials = []
    material_ids: set[str] = set()
    inputs_path = work / "inputs"
    if inputs_path.is_symlink() or not inputs_path.is_dir():
        raise FlowError("inputs/ must be a real directory for supplemental materials")
    inputs = inputs_path.resolve()
    for i, entry in enumerate(response["materials"]):
        if not isinstance(entry, dict): raise FlowError(f"material {i}: must be a mapping")
        _validate_keys(entry, {"material_id", "path"}, f"material {i}")
        mid = entry["material_id"]
        if not isinstance(mid, str) or not MATERIAL_ID_RE.fullmatch(mid) or mid in material_ids:
            raise FlowError(f"material {i}: invalid or duplicate material_id")
        material_ids.add(mid)
        rel = _safe_rel(entry["path"], f"material {mid} path")
        candidate = work / Path(*rel.parts)
        if not _inside(candidate, inputs):
            raise FlowError(f"material {mid}: path must resolve inside inputs/")
        _regular_nosymlink(candidate, f"material {mid}")
        materials.append({"material_id": mid, "path": rel.as_posix(), "source": candidate})
    dispositions = response["dispositions"]
    if not isinstance(dispositions, list): raise FlowError("dispositions must be a list")
    source_items = {x["item_id"]: x for x in index["items"]}
    seen: set[str] = set(); disp_by_id = {}
    for i, disp in enumerate(dispositions):
        if not isinstance(disp, dict): raise FlowError(f"disposition {i}: must be a mapping")
        dkeys = {"item_id", "status", "group_id", "decision", "material_refs", "rationale"}
        _validate_keys(disp, dkeys, f"disposition {i}")
        iid = disp["item_id"]
        if iid not in source_items or iid in seen: raise FlowError("unknown or duplicate disposition item_id")
        seen.add(iid)
        if disp["status"] not in STATUSES: raise FlowError(f"{iid}: invalid status")
        gid = disp["group_id"]
        if not isinstance(gid, str) or (gid and not ID_RE.fullmatch(gid)): raise FlowError(f"{iid}: invalid group_id")
        for key in ("decision", "rationale"):
            if not isinstance(disp[key], str): raise FlowError(f"{iid}: {key} must be a string")
        refs = disp["material_refs"]
        if (not isinstance(refs, list) or any(not isinstance(x, str) for x in refs)
                or len(refs) != len(set(refs)) or any(x not in material_ids for x in refs)):
            raise FlowError(f"{iid}: unknown or duplicate material_refs")
        if disp["status"] == "decided" and not disp["decision"].strip(): raise FlowError(f"{iid}: decided requires decision")
        if disp["status"] == "supported" and not refs: raise FlowError(f"{iid}: supported requires material_refs")
        if disp["status"] in {"carried", "dismissed"} and not disp["rationale"].strip(): raise FlowError(f"{iid}: {disp['status']} requires rationale")
        disp_by_id[iid] = disp
    if seen != set(source_items): raise FlowError("every source item must appear exactly once in dispositions")
    # Operator grouping may only combine semantically identical rows.
    groups: dict[str, list[str]] = {}
    for iid, disp in disp_by_id.items():
        if disp["group_id"]: groups.setdefault(disp["group_id"], []).append(iid)
    for gid, ids in groups.items():
        signatures = set()
        for iid in ids:
            d, src = disp_by_id[iid], source_items[iid]
            signatures.add((d["status"], src["target_section"], d["decision"], tuple(d["material_refs"]), d["rationale"]))
        if len(signatures) != 1: raise FlowError(f"group {gid}: members have inconsistent status/section/decision/material/rationale")
    return response, materials


def _capture_materials(materials: list[dict[str, Any]], payload: Path, referenced: set[str]) -> tuple[dict[str, str], bytes]:
    total = 0; gathered = []
    for mat in materials:
        raw = _read_stable(mat["source"], f"material {mat['material_id']}", MAX_MATERIAL_BYTES)
        total += len(raw)
        if total > MAX_MATERIAL_BYTES: raise FlowError("supplemental material total byte cap exceeded")
        gathered.append((mat, raw, _sha(raw)))
    # Re-read before publication, detecting concurrent mutation.
    for mat, raw, digest in gathered:
        again = _read_stable(mat["source"], f"material {mat['material_id']}", MAX_MATERIAL_BYTES)
        if raw != again or digest != _sha(again): raise FlowError(f"material changed during capture: {mat['material_id']}")
    if gathered:
        _ensure_dir_chain(payload, payload / "supplemental-materials")
    by_sha: dict[str, dict[str, Any]] = {}
    id_to_sha = {}
    for mat, raw, digest in gathered:
        id_to_sha[mat["material_id"]] = digest
        if digest not in by_sha:
            _write_exclusive(payload / "supplemental-materials" / digest, raw, max_bytes=MAX_MATERIAL_BYTES)
            by_sha[digest] = {"sha256": digest, "bytes": len(raw), "stored_path": f"supplemental-materials/{digest}", "sources": []}
        by_sha[digest]["sources"].append({
            "material_id": mat["material_id"], "original_path": mat["path"],
            "original_basename": PurePosixPath(mat["path"]).name,
            "unused": mat["material_id"] not in referenced,
        })
    rows = []
    for digest in sorted(by_sha):
        row = by_sha[digest]
        row["sources"].sort(key=lambda x: x["material_id"].encode("utf-8")); rows.append(row)
    return id_to_sha, _yaml_bytes({"schema_version": 1, "materials": rows})


def _curation_outputs(curation_id: str, source_run_id: str, source_digest: str, response_digest: str,
                      created_at: str, index: dict[str, Any], response: dict[str, Any], id_to_sha: dict[str, str],
                      sections: list[tuple[str, str]]) -> tuple[bytes, bytes, bytes]:
    source_items = {x["item_id"]: x for x in index["items"]}
    section_order = {sid: i for i, (sid, _title) in enumerate(sections)}
    titles = dict(sections)
    content_items = []
    for disp in response["dispositions"]:
        src = source_items[disp["item_id"]]
        content_items.append({
            "item_id": disp["item_id"], "group_id": disp["group_id"], "perspective": src["item_id"].split("/")[1],
            "status": disp["status"], "target_section": src["target_section"], "consideration": src["consideration"],
            "human_question": src["human_question"], "decision": disp["decision"],
            "material_sha256": sorted({id_to_sha[x] for x in disp["material_refs"]}), "rationale": disp["rationale"],
        })
    content_items.sort(key=lambda x: (section_order[x["target_section"]], x["group_id"] == "", x["group_id"].encode("utf-8"), STATUS_ORDER[x["status"]], x["item_id"].encode("utf-8")))
    content = {"items": content_items}
    semantic_digest = _sha(_yaml_bytes(content))
    curation = {"schema_version": 1, "metadata": {"curation_id": curation_id, "source_run_id": source_run_id,
                "source_payload_digest_sha256": source_digest, "operator_response_sha256": response_digest,
                "created_at": created_at}, "semantic_digest_sha256": semantic_digest, "content": content}

    lines = ["# Curated drafting notes", "", f"- Source contribution digest: `{source_digest}`", f"- Operator response sha256: `{response_digest}`"]
    settled = [x for x in content_items if x["status"] in {"decided", "supported"}]
    for sid, title in sections:
        section_items = [x for x in settled if x["target_section"] == sid]
        if not section_items: continue
        lines += ["", f"## Section `{sid}`", "", f"- Section title: {json.dumps(title, ensure_ascii=False)}"]
        group_map: dict[str, list[dict[str, Any]]] = {}
        for item in section_items:
            key = item["group_id"] or item["item_id"]
            group_map.setdefault(key, []).append(item)
        for key in sorted(group_map, key=lambda x: x.encode("utf-8")):
            members = sorted(group_map[key], key=lambda x: x["item_id"].encode("utf-8")); first = members[0]
            lines += ["", f"### `{key}`", f"- Status: {first['status']}", f"- Decision: {json.dumps(first['decision'], ensure_ascii=False)}"]
            if first["material_sha256"]:
                lines.append("- Materials: " + ", ".join(f"`{x}`" for x in first["material_sha256"]))
            lines.append("- Provenance: " + ", ".join(f"`{x['item_id']}`" for x in members))
    opens = [x for x in content_items if x["status"] == "open"]
    if opens:
        lines += ["", "## Unresolved — not drafting evidence", ""]
        lines += [f"- `{x['item_id']}` — {json.dumps(x['human_question'], ensure_ascii=False)}" for x in opens]
    notes = ("\n".join(lines).rstrip("\n") + "\n").encode("utf-8")
    open_lines = ["# Open questions"]
    for sid, _title in sections:
        rows = [x for x in opens if x["target_section"] == sid]
        if rows:
            open_lines += ["", f"## Section `{sid}`", ""]
            open_lines += [f"- `{x['item_id']}` — {json.dumps(x['human_question'], ensure_ascii=False)}" for x in rows]
    open_questions = ("\n".join(open_lines).rstrip("\n") + "\n").encode("utf-8")
    return _yaml_bytes(curation), notes, open_questions


def curate(args: argparse.Namespace) -> None:
    _validate_id(args.source_run_id, "source run ID"); _validate_id(args.curation_id, "curation ID")
    work = Path.cwd().resolve()
    source_marker, index, source_payload = _load_contribution(work, args.source_run_id)
    response_path = Path(args.response)
    if not response_path.is_absolute(): response_path = work / response_path
    if response_path.resolve() == (source_payload / "human-response.template.yaml").resolve():
        raise FlowError("do not edit/use the bundle template directly; pass a copied response file")
    response_raw = _read_stable(response_path, "human response", MAX_ARTIFACT_BYTES)
    response, materials = _validate_response(response_raw, response_path, args.source_run_id, index, work)
    run_dir = work / "work" / "curations" / args.curation_id
    _reserve(run_dir); payload = run_dir / "payload"
    referenced = {x for d in response["dispositions"] for x in d["material_refs"]}
    print(f"docloop: material capture destination={run_dir} files={len(materials)}", file=sys.stderr)
    id_to_sha, materials_bytes = _capture_materials(materials, payload, referenced)
    # Revalidate exact response bytes after material capture.
    if _read_stable(response_path, "human response", MAX_ARTIFACT_BYTES) != response_raw:
        raise FlowError("human response changed during capture")
    _write_exclusive(payload / "human-response.yaml", response_raw)
    _write_exclusive(payload / "materials.yaml", materials_bytes)
    contribution_ref = {"schema_version": 1, "source_run_id": args.source_run_id,
                        "source_payload_digest_sha256": source_marker["payload_digest_sha256"],
                        "source_index_sha256": response["source_index_sha256"]}
    _write_exclusive(payload / "contribution-ref.yaml", _yaml_bytes(contribution_ref))
    manifest = _load_yaml_bytes((source_payload / "snapshot" / "manifest.yaml").read_bytes(), "snapshot manifest")
    policy = _load_yaml_bytes((source_payload / "snapshot" / "policy.yaml").read_bytes(), "snapshot policy")
    sections = _policy_sections(manifest, policy)
    created = _utc_now()
    curation_bytes, notes, open_questions = _curation_outputs(args.curation_id, args.source_run_id,
        source_marker["payload_digest_sha256"], _sha(response_raw), created, index, response, id_to_sha, sections)
    for name, data in (("curation.yaml", curation_bytes), ("draft-notes.md", notes), ("open-questions.md", open_questions)):
        _write_exclusive(payload / name, data)
    run = {"schema_version": 1, "stage": "curate", "run_id": args.curation_id, "source_run_id": args.source_run_id,
           "started_at": created, "finished_at": _utc_now()}
    _write_exclusive(payload / "run.yaml", _yaml_bytes(run))
    digest = _finalize(run_dir, "curate", args.curation_id)
    print(f"docloop: curate complete id={args.curation_id} digest={digest}", file=sys.stderr)


def draft_curated(args: argparse.Namespace) -> None:
    _validate_id(args.curation_id, "curation ID")
    work = Path.cwd().resolve()
    logical_work = os.environ.get("PWD", "")
    try:
        if not logical_work or not Path(logical_work).is_absolute() or not os.path.samefile(logical_work, work):
            logical_work = str(work)
    except OSError:
        logical_work = str(work)
    marker, payload = _accepted_bundle(work / "work" / "curations" / args.curation_id, "curate", args.curation_id)
    notes = _read_stable(payload / "draft-notes.md", "draft notes", MAX_ARTIFACT_BYTES)
    try: notes_text = notes.decode("utf-8")
    except UnicodeDecodeError as exc: raise FlowError("draft notes are not UTF-8") from exc
    if not notes_text.endswith("\n"): raise FlowError("draft notes must have a final newline")
    notes_digest = _sha(notes)
    draft = (ROOT / "prompts" / "draft.md").read_text(encoding="utf-8")
    prompt = draft + "\n\n---\n## Run context\n" + f"- Work folder: {logical_work}\n- docloop lib (scripts): {ROOT / 'lib'}\n"
    if (work / "manifest.yaml").is_file(): prompt += f"- Manifest: {logical_work}/manifest.yaml\n"
    # Bash command substitution in the existing draft path strips trailing
    # newlines from stage_prompt.  Mirror those exact bytes before appending.
    prompt = prompt.rstrip("\n")
    prompt += ("\n\n---\n## Verified optional curated input\n"
               f"- Curation ID: {args.curation_id}\n"
               f"- COMPLETE payload digest: {marker['payload_digest_sha256']}\n"
               f"- draft-notes sha256: {notes_digest}\n\n"
               "<docloop-curated-input>\n" + notes_text + "</docloop-curated-input>\n")
    model = os.environ.get("DOCLOOP_MODEL", "codex")
    # Draft stdout/stderr belong to the caller, matching the existing run_model path.
    global _active_child
    if _interrupted:
        raise FlowError("interrupted before draft model launch")
    child = subprocess.Popen(_model_command(model, prompt, False), cwd=work, start_new_session=True)
    _active_child = child
    try:
        while child.poll() is None:
            if _interrupted:
                _terminate_child_group(child)
                break
            time.sleep(0.02)
        if _interrupted:
            _terminate_child_group(child)
        rc = child.wait()
    finally:
        if child.poll() is None:
            _terminate_child_group(child)
        _active_child = None
    if _interrupted:
        raise FlowError("interrupted; draft model process group terminated and reaped")
    if rc != 0: raise FlowError(f"draft model exited nonzero ({rc})")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="contribution_flow.py")
    sub = parser.add_subparsers(dest="command", required=True)
    p = sub.add_parser("contribute"); p.add_argument("run_id"); p.add_argument("perspectives", nargs="+"); p.set_defaults(func=contribute)
    p = sub.add_parser("curate"); p.add_argument("source_run_id"); p.add_argument("curation_id"); p.add_argument("response"); p.set_defaults(func=curate)
    p = sub.add_parser("draft-curated"); p.add_argument("curation_id"); p.set_defaults(func=draft_curated)
    return parser


def main(argv: list[str] | None = None) -> int:
    os.umask(0o077); _install_signal_handlers()
    try:
        args = build_parser().parse_args(argv)
        args.func(args)
        return 0
    except FlowError as exc:
        print(f"docloop: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
