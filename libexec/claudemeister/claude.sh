#!/usr/bin/env bash
set -uo pipefail

CONF="${XDG_CONFIG_HOME:-$HOME/.config}/mnrh/config"
LIB="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/lib"

projects_dir() {
  local d="${MNRH_PROJECTS:-}"
  [ -z "$d" ] && [ -f "$CONF" ] && d=$(sed -n 's/^projects=//p' "$CONF" | head -1)
  d="${d:-$HOME/Developer}"
  d="${d/#\~/$HOME}"
  echo "$d"
}
CLAUDE_BIN="${MNRH_CLAUDE_BIN:-claude}"
MENU="$LIB/menu.py"

help() {
  cat <<'EOF'
Запуск
  claudemeister                     меню (MNRH_NO_MENU=1 сразу к выбору проекта)
  claudemeister open                выбрать проект в папке проектов
  claudemeister <имя>               сразу в проекте (начала имени хватит) или в папке по пути
  claudemeister root                в домашней папке
  claudemeister ... -k | -n         caffeinate или обычная сессия, без вопроса
  claudemeister ... -x | -d         через свой VPS или напрямую, без вопроса
  claudemeister ... --new           новая сессия, не предлагать сохраненные
  claudemeister ... -i              без записи на диск: ни переписки, ни истории промптов
  claudemeister ... -- <аргументы>  аргументы для claude: claudemeister kcalm -- --continue
  claudemeister sessions            сохраненные сессии: продолжить, перенести, удалить (sessions -h)
  claudemeister ps                  запущенные сессии: кто ждет ответа, перейти во вкладку
  claudemeister restore             вернуть сессии, открытые до перезагрузки или сбоя
  claudemeister usage               токены и активное время по проектам (--days N, --project P)

Изнутри Claude Code (они же /restart, /forget, /fresh, /slim)
  claudemeister restart             закрыть и открыть эту же сессию, с claude update
  claudemeister forget              удалить эту сессию без следа и открыть чистую
  claudemeister fresh               новая сессия без названия, эта остается в /resume
  claudemeister slim                сжать эту сессию и открыть снова

Режимы (on, off)
  claudemeister humanize            правила письма без признаков нейросети; check <файл>, rules
  claudemeister guard               вопрос перед чтением ключей и опасными командами; test, log
  claudemeister buildlog            выжимка длинного вывода сборок для Claude; test <файл>
  claudemeister notify              уведомления, когда Claude ждет; after <сек>, test, telegram
  claudemeister lid                 не засыпать с закрытой крышкой; setup, release, remove

Прокси через свой VPS
  claudemeister proxy               состояние; setup, test, update, remove (proxy -h)

Инструменты проекта (Claude зовет их и сам)
  claudemeister brief               где мы остановились: git, прошлая сессия, бэклог (/where)
  claudemeister todo                бэклог проекта между сессиями (/todo, todo -h)
  claudemeister parallel "<…>"      задача в своей ветке и worktree, во втором окне (/parallel)
  claudemeister ask <пр> <…>        спросить отдельный Claude про другой проект (/ask)
  claudemeister release-notes       материал для "Что нового" к релизу (/release-notes)
  claudemeister shot                снимок окна приложения (/shot)
  claudemeister deps                устаревшие зависимости Gradle в gradle/libs.versions.toml

Установка
  claudemeister setup               команды, хуки, MCP-сервер, хук zsh, прокси, promptsill (setup -h)
  claudemeister doctor              проверить установку
  claudemeister --version           версия

caffeinate (по умолчанию) не даёт Mac уснуть, пока идёт сессия:
удобно для долгих задач и Remote Control с телефона. Экран при этом гаснуть может.
С закрытой крышкой на батарее Mac все равно уснет, caffeinate этого не отменяет.
claudemeister lid setup разрешит не засыпать и с закрытой крышкой (спросит пароль sudo).
Папка проектов: ~/Developer, строка projects=<путь> в ~/.config/mnrh/config или MNRH_PROJECTS=<путь>.
EOF
}

