import json
import os
import re
import shlex
import shutil
import subprocess
import sys

HOME = os.path.expanduser("~")
# Папка Claude Code; MNRH_CLAUDE_HOME подменяет ее в тестах
CLAUDE_HOME = os.environ.get("MNRH_CLAUDE_HOME", os.path.join(HOME, ".claude"))
ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.realpath(__file__)))))
# Команда claudemeister для хуков, MCP и перезапуска. Сначала из PATH: путь /opt/homebrew/bin/claudemeister
# переживает brew upgrade, а путь в Cellar нет.
CM = shutil.which("claudemeister") or os.path.join(ROOT, "bin", "claudemeister")
# Полный mnrh, если стоит: на нем mac_status, free_memory и сон с закрытой крышкой (mnrh sleep lid).
MNRH = shutil.which("mnrh")


def write_json(path, data, mode=None, newline=False, **dump):
    """Записать JSON атомарно: во временный файл рядом и os.replace. mode: права нового файла (с учетом umask),
    newline: перевод строки в конце; остальное уходит в json.dump."""
    tmp = f"{path}.{os.getpid()}.tmp"
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o666 if mode is None else mode)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(data, f, **dump)
            if newline:
                f.write("\n")
        os.replace(tmp, path)
    except BaseException:
        try:
            os.remove(tmp)
        except OSError:
            pass
        raise


def hook_command(sub):
    return f"{shlex.quote(CM)} {sub}"


def is_hook(command, sub):
    """Хук claudemeister <sub> или старый, из mnrh: mnrh claude <sub>."""
    # после имени может стоять кавычка: shlex.quote берет в кавычки путь с пробелом
    # только программа с именем claudemeister или mnrh claude; ~/bin/my-claude notice чужой, его не трогаем
    return re.search(rf"(?:\bmnrh['\"]?\s+claude|(?:^|[/\s'\"])claudemeister)['\"]?\s+{re.escape(sub)}(?:\s|$)",
                     command or "") is not None


# Хук или statusLine, которые ставил mnrh claude setup: .../mnrh claude <команда>
# Хуки Claude Code, которые ставит claudemeister setup: (событие, команда claudemeister, async, matcher).
# Старые хуки mnrh claude <команда> setup заменяет этими. Поменял matcher: claude_setup.pending заметит и предложит setup.
HOOKS = [("SessionStart", "notice", False, None), ("SessionEnd", "session-end", False, None),
         ("UserPromptSubmit", "notify-hook", True, None), ("Stop", "notify-hook", True, None),
         ("Notification", "notify-hook", True, None),
         ("PreToolUse", "guard-hook", False, "Bash|Read|Edit|Write|MultiEdit|NotebookEdit|Grep"),
         ("SessionStart", "humanize-hook", False, None), ("UserPromptSubmit", "humanize-hook", False, None),
         ("Stop", "humanize-hook", True, None)]
STATUS_CMD = "statusline"
LEGACY_CALL = re.compile(r"\bmnrh['\"]?\s+claude\s+\S")
TTY = sys.stdout.isatty()


def parse_env(env=None):
    """Окружение для команд, чей вывод разбираем. В ru_RU ps и top пишут 0,1 вместо 0.1, и float() падает.
    LC_ALL перекрывает LC_NUMERIC, поэтому его значение переносим в LANG: язык сообщений остается тем же."""
    env = dict(os.environ if env is None else env)
    if env.get("LC_ALL"):
        env["LANG"] = env.pop("LC_ALL")
    env.pop("LC_ALL", None)
    env["LC_NUMERIC"] = "C"
    return env


def run(cmd, timeout=30, env=None):
    try:
        return subprocess.run(cmd, capture_output=True, text=True, timeout=timeout, env=parse_env(env)).stdout
    except (subprocess.TimeoutExpired, FileNotFoundError, PermissionError):
        return ""


def paint(text, code):
    return f"\033[{code}m{text}\033[0m" if TTY else text


OK = paint("✓", "32")
WARN = paint("!", "33")
BAD = paint("✗", "31")


def tilde(path):
    return path.replace(HOME, "~", 1) if path.startswith(HOME) else path


def has_flag(args, *names):
    return any(a in args for a in names)


SETTINGS_NAMES = {
    "settings": ("Системные настройки", "System Settings"),
    "privacy": ("Конфиденциальность и безопасность", "Privacy & Security"),
    "automation": ("Автоматизация", "Automation"),
    "screen": ("Запись экрана и аудио", "Screen & System Audio Recording"),
    "notifications": ("Уведомления", "Notifications"),
    "allow": ("Разрешить уведомления", "Allow notifications"),
    "general": ("Основные", "General"),
    "login": ("Объекты входа и расширения", "Login Items & Extensions"),
}
_english = None


