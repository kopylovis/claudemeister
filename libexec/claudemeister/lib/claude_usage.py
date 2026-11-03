#!/usr/bin/env python3
"""claudemeister usage: токены и активное время по проектам и моделям из переписок Claude Code.

Берётся поле usage у ответов модели (одно на запрос, хотя строк в переписке у ответа бывает несколько) и время
строк. Активное время — сумма промежутков меньше 5 минут между соседними строками, вместе с субагентами.
Итоги по дням хранятся в ~/.cache/mnrh/usage/<id>.json и дочитываются только по новым строкам.
"""
import glob
import json
import os
import re
import sys
import time
from datetime import datetime

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from mnrhlib import HOME, has_flag, write_json  # noqa: E402
import claude_search  # noqa: E402

CACHE = os.path.join(os.environ.get("XDG_CACHE_HOME") or os.path.join(HOME, ".cache"), "mnrh", "usage")
VERSION = 1
GAP = 300  # перерыв дольше 5 минут не считается работой
KINDS = ("input_tokens", "output_tokens", "cache_read_input_tokens", "cache_creation_input_tokens")
KIND_NAMES = ("вход", "выход", "кэш чт.", "кэш зап.")
RECENT_IDS = 64
TS = re.compile(rb'"timestamp":\s*"([^"]+)"')
CWD = re.compile(rb'"cwd":\s*"((?:[^"\\]|\\.)*)"')
TITLES = ((b'"custom-title"', "custom", "customTitle"), (b'"ai-title"', "ai", "aiTitle"),
          (b'"last-prompt"', "prompt", "lastPrompt"))


def cache_path(sid):
    return os.path.join(CACHE, sid + ".json")


def drop(sid):
    try:
        os.remove(cache_path(sid))
    except OSError:
        pass


def epoch(ts):
    try:
        return datetime.fromisoformat(ts.replace("Z", "+00:00")).timestamp()
    except ValueError:
        return None


def files_of(path):
    """Переписка сессии и переписки её субагентов: {ключ: путь}."""
    sid = os.path.basename(path)[:-6]
    out = {"main": path}
    for p in glob.glob(os.path.join(glob.escape(os.path.join(os.path.dirname(path), sid)), "subagents", "*.jsonl")):
        out["subagents/" + os.path.basename(p)] = p
    return out


def fresh(path):
    return {"v": VERSION, "path": path, "cwd": None, "custom": None, "ai": None, "prompt": None,
            "files": {}, "days": {}, "spans": []}


def merge_spans(spans):
    out = []
    for a, b in sorted(spans):
        if out and a <= out[-1][1]:
            out[-1][1] = max(out[-1][1], b)
        else:
            out.append([a, b])
    return out


def read_file(data, key, path, st):
    f = data["files"].setdefault(key, {"ino": st.st_ino, "offset": 0, "last": None, "ids": []})
    with open(path, "rb") as fh:
        fh.seek(f["offset"])
        chunk = fh.read(st.st_size - f["offset"])
    end = chunk.rfind(b"\n") + 1
    days, spans, ids = data["days"], [], f["ids"]
    seen = set(ids)
    last = f["last"]
    for raw in chunk[:end].split(b"\n"):
        m = TS.search(raw)
        if not m:
            if key == "main":
                note_title(data, raw)
            continue
        t = epoch(m.group(1).decode("ascii", "replace"))
        if t is None:
            continue
        if last is not None and 0 <= t - last < GAP:
            spans.append([round(last), round(t)])
        if last is None or t > last:
            last = t
        if key == "main":
            if data["cwd"] is None:
                c = CWD.search(raw)
                if c:
                    try:
                        data["cwd"] = json.loads(b'"' + c.group(1) + b'"')
                    except ValueError:
                        pass
            note_title(data, raw)
        if b'"usage"' not in raw or b'"assistant"' not in raw:
            continue
        try:
            o = json.loads(raw.decode("utf-8", "replace"))
        except ValueError:
            continue
        msg = o.get("message") if isinstance(o, dict) and o.get("type") == "assistant" else None
        if not isinstance(msg, dict) or not isinstance(msg.get("usage"), dict):
            continue
        model = msg.get("model")
        if not isinstance(model, str) or not model or model.startswith("<"):
            continue
        mid = msg.get("id") or o.get("requestId") or o.get("uuid")
        if mid in seen:  # у ответа из нескольких блоков usage повторяется в каждой строке
            continue
        seen.add(mid)
        ids.append(mid)
        u = msg["usage"]
        row = days.setdefault(time.strftime("%Y-%m-%d", time.localtime(t)), {}).setdefault(model, [0] * 5)
        for i, k in enumerate(KINDS):
            v = u.get(k)
            if isinstance(v, int):
                row[i] += v
        row[4] += 1
    f["ids"] = ids[-RECENT_IDS:]
    f["last"] = last
    f["offset"] += end
    if spans:
        data["spans"] = merge_spans(data["spans"] + spans)
    return bool(end)


