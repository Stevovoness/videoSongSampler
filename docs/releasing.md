# Building and releasing

Keep this file up to date when the build or release process changes (see [CLAUDE.md](../CLAUDE.md)).

## One-time setup (Windows 10/11, 64-bit)
```
py -3.11 -m venv .venv
.venv\Scripts\pip install -r requirements.txt
.venv\Scripts\pip install --no-deps basic-pitch==0.4.0
winget install JRSoftware.InnoSetup --scope user      # for the installer; without it only the zip is made
```
Use Python 3.11: PyInstaller doesn't support 3.10.0.

## Build
1. Bump `__version__` in `vsampler/__init__.py` and move the **Unreleased** entries in `docs/CHANGELOG.md` under
   that version.
2. Run the tests: `.venv\Scripts\python -m pytest tests -q`
3. Build: `.venv\Scripts\python build_exe.py`
   - It runs `setup_tools.py`, which downloads Rubber Band and Audiveris (with its Java runtime) into `tools/`
     the first time.
   - It makes a one-folder PyInstaller build (`VideoSampler.spec`) in `dist/VideoSampler/`.
   - It writes `release/VideoSampler-<version>-Windows-portable.zip` and, with Inno Setup installed,
     `release/VideoSampler-Setup-<version>.exe` (from `installer/VideoSampler.iss`; it installs per-user and
     needs no admin rights).
4. Check that it starts: run `dist\VideoSampler\VideoSampler.exe`, or the installer.

`build/`, `dist/`, `release/`, `installer/Output/` and `tools/audiveris/` are build output. They're in
`.gitignore` and must never be committed.

## Publish
1. Commit and push the version bump and the changelog (`git push origin main`).
2. Tag the release and attach both files to a GitHub release, either on the website (Releases → Draft a new
   release → tag `v<version>`) or with the GitHub CLI:
   ```
   gh release create v<version> release\* --title "Video Sampler <version>" --notes-file <notes.md>
   ```
   Use that version's section of `docs/CHANGELOG.md` as the release notes.
