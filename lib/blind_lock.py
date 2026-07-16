#!/usr/bin/env python3
"""blind_lock.py — seal a prediction before the outcome exists; verify it after.

Ported (downstream) from the meta-learning-loop skill's B1 blind-diagnosis lock;
upstream canonical lives in the private skill repo (docuauthring, contracts/blind-diagnosis.yaml).
docloop ships only the lock/verify primitive — the full learning lifecycle (experiment
cards, lesson states, human gate) stays upstream; judgment stays with the human.

Why: writing has no oracle, so "I knew it" claims are cheap after the fact. The lock
makes a prediction falsifiable: hash the payload file BEFORE the outcome is revealed,
keep the digest OUTSIDE the hashed file (a sidecar — a digest inside the file it hashes
is circular), and re-hash at reveal time. Locking after the fact proves nothing; for
third-party verifiability, commit the payload+sidecar before the outcome exists.

Usage:
  blind_lock.py lock   <payload-file> [--locker NAME]   -> writes <payload-file>.lock.yaml
  blind_lock.py verify <payload-file> <sidecar>         -> exit 0 (intact) / 1 (mismatch)
Exit codes: 0 ok · 1 digest mismatch (verify) · 2 usage/missing/malformed · 3 sidecar already exists (lock)
"""
import sys, os, re, hashlib, datetime

import yaml

_HEADER = (
    "# B1 lock sidecar — lives OUTSIDE the hashed payload (a digest inside the file it\n"
    "# hashes is circular). Payload is immutable after this lock; additions go in a new\n"
    "# payload + new sidecar. For third-party verifiability, commit both before the reveal.\n"
)


def _sha256(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


def lock(payload, locker="unknown"):
    if not os.path.isfile(payload):
        print(f"blind_lock: no such payload file: {payload}", file=sys.stderr)
        return 2
    sidecar = payload + ".lock.yaml"
    digest = _sha256(payload)
    doc = {"schema_version": 1, "lock_sidecar": {
        "payload_ref": os.path.abspath(payload),
        "digest": digest,
        "algorithm": "sha256",
        "byte_length": os.path.getsize(payload),
        "lock_time": datetime.datetime.now().astimezone().isoformat(timespec="seconds"),
        "locker": locker,
    }}
    try:
        # O_CREAT|O_EXCL: atomic create — a concurrent or repeated lock cannot truncate the
        # original sidecar (re-lock would erase the original lock time; append a NEW payload instead).
        fd = os.open(sidecar, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o644)
    except FileExistsError:
        print(f"blind_lock: refusing to overwrite existing sidecar: {sidecar} "
              "(a re-lock would erase the original lock time — append a NEW payload instead)",
              file=sys.stderr)
        return 3
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        f.write(_HEADER)
        yaml.safe_dump(doc, f, allow_unicode=True, sort_keys=False)
    print(f"locked: {payload}\n  sha256 {digest}\n  sidecar {sidecar}")
    return 0


def verify(payload, sidecar):
    for p in (payload, sidecar):
        if not os.path.isfile(p):
            print(f"blind_lock: no such file: {p}", file=sys.stderr)
            return 2
    try:
        with open(sidecar, encoding="utf-8") as f:
            doc = yaml.safe_load(f)
        recorded = doc["lock_sidecar"]["digest"]
    except Exception as e:  # malformed sidecar is a usage error, not a tamper verdict
        print(f"blind_lock: malformed sidecar ({e.__class__.__name__}): {sidecar}", file=sys.stderr)
        return 2
    algo = doc["lock_sidecar"].get("algorithm")
    if algo != "sha256":
        print(f"blind_lock: unsupported/missing algorithm in sidecar: {algo!r}", file=sys.stderr)
        return 2
    if not isinstance(recorded, str) or not re.fullmatch(r"[0-9a-f]{64}", recorded):
        print(f"blind_lock: sidecar digest is not a sha256 hex string: {recorded!r}", file=sys.stderr)
        return 2
    actual = _sha256(payload)
    if actual == recorded:
        print(f"verified: payload intact (sha256 {actual})")
        return 0
    print(f"MISMATCH: payload was modified after lock\n  recorded {recorded}\n  actual   {actual}\n"
          "-> do not judge the prediction; record the run as diagnostic-only.", file=sys.stderr)
    return 1


def _usage():
    print(__doc__.strip(), file=sys.stderr)
    return 2


def main(argv):
    if not argv:
        return _usage()
    cmd, rest = argv[0], argv[1:]
    if cmd == "lock":
        locker = "unknown"
        if "--locker" in rest:
            i = rest.index("--locker")
            if i + 1 >= len(rest):  # a flag with no value is a usage error, not a silent default
                print("blind_lock: --locker requires a value", file=sys.stderr)
                return 2
            locker = rest[i + 1]
            rest = rest[:i] + rest[i + 2:]
        if len(rest) != 1:
            return _usage()
        return lock(rest[0], locker)
    if cmd == "verify" and len(rest) == 2:
        return verify(rest[0], rest[1])
    return _usage()


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
