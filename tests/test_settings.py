import copy
import json
import os
import shutil
import stat
import sys
import tempfile
import unittest

sys.dont_write_bytecode = True
sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                                "libexec", "claudemeister", "lib"))
import claude_settings as cs  # noqa: E402
from mnrhlib import HOOKS, is_hook  # noqa: E402

FOREIGN = {"type": "command", "command": "~/bin/my-stop.sh"}
LEGACY = {"type": "command", "command": "/usr/local/bin/mnrh claude notify-hook", "async": True}


class Files(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp(prefix="cm-settings-")
        self.addCleanup(shutil.rmtree, self.dir, True)

    def test_load(self):
        path = os.path.join(self.dir, "settings.json")
        self.assertEqual(cs.load(path), {})
        with open(path, "w") as f:
            f.write("[]")
        self.assertRaises(ValueError, cs.load, path)
        with open(path, "w") as f:
            f.write("{oops")
        self.assertRaises(ValueError, cs.load, path)

    def test_save_keeps_symlink_and_mode(self):
        real = os.path.join(self.dir, "dotfiles", "settings.json")
        os.makedirs(os.path.dirname(real))
        with open(real, "w") as f:
            f.write("{}")
        os.chmod(real, 0o600)
        link = os.path.join(self.dir, "claude", "settings.json")
        os.makedirs(os.path.dirname(link))
        os.symlink(real, link)
        cs.save_atomic({"theme": "dark"}, link)
        self.assertTrue(os.path.islink(link))
        self.assertEqual(cs.load(real), {"theme": "dark"})
        self.assertEqual(stat.S_IMODE(os.stat(real).st_mode), 0o600)
        self.assertEqual(sorted(os.listdir(os.path.dirname(real))), ["settings.json"])

    def test_save_creates_folder(self):
        path = os.path.join(self.dir, "new", "settings.json")
        cs.save_atomic({"a": "б"}, path)
        with open(path) as f:
            self.assertEqual(f.read(), '{\n  "a": "б"\n}\n')


class Hooks(unittest.TestCase):
    def test_install_keeps_foreign_and_replaces_legacy(self):
        s = {"hooks": {"Stop": [{"hooks": [FOREIGN]}, {"hooks": [LEGACY]}]}, "theme": "dark"}
        cs.ensure_hooks(s)
        self.assertEqual(cs.missing(s, exact=True), [])
        stop = [h["command"] for e in s["hooks"]["Stop"] for h in e["hooks"]]
        self.assertEqual(stop[0], FOREIGN["command"])
        self.assertFalse(any("mnrh claude" in c for c in stop))
        self.assertEqual(len(cs.hook_commands(s)), len(HOOKS) + 1)

    def test_remove_is_symmetric(self):
        before = {"hooks": {"Stop": [{"hooks": [FOREIGN]}]}, "theme": "dark"}
        s = copy.deepcopy(before)
        cs.ensure_hooks(s)
        cs.ensure_hooks(s, install=False)
        self.assertEqual(s, before)
        s = {}
        cs.ensure_hooks(s)
        cs.ensure_hooks(s, install=False)
        self.assertEqual(s, {})

    def test_reinstall_is_stable(self):
        s = {}
        cs.ensure_hooks(s)
        once = copy.deepcopy(s)
        cs.ensure_hooks(s)
        self.assertEqual(s, once)

    def test_missing_checks_matcher_only_when_exact(self):
        s = {}
        cs.ensure_hooks(s)
        s["hooks"]["PreToolUse"][0]["matcher"] = "Bash"
        guard = [r for r in HOOKS if r[1] == "guard-hook"]
        self.assertEqual(cs.missing(s, guard), [])
        self.assertEqual(cs.missing(s, guard, exact=True), guard)

    def test_add_missing_hooks_adds_only_missing(self):
        rows = cs.rows("humanize-hook")
        self.assertEqual([r[0] for r in rows], ["SessionStart", "UserPromptSubmit", "Stop"])
        s = {"hooks": {"Stop": [{"hooks": [{"type": "command", "command": "claudemeister humanize-hook"}]}]}}
        self.assertEqual(cs.add_missing_hooks(s, rows), ["SessionStart", "UserPromptSubmit"])
        self.assertEqual(len(s["hooks"]["Stop"]), 1)
        self.assertEqual(cs.add_missing_hooks(s, rows), [])

    def test_malformed_hooks(self):
        for bad in ([1, 2], {"Stop": "x"}, {"Stop": [{"hooks": None}]}):
            self.assertRaises(ValueError, cs.hook_commands, {"hooks": bad})
        self.assertEqual(cs.hook_commands({"hooks": None}), [])


class StatusLine(unittest.TestCase):
    PS = "/opt/homebrew/bin/promptsill"
    STUB = {"type": "command", "command": "/usr/local/bin/mnrh claude statusline", "padding": 1}
    MINE = {"type": "command", "command": "~/bin/my-line.sh"}

    def after(self, line, promptsill, install=True):
        s = {"statusLine": copy.deepcopy(line)} if line is not None else {}
        changed = cs.ensure_statusline(s, promptsill, install)
        return s.get("statusLine"), changed

    def test_install(self):
        self.assertEqual(self.after(None, self.PS)[0]["command"], self.PS)
        self.assertEqual(self.after(self.STUB, self.PS)[0]["command"], self.PS)
        self.assertEqual(self.after(self.MINE, self.PS), (self.MINE, False))
        self.assertEqual(self.after(None, None), (None, False))
        line, changed = self.after(self.STUB, None)
        self.assertTrue(changed and is_hook(line["command"], "statusline") and "mnrh claude" not in line["command"])
        self.assertEqual(line["padding"], 1)

    def test_remove(self):
        self.assertEqual(self.after(self.STUB, None, False), (None, True))
        self.assertEqual(self.after({"command": self.PS}, None, False), (None, True))
        self.assertEqual(self.after(self.MINE, self.PS, False), (self.MINE, False))


class Humanize(unittest.TestCase):
    def test_rows_come_from_mnrhlib(self):
        import claude_humanize
        self.assertEqual(claude_humanize.HOOK_ROWS, [r for r in HOOKS if r[1] == "humanize-hook"])


if __name__ == "__main__":
    unittest.main()
