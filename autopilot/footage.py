"""People and their footage: local files first, then (with a YouTube API key) searching their official channels."""
from __future__ import annotations

import glob
import json
import os
import re
from dataclasses import dataclass, field
from pathlib import Path

import requests
import yaml

VIDEO_TYPES = (".mp4", ".mov", ".mkv", ".webm", ".m4v")


@dataclass
class Person:
    name: str
    aliases: list[str] = field(default_factory=list)
    tier: int = 2
    kind: str = "person"            # politician | group | celebrity | person
    footage: list[str] = field(default_factory=list)
    youtube_channels: list[str] = field(default_factory=list)
    search: str = ""
    credit: str = ""


def _norm(s: str) -> str:
    return re.sub(r"[^a-z0-9]+", " ", s.lower()).strip()


def load_people(path: str | Path) -> list[Person]:
    p = Path(path)
    return [Person(**d) for d in (yaml.safe_load(p.read_text(encoding="utf-8")) or [])] if p.exists() else []


def find_person(people: list[Person], who: str) -> Person | None:
    w = _norm(who)
    for p in people:
        if w in {_norm(n) for n in [p.name, *p.aliases]}:
            return p
    return None


def local_footage(person: Person, root: Path) -> list[str]:
    files: list[str] = []
    for pattern in person.footage:
        pat = pattern if os.path.isabs(pattern) else str(root / pattern)
        files += sorted(f for f in glob.glob(pat) if f.lower().endswith(VIDEO_TYPES))
    return list(dict.fromkeys(files))


def title_of(path: str) -> str:
    """A readable title for a footage file (saved metadata if any, else the file name)."""
    meta = Path(path + ".json")
    if meta.exists():
        return json.loads(meta.read_text(encoding="utf-8")).get("title", "")
    stem = Path(path).stem
    stem = re.sub(r"^YTDown\.com_YouTube_Media_[\w-]{11}_", "", stem)
    stem = re.sub(r"_\d{3}_\d{3,4}p$", "", stem)
    return stem.replace("-", " ").replace("_", " ").strip()


def youtube_search(person: Person, api_key: str, max_results: int = 5) -> list[dict]:
    """Recent long videos from the person's official channels (YouTube Data API search.list)."""
    found = []
    for channel in person.youtube_channels or [""]:
        params = {"part": "snippet", "type": "video", "videoDuration": "long", "order": "date",
                  "maxResults": max_results, "q": person.search or person.name, "key": api_key}
        if channel:
            params["channelId"] = channel
        r = requests.get("https://www.googleapis.com/youtube/v3/search", params=params, timeout=30)
        r.raise_for_status()
        for item in r.json().get("items", []):
            found.append({"id": item["id"]["videoId"], "title": item["snippet"]["title"],
                          "channel": item["snippet"]["channelId"], "published": item["snippet"]["publishedAt"]})
    return found


def download(video: dict, folder: Path) -> str:
    """Download one search result with yt-dlp (if installed). Note: downloading from YouTube is against
    YouTube's terms of service; prefer the source's own archive where there is one (see docs/autopilot.md)."""
    try:
        import yt_dlp
    except ImportError as e:
        raise RuntimeError("Downloading needs yt-dlp: pip install yt-dlp") from e
    folder.mkdir(parents=True, exist_ok=True)
    out = folder / f"{video['id']}.mp4"
    if not out.exists():
        opts = {"format": "best[height<=720][ext=mp4]/best[ext=mp4]", "outtmpl": str(out), "quiet": True}
        with yt_dlp.YoutubeDL(opts) as ydl:
            ydl.download([f"https://www.youtube.com/watch?v={video['id']}"])
    Path(str(out) + ".json").write_text(json.dumps(video), encoding="utf-8")
    return str(out)


def get_footage(person: Person, root: Path, download_dir: Path, allowed_tiers: list[int],
                blocked_channels: set[str] = frozenset()) -> list[str]:
    """Footage files for `person`: local first; otherwise search and download (needs YOUTUBE_API_KEY)."""
    if person.tier not in allowed_tiers:
        return []
    files = local_footage(person, root)
    if files:
        return files
    key = os.environ.get("YOUTUBE_API_KEY")
    if not key or not person.youtube_channels:
        return []
    results = [v for v in youtube_search(person, key) if v["channel"] not in blocked_channels]
    return [download(v, download_dir / re.sub(r"\W+", "_", person.name)) for v in results[:3]]
