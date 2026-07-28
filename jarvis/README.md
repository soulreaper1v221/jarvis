# jarvis

A single `.exe` that pairs two AI models — one for **planning/review** and one for **code generation** — to design and build software for you.

> "build me a CLI todo list app" → returns a plan, generates each module, reviews the code, and (optionally) writes tests.

**One `.exe`. Pick your tier. Zero setup.**

---

## What it does (full feature list)

A single Python script + GUI + REST API for orchestrating two AI models (a planner and a codex) to build software for you. Everything below is in the shipped binary.

### Core: two-model orchestration
- **Plan + build pipeline** — one model designs the change (plan, file list, review), the other writes the code, then a review pass. Output is a project, not just a chat answer.
- **Personas** — `--persona engineer` (default, terse) or `--persona jarvis` (slightly more verbose, friendly).
- **Interactive terminal menu** — pick a category, type a request, get a result. No flags required.
- **GUI mode** — full chat window (tkinter). Type, get streamed code, save outputs.
- **Text-only / JSON output** — `--text-only` strips decorations, `--json` returns the full structured result.

### Models & providers
- **Free tier** — OpenRouter free models (default: `meta-llama/llama-3.3-70b-instruct:free` for planning, `qwen/qwen-2.5-coder-32b-instruct:free` for code). No key needed beyond an OpenRouter account.
- **Paid tier** — Claude 3.5 Sonnet for both, one model no decisions needed.
- **Custom** — point at any OpenAI-compatible endpoint: OpenAI, Anthropic via proxy, Ollama, vLLM, LM Studio, llama.cpp, etc.
- **Per-model swap** — different model for planning vs. code, or the same model for both.
- **Per-call overrides** — `--set sonnet_model=...` etc. without editing the file.

### Deep research
- **Pre-planning research** — `--research` fetches the URLs in your request + a quick search for any library/API names mentioned, so the plan reflects the current API, not the model's training cutoff.
- **Add custom URLs / terms** — `--research-url https://...` (repeatable), `--research-term 'Stripe API 2024'`.
- **One-shot deep report** — `--deep-report "topic"` does one big batch of searches + fetches, writes a structured report.
- **Long-running research sessions** — `--deep-research "topic"` iterates over hours, builds a notes file, can be paused/resumed, and you can ask follow-up questions mid-session (`--resume <id>`).
- **Time / iteration budgets** — `--max-time 5h`, `--max-iterations 50`, accepts `"5h"`, `"30m"`, `"2h30m"`, or raw seconds.
- **List / delete sessions** — `--sessions` to see them all, `--delete-session <id>` to clean up.

### File generation
- **Generate any file** — `--generate-file "a Dockerfile for nginx"` writes the file to disk.
- **Sandbox-tested** — `--sandbox-test` actually runs Python code in a temp dir with a 10s timeout, AST-checks for dangerous calls (os.system, subprocess, socket, etc.) before running, reports stdout / stderr / exit.
- **Binary files** — image / pdf / etc. outputs also write a `.generator.py` so you can regenerate the file later.
- **Custom output path** — `--generate-output ./Dockerfile`.

### Self-modification
- **Modify its own source** — `--self-modify "add a --foo flag"` lets jarvis edit `jarvis.py` + `gui.py` itself. Requires a clean git tree, opens a `self-modify` side branch, runs tests after the patch, auto-reverts on failure.
- **Save points** — `--self-savepoint label` snapshots the current state; `--self-revert label-or-sha` rolls back.
- **Status** — `--self-status` shows the current branch, save points, and last applied change.

### Offline mode
- **No internet at all** — `--offline` refuses to call any remote API, requires local model endpoints (Ollama, vLLM, LM Studio, llama.cpp server).
- **Disables research** — research needs the web, so it's off in offline mode.

### Godot project-aware writing
- **Auto-detection** — if cwd contains `project.godot` (walks up 4 dirs), jarvis automatically switches the codex into Godot-aware mode.
- **Engine detection** — `config_version=5` → Godot 4.x, `config_version=4` → Godot 3.x.
- **Project context injection** — main scene, autoloads, engine version injected into the codex system prompt.
- **Style rules** — gdscript by default, signals + node lifecycle, `@export` annotations, snake_case + PascalCase, scene file structure.
- **Override** — `--godot` to force on, `--no-godot` to force off.

### Projects store
- **Personal project catalog** at `~/.jarvis/projects/<name>/` — list, switch, scaffold, adopt, import, remove.
- **Scaffold new** — `--project new mygame godot` (creates `project.godot` + `scenes/main.tscn` + `scripts/main.gd`) or `--project new mypkg python` (creates `pyproject.toml` + `src/<pkg>/`).
- **Adopt existing** — `--project add /path/to/existing` records the path (no copy, your files stay put).
- **Import (copy) in** — `--project import /path/to/existing` copies it into the store.
- **Active project** — `--project use mygame` sets it; any direct request uses it as the project context for `--write` outputs.
- **Status / path / open** — `--project status` (full status), `--project path` (prints path), `--project open` (opens in OS file manager).

### Google Drive sync (watch-folder)
- **Zero-OAuth sync** — `jarvis drive set ~/Google\ Drive/jarvis` tells jarvis where the synced folder is; the OS does the upload/download.
- **Push** — `jarvis drive push` copies each project subfolder from `~/.jarvis/projects/` to the drive folder.
- **Pull** — `jarvis drive pull` copies from the drive folder back, but skips anything that already exists locally (no clobbering your manual edits).
- **Status / unset** — `jarvis drive status` shows the path, `jarvis drive unset` forgets it.
- **Multi-cloud** — works with any watch-folder: Google Drive, OneDrive, Dropbox, Syncthing, iCloud.

