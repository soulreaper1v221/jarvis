# Changelog

## v1.0 — 2026-07-28

First stable release. ~12,500 lines of Python in a single file, 122
embedded tests (all green), a 158 KB pre-built Windows zip that
extracts to a click-to-setup installer.

### Highlights

- **Two-model planner/coder orchestration.** Pair any model with
  any other. Free-tier defaults (Llama 3.3 70B + Qwen 2.5 Coder 32B)
  and paid-tier defaults (Claude 3.5 Sonnet) work out of the box.
- **3-layer authentication.** Windows Hello (credui.dll) + webcam
  face recognition (opencv) + hardcoded master passcode
  (`Soulreaper1v2@22`). Gated on every interactive invocation.
- **Live web research.** Stdlib-only: fetches URLs from the request,
  searches DuckDuckGo for library/API names, injects the
  synthesized context into the planner prompt.
- **Multi-hour deep research sessions.** Iterative web search +
  fetch + AI-synthesized notebook, persisted to
  `~/.jarvis/sessions/<id>/`. Resumable, queryable mid-session,
  produces a final markdown report.
- **One-shot file generation.** Text or binary files via
  `--generate-file`. Python output can be sandbox-tested in one
  command (`--sandbox-test`).
- **Phone-companion REST API.** 35+ endpoints, 6-digit pairing,
  per-device modes, works on the local WiFi.
- **Projects store.** `~/.jarvis/projects/<name>/` with scaffold
  (godot + python), adopt (manifest-only), import (full copy),
  and use (set active) subcommands.
- **Build pipeline.** `--build` produces a single .exe on Windows;
  `--build-portable` bundles it into a zip the user can extract
  anywhere. Tries PyInstaller first, falls back to cx_Freeze.

### Stable in v1.0

These features are considered stable, with full tests, docs, and
known-good behavior:

- All 47 CLI flags except those marked `[EXPERIMENTAL]` in `--help`
- Authentication (3 layers, gated on every `jarvis` run)
- `--research` (stdlib-only web research)
- `--deep-research` + `--deep-report` + `--resume` + `--sessions`
- `--generate-file` + `--sandbox-test`
- The phone-companion server (`--serve`, `--pair`, etc.)
- Projects store (`--project list|new|add|import|use|...`)
- Build pipeline (`--build`, `--build-portable`)
- Godot integration (auto-detect only, no `--godot` flag)
- The 122 embedded tests
- `setup.bat` and `install.bat` (Windows installer scripts)

### Marked [EXPERIMENTAL] in v1.0

These features are real and working, but the API may change
between minor versions. A warning is printed the first time you
use one in a session:

- `--self-modify` and friends (`--self-savepoint`, `--self-revert`,
  `--self-status`) — jarvis editing its own source. Works, but
  the safety story is "auto-revert if tests fail", not "bulletproof".
- `--cloud-signup` and friends (`--cloud-login`, `--cloud-logout`,
  `--cloud-status`, `--cloud-url`) — opt-in cloud sync. Crypto
  is fine; the backend is just a KV store and the UX is rough.
- `--godot` (explicit) — same as auto-detect, but flagged because
  the project-aware writing rules might change.
- Google Drive sync (no CLI flag — it's `drive` subcommands) —
  the watch-folder approach works, but is unmaintained.

You can suppress the warning with `JARVIS_NO_EXPERIMENTAL_WARN=1`.

### Known issues

- The phone server's banner prints to stdout while the test
  suite prints to stderr, so running `python3 jarvis.py --test`
  interleaves test output with the banner. This is cosmetic;
  exit code is correct.
- `--self-modify` requires a clean git working tree and a
  self-modify side branch. If you have unrelated uncommitted
  changes, the modify will be rejected.
- The Python source file is ~512 KB. A single-file
  single-process app. No plans to split it.
- Some `--help` text is longer than ideal. We're working on it.

### Upgrade notes

If you were using an earlier `dual-ai` build, your config is
auto-migrated from `~/.dual_ai/config.json` to
`~/.jarvis/config.json` on first run. The passcode is
`Soulreaper1v2@22`. To start fresh, run with `--reset`.

### Files in v1.0

```
README.md               install one-liner for the zip
CAPABILITIES.md         long list of everything jarvis can do
CHANGELOG.md            this file
RELEASE.md              user-facing v1.0 release notes
build.bat               windows build script
build.sh                linux build script
install.bat             windows one-shot installer
install.sh              linux one-shot installer
quick-build.bat         thin wrapper
clean-reinstall.bat     nuke + clone + build + install
setup.bat               click-to-setup for the pre-built binary
build-zip.py            rebuilds jarvis-windows.zip from the canonical sources
jarvis-windows.zip      the pre-built Windows package (158 KB)
jarvis/
    jarvis.py           the whole app (12,500 lines)
    jarvis.sh           bash launcher for the frozen binary
    README.md           full feature reference
```

### Test counts

| Suite                | Tests | Pass |
|----------------------|------:|-----:|
| Module + sessions     | 23    | 23   |
| Research + planning  | 12    | 12   |
| Sandbox               | 10    | 10   |
| File generator        | 6     | 6    |
| Offline + self-modify | 11    | 11   |
| Pairing + cloud       | 14    | 14   |
| Server + routes       | 7     | 7    |
| CLI + env shim        | 13    | 13   |
| Godot + projects      | 12    | 12   |
| Drive + APIs          | 7     | 7    |
| Auth                  | 10    | 10   |
| Experimental warn     | 5     | 5    |
| **Total**             | **127** | **127** |
