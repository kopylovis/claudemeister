"""Длинный вывод сборок и тестов в контексте Claude: хук PostToolUse для Bash.

Если команда — Gradle, xcodebuild, swift build/test, npm/pnpm/yarn test или build, pytest или make, а вывод длинный,
Claude видит вместо него выжимку: упавшие задачи и тесты, ошибки и предупреждения со строками вокруг, итоговые строки
и путь к полному логу в ~/.cache/mnrh/logs/<сессия>/. Подменяет вывод поле updatedToolOutput хука PostToolUse.
PostToolUse приходит только после вызова с кодом 0. При ненулевом коде Claude Code шлет PostToolUseFailure, а там
заменить вывод нельзя, поэтому упавшая сборка без | tee или ; echo доходит до Claude как есть (Claude Code сам режет
вывод длиннее 30 000 символов).
"""
import json
import os
import re
import shutil
import subprocess
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from mnrhlib import CACHE, CLAUDE_HOME, read_config, tilde, write_config  # noqa: E402
from claude_guard import positional, split_segments, strip_env, words  # noqa: E402

LOGS = os.path.join(CACHE, "logs")
MAX_LINES = 200
MAX_BYTES = 20 * 1024
KEEP_DAYS = 7
READ_LIMIT = 64 << 20
# сколько строк брать: вокруг ошибки, в конце вывода; сколько ошибок и предупреждений; длина строки и выжимки
BEFORE, AFTER, TAIL = 2, 3, 12
MAX_ERRORS, MAX_WARNINGS, MAX_SUMMARY = 40, 25, 30
LINE_CHARS, BUDGET = 400, 14000

ANSI = re.compile(r"\x1b\[[0-9;?]*[A-Za-z]|\x1b\][^\x07]*\x07|\r")
ERROR = re.compile(r"""(?x)
    ^e:\s | :\d+(?::\d+)?:\s*(?:fatal\s)?error: | ^\s*(?:fatal\s)?error(?:\[\w+\])?: | \berror\s(?:TS|CS)\d+
    | ^ERROR\b | ^E\s{2,} | \bFAILED\b | ^\s*FAIL\s | ^\s*●\s | Test\sCase\s.*\sfailed | ^\s*✘ | \bTraceback\b
    | ^\s*(?:\w+\.)*\w*(?:Error|Exception):\s | make(?:\[\d+\])?:\s\*\*\* | npm\sERR! | ^\s*panic: | Execution\sfailed
    | Compilation\s(?:error|failed) | Testing\sfailed | Assertion(?:Error|\sfailed) | XCTAssert\w*\sfailed""")
LOCATION = re.compile(r"^(?:w:\s+)?(?:file://)?\S+?:\d+(?::\d+)?:?\s*")
WARNING = re.compile(r"^w:\s|:\d+(?::\d+)?:\s*warning:|^\s*warning:|^WARNING\b|^\s*WARN\b|npm WARN|^\[WARN")
SUMMARY = re.compile(r"""(?x)
    BUILD\s(?:SUCCESSFUL|FAILED) | \*\*\s[A-Z\s]+\s(?:SUCCEEDED|FAILED|INTERRUPTED)\s\*\* | ^\d+\sactionable\stasks
    | \btests?\scompleted\b | ^FAILURE: | Executed\s\d+\stests? | Test\sSuite\s'All\stests' | Test\srun\swith
    | ^\s*Tests?:\s | ^\s*Test\s(?:Suites|Files):\s | ^=+\s.*\b(?:passed|failed|errors?|skipped|no\stests\sran)\b.*=+$
    | Build\scomplete! | ^\(\d+\sfailures?\)$ | ^Ran\s\d+\stests? | ^(?:OK|FAILED)\s*(?:\(|$) | ^\s*Duration\s
    | compiled\s(?:successfully|with) | ✓\sbuilt\sin | ^make(?:\[\d+\])?:\s.*Error\s\d+""")
