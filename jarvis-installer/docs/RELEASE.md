# jarvis v1.0 — release notes

The first stable release. A single-file Python CLI that pairs two
AI models (a planner and a coder) to design and build software.

## what's in the box

The `jarvis-windows.zip` in this release is a 158 KB package that
extracts to:

```
jarvis-windows.zip
├── README.md           quick install one-liner
├── setup.bat           double-click to install (recommended)
└── jarvis/
    ├── jarvis.py       the whole app
    ├── jarvis.sh       bash launcher
    ├── README.md       full feature reference
    ├── setup.bat       same as the top-level one
    ├── install.bat     one-shot installer (downloads Python if needed)
    ├── build.bat       just builds
    ├── build.sh, install.sh
    ├── quick-build.bat
    └── clean-reinstall.bat
```

The zip is source-only — no pre-built `.exe`. `setup.bat`
handles the build for you on first run, downloading a portable
Python if you don't have one.

## install

### Windows (recommended: download the zip)

1. Download `jarvis-windows.zip` from this release.
2. Extract anywhere (e.g. `C:\jarvis\`).
3. Open the extracted folder.
4. **Double-click `setup.bat`.**
5. If Windows shows a SmartScreen warning, click "More info"
   then "Run anyway".
6. Wait for setup to finish. It'll:
   - Find the source
   - Download Python 3.11 if you don't have it
   - Build `jarvis.exe` (one-time, ~30 sec)
   - Copy it to `%USERPROFILE%\jarvis-exe\`
   - Add that to your user PATH
   - Walk you through auth setup
7. Open a **new** cmd window. Type `jarvis`. Auth gate fires.

### Windows (alternative: build from source on GitHub)

If you'd rather have git clone the repo:

```bat
git clone https://github.com/soulreaper1v221/jarvis.git
cd jarvis
install.bat
```

Same flow as above, just from source.

### macOS / Linux

```bash
curl -fsSL https://raw.githubusercontent.com/soulreaper1v221/jarvis/main/install.sh | bash
```

## first-run auth

On first run, `setup.bat` will launch the auth-setup wizard. You
have three options:

1. **Windows Hello** (face / fingerprint / PIN). Pops the standard
   Windows Security dialog. If you don't have a fingerprint
   reader or webcam, skip this and use the passcode.
2. **Webcam face recognition** (optional). Snap a photo from
   your webcam, save it to `~/.jarvis/face.jpg`, and jarvis will
   match against it on every run. Requires `opencv-python`:
   ```bat
   pip install opencv-python
   jarvis --auth-setup
   ```
3. **Master passcode.** Hardcoded into the binary:
   `Soulreaper1v2@22`. Always works as a fallback.

You can also set `JARVIS_BYPASS=<passcode>` to skip the gate in
CI / automation.

## daily use

```bat
jarvis                                       REM interactive chat
jarvis "build me a CLI todo app"             REM one-shot request
jarvis --research "the new pytorch API"      REM with web research
jarvis --deep-research "quantum computing"   REM multi-hour session
jarvis --generate-file "a Dockerfile"        REM produces a file
jarvis --auth-test                            REM see which layers work
jarvis --help                                 REM list all 47 flags
```

## features at a glance

| Area               | What it does                                                 |
|--------------------|--------------------------------------------------------------|
| Core               | two-model pairing, free/paid tiers, custom endpoints        |
| Auth               | 3 layers, gated on every `jarvis` run                       |
| Research           | live web context, multi-hour deep sessions                  |
| Files              | text + binary gen, sandbox-tested                           |
| Phone              | REST API server, 6-digit pairing, 35+ endpoints            |
| Projects           | scaffold/adopt/import, godot + python                       |
| Offline            | refuse remote APIs, use local models only                   |
| Self-modify        | jarvis patches its own source (experimental)                |
| Cloud              | opt-in account + encrypted config sync (experimental)       |
| Drive              | watch-folder sync to google drive / dropbox (experimental)  |
| Godot              | project-aware writing, auto-detects from `project.godot`    |
| Build              | pyinstaller + cx_Freeze, produces a single .exe             |

See `CAPABILITIES.md` for the full breakdown (every flag, every
endpoint, every feature) and `CHANGELOG.md` for what changed.

## requirements

- **Windows 10/11** for the .exe (or **Python 3.6+** to run from
  source on any platform).
- **Internet** for the initial Python download and the LLM
  API calls (or a local model endpoint + `--offline`).
- **~150 MB** disk for the binary + frozen stdlib.
- **~30 seconds** for the first-time build.
- **No admin rights** required.
- **No Python install** required (the setup downloads a portable
  one to `%USERPROFILE%\jarvis-tools\`).

## known issues

- Some `--help` text is longer than ideal. The user-facing
  documentation in `CAPABILITIES.md` is the canonical reference.
- The phone server's banner prints to stdout while the test
  suite prints to stderr; running `python3 jarvis.py --test`
  interleaves test output with the banner. Cosmetic only.
- `--self-modify` is marked experimental. It auto-reverts if
  tests fail, but you should commit your work before using it.

## getting help

- **Bug reports / feature requests**: file an issue on GitHub
  at https://github.com/soulreaper1v221/jarvis/issues.
- **Security disclosure**: same, with the `security` label.
- **The user-facing README** lives at `jarvis/README.md` in
  the zip; the **long-form feature list** is in
  `CAPABILITIES.md`; the **changelog** is in `CHANGELOG.md`.

## what's next

- v1.1 will harden the experimental features (self-modify
  safety, cloud sync UX, godot project detection) and graduate
  them to stable if they hold up.
- v1.2 will add a web UI for the phone-companion flow (the
  current phone UI is HTML served from the binary, but it's
  text-only).
- v2.0 will split the source into a small package + plugins
  if the project outgrows a single file.

## license

MIT. See `LICENSE` (same as the GitHub repo).
