import calendar
import fnmatch
import hashlib
import json
import os
import re
import shlex
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from mnrhlib import BADGES, CACHE, HOME, badge, read_config, tilde, write_config, write_json

LOG = os.path.join(HOME, "Library", "Logs", "mnrh-guard.log")
TAIL = 4 << 20

RULES = {
    "secrets-read": "чтение и правка секретов: .env, *.p8, *.p12, *.jks, *.keystore, приватные ключи SSH, "
                    "service-account/credentials *.json, keystore.properties, .netrc, пароли из связки ключей",
    "secrets-delete": "удаление секретов: rm файла-ключа или папки, где они лежат (~/.ssh, fastlane/ с ключами…), "
                      "security delete-* из связки ключей",
    "destructive": "необратимые команды: git reset --hard, push --force, clean -fd, checkout/restore всего, "
                   "branch -D, rm -r вне проекта, самого проекта или ~, curl | sh, dd на диск, diskutil erase, mkfs, "
                   "chmod -R системных папок; и внутри bash -c, eval, xargs, sudo",
}
GUARD_STATE = os.path.join(CACHE, "guard")
YES = re.compile(r"^\W*(да|ага|угу|ок|окей|ok|okay|yes|y|давай|выполняй|можно|разрешаю|разреши|делай|go|sure)\b", re.I)
SENSITIVE_DIRS = ["~/.ssh", "~/.gnupg", "~/.aws", "~/Library/Keychains", "~/.config/gh"]
DELETERS = {"rm", "rmdir", "unlink", "shred", "srm", "trash"}
WALK_LIMIT = 3000

SECRET_NAMES = [".env", ".env.*", "*.p8", "*.p12", "*.pfx", "*.jks", "*.keystore", "*.pem", "*.key",
                "id_rsa", "id_ed25519", "id_ecdsa", "id_dsa", "*service-account*.json", "*service_account*.json",
                "*credentials*.json", "credentials", "keystore.properties", "signing.properties", ".netrc",
                ".npmrc", ".pypirc", "*.mobileprovision"]
# имена, по которым видно, что маска Grep ("**/.env*", "id_*") захватит секреты; "*" и "*.*" ищут везде и не в счет
SECRET_SAMPLES = [".env", ".env.local", ".env.production", "id_rsa", "id_ed25519", ".netrc", ".npmrc", "key.p8",
                  "release.jks", "release.keystore"]
CATCH_ALL = {"*", "**", "**/*", "*.*"}
NOT_SECRET = [".env.example", ".env.sample", ".env.template", ".env.dist", "*.pub"]
READERS = {"cat", "less", "more", "head", "tail", "bat", "cp", "scp", "rsync", "base64", "xxd", "hexdump",
           "strings", "grep", "rg", "open", "pbcopy", "sed", "awk", "source", ".", "keytool", "openssl"}


def enabled():
    return read_config().get("guard", "on") != "off"


def sync_badge(on=None):
    """Метка guard во второй строке promptsill, пока защита включена."""
    on = enabled() if on is None else on
    badge("mnrh-guard", {"text": "guard", "color": "green"} if on else None)


def hold_badge(sid, rule):
    """На две минуты в этой сессии: guard остановил вызов."""
    if not sid:
        return
    try:
        for name in os.listdir(BADGES):
            if name.startswith("mnrh-guard_") and time.time() - os.path.getmtime(os.path.join(BADGES, name)) > 600:
                os.remove(os.path.join(BADGES, name))
    except OSError:
        pass
    what = {"secrets-read": "чтение секрета", "secrets-delete": "удаление секрета",
            "destructive": "опасную команду"}.get(rule, rule)
    badge(f"mnrh-guard_{sid[:8]}", {"text": f"остановил {what}", "color": "yellow", "session": sid,
                                    "expires": int(time.time() + 120)}, refresh=True)


def skipped():
    return {r.strip() for r in read_config().get("guard_skip", "").split(",") if r.strip()}


def log(line):
    try:
        with open(LOG, "a") as f:
            f.write(time.strftime("%Y-%m-%d %H:%M:%S ") + line + "\n")
    except OSError:
        pass


