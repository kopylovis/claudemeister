"""Шаги claudemeister setup и проверка перед запуском claudemeister.

setup записывает в ~/.config/mnrh/claude-setup.json, какие шаги сделаны и от каких отказались.
claudemeister перед запуском сверяет запись со STEPS. Если setup на этом Mac не запускали или после
обновления в STEPS появился шаг, которого нет в записи, предлагает настроить.
Новый шаг в setup: добавь его id и описание в STEPS, и при первом запуске после обновления его предложат.
Шаг с вопросом еще и в ASKS, а setup записывает его результат сам, как proxy_step.
"""
import json
import os
import shutil
import subprocess
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from mnrhlib import HOME, HOOKS, LEGACY_CALL, OK, PROMPTSILL_HINT, WARN, promptsill_path, tilde  # noqa: E402

STATE = os.path.join(os.environ.get("XDG_CONFIG_HOME") or os.path.join(HOME, ".config"), "mnrh", "claude-setup.json")
CLAUDE = os.environ.get("MNRH_CLAUDE_HOME", os.path.join(HOME, ".claude"))
STEPS = [
    ("commands", "команды /restart, /forget, /fresh, /slim, /todo и другие"),
    ("hooks", "хуки Claude Code: сообщения после /restart, guard, уведомления, humanization, строка состояния"),
    ("zsh", "хук перезапуска в ~/.zshrc"),
    ("mcp", "MCP-сервер claudemeister"),
    ("notify-app", "приложение уведомлений"),
    ("proxy", "прокси через VPS для claudemeister -x"),
    ("promptsill", "строка состояния promptsill: лимиты, контекст, порты, память"),
]
IDS = [s for s, _ in STEPS]
# Шаги с вопросами, у каждого своя функция. Остальные setup делает сам и отмечает сделанными все разом.
ASKS = {"proxy", "promptsill"}
AUTO = set(IDS) - ASKS
# Эти шаги setup делал и до того, как начал вести запись. Если записи нет, а /restart стоит, они сделаны.
BEFORE_STATE = {"commands", "hooks", "zsh", "mcp", "notify-app"}
YES = ("", "y", "yes", "д", "да")
# Шаги, которые ставил mnrh claude setup. Пока команды, хуки или MCP вызывают mnrh claude, их надо переписать.
LEGACY = ["commands", "hooks", "mcp"]


def raw():
    try:
        with open(STATE) as f:
            data = json.load(f)
        return data if isinstance(data, dict) else None
    except (OSError, ValueError):
        return None


def read():
    data = raw()
    if data is None:
        return None
    return set(data.get("done", [])), set(data.get("skipped", []))


def write(done, skipped, legacy_declined=None):
    """legacy_declined: отказались переводить mnrh claude на claudemeister; None — оставить как было."""
    if legacy_declined is None:
        legacy_declined = bool((raw() or {}).get("legacy_declined"))
    data = {"done": sorted(done), "skipped": sorted(skipped - done)}
    if legacy_declined:
        data["legacy_declined"] = True
    os.makedirs(os.path.dirname(STATE), exist_ok=True)
    tmp = STATE + ".tmp"
    with open(tmp, "w") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
        f.write("\n")
    os.replace(tmp, STATE)


def current():
    """(сделано, отказались). Без записи, но с /restart считаем сделанным то, что setup ставил до записи."""
    state = read()
    if state is not None:
        return state
    if os.path.exists(os.path.join(CLAUDE, "commands", "restart.md")):
        return set(BEFORE_STATE), set()
    return set(), set()


def record(done=(), skipped=()):
    old = current()
    write(old[0] | set(done), (old[1] - set(done)) | set(skipped))


def proxy_configured():
    import claude_proxy
    return claude_proxy.configured()


