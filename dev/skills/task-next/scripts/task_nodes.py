#!/usr/bin/env python3
"""task_nodes.py — the deterministic nodes of the `task-next` / `task-new` code cycle.

Branch derivation, CHANGELOG `## Unreleased` insertion and backlog/tasks line deletion are
transforms with one correct output for a given input. Leaving them as prose means every run
re-reads and re-interprets the rule; putting them here means the rule is executed, not recalled.
`task-new` invokes this same file as a bundled sibling
(`$SKILL_DIR/../task-next/scripts/task_nodes.py`) — both skills ship in the `dev` plugin and are
always co-installed, so there is exactly one copy of each rule.

Usage:
  python3 task_nodes.py branch --title TITLE [--tag TAG] [--max-slug N] < items
  python3 task_nodes.py changelog --file PATH --title TITLE [--plugin P --version V]
                                  [--units N] [--link PATH] [--date YYYY-MM-DD]
  python3 task_nodes.py prune-backlog --file PATH < items
  python3 task_nodes.py prune-tasks --file PATH (--block TITLE | < items)
  python3 task_nodes.py --test

  branch          Print `<type>/<slug>`. Item lines on stdin supply the `[TYPE]` tag: all items
                  sharing one tag give that prefix (lowercased); mixed or absent tags fall back
                  to `fix` and say so on stderr. `--tag` skips stdin derivation entirely.
                  Does NOT run git — the caller does `git checkout -b "$(...)"`.

  changelog       Compose one entry and insert it as the FIRST line under `## Unreleased`,
                  creating that section (and the file) when absent. Prints the inserted line.
                  An identical line already under `## Unreleased` is left alone (re-run safe).

  prune-backlog   Delete the verbatim `- [ ]` lines given on stdin, then delete any heading
                  this run left with an entirely blank region, or with nothing left but its own
                  intro prose. Refuses (exit 1) on a line that matches nothing, rather than
                  deleting an approximation of it. Then warns on stderr (exit stays 0) when a
                  surviving `*(blocked by: …)*` marker still names something this run deleted —
                  nothing else would ever clear it, and the marked item is invisible to
                  candidate selection until someone does.

  prune-tasks     Same deletion pass, plus `--block TITLE` to delete a whole h1 sprint block.
                  Deletes the file when nothing but blank lines is left — safe only because
                  tasks.md is the Sprint Contract and nothing else, so it refuses (exit 1) on a
                  tasks.md still holding a `## Review Backlog` / `## Security Fixes` section and
                  names the migration to backlog.md.

The CHANGELOG contract's decidable subset is checked before the line is written, but this file
states none of it: `changelog` locates the repo's own `scripts/ci/check_changelog_entries.py`,
imports it, and runs its `check_file` over the composed entry. A repo without that script has
declared no rule, so the entry is written unchecked. Nothing is re-implemented or re-hardcoded
here — a second copy of `MAX_LEN` inside a shipped script is exactly the drift that
single-sourcing removed. Every judgment the *CHANGELOG Entry Contract* states (no explanatory
clauses, no file lists) stays on review, where it already lives.

Heading detection is imported from the sibling `backlog_candidates.py` so the two scripts agree
on what a heading is — fenced code blocks and HTML comments are markup in both, and a `## Fake`
inside a sample must not become a deletion boundary here either.

Self-check (--test): exits 0 on PASS, 1 on FAIL. All fixtures are in-memory or in a tempdir.
"""

from __future__ import annotations

import datetime
import importlib.util
import io
import os
import re
import subprocess
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from backlog_candidates import (  # noqa: E402
    _BLOCK_MARKER_RE,
    _MARKER_PLACEHOLDER_RE,
    _headings,
    _strip_fenced_blocks,
    _strip_html_comments,
    tasks_persistent_sections,
    tokenize,
)

# `[FEAT]`, `[HARNESS]`, … as written at the head of a checkbox item's text.
_TAG_RE = re.compile(r"\[([A-Z][A-Z_-]*)\]")
# Group 1 is the state character, group 2 the item body.
_CHECKBOX_LINE_RE = re.compile(r"^\s*-\s*\[([ xX>])\]\s*(.*)$")
DEFAULT_MAX_SLUG = 48
FALLBACK_TAG = "fix"


# ---------------------------------------------------------------------------
# branch
# ---------------------------------------------------------------------------

def slugify(title: str, max_len: int = DEFAULT_MAX_SLUG) -> str:
    """Lowercase kebab slug, truncated at a word boundary.

    A leading `[TYPE]` tag is dropped: it is already encoded in the branch prefix, so
    `harness/harness-script-…` would say it twice.
    """
    text = _TAG_RE.sub(" ", title)
    text = text.replace("`", " ").replace("*", " ")
    text = re.sub(r"[^A-Za-z0-9]+", "-", text).strip("-").lower()
    text = re.sub(r"-{2,}", "-", text)
    if len(text) <= max_len:
        return text
    cut = text[:max_len]
    # Prefer a word boundary, but never return an empty slug for a single very long token.
    return cut.rsplit("-", 1)[0] if "-" in cut else cut


def derive_tag(item_lines: list[str]) -> tuple[str, str | None]:
    """Return `(prefix, warning)` for the branch type prefix.

    All items sharing one `[TYPE]` tag give that tag lowercased. Mixed tags, or no tag at all,
    fall back to `fix` with a warning — matching the SKILL.md rule this replaces.
    """
    tags = []
    for raw in item_lines:
        m = _CHECKBOX_LINE_RE.match(raw)
        body = m.group(2) if m else raw
        found = _TAG_RE.match(body.strip())
        if found:
            tags.append(found.group(1).lower())
    if not tags:
        return FALLBACK_TAG, f"No [TYPE] tag on any item — defaulting branch prefix to `{FALLBACK_TAG}/`"
    unique = sorted(set(tags))
    if len(unique) > 1:
        return (
            FALLBACK_TAG,
            f"Items carry mixed [TYPE] tags ({', '.join(unique)}) — defaulting branch prefix to `{FALLBACK_TAG}/`",
        )
    if len(tags) != len(item_lines):
        return (
            unique[0],
            f"{len(item_lines) - len(tags)} of {len(item_lines)} items carry no [TYPE] tag — "
            f"using the shared tag `{unique[0]}/` from the rest",
        )
    return unique[0], None


def branch_name(title: str, item_lines: list[str], tag: str | None = None, max_slug: int = DEFAULT_MAX_SLUG) -> tuple[str, str | None]:
    warning = None
    if tag is None:
        tag, warning = derive_tag(item_lines)
    slug = slugify(title, max_slug)
    if not slug:
        return "", "Title produced an empty slug — pass a title with at least one alphanumeric character"
    return f"{tag.lower()}/{slug}", warning


# ---------------------------------------------------------------------------
# changelog
# ---------------------------------------------------------------------------

UNRELEASED_HEADING = "## Unreleased"


# ---------------------------------------------------------------------------
# Line-ending-preserving split/join
# ---------------------------------------------------------------------------
#
# `text.splitlines()` + `"\n".join(...)` rewrites every line ending in the file, so a CRLF
# checkout comes back LF-only — including regions this run never touched. Markdown is not
# LF-pinned in this repo (`docs/conventions.md` → the CRLF note; `bump-version.sh` carries the
# same guard), so each line's own terminator is carried alongside it and re-emitted verbatim.
# Only a line that had no terminator at all — the last line of a file with no trailing newline —
# gets the file's dominant ending.

def _split(text: str) -> tuple[list[str], list[str]]:
    """`(bodies, terminators)`, index-aligned. `terminators[i]` is "" only at a newline-less EOF."""
    bodies, eols = [], []
    for raw in text.splitlines(keepends=True):
        body = raw.rstrip("\r\n")
        bodies.append(body)
        eols.append(raw[len(body):])
    return bodies, eols


def _dominant_eol(eols: list[str]) -> str:
    return "\r\n" if eols.count("\r\n") > eols.count("\n") else "\n"


def _join(bodies: list[str], eols: list[str], default: str) -> str:
    return "".join(body + (eol or default) for body, eol in zip(bodies, eols))


def compose_entry(
    title: str,
    *,
    plugin: str | None = None,
    version: str | None = None,
    units: int | None = None,
    link: str | None = None,
    date: str | None = None,
) -> str:
    """`- [done] <title> [(<N> units)] [(<plugin> v<X.Y.Z>)] (<date>) [→ <link>]`."""
    parts = [f"- [done] {title.strip()}"]
    if units is not None:
        parts.append(f"({units} units)")
    if plugin and version:
        parts.append(f"({plugin} v{version.lstrip('v')})")
    parts.append(f"({date or datetime.date.today().isoformat()})")
    line = " ".join(parts)
    if link:
        line += f" → {link}"
    return line


# The repo's own enforcement point for the contract's decidable subset. Found rather than
# reimplemented: this script ships from a plugin cache and runs inside whatever repo the cycle
# is in, so the rule it must honour is that repo's, at that repo's current values.
CHECKER_RELPATH = Path("scripts") / "ci" / "check_changelog_entries.py"


def find_entry_checker(start: Path) -> Path | None:
    """Nearest `scripts/ci/check_changelog_entries.py` at or above `start`, else None.

    None is a valid answer, not a failure: a repo that ships no checker has declared no
    machine-decidable changelog rule, and inventing one here is how a second copy of the
    cap gets born.

    The walk stops at the first directory holding `.git`, so a nested checkout never inherits
    an outer repo's cap — the rule that applies is the one the CHANGELOG's own repo enforces.
    """
    here = start.resolve()
    for base in (here, *here.parents):
        candidate = base / CHECKER_RELPATH
        if candidate.is_file():
            return candidate
        if (base / ".git").exists():
            return None
    return None


