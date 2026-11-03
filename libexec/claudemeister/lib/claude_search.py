import glob
import json
import math
import os
import re
import shlex
from datetime import datetime

import stemmer
from mnrhlib import CLAUDE_HOME, HOME, UUID_RE, tilde, write_json

CLAUDE = CLAUDE_HOME
PROJECTS = os.path.join(CLAUDE, "projects")
INDEX = os.path.join(os.environ.get("XDG_CACHE_HOME") or os.path.join(HOME, ".cache"), "mnrh", "sessions-index")
VERSION = 3
# основы слов лежат рядом в <id>.terms.json: хукам нужен только текст, а основы досчитываются при поиске
TERMS_VERSION = 1
UUID = UUID_RE
REMINDER = re.compile(r"<system-reminder>.*?</system-reminder>", re.S)
LOCAL = ("<command-name>", "<local-command-stdout>", "<local-command-stderr>", "<local-command-caveat>",
         "<task-notification>")
ROLES = {"user": "ты", "claude": "claude", "tool": "команда", "error": "ошибка", "summary": "сводка"}
TOOL_CAP = 400
# ошибки инструментов индексируются, обычный вывод нет; у длинной ошибки берутся начало и конец, где обычно причина
ERROR_HEAD, ERROR_TAIL = 600, 900
# ошибки самого Claude Code, а не команды: отказ в разрешении, «сначала прочитай файл» и т. п.
ERROR_NOISE = ("Permission for this action was denied", "The user doesn't want to proceed",
               "File has not been read yet", "File has been modified since", "Blocked: sleep",
               "[Request interrupted")
READ_BUDGET = 40000


def index_path(sid):
    return os.path.join(INDEX, sid + ".json")


def terms_path(sid):
    return os.path.join(INDEX, sid + ".terms.json")


def drop(sid):
    for p in (index_path(sid), terms_path(sid)):
        try:
            os.remove(p)
        except OSError:
            pass


def sessions():
    out = {}
    for path in glob.glob(os.path.join(glob.escape(PROJECTS), "*", "*.jsonl")):
        sid = os.path.basename(path)[:-6]
        if UUID.match(sid) and os.path.isfile(path):
            out[sid] = path
    return out


def tool_line(block):
    name = block.get("name") or "?"
    inp = block.get("input") if isinstance(block.get("input"), dict) else {}
    if name == "Bash":
        text = "$ " + str(inp.get("command") or "")
    else:
        arg = next((str(inp[k]) for k in ("file_path", "path", "pattern", "url", "query", "description", "prompt")
                    if inp.get(k)), "")
        text = f"{name} {arg}".strip()
    return text[:TOOL_CAP]


def error_text(block):
    c = block.get("content")
    if isinstance(c, list):
        c = "\n".join(b.get("text", "") for b in c if isinstance(b, dict) and b.get("type") == "text")
    if not isinstance(c, str):
        return ""
    c = c.replace("<tool_use_error>", "").replace("</tool_use_error>", "").strip()
    if not c or any(c.startswith(n) or n in c[:120] for n in ERROR_NOISE):
        return ""
    if len(c) > ERROR_HEAD + ERROR_TAIL:
        c = c[:ERROR_HEAD] + f"\n… (−{len(c) - ERROR_HEAD - ERROR_TAIL} симв.) …\n" + c[-ERROR_TAIL:]
    return c


def extract(o):
    kind = o.get("type")
    msg = o.get("message") or {}
    content = msg.get("content") if isinstance(msg, dict) else None
    out = []
    if kind == "user":
        if o.get("isMeta"):
            return out
        role = "summary" if o.get("isCompactSummary") else "user"
        texts = [content] if isinstance(content, str) else \
            [b.get("text", "") for b in content or [] if isinstance(b, dict) and b.get("type") == "text"]
        for t in texts:
            if not isinstance(t, str) or any(m in t for m in LOCAL):
                continue
            t = REMINDER.sub("", t).strip()
            if t:
                out.append((role, t))
        if isinstance(content, list):
            for b in content:
                if isinstance(b, dict) and b.get("type") == "tool_result" and b.get("is_error"):
                    t = error_text(b)
                    if t:
                        out.append(("error", t))
    elif kind == "assistant" and isinstance(content, list):
        for b in content:
            if not isinstance(b, dict):
                continue
            if b.get("type") == "text" and (b.get("text") or "").strip():
                out.append(("claude", b["text"].strip()))
            elif b.get("type") == "tool_use":
                out.append(("tool", tool_line(b)))
    return out


