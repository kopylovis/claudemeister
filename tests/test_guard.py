import os
import sys
import unittest

sys.dont_write_bytecode = True
sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                                "libexec", "claudemeister", "lib"))
import claude_guard as g  # noqa: E402


class Grep(unittest.TestCase):
    def blocked(self, **inp):
        return g.check("Grep", dict(pattern=".", **inp), "/tmp") is not None

    def test_secret_files_and_masks(self):
        self.assertTrue(self.blocked(path="fastlane/.env.default"))
        self.assertTrue(self.blocked(path="~/.ssh/id_ed25519"))
        self.assertTrue(self.blocked(glob=".env*"))
        self.assertTrue(self.blocked(glob="**/.env.*"))
        self.assertTrue(self.blocked(glob="*.p8"))

    def test_ordinary_searches(self):
        self.assertFalse(self.blocked(path="src"))
        self.assertFalse(self.blocked(glob="*.kt"))
        self.assertFalse(self.blocked(glob="*"))
        self.assertFalse(self.blocked(glob="**/*.properties"))
        self.assertFalse(self.blocked(path=".env.example"))
        self.assertFalse(self.blocked())


class Files(unittest.TestCase):
    def test_read_of_a_secret(self):
        self.assertIsNotNone(g.check("Read", {"file_path": "/p/.env"}, "/p"))
        self.assertIsNone(g.check("Read", {"file_path": "/p/README.md"}, "/p"))


if __name__ == "__main__":
    unittest.main()
