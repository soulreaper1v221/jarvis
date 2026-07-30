# jarvis — what it can do

A single-file Python CLI that pairs two AI models (a planner and a
coder) to design and build software for you. No Python required to
run the pre-built binary. ~12,500 lines, ~512 KB source, ~4 MB
frozen binary, ~50 MB on disk with the bundled stdlib.

Below is **every feature** the app has, grouped by area. Most
features work in three modes: (1) the default interactive mode,
(2) a one-shot CLI invocation, and (3) the phone-companion REST API
when `--serve` is running.

> **v1.0 stability:** the planner/coder loop, auth, research, file
> generation, the phone companion, projects, offline mode, and
> the build pipeline are **stable**. Self-modification, cloud sync,
> google drive sync, and explicit `--godot` are marked
> **`[EXPERIMENTAL]`** — they work, but the API may change
> between minor versions. A warning is printed the first time you
> use one in a session. See `CHANGELOG.md` for the full list.

---

## Core: the planner/coder loop

- **One-shot requests.** `jarvis "build me a CLI todo list app"` —
  plans with one model, codes with another, returns a plan + code.
- **Interactive mode.** `jarvis` (no args) drops you into a chat
  with a memory of past sessions.
- **Two-model pairing.** The "planner" (default: a strong reasoning
  model) writes the design, the "coder" (default: a code-specialist
  model) writes the implementation. Either can be the same model;
  each is independently configurable.
- **Free-tier and paid-tier model catalogs.** Built-in lists of
  curated models: ~8 free models (Llama 3.3 70B, Qwen 2.5 Coder
  32B, DeepSeek V3, Mistral Small, Gemini Flash, etc.) and ~6 paid
  options (Claude 3.5 Sonnet, GPT-4o, Claude 3.5 Haiku, etc.).
- **Custom endpoints.** Point at OpenAI direct, Anthropic direct,
  Ollama, vLLM, LM Studio, or any OpenAI-compatible API by giving
  the URL + key + model name(s).
- **Auto-pick vs manual-pick.** Let the tool pick the best free
  models for the task, or pick the planner/coder separately.
- **Persona switch.** `--persona engineer` (concise, code-first)
  or `--persona jarvis` (more verbose, with explanations).

## Output & build modes

- **`--write`** — write generated files to disk (default: print to
  stdout only).
- **`--output <dir>`** — set the output directory
  (default: `~/.jarvis/output/`).
- **`--with-tests`** — also generate a pytest test file for the
  code.
- **`--no-review`** — skip the code-review pass (faster, lower
  quality).
- **`--text-only`** — strip decorative formatting (for piping into
  scripts / logs).
- **`--json`** — print the full result as JSON (one-shot mode).
- **Mode picker.** `--mode gui` (Tk chat window), `--mode terminal`
  (REPL), `--mode ask` (Q&A only), `--mode auto` (default: pick
  by environment).

## Authentication (3 layers, runs on every `jarvis` invocation)

- **Windows Hello** — face / fingerprint / PIN via `ctypes` +
  `credui.dll`. No extra deps. Pops the standard Windows Security
  dialog.
- **Webcam face recognition** — if `opencv-python` is installed
  and a face is registered, snap a photo from the default camera
  and match it against `~/.jarvis/face.jpg` using MSE.
- **Master passcode** — hardcoded fallback: `Soulreaper1v2@22`.
  Bypasses with `JARVIS_BYPASS=<passcode>` env var.
- **`--auth-setup`** — interactive wizard: tests each layer,
  registers a face photo from the webcam, lets you test the
  passcode. Bypasses the gate (so you can set up auth).
- **`--auth-test`** — report which of the 3 layers work on the
  current machine.
- **`--no-auth`** — skip the auth gate for one run.
- **`--change-passcode`** — rotate the master passcode. Prompts for
  the current passcode, then the new one (twice). The new
  passcode is stored in `~/.jarvis/config.json` as
  `passcode_override`. The hardcoded fallback in the binary
  still works too, so a fresh install on a new box can still
  authenticate. Use `--reset` to clear the override.
