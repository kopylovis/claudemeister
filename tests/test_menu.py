import os
import re
import sys
import unittest

sys.dont_write_bytecode = True
sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                                "libexec", "claudemeister", "lib"))
import mnrhlib  # noqa: E402
import claude_menu as m  # noqa: E402

ANSI = re.compile(r"\x1b\[[0-9;]*m")


class Menu(unittest.TestCase):
    def setUp(self):
        self.saved = mnrhlib.lang, m.mode_on

    def tearDown(self):
        mnrhlib.lang, m.mode_on = self.saved

    def plain(self, lang, on=()):
        mnrhlib.lang = lambda: lang
        m.mode_on = lambda name: name in on
        return [(k, ANSI.sub("", t)) for k, t in m.rows()]

    def test_rows_match_items(self):
        rows = self.plain("ru")
        self.assertEqual(len(rows), len(m.ITEMS))
        self.assertEqual([k is None for k, _ in rows], [k is None for k, _, _ in m.ITEMS])

    def test_both_languages(self):
        self.assertIn("Открыть проект", self.plain("ru")[1][1])
        self.assertIn("Open a project", self.plain("en")[1][1])

    def test_mode_shows_state_and_next_command(self):
        rows = dict((k.split(" ", 2)[1] if k and k.startswith("mode ") else k, t)
                    for k, t in self.plain("en", on={"guard"}))
        self.assertIn("[on]", rows["guard"])
        self.assertTrue(rows["guard"].endswith("claudemeister guard off"))
        self.assertIn("[off]", rows["lid"])
        self.assertTrue(rows["lid"].endswith("claudemeister lid on"))

    def test_commands_line_up(self):
        for lang in ("ru", "en"):
            cols = {t.index("claudemeister ") for k, t in self.plain(lang, on={"humanize"}) if k}
            self.assertEqual(len(cols), 1, lang)


if __name__ == "__main__":
    unittest.main()
