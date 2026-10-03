# Autopilot: the daily Shorts channel and the custom-video service

This document explains how the whole automation fits together: what runs, where and when, what each part does,
how it's configured, and the rules it follows. Keep it up to date as the parts are built (see
[CLAUDE.md](../CLAUDE.md)).

## Status
| Part | State |
|---|---|
| Engine: Shorts preset, text overlays, outro, song excerpt, length limit, loudness | **Built** (v1.4.0) |
| Engine: automatic project builder (`vsampler/auto.py`) | **Built** (v1.4.0) |
| Command line (`vsampler/cli.py`: `analyse`, `auto`, `render`) | **Built** (v1.4.0) |
| Linux server image (`Dockerfile`, `requirements-server.txt`, `requirements-autopilot.txt`) | **Written, not yet test-built** |
| Adding text without re-rendering (`vsampler/render/burn.py`, `vsampler text`) | **Built** |
| `autopilot/` phase 1: config, ideas, seasons, songs (chorus finding), people and local footage, build, context (transcripts, frames), writer and judge (Claude, template fallback), outro, run state machine, **review page**, notifications | **Built** |
| YouTube search and download for footage (`footage.py`) | Written, untested (needs `YOUTUBE_API_KEY`) |
| Trends, publishing to the platforms, stats | Planned |
| Custom-order storefront and fulfilment | Planned |

The detailed task list is [TODO.md](TODO.md). Sections describing planned parts are the design to build to. Update them, and this table, as each part lands.

---

## 1. Overview
Once a day, a server picks what's trending, finds footage of the person everyone is talking about, and uses
Video Sampler to make them "sing" a song. It adds text about what's happening in the video and ends with a
personal "I can make one of these for you" sign-off. Then it publishes to YouTube Shorts, TikTok, Instagram
Reels and Facebook Reels. The channel's main job is to advertise the paid service, where customers send in
videos of their friends and family and get their own custom video back.

```
            ┌──────────── seasons.yaml (theme calendar)
            ▼
 trends ──▶ pick topic + person ──▶ footage (allowlisted sources) ──▶ song (trending MIDI, else public-domain)
                                             │                               │
                                             ▼                               ▼
                                   context: transcript, keyframes ◀── vsampler auto (clip finder, key, coverage)
                                             │
                                             ▼
                          writer (hook, captions, titles) ──▶ safety judge ──▶ outro (your recorded sign-off)
                                                                                     │
                                                                                     ▼
                                       vsampler render (Shorts: 1080×1920, text, outro, -14 dB loudness)
                                                                                     │
                                         publish_mode = approval ──▶ phone message: Approve / Skip / Redo
                                         publish_mode = auto     ──▶ straight on
                                                                                     ▼
                                              publish: YouTube · TikTok · Instagram · Facebook
                                                                                     ▼
                                                     stats ──▶ db ──▶ steers tomorrow's choices
```

Everything runs on one Linux server (Docker), started by a daily timer. The engine never imports Qt, so it runs
there exactly as it does on Windows.

## 2. The daily workflow
**How to run it (phase 1):**
```
.venv\Scripts\pip install -r requirements-autopilot.txt     # once
.venv\Scripts\python -m autopilot ideas                      # which ideas can be made, and what each is missing
.venv\Scripts\python -m autopilot run                        # today's video; prints / sends the review link
.venv\Scripts\python -m autopilot run --idea "Donald Trump — Fireflies" --date 2026-12-20
.venv\Scripts\python -m autopilot review                     # serve the review page again for waiting runs
.venv\Scripts\python -m autopilot status                     # the latest runs
```
`run` builds the video without text, sends the link (Telegram, email, or just the console) and serves the review
page at `http://127.0.0.1:8765/run/<id>?token=…` until you approve or skip it, or the deadline passes. In
`publish_mode: auto` it goes straight through to "approved". The approved video and its `metadata.json` (title,
description, hashtags) are in `autopilot_data/runs/<id>/`, ready to post (publishing comes in a later phase).