def validate_entry(entry: str, checker: Path, document: str | None = None) -> list[str]:
    """Run `checker`'s own `check_file` over `entry`; return the violations it is answerable for.

    `document` is the full text the entry is about to be written into. Pass it: inserting a line
    can *create* a violation out of what already follows it — an indented line that was fine
    standing alone becomes a continuation under the new entry — and a probe of the entry by
    itself cannot see that. Only violations reported on the entry's own line, or the line
    directly beneath it where the checker reports continuations, are returned; pre-existing
    violations elsewhere in the file are not this write's to block. Without `document` the entry
    is probed alone inside a throwaway `## Unreleased`, because the checker scopes link
    resolution to that section and a probe without it would skip the rule that fires most.

    Every failure of the checker itself — unimportable, no `check_file`, raising, or calling
    `sys.exit` at import because it has no `__main__` guard — yields no violations and a stderr
    note. CI still runs the real thing, so a broken local copy must not take the cycle tail down
    with it.
    """
    focus: set[int] | None = None
    if document is None:
        probe_text = f"# Changelog\n\n## Unreleased\n\n{entry}\n"
    else:
        probe_text = document
        bodies = document.splitlines()
        if entry not in bodies:
            return []  # cannot attribute anything to a write we cannot locate
        at = bodies.index(entry) + 1
        focus = {at}
        # The line below is this write's problem only when the insertion made it a continuation
        # — indented and non-blank. A neighbouring entry that was already over the cap is not.
        below = bodies[at] if at < len(bodies) else ""
        if below.strip() and below[:1].isspace():
            focus.add(at + 1)

    try:
        spec = importlib.util.spec_from_file_location("_changelog_entry_checker", checker)
        if spec is None or spec.loader is None:
            raise ImportError(f"no loader for {checker}")
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        check_file = getattr(module, "check_file", None)
        if check_file is None:
            raise AttributeError("check_file")

        with tempfile.TemporaryDirectory() as tmp:
            probe = Path(tmp) / "CHANGELOG.md"
            probe.write_text(probe_text, encoding="utf-8")
            raw = check_file(probe) or []
            # The probe's `path:lineno:` prefix names a tempfile, not the real target: it is
            # matched exactly (paths can hold spaces) to both locate and strip it.
            prefix = re.compile(rf"^{re.escape(str(probe))}:(\d+):\s*")
            out = []
            for violation in raw:
                m = prefix.match(str(violation))
                if focus is not None and (m is None or int(m.group(1)) not in focus):
                    continue
                out.append(prefix.sub("", str(violation)))
            return out
    except (Exception, SystemExit) as e:  # noqa: BLE001 — a broken checker degrades to unchecked
        sys.stderr.write(f"Note: could not run {checker} on the entry ({e}) — writing unchecked\n")
        return []


def insert_changelog_entry(text: str, entry: str) -> tuple[str, bool]:
    """Insert `entry` as the first line under `## Unreleased`. Returns `(new_text, inserted)`.

    Creates the section (after a leading `# ` title if there is one) and the whole file when
    absent. An identical entry already present under `## Unreleased` is left alone so a re-run
    after a partial cycle does not double-log.
    """
    lines, eols = _split(text)
    eol = _dominant_eol(eols)
    idx = next((i for i, ln in enumerate(lines) if ln.strip() == UNRELEASED_HEADING), None)

    def splice(at: int, block: list[str]) -> None:
        lines[at:at] = block
        eols[at:at] = [eol] * len(block)

    if idx is None:
        if not lines:
            return f"# Changelog{eol}{eol}{UNRELEASED_HEADING}{eol}{eol}{entry}{eol}", True
        # After a leading `# ` title (and the blank line under it), else at the very top.
        at = 0
        if lines[0].startswith("# "):
            at = 1
            while at < len(lines) and not lines[at].strip():
                at += 1
        block = [UNRELEASED_HEADING, "", entry, ""]
        if at > 0 and lines[at - 1].strip():
            block = ["", *block]
        splice(at, block)
        return _join(lines, eols, eol), True

    # Section end: the next heading of any level, or EOF.
    end = next(
        (i for i in range(idx + 1, len(lines)) if lines[i].startswith("#")),
        len(lines),
    )
    if any(lines[i].strip() == entry.strip() for i in range(idx + 1, end)):
        return text, False

    at = idx + 1
    while at < end and not lines[at].strip():
        at += 1
    if at == idx + 1:
        splice(at, [""])
        at += 1
    splice(at, [entry])
    return _join(lines, eols, eol), True


# ---------------------------------------------------------------------------
# prune (shared by prune-backlog and prune-tasks)
# ---------------------------------------------------------------------------

def _heading_levels(text: str) -> dict[int, int]:
    """`{0-based line index: heading level}` for real level-1..3 headings.

    Fenced and commented `##` lines are markup per `backlog_candidates` semantics and must not
    become deletion boundaries here either — a fake heading would otherwise split a section and
    make a still-populated region look empty.
    """
    return {h["line"] - 1: h["level"] for h in _headings(tokenize(text))}


def _region_blank(lines: list[str], start: int, levels: dict[int, int]) -> bool:
    """True if `start`'s whole section is blank — its own items AND everything nested under it.

    The section ends at the next heading of level <= `start`'s, NOT the next heading of any
    level. Ending at any heading would stop the scan at `start`'s own surviving child, report
    the parent as empty, and delete it — orphaning that child. Because a surviving child's
    heading line is itself non-blank, this span test protects the parent automatically; a child
    the same run dropped is already blanked and correctly does not.
    """
    level = levels.get(start, 1)
    end = next(
        (h for h in sorted(levels) if h > start and levels[h] <= level),
        len(lines),
    )
    return all(not lines[i].strip() for i in range(start + 1, min(end, len(lines))))


_LIST_ITEM_RE = re.compile(r"^\s*(?:[-*+]\s|\d+\.\s)")


def _region_prose_only(lines: list[str], start: int, levels: dict[int, int]) -> bool:
    """True if `start`'s region holds only prose — at least one non-blank line, and none of them
    a heading or a list item.

    Same level-aware span as `_region_blank` (see its docstring): the region ends at the next
    heading of level <= `start`'s, not the next heading of any level, so a surviving child
    heading's own body is never mistaken for `start`'s. A surviving `[x]`/`[>]` line IS a list
    item, so it keeps `start` alive here exactly as it does for `_region_blank`; a surviving
    child heading is itself excluded by the "no heading line" half of this test.

    `lines` must already have fenced/commented spans blanked out (the same `masked` view line
    matching uses) — otherwise a fenced sample with no real prose reads as content and a heading
    holding nothing but an illustrative example would be wrongly deleted.
    """
    level = levels.get(start, 1)
    end = next(
        (h for h in sorted(levels) if h > start and levels[h] <= level),
        len(lines),
    )
    region = lines[start + 1:min(end, len(lines))]
    if not any(ln.strip() for ln in region):
        return False
    for i, ln in enumerate(region, start=start + 1):
        if not ln.strip():
            continue
        if i in levels or _LIST_ITEM_RE.match(ln):
            return False
    return True


def _collapse_gap_blanks(lines: list[str], origin: list[int]) -> list[int]:
    """Source indices to keep, collapsing only the blank runs a deletion created.

    `origin[i]` is the source index of `lines[i]`; a jump in that sequence means something was
    removed between them. Only those seams are collapsed, so the diff stays scoped to the edit.
    Returning indices rather than text lets the caller carry each line's own terminator through.
    """
    out: list[int] = []
    i = 0
    while i < len(lines):
        if lines[i].strip():
            out.append(origin[i])
            i += 1
            continue
        j = i
        while j < len(lines) and not lines[j].strip():
            j += 1
        # A seam exists if lines were dropped just before, inside, or just after this blank run.
        contiguous = all(origin[k + 1] == origin[k] + 1 for k in range(i, j - 1))
        seam = (
            (i > 0 and origin[i] != origin[i - 1] + 1)
            or not contiguous
            or (j < len(lines) and origin[j] != origin[j - 1] + 1)
        )
        keep = 1 if (seam and j - i > 1) else j - i
        # A seam at the very end of the file leaves no separator worth keeping.
        if seam and j >= len(lines):
            keep = 0
        out.extend(origin[i:i + keep])
        i = j
    return out