- **State stored in `~/.jarvis/`** — face photo at
  `~/.jarvis/face.jpg`, config + sessions in the same dir.

## Research (web context before planning)

- **`--research`** — gather live web context before planning:
  fetch URLs found in the request, search DuckDuckGo for library
  / API / framework names mentioned, and pass the synthesized
  context to the planner.
- **`--research-url <url>`** — repeatable, adds explicit URLs to
  fetch.
- **`--research-term <term>`** — repeatable, adds explicit search
  terms.
- **`--no-research`** — disable research for this run, even if
  the config has it on.
- **Source: stdlib only.** `urllib.request` + a tolerant DDG HTML
  scraper + a simple HTML→text stripper. No API keys, no Selenium.

## Deep research (multi-hour, resumable)

- **`--deep-research "<topic>"`** — start a long-running session.
  Iteratively searches the web, fetches pages, asks the AI to
  merge findings into a running notebook, and you can ask
  follow-up questions mid-session. Stops on time budget,
  iteration limit, or signal.
- **`--deep-report "<topic>"`** — one-shot: 20–50 search+fetch
  rounds, then writes a synthesized report. Faster, no
  follow-up.
- **`--resume <session-id>`** — continue an existing session from
  where it left off.
- **`--sessions`** — list all sessions on disk.
- **`--delete-session <id>`** — wipe a session and all its files.
- **`--max-time`** — time budget (default 5h). Accepts `1800`,
  `30m`, `2h30m`, `1h15m30s`, etc.
- **`--max-iterations N`** — hard cap on iterations (default 50).
- **Persisted on disk.** Each session lives at
  `~/.jarvis/sessions/<id>/` with `session.json` + human-readable
  mirrors `notes.md`, `sources.json`, `questions.json`, `plan.md`,
  `report.md`.
- **Q&A mid-session.** Ask a question → AI answers using the
  current notes → notes are saved with the Q&A in
  `questions.json`.

## File generation (text + binary)

- **`--generate-file "<request>"`** — produce a single file
  (Dockerfile, SQL migration, gdscript, etc.) and write it to
  disk. Detects text vs binary from the request keywords.
- **`--generate-output <path>`** — where to write the file
  (default: `./<filename>` in cwd).
- **`--sandbox-test`** — if the generated file is Python, run it
  in a sandbox and report stdout / stderr / exit code. Sandbox
  has AST-level safety checks (blocks `os`, `subprocess`,
  `requests`, `ctypes`, `socket`, etc., and absolute paths in
  `/etc`, `/usr`, `/var`, etc.).
- **Binary generation** — produces a JSON manifest with
  base64-encoded content + a `generator_script` so the user can
  regenerate it later.
- **Auto filename** — picked from a comment in the first line
  (`# hello.py`) or from the language hint.

## Self-modification (jarvis editing its own source) `[EXPERIMENTAL]`

- **`--self-modify "<request>"`** — ask the AI for a unified-diff
  patch, apply it, run the test suite, auto-revert on failure,
  commit on success. Requires `enable_self_modify=true` in
  config and a clean git working tree.
- **`--self-savepoint <label>`** — snapshot the current state on
  the `self-modify` side branch.
- **`--self-revert <target>`** — roll back to a save point.
  Accepts a commit hash, branch name, or label substring.
- **`--self-status`** — show current branch, save points, last
  applied change.
- **Snapshot on a side branch** — the main branch is never
  touched. The `self-modify` branch is reset to current `main`
  HEAD before each modification.
- **Auto-revert on test failure** — if the test suite fails
  after the patch is applied, jarvis checks out the snapshot
  and `git clean`s the worktree.

## Phone companion (REST API + pairing)

