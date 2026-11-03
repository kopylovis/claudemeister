import os
import sys
import unittest

sys.dont_write_bytecode = True
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from test_sessions import in_sandbox  # noqa: E402


class Lost(unittest.TestCase):
    def test_only_dead_sessions_with_a_transcript_come_back(self):
        r, _ = in_sandbox("""
            import json, os, time, claude_live as l
            home = os.environ["HOME"]
            gone, alive, empty = ("11111111-0000-0000-0000-000000000001", "11111111-0000-0000-0000-000000000002",
                                  "11111111-0000-0000-0000-000000000003")
            proj = os.path.join(home, ".claude", "projects", "-w-app")
            reg = os.path.join(home, ".claude", "sessions")
            os.makedirs(proj); os.makedirs(reg); os.makedirs(l.OPEN)
            for sid in (gone, alive):
                open(os.path.join(proj, sid + ".jsonl"), "w").write("{}\\n")
            json.dump({"pid": os.getpid(), "sessionId": alive}, open(os.path.join(reg, f"{os.getpid()}.json"), "w"))
            for sid, pid in ((gone, 99999999), (alive, os.getpid()), (empty, 99999998)):
                l.write_json(l.record_path(sid), {"sid": sid, "cwd": "/w/app", "pid": pid, "at": time.time()})
            l.write_json(l.record_path("old"), {"sid": "old", "at": time.time() - 40 * 86400})
            print(" ".join(r["sid"][-1] for r in l.lost()))
            print(sorted(os.listdir(l.OPEN)))
        """)
        self.assertEqual(r.returncode, 0, r.stderr)
        lines = r.stdout.splitlines()
        self.assertEqual(lines[0], "1")
        self.assertNotIn("old.json", lines[1])
        self.assertNotIn("0003", lines[1])


if __name__ == "__main__":
    unittest.main()