### Phone companion
- **Local HTTP server** — `jarvis --serve` (or `jarvis --serve 0.0.0.0 --port 8765`) hosts a web UI on the laptop.
- **6-digit pairing** — the laptop prints a 6-digit code; you open `http://<laptop-ip>:8765/` on your phone and type the code. (QR generation was removed; the URL+code path is the canonical flow.)
- **Full REST API** — 26 endpoints under `/api/*` covering status, devices, modes, chat, sessions, generate, sandbox-test, config, account, files, and cloud. See "Phone API" below.
- **Per-device modes** — different phones can have different `persona`, `review`, `tests`, `offline` settings.
- **Phone stays key-less** — the phone sends requests; the laptop forwards them, so the API key never leaves the laptop.

### Cloud account sync (optional)
- **Email + password config sync** across devices — `--cloud-signup`, `--cloud-login`, `--cloud-logout`, `--cloud-status`.
- **Works with any KV backend** — `JARVIS_CLOUD_URL` can point at npoint.io, jsonbin.io, your own relay, etc.
- **PBKDF2-hashed password** — your config is encrypted with a key derived from your password; the KV backend only sees ciphertext.

### Backward compatibility
- **Full rename** — `dual_ai` → `jarvis` across code, CLI, config dir, docs.
- **Auto-migrate** — `~/.dual_ai/` is copied to `~/.jarvis/` on first launch.
- **`DualAIError` alias** — `from jarvis import DualAIError` still works.
- **`DUAL_AI_*` env vars** — still honored; `JARVIS_*` takes precedence when both are set.

### Developer / packaging
- **Two files in the folder** — `jarvis.py` (the whole app: CLI, GUI, REST server, tests, build) + `jarvis.sh` (launcher for the frozen binary) + `README.md`. Nothing else to download.
- **Single-file app** — `jarvis.py` is one Python file (~12k lines, ~480KB) with everything: the phone server, sandbox, deep research, GUI, projects store, Drive sync, the test suite, the PyInstaller build helper, and the portable-bundle builder.
- **Run tests** — `python3 jarvis.py --test` (or `python3 jarvis.py --test 2>&1 | tail -5` for just the summary).
- **Build the .exe** — `python3 jarvis.py --build` writes a temporary `jarvis.spec`, invokes PyInstaller, falls back to cx_Freeze if PyInstaller fails. No separate spec or build script to maintain.
- **Build a portable archive** — `python3 jarvis.py --build-portable` produces `jarvis-portable.tar.gz` (~22MB) that you can extract on any machine (no Python required). Includes a `jarvis.sh` launcher.
- **Python 3.6+** — no walrus, no match/case, no PEP 604 unions. AST-verified on 3.6 grammar.
- **PyInstaller / cx_Freeze** — produces a standalone binary (~4-18MB depending on tool).
- **Test suite** — 112 tests, all green, covering sessions, research, pairing, cloud, QR removal, server routes, Godot detection, projects scaffolding, drive push/pull, env shim, legacy migration, and the new REST routes for projects/drive.

---

## Phone API (REST endpoints)

All endpoints require an `X-Device-Token` header (the `device_id` returned by `POST /api/pair`), except `/api/pair` itself.

| Method | Path | Purpose |
|---|---|---|
| `GET`  | `/api/status` | server info, active session, device count |
| `POST` | `/api/pair` | `{code, name, kind}` → `{device_id, ...}` |
| `GET`  | `/api/devices` | list paired devices |
| `DELETE` | `/api/devices` | `{id}` → unpair |
| `GET`  | `/api/modes` | current shared + per-device modes |
| `POST` | `/api/modes` | `{patch: {...}}` → update modes |
| `POST` | `/api/chat` | `{text}` → run a one-shot request |
| `GET`  | `/api/sessions` | list research sessions |
| `POST` | `/api/sessions` | start a new session or report |
| `GET`  | `/api/sessions/<id>` | session detail |
| `POST` | `/api/sessions/<id>/ask` | `{question}` → answer |
| `POST` | `/api/sessions/<id>/pause` | pause worker |
| `POST` | `/api/sessions/<id>/resume` | resume worker |
| `POST` | `/api/sessions/<id>/report` | force final report now |
| `POST` | `/api/generate` | `{request}` → generate a file |
| `POST` | `/api/sandbox-test` | `{code}` → sandbox result |
| `GET`  | `/api/qr` | `{url, code, note}` (URL+code flow, QR removed) |
| `GET`  | `/api/config` | current (non-secret) config |
| `POST` | `/api/config` | `{patch: {...}}` → update config |
| `POST` | `/api/account/signup` | `{email, password, config}` → register |
| `POST` | `/api/account/login` | `{email, password}` → pull config |
| `POST` | `/api/account/logout` | clear local cloud session |
| `GET`  | `/api/account/status` | `{signed_in, email}` |
| `GET`  | `/api/files` | list generated files |
| `GET`  | `/api/files/<path>` | download a generated file |
| `GET`  | `/api/cloud/code` | new cloud pairing code |
| `GET`  | `/api/cloud/info` | cloud backend info |
| `GET`  | `/api/projects` | list projects + active |
| `POST` | `/api/projects` | `{action, name, [path], [kind]}` → projects CRUD (list/active/path/use/new/add/import/remove/open) |
| `GET`  | `/api/projects/active` | current active project name |
| `POST` | `/api/projects/active` | `{name}` → set active |
| `GET`  | `/api/drive` | drive status (configured folder, last sync) |
| `POST` | `/api/drive` | `{action, [folder]}` → set/unset/push/pull |

