#!/usr/bin/env python3
import json
import os
import re
import shlex
import signal
import subprocess
import sys
import time
import uuid

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from mnrhlib import CM, HOME, MNRH, settings_path, tilde  # noqa: E402
from claude_proc import (CLAUDE_BIN, SHELLS, RestartError, argv_of, caffeinate_args, claude_version,  # noqa: E402
                         drop_name, find_claude, launcher, ps, resume_flags, session_file, session_path)
import claude_search  # noqa: E402

LOG = os.path.join(HOME, "Library", "Logs", "mnrh-claude-restart.log")
SLIM_MIN = 5 << 20
QUEUE = os.path.join(HOME, ".cache", "mnrh", "restart")
NOTICES = os.path.join(HOME, ".cache", "mnrh", "notice")
NOTICE_TTL = 300
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
    from setup_cmd import ZSHRC, is_zsh_hook, zshrc_lines
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


if __name__ == "__main__":
    # mcp_server и setup_cmd импортируют этот модуль по имени: без этого он загрузился бы второй раз
    sys.modules.setdefault("claude_restart", sys.modules[__name__])
    cmd, rest = (sys.argv[1], sys.argv[2:]) if len(sys.argv) > 1 else ("restart", [])
    if cmd == "mcp":
        import mcp_server
        mcp_server.mcp()
    elif cmd == "notice":
        sys.exit(notice())
    elif cmd == "session-end":
        sys.exit(session_end())
    elif cmd == "setup":
        import setup_cmd
        sys.exit(setup_cmd.setup(rest))
    elif cmd == "slim":
        sys.exit(cli_slim(rest))
    else:
        sys.exit(cli(rest, forget=cmd == "forget", fresh=cmd == "fresh"))
