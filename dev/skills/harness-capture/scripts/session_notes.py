#!/usr/bin/env python3
"""
Session observation notes — the write-side of `dev:harness-capture`.

The retrospective used to run only at the end of a session, reconstructing signals
from context that a long session or a compaction has already thinned. This store
makes the capture immediate and mechanical: the moment a signal fires, one `add`
writes it to disk in a fixed shape; the retrospective later reads back what was
actually captured instead of what is still remembered.

Design rules this file enforces, so the SKILL.md does not have to restate them:

  * One store per repository, shared across worktrees. The path derives from
    `git rev-parse --git-common-dir`, never from the cwd — a per-worktree store
    silently shards the same session's notes across checkouts, and a store under a
    temporary worktree is deleted with it.
  * A note carries `title`, `issue`, `improvement`, `principle`. `principle` is the
    generalisable takeaway and the field the retrospective's routing gate reads; a
    note missing any field is refused at write time, when its author is still here
    to fix it, rather than at read time weeks later.
  * `status` distinguishes "no store" (nothing was ever captured) from "store with
    zero pending notes" (captured and already flushed). A single "0" conflates a
    quiet session with a broken path, and only the second is a defect.

Usage:
  python3 session_notes.py add --title T --issue I --improvement M --principle P
                               [--target NAME] [--store PATH]
  python3 session_notes.py list [--store PATH]       # pending notes, as markdown
  python3 session_notes.py status [--store PATH]     # NO-STORE | EMPTY | PENDING n
  python3 session_notes.py flush [--store PATH]      # mark pending notes routed
  python3 session_notes.py --test

Exit: 0 on success, 1 on a refused write or a broken store, 2 when `status` cannot
locate a store at all (the instrument-versus-population distinction, as a code).
"""

import argparse
import json
import os
import re
import subprocess
import sys
import tempfile
import time

STORE_BASENAME = os.path.join("harness-capture", "notes.jsonl")
FIELDS = ("title", "issue", "improvement", "principle")
MAX_FIELD = 2000
# C0/C1 controls and the zero-width / bidi range that `check_asset_hygiene.py` bans in
# shipped assets. A note is replayed into an agent's context at flush time, so it gets
# the same treatment as any other text the model reads.
CONTROL_RE = re.compile("[\\x00-\\x08\\x0b-\\x1f\\x7f-\\x9f\\u200b-\\u200f\\u202a-\\u202e\\u2066-\\u2069\\ufeff]")

EXIT_NO_STORE = 2


class NoteError(Exception):
    """A refused write or an unreadable store."""


def git_common_dir(cwd=None):
    """Absolute path of the repo's shared git directory, or None outside a repo."""
    try:
        out = subprocess.check_output(
            ["git", "rev-parse", "--git-common-dir"],
            cwd=cwd or os.getcwd(),
            text=True,
            stderr=subprocess.DEVNULL,
        ).strip()
    except (subprocess.CalledProcessError, OSError):
        return None
    if not out:
        return None
    return os.path.abspath(os.path.join(cwd or os.getcwd(), out))


def resolve_store(explicit=None, cwd=None):
    """Store path from --store, then $HARNESS_CAPTURE_STORE, then the git common dir."""
    if explicit:
        return os.path.abspath(explicit)
    env = os.environ.get("HARNESS_CAPTURE_STORE")
    if env:
        return os.path.abspath(env)
    common = git_common_dir(cwd)
    if common is None:
        raise NoteError(
            "no store: not inside a git repository. Pass --store PATH or set "
            "HARNESS_CAPTURE_STORE to a path that outlives this session."
        )
    return os.path.join(common, STORE_BASENAME)


def clean_field(name, value):
    if value is None:
        raise NoteError(f"--{name} is required")
    text = " ".join(str(value).split())
    if not text:
        raise NoteError(f"--{name} is empty; a note without a {name} is not actionable")
    if CONTROL_RE.search(text):
        raise NoteError(f"--{name} carries a control, zero-width or bidi character")
    if len(text) > MAX_FIELD:
        raise NoteError(f"--{name} is {len(text)} chars, over the {MAX_FIELD} cap")
    return text


def read_notes(path):
    """Every note in the store, oldest first. A malformed line is an error, not a skip."""
    if not os.path.exists(path):
        return None
    notes = []
    with open(path, encoding="utf-8") as f:
        for lineno, line in enumerate(f, 1):
            if not line.strip():
                continue
            try:
                rec = json.loads(line)
            except json.JSONDecodeError as e:
                raise NoteError(f"{path}:{lineno} is not valid JSON ({e}); repair before writing") from e
            if not isinstance(rec, dict) or "id" not in rec:
                raise NoteError(f"{path}:{lineno} is not a note record")
            notes.append(rec)
    return notes