---

## First run: the startup menu

The very first time you launch `jarvis`, you'll see a startup menu asking which tier you want:

```
============================================================
 jarvis
 Sonnet 5 + GPT Codex 5.3  (or whichever models you pick)
============================================================

  1) Free tier
       Best for casual use. Auto-picks the best free models,
       or you can pick from a list of 8 free models.

  2) Paid tier
       Best quality. Uses Claude 3.5 Sonnet for everything
       (or pick from 6 paid models). Typical cost: $0.05-1.50/project.

  3) Custom
       Provide the API URL, key, and model name(s) yourself.
       Use this for direct OpenAI / Anthropic / Ollama / etc.

  q) quit
```

After you pick a tier and enter your key, the tool saves everything to `~/.jarvis/config.json` and drops you into the main UI. **All subsequent launches skip this menu entirely.**

To re-run the startup menu later: `jarvis --reset` (terminal) or menu option 6 in the interactive menu.

---

## API keys — what you need (free tier available)

The default config is set up for **OpenRouter** — one account, one key, many models, with a generous free tier. You can also use OpenAI, Anthropic, Google, or any other OpenAI-compatible API.

**To get a free key:**

1. Go to [openrouter.ai](https://openrouter.ai) and sign up (Google or email)
2. Click your avatar → **Keys** → **Create Key**
3. Copy the key (starts with `sk-or-v1-...`)

That's it. One key, two models.

---

## Free models you can pick from (built into the tool)

On first run (and via the "Change models" option in the menu), you get a dropdown of these free OpenRouter models, each with a short "best for" hint:

| # | Model | Best for |
|---|---|---|
| 1 | **Qwen 2.5 Coder 32B** | code generation, refactoring, tests |
| 2 | **Llama 3.3 70B** | general reasoning, planning, analysis |
| 3 | **DeepSeek V3** | long-form reasoning, planning, structured output |
| 4 | **DeepSeek Coder** *(when free)* | code generation, debugging |
| 5 | **Gemini 2.0 Flash** | fast responses, multimodal, general use |
| 6 | **Mistral Small 3.2 24B** | balanced reasoning + code, multilingual |
| 7 | **Qwen 2.5 72B** | general reasoning, code review |
| 8 | **Hermes 3 405B** | complex reasoning, large context (slower) |

**Recommended combo to start:**

- **Planner:** Llama 3.3 70B (great at structured JSON output, which our system needs)
- **Coder:** Qwen 2.5 Coder 32B (purpose-built for code, very strong)

You can also type a custom model id (e.g. `openai/gpt-4o-mini` or `anthropic/claude-3.5-sonnet`) at the picker for paid options.

You can swap to any other model OpenRouter supports with `--set sonnet_model=...` or `--set codex_model=...`. For example, `--set sonnet_model=openai/gpt-4o-mini` or `--set codex_model=anthropic/claude-3.5-sonnet`.

---

## Paid models you can pick from

If you picked "Paid tier" at the startup menu, you get a dropdown of these paid OpenRouter models. **All cost real money;** you only pay for what you use, and typical project cost is $0.05-1.50.

| # | Model | Best for | Cost (per 1M tokens) |
|---|---|---|---|
| 1 | **Claude 3.5 Sonnet** | best overall quality, strong at code and reasoning | $3 in / $15 out |
| 2 | **GPT-4o** | OpenAI flagship, very strong all-rounder | $2.50 in / $10 out |
| 3 | **GPT-4o mini** | cheap and good; great value | $0.15 in / $0.60 out |
| 4 | **Claude 3.5 Haiku** | fast and cheap, decent quality | $0.80 in / $4 out |
| 5 | **Gemini 2.0 Flash (paid)** | very fast, large context window | $0.10 in / $0.40 out |
| 6 | **Mistral Codestral** | code-specialized | $0.30 in / $0.90 out |

**Recommended paid combo to start:** Claude 3.5 Sonnet for everything (or "Auto-pick the best paid model for everything" in the menu does this for you).

---

## Two ways to use it

You pick — and the choice is remembered.

### 🖥  GUI mode (chat window)

A clean dark chat interface. Type your request at the bottom, watch the plan and code stream in. Looks like a personal-assistant app.

- Persona switcher in the top-right menu
- "Open output folder" button to jump to the generated code
- Output scrolls automatically
- No terminal needed

### ⌨️  Terminal mode (CLI)

Plain text in, plain text out. Pipeable, scriptable, fast.

```
jarvis "build me a CLI todo list app" > plan.txt
```

The first time you launch, you'll see a small chooser window:

```
   jarvis
   Choose how you want to use it

   [ GUI ]   [ Terminal ]

   (your choice is remembered for next time)
```

Click your preference. From then on, that's what opens. Override per-run with `--mode`:

```
jarvis --mode terminal "..."     # always use terminal
jarvis --mode gui "..."          # always use GUI
jarvis --mode ask "..."          # show the chooser every time
jarvis --mode auto "..."         # default: use remembered choice
```

If `tkinter` isn't installed (e.g. some minimal Linux Pythons), the tool automatically falls back to terminal mode. You'll see a one-line note, then the CLI.

---

## Quick start

### If you have the `.exe`

Just run it — double-click or from a terminal. First time it asks for your two API keys (masked input, 30 seconds). Saved to `~/.jarvis/config.json`. Never asked again.

### If you have the source

```bash
pip install requests
python jarvis.py "build me a CLI todo list app"
```

(Tkinter ships with the standard Python install on Windows and macOS, so the GUI works out of the box.)

---

## Build the `.exe`

```bash
pip install requests pyinstaller
python3 jarvis.py --build
# -> dist/jarvis.exe   (~18 MB, self-contained)
```

The `--build` flag writes a temporary `jarvis.spec`, invokes PyInstaller, and cleans up — no separate `build.py` or `jarvis.spec` file needed (the spec is embedded inside `jarvis.py`). Pass `--no-clean` for an incremental build.

If PyInstaller fails (e.g. on Linux systems with a static-only Python that ships without `libpython3.X.so`), `--build` automatically falls back to **cx_Freeze**:

```bash
pip install cx-freeze
python3 jarvis.py --build
# -> dist_exe/jarvis + dist_exe/lib/   (run the jarvis binary)
```

Both options produce a standalone binary that doesn't need Python installed on the target machine.

**Note on sandbox tests:** when you run `--test` inside a frozen binary, 4 of the 112 tests will fail — these are the sandbox tests that spawn a Python subprocess. In a frozen build, `sys.executable` is the frozen binary itself, which can't be re-invoked to run an arbitrary script. Run the full 112 tests from the source form (`python3 jarvis.py --test`) to verify everything end-to-end.

Copy `dist/jarvis.exe` (or `dist_exe/jarvis`) anywhere. Desktop, downloads, USB stick. **No installer, no Python, no PATH to set up.**

### Portable bundle (one-file extract-anywhere)

Want a single archive you can drop on any machine? Use `--build-portable`:

```bash
pip install requests cx-freeze
python3 jarvis.py --build-portable
# -> jarvis-portable.tar.gz   (~22 MB)
# -> jarvis-portable.zip      (Windows-friendly alternative)

# Install on another machine (no Python needed):
tar -xzf jarvis-portable.tar.gz -C ~/jarvis
~/jarvis/jarvis/jarvis.sh --help
```

The portable bundle includes:
- `jarvis` — the standalone binary (4.3 MB)
- `lib/` — frozen Python stdlib (≈ 19 MB)
- `share/` — frozen application data
- `jarvis.sh` — launcher script (handles path discovery)
- `README.txt` — quick-start instructions

After extraction, run `./jarvis/jarvis` (or `./jarvis/jarvis.sh`) and the first-run wizard appears. No Python install required.

---

## Usage

```
jarvis "build me a thing"               # one-shot (terminal mode)
jarvis                                  # launch remembered UI
jarvis --persona jarvis "..."           # JARVIS tone
jarvis --text-only "..." > out.txt      # no decorations
jarvis --with-tests "..."               # also generate pytest tests
jarvis --write --output ./my_proj "..." # save generated files
jarvis --json "..."                     # print result as JSON
jarvis --mode gui                       # force GUI launch
jarvis --mode terminal                  # force terminal launch
jarvis --reset                          # wipe config + re-run setup
jarvis --show-config                    # show saved config (keys masked)
jarvis --set persona=jarvis             # change a setting
jarvis --help                           # all options
```

### Interactive terminal menu

```
==== jarvis ====
1) Design a system
2) Generate a full project (plan + code + review)
3) Review / explain existing code
4) Change persona (current: engineer)
5) Exit
```

### GUI mode

The GUI is a chat window. Type at the bottom, press Enter. The plan and code appear as messages, with code shown in a darker monospace block for readability. There's a persona menu in the top-right.

### Personas

| Flag | Tone |
|---|---|
| `--persona engineer` (default) | Terse, technical, no fluff |
| `--persona jarvis` | Calm, polite, slightly formal — the AI works "as JARVIS" |

You can also change the persona with `jarvis --set persona=jarvis` (remembered) or pick from the menu.

### Text-only mode

`--text-only` strips out all decorative formatting — section bars, indented boxes, etc. Output is plain text. Useful for:

- `jarvis --text-only "..." > build.txt` — save to a file
- CI / scripted use
- Screen readers

(Terminal mode only. GUI is always pretty.)

---

## Where do the API keys go?

Saved to `~/.jarvis/config.json` (your home directory). The file is:

- Owner-readable only (`chmod 600` on POSIX, default ACL on Windows)
- Never sent anywhere except the official model APIs
- Easy to change: `jarvis --reset` brings back the first-run wizard
- Easy to view: `jarvis --show-config` (keys are masked in the output)

Example config file:

```json
{
  "sonnet_api_key":  "sk-or-v1-...",
  "codex_api_key":   "sk-or-v1-...",
  "sonnet_api_url":  "https://openrouter.ai/api/v1/chat/completions",
  "codex_api_url":   "https://openrouter.ai/api/v1/chat/completions",
  "sonnet_model":    "meta-llama/llama-3.3-70b-instruct:free",
  "codex_model":     "qwen/qwen-2.5-coder-32b-instruct:free",
  "persona":         "engineer",
  "enable_review":   true,
  "enable_tests":    false,
  "ui_mode":         "gui",
  "timeout":         120,
  "retries":         3,
  "backoff":         1.5
}
```

(You only need one OpenRouter key for both — same key in both `sonnet_api_key` and `codex_api_key`.)

The `ui_mode` field remembers your GUI/terminal preference.

Change any of these with `--set KEY=VALUE`:

```bash
jarvis --set persona=jarvis
jarvis --set timeout=180
jarvis --set sonnet_model=openai/gpt-4o-mini          # use GPT-4o for planning
jarvis --set codex_model=anthropic/claude-3.5-sonnet  # use Claude for code
jarvis --set ui_mode=terminal                          # forget the GUI
```

---

## Using other providers

OpenRouter is the default but the tool works with any OpenAI-compatible chat-completions API. To use a different provider, just set the URLs and model names:

```bash
# Direct OpenAI
jarvis --set sonnet_api_url=https://api.openai.com/v1/chat/completions
jarvis --set codex_api_url=https://api.openai.com/v1/chat/completions
jarvis --set sonnet_model=gpt-4o-mini
jarvis --set codex_model=gpt-4o
jarvis --set sonnet_api_key=sk-...      # your OpenAI key
jarvis --set codex_api_key=sk-...       # same key, different model

# Direct Anthropic (via their OpenAI-compatible proxy or any local proxy)
jarvis --set sonnet_api_url=https://your-anthropic-proxy/v1/chat/completions
jarvis --set sonnet_model=claude-3-5-sonnet-latest
jarvis --set sonnet_api_key=sk-ant-...

# Local Ollama / vLLM / LM Studio
jarvis --set sonnet_api_url=http://localhost:11434/v1/chat/completions
jarvis --set codex_api_url=http://localhost:11434/v1/chat/completions
jarvis --set sonnet_model=llama3.1
jarvis --set codex_model=qwen2.5-coder
jarvis --set sonnet_api_key=ollama        # any non-empty string works
```

If the URL isn't `openrouter.ai`, the tool skips the OpenRouter-specific headers automatically, so other providers won't reject the request.

---

## What `--write` produces

```
<output_dir>/
├── plan.json
└── modules/
    ├── ModuleA/
    │   ├── module.py        # the implementation
    │   ├── REVIEW.md        # the Sonnet 5 review
    │   └── test_module.py   # the pytest suite (if --with-tests)
    └── ModuleB/
        └── ...
```

Default location: `~/.jarvis/output/`. In the GUI, click the persona menu → "Open output folder" to jump there.

---

## Deep research (optional)

By default, jarvis uses the model's training-data knowledge. For tasks that involve current APIs, library versions, or anything that's changed recently, you can enable **deep research** — the tool will gather live web context *before* the planner runs, so the plan reflects the latest docs and best practices.

```bash
# Enable research for one request
jarvis --research "Build a FastAPI app that uses the Stripe API for payments"

# Add specific URLs you want researched
jarvis --research --research-url https://docs.stripe.com/api --research-url https://fastapi.tiangolo.com "..."

# Add search terms
jarvis --research --research-term "Stripe webhook signature 2024" "..."

# Or set as default (persisted to config)
jarvis --set enable_research=true
```

When research is on, the tool:

1. **Extracts URLs** from your request and fetches them in parallel.
2. **Extracts library/API names** (FastAPI, Stripe, PostgreSQL, React, etc. — 60+ in the curated list, plus any capitalized proper nouns) and runs a quick DuckDuckGo search for each.
3. **Sends `web_search_options`** to the model in the request body (so models that support native web search can use it too).
4. **Injects a "current context"** section into the planner's prompt.

You'll see `Researched N sources before planning.` printed when it's done.

Toggles in the menus:
- Terminal: menu option **6) Toggle research**
- GUI: persona menu → **"Research (fetch URLs + web search)"** checkbox
- GUI: persona menu → **"Add research URL..."** to add a URL for the next request

