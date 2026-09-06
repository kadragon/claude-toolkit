---
name: harness-capture
description: >-
  Retrospect on the CURRENT conversation — route any reusable lesson to docs/,
  auto-memory, or CLAUDE.md/AGENTS.md, and tidy the auto-memory store. Also the
  signal-gated retrospect in task-review. Cross-session mining → harness-curate.
version: 3.1.0
---

# Capture Learnings — session retrospective

The **warm path**: reflect on this one session from your own context and decide whether
anything earned a write-back. `harness-curate` is the cold path — cross-session transcript
mining. Use that for "what should I build across all my work"; use this for "did I just learn
something worth saving?"

## When to use

- The user asks to reflect on, or capture learnings from, the current session.
- The review cycle calls it because a correction, gotcha, or reusable workflow surfaced.
- The user asks to tidy the auto-memory store → jump to **Memory hygiene**.

Not for cross-project audits, unused-skill cleanup, or building a named asset → `harness-curate`
/ `skill-creator`.

## Capture on the spot

The retrospective's failure mode was never a bad gate — it was that nothing reached the gate. A
signal noticed at turn 12 and recalled at turn 90 is a signal a compaction can delete, and
"remember to mention this later" fails hardest in exactly the sessions that produce the best
material. So the capture is a write, at the moment, not an intention:

```sh
SKILL_DIR="<absolute parent directory of the loaded SKILL.md>"
NOTES="$SKILL_DIR/scripts/session_notes.py"
python3 "$NOTES" add --title "flush trigger never fired" \
  --issue "what happened, specific enough to reconstruct without this conversation" \
  --improvement "the concrete delta — name the file and section, or the missing asset" \
  --principle "the generalisable takeaway; blank means it was task context, not a lesson" \
  --target dev:task-next        # optional: the asset the note is about
```

**Two triggers, both bound to something already in the tool record** — never to noticing that a
moment qualifies:

1. **Capture** — a user correction, an error→recovery gotcha, or a workflow you would repeat
   surfaces. Write the note in the same turn or the next.
2. **Flush** — any action declaring a unit of work complete: a commit, a PR opened or merged, a
   final report, a queue item marked done, or this skill being invoked. Run
   `python3 "$NOTES" list`, route what it returns through *How to run* below, then
   `python3 "$NOTES" flush`.

`status` and `list` separate three states, and the exit code is the point: `NO-STORE` (exit 2) —
nothing captured **or** the path is wrong; `EMPTY` (exit 0) — captured and already routed;
`PENDING n` (exit 0). Never read a zero as "quiet session" without the code that says which one
it was. If notes you wrote are missing, stop and check the path before writing anything else.

Store path, note schema, parallel writers, compaction recovery, and the reasoning behind each
rule: `references/session-notes.md`. Load it on first setup, on any unexpected command output,
or after a compaction.

## How to run

1. **Reflect.** Start from `python3 "$NOTES" list` — the notes are the record, not your recall
   of them — then add anything the session produced since the last capture. Three kinds of
   signal: a **reusable workflow** you would repeat across sessions; an **error → recovery**
   that revealed a durable setup gotcha or approach correction; a **user correction** of your
   approach, preference, or style.

2. **Gate.** Capture only if all three hold — **reusable** across sessions, **objectively
   checked** this session (test / exit 0 / verifier), and **not a no-op** (it changes behavior
   versus what the agent does by default; an instruction the model already obeys pays load to say
   nothing — drop it whole, do not trim it to a shorter no-op).

