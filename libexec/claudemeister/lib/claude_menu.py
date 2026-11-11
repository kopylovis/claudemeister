"""Меню действий claudemeister: claudemeister без аргументов в терминале.

Действие запускается как claudemeister <аргументы>, полная команда печатается, чтобы в следующий раз набрать ее
сразу. После действия можно вернуться в меню. Режимы (humanize, guard, buildlog, уведомления, крышка) по Enter
переключаются на месте: меню остается и показывает новое состояние. Язык берется из mnrhlib.lang().
"""
import os
import shlex
import signal
import subprocess
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from menu import choice, pick  # noqa: E402
from mnrhlib import CM, RESTART_QUEUE, tr  # noqa: E402

# (команда, по-русски, по-английски). Группа: команда None. Режим: "mode <имя>", Enter переключает его.
ITEMS = [
    (None, "Сессии", "Sessions"),
    ("open", "Открыть проект", "Open a project"),
    ("root", "Открыть в домашней папке", "Open in the home folder"),
    ("sessions", "Сохраненные сессии: продолжить, перенести, удалить", "Saved sessions: resume, move, delete"),
    ("ps", "Запущенные сессии: кто ждет ответа", "Running sessions: who is waiting for you"),
    ("restore", "Вернуть сессии после перезагрузки или сбоя", "Reopen sessions after a reboot or crash"),
    ("usage", "Расход: токены и время по проектам", "Usage: tokens and time per project"),
    (None, "Режимы, Enter переключает", "Modes, Enter switches"),
    ("mode humanize", "Писать без признаков нейросети", "Write without AI tells"),
    ("mode guard", "Спрашивать перед опасными командами", "Ask before risky commands"),
    ("mode buildlog", "Выжимка длинного вывода сборок", "Digest long build output"),
    ("mode notify", "Уведомлять, когда Claude ждет", "Notify when Claude is waiting"),
    ("mode lid", "Не засыпать с закрытой крышкой", "Stay awake with the lid closed"),
    (None, "Прокси через свой VPS", "Proxy through your VPS"),
    ("proxy", "Состояние", "Status"),
    ("proxy test", "Проверить выход", "Check the exit"),
    ("proxy setup", "Настроить по ссылке подписки", "Set up from a subscription link"),
    ("proxy restart", "Перезапустить mihomo", "Restart mihomo"),
    ("proxy update", "Обновить mihomo", "Update mihomo"),
    (None, "Установка", "Install"),
    ("setup", "Настроить Claude Code: команды, хуки, MCP-сервер", "Set up Claude Code: commands, hooks, MCP server"),
    ("doctor", "Проверить установку", "Check the install"),
]


def mode_on(name):
    """Включен ли режим сейчас. Модули грузятся по требованию: меню не должно падать из-за одного из них."""
    try:
        if name == "humanize":
            import claude_humanize
            return claude_humanize.enabled()
        if name == "guard":
            import claude_guard
            return claude_guard.enabled()
        if name == "buildlog":
            import claude_buildlog
            return claude_buildlog.enabled()
        if name == "notify":
            import claude_notify
            return claude_notify.enabled()
        if name == "lid":
            import claude_lid
            return claude_lid.active()
    except Exception:
        pass
    return False


def dim(text):
    return f"\x1b[2m{text}\x1b[0m"


def rows():
    """Строки для pick: (ключ для поиска, текст). У режима после названия [вкл] или [выкл] и команда, которая
    его переключит."""
    on, off = tr("вкл", "on"), tr("выкл", "off")
    entries = []
    for key, ru, en in ITEMS:
        label = tr(ru, en)
        if key and key.startswith("mode "):
            name = key.split()[1]
            state = mode_on(name)
            flag = f"[{on if state else off}]"
            entries.append((key, label, flag, state, f"{name} {'off' if state else 'on'}"))
        else:
            entries.append((key, label, "", False, key))
    width = max(len(label) for key, label, _, _, _ in entries if key) + 2
    flags = max(len(f"[{on}]"), len(f"[{off}]")) + 2
    out = []
    for key, label, flag, state, cmd in entries:
        if key is None:
            out.append((None, label))
            continue
        mark = (f"\x1b[32m{flag}\x1b[0m" if state else dim(flag)) + " " * (flags - len(flag))
        out.append((f"{key} {label}", f"{label.ljust(width)}{mark}{dim(f'claudemeister {cmd}')}"))
    return out


def pick_action(at=1, note=""):
    title = "\x1b[1;35mclaudemeister\x1b[0m" + (f"  {dim(note)}" if note else "")
    return pick(rows(), title=title, esc=tr("выход", "quit"), default=at)


def restart_pending():
    """/restart уже положил задание для этой вкладки: меню больше не показываем, вкладку займет Claude."""
    try:
        job = os.path.join(RESTART_QUEUE, os.path.basename(os.ttyname(0)))
        return time.time() - os.path.getmtime(job) < 120
    except OSError:
        return False


def again(rc):
    if restart_pending():
        return False
    if rc == 130:
        return True
    head = (f"\x1b[32m✓ {tr('Готово', 'Done')}\x1b[0m" if rc == 0 else
            f"\x1b[33m! {tr(f'Завершилось с кодом {rc}', f'Exited with code {rc}')}\x1b[0m")
    try:
        print()
        return choice(head, [("menu", tr("↩ В меню", "↩ Menu"), ""), ("quit", tr("Выйти", "Quit"), "qй")],
                      default=0, back="quit", esc=tr("выход", "quit")) == "menu"
    except OSError:
        return False


def run(args):
    # Ctrl-C прерывает действие, а не меню; обработчик на Python в запущенной программе сбрасывается
    old = signal.signal(signal.SIGINT, lambda *_: None)
    try:
        rc = subprocess.run([CM] + args).returncode
    finally:
        signal.signal(signal.SIGINT, old)
    return 128 - rc if rc < 0 else rc


def switch(name):
    """Переключить режим на месте. Возвращает строку для заголовка меню."""
    want = "off" if mode_on(name) else "on"
    import claude_lid
    if name == "lid" and want == "on" and not claude_lid.ready():
        # правила sudoers еще нет: lid on поставит его и спросит пароль sudo, это надо видеть
        print(dim("$ claudemeister lid on"), flush=True)
        run(["lid", "on"])
    else:
        done = subprocess.run([CM, name, want], capture_output=True, text=True)
        if done.returncode != 0:
            last = (done.stderr or done.stdout).strip().splitlines()
            return tr(f"{name}: не переключилось", f"{name}: did not switch") + (f" ({last[-1][:60]})" if last else "")
    return f"{name}: " + (tr("включено", "on") if mode_on(name) else tr("выключено", "off"))


def main():
    at, note = 1, ""
    while True:
        at = pick_action(at, note)
        if at is None:
            return 0
        note = ""
        key = ITEMS[at][0]
        if key.startswith("mode "):
            note = switch(key.split()[1])
            continue
        print(dim(f"$ claudemeister {key}"), flush=True)
        if not again(run(shlex.split(key))):
            return 0


if __name__ == "__main__":
    sys.exit(main())
