import json
import os
import shutil
import sys
import tempfile
import unittest
from unittest import mock

sys.dont_write_bytecode = True
sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                                "libexec", "claudemeister", "lib"))
import claude_notify  # noqa: E402
import claude_settings  # noqa: E402


class Setting(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.dir, True)
        self.user = self.file("user.json")
        self.legacy = self.file("legacy.json")
        self.managed = self.file("managed.json")

    def file(self, name, data=None):
        path = os.path.join(self.dir, name)
        if data is not None:
            os.makedirs(os.path.dirname(path), exist_ok=True)
            with open(path, "w") as f:
                json.dump(data, f)
        return path

    def get(self, cwd=None):
        return claude_settings.setting("inputNeededNotifEnabled", cwd, self.user, self.legacy, self.managed)

    def test_missing_everywhere(self):
        self.assertIsNone(self.get())

    def test_order(self):
        self.file("legacy.json", {"inputNeededNotifEnabled": True})
        self.assertIs(self.get(), True)
        self.file("user.json", {"inputNeededNotifEnabled": False})
        self.assertIs(self.get(), False)
        proj = os.path.join(self.dir, "proj")
        self.file("proj/.claude/settings.local.json", {"inputNeededNotifEnabled": True})
        self.assertIs(self.get(proj), True)
        self.file("managed.json", {"inputNeededNotifEnabled": False})
        self.assertIs(self.get(proj), False)

    def test_broken_file_skipped(self):
        with open(self.user, "w") as f:
            f.write("{nope")
        self.file("legacy.json", {"inputNeededNotifEnabled": True})
        self.assertIs(self.get(), True)


class NativePush(unittest.TestCase):
    def native(self, env, enabled, **payload):
        p = {"hook_event_name": "Notification", "notification_type": "permission_prompt", "cwd": "/p"}
        p.update(payload)
        with mock.patch.dict(os.environ, env, clear=False), \
                mock.patch.object(claude_settings, "setting", lambda key, cwd=None: enabled):
            if "CLAUDE_CODE_BRIDGE_SESSION_ID" not in env:
                os.environ.pop("CLAUDE_CODE_BRIDGE_SESSION_ID", None)
            return claude_notify.native_push(p)

    def test_remote_control_with_push_on(self):
        rc = {"CLAUDE_CODE_BRIDGE_SESSION_ID": "session_abc"}
        self.assertTrue(self.native(rc, True))
        self.assertTrue(self.native(rc, True, notification_type="elicitation_dialog"))

    def test_our_push_stays(self):
        rc = {"CLAUDE_CODE_BRIDGE_SESSION_ID": "session_abc"}
        self.assertFalse(self.native({}, True))
        self.assertFalse(self.native(rc, False))
        self.assertFalse(self.native(rc, None))
        # «Готово» и долгое ожидание Claude присылает, только если сам решит: их не отключаем
        self.assertFalse(self.native(rc, True, notification_type="idle_prompt"))
        self.assertFalse(self.native(rc, True, hook_event_name="Stop"))


if __name__ == "__main__":
    unittest.main()
