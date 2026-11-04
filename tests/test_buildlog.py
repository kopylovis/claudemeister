import json
import os
import shutil
import sys
import tempfile
import unittest

sys.dont_write_bytecode = True
TESTS = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(os.path.dirname(TESTS), "libexec", "claudemeister", "lib"))
import claude_buildlog as b  # noqa: E402

FIXTURES = os.path.join(TESTS, "fixtures")


def fixture(name):
    with open(os.path.join(FIXTURES, name), encoding="utf-8") as f:
        return f.read()


class Kind(unittest.TestCase):
    def test_build_commands(self):
        for cmd, kind in [("./gradlew assembleDebug", "gradle"), ("cd app && ./gradlew :core:test --continue", "gradle"),
                          ("gradle build", "gradle"), ("JAVA_HOME=/x ./gradlew test", "gradle"),
                          ("xcodebuild -scheme App test", "xcodebuild"),
                          ("set -o pipefail && xcodebuild build | xcbeautify", "xcodebuild"),
                          ("xcrun xcodebuild -list", "xcodebuild"), ("swift build", "swift"), ("swift test", "swift"),
                          ("npm test", "npm"), ("npm run build", "npm"), ("pnpm -r build", "npm"),
                          ("yarn test:unit", "npm"), ("pnpm --filter web build", "npm"), ("pytest -q", "pytest"),
                          ("python3 -m pytest tests", "pytest"), ("uv run pytest", "pytest"), ("make", "make"),
                          ("make test", "make"), ("time make -j8", "make")]:
            self.assertEqual(b.build_kind(cmd), kind, cmd)

    def test_other_commands(self):
        for cmd in ("ls -la", "git log", "npm install", "npm run lint", "swift --version", "cat build.gradle",
                    "echo ./gradlew build", "python3 script.py", "grep -r make .", "yarn add react", ""):
            self.assertIsNone(b.build_kind(cmd), cmd)


class Shrink(unittest.TestCase):
    def test_gradle_failure(self):
        text = fixture("gradle-failure.log")
        self.assertTrue(b.is_long(text))
        out = b.shrink(text, "/x/logs/s/1.log")
        self.assertLess(len(out) * 2, len(text))
        for want in ("Полный лог: /x/logs/s/1.log", "Unresolved reference 'rememberScrollState'",
                     "> Task :app:compileDebugKotlin FAILED", "ParserTest > parsesEmptyInput FAILED",
                     "expected:<0> but was:<1>", "42 tests completed, 1 failed",
                     "Execution failed for task ':core:testDebugUnitTest'.", "BUILD FAILED in 1m 12s",
                     "187 actionable tasks", "'kotlinOptions(KotlinJvmOptions.() -> Unit): Unit' is deprecated"):
            self.assertIn(want, out)
        # стек теста и задачи UP-TO-DATE не нужны
        self.assertNotIn("DirectMethodHandleAccessor.java:120", out)
        self.assertLess(out.count("UP-TO-DATE"), 5)

    def test_xcodebuild_build_failure(self):
        text = fixture("xcodebuild-build-failure.log")
        out = b.shrink(text, "/x/1.log")
        self.assertLess(len(out) * 10, len(text))
        for want in ("HomeView.swift:57:25: error: cannot find 'ItemRowView' in scope", "ItemRowView(item: item)",
                     "error: value of type 'Store' has no member 'reload'", "** BUILD FAILED **",
                     "The following build commands failed:", "SwiftEmitModule normal arm64", "(2 failures)",
                     "Item.swift:23:9: warning: variable 'count' was never mutated"):
            self.assertIn(want, out)
        # одинаковые предупреждения из разных строк — одной строкой со счетчиком
        self.assertEqual(out.count("'foregroundColor' is deprecated"), 1)
        self.assertIn("и ещё 29 таких же", out)
        # длинные строки вызова компилятора обрезаны
        self.assertTrue(all(len(line) <= b.LINE_CHARS + 60 for line in out.split("\n")[1:]))

    def test_xcodebuild_test_failure(self):
        out = b.shrink(fixture("xcodebuild-test-failure.log"), "/x/1.log")
        for want in ("XCTAssertEqual failed", "Test Case '-[TossyTests.SyncTests testSyncCase6]' failed",
                     "Executed 120 tests, with 1 failure", "Failing tests:", "SyncTests.testSyncCase6()",
                     "** TEST FAILED **"):
            self.assertIn(want, out)
        self.assertNotIn("testParseCase40]' passed", out)

    def test_short_or_unhelpful(self):
        self.assertFalse(b.is_long("BUILD SUCCESSFUL in 3s\n"))
        self.assertTrue(b.is_long("x\n" * 201))
        self.assertTrue(b.is_long("x" * (b.MAX_BYTES + 1)))
        # если почти все строки важны, выжимка не короче вдвое: оставить вывод как есть
        dense = "\n".join(f"e: file:///a.kt:{i}:1 Unresolved reference\n  x\n  y\n  z\n" for i in range(42))
        self.assertTrue(b.is_long(dense))
        self.assertIsNone(b.shrink(dense, "/x"))

    def test_error_cap_is_reported(self):
        text = "\n".join(f"e: file:///a.kt:{i}:1 Unresolved reference 'x{i}'\n" + "\n".join(["ok " * 12] * 8) for i in range(100))
        out = b.shrink(text, "/x")
        self.assertIn("x39'", out)
        self.assertNotIn("x41'", out)
        self.assertIn("ещё строк с ошибками: 60", out)

    def test_budget(self):
        text = "\n".join(f"src/f{i}.c:{i}:1: error: unknown type name 'foo{i}'" + ("\n" + "filler " * 80) * 20
                         for i in range(400))
        out = b.shrink(text, "/x")
        self.assertLessEqual(len(out), b.BUDGET + 2000)
        self.assertIn("выжимка обрезана", out)


