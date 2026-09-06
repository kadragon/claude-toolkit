#!/usr/bin/env python3
"""backlog_candidates.py — deterministic candidate-group parser for task-next Step 1.

Usage:
  python3 backlog_candidates.py --tasks PATH --backlog PATH [--full-scan] [--json]
  python3 backlog_candidates.py --test

  --tasks PATH    path to tasks.md (optional file; absent path is treated as empty input,
                   same as the `2>/dev/null` grep fallback it replaces)
  --backlog PATH  path to backlog.md (required — this repo's prereqs guarantee it exists,
                   so a missing/unreadable file here is treated as a real error, not "no candidates")
  --full-scan     run the full-scan algorithm instead of the fast path (see below)
  --json          emit a JSON array of candidate objects instead of the plain-text list

Output (plain text, one line per candidate, in the algorithm's selection order):
  [N] <source>: <heading> (<M> items)
  h1 sprint blocks (tasks.md Phase A) omit the "(<M> items)" suffix — they are a scope
  announcement, not an item-counted group.

Empty result (stdout stays empty / `[]`; a diagnosis goes to STDERR): zero candidates has
three very different meanings and silently printing nothing conflates them — the queue is
genuinely clear, every open item is parked, or the file uses a shape this parser cannot see
(prose bullets instead of `- [ ]`, or items sitting above the first heading). Only the first
means "no work". The other two are false negatives that read as "nothing to do" while real
work sits in the file, so `zero_candidate_diagnosis()` names which one applies per source
file. Diagnostics go to stderr specifically so `--json` stdout stays machine-parseable.

Parsing semantics (must match `SKILL.md` Step 1 prose exactly — this script does not
reinterpret it):

  HTML comments: `<!-- ... -->` spans are blanked out before tokenizing (line count preserved,
  so line numbers stay grep-compatible). Format templates parked in a comment — e.g. the
  `## Feature Name` / `- [ ] Simplest case` block harness-init seeds `backlog.md` with — are
  markup, not work, and must never be reported as candidates.

  Fenced code blocks: ```-fenced and ~~~-fenced spans are blanked the same way, immediately after
  the comment pass. A `#`/`##`/`###` line in a code sample is markup too, and a fake heading does
  more than pollute the listing — it truncates the enclosing region (see "Directly owns" below),
  so a real `- [ ]` sitting after the fence stops counting toward its actual heading. Openers are
  3+ backticks/tildes indented at most 3 spaces; a closer needs the same character and at least
  the opener's length; an unclosed fence runs to EOF.

  Phase A (tasks.md h1 sprint blocks): an `# ` heading is a candidate if `status: open`
  is the FIRST `status:` line whose line number falls strictly between this h1 and the next
  h1 (or EOF) — NOT literally the next line in the file. Body content commonly sits between
  the heading and its status line.

  Blocked/deferred marker exclusion (applies everywhere "direct open items" are counted): an
  otherwise-open `- [ ]` item whose text contains a `*(deferred: ...)*` or
  `*(blocked by: <n>-<slug>)*` marker is excluded from the open-item count entirely — same
  effect as if it were `[>]`. A heading whose open items are ALL marked is therefore not a
  candidate (matches `SKILL.md` Step 2 "Deferred items"/"blocked" rules); a heading with a mix
  of marked and unmarked open items is still a candidate, counting only the unmarked ones.
  The marker counts wherever it sits in a multi-line item: `tokenize` folds an item's indented
  continuation lines into its text, so a marker on a wrapped item's last line is seen — including
  when an indented fenced block or HTML comment sits between the item and its marker.

  "Directly owns" (Phase B/C, full-scan rules 2-5): open `- [ ]` checkbox items collected
  from just after a heading up to (not including) the next heading of ANY level 1-3 — not
  just the next heading of the same or broader level. A checkbox sitting after a nested h3
  child does NOT count toward that h3's h2 parent.

  tasks.md contributes h1 sprint blocks and NOTHING else. It is the Sprint Contract, deleted
  whole at sprint close, so no queue lives there — `## Review Backlog` findings now go to
  backlog.md (`harness-init/references/backlog-template.md`). An older tasks.md that still holds
  such a section is reported by `tasks_persistent_sections()` as a migration warning on stderr,
  never selected as a candidate.

  Phase B (backlog.md, FAST PATH ONLY): top-to-bottom, TYPE-AGNOSTIC scan collecting up to 2
  qualifying h2-or-h3 headings in raw document order (h2/h3 interleaved as they appear).
  Skipped entirely if Phase A already produced FAST_PATH_CAP (3) candidates. The fast-path
  total across Phase A + B is capped at FAST_PATH_CAP (3) — matched to `AskUserQuestion`'s
  4-option cap once the mandatory "더 많은 항목 보기" option is appended (3 candidates + 1 = 4).
  A truncation (fewer candidates shown than a full scan would find) is signalled to the
  orchestrator via `truncation_note()`, written to stderr in fast-path mode only — see below.

  Full scan is a DIFFERENT algorithm from Phase B, not a superset call to the same helper:
  rule 1 = all qualifying tasks.md h1 blocks (Phase A, uncapped); rule 2 = ALL qualifying
  backlog.md h3 headings, in document order among h3s only; rule 3 = ALL qualifying backlog.md
  h2 headings, in document order among h2s only. Rules 2 and 3 apply TYPE PRIORITY (all h3
  first, then all h2) — this is NOT the same ordering as Phase B's raw document order across
  types, and the two must stay separate functions (`fast_path()` / `full_scan()`) rather than
  one shared helper, or the divergence silently disappears.

Self-check (--test):
  Exercises the status-line-gap case (Phase A), the direct-items heading-boundary case
  (nested h3 item must not count toward its h2 parent), the all-parked-skip case (every item
  `[x]`/`[>]` under a heading is not a candidate), Phase-B limit truncation (cap 3 total
  across A+B) and `truncation_note` in both directions (truncated -> note, not truncated ->
  None), the Phase-B-vs-full-scan ordering divergence on backlog.md h2/h3
  interleaving, the tasks.md findings-section guard (never a candidate in either algorithm;
  reported by `tasks_persistent_sections`, and a healthy `## Scope` does not trip it),
  and the blocked/deferred-marker exclusion case (all-marked heading is not a
  candidate; mixed marked+unmarked heading counts only the unmarked items), and the
  HTML-comment case (a commented-out template heading + item is not a candidate, and line
  numbers of the real content after it are unshifted), and the fenced-code-block case (a fenced
  `## Fake` between a heading and its items neither becomes a candidate nor truncates the real
  heading's region; tilde fences, info strings, longer-closer nesting, unclosed fences, the
  4-space-indent non-fence, and the comments-before-fences ordering all covered), and the
  multi-line-item case (a marker on a wrapped item's indented continuation line parks the item,
  and still counts across an indented fenced block or HTML comment nested in the item; an
  unindented or blank-line-separated line is not folded in; the token keeps its FIRST line
  number). All fixtures are
  in-memory strings — no real files touched. Exits 0 on PASS, 1 on FAIL.
"""

from __future__ import annotations

import json
import math
import re
import sys

# ---------------------------------------------------------------------------
# Pure-function core (testable without real filesystem)
# ---------------------------------------------------------------------------

_HEADING_RE = re.compile(r"^(#{1,3})\s+(.*)$")
_STATUS_RE = re.compile(r"^status:\s*(\S+)")
_CHECKBOX_RE = re.compile(r"^-\s*\[([ xX>])\]\s*(.*)$")
# Generalized skip marker: `*(deferred: ...)*` or `*(blocked by: <n>-<slug>)*` — both mean
# "otherwise-open item is not actually actionable yet", same treatment as a `[>]` checkbox.
# Group 1 is the keyword and group 2 the payload (the reason, or the blocker's `<n>-<slug>`).
# `_is_blocked` inspects the payload; `task_nodes.orphaned_blockers` selects `blocked by` markers by
# the keyword and resolves their payload against the items a prune run deleted.
_BLOCK_MARKER_RE = re.compile(r"\*\(\s*(deferred|blocked by)\s*:(.*?)\)\*", re.IGNORECASE)
# `<slug>`, `<n>-<slug>` — a payload that is NOTHING BUT angle-bracket placeholders and separators
# is a documentation example of the syntax, not a blocker anyone can resolve; an item describing
# the marker format in its own prose would otherwise park itself forever, with no blocker to
# clear. A payload that merely embeds a placeholder (`3-migrate-to-<v2>-api`) still names a real
# blocker and must stay parked, so the test is "no alphanumeric survives stripping the
# placeholders", not "contains a placeholder".
_MARKER_PLACEHOLDER_RE = re.compile(r"<[^<>]*>")


def _is_placeholder_payload(payload: str) -> bool:
    """True if `payload` names no resolvable blocker — only `<placeholders>` and separators."""
    return not any(c.isalnum() for c in _MARKER_PLACEHOLDER_RE.sub(" ", payload))
