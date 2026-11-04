import os
import sys
import unittest

sys.dont_write_bytecode = True
sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                                "libexec", "claudemeister", "lib"))
import claude_lid as lid  # noqa: E402

PS = """  101 /bin/zsh -l
  202 caffeinate -is /Users/me/.local/bin/claude --resume 1234
  303 caffeinate -is claude
  404 caffeinate -i make
  505 /usr/bin/python3 claude_proxy.py run -- caffeinate -is claude
  606 caffeinate -is /opt/claude-tools/bin/claude-wrapper
"""


class Lid(unittest.TestCase):
    def setUp(self):
        self.saved = lid.run, lid.SUDOERS

    def tearDown(self):
        lid.run, lid.SUDOERS = self.saved

    def test_sessions_are_caffeinate_wrapping_claude(self):
        lid.run = lambda cmd, **kw: PS
        self.assertEqual(lid.sessions(), [202, 303])

    def test_not_ready_without_rule(self):
        lid.SUDOERS = "/nonexistent-cm/sudoers.d/mnrh-pmset"
        self.assertFalse(lid.ready())
        self.assertFalse(lid.keep())
        self.assertIn("claudemeister lid setup", lid.status_lines()[0])

    def test_disabled_reads_pmset(self):
        lid.run = lambda cmd, **kw: " SleepDisabled\t\t1\n hibernatemode 3\n"
        self.assertTrue(lid.disabled())
        lid.run = lambda cmd, **kw: " hibernatemode 3\n"
        self.assertFalse(lid.disabled())

    def test_unknown_command(self):
        self.assertEqual(lid.main(["nope"]), 2)


if __name__ == "__main__":
    unittest.main()
