import os
import subprocess
import sys
import tempfile
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


class Doctor(unittest.TestCase):
    def test_fresh_home_is_reported_broken(self):
        home = tempfile.mkdtemp(prefix="cm-doctor-")
        env = dict(os.environ, HOME=home, XDG_CONFIG_HOME=os.path.join(home, ".config"), ZDOTDIR=home,
                   PYTHONDONTWRITEBYTECODE="1", MNRH_CLAUDE_BIN="/nonexistent/claude")
        env.pop("MNRH_CLAUDE_HOME", None)
        r = subprocess.run([os.path.join(ROOT, "bin", "claudemeister"), "doctor"], env=env, capture_output=True,
                           text=True, timeout=60)
        self.assertEqual(r.returncode, 1, r.stdout + r.stderr)
        self.assertIn("нет /nonexistent/claude в PATH", r.stdout)
        self.assertIn("нет команд", r.stdout)
        self.assertIn("claudemeister setup", r.stdout)
        self.assertNotIn("Traceback", r.stdout + r.stderr)


if __name__ == "__main__":
    unittest.main()
