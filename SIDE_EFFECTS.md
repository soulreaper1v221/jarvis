# jarvis v1.0 — side effects audit

A per-flag rundown of what each command actually does, so you can
run jarvis without accidentally nuking state.

Legend:
- **none** — read-only, prints output, exits
- **state** — modifies `~/.jarvis/` (config, sessions, projects)
- **git** — modifies a git repo (init, branch, commit, revert)
- **network** — makes HTTP calls to a model API or web
- **file** — writes to cwd or `--output` directory
- **os** — modifies user-level state (PATH, env, system files)

All commands short-circuit before doing anything if `--help` or
`--version`. `--show-config` is read-only. `--auth-test`,
`--auth-setup`, `--self-status`, `--list-devices`, `--sessions`,
`--cloud-status` are all **none** — they only print.

---

## safe to run anytime (no side effects beyond what they say)

| Flag                      | What it does                                |
|---------------------------|---------------------------------------------|
| `--help`                  | prints help, exits                           |
| `--show-config`           | prints masked config, exits                  |
| `--auth-test`             | tests 3 auth layers, prints report           |
| `--auth-setup`            | interactive wizard, registers face from cam  |
| `--sessions`              | lists deep research sessions                |
| `--self-status`           | prints self-modify state                     |
| `--list-devices`          | lists paired phone devices                   |
| `--cloud-status`          | prints cloud backend status                  |
| `--cloud-url URL`         | **prints** the env var to set; doesn't change anything itself |
| `--cloud-logout`          | **no-op** in v1.0 (prints a message, clears nothing) |
| `--qr`                    | deprecated; prints a deprecation message    |

These are all **read-only** and safe. Run them whenever.

---

## destructive (asks for confirmation or has a clear effect)

| Flag                              | Effect                                                                       |
|-----------------------------------|------------------------------------------------------------------------------|
| `--reset`                         | **deletes `~/.jarvis/config.json`** then runs the first-run wizard. If you say quit at the wizard, your config is wiped. |
| `--delete-session SESSION_ID`     | **deletes `~/.jarvis/sessions/<id>/`** recursively. No prompt, no undo. |
| `--unpair DEVICE_ID`              | **removes a paired device** from `~/.jarvis/pairing.json`. No prompt.       |
| `--self-revert TARGET`            | **resets your git working tree to a past commit** (on the `self-modify` branch). The main branch is untouched, but uncommitted work in your cwd is gone. |
| `--change-passcode`               | verifies your current passcode, then **writes a new passcode to `~/.jarvis/config.json`** as `passcode_override`. The hardcoded fallback in the binary still works too, so a fresh install on a new box can still authenticate. |
| `--change-cloud-password`         | verifies the current cloud password by signing in, then **re-encrypts the stored config with a new salt + new key**. The old password is invalidated as part of the change. |
| `--cloud-signup EMAIL`            | **creates an account** on the cloud backend, encrypted with the password you type. |
| `--cloud-login EMAIL`             | **uploads your local config** to the cloud backend (overwriting remote). Excludes api keys if local ones are set. |
| `--project remove NAME [--delete-files]` | **deletes the project manifest**, and with `--delete-files`, also **deletes the project files from disk** under `~/.jarvis/projects/<name>/`. |

These all do what they say. Nothing accidental. The dangerous one
is `--self-revert` because "revert" sounds soft but it's actually a
hard reset.

---

## state-modifying but reversible

| Flag                          | Effect                                                                       |
|-------------------------------|------------------------------------------------------------------------------|
| `--set KEY=VALUE`             | **modifies `~/.jarvis/config.json`**. Persisted to disk immediately. Reversible by `--set KEY=oldvalue` or by editing the file. |
| `--self-savepoint LABEL`      | **creates an empty commit** on the `self-modify` side branch in your git repo. Doesn't touch main. Reversible with `git branch -D self-modify`. |
| `--self-modify REQUEST`        | **modifies `jarvis.py` itself** in your cwd. Takes a git snapshot first, runs the test suite, auto-reverts if tests fail. If tests pass, commits on `self-modify` branch. |

---

## network calls (no local state change, but talks to the world)

| Flag                          | Network                                                          |
|-------------------------------|------------------------------------------------------------------|
| `--research`                  | fetches URLs in your request + DDG search, all on the open web  |
| `--research-url URL`          | fetches the URL                                                   |
| `--research-term TERM`        | DDG search                                                        |
| `--deep-research TOPIC`       | DDG + URL fetches + model API, may run for `--max-time` (default 5h) |
| `--deep-report TOPIC`         | one-shot DDG + URL fetches + model API, ~minutes                 |
| `--resume SESSION_ID`         | model API for Q&A mid-session                                     |
| `--generate-file REQUEST`     | model API (text or binary generation)                            |
| `--sandbox-test`              | runs the generated Python in a sandbox; no network (sandbox blocks it) |
| `--self-modify REQUEST`        | model API (asks for the patch) + local git ops                   |
| `--cloud-signup/login`        | cloud backend HTTP                                                |
| `--serve`                     | local web server; no external calls                              |

