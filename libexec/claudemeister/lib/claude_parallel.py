import glob
import json
import os
import re
import shutil
import subprocess
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from mnrhlib import CLAUDE_HOME, find_project, projects_dir, tilde

TRANSLIT = dict(zip("абвгдеёжзийклмнопрстуфхцчшщъыьэюя",
                    ["a", "b", "v", "g", "d", "e", "e", "zh", "z", "i", "y", "k", "l", "m", "n", "o", "p", "r", "s",
                     "t", "u", "f", "h", "ts", "ch", "sh", "sch", "", "y", "", "e", "yu", "ya"]))
COPY = ["local.properties", ".env", ".env.*", "fastlane/.env", "fastlane/.env.*"]


def git(root, *args, check=False):
    r = subprocess.run(["git", "-C", root, *args], capture_output=True, text=True, timeout=120)
    if check and r.returncode:
        raise RuntimeError((r.stderr or r.stdout).strip() or f"git {' '.join(args)} упал")
    return r.stdout.strip()


def slug(text):
    low = "".join(TRANSLIT.get(ch, ch) for ch in text.lower())
    out = ""
    for w in re.findall(r"[a-z0-9]+", low):
        if len(out) + len(w) + 1 > 28:
            break
        out = f"{out}-{w}" if out else w
    return out or time.strftime("task-%m%d-%H%M")


def worktree_root(root):
    return os.path.join(projects_dir(), ".worktrees", os.path.basename(root.rstrip("/")))


def copy_local(root, path):
    copied = []
    for pattern in COPY:
        for src in glob.glob(os.path.join(glob.escape(root), pattern)):
            rel = os.path.relpath(src, root)
            dst = os.path.join(path, rel)
            if os.path.isfile(src) and not os.path.exists(dst) and not git(root, "ls-files", rel):
                os.makedirs(os.path.dirname(dst), exist_ok=True)
                shutil.copy2(src, dst)
                copied.append(rel)
    return copied


PERMISSION_MODES = ["default", "acceptEdits", "auto", "plan"]


def start(task, project=None, branch=None, mode="caffeinate", cwd=None, cm=None, permission=None, rc=True):
    root, err = find_project(project, cwd)
    if err:
        return err, True
    if not git(root, "rev-parse", "--git-dir"):
        return f"{tilde(root)} — не git-репозиторий, worktree не сделать", True
    task = " ".join((task or "").split())
    if not task:
        return "Нужна задача: что сделать параллельно", True
    base = git(root, "branch", "--show-current") or git(root, "rev-parse", "--short", "HEAD")
    name = branch or f"parallel/{slug(task)}"
    if git(root, "rev-parse", "--verify", "--quiet", f"refs/heads/{name}"):
        name = f"{name}-{time.strftime('%H%M%S')}"
    path = os.path.join(worktree_root(root), name.replace("/", "-"))
    os.makedirs(os.path.dirname(path), exist_ok=True)
    try:
        git(root, "worktree", "add", "-b", name, path, "HEAD", check=True)
    except RuntimeError as e:
        return f"Не создал worktree: {e}", True
    git(root, "config", f"branch.{name}.mnrhBase", base)
    git(root, "config", f"branch.{name}.mnrhTask", task)
    copied = copy_local(root, path)
    dirty = git(root, "status", "--porcelain")
    prompt = (f"Это параллельная задача в отдельном git worktree {path}, ветка {name}, ответвлена от {base} "
              f"проекта {root}. Задача: {task}\n\nРаботай только в этой папке. Если нужны зависимости "
              f"(node_modules и т. п.), поставь их здесь. Когда закончишь, закоммить результат в ветку {name} "
              f"(не сливай и не пушь) и коротко отчитайся, что сделано и как проверить.")
    import claude_open
    flags = ["--permission-mode", permission] if permission in PERMISSION_MODES and permission != "default" else []
    text, bad = claude_open.open_claude({"path": path, "mode": mode, "prompt": prompt, "flags": flags,
                                         "trust": claude_open.trusted(root), "remote_control": rc}, cm or "claudemeister")
    lines = [f"Ветка {name} от {base}, worktree {tilde(path)}."]
    if copied:
        lines.append("Скопировал неотслеживаемые настройки: " + ", ".join(copied) + ".")
    if dirty:
        lines.append("В основной папке есть незакоммиченные изменения — в worktree их нет, он от последнего коммита.")
    lines.append(text)
    lines.append(f"Потом: claudemeister parallel pr {name} — через PR, merge {name} — влить локально, "
                 f"drop {name} — выбросить.")
    return "\n".join(lines), bad


