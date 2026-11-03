"""~/.claude/settings.json: чтение, атомарная запись, хуки и statusLine claudemeister.

Хуки берутся из mnrhlib.HOOKS: (событие, команда claudemeister, async, matcher).
"""
import json
import os
import shlex
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from mnrhlib import CLAUDE_HOME, HOOK_TIMEOUTS, HOOKS, STATUS_CMD, hook_command, is_hook  # noqa: E402

PATH = os.path.join(CLAUDE_HOME, "settings.json")
# Ключи, которые старые версии Claude Code держали в ~/.claude.json; новые его еще читают
LEGACY = os.path.join(os.path.expanduser("~"), ".claude.json")
MANAGED = "/Library/Application Support/ClaudeCode/managed-settings.json"


def load(path=PATH):
    """settings.json как dict; {} если файла нет. ValueError, если это не JSON-объект."""
    try:
        with open(path) as f:
            data = json.load(f)
    except FileNotFoundError:
        return {}
    if not isinstance(data, dict):
        raise ValueError("ожидался JSON-объект")
    return data


def setting(key, cwd=None, user=PATH, legacy=LEGACY, managed=MANAGED):
    """Значение ключа настроек Claude Code с учетом старшинства файлов: managed, settings.local.json и
    settings.json проекта, пользовательский, потом ~/.claude.json от старых версий. None, если нигде нет."""
    files = [managed]
    if cwd:
        files += [os.path.join(cwd, ".claude", "settings.local.json"), os.path.join(cwd, ".claude", "settings.json")]
    files += [user, legacy]
    for path in files:
        try:
            data = load(path)
        except (OSError, ValueError):
            continue
        if key in data:
            return data[key]
    return None


def save_atomic(data, path=PATH):
    """Записать через временный файл и os.replace. settings.json из dotfiles остается ссылкой."""
    path = os.path.realpath(path)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = f"{path}.{os.getpid()}.mnrh-tmp"
    try:
        with open(tmp, "w") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
            f.write("\n")
        if os.path.exists(path):
            os.chmod(tmp, os.stat(path).st_mode & 0o7777)
        os.replace(tmp, path)
    finally:
        if os.path.exists(tmp):
            os.remove(tmp)


def hook_commands(settings):
    """[(событие, matcher, команда)] всех хуков в settings. ValueError, если hooks не в формате Claude Code."""
    hooks = settings.get("hooks") or {}
    out = []
    try:
        for event, entries in hooks.items():
            for e in entries:
                for h in e.get("hooks", []):
                    out.append((event, e.get("matcher"), str(h.get("command", ""))))
    except (AttributeError, TypeError) as e:
        raise ValueError(f"hooks: {e}") from None
    return out


def rows(cmd):
    """Строки HOOKS одной команды, например notify-hook."""
    return [r for r in HOOKS if r[1] == cmd]


def missing(settings, wanted=HOOKS, exact=False):
    """Строки wanted, которых нет в settings. exact: matcher тоже должен совпасть, иначе хук считается старым."""
    have = hook_commands(settings)
    return [r for r in wanted if not any(ev == r[0] and (not exact or m == r[3]) and is_hook(c, r[1])
                                         for ev, m, c in have)]


def entry(cmd, run_async, matcher):
    hook = {"type": "command", "command": hook_command(cmd), "timeout": HOOK_TIMEOUTS.get(cmd, 10)}
    if run_async:
        hook["async"] = True
    return dict({"matcher": matcher} if matcher else {}, hooks=[hook])


def ensure_hooks(settings, install=True, wanted=HOOKS):
    """Переставить наши хуки: записи с ними (и со старыми mnrh claude ...) убрать, при install добавить в конец."""
    hooks = settings.setdefault("hooks", {})
    for event, cmd, run_async, matcher in wanted:
        entries = [e for e in hooks.get(event, [])
                   if not any(is_hook(h.get("command", ""), cmd) for h in e.get("hooks", []))]
        if install:
            entries.append(entry(cmd, run_async, matcher))
        if entries:
            hooks[event] = entries
        else:
            hooks.pop(event, None)
    if not hooks:
        settings.pop("hooks")


def add_missing_hooks(settings, wanted):
    """Добавить хуки из wanted, которых нет; остальные записи не трогает. Возвращает события добавленных."""
    added = missing(settings, wanted)
    hooks = settings.setdefault("hooks", {})
    for event, cmd, run_async, matcher in added:
        hooks.setdefault(event, []).append(entry(cmd, run_async, matcher))
    return [r[0] for r in added]


# Claude Code удаляет переписки старше cleanupPeriodDays, по умолчанию через 30 дней. Поиск по сессиям
# и /resume теряют все старше месяца, поэтому setup просит хранить год. Свое значение пользователя не трогаем.
RETENTION_DAYS = 365


def ensure_retention(settings, install=True):
    """True, если записали cleanupPeriodDays (его не было). setup --remove убирает только наше значение."""
    if not install:
        if settings.get("cleanupPeriodDays") == RETENTION_DAYS:
            settings.pop("cleanupPeriodDays")
        return False
    if "cleanupPeriodDays" in settings:
        return False
    settings["cleanupPeriodDays"] = RETENTION_DAYS
    return True


def statusline_command(settings):
    line = settings.get("statusLine")
    return str(line.get("command", "")) if isinstance(line, dict) else ""


def ensure_statusline(settings, promptsill, install=True):
    """promptsill (путь или None) в statusLine, если там пусто или заглушка claudemeister statusline.
    Без promptsill заглушка mnrh claude переписывается на claudemeister. install=False убирает нашу строку.
    Чужую statusLine не трогает. Возвращает True, если что-то поменял."""
    line = settings.get("statusLine")
    command = statusline_command(settings)
    if install and promptsill and (not line or is_hook(command, STATUS_CMD)):
        settings["statusLine"] = {"type": "command", "command": shlex.quote(promptsill),
                                  "padding": 0, "refreshInterval": 10}
    elif install and is_hook(command, STATUS_CMD):
        settings["statusLine"] = dict(line, command=hook_command(STATUS_CMD))
    elif not install and (is_hook(command, STATUS_CMD) or "promptsill" in command):
        settings.pop("statusLine")
    else:
        return False
    return True