def note_title(data, raw):
    for marker, key, field in TITLES:
        if marker in raw:
            try:
                o = json.loads(raw.decode("utf-8", "replace"))
            except ValueError:
                return
            if isinstance(o, dict) and o.get(field):
                data[key] = o[field]
            return


def update(sid, path):
    try:
        with open(cache_path(sid), encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, ValueError):
        data = None
    files = files_of(path)
    stats = {}
    for key, p in files.items():
        try:
            stats[key] = os.stat(p)
        except OSError:
            pass
    if "main" not in stats:
        return None
    # переписанный файл (slim) или пропавший субагент: итоги по дням не вычесть, считаем заново
    if not isinstance(data, dict) or data.get("v") != VERSION or data.get("path") != path or any(
            key not in stats or stats[key].st_ino != f.get("ino") or f.get("offset", 0) > stats[key].st_size
            for key, f in data.get("files", {}).items()):
        data = fresh(path)
    changed = False
    for key, st in stats.items():
        if data["files"].get(key, {}).get("offset") != st.st_size:
            changed = read_file(data, key, files[key], st) or changed
    if changed or not os.path.exists(cache_path(sid)):
        os.makedirs(CACHE, mode=0o700, exist_ok=True)
        write_json(cache_path(sid), data, mode=0o600, ensure_ascii=False, separators=(",", ":"))
    return data


def refresh():
    found = claude_search.sessions()
    out = {}
    for sid, path in found.items():
        try:
            data = update(sid, path)
        except OSError:
            data = None
        if data:
            out[sid] = data
    for f in glob.glob(os.path.join(glob.escape(CACHE), "*.json")):
        if os.path.basename(f)[:-5] not in found:
            try:
                os.remove(f)
            except OSError:
                pass
    return out


def period_start(days):
    """Полночь первого дня периода: days=1 — сегодня, 7 — сегодня и 6 дней до него."""
    lt = time.localtime()
    return time.mktime((lt.tm_year, lt.tm_mon, lt.tm_mday - (days - 1), 0, 0, 0, 0, 0, -1))


def collect(days=7, project=None):
    """Сессии с расходом за период: [{sid, data, models: {модель: [4 вида, запросы]}, active, total}]."""
    since = period_start(days) if days else 0
    since_day = time.strftime("%Y-%m-%d", time.localtime(since)) if days else ""
    now = time.time()
    out = []
    for sid, data in refresh().items():
        if project and not claude_search.matches_project(data, project):
            continue
        models = {}
        for day, per in data["days"].items():
            if day < since_day:
                continue
            for model, row in per.items():
                acc = models.setdefault(model, [0] * 5)
                for i in range(5):
                    acc[i] += row[i]
        active = sum(max(0, min(b, now) - max(a, since)) for a, b in data["spans"] if b > since)
        if not models and active < 60:
            continue
        last = data["spans"][-1][1] if data["spans"] else None
        out.append({"sid": sid, "data": data, "models": models, "active": active, "last": last,
                    "total": sum(sum(r[:4]) for r in models.values())})
    return out


def num(n):
    if n >= 10 ** 9:
        return f"{n / 1e9:.1f} млрд"
    if n >= 10 ** 7:
        return f"{n / 1e6:.0f} млн"
    if n >= 10 ** 6:
        return f"{n / 1e6:.1f} млн"
    if n >= 10 ** 4:
        return f"{n / 1e3:.0f} тыс."
    if n >= 1000:
        return f"{n / 1e3:.1f} тыс."
    return str(n)


def dur(sec):
    m = int(round(sec / 60))
    return f"{m // 60} ч {m % 60:02d} мин" if m >= 60 else f"{m} мин"


def short(name, n=40):
    return name if len(name) <= n else "…" + name[-(n - 1):]


def table(title, head, rows):
    """Строки с выравниванием: первая колонка влево, остальные вправо."""
    rows = [head] + rows
    widths = [max(len(r[i]) for r in rows) for i in range(len(head))]
    lines = [title]
    for r in rows:
        lines.append("  " + r[0].ljust(widths[0]) + "".join("  " + c.rjust(w) for c, w in zip(r[1:], widths[1:])))
    return lines


