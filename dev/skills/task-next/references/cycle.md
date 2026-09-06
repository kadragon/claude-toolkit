# Code cycle — shared by `task-next` and `task-new`

One implementation cycle from branch to hand-off. The calling `SKILL.md` names the item(s) and
the overrides that apply; everything else is here. `CYCLE_DIR` below is the absolute directory
of this file (`task-next/references/`), whichever skill is reading it.

## Branch

```bash
CYCLE_DIR="<absolute directory of this cycle.md>"
NODES="$CYCLE_DIR/../scripts/task_nodes.py"
[[ -r "$NODES" ]] || { echo "Bundled script missing or unreadable: $NODES" >&2; exit 1; }
# queue item(s): pipe each selected item line verbatim; free-text request: pass --tag <TYPE> and no stdin
BRANCH=$(printf '%s\n' "<each selected item line, verbatim>" | python3 "$NODES" branch --title "<title>")
git checkout -b "$BRANCH"
```

The script applies the shared-`[type]`-else-`fix/` rule and warns on stderr when it falls back.

## Scope

Look yourself first — one or two searches. Spawn `explorer` (or the built-in `Explore` when no
such role exists) only when the survey means reading 10+ files or would flood the main context.

## Plan gate

**Non-trivial** — tag is `[FEAT]` or `[REFACTOR]`, or 3+ files, or a new public API/schema:
load plan mode (`ToolSearch` `select:EnterPlanMode,ExitPlanMode`), call `EnterPlanMode`, design
the approach, **write the drafted Sprint Contract into the plan body**, call `ExitPlanMode`. One
approval covers approach and contract. No plan-mode tools → present the plan as a numbered list
and wait for "proceed". **Trivial** — skip. **Unattended run** (subagent, `/loop`, cron) — skip
plan mode, record the plan in the transcript and the PR body, announce, proceed.

## Sprint Contract

```markdown
**Tag:** [FEAT] | [REFACTOR] | [FIX] | [TEST] | [CONSTRAINT] | [DOCS] | [HARNESS] | [PLAN]
**Scope:** files or areas this change touches
**Acceptance criteria:**
- [ ] one concrete, testable criterion per item
**Out of scope:** explicit exclusions
**Lint/test command:** the command that must exit 0
```

The Tag is what the reviewer grades a `[FIX]` reproduction criterion against — write it in. A
`[FIX]` contract names the test that fails before and passes after. A multi-item group gets one
checkbox per item. The contract is authored inline in the conversation unless the calling skill
says to write `tasks.md` (it does so only when a `## Covers` deletion list is needed).

## Implement

Inline by default. Delegate to `implementer` only past the global gate — 10+ files or 3+
independent units (`docs/delegation.md`) — with a brief carrying the contract, absolute paths of
every in-scope file, and the lint/test command; the implementer never verifies its own output,
and reports through its final output, the only channel a role-file agent has
(`docs/delegation.md`); brief it never to finish silently. Rules either way:

- **Per-item checkpoint** — for a multi-item group, run the lint/test command after each item
  before starting the next. Do not commit per item.
- **Stuck-fix stop** — the same fix attempted 3+ times on one file without the command passing →
  stop and report.
- **Destructive-command guard** — never `git push --force`/`--force-with-lease`, `git reset
  --hard`, `git clean -f`/`-fd`, or `git branch -D` while implementing. Stop and ask instead.
- An implementer that fails or returns unusable output → stop and report.

Verification is not run here: the review cycle's reviewer grades the diff against the contract
(hand-off below). `--tree` and `--all` verify per worktree before collapsing, per their references.

## Version bump

After all changes, before hand-off. Judge *which* plugin and *which* level; the rewrite is scripted:

```bash
[[ -f scripts/bump-version.sh ]] && bash scripts/bump-version.sh <plugin> <major|minor|patch> \
  [--skill <name> <major|minor|patch>]
```

Pass `--skill` when the change touched that skill's own `SKILL.md`; the script takes one `--skill`
per run, so a second skill's `version:` is edited by hand. Rules: the script header, or
`docs/conventions.md` → *Plugin Version Bump Rules* where it exists. No script → edit the
manifests by hand; no `plugin.json` → skip; neither script nor conventions doc → ask the user
for the level (never default it, even unattended).

## Cleanup

Leave everything uncommitted — it lands in the review cycle's first commit. Your judgment is
*which* lines are done; the edits are scripted and refuse (exit 1, nothing written) on an
ambiguous or non-verbatim match — re-read and re-run rather than loosening the input.

```bash
CYCLE_DIR="<absolute directory of this cycle.md>"
NODES="$CYCLE_DIR/../scripts/task_nodes.py"
[[ -r "$NODES" ]] || { echo "Bundled script missing or unreadable: $NODES" >&2; exit 1; }
# only when a sprint block exists in tasks.md
python3 "$NODES" prune-tasks --file tasks.md --block "<h1 title>"
# the backlog.md line(s) this cycle completed, verbatim
printf '%s\n' "<each completed - [ ] line>" | python3 "$NODES" prune-backlog --file backlog.md
# one CHANGELOG entry; drop --plugin/--version in a repo with no versioned plugin
python3 "$NODES" changelog --file CHANGELOG.md --title "<title>" \
  --plugin <plugin> --version <X.Y.Z> [--link docs/<owning-doc>.md]
```

A heading is dropped only where this cycle emptied it. `changelog` validates the line against the
repo's `scripts/ci/check_changelog_entries.py` (one line, ≤160 chars, at most one `→` link, no
explanatory clauses).

`prune-backlog` exits 0 but warns on stderr when a surviving `*(blocked by: …)*` marker still names
an item or heading it just deleted — this cycle *was* the blocker. Act on that warning: delete the
named marker before you hand off, or the marked item is invisible to candidate selection with
nothing left to clear it. The warning is advisory because the marker's rewording is a judgment
call, so an unread one is a silently unselectable item.

**Blocked-marker sync** (queue items only, scoped to items inspected this run): an item you
verified is blocked and carries no marker → append `*(blocked by: <slug>)*` or `*(deferred:
<reason>)*`. A marker whose blocker you can see has landed (`[x]`, or removed in git) → delete
it. Match on the slug by judgment, never on a numeric prefix; failing to find a match is not
evidence the blocker landed. Disclose synced markers in the PR body.

## Hand off

**Do not commit.** Call the Skill tool with "dev:task-review-cycle" and
`args: --from <task-next|task-new> --auto`, and **restate the Sprint Contract verbatim** in the
invocation — cleanup has already pruned `tasks.md`, so this is the reviewer's only copy. The
review cycle commits, reviews the diff against the contract, routes by diff size (direct merge
under 100 lines, PR + CI otherwise), applies findings, records out-of-scope items to
`backlog.md`, and merges.

If the cycle reports CI failure and the PR is abandoned: close the PR and delete the branch —
`main` never received the cleanup edits, so nothing rolls back.