- **`--serve [host]`** — start a local web server (default
  `0.0.0.0:8765`). A phone on the same WiFi can open the URL,
  type the 6-digit pairing code, and control jarvis from the
  browser.
- **`--port <port>`** — change the port.
- **`--pair`** — print a fresh 6-digit pairing code + URL.
- **`--list-devices`** — list paired devices.
- **`--unpair <device-id>`** — remove a paired device.
- **Per-device modes** — each device has its own mode flags
  (offline, sandbox, research, etc.). Shared global modes are
  also configurable.
- **REST endpoints (35+):**
  - `GET /` — server status
  - `GET /api/status`, `POST /api/pair`
  - `GET /api/devices`, `DELETE /api/devices`
  - `GET /api/modes`, `POST /api/modes`
  - `POST /api/chat`
  - `GET /api/sessions`, `POST /api/sessions`,
    `GET /api/sessions/<id>`,
    `POST /api/sessions/<id>/ask`,
    `POST /api/sessions/<id>/pause`,
    `POST /api/sessions/<id>/resume`,
    `POST /api/sessions/<id>/report`
  - `POST /api/generate`, `POST /api/sandbox-test`
  - `GET /api/qr` (JSON with the URL + code, for the phone)
  - `GET /api/config`, `POST /api/config`
  - `POST /api/account/signup`, `POST /api/account/login`,
    `POST /api/account/logout`, `GET /api/account/status`
  - `GET /api/files`, `GET /api/files/<path>`
  - `GET /api/cloud/code`, `GET /api/cloud/info`
  - `GET /api/projects`, `POST /api/projects`,
    `GET /api/projects/active`, `POST /api/projects/active`
  - `GET /api/drive`, `POST /api/drive`

## Cloud sync (optional, opt-in) `[EXPERIMENTAL]`

- **`--cloud-signup <email>`** — create an account; prompts for
  a password (≥6 chars). Config is encrypted client-side with
  PBKDF2-derived keys before upload.
- **`--cloud-login <email>`** — sign in, remote config is
  merged into the local one.
- **`--cloud-logout`** — forget the local sign-in (doesn't delete
  the account).
- **`--cloud-status`** — show whether cloud sign-in is active.
- **`--cloud-url <url>`** — set the cloud backend URL (default
  via `JARVIS_CLOUD_URL` env var).
- **`--change-cloud-password`** — rotate the password on the cloud
  account. Prompts for the current password, then the new one
  (twice). Re-encrypts the stored config with a new salt + new
  key. The old password is invalidated as part of the change.
- **Crypto: PBKDF2 + Fernet-like encrypt + HMAC.** Config is
  never sent in plaintext; the cloud backend only stores opaque
  ciphertext.

## Projects store (a project manager built in)

- **`--project list`** — list all projects in the store
  (`~/.jarvis/projects/`).
- **`--project new <name> godot`** — scaffold a fresh Godot
  project (project.godot + scenes/ + scripts/).
- **`--project new <name> python`** — scaffold a fresh Python
  project (pyproject.toml + src/<name>/__init__.py).
- **`--project add <path> <name>`** — adopt an existing project
  from a path (manifest only, files stay where they are).
- **`--project import <path> <name>`** — copy a project into
  the store.
- **`--project use <name>`** — set the active project (subsequent
  `jarvis` invocations default to writing into it).
- **`--project active`** — show the current active project.
- **`--project path`** — print the active project's path.
- **`--project open`** — open the active project's folder in
  the file explorer.
- **`--project status`** — show details of the active project.
- **`--project remove [--delete-files] <name>`** — unregister
  a project; with `--delete-files`, also remove the files.

## Godot integration (project-aware writing)

- **`--godot`** — force Godot project-aware mode on. The codex
  model gets a Godot-flavored system prompt: gdscript style
  (snake_case vars, PascalCase nodes), `_ready()` /
  `_process(delta)` lifecycle, signals (`signal foo(args)` /
  `foo.emit(...)`), `@export var foo: int = 0` (Godot 4), etc.
