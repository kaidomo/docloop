#!/usr/bin/env python3
"""match_review_rounds.py -- finding comparison table between review rounds (CONTRACT §13).

Downstream port of the upstream generator (docauth skills/review-gate/scripts/
match_review_rounds.py). docloop's validator has required `round_context.comparison_ref`
since v0.13 whenever `input_gate.prior_round.exists` is true, but the tool that *produces*
that file was never ported -- so a second round meant hand-writing the table to match a
signature. This closes that gap.

**Not an automatic verdict.** It only looks at whether a previous round's ids reappear in
this round's text and, if they do, whether open/closed vocabulary sits near them. When no
verdict word is found the row stays `unknown`. A row marked `resolved` means only that the
previous id is absent from this round's text -- it is not evidence the defect was fixed,
and it does not distinguish a real fix from this round's lenses simply missing it.

**Disclosed limits (kept, not fixed -- upstream peer review r1-02 -> r2-01 -> r3-01/r3-02).**
Negation detection is a substring search that does not know clause or sentence boundaries,
so it can over-classify as open (an unrelated trigger falling inside the 30-character
lookback) and under-classify as closed (Korean negations shaped like "~지 않았다" are not in
the trigger list). Those are opposite failure directions, so a longer trigger list does not
end them; the real fix is a parser that knows clause boundaries, which is outside what a
human-read comparison table is for. The same root produced partial bypasses for three
consecutive rounds upstream, which is exactly the case CONTRACT §12.6 ⓔ answers with
"disclose the limit and stop widening" -- every verdict row already says heuristic, not
final, so this limit explains why that wording is there rather than opening new risk.

**The header line is deliberately Korean, and it is not a language choice.**
`# 라운드 대조 —` is the byte signature `validate_review_result.py` matches to accept a
comparison_ref, and docloop ported that validator as-is. Translating it would silently
break every receipt this generator exists to feed, so it is identical under every `--lang`.

**The verdict column has both of this repo's languages** (`--lang ko|en`, default `ko`),
the way the docs are paired as `<name>.md` / `<name>.ko.md`. The default is upstream's own
wording byte for byte, so a docloop table and a docauth table of the same rounds compare
directly; `--lang en` is docloop's translation for readers who want it. Nothing
machine-reads either set -- the validator matches the header and the file hash, never the
body -- so the test suite pins both as goldens.

Usage:
  match_review_rounds.py PREV_ROUND.md CURR_ROUND.md \\
      --prev-round N-1 --curr-round N [--lang ko|en] [--out TABLE.md]

Exit codes: 0 = table produced (regardless of how many rows carried/resolved/new).
1 = input error.
"""
import argparse
import re
import sys

CLOSED_WORDS = (
    "CLOSED", "closed", "닫힘", "해소", "종결", "반영", "resolved", "fixed",
    "없음", "없으며", "없습니다", "없다", "찾지 못했다", "발견하지 못했",
)
OPEN_WORDS = (
    "OPEN", "open", "remains", "잔여", "미종결", "여전히", "그대로", "부분",
    "partial", "still", "재발", "reopen",
)
# A fixed prefix list misses "never resolved" / "cannot be resolved" / "wasn't
# resolved", where a word sits between the negation and the stem. So the test is
# not prefix matching but whether a trigger appears in the short span *before* the
# stem. Triggers match as substrings, so the "non" in "nonresolved" is caught -- at
# the cost of occasionally over-excluding an unrelated "non" (as in "anonymous").
# For a table a human reads, being slow to call something closed is the safe
# direction; missing a close is cheaper than asserting one.
NEGATION_TRIGGERS = (
    "미", "비", "무", "불", "안 ",
    "not", "never", "cannot", "can't", "won't", "wasn't", "isn't", "aren't",
    "doesn't", "didn't", "no longer", "non", "un",
)
NEGATION_LOOKBACK = 30


def _round_id_pattern(round_no):
    # No trailing \b: Korean particles (은/는/이/가/과/의 ...) are part of Python's
    # Unicode \w, so "r6-01은" would not present a boundary and id extraction failed
    # outright. `\d+` is greedy and already consumes the full number, so dropping the
    # trailing boundary cannot over-match into a longer id.
    return re.compile(rf"\br{re.escape(str(round_no))}-\d+")


def _extract_ids(text, round_no):
    """Every id this round owns (`r{round_no}-\\d+`), in order of appearance, deduped."""
    pattern = _round_id_pattern(round_no)
    seen = []
    for match in pattern.finditer(text):
        token = match.group(0)
        if token not in seen:
            seen.append(token)
    return seen


def _id_token_index(text, id_token):
    """First position where id_token (e.g. `r3-01`) appears as an exact token.

    A plain substring search (`id_token in text`) would find `r3-01` inside `r3-010`
    and merge two different findings into one. Only positions with no digit following
    count; no leading boundary is required, for the same reason as in
    `_round_id_pattern` -- the token starts with `r`, so it cannot land mid-number."""
    pattern = re.compile(re.escape(id_token) + r"(?!\d)")
    match = pattern.search(text)
    return match.start() if match else -1


