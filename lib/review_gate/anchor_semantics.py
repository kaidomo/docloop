#!/usr/bin/env python3
"""anchor_semantics.py -- the stable line anchor, shared by everything that resolves one.

Downstream port of the two functions the upstream summary axis imports from
`audit_quotes.py` (docauth#207 option 2). Only these two are ported: the rest of
`audit_quotes.py` is a quote re-collation gate that docloop states as an instruction to
the reviewer in `prompts/review.md` rather than as a machine check, and pulling the whole
CLI across would add an obligation this repo does not have. Extracting the semantics
instead keeps one definition of what an anchor means, which is the part every consumer
must agree on.

`A<12 hex>` is the anchor of a line's normalized content, so an anchor survives the
document being edited above it -- that is the whole point of the form. Normalization
collapses whitespace only: a line written with different spacing must produce the same
anchor, or an editor's reflow silently destroys evidence.
"""
from __future__ import annotations

import hashlib
import re

ANCHOR_HASH_LEN = 12
WS = re.compile(r"\s+")


def norm(text: str) -> str:
    """Normalize for comparison -- whitespace only; bold and punctuation are preserved."""
    return WS.sub(" ", text).strip()


def anchor_hash(text: str) -> str:
    """Line content -> stable identifier (docauth#207 option 2).

    Only ever applied to text that went through `norm()`: the same line written with
    different spacing has to yield the same anchor, or a difference in editing tools or
    hand transcription reads as evidence that vanished.
    """
    return "A" + hashlib.sha256(text.encode("utf-8")).hexdigest()[:ANCHOR_HASH_LEN]