def load_index(sid):
    try:
        with open(index_path(sid), encoding="utf-8") as f:
            data = json.load(f)
        return data if data.get("v") == VERSION else None
    except (OSError, ValueError):
        return None


def save_index(sid, data):
    os.makedirs(INDEX, mode=0o700, exist_ok=True)
    write_json(index_path(sid), data, mode=0o600, ensure_ascii=False, separators=(",", ":"))


def update(sid, path):
    try:
        st = os.stat(path)
    except OSError:
        return None
    data = load_index(sid)
    if not data or data.get("path") != path or data.get("ino") != st.st_ino or data.get("offset", 0) > st.st_size:
        data = {"v": VERSION, "gen": os.urandom(4).hex(), "path": path, "ino": st.st_ino, "offset": 0, "cwd": None,
                "custom": None, "ai": None, "prompt": None, "first": None, "last": None, "msgs": []}
    if data["offset"] == st.st_size:
        return data
    with open(path, "rb") as f:
        f.seek(data["offset"])
        chunk = f.read(st.st_size - data["offset"])
    end = chunk.rfind(b"\n") + 1
    for raw in chunk[:end].split(b"\n"):
        if not raw.strip():
            continue
        try:
            o = json.loads(raw.decode("utf-8", "replace"))
        except ValueError:
            continue
        if not isinstance(o, dict):
            continue
        kind = o.get("type")
        if kind == "custom-title" and o.get("customTitle"):
            data["custom"] = o["customTitle"]
        elif kind == "ai-title" and o.get("aiTitle"):
            data["ai"] = o["aiTitle"]
        elif kind == "last-prompt" and o.get("lastPrompt"):
            data["prompt"] = o["lastPrompt"]
        if data["cwd"] is None and o.get("cwd"):
            data["cwd"] = o["cwd"]
        ts = o.get("timestamp") if isinstance(o.get("timestamp"), str) else None
        for role, text in extract(o):
            data["msgs"].append([role, ts, text])
            if ts:
                data["first"] = data["first"] or ts
                data["last"] = ts
    data["offset"] += end
    if end:
        save_index(sid, data)
    return data


def refresh():
    found = sessions()
    out = {}
    for sid, path in found.items():
        data = update(sid, path)
        if data:
            out[sid] = data
    for f in glob.glob(os.path.join(glob.escape(INDEX), "*.json")):
        if os.path.basename(f).split(".")[0] not in found:
            try:
                os.remove(f)
            except OSError:
                pass
    return out


def title(sid, data):
    try:
        with open(os.path.join(os.path.dirname(data["path"]), sid, "custom-title.json")) as f:
            custom = json.load(f).get("customTitle")
            if custom:
                return custom
    except (OSError, ValueError, AttributeError):
        pass
    return data.get("custom") or data.get("ai") or data.get("prompt") or "без названия"


def folder(data):
    from mnrhlib import DEV
    cwd = home_of(data)
    if cwd.startswith(DEV + "/") and "/" not in cwd[len(DEV) + 1:]:
        return cwd[len(DEV) + 1:]
    return tilde(cwd) if cwd else os.path.basename(os.path.dirname(data["path"]))


_HOMES = {}


def home_of(data):
    pdir = os.path.basename(os.path.dirname(data["path"]))
    if pdir not in _HOMES:
        from session_store import decode
        _HOMES[pdir] = decode(pdir)
    return _HOMES[pdir] or data.get("cwd") or ""