def running_in(path):
    """Claude открыт в этом worktree или глубже. Соседний worktree fix-login для fix не в счет."""
    claude = CLAUDE_HOME
    for f in glob.glob(os.path.join(claude, "sessions", "*.json")):
        try:
            with open(f) as fh:
                info = json.load(fh)
            cwd, pid = str(info.get("cwd") or ""), int(info.get("pid") or 0)
            # pid 0 в kill означает всю группу процессов, такой kill всегда удается
            if pid > 0 and (cwd == path or cwd.startswith(path.rstrip("/") + "/")):
                os.kill(pid, 0)
                return True
        except (OSError, ValueError, TypeError, OverflowError):
            continue
    return False


def entries(root):
    out, cur = [], {}
    for line in git(root, "worktree", "list", "--porcelain").splitlines() + [""]:
        if not line:
            if cur.get("path", "").startswith(worktree_root(root) + "/") and cur.get("branch"):
                out.append(cur)
            cur = {}
        elif line.startswith("worktree "):
            cur["path"] = line[9:]
        elif line.startswith("branch "):
            cur["branch"] = line[7:].replace("refs/heads/", "")
    for e in out:
        b = e["branch"]
        e["base"] = git(root, "config", f"branch.{b}.mnrhBase") or git(root, "branch", "--show-current")
        e["task"] = git(root, "config", f"branch.{b}.mnrhTask")
        e["ahead"] = git(root, "rev-list", "--count", f"{e['base']}..{b}") or "?"
        e["dirty"] = len(git(e["path"], "status", "--porcelain").splitlines())
        e["running"] = running_in(e["path"])
    return out


def listing(project=None, cwd=None):
    root, err = find_project(project, cwd)
    if err:
        return err, True
    es = entries(root)
    if not es:
        return f"Параллельных задач в {os.path.basename(root)} нет.", False
    lines = [f"Параллельные задачи {os.path.basename(root)}:"]
    for e in es:
        state = "Claude работает" if e["running"] else "Claude закрыт"
        lines.append(f"  {e['branch']}: коммитов {e['ahead']} поверх {e['base']}, незакоммичено {e['dirty']}, {state}")
        if e["task"]:
            lines.append(f"    задача: {e['task'][:160]}")
    return "\n".join(lines), False


def pick(branch, project=None, cwd=None):
    """-> (root, задача, ошибка)"""
    root, err = find_project(project, cwd)
    if err:
        return None, None, err
    match = [e for e in entries(root) if e["branch"] == branch or e["branch"].endswith("/" + branch)]
    if len(match) != 1:
        return root, None, f"Нет параллельной задачи «{branch}». " + listing(project, cwd)[0]
    return root, match[0], None


def forget_task(root, e, delete="-D"):
    """Убрать worktree, ветку и записи о задаче."""
    subprocess.run(["git", "-C", root, "worktree", "remove", "--force", e["path"]], capture_output=True)
    git(root, "branch", delete, e["branch"])
    for key in ("mnrhBase", "mnrhTask"):
        git(root, "config", "--unset", f"branch.{e['branch']}.{key}")


def finish(branch, action, project=None, cwd=None):
    root, e, err = pick(branch, project, cwd)
    if err:
        return err, True
    if e["running"]:
        return f"В {tilde(e['path'])} ещё работает Claude — сначала закрой его.", True
    if action == "merge":
        if e["dirty"]:
            return f"В worktree незакоммичено файлов: {e['dirty']}. Сначала закоммить их там.", True
        if git(root, "status", "--porcelain", "--untracked-files=no"):
            return "В основной папке есть незакоммиченные изменения — сначала закоммить или отложи их.", True
        current = git(root, "branch", "--show-current")
        if current != e["base"]:
            return f"Основная папка сейчас на ветке {current}, а задача ответвлена от {e['base']}. Переключись на неё.", True
        r = subprocess.run(["git", "-C", root, "merge", "--no-ff", "--no-edit", e["branch"]],
                           capture_output=True, text=True)
        if r.returncode:
            subprocess.run(["git", "-C", root, "merge", "--abort"], capture_output=True)
            return f"Слить без конфликтов не вышло, слияние отменено:\n{(r.stdout + r.stderr).strip()[-1500:]}", True
    forget_task(root, e, "-d" if action == "merge" else "-D")
    if action == "merge":
        return f"Влил {e['branch']} в {e['base']} ({e['ahead']} коммитов), worktree и ветку убрал. Пушить — сам.", False
    return f"Выбросил {e['branch']}: worktree и ветка удалены.", False