# HTML comments hold format templates (`## Feature Name` / `- [ ] Simplest case`) that must
# never surface as candidates.
_HTML_COMMENT_RE = re.compile(r"<!--.*?-->", re.DOTALL)
# Fenced code blocks hold command samples and markdown examples — same problem as comments, but
# fences are line-anchored (a span regex is the wrong tool). Up to 3 leading spaces per
# CommonMark; 4+ makes it an indented code block's content, not a fence opener.
_FENCE_RE = re.compile(r"^ {0,3}(`{3,}|~{3,})(.*)$")
# A plain list bullet that is NOT a checkbox. Never used for candidate selection — it exists
# only so an empty result can tell "this heading has no work" apart from "this heading's work
# is written as prose bullets, which the selector cannot see".
_BULLET_RE = re.compile(r"^\s*[-*+]\s+\S")

# h2 titles shipped tooling used to append to tasks.md, before findings moved to backlog.md.
# Presence of one in tasks.md is a migration signal, never a candidate source. Matched as a
# PREFIX: security-overview writes `## Security Fixes — <repo-name>`.
PERSISTENT_SECTION_TITLES = ("Review Backlog", "Security Fixes")

# The fast path's combined Phase A + Phase B cap. Matched to `AskUserQuestion`'s 4-option cap
# once the mandatory "더 많은 항목 보기" option (SKILL.md Step 1) is appended: 3 candidates + 1 = 4.
FAST_PATH_CAP = 3

# The Sprint Contract's own sections (lowercased). These own open `- [ ]` items legitimately —
# `## Acceptance criteria` is checkboxes by definition — so they are the exception to "an h2 with
# open items in tasks.md is misplaced queue content".
SPRINT_SECTION_TITLES = frozenset(
    {
        "scope",
        "acceptance criteria",
        "out of scope",
        "evaluator feedback",
        "covers",
        "lint/test command",
    }
)


def _is_blocked(text: str) -> bool:
    """True if `text` (the checkbox item body) carries a *resolvable* deferred/blocked-by marker.

    A `blocked by` marker whose payload is nothing but angle-bracket placeholders is prose quoting
    the syntax — an item about the marker format, or a template — so it parks nothing. Without this, an
    item that merely documents `*(blocked by: <slug>)*` is filtered out of candidate selection
    permanently, because there is no blocker to land and no marker anyone would think to clear.

    The escape hatch is scoped to `blocked by` on purpose. A `deferred` payload is free prose that
    may legitimately contain `<v2>` or `<team>`; treating those as placeholders would un-park an
    item somebody deliberately deferred, which is the opposite failure.
    """
    return any(
        not (m.group(1).lower() == "blocked by" and _is_placeholder_payload(m.group(2)))
        for m in _BLOCK_MARKER_RE.finditer(text)
    )


def _strip_html_comments(text: str) -> str:
    """Blank out `<!-- ... -->` spans, preserving line count so token line numbers stay 1-based.

    The trailing-newline guard is the same class of fix as `_scan_fenced_blocks`'s `+ "\\n" if out`
    below, for the same reason: an N-line match holds only N-1 newlines, so when the match ends at
    true EOF with nothing after it, the `"\\n" * count` replacement is one separator short and a
    bare `.sub` drops the file's last line. The guard goes on the INPUT because neither
    output-side variant works — spelling both out, since each fails on a different input and
    picking between them by inspection is how this bug got written in the first place:

      - append unconditionally after `.sub`: fixes the EOF case, but inflates every input that
        already ends in a newline ("a\\n" -> 2 lines, "```\\n" -> 2).
      - append only when the result lacks a trailing newline: never fires, because the
        substituted text already ends in one — so the EOF case stays broken (3 lines, want 4).

    Normalizing the input is correct for both classes at once. Guard the empty string, whose
    line count is already 0.
    """
    if text and not text.endswith("\n"):
        text += "\n"
    return _HTML_COMMENT_RE.sub(lambda m: "\n" * m.group(0).count("\n"), text)


def _scan_fenced_blocks(text: str) -> tuple[str, int | None]:
    """Blank out fenced code blocks, preserving line count so token line numbers stay 1-based.

    A `#`/`##`/`###` line inside a ```-fenced sample is markup, not a heading — and treating it as
    one corrupts *selection*, not just reporting: `_region_end` boundaries on the next level-1..3
    heading, so a fake heading truncates the enclosing region and a real `- [ ]` after the fence
    stops counting toward its actual heading.

    Fence rules follow CommonMark where it matters here: an opener is 3+ backticks or 3+ tildes
    indented at most 3 spaces (4+ spaces is indented-code content, not a fence); a closer must use
    the same character, run at least as long as the opener, and carry nothing but whitespace after
    it. An unclosed fence runs to EOF. The delimiter lines are blanked too — they are never
    headings, status lines, or checkboxes, so nothing is lost.

    Returns `(blanked_text, unclosed_opener_line)`. The second element is the whole point of the
    tuple: an unclosed fence is CommonMark-correct to blank through EOF, but an odd number of
    ``` is an ordinary hand-editing typo, and swallowing the rest of a backlog silently is the
    exact false-negative class `zero_candidate_diagnosis` exists to prevent — yet invisible to it,
    since the swallowed headings never become tokens. Callers surface it (see `main`).
    """
    out = []
    fence_char = None
    fence_len = 0
    opened_at = None
    for line in text.splitlines():
        if fence_char is None:
            m = _FENCE_RE.match(line)
            # CommonMark forbids a backtick in a BACKTICK fence's info string (tilde fences allow
            # it). Without this guard an ordinary prose line like ```foo`bar reads as an unclosed
            # opener and blanks the file through EOF — precisely the silent candidate loss this
            # function exists to prevent. Openers only; the closer branch below already requires
            # nothing but whitespace after the run.
            if m and not (m.group(1)[0] == "`" and "`" in m.group(2)):
                fence_char = m.group(1)[0]
                fence_len = len(m.group(1))
                opened_at = len(out) + 1   # 1-based line of this opener
                out.append("")
                continue
            out.append(line)
            continue
        # Inside a fence: only a same-char run at least as long as the opener, with nothing but
        # whitespace after it, closes. A shorter or differently-charactered run is just content.
        m = _FENCE_RE.match(line)
        if m and m.group(1)[0] == fence_char and len(m.group(1)) >= fence_len and not m.group(2).strip():
            fence_char = None
            fence_len = 0
            opened_at = None
        out.append("")
    # The trailing "\n" is required, not cosmetic: when an unclosed fence blanks the file's LAST
    # line, `out` ends in "" and a bare `"\n".join(out)` loses exactly that line on the caller's
    # `.splitlines()` — silently breaking the line-count contract in the one case with no content
    # after the fence to make the drift visible. Guard the empty input, whose join is already "".
    blanked = "\n".join(out) + "\n" if out else ""
    return blanked, opened_at


def _strip_fenced_blocks(text: str) -> str:
    """`_scan_fenced_blocks` without the unclosed-opener line — for callers that only want text."""
    return _scan_fenced_blocks(text)[0]


def unclosed_fence_line(text: str) -> int | None:
    """1-based line of an unbalanced fence opener in `text`, or None.

    Applies the same comments-then-fences order `tokenize` uses, so a ``` parked inside an HTML
    comment is not reported as an unbalanced fence.
    """
    return _scan_fenced_blocks(_strip_html_comments(text))[1]


def tokenize(text: str) -> list[dict]:
    """Classify each line into a typed token: heading / status / checkbox / bullet.

    Unrecognized lines (body prose) are dropped — only heading/status/checkbox matter for
    candidate detection, and every selector filters on `type`, so the diagnosis-only `bullet`
    token is inert to them. Line numbers are 1-based to match grep -n output.

    `<!-- ... -->` spans and fenced code blocks are blanked out first: template/example markup,
    whether parked in a comment or shown in a code sample, is not real content, so it must not
    produce headings or checkbox items.

    Comments are stripped BEFORE fences, and the order is load-bearing: a doc template parked in a
    comment often contains a lone ``` opener, which — if fences ran first — would open a phantom
    unclosed fence and blank the rest of the file.

    A checkbox item wrapped across several lines keeps its indented continuation lines: they are
    folded into the token's `text` (single-space joined), so `_is_blocked` sees the whole item.
    Without this a `*(deferred: ...)*` marker parked on a wrapped item's last line is invisible and
    the item surfaces as an actionable candidate — the exact false positive the marker exists to
    prevent. The token's `line` stays the FIRST line, so grep -n style reporting is unchanged.
    Only indented, non-blank lines continue an item; a blank line, a heading, a status line, a
    checkbox, or a nested bullet ends it (CommonMark lazy continuation is deliberately not
    supported — an unindented line is far more likely to be new prose than a wrapped item).
    The indent test reads the RAW line, so an indented fenced block or HTML comment nested inside
    an item does not close it even though the strippers blank those lines out.
    """
    tokens: list[dict] = []
    open_item: dict | None = None
    raw_lines = text.splitlines()
    for i, line in enumerate(_strip_fenced_blocks(_strip_html_comments(text)).splitlines(), start=1):
        m = _HEADING_RE.match(line)
        if m:
            open_item = None
            tokens.append(
                {"type": "heading", "level": len(m.group(1)), "title": m.group(2).strip(), "line": i}
            )
            continue
        m = _STATUS_RE.match(line)
        if m:
            open_item = None
            tokens.append({"type": "status", "value": m.group(1), "line": i})
            continue
        m = _CHECKBOX_RE.match(line)
        if m:
            open_item = {"type": "checkbox", "state": m.group(1), "text": m.group(2), "line": i}
            tokens.append(open_item)
            continue
        if _BULLET_RE.match(line):
            open_item = None
            tokens.append({"type": "bullet", "line": i})
            continue
        if open_item is not None:
            # Indentation is read from the RAW line, not the masked one: a fenced sample or an HTML
            # comment nested under an item is blanked to "" by the strippers, and testing the masked
            # line would read that as a blank separator and close the item — dropping a
            # `*(deferred: ...)*` marker that follows the block. Folding still uses the masked text,
            # so the code inside the fence never becomes item text.
            raw = raw_lines[i - 1] if i <= len(raw_lines) else ""
            if raw[:1].isspace() and raw.strip():
                if line.strip():
                    open_item["text"] = f"{open_item['text']} {line.strip()}".strip()
                continue
        open_item = None
    return tokens