# начало блока, сколько строк после него взять и где блок кончается
BLOCKS = [(re.compile(r"^\* What went wrong:"), 12, re.compile(r"^\* Try:")),
          (re.compile(r"The following build commands failed:"), 20, re.compile(r"^\s*$")),
          (re.compile(r"^Failing tests:"), 20, re.compile(r"^\s*$")),
          (re.compile(r"short test summary info"), 30, re.compile(r"^=+"))]
PACKAGE_MANAGERS = {"npm", "pnpm", "yarn", "bun"}
SCRIPT = re.compile(r"^(?:test|build)(?::[\w:.-]+)?$|^t$")


def enabled():
    return read_config().get("buildlog", "on") != "off"


def find_build(cmd):
    """(чем собирают, имя программы, ее аргументы) для первой сборки в команде; None, если сборки нет."""
    for seg in split_segments(cmd or ""):
        ws = strip_env(words(seg))
        if not ws:
            continue
        exe, rest = os.path.basename(ws[0]), ws[1:]
        kind = None
        if exe in ("gradlew", "gradle", "gradlew.bat"):
            kind = "gradle"
        elif exe == "xcodebuild" or (exe == "xcrun" and rest[:1] == ["xcodebuild"]):
            kind = "xcodebuild"
        elif exe == "swift" and rest[:1] in (["build"], ["test"]):
            kind = "swift"
        elif exe in PACKAGE_MANAGERS and any(SCRIPT.match(a) for a in positional(rest)[:3]):
            kind = "npm"
        elif exe in ("pytest", "py.test") or (re.match(r"^python[\d.]*$", exe) and "pytest" in rest[1:2]
                                              and rest[:1] == ["-m"]) or \
                (exe in ("uv", "poetry", "pipenv", "pdm", "hatch") and "pytest" in rest[:3]):
            kind = "pytest"
        elif exe in ("make", "gmake"):
            kind = "make"
        if kind:
            return kind, exe, rest
    return None


def build_kind(cmd):
    """Чем собирают в команде: gradle, xcodebuild, swift, npm, pytest, make; None, если ничем из этого."""
    found = find_build(cmd)
    return found[0] if found else None


def is_long(text):
    return text.count("\n") + 1 > MAX_LINES or len(text.encode("utf-8", "replace")) > MAX_BYTES


def full_output(resp):
    """Весь вывод вызова. Длинный Claude Code отдает в stdout первые 30 000 символов, а целиком кладет в файл
    persistedOutputPath (поле не описано в документации, поэтому без него берем stdout и stderr)."""
    path = resp.get("persistedOutputPath")
    if isinstance(path, str) and os.path.realpath(path).startswith(os.path.realpath(CLAUDE_HOME) + os.sep):
        try:
            if os.path.getsize(path) <= READ_LIMIT:
                with open(path, encoding="utf-8", errors="replace") as f:
                    return f.read()
        except OSError:
            pass
    out, err = str(resp.get("stdout") or ""), str(resp.get("stderr") or "")
    return out + ("\n" + err if out and err else err)


def clip(line):
    return line if len(line) <= LINE_CHARS else line[:LINE_CHARS - 1] + "…"


def warning_key(line):
    """Текст предупреждения без файла и строки: одно и то же предупреждение в разных местах считается одним."""
    return LOCATION.sub("", line.strip(), count=1)


