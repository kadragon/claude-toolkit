#!/usr/bin/env python3
"""Regression tests for ci-wait.sh's consecutive-failure (rework) cap.

The behavior under test is edge #8/C3: "hard-stop after 3 CI failures" existed only as
prose, so the orchestrator had to remember the count across separate script invocations —
exactly the kind of tally a fresh context loses. The cap now lives in the script and is
reported as `reason: "rework-cap"`.

Covered: the counter increments across invocations, trips at the 3rd real failure, resets
on either kind of pass (`success` and no-CI-configured), and is NOT incremented by a timeout
or a ci-status error. The timing constants are env-overridable purely so the timeout and
no-checks-grace branches are reachable here without editing the script.

Also covered: the opening fast-poll window. `harness-check` finishes in 13-22s, so a single
20s cadence discovers a pass up to a full interval late; the script polls at
CI_WAIT_FAST_POLL_INTERVAL for the first CI_WAIT_FAST_POLL_SECS and then falls back.

These cases count stub invocations rather than asserting on wall-clock gaps, which removes
flake from the *gap* between polls but not from the deadline itself: the status call precedes
the deadline check, so per-iteration overhead (a forked hub.sh, two jq calls, two `date`
calls) can trip the deadline one poll early. Each budget below is therefore set well above the
minimum that satisfies its assertion, so several slow iterations cannot red the gate.

ci-wait.sh resolves hub.sh relative to its own directory, so each case runs against a
stub hub.sh in a throwaway git repo — no network, no gh.

Run: python3 dev/skills/task-review-cycle/scripts/test_ci_wait.py
"""

import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

SCRIPT = Path(__file__).resolve().parent / "ci-wait.sh"

PASS = "\033[92mPASS\033[0m"
FAIL = "\033[91mFAIL\033[0m"
_results = []


def check(name, condition, detail=""):
    label = PASS if condition else FAIL
    print(f"  {label}  {name}" + (f"\n       {detail}" if detail and not condition else ""))
    _results.append(condition)


def make_repo(tmp: Path, status: str, name: str = "") -> Path:
    """A git repo holding ci-wait.sh next to a hub.sh stub that always reports `status`.

    `name` overrides the directory, so two repos can share a status.
    """
    repo = tmp / (name or status.replace(" ", "_"))
    scripts = repo / "scripts"
    scripts.mkdir(parents=True)
    subprocess.run(["git", "init", "-q"], cwd=repo, check=True)
    shutil.copy(SCRIPT, scripts / "ci-wait.sh")
    stub = scripts / "hub.sh"
    stub.write_text(f'#!/usr/bin/env bash\necho \'{{"status": "{status}"}}\'\n', encoding="utf-8")
    stub.chmod(0o755)
    return repo


def make_counting_repo(tmp: Path, name: str) -> Path:
    """Like make_repo("pending"), but the stub records one line per invocation.

    Counting calls is what makes the cadence observable without timing assertions: over a
    fixed window, a tighter interval is simply more calls.
    """
    repo = tmp / name
    scripts = repo / "scripts"
    scripts.mkdir(parents=True)
    subprocess.run(["git", "init", "-q"], cwd=repo, check=True)
    shutil.copy(SCRIPT, scripts / "ci-wait.sh")
    stub = scripts / "hub.sh"
    stub.write_text(
        "#!/usr/bin/env bash\n"
        'echo x >> "$(dirname "${BASH_SOURCE[0]}")/../calls.log"\n'
        "echo '{\"status\": \"pending\"}'\n",
        encoding="utf-8",
    )
    stub.chmod(0o755)
    return repo


def call_count(repo: Path) -> int:
    log = repo / "calls.log"
    return len(log.read_text(encoding="utf-8").splitlines()) if log.exists() else 0


def run(repo: Path, pr: str = "7", timeout: int = 30, **env_overrides) -> dict:
    env = dict(os.environ)
    env.update({k: str(v) for k, v in env_overrides.items()})
    proc = subprocess.run(
        ["bash", str(repo / "scripts" / "ci-wait.sh"), pr],
        cwd=repo,
        capture_output=True,
        text=True,
        env=env,
        timeout=timeout,
    )
    try:
        return json.loads(proc.stdout)
    except json.JSONDecodeError:
        return {"_stdout": proc.stdout, "_stderr": proc.stderr, "_rc": proc.returncode}


def strike_file(repo: Path, pr: str = "7") -> Path:
    return repo / ".git" / f"task-review-ci-strikes-{pr}"