def prune_lines(text: str, targets: list[str]) -> tuple[str, list[str]]:
    """Delete each verbatim line in `targets`, then any heading this run left blank or prose-only.

    A heading whose region this run drained down to nothing, or down to only its intro prose
    (no surviving item, no surviving child heading), is deleted along with that region — see
    `_region_blank` and `_region_prose_only`. A section this run never touched, or one where a
    child heading or an `[x]`/`[>]` item survives, keeps its heading untouched either way.

    Returns `(new_text, problems)`. `problems` is non-empty when a target matched nothing — or
    matched more than once, which is the more dangerous case: two sections can hold identically
    worded items, only one of which is done, and deleting both silently discards live work. Both
    are fatal to the whole run; the caller must not delete an approximate or an ambiguous match.
    """
    lines, eols = _split(text)
    default_eol = _dominant_eol(eols)
    # Match against the same masked view heading detection uses: a `- [ ]` inside a fenced sample
    # or an HTML comment is markup, not work. harness-init seeds `backlog.md` with a commented-out
    # `- [ ] Simplest case` template, so without this an item worded like the template is either
    # deleted from the comment or — since the ambiguity guard above — blocks its own deletion.
    masked = _strip_fenced_blocks(_strip_html_comments(text)).splitlines()
    if len(masked) != len(lines):  # line-count contract broken upstream; fail safe, do not guess
        return text, ["internal: masked line count does not match the source file"]
    wanted = [t.rstrip() for t in targets if t.strip()]
    problems = []
    drop: set[int] = set()
    for target in wanted:
        hits = [i for i, ln in enumerate(masked) if ln.rstrip() == target]
        if not hits:
            problems.append(f"no line matches verbatim: {target!r}")
        elif len(hits) > 1:
            where = ", ".join(str(i + 1) for i in hits)
            problems.append(
                f"{len(hits)} lines match verbatim (lines {where}) — ambiguous, refusing to guess "
                f"which one is done; disambiguate the wording first: {target!r}"
            )
        else:
            drop.add(hits[0])
    if problems:
        return text, problems

    # Headings whose region this run may have emptied — computed on the ORIGINAL text so a
    # heading that was already empty before the run is left alone (deliberate history).
    levels = _heading_levels(text)
    heads = sorted(levels)
    owned: set[int] = set()
    for i in sorted(drop):
        prior = [h for h in heads if h < i]
        if prior:
            owned.add(prior[-1])

    # Cascade: deleting an h3 can empty its h2 parent, but only if the parent holds nothing else —
    # including no surviving child heading, which `_region_blank`'s level-aware span enforces.
    # A heading left with intro prose only (no surviving item, no surviving child) cascades the
    # same way, but its prose lines are non-blank and must be dropped along with the heading —
    # otherwise the dangling description this fix exists to remove would stay behind.
    while True:
        surviving = [ln if i not in drop else "" for i, ln in enumerate(lines)]
        masked_surviving = [ln if i not in drop else "" for i, ln in enumerate(masked)]
        alive = {h: lv for h, lv in levels.items() if h not in drop}
        # Keep the file's first heading out of the blank cascade. It is the schema root even when
        # a repo omits an h1 wrapper; later headings remain ordinary sections and may cascade away.
        root_heading = heads[0] if heads else None
        newly = {
            h for h in owned
            if h not in drop and h != root_heading
            and _region_blank(surviving, h, alive)
        }
        prose_only = {
            h for h in owned
            if h not in drop and h not in newly and (not heads or h != heads[0])
            and _region_prose_only(masked_surviving, h, alive)
        }
        if not newly and not prose_only:
            break
        drop.update(newly)
        for h in prose_only:
            level = alive.get(h, 1)
            end = next((x for x in sorted(alive) if x > h and alive[x] <= level), len(lines))
            drop.update(range(h, min(end, len(lines))))
        for h in newly | prose_only:
            # The next owner to re-check is `h`'s actual PARENT — the nearest preceding heading
            # of a STRICTLY LOWER level — not merely the nearest preceding heading of any level.
            # A same-level sibling can sit between `h` and its parent; picking it here would add
            # an untouched, unrelated section to `owned` and let the next pass sweep it away too.
            h_level = levels.get(h, 1)
            prior = [x for x in heads if x < h and x not in drop and levels.get(x, 1) < h_level]
            if prior:
                owned.add(prior[-1])

    kept = [i for i in range(len(lines)) if i not in drop]
    final = _collapse_gap_blanks([lines[i] for i in kept], kept)
    out = _join([lines[i] for i in final], [eols[i] for i in final], default_eol)
    return (out if out.strip() else ""), []


# ---------------------------------------------------------------------------
# orphaned blockers (prune-backlog only)
# ---------------------------------------------------------------------------

# `slugify` truncates at a word boundary for branch names; slug MATCHING wants the whole thing,
# since the identifying word can sit past the branch-name budget.
_SLUG_UNTRUNCATED = 10 ** 6


def _slug_tokens(text: str) -> list[str]:
    """Untruncated kebab tokens of `text` — the unit blocker slugs are matched in."""
    return [t for t in slugify(text, max_len=_SLUG_UNTRUNCATED).split("-") if t]


def _blocker_tokens(payload: str) -> list[str]:
    """Tokens of a `*(blocked by: <n>-<slug>)*` payload, minus the batch-position `<n>` prefix.

    `<n>` is frozen when the ticket batch is written and is never renumbered (`dev:task-tickets`
    SKILL.md), so it identifies nothing — the slug is the identifying half. A payload that is
    nothing but a number is unresolvable by design and yields no tokens, so it matches nothing
    rather than matching everything.
    """
    tokens = _slug_tokens(payload)
    if tokens and tokens[0].isdigit():
        tokens = tokens[1:]
    return [] if all(t.isdigit() for t in tokens) else tokens


def _contains_tokens(haystack: list[str], needle: list[str]) -> bool:
    """True if every `needle` token appears in `haystack`, in order, as a whole token.

    Whole tokens, not a substring: `auth` must not match `authors`. In order but NOT contiguous,
    because a marker's slug is the item's abbreviated short name while a subject is the full
    slugified title, stopwords and all: PR #258's own case is the payload
    `skill-run-sink-cross-process-append-lock` against the heading
    `harness-skill-run-sink-has-no-cross-process-append-lock` — a subsequence, never a run. A
    contiguous test passes hand-written fixtures and misses every real marker.
    """
    if not needle or len(needle) > len(haystack):
        return False
    it = iter(haystack)
    return all(any(h == t for h in it) for t in needle)


def _line_owners(masked: list[str], levels: dict[int, int]) -> tuple[dict[int, int], dict[int, str]]:
    """`({line index: owning checkbox line}, {checkbox line: state char})`.

    A wrapped item's continuation lines belong to the checkbox that opened it, so a marker on a
    continuation line resolves to the same item as one written inline. Without this the owner test
    below compares a continuation index against a checkbox index, never matches, and the item
    suppresses its own warning. A blank line or a heading ends the item, matching how `tokenize`
    folds continuations.
    """
    owner: dict[int, int] = {}
    state: dict[int, str] = {}
    current: int | None = None
    for i, ln in enumerate(masked):
        if not ln.strip() or i in levels:
            current = None
            continue
        m = _CHECKBOX_LINE_RE.match(ln)
        if m:
            current = i
            state[i] = m.group(1)
        if current is not None:
            owner[i] = current
    return owner, state


def orphaned_blockers(original: str, removed: list[str], new_text: str, label: str) -> list[str]:
    """Warning lines for surviving `*(blocked by: …)*` markers naming something this run deleted.

    An item whose blocker marker outlives its blocker is filtered out of candidate selection by
    `backlog_candidates.py` forever: the blocker will never land again, so nothing prompts anyone
    to clear the marker. Detection is advisory — the caller warns and still writes the file, since
    a marker's wording is a judgment call and rewriting it here would be guessing.

    Subjects are the item lines this run deleted plus the headings it drained away (a marker often
    names the section, not the item). A marker is reported only when all of these hold:

      - it is `blocked by`, not `deferred` — a deferral names a reason, not an item;
      - its payload holds no `<placeholder>`, so prose quoting the syntax is not a blocker;
      - it survives outside a fence or an HTML comment, per the masked view the pruner already
        trusts for deletion — a marker inside a sample is markup;
      - no *other* surviving OPEN item still answers to the same slug, which would mean the
        blocker is alive and this marker is fine. A `[x]`/`[>]` item is not a live blocker — a
        landed blocker is precisely the marker that needs clearing — and neither is a fellow
        blockee carrying the same marker, or two items waiting on one pruned blocker would
        silence each other and both stay unselectable.
    """
    subjects: list[tuple[str, list[str]]] = []
    for raw in removed:
        m = _CHECKBOX_LINE_RE.match(raw)
        body = m.group(2) if m else raw
        tokens = _slug_tokens(body)
        if tokens:
            subjects.append((slugify(body), tokens))
    surviving_titles = [h["title"] for h in _headings(tokenize(new_text))]
    for title in (h["title"] for h in _headings(tokenize(original))):
        if title in surviving_titles:
            surviving_titles.remove(title)  # consume one, so a duplicated title is not double-counted
            continue
        tokens = _slug_tokens(title)
        if tokens:
            subjects.append((slugify(title), tokens))
    if not subjects:
        return []

    lines = new_text.splitlines()
    masked = _strip_fenced_blocks(_strip_html_comments(new_text)).splitlines()
    if len(masked) != len(lines):  # same fail-safe as `prune_lines`: warn about nothing, not wrongly
        return []
    owner, state = _line_owners(masked, _heading_levels(new_text))
    # Per surviving item: its own text minus its own markers (an item is never the blocker it
    # waits on), and the blocker slugs it is itself waiting on.
    bodies: dict[int, list[str]] = {}
    waiting: dict[int, list[list[str]]] = {}
    for i, ln in enumerate(masked):
        item = owner.get(i)
        if item is None:
            continue
        bodies.setdefault(item, []).append(_BLOCK_MARKER_RE.sub(" ", ln))
        for marker in _BLOCK_MARKER_RE.finditer(ln):
            if marker.group(1).lower() == "blocked by":
                waiting.setdefault(item, []).append(_blocker_tokens(marker.group(2)))
    survivors = {
        item: _slug_tokens(" ".join(parts))
        for item, parts in bodies.items()
        if state.get(item) == " "
    }

    findings: dict[str, list[str]] = {}
    for i, ln in enumerate(masked):
        for marker in _BLOCK_MARKER_RE.finditer(ln):
            if marker.group(1).lower() != "blocked by":
                continue
            if _MARKER_PLACEHOLDER_RE.search(marker.group(2)):
                continue
            tokens = _blocker_tokens(marker.group(2))
            if not tokens:
                continue
            hit = next((s for s in subjects if _contains_tokens(s[1], tokens)), None)
            if hit is None:
                continue
            this_item = owner.get(i)
            if any(
                _contains_tokens(toks, tokens)
                for j, toks in survivors.items()
                if j != this_item and tokens not in waiting.get(j, [])
            ):
                continue
            hits = findings.setdefault(hit[0], [])
            entry = f"  {label}:{i + 1}: {lines[i].strip()}"
            if entry not in hits:
                hits.append(entry)

    out: list[str] = []
    for slug, hits in findings.items():
        out.append(
            f"Warning: pruned item `{slug}` is still named as a blocker by {len(hits)} surviving "
            "line(s). Nothing will clear the marker now that the blocker is gone, so those items "
            "stay invisible to candidate selection until you delete it by hand:"
        )
        out.extend(hits)
    return out


