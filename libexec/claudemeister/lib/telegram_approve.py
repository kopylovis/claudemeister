"""Разрешить или отклонить запрос Claude Code из Telegram, пока тебя нет за Mac.

Хук PermissionRequest (claudemeister approve-hook) блокирующий. Если ответ из Telegram включен
(telegram_approve=on), бот подключен и ты не за Mac, он присылает в Telegram запрос с кнопками
«Разрешить» и «Отклонить» и ждет нажатия. Если ты вернулся к Mac, время вышло или что-то пошло
не так, хук выходит без вывода, и Claude Code спрашивает как обычно.

Кнопки читает getUpdates. Если запросов несколько (параллельные сессии или субагенты), опрашивает
Telegram только тот хук, что держит блокировку, и раскладывает ответы по файлам <id>.answer,
чтобы один хук не подтверждал и не терял нажатия, которые ждет другой.
"""
import contextlib
import fcntl
import html
import json
import os
import re
import secrets
import signal
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from mnrhlib import CACHE, HOME, HOOK_TIMEOUTS, UUID_RE, read_config, tilde, write_config, write_json  # noqa: E402
import telegram  # noqa: E402

DIR = os.path.join(CACHE, "notify", "approve")
# Таймаут хука в settings.json. Ждем меньше, чтобы успеть убрать кнопки и выйти самим.
HOOK_TIMEOUT = HOOK_TIMEOUTS["approve-hook"]
WAIT = HOOK_TIMEOUT - 30
POLL = 5
# Эти инструменты идут через запрос разрешения, но на деле это вопрос или план: разрешить их вслепую нельзя.
SKIP_TOOLS = {"AskUserQuestion", "ExitPlanMode", "EnterPlanMode"}
SHOW = 600
DATA_RE = re.compile(r"^cm1:([0-9a-f]{16}):([yn])$")
SECRET_RE = re.compile(r"(?i)((?:token|secret|password|passwd|api[_-]?key|authorization|credentials?)"
                       r"[\w-]*[\"']?\s*[:=]\s*[\"']?)([^\s\"'&;|]{4,})")
URL_CRED_RE = re.compile(r"(://[^/\s:@]+:)[^@\s/]+@")
SCHEME_RE = re.compile(r"(?i)\b(bearer|basic|token)\s+[A-Za-z0-9._~+/=-]{4,}")


def enabled():
    return read_config().get("telegram_approve", "off") == "on"


def clean(text):
    text = re.sub(r"[\x00-\x08\x0b-\x1f\x7f]", "", str(text))
    text = URL_CRED_RE.sub(r"\1•••@", text)
    text = SCHEME_RE.sub(r"\1 •••", text)
    return SECRET_RE.sub(r"\1•••", text)


def short(text, n=SHOW):
    text = clean(text).strip()
    if len(text) <= n:
        return text, False
    return text[:n] + "…", True


def render(tool, inp):
    """(что показать моноширинно, пояснение, обрезано ли)."""
    inp = inp if isinstance(inp, dict) else {}
    if tool == "Bash":
        body, cut = short(inp.get("command", ""))
        note = short(inp.get("description", ""), 200)[0]
        return body, note, cut
    for key in ("file_path", "notebook_path", "path"):
        if isinstance(inp.get(key), str):
            body, cut = short(tilde(inp[key]))
            return body, "", cut
    if tool == "WebFetch" and isinstance(inp.get("url"), str):
        body, cut = short(inp["url"])
        return body, "", cut
    if tool == "WebSearch" and isinstance(inp.get("query"), str):
        body, cut = short(inp["query"])
        return body, "", cut
    body, cut = short(json.dumps(inp, ensure_ascii=False))
    return body, "", cut


def project_name(cwd):
    cwd = (cwd or "").rstrip("/")
    return "~" if cwd == HOME else os.path.basename(cwd) or "Claude"


def guard_warning(tool, inp, cwd):
    """Почему guard придержал этот вызов (ключи и секреты), если придержал: пусть это будет видно и в Telegram."""
    try:
        import claude_guard
        found = claude_guard.check(tool, inp if isinstance(inp, dict) else {}, cwd)
    except Exception:
        return ""
    return found[1] if found else ""


def message(payload, title=""):
    tool = str(payload.get("tool_name") or "?")
    body, note, cut = render(tool, payload.get("tool_input"))
    parts = [f"<b>{html.escape(project_name(payload.get('cwd')))}: нужно разрешение</b>"]
    if title:
        parts.append(f"<i>{html.escape(title)}</i>")
    if payload.get("agent_type"):
        parts.append(f"Субагент: {html.escape(str(payload['agent_type']))}")
    parts.append(f"Инструмент: <b>{html.escape(tool)}</b>")
    warn = guard_warning(tool, payload.get("tool_input"), payload.get("cwd"))
    if warn:
        parts.append(f"⚠️ claudemeister guard: {html.escape(warn)}")
    if body:
        parts.append(f"<pre>{html.escape(body)}</pre>")
    if note:
        parts.append(html.escape(note))
    if "•••" in body:
        parts.append("Похожее на пароли и токены скрыто (•••).")
    if cut:
        parts.append("⚠️ Показано не целиком. Если не уверен, отклони или ответь на Mac.")
    return "\n".join(parts)