def is_secret(path):
    name = os.path.basename(path.rstrip("/"))
    if not name or any(fnmatch.fnmatch(name, p) for p in NOT_SECRET):
        return False
    if any(fnmatch.fnmatch(name, p) for p in SECRET_NAMES):
        return True
    full = os.path.expanduser(path)
    for d in ("~/.ssh/", "~/.aws/", "~/.gnupg/", "~/.config/gh/hosts", "~/.claude/.credentials"):
        if full.startswith(os.path.expanduser(d)) and not name.endswith((".pub", "known_hosts", "config")):
            return True
    return False


def split_pipelines(cmd):
    """Команды через ;, &&, ||, & и перевод строки, каждая — список стадий конвейера через |.
    Разделители в кавычках, после \\ и в комментарии # не считаются."""
    commands, stages, cur = [], [], []
    quote, i, n = None, 0, len(cmd)

    def end_stage():
        text = "".join(cur).strip()
        cur.clear()
        if text:
            stages.append(text)

    def end_command():
        end_stage()
        if stages:
            commands.append(list(stages))
        stages.clear()

    while i < n:
        c, two = cmd[i], cmd[i:i + 2]
        if quote:
            cur.append(c)
            if c == quote:
                quote = None
            elif c == "\\" and quote == '"' and i + 1 < n:
                cur.append(cmd[i + 1])
                i += 1
            i += 1
        elif c in "'\"":
            quote = c
            cur.append(c)
            i += 1
        elif c == "\\" and i + 1 < n:
            cur.append(two)
            i += 2
        elif c == "#" and (not cur or cur[-1].isspace()):
            while i < n and cmd[i] != "\n":
                i += 1
        elif two in ("&&", "||"):
            end_command()
            i += 2
        elif c in ";\n":
            end_command()
            i += 1
        elif c == "|" and not (cur and cur[-1] == ">"):
            end_stage()
            i += 2 if two == "|&" else 1
        elif c == "&" and not (cur and cur[-1] in "<>") and two != "&>":
            end_command()
            i += 1
        else:
            cur.append(c)
            i += 1
    end_command()
    return commands


def split_segments(cmd):
    return [stage for stages in split_pipelines(cmd) for stage in stages]


def words(segment):
    try:
        return shlex.split(segment, comments=True)
    except ValueError:
        return segment.split()


ASSIGN = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*=")
# Обертки, после которых идет сама команда, и их ключи со значением: sudo -u root rm → rm
WRAPPERS = {"sudo": {"-u", "-g", "-C", "-D", "-h", "-p", "-r", "-t", "-U", "-T"}, "command": set(), "exec": {"-a"},
            "nohup": set(), "time": set(), "env": {"-u", "-S", "-C", "-P"}, "caffeinate": {"-t", "-w"},
            "xargs": {"-n", "-I", "-L", "-P", "-s", "-E", "-d", "-J", "-R", "-S", "-a"}, "nice": {"-n"},
            "timeout": {"-s", "-k"}}


def strip_env(ws):
    ws = list(ws)
    while True:
        i = 0
        while i < len(ws) and ASSIGN.match(ws[i]):
            i += 1
        ws = ws[i:]
        name = os.path.basename(ws[0]) if ws else ""
        if name not in WRAPPERS:
            return ws
        ws = ws[1:]
        while ws and ws[0].startswith("-") and ws[0] != "-":
            opt = ws.pop(0)
            if opt == "--":
                break
            if opt in WRAPPERS[name] and ws:
                ws.pop(0)
        if name == "timeout" and ws:
            ws = ws[1:]


def resolve(path, cwd, env):
    def sub(m):
        name = m.group(1) or m.group(2)
        return env.get(name, m.group(0))
    path = re.sub(r"\$\{([A-Za-z_]\w*)(?::[?-][^}]*)?\}|\$([A-Za-z_]\w*)", sub, path)
    if "$" in path or "`" in path:
        return None
    path = os.path.expanduser(path)
    return os.path.normpath(os.path.join(cwd, path))


def assigned(value, cwd, env):
    if value.startswith(("/", "~", "$", ".")):
        return resolve(value, cwd, env) or value
    return value