def persistent_sections(text: str) -> list[str]:
    """Titles of findings sections that must not be in `tasks.md`.

    `tasks.md` is the Sprint Contract and is deleted whole at sprint close, so anything meant to
    outlive the sprint belongs in `backlog.md`. An h1 block runs to the next h1 or EOF, which means
    a findings section placed after the sprint heading is deleted with it — and a file left with
    nothing is unlinked outright. Rather than guess a safe boundary inside a file that should not
    need one, `prune-tasks` refuses on this shape and names the migration.

    Thin wrapper over `backlog_candidates.tasks_persistent_sections` on purpose: that function is
    the single definition, so the pruner's refusal and the candidate scanner's warning cannot
    disagree about the same file. Do not re-derive the rule here.
    """
    return [s["title"] for s in tasks_persistent_sections(tokenize(text))]


def prune_h1_block(text: str, title: str) -> tuple[str, list[str]]:
    """Delete the whole `# <title>` block — heading, `status:` line, and body — up to the next h1.

    Correct only because `tasks.md` holds the Sprint Contract and nothing else; `cmd_prune` blocks
    the mixed-content case up front via `persistent_sections`.
    """
    lines, eols = _split(text)
    default_eol = _dominant_eol(eols)
    tok = tokenize(text)
    h1s = [t for t in tok if t["type"] == "heading" and t["level"] == 1]
    start = next((t["line"] - 1 for t in h1s if t["title"].strip() == title.strip()), None)
    if start is None:
        titles = ", ".join(repr(t["title"]) for t in h1s) or "none"
        return text, [f"no h1 block titled {title!r} (h1 blocks present: {titles})"]
    end = next((t["line"] - 1 for t in h1s if t["line"] - 1 > start), len(lines))
    kept = [i for i in range(len(lines)) if not (start <= i < end)]
    final = _collapse_gap_blanks([lines[i] for i in kept], kept)
    out = _join([lines[i] for i in final], [eols[i] for i in final], default_eol)
    return (out if out.strip() else ""), []


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def _read_stdin_lines() -> list[str]:
    if sys.stdin is None or sys.stdin.isatty():
        return []
    return [ln for ln in sys.stdin.read().splitlines() if ln.strip()]


def _opt(argv: list[str], name: str) -> str | None:
    if name in argv:
        i = argv.index(name)
        if i + 1 >= len(argv):
            sys.stderr.write(f"Error: {name} requires a value\n")
            sys.exit(1)
        return argv[i + 1]
    return None


def _require(value: str | None, name: str) -> str:
    if value is None:
        sys.stderr.write(f"Error: {name} is required\n")
        sys.exit(1)
    return value


def cmd_branch(argv: list[str]) -> int:
    title = _require(_opt(argv, "--title"), "--title")
    max_slug = int(_opt(argv, "--max-slug") or DEFAULT_MAX_SLUG)
    # Resolve --tag BEFORE touching stdin: with a tag there is nothing to derive, and an inherited
    # open pipe (task-new's documented invocation passes no stdin redirect) would block the read
    # forever. `isatty()` alone does not cover that case.
    tag = _opt(argv, "--tag")
    items = [] if tag else _read_stdin_lines()
    name, warning = branch_name(title, items, tag, max_slug)
    if not name:
        sys.stderr.write(f"Error: {warning}\n")
        return 1
    if warning:
        sys.stderr.write(f"Warning: {warning}\n")
    print(name)
    return 0


def cmd_changelog(argv: list[str]) -> int:
    path = Path(_opt(argv, "--file") or "CHANGELOG.md")
    units = _opt(argv, "--units")
    entry = compose_entry(
        _require(_opt(argv, "--title"), "--title"),
        plugin=_opt(argv, "--plugin"),
        version=_opt(argv, "--version"),
        units=int(units) if units is not None else None,
        link=_opt(argv, "--link"),
        date=_opt(argv, "--date"),
    )
    text = path.read_text(encoding="utf-8") if path.is_file() else ""
    new_text, inserted = insert_changelog_entry(text, entry)
    if inserted:
        # Validate the document as it would be written, then write it — never the reverse.
        checker = find_entry_checker(path.parent)
        problems = validate_entry(entry, checker, document=new_text) if checker else []
        if problems:
            sys.stderr.write(
                f"Error: the composed entry violates the CHANGELOG Entry Contract, per {checker}:\n"
            )
            for p in problems:
                sys.stderr.write(f"  {p}\n")
            sys.stderr.write(
                f"  entry: {entry}\n"
                "Shorten the title or drop the extra link — the detail belongs in the owning doc.\n"
                "Nothing was written.\n"
            )
            return 1
        path.write_text(new_text, encoding="utf-8")
    else:
        sys.stderr.write("Note: an identical entry is already under ## Unreleased — left as is\n")
    print(entry)
    return 0


def _write_or_delete(path: Path, text: str, delete_if_empty: bool) -> str:
    if not text.strip() and delete_if_empty:
        os.remove(path)
        return f"{path}: deleted (no content left)"
    path.write_text(text, encoding="utf-8")
    return f"{path}: updated"


def cmd_prune(argv: list[str], *, delete_if_empty: bool) -> int:
    path = Path(_require(_opt(argv, "--file"), "--file"))
    if not path.is_file():
        sys.stderr.write(f"Error: not a file: {path}\n")
        return 1
    text = path.read_text(encoding="utf-8")
    if delete_if_empty:
        stale = persistent_sections(text)
        if stale:
            names = ", ".join(f"`## {s}`" for s in stale)
            sys.stderr.write(
                f"Error: {path} holds persistent section(s) {names}, which belong in backlog.md.\n"
                "tasks.md is the Sprint Contract only and is deleted at sprint close, so pruning "
                "here would destroy them.\n"
                "Move those sections to backlog.md verbatim, then re-run. Nothing was deleted.\n"
            )
            return 1
    block = _opt(argv, "--block")
    targets: list[str] = []
    if block is not None:
        new_text, problems = prune_h1_block(text, block)
    else:
        targets = _read_stdin_lines()
        if not targets:
            sys.stderr.write("Error: no lines on stdin to delete (and no --block given)\n")
            return 1
        new_text, problems = prune_lines(text, targets)
    if problems:
        for p in problems:
            sys.stderr.write(f"Error: {p}\n")
        sys.stderr.write("Nothing was deleted — fix the input and re-run.\n")
        return 1
    print(_write_or_delete(path, new_text, delete_if_empty))
    # Advisory only, and emitted after the write: the deletion itself was correct, and the run must
    # not fail over a marker whose rewording is the caller's judgment call. `tasks.md` is deleted
    # whole at sprint close and holds no queue markers, so this is the backlog pruner's concern.
    if targets and not delete_if_empty:
        for line in orphaned_blockers(text, targets, new_text, str(path)):
            sys.stderr.write(f"{line}\n")
    return 0


USAGE = (
    "Usage: task_nodes.py {branch|changelog|prune-backlog|prune-tasks|--test} [options]\n"
    "       see the module docstring for each subcommand's flags\n"
)


def main(argv: list[str]) -> int:
    if not argv:
        sys.stderr.write(USAGE)
        return 1
    cmd, rest = argv[0], argv[1:]
    if cmd == "--test":
        return run_tests()
    if cmd == "branch":
        return cmd_branch(rest)
    if cmd == "changelog":
        return cmd_changelog(rest)
    if cmd == "prune-backlog":
        return cmd_prune(rest, delete_if_empty=False)
    if cmd == "prune-tasks":
        return cmd_prune(rest, delete_if_empty=True)
    sys.stderr.write(f"Unknown subcommand: {cmd}\n{USAGE}")
    return 1


