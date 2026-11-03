import json
import os
import shutil
import subprocess
import sys
import tempfile
import textwrap
import unittest

LIB = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "libexec", "claudemeister", "lib")
RESTART_MD = "!`claudemeister restart $ARGUMENTS`\n"
LEGACY_MD = "!`/usr/local/bin/mnrh claude restart $ARGUMENTS`\n"
sys.path.insert(0, LIB)
from mnrhlib import HOOKS  # noqa: E402

ALL = ["commands", "hooks", "zsh", "mcp", "notify-app", "proxy", "promptsill"]


def sandbox(files, code, promptsill=False):
    """Код в отдельном процессе с временным HOME и файлами files {путь от HOME: текст или объект для JSON}.
    Печатает то, что напечатал код; второй элемент — содержимое файлов после него."""
    home = tempfile.mkdtemp(prefix="cm-test-")
    try:
        for rel, body in files.items():
            path = os.path.join(home, rel)
            os.makedirs(os.path.dirname(path), exist_ok=True)
            with open(path, "w") as f:
                f.write(body if isinstance(body, str) else json.dumps(body))
        fake = os.path.join(home, "bin", "promptsill")
        if promptsill:
            os.makedirs(os.path.dirname(fake))
            with open(fake, "w") as f:
                f.write("#!/bin/sh\n")
            os.chmod(fake, 0o755)
        env = dict(os.environ, HOME=home, XDG_CONFIG_HOME=os.path.join(home, ".config"), PYTHONDONTWRITEBYTECODE="1",
                   XDG_STATE_HOME=os.path.join(home, ".state"), MNRH_PROMPTSILL=fake, ZDOTDIR=home)
        env.pop("MNRH_CLAUDE_HOME", None)
        r = subprocess.run([sys.executable, "-c", f"import sys; sys.path.insert(0, {LIB!r})\n" + textwrap.dedent(code)],
                           env=env, capture_output=True, text=True)
        if r.returncode != 0:
            raise AssertionError(r.stderr)
        after = {}
        for rel in files:
            path = os.path.join(home, rel)
            if os.path.exists(path):
                with open(path) as f:
                    text = f.read()
                try:
                    after[rel] = json.loads(text)
                except ValueError:
                    after[rel] = text
        return r.stdout, after
    finally:
        shutil.rmtree(home, True)


def hooks(*rows):
    """settings.json с хуками [(событие, команда, matcher)]."""
    out = {}
    for event, command, matcher in rows:
        out.setdefault(event, []).append(dict({"matcher": matcher} if matcher else {},
                                              hooks=[{"type": "command", "command": command}]))
    return {"hooks": out}


def current_hooks():
    return dict(hooks(*[(e, f"claudemeister {c}", m) for e, c, _, m in HOOKS]), cleanupPeriodDays=365)


SETTINGS = ".claude/settings.json"
RESTART = ".claude/commands/restart.md"
STATE = ".config/mnrh/claude-setup.json"
DONE = {"done": ALL, "skipped": []}


