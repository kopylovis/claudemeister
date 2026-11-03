import os
import subprocess
import sys
import time
import unittest

sys.dont_write_bytecode = True
sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                                "libexec", "claudemeister", "lib"))
import claude_restart as r  # noqa: E402


class ResumeFlags(unittest.TestCase):
    def check(self, args, want):
        self.assertEqual(r.resume_flags(args), want, args)

    def test_prompt_after_a_boolean_flag_is_dropped(self):
        self.check(["--dangerously-skip-permissions", "fix the flaky test"], ["--dangerously-skip-permissions"])
        self.check(["--verbose", "hi"], ["--verbose"])

    def test_values_are_kept(self):
        self.check(["--model", "opus", "--add-dir", "a", "b", "--verbose"],
                   ["--model", "opus", "--add-dir", "a", "b", "--verbose"])
        self.check(["--model=sonnet", "prompt"], ["--model=sonnet"])
        self.check(["--permission-mode", "plan", "--", "-x"], ["--permission-mode", "plan"])

    def test_session_flags_are_dropped(self):
        self.check(["-r", "abc", "--name", "x", "--remote-control"], ["--name", "x", "--remote-control"])
        self.check(["--remote-control", "-c"], ["--remote-control"])
        self.check(["-p", "question", "-w", "feat", "--session-id", "u"], [])

    def test_unknown_flag_takes_one_word(self):
        self.check(["--unknown", "val", "--mystery"], ["--unknown", "val", "--mystery"])

    def test_drop_name(self):
        self.assertEqual(r.drop_name(["-n", "my name", "--model", "opus", "--name=x"]), ["--model", "opus"])

    def test_split_caffeinate(self):
        self.assertEqual(r.split_caffeinate(["-is", "-t", "60", "claude", "-c"]), (["-is", "-t", "60"],
                                                                                   ["claude", "-c"]))


class Argv(unittest.TestCase):
    def test_exact_argv_of_a_live_process(self):
        args = ["-c", "import time; time.sleep(5)", "what's wrong", "fix the  flaky test", "юникод", ""]
        p = subprocess.Popen([sys.executable] + args)
        try:
            time.sleep(0.3)
            self.assertEqual(r.argv_of(p.pid)[1:], args)
        finally:
            p.kill()
            p.wait()

    def test_missing_process(self):
        self.assertIsNone(r.proc_argv(99999999))


if __name__ == "__main__":
    unittest.main()