def when(ts, year=False):
    if not ts:
        return "?"
    try:
        dt = datetime.fromisoformat(ts.replace("Z", "+00:00")).astimezone()
    except ValueError:
        return ts[:16]
    return dt.strftime("%d.%m.%Y %H:%M" if year else "%d.%m %H:%M")


def age_days(ts):
    try:
        dt = datetime.fromisoformat(ts.replace("Z", "+00:00"))
    except (AttributeError, ValueError):
        return None
    return (datetime.now(dt.tzinfo) - dt).total_seconds() / 86400


def load_terms(sid, data):
    """Основы слов по сообщениям сессии: длина каждого сообщения и для каждой основы «i» или «i:сколько раз».
    Досчитывает только сообщения, добавленные в индекс после прошлого раза."""
    try:
        with open(terms_path(sid), encoding="utf-8") as f:
            t = json.load(f)
    except (OSError, ValueError):
        t = None
    n = len(data["msgs"])
    if not isinstance(t, dict) or t.get("v") != TERMS_VERSION or t.get("gen") != data.get("gen") \
            or not isinstance(t.get("n"), int) or t["n"] > n:
        t = {"v": TERMS_VERSION, "gen": data.get("gen"), "n": 0, "lens": [], "post": {}}
    if t["n"] < n:
        new = {}
        for i in range(t["n"], n):
            stems = stemmer.terms(data["msgs"][i][2])
            t["lens"].append(len(stems))
            counts = {}
            for x in stems:
                counts[x] = counts.get(x, 0) + 1
            for x, c in counts.items():
                new.setdefault(x, []).append(str(i) if c == 1 else f"{i}:{c}")
        post = t["post"]
        for x, items in new.items():
            add = ",".join(items)
            post[x] = post[x] + "," + add if x in post else add
        t["n"] = n
        os.makedirs(INDEX, mode=0o700, exist_ok=True)
        write_json(terms_path(sid), t, mode=0o600, ensure_ascii=False, separators=(",", ":"))
    return t


def postings(raw):
    out = {}
    for item in raw.split(","):
        i, _, c = item.partition(":")
        out[int(i)] = int(c) if c else 1
    return out


class Query:
    """Запрос: основы слов, фразы (слова в кавычках или через дефис) и исходные слова для подсказок."""
    # слово запроса, которое уже само себе основа (подпис, fastl, keystor), от 3 букв находит и более длинные:
    # fastl → fastlane. Полное слово ищется только по своей основе, иначе «подпись» нашла бы и «подписку»
    PREFIX_MIN = 3
    PREFIX_WEIGHT = 0.5

    def __init__(self, text):
        try:
            parts = shlex.split(text)
        except ValueError:
            parts = text.split()
        self.phrases, self.terms, self.words, self.prefix = [], [], {}, set()
        for part in parts:
            sp = stemmer.spans(part)
            for x, a, b in sp:
                if x not in self.terms:
                    self.terms.append(x)
                    self.words[x] = part[a:b].lower()
                    if len(x) >= self.PREFIX_MIN and x == self.words[x].replace("ё", "е"):
                        self.prefix.add(x)
            if len(sp) > 1:
                self.phrases.append([x for x, _, _ in sp])

    def matches(self, stem, term):
        return stem == term or (term in self.prefix and stem.startswith(term))

    def variants(self, vocab):
        """Для каждой основы запроса — основы индекса, которые она находит, с весом."""
        out = {}
        for q in self.terms:
            got = {q: 1.0} if q in vocab else {}
            if q in self.prefix:
                for v in vocab:
                    if v != q and v.startswith(q):
                        got[v] = self.PREFIX_WEIGHT
            out[q] = got
        return out

    def phrase_ok(self, text):
        if not self.phrases:
            return True
        stems = [x for x, _, _ in stemmer.spans(text)]
        for ph in self.phrases:
            if not any(all(self.matches(stems[i + k], q) for k, q in enumerate(ph))
                       for i in range(len(stems) - len(ph) + 1)):
                return False
        return True