Research is **off by default** (no startup penalty; faster). Turn it on when you need up-to-date info.

## Deep research sessions (multi-hour, resumable)

The `--research` flag above is a quick pre-plan web scrape - useful
but quick. For real research that takes **hours** and where you want
to **ask questions along the way** and **come back later**, use
**deep research sessions**.

```bash
# Start a 5-hour research session on a topic
jarvis --deep-research "Rust async runtimes 2024 comparison"

# Customize the time budget (30 min, 1 hour, 2h30m, etc.)
jarvis --deep-research "quantum error correction" --max-time 30m
jarvis --deep-research "kubernetes networking" --max-time 1h
jarvis --deep-research "GDPR compliance for SaaS" --max-time 5h

# Cap by iteration count instead of (or in addition to) time
jarvis --deep-research "small topic" --max-iterations 10

# One-shot deep report: do one big batch + write a report, no follow-ups
jarvis --deep-report "WebGPU state of the art 2024"

# List all your past sessions
jarvis --sessions

# Resume a session from hours or days ago
jarvis --resume kubernetes-networking-3a8f2c

# Delete a session
jarvis --delete-session kubernetes-networking-3a8f2c
```

What you get:

- **A research plan** generated up front: 8-20 specific search
  questions, ordered from foundational to specific.
