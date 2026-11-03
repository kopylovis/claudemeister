"""Тексты команд Claude Code (~/.claude/commands/*.md), которые ставит claudemeister setup."""

EXTRA_COMMANDS = ["todo", "where", "release-notes", "parallel", "shot", "ask", "humanize"]


EXTRA_SLASH = {
    "todo": """---
description: Бэклог проекта между сессиями: /todo — список, /todo <текст> — добавить, /todo done <N> — сделано
allowed-tools: Bash(claudemeister todo:*)
---
!`claudemeister todo $ARGUMENTS`

Покажи пользователю результат выше как есть, без своих действий и пояснений.
""",
    "where": """---
description: Где мы остановились: git, прошлая сессия в этом проекте, бэклог
allowed-tools: Bash(claudemeister brief:*)
---
!`claudemeister brief`

По справке выше коротко скажи пользователю, где остановились (что было сделано и в каком всё состоянии) и
что логично делать дальше — 2–5 пунктов. Ничего не запускай и не меняй.
""",
    "release-notes": """---
description: Заметки к релизу «Что нового» на русском и английском для fastlane; аргументы — проект и/или --since <тег>
allowed-tools: mcp__claudemeister__release_notes_context, mcp__claudemeister__release_notes_write, mcp__claudemeister__session_search, mcp__claudemeister__session_read
---
Подготовь заметки к релизу. Аргументы пользователя: «$ARGUMENTS» (может быть имя проекта и --since <тег или коммит>).

1. Вызови release_notes_context (project и since — из аргументов, если они есть).
2. Если по коммитам неясно, что изменилось для пользователя, загляни в сессии через session_search/session_read.
3. Напиши «Что нового» для ru-RU и en-US: для пользователей, по делу, без технических подробностей, не длиннее
   500 символов (тогда текст подойдёт и для Google Play, и для App Store, RuStore, Firebase).
4. Покажи оба текста и спроси, сохранить ли. После согласия вызови release_notes_write и покажи команду для fastlane.
""",
    "parallel": """---
description: Сделать задачу параллельно: отдельная ветка и worktree, второй Claude в новом окне; list — что идёт
allowed-tools: mcp__claudemeister__parallel_task, mcp__claudemeister__parallel_list
---
Аргументы: «$ARGUMENTS».

Если аргументов нет или это слово list — вызови parallel_list и покажи результат.
Иначе вызови parallel_task с task — задачей из аргументов, переписанной так, чтобы она была понятна без контекста
этой сессии (добавь нужные факты отсюда, если задача на них опирается). Потом коротко скажи пользователю, где
открылся второй Claude, ссылку Remote Control, если она есть, и как потом закончить: через PR
(claudemeister parallel pr <ветка>: пуш, PR, CI, слияние и уборка worktree), влить локально или выбросить ветку.
""",
    "shot": """---
description: Снимок окна приложения (Chrome, Safari, Simulator…): /shot <приложение> [вопрос]; без аргументов — список окон
allowed-tools: Bash(claudemeister shot:*), Read
---
!`claudemeister shot $1`

Если выше путь к PNG — открой его инструментом Read и ответь на вопрос пользователя о снимке: «$ARGUMENTS»
(первое слово — приложение). Если вопроса нет — коротко опиши, что видно. Если выше список окон или ошибка —
покажи их пользователю.
""",
    "ask": """---
description: Спросить про другой проект: /ask <проект> <вопрос> — ответит отдельный Claude, только чтение
allowed-tools: mcp__claudemeister__ask_project
---
Аргументы: «$ARGUMENTS». Первое слово — проект, остальное — вопрос.

Вызови ask_project с этим проектом и вопросом. Если вопрос опирается на контекст этой сессии, допиши в него
нужные факты, чтобы он был понятен без неё. Ответ перескажи пользователю коротко, сохранив пути и код.
""",
    "humanize": """---
description: claudemeister humanization: писать без признаков нейросети; /humanize переключает, on, off, status
allowed-tools: Bash(claudemeister humanize:*)
---
!`claudemeister humanize switch $ARGUMENTS`

Если выше написано «включена» и есть свод правил, с этого ответа строго следуй ему во всём, что пишешь людям.
Если «выключена», правила больше не обязательны. Ответь пользователю одной короткой фразой, в каком состоянии режим.
""",
}


SLIM_SLASH = """---
description: Сжать эту сессию (старые скриншоты и длинные выводы) и открыть её снова; list — другие сессии
allowed-tools: Bash(claudemeister slim:*)
---
!`claudemeister slim $ARGUMENTS`

Если выше написано «Сжимаю», Claude Code сейчас закроется сам: ничего не делай и ответь одним словом: «Сжимаю».
Иначе коротко перескажи пользователю, что написано выше, без своих действий.
"""


FORGET_SLASH = """---
description: Забыть эту сессию без следа и открыть чистый Claude Code в той же папке
allowed-tools: Bash(claudemeister forget:*)
---
!`claudemeister forget $ARGUMENTS`

Эта сессия сейчас удалится, и Claude Code откроется заново. Ничего не делай и ответь одним словом: «Забываю».
"""


FRESH_SLASH = """---
description: Новая сессия без названия и с пустым контекстом; эта остаётся в /resume
allowed-tools: Bash(claudemeister fresh:*)
---
!`claudemeister fresh $ARGUMENTS`

Claude Code сейчас закроется и откроется с новой сессией. Ничего не делай и ответь одним словом: «Открываю».
"""


SLASH = """---
description: Перезапустить Claude Code с этой же сессией (с claude update перед запуском)
allowed-tools: Bash(claudemeister restart:*)
---
!`claudemeister restart $ARGUMENTS`

Claude Code сейчас перезапустится сам. Ничего не делай и ответь одним словом: «Перезапускаюсь».
"""