def pick(lines):
    """(номера строк для выжимки, {номер: сколько еще таких же предупреждений}, [строки о том, что не вошло])."""
    keep, n = set(), len(lines)
    errors = summary = 0
    first, repeats, extra = {}, {}, 0
    for i, line in enumerate(lines):
        for start, size, stop in BLOCKS:
            if start.search(line):
                keep.add(i)
                for j in range(i + 1, min(n, i + 1 + size)):
                    keep.add(j)
                    if stop.search(lines[j]):
                        break
        if SUMMARY.search(line):
            if summary < MAX_SUMMARY:
                keep.add(i)
            summary += 1
        elif ERROR.search(line):
            if errors < MAX_ERRORS:
                keep.update(range(max(0, i - BEFORE), min(n, i + AFTER + 1)))
            errors += 1
        elif WARNING.search(line):
            key = warning_key(line)
            if key in first:
                repeats[first[key]] = repeats.get(first[key], 0) + 1
            elif len(first) < MAX_WARNINGS:
                first[key] = i
                keep.add(i)
            else:
                extra += 1
    keep.update(range(max(0, n - TAIL), n))
    dropped = ([f"… ещё строк с ошибками: {errors - MAX_ERRORS}, они в полном логе"] if errors > MAX_ERRORS else []) + \
        ([f"… ещё разных предупреждений: {extra}, они в полном логе"] if extra else [])
    return sorted(keep), {i: c for i, c in repeats.items() if i in keep}, dropped


def render(lines, idx, header, footer, repeats=None):
    out, prev = [header], -1
    for i in idx:
        if i > prev + 1:
            out.append(f"… пропущено строк: {i - prev - 1}")
        more = (repeats or {}).get(i)
        out.append(clip(lines[i]) + (f"  [и ещё {more} таких же в других местах]" if more else ""))
        prev = i
    if prev < len(lines) - 1:
        out.append(f"… пропущено строк: {len(lines) - 1 - prev}")
    out += footer
    return "\n".join(out)


def shrink(text, log_path):
    """Выжимка из длинного вывода или None, если она не короче вдвое."""
    lines = ANSI.sub("", text).rstrip("\n").split("\n")
    idx, repeats, footer = pick(lines)
    size = len(text.encode("utf-8", "replace"))
    header = (f"[claudemeister buildlog: вывод длинный ({len(lines)} строк, {max(1, size // 1024)} КБ), здесь выжимка: "
              f"ошибки, предупреждения, итог и конец. Полный лог: {tilde(log_path)}, ищи в нём grep или читай Read "
              f"с offset, если выжимки мало]")
    compact = render(lines, idx, header, footer, repeats)
    if len(compact) > BUDGET:
        tail = [i for i in idx if i >= len(lines) - TAIL]
        head, total = [], len(header) + sum(len(clip(lines[i])) + 1 for i in tail) + 200
        for i in idx:
            if i in tail:
                break
            total += len(clip(lines[i])) + 1
            if total > BUDGET:
                break
            head.append(i)
        compact = render(lines, head + tail, header, footer + ["(выжимка обрезана по размеру, остальное в полном логе)"],
                         repeats)
    return compact if len(compact) * 2 <= len(text) else None


def log_path(sid):
    name = re.sub(r"[^\w-]", "_", sid or "") or "none"
    stamp = time.strftime("%Y%m%d-%H%M%S") + f"-{int(time.time() * 1000) % 1000:03d}"
    return os.path.join(LOGS, name, f"{stamp}.log")


def prune(now=None):
    """Логи старше KEEP_DAYS дней и пустые папки сессий убрать."""
    now = now or time.time()
    try:
        sessions = os.listdir(LOGS)
    except OSError:
        return
    for s in sessions:
        d = os.path.join(LOGS, s)
        try:
            for f in os.listdir(d):
                p = os.path.join(d, f)
                if now - os.path.getmtime(p) > KEEP_DAYS * 86400:
                    os.remove(p)
            if not os.listdir(d):
                os.rmdir(d)
        except OSError:
            pass


def save(path, cmd, text):
    os.makedirs(os.path.dirname(path), mode=0o700, exist_ok=True)
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        f.write(f"$ {cmd}\n\n{text}")


