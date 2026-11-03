"""MCP-сервер claudemeister: claudemeister mcp."""
import json
import os
import shutil
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from mnrhlib import CM, MNRH, tilde  # noqa: E402
from claude_proc import RestartError, current_sid  # noqa: E402
from claude_restart import restart, slim_current  # noqa: E402
import claude_mac  # noqa: E402
import claude_open  # noqa: E402
import claude_search  # noqa: E402
import gradle_deps  # noqa: E402
import claude_ask  # noqa: E402
import claude_parallel  # noqa: E402
import mnrh_todo  # noqa: E402
import release_notes  # noqa: E402
import window_shot  # noqa: E402

MCP_NAME = "claudemeister"


TOOL = {
    "name": "restart",
    "description": ("Перезапустить этот Claude Code: процесс закроется и через пару секунд откроется снова "
                    "с этой же сессией в той же вкладке терминала. Перед запуском выполняется claude update, "
                    "так что это же способ поставить обновление. Вызывай только когда пользователь сам просит "
                    "перезапуститься или обновиться; после вызова ничего больше не делай, процесс завершится."),
    "inputSchema": {
        "type": "object",
        "properties": {"update": {"type": "boolean", "description": "выполнить claude update перед запуском",
                                  "default": True}},
    },
}


FORGET_TOOL = {
    "name": "forget",
    "description": ("Забыть эту сессию: Claude Code закроется, текущая переписка удалится с диска без следа "
                    "(вместе с историей запросов), и в той же папке и вкладке откроется новый чистый Claude — "
                    "как /clear, только старое не сохраняется. Вызывай только когда пользователь сам просит забыть "
                    "или удалить эту сессию; после вызова ничего больше не делай."),
    "inputSchema": {"type": "object", "properties": {}},
}


FRESH_TOOL = {
    "name": "fresh",
    "description": ("Начать новую сессию: Claude Code закроется, и в той же папке и вкладке откроется новый Claude "
                    "с пустым контекстом и без названия. Текущая сессия остаётся на диске и в /resume, как после "
                    "/clear, но название в новую не переходит. Вызывай только когда пользователь сам просит начать "
                    "новую сессию; после вызова ничего больше не делай."),
    "inputSchema": {"type": "object", "properties": {}},
}


def version():
    root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.realpath(__file__)))))
    try:
        with open(os.path.join(root, "VERSION")) as f:
            return f.read().strip() or "0"
    except OSError:
        return "0"


def call_restart(tool, a):
    update = a.get("update", True)
    try:
        if tool == "forget":
            p = restart(update=False, delay=2.5, forget=True)
            return (f"Готово: через пару секунд Claude Code закроется, сессия {p['sid']} будет удалена, "
                    f"и в {tilde(p['cwd'])} откроется новая."), False
        if tool == "fresh":
            p = restart(update=False, delay=2.5, fresh=True)
            return (f"Готово: через пару секунд Claude Code закроется, и в {tilde(p['cwd'])} откроется новая сессия. "
                    f"Сессия {p['sid']} останется в /resume."), False
        p = restart(update=bool(update), delay=2.5)
        return (f"Перезапуск запущен: через пару секунд Claude Code закроется"
                f"{', обновится' if update else ''} и снова откроет сессию {p['sid']} в {tilde(p['cwd'])}."), False
    except RestartError as e:
        return f"Не получилось: {e}", True


SLIM_TOOL = {
    "name": "slim",
    "description": ("Сжать эту сессию: Claude Code закроется, из старой части сессии (до двух последних /compact) "
                    "уберутся скриншоты и длинные выводы инструментов, и сессия откроется снова в той же вкладке. "
                    "То, что получает модель, не меняется, оригинал хранится 14 дней. Если сжимать нечего, "
                    "ничего не закрывается. Вызывай только когда пользователь сам просит сжать сессию; если ответ "
                    "говорит, что Claude закроется, больше ничего не делай."),
    "inputSchema": {"type": "object", "properties": {
        "dry_run": {"type": "boolean", "description": "только посчитать, сколько освободится"}}},
}