- **An iterative research loop** that runs in the background,
  picking a few open questions per round, doing DuckDuckGo searches
  in parallel, fetching the top results, and updating a running
  **notebook of findings** (`notes.md`). Each round calls the model
  once to merge new findings into the notebook.
- **Interactive Q&A** at any time. While the loop is running, type
  a question; the loop pauses, the AI answers using the running
  notes, then asks if you want to resume.
- **Persistent on disk**. Sessions live in
  `~/.jarvis/sessions/<id>/` with `session.json`, `notes.md`,
  `sources.json`, `questions.json`, `plan.md`, and `report.md`. Come
  back hours or days later and pick up where you left off.
- **Budgets that you control**. Time budget (`--max-time`, default
  5 hours), iteration cap (`--max-iterations`, default 50), and a
  hard `Ctrl-C` save - the session always saves cleanly even if you
  kill the process.
- **A final synthesized report** (`report.md`) is written
  automatically when the session completes (out of questions, or
  budget reached, or you stop it gracefully).

**How to use the interactive session** (terminal mode):

```
[Q&A/quantum-error-correction-7f3a2b] > what are the leading approaches?
  (the AI answers using the running notes)
[Q&A/...] > status
  iter 3  12m 34s / 5h budget  sources: 11  open-Q: 4
[Q&A/...] > q who coined the term "magic state distillation"?
  added to open questions: who coined the term "magic state distillation"?
[Q&A/...] > report
  writing report... report written to /home/you/.jarvis/sessions/.../report.md
[Q&A/...] > resume
  (the research loop continues, picking up the new question)
[Q&A/...] > quit
  (the session is saved; resume it later with --resume)
```

