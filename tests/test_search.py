import os
import sys
import textwrap
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from test_sessions import LIB, in_sandbox  # noqa: E402

sys.path.insert(0, LIB)
import stemmer  # noqa: E402

# синтетические сессии: пишет их сам тест, во временный HOME
SETUP = """
import json, os, sys
import claude_search as cs
PROJ = os.path.join(cs.PROJECTS, "-tmp-demo")
os.makedirs(PROJ, exist_ok=True)
A = "aaaaaaaa-0000-0000-0000-000000000000"
B = "bbbbbbbb-0000-0000-0000-000000000000"
C = "cccccccc-0000-0000-0000-000000000000"

def line(kind, text, ts="2026-11-01T10:00:00.000Z"):
    if kind == "user":
        return {"type": "user", "timestamp": ts, "cwd": "/tmp/demo", "message": {"role": "user", "content": text}}
    return {"type": "assistant", "timestamp": ts,
            "message": {"role": "assistant", "content": [{"type": "text", "text": text}]}}

def write(sid, rows, mode="w"):
    with open(os.path.join(PROJ, sid + ".jsonl"), mode) as f:
        for r in rows:
            f.write(json.dumps(r, ensure_ascii=False) + "\\n")

write(A, [line("user", "почему падают сессии после обновления прокси?"),
          line("assistant", "Прокси-соединения рвутся через минуту, сессии теряются. Прокси надо держать живым.")])
write(B, [line("user", "настрой подпись релизов"),
          line("assistant", "Подписи для RuStore лежат в keystore, signing настроен в fastlane.")])
write(C, [line("user", "соединение через прокси не нужно"),
          line("assistant", "Подписка на рассылку оформлена.")])
"""


def run(code, extra=""):
    r, _ = in_sandbox(SETUP + extra + textwrap.dedent(code))
    if r.returncode:
        raise AssertionError(r.stderr)
    return r.stdout


class Stemmer(unittest.TestCase):
    def same(self, *words):
        stems = {stemmer.stem(w) for w in words}
        self.assertEqual(len(stems), 1, dict(zip(words, map(stemmer.stem, words))))

    def test_russian_forms(self):
        self.same("сессия", "сессии", "сессию", "сессий", "сессиях", "сессиями")
        self.same("подпись", "подписи", "подписью")
        self.same("ключ", "ключей", "ключами")
        self.same("настройка", "настройки", "настройкой")
        self.same("делали", "делать")
        self.same("ещё", "еще")

    def test_english_forms(self):
        self.same("signing", "signed", "sign", "signs")
        self.same("keystore", "keystores")
        self.same("configure", "configuring", "configured")
        self.same("status", "statuses")

    def test_terms_split_on_hyphen_and_drop_stop_words(self):
        self.assertEqual(stemmer.terms("Как мы чинили прокси-соединения в session_search?"),
                         [stemmer.stem("чинили"), stemmer.stem("прокси"), stemmer.stem("соединения"),
                          "session", "search"])
        self.assertEqual(stemmer.terms("версия 1.5.0"), [stemmer.stem("версия"), "1", "5", "0"])

    def test_spans_point_at_the_original_words(self):
        text = "Прокси-Соединения, ок"
        self.assertEqual([text[a:b] for _, a, b in stemmer.spans(text)], ["Прокси", "Соединения", "ок"])


ERRORS = """
D = "dddddddd-0000-0000-0000-000000000000"
def result(text, is_error):
    return {"type": "user", "timestamp": "2026-11-01T10:00:00.000Z",
            "message": {"role": "user", "content": [{"type": "tool_result", "tool_use_id": "x", "content": text,
                                                     "is_error": is_error}]}}
long_error = "Exit code 1\\n" + "строка лога\\n" * 300 + "Execution failed: NoSuchMethodError в конце"
write(D, [{"type": "assistant", "timestamp": "2026-11-01T10:00:00.000Z", "message": {"role": "assistant",
           "content": [{"type": "tool_use", "name": "Bash", "input": {"command": "./gradlew build"}}]}},
          result(long_error, True),
          result("BUILD SUCCESSFUL обычный вывод квазар", False),
          result([{"type": "text", "text": "<tool_use_error>File has not been read yet. Read it first</tool_use_error>"}], True),
          result("Permission for this action was denied by the user квазар", True)])
"""