- **`--no-godot`** — force it off.
- **Auto-detect.** If `project.godot` is in cwd or a parent dir,
  Godot mode is on by default.
- **Reads `project.godot`** — picks up the engine version
  (Godot 4.x vs 3.x from `config_version`), main scene,
  autoloads, and feeds them into the prompt.

## Google Drive sync (watch-folder, no OAuth) `[EXPERIMENTAL]`

- **Drive folder config** at `~/.jarvis/drive.json`. Point
  jarvis at a folder that's synced by Google Drive / Dropbox /
  OneDrive desktop, and any project changes show up on every
  machine that has that folder.
- **Name-based sync** — `jarvis drive push` copies the store to
  the drive folder; `jarvis drive pull` does the reverse.
  Never overwrites: a project present on both sides is left
  alone (so the user has to resolve conflicts themselves).
- **Works offline, no API keys.** Stdlib only.

## Sandbox (run generated code safely)

- **AST-level safety check** before running. Blocks: `os`,
  `sys`, `subprocess`, `ctypes`, `cffi`, `socket`, `ssl`,
  `urllib`, `http`, `requests`, `httpx`, `asyncio`,
  `multiprocessing`, `threading`, `shutil`, `importlib`, `imp`,
  `signal`, and dangerous attribute access on builtins
  (`system`, `popen`, `exec`, `kill`, `chmod`, `chown`, etc.).
- **Blocks suspicious paths** — strings starting with `/etc/`,
  `/usr/`, `/var/`, `/root/`, `/home/`, `/tmp/`, `/proc/`,
  `/sys/`, or `C:\…`.
- **Blocks path traversal** in `extra_files` (e.g. `../evil.txt`).
- **Runs in a temp directory** so file ops are scoped.
- **Per-run timeout** (default 10s).
- **Network isolation on Linux** (uses `unshare --net` when
  available; falls back gracefully otherwise).
- **Env scrubbed** — no API keys leak into the sandbox.

## Config & setup

- **`~/.jarvis/config.json`** — persists the API keys, model
  picks, persona, timeouts, mode, etc. Mode `0o600` on Unix.
- **`~/.dual_ai/`** — legacy config dir; auto-migrated to
  `~/.jarvis/` on first access.
- **`--show-config`** — print the saved config (with keys
  masked) and exit.
- **`--set KEY=VALUE`** — set a config value from the CLI.
  Repeatable. Example: `--set enable_self_modify=true
  --set persona=jarvis`.
- **`--reset`** — wipe the saved config and re-run the startup
  wizard.
- **Env var shim** — old `DUAL_AI_*` env vars still work;
  new `JARVIS_*` env vars are preferred. Both are honored
  (legacy wins when both are set).
- **First-run wizard** — tier chooser (free / paid / custom /
  quit), key entry, model picks, all stored in one go.

## Offline mode

- **`--offline`** — refuse any remote API calls. All model
  endpoints must be local (Ollama, vLLM, LM Studio, etc.).
  Refuses URLs that aren't on `localhost` / `127.0.0.1` / a
  private subnet. Also disables `--research` and deep research
  (they need the web).
- **`_is_local_url()`** helper — recognizes `localhost`,
  `127.0.0.1`, `10.x`, `192.168.x`, `172.16-31.x`.

## Build & packaging (jarvis building itself)

- **`--build`** — package as a single executable via
  PyInstaller. On Linux without `libpython.so`, falls back to
  cx_Freeze. Output: `dist/jarvis.exe` (Windows) or
  `dist_exe/jarvis` + `lib/`.
- **`--build-portable`** — same, plus bundles into
  `jarvis-portable.tar.gz` (and `.zip`) that the user can
  extract anywhere. Adds a `jarvis.sh` launcher + `README.txt`
  next to the binary.
- **Embedded test suite** — 122 tests, all green, runnable
  with `python3 jarvis.py --test`. Covers auth, sessions,
  research, sandbox, file-gen, projects, Godot, drive sync,
  pairing, cloud crypto, server routes, env shims, etc.

