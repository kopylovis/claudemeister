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
            import os, claude_sessions as s
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
            import claude_sessions as s
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


if __name__ == "__main__":
    unittest.main()
