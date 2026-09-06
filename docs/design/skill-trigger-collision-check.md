# Deterministic Skill Trigger / Collision Check

> Inspired by [addyosmani/agent-skills](https://github.com/addyosmani/agent-skills)
> (`evals/README.md`, `scripts/run-evals.js` — structural / trigger-routing / behavioral
> eval tiers), surfaced 2026-08-10. The fixture format is not invented here: it is the
> `{query, should_trigger}` shape owned by the official
> [`skill-creator`](https://github.com/anthropics/claude-plugins) plugin
> (`skills/skill-creator/scripts/run_eval.py`, `improve_description.py`), already used by
> `prod/skills/persona-debate/evals/trigger-eval.json`.

## Problem Statement

`docs/eval-criteria.md` weights **Trigger Accuracy at 30%** — the single largest criterion
after Correctness — and its *How to test* line prescribes a model-judged procedure: "Draft
the representative prompts a user would type, confirm this skill is the unambiguous best
match for each." That collides with this repo's first principle, *mechanical enforcement >
verbal agreement* (`AGENTS.md`, `~/.claude/CLAUDE.md`): the 30% criterion is the only one in
the file with no mechanical tier at all.

Three concrete consequences today:

1. **Nothing detects a description that fences off nothing.** `check_skill_frontmatter.py`
   verifies a `description:` *exists*, parses, and is a non-empty string. Its content is
   unchecked. A description rewritten into vague prose still passes CI, and the regression
   surfaces only as a skill that quietly stops firing — the same silent-loader class that
   check exists to catch, one level up.
2. **Nothing detects two descriptions converging.** The repo ships 13 skills, several
   deliberately adjacent by design (`task-new` ↔ `task-next`, `task-spec` ↔ `task-tickets`,
   `harness-capture` ↔ `harness-curate`). Adjacency is correct; *indistinguishability* is
   the defect, and today the only guard is whether a reviewer happens to notice.
3. **The one existing fixture is unreachable by CI.**
   `prod/skills/persona-debate/evals/trigger-eval.json` holds 20 declared trigger cases, and
   no job reads it. Its intended consumer, `skill-creator`'s `run_eval.py`, spawns N real
   Claude sessions per query and scores a trigger *rate* against a threshold — accurate, but
   non-deterministic, API-billed, and unusable as a merge gate.

Restated as this repo's edge-scoring test (`backlog.md` → *task-\* edge enforcement*): a
description regression is **Silent** (invisible until a user's prompt fails to route),
**Costly** (it ships to the marketplace and survives every later session), and — this spec's
whole burden — must be made **Decidable**.

## Solution

Add one deterministic, stdlib-plus-PyYAML CI check that scores what a lexical ranker can
honestly decide, and is explicit about what it cannot. Two independent halves, one script:

**Half A — fixture ranking.** Build a TF-IDF vector space over the 13 skill descriptions.
For each declared fixture query, rank all 13 descriptions by cosine similarity:

- `should_trigger: true` → the owning skill must rank **1st, without a tie**. A tie at rank 1
  is a failure, because `docs/eval-criteria.md` asks for the *unambiguous* best match.
- `should_trigger: false` → the owning skill must **not** rank 1st.

**Half B — near-collision.** Compute pairwise cosine similarity between the 13 descriptions.
High similarity is **not** the defect — deliberate neighbors are supposed to be similar. The
defect is high similarity *without mutual disambiguation*: a pair at or above the calibrated
threshold fails unless **each** description names the other skill (its `name:`, or a
`NOT for …` / `→ name` clause pointing at it). The fix a failure demands is therefore the
same artifact that helps the real model-judged router — a cross-pointer — not a suppression
entry.

**What this check is, precisely.** A lexical ranker is a *proxy* for a model-judged router,
and this spec does not pretend otherwise. It establishes **necessary conditions**, not
sufficient ones: that each skill's description carries tokens distinctive enough to win its
own declared queries, that no pair is lexically interchangeable without a written fence, and
that fixtures exist and are non-vacuous. Passing does not certify the skill fires in
practice; `skill-creator`'s model-judged runner remains the semantic tier above it. The two
consume **one** fixture file, so declaring a trigger case never has to be done twice.

## User Stories

- As a harness maintainer, I want a PR that flattens a skill's `description:` to fail CI, so
  that the 30%-weighted Trigger Accuracy criterion has a mechanical floor instead of relying
  on a reviewer noticing prose drift.
- As a harness maintainer, I want two converging descriptions flagged with the specific pair
  named, so that I fix it by writing the cross-pointer the router actually needs rather than
  discovering the collision from a misrouted session weeks later.
- As a skill author, I want the trigger cases I declare in `trigger-eval.json` to be *scored*
  by CI on every PR, so that the file is a live contract rather than documentation that rots.
- As a skill author, I want a query I have judged unscoreable-by-lexical-means to be waivable
  in place with a stated reason, so that a proxy's false negative does not pressure anyone
  into weakening the gate itself.
- As a reviewer, I want the check's report to state how many queries it *skipped* and why, so
  that a fixture whose coverage is near-zero is visible rather than reported as a pass.

## Implementation Decisions

Resolved via `Skill(dev:task-grill)` this session; repo facts read, not assumed.

### 1. Fixture format and location — reuse, do not invent

`{skill}/evals/trigger-eval.json`, a JSON array of `{"query": str, "should_trigger": bool}`.
This is `skill-creator`'s format, already on disk at
`prod/skills/persona-debate/evals/trigger-eval.json`. One extension, additive and ignored by
`skill-creator`: an optional `"waived": "<reason>"` key on a query (see §4).

### 2. Coverage — phased, with a ratchet (grill Q1)

- **Half B (collision) covers all 13 skills immediately.** It needs no fixtures, so it is
  fully load-bearing on day one.
- **Half A scores whatever fixtures exist** — today one file.
- **Ratchet:** a PR that modifies any `*/skills/*/SKILL.md` must leave that skill with a
  `trigger-eval.json`. Detected the way the existing `version-bump` job detects changed
  paths (`git diff origin/main...HEAD -- <path>`), which is why this job is `pull_request`-
  triggered like every other job in `harness-check.yml`.

Rejected: authoring all 12 missing fixtures up front. That is what deferred this backlog item
in the first place ("not one sprint"), and a fixture reverse-engineered from the very
description it is meant to test is circular — the ratchet gets each fixture written at the
moment someone is actually reasoning about that skill's triggering.

### 3. Pass bar — top-1, merge-blocking (grill Q2)

Top-1-without-tie for positives, not-top-1 for negatives, `exit 1` on violation. Not
warn-only: every other job in `harness-check.yml` blocks, and a lone advisory job is a report
that gets scrolled past. The word *unambiguous* in `docs/eval-criteria.md` is what makes
top-1 the repo's own bar rather than a threshold invented here.

### 4. Language commensurability — score only comparable queries (grill Q5)

**This is the constraint that most shapes Half A, and it was found by measurement, not
assumed.** `persona-debate`'s `description:` is English; 17 of its 20 fixture queries are
Korean. Token overlap between a Korean query and an English description is ≈0, so *every*
skill scores ≈0 and rank 1 is effectively arbitrary. A ranker that reports a verdict there
would be reporting noise.

Therefore each string is assigned a script class — `ko` when Hangul characters are at least
30% of its letter characters, else `en` — and Half A scores a query **only when its class
matches the owning description's class**. Skipped queries are counted per skill and printed;
they remain in the file for `skill-creator`'s model-judged runner, which has no such limit.

**Fail-closed floor:** a fixture must yield **≥1 scorable positive** query. This is
satisfiable today, if barely — `persona-debate` measures 1 scorable English positive out
of 20 declared queries (19 skipped by language mismatch) — and it is what stops a fixture
from existing while covering nothing — the vacuous-gate failure mode
`check_skill_frontmatter.py` already guards with its `asset_count == 0` check. No minimum is
imposed on negatives in this slice: `persona-debate` currently has **0 scorable negatives**,
and a floor that the sole existing fixture cannot meet would have to be weakened on day one
to go green. Negative coverage is reported per skill so the floor can be ratcheted upward
later on evidence.

### 5. Asset scope — skills only (grill Q3)

13 skills (`dev/skills/*` ×10, `prod/skills/*` ×3). Excluded:

- **Commands** (`dev/commands/security-overview.md`) — invoked explicitly as `/name`, so a
  description collision cannot misroute them.
- **Agents** (`prod/agents/persona-actor.md`, `.claude/agents/*.md` ×5) — their descriptions
  do drive orchestrator routing, but the six are plainly distinct, so the check would detect
  zero today. Re-file with a recorded misroute, per the discipline the *Cut* section of
  `backlog.md` already sets for this repo.

### 6. Collision policy — mutual pointer required, no waiver file (grill Q4)

A pair at or above the threshold passes iff each description names the other. No suppression
file: an entry there would be a second maintenance surface that helps no router, whereas the
cross-pointer is exactly what `docs/eval-criteria.md`'s score-5 row asks for ("explicit
`NOT for …` exclusions that fence off neighbor skills").

**Threshold calibration is an implementation task with a stated method, not a guessed
constant.** Compute the full current pair distribution, then pin τ so that (a) every pair at
or above τ currently carries mutual pointers, and (b) τ is low enough to actually capture the
deliberate-neighbor pairs rather than nothing. Record the measured distribution and the
resulting τ in the script's module docstring — the calibration-in-the-header convention the
`ruff` and `bump-version` jobs in `harness-check.yml` already follow.

**Expected first-run finding, flagged now so it is not read as scope creep later:**
`harness-capture`'s description names `harness-curate` ("Cross-session mining →
harness-curate"), but `harness-curate`'s description names `harness-init`, **not**
`harness-capture`. If that pair lands at or above τ, the implementing ticket must add the
missing pointer to `harness-curate`'s description. That edit touches `dev/`, so it pulls in a
`dev/` plugin version bump plus a `--skill harness-curate` bump; a change confined to
`scripts/ci/` alone would need no bump, since the `version-bump` job only fires on `dev/` or
`prod/` diffs.

### 7. Deliverables and wiring

| Path | Role |
|------|------|
| `scripts/ci/check_skill_triggers.py` | the check; exit 0/1, always prints a full report |
| `scripts/ci/test_check_skill_triggers.py` | self-contained regression test, run first in CI |
| `.github/workflows/harness-check.yml` | new `skill-triggers` job (test step, then check step) |
| `docs/eval-criteria.md` §1 *How to test* | amend — currently claims model-judged only |

Conventions taken from the four existing checks rather than re-decided: path-based discovery
via `git ls-files` (never content-gated, per `check_skill_frontmatter.py`'s reasoning),
fail-closed on zero discovered assets, `sys.stdout.reconfigure` for UTF-8 output, PyYAML for
frontmatter parsed at the loader's strictness, and the `test_*.py`-before-`check_*.py` step
order every job in `harness-check.yml` uses.

**No new dependency and no network.** TF-IDF, cosine, and tokenization are ~40 lines of
stdlib. PyYAML is already installed for the `skill-frontmatter` job. Embedding or
semantic-similarity models are out (§*Out of Scope*): they would add a dependency, a download,
and non-determinism to a merge gate.

## Testing Decisions

`python3 scripts/ci/test_check_skill_triggers.py` — self-contained, temp-dir fixtures, no
network, mirroring `test_check_harness_drift.py` / `test_check_skill_frontmatter.py`. Cases
the verifier is expected to derive beyond this list too; these are the floor, not the ceiling:

| Case | Expected |
|------|----------|
| Corpus of distinct descriptions + matching positives | pass |
| Description stripped of its distinctive tokens | fail — owning skill not rank 1 |
| Positive query tying at rank 1 between two skills | fail — tie is not unambiguous |
| Negative query that ranks the owning skill 1st | fail |
| Colliding pair, neither naming the other | fail, both names in the message |
| Same pair, mutual `NOT for …` pointers added | pass |
| Pair below τ, no pointers | pass |
| Korean query against an English description | skipped, counted in the report, not scored |
| Fixture whose scorable positives number 0 | fail (vacuous-fixture floor) |
| `"waived": "reason"` on a query | skipped, reason echoed in the report |
| Malformed / non-array / non-UTF-8 fixture JSON | fail with the path named |
| Discovery finds zero skills | fail closed |
| Ratchet: changed `SKILL.md`, no `trigger-eval.json` | fail naming the skill |

**Acceptance gate:** `python3 scripts/ci/check_skill_triggers.py` exits 0 against the real
repo, and `ruff check --no-cache .` stays at 0 violations. Per
`docs/eval-criteria.md`, the agent that implements this does not verify it — `qa-verifier`
grades against the Sprint Contract, and the standing-checks floor is inherited, not restated
here.

## Out of Scope

- **Agent and command assets** (§5) — re-file only with a recorded misroute.
- **Bulk authoring of the 12 missing `trigger-eval.json` files** — delivered incrementally by
  the ratchet (§2).
- **Replacing `skill-creator`'s model-judged runner.** It stays the semantic tier; this check
  is the deterministic tier beneath it. Neither subsumes the other.
- **Embedding / LLM-based similarity, and any network call or new dependency** in CI.
- **Rewriting descriptions into Korean as a repository-wide policy**, or changing that policy,
  remains out of scope (grill Q5 rejected this route). The approved bilingual
  `prod/skills/hwpx/SKILL.md` description is a single corpus fixture for exercising `ko`
  scoring; it does not change the repository-wide description-language policy. Only the
  specific cross-pointer edit §6 predicts is otherwise in scope for the implementing tickets.
- **A coverage floor on negative queries** — reported, not gated, until fixtures accumulate.
- **Any claim that passing this check means a skill fires correctly.** The check's own report
  wording must not overstate it.

## Further Notes

**Open risks.**

- *Proxy validity.* The ranker is not the router. Mitigations: the per-query `"waived"`
  escape, the language-commensurability skip, and report wording that states the necessary-
  condition framing. If the waiver count ever grows to dominate the fixture, that is evidence
  the proxy has stopped earning its place — treat it as a prune signal for
  `dev:harness-curate`, not as a reason to loosen τ.
- *τ drift.* Each new skill perturbs both the IDF weights and the pair distribution. A skill
  added later can push a pair over τ and fail an unrelated PR. This is the intended behavior
  (that pair genuinely needs a pointer), but the failure message must say so plainly, or it
  reads as a flaky gate.
- *Ratchet blind spot.* The trigger is "`SKILL.md` changed", so a description that was always
  weak is never forced to grow a fixture until someone edits that file. Accepted: the
  alternative is the bulk authoring §2 rejects.

**Sequencing.** `Skill(dev:task-tickets)` should split this into vertical slices, in
dependency order: (1) ranker + Half A + tests + CI job, scoring the one existing fixture;
(2) Half B collision with measured τ, plus whichever cross-pointer edit the measurement
actually demands — including the `dev/` version bump that edit pulls in; (3) the ratchet and
the `docs/eval-criteria.md` §1 amendment. Slice 2's description edit must not be pre-committed
in slice 1: it depends on a number nobody has measured yet.

**Backlog linkage.** This spec discharges the `*(deferred: needs a docs/design/ spec first)*`
marker on the `## Harness — deterministic skill trigger/collision check` item in `backlog.md`.
That marker is cleared by `task_nodes.py prune-backlog` when the implementing tickets land,
not by this document.

## Measured Calibration

Recorded by slice 2, using slice 1's shipped ranker (`scripts/ci/check_skill_triggers.py` —
`Corpus`, sublinear TF, smoothed IDF, L2-normalized cosine) over the 13 discovered
descriptions. §6 required τ to be measured rather than guessed; this section is that
measurement. Slice 3 copies τ and this basis into the Half B module docstring.

### Distribution before the cross-pointer edits

78 pairs. max **0.5827** · p99 0.3081 · p95 0.2188 · p90 0.1735 · median 0.0502 ·
mean 0.0802 · stdev 0.0891 · min 0.0000. Percentiles are **floor-index** (`numpy`'s
`method="lower"`), not linearly interpolated — the two disagree materially at this sample size,
and this section is meant to be re-runnable.

The head of the distribution, with each pair's mutual-pointer status at measurement time
(*mutual* = each description contains the other's `name:`, the test Half B implements):

| pair | cosine | pointers before |
|------|--------|-----------------|
| `task-new` ↔ `task-next` | 0.5827 | mutual |
| `harness-curate` ↔ `harness-init` | 0.3081 | one-way (curate → init) |
| `harness-capture` ↔ `harness-curate` | 0.3034 | one-way (capture → curate) |
| `task-spec` ↔ `task-tickets` | 0.2685 | neither |
| `task-grill` ↔ `task-tickets` | 0.2188 | neither |
| `task-grill` ↔ `task-new` | 0.2071 | neither |
| `task-new` ↔ `task-spec` | 0.1859 | neither |
| `harness-capture` ↔ `harness-init` | 0.1810 | neither |

§6's *expected first-run finding* is confirmed: `harness-capture`'s description named
`harness-curate`, and `harness-curate`'s named `harness-init` but not `harness-capture`.

### τ = 0.25

- **Condition (b) — low enough to capture the deliberate neighbors — is the binding one.**
  The spec names `task-new`↔`task-next`, `task-spec`↔`task-tickets` and
  `harness-capture`↔`harness-curate` as deliberate adjacents. The lowest of the three measures
  0.2685, so τ ≤ 0.2685 is forced by the spec's own examples, not chosen.
- **Pinned to the widest gap in the band.** Below the 0.5827 outlier, the largest interval
  between consecutive pairs is 0.2685 → 0.2188 (0.0497), and τ = 0.25 sits inside it. This is
  what selected 0.25 from the pre-edit distribution; it is *not* the reason it survives the
  edits — the sub-τ margin narrows once the pointers land. See *Distribution after the
  cross-pointer edits* for the measured margins and the argument that keeps τ there.
- **Condition (a) — every pair at or above τ carries mutual pointers — holds after this
  slice's edits**, which is the whole point of sequencing them before the Half B gate.

τ = 0.25 selects exactly four pairs, every one a deliberate neighbor:

| pair | cosine after edits | pointer added by this slice |
|------|--------------------|-----------------------------|
| `task-new` ↔ `task-next` | 0.5880 | none — already mutual |
| `task-spec` ↔ `task-tickets` | 0.3869 | both directions |
| `harness-capture` ↔ `harness-curate` | 0.3392 | `harness-curate` → `harness-capture` |
| `harness-curate` ↔ `harness-init` | 0.3203 | `harness-init` → `harness-curate` |

### Distribution after the cross-pointer edits

Adding a pointer inserts the other skill's name tokens into a description, so it **raises** that
pair's cosine and perturbs every IDF weight. The post-edit distribution was therefore re-measured
rather than assumed: max **0.5880** · p99 0.3869 · p95 0.2365 · p90 0.1808 · median 0.0523 ·
mean 0.0848 · stdev 0.0984 · min 0.0000 (floor-index percentiles, as above).

τ = 0.25 still selects exactly the four intended pairs and nothing else: they rose (as intended,
since each now names its neighbor), and the highest pair *without* mutual pointers is
`task-new` ↔ `task-spec` at **0.2365** — still below τ. Two different margins matter here and
they move in opposite directions, so both are stated rather than the flattering one:

- **Interval width around τ** widened, 0.0497 → 0.0838 (0.3203 → 0.2365).
- **Distance from τ down to the nearest sub-τ pair** *shrank*, 0.0312 → 0.0135, because this
  slice's own pointer edits lifted `task-new` ↔ `task-spec` from 0.1859 to 0.2365. τ is
  therefore no longer centred in its gap (the centre is ≈0.278).

**τ = 0.25 is kept anyway, and not because 0.278 is unavailable.** Re-centring would select the
same four pairs *today* while quietly weakening the gate against the one regression it most
needs to catch — a maintainer deleting a pointer this slice added. Removing a pointer returns
that pair to roughly its pre-edit cosine, and the gate only fires if that value is still at or
above τ. `task-spec` ↔ `task-tickets` falls back to 0.2685: caught at τ = 0.25, **missed** at
τ = 0.278. Preserving catch-power against a reverted pointer outranks headroom against an
unrelated wording tweak, which merely produces a fixable false positive.

**Re-measure after any description edit, not just after a skill is added.** Both perturb the
corpus-wide IDF weights — this is the *τ drift* risk under *Open risks* — but they are not
equal in size, and the note that matters is the counter-intuitive one: **the remedy is
self-amplifying.** Appending a pointer sentence lengthens a description and injects its
neighbor's name tokens, which raises that description's similarity to *other* descriptions too,
not only to the pair being fixed. Adding a pointer to clear one pair can therefore push a
different pair — one the spec never designated a deliberate neighbor — over τ. Corpus-membership
churn (a 14th skill appearing, an existing one removed) moves the same figures by far less.
So a pointer edit is followed by a re-measure of the whole distribution, exactly as this slice
did, never by a lowered τ.

### Measured 2026-09-06 — `harness-capture`'s description is length-pinned, in both directions

Adding the session note store to `dev:harness-capture` came with a proposed description edit
naming the new capture step. Both a longer and a shorter description fail Half A, against
*different* neighbours:

| Description | Result |
|-------------|--------|
| Unchanged (shipped) | all scorable queries pass |
| +1 clause (longer) | `task-review` negative *"review the current diff for correctness bugs and simplification cleanups"* ranks `task-review` 1st (0.2069) |
| Reworded shorter | `task-review` positive *"start the review cycle with --auto so it skips the confirmation"* ranks `harness-capture` 1st (0.2730; 0.3040 on a second variant) |

The mechanism: `harness-capture`'s description carries both `CURRENT` and `review`, so after L2
normalization its weight on that vocabulary is a direct function of the description's length.
Lengthening dilutes it until `task-review` wins a query neither skill should own — the built-in
`code-review` skill is outside this corpus, which is why that negative has no correct winner
inside it. Shortening concentrates it until `harness-capture` outranks `task-review` on
`task-review`'s own positives.

**The rule this establishes: a description's *length* is a tuned parameter, not free space.**
Measure both directions before editing one — an edit that looks purely additive can fail through
normalization alone, without introducing a single colliding token. The change that prompted this
shipped with the description untouched and put the new behavior in the skill body instead.