def check_secrets_cmd(ws, segment):
    if not ws:
        return None
    exe = os.path.basename(ws[0])
    if exe == "security" and len(ws) > 1 and (ws[1] == "dump-keychain" or
                                               (ws[1].startswith("find-") and ("-w" in ws or "-g" in ws))):
        return "secrets-read", "пароль из связки ключей попадёт в переписку"
    if exe in READERS:
        for w in ws[1:]:
            if not w.startswith("-") and is_secret(w):
                return "secrets-read", f"{exe} {tilde(os.path.expanduser(w))}: содержимое секрета попадёт в переписку"
    for m in re.finditer(r"<\s*([^\s<>|;&]+)", segment):
        if is_secret(m.group(1)):
            return "secrets-read", f"чтение {m.group(1)}: содержимое секрета попадёт в переписку"
    return None


def holds_secrets(path):
    full = os.path.realpath(path)
    for d in SENSITIVE_DIRS:
        d = os.path.realpath(os.path.expanduser(d))
        if full == d or d.startswith(full.rstrip("/") + "/"):
            return tilde(d) + "/"
        if full.startswith(d + "/"):
            return tilde(path)
    if os.path.isfile(path):
        return tilde(path) if is_secret(path) else None
    if not os.path.isdir(path):
        return tilde(path) if is_secret(path) else None
    seen = 0
    for root, dirs, files in os.walk(path):
        dirs[:] = [x for x in dirs if x not in ("node_modules", ".git", "build", ".gradle", "Pods", "DerivedData")]
        for f in files:
            seen += 1
            if is_secret(os.path.join(root, f)):
                return tilde(os.path.join(root, f))
        if seen > WALK_LIMIT or root.count(os.sep) - path.count(os.sep) >= 4:
            dirs[:] = []
    return None


def check_secret_delete(ws, cwd, env):
    exe = os.path.basename(ws[0])
    if exe == "security" and len(ws) > 1 and ws[1].startswith("delete-"):
        return "secrets-delete", f"security {ws[1]} удалит запись из связки ключей"
    if exe not in DELETERS:
        return None
    for t in ws[1:]:
        if t.startswith("-"):
            continue
        p = resolve(t, cwd, env)
        if p is None:
            continue
        found = holds_secrets(p)
        if found:
            what = (f"папка с ключами {found}" if found.endswith("/") else "это секрет" if found == tilde(p)
                    else f"там секрет: {found}")
            return "secrets-delete", f"{exe} {tilde(p)} — {what}, восстановить не получится"
    return None


def short_flags(args):
    """Буквы коротких ключей до --: ['-fd', '-x'] → {'f', 'd', 'x'}."""
    letters = set()
    for a in args:
        if a == "--":
            break
        if a.startswith("-") and not a.startswith("--") and len(a) > 1:
            letters |= set(a[1:])
    return letters


def positional(args):
    """Аргументы, кроме ключей; после -- все."""
    out, dash = [], False
    for a in args:
        if dash or not a.startswith("-") or a == "-":
            out.append(a)
        elif a == "--":
            dash = True
    return out


def within(path, base, strict=False):
    """path совпадает с base или лежит внутри (strict: только внутри). Сравнивает и как есть, и после realpath."""
    for p, b in ((path, base), (os.path.realpath(path), os.path.realpath(base))):
        b = b.rstrip("/") or "/"
        if p.startswith(b if b == "/" else b + "/") or (not strict and p == b):
            return True
    return False


def project_of(cwd):
    """Корень git-репозитория над cwd, иначе сам cwd. Домашняя папка проектом не считается (там бывает dotfiles)."""
    d = cwd
    while True:
        if os.path.exists(os.path.join(d, ".git")):
            break
        parent = os.path.dirname(d)
        if parent == d:
            return cwd
        d = parent
    return cwd if within(HOME, d) else d


# rm -r здесь не спрашиваем: временные папки и кеши
SCRATCH = ["/tmp", "/private/tmp", "/var/folders", "/private/var/folders", os.environ.get("TMPDIR") or "/tmp",
           os.path.join(HOME, "Library", "Caches"), os.path.join(HOME, ".cache"),
           os.path.join(HOME, "Library", "Developer", "Xcode", "DerivedData")]
