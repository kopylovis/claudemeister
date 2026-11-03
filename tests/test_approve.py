import io
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
import telegram  # noqa: E402
import telegram_approve as ta  # noqa: E402
from mnrhlib import HOOK_TIMEOUTS, HOOKS  # noqa: E402

SID = "11111111-2222-3333-4444-555555555555"
CHAT = "4242"


def payload(**over):
    p = {"session_id": SID, "hook_event_name": "PermissionRequest", "cwd": "/Users/x/Developer/app",
         "tool_name": "Bash", "tool_input": {"command": "rm -rf build", "description": "Remove build"}}
    p.update(over)
    return p


class FakeTelegram:
    """Подмена telegram.call: запоминает вызовы, на getUpdates отдает заранее заданные пачки."""

    def __init__(self, batches=None, user=CHAT, chat=CHAT, answer="y"):
        self.calls = []
        self.batches = list(batches or [])
        self.user, self.chat, self.answer = user, chat, answer
        self.sent = None

    def __call__(self, method, tok=None, wait=15, **params):
        self.calls.append((method, params))
        if method == "sendMessage":
            self.sent = params
            return {"message_id": 77}
        if method == "getUpdates":
            if "offset" in params:
                return []
            if self.batches:
                return self.batches.pop(0)(self)
            return []
        return True

    def press(self, data=None, message_id=77, update_id=500):
        rid = json.loads(self.sent["reply_markup"])["inline_keyboard"][0][0]["callback_data"].split(":")[1]
        return [{"update_id": update_id, "callback_query": {
            "id": "cb1", "data": data or f"cm1:{rid}:{self.answer}", "from": {"id": int(self.user)},
            "message": {"message_id": message_id, "chat": {"id": int(self.chat)}}}}]

    def methods(self):
        return [m for m, _ in self.calls]