def keyboard(rid):
    return json.dumps({"inline_keyboard": [[{"text": "Разрешить", "callback_data": f"cm1:{rid}:y"},
                                            {"text": "Отклонить", "callback_data": f"cm1:{rid}:n"}]]})


def path(rid, ext):
    return os.path.join(DIR, f"{rid}.{ext}")


def read_json(p):
    try:
        with open(p) as f:
            data = json.load(f)
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def pending_for(sid):
    """Ждет ли сейчас какой-нибудь запрос этой сессии ответа из Telegram (тогда notify-hook туда не шлет)."""
    try:
        names = os.listdir(DIR)
    except OSError:
        return False
    cutoff = time.time() - HOOK_TIMEOUT
    for name in names:
        if name.endswith(".pending"):
            data = read_json(os.path.join(DIR, name))
            if data.get("sid") == sid and data.get("at", 0) > cutoff:
                return True
    return False


def accept(cb, chat_id):
    """Ответ из нажатия, если оно годится: (rid, 'allow'|'deny') или None."""
    m = DATA_RE.match(str(cb.get("data") or ""))
    msg = cb.get("message") or {}
    if not m or str((cb.get("from") or {}).get("id")) != chat_id or str((msg.get("chat") or {}).get("id")) != chat_id:
        return None
    rid = m.group(1)
    want = read_json(path(rid, "pending"))
    if not want or want.get("message_id") != msg.get("message_id"):
        return None
    return rid, "allow" if m.group(2) == "y" else "deny"


def poll_once(chat_id, timeout):
    """Один запрос getUpdates; разложить нажатия по файлам ответа. Вызывать под блокировкой.
    Без offset Telegram отдает все неподтвержденные обновления, поэтому свой offset между запусками не храним:
    после разбора сразу подтверждаем прочитанное. Сохраненный offset мог бы оказаться больше новых номеров
    (после недели тишины Telegram начинает их со случайного числа) и молча съесть нажатия."""
    updates = telegram.call("getUpdates", wait=timeout + 15, timeout=timeout, allowed_updates='["callback_query"]')
    last = None
    for u in updates:
        last = max(last or 0, u.get("update_id", 0))
        cb = u.get("callback_query")
        if not cb:
            continue
        got = accept(cb, chat_id)
        if got:
            rid, answer = got
            write_json(path(rid, "answer"), {"answer": answer})
            text = "Разрешено" if answer == "allow" else "Отклонено"
        else:
            text = "Этот запрос уже не ждёт ответа"
        with contextlib.suppress(telegram.TelegramError):
            telegram.call("answerCallbackQuery", callback_query_id=cb.get("id"), text=text)
    if last is not None:
        telegram.call("getUpdates", offset=last + 1, timeout=0, allowed_updates='["callback_query"]')