SYSTEM_DIRS = {"/", "/System", "/Library", "/Applications", "/Users", "/usr", "/usr/local", "/bin", "/sbin", "/etc",
               "/var", "/private", "/opt", "/opt/homebrew", HOME, os.path.join(HOME, "Library")}
SHELLS = {"sh", "bash", "zsh", "dash", "ksh", "fish"}
INTERPRETER = re.compile(r"^(?:python[\d.]*|perl|ruby|node)$")
FROM_NET = re.compile(r"[<$]\(\s*(?:\S*/)?(?:curl|wget)\b")
DISK_DEV = re.compile(r"^of=/dev/(?:r?disk\d|sd[a-z]|nvme|hd[a-z]|mmcblk|vd[a-z]|xvd|loop|md\d|mapper/)")
ERASE = {"secureerase", "zerodisk", "randomdisk", "partitiondisk", "reformat"}


def runs_stdin(ws):
    """Интерпретатор, который выполнит скрипт со стандартного ввода: curl … | sh, но не | python3 -m json.tool."""
    exe = os.path.basename(ws[0]) if ws else ""
    shell = exe in SHELLS
    if not shell and not INTERPRETER.match(exe):
        return False
    for a in ws[1:]:
        if a == "-":
            return True
        if a == "--":
            continue
        if a.startswith("--"):
            if a in ("--eval", "--print", "--command"):
                return False
            continue
        if a.startswith("-"):
            if shell and "s" in a[1:]:
                return True
            if (shell and "c" in a[1:]) or (not shell and a[1:2] in ("c", "m", "e", "E", "p")):
                return False
            continue
        return False
    return True


def shell_script(ws):
    """Текст команды из bash -c '…' и eval …, иначе None."""
    exe = os.path.basename(ws[0])
    if exe == "eval":
        return " ".join(ws[1:]) or None
    if exe not in SHELLS:
        return None
    has_c = False
    for a in ws[1:]:
        if a.startswith("--"):
            continue
        if a.startswith("-") and len(a) > 1:
            has_c = has_c or "c" in a[1:]
            continue
        return a if has_c else None
    return None


def whole_tree(spec, cwd, env):
    """Путь git, который накрывает всю рабочую папку (или cwd целиком): ., :/, *, .."""
    if spec in (":/", ":", ":/.", ":/*", "*", ":(top)"):
        return True
    p = resolve(spec, cwd, env)
    return p is not None and within(cwd, p)


def check_git(ws, cwd, env):
    args, i = ws[1:], 0
    while i < len(args) and args[i].startswith("-"):
        if args[i] in ("-C", "-c", "--git-dir", "--work-tree", "--namespace") and i + 1 < len(args):
            if args[i] == "-C":
                cwd = resolve(args[i + 1], cwd, env) or cwd
            i += 2
        else:
            i += 1
    if i >= len(args):
        return None
    sub, rest = args[i], args[i + 1:]
    flags, longs, paths = short_flags(rest), set(rest[:rest.index("--")] if "--" in rest else rest), positional(rest)
    lost = "восстановить не получится"
    if sub == "reset" and "--hard" in longs:
        return f"git reset --hard сотрёт незакоммиченные правки, {lost}"
    if sub == "push":
        force = next((a for a in rest if a == "--force" or a.startswith(("--force-with-lease", "--force-if-includes"))),
                     "-f" if "f" in flags else next((f"+{a[1:]}" for a in paths if a.startswith("+")), None))
        if force:
            return f"git push {force} перепишет ветку на сервере, чужие коммиты там пропадут"
    if sub == "clean" and ("f" in flags or "--force" in longs) and flags & {"d", "x", "X"} and \
            "n" not in flags and "--dry-run" not in longs:
        ignored = " и всё из .gitignore (.env, локальные настройки)" if flags & {"x", "X"} else ""
        return f"git clean удалит неотслеживаемые файлы и папки{ignored}; их нет в git, {lost}"
    if sub == "checkout" and ("f" in flags or "--force" in longs):
        return f"git checkout --force отменит все незакоммиченные правки, {lost}"
    if sub in ("checkout", "restore"):
        staged_only = ("S" in flags or "--staged" in longs) and not ("W" in flags or "--worktree" in longs)
        spec = next((p for p in paths if whole_tree(p, cwd, env)), None)
        if spec and not (sub == "restore" and staged_only):
            return f"git {sub} {spec} отменит все незакоммиченные правки в {tilde(cwd)}, {lost}"
    if sub == "branch" and ("D" in flags or (("d" in flags or "--delete" in longs) and
                                             ("f" in flags or "--force" in longs))):
        return "git branch -D удалит ветку, даже если она никуда не влита"
    return None


