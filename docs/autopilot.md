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
| Linux server image (`Dockerfile`, `requirements-server.txt`) | **Written, not yet test-built** |
| `autopilot/` package (trends, seasons, footage, writer, outro, review, publish, stats) | Planned |
| Custom-order storefront and fulfilment | Planned |

Sections describing planned parts are the design to build to. Update them, and this table, as each part lands.

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

### `autopilot/` (planned)
| Module | Job |
|---|---|
| `config.yaml` | All settings (§4). |
| `seasons.yaml`, `seasons.py` | Theme calendar and today's active themes. |
| `trends.py` | Today's stories and people, ranked and filtered. |
| `songs.py`, `songs/` | Song library: MIDI files plus `songs.yaml` (title, status `trending` / `public_domain`, catchy section, mood, themes). |
| `footage.py` | Search and download from allowed sources, recording provenance. |
| `build.py` | Runs `vsampler auto` over candidates and keeps the best report. |
| `context.py` | Transcript, keyframes and song section, giving the "what's on screen" timeline. |
| `writer.py` | Claude API: overlays, titles, descriptions, hashtags; also drafts customer emails in your voice. |
| `judge.py` | Claude API safety check (pass/fail plus reasons). |
| `outro.py`, `outros/` | Your recorded sign-off clips plus `outros.yaml` (which theme each suits). |
| `review.py` | Approval messages (Telegram bot or email) with Approve / Skip / Redo. |
| `publish/` | `youtube.py`, `tiktok.py`, `instagram.py`, `facebook.py`: one interface, `upload(video, meta) -> post_id`. |
| `stats.py` | Pulls performance numbers from each platform. |
| `db.py` | SQLite: people, songs and footage used, posts, stats, claims per source channel. |
| `run_daily.py` | Runs the steps in order, logs, retries and alerts. `--dry-run` does everything except publish. `--date` pretends it's another day (to test themes). |
| `orders/` | The custom-video service (§5). |

## 4. Configuration (planned)
`config.yaml`:
```yaml
publish_mode: approval          # approval | auto
post_times: {youtube: "17:00", tiktok: "19:00", instagram: "18:00", facebook: "18:00"}   # local time
platforms: {youtube: true, tiktok: true, instagram: true, facebook: true}
timezone: Europe/London
footage_tiers: [1]              # add 2 to allow fair-use news/creator footage (see §7)
tier2_guards: {max_fragment: 1.0, max_continuous: 2.0}
sources:
  allow: [whitehouse, house_floor, senate_floor, govinfo, licensed_creators]
  block: []                     # channels that have claimed a video are added here automatically
min_coverage: 0.7
person_cooldown_days: 5
banned_topics: [mass-casualty events, deaths, children]
review: {channel: telegram, deadline_minutes: 120}
links: {order_page: "https://…", bio: "https://…"}
claude: {model: claude-sonnet-5-5}
```

`seasons.yaml`: each theme has a date window (or a rule, e.g. "2nd Sunday of May"), a lead time, a weight, a
song pool, a tone for the writer and the CTA wording:
```yaml
- name: mothers_day_uk
  date: "fourth Sunday of Lent"
  lead_days: 21                  # start pushing three weeks before, when people order gifts
  weight: 0.7                    # how strongly it steers the day's topic and song (0..1)
  kind: family                   # family | political
  songs: [you_are_my_sunshine, happy_birthday_style_pd]
  tone: "warm, cheeky, about mums"
  cta: "Get Mum singing this for Mother's Day 💐 link in bio"
- name: state_of_the_union
  date: "rule:sotu"              # looked up each year
  lead_days: 2
  weight: 0.9
  kind: political
  tone: "satirical, about the speech itself"
```
Planned family themes: Valentine's, Mother's Day (UK and US), Father's Day, graduation, wedding season,
Halloween, Thanksgiving, Christmas, New Year, plus birthdays and retirements as evergreen themes. Planned
political themes: State of the Union, primaries, debates, Election Day, inauguration, budget and shutdown fights.

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

**Music:**
- Trending songs are re-performed from a melody MIDI, so no original recording is used.
- The composition is still the publisher's, so expect claims that share revenue on those posts.
- Public-domain tunes avoid that.

**Money reality check:**
- YouTube monetisation needs 1,000 subscribers and 10M Shorts views in 90 days.
- Shorts pay very little per view.
- The paid service is the business, and the channel is its advert.
