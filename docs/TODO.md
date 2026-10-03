# To-do list

The work still to do on the app and the automation (see [autopilot.md](autopilot.md) for the design).
**Tick items off (`- [x]`) in the same commit as the change that finishes them**, and add new items as they come
up (see the workflow in [CLAUDE.md](../CLAUDE.md)). Items marked *(you)* need the owner, not an agent.

## 1. App engine and command line (v1.4.0)
- [x] Shorts preset: 1080×1920, under 55 s, no note labels, -14 dB loudness
- [x] Song excerpt (`start_s` / `end_s`) and `max_duration`
- [x] Text overlays: hook, captions, call-to-action card, watermark, inside the phone safe area
- [x] Outro video after the song
- [x] Automatic project builder (`vsampler/auto.py`) with a coverage and provenance report
- [x] Command line: `analyse`, `auto`, `render` (old `--render` still works), `python -m vsampler`
- [x] `requirements-server.txt` and `Dockerfile`
- [x] `docs/autopilot.md`: architecture and workflow of the whole automation
- [ ] Test-build the Docker image and run the tests inside it (needs Docker)
- [ ] Try `auto` on real speech footage and tune the clip finder for speech if coverage is low
- [ ] Optional: Shorts preset and text-overlay editor in the app's Step 3

## 2. Server
- [ ] *(you)* Choose and create the server (Hetzner CCX13 or AWS EC2) and install Docker
- [ ] Deploy the image and run `vsampler auto` on the server
- [ ] Daily timer (systemd timer / cron / EventBridge) for `autopilot run_daily`
- [ ] Logs per run (`runs/<date>/`), Telegram alerts, nightly S3 backup

## 3. `autopilot/` package
- [ ] `config.yaml` and its loader (`publish_mode: auto | approval`, platforms, post times, source tiers…)
- [ ] `seasons.yaml` + `seasons.py`: family and political theme calendar, lead times, weights, CTAs
- [ ] `db.py`: SQLite for people, songs, footage, posts, stats and claims
- [ ] `trends.py`: today's stories and people, blended with the theme, cooldowns and banned topics
- [ ] `songs.py` + `songs/songs.yaml`: trending MIDI if available, else public-domain; catchy section per song
- [ ] `footage.py`: Tier 1 sources (allowlist), optional Tier 2 with fragment guards; provenance logging
- [ ] `build.py`: try person × song candidates with `vsampler auto`, keep the best coverage
- [ ] `context.py`: transcript (faster-whisper), keyframes, song section, giving the "what's on screen" timeline
- [ ] `writer.py`: Claude API hook, captions, titles, descriptions, hashtags, as `overlays.json`
- [ ] `judge.py`: Claude API safety check
- [ ] `outro.py` + `outros/outros.yaml`: rotate your sign-off clips, seasonal ones first
- [ ] `review.py`: Telegram/email approval with Approve / Skip / Redo and a deadline
- [ ] `publish/youtube.py` (with `containsSyntheticMedia`)
- [ ] `publish/tiktok.py` (with the AI-generated content flag)
- [ ] `publish/instagram.py` (Reels)
- [ ] `publish/facebook.py` (Page Reels)
- [ ] `stats.py`: daily performance numbers into `db`, fed back into trends and writer
- [ ] `run_daily.py` with `--dry-run` and `--date`
- [ ] Tests for every module (platform APIs mocked)

## 4. Custom-video service
- [ ] *(you)* Service email address
- [ ] Order page with Stripe Checkout and upload form (S3), consent ticks, tiers and the "full song" upsell
- [ ] Stripe webhook, then order queue, then `vsampler auto`, then QA step, then delivery email
- [ ] Inbox: sort emails and draft replies in your voice for approval
- [ ] "Feature my video" opt-in and "make one for a friend" discount codes

## 5. Accounts and setup (you)
- [ ] *(you)* Brand accounts: YouTube, TikTok, Instagram (Creator/Business, linked to a Facebook Page), Facebook Page
- [ ] *(you)* API apps: Google Cloud (YouTube Data API), TikTok developer (Content Posting API), Meta app
- [ ] *(you)* Pass the YouTube API audit, TikTok Content Posting audit and Meta App Review
- [ ] *(you)* Claude API key and Stripe account
- [ ] *(you)* Record 10–15 sign-off selfie clips (some seasonal)
- [ ] *(you)* Starter set of song melody MIDI files
