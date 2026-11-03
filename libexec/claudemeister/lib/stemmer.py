"""Основы слов для поиска по сессиям: русский по Snowball, английский по первым шагам Porter.

Одна и та же функция terms() режет и индексируемый текст, и запрос, поэтому «сессия» находит «сессии»,
а «прокси-соединение» находит «прокси-соединения». Без сторонних пакетов, /usr/bin/python3 3.9.
"""
import re

WORD = re.compile(r"[^\W_]+")
CYR = re.compile(r"[а-я]")
LATIN = re.compile(r"^[a-z]+$")

# служебные слова не индексируются и выкидываются из запроса: «как мы делали подпись» = «делали подпись»
STOP = frozenset("""
и в во не что он на я с со как а то все она так его но да ты к у же вы за бы по только ее мне было вот от
меня еще нет о из ему теперь когда даже ну вдруг ли если уже или ни быть был него до вас нибудь опять уж
вам ведь там потом себя ничего ей может они тут где есть надо ней для мы тебя их чем была сам чтоб без
будто чего раз тоже себе под будет ж тогда кто этот того потому этого какой совсем ним здесь этом один
почти мой тем чтобы нее сейчас были куда зачем всех никогда можно при наконец два об другой хоть после
над больше тот через эти нас про всего них какая много разве три эту моя впрочем хорошо свою этой перед
иногда лучше чуть том нельзя такой им более всегда конечно всю между это эта
the a an of to in on at for and or but is are was were be been being it its this that these those with as
by from not no do does did so if then than there here we you he she they them our your his her my me
can could will would should has have had into about also just only very
""".split())

RU_VOWELS = "аеиоуыэюя"

PERFECTIVE = (("в", "вши", "вшись"), ("ив", "ивши", "ившись", "ыв", "ывши", "ывшись"))
REFLEXIVE = ((), ("ся", "сь"))
ADJECTIVE = ((), ("ее", "ие", "ые", "ое", "ими", "ыми", "ей", "ий", "ый", "ой", "ем", "им", "ым", "ом", "его",
                  "ого", "ему", "ому", "их", "ых", "ую", "юю", "ая", "яя", "ою", "ею"))
PARTICIPLE = (("ем", "нн", "вш", "ющ", "щ"), ("ивш", "ывш", "ующ"))
VERB = (("ла", "на", "ете", "йте", "ли", "й", "л", "ем", "н", "ло", "но", "ет", "ют", "ны", "ть", "ешь", "нно"),
        ("ила", "ыла", "ена", "ейте", "уйте", "ите", "или", "ыли", "ей", "уй", "ил", "ыл", "им", "ым", "ен", "ило",
         "ыло", "ено", "ят", "ует", "уют", "ит", "ыт", "ены", "ить", "ыть", "ишь", "ую", "ю"))
NOUN = ((), ("а", "ев", "ов", "ие", "ье", "е", "иями", "ями", "ами", "еи", "ии", "и", "ией", "ей", "ой", "ий", "й",
             "иям", "ям", "ием", "ем", "ам", "ом", "о", "у", "ах", "иях", "ях", "ы", "ь", "ию", "ью", "ю", "ия", "ья",
             "я"))
SUPERLATIVE = ((), ("ейше", "ейш"))


def _regions(word):
    """RV и R2 по Snowball: RV после первой гласной, R2 — R1 от R1."""
    rv = next((i + 1 for i, c in enumerate(word) if c in RU_VOWELS), len(word))

    def r(start):
        for i in range(max(start, 1), len(word)):
            if word[i] not in RU_VOWELS and word[i - 1] in RU_VOWELS:
                return i + 1
        return len(word)

    r1 = r(0)
    r2 = r(r1 + 1) if r1 < len(word) else len(word)
    return rv, r2


def _among(word, rv, groups):
    """Длина самого длинного окончания внутри RV; окончания первой группы — только после а или я."""
    best, first = 0, False
    for grp, endings in enumerate(groups):
        for e in endings:
            if len(e) > best and word.endswith(e) and len(word) - len(e) >= rv:
                best, first = len(e), grp == 0
    if best and first:
        p = len(word) - best - 1
        if p < rv or word[p] not in "ая":
            return 0
    return best