def pending(notes):
    return [n for n in notes if not n.get("flushed")]


def add(path, fields, target=None, now=None):
    values = {k: clean_field(k, fields.get(k)) for k in FIELDS}
    existing = read_notes(path) or []
    rec = {
        "id": len(existing) + 1,
        "ts": time.strftime("%Y-%m-%dT%H:%M:%S", time.localtime(now if now is not None else time.time())),
        **values,
    }
    if target:
        rec["target"] = clean_field("target", target)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "a", encoding="utf-8") as f:
        f.write(json.dumps(rec, ensure_ascii=False, sort_keys=True) + "\n")
    return rec


def render(notes):
    out = []
    for n in notes:
        head = f"### {n['id']}. {n['title']}"
        if n.get("target"):
            head += f"  (target: {n['target']})"
        out.append(head)
        out.append(f"- **Issue:** {n['issue']}")
        out.append(f"- **Improvement:** {n['improvement']}")
        out.append(f"- **Principle:** {n['principle']}")
        out.append("")
    return "\n".join(out).rstrip()


def flush(path, now=None):
    notes = read_notes(path)
    if notes is None:
        raise NoteError(f"no store at {path}; nothing to flush")
    stamp = time.strftime("%Y-%m-%d", time.localtime(now if now is not None else time.time()))
    marked = 0
    for n in notes:
        if not n.get("flushed"):
            n["flushed"] = stamp
            marked += 1
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        for n in notes:
            f.write(json.dumps(n, ensure_ascii=False, sort_keys=True) + "\n")
    os.replace(tmp, path)
    return marked


def cmd_add(args):
    path = resolve_store(args.store)
    rec = add(path, vars(args), target=args.target)
    print(f"note {rec['id']} captured -> {path}")
    return 0


def cmd_list(args):
    path = resolve_store(args.store)
    notes = read_notes(path)
    if notes is None:
        print(f"NO-STORE {path}")
        return EXIT_NO_STORE
    open_notes = pending(notes)
    if not open_notes:
        print(f"EMPTY {path} ({len(notes)} note(s), all flushed)")
        return 0
    print(render(open_notes))
    return 0


def cmd_status(args):
    path = resolve_store(args.store)
    notes = read_notes(path)
    if notes is None:
        print(f"NO-STORE {path}")
        return EXIT_NO_STORE
    open_notes = pending(notes)
    if not open_notes:
        print(f"EMPTY {path} ({len(notes)} note(s), all flushed)")
        return 0
    print(f"PENDING {len(open_notes)} {path}")
    return 0


def cmd_flush(args):
    path = resolve_store(args.store)
    marked = flush(path)
    print(f"flushed {marked} note(s) -> {path}")
    return 0