def report(days=7, project=None, top=5):
    rows = collect(days, project)
    period = f"за {days} дн. (с {time.strftime('%d.%m', time.localtime(period_start(days)))})" if days \
        else "за всё время"
    where = f" в проекте «{project}»" if project else ""
    if not rows:
        return f"Claude Code {period}{where}: ни одной сессии с расходом.", False
    total = [0] * 5
    by_project, by_model = {}, {}
    for r in rows:
        name = short(claude_search.folder(r["data"]))
        p = by_project.setdefault(name, {"sessions": 0, "active": 0, "tok": [0] * 5})
        p["sessions"] += 1
        p["active"] += r["active"]
        for model, row in r["models"].items():
            m = by_model.setdefault(model, {"sessions": 0, "tok": [0] * 5})
            m["sessions"] += 1
            for i in range(5):
                m["tok"][i] += row[i]
                p["tok"][i] += row[i]
                total[i] += row[i]
    active = sum(r["active"] for r in rows)
    lines = [f"Claude Code {period}{where}: сессий {len(rows)}, активно {dur(active)}, запросов к модели {total[4]}.",
             f"Токены: всего {num(sum(total[:4]))}; " + ", ".join(
                 f"{n} {num(v)}" for n, v in zip(("вход", "выход", "чтение кэша", "запись в кэш"), total)) + ".",
             ""]
    projects = sorted(by_project.items(), key=lambda kv: (-sum(kv[1]["tok"][:4]), kv[0]))
    lines += table("По проектам:", ["проект", "сессий", "время", "запросов", *KIND_NAMES],
                   [[name, str(p["sessions"]), dur(p["active"]), str(p["tok"][4]), *map(num, p["tok"][:4])]
                    for name, p in projects])
    lines.append("")
    models = sorted(by_model.items(), key=lambda kv: (-sum(kv[1]["tok"][:4]), kv[0]))
    lines += table("По моделям:", ["модель", "сессий", "запросов", *KIND_NAMES],
                   [[name, str(m["sessions"]), str(m["tok"][4]), *map(num, m["tok"][:4])] for name, m in models])
    heavy = sorted(rows, key=lambda r: -r["total"])[:max(0, top)]
    if heavy:
        lines += ["", "Самые тяжёлые сессии (по сумме токенов):"]
        for r in heavy:
            out = sum(row[1] for row in r["models"].values())
            last = time.strftime("%d.%m %H:%M", time.localtime(r["last"])) if r["last"] else "?"
            lines.append(f"  {r['sid'][:8]}  {short(claude_search.folder(r['data']))}  {last}  {dur(r['active'])}  "
                         f"всего {num(r['total'])}, выход {num(out)}  «{claude_search.title(r['sid'], r['data'])}»")
    lines += ["", "Вход — токены без кэша; время — промежутки меньше 5 минут между событиями, вместе с субагентами."]
    return "\n".join(lines), False


TOOL = {
    "name": "usage_report",
    "description": (
        "Расход Claude Code на этом Mac за период: по проектам и моделям число сессий, активное время, запросы "
        "к модели и токены (вход, выход, чтение и запись кэша), плюс самые тяжёлые сессии с названиями. Только "
        "читает переписки. Используй, когда спрашивают, сколько ушло токенов или времени, какой проект или "
        "модель съедает больше, что за сессия так раздулась."),
    "annotations": {"readOnlyHint": True},
    "inputSchema": {
        "type": "object",
        "properties": {
            "days": {"type": "integer", "description": "за сколько последних дней, считая сегодня; по умолчанию 7, 0 — за всё время"},
            "project": {"type": "string", "description": "только этот проект: имя папки или его начало, «~» — домашний каталог"},
            "top": {"type": "integer", "description": "сколько самых тяжёлых сессий показать, по умолчанию 5"},
        },
    },
}


def handle(a):
    days = a.get("days")
    days = 7 if days is None else int(days)
    if days < 0:
        return "days — число дней, 0 или больше.", True
    return report(days=days, project=a.get("project") or None, top=int(a.get("top") or 5))


USAGE = """claudemeister usage [--days N] [--project P] [--top N]
  токены и активное время Claude Code по проектам и моделям
  --days N     за N последних дней, считая сегодня (по умолчанию 7; 0 — за всё время)
  --project P  только этот проект: имя папки или его начало, ~ — домашний каталог
  --top N      сколько самых тяжёлых сессий показать (по умолчанию 5)"""


def main(argv):
    if has_flag(argv, "-h", "--help"):
        print(USAGE)
        return 0
    opts = {"--days": "7", "--project": None, "--top": "5"}
    short = {"-d": "--days", "-p": "--project", "-t": "--top"}
    i = 0
    while i < len(argv):
        a = short.get(argv[i], argv[i])
        if "=" in a and a.split("=", 1)[0] in opts:
            a, v = a.split("=", 1)
            opts[a] = v
            i += 1
            continue
        if a not in opts or i + 1 >= len(argv):
            print(f"claudemeister usage: не понимаю {argv[i]}\n\n{USAGE}", file=sys.stderr)
            return 2
        opts[a] = argv[i + 1]
        i += 2
    for k in ("--days", "--top"):
        if not opts[k].isdigit():
            print(f"claudemeister usage: после {k} нужно число", file=sys.stderr)
            return 2
    if not os.path.isdir(claude_search.PROJECTS):
        print("Сессий Claude Code на этом Mac нет.")
        return 0
    text, err = report(days=int(opts["--days"]), project=opts["--project"], top=int(opts["--top"]))
    print(text)
    return 1 if err else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