**GUI mode**: from the chat window's persona menu, pick **"Deep
research session..."**. A separate window opens where you can
start, resume, and ask questions. The main chat window stays usable
while research runs in the background.

**Toggles**:
- Terminal: menu option **7) Deep research session** in the main menu.
- GUI: persona menu -> **"Deep research session..."**

**Programmatic use**: `jarvis.run_deep_research_session(topic, cfg,
max_seconds=5*3600, max_iterations=50)`. Returns the
`DeepResearchSession` object, which is also saved to disk.

**Tunables** (env vars or constant overrides):
- `JARVIS_SESSION_NOTES_CHARS` - how many notes chars to send the
  model when answering a question. Default 6000 (~1500 tokens).
- `JARVIS_SESSION_SOURCE_KEEP` - how much of each fetched page to
  keep on disk. Default 2000 chars.
- `JARVIS_SESSION_COOLDOWN` - sleep between research iterations
  (politeness to DDG). Default 2 seconds.

## Offline mode (no internet required)

If you want to run `jarvis` without making any calls to the public internet — for privacy, cost, or because you're offline — point it at a local model endpoint and turn on offline mode.

```bash
# One-time: configure a local model endpoint
# (Ollama, vLLM, LM Studio, llama.cpp's server, etc.)
jarvis --set sonnet_api_url=http://localhost:11434/v1/chat/completions
jarvis --set codex_api_url=http://localhost:11434/v1/chat/completions
jarvis --set sonnet_model=llama3.1
jarvis --set codex_model=qwen2.5-coder

# Turn on offline mode
jarvis --set offline=true

# From now on, every invocation refuses to call any remote URL
jarvis
```

When offline mode is on:
- `jarvis` checks that both `sonnet_api_url` and `codex_api_url` point at local endpoints (localhost, 127.0.0.1, RFC1918 private IPs, or `*.local`). If either is remote, the tool refuses to start.
- A banner is printed on every launch telling you you're in offline mode.
- `--research`, deep research, and DuckDuckGo web searches are all disabled (they need the internet). You get a clear warning if you try.
- Everything else (planning, code generation, file generation, sandbox tests, self-modification) works against the local model.

To go back to a remote provider:

```bash
jarvis --set offline=false
```

## File generation (any file type, sandbox-tested)

Want a Dockerfile? A SQL migration? A PNG logo? A CI YAML? The `--generate-file` flag produces a single file of any type, and can optionally run it in a sandbox to verify it works.

```bash
# Generate a text file
jarvis --generate-file "a Dockerfile for nginx with multi-stage build" \
  --generate-output ./Dockerfile

# Generate and test a Python script
jarvis --generate-file "a Python script that prints Fibonacci numbers" \
  --generate-output ./fib.py --sandbox-test

# Generate a binary file (image, pdf, etc.) -- also writes a .generator.py
# so you can regenerate the file later
jarvis --generate-file "a 64x64 transparent PNG logo" \
  --generate-output ./logo.png
```

How it works:
- For text files (code, config, scripts, markup, data), the model outputs a fenced code block; the filename is guessed from the language tag and any `# filename.ext` comment on the first line.
- For binary files (image, PDF, zip, font, etc.), the model outputs a JSON object with the base64-encoded content plus a `generator_script` (a Python snippet that produces the same file, so you can regenerate it later).
- With `--sandbox-test`, generated Python is actually run in the sandbox. The output shows stdout, stderr, exit code, and runtime. A safety check (AST analysis) blocks dangerous operations (os.system, subprocess, network calls, etc.) before anything runs.

The sandbox itself:
- **AST safety check** rejects `import os`, `import subprocess`, `import requests`, `import socket`, `import asyncio`, `exec()`, `eval()`, and a long list of other dangerous patterns before anything runs.
- Runs the code in a fresh `tempfile.mkdtemp` directory, with a 10-second default timeout.
- Scrubs the environment (no API keys leaking in, `HOME` is the temp dir, `TMPDIR` is the temp dir).
- On Linux with permissions, uses `unshare --net` to disable network access. Falls back gracefully if unshare isn't available.

## Self-modification (jarvis rewriting its own code)

The most dangerous feature, and therefore the most heavily guarded. With `enable_self_modify=true`, you can ask `jarvis` to modify its own source code. It does so safely using Git snapshots and the test suite as a safety net.

**First, opt in (one-time):**

```bash
jarvis --set enable_self_modify=true
```

**Then ask for a change:**

```bash
# Make jarvis add a new --foo flag
jarvis --self-modify "add a --foo flag that does X"

# Make jarvis fix a bug
jarvis --self-modify "fix the bug where Y returns the wrong thing"
```