class Pending(unittest.TestCase):
    def pending(self, files, promptsill=False):
        out, _ = sandbox(files, """
            import claude_setup as c
            steps, fresh = c.pending()
            print(("fresh " if fresh else "") + " ".join(steps))
        """, promptsill)
        return out.split()

    def test_first_run(self):
        self.assertEqual(self.pending({}), ["fresh"] + ALL)

    def test_before_state_was_recorded(self):
        self.assertEqual(self.pending({RESTART: RESTART_MD}), ["proxy", "promptsill"])
        self.assertEqual(self.pending({RESTART: RESTART_MD}, promptsill=True), ["proxy"])

    def test_all_done(self):
        self.assertEqual(self.pending({RESTART: RESTART_MD, STATE: DONE, SETTINGS: current_hooks()}), [])

    def test_skipped_is_not_asked_again(self):
        state = {"done": ["commands", "hooks", "zsh", "mcp", "notify-app"], "skipped": ["proxy", "promptsill"]}
        self.assertEqual(self.pending({RESTART: RESTART_MD, STATE: state}), [])

    def test_new_step_after_update(self):
        state = {"done": ["commands", "hooks", "zsh", "mcp", "proxy", "promptsill"], "skipped": []}
        self.assertEqual(self.pending({RESTART: RESTART_MD, STATE: state}), ["notify-app"])

    def test_outdated_hooks(self):
        settings = current_hooks()
        settings["hooks"]["PreToolUse"][0]["matcher"] = "Bash|Read"
        self.assertEqual(self.pending({RESTART: RESTART_MD, STATE: DONE, SETTINGS: settings}), ["hooks"])
        del settings["hooks"]["PreToolUse"]
        self.assertEqual(self.pending({RESTART: RESTART_MD, STATE: DONE, SETTINGS: settings}), ["hooks"])
        # без settings.json хуки не проверить: не предлагаем
        self.assertEqual(self.pending({RESTART: RESTART_MD, STATE: DONE}), [])

    def test_new_hook_after_update(self):
        # установка до buildlog: хука PostToolUse нет, setup предлагают снова
        settings = current_hooks()
        del settings["hooks"]["PostToolUse"]
        self.assertEqual(self.pending({RESTART: RESTART_MD, STATE: DONE, SETTINGS: settings}), ["hooks"])

    def test_retention_not_set(self):
        settings = current_hooks()
        del settings["cleanupPeriodDays"]
        self.assertEqual(self.pending({RESTART: RESTART_MD, STATE: DONE, SETTINGS: settings}), ["hooks"])
        settings["cleanupPeriodDays"] = 30
        self.assertEqual(self.pending({RESTART: RESTART_MD, STATE: DONE, SETTINGS: settings}), [])

    def test_legacy_restart_command(self):
        files = {RESTART: LEGACY_MD, STATE: DONE, SETTINGS: current_hooks()}
        self.assertEqual(self.pending(files), ["commands", "hooks", "mcp"])
        files[STATE] = dict(DONE, legacy_declined=True)
        self.assertEqual(self.pending(files), [])

    def test_legacy_hook_and_statusline(self):
        settings = current_hooks()
        settings["hooks"]["Stop"].append({"hooks": [{"type": "command", "command": "/x/mnrh claude notify-hook"}]})
        self.assertEqual(self.pending({RESTART: RESTART_MD, STATE: DONE, SETTINGS: settings}),
                         ["commands", "hooks", "mcp"])
        settings = dict(current_hooks(), statusLine={"type": "command", "command": "mnrh claude statusline"})
        self.assertEqual(self.pending({RESTART: RESTART_MD, STATE: DONE, SETTINGS: settings}),
                         ["commands", "hooks", "mcp"])

    def test_legacy_mcp_server(self):
        base = {RESTART: RESTART_MD, STATE: DONE, SETTINGS: current_hooks()}
        ours = {"mcpServers": {"mnrh": {"command": "/usr/local/bin/mnrh", "args": ["claude", "mcp"]}}}
        foreign = {"mcpServers": {"mnrh": {"command": "npx", "args": ["mnrh-server"]}}}
        self.assertEqual(self.pending(dict(base, **{".claude.json": ours})), ["commands", "hooks", "mcp"])
        self.assertEqual(self.pending(dict(base, **{".claude.json": foreign})), [])

    def test_broken_files_do_not_crash(self):
        for settings in ("not json", "[]", '{"hooks": [1]}', '{"hooks": {"Stop": "x"}}'):
            self.assertEqual(self.pending({RESTART: RESTART_MD, STATE: DONE, SETTINGS: settings}), [], settings)
        self.assertEqual(self.pending({RESTART: RESTART_MD, STATE: "not json"}), ["proxy", "promptsill"])


class Check(unittest.TestCase):
    def check(self, files, answer):
        return sandbox(files, f"""
            import sys, claude_setup as c
            sys.stdin.isatty = sys.stdout.isatty = lambda: True
            c.ask = lambda *a, **k: {answer!r}
            c.check()
        """)

    def test_not_a_terminal_is_silent(self):
        out, _ = sandbox({}, "import claude_setup as c; print(c.check())")
        self.assertEqual(out, "0\n")

    def test_later_records_nothing(self):
        out, after = self.check({RESTART: RESTART_MD, STATE: {"done": ["commands"], "skipped": []}}, "later")
        self.assertIn("Спрошу при следующем запуске", out)
        self.assertEqual(after[STATE], {"done": ["commands"], "skipped": []})

    def test_never_skips_new_steps(self):
        out, after = self.check({RESTART: RESTART_MD, STATE: {"done": ["commands"], "skipped": []}}, "never")
        self.assertIn("Больше не спрошу", out)
        self.assertEqual(after[STATE]["done"], ["commands"])
        self.assertEqual(after[STATE]["skipped"], sorted(set(ALL) - {"commands"}))

    def test_never_on_legacy_remembers_the_refusal(self):
        out, after = self.check({RESTART: LEGACY_MD, STATE: DONE}, "never")
        self.assertIn("mnrh claude", out)
        self.assertTrue(after[STATE].get("legacy_declined"))
        self.assertEqual(after[STATE]["skipped"], [])


