import os
import sys
import textwrap
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from test_sessions import in_sandbox  # noqa: E402

# синтетические переписки во временном HOME: время считается от «сейчас», чтобы --days работал
SETUP = """
import json, os, time
from datetime import datetime, timezone
import claude_search as cs, claude_usage as cu
A = "aaaaaaaa-0000-0000-0000-000000000000"
B = "bbbbbbbb-0000-0000-0000-000000000000"
NOW = time.time()
os.makedirs(os.path.join(os.environ["HOME"], "work", "alpha"), exist_ok=True)
os.makedirs(os.path.join(os.environ["HOME"], "work", "beta"), exist_ok=True)

def proj(name):
    d = os.path.join(cs.PROJECTS, cs.re.sub(r"[^A-Za-z0-9]", "-", os.path.join(os.environ["HOME"], "work", name)))
    os.makedirs(d, exist_ok=True)
    return d

def ts(ago):
    return datetime.fromtimestamp(NOW - ago, timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.000Z")

def user(ago, text="привет"):
    return {"type": "user", "timestamp": ts(ago), "cwd": "/x", "message": {"role": "user", "content": text}}

def reply(ago, mid, model="claude-opus-5-5", inp=10, out=100, cr=1000, cc=50, blocks=1):
    u = {"input_tokens": inp, "output_tokens": out, "cache_read_input_tokens": cr,
         "cache_creation_input_tokens": cc}
    return [{"type": "assistant", "timestamp": ts(ago), "requestId": "r" + mid,
             "message": {"id": mid, "model": model, "role": "assistant", "usage": u,
                         "content": [{"type": "text", "text": "ок"}]}} for _ in range(blocks)]

def write(path, rows, mode="w"):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, mode) as f:
        for r in rows:
            f.write(json.dumps(r, ensure_ascii=False) + "\\n")

PA = os.path.join(proj("alpha"), A + ".jsonl")
PB = os.path.join(proj("beta"), B + ".jsonl")
# A: 3 запроса, один из двух блоков (usage повторяется), промежутки 120 с и 60 с, потом перерыв 20 минут
write(PA, [user(3000), *reply(2880, "m1", blocks=2), *reply(2820, "m2"), user(1620), *reply(1600, "m3"),
           *reply(1590, "s", model="<synthetic>"), {"type": "custom-title", "customTitle": "Альфа"}])
# субагент A работает, пока основная переписка молчит
write(os.path.join(os.path.dirname(PA), A, "subagents", "agent-1.jsonl"),
      [*reply(2700, "a1", model="claude-haiku-5-5", out=7), *reply(2600, "a2", model="claude-haiku-5-5", out=7)])
# B: давняя сессия, 40 дней назад
write(PB, [user(40 * 86400), *reply(40 * 86400 - 30, "b1", out=5000)])
"""


def run(code):
    r, _ = in_sandbox(SETUP + textwrap.dedent(code))
    if r.returncode:
        raise AssertionError(r.stderr)
    return r.stdout


class Usage(unittest.TestCase):
    def test_tokens_dedup_and_active_time(self):
        out = run("""
            rows = {r["sid"][:8]: r for r in cu.collect(days=7)}
            a = rows["aaaaaaaa"]
            print(sorted(rows), a["models"]["claude-opus-5-5"], a["models"]["claude-haiku-5-5"])
            print(round(a["active"]), a["total"])
        """).splitlines()
        self.assertEqual(out[0], "['aaaaaaaa'] [30, 300, 3000, 150, 3] [20, 14, 2000, 100, 2]")
        # основная: 3000→2880→2820 (180 с), 1620→1600→1590 (30 с), между ними перерыв 20 минут;
        # субагент: 2700→2600 (100 с), пока основная молчит, тоже работа
        self.assertEqual(out[1], f"{180 + 30 + 100} {3480 + 2134}")

    def test_period_and_project(self):
        out = run("""
            print(sorted(r["sid"][:8] for r in cu.collect(days=0)))
            print(sorted(r["sid"][:8] for r in cu.collect(days=0, project="beta")))
            text, err = cu.report(days=7)
            print(err, "Альфа" in text, "alpha" in text, "beta" in text, "claude-haiku-5-5" in text, "<synthetic>" in text)
            print(cu.report(days=7, project="nope")[0])
        """).splitlines()
        self.assertEqual(out[0], "['aaaaaaaa', 'bbbbbbbb']")
        self.assertEqual(out[1], "['bbbbbbbb']")
        self.assertEqual(out[2], "False True True False True False")
        self.assertIn("ни одной сессии", out[3])

    def test_incremental_rewrite_and_drop(self):
        out = run("""
            cu.collect(days=7)
            write(PA, [*reply(10, "m4", out=1)], "a")
            a = [r for r in cu.collect(days=7) if r["sid"] == A][0]
            print(a["models"]["claude-opus-5-5"])
            # переписанный файл (slim): новый inode, считается заново, без двойного счёта
            os.rename(PA, PA + ".old")
            write(PA, [*reply(5, "m9", out=2)])
            os.remove(PA + ".old")
            a = [r for r in cu.collect(days=7) if r["sid"] == A][0]
            print(a["models"]["claude-opus-5-5"])
            import session_store
            s = session_store.by_id(B)[0]
            session_store.delete(s)
            print(os.path.exists(cu.cache_path(B)), os.path.exists(cu.cache_path(A)))
        """).splitlines()
        self.assertEqual(out, ["[40, 301, 4000, 200, 4]", "[10, 2, 1000, 50, 1]", "False True"])

    def test_cli_and_mcp(self):
        out = run("""
            import mcp_server
            print("usage_report" in mcp_server.READ_ONLY)
            text, err = mcp_server.HANDLERS["usage_report"]({"days": 0, "top": 1})
            print(err, text.count("всего "))
            print(cu.main(["--days", "x"]), cu.main(["--bogus"]), cu.main(["-d", "3", "-p", "alpha"]))
        """).splitlines()
        self.assertEqual(out[0], "True")
        self.assertEqual(out[1], "False 2")  # строка «Токены: всего …» и одна тяжёлая сессия
        self.assertEqual(out[-1], "2 2 0")


if __name__ == "__main__":
    unittest.main()
