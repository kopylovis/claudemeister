#!/usr/bin/env python3
import json
import os
import re
import shlex
import shutil
import signal
import subprocess
import sys
import time
import uuid

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from mnrhlib import HOOKS, STATUS_CMD  # noqa: E402,F401
from mnrhlib import CM, HOME, MNRH, PROMPTSILL_HINT, ROOT, hook_command, is_hook, promptsill_path, settings_path, tilde
from claude_proc import (CLAUDE, CLAUDE_BIN, SHELLS, RestartError, argv_of, caffeinate_args,  # noqa: E402,F401
                         claude_version, current_sid, drop_name, find_claude, launcher, ps, resume_flags,
                         session_file, session_path)
from slash import EXTRA_COMMANDS, EXTRA_SLASH, FORGET_SLASH, FRESH_SLASH, SLASH, SLIM_SLASH  # noqa: E402
import claude_mac
import claude_open
import claude_search
import gradle_deps
import claude_ask
import claude_parallel
import mnrh_todo
import release_notes
import window_shot

LOG = os.path.join(HOME, "Library", "Logs", "mnrh-claude-restart.log")
COMMANDS = os.path.join(CLAUDE, "commands")
COMMAND_FILE = os.path.join(COMMANDS, "restart.md")
FORGET_FILE = os.path.join(COMMANDS, "forget.md")
FRESH_FILE = os.path.join(COMMANDS, "fresh.md")
OLD_FORGET_FILE = os.path.join(COMMANDS, "forget-session.md")
SLIM_FILE = os.path.join(COMMANDS, "slim.md")
SLIM_MIN = 5 << 20
QUEUE = os.path.join(HOME, ".cache", "mnrh", "restart")
NOTICES = os.path.join(HOME, ".cache", "mnrh", "notice")
NOTICE_TTL = 300
SETTINGS = os.path.join(CLAUDE, "settings.json")
MCP_NAME = "claudemeister"
# Так MCP-сервер назывался, пока claudemeister был частью mnrh (mnrh claude mcp). setup его убирает.
OLD_MCP_NAME = "mnrh"
# Строка хука перезапуска в ~/.zshrc: новая и та, что ставил mnrh
ZSH_MARKERS = ("share/claudemeister/restart.zsh", "share/mnrh/restart.zsh")
ZSHRC = os.path.join(os.environ.get("ZDOTDIR") or HOME, ".zshrc")
UUID_RE = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$")

FIND_TAB = {
    "Apple_Terminal": '''
on run argv
  tell application "Terminal"
    repeat with w in windows
      repeat with t in tabs of w
        if tty of t is (item 1 of argv) then
          if (count of argv) > 1 then do script (item 2 of argv) in t
          return "ok"
        end if
      end repeat
    end repeat
  end tell
  return "notfound"
end run''',
    "iTerm.app": '''
on run argv
  tell application "iTerm2"
    repeat with w in windows
      repeat with t in tabs of w
        repeat with s in sessions of t
          if tty of s is (item 1 of argv) then
            if (count of argv) > 1 then tell s to write text (item 2 of argv)
            return "ok"
          end if
        end repeat
      end repeat
    end repeat
  end tell
  return "notfound"
end run''',
}

def no_hook_reason(pid, term):
    head = f"в этом терминале ({term}) Claude возвращается во вкладку только через хук zsh, а его здесь нет: "
    parent = launcher(pid)
    if parent not in SHELLS:
        return (head + f"Claude запущен не из shell, а напрямую ({parent or 'неизвестно кем'}), например вкладкой "
                "Claude Code в IDE, и после выхода вкладке некуда вернуться. Открой обычную вкладку терминала "
                "и запусти claude там")
    if parent != "zsh":
        return head + f"shell здесь {parent}, а хук есть только для zsh"
    hooked = any(is_zsh_hook(l) for l in zshrc_lines())
    if not hooked:
        return head + f"в {tilde(ZSHRC)} нет строки с хуком. Выполни claudemeister setup и открой новую вкладку"
    return (head + f"в {tilde(ZSHRC)} он есть, но эта вкладка открыта раньше. Открой новую вкладку терминала "
            "и запусти claude там")