# Сколько ждать, пока у свежего PR появятся проверки: GitHub Actions заводит их через несколько секунд
CHECKS_APPEAR = 15
CHECKS_APPEAR_ACTIONS = 90
CI_TIMEOUT = 3 * 3600


def gh(cwd, *args, timeout=120):
    try:
        return subprocess.run(["gh", *args], cwd=cwd, capture_output=True, text=True, timeout=timeout)
    except subprocess.TimeoutExpired as ex:
        return subprocess.CompletedProcess(ex.cmd, 124, "", f"gh не ответил за {timeout} с")


def out_of(r, limit=1500):
    return ((r.stdout or "") + (r.stderr or "")).strip()[-limit:]


def github_repo(url):
    """owner/repo (или host/owner/repo не на github.com) из адреса remote; None, если это не похоже на GitHub."""
    m = re.match(r"^(?:[a-z+]+://)?(?:[^@/]+@)?([^/:]+)[:/]+([^/]+/[^/]+?)(?:\.git)?/*$", url or "")
    if not m or "." not in m.group(1):
        return None
    host, path = m.groups()
    return path if host == "github.com" else f"{host}/{path}"


def pr_checks(repo, num, path, log):
    """-> (есть ли проверки, прошли ли, текст про упавшие)"""
    wait = CHECKS_APPEAR_ACTIONS if os.path.isdir(os.path.join(path, ".github", "workflows")) else CHECKS_APPEAR
    deadline = time.time() + wait
    while True:
        r = gh(path, "pr", "checks", str(num), "-R", repo, "--json", "name,bucket,link")
        try:
            found = json.loads(r.stdout or "null")
        except ValueError:
            found = None
        if isinstance(found, list) and found:
            break
        if "no checks reported" not in out_of(r) and not isinstance(found, list):
            return True, False, f"gh pr checks не ответил: {out_of(r) or r.returncode}"
        if time.time() >= deadline:
            return False, True, ""
        time.sleep(5)
    log(f"Жду CI ({len(found)} проверок)…")
    r = gh(path, "pr", "checks", str(num), "-R", repo, "--watch", "--fail-fast", "-i", "15", timeout=CI_TIMEOUT)
    if r.returncode == 0:
        return True, True, ""
    r = gh(path, "pr", "checks", str(num), "-R", repo, "--json", "name,bucket,link")
    try:
        bad = [c for c in json.loads(r.stdout or "[]") if c.get("bucket") in ("fail", "cancel")]
    except (ValueError, AttributeError):
        bad = []
    if not bad:
        return True, False, out_of(r) or "gh pr checks --watch завершился с ошибкой"
    return True, False, "\n".join(f"  {c.get('name')}: {c.get('bucket')} {c.get('link') or ''}".rstrip() for c in bad)