def call_slim(a):
    try:
        text, _ = slim_current(dry=bool(a.get("dry_run")), delay=2.5)
        return text, False
    except RestartError as e:
        return f"Не получилось: {e}", True


HANDLERS = {
    "restart": lambda a: call_restart("restart", a),
    "forget": lambda a: call_restart("forget", a),
    "fresh": lambda a: call_restart("fresh", a),
    "slim": call_slim,
    "open_claude": lambda a: claude_open.open_claude(a, CM),
    "session_search": lambda a: claude_search.handle("session_search", a, current_sid()),
    "session_read": lambda a: claude_search.handle("session_read", a),
    # без полного mnrh этих инструментов нет и в tools/list
    **({"mac_status": lambda a: claude_mac.handle("mac_status", a, MNRH),
        "free_memory": lambda a: claude_mac.handle("free_memory", a, MNRH)} if MNRH else {}),
    "deps_outdated": lambda a: gradle_deps.handle(a),
    "todo_list": lambda a: mnrh_todo.handle("todo_list", a, os.getcwd()),
    "todo_add": lambda a: mnrh_todo.handle("todo_add", a, os.getcwd()),
    "todo_done": lambda a: mnrh_todo.handle("todo_done", a, os.getcwd()),
    "release_notes_context": lambda a: release_notes.handle("release_notes_context", a, os.getcwd()),
    "release_notes_write": lambda a: release_notes.handle("release_notes_write", a, os.getcwd()),
    "parallel_task": lambda a: claude_parallel.handle("parallel_task", a, os.getcwd(), CM),
    "parallel_list": lambda a: claude_parallel.handle("parallel_list", a, os.getcwd()),
    "parallel_finish": lambda a: claude_parallel.handle("parallel_finish", a, os.getcwd()),
    "window_screenshot": lambda a: window_shot.handle(a),
    "ask_project": lambda a: claude_ask.ask(a.get("project"), a.get("question"), a.get("model")),
}
# mac_status и free_memory запускают команды mnrh, поэтому они есть, только когда стоит полный mnrh
EXTRA_TOOLS = (claude_search.TOOLS + (claude_mac.TOOLS if MNRH else []) + [gradle_deps.TOOL] + mnrh_todo.TOOLS + release_notes.TOOLS
               + claude_parallel.TOOLS + [window_shot.TOOL, claude_ask.TOOL])
READ_ONLY = [t["name"] for t in EXTRA_TOOLS if (t.get("annotations") or {}).get("readOnlyHint")] + \
    ["todo_add", "todo_done"]


def tools():
    return [TOOL, FORGET_TOOL, FRESH_TOOL, SLIM_TOOL, claude_open.TOOL] + EXTRA_TOOLS


LIB = os.path.dirname(os.path.abspath(__file__))
PENDING = "MNRH_MCP_PENDING"
TOOL_NAMES = "MNRH_MCP_TOOLS"


def code_stamp():
    """Отпечаток кода: меняется после brew upgrade или git pull, тогда сервер перезапускает себя."""
    try:
        files = [os.path.join(LIB, n) for n in os.listdir(LIB) if n.endswith(".py")]
        files.append(os.path.join(os.path.dirname(os.path.dirname(LIB)), "..", "VERSION"))
        return len(files), max(os.path.getmtime(f) for f in files)
    except (OSError, ValueError):
        return None


HOME_DIR = os.path.realpath(os.path.join(LIB, "..", "..", ".."))
OWN_BIN = os.path.join(HOME_DIR, "bin", "claudemeister")


def brew_bin():
    """claudemeister из Homebrew в PATH, если этот сервер запущен из Cellar; иначе None."""
    if "/Cellar/claudemeister/" not in HOME_DIR + "/":
        return None
    return shutil.which("claudemeister")