case "${1:-}" in
  open) shift ;;
  proxy) shift; exec /usr/bin/python3 "$(dirname "$MENU")/claude_proxy.py" "$@" ;;
  sessions) shift; exec /usr/bin/python3 "$(dirname "$MENU")/claude_sessions.py" "$@" ;;
  usage) shift; exec /usr/bin/python3 "$(dirname "$MENU")/claude_usage.py" "$@" ;;
  brief) exec /usr/bin/python3 "$(dirname "$MENU")/claude_brief.py" "$@" ;;
  ps) shift; exec /usr/bin/python3 "$(dirname "$MENU")/claude_live.py" ps "$@" ;;
  restore) exec /usr/bin/python3 "$(dirname "$MENU")/claude_live.py" "$@" ;;
  doctor) shift; exec /usr/bin/python3 "$(dirname "$MENU")/claude_doctor.py" "$@" ;;
  lid) shift; exec /usr/bin/python3 "$(dirname "$MENU")/claude_lid.py" "$@" ;;
  parallel) exec /usr/bin/python3 "$(dirname "$MENU")/claude_parallel.py" "$@" ;;
  ask) exec /usr/bin/python3 "$(dirname "$MENU")/claude_ask.py" "$@" ;;
  release-notes) exec /usr/bin/python3 "$(dirname "$MENU")/release_notes.py" "$@" ;;
  statusline) shift; exec /usr/bin/python3 "$(dirname "$MENU")/claude_status.py" "$@" ;;
  humanize|humanize-hook) exec /usr/bin/python3 "$(dirname "$MENU")/claude_humanize.py" "$@" ;;
  buildlog|buildlog-hook) exec /usr/bin/python3 "$(dirname "$MENU")/claude_buildlog.py" "$@" ;;
  guard|guard-hook) exec /usr/bin/python3 "$(dirname "$MENU")/claude_guard.py" "$@" ;;
  approve-hook) grep -qsx "telegram_approve=on" "$CONF" || exit 0; exec /usr/bin/python3 "$(dirname "$MENU")/telegram_approve.py" "$@" ;;
  notify|notify-hook|focus) exec /usr/bin/python3 "$(dirname "$MENU")/claude_notify.py" "$@" ;;
  restart|forget|fresh|slim|mcp|setup|notice|session-end) exec /usr/bin/python3 "$(dirname "$MENU")/claude_restart.py" "$@" ;;
esac

target="" mode="" net="" new=0 passthrough=()
while [ $# -gt 0 ]; do
  case "$1" in
    -h|--help) help; exit 0 ;;
    -n|--normal) mode=normal ;;
    -k|--caffeinate) mode=caffeinate ;;
    -x|--proxy) net=vps ;;
    -d|--direct) net=direct ;;
    --new) new=1 ;;
    -i|--incognito) new=1; export CLAUDE_CODE_SKIP_PROMPT_HISTORY=1 ;;
    --) shift; passthrough=("$@"); break ;;
    -*) echo "claudemeister: неизвестный флаг $1" >&2; exit 2 ;;
    *) [ -z "$target" ] && target="$1" || { echo "claudemeister: лишний аргумент $1" >&2; exit 2; } ;;
  esac
  shift
done

if [ ! -t 0 ] || [ ! -t 1 ]; then
  echo "claudemeister: нужен терминал — Claude Code интерактивный." >&2
  exit 2
fi

# setup на этом Mac не делали или после обновления в нем появился новый шаг: предложить настроить
/usr/bin/python3 "$LIB/claude_setup.py" check

DEV=$(projects_dir)
# папку проектов спрашивает mnrh init, если стоит полный mnrh; без него остаются ~ и пути
if [ ! -f "$CONF" ] && [ -z "${MNRH_PROJECTS:-}" ] && [ ! -d "$DEV" ] && command -v mnrh >/dev/null; then
  mnrh init --projects-only || exit 1
  DEV=$(projects_dir)
fi

