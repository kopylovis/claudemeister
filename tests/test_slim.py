import os
import sys
import tempfile
import unittest

sys.dont_write_bytecode = True
sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                                "libexec", "claudemeister", "lib"))
import claude_slim as slim  # noqa: E402


class Race(unittest.TestCase):
    def test_lines_added_after_scan_stop_the_rewrite(self):
        folder = tempfile.mkdtemp()
        src, dst = os.path.join(folder, "s.jsonl"), os.path.join(folder, "out")
        with open(src, "w") as f:
            f.write('{"type":"user"}\n{"type":"assistant"}\n')
        info = slim.scan(src)
        with open(src, "a") as f:
            f.write('{"type":"user"}\n')
        prot = {"flags": [True] * len(info)}
        with self.assertRaises(slim.SlimError):
            slim.write_slim(src, dst, info, prot)


if __name__ == "__main__":
    unittest.main()