## Misc

- **Single Python file** — `jarvis.py` is the whole app. No
  monorepo, no microservices, no `pip install jarvis`. Just one
  file + a build script.
- **Python 3.6+ compatible.** Uses `from __future__ import
  annotations`, no walrus, no `match/case`, no PEP 604
  unions. Tested on Python 3.11.
- **No required third-party deps at runtime** — only `requests`
  is required, and it's lazy-imported (so `--help`,
  `--show-config`, `--auth-test`, etc. work without it).
- **SmartScreen-friendly** — `setup.bat` and `install.bat` both
  have UTF-16 self-heal blocks at the top, in case `irm`
  downloads them as UTF-16 LE with BOM.
- **Cross-platform** — works on Windows, macOS, Linux. The
  frozen binary is per-platform; the source is one file.

---

## Quick reference: all 47 CLI flags

```
--no-review            Skip the code-review pass.
--with-tests           Also generate pytest tests.
--write                Write generated files to disk.
--output DIR           Output directory (default ~/.jarvis/output/).
--persona              engineer | jarvis.
--text-only            Strip decorative formatting.
--json                 Print the full result as JSON.
--reset                Wipe the saved config and re-run setup.
--show-config          Print the saved config (with keys masked) and exit.
--set KEY=VALUE        Set a config value. Repeatable.
--mode                 auto | gui | terminal | ask.
--research             Gather web context before planning.
--research-url URL     Add a URL to fetch. Repeatable.
--research-term TERM   Add a search term. Repeatable.
--no-research          Disable research for this run.
--deep-research TOPIC  Start a long-running deep research session.
--deep-report TOPIC    One-shot deep research with a written report.
--resume SESSION_ID    Resume an existing deep research session.
--sessions             List all deep research sessions.
--delete-session ID    Delete a deep research session.
--max-time             Time budget (1800 / 30m / 2h30m / ...). Default 5h.
--max-iterations N     Max research iterations. Default 50.
--offline              Refuse remote API calls. Use local models.
--generate-file REQ    Generate a single file of any type.
--generate-output PATH Where to write the generated file.
--sandbox-test         Run the generated Python in the sandbox.
--self-modify REQ      [EXPERIMENTAL] Let jarvis modify its own source code.
--self-savepoint LBL   [EXPERIMENTAL] Snapshot the current state.
--self-revert TARGET   [EXPERIMENTAL] Roll back to a save point.
--self-status          [EXPERIMENTAL] Show self-modify state.
--auth-setup           Set up authentication (Windows Hello / webcam / passcode).
--auth-test            Report which auth layers work.
--no-auth              Skip auth for this run.
--change-passcode      Rotate the master passcode. Stores an override
                       in config; hardcoded fallback still works.
--serve [HOST]         Start the phone-companion web server. Default 0.0.0.0.
--port PORT            Port for --serve. Default 8765.
--pair                 Print a fresh 6-digit pairing code.
--qr                   Deprecated. (Was a QR code; now just prints a URL.)
--unpair ID            Remove a paired device.
--list-devices         List all paired devices.
--cloud-signup EMAIL   [EXPERIMENTAL] Create a cloud account.
--cloud-login EMAIL    [EXPERIMENTAL] Sign in to a cloud account.
--cloud-logout         [EXPERIMENTAL] Forget the current cloud sign-in.
--cloud-status         [EXPERIMENTAL] Show whether cloud sign-in is active.
--cloud-url URL        [EXPERIMENTAL] Set the cloud backend URL.
--change-cloud-password [EXPERIMENTAL] Rotate the cloud account password.
--godot                [EXPERIMENTAL] Force Godot project-aware writing on.
--no-godot             Force Godot project-aware writing off.
--project SUBCMD       Manage projects: list | new | add | import |
                       use | active | path | open | status | remove.
```