---

## file-writing (creates files in cwd or --output)

| Flag                          | Where it writes                                                 |
|-------------------------------|-----------------------------------------------------------------|
| `jarvis.py --write`           | writes generated code to `~/.jarvis/output/` (or `--output DIR`) |
| `--generate-file ...`         | writes the generated file to `--generate-output` (default `./<filename>`) |
| `--build` / `--build-portable` | writes the frozen binary to `dist/` or `dist_exe/`            |
| `--serve`                     | writes pairing state to `~/.jarvis/pairing.json`                |
| `setup.bat` (Windows)         | writes `jarvis.exe` to `%USERPROFILE%\jarvis-exe\` and adds it to user PATH |

---

## os-level (rare, only with specific flags)

| Flag                          | OS change                                                        |
|-------------------------------|------------------------------------------------------------------|
| `setup.bat` (Windows)         | adds `%USERPROFILE%\jarvis-exe` to the **user PATH** (HKCU\Environment). No admin needed. No system files modified. |
| `--serve` (default 0.0.0.0)   | opens a network port. Not a firewall change, but other devices on your WiFi can connect to it. |
| `--build` (PyInstaller)       | may briefly create `build/` and `dist/` directories in cwd. Cleans them up on success. |

---

## "this might surprise you" — flags that look innocent but do more

These are the ones I'd flag as **"read the doc first"**:

1. **`--reset`** — wipes your API keys + model picks + persona. The
   message says "Config wiped." but the old config is not backed up.
   If you have a working setup, **don't run `--reset`**.

2. **`--self-revert`** — accepts a commit hash, branch name, **or
   label substring**. A partial match on the wrong label can roll
   you back further than you wanted. Always `--self-status` first,
   confirm the target, then `--self-revert`.

3. **`--project remove NAME --delete-files`** — the bare
   `--project remove NAME` only deletes the manifest, but
   `--delete-files` **recursively deletes the project directory
   from `~/.jarvis/projects/`**. There's no prompt. If you have
   files you care about, leave `--delete-files` off.

4. **`--cloud-login`** — uploads your config to the cloud backend.
   Excludes api keys (won't overwrite local ones), but uploads
   everything else: model picks, persona, timeouts, project
   preferences. Read the [cloud docs] before using.

5. **`--self-modify`** — patches `jarvis.py` itself. The auto-revert
   is good but not bulletproof. **Commit your work first** before
   running it.

6. **`setup.bat` on Windows** — adds `%USERPROFILE%\jarvis-exe` to
   PATH. The user-PATH change persists across reboots. If you
   ever want to remove it, run `reg delete HKCU\Environment /v PATH /f`
   (or remove the directory manually).

[cloud docs]: ./CHANGELOG.md#marked-experimental-in-v1.0

---

## command pairs that look similar but do very different things

| Command                       | Does this                              |
|-------------------------------|----------------------------------------|
| `--show-config`               | prints config (read-only)               |
| `--reset`                     | **deletes** config then runs setup     |
| `--auth-test`                 | tests which auth layers work (read-only)|
| `--auth-setup`                | interactive wizard, registers face     |
| `--no-auth`                   | skips the auth gate for one run        |
| `--sessions`                  | lists deep research sessions (read-only)|
| `--delete-session ID`         | **deletes** a deep research session     |
| `--self-status`               | shows self-modify state (read-only)    |
| `--self-savepoint LABEL`      | creates a save point on the side branch |
| `--self-revert TARGET`        | **resets** to a past commit              |
| `--list-devices`              | lists paired devices (read-only)        |
| `--unpair ID`                 | **removes** a paired device             |
| `--cloud-status`              | shows cloud state (read-only)           |
| `--cloud-logout`              | clears local cloud sign-in (no-op in v1)|
| `--cloud-url URL`             | **prints** the env-var line; doesn't change anything |
| `--cloud-signup EMAIL`        | creates an account (asks for password)  |
| `--cloud-login EMAIL`         | uploads config to cloud (asks for password) |

---

## one-liner safe probe

If you just want to "see if jarvis works" without touching anything,
run this:

```bat
jarvis --auth-test
```

That's it. It tests the 3 auth layers (Windows Hello + webcam +
passcode) and reports which ones work on your machine. Doesn't
write any files, doesn't change any state, doesn't make any network
calls. Safe to run as many times as you want.
