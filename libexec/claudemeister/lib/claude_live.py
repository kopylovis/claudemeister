"""Открытые сессии Claude Code: claudemeister ps показывает их и переводит во вкладку, restore открывает заново
те, что были открыты, когда Mac перезагрузился или Claude Code упал.

SessionStart (хук notice) записывает каждую сессию в ~/.cache/mnrh/open/<id>.json: папку, терминал, вкладку,
caffeinate, прокси и флаги запуска. SessionEnd (хук session-end) убирает запись. Запись, у которой процесса
больше нет, значит, что сессия закрылась не сама.
"""
import json
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from mnrhlib import CACHE, CLAUDE_HOME, CM, OK, WARN, confirm, paint, tilde, write_json  # noqa: E402

OPEN = os.path.join(CACHE, "open")
KEEP_DAYS = 30
STATUS = {"waiting": ("ждет тебя", "33"), "busy": ("работает", "36"), "idle": ("свободна", "2")}
USAGE = """claudemeister ps              открытые сессии Claude Code: статус, проект, название; выбрать и перейти во вкладку
claudemeister ps -l           только список
claudemeister restore [-y]    открыть заново сессии, которые были открыты, когда Mac выключился или Claude упал"""


def record_path(sid):
    return os.path.join(OPEN, sid + ".json")


def remember(sid, cwd, pid, tty, bundle, proxy):
    """Из хука SessionStart, в фоне: caffeinate и флаги читаются из процесса Claude."""
    from claude_proc import argv_of, caffeinate_args, drop_name, resume_flags
    flags = drop_name(resume_flags(argv_of(pid)[1:]))
    os.makedirs(OPEN, exist_ok=True)
    write_json(record_path(sid), {"sid": sid, "cwd": cwd, "pid": pid, "tty": tty, "bundle": bundle, "proxy": proxy,
                                  "caffeinate": caffeinate_args(pid) is not None, "flags": flags, "at": time.time()})


def forget(sid):
    try:
        os.remove(record_path(sid))
    except OSError:
        pass


def alive(pid, sid):
    """Процесс жив и это та же сессия: после перезагрузки pid мог достаться другой программе."""
    try:
        with open(os.path.join(CLAUDE_HOME, "sessions", f"{int(pid)}.json")) as f:
            info = json.load(f)
        os.kill(int(pid), 0)
    except (OSError, ValueError, TypeError, OverflowError):
        return False
    return info.get("sessionId") == sid


def records():
    out = []
    try:
        names = [n for n in os.listdir(OPEN) if n.endswith(".json")]
    except OSError:
        return out
    for name in names:
        path = os.path.join(OPEN, name)
        try:
            with open(path) as f:
                r = json.load(f)
        except (OSError, ValueError):
            continue
        if not isinstance(r, dict) or time.time() - r.get("at", 0) > KEEP_DAYS * 86400:
            forget(name[:-5])
            continue
        out.append(r)
    return out


def live():
    """Сессии из реестра Claude Code (~/.claude/sessions/<pid>.json), у которых жив процесс."""
    folder = os.path.join(CLAUDE_HOME, "sessions")
    try:
        names = [n for n in os.listdir(folder) if n.endswith(".json")]
    except OSError:
        return []
    found = []
    for name in names:
        try:
            with open(os.path.join(folder, name)) as f:
                s = json.load(f)
            pid = int(s.get("pid") or 0)
            if pid <= 0:
                continue
            os.kill(pid, 0)
        except (OSError, ValueError, TypeError, OverflowError):
            continue
        found.append(s)
    return sorted(found, key=lambda s: (s.get("status") != "waiting", -(s.get("statusUpdatedAt") or 0)))


def ago(ms):
    if not isinstance(ms, (int, float)):
        return ""
    sec = max(0, time.time() - ms / 1000)
    return f"{int(sec)} с" if sec < 60 else f"{int(sec // 60)} мин" if sec < 3600 else f"{int(sec // 3600)} ч"


def title(s):
    name = s.get("name") if isinstance(s.get("name"), str) else ""
    return name or "без названия"