### The review (approval mode)
| Step | You see | You can |
|---|---|---|
| 1 · Preview | The video **without text** | **Looks good, write the text** · **Different clips** (same idea, other takes) · **Different idea** · **Different part of the song** · Skip |
| 2 · Text | The text the writer drafted (hook, captions with times, call to action, watermark), the judge's checks, the title, description and hashtags | Edit, add or remove lines and their times · **Preview** the text on a still at the video's current time (instant) · **Rewrite the text** (with a note to the writer) · **Apply text** · Different clips · Skip |
| 3 · Final | The video **with the text** | **Approve** · **Edit the text** (back to step 2; applying again only re-draws the text) · Different clips · Skip |

**A different part of the song** (any step): the box under the video lists suggested parts (`songs.song_parts`:
where each different repeated phrase first appears, the most repeated, the chorus, first; then the start of the
song) and lets you type your own start and end (5–60 s). The video is made again from that part (a few minutes),
and the text is written again after. "Use this part for this song from now on" saves it in
`autopilot_data/songs.yaml`. On the command line: `python -m autopilot run --part 83.5-105`.

Applying text never re-renders the song: `burn_overlays` draws the text on the text-free video and copies the
sound across (`python main.py text --video base.mp4 --overlays text.json --out final.mp4` does the same by hand).

