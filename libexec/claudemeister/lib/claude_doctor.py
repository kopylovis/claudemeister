"""claudemeister doctor: одной командой проверить, что все части claudemeister на месте и работают."""
import json
import os
import shutil
import subprocess
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from mnrhlib import BAD, CM, OK, ROOT, WARN, promptsill_path, read_config, tilde  # noqa: E402
from claude_proc import CLAUDE_BIN  # noqa: E402
import claude_settings  # noqa: E402

USAGE = """claudemeister doctor    проверить установку: claude, команды, хуки, MCP-сервер, хук zsh, уведомления,
                       promptsill, прокси, хранение переписок. Код выхода 1, если что-то сломано"""


def run(cmd, timeout=15, stdin=None):
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout, input=stdin)
        return r.returncode, (r.stdout or "") + (r.stderr or "")
    except (OSError, subprocess.TimeoutExpired) as e:
        return 1, str(e)


def version():
    try:
        with open(os.path.join(ROOT, "VERSION")) as f:
            return f.read().strip()
    except OSError:
        return "?"


def check_install():
    where = "Homebrew" if "/Cellar/claudemeister/" in ROOT + "/" else f"из папки {tilde(ROOT)}"
    rows = [("ok", f"claudemeister {version()}, {where}")]
    if not shutil.which("claudemeister"):
        rows.append(("bad", "claudemeister нет в PATH, а команды и хуки вызывают его по имени"))
    claude = shutil.which(CLAUDE_BIN)
    if not claude:
        return rows + [("bad", f"нет {CLAUDE_BIN} в PATH: поставь Claude Code")]
    code, out = run([claude, "--version"])
    rows.append(("ok", f"Claude Code {out.split()[0] if code == 0 and out.split() else '?'}"))
    return rows


def check_commands():
    import setup_cmd
    from slash import EXTRA_SLASH, FORGET_SLASH, FRESH_SLASH, SLASH, SLIM_SLASH
    pages = [(setup_cmd.COMMAND_FILE, SLASH), (setup_cmd.FORGET_FILE, FORGET_SLASH),
             (setup_cmd.FRESH_FILE, FRESH_SLASH), (setup_cmd.SLIM_FILE, SLIM_SLASH)] + \
        [(os.path.join(setup_cmd.COMMANDS, f"{n}.md"), EXTRA_SLASH[n]) for n in setup_cmd.EXTRA_COMMANDS]
    missing, stale = [], []
    for path, text in pages:
        name = "/" + os.path.basename(path)[:-3]
        try:
            with open(path) as f:
                if f.read() != text:
                    stale.append(name)
        except OSError:
            missing.append(name)
    if missing:
        return [("bad", f"нет команд {', '.join(missing)}: claudemeister setup")]
    if stale:
        return [("warn", f"команды {', '.join(stale)} от другой версии: claudemeister setup обновит их")]
    return [("ok", f"команды /restart, /forget и еще {len(pages) - 2} на месте")]


def check_settings():
    try:
        settings = claude_settings.load()
    except ValueError as e:
        return [("bad", f"{tilde(claude_settings.PATH)} не читается как JSON: {e}")]
    rows = []
    lost = claude_settings.missing(settings, exact=True)
    if lost:
        names = sorted({f"{event} {cmd}" for event, cmd, _, _ in lost})
        rows.append(("bad", f"нет хуков или они старые: {', '.join(names)}. claudemeister setup поставит"))
    else:
        rows.append(("ok", "хуки Claude Code на месте"))
    days = settings.get("cleanupPeriodDays")
    rows.append(("ok", f"переписки хранятся {days} дней") if isinstance(days, int) and days >= 90 else
                ("warn", f"переписки удаляются через {days or 30} дней, поиск по сессиям их потеряет. "
                         "claudemeister setup поставит 365"))
    line = claude_settings.statusline_command(settings)
    if "promptsill" in line:
        rows.append(("ok", "строка состояния promptsill"))
    elif line:
        rows.append(("ok", f"строка состояния своя: {line[:60]}"))
    elif promptsill_path():
        rows.append(("warn", "promptsill стоит, но statusLine пустая: promptsill install"))
    else:
        rows.append(("warn", "строки состояния нет: brew install kopylovis/tap/promptsill && promptsill install"))
    return rows


def check_mcp():
    import setup_cmd
    from mcp_server import MCP_NAME
    listed, registered = setup_cmd.mcp_get(MCP_NAME)
    if not listed:
        return [("bad", f"MCP-сервер {MCP_NAME} не подключен: claudemeister setup")]
    rows = []
    if registered[1:] != ["mcp"] or os.path.realpath(registered[0]) != os.path.realpath(CM):
        rows.append(("warn", f"MCP-сервер подключен к {' '.join(registered)}, а не к {tilde(CM)} mcp"))
    req = json.dumps({"jsonrpc": "2.0", "id": 1, "method": "tools/list"}) + "\n"
    code, out = run(registered or [CM, "mcp"], timeout=20, stdin=req)
    try:
        count = len(json.loads(out.splitlines()[0])["result"]["tools"])
        rows.append(("ok", f"MCP-сервер {MCP_NAME} отвечает, инструментов {count}"))
    except (ValueError, KeyError, IndexError):
        rows.append(("bad", f"MCP-сервер не ответил на tools/list: {out.strip()[:120] or code}"))
    return rows


def check_zsh():
    import setup_cmd
    if any(setup_cmd.is_zsh_hook(line) for line in setup_cmd.zshrc_lines()):
        return [("ok", f"хук перезапуска в {tilde(setup_cmd.ZSHRC)}")]
    return [("warn", f"нет хука перезапуска в {tilde(setup_cmd.ZSHRC)}: /restart вернется во вкладку только в "
                     "Terminal и iTerm2. claudemeister setup добавит")]


def check_notify():
    import claude_notify
    if not claude_notify.enabled():
        return [("ok", "уведомления выключены (claudemeister notify on)")]
    if os.path.isdir(claude_notify.AGENT.app):
        return [("ok", f"уведомления: {tilde(claude_notify.AGENT.app)}")]
    return [("bad", "уведомления включены, но приложения нет: claudemeister setup соберет его (нужен swiftc)")]


def check_proxy():
    import claude_proxy
    if claude_proxy.configured():
        return [("ok", "прокси через VPS настроен (проверить выход: claudemeister proxy test)")]
    return [("ok", "прокси через VPS не настроен, claudemeister -x недоступен")]


def check_modes():
    conf = read_config()
    states = [f"{name} {'вкл' if on else 'выкл'}" for name, on in
              (("guard", conf.get("guard", "on") != "off"), ("humanize", conf.get("humanize", "off") == "on"))]
    return [("ok", "режимы: " + ", ".join(states))]


CHECKS = [check_install, check_commands, check_settings, check_mcp, check_zsh, check_notify, check_proxy,
          check_modes]


def main(args):
    if any(a in ("-h", "--help", "help") for a in args):
        print(USAGE)
        return 0
    marks = {"ok": OK, "warn": WARN, "bad": BAD}
    worst = "ok"
    for check in CHECKS:
        try:
            rows = check()
        except Exception as e:
            rows = [("bad", f"{check.__name__[6:]}: проверка упала ({e!r})")]
        for level, text in rows:
            print(f"{marks[level]} {text}")
            if level == "bad" or (level == "warn" and worst == "ok"):
                worst = level
    print()
    print({"ok": "Все в порядке.", "warn": "Работает, но есть что поправить.",
           "bad": "Есть поломки, начни с claudemeister setup."}[worst])
    return 1 if worst == "bad" else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
