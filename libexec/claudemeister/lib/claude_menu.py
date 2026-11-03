"""Меню действий claudemeister: claudemeister без аргументов в терминале.

Выбранное действие запускается как claudemeister <аргументы>, полная команда печатается, чтобы в следующий раз
набрать ее сразу. После действия можно вернуться в меню.
"""
import os
import shlex
import signal
import subprocess
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from menu import choice, pick  # noqa: E402
from mnrhlib import CM, RESTART_QUEUE  # noqa: E402

ACTIONS = [
    ("open", "выбрать проект"),
    ("root", "в домашнем каталоге"),
    ("sessions", "сохранённые сессии"),
    ("proxy", "прокси через VPS: статус"),
    ("proxy test", "прокси через VPS: проверить выход"),
    ("proxy setup", "прокси через VPS: настроить по подписке"),
    ("humanize switch", "humanization: включить или выключить"),
    ("setup", "настроить Claude Code: команды, хуки, MCP-сервер"),
    ("doctor", "проверить установку claudemeister"),
]


def dim(text):
    return f"\x1b[2m{text}\x1b[0m"


def pick_action(at=0):
    width = max(len(label) for _, label in ACTIONS) + 3
    items = [(f"{a} {label}", f"{label.ljust(width)}{dim(f'claudemeister {a}')}") for a, label in ACTIONS]
    return pick(items, title="\x1b[1;35mclaudemeister\x1b[0m", esc="выход", default=at)


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
    head = "\x1b[32m✓ Готово\x1b[0m" if rc == 0 else f"\x1b[33m! Завершилось с кодом {rc}\x1b[0m"
    try:
        print()
        return choice(head, [("menu", "↩ В меню", ""), ("quit", "Выйти", "qй")],
                      default=0, back="quit", esc="выход") == "menu"
    except OSError:
        return False


def main():
    at = 0
    while True:
        at = pick_action(at)
        if at is None:
            return 0
        args = shlex.split(ACTIONS[at][0])
        print(dim(f"$ claudemeister {ACTIONS[at][0]}"), flush=True)
        # Ctrl-C прерывает действие, а не меню; обработчик на Python в запущенной программе сбрасывается
        old = signal.signal(signal.SIGINT, lambda *_: None)
        try:
            rc = subprocess.run([CM] + args).returncode
        finally:
            signal.signal(signal.SIGINT, old)
        if rc < 0:
            rc = 128 - rc
        if not again(rc):
            return 0


if __name__ == "__main__":
    sys.exit(main())
