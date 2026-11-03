"""Процесс Claude Code, из которого нас вызвали: pid, tty, argv, флаги для перезапуска.

Модуль нужен хукам (notify-hook, humanize) и перезапуску, поэтому импортирует только stdlib и mnrhlib.
"""
import glob
import json
import os
import re
import shlex
import shutil
import subprocess
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from mnrhlib import CLAUDE_HOME  # noqa: E402

CLAUDE = CLAUDE_HOME
CLAUDE_BIN = os.environ.get("MNRH_CLAUDE_BIN", "claude")
# флаги claude, которые при перезапуске не нужны: сессию задаем сами, остальное открыло бы что-то новое
DROP = {"-r", "--resume", "-c", "--continue", "--session-id", "--fork-session", "-p", "--print", "-w", "--worktree",
        "--tmux", "--from-pr", "--teleport", "--cloud", "--bg", "--background", "--desktop"}
# сколько значений берет флаг, по claude --help: one ровно одно, many до следующего флага, maybe одно, если оно не флаг
FLAG_VALUES = {
    **dict.fromkeys(["--agent", "--agents", "--append-system-prompt", "--autocompact", "--debug-file", "--effort",
                     "--environment", "--fallback-model", "--input-format", "--json-schema", "--max-budget-usd",
                     "--model", "-n", "--name", "--output-format", "--permission-mode", "--permission-prompts",
                     "--plugin-dir", "--plugin-url", "--remote-control-session-name-prefix", "--session-id",
                     "--setting-sources", "--settings", "--system-prompt", "--system-prompt-snapshot"], "one"),
    **dict.fromkeys(["--add-dir", "--allowedTools", "--allowed-tools", "--betas", "--disallowedTools",
                     "--disallowed-tools", "--file", "--mcp-config", "--tools"], "many"),
    **dict.fromkeys(["--cloud", "-d", "--debug", "--from-pr", "--prompt-suggestions", "--remote-control", "-r",
                     "--resume", "--teleport", "-w", "--worktree"], "maybe"),
    **dict.fromkeys(["--allow-dangerously-skip-permissions", "--ax-screen-reader", "--bg", "--background", "--bare",
                     "--brief", "--chrome", "-c", "--continue", "--dangerously-skip-permissions", "--desktop",
                     "--disable-slash-commands", "--exclude-dynamic-system-prompt-sections", "--fork-session",
                     "--forward-subagent-text", "--ide", "--include-hook-events", "--include-partial-messages",
                     "--no-chrome", "--no-session-persistence", "-p", "--print", "--replay-user-messages",
                     "--restricted", "--safe-mode", "--strict-mcp-config", "--tmux", "--verbose"], "none"),
}


class RestartError(Exception):
    pass


def ps(field, pid):
    r = subprocess.run(["ps", "-o", f"{field}=", "-p", str(pid)], capture_output=True, text=True)
    return r.stdout.strip()


def session_file(pid):
    return os.path.join(CLAUDE, "sessions", f"{pid}.json")


def find_claude():
    pid = os.getpid()
    for _ in range(30):
        if os.path.exists(session_file(pid)):
            return pid
        ppid = ps("ppid", pid)
        if not ppid or int(ppid) <= 1:
            break
        pid = int(ppid)
    env_pid = os.environ.get("CLAUDE_PID")
    if env_pid and os.path.exists(session_file(env_pid)):
        return int(env_pid)
    raise RestartError("не нашёл процесс Claude Code: запускай изнутри сессии (/restart или MCP)")


def resume_flags(args):
    """Флаги прежнего запуска без первого промпта: он уже в сессии, и повторять его не надо."""
    out, i = [], 0
    while i < len(args):
        a = args[i]
        i += 1
        if a == "--":
            break
        if not a.startswith("-") or a == "-":
            continue
        name, eq, _ = a.partition("=")
        # незнакомый флаг: как раньше, берем следующее слово, если оно не флаг
        kind = "none" if eq else FLAG_VALUES.get(name, "maybe")
        values = []
        if kind == "one" and i < len(args):
            values = [args[i]]
        elif kind in ("maybe", "many"):
            while i + len(values) < len(args) and not args[i + len(values)].startswith("-"):
                values.append(args[i + len(values)])
                if kind == "maybe":
                    break
        i += len(values)
        if name not in DROP:
            out += [a] + values
    return out


