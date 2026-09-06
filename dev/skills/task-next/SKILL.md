---
name: task-next
version: 2.0.1
description: >-
  Pull the next queued item from backlog.md/tasks.md and run the full code cycle: branch,
  Sprint Contract, implement, version bump, review. Flags: --all (parallel batch),
  --tree (worktree isolation). New work you just described → task-new.
disable-model-invocation: true
---

# Next Tasks

Pick queued work and run the code cycle in `references/cycle.md`. This skill is the selection
layer; the cycle and the review are elsewhere.

**Modes:** default = single-pick (Steps 1–4). `--all` (or "전부 처리", "모두 돌려", "batch all")
→ `references/batch.md`. `--tree` → single-pick through a git worktree, `references/tree.md`.
A free-text request not yet on the queue is `task-new`'s job.

## Prerequisites

**Required:** `backlog.md`. Missing → stop and point to `dev:harness-init`. Read
`docs/conventions.md` when present; when absent the linter is the authority.

**Working tree gate.** Capture the state before judging dirty files:

```bash
dirty=$(git status --porcelain)
if [[ -n "$dirty" ]]; then
  current_branch=$(git branch --show-current)
  task_contract_dirty=$(git status --porcelain -- tasks.md)
  task_worktree=$(git worktree list --porcelain | grep -E '^worktree .*/\.worktrees/' || true)
  non_queue_dirty=$(git status --porcelain -- ':(exclude,top)backlog.md')   # top: anchor at repo root
  queue_delta=$(git diff --stat HEAD -- backlog.md)                          # HEAD: a staged backlog.md still shows
fi
```

| State | Action |
|-------|--------|
| clean | proceed |
| dirty, not on `main`/`master` | *Work already in flight* (`references/edge-cases.md`) |
| on `main`, `task_contract_dirty` and `task_worktree` both non-empty | same edge case — a `--tree` run is in flight |
| on `main`, `non_queue_dirty` empty | `backlog.md` alone is not stray (the `task-tickets` hand-off leaves it uncommitted): announce that it is being carried, quote `queue_delta` (or the file's line count when untracked), proceed |
| anything else | list the dirty files, stop, ask the user to commit, stash, or discard |

`tasks.md` is optional. It holds the Sprint Contract and nothing else, exists only when a
`## Covers` deletion list is needed (a pre-existing `status: open` h1 block, or a ≥2-item group;
`--tree` always), and every persistent item lives in `backlog.md`. If the candidate script warns
that `tasks.md` still carries a `## Review Backlog` / `## Security Fixes` section, move it to
`backlog.md` verbatim first.

## Step 1 — Gather candidates

```bash
SKILL_DIR="<absolute parent directory of the loaded SKILL.md>"
CANDIDATES="$SKILL_DIR/scripts/backlog_candidates.py"
[[ -r "$CANDIDATES" ]] || { echo "Bundled script missing or unreadable: $CANDIDATES" >&2; exit 1; }
python3 "$CANDIDATES" --tasks tasks.md --backlog backlog.md              # fast path, cap 3
python3 "$CANDIDATES" --tasks tasks.md --backlog backlog.md --full-scan  # full list, type priority
```

The script owns selection: `tasks.md` `status: open` h1 blocks, then `backlog.md` h2/h3 groups
with open `- [ ]` items, skipping `[x]`/`[>]`/deferred/blocked items and anything inside
comments or code fences. **Read stderr every run**: an unbalanced-fence warning makes that file
untrustworthy; a persistent-section warning means `tasks.md` needs the move above; a
zero-candidate diagnosis is relayed verbatim (never say "queue clear" on empty stdout alone); a
truncation note means the fast path hid groups — say how many when offering more.

| Fast-path count | Action |
|-----------------|--------|
| 0 | relay the diagnosis, then run `--full-scan` |
| 1 | announce the group → Step 3 |
| 2–3 | one `AskUserQuestion` (Codex: a numbered list) listing the candidates plus **"더 많은 항목 보기"** as the last option; that option → `--full-scan` → Step 2 |

Unattended run: no question — run `--full-scan`, take `[1]`, announce.

## Step 2 — Select (full-scan list)

Print every group as `[N] <source>: <title> (<M> items)` and wait for a number (unattended:
`[1]`). Zero groups → read stderr first; report "backlog and tasks are clear" only when nothing
was hidden. A group of >8 open items → confirm all-or-subset with the user (unattended: abort and
report). A group whose every open item is deferred/blocked is not a candidate — surface the
blocker; all groups blocked → report and stop.

## Step 3 — Run the cycle

Follow `references/cycle.md` end to end with these overrides:

**Mark active — after the plan gate.**
- `tasks.md` h1 block (`status: open`) → flip to `status: active`; the block is the contract.
- `backlog.md` group with ≥2 in-scope items → write `tasks.md`: `# <heading verbatim>`,
  `status: active`, the contract, and `## Covers` listing each item line **verbatim** (the
  deletion list). Leave the items `- [ ]` in `backlog.md` until cleanup.
- exactly 1 item → no file; author the contract inline and carry the item's verbatim line to
  `prune-backlog` yourself. `--tree` writes `tasks.md` even for one item.

**Cleanup** runs `prune-tasks` only when a sprint block exists, and `prune-backlog` over the
`## Covers` lines (or the single carried line). Post-merge, `backlog.md` and `tasks.md` carry no
`[x]`, `[>]`, or stale sprint markers.

## Step 4 — Hand off

Per `references/cycle.md` → *Hand off*: `args: --from task-next --auto`, Sprint Contract restated
verbatim. `--tree` and `--all` hand off the same way after their own per-worktree verification.

## Edge cases

Recognise by name; open `references/edge-cases.md` before acting.

- **Work already in flight** — a feature branch or `--tree` worktree carrying uncommitted work.
  Three ordered checks produce a diagnosis; always still ask yes/no before resuming.
- **Deferred backlog item (≥2 candidates)** — surface the blocker, confirm it is resolved.
- **Deferred item in a group** — warn and continue with the group's other items.
- **Review finding spans multiple PRs** — scope to the specific `file:line`.
