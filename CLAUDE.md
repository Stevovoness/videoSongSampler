# Video Sampler: notes for AI agents

Read this file before changing anything in the repository.

## Mandatory workflow: after EVERY change
Every change (code, tests, build scripts, assets, installer, docs) must end with these steps, in order. Do not
finish a task or hand back to the user until all of them are done. **Never skip the docs or the to-do list,
however small the change.**

0. **Before starting:** read `docs/TODO.md` to see where the work stands. If the task isn't on the list, add it.

1. **Run the tests:** `.venv\Scripts\python -m pytest tests -q`. They must pass. Add or update tests for the
   behaviour you changed.
2. **Update the docs** so they describe the code as it is now:
   - `docs/CHANGELOG.md`: add a line under **Unreleased** (or under the new version if you bumped it).
   - `README.md`: for anything a user sees (features, buttons, keyboard shortcuts, requirements).
   - `docs/architecture.md`: when you add, rename or move modules, or change how the engine works.
   - `docs/releasing.md`: when the build, installer, server image or release steps change.
   - `docs/autopilot.md`: when the command line, `auto.py`, overlays, the server image or the planned
     automation (`autopilot/`) change. Keep its **Status** table current.
   - This file: when the workflow or the project's conventions change.
3. **Tick off the to-do list:** in `docs/TODO.md`, change `- [ ]` to `- [x]` for every item the change finished,
   and add any new follow-up items you found. Do this in the same commit as the change.
4. **Commit and push to git:**
   ```
   git add -A
   git commit -m "<short summary of the change>"
   git push origin main
   ```
   Use clear commit messages. Never commit build output (`build/`, `dist/`, `release/`, `installer/Output/`),
   `.venv/` or `tools/audiveris/`; `.gitignore` already excludes them. If a push is rejected, run
   `git pull --rebase origin main`, re-run the tests, and push again. Never force-push.

Before handing back, check: tests pass ✔, docs and changelog updated ✔, `docs/TODO.md` ticked ✔, pushed ✔.

## Project at a glance
A Windows desktop app (Python 3.11, PySide6) that turns short video clips of single notes and percussive sounds
into a song plus a collage music video. See `README.md` for features and `docs/architecture.md` for how the code
fits together.

- Run from source: `.venv\Scripts\python main.py` (window), or `.venv\Scripts\python main.py auto|analyse|render …`
  (command line, see `vsampler/cli.py`).
- Set up a fresh environment: see "From source" in `README.md` (create the venv with `py -3.11`).
- Build the release (installer and portable zip): `.venv\Scripts\python build_exe.py`. Full steps are in
  `docs/releasing.md`.
- Version: `vsampler/__init__.py` (`__version__`). Bump the minor version for new features and the patch version
  for fixes, and record it in `docs/CHANGELOG.md`.

## Conventions
- Match the surrounding style: type hints, `from __future__ import annotations`, short docstrings, and comments
  only where something isn't obvious.
- The engine (`vsampler/*.py`, `vsampler/render/`, `vsampler/importers/`) must not import Qt. Only `vsampler/gui/`
  uses PySide6.
- No window may extend past the screen. Size new windows with `gui/screen.preferred_size()`, wrap large content
  in `gui/screen.scrollable()`, keep important buttons outside the scrolling part, and avoid big fixed minimum
  sizes. `ScreenGuard` (installed in `main.py`) is the safety net.
- User-facing text is friendly and non-technical (the app is aimed at non-musicians).
- The engine must keep working headless on Linux (the automation server, `Dockerfile`). Don't add Windows-only
  calls to it, and add new engine dependencies to `requirements-server.txt` as well.
- Save files (`.vsproj`) must stay backward compatible. `Project.from_json` ignores unknown fields and fills in
  missing ones.
- On Windows PowerShell 5.1, read files with `-Encoding UTF8`. The source contains non-ASCII characters
  (·, —, 🥁) that would otherwise be corrupted.