def term_hits(t, variants):
    """{основа запроса: {номер сообщения: вес совпадений}} для одной сессии."""
    post = t["post"]
    out = {}
    for q, vs in variants.items():
        hits = {}
        for v, w in vs.items():
            raw = post.get(v)
            if raw:
                for i, c in postings(raw).items():
                    hits[i] = hits.get(i, 0) + c * w
        out[q] = hits
    return out


def snippet(text, query, idf=None, width=320):
    """Кусок текста вокруг места, где рядом больше всего разных слов запроса (редкие слова весят больше)."""
    flat = " ".join(text.split())
    found = [(a, q) for x, a, _ in stemmer.spans(flat) for q in query.terms if query.matches(x, q)]
    pos = 0
    if found:
        best = -1.0
        for a, _ in found:
            near = {q for b, q in found if a <= b < a + width * 2 // 3}
            score = sum((idf or {}).get(q, 1.0) for q in near)
            if score > best:
                best, pos = score, a
    start = max(0, pos - width // 3)
    piece = flat[start:start + width]
    return ("…" if start else "") + piece + ("…" if start + width < len(flat) else "")


def matches_project(data, project):
    p = project.lower().strip()
    cwd = home_of(data).lower()
    return p in (os.path.basename(cwd), cwd) or os.path.basename(cwd).startswith(p) or \
        (p in ("~", "root", "home") and cwd == HOME.lower())


K1, B = 1.2, 0.75


def search(query, project=None, days=None, roles=None, limit=8, exclude=None):
    """BM25 по сообщениям: все слова в одном сообщении (или хотя бы половина, если так ничего нет),
    сессии по сумме трёх лучших сообщений с небольшой прибавкой за свежесть."""
    if not query.strip():
        return "Пустой запрос: дай хотя бы одно слово.", True
    q = Query(query)
    if not q.terms:
        return f"В запросе «{query}» одни служебные слова (как, мы, в…), а они не индексируются. Дай слова по теме.", True
    idx = refresh()
    exclude = set(exclude or [])
    scope = []
    for sid, data in idx.items():
        if sid in exclude or (project and not matches_project(data, project)):
            continue
        if days and (age_days(data.get("last")) or 0) > days:
            continue
        scope.append((sid, data, load_terms(sid, data)))
    vocab = set()
    for _, _, t in scope:
        vocab.update(t["post"])
    variants = q.variants(vocab)
    total = sum(len(t["lens"]) for _, _, t in scope) or 1
    avgdl = sum(sum(t["lens"]) for _, _, t in scope) / total or 1.0
    per_session = [(sid, data, t, term_hits(t, variants)) for sid, data, t in scope]
    df = {x: sum(len(h[x]) for *_, h in per_session) for x in q.terms}
    idf = {x: math.log(1 + (total - df[x] + 0.5) / (df[x] + 0.5)) for x in q.terms}
    need = len(q.terms)
    hits, partial = [], []
    for sid, data, t, h in per_session:
        candidates = {}
        for x in q.terms:
            for i in h[x]:
                candidates[i] = candidates.get(i, 0) + 1
        full, half = [], []
        for i, got in candidates.items():
            role = data["msgs"][i][0]
            if roles and role not in roles:
                continue
            if got < need and (need == 1 or got * 2 < need):
                continue
            norm = K1 * (1 - B + B * t["lens"][i] / avgdl)
            score = sum(idf[x] * h[x][i] * (K1 + 1) / (h[x][i] + norm) for x in q.terms if i in h[x])
            if got == need and q.phrase_ok(data["msgs"][i][2]):
                full.append((score, i))
            else:
                half.append((score, i))
        for found, bucket in ((full, hits), (half, partial)):
            if found:
                found.sort(key=lambda x: (-x[0], x[1]))
                top = sum(s * w for (s, _), w in zip(found, (1, 0.5, 0.25)))
                age = age_days(data.get("last"))
                fresh = 1 + 0.3 / (1 + (age if age is not None and age > 0 else 0) / 14)
                bucket.append((top * fresh, sid, data, [i for _, i in found]))
    exact = bool(hits)
    chosen = sorted(hits or partial, key=lambda x: (-x[0], x[1]))
    where = (f" в проекте «{project}»" if project else "") + (f" за {days} дн." if days else "")
    if not chosen:
        missing = [q.words[x] for x in q.terms if not df[x]]
        text = f"По запросу «{query}»{where} ничего не нашёл (просмотрено сессий: {len(scope)})."
        if missing:
            text += (" Нигде не встречается: " + ", ".join(missing) + ". Формы слова уже учтены "
                     "(сессия найдёт и сессии), так что проверь написание или попробуй другое слово.")
        return text, False
    lines = [f"Сессий с совпадениями{where}: {len(chosen)}"
             f"{'' if exact else ' (только частичные: не все слова в одном сообщении)'}. "
             f"Показаны {min(limit, len(chosen))}, лучшие совпадения сверху."]
    for _, sid, data, found in chosen[:limit]:
        lines.append("")
        lines.append(f"■ {sid[:8]}  {folder(data)}  {when(data.get('last'))}  «{title(sid, data)}»  "
                     f"совпадений: {len(found)}")
        for i in found[:3]:
            role, ts, text = data["msgs"][i]
            lines.append(f"  #{i} [{ROLES.get(role, role)} {when(ts)}] {snippet(text, q, idf)}")
        if len(found) > 3:
            more = ", ".join(f"#{i}" for i in found[3:18])
            lines.append(f"  ещё: {more}{' …' if len(found) > 18 else ''}")
    lines.append("")
    lines.append("Прочитать место целиком: session_read с id и at=<номер после #>.")
    return "\n".join(lines), False


def resolve(ref, idx):
    ref = (ref or "").strip().lower()
    if not ref:
        return None, "Нужен id сессии (первые 8 символов из session_search)."
    hits = [sid for sid in idx if sid.startswith(ref)]
    if not hits:
        hits = [sid for sid, d in idx.items() if ref in title(sid, d).lower()]
    if len(hits) == 1:
        return hits[0], None
    if hits:
        return None, f"«{ref}» подходит к {len(hits)} сессиям, дай больше символов id."
    return None, f"Сессии «{ref}» нет."


def read(ref, at=None, query=None, before=2, after=8, max_chars=2000, full=False):
    idx = refresh()
    sid, err = resolve(ref, idx)
    if err:
        return err, True
    data = idx[sid]
    msgs = data["msgs"]
    total = len(msgs)
    head = (f"Сессия {sid}  «{title(sid, data)}»\nпапка: {tilde(home_of(data) or '?')} · "
            f"{when(data.get('first'), True)} — {when(data.get('last'), True)} · сообщений: {total}")
    if not total:
        return head + "\nВ ней нет ни одного сообщения.", False
    note = ""
    if query:
        q = Query(query)
        t = load_terms(sid, data)
        h = term_hits(t, q.variants(t["post"]))
        found = sorted(i for i in set.intersection(*(set(h[x]) for x in q.terms)) if q.phrase_ok(msgs[i][2])) \
            if q.terms else []
        if not found:
            return head + f"\n«{query}» в этой сессии не встречается.", False
        at = found[0] if at is None else at
        if len(found) > 1:
            note = "Совпадения в сессии: " + ", ".join(f"#{i}" for i in found[:30]) + (" …" if len(found) > 30 else "")
    if at is None:
        at = 0
    at = int(at)
    if at < 0:
        at = max(0, total + at)
    at = min(at, total - 1)
    before, after = max(0, int(before)), max(0, int(after))
    lo, hi = max(0, at - before), min(total, at + after + 1)
    lines = [head]
    if note:
        lines.append(note)
    if lo:
        lines.append(f"… раньше ещё {lo} (at={max(0, lo - 1)})")
    budget = READ_BUDGET
    shown_to = lo
    for i in range(lo, hi):
        role, ts, text = msgs[i]
        cap = len(text) if full else max_chars
        body = text if len(text) <= cap else text[:cap] + f"\n… (+{len(text) - cap} симв., целиком: at={i} full=true)"
        block = f"\n#{i} [{ROLES.get(role, role)} {when(ts)}]{' ◀' if i == at else ''}\n{body}"
        if budget - len(block) < 0 and i > at:
            break
        budget -= len(block)
        lines.append(block)
        shown_to = i + 1
    if shown_to < total:
        lines.append(f"\n… дальше ещё {total - shown_to} (at={shown_to})")
    return "\n".join(lines), False


SEARCH_TOOL = {
    "name": "session_search",
    "description": (
        "Поиск по всем прошлым сессиям Claude Code на этом Mac (все проекты): запросы пользователя, твои ответы, "
        "выполненные команды, их ошибки (вывод с ненулевым кодом и т. п., обычный вывод не ищется) и сводки после "
        "/compact. Чтобы найти, как уже чинили такую же ошибку, ищи её текст с roles=[\"error\"]. Используй, когда пользователь ссылается на прошлую работу "
        "(«как мы делали…», «в прошлый раз», «в сессии sightra»), или когда решение, скорее всего, уже "
        "находили раньше. Все слова должны встретиться в одном сообщении, регистр и форма слова не важны "
        "(сессия найдёт сессии, signing найдёт signed), обрубок слова от 3 букв (fastl, подпис) находит "
        "и слова длиннее. Служебные слова (как, мы, the) не учитываются. Фразу или слова через дефис "
        "ищет подряд. Сверху сессии и сообщения, где совпадение лучше. Текущая сессия не ищется. "
        "Дальше открывай найденное через session_read."),
    "annotations": {"readOnlyHint": True},
    "inputSchema": {
        "type": "object",
        "properties": {
            "query": {"type": "string", "description": "слова через пробел, фраза в кавычках"},
            "project": {"type": "string", "description": "только в этом проекте: имя папки или его начало, «~» — домашний каталог"},
            "days": {"type": "integer", "description": "только сессии, в которых что-то было за последние N дней"},
            "roles": {"type": "array", "items": {"type": "string", "enum": list(ROLES)},
                      "description": "где искать: user — запросы, claude — ответы, tool — команды, error — ошибки команд, "
                                     "summary — сводки; по умолчанию везде"},
            "limit": {"type": "integer", "description": "сколько сессий показать, по умолчанию 8"},
        },
        "required": ["query"],
    },
}

READ_TOOL = {
    "name": "session_read",
    "description": (
        "Прочитать кусок прошлой сессии Claude Code: сообщения вокруг номера из session_search. "
        "Без at и query — начало сессии, at=-10 — последние сообщения. Длинные сообщения обрезаются, "
        "full=true — целиком."),
    "annotations": {"readOnlyHint": True},
    "inputSchema": {
        "type": "object",
        "properties": {
            "id": {"type": "string", "description": "id сессии или его начало (8 символов из session_search)"},
            "at": {"type": "integer", "description": "номер сообщения (#N из session_search); отрицательный — с конца"},
            "query": {"type": "string", "description": "перейти к первому сообщению с этими словами в этой сессии"},
            "before": {"type": "integer", "description": "сколько сообщений до, по умолчанию 2"},
            "after": {"type": "integer", "description": "сколько сообщений после, по умолчанию 8"},
            "full": {"type": "boolean", "description": "не обрезать длинные сообщения"},
        },
        "required": ["id"],
    },
}

TOOLS = [SEARCH_TOOL, READ_TOOL]


def handle(name, a, current=None):
    if name == "session_search":
        roles = [r for r in a.get("roles") or [] if r in ROLES] or None
        return search(str(a.get("query") or ""), project=a.get("project") or None, days=a.get("days") or None,
                      roles=roles, limit=int(a.get("limit") or 8), exclude=[current] if current else None)
    return read(a.get("id"), at=a.get("at"), query=a.get("query") or None,
                before=a.get("before", 2), after=a.get("after", 8), full=bool(a.get("full")))
