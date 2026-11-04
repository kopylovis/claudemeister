import os
import subprocess
import sys
import tempfile
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "libexec", "claudemeister", "lib"))

import claude_doctor  # noqa: E402


def hooks(*commands):
    return {"hooks": {"Stop": [{"hooks": [{"type": "command", "command": c} for c in commands]}]}}


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


class ItermHooks(unittest.TestCase):
    def test_no_iterm_integration_says_nothing(self):
        self.assertEqual(claude_doctor.iterm_rows(hooks("claudemeister notify-hook")), [])
        self.assertEqual(claude_doctor.iterm_rows({}), [])

    def test_new_hook_is_ok(self):
        rows = claude_doctor.iterm_rows(hooks("~/.config/iterm2/it2 handle-agent-hook", "claudemeister notify-hook"))
        self.assertEqual([r[0] for r in rows], ["ok"])

    def test_old_hook_that_still_exists_warns(self):
        path = tempfile.mkdtemp(prefix="cm-iterm-")
        binary = os.path.join(path, "cc-status")
        open(binary, "w").close()
        rows = claude_doctor.iterm_rows(hooks(binary, binary))
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0][0], "warn")
        self.assertIn("Install Claude Code Integration", rows[0][1])

    def test_old_hook_with_missing_file_is_broken(self):
        rows = claude_doctor.iterm_rows(hooks("/nonexistent-cm/iterm2/cc-status"))
        self.assertEqual(rows[0][0], "bad")
        self.assertIn("/nonexistent-cm/iterm2/cc-status", rows[0][1])

    def test_unparsable_command_is_skipped(self):
        self.assertEqual(claude_doctor.iterm_rows(hooks("cc-status 'unclosed")), [])


if __name__ == "__main__":
    unittest.main()