class Search(unittest.TestCase):
    def test_word_forms_and_hyphens(self):
        out = run("""
            for q in ("сессия", "прокси-соединение", "подписями", "signed", "fastl", "rustor"):
                text, err = cs.search(q)
                print(q, "|", " ".join(sorted(s[2:10] for s in text.splitlines() if s.startswith("■ "))))
        """)
        got = dict(l.split(" | ") for l in out.splitlines())
        self.assertEqual(got["сессия"], "aaaaaaaa")
        self.assertEqual(got["прокси-соединение"], "aaaaaaaa")  # в C те же слова, но не подряд
        self.assertEqual(got["подписями"], "bbbbbbbb")  # «подписка» в C — другое слово
        self.assertEqual(got["signed"], "bbbbbbbb")
        self.assertEqual(got["fastl"], "bbbbbbbb")
        self.assertEqual(got["rustor"], "bbbbbbbb")

    def test_ranking_and_stop_words(self):
        out = run("""
            text, _ = cs.search("как мы держали прокси")
            print([s[2:10] for s in text.splitlines() if s.startswith("■ ")])
            text, _ = cs.search("прокси")
            print([s[2:10] for s in text.splitlines() if s.startswith("■ ")])
            print(cs.search("как мы")[0])
        """).splitlines()
        self.assertEqual(out[0], "['aaaaaaaa']")
        self.assertEqual(out[1], "['aaaaaaaa', 'cccccccc']")  # в A прокси в двух сообщениях, в C в одном
        self.assertIn("служебные", out[2])

    def test_missing_word(self):
        out = run("""
            print(cs.search("квазар")[0])
        """)
        self.assertIn("ничего не нашёл", out)
        self.assertIn("квазар", out)

    def test_incremental_and_rewrite(self):
        out = run("""
            cs.search("прокси")
            t = json.load(open(cs.terms_path(A)))
            print(t["n"], len(t["lens"]))
            write(A, [line("user", "а квазар нашёлся?", "2026-11-02T10:00:00.000Z")], "a")
            print("квазар" in cs.search("квазары")[0], json.load(open(cs.terms_path(A)))["n"])
            # slim переписывает файл целиком: новый inode, индекс и основы считаются заново
            path = os.path.join(PROJ, A + ".jsonl")
            os.rename(path, path + ".old")
            write(A, [line("user", "только пульсар")])
            os.remove(path + ".old")
            print("aaaaaaaa" in cs.search("квазар")[0], "aaaaaaaa" in cs.search("пульсары")[0])
            print(json.load(open(cs.terms_path(A)))["n"])
            os.remove(path)
            cs.refresh()
            print(os.path.exists(cs.terms_path(A)), os.path.exists(cs.index_path(A)))
        """).split()
        self.assertEqual(out, ["2", "2", "True", "3", "False", "True", "1", "False", "False"])

    def test_tool_errors_are_indexed_and_output_is_not(self):
        out = run("""
            idx = cs.refresh()
            msgs = idx[D]["msgs"]
            print([m[0] for m in msgs])
            print(len(msgs[1][2]) < 1700, msgs[1][2].startswith("Exit code 1"), msgs[1][2].endswith("в конце"))
            print("dddddddd" in cs.search("NoSuchMethodError", roles=["error"])[0],
                  "dddddddd" in cs.search("NoSuchMethodError", roles=["user"])[0],
                  "dddddddd" in cs.search("квазар")[0])
        """, ERRORS).splitlines()
        self.assertEqual(out, ["['tool', 'error']", "True True True", "True False False"])

    def test_read_with_query(self):
        out = run("""
            text, err = cs.read("aaaa", query="сессия")
            print(err, "◀" in text, "Совпадения в сессии: #0, #1" in text)
        """)
        self.assertEqual(out.split(), ["False", "True", "True"])

    def test_drop_removes_terms(self):
        out = run("""
            cs.search("прокси")
            cs.drop(A)
            print(os.path.exists(cs.terms_path(A)), os.path.exists(cs.index_path(A)), os.path.exists(cs.terms_path(B)))
        """)
        self.assertEqual(out.split(), ["False", "False", "True"])


if __name__ == "__main__":
    unittest.main()
