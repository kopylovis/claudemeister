"""claudemeister setup: команды, хуки Claude Code, хук zsh, MCP-сервер; --remove убирает их."""
import os
import re
import shutil
import subprocess
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from mnrhlib import CLAUDE_HOME, CM, HOME, PROMPTSILL_HINT, ROOT, STATUS_CMD, is_hook, promptsill_path, tilde  # noqa: E402
from claude_proc import CLAUDE_BIN  # noqa: E402
import claude_settings  # noqa: E402
from mcp_server import MCP_NAME, READ_ONLY, tools  # noqa: E402
from slash import EXTRA_COMMANDS, EXTRA_SLASH, FORGET_SLASH, FRESH_SLASH, SLASH, SLIM_SLASH  # noqa: E402

COMMANDS = os.path.join(CLAUDE_HOME, "commands")
COMMAND_FILE = os.path.join(COMMANDS, "restart.md")
FORGET_FILE = os.path.join(COMMANDS, "forget.md")
FRESH_FILE = os.path.join(COMMANDS, "fresh.md")
OLD_FORGET_FILE = os.path.join(COMMANDS, "forget-session.md")
SLIM_FILE = os.path.join(COMMANDS, "slim.md")
SETTINGS = claude_settings.PATH
# Так MCP-сервер назывался, пока claudemeister был частью mnrh (mnrh claude mcp). setup его убирает.
OLD_MCP_NAME = "mnrh"
# Строка хука перезапуска в ~/.zshrc: новая и та, что ставил mnrh
ZSH_MARKERS = ("share/claudemeister/restart.zsh", "share/mnrh/restart.zsh")
ZSHRC = os.path.join(os.environ.get("ZDOTDIR") or HOME, ".zshrc")


def is_zsh_hook(line):
    return any(m in line for m in ZSH_MARKERS)


def hook_file():
    root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.realpath(__file__)))))
    m = re.match(r"(.*)/Cellar/claudemeister/[^/]+/(.*)", root)
    if m:
        root = f"{m.group(1)}/opt/claudemeister/{m.group(2)}"
    return os.path.join(root, "share", "claudemeister", "restart.zsh")


def zshrc_lines():
    try:
        with open(ZSHRC) as f:
            return f.read().splitlines(keepends=True)
    except OSError:
        return []


def write_zshrc(lines):
    with open(ZSHRC, "w") as f:
        f.writelines(lines)


def rename_rule(rule):
    """mcp__mnrh и mcp__mnrh__<инструмент> → то же для claudemeister; остальные правила как есть."""
    if rule == f"mcp__{OLD_MCP_NAME}":
        return f"mcp__{MCP_NAME}"
    if rule.startswith(f"mcp__{OLD_MCP_NAME}__"):
        return f"mcp__{MCP_NAME}__" + rule[len(f"mcp__{OLD_MCP_NAME}__"):]
    return rule


def notice_hook(install, rename_rules=False):
    """rename_rules: перевести разрешения старого MCP-сервера mnrh на claudemeister (сервер mnrh был наш)."""
    try:
        settings = claude_settings.load()
    except ValueError as e:
        print(f"✗ {tilde(SETTINGS)} не читается как JSON ({e}), хуки Claude Code не трогаю")
        return
    claude_settings.ensure_hooks(settings, install)
    retention = claude_settings.ensure_retention(settings, install)
    line = settings.get("statusLine")
    command = claude_settings.statusline_command(settings)
    ours = is_hook(command, STATUS_CMD) or "promptsill" in command
    promptsill = promptsill_path()
    # без promptsill заглушка statusline остается, но уже claudemeister, а не mnrh claude
    claude_settings.ensure_statusline(settings, promptsill, install)
    status_note = ("! строка состояния: у тебя уже своя statusLine, не трогаю" if line and not ours else
                   "✓ строка состояния: promptsill уже стоит в statusLine" if "promptsill" in command else
                   "✓ строка состояния → statusLine (promptsill)" if promptsill else
                   f"! строка состояния теперь отдельная программа promptsill: {PROMPTSILL_HINT}, "
                   f"потом снова claudemeister setup")
    allow_rules = [f"mcp__{MCP_NAME}__{name}" for name in READ_ONLY]
    # разрешения старого MCP-сервера mnrh тоже убираем: инструменты теперь у claudemeister
    old_rules = {f"mcp__{OLD_MCP_NAME}__{name}" for name in READ_ONLY + ["mac_status", "free_memory"]}
    perms = settings.setdefault("permissions", {})
    if rename_rules:
        for key in ("allow", "ask", "deny"):
            if isinstance(perms.get(key), list):
                perms[key] = list(dict.fromkeys(rename_rule(r) for r in perms[key]))
    allow = [r for r in perms.get("allow", []) if r not in allow_rules and r not in old_rules] + \
        (allow_rules if install else [])
    if allow:
        perms["allow"] = allow
    else:
        perms.pop("allow", None)
    if not perms:
        settings.pop("permissions")
    claude_settings.save_atomic(settings)
    if install:
        print(f"✓ сообщения после /restart и /forget, удаление пустых сессий → хуки SessionStart и SessionEnd "
              f"в {tilde(SETTINGS)}")
        print(status_note)
        print("✓ защита секретов: вопрос перед чтением и удалением ключей → хук PreToolUse (claudemeister guard)")
        print("✓ уведомления, когда Claude ждёт тебя → хуки UserPromptSubmit, Stop и Notification "
              "(claudemeister notify -h)")
        print("✓ claudemeister humanization: правила письма без признаков нейросети → хуки SessionStart, UserPromptSubmit "
              "и Stop; включить: /humanize или claudemeister humanize on")
        print(f"✓ без вопроса о разрешении: {', '.join(READ_ONLY)} (они только читают)")
        days = settings.get("cleanupPeriodDays")
        print(f"✓ переписки хранятся {claude_settings.RETENTION_DAYS} дней вместо 30 → cleanupPeriodDays"
              if retention else f"✓ переписки хранятся {days} дней (cleanupPeriodDays, твое значение)")