def plan(update=True, forget=False, slim=False, fresh=False):
    pid = find_claude()
    try:
        with open(session_file(pid)) as f:
            info = json.load(f)
    except (OSError, ValueError) as e:
        raise RestartError(f"не прочитал {tilde(session_file(pid))}: {e}")
    sid = info.get("sessionId") or os.environ.get("CLAUDE_CODE_SESSION_ID")
    if not sid:
        raise RestartError("не знаю id текущей сессии")
    cwd = info.get("cwd") or os.getcwd()
    tty = ps("tty", pid)
    if not tty or tty in ("??", "-"):
        raise RestartError("у Claude нет терминала, перезапускать некуда")
    tty = "/dev/" + tty if not tty.startswith("/dev/") else tty
    term = os.environ.get("TERM_PROGRAM", "")
    if os.environ.get("MNRH_RESTART_HOOK") == "1":
        via = "zsh"
    elif term in FIND_TAB:
        via = term
    else:
        raise RestartError(no_hook_reason(pid, term or os.environ.get("TERMINAL_EMULATOR") or "неизвестный"))

    flags = resume_flags(argv_of(pid)[1:])
    caffeinate = caffeinate_args(pid)
    new = forget or fresh
    if new:
        flags = drop_name(flags)

    new_sid = str(uuid.uuid4()) if new else sid
    # сессия через VPS (claudemeister -x): её окружение досталось и нам, перезапускаем тоже через VPS
    proxy = [CM, "proxy", "run", "--"] if os.environ.get("MNRH_PROXY") else []
    run = (proxy + (["caffeinate"] + caffeinate if caffeinate is not None else []) + [CLAUDE_BIN]
           + (["--session-id", new_sid] if new else ["--resume", sid]) + flags)
    update = update and not new and not slim
    # сон с закрытой крышкой держит mnrh sleep lid, он есть только в полном mnrh
    keep = f"{shlex.quote(MNRH)} sleep lid keep >/dev/null 2>&1; " if caffeinate is not None and MNRH else ""
    line = (f"cd {shlex.quote(cwd)} && " + keep
            + (f"{{ {shlex.quote(CLAUDE_BIN)} update; " if update else "{ ")
            + ("clear; printf '\\033[3J'; " if new else "")
            + (f"{shlex.quote(CM)} sessions slim {sid} -y --notice; " if slim else "")
            + shlex.join(run) + "; }")
    return {"pid": pid, "sid": sid, "cwd": cwd, "tty": tty, "term": term, "via": via, "line": line, "forget": forget,
            "slim": slim, "fresh": fresh,
            "title": session_title(sid) or info.get("name") or sid, "new_sid": new_sid,
            "version": claude_version()}


def osascript(term, *args):
    r = subprocess.run(["osascript", "-e", FIND_TAB[term], *args], capture_output=True, text=True, timeout=60)
    return r.returncode, (r.stdout.strip() or r.stderr.strip())


def preflight(p):
    if p["via"] == "zsh":
        os.makedirs(QUEUE, exist_ok=True)
        return
    code, out = osascript(p["term"], p["tty"])
    if code != 0:
        raise RestartError("macOS не дал управлять терминалом: разреши в "
                           f"{settings_path('settings', 'privacy', 'automation')} ({out})")
    if out != "ok":
        raise RestartError(f"не нашёл вкладку с {p['tty']}")


def log(msg):
    try:
        os.makedirs(os.path.dirname(LOG), exist_ok=True)
        with open(LOG, "a") as f:
            f.write(time.strftime("%Y-%m-%d %H:%M:%S ") + msg + "\n")
    except OSError:
        pass