def _id_present(text, id_token):
    return _id_token_index(text, id_token) != -1


def _window_around(text, token, radius=200):
    """radius characters on each side of the token -- the verdict-word search window,
    anchored at the first exact-token occurrence."""
    idx = _id_token_index(text, token)
    if idx == -1:
        return ""
    start = max(0, idx - radius)
    end = min(len(text), idx + len(token) + radius)
    return text[start:end]


def _closed_word_occurrences(window, word):
    """For each occurrence of `word` in `window`, whether it is negated (list of bool).

    To catch "never resolved" / "cannot be resolved" / "wasn't resolved", where a word
    sits between the negation and the stem, the search covers the whole
    NEGATION_LOOKBACK span before the stem rather than the immediately preceding
    prefix. A negated occurrence is itself an *open* signal ("not resolved" = not
    closed yet): it does not merely discard the closed signal, it flips it."""
    lower_window = window.lower()
    lower_word = word.lower()
    negated_flags = []
    start = 0
    while True:
        idx = lower_window.find(lower_word, start)
        if idx == -1:
            break
        lookback = lower_window[max(0, idx - NEGATION_LOOKBACK):idx]
        negated_flags.append(any(trigger.lower() in lookback for trigger in NEGATION_TRIGGERS))
        start = idx + len(lower_word)
    return negated_flags


def _classify_mention(window):
    has_closed = False
    lower_window = window.lower()
    has_open = any(word.lower() in lower_window for word in OPEN_WORDS)
    for word in CLOSED_WORDS:
        for negated in _closed_word_occurrences(window, word):
            if negated:
                has_open = True
            else:
                has_closed = True
    if has_open and not has_closed:
        return "open"
    if has_closed and not has_open:
        return "closed_self_report"
    if has_open and has_closed:
        return "mixed"
    return "unknown"


def _new_ids_in_current(curr_text, curr_round, prev_ids):
    """Ids this round owns that do not mention a previous-round id inside their nearby
    window -- a candidate marker for "new", never a verdict. A wide window (300 chars,
    as it once was) drags an unrelated previous id from a distant paragraph into an
    independent new finding. Narrower is still a heuristic, so every result here
    assumes a human re-check."""
    curr_ids = _extract_ids(curr_text, curr_round)
    adjacent_ids = []
    for curr_id in curr_ids:
        window = _window_around(curr_text, curr_id, radius=120)
        references_prev = any(_id_present(window, prev_id) for prev_id in prev_ids)
        if references_prev:
            adjacent_ids.append(curr_id)
    new_ids = [cid for cid in curr_ids if cid not in adjacent_ids]
    return curr_ids, new_ids


def build_table(prev_text, curr_text, prev_round, curr_round):
    """Returns (rows, curr_ids, new_ids); rows carry the carried/resolved verdict per
    previous-round id."""
    prev_ids = _extract_ids(prev_text, prev_round)
    rows = []
    for prev_id in prev_ids:
        if not _id_present(curr_text, prev_id):
            rows.append((prev_id, "absent"))
            continue
        window = _window_around(curr_text, prev_id)
        rows.append((prev_id, _classify_mention(window)))
    curr_ids, new_ids = _new_ids_in_current(curr_text, curr_round, prev_ids)
    return rows, curr_ids, new_ids


# The verdicts and labels a human reads, in both of this repo's languages. `ko` is
# upstream's wording byte for byte, so a docloop table and a docauth table of the same
# rounds are directly comparable; `en` is docloop's translation. Nothing machine-reads
# these -- validate_review_result.py matches the header signature and the file hash,
# never the body -- so the goldens in the test suite are what keeps them from drifting.
VERDICTS = {
    "ko": {
        "open": "carried(open)",
        "closed_self_report": "carried(closed 자기언급 — 실제 종결은 신규 id 부재로 재확인)",
        "mixed": "carried(open) — 열림·닫힘 어휘 동시 등장, 사람 재확인 권장",
        "unknown": "불명",
        "absent": "resolved(미언급 — 사람 확인 필요)",
        "new_candidate": "신규 후보(근접 창 안에 이전 id 언급 없음)",
        "adjacent": "이전 id 인접 언급 있음(사람이 재확인)",
    },
    "en": {
        "open": "carried(open)",
        "closed_self_report": "carried(closed by self-report -- confirm the close by the id "
                              "being absent next round)",
        "mixed": "carried(open) -- open and closed words both present; a human should re-check",
        "unknown": "unknown",
        "absent": "resolved(not mentioned -- needs human confirmation)",
        "new_candidate": "new candidate (no previous-round id inside the nearby window)",
        "adjacent": "a previous id is mentioned nearby (human re-check)",
    },
}

