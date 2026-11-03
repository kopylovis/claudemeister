import json
import os
import shutil
import subprocess
import sys
import tempfile
import textwrap
import unittest

LIB = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "libexec", "claudemeister", "lib")


def in_sandbox(code):
    """Пути считаются при импорте, поэтому каждый тест идет в своем процессе с временным HOME."""
    home = tempfile.mkdtemp(prefix="cm-test-")
    env = dict(os.environ, HOME=home, XDG_CACHE_HOME=os.path.join(home, ".cache"), PYTHONDONTWRITEBYTECODE="1")
    env.pop("MNRH_CLAUDE_HOME", None)
    r = subprocess.run([sys.executable, "-c", f"import sys; sys.path.insert(0, {LIB!r})\n" + textwrap.dedent(code)],
                       env=env, capture_output=True, text=True)
    shutil.rmtree(home, True)
    return r, home


class Forget(unittest.TestCase):
    def test_forget_caches_removes_only_this_session(self):
        r, home = in_sandbox("""
            import os, session_store as s
            sid, other = "11111111-2222-3333-4444-555555555555", "99999999-2222-3333-4444-555555555555"
            cache = os.path.join(os.environ["HOME"], ".cache", "mnrh")
            files = ["slim-backup/{}.20260101-000000.jsonl.gz", "notify/{}.json", "humanize/{}.hits",
                     "humanize/{}.primed", "guard/{}.json", "notice/{}.json"]
            for f in files:
                for x in (sid, other):
                    p = os.path.join(cache, f.format(x))
                    os.makedirs(os.path.dirname(p), exist_ok=True)
                    open(p, "w").close()
            s.forget_caches(sid)
            s.forget_caches("../../etc")
            left = sorted(os.path.relpath(os.path.join(d, n), cache) for d, _, ns in os.walk(cache) for n in ns)
            print("\\n".join(left))
        """)
        self.assertEqual(r.returncode, 0, r.stderr)
        left = r.stdout.split()
        self.assertEqual(len(left), 6, left)
        self.assertTrue(all("99999999" in p for p in left), left)


class Offline(unittest.TestCase):
    def test_unplugged_drive_is_not_an_orphan(self):
        r, _ = in_sandbox("""
            import session_store as s
            print(s.offline("/Volumes/NoSuchDrive-cm/proj", "-Volumes-NoSuchDrive-cm-proj"))
            print(s.offline(None, "-Volumes-NoSuchDrive-cm-proj"))
            print(s.offline("/Users/x/gone", "-Users-x-gone"))
            print(s.offline(None, "-Users-x-gone"))
        """)
        self.assertEqual(r.stdout.split(), ["True", "True", "False", "False"], r.stderr)


class Statusline(unittest.TestCase):
    def run_case(self, settings):
        code = """
            import json, os, claude_setup as c
            c.promptsill_path = lambda: "/opt/homebrew/bin/promptsill"
            os.makedirs(c.CLAUDE, exist_ok=True)
            path = os.path.join(c.CLAUDE, "settings.json")
            if SETTINGS is not None:
                json.dump(SETTINGS, open(path, "w"))
            c.promptsill_statusline()
            print(json.dumps(json.load(open(path)).get("statusLine")))
        """.replace("SETTINGS", repr(settings))
        r, _ = in_sandbox(code)
        self.assertEqual(r.returncode, 0, r.stderr)
        return json.loads(r.stdout.splitlines()[-1])

    def test_empty_settings_get_promptsill(self):
        self.assertEqual(self.run_case(None)["command"], "/opt/homebrew/bin/promptsill")
        self.assertEqual(self.run_case({"theme": "dark"})["command"], "/opt/homebrew/bin/promptsill")

    def test_stub_is_replaced_and_foreign_line_kept(self):
        stub = {"type": "command", "command": "/usr/local/bin/claudemeister statusline"}
        self.assertEqual(self.run_case({"statusLine": stub})["command"], "/opt/homebrew/bin/promptsill")
        mine = {"type": "command", "command": "~/bin/my-line.sh"}
        self.assertEqual(self.run_case({"statusLine": mine}), mine)


