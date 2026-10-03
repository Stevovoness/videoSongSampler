"""The ideas list: "<person> singing “<song>”" lines turned into a queue, and choosing today's idea."""
from __future__ import annotations

import re
from dataclasses import asdict, dataclass
from datetime import date
from pathlib import Path

import yaml

QUOTES = "“”\"‘’"
_SINGING = re.compile(r"^(?P<who>.+?)\s+singing\s+[“\"](?P<song>[^”\"]+)[”\"]\s*(?:[—–-]\s*(?P<rest>.*))?$", re.I)
_DASH = re.compile(r"^(?P<who>.+?)\s+[—–-]\s+[“\"](?P<song>[^”\"]+)[”\"]\s*$")


@dataclass
class Idea:
    id: str
    who: str
    song: str
    artist: str = ""
    category: str = ""
    favourite: bool = False
    note: str = ""

    @property
    def label(self) -> str:
        return f"{self.who} — {self.song}"


def slug(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")


def _clean_header(line: str) -> str:
    return re.sub(r"^[^\w]+", "", line).strip()


def parse(text: str) -> list[Idea]:
    """Ideas from the free-form list. Lines like 'X singing “Song” — Artist — note' or 'X — “Song”'.

    Headers (lines that aren't ideas) set the category. A later line repeating an idea (e.g. a "top 10" list)
    marks it as a favourite instead of adding it twice.
    """
    ideas: dict[str, Idea] = {}
    category = ""
    favourites = False
    for raw in text.splitlines():
        line = raw.strip()
        if not line:
            continue
        m = _SINGING.match(line) or _DASH.match(line)
        if not m:
            if len(line) < 70 and not line.endswith(":"):
                category = _clean_header(line)
                favourites = "top" in category.lower()
            continue
        who = m.group("who").strip(" *-")
        song = m.group("song").strip()
        rest = (m.groupdict().get("rest") or "").strip()
        artist, _, note = rest.partition("—")
        key = slug(f"{who}-{song}")
        same = ideas.get(key) or next(   # "Putin — Call Me Maybe" repeats "Vladimir Putin singing …"
            (i for i in ideas.values() if slug(i.song) == slug(song)
             and set(slug(who).split("-")) <= set(slug(i.who).split("-"))), None)
        if same is not None:
            same.favourite = same.favourite or favourites
            continue
        ideas[key] = Idea(key, who, song, artist.strip(), category if not favourites else "", favourites,
                          note.strip())
    return list(ideas.values())


def load(path: str | Path) -> list[Idea]:
    p = Path(path)
    if not p.exists():
        return []
    if p.suffix in (".yaml", ".yml"):
        return [Idea(**d) for d in yaml.safe_load(p.read_text(encoding="utf-8")) or []]
    return parse(p.read_text(encoding="utf-8"))


def save(ideas: list[Idea], path: str | Path) -> None:
    Path(path).write_text(yaml.safe_dump([asdict(i) for i in ideas], allow_unicode=True, sort_keys=False),
                          encoding="utf-8")


def find(ideas: list[Idea], query: str) -> Idea | None:
    """The idea matching a label like 'Donald Trump — Fireflies', an id, or a few words of it."""
    q = slug(query)
    for i in ideas:
        if i.id == q or slug(i.label) == q:
            return i
    words = set(q.split("-"))
    scored = [(len(words & set(i.id.split("-"))), i) for i in ideas]
    best = max(scored, key=lambda s: s[0], default=(0, None))
    return best[1] if best[0] else None


def choose(ideas: list[Idea], ready: callable, today: date, used: set[str], recent_people: set[str],
           theme_words: set[str] = frozenset()) -> Idea | None:
    """Today's idea: one that can be made (footage and song available), not used before, not a person used
    recently; favourites and ideas matching today's theme first."""
    def score(i: Idea) -> float:
        s = 2.0 if i.favourite else 0.0
        words = set(slug(f"{i.who} {i.song} {i.category}").split("-"))
        s += 3.0 * len(words & theme_words)
        return s

    pool = [i for i in ideas if i.id not in used and i.who.lower() not in recent_people and ready(i)]
    if not pool:
        return None
    best = max(score(i) for i in pool)
    top = [i for i in pool if score(i) == best]
    return top[today.toordinal() % len(top)]     # varies day to day, but repeatable for a given date