def _headings(tokens: list[dict], levels: tuple[int, ...] = (1, 2, 3)) -> list[dict]:
    return [t for t in tokens if t["type"] == "heading" and t["level"] in levels]


def _region_end(heading: dict, all_headings: list[dict]) -> float:
    """Line of the next level-1..3 heading after `heading`, or infinity — the region boundary."""
    for h in all_headings:
        if h["line"] > heading["line"]:
            return h["line"]
    return math.inf


def _direct_open_items(tokens: list[dict], heading: dict, all_headings: list[dict]) -> list[dict]:
    """Open checkbox items between `heading` and the next heading of ANY level 1-3.

    all_headings must be the full sorted (by line) list of level-1..3 headings in the same
    token stream — this is what makes a nested h3's items NOT count toward its h2 parent.
    """
    end = _region_end(heading, all_headings)
    items = [
        t
        for t in tokens
        if t["type"] == "checkbox" and heading["line"] < t["line"] < end
    ]
    return [t for t in items if t["state"] == " " and not _is_blocked(t["text"])]


# ---- Phase A: tasks.md h1 sprint blocks ------------------------------------------------

def phase_a_candidates(tasks_tokens: list[dict]) -> list[dict]:
    """h1 blocks whose FIRST status: line strictly between this h1 and the next h1 is 'open'."""
    h1s = _headings(tasks_tokens, (1,))
    result = []
    for idx, h in enumerate(h1s):
        end = h1s[idx + 1]["line"] if idx + 1 < len(h1s) else math.inf
        status_value = None
        for t in tasks_tokens:
            if t["type"] == "status" and h["line"] < t["line"] < end:
                status_value = t["value"]
                break
        if status_value == "open":
            result.append(
                {"source": "tasks.md", "kind": "h1", "title": h["title"], "line": h["line"], "items": None}
            )
    return result


# ---- Migration check: persistent sections that no longer belong in tasks.md -------------

def _is_persistent_title(title: str) -> bool:
    """Prefix match, so `Security Fixes — my-webapp` counts as `Security Fixes`."""
    stripped = title.strip()
    return any(
        stripped == known or stripped.startswith(known + " ")
        for known in PERSISTENT_SECTION_TITLES
    )


def tasks_persistent_sections(tasks_tokens: list[dict]) -> list[dict]:
    """Headings in tasks.md that hold content meant to outlive the sprint.

    THE single definition of "persistent section". `task_nodes.py` imports this to decide whether
    `prune-tasks` may touch the file, and the CLI below uses it to warn — one predicate, so the
    warning and the refusal can never disagree about the same file. Two guards drifting apart is
    worse than either alone: the warning says "move these or they will be lost" while the pruner,
    not recognising the shape, deletes the h1 block to EOF and unlinks the file.

    `tasks.md` is the Sprint Contract and nothing else — it is deleted whole at sprint close, so
    anything meant to outlive the sprint must live in `backlog.md`. Older repos wrote
    `## Review Backlog` findings here. Those are NOT candidates any more, but dropping them
    silently would hide real queued work, so they are reported instead.

    Takes tokens, not raw text, so fenced code blocks and HTML comments are already masked: a
    `## Review Backlog` inside a ```markdown example is documentation, and blocking cleanup on it
    would be a false refusal with no override.

    A Sprint Contract's own `## Scope` / `## Acceptance criteria` own open checkboxes too, so a
    bare "h2 with open items" test would fire on every healthy sprint. Two narrower tests instead,
    either of which qualifies a heading:

      1. its title is one tooling is known to have written (`PERSISTENT_SECTION_TITLES`), or an
         h3 nested under such a heading — this catches findings appended AFTER the sprint h1, the
         placement that used to destroy them, and fires whether or not the items are still open;
      2. it owns open items and neither it nor its parent h2 is one of the Sprint Contract's own
         sections (`SPRINT_SECTION_TITLES`) — this catches an ad-hoc grab-bag heading anywhere in
         the file, whichever side of the sprint h1 it sits on.
    """
    headings = _headings(tasks_tokens)
    coarse = [h for h in headings if h["level"] in (1, 2)]
    known_end = None  # end line of the persistent section currently open, for its h3 children
    parent_is_sprint_section = False
    result = []
    for h in headings:
        if h["level"] in (1, 2) and known_end is not None and h["line"] >= known_end:
            known_end = None
        if h["level"] in (1, 2):
            parent_is_sprint_section = (
                h["level"] == 2 and h["title"].strip().lower() in SPRINT_SECTION_TITLES
            )
        if h["level"] == 2 and _is_persistent_title(h["title"]):
            known_end = _region_end(h, coarse)
            result.append({"title": h["title"], "line": h["line"], "level": 2})
            continue
        if h["level"] == 3 and known_end is not None and h["line"] < known_end:
            result.append({"title": h["title"], "line": h["line"], "level": 3})
            continue
        if (
            h["level"] in (2, 3)
            and h["title"].strip().lower() not in SPRINT_SECTION_TITLES
            and not (h["level"] == 3 and parent_is_sprint_section)
            and _direct_open_items(tasks_tokens, h, headings)
        ):
            result.append({"title": h["title"], "line": h["line"], "level": h["level"]})
    return result


# ---- Phase C: backlog.md fast-path (type-agnostic, document order) --------------------

def backlog_fast_candidates(backlog_tokens: list[dict], limit: int | None = None) -> list[dict]:
    headings = _headings(backlog_tokens)
    result = []
    for h in headings:
        if h["level"] in (2, 3):
            open_items = _direct_open_items(backlog_tokens, h, headings)
            if open_items:
                result.append(
                    {
                        "source": "backlog.md",
                        "kind": f"h{h['level']}",
                        "title": h["title"],
                        "line": h["line"],
                        "items": len(open_items),
                    }
                )
                if limit is not None and len(result) >= limit:
                    break
    return result


# ---- Full-scan rules 4+5: backlog.md, type-priority (all h3, then all h2) -------------

def backlog_h3_candidates(backlog_tokens: list[dict]) -> list[dict]:
    headings = _headings(backlog_tokens)
    result = []
    for h in headings:
        if h["level"] == 3:
            open_items = _direct_open_items(backlog_tokens, h, headings)
            if open_items:
                result.append(
                    {"source": "backlog.md", "kind": "h3", "title": h["title"], "line": h["line"], "items": len(open_items)}
                )
    return result


def backlog_h2_candidates(backlog_tokens: list[dict]) -> list[dict]:
    headings = _headings(backlog_tokens)
    result = []
    for h in headings:
        if h["level"] == 2:
            open_items = _direct_open_items(backlog_tokens, h, headings)
            if open_items:
                result.append(
                    {"source": "backlog.md", "kind": "h2", "title": h["title"], "line": h["line"], "items": len(open_items)}
                )
    return result


# ---- Orchestrators ----------------------------------------------------------------------

def fast_path(tasks_tokens: list[dict], backlog_tokens: list[dict]) -> list[dict]:
    """Phase A (uncapped) + Phase B (<=2, skipped if A already >= FAST_PATH_CAP), truncated to
    FAST_PATH_CAP total."""
    result: list[dict] = []
    result.extend(phase_a_candidates(tasks_tokens))
    if len(result) < FAST_PATH_CAP:
        result.extend(backlog_fast_candidates(backlog_tokens, limit=2))
    return result[:FAST_PATH_CAP]


