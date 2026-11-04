import os
import sys
import unittest

sys.dont_write_bytecode = True
sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                                "libexec", "claudemeister", "lib"))
import claude_proxy as p  # noqa: E402


class Version(unittest.TestCase):
    def setUp(self):
        self.saved = p.mihomo_bin, p.installed_version, p.brew_latest, p.running_version

    def tearDown(self):
        p.mihomo_bin, p.installed_version, p.brew_latest, p.running_version = self.saved

    def fake(self, have, latest=None, running=None, exe="/opt/homebrew/Cellar/mihomo/1.19.33/bin/mihomo"):
        p.mihomo_bin = lambda: exe
        p.installed_version = lambda exe=None: have
        p.brew_latest = lambda: latest
        p.running_version = lambda: running

    def test_parse_and_compare(self):
        self.assertEqual(p.parse_version("Mihomo Meta v1.19.33 darwin arm64"), "1.19.33")
        self.assertIsNone(p.parse_version("garbage"))
        self.assertTrue(p.newer("1.20.0", "1.19.99"))
        self.assertFalse(p.newer("1.19.33", "1.19.33"))
        self.assertFalse(p.newer(None, "1.19.33"))

    def test_up_to_date(self):
        self.fake("1.19.33", latest="1.19.33", running="1.19.33")
        self.assertEqual(p.version_line(), "mihomo 1.19.33 из Homebrew")

    def test_update_available_and_old_process(self):
        self.fake("1.19.34", latest="1.19.35", running="1.19.33")
        line = p.version_line()
        self.assertIn("есть 1.19.35: claudemeister proxy update", line)
        self.assertIn("Запущен еще 1.19.33", line)

    def test_not_from_brew_skips_latest(self):
        self.fake("1.19.33", latest="9.9.9", exe="/usr/local/bin/mihomo")
        self.assertNotIn("proxy update", p.version_line())


if __name__ == "__main__":
    unittest.main()
