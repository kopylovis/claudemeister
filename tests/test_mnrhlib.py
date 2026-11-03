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
from mnrhlib import write_json  # noqa: E402


class WriteJson(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp(prefix="cm-json-")
        self.addCleanup(shutil.rmtree, self.dir, True)
        self.path = os.path.join(self.dir, "a.json")

    def test_writes_and_replaces(self):
        write_json(self.path, {"a": "б"}, ensure_ascii=False)
        with open(self.path, encoding="utf-8") as f:
            self.assertEqual(f.read(), '{"a": "б"}')
        write_json(self.path, {"a": 1}, newline=True, indent=2)
        with open(self.path) as f:
            self.assertEqual(f.read(), '{\n  "a": 1\n}\n')
        self.assertEqual(os.listdir(self.dir), ["a.json"])

    def test_private_mode(self):
        write_json(self.path, {}, mode=0o600)
        self.assertEqual(stat.S_IMODE(os.stat(self.path).st_mode), 0o600)

    def test_failure_keeps_old_file_and_no_temp(self):
        write_json(self.path, {"ok": True})
        with self.assertRaises(TypeError):
            write_json(self.path, {"bad": object()})
        with open(self.path) as f:
            self.assertEqual(json.load(f), {"ok": True})
        self.assertEqual(os.listdir(self.dir), ["a.json"])


if __name__ == "__main__":
    unittest.main()
