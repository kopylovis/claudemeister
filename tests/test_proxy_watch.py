import os
import sys
import unittest

sys.dont_write_bytecode = True
sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                                "libexec", "claudemeister", "lib"))
import claude_proxy as p  # noqa: E402

SERVER = "kz-server"


class WatchStep(unittest.TestCase):
    def test_healthy_does_nothing(self):
        self.assertEqual(p.watch_step({}, 100, "en0", "en0", SERVER), [])

    def test_no_network_waits(self):
        state = {"reject_since": 50}
        self.assertEqual(p.watch_step(state, 100, None, "en0", None), [])
        self.assertIsNone(state["reject_since"])

    def test_interface_change_repins(self):
        self.assertEqual(p.watch_step({}, 100, "en7", "en0", SERVER), ["repin", "healthcheck"])
        # VPN забрал маршрут по умолчанию: интерфейс из конфига надо убрать
        self.assertEqual(p.watch_step({}, 100, "utun4", "en0", SERVER), ["repin", "healthcheck"])
        self.assertEqual(p.watch_step({}, 100, "utun4", None, SERVER), [])

    def test_reject_checks_then_restarts_once(self):
        state = {}
        self.assertEqual(p.watch_step(state, 100, "en0", "en0", "REJECT"), ["healthcheck"])
        self.assertEqual(p.watch_step(state, 110, "en0", "en0", "REJECT"), [])
        self.assertEqual(p.watch_step(state, 100 + p.RESTART_AFTER, "en0", "en0", "REJECT"), ["restart"])
        # сервер и правда лежит: следующий перезапуск не раньше чем через RESTART_GAP
        t = 100 + p.RESTART_AFTER
        self.assertEqual(p.watch_step(state, t + 10, "en0", "en0", "REJECT"), ["healthcheck"])
        self.assertEqual(p.watch_step(state, t + 10 + p.RESTART_AFTER, "en0", "en0", "REJECT"), [])
        self.assertEqual(p.watch_step(state, t + p.RESTART_GAP + 20, "en0", "en0", "REJECT"), ["restart"])

    def test_recovery_resets(self):
        state = {}
        p.watch_step(state, 100, "en0", "en0", "REJECT")
        self.assertEqual(p.watch_step(state, 110, "en0", "en0", SERVER), [])
        self.assertIsNone(state["reject_since"])


if __name__ == "__main__":
    unittest.main()