### The run state machine (`autopilot/pipeline.py`)
```
picked -> built -> text_drafted -> final -> approved
           ^  |        ^   |        |  |
           |  +--------+   +--------+  |   (regenerate video / idea from any review step goes back to built;
           +---------------------------+    skip, expiry and errors end the run)
```
Every change is saved to `runs/<id>/state.json`, so a restart picks the run up where it was. Slow steps run in a
background thread: the server first `claim`s the run (sets `task`, e.g. "Making the video from 55–77 s of the
song", and `busy`), then `act(..., claimed=True)` does the work while the page polls `busy` (the current step),
`progress` and `elapsed` and shows them in a banner that stays on screen. A job that fails clears `busy` and sets
`error`. A server restart clears a job that was cut off. Saving and reading `state.json` retry for up to a second
(`_retry`), because on Windows a file can't be replaced while the page is reading it.

### The full design
Each step either passes something to the next one, or ends the day's run cleanly with a logged reason and a
phone alert. A skipped day is better than a bad post.

| # | Step | What happens | If it fails |
|---|---|---|---|
| 1 | **Theme** | `seasons.py` reads today's date against `seasons.yaml` and returns the active themes, e.g. "Mother's Day (in 3 weeks)", "debate week". | No theme: plain trending day. |
| 2 | **Trends** | `trends.py` collects today's political and cultural stories and the people in them (YouTube most-popular News & Politics, Google/TikTok trends, news RSS). It blends them with the theme, drops banned topics and anyone used in the last N days (`db`), and ranks the people. | Use the next person on the list. |
| 3 | **Footage** | `footage.py` searches the allowed sources for recent long speeches by the top people (see §7: source tiers). It downloads 2–5 videos per person and logs where each came from. | Try the next person; after 3 people, skip the day. |
| 4 | **Song** | `songs.py`: if a currently trending song (or a theme song) has a melody MIDI in `songs/`, use it, otherwise the best public-domain or meme tune for the mood. Each song entry stores its catchy section (`start`/`end`). | Fall back to the public-domain pool. |
| 5 | **Build** | `build.py` runs `vsampler auto --no-render` for 2–3 person × song combinations, using `--max-fragment` for Tier 2 footage. It keeps the one with the best `coverage` in `<out>.report.json`. | Below `min_coverage` for all: skip the day. |
| 6 | **Context** | `context.py` makes a timeline of what's on screen: a transcript of each source speech (`faster-whisper`), which words the sung syllables came from, the song's section and theme, and a few keyframes. | Writer works from the title and topic only. |
| 7 | **Write** | `writer.py` (Claude API) turns the timeline into a hook line, 2–4 timed captions about what's happening on screen, and per-platform titles, descriptions and hashtags. It writes `overlays.json` for the CLI. | Retry once; then skip the day. |
| 8 | **Safety judge** | A second Claude call checks the text and the build report against the rules in §7, and returns pass or fail with reasons. | `auto` mode: next candidate, then skip. `approval` mode: shown to you with the reasons. |
| 9 | **Outro** | `outro.py` picks one of your recorded sign-off clips (seasonal ones when a theme is active) and the matching CTA text. | Use the generic outro. |
| 10 | **Render** | `vsampler render` (or `auto` without `--no-render`): a 1080×1920 video under a minute, with text, outro and even loudness. | Retry once; then alert. |
| 11 | **Review** | `approval` mode: a Telegram/email message with the video, title and Approve / Skip / Redo. No answer by the deadline means skip. `auto` mode: no message. | — |
| 12 | **Publish** | `publish/` uploads to each enabled platform at its own best time, with the altered-content labels on. Each platform is retried separately. | Alert; the other platforms still post. |
| 13 | **Stats** | Next day, `stats.py` pulls views, average view duration, swipe-away and clicks to the order page per post into `db`. `trends` and `writer` use these to favour hooks, songs and people that perform. | — |

**`auto` vs `approval`:** one setting, `publish_mode` in `config.yaml`. Start in `approval` for at least the first
two weeks, while you see what the AI picks. Switch to `auto` once you trust it. The safety judge runs in both.

## 3. Modules

### In the app (built)
- **`vsampler/auto.py`**: `auto_project(videos, song, AutoOptions, SongOptions, RenderSettings)` returns
  `(Project, Song, AutoReport)`.
  - It runs the clip finder over each video and puts the most confident clip of every note on its key, with up
    to `max_takes` more as takes. Percussive hits go on kick, snare and hi-hat if the song has drums.
  - It turns on octave jump and short pitch-shifts, and picks the transpose that plays the most of the song.
  - `max_fragment` cuts every clip to at most that many seconds.
  - Long videos are cut into 10-minute pieces, and speech is analysed over the voice range on all CPU cores: a
    43-minute speech takes about 5 minutes on 8 cores. Each piece's analysis is saved, so trying other songs on the
    same footage is instant.
  - With a piano arrangement, only the tune's track plays (`melody_track`).
  - `AutoReport` gives `coverage` (0–1), the exact / octave / pitch-shifted / missing counts, the transpose, and
    every clip used with its source file and times (provenance).
- **`vsampler/cli.py`**: the command line (also `python main.py …` and `python -m vsampler …`). It prints JSON
  to stdout, writes progress to stderr and `<out>.log`, and uses exit codes `0` OK, `1` failed, `2` bad
  arguments, `3` coverage too low.
  ```
  vsampler analyse speech.mp4
  vsampler auto --videos a.mp4 b.mp4 --song tune.mid --preset shorts --start 32 --end 58 \
                --overlays overlays.json --outro outro_07.mp4 --max-fragment 1.0 --min-coverage 0.7 --out day.mp4
  vsampler render day.vsproj day.mp4
  ```
  `auto` writes `day.vsproj` and `day.report.json` next to the video, even when it doesn't render.
- **`vsampler/render/overlays.py`**: draws `RenderSettings.overlays`.
  - Styles: `hook` (big boxed text at the top, which shrinks after 3 s), `caption` (white with an outline, in the
    lower part), `cta` (a box in the highlight colour) and `watermark`.
  - Text wraps and shrinks to fit, emoji are drawn in colour, and every text fades in.
  - Everything stays inside the safe area, away from the app buttons on the right and the caption along the
    bottom.
- **`RenderSettings`** additions: `preset` (`shorts`: 1080×1920, "only who's singing" layout, no note labels,
  ≤ 55 s, -14 dB loudness), `overlays`, `max_duration`, `outro`, `loudness_db`. **`SongOptions`** additions:
  `start_s`, `end_s` (play only that part of the song).

`overlays.json` (the writer's output) is a list of overlays. `start` and `end` are seconds into the finished
video, and a negative `start` counts back from the end:
```json
[
  {"text": "The Senate choir audition went WELL 😭", "style": "hook"},
  {"text": "wait for the high note on 'budget'", "start": 6.2, "end": 9.0},
  {"text": "Want your mum singing this? Link in bio 💐", "start": -3, "style": "cta"},
  {"text": "@yourchannel", "style": "watermark"}
]
```

### `autopilot/`
| Module | Job | State |
|---|---|---|
| `config.py`, `config.example.yaml` (+ your `config.yaml`, git-ignored) | All settings (§4). Secrets only from environment variables | Built |
| `ideas.py` | Parses `Project combination ideas.txt` ("X singing “Song” — Artist", top-10 repeats become favourites) into `autopilot_data/ideas.yaml`; picks today's idea (can be made, not used, person not used recently, favourites and theme matches first) | Built |
| `seasons.py`, `seasons.yaml` | Theme calendar (fixed dates, "second sunday of may", Easter-relative, US election) and today's active themes | Built |
| `songs.py` | Finds a song's file by title in `song_dirs`; suggests parts of the song (`song_parts`: the chorus first, then other repeated parts) and uses the chorus by default; saves the chosen part in `autopilot_data/songs.yaml` (edit it, or tick "remember" on the review page, to override) | Built |
| `footage.py`, `people.yaml` | Who can sing: tier, local footage globs, official channels. Local files first; YouTube search and download with `YOUTUBE_API_KEY` | Local built; YouTube untested |
| `build.py` | Idea → plan (person, song file, chorus, footage) → `vsampler.auto` (with `seed` for "different clips") → coverage check → `base.mp4` without text | Built |
| `context.py` | What happens in this render: phrases, the words each note was cut from (faster-whisper, only the 30 s blocks used, cached), what was said around them, frames, sources | Built |
| `style.md`, `writer.py` | Claude (`claude-opus-5-5`, structured JSON output, server-side refusal fallback) writes the hook, captions, call to action, title, description and hashtags; one automatic retry if the checks fail. Without a key: template text to edit | Built (Claude path tested with a stand-in) |
| `judge.py` | Fixed rule checks (lengths, timings, edit-bragging, claim words) plus a Claude review when a key is set | Built |
| `outro.py` | Picks one of your sign-off clips from `autopilot_data/outros/outros.yaml` (seasonal first) | Built (waiting for your clips) |
| `pipeline.py` | The run state machine above | Built |
| `review/` | The review page (FastAPI + one HTML page) | Built |
| `notify.py` | Telegram (`TELEGRAM_BOT_TOKEN`, `TELEGRAM_CHAT_ID`), else email (`SMTP_HOST`, `NOTIFY_EMAIL`, …), else console | Built |
| `db.py` | SQLite: history (cooldowns, used ideas), posts, claims per source channel | Built |
| `__main__.py` | `python -m autopilot run / review / ideas / status` | Built |
| `trends.py` | Today's stories and people, ranked and filtered | Planned |
| `publish/` | `youtube.py`, `tiktok.py`, `instagram.py`, `facebook.py`: one interface, `upload(video, meta) -> post_id` | Planned |
| `stats.py` | Performance numbers from each platform | Planned |
| `orders/` | The custom-video service (§5) | Planned |

### How the text is written (planned)
The text is written by an LLM: Claude (`claude-opus-5-5`, set in `config.yaml`) through the API, which also looks at images. A
generic model writes generic captions, so its quality comes from the **context** it gets. `context.py` builds a
"what's on screen" timeline for *this exact render*:

| Context | Where it comes from |
|---|---|
| Who and what the footage is: title, date, channel, description | `footage.py` (search result metadata) |
| What was being said: a timestamped transcript of every source piece | `faster-whisper`, run once per piece and cached like the clip analysis |
| Which words each sung note came from, and the sentence around it (±10 s) | `AutoReport.clips` (source file and times) matched against the transcript |
| What the viewer sees at each moment: 1 frame per song phrase (~every 2 s) | `renderer.preview_frame` on the finished project, sent as images |
| What the song is doing: the song, its section, where the chorus or high note lands | `songs.yaml` plus the note plan (e.g. "highest note at 12.4 s") |
| Why this person is in the news today | `trends.py` headlines |
| Today's theme | `seasons.py` |
| The voice: the style guide and examples below | `writer.py` prompt |
| What worked before: the best-performing hooks | `db` stats |

Claude returns strict JSON (a tool call with a schema): the hook, 2–4 captions with start and end times
snapped to phrase boundaries, the call to action, and per-platform titles, descriptions and hashtags. The code
checks it: times inside the video, hook ≤ 10 words, caption ≤ 8 words, no banned words. A second call, the
**judge**, checks it against the rules in §7 with the same frames. A failed check means one retry, then the next
candidate. Cost is a few cents per video.

### Finding songs and footage (planned)
**The ideas list** (`projects/Project combination ideas.txt`): `ideas.py` turns each line into
`ideas.yaml` entries `{person or group, song, category, footage tier}` (one Claude call, which you review once). On a
normal day, `trends.py` picks the idea that best fits today's news or theme. On a quiet day, the next good idea
from the backlog plays. Each idea is used once, then rested.

**Songs** (`songs.py`), tried in this order:
1. **Your library:** `songs/` files you've added (`.mid` / `.mxl`). MuseScore arrangements like yours are the
   best quality. MuseScore has no API and its terms forbid scraping, so these stay a manual download. That's two
   minutes per song for the curated list.
2. **Lakh MIDI dataset:** about 176,000 MIDI files, 45,000 of them matched to artist and title. Downloaded once
   (a few GB) and indexed, it gives an instant lookup for most hits up to about 2011 (Barbie Girl, Baby,
   Fireflies…). Its licence covers research, which makes it fine for finding the notes. The song's copyright is
   the same issue whichever MIDI file is used (§7).
3. **Transcribe the recording:** the official audio is split into vocals, bass and the rest (Demucs), and the
   app's existing `basic-pitch` transcribes each part into MIDI. It works for brand-new songs no MIDI exists for.
   Quality varies, so these always go through approval.

**The chorus** is found automatically: the most repeated 15–25 s stretch of the tune (self-similarity of the note
sequence), or lyric lines in MusicXML files that have them. It's stored as `start`/`end` in `songs.yaml`.

**Footage** (`footage.py` with `people.yaml`): one entry per person or group, with these fields:
- Names and aliases.
- Their official sources, e.g.:
  - White House and House/Senate floor video for US politicians.
  - kremlin.ru for Putin (its videos are CC BY 4.0).
  - The Obama White House archive and the Reagan Library.
- The footage tier.
- A reference face: their official portrait from Wikimedia Commons.

Then, for each entry:
1. **Search:** the YouTube Data API (`search.list` restricted to those channels, long videos, newest first), or
   the source's own archive.
2. **Download:** long speeches (10–60 min) at 360–720p.
3. **Cut and analyse:** as `auto` does now; cached, so footage is only ever analysed once.
4. **Keep only the right person:** face matching against the reference portrait, plus speaker diarisation
   (`pyannote`), so only clips where *that* person is on screen *and* talking are used. This is the same
   feature as "Choose who sings" in TODO.md.
5. **Check:** if coverage is too low, fetch more footage and repeat.

**Rights per idea:**
- **Government footage** (US politicians, Congress) is Tier 1.
- **Putin** via kremlin.ru is Tier 1 with credit in the description.
- **Celebrities, the Royal Family, films and cartoons** (The Avengers, The Simpsons, Teletubbies…) are Tier 2:
  their footage is owned by studios and broadcasters, who use Content ID heavily.
- **People with little or no recorded speech** (Einstein, Newton) won't work.
- **Using a celebrity's face to advertise the paid service** risks a right-of-publicity claim, separate from
  copyright. On celebrity videos, the call to action stays soft ("link in bio"), not "I'll make you one".

## 4. Configuration
Every setting, with a comment, is in [`autopilot/config.example.yaml`](../autopilot/config.example.yaml). Put the
ones you change in `autopilot/config.yaml` (git ignores it); the rest come from the example. The main ones:
`publish_mode` (`approval` / `auto`), `min_coverage`, `person_cooldown_days`, `footage_tiers`,
`video.seconds`, `claude.model` / `claude.effort`, `review.deadline_minutes`, `links.handle`, and the `cta` texts.

Who can sing, and where their footage comes from, is in [`autopilot/people.yaml`](../autopilot/people.yaml).
A song's catchy part is found automatically and saved in `autopilot_data/songs.yaml`; edit `start` / `end`
there to choose another part.

[`autopilot/seasons.yaml`](../autopilot/seasons.yaml): each theme has a date rule (`"12-25"`,
`"second sunday of may"`, `"easter-21"` for UK Mother's Day, `"us-election"`), how many days before (and after)
it's active, `words` that make an idea fit it, a `tone` for the writer and the `cta` wording:
```yaml
- name: mothers_day_uk
  date: "easter-21"
  lead_days: 21                  # start three weeks before, when people order gifts
  kind: family                   # family | political
  words: [mum, mother, love, sunshine]
  tone: "warm and cheeky, about mums"
  cta: "get Mum singing this for Mother's Day 💐 link in bio"
```
Included: New Year, Valentine's, Mother's Day (UK and US), Father's Day, Independence Day, Halloween, the US
election, Thanksgiving and Christmas. Still to add: graduation, weddings, State of the Union, primaries, debates,
inauguration, budget fights.

Secrets (API keys, OAuth refresh tokens, Stripe keys) never go in `config.yaml` or git. They live in environment
variables or AWS Secrets Manager.

## 5. The custom-video service (planned)
```
order page ──▶ Stripe Checkout ──▶ upload form (S3) ──▶ queue ──▶ vsampler auto ──▶ your QA ──▶ delivery email
   ▲                                                                                      │
   └──────── every Short's outro and bio link                       revision request ◀───┘
```
- **Order form:** names, occasion, song choice (from the licensed or public-domain list, or "any song, personal
  use only"), an in-joke or message for the on-screen text, and the videos.
  - Required ticks: "I have permission from everyone in these videos" and "anyone under 18 has a parent's or
    guardian's consent".
  - Opt-in: "you may feature this on your channel".
- **Tiers:** Basic $15 (one person, ~20 s, from the song list), Standard $29 (up to 4 people, vertical and
  landscape), Premium/Rush $49 (24–48 h, custom text, extra revisions). Upsell: +$5 for the full-song version.
- **Fulfilment:** the Stripe webhook queues the job, the server builds and renders it, and **you always check
  every paid video** before it goes out. Delivery follows the advertised turnaround (48–72 h standard).
- **Inbox:** the service email address forwards into the queue. Claude sorts each email (new order, question,
  revision) and drafts a reply in your voice for you to approve.
- **Tone:** everything customer-facing is first person, personal and warm ("I'll pick the best moments from your
  clips…"), and mentions neither AI nor automation. That's true to how it works: you built the software and you
  finish every video yourself. Don't claim frame-by-frame hand editing. If a customer asks how it's done: "I
  built my own editing software for this, and I check and finish every video myself."

## 6. Deployment and operations (planned unless marked)
- **Server:** one Linux VM running Docker, either a Hetzner CCX13 (about €15 a month) or an AWS EC2 c7i.large
  (about $40–60 a month). Rendering a 45 s Short takes a few minutes on 2–4 vCPUs.
- **Image (written):** `docker build -t vsampler .` then
  `docker run --rm -v "$PWD/work:/work" vsampler auto --videos /work/a.mp4 --song /work/t.mid --preset shorts --out /work/s.mp4`.
  It installs `rubberband-cli` and `fonts-noto-color-emoji`. Run the tests inside it with
  `docker run --rm --entrypoint python vsampler -m pytest tests -q`.
- **Schedule:** a systemd timer (or cron, or AWS EventBridge Scheduler) runs `autopilot run_daily` once a day,
  early enough for review before the first post time.
- **Logs and alerts:** each run writes `runs/<date>/` (inputs, reports, overlays, video, log). Failures and skipped
  days send a Telegram message.
- **Backups:** `db.sqlite`, `songs/`, `outros/` and `config.yaml` are synced to S3 nightly.
- **One-off setup (by hand):**
  - Create the brand accounts on each platform and the service email address.
  - Connect Instagram as a Creator/Business account to a Facebook Page.
  - Create the API apps: a Google Cloud project with the YouTube Data API, a TikTok developer app with the
    Content Posting API, and a Meta app.
  - Pass each platform's review. **Until each review passes, uploads through its API are private only.** That
    means the YouTube API audit, TikTok's Content Posting audit and Meta App Review.
  - Record 10–15 outro takes. Seed `songs/`.
- **Tokens:** OAuth refresh tokens are stored as secrets. `publish/*` refreshes access tokens itself and alerts if
  a refresh token is revoked; re-run `autopilot auth <platform>` to fix it.
- **Alternative:** a paid posting service (e.g. Ayrshare or Upload-Post) can replace the four `publish/`
  adapters and takes care of the platform reviews.

## 7. Content and platform rules
These are enforced in code (writer prompt, judge, build guards). They are not just advice.

**Footage source tiers** (`footage_tiers`):
- **Tier 1 (default):** public-domain US federal government video (White House, House and Senate floor feeds,
  govinfo), plus creators who've given written permission (stored in `db`). C-SPAN's *own* camera footage is
  copyrighted. The House and Senate floor feeds are not.
- **Tier 2 (opt-in):** news or creator footage relied on as fair-use parody.
  - Guards: every clip ≤ `max_fragment` (1 s), no continuous source stretch longer than 2 s, and none of the
    source's own music or commentary.
  - Provenance for every fragment is in the build report, for disputes. Channels that claim a video are added to
    `sources.block` automatically.
  - Fair use is decided after the event: Content ID and the TikTok/Meta matchers act automatically. Downloading
    from YouTube with tools like `yt-dlp` is against YouTube's terms of service.
- Trending videos themselves are only used to pick **topics and people**, never as footage (outside Tier 2).

**Labels:** a real politician made to "sing" counts as altered content showing a real person. YouTube uploads
set `containsSyntheticMedia`, TikTok posts set the AI-generated content flag, and Meta captions say it's parody.
The labels describe the video, not how it was made, and don't reduce reach.

**Writer and judge rules:**
- Never invent a quote or a factual claim about a real person. The text comments on what's visibly happening
  ("hits the high note mid-speech"), not on what they believe or did.
- Mark it as parody or satire in the description.
- No election misinformation (dates, how to vote, results).
- No slurs, sexual content about real people, or tragedies.
- Hooks are clickbait about *the video*: curiosity and comedy, not false promises.

**Writer voice** (`writer.py` puts this in its prompt):
- **"I'm one of you, look at this funny thing."** Write like a viewer sharing a clip with friends, in casual
  TikTok captions: lower case is fine, "POV", "me when", "nobody: / …", one emoji per line at most.
- **Never brag about the edit.** Don't write lines like "every note is a real word" or "not one note added".
  That sounds self-congratulatory. Let the video be the joke.
- **Gently mock the hyper-patriotism**, especially the ultra-right-wing "freedom" aesthetic. Use its own symbols
  as the punchline: eagles, fireworks, "FREEDOM", trucks, the uncle at Thanksgiving, 250 years of being loud.
  Tease the over-the-top vibe, not ordinary people, a group's identity or anyone's looks.
- **Tie every line to the moment on screen** (the event, the speaker, what the song is doing): the high note, the
  troops standing behind the speaker, the chorus kicking in.
- **The call to action sounds like a person:** "ok now imagine your nan doing this. I'll make you one 👇".
- Avoid flag emoji: they don't show on Windows.

Examples (Marine Corps 250th footage, Yankee Doodle):
> POV: your uncle hears "250 years" and stands up at dinner 🦅
> the bald eagle living in my chest rn
> 250 years and they're STILL this loud 🔊
> freedom got so loud it went in tune

**Music:**
- Trending songs are re-performed from a melody MIDI, so no original recording is used.
- The composition is still the publisher's, so expect claims that share revenue on those posts.
- Public-domain tunes avoid that.

**Money reality check:**
- YouTube monetisation needs 1,000 subscribers and 10M Shorts views in 90 days.
- Shorts pay very little per view.
- The paid service is the business, and the channel is its advert.