def system_english():
    global _english
    if _english is None:
        langs = run(["defaults", "read", "-g", "AppleLanguages"], timeout=5)
        m = re.search(r'"?([A-Za-z]{2})', langs)
        _english = bool(m) and m.group(1).lower() != "ru"
    return _english


def settings_name(key):
    names = SETTINGS_NAMES.get(key)
    return (names[1] if system_english() else names[0]) if names else key


def settings_path(*keys):
    return " → ".join(settings_name(k) for k in keys)


CONFIG = os.path.join(os.environ.get("XDG_CONFIG_HOME") or os.path.join(HOME, ".config"), "mnrh", "config")
DEFAULT_DEV = os.path.join(HOME, "Developer")
TOSSLING_PATHS = tuple(os.path.join(d, name) for name in ("tossling", "tossy")
                    for d in ("/opt/homebrew/bin", "/usr/local/bin", os.path.join(HOME, ".local", "bin")))


def tossling_path():
    for name in ("tossling", "tossy"):
        found = shutil.which(name)
        if found:
            return found
    return next((p for p in TOSSLING_PATHS if os.access(p, os.X_OK)), None)


PROMPTSILL_PATHS = tuple(os.path.join(d, "promptsill")
                         for d in ("/opt/homebrew/bin", "/usr/local/bin", os.path.join(HOME, ".local", "bin")))
PROMPTSILL_HINT = "brew install kopylovis/tap/promptsill"


def promptsill_path():
    forced = os.environ.get("MNRH_PROMPTSILL")
    if forced is not None:
        return forced if os.access(forced, os.X_OK) else None
    return shutil.which("promptsill") or next((p for p in PROMPTSILL_PATHS if os.access(p, os.X_OK)), None)


BADGES = os.path.join(os.environ.get("XDG_STATE_HOME") or os.path.join(HOME, ".local", "state"), "promptsill", "badges")


def badge(name, data=None, refresh=False):
    """Метка во второй строке promptsill (файл в BADGES); data=None убирает ее."""
    path = os.path.join(BADGES, name + ".json")
    try:
        if data is None:
            os.remove(path)
        elif refresh or not os.path.exists(path):
            os.makedirs(BADGES, exist_ok=True)
            write_json(path, data, ensure_ascii=False)
    except OSError:
        pass


def read_config():
    conf = {}
    try:
        with open(CONFIG) as f:
            for line in f:
                key, sep, value = line.strip().partition("=")
                if sep:
                    conf[key.strip()] = value.strip()
    except OSError:
        pass
    return conf


def write_config(**values):
    conf = read_config()
    conf.update(values)
    os.makedirs(os.path.dirname(CONFIG), exist_ok=True)
    with open(CONFIG, "w") as f:
        for key, value in conf.items():
            f.write(f"{key}={value}\n")


def projects_dir():
    path = os.environ.get("MNRH_PROJECTS") or read_config().get("projects") or DEFAULT_DEV
    return os.path.abspath(os.path.expanduser(path))


DEV = projects_dir()


def projects():
    if not os.path.isdir(DEV):
        return []
    return sorted(os.path.join(DEV, d) for d in os.listdir(DEV)
                  if os.path.isdir(os.path.join(DEV, d, ".git")))


def confirm(question, assume_yes):
    import sys as _sys
    if assume_yes:
        return True
    if not _sys.stdin.isatty():
        print("Не терминал, подтвердить нельзя. Повтори с -y")
        _sys.exit(2)
    try:
        from menu import choice
        return choice(question, [("yes", "Да", "yд"), ("no", "Нет", "nн")], default=1, back="no", esc="нет") == "yes"
    except OSError:
        return input(f"{question} [y/N] ").strip().lower() in ("y", "yes", "д", "да")


def project_root(cwd=None):
    cwd = os.path.abspath(cwd or os.getcwd())
    out = run(["git", "-C", cwd, "rev-parse", "--show-toplevel"], timeout=5).strip()
    return out or cwd


def find_project(query=None, cwd=None):
    if not query:
        return project_root(cwd), None
    if query in ("~", "root", "home"):
        return HOME, None
    path = os.path.abspath(os.path.expanduser(query))
    if os.path.isdir(path) and ("/" in query or query.startswith(("~", "."))):
        return project_root(path), None
    root = projects_dir()
    names = sorted(d for d in os.listdir(root) if os.path.isdir(os.path.join(root, d)) and not d.startswith(".")) \
        if os.path.isdir(root) else []
    hits = [d for d in names if d.lower() == query.lower()] or \
        [d for d in names if d.lower().startswith(query.lower())]
    if len(hits) == 1:
        return os.path.join(root, hits[0]), None
    if hits:
        return None, f"«{query}» подходит к нескольким проектам: {', '.join(hits)}"
    return None, f"проекта «{query}» нет в {tilde(root)}"