dirs=()
for d in "$DEV"/*/; do
  [ -d "$d" ] && dirs+=("${d%/}")
done

match() {
  local q="$1" d found=()
  for d in ${dirs[@]+"${dirs[@]}"}; do
    case "$(basename "$d")" in "$q") echo "$d"; return 0 ;; esac
  done
  for d in ${dirs[@]+"${dirs[@]}"}; do
    case "$(basename "$d")" in "$q"*) found+=("$d") ;; esac
  done
  if [ "${#found[@]}" -eq 1 ]; then echo "${found[0]}"; return 0; fi
  if [ "${#found[@]}" -gt 1 ]; then
    echo "claudemeister: «${q}» подходит к нескольким: $(for f in "${found[@]}"; do printf '%s ' "$(basename "$f")"; done)" >&2
  else
    echo "claudemeister: проекта «${q}» нет в ${DEV/#$HOME/~}" >&2
  fi
  return 1
}

choose() {
  local d name branch last idx
  idx=$(
    {
      printf '~\t~ (домашний каталог)\n'
      for d in ${dirs[@]+"${dirs[@]}"}; do
        name=$(basename "$d")
        branch=$(git -C "$d" branch --show-current 2>/dev/null)
        last=$(git -C "$d" log -1 --format=%cr 2>/dev/null)
        printf '%s\t%-30s %-28s %s\n' "$name" "$name" "${branch:--}" "$last"
      done
    } | /usr/bin/python3 "$MENU" --title "Проекты в ${DEV/#$HOME/~}:" --label "Проект" --start 0 --default 1
  ) || return $?
  if [ "$idx" -eq 0 ]; then echo "$HOME"; else echo "${dirs[$((idx - 1))]}"; fi
}

if [ -z "$target" ]; then
  dir=$(choose) || exit $?
elif [ "$target" = "root" ] || [ "$target" = "~" ]; then
  dir="$HOME"
elif case "$target" in */*) true ;; *) false ;; esac && [ -d "$target" ]; then
  dir=$(cd "$target" && pwd)
else
  dir=$(match "$target") || exit 1
fi

for a in ${passthrough[@]+"${passthrough[@]}"}; do
  case "$a" in
    -c|--continue|-r|--resume|--resume=*|--session-id|--session-id=*|-p|--print|--teleport|--teleport=*|--from-pr|--from-pr=*) new=1 ;;
  esac
done

if [ "$new" -eq 0 ]; then
  sid=$(/usr/bin/python3 "$LIB/claude_sessions.py" choose "$dir") || exit $?
  [ -n "$sid" ] && [ "$sid" != new ] && passthrough=(--resume "$sid" ${passthrough[@]+"${passthrough[@]}"})
fi

if [ -z "$mode" ]; then
  idx=$(printf 'caffeinate\tcaffeinate — Mac не уснёт, пока идёт сессия\nобычная\tобычная\n' |
    /usr/bin/python3 "$MENU" --title "Режим сессии в ${dir/#$HOME/~}:" --label "Режим") || exit $?
  [ "$idx" = 1 ] && mode=normal || mode=caffeinate
fi

PROXY="$LIB/claude_proxy.py"
if [ -z "$net" ]; then
  net=direct
  if /usr/bin/python3 "$PROXY" configured; then
    [ "$(/usr/bin/python3 "$PROXY" last)" = vps ] && def=1 || def=0
    idx=$(printf 'напрямую\tнапрямую\nvps\tчерез VPS — только эта сессия, остальное напрямую\n' |
      /usr/bin/python3 "$MENU" --title "Сеть:" --label "Сеть" --default "$def") || exit $?
    [ "$idx" = 1 ] && net=vps
    /usr/bin/python3 "$PROXY" remember "$net"
  fi
fi
pre=()
if [ "$net" = vps ]; then
  /usr/bin/python3 "$PROXY" configured || { echo "claudemeister: прокси не настроен или нет mihomo: claudemeister proxy setup" >&2; exit 1; }
  pre=(/usr/bin/python3 "$PROXY" run --)
fi

cd "$dir" || exit 1
echo "-> ${dir/#$HOME/~} · $mode$([ "$net" = vps ] && echo ' · через VPS')$([ "${CLAUDE_CODE_SKIP_PROMPT_HISTORY:-}" = 1 ] && echo ' · без записи')"
# после claudemeister lid setup сторож держит Mac и с закрытой крышкой; без правила шаг молча пропускается
if [ "$mode" = caffeinate ]; then
  /usr/bin/python3 "$LIB/claude_lid.py" keep >/dev/null 2>&1
  exec ${pre[@]+"${pre[@]}"} caffeinate -is "$CLAUDE_BIN" "${passthrough[@]+"${passthrough[@]}"}"
else
  exec ${pre[@]+"${pre[@]}"} "$CLAUDE_BIN" "${passthrough[@]+"${passthrough[@]}"}"
fi