SETUP_USAGE = """claudemeister setup [--country KZ] [--only <имя>]
    поставить команды /restart, /forget и другие, хуки Claude Code, хук zsh, MCP-сервер claudemeister
    и настроить прокси через VPS, если он еще не настроен. --country и --only уходят в claudemeister proxy setup
claudemeister setup --remove    убрать все это (прокси остается, его убирает claudemeister proxy remove)"""


def setup_args(args):
    """{'remove': bool, 'country': str, 'only': str} или None с ошибкой в stderr."""
    opts, it = {"remove": False, "country": "", "only": ""}, iter(args)
    for a in it:
        if a == "--remove":
            opts["remove"] = True
        elif a in ("--country", "--only"):
            value = next(it, "").strip()
            if not value or value.startswith("-"):
                print(f"claudemeister setup: после {a} нужно значение\n\n{SETUP_USAGE}", file=sys.stderr)
                return None
            opts[a[2:]] = value
        else:
            print(f"claudemeister setup: не знаю {a}\n\n{SETUP_USAGE}", file=sys.stderr)
            return None
    if opts["country"] and not re.match(r"^[A-Za-z]{2}$", opts["country"]):
        print("claudemeister setup: --country ждет код страны из двух букв, например KZ", file=sys.stderr)
        return None
    return opts


def mcp_get(name):
    """(подключен ли MCP-сервер name, [команда, аргументы...]) по выводу claude mcp get."""
    r = subprocess.run([CLAUDE_BIN, "mcp", "get", name], capture_output=True, text=True)
    if r.returncode != 0:
        return False, []
    command = re.search(r"^\s*Command:\s*(.+?)\s*$", r.stdout, re.M)
    args = re.search(r"^\s*Args:\s*(.*?)\s*$", r.stdout, re.M)
    return True, ([command.group(1)] if command else []) + (args.group(1).split() if args else [])


def old_mcp():
    """Старый MCP-сервер mnrh: "ours" (mnrh claude mcp), "foreign" (чужой с тем же именем) или "absent"."""
    listed, registered = mcp_get(OLD_MCP_NAME)
    if not listed:
        return "absent"
    ours = registered[1:] == ["claude", "mcp"] and os.path.basename(registered[0]) == "mnrh"
    return "ours" if ours else "foreign"


def remove_old_mcp(state=None):
    """Убрать MCP-сервер mnrh, если это наш старый mnrh claude mcp. Чужой сервер с тем же именем не трогает."""
    if (state or old_mcp()) != "ours":
        return False
    subprocess.run([CLAUDE_BIN, "mcp", "remove", "--scope", "user", OLD_MCP_NAME], capture_output=True)
    return True


