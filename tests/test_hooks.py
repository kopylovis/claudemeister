import os
import sys
import unittest

sys.dont_write_bytecode = True
sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                                "libexec", "claudemeister", "lib"))
from mnrhlib import is_hook  # noqa: E402


class IsHook(unittest.TestCase):
    def test_ours(self):
        for command in ("/x/mnrh claude notice", "mnrh claude notice", "/x/claudemeister notice",
                        "claudemeister notice", "'/a b/claudemeister' notice", "'/a b/mnrh' claude notice --x",
                        "/opt/homebrew/bin/claudemeister notice"):
            self.assertTrue(is_hook(command, "notice"), command)

    def test_not_ours(self):
        for command in ("/x/claudemeister notice-x", "~/bin/my-claude notice", "npx @acme/claude notice",
                        "claude notice", "/x/notclaudemeister notice", "echo claudemeister", ""):
            self.assertFalse(is_hook(command, "notice"), command)


if __name__ == "__main__":
    unittest.main()