def full_scan(tasks_tokens: list[dict], backlog_tokens: list[dict]) -> list[dict]:
    """Rules 1-3 in order, uncapped. Rules 2+3 are type-priority (all h3, then all h2) —
    a genuinely different ordering from Phase B's type-agnostic document order."""
    result: list[dict] = []
    result.extend(phase_a_candidates(tasks_tokens))
    result.extend(backlog_h3_candidates(backlog_tokens))
    result.extend(backlog_h2_candidates(backlog_tokens))
    return result


def format_candidates(candidates: list[dict]) -> list[str]:
    lines = []
    for i, c in enumerate(candidates, start=1):
        if c["kind"] == "h1":
            lines.append(f"[{i}] {c['source']}: {c['title']}")
        else:
            lines.append(f"[{i}] {c['source']}: {c['title']} ({c['items']} items)")
    return lines


def _prose_only_headings(tokens: list[dict]) -> list[dict]:
    """Headings that directly own prose bullets but not one checkbox — invisible work.

    This must be judged PER HEADING, not per file. A file-wide "are there any checkboxes?"
    test is wrong on the common real shape: a backlog whose `## Completed` section is full of
    `- [x]` while its open section is written as prose tickets. That file has plenty of
    checkboxes, so a global test reports "all done" — a confidently wrong answer, worse than
    silence, on exactly the case this diagnosis exists to catch.
    """
    headings = _headings(tokens)
    drifted = []
    for h in headings:
        end = _region_end(h, headings)
        has_box = any(
            t["type"] == "checkbox" and h["line"] < t["line"] < end for t in tokens
        )
        has_bullet = any(
            t["type"] == "bullet" and h["line"] < t["line"] < end for t in tokens
        )
        if has_bullet and not has_box:
            drifted.append(h)
    return drifted


def _diagnose_source(label: str, tokens: list[dict]) -> list[str]:
    """Reasons `label` contributed no candidates — empty list if it has nothing to say."""
    headings = _headings(tokens)
    boxes = [t for t in tokens if t["type"] == "checkbox"]
    bullets = [t for t in tokens if t["type"] == "bullet"]
    if not headings and not boxes and not bullets:
        return []

    msgs = []
    drifted = _prose_only_headings(tokens)
    if drifted:
        shown = ", ".join(f"{h['title']!r} (line {h['line']})" for h in drifted[:3])
        more = f", +{len(drifted) - 3} more" if len(drifted) > 3 else ""
        msgs.append(
            f"{label}: {len(drifted)} heading(s) own prose bullets but no `- [ ]` item — "
            f"{shown}{more}. Only checkbox lines are selectable, and `####`-or-deeper headings "
            "are not headings to this parser (their items attribute to the enclosing "
            "`#`–`###` heading)."
        )

    open_boxes = [t for t in boxes if t["state"] == " "]
    actionable = [t for t in open_boxes if not _is_blocked(t["text"])]
    first = min((h["line"] for h in headings), default=None)
    # "Unattributed" means literally above the first heading — every item after one sits in
    # some region. Do not describe an attributed-but-unselected item as unattributed: that is
    # a false statement about the file, and the reader acts on it.
    stray = [t for t in actionable if first is None or t["line"] < first]
    attributed = [t for t in actionable if t not in stray]
    if stray:
        where = (
            f" (that heading is at line {first})"
            if first
            else " (the file has no `#`–`###` heading at all)"
        )
        # Say `#`–`###` explicitly: a `####` line may well sit above the item and look like a
        # heading to the reader, so "no heading above it" would be false as written.
        msgs.append(
            f"{label}: {len(stray)} actionable item(s) sit above the first `#`–`###` "
            f"heading{where} — only levels 1–3 count here, so a `####`-or-deeper line above an "
            "item does not attribute it."
        )
    if attributed:
        # The reachability gap differs per file — naming tasks.md's causes while diagnosing
        # backlog.md would be a false statement about the file in front of the reader.
        cause = (
            "tasks.md contributes h1 `status: open` sprint blocks only — items under any `##`/"
            "`###` there are reachable by no rule and belong in backlog.md"
            if label == "tasks.md"
            else "the backlog.md rules cover h2/h3 groups only, so items under a top-level "
            "`# ` heading are reachable by no rule"
        )
        msgs.append(
            f"{label}: {len(attributed)} actionable item(s) are attributed to a heading but no "
            f"phase selected them — {cause}."
        )
    if not actionable:
        # Only reachable when nothing actionable exists, so these never contradict the
        # stray/attributed lines above.
        if open_boxes:
            msgs.append(
                f"{label}: {len(open_boxes)} open item(s), all parked by a "
                "`*(blocked by: …)*` / `*(deferred: …)*` marker — clear a blocker to make one "
                "actionable."
            )
        elif boxes and not drifted:
            msgs.append(f"{label}: no open items (all `[x]`/`[>]`).")
    return msgs


def truncation_note(shown: int, total: int) -> str | None:
    """One-line stderr note when the fast path's FAST_PATH_CAP hid additional candidates.

    Pure: `shown` is the count `fast_path()` actually returned, `total` is the count a full
    scan would find. Both algorithms qualify a heading by the same test (see module docstring),
    so `total` is a real count, not an estimate — `main()` gets it by calling `full_scan()` on
    the already-tokenized files (no extra file I/O), and calls this only in fast-path mode, never
    with `--full-scan`. Returns None when nothing was hidden (`total <= shown`).
    """
    if total <= shown:
        return None
    hidden = total - shown
    return (
        f"Note: fast path is showing {shown} of {total} candidate group(s) — {hidden} more "
        'exist. Say how many more when offering "더 많은 항목 보기"; run --full-scan to see them all.'
    )


def zero_candidate_diagnosis(tasks_tokens: list[dict], backlog_tokens: list[dict]) -> list[str]:
    """Human-readable reasons an otherwise-valid run produced zero candidates.

    No `--full-scan` reachability probe here: since tasks.md stopped contributing h2/h3 groups,
    both algorithms qualify a heading by the same test and differ only in cap and ordering, so a
    fast path that found nothing proves the full scan finds nothing. Re-adding a tasks.md-only
    full-scan rule would make "fast path found nothing" and "there is nothing" different facts
    again — restore the probe with it.
    """
    lines = []
    for label, tokens in (("tasks.md", tasks_tokens), ("backlog.md", backlog_tokens)):
        lines.extend(_diagnose_source(label, tokens))
    if not lines:
        return ["No candidates: tasks.md and backlog.md have no headings or items at all."]
    return ["No candidates found. Why:"] + [f"  - {m}" for m in lines]


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def _read_file(path: str | None, *, required: bool = False) -> str:
    """Read `path`, or return "" if absent — matching the `2>/dev/null` grep fallback
    this script replaces. `required=True` (backlog.md) treats a missing/unreadable file
    as fatal instead of silently returning "" — tasks.md stays optional (required=False),
    per task-next' own "absent in the idle state" semantics.
    """
    if not path:
        if required:
            sys.stderr.write("Error: --backlog PATH is required\n")
            sys.exit(1)
        return ""
    try:
        with open(path, "r", encoding="utf-8") as fh:
            return fh.read()
    except OSError as e:
        if required:
            sys.stderr.write(f"Error: could not read required file {path}: {e}\n")
            sys.exit(1)
        return ""


def main(argv: list[str]) -> int:
    if "--test" in argv:
        return run_tests()

    tasks_path = None
    backlog_path = None
    full_scan_flag = False
    json_flag = False

    i = 0
    while i < len(argv):
        a = argv[i]
        if a in ("--tasks", "--backlog") and i + 1 >= len(argv):
            sys.stderr.write(f"Error: {a} requires a path argument\n")
            sys.exit(1)
        if a == "--tasks":
            tasks_path = argv[i + 1]
            i += 2
        elif a == "--backlog":
            backlog_path = argv[i + 1]
            i += 2
        elif a == "--full-scan":
            full_scan_flag = True
            i += 1
        elif a == "--json":
            json_flag = True
            i += 1
        else:
            i += 1

    tasks_raw = _read_file(tasks_path)
    backlog_raw = _read_file(backlog_path, required=True)
    tasks_tokens = tokenize(tasks_raw)
    backlog_tokens = tokenize(backlog_raw)

    # Unbalanced-fence warning, emitted even when candidates WERE found. A stray odd ``` blanks
    # everything after it, which can hide part of the queue while other groups still surface — so
    # gating this on a zero result would miss the partial case entirely, and `zero_candidate_
    # diagnosis` cannot see it either (the swallowed headings never become tokens). stderr keeps
    # `--json` stdout machine-parseable, same split the diagnosis already uses.
    for label, raw in ((tasks_path or "tasks.md", tasks_raw), (backlog_path or "backlog.md", backlog_raw)):
        n = unclosed_fence_line(raw)
        if n is not None:
            sys.stderr.write(
                f"Warning: unbalanced fence opened at line {n} in {label} — everything after it "
                "was treated as code and is not selectable. Close or remove the fence.\n"
            )

    # Migration warning, emitted even when candidates WERE found. tasks.md no longer contributes
    # anything but h1 sprint blocks, so a leftover findings section is real queued work that this
    # run cannot see — reporting it beats dropping it silently. `prune-tasks` refuses on the same
    # shape, so the two scripts agree on what needs moving.
    stale = tasks_persistent_sections(tasks_tokens)
    if stale:
        shown = ", ".join(f"{s['title']!r} (line {s['line']})" for s in stale[:3])
        more = f", +{len(stale) - 3} more" if len(stale) > 3 else ""
        sys.stderr.write(
            f"Warning: {tasks_path or 'tasks.md'} holds {len(stale)} persistent section(s) that "
            f"belong in backlog.md — {shown}{more}. tasks.md is the Sprint Contract only and is "
            "deleted at sprint close; move these to backlog.md verbatim or they will be lost and "
            "are not selectable here.\n"
        )

    candidates = full_scan(tasks_tokens, backlog_tokens) if full_scan_flag else fast_path(tasks_tokens, backlog_tokens)

    # Truncation signal — fast-path mode only, never with --full-scan (which is already the
    # uncapped total, so shown == total there and the note would be a no-op anyway). Reuses the
    # tasks_tokens/backlog_tokens already parsed above — no extra file I/O.
    if not full_scan_flag:
        total = len(full_scan(tasks_tokens, backlog_tokens))
        note = truncation_note(len(candidates), total)
        if note is not None:
            sys.stderr.write(note + "\n")

    if json_flag:
        print(json.dumps(candidates))
    else:
        for line in format_candidates(candidates):
            print(line)

    # Zero candidates is ambiguous — say which kind of empty this is, on stderr so `--json`
    # stdout stays machine-parseable. Exit code stays 0: an empty queue is not an error.
    if not candidates:
        for line in zero_candidate_diagnosis(tasks_tokens, backlog_tokens):
            sys.stderr.write(line + "\n")
    return 0