class Parallel(unittest.TestCase):
    def test_running_in_matches_whole_folders(self):
        r, _ = in_sandbox("""
            import json, os, claude_parallel as p
            d = os.path.join(os.environ["HOME"], ".claude", "sessions")
            os.makedirs(d)
            json.dump({"pid": os.getpid(), "cwd": "/w/app-fix-login"}, open(os.path.join(d, "1.json"), "w"))
            json.dump({"cwd": "/w/app-drop"}, open(os.path.join(d, "2.json"), "w"))
            print(p.running_in("/w/app-fix"), p.running_in("/w/app-fix-login"), p.running_in("/w/app-drop"))
        """)
        self.assertEqual(r.stdout.split(), ["False", "True", "False"], r.stderr)


SESSIONS = """
    import json, os, re, time
    home = os.environ["HOME"]
    proj = os.path.join(home, "proj")
    os.makedirs(proj)
    pdir = os.path.join(home, ".claude", "projects", re.sub("[^A-Za-z0-9]", "-", proj))
    os.makedirs(pdir)
    ids = {"real": "11111111-2222-3333-4444-555555555555", "junk": "22222222-2222-3333-4444-555555555555",
           "other": "33333333-2222-3333-4444-555555555555"}
    def write(sid, lines):
        with open(os.path.join(pdir, sid + ".jsonl"), "w") as f:
            f.writelines(json.dumps(l, separators=(",", ":")) + "\\n" for l in lines)
    user = {"type": "user", "cwd": proj, "gitBranch": "main", "message": {"content": "fix it"}}
    write(ids["real"], [user, {"type": "assistant", "message": {"content": [{"type": "text", "text": "done"}]}},
                        {"type": "ai-title", "aiTitle": "Fixing"}])
    write(ids["other"], [user, {"type": "assistant", "message": {"content": [{"type": "text", "text": "done"}]}}])
    write(ids["junk"], [{"type": "user", "message": {"content": "<command-name>/model</command-name>"}}])
    os.makedirs(os.path.join(pdir, ids["real"], "subagents"))
    open(os.path.join(pdir, ids["real"], "subagents", "a.jsonl"), "w").write("{}\\n")
    old = time.time() - 3600
    for sid in ids.values():
        os.utime(os.path.join(pdir, sid + ".jsonl"), (old, old))
"""


def with_sessions(code):
    return in_sandbox(textwrap.dedent(SESSIONS) + textwrap.dedent(code))


class Lookup(unittest.TestCase):
    def test_by_id_matches_load(self):
        r, _ = with_sessions("""
            import session_store as s
            full = {x["id"]: x for x in s.load()}
            for sid in ids.values():
                assert s.by_id(sid) == [full[sid]], sid
                quick = s.by_id(sid, meta=False)[0]
                assert all(quick[k] == full[sid][k] for k in ("id", "pdir", "path", "extra", "link", "size", "cwd")), sid
            print(full[ids["real"]]["title"], s.by_id("nope"), s.by_id("44444444-2222-3333-4444-555555555555"))
        """)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(r.stdout.split(), ["Fixing", "[]", "[]"])

    def test_forget_and_drop_junk_touch_only_their_session(self):
        r, _ = with_sessions("""
            import claude_restart as cr
            print(cr.drop_junk(os.path.join(pdir, ids["real"] + ".jsonl"), "test"))
            print(cr.drop_junk(os.path.join(pdir, ids["junk"] + ".jsonl"), "test"))
            print(cr.forget_session(ids["real"]), cr.forget_session(ids["real"]))
            print(sorted(os.listdir(pdir)))
        """)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(r.stdout.splitlines(), ["False", "True", "True False",
                                                 "['33333333-2222-3333-4444-555555555555.jsonl']"])

    def test_sweep_removes_old_junk_only(self):
        r, _ = with_sessions("""
            import claude_restart as cr
            cr.sweep(pdir, ids["other"])
            print(sorted(os.listdir(pdir)))
        """)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertNotIn("22222222", r.stdout)
        self.assertIn("11111111-2222-3333-4444-555555555555.jsonl", r.stdout)

    def test_last_session_survives_a_vanishing_file(self):
        r, _ = with_sessions("""
            import claude_brief as b
            real = os.path.getmtime
            def flaky(p):
                if ids["other"] in p:
                    raise FileNotFoundError(p)
                return real(p)
            b.os.path.getmtime = flaky
            print(b.last_session(proj, proj)[0])
        """)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(r.stdout.strip(), "11111111-2222-3333-4444-555555555555")


if __name__ == "__main__":
    unittest.main()
