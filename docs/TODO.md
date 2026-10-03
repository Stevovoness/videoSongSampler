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
- [x] Try `auto` on real speech footage and tune the clip finder for speech (long videos cut into pieces, voice
      range, coarser pitch grid, all CPU cores, saved analyses; tune picks its track and is centred on the voice)
- [ ] **Show the speaker, not B-roll:** check each clip's frame for a face (OpenCV) and prefer takes with one;
      drop clips with no face when there are enough others
- [ ] **Keep the face in shot:** when a 16:9 clip is cropped to 9:16, centre the crop on the face, not the frame
- [ ] **Hide burned-in banners:** crop off the source's own captions/tickers (e.g. the White House banner along the
      bottom), first as a setting, later found automatically (the part of the frame that never changes)
- [ ] **Choose who sings:** group clips by speaker (face or voice) so a video can be one person singing
- [ ] Speed up the clip finder's start-up for each part (~40 s before pitch tracking starts)
- [ ] `interstellar-suite-hans-zimmer.mxl` won't load ("largest tick … likely corrupt"): find out why
- [x] Faster clip finder in the app: all CPU cores, coarse pitch search plus precise re-measure, reused worker
      processes, "talking (faster)" mode
- [x] Save every video's analysis so reopening it (any project, after a restart) is instant
- [x] `auto` plays every part of the song (chords and bass), not just the tune
- [x] Writer voice guide in `docs/autopilot.md` (casual, "one of you", gently mocks hyper-patriotism)
- [ ] Long videos (over 30 minutes) in the app's clip finder: cut them into pieces like `auto` does
- [ ] **Everything `auto` does, in the app** (projects made by `auto` already keep their text and outro when
      opened, saved and rendered in the app, but these can't be seen or changed there yet):
  - [ ] Text editor in Step 3: add, edit and time the hook, captions, call to action and watermark, with a live
        preview frame
  - [ ] Outro video picker in Step 3
  - [ ] "Shorts / Reels / TikTok" preset in Step 3 (size, length cap, loudness, no labels in one click)
  - [ ] "Build it for me" in Step 1: pick long videos and the app chooses every clip and take (`auto_project`),
        with the coverage report
  - [ ] "Only the tune / every part" switch next to the song's tracks
- [ ] **Choose a section of the song in the app** (Step 2): set a start and end (e.g. drag on the piano roll, or
      "chorus only"), so previews and the final render only make that part instead of the whole 4-minute song.
      The engine already supports it (`SongOptions.start_s` / `end_s`, `--start` / `--end` on the command line).
      Remember the section in the project file.
- [ ] Optional: Shorts preset and text-overlay editor in the app's Step 3

## 2. Server
- [ ] *(you)* Choose and create the server (Hetzner CCX13 or AWS EC2) and install Docker
- [ ] Deploy the image and run `vsampler auto` on the server
- [ ] Daily timer (systemd timer / cron / EventBridge) for `python -m autopilot run`
- [ ] Logs per run (`runs/<date>/`), Telegram alerts, nightly S3 backup

## 3. `autopilot/` package
- [x] Phase 1: config, ideas queue, seasons, song finder with chorus finding, people and local footage, build
      (with "different clips" seeds), context (transcripts, frames), writer + judge (Claude, template fallback),
      outro picker, run state machine, notifications
- [x] Review page: preview without text, edit and preview the text, apply it without re-rendering, check the
      final video, approve; regenerate the video or the idea at any step
- [x] `vsampler text`: add text to a finished video without re-rendering
- [ ] Try the writer with a real Claude API key (`ANTHROPIC_API_KEY`) and tune `style.md` on real results
- [x] Choose a different part of the song on the review page (suggested parts or your own times; `--part`)
- [x] Review page: background jobs actually run (fixed), with a progress banner, video overlay and "done" message
- [ ] Editable title / description / hashtags on the review page
- [ ] Review page on the server behind HTTPS (Caddy), with the link sent by Telegram
- [x] `ideas.py` + `ideas.yaml`: turn `Project combination ideas.txt` into a queue of {person, song, tier}
- [ ] `people.yaml`: add everyone on the ideas list (official sources, aliases, tier, a reference portrait)
- [ ] Song finder: your library, then the Lakh MIDI dataset index, then transcription (Demucs + basic-pitch)
- [x] Automatic chorus finding (most repeated stretch of the tune); lyric lines still to do
- [x] Transcripts of source footage (faster-whisper), only the blocks used, cached
- [x] `config.yaml` and its loader (`publish_mode: auto | approval`, tiers, review, Claude…); platforms and post
      times come with publishing
- [x] `seasons.yaml` + `seasons.py`: family and political theme calendar, lead times, words, tone, CTAs
- [x] `db.py`: SQLite for run history (cooldowns, used ideas), posts and claims
- [ ] `trends.py`: today's stories and people, blended with the theme, cooldowns and banned topics
- [x] `songs.py`: song files by title + chorus per song (`autopilot_data/songs.yaml`)
- [x] `footage.py`: local footage, tiers; YouTube search/download written but untested (needs `YOUTUBE_API_KEY`)
- [x] `build.py`: idea -> plan -> `vsampler auto` -> coverage check -> video without text
- [x] `context.py`: transcript (faster-whisper), frames, phrases, giving the "what's on screen" timeline
- [x] `writer.py`: Claude API hook, captions, titles, descriptions, hashtags (tested with a stand-in)
- [x] `judge.py`: rule checks + Claude safety check
- [x] `outro.py`: rotate your sign-off clips, seasonal ones first (from `autopilot_data/outros/outros.yaml`)
- [x] Review: the web review page plus Telegram/email notification, with a deadline
- [ ] `publish/youtube.py` (with `containsSyntheticMedia`)
- [ ] `publish/tiktok.py` (with the AI-generated content flag)
- [ ] `publish/instagram.py` (Reels)
- [ ] `publish/facebook.py` (Page Reels)
- [ ] `stats.py`: daily performance numbers into `db`, fed back into trends and writer
- [x] `python -m autopilot run` with `--date` (and `--idea`); a separate `--dry-run` comes with publishing
- [x] Tests for the phase 1 modules (`tests/test_autopilot.py`, Claude stood in)

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