def watcher(p, delay):
    time.sleep(delay)
    log(f"restart {p['sid']} pid {p['pid']} tty {p['tty']} via {p['via']}")
    job = os.path.join(QUEUE, os.path.basename(p["tty"]))
    write_notice(p, "pending" if p["forget"] or p.get("slim") else "done")
    if p["via"] == "zsh":
        with open(job, "w") as f:
            f.write(p["line"])
    try:
        os.kill(p["pid"], signal.SIGTERM)
    except ProcessLookupError:
        pass
    for _ in range(150):
        try:
            os.kill(p["pid"], 0)
        except ProcessLookupError:
            break
        time.sleep(0.1)
    else:
        log("SIGTERM не помог, SIGKILL")
        try:
            os.kill(p["pid"], signal.SIGKILL)
        except ProcessLookupError:
            pass
        time.sleep(0.5)
    if p["forget"]:
        ok = forget_session(p["sid"])
        write_notice(p, "done" if ok else "failed")
    if p["via"] == "zsh":
        log(f"zsh: {p['line']}")
        return
    time.sleep(0.8)
    code, out = osascript(p["term"], p["tty"], p["line"])
    log(f"osascript {code} {out}: {p['line']}")


def session_title(sid):
    path = session_path(sid)
    data = claude_search.update(sid, path) if path else None
    title = claude_search.title(sid, data) if data else ""
    return "" if title == "без названия" else title


def write_notice(p, status):
    os.makedirs(NOTICES, exist_ok=True)
    kind = "forget" if p["forget"] else "slim" if p.get("slim") else "fresh" if p.get("fresh") else "restart"
    data = {"kind": kind, "status": status, "title": p["title"],
            "old_sid": p["sid"], "version": p["version"], "at": time.time()}
    tmp = os.path.join(NOTICES, p["new_sid"] + ".tmp")
    with open(tmp, "w") as f:
        json.dump(data, f, ensure_ascii=False)
    os.replace(tmp, os.path.join(NOTICES, p["new_sid"] + ".json"))


def notice_text(n):
    title = n.get("title") or n.get("old_sid", "")
    if len(title) > 60:
        title = title[:59] + "…"
    now = claude_version()
    if n["kind"] == "slim":
        if n.get("status") == "done":
            return (f"✓ claudemeister: сессия «{title}» сжата: {n.get('before')} → {n.get('after')}, скриншотов убрано "
                    f"{n.get('images', 0)}. То, что видит модель, не менялось. Вернуть: "
                    f"claudemeister sessions slim {n.get('old_sid', '')[:8]} --undo")
        if n.get("status") == "failed":
            return f"! claudemeister: сессию «{title}» сжать не удалось ({n.get('error')}). Она открыта без изменений."
        return f"… claudemeister: сессия «{title}» открыта, но сжатие не отчиталось — проверь: claudemeister sessions slim"
    if n["kind"] == "restart":
        was = n.get("version")
        if was and now and was != now:
            ver = f"обновлён {was} → {now}"
        else:
            ver = f"версия {now or was}" + (", обновлений не было" if was and now else "")
        return f"✓ claudemeister: Claude Code перезапущен ({ver}). Сессия «{title}» продолжается."
    if n["kind"] == "fresh":
        return (f"✓ claudemeister: новая чистая сессия. Прошлая «{title}» сохранена, вернуться к ней: "
                f"/resume или claude --resume {n.get('old_sid', '')}")
    if n.get("status") == "done":
        return (f"✓ claudemeister: прошлая сессия «{title}» удалена — переписка, история запросов и file-history. "
                "Это новая чистая сессия.")
    if n.get("status") == "failed":
        return f"! claudemeister: прошлую сессию «{title}» не удалось найти и удалить, проверь: claudemeister sessions -l"
    return f"… claudemeister: прошлая сессия «{title}» ещё удаляется, проверь позже: claudemeister sessions -l"


def hook_payload():
    try:
        payload = json.load(sys.stdin)
    except ValueError:
        return {}
    return payload if isinstance(payload, dict) else {}