def pr(branch, project=None, cwd=None, merge=True, log=None):
    """Отправить ветку параллельной задачи в PR, дождаться CI, слить и убрать worktree. -> (текст, ошибка ли)"""
    log = log or (lambda text: None)
    root, e, err = pick(branch, project, cwd)
    if err:
        return err, True
    name, base, path = e["branch"], e["base"], e["path"]
    if e["running"]:
        return f"В {tilde(path)} ещё работает Claude — сначала закрой его.", True
    if e["dirty"]:
        return f"В worktree незакоммичено файлов: {e['dirty']}. Сначала закоммить их там.", True
    if e["ahead"] == "0":
        return f"В {name} нет коммитов поверх {base}, отправлять нечего.", True
    if not shutil.which("gh"):
        return "Нужен GitHub CLI: brew install gh, потом gh auth login.", True
    remote = git(root, "config", f"branch.{base}.remote") or "origin"
    repo = github_repo(git(root, "config", f"remote.{remote}.url"))
    if not repo:
        return f"Remote {remote} проекта не на GitHub, PR не открыть.", True

    r = subprocess.run(["git", "-C", root, "push", "--no-follow-tags", "-u", remote, f"refs/heads/{name}:refs/heads/{name}"],
                       capture_output=True, text=True, timeout=300)
    if r.returncode:
        return f"Не запушил {name} в {remote}:\n{out_of(r)}", True
    log(f"Запушил {name} в {remote}.")

    r = gh(path, "pr", "list", "-R", repo, "--head", name, "--state", "open", "--json", "number,url")
    try:
        opened = json.loads(r.stdout or "[]")
    except ValueError:
        opened = []
    if opened:
        num, url = opened[0]["number"], opened[0]["url"]
        log(f"PR уже открыт: {url}")
    else:
        r = gh(path, "pr", "create", "-R", repo, "--fill", "--base", base, "--head", name)
        url = (r.stdout or "").strip().splitlines()[-1:] or [""]
        url = url[0]
        m = re.search(r"/pull/(\d+)", url)
        if r.returncode or not m:
            return f"Не открыл PR:\n{out_of(r)}", True
        num = int(m.group(1))
        log(f"Открыл PR: {url}")
    if not merge:
        return f"Ветка {name} в PR #{num}, сливать не стал. Worktree на месте: {tilde(path)}.", False

    subprocess.run(["git", "-C", root, "fetch", "--no-tags", "-q", remote, base], capture_output=True, timeout=300)
    ref = f"{remote}/{base}" if git(root, "rev-parse", "--verify", "--quiet", f"refs/remotes/{remote}/{base}") else base
    how = "--merge" if git(root, "rev-list", "--merges", "-1", ref) else "--rebase"

    has, ok, bad = pr_checks(repo, num, path, log)
    if not ok:
        return (f"CI по PR #{num} не прошёл, сливать не стал. Worktree оставил: {tilde(path)}.\n{bad}\n"
                f"Почини там, закоммить и снова: claudemeister parallel pr {name}"), True
    log("CI прошёл." if has else "Проверок у PR нет, сливаю сразу.")

    r = gh(path, "pr", "merge", str(num), "-R", repo, how, "--delete-branch", timeout=300)
    if r.returncode:
        return f"PR #{num} не слился ({how}), worktree оставил:\n{out_of(r)}", True
    forget_task(root, e)
    git(root, "branch", "-d", "-r", f"{remote}/{name}")
    lines = [f"Слил PR #{num} в {base} ({how[2:]}), ветку на GitHub удалил, worktree и локальную ветку убрал."]
    subprocess.run(["git", "-C", root, "fetch", "--no-tags", "-q", remote, base], capture_output=True, timeout=300)
    if git(root, "branch", "--show-current") != base:
        lines.append(f"Основная папка не на {base}, её не трогал.")
    elif git(root, "status", "--porcelain", "--untracked-files=no"):
        lines.append(f"В основной папке есть незакоммиченные изменения, {base} не подтягивал: git pull --ff-only сам.")
    else:
        r = subprocess.run(["git", "-C", root, "merge", "--ff-only", "-q", f"{remote}/{base}"],
                           capture_output=True, text=True)
        lines.append(f"Подтянул {base} в основной папке." if not r.returncode else
                     f"{base} в основной папке не подтягивается перемоткой: {out_of(r, 300)}")
    return "\n".join(lines), False