LABELS = {
    "ko": {
        "preamble": [
            "**자동 판정 아님 — 사람이 볼 대조표.** `불명`은 스크립트가 열림/닫힘 어휘를",
            "찾지 못했다는 뜻이며, `resolved`는 이전 라운드 id가 이번 라운드 원문에",
            "없다는 사실만 의미한다(수정 확인이 아니라 미언급 확인).",
        ],
        "prev_heading": "## r{prev} 의 id {n}건 대조",
        "prev_header": "| id | 판정 |",
        "curr_heading": "## r{curr} 자체 id {total}건 중 신규 후보 {new}건",
        "curr_header": "| id | 판정(휴리스틱 — 확정 아님) |",
    },
    "en": {
        "preamble": [
            "**Not an automatic verdict -- a table for a human to read.** `unknown` means the",
            "script found no open/closed vocabulary near the id. `resolved` means only that the",
            "previous round's id is absent from this round's text: not-mentioned, not fixed.",
        ],
        "prev_heading": "## {n} ids from r{prev}",
        "prev_header": "| id | verdict |",
        "curr_heading": "## {new} new candidates among r{curr}'s own {total} ids",
        "curr_header": "| id | verdict (heuristic -- not final) |",
    },
}

LANGUAGES = tuple(VERDICTS)


def render_table(rows, curr_ids, new_ids, prev_round, curr_round, lang="ko"):
    """Render the table. `lang` selects the human-readable wording only.

    The header line is identical in every language on purpose: it is the byte signature
    `validate_review_result.py` matches to accept a `round_context.comparison_ref`, not
    prose. Translating it would break every receipt this generator exists to feed.
    """
    if lang not in VERDICTS:
        raise ValueError(f"unknown lang {lang!r} (expected one of {', '.join(LANGUAGES)})")
    verdicts, labels = VERDICTS[lang], LABELS[lang]
    lines = [
        f"# 라운드 대조 — r{prev_round} → r{curr_round}",
        "",
        *labels["preamble"],
        "",
        labels["prev_heading"].format(prev=prev_round, n=len(rows)),
        "",
        labels["prev_header"],
        "|---|---|",
    ]
    for prev_id, verdict_key in rows:
        lines.append(f"| `{prev_id}` | {verdicts[verdict_key]} |")
    lines.append("")
    lines.append(labels["curr_heading"].format(
        curr=curr_round, total=len(curr_ids), new=len(new_ids)))
    lines.append("")
    lines.append(labels["curr_header"])
    lines.append("|---|---|")
    for curr_id in curr_ids:
        mark = verdicts["new_candidate"] if curr_id in new_ids else verdicts["adjacent"]
        lines.append(f"| `{curr_id}` | {mark} |")
    lines.append("")
    return "\n".join(lines)


def main(argv=None):
    parser = argparse.ArgumentParser(
        description="finding comparison table between review rounds (CONTRACT §13)")
    parser.add_argument("prev_round_path")
    parser.add_argument("curr_round_path")
    parser.add_argument("--prev-round", required=True, type=int)
    parser.add_argument("--curr-round", required=True, type=int)
    parser.add_argument("--out", default=None, help="output path (stdout when omitted)")
    parser.add_argument(
        "--lang", choices=sorted(LANGUAGES), default="ko",
        help="wording of the verdict column (default: ko, upstream's own wording -- "
             "the header signature is identical either way)",
    )
    parser.add_argument(
        "--allow-non-adjacent",
        action="store_true",
        help="proceed even when curr-round != prev-round + 1 (warns instead of failing).",
    )
    args = parser.parse_args(argv)

    if args.curr_round != args.prev_round + 1:
        message = (
            f"rounds are not adjacent (--prev-round {args.prev_round}, "
            f"--curr-round {args.curr_round}) -- this table only means something for "
            "'the round immediately before -> this round'. Skipping a round drops that "
            "round's carried/resolved verdicts entirely."
        )
        if not args.allow_non_adjacent:
            print(f"ERROR: {message} Use --allow-non-adjacent to proceed anyway.", file=sys.stderr)
            return 1
        print(f"WARNING: {message}", file=sys.stderr)

    try:
        with open(args.prev_round_path, encoding="utf-8") as f:
            prev_text = f.read()
    except FileNotFoundError:
        print(f"ERROR: previous round file not found: {args.prev_round_path}", file=sys.stderr)
        return 1
    try:
        with open(args.curr_round_path, encoding="utf-8") as f:
            curr_text = f.read()
    except FileNotFoundError:
        print(f"ERROR: current round file not found: {args.curr_round_path}", file=sys.stderr)
        return 1

    rows, curr_ids, new_ids = build_table(prev_text, curr_text, args.prev_round, args.curr_round)
    table = render_table(rows, curr_ids, new_ids, args.prev_round, args.curr_round, args.lang)

    if args.out:
        with open(args.out, "w", encoding="utf-8") as f:
            f.write(table + "\n")
    else:
        print(table)
    return 0


if __name__ == "__main__":
    sys.exit(main())