def check_rm(ws, cwd, env, root):
    args = ws[1:]
    if not (short_flags(args) & {"r", "R"} or "--recursive" in args):
        return None
    for t in positional(args):
        p = resolve(t, cwd, env)
        if p is None:
            continue
        while os.path.basename(p) in ("*", ".*"):
            p = os.path.dirname(p)
        if p == "/":
            what = "это корень диска"
        elif within(HOME, p):
            what = "это домашняя папка" if p == HOME else "внутри домашняя папка"
        elif os.path.normpath(p) == root or os.path.realpath(p) == os.path.realpath(root):
            what = "это сам проект"
        elif within(p, root) and not within(HOME, root):
            continue
        elif any(within(p, d, strict=True) for d in SCRATCH):
            continue
        else:
            what = f"проекта нет, Claude запущен в {tilde(root)}" if within(HOME, root) else \
                f"это вне проекта {tilde(root)}"
        return f"rm -r {tilde(p)} — {what}, восстановить не получится"
    return None


def check_destructive(ws, seg, cwd, env, root):
    exe = os.path.basename(ws[0])
    if exe == "git":
        return check_git(ws, cwd, env)
    if exe == "rm":
        return check_rm(ws, cwd, env, root)
    if (exe in SHELLS or INTERPRETER.match(exe) or exe in ("source", ".")) and FROM_NET.search(seg):
        return f"{exe} выполнит скрипт прямо из сети, не глядя"
    if exe == "dd":
        dev = next((a for a in ws[1:] if DISK_DEV.match(a)), None)
        if dev:
            return f"dd {dev} перезапишет диск, данные на нём пропадут"
    if exe == "diskutil" and len(ws) > 1:
        sub = ws[1].lower()
        if sub.startswith("erase") or sub in ERASE or (sub == "apfs" and len(ws) > 2 and
                                                        ws[2].lower().startswith(("delete", "erase"))):
            return f"diskutil {' '.join(ws[1:3] if sub == 'apfs' else ws[1:2])} сотрёт диск или раздел"
    if exe == "mkfs" or exe.startswith(("mkfs.", "newfs")):
        return f"{exe} отформатирует раздел, данные на нём пропадут"
    if exe in ("chmod", "chown", "chgrp") and "R" in short_flags(ws[1:]):
        for t in positional(ws[1:])[1:]:
            p = resolve(t, cwd, env)
            if p in SYSTEM_DIRS:
                return f"{exe} -R {tilde(p)} поменяет права во всей папке, вернуть как было не получится"
    return None


def scan_bash(cmd, cwd, env, root, depth=0):
    """Находки (правило, почему) по порядку команд."""
    for stages in split_pipelines(cmd):
        fetched = False
        for seg in stages:
            raw = words(seg)
            for w in raw:
                m2 = re.match(r"^([A-Za-z_]\w*)=(.*)$", w)
                if not m2:
                    break
                env[m2.group(1)] = assigned(m2.group(2), cwd, env)
            ws = strip_env(raw)
            if not ws:
                continue
            if ws[0] == "export":
                for w in ws[1:]:
                    m2 = re.match(r"^([A-Za-z_]\w*)=(.*)$", w)
                    if m2:
                        env[m2.group(1)] = assigned(m2.group(2), cwd, env)
                continue
            if ws[0] == "cd" and len(ws) > 1:
                nxt = resolve(ws[1], cwd, env)
                if nxt:
                    cwd = nxt
                continue
            for found in (check_secret_delete(ws, cwd, env), check_secrets_cmd(ws, seg)):
                if found:
                    yield found
            why = check_destructive(ws, seg, cwd, env, root)
            if not why and fetched and runs_stdin(ws):
                why = f"curl | {os.path.basename(ws[0])} выполнит скрипт прямо из сети, не глядя"
            if why:
                yield "destructive", why
            fetched = fetched or os.path.basename(ws[0]) in ("curl", "wget")
            script = shell_script(ws)
            if script and depth < 3:
                yield from scan_bash(script, cwd, dict(env), root, depth + 1)