def setup(args):
    import claude_setup
    if any(a in ("-h", "--help", "help") for a in args):
        print(SETUP_USAGE)
        return 0
    opts = setup_args(args)
    if opts is None:
        return 2
    if not shutil.which(CLAUDE_BIN):
        # без claude не зарегистрировать MCP-сервер, а setup бросил бы работу на полпути с traceback
        print(f"claudemeister setup: не нашел {CLAUDE_BIN} в PATH. Поставь Claude Code "
              "(https://docs.claude.com/en/docs/claude-code) и запусти setup снова", file=sys.stderr)
        return 1
    if opts["remove"]:
        for f in [COMMAND_FILE, FORGET_FILE, FRESH_FILE, SLIM_FILE, OLD_FORGET_FILE] + \
                [os.path.join(COMMANDS, f"{n}.md") for n in EXTRA_COMMANDS]:
            if os.path.exists(f):
                os.remove(f)
        subprocess.run([CLAUDE_BIN, "mcp", "remove", "--scope", "user", MCP_NAME], capture_output=True)
        remove_old_mcp()
        notice_hook(False)
        lines = zshrc_lines()
        kept = [l for l in lines if not is_zsh_hook(l)]
        if kept != lines:
            write_zshrc(kept)
        print("Убрал /restart, /forget, /fresh, /slim, /todo, /where, /release-notes, /parallel, /shot, /ask, "
              f"/humanize, MCP-сервер {MCP_NAME} и хук из {tilde(ZSHRC)}.")
        # claudemeister больше не предлагает setup при запуске; вернуть все: claudemeister setup
        claude_setup.write(set(), set(claude_setup.IDS))
        return 0
    os.makedirs(os.path.dirname(COMMAND_FILE), exist_ok=True)
    if os.path.exists(OLD_FORGET_FILE):
        os.remove(OLD_FORGET_FILE)
    pages = [(COMMAND_FILE, SLASH), (FORGET_FILE, FORGET_SLASH), (FRESH_FILE, FRESH_SLASH), (SLIM_FILE, SLIM_SLASH)] + \
        [(os.path.join(COMMANDS, f"{n}.md"), EXTRA_SLASH[n]) for n in EXTRA_COMMANDS]
    for path, text in pages:
        with open(path, "w") as f:
            f.write(text)
        print(f"✓ /{os.path.basename(path)[:-3]} → {tilde(path)}")

    # до хуков: если promptsill поставится сейчас, notice_hook сразу пропишет его в statusLine
    promptsill = claude_setup.promptsill_step()
    old = old_mcp()
    notice_hook(True, rename_rules=old != "foreign")
    hook = hook_file()
    line = f'[ -f "{hook}" ] && source "{hook}"\n'
    lines = zshrc_lines()
    if line in lines:
        print(f"✓ хук перезапуска уже в {tilde(ZSHRC)}")
    else:
        lines = [l for l in lines if not is_zsh_hook(l)]
        if lines and not lines[-1].endswith("\n"):
            lines[-1] += "\n"
        write_zshrc(lines + [line])
        print(f"✓ хук перезапуска → {tilde(ZSHRC)} (работает в новых вкладках терминала)")

    if remove_old_mcp(old):
        print(f"✓ MCP-сервер {OLD_MCP_NAME} (mnrh claude mcp) убран, его инструменты теперь у {MCP_NAME}")
    listed, registered = mcp_get(MCP_NAME)
    if listed and registered != [CM, "mcp"]:
        subprocess.run([CLAUDE_BIN, "mcp", "remove", "--scope", "user", MCP_NAME], capture_output=True)
        print(f"✓ MCP-сервер {MCP_NAME} был подключён к {' '.join(registered) or '?'}, переключаю на {tilde(CM)} mcp")
        listed = False
    if listed:
        print(f"✓ MCP-сервер {MCP_NAME} уже подключён")
    else:
        r = subprocess.run([CLAUDE_BIN, "mcp", "add", "--scope", "user", MCP_NAME, "--", CM, "mcp"],
                           capture_output=True, text=True)
        if r.returncode != 0:
            print(f"✗ не подключил MCP: {r.stderr.strip() or r.stdout.strip()}")
            return 1
        print(f"✓ MCP-сервер {MCP_NAME}: {tilde(CM)} mcp (инструменты: {', '.join(t['name'] for t in tools())})")
    import claude_notify
    if claude_notify.ensure_app():
        print(f"✓ приложение уведомлений → {tilde(claude_notify.AGENT.app)}; проверить: claudemeister notify test")
    else:
        print("✗ не собрал приложение уведомлений: нужен swiftc (xcode-select --install)")
    proxy = claude_setup.proxy_step(opts["country"], opts["only"])
    asked = {"proxy": proxy, "promptsill": promptsill}
    claude_setup.record(set(claude_setup.AUTO) | {s for s, r in asked.items() if r == "done"},
                        {s for s, r in asked.items() if r == "skipped"})
    # setup все перевел на claudemeister, прежний отказ от переноса больше не нужен
    claude_setup.write(*claude_setup.current(), legacy_declined=False)
    if not shutil.which("claudemeister"):
        print(f"! claudemeister нет в PATH, а /restart, /todo и другие команды вызывают его по имени. "
              f"Поставь его: brew install kopylovis/tap/claudemeister или make install в {tilde(ROOT)}")
    print("Подхватится в новых вкладках и сессиях Claude Code; в уже открытых — после перезапуска.")
    return 0