def installed_elsewhere():
    """brew upgrade ставит новую версию в другую папку Cellar: claudemeister в PATH уже не наш."""
    found = brew_bin()
    return bool(found) and os.path.dirname(os.path.dirname(os.path.realpath(found))) != HOME_DIR


def reload(pending):
    """Заменить процесс свежим claudemeister mcp на тех же stdin и stdout: Claude Code этого не заметит.
    Непрочитанные запросы уходят новому процессу через файл."""
    import tempfile
    fd, path = tempfile.mkstemp(prefix="cm-mcp-")
    with os.fdopen(fd, "wb") as f:
        f.write(pending)
    os.environ[PENDING] = path
    os.environ[TOOL_NAMES] = ",".join(sorted(t["name"] for t in tools()))
    target = brew_bin() or OWN_BIN
    sys.stdout.flush()
    os.execv(target, [target, "mcp"])


def lines_from(fd, carry=b""):
    """Строки stdin без буфера Python: при перезапуске нельзя потерять уже прочитанное."""
    buf = carry
    while True:
        while b"\n" in buf:
            line, buf = buf.split(b"\n", 1)
            yield line, buf
        chunk = os.read(fd, 65536)
        if not chunk:
            if buf.strip():
                yield buf, b""
            return
        buf += chunk


def mcp():
    def send(msg):
        sys.stdout.write(json.dumps(msg, ensure_ascii=False) + "\n")
        sys.stdout.flush()

    carry = b""
    path = os.environ.pop(PENDING, "")
    if path:
        try:
            with open(path, "rb") as f:
                carry = f.read()
            os.remove(path)
        except OSError:
            pass
    before = os.environ.pop(TOOL_NAMES, None)
    if before is not None and before != ",".join(sorted(t["name"] for t in tools())):
        send({"jsonrpc": "2.0", "method": "notifications/tools/list_changed"})
    stamp = code_stamp()

    for raw, rest in lines_from(sys.stdin.fileno(), carry):
        now = code_stamp()
        if now != stamp or now is None or installed_elsewhere():
            reload(raw + b"\n" + rest)
        try:
            req = json.loads(raw.decode("utf-8", "replace"))
        except ValueError:
            continue
        if not isinstance(req, dict):  # пакеты JSON-RPC (массивы) MCP не использует, а падать из-за них нельзя
            continue
        rid, method = req.get("id"), req.get("method")
        if rid is None:
            continue
        if method == "initialize":
            ver = (req.get("params") or {}).get("protocolVersion") or "2025-06-18"
            send({"jsonrpc": "2.0", "id": rid, "result": {
                "protocolVersion": ver,
                "capabilities": {"tools": {"listChanged": True}},
                "serverInfo": {"name": MCP_NAME, "version": version()}}})
        elif method == "tools/list":
            send({"jsonrpc": "2.0", "id": rid, "result": {"tools": tools()}})
        elif method == "tools/call":
            params = req.get("params") or {}
            handler = HANDLERS.get(params.get("name"))
            if not handler:
                send({"jsonrpc": "2.0", "id": rid, "error": {"code": -32602, "message": "нет такого инструмента"}})
                continue
            args = params.get("arguments")
            try:
                result = handler(args if isinstance(args, dict) else {})
            except Exception as e:
                result = (f"Не получилось: {e!r}", True)
            if isinstance(result, dict):
                send({"jsonrpc": "2.0", "id": rid, "result": result})
                continue
            text, err = result
            send({"jsonrpc": "2.0", "id": rid, "result": {"content": [{"type": "text", "text": text}], "isError": err}})
        elif method == "ping":
            send({"jsonrpc": "2.0", "id": rid, "result": {}})
        else:
            send({"jsonrpc": "2.0", "id": rid, "error": {"code": -32601, "message": f"не умею {method}"}})