def check_bash(cmd, cwd, skip=()):
    env = {"HOME": HOME, "TMPDIR": os.environ.get("TMPDIR", "/tmp"), "PWD": cwd}
    return next((f for f in scan_bash(cmd, cwd, env, project_of(cwd)) if f[0] not in skip), None)


def check(tool, inp, cwd, skip=()):
    """(правило, почему) для первого правила, которое не в skip, или None."""
    cwd = os.path.normpath(cwd or os.getcwd())
    if tool == "Bash":
        return check_bash(str(inp.get("command") or ""), cwd, skip)
    found = None
    if tool == "Grep":
        # Grep с output_mode content выводит строки файла в переписку так же, как Read
        path, mask = str(inp.get("path") or ""), str(inp.get("glob") or "")
        name = mask[3:] if mask.startswith("**/") else mask
        if (path and is_secret(path)) or (name and (is_secret(name) or name not in CATCH_ALL and any(
                fnmatch.fnmatch(sample, name) for sample in SECRET_SAMPLES))):
            found = "secrets-read", f"поиск в {tilde(path or mask)}: это секрет, найденные строки попадут в переписку"
    path = inp.get("file_path") or inp.get("notebook_path") or ""
    if tool in ("Read", "Edit", "Write", "MultiEdit", "NotebookEdit") and path and is_secret(path):
        verb = "чтение" if tool == "Read" else "правка"
        found = "secrets-read", f"{verb} {tilde(path)}: это секрет, его содержимое попадёт в переписку"
    return found if found and found[0] not in skip else None


def call_key(sid, tool, inp):
    raw = json.dumps([sid, tool, inp.get("command") or inp.get("file_path") or inp.get("path") or inp.get("glob")
                      or ""], sort_keys=True)
    return hashlib.sha256(raw.encode()).hexdigest()[:16]


def pending_path(sid):
    return os.path.join(GUARD_STATE, re.sub(r"[^\w-]", "_", sid or "none") + ".json")


def load_pending(sid):
    try:
        with open(pending_path(sid)) as f:
            return json.load(f)
    except (OSError, ValueError):
        return {}


def save_pending(sid, data):
    os.makedirs(GUARD_STATE, exist_ok=True)
    data = {k: v for k, v in data.items() if time.time() - v < 3600}
    path = pending_path(sid)
    write_json(path, data)


def last_user(path):
    try:
        size = os.path.getsize(path)
        with open(path, "rb") as f:
            f.seek(max(0, size - TAIL))
            chunk = f.read()
    except (OSError, TypeError):
        return "", 0
    for raw in reversed(chunk.split(b"\n")):
        if not re.search(rb'"type":\s*"user"', raw):
            continue
        try:
            o = json.loads(raw.decode("utf-8", "replace"))
        except ValueError:
            continue
        if o.get("isMeta") or o.get("isCompactSummary"):
            continue
        c = (o.get("message") or {}).get("content")
        text = c if isinstance(c, str) else "\n".join(
            b.get("text", "") for b in c or [] if isinstance(b, dict) and b.get("type") == "text") if isinstance(c, list) else ""
        if not text:
            continue
        try:
            ts = calendar.timegm(time.strptime(o.get("timestamp", "")[:19], "%Y-%m-%dT%H:%M:%S"))
        except ValueError:
            ts = 0
        return text, ts
    return "", 0


