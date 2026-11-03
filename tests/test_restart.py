import os
import subprocess
import sys
import time
import unittest

sys.dont_write_bytecode = True
sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                                "libexec", "claudemeister", "lib"))
import claude_proc as r  # noqa: E402


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


class LightImports(unittest.TestCase):
    """Хуки notify-hook и humanize-hook берут отсюда find_claude и тексты команд и не должны тянуть MCP-сервер."""
    HEAVY = {"mcp_server", "claude_restart", "gradle_deps", "claude_search", "claude_open", "claude_parallel",
             "window_shot", "http.client", "urllib.request"}

    def loaded(self, modules):
        code = (f"import sys; sys.path.insert(0, {os.path.dirname(r.__file__)!r}); import {modules}; "
                "print(' '.join(sys.modules))")
        out = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, check=True).stdout
        return set(out.split())

    def test_proc_and_slash_are_light(self):
        self.assertEqual(self.loaded("claude_proc, slash") & self.HEAVY, set())

    def test_restart_does_not_load_mcp_server(self):
        self.assertEqual(self.loaded("claude_restart") & (self.HEAVY - {"claude_restart", "claude_search"}), set())


class Incognito(unittest.TestCase):
    def test_restart_refuses_without_a_transcript(self):
        import claude_restart
        os.environ["CLAUDE_CODE_SKIP_PROMPT_HISTORY"] = "1"
        try:
            with self.assertRaises(claude_restart.RestartError):
                claude_restart.plan()
            with self.assertRaises(claude_restart.RestartError):
                claude_restart.plan(slim=True)
        finally:
            del os.environ["CLAUDE_CODE_SKIP_PROMPT_HISTORY"]


if __name__ == "__main__":
    unittest.main()