# ---------------------------------------------------------------------------
# Self-check (--test)
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
    print("=== task_nodes.py --test ===\n")

    # ---- branch ----
    print("Test 1: branch — tag derivation and slugification")
    shared = [
        "- [ ] [HARNESS] script the nodes",
        "- [ ] [HARNESS] and the other nodes",
    ]
    name, warn = branch_name("Script the deterministic `task-*` nodes", shared)
    _assert(name == "harness/script-the-deterministic-task-nodes", f"shared tag gives its prefix (got {name!r})")
    _assert(warn is None, "a clean shared tag emits no warning")

    mixed = ["- [ ] [FIX] a", "- [ ] [FEAT] b"]
    name, warn = branch_name("Two different things", mixed)
    _assert(name.startswith("fix/"), "mixed tags fall back to fix/")
    _assert(warn is not None and "mixed" in warn, "mixed tags warn on stderr")

    name, warn = branch_name("Untagged finding from a review", ["- [ ] no tag here"])
    _assert(name == "fix/untagged-finding-from-a-review", "untagged items fall back to fix/")
    _assert(warn is not None and "No [TYPE] tag" in warn, "untagged items warn on stderr")

    name, warn = branch_name("Partly tagged", ["- [ ] [FIX] a", "- [ ] b"])
    _assert(name.startswith("fix/") and warn is not None and "carry no [TYPE] tag" in warn,
            "a partially tagged group uses the shared tag and says how many lacked one")

    _assert(branch_name("Anything", [], tag="REFACTOR")[0].startswith("refactor/"),
            "--tag overrides stdin derivation")

    # REGRESSION GUARD (claude review, PR #192) — with --tag there is nothing to derive from
    # stdin, and an inherited open pipe (task-new passes no redirect) blocks the read forever.
    with tempfile.TemporaryDirectory() as _td:
        _r, _w = os.pipe()
        _proc = subprocess.Popen(
            [sys.executable, __file__, "branch", "--title", "some title", "--tag", "FIX"],
            stdin=_r, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, cwd=_td,
        )
        os.close(_r)
        try:
            _out, _ = _proc.communicate(timeout=10)
            _assert(_out.decode().strip() == "fix/some-title",
                    "--tag short-circuits the stdin read — no hang on an inherited open pipe")
        except subprocess.TimeoutExpired:
            _proc.kill()
            _assert(False, "--tag short-circuits the stdin read — no hang on an inherited open pipe")
        finally:
            os.close(_w)
    _assert(slugify("[HARNESS] Leading tag is dropped") == "leading-tag-is-dropped",
            "a leading [TYPE] tag is not repeated in the slug")
    _assert(slugify("a" * 80) == "a" * DEFAULT_MAX_SLUG,
            "a single over-long token is hard-truncated rather than emptied")
    _assert(len(slugify("one two three four five six seven eight nine ten")) <= DEFAULT_MAX_SLUG,
            "a long title is truncated at a word boundary within the cap")
    _assert(branch_name("!!!", [])[0] == "", "a title with no alphanumerics is an error, not an empty slug")

    # ---- changelog ----
    print("\nTest 2: changelog — composition and insertion")
    _assert(
        compose_entry("a thing", plugin="dev", version="4.0.32", date="2026-08-03")
        == "- [done] a thing (dev v4.0.32) (2026-08-03)",
        "the standard shape composes exactly as the Entry Contract states",
    )
    _assert(
        compose_entry("b", date="2026-08-03") == "- [done] b (2026-08-03)",
        "the plugin/version clause is dropped in a repo with no versioned plugin",
    )
    _assert(
        compose_entry("c", plugin="dev", version="v1.2.3", units=3, link="docs/x.md", date="2026-08-03")
        == "- [done] c (3 units) (dev v1.2.3) (2026-08-03) → docs/x.md",
        "units and link clauses land in the batch-mode order, and a leading v is not doubled",
    )

    existing = "# Changelog\n\n## Unreleased\n\n- [done] older (2026-08-01)\n"
    out, inserted = insert_changelog_entry(existing, "- [done] newer (2026-08-03)")
    _assert(inserted, "a new entry is inserted")
    _assert(
        out.splitlines()[4:6] == ["- [done] newer (2026-08-03)", "- [done] older (2026-08-01)"],
        "the new entry lands FIRST under ## Unreleased, above the previous newest",
    )
    out2, inserted2 = insert_changelog_entry(out, "- [done] newer (2026-08-03)")
    _assert(not inserted2 and out2 == out, "an identical entry already present is left alone (re-run safe)")

    no_section = "# Changelog\n\n## 4.0.0\n\n- [done] shipped (2026-01-01)\n"
    out, _ = insert_changelog_entry(no_section, "- [done] fresh (2026-08-03)")
    lines = out.splitlines()
    _assert(
        lines.index("## Unreleased") < lines.index("## 4.0.0"),
        "an absent ## Unreleased is created above the existing version sections",
    )
    _assert(
        lines[lines.index("## Unreleased") + 2] == "- [done] fresh (2026-08-03)",
        "the entry lands under the section it just created",
    )
    out, _ = insert_changelog_entry("", "- [done] first ever (2026-08-03)")
    _assert(
        out == "# Changelog\n\n## Unreleased\n\n- [done] first ever (2026-08-03)\n",
        "an absent file is created with a title, the section, and the entry",
    )
    empty_section = "# Changelog\n\n## Unreleased\n\n## 4.0.0\n"
    out, _ = insert_changelog_entry(empty_section, "- [done] x (2026-08-03)")
    _assert(
        out.splitlines()[:5] == ["# Changelog", "", "## Unreleased", "", "- [done] x (2026-08-03)"],
        "an existing but empty ## Unreleased takes the entry without eating the next heading",
    )

    # ---- prune ----
    print("\nTest 3: prune — verbatim deletion, emptied headings, untouched history")
    backlog = """# Backlog

## Group one

Preamble prose that outlives its items.

### Sub A

- [ ] [FIX] the only item under Sub A

### Sub B

- [ ] [FIX] one
- [ ] [FIX] two

## Group two

- [ ] [FEAT] lonely item
"""
    out, problems = prune_lines(backlog, ["- [ ] [FIX] the only item under Sub A"])
    _assert(problems == [], "a verbatim match produces no problems")
    _assert("### Sub A" not in out, "the heading this run emptied is deleted")
    _assert("## Group one" in out and "Preamble prose" in out,
            "the parent h2 survives because its preamble is still content")
    _assert("### Sub B" in out and "- [ ] [FIX] one" in out, "sibling groups are untouched")

    out, problems = prune_lines(backlog, ["- [ ] [FEAT] lonely item"])
    _assert("## Group two" not in out, "an h2 whose only content was the deleted item is deleted too")

    out, problems = prune_lines(backlog, ["- [ ] [FIX] one"])
    _assert(problems == [] and "### Sub B" in out and "- [ ] [FIX] two" in out,
            "a heading with a remaining item is kept")

    _, problems = prune_lines(backlog, ["- [ ] [FIX] the only item under sub a"])
    _assert(len(problems) == 1 and "no line matches verbatim" in problems[0],
            "a near-miss target is refused, not approximated")
    unchanged, problems = prune_lines(backlog, ["- [ ] nope"])
    _assert(unchanged == backlog, "a refused run deletes nothing at all")

    # REGRESSION GUARD (agy review, PR #192) — every fixture above gives the parent h2 a preamble,
    # which hid this: with no preamble, `_region_blank` used to stop at the parent's own surviving
    # child h3, call the parent empty, and delete it — orphaning that child. The section must end
    # at the next heading of level <= its own, not the next heading of any level.
    siblings = """# Root

## Group

### Sub A

- [ ] [FIX] item A

### Sub B

- [ ] [FIX] item B
"""
    out, _ = prune_lines(siblings, ["- [ ] [FIX] item A"])
    _assert("## Group" in out, "a parent with a surviving child h3 is NOT deleted, even with no preamble")
    _assert("### Sub A" not in out, "the emptied child h3 is still deleted")
    _assert("### Sub B" in out and "- [ ] [FIX] item B" in out, "the surviving child is not orphaned")

    out, _ = prune_lines(siblings, ["- [ ] [FIX] item A", "- [ ] [FIX] item B"])
    _assert("# Root" in out and "## Group" not in out,
            "emptying every child still cascades the non-root parent away")

    history = """## Shipped

- [x] done long ago

## Live

- [ ] [FIX] current
"""
    out, _ = prune_lines(history, ["- [ ] [FIX] current"])
    _assert("## Shipped" in out and "- [x] done long ago" in out,
            "a heading holding only [x] history is NOT deleted — this run did not empty it")
    _assert("## Live" not in out, "the heading this run did empty IS deleted")

    # `_region_prose_only` — a heading drained to nothing but its own intro prose (PR #197).
    # Root heading ('# Root') is present so '## Group with intro prose' is NOT `heads[0]` and is
    # therefore not exempt from the prose-only cascade (PR #203 restricted that exemption to the
    # file's actual first heading — see the (f)/(g) fixtures below).
    prose_group = """# Root

## Group with intro prose

Intro prose that describes the group.

- [ ] [FIX] the only item

## Untouched prose-only section

Just a Source: line, no items — deliberate history, never touched by this run.
"""
    out, _ = prune_lines(prose_group, ["- [ ] [FIX] the only item"])
    _assert("## Group with intro prose" not in out and "Intro prose that describes" not in out,
            "(a) a heading drained to prose-only: heading AND its intro prose are both dropped")
    _assert("## Untouched prose-only section" in out and "Just a Source: line" in out,
            "(d) a prose-only section this run never touched keeps its heading and prose")

    prose_survivor = """## Group with intro prose

Intro prose that describes the group.

- [x] done already
- [ ] [FIX] the only open item
"""
    out, _ = prune_lines(prose_survivor, ["- [ ] [FIX] the only open item"])
    _assert("## Group with intro prose" in out and "Intro prose that describes" in out
            and "- [x] done already" in out,
            "(b) a surviving [x] item keeps the heading and its intro prose")

    prose_child_survives = """## Parent group

Intro prose for the parent.

- [ ] [FIX] parent-level item

### Sub child

- [ ] [FIX] child item
"""
    out, _ = prune_lines(prose_child_survives, ["- [ ] [FIX] parent-level item"])
    _assert("## Parent group" in out and "Intro prose for the parent." in out,
            "(c) a surviving child heading keeps the parent heading and its intro prose")

    _assert("### Sub child" in out and "- [ ] [FIX] child item" in out,
            "(c) the surviving child itself is untouched")

    # (e) The root heading is exempt: draining the file's last item must not take `# Backlog` and
    # its standing preamble with it. `backlog.md` is a prerequisite that must keep its schema.
    root_preamble = """# Backlog

Queue of work not yet in flight. Do not delete this file.

## Now

- [ ] [FIX] only item
"""
    out, _ = prune_lines(root_preamble, ["- [ ] [FIX] only item"])
    _assert("# Backlog" in out and "Do not delete this file." in out,
            "(e) the root heading and its preamble prose survive draining the last item")
    _assert("## Now" not in out, "(e) the drained h2 under the root is still deleted")

    # REGRESSION GUARD — the root exemption also covers a schema-only root with no preamble. The
    # non-root prose-only section is removed, but the first heading remains so backlog.md is never
    # rewritten byte-empty.
    root_without_preamble = """# Backlog

# Other

Intro prose for other.

- [ ] drain me
"""
    out, _ = prune_lines(root_without_preamble, ["- [ ] drain me"])
    _assert("# Backlog" in out, "(e2) a schema-only root survives without preamble prose")
    _assert("# Other" not in out and "Intro prose for other." not in out,
            "(e2) the drained non-root prose-only section still cascades away")

    # REGRESSION GUARD — a heading whose immediately preceding sibling (same level, NOT its
    # parent) happened to be prose-only and untouched by this run must survive. The cascade must
    # walk up to the actual PARENT (nearest heading of a strictly lower level), not just the
    # nearest preceding heading of any level — otherwise an untouched sibling gets swept in.
    sibling_prose_untouched = """# Root

## Untouched note

Some standing prose that was never touched.

## Live group

- [ ] [FIX] only item
"""
    out, _ = prune_lines(sibling_prose_untouched, ["- [ ] [FIX] only item"])
    _assert("## Untouched note" in out and "Some standing prose that was never touched." in out,
            "a prose-only sibling heading preceding the drained one survives — this run never emptied it")
    _assert("## Live group" not in out, "the heading this run actually drained is still deleted")

    # REGRESSION GUARD (codex review, PR #203) — the exemption above must cover only the file's
    # FIRST heading, not every h1. A second top-level heading ('# Other') is not the root and must
    # cascade like any other prose-only section once its last item drains.
    non_root_h1 = """# Backlog

Root preamble.

# Other

Intro prose for other.

## Group

- [ ] drain me
"""
    out, _ = prune_lines(non_root_h1, ["- [ ] drain me"])
    _assert("# Backlog" in out and "Root preamble." in out,
            "(f) the file's actual root heading and its preamble survive")
    _assert("## Group" not in out, "(f) the drained h2 is deleted")
    _assert("# Other" not in out and "Intro prose for other." not in out,
            "(f) a non-root h1 drained to prose-only cascades away — it is not exempt")

    # A file whose first heading is an h2 (no h1 at all): that h2 IS the root and must be exempt.
    no_h1_root = """## Overview

Preamble with no h1 wrapper.

- [ ] [FIX] only item
"""
    out, _ = prune_lines(no_h1_root, ["- [ ] [FIX] only item"])
    _assert("## Overview" in out and "Preamble with no h1 wrapper." in out,
            "(g) the file's first heading is exempt even when it is not an h1")

    no_h1_schema_only = "## Overview\n\n- [ ] only item\n"
    out, _ = prune_lines(no_h1_schema_only, ["- [ ] only item"])
    _assert("## Overview" in out, "(g2) a schema-only non-h1 root is not rewritten byte-empty")

    no_h1_root_with_child = """## Overview

### Sub

- [ ] only item
"""
    out, _ = prune_lines(no_h1_root_with_child, ["- [ ] only item"])
    _assert("## Overview" in out and "### Sub" not in out,
            "(g3) a non-h1 root with a drained child still keeps the root heading")

    # REGRESSION GUARD (claude review, PR #192) — heading detection honours fences/comments but
    # line matching did not, so the commented-out `- [ ] Simplest case` template harness-init
    # seeds into backlog.md collided with a real item worded the same way. With the ambiguity
    # guard above that collision blocks a legitimate deletion outright.
    commented_template = """# Backlog

<!--
## Feature Name
- [ ] Simplest case
-->

## Real group

- [ ] Simplest case
"""
    out, problems = prune_lines(commented_template, ["- [ ] Simplest case"])
    _assert(problems == [], "a commented-out template line is not a competing match")
    _assert("## Real group" not in out and "<!--" in out,
            "the real item is deleted (emptying its heading) while the comment is left intact")

    fenced_item = """## Real group

```markdown
- [ ] Simplest case
```

- [ ] Simplest case
"""
    out, problems = prune_lines(fenced_item, ["- [ ] Simplest case"])
    _assert(problems == [] and "```markdown" in out,
            "a fenced sample line is not a competing match either, and survives the deletion")

    fenced = """## Real group

```markdown
## Fake heading in a sample
```

- [ ] [FIX] real item after a fence
"""
    out, _ = prune_lines(fenced, ["- [ ] [FIX] real item after a fence"])
    _assert("## Real group" in out and "## Fake heading in a sample" in out,
            "a fenced `##` is not a deletion boundary, so the real heading is not falsely emptied")

    seam = "## A\n\n- [ ] x\n- [ ] y\n\n## B\n\n- [ ] z\n"
    out, _ = prune_lines(seam, ["- [ ] x"])
    _assert(out == "## A\n\n- [ ] y\n\n## B\n\n- [ ] z\n",
            "deleting one of several items leaves the surrounding blank lines exactly as they were")

    # REGRESSION GUARD (qa-verifier) — two sections can hold identically worded items with only
    # one of them done. Deleting both silently discards live work, so an ambiguous match is fatal.
    # The zero-match case was already refused; the multi-match case is strictly more dangerous.
    duplicate = """## Group A

- [ ] [FIX] update the docs

## Group B

- [ ] [FIX] update the docs
"""
    unchanged, problems = prune_lines(duplicate, ["- [ ] [FIX] update the docs"])
    _assert(len(problems) == 1 and "2 lines match verbatim" in problems[0],
            "a target matching two lines is refused as ambiguous, not applied to both")
    _assert(bool(problems) and "lines 3, 7" in problems[0],
            "the ambiguity message names the 1-based line numbers so the caller can disambiguate")
    _assert(unchanged == duplicate, "an ambiguous run deletes nothing at all")

    # REGRESSION GUARD (qa-verifier) — markdown is not LF-pinned here (docs/conventions.md), so a
    # Windows checkout arrives CRLF. Rewriting every terminator would touch regions this run never
    # edited, breaking the byte-identical guarantee for untouched sections.
    crlf = "## A\r\n\r\n- [ ] x\r\n- [ ] y\r\n\r\n## B\r\n\r\n- [ ] z\r\n"
    out, _ = prune_lines(crlf, ["- [ ] x"])
    _assert(out == "## A\r\n\r\n- [ ] y\r\n\r\n## B\r\n\r\n- [ ] z\r\n",
            "CRLF survives prune_lines — every surviving line keeps its own terminator")
    _assert("\n" not in out.replace("\r\n", ""), "no bare LF is introduced into a CRLF file")

    crlf_block = "# One\r\n\r\nstatus: active\r\n\r\n# Two\r\n\r\nstatus: open\r\n"
    out, _ = prune_h1_block(crlf_block, "One")
    _assert(out == "# Two\r\n\r\nstatus: open\r\n", "CRLF survives prune_h1_block")

    crlf_log = "# Changelog\r\n\r\n## Unreleased\r\n\r\n- [done] older (2026-08-01)\r\n"
    out, _ = insert_changelog_entry(crlf_log, "- [done] newer (2026-08-03)")
    _assert(
        out == "# Changelog\r\n\r\n## Unreleased\r\n\r\n- [done] newer (2026-08-03)\r\n"
               "- [done] older (2026-08-01)\r\n",
        "CRLF survives insert_changelog_entry, and the inserted line uses the file's own ending",
    )
    mixed = "# Changelog\n\n## Unreleased\n"
    out, _ = insert_changelog_entry(mixed, "- [done] x (2026-08-03)")
    _assert("\r" not in out, "an LF file stays LF — the dominant ending decides, not a hardcoded one")

    no_final_newline = "## A\n\n- [ ] x\n- [ ] y"
    out, _ = prune_lines(no_final_newline, ["- [ ] x"])
    _assert(out == "## A\n\n- [ ] y\n",
            "a file with no trailing newline gains one from the dominant ending, losing no content")

    # ---- orphaned blockers ----
    print("\nTest 3c: prune-backlog — surviving `*(blocked by: …)*` markers naming a pruned item")
    orphan_src = """## Sink lock

- [ ] [FEAT] Make the skill-run sink lock durable
- [ ] [HARNESS] Report lock contention *(blocked by: 3-sink-lock)*
"""
    done = "- [ ] [FEAT] Make the skill-run sink lock durable"
    pruned, problems = prune_lines(orphan_src, [done])
    _assert(problems == [], "the orphan fixture prunes cleanly (guards the fixture, not the feature)")
    warnings = orphaned_blockers(orphan_src, [done], pruned, "backlog.md")
    _assert(
        len(warnings) == 2
        and warnings[0].startswith("Warning: pruned item `make-the-skill-run-sink-lock-durable`")
        and warnings[1] == "  backlog.md:3: - [ ] [HARNESS] Report lock contention *(blocked by: 3-sink-lock)*",
        "a marker naming the pruned item is reported with the item's slug and the surviving line",
    )
    _assert(
        orphaned_blockers(orphan_src, [done], pruned, "backlog.md")
        == orphaned_blockers(orphan_src, [done], pruned, "backlog.md"),
        "detection is deterministic — same input, same warning lines",
    )

    # The blocker is named by the HEADING the run drained away, not by the item's own wording.
    heading_src = """# Backlog

## Sink lock

- [ ] [FEAT] Make it durable

## Follow-ups

- [ ] [HARNESS] Report contention *(blocked by: sink-lock)*
"""
    only_item = "- [ ] [FEAT] Make it durable"
    pruned_heading, _ = prune_lines(heading_src, [only_item])
    _assert(
        any("`sink-lock`" in w for w in orphaned_blockers(heading_src, [only_item], pruned_heading, "b.md")),
        "a marker naming a heading this run emptied away is orphaned too, not only an item slug",
    )

    # Negative cases — each must stay silent.
    placeholder_src = """## Docs

- [ ] [FEAT] Make the sink lock durable
- [ ] [HARNESS] Document the `*(blocked by: <slug>)*` marker format
"""
    ph_done = "- [ ] [FEAT] Make the sink lock durable"
    ph_pruned, _ = prune_lines(placeholder_src, [ph_done])
    _assert(
        orphaned_blockers(placeholder_src, [ph_done], ph_pruned, "backlog.md") == [],
        "a placeholder marker in prose is a syntax example, never an orphaned blocker",
    )

    fenced_src = """## Docs

- [ ] [FEAT] Make the sink lock durable
- [ ] [HARNESS] Explain the syntax

```markdown
- [ ] [FEAT] later work *(blocked by: 3-sink-lock)*
```
"""
    f_pruned, _ = prune_lines(fenced_src, [ph_done])
    _assert(
        orphaned_blockers(fenced_src, [ph_done], f_pruned, "backlog.md") == [],
        "a marker inside a fenced sample is markup — the same masked view the pruner deletes by",
    )

    alive_src = """## Group

- [ ] [FEAT] Add sink lock telemetry
- [ ] [FEAT] Add sink lock telemetry dashboards
- [ ] [HARNESS] Report contention *(blocked by: sink-lock-telemetry)*
"""
    a_done = "- [ ] [FEAT] Add sink lock telemetry"
    a_pruned, _ = prune_lines(alive_src, [a_done])
    _assert(
        orphaned_blockers(alive_src, [a_done], a_pruned, "backlog.md") == [],
        "a surviving open item still answering to the slug means the blocker is alive, not orphaned",
    )

    deferred_src = """## Group

- [ ] [FEAT] Make the sink lock durable
- [ ] [HARNESS] Report contention *(deferred: sink lock ops values missing)*
"""
    d_pruned, _ = prune_lines(deferred_src, [ph_done])
    _assert(
        orphaned_blockers(deferred_src, [ph_done], d_pruned, "backlog.md") == [],
        "a *(deferred: …)* marker names a reason, not an item, so it is never orphaned",
    )

    numeric_src = """## Group

- [ ] [FEAT] Make the sink lock durable
- [ ] [HARNESS] Report contention *(blocked by: 3)*
"""
    n_pruned, _ = prune_lines(numeric_src, [ph_done])
    _assert(
        orphaned_blockers(numeric_src, [ph_done], n_pruned, "backlog.md") == [],
        "a number-only payload identifies nothing and must match nothing, not everything",
    )
    # REGRESSION (PR #269 review): the real PR #258 shape — an abbreviated marker slug against a
    # slugified heading full of stopwords is an order-preserving subsequence, never a contiguous
    # run. A contiguous test passed every fixture above and missed the only case that mattered.
    real_src = """# Backlog

## Sink lock

- [ ] [HARNESS] skill-run sink has no cross-process append lock

## Follow-ups

- [ ] [HARNESS] Report contention *(blocked by: skill-run-sink-cross-process-append-lock)*
"""
    real_done = "- [ ] [HARNESS] skill-run sink has no cross-process append lock"
    real_pruned, _ = prune_lines(real_src, [real_done])
    _assert(
        any("cross-process-append-lock" in w for w in
            orphaned_blockers(real_src, [real_done], real_pruned, "backlog.md")),
        "REGRESSION: an abbreviated marker slug matches its subject as a subsequence, not a run",
    )

    # REGRESSION (PR #269 review): two items waiting on one pruned blocker are fellow blockees, not
    # each other's blocker. Counting them as live silenced both and left both unselectable.
    siblings_src = """# Backlog

## Group

- [ ] [FEAT] Make the sink lock durable
- [ ] [FEAT] Sink lock telemetry *(blocked by: sink-lock)*
- [ ] [FEAT] Sink lock dashboards *(blocked by: sink-lock)*
"""
    sib_done = "- [ ] [FEAT] Make the sink lock durable"
    sib_pruned, _ = prune_lines(siblings_src, [sib_done])
    sib_out = orphaned_blockers(siblings_src, [sib_done], sib_pruned, "backlog.md")
    _assert(
        len(sib_out) == 3 and sum(1 for w in sib_out if w.startswith("  ")) == 2,
        "REGRESSION: sibling blockees do not suppress each other — both orphaned lines are reported",
    )

    # REGRESSION (PR #269 review): a marker on a continuation line belongs to its own item, so the
    # owner test must resolve it back to the checkbox line or the item silences itself.
    wrapped_src = """# Backlog

## Group

- [ ] [FEAT] Make the sink lock durable
- [ ] [FEAT] Report contention, a long item whose marker wrapped
      onto the next line *(blocked by: sink-lock)*
"""
    wrapped_pruned, _ = prune_lines(wrapped_src, [sib_done])
    _assert(
        any(w.startswith("  ") for w in
            orphaned_blockers(wrapped_src, [sib_done], wrapped_pruned, "backlog.md")),
        "REGRESSION: a marker on a continuation line still reports — the item is not its own blocker",
    )

    # REGRESSION (PR #269 review): a landed `[x]` blocker is exactly the marker that needs clearing.
    closed_src = """# Backlog

## Group

- [ ] [FEAT] Make the sink lock durable
- [x] [FEAT] Make the sink lock durable again
- [ ] [FEAT] Report contention *(blocked by: sink-lock)*
"""
    closed_pruned, _ = prune_lines(closed_src, [sib_done])
    _assert(
        any(w.startswith("  ") for w in
            orphaned_blockers(closed_src, [sib_done], closed_pruned, "backlog.md")),
        "REGRESSION: a closed [x] item is not a live blocker, so it cannot suppress the warning",
    )

    _assert(
        orphaned_blockers("", [], "", "backlog.md") == []
        and orphaned_blockers(orphan_src, [], pruned, "backlog.md") == []
        and orphaned_blockers(orphan_src, [done], "```\n", "backlog.md") == [],
        "empty and malformed input yield no warnings rather than a crash or a false report",
    )

    # ---- prune-tasks h1 block ----
    print("\nTest 4: prune-tasks — h1 sprint blocks")
    tasks = """# Sprint one

status: active

## Scope

body content

# Sprint two

status: open
"""
    out, problems = prune_h1_block(tasks, "Sprint one")
    _assert(problems == [] and out.startswith("# Sprint two"),
            "the whole h1 block — heading, status, body — is deleted up to the next h1")
    _assert("## Scope" not in out and "body content" not in out, "the block's body goes with it")

    out, problems = prune_h1_block(tasks, "Sprint three")
    _assert(len(problems) == 1 and "no h1 block titled" in problems[0] and "'Sprint one'" in problems[0],
            "a missing title is refused and the available titles are named")

    only, _ = prune_h1_block("# Only\n\nstatus: active\n", "Only")
    _assert(only == "", "deleting the last block leaves an empty string, which the CLI turns into a file delete")

    # ---- CLI file handling ----
    print("\nTest 5: CLI — file write and delete-if-empty")
    with tempfile.TemporaryDirectory() as td:
        p = Path(td) / "tasks.md"
        p.write_text("# Only\n\nstatus: active\n", encoding="utf-8")
        msg = _write_or_delete(p, "", delete_if_empty=True)
        _assert(not p.exists() and "deleted" in msg, "prune-tasks deletes a file left with no content")

        b = Path(td) / "backlog.md"
        b.write_text("# Backlog\n", encoding="utf-8")
        _write_or_delete(b, "", delete_if_empty=False)
        _assert(b.exists() and b.read_text(encoding="utf-8") == "",
                "prune-backlog never deletes backlog.md — it is a prerequisite file")

        # REGRESSION GUARD: the mixed-content shape that used to delete all of tasks.md.
        # `## Review Backlog` sits AFTER the sprint h1, so the h1-to-EOF boundary swallows it and
        # `delete_if_empty` unlinks the file. prune-tasks must refuse and change nothing.
        mixed = Path(td) / "tasks.md"
        mixed_text = (
            "# Fix the thing\n\nstatus: active\n\n## Covers\n\n- [ ] [FIX] a\n\n"
            "## Review Backlog\n\n### PR #101 — earlier PR (2026-07-01)\n\n"
            "- [ ] [debt] leftover finding\n"
        )
        mixed.write_text(mixed_text, encoding="utf-8")
        rc = main(["prune-tasks", "--file", str(mixed), "--block", "Fix the thing"])
        _assert(
            rc == 1 and mixed.exists() and mixed.read_text(encoding="utf-8") == mixed_text,
            "prune-tasks refuses a tasks.md holding a persistent section, leaving it untouched",
        )
        _assert(
            persistent_sections(mixed_text) == ["Review Backlog", "PR #101 — earlier PR (2026-07-01)"]
            and persistent_sections("# S\n\nstatus: active\n\n## Scope\n\n- [ ] x\n") == []
            and persistent_sections("## Security Fixes — my-webapp\n\n- [ ] rotate\n")
            == ["Security Fixes — my-webapp"],
            "the guard matches findings sections (suffix and all), not a contract's ## Scope",
        )
        # REGRESSION GUARD: the pruner and the candidate scanner share ONE predicate. Before
        # this, a grab-bag h2 was warned about by one and deleted by the other.
        grab = "# S\n\nstatus: active\n\n## Scope\n\n- [ ] a\n\n## Follow-ups\n\n- [ ] leftover\n"
        gr = Path(td) / "grab.md"
        gr.write_text(grab, encoding="utf-8")
        rc = main(["prune-tasks", "--file", str(gr), "--block", "S"])
        _assert(
            rc == 1 and gr.exists() and gr.read_text(encoding="utf-8") == grab,
            "prune-tasks refuses an ad-hoc grab-bag section too, not only the two known titles",
        )
        _assert(
            persistent_sections("# S\n\nstatus: active\n\n```markdown\n## Review Backlog\n```\n")
            == [],
            "a findings heading inside a fence does not trigger a false refusal",
        )
        # prune-backlog must NOT refuse — backlog.md is where these sections belong.
        bl = Path(td) / "bl.md"
        bl.write_text("## Review Backlog\n\n- [ ] [debt] one\n- [ ] [debt] two\n", encoding="utf-8")
        real_stdin, sys.stdin = sys.stdin, io.StringIO("- [ ] [debt] one\n")
        try:
            rc = main(["prune-backlog", "--file", str(bl)])
        finally:
            sys.stdin = real_stdin
        _assert(
            rc == 0 and "- [ ] [debt] two" in bl.read_text(encoding="utf-8"),
            "prune-backlog still prunes a `## Review Backlog` section in backlog.md",
        )

        c = Path(td) / "CHANGELOG.md"
        rc = main(["changelog", "--file", str(c), "--title", "t", "--plugin", "dev",
                   "--version", "1.0.0", "--date", "2026-08-03"])
        _assert(rc == 0 and c.read_text(encoding="utf-8").endswith("- [done] t (dev v1.0.0) (2026-08-03)\n"),
                "the changelog subcommand creates the file end-to-end")
        _assert(find_entry_checker(Path(td)) is None,
                "a tree with no scripts/ci/ checker reports None, and the write above proceeded")

        # ---- changelog: write-time contract validation ----
        print("\nTest 6: changelog — the entry is validated before the file is written")
        stub = (
            "def check_file(path, *, check_links=True):\n"
            "    out = []\n"
            "    for i, line in enumerate(path.read_text().splitlines(), 1):\n"
            "        if line.startswith('- [done]') and len(line) > 40:\n"
            "            out.append(f'{path}:{i}: entry is {len(line)} chars (max 40)')\n"
            "    return out\n"
        )
        repo = Path(td) / "repo"
        (repo / "scripts" / "ci").mkdir(parents=True)
        (repo / "scripts" / "ci" / "check_changelog_entries.py").write_text(stub, encoding="utf-8")
        _assert(
            find_entry_checker(repo) == (repo / "scripts" / "ci" / "check_changelog_entries.py").resolve(),
            "the checker is discovered by walking up from the changelog's own directory",
        )
        rc_bad = main(["changelog", "--file", str(repo / "CHANGELOG.md"),
                       "--title", "x" * 60, "--date", "2026-08-03"])
        _assert(
            rc_bad == 1 and not (repo / "CHANGELOG.md").exists(),
            "a violating entry exits 1 and the CHANGELOG is never created",
        )
        rc_ok = main(["changelog", "--file", str(repo / "CHANGELOG.md"),
                      "--title", "short", "--date", "2026-08-03"])
        _assert(
            rc_ok == 0 and "- [done] short (2026-08-03)" in (repo / "CHANGELOG.md").read_text(encoding="utf-8"),
            "a compliant entry is written exactly as before the gate existed",
        )
        _assert(
            validate_entry("- [done] " + "y" * 60, repo / "scripts" / "ci" / "check_changelog_entries.py")
            == ["entry is 69 chars (max 40)"],
            "the probe's own tempfile path:lineno prefix is stripped from the reported violation",
        )

        nested = repo / "vendor" / "inner"
        (nested / ".git").mkdir(parents=True)
        _assert(
            find_entry_checker(nested) is None,
            "the walk stops at a .git boundary — a nested checkout does not inherit the outer cap",
        )

        # Every way a foreign checker can misbehave degrades to "unchecked", never to a
        # traceback out of the cycle tail: unimportable, exiting at import (no __main__ guard),
        # raising inside check_file, or taking arguments this caller does not pass.
        for name, body in [
            ("broken", "this is not python(\n"),
            ("exits", "import sys\nsys.exit(2)\n"),
            ("raises", "def check_file(path, *, check_links=True):\n    raise RuntimeError('boom')\n"),
            ("mismatch", "def check_file(path, root):\n    return []\n"),
            ("returns_none", "def check_file(path, *, check_links=True):\n    return None\n"),
        ]:
            bad = Path(td) / name
            (bad / "scripts" / "ci").mkdir(parents=True)
            (bad / "scripts" / "ci" / "check_changelog_entries.py").write_text(body, encoding="utf-8")
            rc_bad_checker = main(["changelog", "--file", str(bad / "CHANGELOG.md"),
                                   "--title", "t", "--date", "2026-08-03"])
            _assert(
                rc_bad_checker == 0 and (bad / "CHANGELOG.md").is_file(),
                f"a checker that {name} degrades to writing unchecked rather than blocking the cycle",
            )

        # The probe lands wherever TMPDIR points, and that path can hold spaces.
        spaced = Path(td) / "tmp dir with spaces"
        spaced.mkdir()
        prev_tmpdir = os.environ.get("TMPDIR")
        os.environ["TMPDIR"] = str(spaced)
        try:
            spaced_out = validate_entry(
                "- [done] " + "y" * 60, repo / "scripts" / "ci" / "check_changelog_entries.py")
        finally:
            if prev_tmpdir is None:
                del os.environ["TMPDIR"]
            else:
                os.environ["TMPDIR"] = prev_tmpdir
        _assert(
            spaced_out == ["entry is 69 chars (max 40)"],
            "the prefix strip survives a probe path containing spaces",
        )

    # End-to-end against this repo's real checker, when task_nodes.py is running from a checkout
    # that has one. Skipped from a plugin cache — there is no checker above it to find.
    real = find_entry_checker(Path(__file__).resolve().parent)
    if real is None:
        print("  SKIP: no scripts/ci/check_changelog_entries.py above this script")
    else:
        _assert(
            validate_entry("- [done] " + "z" * 200 + " (2026-08-03)", real) != [],
            "the real checker rejects an over-cap entry through validate_entry",
        )
        _assert(
            validate_entry("- [done] t (2026-08-03) → docs/a.md → docs/b.md", real) != [],
            "the real checker rejects the two-link entry that PR #216 shipped past authorship",
        )
        _assert(
            validate_entry("- [done] t (dev v1.0.0) (2026-08-03) → docs/conventions.md", real) == [],
            "a compliant entry with a resolving link passes the real checker",
        )

        # A violation the insertion CREATES: the indented line was legal until an entry landed
        # directly above it and turned it into that entry's continuation.
        good = "- [done] fine (2026-08-03)"
        adopts = "# Changelog\n\n## Unreleased\n\n  a note nobody indented on purpose\n"
        with_doc, _ = insert_changelog_entry(adopts, good)
        _assert(
            validate_entry(good, real, document=with_doc) != [],
            "an entry that turns the line below it into a continuation is caught in context",
        )
        _assert(
            validate_entry(good, real) == [],
            "the same entry probed alone looks clean — which is why context is checked",
        )
        # ...and a pre-existing violation elsewhere is not this write's to block.
        stale = "# Changelog\n\n## Unreleased\n\n- [done] " + "q" * 200 + " (2026-01-01)\n"
        with_stale, _ = insert_changelog_entry(stale, good)
        _assert(
            validate_entry(good, real, document=with_stale) == [],
            "an over-cap entry already in the file does not block an unrelated new entry",
        )

    print(f"\n=== Results: {PASS_COUNT} PASS, {FAIL_COUNT} FAIL ===")
    return 0 if FAIL_COUNT == 0 else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
