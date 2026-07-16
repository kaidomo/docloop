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
"""
import sys, os, hashlib, datetime


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
    if os.path.exists(sidecar):
        print(f"blind_lock: refusing to overwrite existing sidecar: {sidecar} "
              "(a re-lock would erase the original lock time — append a NEW payload instead)",
              file=sys.stderr)
        return 3
    digest = _sha256(payload)
    body = (
        "# B1 lock sidecar — lives OUTSIDE the hashed payload (a digest inside the file it\n"
        "# hashes is circular). Payload is immutable after this lock; additions go in a new\n"
        "# payload + new sidecar. For third-party verifiability, commit both before the reveal.\n"
        "schema_version: 1\n"
        "lock_sidecar:\n"
        f"  payload_ref: \"{os.path.abspath(payload)}\"\n"
        f"  digest: \"{digest}\"\n"
        "  algorithm: sha256\n"
        f"  byte_length: {os.path.getsize(payload)}\n"
        f"  lock_time: \"{datetime.datetime.now().astimezone().isoformat(timespec='seconds')}\"\n"
        f"  locker: \"{locker}\"\n"
    )
    with open(sidecar, "w", encoding="utf-8") as f:
        f.write(body)
    print(f"locked: {payload}\n  sha256 {digest}\n  sidecar {sidecar}")
    return 0


def verify(payload, sidecar):
    for p in (payload, sidecar):
        if not os.path.isfile(p):
            print(f"blind_lock: no such file: {p}", file=sys.stderr)
            return 2
    recorded = None
    with open(sidecar, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line.startswith("digest:"):
                recorded = line.split(":", 1)[1].strip().strip('"')
                break
    if not recorded:
        print("blind_lock: sidecar has no digest field", file=sys.stderr)
        return 2
    actual = _sha256(payload)
    if actual == recorded:
        print(f"verified: payload intact (sha256 {actual})")
        return 0
    print(f"MISMATCH: payload was modified after lock\n  recorded {recorded}\n  actual   {actual}\n"
          "-> do not judge the prediction; record the run as diagnostic-only.", file=sys.stderr)
    return 1


def main(argv):
    if len(argv) >= 2 and argv[0] == "lock":
        locker = "unknown"
        args = [a for a in argv[1:] if a != "--locker"]
        if "--locker" in argv[1:]:
            i = argv.index("--locker")
            if i + 1 < len(argv):
                locker = argv[i + 1]
                args = argv[1:i] + argv[i + 2:]
        if len(args) == 1:
            return lock(args[0], locker)
    if len(argv) == 3 and argv[0] == "verify":
        return verify(argv[1], argv[2])
    print(__doc__.strip(), file=sys.stderr)
    return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