def background(fn, *args):
    if os.fork():
        return
    os.setsid()
    if os.fork():
        os._exit(0)
    devnull = os.open(os.devnull, os.O_RDWR)
    for fd in (0, 1, 2):
        os.dup2(devnull, fd)
    signal.signal(signal.SIGHUP, signal.SIG_IGN)
    try:
        fn(*args)
    except Exception as e:
        log(f"ошибка: {e!r}")
    os._exit(0)


def drop_junk(path, why):
    import claude_sessions
    sid = os.path.basename(path)[:-6]
    if os.path.islink(path) or sid in claude_sessions.running_ids() or not claude_sessions.is_junk(path):
        return False
    for s in claude_sessions.load():
        if s["id"] == sid and not s["link"]:
            s["running"] = None
            claude_sessions.delete(s)
            log(f"пустая сессия {sid} удалена ({why})")
            return True
    return False


def warm_up(pdir, keep):
    sweep(pdir, keep)
    claude_search.refresh()


def sweep(pdir, keep):
    for name in os.listdir(pdir):
        path = os.path.join(pdir, name)
        if not name.endswith(".jsonl") or not UUID_RE.match(name[:-6]) or name[:-6] == keep:
            continue
        try:
            if time.time() - os.path.getmtime(path) < 60:
                continue
        except OSError:
            continue
        drop_junk(path, "уборка при старте")


def session_end():
    payload = hook_payload()
    sid, path = payload.get("session_id"), payload.get("transcript_path")
    if not sid or not UUID_RE.match(sid) or not path or not path.endswith(sid + ".jsonl"):
        return 0
    try:
        pid = find_claude()
    except RestartError:
        pid = None

    def later():
        if payload.get("reason") == "clear" or pid is None:
            time.sleep(2)
        else:
            for _ in range(100):
                try:
                    os.kill(pid, 0)
                except ProcessLookupError:
                    break
                time.sleep(0.1)
            time.sleep(0.5)
        drop_junk(path, f"закрытие: {payload.get('reason') or '?'}")

    background(later)
    return 0


def read_notice(sid):
    path = os.path.join(NOTICES, sid + ".json")
    n = None
    for _ in range(30):
        try:
            with open(path) as f:
                n = json.load(f)
        except (OSError, ValueError):
            return None
        if n.get("status") != "pending":
            break
        time.sleep(0.1)
    try:
        os.remove(path)
    except OSError:
        pass
    return n if n and time.time() - n.get("at", 0) <= NOTICE_TTL else None


def brief_wanted(payload, n):
    import claude_brief
    if payload.get("source") != "startup" or (n and n.get("kind") in ("forget", "fresh")):
        return False
    if os.environ.get("MNRH_CHILD") == "1" or not claude_brief.enabled():
        return False
    try:
        tty = ps("tty", find_claude())
    except RestartError:
        return False
    return bool(tty) and "?" not in tty


def notice():
    import claude_guard
    claude_guard.sync_badge()
    payload = hook_payload()
    sid = payload.get("session_id")
    if not sid or not UUID_RE.match(sid):
        return 0
    path = payload.get("transcript_path")
    if path and os.path.isdir(os.path.dirname(path)):
        background(warm_up, os.path.dirname(path), sid)
    n = read_notice(sid)
    messages, out = [notice_text(n)] if n else [], {}
    if brief_wanted(payload, n):
        import claude_brief
        try:
            context, line = claude_brief.context_for_model(payload.get("cwd") or os.getcwd(), exclude=sid)
        except Exception as e:
            log(f"brief: {e!r}")
            context, line = "", ""
        if context:
            out["hookSpecificOutput"] = {"hookEventName": "SessionStart", "additionalContext": context}
        if line:
            messages.append(line)
    if messages:
        out["systemMessage"] = "\n".join(messages)
    if out:
        print(json.dumps(out, ensure_ascii=False))
    return 0