def hooks_outdated():
    """True, если какого-то нашего хука нет в settings.json или у него старый matcher (например, guard без Grep)."""
    from mnrhlib import is_hook
    try:
        with open(os.path.join(CLAUDE, "settings.json")) as f:
            hooks = json.load(f).get("hooks") or {}
        have = {(event, e.get("matcher"), h.get("command", "")) for event, entries in hooks.items()
                for e in entries for h in e.get("hooks", [])}
    except (OSError, ValueError, AttributeError, TypeError):
        return False
    return any(not any(ev == event and m == matcher and is_hook(str(c), cmd) for ev, m, c in have)
               for event, cmd, _, matcher in HOOKS)


def legacy():
    """True, если /restart, хуки или MCP-сервер еще из mnrh: вызывают mnrh claude, а не claudemeister."""
    def text(path):
        try:
            with open(path) as f:
                return f.read()
        except OSError:
            return ""
    if LEGACY_CALL.search(text(os.path.join(CLAUDE, "commands", "restart.md"))):
        return True
    try:
        settings = json.loads(text(os.path.join(CLAUDE, "settings.json")) or "{}")
        commands = [h.get("command", "") for entries in (settings.get("hooks") or {}).values()
                    for e in entries for h in e.get("hooks", [])]
        line = settings.get("statusLine")
        commands.append(line.get("command", "") if isinstance(line, dict) else "")
    except (ValueError, AttributeError, TypeError):
        commands = []
    if any(LEGACY_CALL.search(str(c)) for c in commands):
        return True
    try:
        servers = json.loads(text(os.path.join(HOME, ".claude.json")) or "{}").get("mcpServers") or {}
    except (ValueError, AttributeError):
        return False
    old = servers.get("mnrh") if isinstance(servers, dict) else None
    return isinstance(old, dict) and old.get("args") == ["claude", "mcp"]


def pending():
    """(шаги, которых нет ни в сделанных, ни в отказах; True, если setup на этом Mac не запускали)."""
    if read() is None and not os.path.exists(os.path.join(CLAUDE, "commands", "restart.md")):
        return list(IDS), True
    done, skipped = current()
    if "proxy" not in done and proxy_configured():
        done = done | {"proxy"}
    if "promptsill" not in done and promptsill_path():
        done = done | {"promptsill"}
    if not (raw() or {}).get("legacy_declined") and legacy():
        done = done - set(LEGACY)
    if "hooks" in done and hooks_outdated():
        done = done - {"hooks"}
    return [s for s in IDS if s not in done and s not in skipped], False


def ask(question, options, default=0, back=None):
    """options: [(значение, подпись, клавиши)]; back: что вернуть на Esc. Без меню спрашивает строкой."""
    back = back or options[-1][0]
    try:
        from menu import choice
        return choice(question, options, default=default, back=back,
                      esc=next(label for value, label, _ in options if value == back).lower())
    except OSError:
        hint = "/".join(o[1] for o in options)
        answer = input(f"{question} [{hint}] ").strip().lower()
        for value, label, keys in options:
            if answer == label.lower() or (answer and answer in keys):
                return value
        return options[default][0]


def proxy_step(country="", only=""):
    """Шаг setup про прокси. Возвращает "done", "skipped" или None (не настроен и не спрашивали)."""
    import claude_proxy
    if claude_proxy.configured():
        want = claude_proxy.setting("country")
        print(f"{OK} прокси через VPS уже настроен" + (f", выход должен быть в {want}" if want else "")
              + " (проверить: claudemeister proxy test)")
        if country and country.upper() != want.upper():
            print(f"{WARN} --country {country.upper()} не применил, прокси уже настроен. "
                  f"Поменять: claudemeister proxy setup --country {country.upper()}")
        return "done"
    if not sys.stdin.isatty():
        print(f"{WARN} прокси через VPS не настроен, не терминал. Настроить: claudemeister proxy setup --country KZ")
        return None
    print("Прокси через VPS: сессии claudemeister -x ходят через твой сервер (подписка 3x-ui), остальное напрямую.")
    if ask("Настроить прокси сейчас?", [("yes", "Да", "yд"), ("no", "Пропустить", "nн")]) != "yes":
        print(f"{WARN} прокси пропущен. Настроить потом: claudemeister proxy setup")
        return "skipped"
    if not country:
        country = input("Страна выхода, которую проверять при запуске, например KZ (Enter, не проверять): ").strip()
    claude_proxy.setup((["--country", country] if country else []) + (["--only", only] if only else []))
    return "done" if claude_proxy.configured() else None