def decide(payload):
    tool = payload.get("tool_name") or ""
    inp = payload.get("tool_input") if isinstance(payload.get("tool_input"), dict) else {}
    found = check(tool, inp, payload.get("cwd"), skipped())
    if not found:
        return None
    rule, why = found
    mode = payload.get("permission_mode") or "default"
    what = str(inp.get("command") or inp.get("file_path") or "")[:200].replace("\n", " ")
    if mode not in ("bypassPermissions", "auto", "dontAsk"):
        log(f"ask   {rule:<14} {what}")
        hold_badge(payload.get("session_id"), rule)
        return {"permissionDecision": "ask", "permissionDecisionReason": f"claudemeister guard: {why}"}
    sid = payload.get("session_id")
    key = call_key(sid, tool, inp)
    pending = load_pending(sid)
    text, ts = last_user(payload.get("transcript_path"))
    if key in pending and ts > pending[key] and YES.search(text.strip()):
        pending.pop(key)
        save_pending(sid, pending)
        log(f"allow {rule:<14} {what}")
        return {"permissionDecision": "allow", "permissionDecisionReason": "claudemeister guard: пользователь разрешил"}
    pending[key] = time.time()
    save_pending(sid, pending)
    log(f"hold  {rule:<14} {what}")
    hold_badge(sid, rule)
    return {"permissionDecision": "deny", "permissionDecisionReason": (
        f"claudemeister guard: {why}. Это не отказ: спроси пользователя одним коротким вопросом, выполнить ли это, и объясни "
        f"зачем. Если он ответит «да» — повтори ровно этот же вызов, и он пройдёт. Если нет — не делай и не ищи "
        f"обходной путь.")}


def hook():
    try:
        payload = json.load(sys.stdin)
    except ValueError:
        return 0
    on = enabled()
    sync_badge(on)
    if not isinstance(payload, dict) or not on:
        return 0
    try:
        out = decide(payload)
    except Exception as e:
        log(f"error {e!r}")
        return 0
    if out:
        print(json.dumps({"hookSpecificOutput": dict({"hookEventName": "PreToolUse"}, **out)}, ensure_ascii=False))
    return 0


def usage():
    print("claudemeister guard                   состояние и правила")
    print("claudemeister guard on | off          включить или выключить совсем")
    print("claudemeister guard skip <правило>    не проверять правило; unskip <правило> — вернуть")
    print("claudemeister guard test '<команда>'  что сделает guard с этой командой (в папке, где запущен)")
    print("claudemeister guard log               последние решения")
    print()
    print("Хук PreToolUse следит за чтением и удалением секретов и ключей и за необратимыми командами")
    print("(git reset --hard, push --force, rm -r вне проекта, curl | sh…). В обычном режиме Claude Code")
    print("спросит разрешение сам. В auto Claude спросит тебя в чате: ответишь «да» — этот вызов пройдёт.")


def status():
    skip = skipped()
    print(f"claudemeister guard: {'включён' if enabled() else 'выключен'}. Лог: {tilde(LOG)}")
    for rule, text in RULES.items():
        mark = "  выкл" if rule in skip else "  вкл "
        print(f"{mark} {rule:<11} {text}")


def main():
    args = sys.argv[1:]
    if args[:1] == ["guard-hook"]:
        return hook()
    rest = args[1:]
    if not rest:
        status()
        return 0
    cmd = rest[0]
    if cmd in ("-h", "--help"):
        usage()
        return 0
    if cmd in ("on", "off"):
        write_config(guard=cmd)
        sync_badge(cmd == "on")
        print(f"claudemeister guard {'включён' if cmd == 'on' else 'выключен'}")
        return 0
    if cmd in ("skip", "unskip") and len(rest) == 2:
        if rest[1] not in RULES:
            print(f"Нет правила «{rest[1]}». Есть: {', '.join(RULES)}", file=sys.stderr)
            return 2
        skip = skipped()
        skip = skip | {rest[1]} if cmd == "skip" else skip - {rest[1]}
        write_config(guard_skip=",".join(sorted(skip)))
        print(f"Правило {rest[1]} {'выключено' if cmd == 'skip' else 'включено'}")
        return 0
    if cmd == "test" and len(rest) >= 2:
        text = " ".join(rest[1:])
        tool, inp = ("Read", {"file_path": text}) if not re.search(r"\s", text) and is_secret(text) else \
            ("Bash", {"command": text})
        found = check(tool, inp, os.getcwd())
        if not found:
            print("пропущу")
        else:
            print(f"остановлю ({found[0]}{', правило выключено' if found[0] in skipped() else ''}): {found[1]}")
        return 0
    if cmd == "log":
        try:
            with open(LOG) as f:
                print("".join(f.readlines()[-30:]), end="")
        except OSError:
            print("Лог пуст.")
        return 0
    usage()
    return 2


if __name__ == "__main__":
    sys.exit(main())