def run_tests():
    results = []

    def check(name, ok):
        results.append((name, ok))
        print(f"{'PASS' if ok else 'FAIL'}: {name}")

    def expect_error(name, fn):
        try:
            fn()
        except NoteError:
            check(name, True)
            return
        check(name, False)

    tmpdir = tempfile.mkdtemp(prefix="session-notes-test-")
    saved = os.environ.get("HARNESS_CAPTURE_STORE")
    try:
        store = os.path.join(tmpdir, "harness-capture", "notes.jsonl")

        check("status on a missing store reports NO-STORE", read_notes(store) is None)

        rec = add(store, {
            "title": "flush trigger never fired",
            "issue": "the retrospective ran only at the end",
            "improvement": "bind the flush to the commit",
            "principle": "a trigger must hang on an event visible in the tool record",
        })
        check("first note gets id 1", rec["id"] == 1)
        check("store created on first add", os.path.exists(store))

        rec2 = add(store, dict.fromkeys(FIELDS, "x"), target="dev:harness-capture")
        check("second note gets id 2", rec2["id"] == 2)
        check("target is recorded", rec2["target"] == "dev:harness-capture")

        notes = read_notes(store)
        check("both notes read back", len(notes) == 2)
        check("both notes pending", len(pending(notes)) == 2)

        expect_error("empty field refused", lambda: add(store, dict.fromkeys(FIELDS, "   ")))
        expect_error("missing field refused", lambda: add(store, {"title": "t", "issue": "i"}))
        expect_error("control character refused", lambda: add(store, dict.fromkeys(FIELDS, "a\u200bb")))
        expect_error("over-cap field refused", lambda: add(store, dict.fromkeys(FIELDS, "z" * (MAX_FIELD + 1))))
        check("refused writes appended nothing", len(read_notes(store)) == 2)

        check("whitespace is collapsed", add(store, dict.fromkeys(FIELDS, " a \n b "))["title"] == "a b")

        marked = flush(store)
        check("flush marks every pending note", marked == 3)
        check("flush leaves nothing pending", pending(read_notes(store)) == [])
        check("flush preserves the history", len(read_notes(store)) == 3)
        check("second flush is a no-op", flush(store) == 0)

        rendered = render(read_notes(store)[:1])
        check("render carries all three body fields",
              "**Issue:**" in rendered and "**Improvement:**" in rendered and "**Principle:**" in rendered)

        broken = os.path.join(tmpdir, "harness-capture", "broken.jsonl")
        with open(broken, "w", encoding="utf-8") as f:
            f.write("{not json}\n")
        expect_error("malformed store errors instead of reading empty", lambda: read_notes(broken))

        os.environ["HARNESS_CAPTURE_STORE"] = store
        check("env var overrides the git-dir default", resolve_store(None) == store)
        explicit = os.path.join(tmpdir, "explicit.jsonl")
        check("--store beats the env var", resolve_store(explicit) == explicit)
        os.environ.pop("HARNESS_CAPTURE_STORE")

        # A worktree resolves to the main checkout's git dir, so one repo has one store.
        repo = os.path.join(tmpdir, "repo")
        os.makedirs(repo)
        env = {**os.environ, "GIT_CONFIG_GLOBAL": os.path.join(tmpdir, "gitconfig"), "HOME": tmpdir}
        quiet = {"cwd": repo, "env": env, "stdout": subprocess.DEVNULL, "stderr": subprocess.DEVNULL}
        if subprocess.call(["git", "init", "-q", "-b", "main"], **quiet) == 0:
            subprocess.call(["git", "-c", "user.email=t@t", "-c", "user.name=t",
                             "commit", "-q", "--allow-empty", "-m", "init"], **quiet)
            wt = os.path.join(tmpdir, "wt")
            if subprocess.call(["git", "worktree", "add", "-q", wt, "-b", "side"], **quiet) == 0:
                check("worktree shares the main checkout's store",
                      resolve_store(None, cwd=wt) == resolve_store(None, cwd=repo))
            else:
                check("worktree shares the main checkout's store (skipped: worktree add failed)", True)
        else:
            check("worktree shares the main checkout's store (skipped: git init failed)", True)

        outside = os.path.join(tmpdir, "outside")
        os.makedirs(outside)
        if git_common_dir(cwd=outside) is None:
            expect_error("outside a repo, resolve fails loudly",
                         lambda: resolve_store(None, cwd=outside))
        else:
            check("outside a repo, resolve fails loudly (skipped: tmp is inside a repo)", True)
    finally:
        if saved is None:
            os.environ.pop("HARNESS_CAPTURE_STORE", None)
        else:
            os.environ["HARNESS_CAPTURE_STORE"] = saved
        import shutil

        shutil.rmtree(tmpdir, ignore_errors=True)

    failed = [n for n, ok in results if not ok]
    print(f"\n{len(results) - len(failed)}/{len(results)} passed")
    return 1 if failed else 0


def main():
    ap = argparse.ArgumentParser(description="Session observation notes for dev:harness-capture.")
    ap.add_argument("--test", action="store_true", help="run self-tests")
    sub = ap.add_subparsers(dest="cmd")

    p_add = sub.add_parser("add", help="capture one observation now")
    for field in FIELDS:
        p_add.add_argument(f"--{field}", required=True)
    p_add.add_argument("--target", default=None, help="the asset the note is about")
    p_add.set_defaults(func=cmd_add)

    for name, fn, help_text in (
        ("list", cmd_list, "print pending notes as markdown"),
        ("status", cmd_status, "NO-STORE | EMPTY | PENDING n"),
        ("flush", cmd_flush, "mark pending notes as routed"),
    ):
        p = sub.add_parser(name, help=help_text)
        p.set_defaults(func=fn)

    for p in sub.choices.values():
        p.add_argument("--store", default=None, help="override the store path")

    args = ap.parse_args()
    if args.test:
        return run_tests()
    if not getattr(args, "func", None):
        ap.print_help()
        return 1
    try:
        return args.func(args)
    except NoteError as e:
        print(f"ERROR: {e}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