3. **Route**, cheapest standing cost first — take the first row that fits:

   | Kind | Destination | Cost |
   |------|-------------|------|
   | Repeatable mistake with a checkable shape | a hook / lint / test in the owning repo | paid only when it fires |
   | Coding standard / review rule | the review-time asset (a `code-review` rule, the reviewer agent), not the implementer's instruction file | paid once per diff |
   | Reusable workflow | `skill-creator` (new or improved skill) | the `description:` line |
   | Setup/infra fix | `docs/<topic>.md` in the owning repo | one docs-index row |
   | Approach correction / preference | auto-memory (below), or `CLAUDE.md` / `AGENTS.md` only when the fact must be in context before anything asks | memory: recall-gated; instruction file: every turn — the highest bar |
   | Workflow misunderstanding | `skill-creator` improvement to that skill | the `description:` line |
   | Lesson true of every asset of a kind | the owning cross-cutting doc — `docs/writing-for-agents.md` for agent-facing writing, `docs/conventions.md` for shell/commit rules | one doc, not one copy per skill |

   **Check the target's siblings before routing to one of them.** This repo has families — the
   `task-*` cycle skills, the `harness-*` skills, the `scripts/ci/check_*.py` checkers, the
   hooks under `dev/hooks/`. Fast test: could the sentence survive having the asset's name
   removed? If yes, widen the delta to every sibling it fits or say in one line why it does not;
   if it fits them all, it is the cross-cutting row above, not a per-skill edit.

   Mechanism before sentence: a rule expressible as a check goes there first; prose is the
   fallback. Every proposal names in one line **the concrete failure this prevents** ("without
   this: X happens again") — for memory it lands in the body.

4. Nothing clears the gate → say so in one line and stop, then `flush` anyway so the next
   retrospective does not re-litigate the same notes. That is the normal outcome.

**Deferring is a decision, not a neutral hold.** Before writing any "later", name which specific
observation would change the decision and when it could realistically arrive; if you cannot,
the evidence is either conclusive or more of it changes nothing — act now. Routing heavy work to
`backlog.md` is not this pattern: the queue is a place a later session actually reads.

**Writing the delta.** State the positive target ("quote the rule verbatim"), not the prohibition.
For pointer-shaped text (a `MEMORY.md` hook, a docs-index row, a `description:`), front-load a
leading word the model already thinks with, and give each distinct case one trigger — synonyms
pay load for no reach. Longer treatment where present: `docs/writing-for-agents.md`.

## From the review cycle

You are on a feature branch about to commit, so light in-scope write-backs are welcome: a
preference or correction → auto-memory (Codex: `AGENTS.md`); a small doc or gotcha tied to this
change → inline edit to `docs/*.md` / `AGENTS.md`, it rides into the commit. Anything heavy — a
new skill, a skill overhaul, a multi-file rewrite — goes to `backlog.md` as a follow-up. Under
`--auto` do not pause for the per-write veto; the review and CI are the safety net, and a
destructive memory prune is deferred to `backlog.md` rather than blocking.

The cycle's call is itself a flush trigger: read the pending notes first, and `flush` once
they are routed, so the commit and the note store agree about what this session learned.

## Writing to auto-memory

Auto-memory (`# Memory` store + `MEMORY.md` index) is a Claude Code mechanism; on Codex route
the same lesson to `AGENTS.md` with the same rules, and skip hygiene.

1. **Read the index first** for an entry that already covers the fact or sits next to it.
2. **Update over create.** Two files saying almost the same thing is the failure mode.
3. **Earn the entry** — reusable, non-obvious from the repo, not a one-off.
4. **Show the write before applying** — name the file, quote the fact — then write with the
   `Write`/`Edit` tool (a shell redirect bypasses the guard) and refresh the `MEMORY.md` line.
   Pre-check the draft so it can land: write it to a scratch file outside the store, then

   ```sh
   SKILL_DIR="<absolute parent directory of the loaded SKILL.md>"
   DRAFT="<absolute path of the scratch file>"
   GUARD="$SKILL_DIR/../../hooks/memory-guard/guard.py"
   PY=$(command -v python3 || command -v python || true)
   if [ -r "$GUARD" ] && [ -n "$PY" ]; then "$PY" "$GUARD" --check-file "$DRAFT"; else echo "memory-guard unavailable — write, and let the hook judge" >&2; fi
   ```

   Exit `0` clean, `1` finding, `2` the check did not run. The draft goes through a file, never
   the command line (a body containing `$(...)` would execute). Name the draft after the memory —
   `MEMORY.md` alone is exempt from the size cap. A denial is a rewrite, never a bypass.
5. **Opportunistic hygiene** on any stale or contradicted neighbour you notice.

**`metadata.status`** — `active` (write on every new memory; absent reads as active),
`superseded` (replaced by a later memory; kept until the prune is confirmed), `rejected`
(vetoed or disproved; kept so the lesson is not re-learned). The hook denies any other value or
a `status:` outside `metadata:`. Never backfill for its own sake.

## Memory hygiene

Stores collect sediment. Run on the neighbours you touched, or over the whole store on request.
Audit → report → targeted diff → approval; never bulk-delete silently.

| Red flag | Check |
|----------|-------|
| **Stale** | names a file, flag, skill, or path that no longer exists — verify with a grep first |
| **Wrong** | contradicted by what happened this session |
| **Redundant** | two files, one fact — merge into the sharper one |
| **Index drift** | pointer with no file, file with no pointer, hook that no longer matches |
| **Bloat** | restates what the repo/docs/git already record, or never recurred |
| **No-op** | describes the default behavior anyway |

Anything already `superseded`/`rejected` is a prune candidate a previous pass judged — confirm
and delete, no re-read. Present findings compactly (file · flag · action) and apply only what the
user approves. Redundant loser or superseding rewrite → `superseded`; Wrong → `rejected`; Stale,
Bloat, No-op → plain deletion. Leave `MEMORY.md` with exactly one line per surviving file.

Moving a repo-scoped fact *out* of memory into a repo's `docs/` is `harness-curate`'s call; when
it routes a promotion here, it has already written the doc and this skill deletes the memory and
repairs the index.

## Additional Resources

- **`scripts/session_notes.py`** — the note store behind *Capture on the spot*: `add` (the four
  fields are mandatory and rejected empty, over 2000 chars, or carrying control/zero-width/bidi
  characters), `list`, `status`, `flush`. The path resolves from `git rev-parse --git-common-dir`,
  so one repo has one store shared by every worktree — never a cwd-relative path. `--test` covers
  the schema, the exit codes, and the worktree case.
- **`references/session-notes.md`** — store layout, flush-trigger reasoning, parallel writers,
  compaction recovery, the sibling/cross-cutting rules in full, and the CC BY 4.0 attribution for
  the methodology this capture layer adapts.
- **`hooks/memory-guard/guard.py`** — the `PreToolUse(Write|Edit)` gate behind step 4: blocks a
  memory write carrying a secret pattern, a control/bidi/zero-width character, an over-cap body,
  or a bad `status:`; `--check-file <path|->` is the same policy as a CLI. Fails open on
  unparseable payloads. `--test` covers each family.

---

Immediate-capture methodology adapted from *Task Observer ("One Skill to Rule Them All")* by
Eoghan Henn, rebelytics.com — CC BY 4.0. Scope of the adaptation and what was deliberately not
carried over: `references/session-notes.md`.
