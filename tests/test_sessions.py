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


if __name__ == "__main__":
    unittest.main()