START_TOOL = {
    "name": "parallel_task",
    "description": ("Запустить задачу параллельно: создать git worktree на новой ветке (от текущего коммита проекта) "
                    "и открыть там второй Claude Code в новом окне терминала с этой задачей и Remote Control. "
                    "Второй Claude закоммитит результат в свою ветку; потом parallel_finish вольёт или выбросит её. "
                    "Вызывай, когда пользователь просит сделать что-то параллельно, в фоне или в отдельной ветке."),
    "inputSchema": {"type": "object", "properties": {
        "task": {"type": "string", "description": "задача для второго Claude, понятная без контекста этой сессии"},
        "project": {"type": "string", "description": "проект; по умолчанию текущий"},
        "branch": {"type": "string", "description": "имя ветки; по умолчанию parallel/<по задаче>"},
        "mode": {"type": "string", "enum": ["caffeinate", "normal"]},
        "permission_mode": {"type": "string", "enum": PERMISSION_MODES,
                            "description": "режим разрешений второго Claude; по умолчанию обычный (спрашивает, "
                                           "вопрос приходит уведомлением). auto или acceptEdits — только если "
                                           "пользователь так сказал"},
        "remote_control": {"type": "boolean", "description": "Remote Control, по умолчанию да"}},
        "required": ["task"]},
}
LIST_TOOL = {
    "name": "parallel_list",
    "description": "Параллельные задачи проекта: ветки, сколько коммитов, есть ли незакоммиченное, работает ли там Claude.",
    "annotations": {"readOnlyHint": True},
    "inputSchema": {"type": "object", "properties": {"project": {"type": "string"}}},
}
FINISH_TOOL = {
    "name": "parallel_finish",
    "description": ("Закончить параллельную задачу: merge — влить её ветку в ветку, от которой она ответвлена "
                    "(без пуша), drop — выбросить ветку и worktree, pr — запушить ветку, открыть PR в исходную "
                    "ветку, дождаться CI, слить его и убрать worktree (no_merge: только открыть PR). pr может "
                    "ждать CI долго. Только с согласия пользователя."),
    "annotations": {"destructiveHint": True},
    "inputSchema": {"type": "object", "properties": {
        "branch": {"type": "string"}, "action": {"type": "string", "enum": ["merge", "drop", "pr"]},
        "no_merge": {"type": "boolean", "description": "для pr: только запушить и открыть PR, не сливать"},
        "project": {"type": "string"}}, "required": ["branch", "action"]},
}
TOOLS = [START_TOOL, LIST_TOOL, FINISH_TOOL]


def handle(name, a, cwd=None, cm=None):
    if name == "parallel_task":
        return start(a.get("task"), a.get("project"), a.get("branch"), a.get("mode") or "caffeinate", cwd, cm,
                     a.get("permission_mode"), a.get("remote_control", True) is not False)
    if name == "parallel_list":
        return listing(a.get("project"), cwd)
    if a.get("action") == "pr":
        done = []
        text, bad = pr(a.get("branch", ""), a.get("project"), cwd, merge=not a.get("no_merge"), log=done.append)
        return "\n".join(done + [text]), bad
    return finish(a.get("branch", ""), a.get("action"), a.get("project"), cwd)


def main():
    args = sys.argv[2:] if sys.argv[1:2] == ["parallel"] else sys.argv[1:]
    if not args or args[0] in ("-h", "--help"):
        print('claudemeister parallel "<задача>" [-p проект] [-b ветка] [-n] [--permission-mode auto] [--no-rc]')
        print("                         новая ветка и worktree от текущего коммита, в новом окне — Claude с задачей")
        print("claudemeister parallel list           параллельные задачи этого проекта")
        print("claudemeister parallel merge <ветка>  влить в исходную ветку (без пуша) и убрать worktree")
        print("claudemeister parallel drop <ветка>   выбросить ветку и worktree")
        print("claudemeister parallel pr <ветка> [--no-merge]")
        print("                         запушить, открыть PR в исходную ветку, дождаться CI, слить, убрать worktree")
        print()
        print(f"Worktree лежат в {tilde(os.path.join(projects_dir(), '.worktrees'))}/<проект>/. В Claude Code: /parallel.")
        return 0 if args else 2
    opts = {}
    rest = []
    i = 0
    while i < len(args):
        if args[i] in ("-p", "-b", "--permission-mode") and i + 1 < len(args):
            opts[args[i]] = args[i + 1]
            i += 2
            continue
        if args[i] in ("-n", "--no-rc", "--no-merge"):
            opts[args[i]] = True
        else:
            rest.append(args[i])
        i += 1
    if rest[:1] == ["list"]:
        text, bad = listing(opts.get("-p"))
    elif rest[:1] in (["merge"], ["drop"]) and len(rest) == 2:
        text, bad = finish(rest[1], rest[0], opts.get("-p"))
    elif rest[:1] == ["pr"] and len(rest) == 2:
        text, bad = pr(rest[1], opts.get("-p"), merge=not opts.get("--no-merge"), log=lambda t: print(t, flush=True))
    else:
        from mnrhlib import CM
        text, bad = start(" ".join(rest), opts.get("-p"), opts.get("-b"), "normal" if opts.get("-n") else "caffeinate",
                          cm=CM, permission=opts.get("--permission-mode"), rc=not opts.get("--no-rc"))
    print(text)
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