def main() -> int:
    with tempfile.TemporaryDirectory() as tmp:
        tmp = Path(tmp)

        print("\n-- the cap trips on the 3rd consecutive real failure --")
        repo = make_repo(tmp, "failure")
        first = run(repo)
        check("1st failure reports no reason and failures=1",
              first.get("passed") is False
              and "reason" not in first
              and first.get("failures") == 1, f"got {first}")

        second = run(repo)
        check("2nd failure still has no reason, failures=2",
              "reason" not in second and second.get("failures") == 2, f"got {second}")

        third = run(repo)
        check("3rd failure reports reason=rework-cap",
              third.get("reason") == "rework-cap"
              and third.get("failures") == 3
              and third.get("max_failures") == 3, f"got {third}")

        fourth = run(repo)
        check("it stays tripped on further failures",
              fourth.get("reason") == "rework-cap" and fourth.get("failures") == 4,
              f"got {fourth}")

        print("\n-- the opening window polls fast, then falls back --")
        fast = make_counting_repo(tmp, "fast_window")
        run(fast, CI_WAIT_TIMEOUT_SECS=6, CI_WAIT_POLL_INTERVAL=30,
            CI_WAIT_FAST_POLL_INTERVAL=1, CI_WAIT_FAST_POLL_SECS=10)
        check("a 1s opening cadence polls every second inside a 6s budget",
              call_count(fast) >= 4, f"got {call_count(fast)} calls, expected >= 4")

        slow = make_counting_repo(tmp, "slow_fallback")
        run(slow, CI_WAIT_TIMEOUT_SECS=2, CI_WAIT_POLL_INTERVAL=3,
            CI_WAIT_FAST_POLL_INTERVAL=1, CI_WAIT_FAST_POLL_SECS=0)
        # The status call precedes the deadline check, so a 3s sleep over a 2s budget still
        # costs one more poll: t=0 and t=3. The fast cadence would have polled at t=0,1,2, so
        # an upper bound alone discriminates — and it stays true if a slow first iteration
        # burns the whole budget and the run ends at one call.
        check("a zero-length opening window falls straight back to POLL_INTERVAL",
              call_count(slow) <= 2, f"got {call_count(slow)} calls, expected <= 2")

        clamped = make_counting_repo(tmp, "explicit_override")
        run(clamped, CI_WAIT_TIMEOUT_SECS=4, CI_WAIT_POLL_INTERVAL=1)
        check("an explicit POLL_INTERVAL below the 5s fast default is not slowed by it",
              call_count(clamped) >= 3, f"got {call_count(clamped)} calls, expected >= 3")

        floored = make_counting_repo(tmp, "zero_interval")
        run(floored, CI_WAIT_TIMEOUT_SECS=4, CI_WAIT_POLL_INTERVAL=0,
            CI_WAIT_FAST_POLL_INTERVAL=0)
        check("a zero fast interval is floored instead of busy-looping",
              call_count(floored) <= 2, f"got {call_count(floored)} calls, expected <= 2")

        # The slow path had the same hole for longer: the numeric guard accepts 0, so
        # CI_WAIT_POLL_INTERVAL=0 turned the long tail into `sleep 0`. A zero-length opening
        # window forces the fallback branch on the very first iteration, so this exercises
        # POLL_INTERVAL alone — the fast interval never runs.
        # Killed rather than waited out. The floor is hardcoded at 20s, so once it works the
        # very first sleep outlasts any budget worth setting — letting the run finish would
        # add 20s to every suite invocation to observe a single call. Pre-fix the same window
        # is a `sleep 0` busy loop, and the call count over a fixed 4s discriminates the two
        # without either side having to terminate.
        slow_floored = make_counting_repo(tmp, "zero_slow_interval")
        try:
            run(slow_floored, timeout=4, CI_WAIT_POLL_INTERVAL=0, CI_WAIT_FAST_POLL_SECS=0)
        except subprocess.TimeoutExpired:
            pass
        check("a zero POLL_INTERVAL is floored instead of busy-looping the long tail",
              call_count(slow_floored) <= 3, f"got {call_count(slow_floored)} calls, expected <= 3")

        print("\n-- a pass clears the counter --")
        passing = make_repo(tmp, "success")
        strike_file(passing).write_text("2\n", encoding="utf-8")
        result = run(passing)
        check("a passing run reports passed=true", result.get("passed") is True, f"got {result}")
        check("and deletes the strike file", not strike_file(passing).exists())

        print("\n-- non-rework outcomes never increment --")
        errored = make_repo(tmp, "bogus-status")
        strike_file(errored).write_text("1\n", encoding="utf-8")
        result = run(errored)
        check("a ci-status error is reported as its own reason",
              result.get("passed") is False
              and result.get("reason", "").startswith("ci-status returned"), f"got {result}")
        check("and leaves the counter untouched",
              strike_file(errored).read_text().strip() == "1")

        timed_out = make_repo(tmp, "pending")
        strike_file(timed_out).write_text("2\n", encoding="utf-8")
        result = run(timed_out, CI_WAIT_TIMEOUT_SECS=1, CI_WAIT_POLL_INTERVAL=1)
        check("a timeout is reported as reason=timeout",
              result.get("passed") is False and result.get("reason") == "timeout", f"got {result}")
        check("and leaves the counter untouched",
              strike_file(timed_out).read_text().strip() == "2")

        print("\n-- a no-CI repo counts as a pass and clears the counter --")
        no_ci = make_repo(tmp, "none")
        strike_file(no_ci).write_text("2\n", encoding="utf-8")
        result = run(no_ci, CI_WAIT_NO_CHECKS_GRACE_SECS=0)
        check("no CI checks reports passed=true",
              result.get("passed") is True
              and result.get("reason") == "no CI checks found", f"got {result}")
        check("and clears the strike file like a real pass",
              not strike_file(no_ci).exists())

        print("\n-- a repo WITH CI configured never passes on an unregistered check set --")
        configured = make_repo(tmp, "none", name="none_configured")
        (configured / ".github" / "workflows").mkdir(parents=True)
        (configured / ".github" / "workflows" / "ci.yml").write_text("on: pull_request\n", encoding="utf-8")
        strike_file(configured).write_text("2\n", encoding="utf-8")
        result = run(configured, CI_WAIT_NO_CHECKS_GRACE_SECS=0,
                     CI_WAIT_CONFIGURED_GRACE_SECS=1, CI_WAIT_POLL_INTERVAL=1)
        check("checks that never register report passed=false",
              result.get("passed") is False
              and result.get("reason") == "checks-never-registered", f"got {result}")
        check("and leave the strike counter untouched — it is not rework",
              strike_file(configured).exists()
              and strike_file(configured).read_text().strip() == "2")

        print("\n-- a Forgejo/Gitea workflow directory counts as CI configured too --")
        forgejo = make_repo(tmp, "none", name="none_forgejo")
        (forgejo / ".forgejo" / "workflows").mkdir(parents=True)
        (forgejo / ".forgejo" / "workflows" / "ci.yaml").write_text("on: pull_request\n", encoding="utf-8")
        result = run(forgejo, CI_WAIT_NO_CHECKS_GRACE_SECS=0,
                     CI_WAIT_CONFIGURED_GRACE_SECS=1, CI_WAIT_POLL_INTERVAL=1)
        check("a .forgejo/workflows entry blocks the no-CI pass",
              result.get("passed") is False
              and result.get("reason") == "checks-never-registered", f"got {result}")

        print("\n-- an empty workflow directory is not CI configured --")
        empty_dir = make_repo(tmp, "none", name="none_empty_workflows")
        (empty_dir / ".github" / "workflows").mkdir(parents=True)
        result = run(empty_dir, CI_WAIT_NO_CHECKS_GRACE_SECS=0, CI_WAIT_POLL_INTERVAL=1)
        check("an empty workflows dir still passes as no-CI",
              result.get("passed") is True
              and result.get("reason") == "no CI checks found", f"got {result}")

        print("\n-- a garbage configured-grace override falls back, it does not kill the script --")
        garbage = make_repo(tmp, "none", name="none_garbage_grace")
        (garbage / ".github" / "workflows").mkdir(parents=True)
        (garbage / ".github" / "workflows" / "ci.yml").write_text("on: pull_request\n", encoding="utf-8")
        result = run(garbage, CI_WAIT_NO_CHECKS_GRACE_SECS=0,
                     CI_WAIT_CONFIGURED_GRACE_SECS="abc", CI_WAIT_TIMEOUT_SECS=1,
                     CI_WAIT_POLL_INTERVAL=1)
        check("a non-numeric grace keeps the default and still emits JSON",
              result.get("passed") is False
              and result.get("reason") in ("timeout", "checks-never-registered"), f"got {result}")

        print("\n-- a garbage cap override falls back to 3, it does not kill the script --")
        capped = make_repo(tmp, "failure0")
        (capped / "scripts" / "hub.sh").write_text(
            '#!/usr/bin/env bash\necho \'{"status": "failure"}\'\n', encoding="utf-8")
        run(capped, CI_WAIT_MAX_STRIKES="abc")
        run(capped, CI_WAIT_MAX_STRIKES="abc")
        result = run(capped, CI_WAIT_MAX_STRIKES="abc")
        check("a non-numeric CI_WAIT_MAX_STRIKES still trips at the default 3",
              result.get("reason") == "rework-cap" and result.get("max_failures") == 3,
              f"got {result}")

        print("\n-- per-PR isolation --")
        repo2 = make_repo(tmp, "failure2")
        # `failure2` is not a status ci-wait.sh knows; rebuild the stub as a real failure.
        (repo2 / "scripts" / "hub.sh").write_text(
            '#!/usr/bin/env bash\necho \'{"status": "failure"}\'\n', encoding="utf-8")
        run(repo2, pr="7")
        run(repo2, pr="7")
        other = run(repo2, pr="8")
        check("a different PR starts its own count",
              other.get("failures") == 1 and "reason" not in other, f"got {other}")

        print("\n-- a corrupt strike file degrades to zero, not a crash --")
        corrupt = make_repo(tmp, "failure3")
        (corrupt / "scripts" / "hub.sh").write_text(
            '#!/usr/bin/env bash\necho \'{"status": "failure"}\'\n', encoding="utf-8")
        strike_file(corrupt).write_text("not-a-number\n", encoding="utf-8")
        result = run(corrupt)
        check("a non-numeric counter restarts at 1",
              result.get("failures") == 1, f"got {result}")

    passed = sum(_results)
    total = len(_results)
    print(f"\n=== Results: {passed} PASS, {total - passed} FAIL ===")
    return 0 if passed == total else 1


if __name__ == "__main__":
    sys.exit(main())