def decide(payload):
    """updatedToolOutput для хука или None, если вывод оставить как есть."""
    if payload.get("tool_name") != "Bash" or payload.get("hook_event_name", "PostToolUse") != "PostToolUse":
        return None
    inp = payload.get("tool_input") if isinstance(payload.get("tool_input"), dict) else {}
    resp = payload.get("tool_response")
    cmd = str(inp.get("command") or "")
    if inp.get("run_in_background") or not isinstance(resp, dict) or resp.get("interrupted") or \
            resp.get("isImage") or resp.get("backgroundTaskId") or not build_kind(cmd):
        return None
    text = full_output(resp)
    if not is_long(text):
        return None
    path = log_path(payload.get("session_id"))
    compact = shrink(text, path)
    if compact is None:
        return None
    save(path, cmd, text)
    prune()
    out = {k: v for k, v in resp.items() if k not in ("persistedOutputPath", "persistedOutputSize")}
    out.update(stdout=compact, stderr="")
    return out


XCODE_ACTIONS = {"build", "test", "archive", "clean", "analyze", "build-for-testing", "test-without-building",
                 "docbuild", "installsrc", "install"}
TABS = os.path.join(CACHE, "tabs")
TAB_DELAY = 1.5
LABEL_CHARS = 48


def tab_label(cmd):
    """Подпись для вкладки: программа и цели сборки без ключей, например gradlew :app:assembleDebug."""
    found = find_build(cmd)
    if not found:
        return None
    kind, exe, rest = found
    if exe == "xcrun":
        exe, rest = "xcodebuild", rest[1:]
    args = [a for a in positional(rest) if "=" not in a and "/" not in a.rstrip("/")]
    if kind == "xcodebuild":
        args = [a for a in rest if a in XCODE_ACTIONS] or ["build"]
    label = " ".join([exe] + args[:3])
    return label if len(label) <= LABEL_CHARS else label[:LABEL_CHARS - 1] + "…"


def iterm_target(env=None):
    """Аргументы it2 для вкладки этой сессии: [путь к it2, ключи сессии]. None, если это не iTerm2 или it2 нет."""
    env = os.environ if env is None else env
    if env.get("LC_TERMINAL") != "iTerm2" and env.get("TERM_PROGRAM") != "iTerm.app":
        return None
    it2 = shutil.which("it2", path=env.get("PATH")) or next(
        (p for p in (os.path.expanduser("~/.config/iterm2/it2"), os.path.expanduser("~/.iterm2/bin/it2"),
                     "/Applications/iTerm.app/Contents/Resources/utilities/it2") if os.access(p, os.X_OK)), None)
    if not it2:
        return None
    if env.get("TMUX") and env.get("TMUX_PANE"):
        return [it2, "--tmux", env["TMUX"], "--tmux-pane", env["TMUX_PANE"], "--quiet-if-unresolved"]
    sid = env.get("ITERM_SESSION_ID", "").rpartition(":")[2]
    return [it2, "-s", sid] if sid else None


def set_detail(target, text):
    try:
        subprocess.run([target[0], "session", "set-status", *target[1:], "--detail", text],
                       capture_output=True, timeout=3)
    except (OSError, subprocess.TimeoutExpired):
        pass


def tab_marker(tool_id, kind):
    return os.path.join(TABS, re.sub(r"[^\w-]", "_", tool_id) + "." + kind)


def remove(path):
    """True, если файл был и его удалили. Из двух процессов удаляет ровно один."""
    try:
        os.remove(path)
        return True
    except FileNotFoundError:
        return False


def prune_tabs(now=None):
    now = now or time.time()
    for name in os.listdir(TABS):
        path = os.path.join(TABS, name)
        if now - os.path.getmtime(path) > 86400:
            remove(path)