class Hook(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.saved = b.LOGS, b.CLAUDE_HOME
        b.LOGS, b.CLAUDE_HOME = os.path.join(self.tmp, "logs"), os.path.join(self.tmp, "claude")

    def tearDown(self):
        b.LOGS, b.CLAUDE_HOME = self.saved
        shutil.rmtree(self.tmp)

    def payload(self, stdout, cmd="./gradlew build", **resp):
        return {"hook_event_name": "PostToolUse", "tool_name": "Bash", "session_id": "abc-1",
                "tool_input": {"command": cmd},
                "tool_response": dict({"stdout": stdout, "stderr": "", "interrupted": False, "isImage": False,
                                       "noOutputExpected": False}, **resp)}

    def test_replaces_output_and_saves_log(self):
        text = fixture("gradle-failure.log")
        out = b.decide(self.payload(text))
        self.assertEqual(set(out), {"stdout", "stderr", "interrupted", "isImage", "noOutputExpected"})
        self.assertEqual(out["stderr"], "")
        self.assertIn("BUILD FAILED in 1m 12s", out["stdout"])
        logs = os.listdir(os.path.join(b.LOGS, "abc-1"))
        self.assertEqual(len(logs), 1)
        path = os.path.join(b.LOGS, "abc-1", logs[0])
        self.assertIn(path.replace(b.HOME if hasattr(b, "HOME") else "", ""), out["stdout"].replace("~", ""))
        with open(path) as f:
            saved = f.read()
        self.assertTrue(saved.startswith("$ ./gradlew build\n\n"))
        self.assertTrue(saved.endswith(text))
        self.assertEqual(oct(os.stat(path).st_mode & 0o777), "0o600")

    def test_reads_persisted_output(self):
        # длинный вывод Claude Code отдает обрезанным, а целиком кладет в файл в своей папке
        text = fixture("xcodebuild-build-failure.log")
        persisted = os.path.join(b.CLAUDE_HOME, "projects", "p", "s", "tool-results", "x.txt")
        os.makedirs(os.path.dirname(persisted))
        with open(persisted, "w") as f:
            f.write(text)
        out = b.decide(self.payload(text[:30000], cmd="xcodebuild build", persistedOutputPath=persisted,
                                    persistedOutputSize=len(text)))
        self.assertNotIn("persistedOutputPath", out)
        self.assertIn("** BUILD FAILED **", out["stdout"])
        # путь вне папки Claude Code не читаем
        outside = os.path.join(self.tmp, "x.txt")
        shutil.copy(persisted, outside)
        out = b.decide(self.payload("short", cmd="xcodebuild build", persistedOutputPath=outside))
        self.assertIsNone(out)

    def test_leaves_alone(self):
        long_text = fixture("gradle-failure.log")
        self.assertIsNone(b.decide(self.payload(long_text, cmd="cat build.log")))
        self.assertIsNone(b.decide(self.payload("BUILD SUCCESSFUL in 2s")))
        self.assertIsNone(b.decide(self.payload(long_text, interrupted=True)))
        self.assertIsNone(b.decide(self.payload(long_text, backgroundTaskId="b1")))
        p = self.payload(long_text)
        p["tool_input"]["run_in_background"] = True
        self.assertIsNone(b.decide(p))
        p = self.payload(long_text)
        p["tool_name"] = "Read"
        self.assertIsNone(b.decide(p))
        self.assertFalse(os.path.exists(b.LOGS))

    def test_prune(self):
        old = os.path.join(b.LOGS, "s1", "old.log")
        os.makedirs(os.path.dirname(old))
        open(old, "w").close()
        os.utime(old, (1, 1))
        b.prune()
        self.assertFalse(os.path.exists(os.path.dirname(old)))

    def test_hook_output_shape(self):
        import io
        import contextlib
        saved_stdin, saved_enabled = sys.stdin, b.enabled
        sys.stdin, b.enabled = io.StringIO(json.dumps(self.payload(fixture("gradle-failure.log")))), lambda: True
        buf = io.StringIO()
        try:
            with contextlib.redirect_stdout(buf):
                b.hook()
        finally:
            sys.stdin, b.enabled = saved_stdin, saved_enabled
        out = json.loads(buf.getvalue())["hookSpecificOutput"]
        self.assertEqual(out["hookEventName"], "PostToolUse")
        self.assertIn("BUILD FAILED", out["updatedToolOutput"]["stdout"])


class TabLabel(unittest.TestCase):
    def test_labels(self):
        self.assertEqual(b.tab_label("cd app && ./gradlew :app:assembleDebug --no-daemon"), "gradlew :app:assembleDebug")
        self.assertEqual(b.tab_label("xcodebuild -scheme App -destination 'platform=iOS Simulator,name=iPhone 16' test"),
                         "xcodebuild test")
        self.assertEqual(b.tab_label("xcrun xcodebuild -workspace A.xcworkspace -scheme A"), "xcodebuild build")
        self.assertEqual(b.tab_label("npm run build"), "npm run build")
        self.assertEqual(b.tab_label("python3 -m pytest tests/test_x.py -q"), "python3 pytest")
        self.assertIsNone(b.tab_label("ls -la"))

    def test_long_label_is_clipped(self):
        label = b.tab_label("make " + " ".join(f"target{i}" * 3 for i in range(5)))
        self.assertLessEqual(len(label), b.LABEL_CHARS)
        self.assertTrue(label.endswith("…"))


class ItermTarget(unittest.TestCase):
    def setUp(self):
        self.bin = tempfile.mkdtemp(prefix="cm-it2-")
        it2 = os.path.join(self.bin, "it2")
        with open(it2, "w") as f:
            f.write("#!/bin/sh\n")
        os.chmod(it2, 0o755)
        self.it2 = it2

    def tearDown(self):
        shutil.rmtree(self.bin)

    def test_iterm_session(self):
        env = {"TERM_PROGRAM": "iTerm.app", "PATH": self.bin, "ITERM_SESSION_ID": "w0t1p0:ABC-123"}
        self.assertEqual(b.iterm_target(env), [self.it2, "-s", "ABC-123"])

    def test_tmux_pane(self):
        env = {"LC_TERMINAL": "iTerm2", "PATH": self.bin, "TMUX": "/tmp/tmux-501/default,1,0", "TMUX_PANE": "%3",
               "ITERM_SESSION_ID": "w0t1p0:OUTER"}
        self.assertEqual(b.iterm_target(env), [self.it2, "--tmux", "/tmp/tmux-501/default,1,0", "--tmux-pane", "%3",
                                               "--quiet-if-unresolved"])

    def test_other_terminal(self):
        self.assertIsNone(b.iterm_target({"TERM_PROGRAM": "Apple_Terminal", "PATH": self.bin}))


class TabHook(unittest.TestCase):
    def setUp(self):
        self.tabs = tempfile.mkdtemp(prefix="cm-tabs-")
        self.old, b.TABS = b.TABS, os.path.join(self.tabs, "tabs")
        self.calls = []
        self.old_set, b.set_detail = b.set_detail, lambda target, text: self.calls.append(text)

    def tearDown(self):
        b.TABS, b.set_detail = self.old, self.old_set
        shutil.rmtree(self.tabs)

    def event(self, name, command="./gradlew assembleDebug", tool_id="toolu_1"):
        return {"hook_event_name": name, "tool_name": "Bash", "tool_use_id": tool_id,
                "tool_input": {"command": command}}

    def test_long_build_sets_detail(self):
        b.tab_hook(self.event("PreToolUse"), ["it2"], sleep=lambda s: None)
        self.assertEqual(self.calls, ["gradlew assembleDebug"])

    def test_long_build_leaves_no_files(self):
        b.tab_hook(self.event("PreToolUse"), ["it2"], sleep=lambda s: None)
        b.tab_hook(self.event("PostToolUse"), ["it2"])
        self.assertEqual(self.calls, ["gradlew assembleDebug"])
        self.assertEqual(os.listdir(b.TABS), [])

    def test_build_finished_during_delay_sets_nothing(self):
        b.tab_hook(self.event("PreToolUse"), ["it2"],
                   sleep=lambda s: b.tab_hook(self.event("PostToolUseFailure"), ["it2"]))
        self.assertEqual(self.calls, [])
        self.assertEqual(os.listdir(b.TABS), [])

    def test_end_before_start_sets_nothing(self):
        os.makedirs(b.TABS)
        b.tab_hook(self.event("PostToolUse"), ["it2"])
        b.tab_hook(self.event("PreToolUse"), ["it2"], sleep=lambda s: None)
        self.assertEqual(self.calls, [])
        self.assertEqual(os.listdir(b.TABS), [])

    def test_no_pre_hook_no_files(self):
        b.tab_hook(self.event("PostToolUse"), ["it2"])
        self.assertFalse(os.path.exists(b.TABS))

    def test_build_finished_while_setting_clears(self):
        def finish(target, text):
            self.calls.append(text)
            if text:
                b.tab_hook(self.event("PostToolUse"), ["it2"])
        b.set_detail = finish
        b.tab_hook(self.event("PreToolUse"), ["it2"], sleep=lambda s: None)
        self.assertEqual(self.calls, ["gradlew assembleDebug", ""])

    def test_not_a_build(self):
        b.tab_hook(self.event("PreToolUse", command="git status"), ["it2"], sleep=lambda s: None)
        b.tab_hook(self.event("PostToolUse", command="git status"), ["it2"])
        self.assertEqual(self.calls, [])
        self.assertFalse(os.path.exists(b.TABS))

    def test_background_build_is_skipped(self):
        event = self.event("PreToolUse")
        event["tool_input"]["run_in_background"] = True
        b.tab_hook(event, ["it2"], sleep=lambda s: None)
        self.assertEqual(self.calls, [])


if __name__ == "__main__":
    unittest.main()