def promptsill_step():
    """Шаг setup про promptsill. Возвращает "done", "skipped" или None (не поставлен и не спрашивали)."""
    if promptsill_path():
        print(f"{OK} строка состояния promptsill уже стоит")
        return "done"
    if not (sys.stdin.isatty() and sys.stdout.isatty()):
        print(f"{WARN} строки состояния promptsill нет, не терминал. Поставить: {PROMPTSILL_HINT}")
        return None
    print("promptsill: строка состояния Claude Code с темпом лимитов, контекстом, dev-серверами, портами и памятью.")
    if ask("Поставить promptsill?", [("yes", "Да", "yд"), ("no", "Пропустить", "nн")]) != "yes":
        print(f"{WARN} promptsill пропущен. Поставить потом: {PROMPTSILL_HINT}, затем claudemeister setup")
        return "skipped"
    brew = shutil.which("brew") or next((p for p in ("/opt/homebrew/bin/brew", "/usr/local/bin/brew")
                                         if os.access(p, os.X_OK)), None)
    if not brew:
        print(f"{WARN} нет Homebrew. Поставь его с https://brew.sh, потом {PROMPTSILL_HINT}")
        return None
    r = subprocess.run([brew, "install", "kopylovis/tap/promptsill"])
    if r.returncode != 0 or not promptsill_path():
        print(f"{WARN} promptsill не поставился. Попробуй сам: {PROMPTSILL_HINT}")
        return None
    print(f"{OK} promptsill поставлен")
    return "done"


def check():
    """Перед запуском claudemeister: предложить setup, если его не делали или появились новые шаги."""
    if os.environ.get("MNRH_NO_SETUP_CHECK") or not (sys.stdin.isatty() and sys.stdout.isatty()):
        return 0
    steps, fresh = pending()
    if not steps:
        return 0
    about = dict(STEPS)
    if fresh:
        print("claudemeister setup на этом Mac еще не запускали. Он ставит:")
    elif legacy():
        print("Команды, хуки и MCP-сервер Claude Code еще вызывают mnrh claude. Теперь это отдельная программа "
              "claudemeister, setup перепишет их на нее:")
    else:
        print("После обновления в claudemeister setup есть шаги, которые здесь не настроены:")
    for s in steps:
        print(f"  {about[s]}")
    answer = ask("Настроить сейчас?", [("yes", "Да", "yд"), ("later", "Позже", "lп"), ("never", "Не предлагать", "nн")],
                 back="later")
    print()
    if answer == "never":
        migrate = legacy() and set(LEGACY) & set(steps)
        record(skipped=set(steps) - (set(LEGACY) if migrate else set()))
        if migrate:
            # шаги переноса в записи сделаны (их ставил mnrh claude setup), поэтому отказ помним отдельно
            done, skipped = current()
            write(done, skipped, legacy_declined=True)
        print("Больше не спрошу. Настроить можно в любой момент: claudemeister setup\n")
    elif answer == "yes" and set(steps) <= ASKS:
        for step in steps:
            result = proxy_step() if step == "proxy" else promptsill_step()
            if result:
                record(**{"done" if result == "done" else "skipped": [step]})
        print()
    elif answer == "yes":
        subprocess.run([sys.executable, os.path.join(os.path.dirname(os.path.abspath(__file__)), "claude_restart.py"),
                        "setup"])
        print()
    else:
        print("Спрошу при следующем запуске.\n")
    return 0


if __name__ == "__main__":
    cmd = sys.argv[1] if len(sys.argv) > 1 else ""
    if cmd == "check":
        sys.exit(check())
    if cmd == "pending":
        steps, fresh = pending()
        print(("fresh " if fresh else "") + " ".join(steps))
        sys.exit(0)
    print("claude_setup.py check | pending", file=sys.stderr)
    sys.exit(2)