def tab_hook(payload, target, sleep=time.sleep):
    """Подпись сборки во вкладке iTerm2. Хук iTerm2 стирает подпись на каждом событии, в том числе на
    PreToolUse этого же вызова, поэтому ставим ее через TAB_DELAY секунд. Пока PreToolUse ждет, в TABS лежит
    <id>.wait, после подписи <id>.shown. Конец вызова удаляет тот, что есть: если сборка кончилась раньше, подпись
    не ставим, а если пока ставили, убираем. Конец, пришедший раньше PreToolUse, оставляет <id>.done."""
    event = payload.get("hook_event_name")
    inp = payload.get("tool_input") if isinstance(payload.get("tool_input"), dict) else {}
    tool_id = str(payload.get("tool_use_id") or "")
    if payload.get("tool_name") != "Bash" or not tool_id or inp.get("run_in_background"):
        return
    label = tab_label(str(inp.get("command") or ""))
    if not label:
        return
    wait, shown, done = (tab_marker(tool_id, k) for k in ("wait", "shown", "done"))
    if event in ("PostToolUse", "PostToolUseFailure"):
        if not remove(wait) and not remove(shown) and os.path.isdir(TABS):
            open(done, "w").close()
            prune_tabs()
        return
    if event != "PreToolUse":
        return
    os.makedirs(TABS, mode=0o700, exist_ok=True)
    if remove(done):
        return
    open(wait, "w").close()
    prune_tabs()
    sleep(TAB_DELAY)
    if not os.path.exists(wait):
        return
    set_detail(target, label)
    try:
        os.rename(wait, shown)
    except FileNotFoundError:
        set_detail(target, "")


def hook():
    try:
        payload = json.load(sys.stdin)
    except ValueError:
        return 0
    if not isinstance(payload, dict) or not enabled():
        return 0
    target = iterm_target()
    if target:
        try:
            tab_hook(payload, target)
        except OSError:
            pass
    try:
        out = decide(payload)
    except Exception:
        return 0
    if out:
        print(json.dumps({"hookSpecificOutput": {"hookEventName": "PostToolUse", "updatedToolOutput": out}},
                         ensure_ascii=False))
    return 0


def usage():
    print("claudemeister buildlog                 включено ли и где логи")
    print("claudemeister buildlog on | off        включить или выключить")
    print("claudemeister buildlog test <файл>     что увидит Claude вместо такого вывода")
    print()
    print(f"Хук PostToolUse сжимает длинный вывод (больше {MAX_LINES} строк или {MAX_BYTES // 1024} КБ) Gradle, "
          "xcodebuild,")
    print("swift build/test, npm/pnpm/yarn test и build, pytest и make: Claude видит ошибки, предупреждения и итог,")
    print(f"а полный лог лежит в {tilde(LOGS)}/<сессия>/ ({KEEP_DAYS} дней). Только для команд с кодом 0:")
    print("вывод упавшей команды Claude Code заменить не дает.")
    print(f"В iTerm2 сборка дольше {TAB_DELAY:g} с подписана во вкладке рядом со статусом Claude: gradlew :app:assembleDebug.")


def main():
    args = sys.argv[1:]
    if args[:1] == ["buildlog-hook"]:
        return hook()
    rest = args[1:]
    if not rest:
        print(f"claudemeister buildlog: {'включён' if enabled() else 'выключен'}. Логи: {tilde(LOGS)}")
        return 0
    if rest[0] in ("on", "off") and len(rest) == 1:
        write_config(buildlog=rest[0])
        print(f"claudemeister buildlog {'включён' if rest[0] == 'on' else 'выключен'}")
        return 0
    if rest[0] == "test" and len(rest) == 2:
        try:
            with open(rest[1], encoding="utf-8", errors="replace") as f:
                text = f.read()
        except OSError as e:
            print(f"Не прочитал {rest[1]}: {e.strerror}", file=sys.stderr)
            return 1
        if not is_long(text):
            print(f"Вывод короткий (до {MAX_LINES} строк и {MAX_BYTES // 1024} КБ), Claude увидит его как есть")
            return 0
        print(shrink(text, os.path.join(LOGS, "<сессия>", "<время>.log")) or
              "Выжимка вышла не короче половины вывода, Claude увидит его как есть")
        return 0
    usage()
    return 0 if rest[0] in ("-h", "--help") else 2


if __name__ == "__main__":
    sys.exit(main())
