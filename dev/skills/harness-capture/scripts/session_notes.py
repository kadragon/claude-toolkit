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
  python3 session_notes.py add --from-json PATH|-  [--store PATH]   # preferred
  python3 session_notes.py add --title T --issue I --improvement M --principle P
                               [--target NAME] [--store PATH]
  python3 session_notes.py list [--store PATH]       # pending notes, as markdown
  python3 session_notes.py status [--store PATH]     # NO-STORE | EMPTY | PENDING n
  python3 session_notes.py flush --through N [--store PATH]   # mark notes 1..N routed
  python3 session_notes.py --test

`--from-json` is the form to reach for whenever a field quotes something you did not
author literally — an error message, the user's words, a diff. Note text passed as a
shell argument is expanded by the shell first, so a body containing `$(...)` executes
before this script ever sees it; a file never goes through that expansion.

`flush` takes `--through N`, the highest id the `list` you just routed showed. Marking
"everything pending" would also mark a note captured *while* you were routing — it
would leave the queue having never been read. With pending notes present and no
`--through`, the flush is refused; with none, it is a no-op that exits 0.

Exit: 0 on success, 1 on a refused write, a refused flush, or a broken store, 2 when
`status` or `list` cannot locate a store at all (the instrument-versus-population
distinction, as a code).
"""

import argparse
import json
import os
import re
import subprocess
import sys
import tempfile
import time
from contextlib import contextmanager

try:
    import fcntl
except ImportError:                                   # Windows: no flock
    fcntl = None

STORE_BASENAME = os.path.join("harness-capture", "notes.jsonl")
FIELDS = ("title", "issue", "improvement", "principle")
MAX_FIELD = 2000
# The same ban list `scripts/ci/check_asset_hygiene.py` applies to shipped assets, kept
# in step with it deliberately: C0 (minus tab/newline/CR) and DEL, C1, the bidi marks
# (including U+061C, which is not in any contiguous range with the others), the bidi
# embedding/override and isolate ranges, the zero-width and invisible set (including
# U+2060), and the invisible line/paragraph separators. A note is replayed into an
# agent's context at flush time, so it gets the same treatment as any other text the
# model reads. A checker cannot be imported from here — this script ships inside a
# plugin installed into repos that have no `scripts/ci/` — so the set is restated, and
# `--test` pins each family.
CONTROL_RE = re.compile(
    "[\\x00-\\x08\\x0b\\x0c\\x0e-\\x1f\\x7f-\\x9f"
    "\\u061c\\u200b-\\u200f\\u202a-\\u202e\\u2060\\u2066-\\u2069"
    "\\u2028\\u2029\\ufeff]"
)

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
    raw = str(value)
    # Checked BEFORE the collapse: `str.split()` treats U+2028/U+2029 and several C0/C1
    # codepoints as whitespace and would silently delete them, so a post-collapse check
    # can never see the characters it claims to ban.
    if CONTROL_RE.search(raw):
        raise NoteError(f"--{name} carries a control, zero-width or bidi character")
    text = " ".join(raw.split())
    if not text:
        raise NoteError(f"--{name} is empty; a note without a {name} is not actionable")
    if len(text) > MAX_FIELD:
        raise NoteError(f"--{name} is {len(text)} chars, over the {MAX_FIELD} cap")
    return text


@contextmanager
def store_lock(path):
    """Serialise append and read-modify-replace against the store.

    `flush` rewrites the whole file, so an `add` landing between its read and its
    `os.replace` would be discarded. Both operations take this lock. The lock lives in a
    sidecar file, not the store itself: the store is replaced by `os.replace`, and a
    descriptor held on a replaced inode guards nothing. Where `fcntl` is unavailable
    (Windows), the lock degrades to a no-op — single-writer use is unaffected, and the
    degradation is stated rather than silently assumed away.
    """
    if fcntl is None:
        yield
        return
    os.makedirs(os.path.dirname(path), exist_ok=True)
    fd = os.open(path + ".lock", os.O_CREAT | os.O_RDWR, 0o600)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX)
        yield
    finally:
        try:
            fcntl.flock(fd, fcntl.LOCK_UN)
        finally:
            os.close(fd)


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
            missing = [k for k in FIELDS if not isinstance(rec.get(k), str)]
            if missing:
                raise NoteError(
                    f"{path}:{lineno} (id {rec['id']}) is missing {', '.join(missing)}; "
                    "repair the line before writing"
                )
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


def add_locked(path, fields, target=None, now=None):
    """`add` under the store lock — the id read and the append must not interleave."""
    with store_lock(path):
        return add(path, fields, target=target, now=now)


def load_json_fields(source):
    """Note fields from a JSON object in a file (or stdin as `-`)."""
    try:
        raw = sys.stdin.read() if source == "-" else open(source, encoding="utf-8").read()
    except OSError as e:
        raise NoteError(f"cannot read {source}: {e}") from e
    try:
        data = json.loads(raw)
    except json.JSONDecodeError as e:
        raise NoteError(f"{source} is not valid JSON ({e})") from e
    if not isinstance(data, dict):
        raise NoteError(f"{source} must hold a JSON object with the note fields")
    return data


def render(notes):
    out = []
    for n in notes:
        head = f"### {n['id']}. {n.get('title', '(untitled)')}"
        if n.get("target"):
            head += f"  (target: {n['target']})"
        out.append(head)
        out.append(f"- **Issue:** {n.get('issue', '')}")
        out.append(f"- **Improvement:** {n.get('improvement', '')}")
        out.append(f"- **Principle:** {n.get('principle', '')}")
        out.append("")
    return "\n".join(out).rstrip()


def flush(path, through=None, now=None):
    """Mark pending notes with `id <= through` as routed. Returns the count marked.

    `through` is the highest id the `list` you just routed reported. Without it, a note
    captured while you were routing the earlier ones would be marked routed too and
    would leave the queue having never been read — so a bare flush is refused whenever
    anything is pending, and is a no-op when nothing is.
    """
    with store_lock(path):
        notes = read_notes(path)
        if notes is None:
            return None                       # absent store: a normal quiet session
        open_ids = [n["id"] for n in notes if not n.get("flushed")]
        if not open_ids:
            return 0                          # nothing pending: no rewrite, no race
        if through is None:
            raise NoteError(
                f"{len(open_ids)} note(s) pending (ids {min(open_ids)}-{max(open_ids)}): "
                "run `list`, route what it shows, then `flush --through <highest id listed>`"
            )
        stamp = time.strftime("%Y-%m-%d", time.localtime(now if now is not None else time.time()))
        marked = 0
        for n in notes:
            if not n.get("flushed") and n["id"] <= through:
                n["flushed"] = stamp
                marked += 1
        if marked == 0:
            return 0
        # Per-process temp name: two concurrent flushes must not share one scratch path.
        tmp = f"{path}.tmp.{os.getpid()}"
        try:
            with open(tmp, "w", encoding="utf-8") as f:
                for n in notes:
                    f.write(json.dumps(n, ensure_ascii=False, sort_keys=True) + "\n")
            os.replace(tmp, path)
        except BaseException:
            if os.path.exists(tmp):
                os.unlink(tmp)
            raise
        return marked


def cmd_add(args):
    path = resolve_store(args.store)
    fields = vars(args)
    target = args.target
    if args.from_json:
        data = load_json_fields(args.from_json)
        fields = data
        target = data.get("target", target)
    rec = add_locked(path, fields, target=target)
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
    print(f"\n# routed them all? flush --through {max(n['id'] for n in open_notes)}")
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
    marked = flush(path, through=args.through)
    if marked is None:
        print(f"NO-STORE {path} (nothing captured this session)")
        return 0
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

        expect_error("bare flush refused while notes are pending", lambda: flush(store))
        marked = flush(store, through=3)
        check("flush --through marks every note up to N", marked == 3)
        check("flush leaves nothing pending", pending(read_notes(store)) == [])
        check("flush preserves the history", len(read_notes(store)) == 3)
        check("second flush is a no-op", flush(store, through=3) == 0)

        # A note captured while the earlier ones were being routed must survive the flush.
        add(store, dict.fromkeys(FIELDS, "captured mid-routing"))
        check("mid-routing note is pending", [n["id"] for n in pending(read_notes(store))] == [4])
        check("flush --through 3 skips the later note", flush(store, through=3) == 0)
        check("later note still pending after a scoped flush",
              [n["id"] for n in pending(read_notes(store))] == [4])
        check("flush --through 4 clears it", flush(store, through=4) == 1)

        absent = os.path.join(tmpdir, "gone", "notes.jsonl")
        check("flush on an absent store is a no-op, not an error", flush(absent) is None)
        check("flush on an absent store creates nothing", not os.path.exists(absent))

        rendered = render(read_notes(store)[:1])
        check("render carries all three body fields",
              "**Issue:**" in rendered and "**Improvement:**" in rendered and "**Principle:**" in rendered)

        broken = os.path.join(tmpdir, "harness-capture", "broken.jsonl")
        with open(broken, "w", encoding="utf-8") as f:
            f.write("{not json}\n")
        expect_error("malformed store errors instead of reading empty", lambda: read_notes(broken))

        # A record `read_notes` accepts must be one `render` can print — no KeyError path.
        partial = os.path.join(tmpdir, "harness-capture", "partial.jsonl")
        with open(partial, "w", encoding="utf-8") as f:
            f.write(json.dumps({"id": 1, "title": "t"}) + "\n")
        expect_error("record missing body fields is refused at read", lambda: read_notes(partial))
        check("render survives a record without the body fields",
              "**Issue:**" in render([{"id": 9}]))

        # Every family in the shipped-asset ban list, including the two that sit in no
        # contiguous range with the rest and the separators `str.split()` would eat.
        for label, ch in (("C0", "\x01"), ("C1", "\x85"), ("DEL", "\x7f"),
                          ("U+061C bidi mark", "\u061c"), ("U+200B zero-width", "\u200b"),
                          ("U+202E override", "\u202e"), ("U+2060 word-joiner", "\u2060"),
                          ("U+2066 isolate", "\u2066"), ("U+2028 separator", "\u2028"),
                          ("U+FEFF BOM", "\ufeff")):
            expect_error(f"{label} refused",
                         lambda c=ch: add(store, dict.fromkeys(FIELDS, f"a{c}b")))
        check("tab and newline are collapsed, not refused",
              add(store, dict.fromkeys(FIELDS, "a\tb\nc"))["title"] == "a b c")
        flush(store, through=99)

        # --from-json: the route that keeps note text away from the shell.
        payload = os.path.join(tmpdir, "note.json")
        with open(payload, "w", encoding="utf-8") as f:
            json.dump({**dict.fromkeys(FIELDS, "$(echo pwned) `id`"), "target": "dev:x"}, f)
        loaded = load_json_fields(payload)
        rec_j = add(store, loaded, target=loaded.get("target"))
        check("shell metacharacters are stored verbatim, never expanded",
              rec_j["title"] == "$(echo pwned) `id`" and rec_j["target"] == "dev:x")
        expect_error("non-object JSON refused", lambda: load_json_fields(os.devnull))
        flush(store, through=rec_j["id"])

        # The lock is what makes the parallel-writer guarantee true. Asserted by holding
        # it and showing a second process cannot append until it is released — a real
        # race would be timing-dependent and would pass on a good day.
        if fcntl is not None:
            me = os.path.abspath(__file__)
            argv = [sys.executable, me, "add", "--store", store, "--target", "lock-probe"]
            for field in FIELDS:
                argv += [f"--{field}", "written while the lock was held"]
            before = len(read_notes(store))
            with store_lock(store):
                try:
                    subprocess.run(argv, timeout=2, capture_output=True)
                    blocked = False
                except subprocess.TimeoutExpired:
                    blocked = True
                check("a second writer blocks while the lock is held", blocked)
                check("and appended nothing meanwhile", len(read_notes(store)) == before)
            done = subprocess.run(argv, timeout=30, capture_output=True)
            check("the same writer succeeds once the lock is released", done.returncode == 0)
            check("its note landed", len(read_notes(store)) == before + 1)
        else:
            check("a second writer blocks while the lock is held (skipped: no fcntl)", True)

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
        # Not `required`: --from-json supplies the same fields without the shell in the
        # path. Whichever route is taken, `clean_field` refuses a missing one.
        p_add.add_argument(f"--{field}", default=None)
    p_add.add_argument("--target", default=None, help="the asset the note is about")
    p_add.add_argument("--from-json", dest="from_json", default=None,
                       help="read the note fields from a JSON object in PATH (or `-` for stdin); "
                            "use this whenever a field quotes text you did not author literally")
    p_add.set_defaults(func=cmd_add)

    for name, fn, help_text in (
        ("list", cmd_list, "print pending notes as markdown"),
        ("status", cmd_status, "NO-STORE | EMPTY | PENDING n"),
        ("flush", cmd_flush, "mark notes up to --through as routed"),
    ):
        p = sub.add_parser(name, help=help_text)
        if name == "flush":
            p.add_argument("--through", type=int, default=None,
                           help="highest note id the `list` you just routed showed")
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