def forget_session(sid):
    import claude_sessions
    for s in claude_sessions.load():
        if s["id"] == sid:
            s["running"] = None
            claude_sessions.delete(s)
            log(f"forget: удалил {sid}")
            return True
    log(f"forget: сессии {sid} не нашёл")
    return False


def detach(p, delay):
    background(watcher, p, delay)


def restart(update=True, delay=1.5, dry=False, forget=False, slim=False, fresh=False):
    p = plan(update, forget, slim, fresh)
    preflight(p)
    if dry:
        return p
    detach(p, delay)
    return p


def cli(args, forget=False, fresh=False):
    name = "forget" if forget else "fresh" if fresh else "restart"
    if "-h" in args or "--help" in args:
        if fresh:
            print("claudemeister fresh              изнутри Claude Code: закрыть его и открыть новую сессию без названия")
            print("                               и с пустым контекстом в той же папке и вкладке; эта остаётся в /resume")
            print("claudemeister fresh --dry-run    только показать, что будет сделано")
        elif forget:
            print("claudemeister forget             изнутри Claude Code: закрыть его, удалить эту сессию без следа")
            print("                               (переписка, история запросов, file-history) и открыть чистый Claude")
            print("                               в той же папке и вкладке — как /clear, только старое не сохраняется")
            print("claudemeister forget --dry-run   только показать, что будет сделано")
        else:
            print("claudemeister restart            изнутри Claude Code: закрыть его и открыть эту же сессию в той же вкладке")
            print("                               перед запуском выполняется claude update")
            print("claudemeister restart --no-update  без обновления")
            print("claudemeister restart --dry-run    только показать, что будет сделано")
        print()
        print(f"Обычно вызывается из Claude: /{name} или инструментом {name} из MCP-сервера claudemeister")
        print("(установить: claudemeister setup). Лог: " + tilde(LOG))
        return 0
    try:
        p = restart(update="--no-update" not in args, dry="--dry-run" in args, forget=forget, fresh=fresh)
    except RestartError as e:
        print(f"claudemeister {name}: {e}", file=sys.stderr)
        return 1
    if "--dry-run" in args:
        extra = f"удалю сессию {p['sid']}, " if forget else ""
        print(f"закрою pid {p['pid']}, {extra}в {p['tty']} через {p['via']} выполню:\n  {p['line']}")
    elif forget:
        print(f"Через пару секунд Claude Code закроется, сессия «{p['title']}» будет удалена, и откроется чистая.")
    elif fresh:
        print(f"Через пару секунд Claude Code закроется и откроется новая сессия. «{p['title']}» останется в /resume.")
    else:
        print(f"Перезапускаю Claude Code через пару секунд, сессия «{p['title']}» откроется снова в этой вкладке.")
    return 0


def fmt_mb(n):
    return f"{n / 1048576:.0f} МБ" if n >= 10 << 20 else f"{n / 1048576:.1f} МБ"


def slim_current(dry=False, delay=1.5):
    import claude_slim
    pid = find_claude()
    try:
        with open(session_file(pid)) as f:
            sid = json.load(f).get("sessionId")
    except (OSError, ValueError):
        sid = None
    path = session_path(sid) if sid else None
    if not path:
        raise RestartError("не нашёл файл этой сессии")
    _, _, st = claude_slim.plan(path)
    if not st:
        return "Сжимать нечего: в сессии меньше двух /compact, а последние два отрезка не трогаются.", False
    if st["before"] - st["after"] < SLIM_MIN:
        return (f"Сжимать почти нечего: {fmt_mb(st['before'])} → {fmt_mb(st['after'])}. "
                "Старые скриншоты и выводы уже маленькие."), False
    what = (f"«{session_title(sid) or sid}»: {fmt_mb(st['before'])} → {fmt_mb(st['after'])}, "
            f"скриншотов {st['images']}, длинных выводов {st['texts']}")
    if dry:
        return f"Сожму {what}.", False
    restart(update=False, delay=delay, slim=True)
    return (f"Сжимаю {what}. Claude Code сейчас закроется, сессия сожмётся в этой вкладке и откроется снова; "
            "то, что видит модель, не меняется."), True