def stem_ru(word):
    word = word.replace("ё", "е")
    rv, r2 = _regions(word)
    n = _among(word, rv, PERFECTIVE)
    if n:
        word = word[:-n]
    else:
        n = _among(word, rv, REFLEXIVE)
        # -ся/-сь снимаем только у глагола или причастия: иначе «подпись» стала бы «подп», а «подписи» — «подпис»
        if n and (_among(word[:-n], rv, ADJECTIVE) or _among(word[:-n], rv, VERB)):
            word = word[:-n]
        n = _among(word, rv, ADJECTIVE)
        if n:
            word = word[:-n]
            n = _among(word, rv, PARTICIPLE)
            if n:
                word = word[:-n]
        else:
            n = _among(word, rv, VERB) or _among(word, rv, NOUN)
            if n:
                word = word[:-n]
    if word.endswith("и") and len(word) - 1 >= rv:
        word = word[:-1]
    for d in ("ость", "ост"):
        if word.endswith(d) and len(word) - len(d) >= r2:
            word = word[:-len(d)]
            break
    if word.endswith("нн") and len(word) - 2 >= rv:
        return word[:-1]
    n = _among(word, rv, SUPERLATIVE)
    if n:
        word = word[:-n]
        if word.endswith("нн") and len(word) - 2 >= rv:
            word = word[:-1]
    elif word.endswith("ь") and len(word) - 1 >= rv:
        word = word[:-1]
    return word


def _cons(w, i):
    c = w[i]
    if c in "aeiou":
        return False
    if c == "y":
        return i == 0 or not _cons(w, i - 1)
    return True


def _m(w):
    """Число переходов гласная→согласная (m у Porter)."""
    form = "".join("c" if _cons(w, i) else "v" for i in range(len(w)))
    return form.count("vc")


def _vowel(w):
    return any(not _cons(w, i) for i in range(len(w)))


def _cvc(w):
    return len(w) >= 3 and _cons(w, len(w) - 3) and not _cons(w, len(w) - 2) and _cons(w, len(w) - 1) \
        and w[-1] not in "wxy"


def _double(w):
    return len(w) >= 2 and w[-1] == w[-2] and _cons(w, len(w) - 1)


def stem_en(w):
    """Шаги 1a, 1b, 1c и 5 из Porter: множественное число, -ed, -ing, -y, конечная e."""
    if len(w) <= 3:
        return w
    if w.endswith("sses") or w.endswith("ies"):
        w = w[:-2]
    elif w.endswith("s") and not w.endswith("ss") and not w.endswith("us"):
        w = w[:-1]
    if w.endswith("eed"):
        if _m(w[:-3]) > 0:
            w = w[:-1]
    else:
        for suf in ("ed", "ing"):
            if w.endswith(suf) and _vowel(w[:-len(suf)]):
                w = w[:-len(suf)]
                if w.endswith(("at", "bl", "iz")):
                    w += "e"
                elif _double(w) and w[-1] not in "lsz":
                    w = w[:-1]
                elif _m(w) == 1 and _cvc(w):
                    w += "e"
                break
    if w.endswith("y") and _vowel(w[:-1]):
        w = w[:-1] + "i"
    if w.endswith("e"):
        m = _m(w[:-1])
        if m > 1 or (m == 1 and not _cvc(w[:-1])):
            w = w[:-1]
    if w.endswith("ll") and _m(w) > 1:
        w = w[:-1]
    return w


_CACHE = {}


def stem(word):
    """Основа одного слова в нижнем регистре; цифры и смешанные слова как есть."""
    s = _CACHE.get(word)
    if s is None:
        if CYR.search(word):
            s = stem_ru(word) if not re.search(r"[a-z0-9]", word) else word.replace("ё", "е")
        elif LATIN.match(word):
            s = stem_en(word)
        else:
            s = word
        if len(_CACHE) < 500000:
            _CACHE[word] = s
    return s


def keep(word):
    return (len(word) > 1 or word.isdigit()) and word not in STOP


def terms(text):
    """Основы слов текста по порядку; дефис, точка и подчёркивание делят слова."""
    return [stem(w) for w in WORD.findall(text.lower().replace("ё", "е")) if keep(w)]


def spans(text):
    """То же, что terms(), но с позициями в исходном тексте: (основа, начало, конец)."""
    out = []
    for m in WORD.finditer(text):
        w = m.group().lower().replace("ё", "е")
        if keep(w):
            out.append((stem(w), m.start(), m.end()))
    return out