def proc_argv(pid):
    """Точный argv процесса через sysctl KERN_PROCARGS2: ps -o args теряет кавычки вокруг аргументов с пробелами."""
    try:
        import ctypes
        import ctypes.util
        libc = ctypes.CDLL(ctypes.util.find_library("c"), use_errno=True)
        mib = (ctypes.c_int * 3)(1, 49, int(pid))  # CTL_KERN, KERN_PROCARGS2
        size = ctypes.c_size_t(0)
        if libc.sysctl(mib, 3, None, ctypes.byref(size), None, 0) != 0:
            return None
        buf = ctypes.create_string_buffer(size.value)
        if libc.sysctl(mib, 3, buf, ctypes.byref(size), None, 0) != 0:
            return None
        raw = buf.raw[:size.value]
        argc = int.from_bytes(raw[:4], sys.byteorder)
        rest = raw[4:]
        rest = rest[rest.index(b"\0"):].lstrip(b"\0")  # путь к исполняемому файлу и выравнивание
        args = rest.split(b"\0")[:argc]
        return [a.decode("utf-8", "surrogateescape") for a in args] if len(args) == argc else None
    except (OSError, ValueError, AttributeError):
        return None


def argv_of(pid):
    argv = proc_argv(pid)
    if argv:
        return argv
    line = ps("args", pid)
    try:
        return shlex.split(line)
    except ValueError:
        return line.split()


def split_caffeinate(args):
    out, i = [], 0
    while i < len(args) and args[i].startswith("-"):
        out.append(args[i])
        if args[i] in ("-t", "-w") and i + 1 < len(args):
            out.append(args[i + 1])
            i += 1
        i += 1
    return out, args[i:]


def caffeinate_args(pid):
    candidates = []
    parent = ps("ppid", pid)
    if parent and os.path.basename(ps("comm", int(parent))) == "caffeinate":
        candidates.append(int(parent))
    r = subprocess.run(["ps", "-axo", "pid=,ppid=,comm="], capture_output=True, text=True)
    for line in r.stdout.splitlines():
        parts = line.split(None, 2)
        if len(parts) == 3 and parts[1] == str(pid) and os.path.basename(parts[2]) == "caffeinate":
            candidates.append(int(parts[0]))
    for c in candidates:
        flags, utility = split_caffeinate(argv_of(c)[1:])
        if utility:
            return flags
    return None


def drop_name(flags):
    out, i = [], 0
    while i < len(flags):
        if flags[i] in ("-n", "--name"):
            i += 2
            continue
        if flags[i].startswith("--name="):
            i += 1
            continue
        out.append(flags[i])
        i += 1
    return out


SHELLS = {"zsh", "bash", "sh", "fish", "login"}


def launcher(pid):
    parent = ps("ppid", pid)
    while parent and int(parent) > 1:
        name = os.path.basename(ps("comm", int(parent))).lstrip("-")
        if name != "caffeinate":
            return name
        parent = ps("ppid", int(parent))
    return ""


def current_sid():
    try:
        with open(session_file(find_claude())) as f:
            return json.load(f).get("sessionId")
    except (RestartError, OSError, ValueError):
        return None


def claude_version(path=None):
    path = path or os.environ.get("CLAUDE_CODE_EXECPATH") or os.path.realpath(shutil.which("claude") or "")
    name = os.path.basename(path)
    return name if re.match(r"^\d+(\.\d+)+$", name) else ""


def session_path(sid):
    found = glob.glob(os.path.join(glob.escape(os.path.join(CLAUDE, "projects")), "*", sid + ".jsonl"))
    return found[0] if found else None