def cli_slim(args):
    import claude_sessions
    if "-h" in args or "--help" in args:
        print("claudemeister slim            изнутри Claude Code: закрыть его, сжать эту сессию (старые скриншоты и")
        print("                            длинные выводы до двух последних /compact) и открыть её снова")
        print("claudemeister slim --dry-run  только посчитать")
        print("claudemeister slim list       что можно сжать среди закрытых сессий")
        print("claudemeister slim <id>       сжать закрытую сессию (вернуть: claudemeister sessions slim <id> --undo)")
        print()
        print("Обычно вызывается из Claude: /slim или инструментом slim из MCP-сервера claudemeister.")
        return 0
    rest = [a for a in args if a != "--dry-run"]
    if rest[:1] == ["list"]:
        return claude_sessions.slim_cmd([], True)
    if rest:
        return claude_sessions.slim_cmd(rest, True)
    try:
        text, _ = slim_current(dry="--dry-run" in args)
    except RestartError as e:
        print(f"claudemeister slim: {e}", file=sys.stderr)
        return 1
    print(text)
    return 0


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


def mcp():
    def send(msg):
        sys.stdout.write(json.dumps(msg, ensure_ascii=False) + "\n")
        sys.stdout.flush()

    for line in sys.stdin:
        try:
            req = json.loads(line)
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
                "capabilities": {"tools": {}},
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
        with open(SETTINGS) as f:
            settings = json.load(f)
    except FileNotFoundError:
        settings = {}
    except ValueError as e:
        print(f"✗ {tilde(SETTINGS)} не читается как JSON ({e}), хуки Claude Code не трогаю")
        return
    hooks = settings.setdefault("hooks", {})
    for event, cmd, run_async, matcher in HOOKS:
        entries = [e for e in hooks.get(event, [])
                   if not any(is_hook(h.get("command", ""), cmd) for h in e.get("hooks", []))]
        if install:
            hook = {"type": "command", "command": hook_command(cmd), "timeout": 10}
            if run_async:
                hook["async"] = True
            entries.append(dict({"matcher": matcher} if matcher else {}, hooks=[hook]))
        if entries:
            hooks[event] = entries
        else:
            hooks.pop(event, None)
    if not hooks:
        settings.pop("hooks")
    line = settings.get("statusLine")
    command = str(line.get("command", "")) if isinstance(line, dict) else ""
    ours = is_hook(command, STATUS_CMD) or "promptsill" in command
    promptsill = promptsill_path()
    if install and promptsill and (not line or is_hook(command, STATUS_CMD)):
        settings["statusLine"] = {"type": "command", "command": shlex.quote(promptsill),
                                  "padding": 0, "refreshInterval": 10}
    elif install and is_hook(command, STATUS_CMD):
        # без promptsill оставляем заглушку statusline, но уже claudemeister, а не mnrh claude
        settings["statusLine"] = dict(line, command=hook_command(STATUS_CMD))
    elif not install and ours:
        settings.pop("statusLine")
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
    tmp = SETTINGS + ".mnrh-tmp"
    with open(tmp, "w") as f:
        json.dump(settings, f, ensure_ascii=False, indent=2)
        f.write("\n")
    os.replace(tmp, SETTINGS)
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


if __name__ == "__main__":
    cmd, rest = (sys.argv[1], sys.argv[2:]) if len(sys.argv) > 1 else ("restart", [])
    if cmd == "mcp":
        mcp()
    elif cmd == "notice":
        sys.exit(notice())
    elif cmd == "session-end":
        sys.exit(session_end())
    elif cmd == "setup":
        sys.exit(setup(rest))
    elif cmd == "slim":
        sys.exit(cli_slim(rest))
    else:
        sys.exit(cli(rest, forget=cmd == "forget", fresh=cmd == "fresh"))