@contextlib.contextmanager
def poll_lock():
    f = open(os.path.join(DIR, "poll.lock"), "a")
    try:
        try:
            fcntl.flock(f, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            yield False
            return
        yield True
    finally:
        f.close()


def wait_answer(rid, chat_id, deadline):
    """'allow', 'deny', 'back' (ты вернулся к Mac) или 'timeout'."""
    while True:
        answer = read_json(path(rid, "answer")).get("answer")
        if answer in ("allow", "deny"):
            return answer
        if not telegram.away():
            return "back"
        left = deadline - time.time()
        if left <= 1:
            return "timeout"
        with poll_lock() as mine:
            if mine:
                try:
                    poll_once(chat_id, int(min(POLL, left - 1)))
                    continue
                except telegram.TelegramError as e:
                    # сеть пропала на минуту: ждем дальше, а не бросаем запрос
                    log_error(str(e))
                    time.sleep(3)
                    continue
        time.sleep(1)


def decision(answer):
    d = {"behavior": answer}
    if answer == "deny":
        d["message"] = "Пользователь отклонил запрос в Telegram"
    return {"hookSpecificOutput": {"hookEventName": "PermissionRequest", "decision": d}}


FINALE = {"allow": "✅ Разрешено", "deny": "❌ Отклонено", "back": "Ты вернулся к Mac — ответь там",
          "timeout": "Время вышло — ответь на Mac", "gone": "Ответ уже не нужен",
          "error": "Не дождался ответа из-за ошибки — ответь на Mac"}


def finish(msg_id, text, outcome):
    with contextlib.suppress(telegram.TelegramError):
        telegram.call("editMessageText", chat_id=telegram.chat(), message_id=msg_id, parse_mode="HTML",
                      text=f"{text}\n\n{FINALE[outcome]}", disable_web_page_preview="true")


def session_title(payload):
    try:
        import claude_notify
        return claude_notify.session_title(payload.get("session_id"), payload.get("transcript_path"))
    except Exception:
        return ""


def log_error(text):
    with contextlib.suppress(OSError):
        os.makedirs(os.path.dirname(DIR), exist_ok=True)
        with open(os.path.join(os.path.dirname(DIR), "errors.log"), "a") as f:
            f.write(f"{time.strftime('%Y-%m-%d %H:%M:%S')} approve: {text}\n")


def wanted(payload):
    """Брать ли этот запрос в Telegram. Сначала дешевые проверки: хук стоит на каждом запросе разрешения."""
    if os.environ.get("MNRH_CHILD") == "1" or not isinstance(payload, dict):
        return False
    if payload.get("hook_event_name") != "PermissionRequest" or payload.get("tool_name") in SKIP_TOOLS:
        return False
    if not UUID_RE.match(str(payload.get("session_id") or "")):
        return False
    if not enabled() or not telegram.chat():
        return False
    return telegram.away() and bool(telegram.token())


class Gone(Exception):
    pass


def _gone(*_):
    raise Gone()


def hook(stdin=sys.stdin, out=sys.stdout):
    try:
        payload = json.load(stdin)
    except ValueError:
        return 0
    if not wanted(payload):
        return 0
    deadline = time.time() + WAIT
    os.makedirs(DIR, exist_ok=True)
    rid = secrets.token_hex(8)
    chat_id = str(telegram.chat())
    text = message(payload, session_title(payload))
    msg_id = None
    outcome = "error"
    # Claude Code снимает хук, если ответ пришел другим путем (в терминале или из приложения Claude)
    for sig in (signal.SIGTERM, signal.SIGHUP, signal.SIGINT):
        signal.signal(sig, _gone)
    try:
        sent = telegram.call("sendMessage", chat_id=chat_id, text=text, parse_mode="HTML",
                             disable_web_page_preview="true", reply_markup=keyboard(rid))
        msg_id = sent.get("message_id")
        write_json(path(rid, "pending"), {"sid": payload["session_id"], "message_id": msg_id, "at": time.time()})
        outcome = wait_answer(rid, chat_id, deadline)
    except Gone:
        outcome = "gone"
    except telegram.TelegramError as e:
        log_error(str(e))
    finally:
        for sig in (signal.SIGTERM, signal.SIGHUP, signal.SIGINT):
            signal.signal(sig, signal.SIG_DFL)
        for ext in ("pending", "answer"):
            with contextlib.suppress(OSError):
                os.remove(path(rid, ext))
        if msg_id:
            finish(msg_id, text, outcome)
    if outcome in ("allow", "deny"):
        json.dump(decision(outcome), out)
        out.write("\n")
    return 0


def hook_installed():
    import claude_settings
    try:
        return not claude_settings.missing(claude_settings.load(), claude_settings.rows("approve-hook"))
    except (OSError, ValueError):
        return False


def install_hook():
    """Поставить хук PermissionRequest, если его нет; остальное в settings.json не трогает."""
    import claude_settings
    try:
        data = claude_settings.load()
        added = claude_settings.add_missing_hooks(data, claude_settings.rows("approve-hook"))
    except (OSError, ValueError) as e:
        print(f"✗ {tilde(claude_settings.PATH)} не читается ({e}), хук не поставил: claudemeister setup")
        return False
    if added:
        claude_settings.save_atomic(data)
        print(f"✓ хук PermissionRequest → {tilde(claude_settings.PATH)}")
    return True


def describe():
    if not enabled():
        return "Разрешения из Telegram: выключены — claudemeister notify telegram approve on"
    tail = "" if hook_installed() else " (хук PermissionRequest не стоит — claudemeister setup)"
    return "Разрешения из Telegram: кнопки «Разрешить» и «Отклонить», когда ты не за Mac" + tail


def main(args):
    value = args[0] if args else ""
    if value == "on":
        if not telegram.configured():
            print("Сначала подключи бота: claudemeister notify telegram")
            return 1
        write_config(telegram_approve="on")
        install_hook()
        print("Когда ты не за Mac, запросы разрешения приходят в Telegram с кнопками «Разрешить» и «Отклонить».")
        print(f"Если ты вернулся к Mac или прошло {WAIT // 60} мин, Claude спросит как обычно.")
        print("Нажать кнопку может любой, у кого открыт твой Telegram. Выключить: claudemeister notify telegram approve off")
        return 0
    if value == "off":
        write_config(telegram_approve="off")
        print("Разрешения из Telegram выключены, Claude спрашивает только на Mac")
        return 0
    print(describe())
    print("claudemeister notify telegram approve on | off")
    return 0 if not value else 2


if __name__ == "__main__":
    sys.exit(hook())
