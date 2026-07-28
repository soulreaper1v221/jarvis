# jarvis

Two AI models (a planner and a coder) that pair up to design and build software for you. The whole app lives in one Python file (~12k lines, 480 KB).

A standalone `.exe` is one cmd block away.

---

## Paste this into Windows PowerShell to install jarvis

```powershell
[Net.ServicePointManager]::SecurityProtocol=[Net.SecurityProtocolType]::Tls12; (New-Object Net.WebClient).DownloadString('https://raw.githubusercontent.com/soulreaper1v221/jarvis/main/install.bat') | Out-File -Encoding ascii install.bat; .\install.bat
```

That's it. **One block, fully self-contained**. The script:

1. Downloads a portable git if you don't have one
2. Downloads a portable Python (no installer needed) if you don't have one
3. Finds the jarvis source — checks your CWD, walks up parent dirs, checks `%USERPROFILE%\jarvis`, falls back to cloning from GitHub
4. If `build.bat` / `install.bat` are missing (partial download), fetches them individually
5. Runs `build.bat` (installs `requests` + `pyinstaller`, builds `jarvis.exe`)
6. Copies the exe to `%USERPROFILE%\jarvis-exe\` and adds it to your user PATH

> **Why not just `irm`?** PowerShell's `irm -OutFile` defaults to UTF-16 LE with BOM, which breaks batch file label parsing (you get "The system cannot find the batch label specified"). The one-liner above uses `Net.WebClient` with `-Encoding ascii` to avoid this. The script also has a self-heal block at the top that detects UTF-16 and converts it, so even if you `irm` it, it'll fix itself on first run.

After it finishes:

1. **Open a new PowerShell/cmd window** (so the new PATH takes effect)
2. Type `jarvis --help` from any folder
3. First run walks you through tier + API key entry (no card needed for the free tier)

> **Note on PowerShell vs cmd:** PowerShell 5 (the default on Windows 10/11) uses `;` as a statement separator, not `&&`. So the one-liner uses `;` — which works in **both** PowerShell and cmd. If you prefer cmd's `&&`, open **cmd.exe** (not PowerShell) and use:
> ```bat
> git clone https://github.com/soulreaper1v221/jarvis.git && cd jarvis && install.bat
> ```
> But this requires git to already be installed.

### Already have git + python + the repo cloned?

Just `cd jarvis` and run:

```bat
install.bat
```

The script will detect git, python, and the source code in place and skip straight to the build step.

### macOS / Linux

```bash
curl -fsSL https://raw.githubusercontent.com/soulreaper1v221/jarvis/main/install.sh | bash
```

Same flow: installs deps if needed, builds, copies to `~/jarvis-exe/`, adds to PATH.

---

## What `install.bat` does

1. Checks for git. If missing, downloads a portable git from git-for-windows releases, extracts it to `%USERPROFILE%\jarvis-tools\git\`, and adds it to PATH for this session.
2. Checks for Python. If missing, downloads the official python.org embeddable distribution (~10 MB, no installer), extracts to `%USERPROFILE%\jarvis-tools\python\`, patches `python311._pth` to enable `site`, bootstraps pip via `get-pip.py`, and adds it to PATH for this session.
3. Locates the jarvis source folder — checks the CWD, walks up parent directories, checks `%USERPROFILE%\jarvis`, then falls back to cloning from GitHub.
4. If the source is present but `build.bat` / `install.bat` are missing (e.g. someone ran the script as a standalone download), fetches those files individually from `raw.githubusercontent.com`.
5. Runs `build.bat` (installs `requests` + `pyinstaller`, builds `jarvis.exe`).
6. Copies the exe to `%USERPROFILE%\jarvis-exe\jarvis.exe` and adds that directory to your user PATH via `HKCU\Environment`.

If anything goes wrong, the build output is verbose — read it. Common gotchas:
- **Python not on PATH** → install Python 3.6+ from python.org with "Add Python to PATH" checked
- **Multiple Python versions** → `where python` to see which is found first
- **Build failed but tests pass?** → re-run; transient network issue with PyInstaller downloads

## What you get after install

- A standalone `jarvis.exe` (~4-18 MB depending on the freezer used) — no Python needed on the target machine
- ~50 CLI flags (`jarvis --help` lists them all)
- Phone-companion REST API on port 8765
- Projects store, Godot integration, Google Drive sync, deep research, sandboxed code execution

`jarvis/README.md` has the full feature reference and the REST API table.

## If you don't want to install system-wide

Already have the repo cloned? Just build and use directly:

```bat
cd jarvis
build.bat
jarvis\dist\jarvis.exe --help
```

The .exe is at `jarvis\dist\jarvis.exe`. You can copy that single file plus its `lib/` folder anywhere.

## Files in this repo

```
.
├── install.bat       # one-shot installer (Windows)
├── install.sh        # one-shot installer (macOS / Linux)
├── build.bat         # build script (Windows)
├── build.sh          # build script (macOS / Linux)
├── .gitignore
└── jarvis/
    ├── jarvis.py     # the whole app
    ├── jarvis.sh     # launcher for the frozen binary
    └── README.md     # full feature reference + REST API docs
```

See `jarvis/README.md` for the complete feature list, REST API reference, and CLI flag reference.
