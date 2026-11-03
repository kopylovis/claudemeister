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
claudemeister                       меню: проект, сессии, прокси, humanization, setup (MNRH_NO_MENU=1 без него)
claudemeister open                  выбрать проект в папке проектов и запустить в нём Claude Code
claudemeister root                  запустить в домашнем каталоге
claudemeister <имя>                 сразу в проекте (достаточно начала имени) или в папке по пути
claudemeister ... -n                обычная сессия без вопроса
claudemeister ... -k                caffeinate без вопроса
claudemeister ... -x                через свой VPS: только эта сессия, остальные программы напрямую
claudemeister ... -d                напрямую, без вопроса про VPS
claudemeister ... --new             новая сессия, не предлагать продолжить сохранённые
claudemeister ... -i                без записи на диск: переписки и истории промптов не останется
claudemeister ... -- <аргументы>    аргументы для claude, например: claudemeister kcalm -- --continue
claudemeister sessions              сохранённые сессии: размер, продолжить, перенести, удалить (sessions -h)
claudemeister restart               изнутри Claude: закрыть и открыть эту же сессию заново, с claude update
claudemeister forget                изнутри Claude: удалить эту сессию без следа и открыть чистую
claudemeister fresh                 изнутри Claude: новая сессия без названия, эта остаётся в /resume (/fresh)
claudemeister slim                  изнутри Claude: сжать эту сессию и открыть снова
claudemeister brief                 где мы остановились: git, прошлая сессия, бэклог (/where)
claudemeister parallel "<…>"        задача в отдельной ветке и worktree, во втором окне Claude (/parallel)
claudemeister ask <пр> <…>          спросить отдельный Claude про другой проект, только чтение (/ask)
claudemeister release-notes         материал для «Что нового» к релизу (/release-notes)
claudemeister todo                  бэклог проекта между сессиями (/todo; подробнее: todo -h)
claudemeister shot                  снимок окна приложения (/shot)
claudemeister deps                  устаревшие зависимости Gradle в gradle/libs.versions.toml
claudemeister proxy                 прокси через VPS для отдельных сессий: setup, test, remove (proxy -h)
claudemeister guard                 спросит перед чтением или удалением ключей и необратимыми командами
claudemeister buildlog              длинный вывод Gradle, xcodebuild, тестов: Claude видит выжимку (on, off, test)
claudemeister humanize              писать без признаков нейросети: on, off, check <файл>, rules (/humanize)
claudemeister notify                уведомления, когда Claude ждёт тебя (on, off, after <сек>, test, telegram)
claudemeister setup                 поставить /restart, хуки, MCP-сервер, хук zsh, прокси и promptsill
                                    (--country KZ для прокси, --remove убрать; подробнее: setup -h)
claudemeister --version             версия

caffeinate (по умолчанию) не даёт Mac уснуть, пока идёт сессия:
удобно для долгих задач и Remote Control с телефона. Экран при этом гаснуть может.
С закрытой крышкой на батарее Mac все равно уснет, caffeinate этого не отменяет.
Папка проектов: ~/Developer, строка projects=<путь> в ~/.config/mnrh/config или MNRH_PROJECTS=<путь>.
EOF
}

case "${1:-}" in
  open) shift ;;
  proxy) shift; exec /usr/bin/python3 "$(dirname "$MENU")/claude_proxy.py" "$@" ;;
  sessions) shift; exec /usr/bin/python3 "$(dirname "$MENU")/claude_sessions.py" "$@" ;;
  brief) exec /usr/bin/python3 "$(dirname "$MENU")/claude_brief.py" "$@" ;;
  parallel) exec /usr/bin/python3 "$(dirname "$MENU")/claude_parallel.py" "$@" ;;
  ask) exec /usr/bin/python3 "$(dirname "$MENU")/claude_ask.py" "$@" ;;
  release-notes) exec /usr/bin/python3 "$(dirname "$MENU")/release_notes.py" "$@" ;;
  statusline) shift; exec /usr/bin/python3 "$(dirname "$MENU")/claude_status.py" "$@" ;;
  humanize|humanize-hook) exec /usr/bin/python3 "$(dirname "$MENU")/claude_humanize.py" "$@" ;;
  buildlog|buildlog-hook) exec /usr/bin/python3 "$(dirname "$MENU")/claude_buildlog.py" "$@" ;;
  guard|guard-hook) exec /usr/bin/python3 "$(dirname "$MENU")/claude_guard.py" "$@" ;;
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
# если рядом стоит mnrh с настроенным sleep lid, он держит Mac и с закрытой крышкой; без mnrh шаг молча пропускается
MNRH=$(command -v mnrh)
if [ "$mode" = caffeinate ]; then
  [ -n "$MNRH" ] && "$MNRH" sleep lid keep >/dev/null 2>&1
  exec ${pre[@]+"${pre[@]}"} caffeinate -is "$CLAUDE_BIN" "${passthrough[@]+"${passthrough[@]}"}"
else
  exec ${pre[@]+"${pre[@]}"} "$CLAUDE_BIN" "${passthrough[@]+"${passthrough[@]}"}"
fi
