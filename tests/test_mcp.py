import json
import os
import shutil
import subprocess
import tempfile
import time
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def request(rid, method, **params):
    return json.dumps({"jsonrpc": "2.0", "id": rid, "method": method, "params": params}) + "\n"


class Reload(unittest.TestCase):
    def setUp(self):
        # копия claudemeister: тест меняет время файлов, рабочую папку не трогаем
        self.copy = tempfile.mkdtemp(prefix="cm-mcp-test-")
        for name in ("bin", "libexec", "share", "VERSION"):
            src = os.path.join(ROOT, name)
            (shutil.copytree if os.path.isdir(src) else shutil.copy2)(src, os.path.join(self.copy, name))
        env = dict(os.environ, PYTHONDONTWRITEBYTECODE="1")
        self.proc = subprocess.Popen([os.path.join(self.copy, "bin", "claudemeister"), "mcp"], stdin=subprocess.PIPE,
                                     stdout=subprocess.PIPE, env=env)

    def tearDown(self):
        self.proc.kill()
        self.proc.wait()
        shutil.rmtree(self.copy, True)

    def send(self, text):
        self.proc.stdin.write(text.encode())
        self.proc.stdin.flush()

    def read(self):
        return json.loads(self.proc.stdout.readline())

    def test_code_change_reloads_without_losing_requests(self):
        self.send(request(1, "initialize"))
        self.assertTrue(self.read()["result"]["capabilities"]["tools"]["listChanged"])
        pid = self.proc.pid
        lib = os.path.join(self.copy, "libexec", "claudemeister", "lib", "mnrhlib.py")
        later = time.time() + 5
        os.utime(lib, (later, later))
        # два запроса одним куском: оба должны дойти до нового процесса
        self.send(request(2, "ping") + request(3, "tools/list"))
        first, second = self.read(), self.read()
        self.assertEqual((first["id"], second["id"]), (2, 3))
        self.assertTrue(second["result"]["tools"])
        self.assertEqual(self.proc.pid, pid)
        self.assertIsNone(self.proc.poll())


if __name__ == "__main__":
    unittest.main()