class Base(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp()
        patches = [mock.patch.object(ta, "DIR", self.dir),
                   mock.patch.object(ta, "enabled", lambda: True),
                   mock.patch.object(ta, "session_title", lambda p: "Сессия"),
                   mock.patch.object(telegram, "chat", lambda: CHAT),
                   mock.patch.object(telegram, "token", lambda: "t"),
                   mock.patch.object(telegram, "away", lambda: True),
                   mock.patch.object(ta.time, "sleep", lambda s: None)]
        for p in patches:
            p.start()
            self.addCleanup(p.stop)
        self.addCleanup(shutil.rmtree, self.dir, True)

    def run_hook(self, p, fake):
        out = io.StringIO()
        with mock.patch.object(telegram, "call", fake):
            self.assertEqual(ta.hook(io.StringIO(json.dumps(p)), out), 0)
        return out.getvalue()


class Hook(Base):
    def test_allow_from_telegram(self):
        fake = FakeTelegram([lambda f: f.press()])
        out = json.loads(self.run_hook(payload(), fake))
        self.assertEqual(out["hookSpecificOutput"], {"hookEventName": "PermissionRequest",
                                                     "decision": {"behavior": "allow"}})
        self.assertIn("rm -rf build", fake.sent["text"])
        self.assertIn("app: нужно разрешение", fake.sent["text"])
        self.assertIn("answerCallbackQuery", fake.methods())
        # прочитанное подтверждено, кнопки убраны, файлы ожидания стерты
        self.assertIn(("getUpdates", {"offset": 501, "timeout": 0, "allowed_updates": '["callback_query"]'}),
                      fake.calls)
        edit = [p for m, p in fake.calls if m == "editMessageText"][0]
        self.assertIn("Разрешено", edit["text"])
        self.assertNotIn("reply_markup", edit)
        self.assertEqual(os.listdir(self.dir), ["poll.lock"])

    def test_deny(self):
        fake = FakeTelegram([lambda f: f.press()], answer="n")
        out = json.loads(self.run_hook(payload(), fake))
        self.assertEqual(out["hookSpecificOutput"]["decision"]["behavior"], "deny")
        self.assertTrue(out["hookSpecificOutput"]["decision"]["message"])

    def test_press_from_another_user_is_ignored(self):
        fake = FakeTelegram([lambda f: f.press()], user="999")
        with mock.patch.object(ta, "WAIT", 1.5):
            out = self.run_hook(payload(), fake)
        self.assertEqual(out, "")
        self.assertIn("Время вышло", [p for m, p in fake.calls if m == "editMessageText"][0]["text"])

    def test_press_in_another_chat_or_message_is_ignored(self):
        for fake in (FakeTelegram([lambda f: f.press()], chat="-100"),
                     FakeTelegram([lambda f: f.press(message_id=12)]),
                     FakeTelegram([lambda f: f.press(data="cm1:0123456789abcdef:y")])):
            with mock.patch.object(ta, "WAIT", 1.5):
                self.assertEqual(self.run_hook(payload(), fake), "")

    def test_user_back_at_mac(self):
        state = iter([True, True, False])
        fake = FakeTelegram()
        with mock.patch.object(telegram, "away", lambda: next(state)):
            self.assertEqual(self.run_hook(payload(), fake), "")
        self.assertIn("вернулся", [p for m, p in fake.calls if m == "editMessageText"][0]["text"])

    def test_network_error_keeps_waiting(self):
        def broken(f):
            raise telegram.TelegramError("нет связи")
        fake = FakeTelegram([broken, lambda f: f.press()])
        with mock.patch.object(ta, "log_error"):
            out = json.loads(self.run_hook(payload(), fake))
        self.assertEqual(out["hookSpecificOutput"]["decision"]["behavior"], "allow")

    def test_send_failure_falls_back(self):
        def fail(method, **kw):
            raise telegram.TelegramError("нет связи")
        with mock.patch.object(ta, "log_error"):
            self.assertEqual(self.run_hook(payload(), fail), "")


class Skips(Base):
    def assert_silent(self, p):
        fake = FakeTelegram()
        self.assertEqual(self.run_hook(p, fake), "")
        self.assertEqual(fake.calls, [])

    def test_off(self):
        with mock.patch.object(ta, "enabled", lambda: False):
            self.assert_silent(payload())

    def test_at_the_mac(self):
        with mock.patch.object(telegram, "away", lambda: False):
            self.assert_silent(payload())

    def test_no_bot(self):
        with mock.patch.object(telegram, "chat", lambda: ""):
            self.assert_silent(payload())

    def test_questions_and_plans(self):
        for tool in ("AskUserQuestion", "ExitPlanMode"):
            self.assert_silent(payload(tool_name=tool))

    def test_child_and_junk(self):
        with mock.patch.dict(os.environ, {"MNRH_CHILD": "1"}):
            self.assert_silent(payload())
        self.assert_silent(payload(session_id="x"))
        self.assert_silent(payload(hook_event_name="PreToolUse"))
        out = io.StringIO()
        self.assertEqual(ta.hook(io.StringIO("junk"), out), 0)
        self.assertEqual(out.getvalue(), "")


class Render(unittest.TestCase):
    def test_secrets_hidden(self):
        body, _, _ = ta.render("Bash", {"command": "curl -H 'Authorization: Bearer abcdef123' "
                                                  "https://u:hunter22@x.io?token=s3cr3tvalue"})
        for secret in ("abcdef123", "hunter22", "s3cr3tvalue"):
            self.assertNotIn(secret, body)

    def test_long_command_marked(self):
        body, _, cut = ta.render("Bash", {"command": "echo " + "x" * 2000})
        self.assertTrue(cut)
        self.assertLessEqual(len(body), ta.SHOW + 1)
        self.assertIn("не целиком", ta.message(payload(tool_input={"command": "x" * 2000})))

    def test_paths_and_html(self):
        self.assertEqual(ta.render("Edit", {"file_path": os.path.expanduser("~/a.txt")})[0], "~/a.txt")
        text = ta.message(payload(tool_input={"command": "echo '<b>' && ls\x1b[2J"}))
        self.assertIn("&lt;b&gt;", text)
        self.assertNotIn("\x1b", text)


class Guard(unittest.TestCase):
    def test_guard_reason_shown(self):
        text = ta.message(payload(tool_name="Read", tool_input={"file_path": "/p/.env"}, cwd="/p"))
        self.assertIn("claudemeister guard", text)
        self.assertNotIn("claudemeister guard", ta.message(payload()))


class Wiring(unittest.TestCase):
    def test_hook_row_and_timeout(self):
        self.assertIn(("PermissionRequest", "approve-hook", False, None), HOOKS)
        self.assertGreater(HOOK_TIMEOUTS["approve-hook"], ta.WAIT + ta.POLL + 15)
        import claude_settings
        entry = claude_settings.entry("approve-hook", False, None)
        self.assertEqual(entry["hooks"][0]["timeout"], HOOK_TIMEOUTS["approve-hook"])
        self.assertEqual(claude_settings.entry("notify-hook", True, None)["hooks"][0]["timeout"], 10)

    def test_notify_skips_duplicate_telegram(self):
        d = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, d, True)
        with mock.patch.object(ta, "DIR", d):
            prompt = {"notification_type": "permission_prompt"}
            self.assertFalse(claude_notify.approval_pending(prompt, SID))
            with open(os.path.join(d, "abc.pending"), "w") as f:
                json.dump({"sid": SID, "message_id": 1, "at": ta.time.time()}, f)
            self.assertTrue(claude_notify.approval_pending(prompt, SID))
            self.assertFalse(claude_notify.approval_pending({"notification_type": "idle_prompt"}, SID))
            self.assertFalse(claude_notify.approval_pending(prompt, "other"))


if __name__ == "__main__":
    unittest.main()