def row(s):
    label, code = STATUS.get(s.get("status"), (s.get("status") or "?", ""))
    folder = os.path.basename(str(s.get("cwd") or "").rstrip("/")) or "~"
    since = ago(s.get("statusUpdatedAt"))
    return (f"{paint(label.ljust(10), code)} {folder[:22].ljust(22)} {title(s)[:40].ljust(40)} "
            f"{paint(since, '2')}")


def tty_of(pid):
    from claude_proc import ps
    tty = ps("tty", int(pid))
    return "/dev/" + tty if tty and "?" not in tty and not tty.startswith("/dev/") else tty


def jump(s):
    import claude_notify
    rec = next((r for r in records() if r.get("sid") == s.get("sessionId")), {})
    tty = rec.get("tty") or tty_of(s["pid"])
    bundles = [rec["bundle"]] if rec.get("bundle") else list(claude_notify.TERMINALS)
    for bundle in bundles:
        term = claude_notify.TERMINALS.get(bundle)
        if term and tty and claude_notify.osascript(claude_notify.FOCUS[term], tty) == "ok":
            return True
    return False


def ps_cmd(args):
    sessions = live()
    if not sessions:
        print("Открытых сессий Claude Code нет.")
        return 0
    if "-l" in args or not (sys.stdin.isatty() and sys.stdout.isatty()):
        for s in sessions:
            print(row(s))
        return 0
    from menu import pick
    at = pick([(str(i), row(s)) for i, s in enumerate(sessions)], title=f"Открытые сессии ({len(sessions)}):",
              esc="выход")
    if at is None:
        return 0
    if not jump(sessions[at]):
        print(f"{WARN} не нашел вкладку этой сессии; она в {tilde(sessions[at].get('cwd') or '?')}")
    return 0


def lost():
    """Записи сессий, которые были открыты, но закрылись не сами: процесса нет, переписка на месте."""
    from session_store import by_id
    out = []
    for r in records():
        sid = r.get("sid")
        if not sid or alive(r.get("pid"), sid):
            continue
        if not by_id(sid, meta=False):
            forget(sid)
            continue
        out.append(r)
    return sorted(out, key=lambda r: r.get("at", 0))


def reopen(r):
    import claude_open
    flags = [f for f in r.get("flags") or [] if f != "--remote-control"]
    rc = "--remote-control" in (r.get("flags") or [])
    saved = os.environ.pop("MNRH_PROXY", None)
    if r.get("proxy"):
        os.environ["MNRH_PROXY"] = "1"
    try:
        return claude_open.open_claude({"path": r["cwd"], "mode": "caffeinate" if r.get("caffeinate") else "normal",
                                        "flags": ["--resume", r["sid"]] + flags, "trust": True,
                                        "remote_control": rc}, CM)
    finally:
        os.environ.pop("MNRH_PROXY", None)
        if saved is not None:
            os.environ["MNRH_PROXY"] = saved


def restore_cmd(args):
    from session_store import by_id
    todo = lost()
    if not todo:
        print("Нечего восстанавливать: все сессии закрывались сами.")
        return 0
    print(f"Были открыты и закрылись не сами ({len(todo)}):")
    for r in todo:
        s = (by_id(r["sid"]) or [{}])[0]
        mode = ("caffeinate" if r.get("caffeinate") else "обычная") + (", через VPS" if r.get("proxy") else "")
        print(f"  {r['sid'][:8]}  {tilde(r.get('cwd') or '?'):30} {(s.get('title') or '')[:40]:40} {mode}")
    if not confirm("Открыть их заново, каждую в своем окне?", "-y" in args):
        return 0
    bad = 0
    for r in todo:
        text, err = reopen(r)
        print(f"{WARN if err else OK} {text}")
        bad += bool(err)
        if not err:
            forget(r["sid"])
    return 1 if bad else 0


def main(argv):
    cmd, args = (argv[0] if argv else "ps"), argv[1:]
    if cmd in ("-h", "--help", "help") or "-h" in args:
        print(USAGE)
        return 0
    if cmd == "restore":
        return restore_cmd(args)
    return ps_cmd(args)


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