class NoticeHook(unittest.TestCase):
    FOREIGN = {"hooks": {"Stop": [{"hooks": [{"type": "command", "command": "~/bin/stop.sh"}]}]},
               "permissions": {"allow": ["Bash(ls)"]}, "theme": "dark"}

    def run_hook(self, settings, code="s.notice_hook(True)", promptsill=False):
        out, _ = sandbox({SETTINGS: settings} if settings is not None else {}, f"""
            import setup_cmd as s
            {code}
            import os, json
            p = s.SETTINGS
            print("---")
            print(open(p).read() if os.path.exists(p) else "null")
        """, promptsill)
        text, _, data = out.partition("---\n")
        return text, json.loads(data)

    def test_install_keeps_foreign_and_remove_restores(self):
        text, data = self.run_hook(self.FOREIGN)
        commands = [h["command"] for e in data["hooks"]["Stop"] for h in e["hooks"]]
        self.assertEqual(commands[0], "~/bin/stop.sh")
        self.assertEqual(data["theme"], "dark")
        self.assertIn("Bash(ls)", data["permissions"]["allow"])
        self.assertIn("mcp__claudemeister__session_read", data["permissions"]["allow"])
        _, back = self.run_hook(self.FOREIGN, "s.notice_hook(True); s.notice_hook(False)")
        self.assertEqual(back, self.FOREIGN)

    def test_retention(self):
        text, data = self.run_hook({})
        self.assertEqual(data["cleanupPeriodDays"], 365)
        self.assertIn("365 дней вместо 30", text)
        text, data = self.run_hook({"cleanupPeriodDays": 90}, "s.notice_hook(True); s.notice_hook(False)")
        self.assertEqual(data["cleanupPeriodDays"], 90)

    def test_empty_settings_round_trip(self):
        _, back = self.run_hook(None, "s.notice_hook(True); s.notice_hook(False)")
        self.assertEqual(back, {})

    def test_statusline(self):
        text, data = self.run_hook({}, promptsill=True)
        self.assertTrue(data["statusLine"]["command"].endswith("/bin/promptsill"))
        self.assertIn("✓ строка состояния → statusLine (promptsill)", text)
        mine = {"type": "command", "command": "~/bin/line.sh"}
        text, data = self.run_hook({"statusLine": mine}, promptsill=True)
        self.assertEqual(data["statusLine"], mine)
        self.assertIn("уже своя statusLine", text)
        stub = {"type": "command", "command": "/x/mnrh claude statusline", "padding": 0}
        text, data = self.run_hook({"statusLine": stub})
        self.assertTrue(data["statusLine"]["command"].endswith("claudemeister statusline"))
        self.assertIn("отдельная программа promptsill", text)
        _, data = self.run_hook({"statusLine": stub}, "s.notice_hook(False)")
        self.assertNotIn("statusLine", data)

    def test_old_mcp_rules_are_renamed(self):
        settings = {"permissions": {"allow": ["mcp__mnrh__todo_list", "mcp__mnrh__ask_project", "mcp__other__x"],
                                    "deny": ["mcp__mnrh"]}}
        _, data = self.run_hook(settings, "s.notice_hook(True, rename_rules=True)")
        allow = data["permissions"]["allow"]
        self.assertIn("mcp__claudemeister__ask_project", allow)
        self.assertIn("mcp__other__x", allow)
        self.assertFalse(any(r.startswith("mcp__mnrh__") for r in allow))
        self.assertEqual(allow.count("mcp__claudemeister__todo_list"), 1)
        self.assertEqual(data["permissions"]["deny"], ["mcp__claudemeister"])
        _, data = self.run_hook(settings, "s.notice_hook(True)")
        self.assertIn("mcp__mnrh__ask_project", data["permissions"]["allow"])
        self.assertNotIn("mcp__mnrh__todo_list", data["permissions"]["allow"])

    def test_broken_json_is_left_alone(self):
        out, after = sandbox({SETTINGS: "{oops"}, "import setup_cmd as s; s.notice_hook(True)")
        self.assertIn("не читается как JSON", out)
        self.assertEqual(after[SETTINGS], "{oops")

    def test_symlink_stays(self):
        out, _ = sandbox({"dotfiles/settings.json": self.FOREIGN}, """
            import os, stat, setup_cmd as s
            real = os.path.join(os.environ["HOME"], "dotfiles", "settings.json")
            os.chmod(real, 0o600)
            os.makedirs(os.path.dirname(s.SETTINGS))
            os.symlink(real, s.SETTINGS)
            s.notice_hook(True)
            print(os.path.islink(s.SETTINGS), oct(stat.S_IMODE(os.stat(real).st_mode)), "hooks" in open(real).read())
        """)
        self.assertEqual(out.split()[-3:], ["True", "0o600", "True"])


if __name__ == "__main__":
    unittest.main()