**The flow (all automatic):**
1. Pre-flight: `enable_self_modify` is on, cwd is a git repo, working tree is clean. If any of these fail, the command exits with a clear error.
2. Snapshot: switch to a side branch called `self-modify`, reset it to current main-branch HEAD, make an empty commit labeled "before: <your request>".
3. AI proposes a unified-diff patch touching only `jarvis.py` and/or `gui.py`.
4. Dry run: `git apply --check` validates the patch would apply cleanly. If not, abort.
5. Apply: `git apply` for real.
6. Test: run the full test suite (`test_deep_research.py`, 59 tests). If any test fails, `git checkout` + `git clean` to revert and report.
7. Commit: if tests pass, commit the change on the `self-modify` side branch with the request as the message.
8. Report: print the snapshot hash, the new commit hash, and the test result.

**Revert to a previous save point:**

```bash
# Save the current state explicitly
jarvis --self-savepoint "before-experiment"

# Try something risky
jarvis --self-modify "rewrite the planner in 10 lines"

# Tests fail, jarvis auto-reverts, you see: "tests failed after patch; reverted"
# OR you decide you don't like it anyway:
jarvis --self-revert before-experiment

# Roll back to before the experiment
git checkout arena/019f9bc1-jarvis
```

The save point system uses Git's commit history: every save point is a labeled commit on the `self-modify` branch. `--self-revert` accepts a commit hash, a branch name, or a substring of a commit message (e.g. "before-experiment"). `--self-status` shows the current state and all save points.

`--self-status` is also useful as a read-only command to see what state the tool is in.

## Phone companion (control jarvis from your phone)

You can run `jarvis` on a laptop/desktop and control it from your phone over the local WiFi. Same code, same config, same conversation history, toggleable modes — just a browser tab on the phone.

### Start the server (host = laptop)

```bash
# On the laptop: start the web server. Prints a 6-digit pairing code.
jarvis --serve

# Optional: bind a specific host/port
jarvis --serve 0.0.0.0 --port 8765
```

The server prints something like:

```
============================================================
 jarvis phone server
============================================================
 Listening on:
   http://0.0.0.0:8765/
   http://192.168.1.42:8765/   <-- try this on your phone
 Pairing code: 482917
 (code expires in 10 min)
```

### Pair from the phone

1. Make sure your phone is on the same WiFi as the laptop.
2. Open `http://192.168.1.42:8765/` (or whatever the server printed) in your phone's browser.
3. A pairing page appears. Type the 6-digit code and your name (e.g. "Pixel 9"). Tap **Pair**.

After pairing, the phone shows the full UI: **Chat**, **Sessions**, **Files**, **Modes**, and **Account** tabs.

### What the phone can do

- **Chat**: ask jarvis anything. If a deep research session is active, your question is automatically answered from the running notes.
- **Sessions**: list all deep research sessions, start a new long-running session, start a one-shot report, or open a session to view its plan/notes/report and ask questions.
- **Files**: see all generated files; download them; generate a new file from a text description.
- **Modes**: toggle any of these from your phone and they apply across **all** paired devices:
  - Web research
  - Code review
  - Auto-tests
  - Offline mode
  - Sandbox testing
  - Persona (`engineer` / `jarvis`)
- **Account**: optional email+password cloud sync (see below).

Each device can have its own per-device modes (so the phone can have sandbox-testing on while the laptop has it off), and there's a shared set that applies to all.

### Pairing commands (run on the host)

```bash
jarvis --pair                    # print a fresh 6-digit code + the URL
jarvis --list-devices            # show all paired devices
jarvis --unpair <device_id>      # remove a paired device
```

Pairing data lives in `~/.jarvis/pairing.json`. Devices that don't ping for a while are still listed — remove them with `--unpair` when you're done.

### Optional cloud sync (cross-device config transfer)

If you want to **move your settings to a brand new device** (not just on the local WiFi), enable cloud sync. Your config — including the API key, encrypted with a password you set — is stored in a small key-value store. You sign in on the new device and pull your config down.

This is **opt-in**. Pairing works fine without it.

**Setup** (one-time, on the host):

```bash
# 1. Set the cloud backend URL. Use a free public KV service or
#    run your own relay (any HTTP endpoint that supports GET/PUT
#    with ?key=... is fine).
export JARVIS_CLOUD_URL="https://api.npoint.io/YOUR_BIN_ID/"
#    or:    set JARVIS_CLOUD_URL=https://api.npoint.io/YOUR_BIN_ID/
#    (jsonbin.io / npoint.io / postman-echo / etc. all work)

# 2. Sign up
jarvis --cloud-signup me@example.com
#    (prompts for password twice, must be 6+ chars)

# 3. On a new device, sign in:
jarvis --cloud-login me@example.com
#    (merges the cloud config into your local config)
```

The password is **never** sent to the server. The API key is encrypted client-side (PBKDF2-HMAC-SHA256, 200k iterations, per-account salt + HMAC-verified ciphertext). Anyone with your password can decrypt it; without the password, the data is opaque.

If you don't set `JARVIS_CLOUD_URL`, the cloud commands are disabled and the phone falls back to local pairing. Your settings stay on the laptop; the phone just reaches it over WiFi.

### Security note

The phone UI is meant for **your own devices on your own WiFi**. Anyone on the same network can reach the server while it's running. To stop the server, press **Ctrl-C** in the terminal where you started it.

The phone never sees your API key directly (it sends requests that the laptop forwards), so the phone doesn't need its own key to control jarvis.

## Python compatibility

Targets **Python 3.6+**. Verified by parsing with the 3.6 grammar. No walrus, no match/case, no modern type-union syntax. Required third-party dep: `requests`. Optional (for the GUI): `tkinter`, which ships with every standard Python install on Windows and macOS.