# ---------------------------------------------------------------------------
# Self-check (--test) — never touches real files
# ---------------------------------------------------------------------------

PASS_COUNT = 0
FAIL_COUNT = 0


def _assert(condition: bool, label: str) -> None:
    global PASS_COUNT, FAIL_COUNT
    if condition:
        PASS_COUNT += 1
        print(f"  PASS: {label}")
    else:
        FAIL_COUNT += 1
        print(f"  FAIL: {label}")


def run_tests() -> int:
    global PASS_COUNT, FAIL_COUNT
    PASS_COUNT = 0
    FAIL_COUNT = 0

    print("=== backlog_candidates.py --test ===\n")

    # ---- Test 1: status-line-gap case (Phase A) ----
    print("Test 1: phase_a_candidates — status line separated from h1 by body content")
    tasks_gap = """# Sprint one

Some body prose here.
More context about the sprint.

status: open

## Review Backlog
"""
    tokens = tokenize(tasks_gap)
    result = phase_a_candidates(tokens)
    _assert(
        result == [{"source": "tasks.md", "kind": "h1", "title": "Sprint one", "line": 1, "items": None}],
        "status: open several lines after h1 still matches (gap case)",
    )

    tasks_gap_active = """# Sprint two

status: active
"""
    result = phase_a_candidates(tokenize(tasks_gap_active))
    _assert(result == [], "status: active h1 is not a candidate")

    tasks_two_h1 = """# Sprint A
status: done

# Sprint B
some body
status: open
"""
    result = phase_a_candidates(tokenize(tasks_two_h1))
    _assert(
        [c["title"] for c in result] == ["Sprint B"],
        "status line correctly bound to nearest h1 (not leaking across the next h1 boundary)",
    )

    # ---- Test 2: direct-items heading-boundary case ----
    print("\nTest 2: direct-items boundary — nested h3 item must not count toward h2 parent")
    nested = """## Parent
### Child
- [ ] child item
"""
    tokens = tokenize(nested)
    headings = _headings(tokens)
    parent = next(h for h in headings if h["title"] == "Parent")
    child = next(h for h in headings if h["title"] == "Child")
    parent_items = _direct_open_items(tokens, parent, headings)
    child_items = _direct_open_items(tokens, child, headings)
    _assert(parent_items == [], "item after nested h3 does not count toward h2 parent")
    _assert(len(child_items) == 1, "item after nested h3 counts toward the h3 itself")

    fast = backlog_fast_candidates(tokens)
    _assert(
        [c["title"] for c in fast] == ["Child"],
        "Parent excluded (0 direct items), Child included (1 direct item)",
    )

    # ---- Test 3: all-parked-skip case ----
    print("\nTest 3: all-parked-skip — every item [x]/[>] under a heading is not a candidate")
    parked = """## Parked group
- [x] done item
- [>] deferred item
"""
    tokens = tokenize(parked)
    result = backlog_fast_candidates(tokens)
    _assert(result == [], "heading with only [x]/[>] items is not a candidate")

    mixed = """## Mixed group
- [x] done item
- [ ] open item
"""
    tokens = tokenize(mixed)
    result = backlog_fast_candidates(tokens)
    _assert(
        result == [{"source": "backlog.md", "kind": "h2", "title": "Mixed group", "line": 1, "items": 1}],
        "heading with >=1 open item alongside parked items is a candidate, counting only open items",
    )

    # ---- Test 3b: blocked/deferred-marker exclusion ----
    print("\nTest 3b: blocked/deferred-marker exclusion — generalizes the [>] skip to inline markers")
    all_deferred = """## Deferred group
- [ ] item one *(deferred: waiting on infra)*
- [ ] item two *(deferred: waiting on infra)*
"""
    tokens = tokenize(all_deferred)
    result = backlog_fast_candidates(tokens)
    _assert(result == [], "heading whose only open items are ALL *(deferred: ...)* is not a candidate")

    all_blocked = """## Blocked group
- [ ] item one *(blocked by: 3-add-auth)*
"""
    tokens = tokenize(all_blocked)
    result = backlog_fast_candidates(tokens)
    _assert(result == [], "heading whose only open item is *(blocked by: <n>-<slug>)* is not a candidate")

    mixed_blocked = """## Mixed blocked group
- [ ] item one *(blocked by: 3-add-auth)*
- [ ] item two
"""
    tokens = tokenize(mixed_blocked)
    result = backlog_fast_candidates(tokens)
    _assert(
        result == [{"source": "backlog.md", "kind": "h2", "title": "Mixed blocked group", "line": 1, "items": 1}],
        "heading with one blocked + one unblocked open item is a candidate, counting only the unblocked item",
    )

    _assert(_is_blocked("plain item, no marker") is False, "_is_blocked is False for unmarked text")
    _assert(_is_blocked("item *(deferred: reason)*") is True, "_is_blocked is True for deferred marker")
    _assert(_is_blocked("item *(blocked by: 2-slug)*") is True, "_is_blocked is True for blocked-by marker")
    _assert(
        _is_blocked("item *(deferred: waiting on (infra) service)*") is True,
        "_is_blocked is True when the reason text has nested parens (non-greedy match, not [^)]*)",
    )
    _assert(
        _is_blocked("item quoting the `*(blocked by: <slug>)*` syntax") is False,
        "_is_blocked is False for a placeholder payload — prose about the marker parks nothing",
    )
    _assert(
        _is_blocked("template line *(blocked by: <n>-<slug>)*") is False,
        "_is_blocked is False for the `<n>-<slug>` template payload",
    )
    _assert(
        _is_blocked("item *(blocked by: <slug>)* *(blocked by: 3-add-auth)*") is True,
        "one real marker still parks an item that also quotes a placeholder one",
    )
    _assert(
        _is_blocked("item *(blocked by: 3-migrate-to-<v2>-api)*") is True,
        "REGRESSION: a real slug that merely embeds a placeholder still parks the item — the "
        "exemption is for a payload that is nothing BUT placeholders",
    )
    _assert(
        _is_blocked("item *(deferred: waiting on <team> reply)*") is True,
        "REGRESSION: the placeholder escape hatch is scoped to `blocked by` — a real deferral with "
        "angle brackets in its reason stays parked",
    )
    placeholder_prose = """## Marker docs
- [ ] [HARNESS] Warn when a pruned item is still named by a `*(blocked by: <slug>)*` marker
"""
    _assert(
        [c["title"] for c in backlog_fast_candidates(tokenize(placeholder_prose))] == ["Marker docs"],
        "an item whose prose quotes the placeholder marker is still a candidate",
    )

    # ---- Test 3b2: multi-line items — marker on a continuation line still parks the item ----
    print("\nTest 3b2: multi-line items — a marker on a wrapped item's continuation line counts")
    wrapped_deferred = """## Wrapped group
- [ ] Verify the endpoint against real traffic, then replace the whole-body substring
  match with a proper response parse and let absence mean "cancelled".
  *(deferred: needs a captured real response)*
- [ ] Confirm one real booking appears on page 0, then drop the UNVERIFIED marker
  from the doc comment. *(deferred: needs one real booking)*
"""
    tokens = tokenize(wrapped_deferred)
    result = backlog_fast_candidates(tokens)
    _assert(result == [], "heading whose wrapped open items are ALL deferred is not a candidate")

    checkboxes = [t for t in tokens if t["type"] == "checkbox"]
    _assert(len(checkboxes) == 2, "wrapped continuation lines do not create extra checkbox tokens")
    _assert(
        [t["line"] for t in checkboxes] == [2, 5],
        "a folded item keeps the line number of its FIRST line",
    )

    wrapped_mixed = """## Wrapped mixed group
- [ ] wrapped item one
  continues here *(deferred: waiting on infra)*
- [ ] wrapped item two
  continues here with no marker
"""
    tokens = tokenize(wrapped_mixed)
    result = backlog_fast_candidates(tokens)
    _assert(
        result == [{"source": "backlog.md", "kind": "h2", "title": "Wrapped mixed group", "line": 1, "items": 1}],
        "only the unmarked wrapped item counts toward the open-item total",
    )

    unindented_after = """## Boundary group
- [ ] wrapped item
  still the item
unindented prose *(deferred: not part of the item)*
"""
    tokens = tokenize(unindented_after)
    item = next(t for t in tokens if t["type"] == "checkbox")
    _assert(
        item["text"] == "wrapped item still the item",
        "an unindented following line is prose, not a lazy continuation — it is not folded in",
    )

    blank_break = """## Blank-break group
- [ ] wrapped item

  *(deferred: separated by a blank line)*
"""
    tokens = tokenize(blank_break)
    item = next(t for t in tokens if t["type"] == "checkbox")
    _assert(item["text"] == "wrapped item", "a blank line ends the item — later indented text is not folded in")

    nested_fence = """## Fenced group
- [ ] item with a fenced sample
  ```sh
  echo hi
  ```
  *(deferred: waiting on a captured response)*
"""
    tokens = tokenize(nested_fence)
    item = next(t for t in tokens if t["type"] == "checkbox")
    _assert(
        item["text"] == "item with a fenced sample *(deferred: waiting on a captured response)*",
        "an indented fenced block nested in an item does not close it — the marker after it still folds in",
    )
    _assert(
        backlog_fast_candidates(tokens) == [],
        "an item deferred after a nested fenced block is not surfaced as actionable",
    )

    nested_comment = """## Commented group
- [ ] item with a nested comment
  <!-- an aside -->
  *(deferred: waiting)*
"""
    item = next(t for t in tokenize(nested_comment) if t["type"] == "checkbox")
    _assert(
        item["text"] == "item with a nested comment *(deferred: waiting)*",
        "an indented HTML comment nested in an item does not close it either",
    )

    unindented_fence = """## Top-level fence group
- [ ] item then a top-level fence
```sh
echo hi
```
  *(deferred: not part of the item)*
"""
    item = next(t for t in tokenize(unindented_fence) if t["type"] == "checkbox")
    _assert(
        item["text"] == "item then a top-level fence",
        "an UNindented fence still ends the item — only nested (indented) blocks continue it",
    )

    # ---- Test 3c: HTML-comment stripping ----
    print("\nTest 3c: HTML comments — commented-out template markup is not a candidate")
    commented_template = """# Backlog

Ordered by priority.

<!--
## Feature Name
> Goal: what and why.

- [ ] Simplest case
- [ ] Next case builds on previous
-->

## Real group
- [ ] real item
"""
    tokens = tokenize(commented_template)
    result = backlog_fast_candidates(tokens)
    _assert(
        [c["title"] for c in result] == ["Real group"],
        "heading + items inside <!-- --> are ignored; only real content is a candidate",
    )
    _assert(
        result[0]["line"] == 13,
        "line numbers after a stripped comment are unshifted (comment blanked, not deleted)",
    )

    inline_comment = """## <!-- draft --> Live group
- [ ] item one <!-- - [ ] ghost item -->
"""
    tokens = tokenize(inline_comment)
    result = backlog_fast_candidates(tokens)
    _assert(
        result == [{"source": "backlog.md", "kind": "h2", "title": "Live group", "line": 1, "items": 1}],
        "single-line comments are stripped in place without dropping the surrounding line",
    )

    # ---- Test 3d: fenced code blocks ----
    print("\nTest 3d: fenced code blocks — headings/items inside a fence are markup, not work")

    # The finding's named fixture: a fenced `## Fake` sits BETWEEN a heading and its items. The
    # bug is not cosmetic — a fake heading truncates the enclosing region (_region_end), so the
    # real item after the fence stops counting toward `Real group` and the group silently drops.
    fenced_between = """## Real group

Some prose before the sample.

```markdown
## Fake heading in a sample
- [ ] fake item
```

- [ ] real item after the fence
"""
    tokens = tokenize(fenced_between)
    result = backlog_fast_candidates(tokens)
    _assert(
        result == [{"source": "backlog.md", "kind": "h2", "title": "Real group", "line": 1, "items": 1}],
        "a fenced `## Fake` between a heading and its items neither becomes a candidate nor "
        "truncates the real heading's region",
    )
    _assert(
        not any(t["type"] == "heading" and t["title"].startswith("Fake") for t in tokens),
        "no heading token is emitted for a `##` line inside a fence",
    )
    _assert(
        [t["line"] for t in tokens if t["type"] == "checkbox"] == [10],
        "line numbers after a stripped fence are unshifted (fence blanked, not deleted)",
    )

    tilde_and_info = """## Tilde group
~~~python
## Fake in a tilde fence
- [ ] ghost
~~~
- [ ] real item
"""
    result = backlog_fast_candidates(tokenize(tilde_and_info))
    _assert(
        result == [{"source": "backlog.md", "kind": "h2", "title": "Tilde group", "line": 1, "items": 1}],
        "`~~~` fences carrying an info string are stripped just like backtick fences",
    )

    # A closing fence must be at least as long as its opener, so the shorter inner fences here
    # do NOT close the outer one — otherwise the sample's own `## Fake` leaks back out.
    nested_fence = """## Four group
````
```
## Fake inside a shorter inner fence
```
````
- [ ] real item
"""
    result = backlog_fast_candidates(tokenize(nested_fence))
    _assert(
        result == [{"source": "backlog.md", "kind": "h2", "title": "Four group", "line": 1, "items": 1}],
        "a shorter inner fence does not close a longer outer fence",
    )

    unclosed_fence = """## Open group
- [ ] real item
```
## Fake after an unclosed fence
- [ ] ghost item
"""
    result = backlog_fast_candidates(tokenize(unclosed_fence))
    _assert(
        result == [{"source": "backlog.md", "kind": "h2", "title": "Open group", "line": 1, "items": 1}],
        "an unclosed fence blanks through EOF, so nothing after it is selectable",
    )

    # REGRESSION GUARD — a 4-space-indented ``` is an indented code block's content, not a fence
    # opener (CommonMark). Treating it as one would open an unclosed fence and silently blank the
    # rest of a real backlog.
    indented_fence = """## Indent group
    ```
- [ ] real item
"""
    result = backlog_fast_candidates(tokenize(indented_fence))
    _assert(
        result == [{"source": "backlog.md", "kind": "h2", "title": "Indent group", "line": 1, "items": 1}],
        "a 4-space-indented ``` does not open a fence, so the following item stays selectable",
    )

    # REGRESSION GUARD — comments are stripped BEFORE fences. A lone ``` parked inside a
    # commented-out doc template must not open a phantom fence that swallows the rest of the file.
    fence_inside_comment = """<!--
```
-->
## Real group
- [ ] real item
"""
    result = backlog_fast_candidates(tokenize(fence_inside_comment))
    _assert(
        result == [{"source": "backlog.md", "kind": "h2", "title": "Real group", "line": 4, "items": 1}],
        "an unbalanced ``` inside an HTML comment does not open a phantom fence",
    )

    # REGRESSION GUARD (qa-verifier) — the line-count contract must hold for EVERY input, not only
    # ones with content after the fence. The candidate/token assertions above cannot see a drop of
    # the file's LAST line (nothing follows it to be mis-numbered), so assert the raw count too.
    for label, fixture in (
        ("fenced_between", fenced_between),
        ("tilde_and_info", tilde_and_info),
        ("nested_fence", nested_fence),
        ("unclosed_fence", unclosed_fence),
        ("indented_fence", indented_fence),
        ("fence_inside_comment", fence_inside_comment),
        ("empty input", ""),
        ("nothing but an unclosed fence", "```\n"),
        ("no trailing newline", "## Group\n- [ ] item"),
        # The fixtures above all end in "\n", so none of them can reach the EOF case in the
        # COMMENT stripper — a comment whose `-->` is the file's last characters. These two do.
        ("comment closing at true EOF", "## Group\n<!--\nx\n-->"),
        ("nothing but a comment at EOF", "<!--\nx\n-->"),
    ):
        _assert(
            len(_strip_fenced_blocks(fixture).splitlines()) == len(fixture.splitlines()),
            f"_strip_fenced_blocks preserves line count exactly ({label})",
        )
        # Same contract, same loop, other stripper: `tokenize` chains comments-then-fences, so a
        # line dropped here shifts every token line number just as surely.
        _assert(
            len(_strip_html_comments(fixture).splitlines()) == len(fixture.splitlines()),
            f"_strip_html_comments preserves line count exactly ({label})",
        )

    # REGRESSION GUARD (codex review) — CommonMark forbids a backtick in a BACKTICK fence's info
    # string. Accepting one turned an ordinary prose line into an unclosed opener that blanked the
    # file through EOF, dropping every real item after it.
    backtick_in_info = """## Real group
```foo`bar
- [ ] real item
"""
    result = backlog_fast_candidates(tokenize(backtick_in_info))
    _assert(
        result == [{"source": "backlog.md", "kind": "h2", "title": "Real group", "line": 1, "items": 1}],
        "a backtick inside a backtick fence's info string does not open a fence",
    )
    tilde_backtick_info = """## Tilde group
~~~foo`bar
## Fake
~~~
- [ ] real item
"""
    result = backlog_fast_candidates(tokenize(tilde_backtick_info))
    _assert(
        result == [{"source": "backlog.md", "kind": "h2", "title": "Tilde group", "line": 1, "items": 1}],
        "a TILDE fence's info string may contain backticks — still a real fence",
    )

    # REGRESSION GUARD (claude review) — an unclosed fence blanking through EOF is CommonMark-
    # correct, but an odd ``` is a routine typo and the swallowed headings never become tokens, so
    # zero_candidate_diagnosis is structurally blind to it. It must be reported on its own channel.
    _assert(
        unclosed_fence_line(unclosed_fence) == 3,
        "unclosed_fence_line reports the opener's 1-based line",
    )
    _assert(
        unclosed_fence_line(fenced_between) is None
        and unclosed_fence_line(nested_fence) is None
        and unclosed_fence_line("") is None,
        "unclosed_fence_line returns None when every fence is balanced",
    )
    _assert(
        unclosed_fence_line(fence_inside_comment) is None,
        "a ``` parked inside an HTML comment is not reported as an unbalanced fence",
    )
    # The partial case: a stray fence hides SOME work while other groups still surface, so the
    # warning cannot be gated on a zero-candidate result.
    partial_swallow = """## Visible group
- [ ] surfaced item
```sh
## Hidden group
- [ ] swallowed item
"""
    _assert(
        [c["title"] for c in backlog_fast_candidates(tokenize(partial_swallow))] == ["Visible group"]
        and unclosed_fence_line(partial_swallow) == 3,
        "a partial swallow still yields candidates, so the warning must not be zero-gated",
    )

    # ---- Test 4: fast_path — cap FAST_PATH_CAP (3) total across Phase A + B ----
    print("\nTest 4: fast_path — cap 3 total across Phase A + B")
    _assert(FAST_PATH_CAP == 3, "FAST_PATH_CAP is 3")
    tasks_many = """# Sprint 1
status: open

# Sprint 2
status: open
"""
    backlog_many = """## B group 1
- [ ] x
## B group 2
- [ ] y
"""
    tasks_tokens = tokenize(tasks_many)
    backlog_tokens = tokenize(backlog_many)
    result = fast_path(tasks_tokens, backlog_tokens)
    _assert(len(result) == 3, "fast_path truncates combined A+B to 3 candidates")
    _assert(
        [c["title"] for c in result] == ["Sprint 1", "Sprint 2", "B group 1"],
        "truncation keeps A(2) then only the first of B",
    )

    # Phase A alone already at FAST_PATH_CAP (3) -> B fully skipped
    tasks_three = """# S1
status: open

# S2
status: open

# S3
status: open
"""
    result = fast_path(tokenize(tasks_three), tokenize(backlog_many))
    _assert(len(result) == 3, "Phase A alone at 3 still caps combined total at 3")
    _assert(
        all(c["source"] == "tasks.md" and c["kind"] == "h1" for c in result),
        "Phase A already at FAST_PATH_CAP skips Phase B entirely",
    )

    # ---- Test 4b: truncation_note — pure signal in both directions ----
    print("\nTest 4b: truncation_note — note when truncated, None when not")
    _assert(
        truncation_note(3, 3) is None,
        "truncation_note is None when shown == total (nothing hidden)",
    )
    _assert(
        truncation_note(3, 2) is None,
        "truncation_note is None when shown > total (defensive; cannot show more than exists)",
    )
    note = truncation_note(3, 5)
    _assert(
        note is not None and "3" in note and "5" in note and "2" in note,
        "truncation_note names shown, total, and the hidden count when total > shown",
    )
    _assert(
        len(fast_path(tasks_tokens, backlog_tokens)) == 3
        and len(full_scan(tasks_tokens, backlog_tokens)) == 4
        and truncation_note(
            len(fast_path(tasks_tokens, backlog_tokens)),
            len(full_scan(tasks_tokens, backlog_tokens)),
        )
        is not None,
        "end-to-end: a real fast_path/full_scan pair on tasks_many/backlog_many truncates and signals",
    )

    # REGRESSION GUARD: tasks.md findings sections are never candidates, whatever their shape.
    tasks_mixed = """# Sprint one

status: open

## Review Backlog

### PR #101 — earlier PR

- [ ] [debt] leftover finding

## Grab bag

- [ ] [doc] another
"""
    mixed_tokens = tokenize(tasks_mixed)
    _assert(
        [c["title"] for c in fast_path(mixed_tokens, tokenize(""))] == ["Sprint one"]
        and [c["title"] for c in full_scan(mixed_tokens, tokenize(""))] == ["Sprint one"],
        "tasks.md h2/h3 findings are not candidates in either algorithm",
    )
    stale = tasks_persistent_sections(mixed_tokens)
    _assert(
        [s["title"] for s in stale] == ["Review Backlog", "PR #101 — earlier PR", "Grab bag"],
        "tasks_persistent_sections reports the findings sections instead of dropping them",
    )
    _assert(
        tasks_persistent_sections(
            tokenize("# Sprint one\n\nstatus: active\n\n## Scope\n\n- [ ] in scope\n")
        )
        == [],
        "a healthy Sprint Contract's own ## Scope checkboxes raise no migration warning",
    )
    _assert(
        tasks_persistent_sections(
            tokenize("# S\n\nstatus: active\n\n## Scope\n\n### Area A\n\n- [ ] x\n")
        )
        == [],
        "an h3 nested under a Sprint Contract section is contract content, not a findings group",
    )
    # REGRESSION GUARD: the two callers must agree. A grab-bag h2 is the case that used to warn
    # "these will be lost" while prune-tasks deleted the file anyway.
    _assert(
        [s["title"] for s in tasks_persistent_sections(
            tokenize("# S\n\nstatus: active\n\n## Scope\n\n- [ ] a\n\n## Follow-ups\n\n- [ ] leftover\n")
        )] == ["Follow-ups"],
        "an ad-hoc grab-bag h2 with open items is reported, not just the two known titles",
    )
    _assert(
        [s["title"] for s in tasks_persistent_sections(
            tokenize("## Security Fixes — my-webapp\n\n### Dependabot\n\n- [x] done\n")
        )] == ["Security Fixes — my-webapp", "Dependabot"],
        "the ` — <repo>` suffix still matches, and fires even when every item is closed",
    )
    # REGRESSION GUARD: fences and comments are markup. Blocking cleanup on a documentation
    # example would be a false refusal with no override flag.
    _assert(
        tasks_persistent_sections(
            tokenize("# S\n\nstatus: active\n\n```markdown\n## Review Backlog\n```\n")
        )
        == []
        and tasks_persistent_sections(
            tokenize("# S\n\nstatus: active\n\n<!--\n## Security Fixes\n-->\n")
        )
        == [],
        "a findings heading inside a fence or an HTML comment is markup, not a persistent section",
    )

    # ---- Test 5: Phase-B-vs-full-scan ordering divergence ----
    print("\nTest 5: Phase B (type-agnostic doc order) vs full_scan (type-priority h3-then-h2)")
    backlog_interleaved = """## H2-A
- [ ] item a
### H3-B
- [ ] item b
## H2-C
- [ ] item c
"""
    tokens = tokenize(backlog_interleaved)
    phase_b_result = backlog_fast_candidates(tokens, limit=2)
    _assert(
        [c["title"] for c in phase_b_result] == ["H2-A", "H3-B"],
        "Phase B picks first 2 in raw document order, type-agnostic",
    )

    full_scan_backlog_only = backlog_h3_candidates(tokens) + backlog_h2_candidates(tokens)
    _assert(
        [c["title"] for c in full_scan_backlog_only] == ["H3-B", "H2-A", "H2-C"],
        "full-scan rules 2+3 apply type priority: all h3 first, then all h2 — diverges from Phase B order",
    )

    # ---- Test 6: full_scan end-to-end composition ----
    print("\nTest 6: full_scan — rules 1-3 concatenated, uncapped")
    tasks_fs = """# Sprint open
status: open
"""
    result = full_scan(tokenize(tasks_fs), tokenize(backlog_interleaved))
    _assert(
        [c["title"] for c in result] == ["Sprint open", "H3-B", "H2-A", "H2-C"],
        "full_scan concatenates rule1..rule3 in order, uncapped, backlog rules type-prioritized",
    )

    # ---- Test 7: format_candidates — h1 omits item count ----
    print("\nTest 7: format_candidates — h1 sprint blocks omit item count")
    candidates = [
        {"source": "tasks.md", "kind": "h1", "title": "Sprint X", "line": 1, "items": None},
        {"source": "backlog.md", "kind": "h2", "title": "Group Y", "line": 5, "items": 3},
    ]
    lines = format_candidates(candidates)
    _assert(lines[0] == "[1] tasks.md: Sprint X", "h1 line has no item count suffix")
    _assert(lines[1] == "[2] backlog.md: Group Y (3 items)", "non-h1 line includes item count suffix")

    # ---- Test 8: zero_candidate_diagnosis — an empty result says WHICH empty it is ----
    print("\nTest 8: zero_candidate_diagnosis — distinguishes the kinds of empty")

    # 8a: prose-ticket format drift. REGRESSION GUARD — the open section is prose while the
    # Completed section is full of `- [x]`, which is the real shape this was first got wrong
    # on: a file-wide "any checkboxes?" test sees the [x]s and reports "all done", a
    # confidently wrong answer on the exact case the diagnosis exists to catch.
    prose_open_with_done_section = """# Backlog

## Open

### Phase 5 — Audit

#### SEC-5-1: header spoofing
- Done when: obtain the trusted proxy IP, then implement.

#### PERF-5-2: pool params
- Done when: record the prod values in docs/.

## Completed

### Phase 4 — Architecture

- [x] extract the service layer
- [x] drop the CDN dependency
"""
    out = zero_candidate_diagnosis(tokenize(""), tokenize(prose_open_with_done_section))
    _assert(
        any("prose bullets but no `- [ ]` item" in line for line in out),
        "prose-ticket open work is reported as format drift even when a Completed section has [x]s",
    )
    _assert(
        any("'Phase 5 — Audit' (line 5)" in line for line in out),
        "the drifted heading is named with its line so the reader can go straight to it",
    )
    _assert(
        not any("no open items" in line for line in out),
        "REGRESSION: the [x]-heavy Completed section must not produce an 'all done' verdict",
    )
    _assert(
        not any(line.startswith("  - tasks.md") for line in out),
        "an absent/empty tasks.md contributes no diagnosis line",
    )
    _assert(
        backlog_fast_candidates(tokenize(prose_open_with_done_section)) == [],
        "the fixture really does yield zero candidates (diagnosis is not masking a hit)",
    )

    # 8a-bis: a `####` heading's checkbox attributes to the enclosing `###`, so the `###` is
    # NOT drifted — guards against flagging correctly-formatted h4-nested work.
    h4_nested = """## Open

### Phase 5

#### TICKET-1
- some prose context
- [ ] Done when: the thing works
"""
    _assert(
        _prose_only_headings(tokenize(h4_nested)) == [],
        "a heading whose region holds both prose and a checkbox is not reported as drifted",
    )
    _assert(
        [c["title"] for c in backlog_fast_candidates(tokenize(h4_nested))] == ["Phase 5"],
        "the h4-nested checkbox really does attribute to the enclosing h3",
    )

    # 8b: every open item parked by a blocked/deferred marker
    all_parked = """## Open
- [ ] fix the thing *(blocked by: 3-upstream-api)*
- [ ] other thing *(deferred: needs ops values)*
"""
    out = zero_candidate_diagnosis(tokenize(""), tokenize(all_parked))
    _assert(
        any("2 open item(s), all parked" in line for line in out),
        "all-parked is reported as parked, with the count, not as an empty queue",
    )

    # 8c: actionable items exist but sit above the first heading
    orphan_items = """- [ ] stray item one
- [ ] stray item two

## Group with nothing
- [x] done
"""
    out = zero_candidate_diagnosis(tokenize(""), tokenize(orphan_items))
    _assert(
        any("sit above the first `#`–`###` heading" in line for line in out),
        "items above the first heading are reported as unattributed, not as absent",
    )

    # 8g: REGRESSION GUARD — a tasks.md h2 is unreachable by any rule now, but its items ARE
    # attributed to a heading. Reporting them as "not attributed to a heading" is a false
    # statement about the file.
    unreachable_h2 = """# Out-of-Scope Findings

## Plugin validation

- [ ] fix the frontmatter parse error
"""
    tasks_tok = tokenize(unreachable_h2)
    _assert(
        fast_path(tasks_tok, tokenize("")) == [] and full_scan(tasks_tok, tokenize("")) == [],
        "a tasks.md h2 group is reachable by neither algorithm",
    )
    out = zero_candidate_diagnosis(tasks_tok, tokenize(""))
    _assert(
        not any("above the first heading" in line for line in out),
        "REGRESSION: an attributed-but-unselected item must not be called unattributed",
    )
    _assert(
        any("no phase selected them" in line for line in out),
        "attributed-but-unselected is named as its own distinct cause",
    )
    _assert(
        not any("no open items" in line for line in out),
        "REGRESSION: 'no open items' must never accompany a line reporting actionable items",
    )

    # 8h: REGRESSION GUARD (qa-verifier) — the unselected-cause clause must describe the file
    # actually being diagnosed. A backlog.md h1 is unreachable for a completely different
    # reason than a tasks.md `###`, and printing tasks.md's causes here is simply false.
    backlog_h1 = """# Top-level backlog.md heading

- [ ] real actionable item directly under an h1
"""
    out = zero_candidate_diagnosis(tokenize(""), tokenize(backlog_h1))
    _assert(
        any("backlog.md rules cover h2/h3 groups only" in line for line in out),
        "a backlog.md h1 is explained by the backlog.md rule set, not tasks.md's",
    )
    _assert(
        not any("Review Backlog" in line for line in out),
        "REGRESSION: tasks.md-only causes must not be cited while diagnosing backlog.md",
    )
    tasks_h3 = """## Some group

### Nested group

- [ ] real actionable item
"""
    out = zero_candidate_diagnosis(tokenize(tasks_h3), tokenize(""))
    _assert(
        any("belong in backlog.md" in line for line in out),
        "the tasks.md explanation is still given when tasks.md is the file being diagnosed",
    )

    # 8i: REGRESSION GUARD (qa-verifier) — "above the first heading" must disclose that only
    # levels 1–3 count, since a `####` line can visually sit above the item.
    h4_above_item = """#### h4 preamble, not a heading to this parser
- [ ] stray item that visually sits under an h4

## Real heading
- [x] done already
"""
    out = zero_candidate_diagnosis(tokenize(""), tokenize(h4_above_item))
    _assert(
        any("only levels 1–3 count here" in line for line in out),
        "REGRESSION: the stray-item message discloses the levels-1-3 restriction",
    )
    _assert(
        not any("items with no heading above them" in line for line in out),
        "REGRESSION: the message no longer claims there is no heading above the item",
    )

    # 8j: the drift message must not name a heading level it did not check
    h4_under_h2 = """## Group directly enclosing an h4

#### TICKET
- prose only, no checkbox
"""
    out = zero_candidate_diagnosis(tokenize(""), tokenize(h4_under_h2))
    _assert(
        any("enclosing `#`–`###` heading" in line for line in out)
        and not any("enclosing `###`)" in line for line in out),
        "the drift message names the enclosing level generically, not a hardcoded `###`",
    )
    _assert(
        not any(
            "reachable with --full-scan" in line
            for line in zero_candidate_diagnosis(tasks_tok, tokenize(""))
        ),
        "no --full-scan suggestion: both algorithms qualify headings by the same test now",
    )

    # 8d: genuinely clear queue — everything closed
    all_done = """## Group
- [x] done one
- [>] moved on
"""
    out = zero_candidate_diagnosis(tokenize(""), tokenize(all_done))
    _assert(
        any("no open items" in line for line in out),
        "an all-closed file is reported as genuinely clear",
    )

    # 8e: both files diagnosed independently in one run
    out = zero_candidate_diagnosis(tokenize(all_parked), tokenize(prose_open_with_done_section))
    _assert(
        any(line.startswith("  - tasks.md") for line in out)
        and any(line.startswith("  - backlog.md") for line in out),
        "each source file gets its own diagnosis line when both have something to say",
    )

    # 8f: truly empty inputs still produce a sentence rather than nothing
    out = zero_candidate_diagnosis(tokenize(""), tokenize(""))
    _assert(
        len(out) == 1 and "no headings or items at all" in out[0],
        "empty-in still yields one explanatory line, never an empty diagnosis",
    )

    print(f"\n=== Results: {PASS_COUNT} PASS, {FAIL_COUNT} FAIL ===")
    return 0 if FAIL_COUNT == 0 else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
