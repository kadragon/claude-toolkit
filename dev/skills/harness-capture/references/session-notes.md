# Session Notes — immediate capture, flush triggers, and their failure modes

Load this when setting the note store up for the first time, when a `session_notes.py`
command misbehaves or reports something you did not expect, after a compaction, or when
deciding whether a moment is a flush point. The always-loaded `SKILL.md` carries the four
commands and the two triggers; everything here is the reasoning and the edge cases.

**Attribution.** The immediate-capture discipline in this skill — write the observation the
moment it fires rather than reconstructing it at the end; the Issue → Improvement → Principle
note shape; binding the flush to an event visible in the tool record; treating an empty
instrument reading as a claim about the instrument; and checking a target's siblings before
routing a lesson to one of them — is adapted from **Task Observer ("One Skill to Rule Them
All") by Eoghan Henn, rebelytics.com**, https://github.com/rebelytics/one-skill-to-rule-them-all,
licensed **CC BY 4.0** (https://creativecommons.org/licenses/by/4.0/).

*Changes made in this adaptation:* the persistent cross-session observation log, its numbering
and archival protocol, the weekly review, the skill-authoring and staging rules, and the
handoff-doc mode are **not** carried over — this repo's cross-session mining lives in
`dev:harness-curate` and its skill authoring in `skill-creator`. What remains is a
session-scoped, repo-local note store whose schema and path resolution are enforced by
`scripts/session_notes.py` rather than by prose, feeding this skill's existing routing gate and
memory hygiene. No text is copied verbatim.

## Where the store lives, and why not somewhere else

`scripts/session_notes.py` resolves the path in one fixed order, and no caller may improvise
a different one:

1. `--store PATH` — tests and one-off runs only.
2. `$HARNESS_CAPTURE_STORE` — for a session working outside any git repository.
3. `$(git rev-parse --git-common-dir)/harness-capture/notes.jsonl` — the default.

`--git-common-dir`, not `--git-dir` and never the cwd. A worktree resolves to the main
checkout's git directory, so every worktree of one repo writes to **one** store. The two
failures this closes are both silent: a cwd-relative path started in a subdirectory yields a
second empty store beside a populated one, and every later read of it reports a clean, empty
backlog — the one answer nobody questions. A store inside a temporary worktree (this repo's
`--tree` review fan-out creates them) is deleted with the worktree, taking the session's notes
with it.

The store is untracked by construction: everything under `.git/` is outside the working tree,
so notes never ride into a commit and never need a `.gitignore` entry.

**Parallel writers.** `add` and `flush` both take an exclusive `flock` on a `notes.jsonl.lock`
sidecar, so a capture landing while another agent is flushing is serialised rather than
discarded. The lock is on a sidecar, not on the store: `flush` replaces the store file, and a
descriptor held on a replaced inode guards nothing. Where `fcntl` is unavailable (Windows) the
lock degrades to a no-op — single-writer use is unaffected, and the degradation is stated here
rather than silently assumed away.

Two writers can still land the same `id`. That is a label collision, not data loss — the file
keeps both lines and `list` renders both. Do not "fix" it by pre-computing ids for a batch:
resolve the id at each write, which is what `add` already does.

**Never pass note text as a shell argument.** A note quotes what happened — an error message,
the user's words, a diff — and `$(...)` inside any of that is expanded by the shell before the
script sees it. `add --from-json <file>` (or `-` for stdin) is the route that has no shell in
it; the `--title/--issue/...` flags remain for short text you authored literally.

## The note shape

Four fields, all mandatory, all refused at write time when empty:

| Field | What goes in it |
|-------|-----------------|
| `title` | one line, so a later scan can tell the notes apart |
| `issue` | what actually happened, specific enough to reconstruct weeks later without this conversation |
| `improvement` | the concrete delta — name the file and the section, or the asset that does not exist yet |
| `principle` | the generalisable takeaway; this is the field the routing gate reads |

`--target` is optional and names the asset the note is about (`dev:task-next`,
`docs/conventions.md`, a hook). The write also rejects a control, zero-width or bidi character
and caps each field at 2000 characters — a note is replayed into an agent's context at flush
time, so it gets the same hygiene as any other shipped text.

**`principle` is the field that decides the note's fate**, which is why it cannot be blank.
"The user preferred one shared module in this repo" is task context. "The skill lacks guidance
for deciding when a module should be centralised" is a principle, and routes. If the principle
cannot survive having this repo's name removed from it, the note is task context — do not
capture it.

## Why the flush hangs on events, not on judgement

The retrospective's old failure mode was not a bad gate; it was that nothing reached the gate.
A rule of the form "notice when something worth capturing happens, and remember it until the
end" fails exactly under the load that produces the best signals, and a compaction erases the
evidence that anything was pending.

So both triggers in `SKILL.md` name an action already present in the tool record:

- **Capture trigger** — a correction, a gotcha, or a repeated workflow surfaces. Write the note
  in the same turn or the next. The write *is* the enforcement; a remembered intention is not.
- **Flush trigger** — any action by which a unit of work is declared complete: a commit, a PR
  opened or merged, a final report, a queue item marked done, this skill being invoked by the
  review cycle. Each is a tool call you were making anyway, so the flush costs one extra
  command.

The flush point is a **property, not a command list**. A list of commands inherits the shape of
the sessions it was derived from and is silently inert in any session that declares completion
some other way. Likewise, a trigger bound to a single tool (a todo counter, a commit hook) is
inert in every session that does not use that tool, which is why the two triggers above are
independent paths rather than one.

**Before trusting a new trigger, run the literal string the project actually produces through
it, plus one real non-event from the tool record.** Invented examples sample the author's model
of the input — the same model that produced the gap.

## Reading the store: an empty result is a claim about the instrument

`status` and `list` report three states, and only one of them is "nothing happened":

| Output | Exit | Means |
|--------|------|-------|
| `NO-STORE <path>` | 2 (`status`/`list`), 0 (`flush`) | the file does not exist — nothing was ever captured, **or** the path is wrong |
| `EMPTY <path> (n note(s), all flushed)` | 0 | the store exists and every note has been routed |
| `PENDING n <path>` | 0 | n notes are waiting for the retrospective |

The distinction is the whole point of the exit code. A single `0` for both of the first two
rows would let a broken path read as a quiet session, and a broken path is the failure that
lasts the rest of the session. Likewise `read_notes` raises on a malformed line, or on a record missing one of the four body
fields, rather than skipping it: a store that parses to zero notes because it is corrupt must
not present as a store with zero notes.

Generalise it past this script — **every retrieval reports on two possibilities at once (the
data is absent, or the question never got asked), and only the second is a defect a "0"
conceals.** A count in a hook, a grep in a skill, a query in a checker: pair each with a second
count derived by different means, and halt on the disagreement. Stated as a property of
instruments it covers the instrument nobody has written yet; enumerated per snippet it is
absent from the next one by construction.

## After a compaction

Do not reconstruct what was captured. Run `status`, then `list` — the store is the record, and
that is what it is for. If notes you know you wrote are missing, stop and check the path before
writing anything: a `NO-STORE` where content existed earlier in the session means the path
moved, not that the notes were imaginary. Never let a fresh `add` silently recreate a store at
a different path.

**A denied or failed write is not a read-only store.** Retry once, and try a second interface
that reaches the same path, before concluding the store is unwritable — a permission classifier
can deny one interface while allowing another. Report "failed N times", never "cannot be done",
until retries and alternatives are actually exhausted; otherwise the rest of the session's
notes are lost silently.

## Siblings: check the family before routing to one member

An insight found while using one asset usually applies to its siblings, and nothing in the
routing table asks. This repo has real families: the `task-*` cycle skills, the `harness-*`
skills, the `scripts/ci/check_*.py` checkers, the hooks under `dev/hooks/`. Before routing a
note to one member, resolve it against the family and either widen the delta or state in one
line why it does not apply.

Fast test: **could this sentence survive having the asset's name removed?** If yes it belongs to
every sibling. A rule that declares itself generic inside one artefact ("this applies to any
file-writing script, not just X") is the cheapest possible propagation signal — treat that
phrasing as an automatic multi-asset flag.

A lesson that applies to *every* asset of a kind is not a sibling case at all; it is
cross-cutting, and it goes to the owning cross-cutting doc (`docs/writing-for-agents.md` for how
agent-facing documents are written, `docs/conventions.md` for shell and commit rules), never
copied into each skill.

## Deferral is a decision and needs the same justification as acting

"Let's wait until this has seen a few days of real use" reads as diligence, which is why it goes
unchallenged. Before writing any "later" into a routing decision, name two things: **which
specific observation would change the decision, and when it could realistically arrive.** If you
cannot name one, the evidence is either already conclusive or more of it changes nothing — act
now. A criterion you can name must also be able to occur: ask who would have to act for it to
fire, and whether they have reason to do the opposite. A deferral whose criterion cannot fire is
a silent drop wearing a precise phrasing.

Deferring to `backlog.md` is a legitimate route for heavy work — a new skill, a multi-file
rewrite — and it is not this pattern, because the queue is a place a later session actually
reads.