Works on:
- Python 3.6, 3.7, 3.8, 3.9, 3.10, 3.11, 3.12, 3.13
- Windows, macOS, Linux
- Inside a PyInstaller bundle, inside a venv, or system Python

---

## Files

```
jarvis/
├── jarvis.py       # the whole app (CLI, GUI, REST server, tests, build)
├── jarvis.sh       # launcher for the frozen binary
└── README.md       # this file
```

Two source files plus a launcher. The entire app — CLI mode, GUI mode, phone REST server, deep research, Godot integration, projects store, Drive sync, the embedded test suite, and the PyInstaller/cx_Freeze build helper — lives in a single `jarvis.py`. No more separate `gui.py`, `test_deep_research.py`, `build.py`, or `jarvis.spec`. Run tests with `python3 jarvis.py --test`, build an `.exe` with `python3 jarvis.py --build`, or get a portable tarball with `python3 jarvis.py --build-portable`.

---

## Godot project-aware writing

If your current working directory contains a `project.godot` file (jarvis walks up to 4 parent directories to find one), jarvis automatically switches the codex (code-generation) model into **Godot-aware mode**:

- gdscript style, not Python
- Uses Godot signals, lifecycle callbacks (`_ready`, `_process`, `_physics_process`), and `@export` annotations
- Respects your `run/main_scene` and any `[autoload]`s
- Knows the engine version (`Godot 4.x` for `config_version=5`, `Godot 3.x` for `config_version=4`)

```bash
# run from inside a Godot project — auto-detected
cd ~/projects/my-game
jarvis "add a health bar that depletes when the player gets hit"

# force it on
jarvis --godot "..."

# force it off (skip the Godot prompt even in a Godot project)
jarvis --no-godot "..."
```

The Godot system prompt is prepended to the regular codex prompt and clearly marked with `[PROJECT CONTEXT]` so the model knows the rules.

---

## Projects store

jarvis keeps a personal **projects store** at `~/.jarvis/projects/<name>/`. You can scaffold a fresh project (Godot or Python), adopt an existing folder, import (copy) one in, switch between them, and back them up to a cloud-synced folder.

```bash
# list everything in the store
jarvis --project list

# scaffold a new Godot game (creates project.godot + scenes/main.tscn + scripts/main.gd)
jarvis --project new mygame godot

# scaffold a new Python package (pyproject.toml + src/<pkg>/__init__.py)
jarvis --project new mypkg python

# adopt an existing folder (records the path, doesn't copy anything)
jarvis --project add ~/code/old-game mygame

# import (copy) a folder into the store
jarvis --project import ~/code/old-game mygame

# switch the active project
jarvis --project use mygame

# show which one is active
jarvis --project active

# show its path
jarvis --project path

# open the active project in your file manager
jarvis --project open

# show full status (active + all projects)
jarvis --project status

# remove from store (default keeps the files; pass --delete-files to nuke)
jarvis --project remove mypkg
```

The **active project** is the one that any direct `jarvis "..."` request will use as the project context for `--write` outputs. Switching is a one-liner: `jarvis --project use <name>`.

---

## Google Drive sync (watch-folder)

If you point your Google Drive desktop app at a folder (or use OneDrive, Dropbox, Syncthing, etc.), jarvis can sync its entire projects store to that folder as a **dumb copy**: each project becomes a subfolder, and pulling back is a no-clobber copy that skips anything that already exists locally (so manual edits on the other machine aren't overwritten).

```bash
# one-time: tell jarvis where the synced folder is
jarvis drive set ~/Google\ Drive/jarvis-projects

# push local store -> drive
jarvis drive push

# pull drive -> local store (no clobber)
jarvis drive pull

# show what's configured
jarvis drive status

# stop syncing (just forgets the path; doesn't delete anything)
jarvis drive unset
```

This is intentionally simple — no OAuth, no API calls, no quota limits. You give jarvis a folder path and the OS takes care of the actual upload/download. If you want to extend this with a real Google Drive API integration later, the `_drive_*` functions in `jarvis.py` are the place to start.

---

## Backward compatibility

If you have an existing `~/.dual_ai/` config directory from a previous version, jarvis **automatically migrates it** to `~/.jarvis/` on first launch — including `config.json` and the `pairing/`, `sessions/`, `output/` subdirectories. The old name keeps working too:

- `DualAIError` is a class alias for `JarvisError` — old imports still work.
- All `DUAL_AI_*` environment variables are still honored; `JARVIS_*` takes precedence if both are set.
- Existing scripts and pairings keep working unchanged.

If for some reason you want to opt out of the migration, set `JARVIS_SKIP_LEGACY_MIGRATE=1` in the environment before first launch.

---

## Troubleshooting

- **`requests` not installed** — run `pip install requests` (the error tells you this).
- **`Sonnet 5 API key is missing`** — run `jarvis --reset` to re-enter your keys.
- **Wrong keys / 401 errors** — same: `jarvis --reset`.
- **GUI never appears, just goes to terminal** — your Python is missing `tkinter`. On most platforms this is included by default; on some minimal Linux installs you need `sudo apt install python3-tk`.
- **Want to switch back to terminal after using the GUI** — run `jarvis --set ui_mode=terminal`, or just use `jarvis --mode terminal` once.
- **Antivirus flags `jarvis.exe`** — known false positive with PyInstaller's bootloader. The source is open and reproducible.
- **Output is wrapped weirdly in PowerShell** — try `jarvis --text-only "..."` for clean output, or set `$PSDefaultParameterValues['Out-File:Encoding'] = 'utf8'`.

---

## License

As-is, no warranty. You are responsible for what you do with the code it generates.
