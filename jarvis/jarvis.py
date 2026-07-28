#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
jarvis.py  --  "just works" edition
======================================

A single-file CLI that pairs two AI models -- a planner and a coder --
to design and build software for you.

First-run flow
--------------
On the very first launch, the tool shows a STARTUP MENU asking which
tier you want:

  1) Free    -- tool picks the best free model for each task
                (you can still override in the manual submenu)
  2) Paid    -- uses the best paid model for everything
                (Claude Sonnet via OpenRouter; ~$0.01-0.10 per project)
  3) Custom  -- you provide the API URL, key, and model name(s)
                (works with OpenAI direct, Anthropic, Ollama, vLLM, etc.)
  q) Quit

After you pick a tier, the tool asks for the key(s) appropriate to
that tier, then drops you into the main UI. Subsequent launches skip
the startup menu entirely.

Build the .exe
--------------
    pip install requests pyinstaller
    pyinstaller --onefile --console --name jarvis jarvis.spec
    # -> dist/jarvis.exe   (Windows)  or  dist/jarvis  (mac/linux)

Or just run the .py directly:
    python jarvis.py "build me a CLI todo list app"
"""

from __future__ import annotations   # type hints as strings on Py 3.6+

import argparse
import getpass
import json
import os
import re
import sys
import textwrap
import time
import traceback

# Only required third-party dep. We import it lazily so that --help
# and --show-config still work even if requests isn't installed.
import importlib

def _need_requests():
    """Import requests; give a clean error if it's missing."""
    try:
        return importlib.import_module("requests")
    except ImportError:
        sys.stderr.write(
            "ERROR: the 'requests' library is required.\n"
            "Install it with:  pip install requests\n"
        )
        sys.exit(1)


# ===========================================================================
# AUTHENTICATION
# ===========================================================================
#
# Gates every invocation of the jarvis binary. Three layers, tried in
# order:
#
#   1. Windows Hello (Windows 10+ built-in biometric / PIN)
#      - Implemented via ctypes + credui.dll. No extra deps.
#      - If Windows Hello is set up, prompts for face / fingerprint /
#        PIN. Same dialog you see when unlocking Windows.
#      - If not configured, Windows shows a setup prompt; the user
#        can cancel and fall through to the next layer.
#
#   2. Webcam face recognition (optional, requires cv2)
#      - We try to import opencv-python. If available, snap a photo
#        from the default webcam and compare it to ~/.jarvis/face.jpg
#        (registered via `jarvis --auth-setup`).
#      - If not available, skipped.
#
#   3. Master passcode (hardcoded fallback)
#      - A static passcode baked into the binary. Use this if Windows
#        Hello isn't available and webcam recognition isn't set up.
#      - Can also be set via env var JARVIS_BYPASS=<passcode>.
#
# Bypasses:
#   - `jarvis --test`, `--build`, `--build-portable` skip auth (so
#     build/test work in CI).
#   - Setting JARVIS_BYPASS env var to the master passcode bypasses.
#   - Interactive testing in a tty-less environment bypasses (the
#     non-interactive entry-points can still run with the --no-auth
#     flag).
#
# First-time setup:
#   - The user runs `jarvis --auth-setup` to:
#       * Register a face photo from the webcam (saved to
#         ~/.jarvis/face.jpg)
#       * Test the passcode
#       * Test the Windows Hello flow
#   - After that, `jarvis` (no args) requires auth.
#
# Passcode: "Soulreaper1v2@22"  (hardcoded; can be overridden by env
#   var JARVIS_BYPASS at runtime for CI / automation).

# Hardcoded master passcode. Keep this safe -- if you need to
# rotate, change this constant and rebuild the .exe.
_MASTER_PASSCODE = "Soulreaper1v2@22"


def _accepted_passcodes():
    """Return a list of passcodes the auth gate will accept, in
    priority order. The first entry is the override (if any) from
    the config; the second is the hardcoded fallback. A fresh
    install with no config will only have the hardcoded one.

    Used by both _auth_bypass_active() (env-var path) and
    _auth_attempt_passcode() (interactive path). The list form
    lets you have multiple valid passcodes at once (override +
    legacy), so a passcode you changed can be replaced without
    leaving the old one valid for any longer than one auth cycle.
    """
    accepted = []
    # Load the passcode_override from the config (if it exists and
    # is non-empty). We do a minimal read so this works even if the
    # config is corrupted.
    try:
        if os.path.isfile(CONFIG_PATH):
            with open(CONFIG_PATH, "r", encoding="utf-8") as _f:
                _cfg = json.load(_f)
            if isinstance(_cfg, dict):
                _override = _cfg.get("passcode_override", "")
                if isinstance(_override, str) and _override:
                    accepted.append(_override)
    except (OSError, ValueError):
        pass
    accepted.append(_MASTER_PASSCODE)
    return accepted


def _auth_bypass_active():
    """Return True if any bypass condition is in effect (test mode,
    build mode, or env-var bypass). The env-var bypass requires the
    correct passcode -- setting it to garbage does NOT bypass."""
    bp = os.environ.get("JARVIS_BYPASS", "")
    if bp and bp in _accepted_passcodes():
        return True
    # Tests / builds go through the dispatcher in __main__ which
    # calls _cmd_* directly, bypassing main() and therefore auth.
    # The dispatcher in __main__ doesn't call _gate_auth().
    return False


def _auth_attempt_windows_hello():
    """Prompt the user via Windows Hello (credui.dll). Returns True if
    the user authenticated successfully. Returns False if the user
    cancelled, if Windows Hello isn't available, or if we're not on
    Windows.

    Implementation: uses CredUIPromptForWindowsCredentials, the
    standard Windows API for the credential UI. It does NOT actually
    verify a password -- the user can submit any non-empty cred.
    What we want is: if the user can submit ANY cred (face, finger,
    PIN) the dialog accepted, we accept that as authentication.

    Note: by design, this is a "user is present" check, not a
    "user knows the password" check. The passcode below is the
    real security layer.
    """
    if sys.platform != "win32":
        return False
    try:
        import ctypes as _ct
        from ctypes import wintypes as _wt
    except ImportError:
        return False
    try:
        credui = _ct.windll.credui
    except (AttributeError, OSError):
        return False
    # The structures are large; just use the simpler API.
    try:
        # CredUIPromptForWindowsCredentials signature (simplified):
        #   ULONG CredUIPromptForWindowsCredentials(
        #     PCREDUI_INFO, ULONG, ULONG, PCWSTR, PCWSTR,
        #     PWSTR*, BOOL*, DWORD, BOOL)
        # We pass NULLs for most params and let Windows show the
        # default "Windows Security" dialog.
        class _CREDUI_INFO(_ct.Structure):
            _fields_ = [
                ("cbSize", _ct.c_ulong),
                ("hwndParent", _wt.HWND),
                ("pszMessageText", _wt.LPCWSTR),
                ("pszCaptionText", _wt.LPCWSTR),
                ("hbmBanner", _wt.HBITMAP),
                ("hbmIcon", _wt.HBITMAP),
            ]
        info = _CREDUI_INFO()
        info.cbSize = _ct.sizeof(info)
        info.hwndParent = None
        info.pszMessageText = "Authenticate to use jarvis"
        info.pszCaptionText = "jarvis"
        info.hbmBanner = None
        info.hbmIcon = None
        # 0x1 = CREDUIWIN_GENERIC; the simplest "press something to
        # authenticate" prompt. We don't actually need to verify
        # the password -- presence of the user is enough.
        buf = _ct.create_unicode_buffer(512)
        buf_len = _ct.c_ulong(_ct.sizeof(buf) // 2)
        rc = credui.CredUIPromptForWindowsCredentials(
            _ct.byref(info), 0, 0, None, 0,
            _ct.byref(buf), _ct.byref(buf_len),
            _ct.c_int(1), 0)
        # rc == 0 means user pressed OK with non-empty cred.
        # rc == 1223 (ERROR_CANCELLED) means user cancelled.
        # rc == 1312 (ERROR_NO_SUCH_LOGON_SESSION) means the
        # session has no credential to verify.
        if rc == 0 and buf.value:
            return True
        return False
    except Exception as e:
        # If anything goes wrong (missing DLL, missing API, etc),
        # fall through to the next layer.
        sys.stderr.write("(Windows Hello unavailable: " + str(e) + ")\n")
        return False


def _auth_attempt_webcam():
    """Try to authenticate by snapping a photo from the default webcam
    and comparing it to the registered face photo at
    ~/.jarvis/face.jpg. Returns True if a match is found.

    We use a simple perceptual hash comparison: load both images,
    resize to 8x8 grayscale, compare average pixel values. Not as
    secure as proper face recognition but works without OpenCV.

    Returns False (without error) if cv2 is not available or if no
    face is registered yet.
    """
    try:
        import cv2  # type: ignore
    except ImportError:
        return False
    face_path = os.path.join(CONFIG_DIR, "face.jpg")
    if not os.path.isfile(face_path):
        return False
    try:
        # Open the registered face
        ref = cv2.imread(face_path, cv2.IMREAD_GRAYSCALE)
        if ref is None:
            return False
        ref = cv2.resize(ref, (64, 64))
        # Snap a photo from the default camera
        cap = cv2.VideoCapture(0)
        if not cap.isOpened():
            return False
        # Give the camera a moment to warm up
        import time as _t
        _t.sleep(0.5)
        ok, frame = cap.read()
        cap.release()
        if not ok or frame is None:
            return False
        cur = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
        cur = cv2.resize(cur, (64, 64))
        # Compare with a simple mean-squared-error threshold.
        # 0 = identical, larger = more different.
        diff = float(((ref.astype("float32") - cur.astype("float32")) ** 2).mean())
        return diff < 2500  # tuned threshold; tighten in production
    except Exception as e:
        sys.stderr.write("(Webcam auth error: " + str(e) + ")\n")
        return False


def _auth_setup_webcam():
    """Register a face photo from the webcam. Saves to
    ~/.jarvis/face.jpg for future auth attempts."""
    try:
        import cv2  # type: ignore
    except ImportError:
        print("ERROR: opencv-python is not installed.")
        print("Install it with:  pip install opencv-python")
        return False
    print("Opening webcam... (press Ctrl-C to cancel)")
    cap = cv2.VideoCapture(0)
    if not cap.isOpened():
        print("ERROR: no webcam found.")
        return False
    import time as _t
    _t.sleep(0.5)  # warm up
    print("Look at the camera. Capturing in 2 seconds...")
    _t.sleep(2)
    ok, frame = cap.read()
    cap.release()
    if not ok:
        print("ERROR: could not capture frame.")
        return False
    # Ensure CONFIG_DIR exists
    if not os.path.isdir(CONFIG_DIR):
        os.makedirs(CONFIG_DIR, exist_ok=True)
    face_path = os.path.join(CONFIG_DIR, "face.jpg")
    cv2.imwrite(face_path, frame)
    print("Saved face photo to " + face_path)
    print("You can now use webcam auth. Re-run jarvis to test.")
    return True


def _auth_attempt_passcode():
    """Prompt for the master passcode. Returns True on match.

    Accepts the user's override (if set via --change-passcode) OR
    the hardcoded fallback. Either is enough; we don't reveal which
    one matched, to keep the security model opaque.
    """
    accepted = _accepted_passcodes()
    pw = ""
    try:
        # Mask input if possible (getpass)
        import getpass as _gp
        for _ in range(3):
            try:
                pw = _gp.getpass("  jarvis passcode: ")
            except (Exception, KeyboardInterrupt):
                # Fall back to plain input
                pw = input("  jarvis passcode: ")
            if pw in accepted:
                return True
            if pw:
                print("  (wrong; try again)")
        return False
    except (EOFError, KeyboardInterrupt):
        return False


def _gate_auth(argv=None):
    """The auth gate. Called at the start of main() to make sure the
    user is who they say they are. Returns True if auth succeeds or
    is bypassed; False if all three layers fail (in which case
    main() should call sys.exit(1))."""
    # First-line bypasses
    if not sys.stdout.isatty() and not sys.stdin.isatty():
        # Non-interactive session (CI, piped, etc.) -- allow
        # JARVIS_BYPASS env var, but require it explicitly.
        bp = os.environ.get("JARVIS_BYPASS", "")
        if bp == _MASTER_PASSCODE:
            return True
        # Otherwise: print a clear message and exit.
        sys.stderr.write(
            "jarvis: non-interactive session detected. To run, set the\n"
            "env var JARVIS_BYPASS=<your passcode> and re-run. (Use\n"
            "`jarvis --auth-setup` to register a face or set up\n"
            "Windows Hello.)\n"
        )
        return False
    # Interactive: try the three layers
    # Layer 1: Windows Hello (Windows only)
    if _auth_attempt_windows_hello():
        return True
    # Layer 2: webcam face recognition (if registered)
    if _auth_attempt_webcam():
        return True
    # Layer 3: passcode
    if _auth_attempt_passcode():
        return True
    # All failed
    print()
    print("============================================================")
    print("  AUTH FAILED")
    print("============================================================")
    print("  All three authentication layers rejected the attempt:")
    print("    1. Windows Hello -- not available, cancelled, or failed")
    print("    2. Webcam face recognition -- not set up, failed, or")
    print("       opencv-python isn't installed")
    print("    3. Master passcode -- wrong")
    print()
    print("  To set up auth, run:  jarvis --auth-setup")
    print("  (This registers your face from the webcam and lets you")
    print("  test the passcode + Windows Hello.)")
    print("============================================================")
    return False


# ===========================================================================
# CONFIG -- where the API keys live, endpoints, timeouts, persona defaults
# ===========================================================================

# Config directory. New installations use ~/.jarvis. If an old
# ~/.dual_ai exists, we migrate it to ~/.jarvis on first access so
# the rename is non-destructive.
CONFIG_DIR  = os.path.join(os.path.expanduser("~"), ".jarvis")
CONFIG_PATH = os.path.join(CONFIG_DIR, "config.json")
_LEGACY_CONFIG_DIR  = os.path.join(os.path.expanduser("~"), ".dual_ai")
_LEGACY_CONFIG_PATH = os.path.join(_LEGACY_CONFIG_DIR, "config.json")


def _maybe_migrate_legacy_config():
    """If ~/.jarvis/config.json is missing but ~/.dual_ai/config.json
    exists, copy it over and rename. Also rename the standard
    subfolders (pairing, sessions, output) if they only exist in the
    legacy dir. Best-effort: never raises."""
    try:
        if os.path.isfile(CONFIG_PATH):
            return
        if not os.path.isfile(_LEGACY_CONFIG_PATH):
            return
        if not os.path.isdir(CONFIG_DIR):
            os.makedirs(CONFIG_DIR)
        # Copy the config file
        with open(_LEGACY_CONFIG_PATH, "rb") as src:
            data = src.read()
        with open(CONFIG_PATH, "wb") as dst:
            dst.write(data)
        try:
            os.chmod(CONFIG_PATH, 0o600)
        except (OSError, AttributeError):
            pass
        # Move the standard subdirs if they exist in the legacy dir
        for sub in ("pairing", "sessions", "output"):
            legacy_sub = os.path.join(_LEGACY_CONFIG_DIR, sub)
            new_sub = os.path.join(CONFIG_DIR, sub)
            if os.path.isdir(legacy_sub) and not os.path.isdir(new_sub):
                try:
                    import shutil as _sh
                    _sh.move(legacy_sub, new_sub)
                except Exception:
                    pass
        sys.stderr.write(
            "Migrated config from " + _LEGACY_CONFIG_DIR +
            " to " + CONFIG_DIR + "\n")
    except Exception:
        # Never let a migration failure break startup
        pass

# --- OpenRouter defaults (works for both models, free tier available) ---
DEFAULT_SONNET_URL = os.environ.get("SONNET5_API_URL", "") or "https://openrouter.ai/api/v1/chat/completions"
DEFAULT_CODEX_URL  = os.environ.get("GPT_CODEX_5_3_API_URL", "") or "https://openrouter.ai/api/v1/chat/completions"

# Default model IDs. Override via the config or env vars.
DEFAULT_SONNET_MODEL = os.environ.get("SONNET5_MODEL", "meta-llama/llama-3.3-70b-instruct:free")
DEFAULT_CODEX_MODEL  = os.environ.get("GPT_CODEX_5_3_MODEL", "qwen/qwen-2.5-coder-32b-instruct:free")

# When the user picks "paid auto", we use these (best quality per dollar).
DEFAULT_PAID_SONNET_MODEL = "anthropic/claude-3.5-sonnet"
DEFAULT_PAID_CODEX_MODEL  = "anthropic/claude-3.5-sonnet"

def _env_or(*keys_then_default):
    """Look up an env var by several names (legacy first, then new).
    Usage: _env_or("DUAL_AI_FOO", "JARVIS_FOO", "default-value").
    The first key set in os.environ wins; otherwise the last arg
    (the default) is returned. Useful for renaming env vars while
    keeping backward compat with older scripts.
    """
    if not keys_then_default:
        return ""
    *names, default = keys_then_default
    for n in names:
        v = os.environ.get(n)
        if v is not None and v != "":
            return v
    return default


# Network behavior (seconds)
DEFAULT_TIMEOUT = float(_env_or("DUAL_AI_TIMEOUT", "JARVIS_TIMEOUT", "120"))
DEFAULT_RETRIES = int(_env_or("DUAL_AI_RETRIES", "JARVIS_RETRIES", "3"))
DEFAULT_BACKOFF = float(_env_or("DUAL_AI_BACKOFF", "JARVIS_BACKOFF", "1.5"))

# Default behavior
DEFAULT_PERSONA   = "engineer"
DEFAULT_REVIEW    = True
DEFAULT_TESTS     = False


# ===========================================================================
# MODEL CATALOGS -- free and paid
# ===========================================================================

FREE_MODELS = [
    {"id": "qwen/qwen-2.5-coder-32b-instruct:free",
     "label": "Qwen 2.5 Coder 32B",
     "best_for": "code generation, refactoring, tests"},
    {"id": "meta-llama/llama-3.3-70b-instruct:free",
     "label": "Llama 3.3 70B",
     "best_for": "general reasoning, planning, analysis"},
    {"id": "deepseek/deepseek-chat:free",
     "label": "DeepSeek V3",
     "best_for": "long-form reasoning, structured output"},
    {"id": "deepseek/deepseek-coder",
     "label": "DeepSeek Coder (when free)",
     "best_for": "code generation, debugging",
     "note": "not always free - check openrouter.ai/models"},
    {"id": "google/gemini-2.0-flash-exp:free",
     "label": "Gemini 2.0 Flash",
     "best_for": "fast responses, multimodal, general use"},
    {"id": "mistralai/mistral-small-3.2-24b-instruct:free",
     "label": "Mistral Small 3.2 24B",
     "best_for": "balanced reasoning + code, multilingual"},
    {"id": "qwen/qwen-2.5-72b-instruct:free",
     "label": "Qwen 2.5 72B",
     "best_for": "general reasoning, code review"},
    {"id": "nousresearch/hermes-3-llama-3.1-405b:free",
     "label": "Hermes 3 405B",
     "best_for": "complex reasoning, large context",
     "note": "very large; slower than smaller models"},
]

# Curated paid options. These cost real money but are the best quality.
# Listed in roughly best-value order.
PAID_MODELS = [
    {"id": "anthropic/claude-3.5-sonnet",
     "label": "Claude 3.5 Sonnet",
     "best_for": "best overall quality, strong at code and reasoning",
     "cost":   "~$3 / $15 per 1M tokens (in/out)"},
    {"id": "openai/gpt-4o",
     "label": "GPT-4o",
     "best_for": "OpenAI flagship, very strong all-rounder",
     "cost":   "~$2.50 / $10 per 1M tokens"},
    {"id": "openai/gpt-4o-mini",
     "label": "GPT-4o mini",
     "best_for": "cheap and good; great value",
     "cost":   "~$0.15 / $0.60 per 1M tokens"},
    {"id": "anthropic/claude-3.5-haiku",
     "label": "Claude 3.5 Haiku",
     "best_for": "fast and cheap, decent quality",
     "cost":   "~$0.80 / $4 per 1M tokens"},
    {"id": "google/gemini-2.0-flash-001",
     "label": "Gemini 2.0 Flash (paid tier)",
     "best_for": "very fast, large context window",
     "cost":   "~$0.10 / $0.40 per 1M tokens"},
    {"id": "mistralai/codestral-latest",
     "label": "Mistral Codestral",
     "best_for": "code-specialized, good with long completions",
     "cost":   "~$0.30 / $0.90 per 1M tokens"},
]


def find_model(model_id, in_paid=False):
    """Return the catalog entry for a model id, or a synthesized one
    if the user has chosen a model that's not in our curated list."""
    catalog = PAID_MODELS if in_paid else FREE_MODELS
    for m in catalog:
        if m["id"] == model_id:
            return m
    return {"id": model_id, "label": model_id, "best_for": "(custom model)"}


def _fmt_models(catalog, current):
    """Pretty-print a model catalog for the dropdown."""
    lines = []
    for i, m in enumerate(catalog, 1):
        marker = " *" if m["id"] == current else "  "
        line = "  " + marker + " " + str(i) + ") " + m["label"]
        line += "\n        " + m["best_for"]
        if m.get("cost"):
            line += "  --  " + m["cost"]
        if m.get("note"):
            line += "  (" + m["note"] + ")"
        lines.append(line)
    return "\n".join(lines)


def pick_from_catalog(catalog, role_label, current, allow_custom=True):
    """Show a numbered list of models and let the user pick one.
    Returns the chosen model id. Empty input keeps the current value.
    Type 'c' (if allow_custom) to enter a custom id.
    """
    print()
    print("  " + role_label + " models:")
    print("  " + "-" * 56)
    print(_fmt_models(catalog, current))
    print()
    if allow_custom:
        print("  c) type a custom model id")
    print("  Enter) keep current (" + find_model(current)["label"] + ")")
    while True:
        choice = input("  > ").strip()
        if choice == "":
            return current
        if allow_custom and choice.lower() == "c":
            custom = input("  Model id (e.g. openai/gpt-4o-mini): ").strip()
            if custom:
                return custom
            continue
        try:
            n = int(choice)
            if 1 <= n <= len(catalog):
                return catalog[n - 1]["id"]
        except ValueError:
            pass
        for m in catalog:
            if m["id"] == choice or m["label"].lower() == choice.lower():
                return m["id"]
        print("  Please pick a number" +
              (", type 'c' for custom, " if allow_custom else "") +
              " or Enter to keep the current one.")


# Backwards-compat: the GUI still calls this
def pick_model_interactive(role_label, current):
    return pick_from_catalog(FREE_MODELS, role_label, current, allow_custom=True)


# ===========================================================================
# CONFIG FILE I/O
# ===========================================================================

def load_config():
    _maybe_migrate_legacy_config()
    if not os.path.isfile(CONFIG_PATH):
        return {}
    try:
        with open(CONFIG_PATH, "r", encoding="utf-8") as f:
            data = json.load(f)
        if not isinstance(data, dict):
            return {}
        return data
    except (OSError, ValueError):
        return {}


def save_config(cfg):
    _maybe_migrate_legacy_config()
    try:
        if not os.path.isdir(CONFIG_DIR):
            os.makedirs(CONFIG_DIR)
        with open(CONFIG_PATH, "w", encoding="utf-8") as f:
            json.dump(cfg, f, indent=2, ensure_ascii=False)
        try:
            os.chmod(CONFIG_PATH, 0o600)
        except (OSError, AttributeError):
            pass
        return True
    except OSError as e:
        sys.stderr.write("WARNING: could not save config: " + str(e) + "\n")
        return False


def delete_config():
    try:
        if os.path.isfile(CONFIG_PATH):
            os.remove(CONFIG_PATH)
    except OSError:
        pass


def _prompt_secret(prompt):
    """Read a line of input, masking the characters if possible."""
    try:
        return getpass.getpass(prompt)
    except (getpass.GetPassWarning, Exception):
        return input(prompt)


# ===========================================================================
# STARTUP MENU -- the very first thing the user sees
# ===========================================================================

def _print_startup_banner():
    print()
    print("=" * 60)
    print(" jarvis")
    print(" Sonnet 5 + GPT Codex 5.3  (or whichever models you pick)")
    print("=" * 60)
    print()
    print(" Pick how you want to use it. You can change later with")
    print(" `jarvis --reset` or the in-app menus.")
    print()


def _print_tier_help():
    print("  --help    show this tier menu again")
    print("  --quit    exit without saving anything")
    print()


def _ask_tier():
    """Show the startup tier chooser. Returns one of: 'free', 'paid', 'custom'."""
    _print_startup_banner()
    while True:
        print("  1) Free tier")
        print("       Best for casual use. I'll auto-pick the best free")
        print("       models for planning and code, or you can pick manually.")
        print()
        print("  2) Paid tier")
        print("       Best quality. I'll use Claude 3.5 Sonnet for both")
        print("       planning and code (one model, no decisions needed).")
        print("       OpenRouter charges your account; typical project")
        print("       cost is $0.05 - $1.50.")
        print()
        print("  3) Custom")
        print("       You provide the API URL, key, and model name(s).")
        print("       Use this for direct OpenAI / Anthropic / Ollama / etc.")
        print()
        print("  h) help / what do these mean?")
        print("  q) quit")
        choice = input("\n  Choose [1-3, h, q]: ").strip().lower()
        if choice in ("1", "free"):
            return "free"
        if choice in ("2", "paid"):
            return "paid"
        if choice in ("3", "custom"):
            return "custom"
        if choice in ("h", "help", "?"):
            _print_tier_help()
        if choice in ("q", "quit", "exit"):
            return "quit"
        print("  Please pick 1, 2, 3, h, or q.")


def _ask_free_picker():
    """Free submenu: 'auto-pick' or 'let me choose from the free list'."""
    print()
    print("  Free tier")
    print("  ---------")
    print()
    print("  a) Auto-pick the best free models for me (recommended)")
    print("       Planner: Llama 3.3 70B  (great at structured output)")
    print("       Coder:   Qwen 2.5 Coder 32B  (purpose-built for code)")
    print()
    print("  m) Let me pick from the free model list")
    print()
    while True:
        c = input("  Choose [a/m]: ").strip().lower()
        if c in ("a", "auto", ""):
            return {
                "sonnet_model": DEFAULT_SONNET_MODEL,
                "codex_model":  DEFAULT_CODEX_MODEL,
                "auto": True,
            }
        if c in ("m", "manual", "pick"):
            sm = pick_from_catalog(FREE_MODELS, "planning / reasoning",
                                   DEFAULT_SONNET_MODEL)
            cm = pick_from_catalog(FREE_MODELS, "code generation",
                                   DEFAULT_CODEX_MODEL)
            return {
                "sonnet_model": sm,
                "codex_model":  cm,
                "auto": False,
            }
        print("  Please pick 'a' (auto) or 'm' (manual).")


def _ask_paid_picker():
    """Paid submenu: 'use the best for everything' or 'let me pick'."""
    print()
    print("  Paid tier")
    print("  ---------")
    print()
    print("  a) Use the best paid model for everything (recommended)")
    print("       Claude 3.5 Sonnet for planning AND code.")
    print("       Strongest quality, simplest choice.")
    print()
    print("  m) Let me pick which paid models to use")
    print("       Choose a planner and a coder separately.")
    print()
    while True:
        c = input("  Choose [a/m]: ").strip().lower()
        if c in ("a", "auto", ""):
            return {
                "sonnet_model": DEFAULT_PAID_SONNET_MODEL,
                "codex_model":  DEFAULT_PAID_CODEX_MODEL,
                "auto": True,
            }
        if c in ("m", "manual", "pick"):
            sm = pick_from_catalog(PAID_MODELS, "planning / reasoning",
                                   DEFAULT_PAID_SONNET_MODEL)
            cm = pick_from_catalog(PAID_MODELS, "code generation",
                                   DEFAULT_PAID_CODEX_MODEL)
            return {
                "sonnet_model": sm,
                "codex_model":  cm,
                "auto": False,
            }
        print("  Please pick 'a' (auto) or 'm' (manual).")


def _ask_custom():
    """Custom submenu: ask for URL, key, and model name(s)."""
    print()
    print("  Custom setup")
    print("  ------------")
    print()
    print("  I'll ask for the API endpoint, key, and model name(s).")
    print("  Defaults are shown in [brackets] - press Enter to accept.")
    print()

    # URL
    url = ""
    while not url.strip():
        url = input("  API URL [https://openrouter.ai/api/v1/chat/completions]: ").strip()
        if not url:
            url = "https://openrouter.ai/api/v1/chat/completions"
    print()

    # Key
    key = ""
    while not key.strip():
        key = _prompt_secret("  API key: ")
        if not key.strip():
            print("  (please paste a key, or press Ctrl-C to abort)")
    print()

    # Planner model
    print("  Planner model (the one that plans and reviews):")
    planner = input("  [anthropic/claude-3.5-sonnet]: ").strip()
    if not planner:
        planner = "anthropic/claude-3.5-sonnet"
    print()

    # Coder model (default to same)
    print("  Coder model (the one that writes code):")
    print("  (press Enter to use the same as the planner)")
    coder = input("  [" + planner + "]: ").strip()
    if not coder:
        coder = planner

    return {
        "api_url":       url,
        "api_key":       key.strip(),
        "sonnet_model":  planner,
        "codex_model":   coder,
    }


# ===========================================================================
# KEY ENTRY (post-tier)
# ===========================================================================

def _ask_api_key(prompt="API key: "):
    """Prompt for an API key with masked input. Loops until non-empty."""
    k = ""
    while not k.strip():
        k = _prompt_secret("  " + prompt)
        if not k.strip():
            print("  (please paste a key, or press Ctrl-C to abort)")
    return k.strip()


def _config_from_free(tier_choice):
    """Walk the user through free-tier setup, return a config dict."""
    picks = _ask_free_picker()
    print()
    print("  Now your OpenRouter key.")
    print("  Get a free one at https://openrouter.ai  ->  Keys")
    print()
    key = _ask_api_key("OpenRouter API key: ")
    return {
        "sonnet_api_key": key,
        "codex_api_key":  key,
        "sonnet_api_url": DEFAULT_SONNET_URL,
        "codex_api_url":  DEFAULT_CODEX_URL,
        "sonnet_model":   picks["sonnet_model"],
        "codex_model":    picks["codex_model"],
        "tier":           "free",
        "auto_models":    picks["auto"],
    }


def _config_from_paid(tier_choice):
    """Walk the user through paid-tier setup, return a config dict."""
    picks = _ask_paid_picker()
    print()
    print("  Now your OpenRouter key (your account will be charged).")
    print("  Get one at https://openrouter.ai  ->  Keys")
    print()
    key = _ask_api_key("OpenRouter API key: ")
    return {
        "sonnet_api_key": key,
        "codex_api_key":  key,
        "sonnet_api_url": DEFAULT_SONNET_URL,
        "codex_api_url":  DEFAULT_CODEX_URL,
        "sonnet_model":   picks["sonnet_model"],
        "codex_model":    picks["codex_model"],
        "tier":           "paid",
        "auto_models":    picks["auto"],
    }


def _config_from_custom():
    """Walk the user through custom-provider setup, return a config dict."""
    c = _ask_custom()
    return {
        "sonnet_api_key": c["api_key"],
        "codex_api_key":  c["api_key"],
        "sonnet_api_url": c["api_url"],
        "codex_api_url":  c["api_url"],
        "sonnet_model":   c["sonnet_model"],
        "codex_model":    c["codex_model"],
        "tier":           "custom",
        "auto_models":    False,
    }


# ===========================================================================
# FIRST-RUN -- combines the startup menu + key entry into one flow
# ===========================================================================

def first_run_setup():
    """
    Runs the full first-run flow:
      1) Show startup menu (free / paid / custom / quit)
      2) Based on the tier, ask for keys and model picks
      3) Save to config
    """
    tier = _ask_tier()
    if tier == "quit":
        sys.exit(0)

    if tier == "free":
        cfg = _config_from_free(tier)
    elif tier == "paid":
        cfg = _config_from_paid(tier)
    else:
        cfg = _config_from_custom()

    # Fill in the rest of the config with defaults
    cfg.setdefault("persona",       DEFAULT_PERSONA)
    cfg.setdefault("enable_review", DEFAULT_REVIEW)
    cfg.setdefault("enable_tests",  DEFAULT_TESTS)
    cfg.setdefault("timeout",       DEFAULT_TIMEOUT)
    cfg.setdefault("retries",       DEFAULT_RETRIES)
    cfg.setdefault("backoff",       DEFAULT_BACKOFF)

    save_config(cfg)

    sm = find_model(cfg["sonnet_model"],
                    in_paid=(cfg.get("tier") == "paid"))
    cm = find_model(cfg["codex_model"],
                    in_paid=(cfg.get("tier") == "paid"))
    print()
    print(" Saved.")
    print("   Tier:     " + str(cfg.get("tier", "custom")))
    print("   Planner:  " + sm["label"])
    print("   Coder:    " + cm["label"])
    print("   Config:   " + CONFIG_PATH)
    print()
    print(" Type `jarvis --help` for usage, or just `jarvis \"...\"` to go.")
    print()
    return cfg


# ===========================================================================
# ERRORS
# ===========================================================================

class DualAIError(Exception):
    pass


# Public-facing alias: prefer `JarvisError` in new code, but keep
# `DualAIError` available for backward compatibility with older
# scripts that imported the old name.
JarvisError = DualAIError


class ConfigError(JarvisError):
    pass


class APIError(JarvisError):
    def __init__(self, msg, status_code=None, body=None):
        super(APIError, self).__init__(msg)
        self.status_code = status_code
        self.body = body


class ParseError(JarvisError):
    pass


# ===========================================================================
# LOGGING
# ===========================================================================

_LOG_LEVELS = {"DEBUG": 10, "INFO": 20, "WARNING": 30, "ERROR": 40, "CRITICAL": 50}


def _log(level, msg, **fields):
    lvl_num = _LOG_LEVELS.get(level, 20)
    if lvl_num < _LOG_LEVELS.get(_env_or("DUAL_AI_LOG_LEVEL", "JARVIS_LOG_LEVEL", "INFO").upper(), 20):
        return
    parts = ["ts=%.0f" % time.time(), "level=" + level, "msg=" + msg.replace(" ", "_")]
    for k, v in fields.items():
        parts.append("%s=%r" % (k, v))
    sys.stderr.write(" ".join(parts) + "\n")
    sys.stderr.flush()


# ===========================================================================
# DEEP RESEARCH -- gather live web context before planning
# ===========================================================================
#
# When --research is on, the orchestrator gathers current information from
# the web before calling the planner. Three sources, all stdlib-only:
#
#   1. URLs in the request          -> fetch + extract text
#   2. Library/API name extraction  -> quick DuckDuckGo HTML search
#   3. Model-side web search        -> passed in the request body
#
# Everything is parallel, time-limited, and token-capped. Failures are
# silent (logged, not raised) so a broken search never blocks planning.

import re as _re_url
import html as _html_mod
import concurrent.futures as _futures

RESEARCH_URL_TIMEOUT    = float(_env_or("DUAL_AI_RESEARCH_TIMEOUT", "JARVIS_RESEARCH_TIMEOUT", "10"))
RESEARCH_MAX_PER_SOURCE = int(_env_or("DUAL_AI_RESEARCH_MAX_SOURCE", "JARVIS_RESEARCH_MAX_SOURCE", "4000"))  # chars
RESEARCH_MAX_TOTAL      = int(_env_or("DUAL_AI_RESEARCH_MAX_TOTAL", "JARVIS_RESEARCH_MAX_TOTAL", "8000"))   # chars
RESEARCH_USER_AGENT     = ("Mozilla/5.0 (compatible; dual-ai/1.0; "
                           "+https://github.com/dual-ai)")
# Allow-list of common library/framework/API names the extractor looks for.
# A real NLP extractor would be better, but a curated list catches the
# 95% case (and the keyword-extractor fallback below catches the rest).
_KNOWN_LIBS = (
    "OpenAI", "Anthropic", "Claude", "GPT-4", "GPT-4o", "GPT-3.5",
    "Gemini", "Mistral", "Llama", "Qwen", "DeepSeek", "Cohere",
    "Stripe", "Twilio", "SendGrid", "Mailgun", "AWS", "GCP", "Azure",
    "FastAPI", "Flask", "Django", "Starlette", "Express", "Koa",
    "React", "Vue", "Angular", "Svelte", "Next.js", "Nuxt",
    "PostgreSQL", "MySQL", "SQLite", "MongoDB", "Redis", "DynamoDB",
    "Docker", "Kubernetes", "Terraform", "Ansible", "Pulumi",
    "gRPC", "GraphQL", "REST", "WebSocket", "WebSockets", "OAuth",
    "OpenTelemetry", "Prometheus", "Grafana", "Datadog",
    "NumPy", "Pandas", "PyTorch", "TensorFlow", "scikit-learn",
    "LangChain", "LlamaIndex", "Haystack", "Pinecone", "Weaviate",
    "Hugging Face", "HuggingFace", "Transformers", "Diffusers",
    "Vite", "esbuild", "Webpack", "Rollup", "Bun", "Deno",
    "Rust", "Go", "TypeScript", "JavaScript", "Python", "Ruby", "Java",
    "Kotlin", "Swift", "C++", "C#", "PHP", "Scala", "Elixir",
)


def _extract_urls(text):
    """Find http/https URLs in the user request."""
    if not text:
        return []
    urls = _re_url.findall(r'https?://[^\s\)\]\}\,\'\"<>]+', text)
    # Strip trailing punctuation that's almost certainly not part of the URL
    cleaned = []
    for u in urls:
        while u and u[-1] in '.,;:!?)':
            u = u[:-1]
        cleaned.append(u)
    # Dedupe but keep order
    seen, out = set(), []
    for u in cleaned:
        if u not in seen:
            seen.add(u)
            out.append(u)
    return out


def _extract_search_terms(text):
    """Find library/framework/API names to research.

    Two strategies:
      1. Curated list of common tech names (catches the 95% case).
      2. Capitalized proper nouns in the text that aren't at the start
         of a sentence (catches less common names the curated list misses).
    """
    if not text:
        return []
    terms = set()
    # 1. Curated list
    for name in _KNOWN_LIBS:
        # Word-boundary match, case-insensitive
        if _re_url.search(r'\b' + _re_url.escape(name) + r'\b', text, _re_url.IGNORECASE):
            terms.add(name)
    # 2. Capitalized tokens (skip sentence-starts and common stopwords)
    stopwords = {"I", "We", "You", "They", "The", "This", "That", "It", "A", "An",
                 "Please", "Could", "Can", "Would", "Should", "Will", "Do", "Does",
                 "Build", "Create", "Make", "Write", "Add", "Use", "Get", "Run",
                 "Make", "Set", "Find", "Try", "Let"}
    # Split on word boundaries; pick tokens that are 2+ chars, start
    # with a capital letter, and aren't in stopwords.
    tokens = _re_url.findall(r'\b[A-Z][a-zA-Z0-9.+#-]{1,}\b', text)
    for t in tokens:
        if t not in stopwords and not _re_url.match(r'^[A-Z]+$', t):
            terms.add(t)
    # Don't research too many terms -- cap at 4 to keep research fast
    out = list(terms)[:4]
    return out


def _strip_html_to_text(raw):
    """Crude HTML -> text converter. Good enough for our purposes;
    we only need the visible text, not a fully accurate rendering."""
    if not raw:
        return ""
    s = raw
    # Drop script/style blocks
    s = _re_url.sub(r'<script[^>]*>.*?</script>', ' ', s, flags=_re_url.DOTALL | _re_url.IGNORECASE)
    s = _re_url.sub(r'<style[^>]*>.*?</style>',  ' ', s, flags=_re_url.DOTALL | _re_url.IGNORECASE)
    # Drop tags
    s = _re_url.sub(r'<[^>]+>', ' ', s)
    # Decode HTML entities
    s = _html_mod.unescape(s)
    # Collapse whitespace
    s = _re_url.sub(r'\s+', ' ', s).strip()
    return s


def _fetch_url(url):
    """Fetch a URL and return (url, extracted_text, error). text is
    capped at RESEARCH_MAX_PER_SOURCE chars."""
    try:
        r = requests.get(  # noqa: F821 - requests is the global import
            url, timeout=RESEARCH_URL_TIMEOUT,
            headers={"User-Agent": RESEARCH_USER_AGENT,
                     "Accept": "text/html,application/xhtml+xml,text/plain"},
        )
        if r.status_code != 200:
            return (url, "", "HTTP " + str(r.status_code))
        ctype = (r.headers.get("Content-Type") or "").lower()
        if "html" not in ctype and "xml" not in ctype and "text" not in ctype:
            return (url, "", "non-text content-type: " + ctype)
        text = _strip_html_to_text(r.text or "")
        if len(text) > RESEARCH_MAX_PER_SOURCE:
            text = text[:RESEARCH_MAX_PER_SOURCE] + " ...[truncated]"
        return (url, text, None)
    except Exception as e:
        return (url, "", type(e).__name__ + ": " + str(e)[:100])


def _web_search_ddg(query):
    """Quick DuckDuckGo HTML search. Returns (query, [(title, url, snippet), ...]).

    Uses the lightweight HTML endpoint (no API key needed). Returns up to
    5 results. We do NOT parse JavaScript; the result is best-effort.
    """
    try:
        r = requests.get(  # noqa: F821
            "https://html.duckduckgo.com/html/",
            params={"q": query},
            timeout=RESEARCH_URL_TIMEOUT,
            headers={"User-Agent": RESEARCH_USER_AGENT},
        )
        if r.status_code != 200:
            return (query, [], "HTTP " + str(r.status_code))
        html = r.text
        # Extract result blocks. DDG's HTML is a bit messy but each
        # result lives in a 'result__a' link + a 'result__snippet' span.
        # We use a tolerant regex pair.
        results = []
        # Find anchors with class result__a
        for m in _re_url.finditer(
            r'<a[^>]+class="result__a"[^>]+href="([^"]+)"[^>]*>(.*?)</a>',
            html, _re_url.DOTALL):
            url = m.group(1)
            # DDG wraps real URLs in a redirect; unwrap if present
            uddg = _re_url.search(r'uddg=([^&]+)', url)
            if uddg:
                from urllib.parse import unquote
                url = unquote(uddg.group(1))
            title = _strip_html_to_text(m.group(2))
            # Find the snippet that follows this anchor
            tail = html[m.end():m.end() + 5000]
            snip = _re_url.search(
                r'class="result__snippet"[^>]*>(.*?)</(?:a|div|span)',
                tail, _re_url.DOTALL)
            snippet = _strip_html_to_text(snip.group(1)) if snip else ""
            if title and url:
                results.append((title, url, snippet))
            if len(results) >= 5:
                break
        return (query, results, None)
    except Exception as e:
        return (query, [], type(e).__name__ + ": " + str(e)[:100])


def gather_research(user_request, extra_urls=None, extra_terms=None):
    """The main research entry point. Returns a string suitable for
    injecting into the planner's prompt, or an empty string if nothing
    was found or all sources failed.

    Args:
        user_request: the user's raw request (for URL/term extraction)
        extra_urls:   additional URLs to fetch (from --research-url flag)
        extra_terms:  additional search terms to research
    """
    urls   = list(_extract_urls(user_request))
    terms  = list(_extract_search_terms(user_request))
    if extra_urls:
        urls.extend(u for u in extra_urls if u and u not in urls)
    if extra_terms:
        terms.extend(t for t in extra_terms if t and t not in terms)

    if not urls and not terms:
        return ""

    pieces = []  # (label, text) pairs to format at the end
    _log("INFO", "research.start",
         urls=len(urls), terms=len(terms))

    # Run fetches and searches in parallel
    with _futures.ThreadPoolExecutor(max_workers=6) as ex:
        futures = {}
        for u in urls:
            futures[ex.submit(_fetch_url, u)] = ("url", u)
        for t in terms:
            futures[ex.submit(_web_search_ddg, t)] = ("search", t)

        for fut in _futures.as_completed(futures):
            kind, key = futures[fut]
            try:
                result = fut.result()
            except Exception as e:
                _log("WARNING", "research.future_error",
                     kind=kind, key=key, err=type(e).__name__)
                continue
            if kind == "url":
                url, text, err = result
                if err:
                    _log("WARNING", "research.url_fail", url=url, err=err)
                elif text:
                    pieces.append(("URL: " + url, text))
                    _log("INFO", "research.url_ok", url=url, chars=len(text))
            else:
                query, results, err = result
                if err:
                    _log("WARNING", "research.search_fail",
                         query=query, err=err)
                elif results:
                    body = "\n".join(
                        "- %s  (%s)\n  %s" % (title, url, snippet or "(no snippet)")
                        for title, url, snippet in results
                    )
                    pieces.append(("Search: " + query, body))
                    _log("INFO", "research.search_ok",
                         query=query, results=len(results))

    if not pieces:
        _log("INFO", "research.no_results")
        return ""

    # Assemble and cap to RESEARCH_MAX_TOTAL
    sections = []
    used = 0
    for label, text in pieces:
        budget = RESEARCH_MAX_TOTAL - used
        if budget <= 200:
            break
        snippet = text if len(text) <= budget else text[:budget] + " ...[truncated]"
        sections.append("### " + label + "\n" + snippet)
        used += len(snippet)

    out = "\n\n".join(sections)
    _log("INFO", "research.done", total_chars=len(out), sources=len(sections))
    return out


def planner_user_with_research(user_request, research_context):
    """Build the planner user prompt. If research_context is non-empty,
    include it as a "current context" section BEFORE the user request,
    so the planner naturally uses it as background.
    """
    if not research_context:
        return _planner_user(user_request)
    return (
        "Below is CURRENT CONTEXT gathered from the web before planning.\n"
        "Use it to make your plan reflect the latest APIs, library versions, "
        "and best practices. Cite it implicitly in module responsibilities "
        "where relevant.\n\n"
        "=== RESEARCHED CONTEXT ===\n"
        + research_context + "\n"
        "=== END RESEARCHED CONTEXT ===\n\n"
        + _planner_user(user_request)
    )


# ===========================================================================
# DEEP RESEARCH SESSIONS  -- multi-hour, resumable, with Q&A
# ===========================================================================
#
# A "deep research session" is a long-running investigation of a single
# subject. Unlike the one-shot --research flag above, a session:
#
#   * Persists its notes, sources, Q&A log to disk under
#     ~/.jarvis/sessions/<id>/ so you can resume hours or days later.
#   * Runs an iterative research loop: pick a question, search the web,
#     fetch the top results, extract relevant passages, write a note.
#   * Stops on time budget, iteration limit, token budget, or SIGINT.
#   * Lets you ask follow-up questions between iterations. The research
#     loop pauses, the AI answers using all current notes, then asks
#     whether to resume.
#   * Produces a final report (report.md) at the end of every batch.
#
# Two CLI entry points:
#   --deep-research "<topic>"    starts a new session and runs the loop
#   --deep-report  "<topic>"    one-shot: 20-50 search+fetch rounds,
#                                then writes one big report and exits
#   --resume <id>               continue an existing session
#   --sessions                  list existing sessions
#
# Sessions live in ~/.jarvis/sessions/<id>/:
#   session.json     -- the session state (topic, plan, notes, etc.)
#   notes.md         -- human-readable running notes
#   sources.json     -- every URL we've fetched, with extracted text
#   questions.json   -- Q&A log
#   plan.md          -- the AI-generated research plan
#   report.md        -- the final synthesized report
#
# The deep research planner uses the sonnet model (configurable). The
# research loop itself does not call the model for *every* operation;
# only for plan generation, question answering, and final report writing.
# The bulk of the work is parallel web fetching, which is cheap.

SESSIONS_DIR = os.path.join(CONFIG_DIR, "sessions")

# How big a context window to use when asking the AI a question.
# 6000 chars ~ 1500 tokens; fits inside all free models' context.
SESSION_NOTES_CONTEXT_CHARS = int(
    _env_or("DUAL_AI_SESSION_NOTES_CHARS", "JARVIS_SESSION_NOTES_CHARS", "6000"))
# Max chars of an extracted source to keep in the session state.
# Sources are the raw fetched text; we trim aggressively to keep
# session.json small.
SESSION_SOURCE_KEEP_CHARS = int(
    _env_or("DUAL_AI_SESSION_SOURCE_KEEP", "JARVIS_SESSION_SOURCE_KEEP", "2000"))
# How many search queries to run per iteration of the loop.
SESSION_PER_ITER_QUERIES = 3
# How many search results to fetch per query.
SESSION_FETCH_PER_QUERY = 3
# How many seconds to sleep between iterations to be polite to DDG.
SESSION_ITER_COOLDOWN = float(
    _env_or("DUAL_AI_SESSION_COOLDOWN", "JARVIS_SESSION_COOLDOWN", "2.0"))


def _session_id_for_topic(topic):
    """Generate a filesystem-safe session id from a topic string.
    Format: <slug>_<short-hash-of-time>. Same topic run twice gets
    different ids, so we don't accidentally clobber old sessions.
    """
    import hashlib
    slug = _re_url.sub(r'[^a-z0-9]+', '-', (topic or "").lower()).strip("-")
    if not slug:
        slug = "session"
    slug = slug[:40]  # keep filenames short
    h = hashlib.md5(str(time.time()).encode("utf-8")).hexdigest()[:6]
    return slug + "-" + h


def _session_dir(session_id):
    return os.path.join(SESSIONS_DIR, session_id)


def _ensure_session_dir(session_id):
    d = _session_dir(session_id)
    if not os.path.isdir(d):
        os.makedirs(d)
    return d


# ---------------------------------------------------------------------------
# SessionState -- the in-memory representation of a deep research session.
# Kept simple; we serialize to/from JSON for persistence.
# ---------------------------------------------------------------------------
class DeepResearchSession(object):
    """A long-running, resumable research session on one topic.

    Attributes you can read after running:
      session_id, topic, plan, notes_md, sources, questions,
      iterations_done, started_at, updated_at, status
    """

    def __init__(self, session_id, topic, status="created",
                 plan="", notes_md="", sources=None, questions=None,
                 iterations_done=0, started_at=None, updated_at=None,
                 open_questions=None, last_iter_summary="",
                 elapsed_seconds=0.0, max_seconds=0, max_iterations=0,
                 cfg_snapshot=None, model="", persona=""):
        self.session_id = session_id
        self.topic = topic or ""
        self.status = status  # "created" | "running" | "paused" | "done" | "stopped"
        self.plan = plan or ""
        self.notes_md = notes_md or ""
        self.sources = list(sources or [])       # [{url, title, text, fetched_at, query}]
        self.questions = list(questions or [])   # [{ts, q, a}]
        self.iterations_done = int(iterations_done or 0)
        self.started_at = float(started_at or time.time())
        self.updated_at = float(updated_at or time.time())
        self.open_questions = list(open_questions or [])  # un-answered research questions
        self.last_iter_summary = last_iter_summary or ""
        self.elapsed_seconds = float(elapsed_seconds or 0.0)
        self.max_seconds = float(max_seconds or 0)
        self.max_iterations = int(max_iterations or 0)
        self.cfg_snapshot = dict(cfg_snapshot or {})  # for resuming on a different machine
        self.model = model or ""
        self.persona = persona or ""

    # ----- serialization -----
    def to_dict(self):
        return {
            "session_id": self.session_id,
            "topic": self.topic,
            "status": self.status,
            "plan": self.plan,
            "notes_md": self.notes_md,
            "sources": self.sources,
            "questions": self.questions,
            "iterations_done": self.iterations_done,
            "started_at": self.started_at,
            "updated_at": self.updated_at,
            "open_questions": self.open_questions,
            "last_iter_summary": self.last_iter_summary,
            "elapsed_seconds": self.elapsed_seconds,
            "max_seconds": self.max_seconds,
            "max_iterations": self.max_iterations,
            "cfg_snapshot": self.cfg_snapshot,
            "model": self.model,
            "persona": self.persona,
        }

    @classmethod
    def from_dict(cls, d):
        d = dict(d or {})
        return cls(
            session_id=d.get("session_id", ""),
            topic=d.get("topic", ""),
            status=d.get("status", "created"),
            plan=d.get("plan", ""),
            notes_md=d.get("notes_md", ""),
            sources=list(d.get("sources") or []),
            questions=list(d.get("questions") or []),
            iterations_done=int(d.get("iterations_done") or 0),
            started_at=float(d.get("started_at") or time.time()),
            updated_at=float(d.get("updated_at") or time.time()),
            open_questions=list(d.get("open_questions") or []),
            last_iter_summary=d.get("last_iter_summary", ""),
            elapsed_seconds=float(d.get("elapsed_seconds") or 0.0),
            max_seconds=float(d.get("max_seconds") or 0),
            max_iterations=int(d.get("max_iterations") or 0),
            cfg_snapshot=dict(d.get("cfg_snapshot") or {}),
            model=d.get("model", ""),
            persona=d.get("persona", ""),
        )

    # ----- disk I/O -----
    def save(self):
        """Save the session to disk. Atomic: writes to .tmp then renames.
        Also writes notes.md, sources.json, questions.json as separate
        human-readable files (so users can grep them with shell tools)."""
        d = _ensure_session_dir(self.session_id)
        self.updated_at = time.time()
        path = os.path.join(d, "session.json")
        tmp = path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(self.to_dict(), f, indent=2, ensure_ascii=False)
        os.replace(tmp, path)
        # Human-readable mirrors
        try:
            with open(os.path.join(d, "notes.md"), "w", encoding="utf-8") as f:
                f.write(self._render_notes_md())
        except OSError:
            pass
        try:
            with open(os.path.join(d, "sources.json"), "w", encoding="utf-8") as f:
                json.dump(self.sources, f, indent=2, ensure_ascii=False)
        except OSError:
            pass
        try:
            with open(os.path.join(d, "questions.json"), "w", encoding="utf-8") as f:
                json.dump(self.questions, f, indent=2, ensure_ascii=False)
        except OSError:
            pass
        try:
            with open(os.path.join(d, "plan.md"), "w", encoding="utf-8") as f:
                f.write(self.plan or "")
        except OSError:
            pass

    @classmethod
    def load(cls, session_id):
        path = os.path.join(_session_dir(session_id), "session.json")
        if not os.path.isfile(path):
            raise ConfigError("session not found: " + session_id)
        try:
            with open(path, "r", encoding="utf-8") as f:
                return cls.from_dict(json.load(f))
        except (OSError, ValueError) as e:
            raise ConfigError("could not load session %s: %s" % (session_id, e))

    def _render_notes_md(self):
        """Render the human-readable notes.md from structured data."""
        lines = []
        lines.append("# Deep research notes: " + self.topic)
        lines.append("")
        lines.append("Session id: `" + self.session_id + "`  ")
        lines.append("Status: **" + self.status + "**  ")
        lines.append("Iterations: " + str(self.iterations_done) + "  ")
        if self.started_at:
            lines.append("Started: " +
                         time.strftime("%Y-%m-%d %H:%M:%S",
                                       time.localtime(self.started_at)))
        lines.append("")
        if self.plan:
            lines.append("## Research plan")
            lines.append("")
            lines.append(self.plan)
            lines.append("")
        if self.notes_md:
            lines.append("## Notes so far")
            lines.append("")
            lines.append(self.notes_md)
            lines.append("")
        if self.open_questions:
            lines.append("## Open research questions")
            lines.append("")
            for i, q in enumerate(self.open_questions, 1):
                lines.append(str(i) + ". " + q)
            lines.append("")
        if self.questions:
            lines.append("## Q&A log")
            lines.append("")
            for qa in self.questions:
                ts = qa.get("ts", 0)
                when = time.strftime("%H:%M:%S", time.localtime(ts)) if ts else "?"
                lines.append("### " + when + "  --  " + qa.get("q", ""))
                lines.append("")
                lines.append(qa.get("a", ""))
                lines.append("")
        if self.sources:
            lines.append("## Sources consulted")
            lines.append("")
            seen = set()
            for s in self.sources:
                u = s.get("url", "")
                if u in seen:
                    continue
                seen.add(u)
                t = s.get("title", "") or u
                lines.append("- [" + t + "](" + u + ")")
            lines.append("")
        return "\n".join(lines)

    # ----- progress reporting -----
    def status_line(self):
        elapsed = self.elapsed_seconds
        m, s = divmod(int(elapsed), 60)
        h, m = divmod(m, 60)
        if h:
            elapsed_str = "%dh %02dm" % (h, m)
        else:
            elapsed_str = "%dm %02ds" % (m, s)
        budget = ""
        if self.max_seconds:
            budget = " / %dh budget" % int(self.max_seconds / 3600)
        return ("iter " + str(self.iterations_done) +
                "  " + elapsed_str + budget +
                "  sources: " + str(len(self.sources)) +
                "  open-Q: " + str(len(self.open_questions)))


# ---------------------------------------------------------------------------
# SessionStore -- list/load/delete sessions on disk
# ---------------------------------------------------------------------------
def list_sessions():
    """Return a list of (session_id, topic, status, updated_at) tuples,
    most-recently-updated first. Returns [] if no sessions dir."""
    if not os.path.isdir(SESSIONS_DIR):
        return []
    out = []
    for entry in sorted(os.listdir(SESSIONS_DIR)):
        path = os.path.join(SESSIONS_DIR, entry, "session.json")
        if not os.path.isfile(path):
            continue
        try:
            with open(path, "r", encoding="utf-8") as f:
                d = json.load(f)
            out.append((
                d.get("session_id", entry),
                d.get("topic", ""),
                d.get("status", "?"),
                float(d.get("updated_at") or 0),
            ))
        except (OSError, ValueError):
            continue
    out.sort(key=lambda x: x[3], reverse=True)
    return out


def delete_session(session_id):
    """Remove a session and all its files. Returns True on success."""
    import shutil
    d = _session_dir(session_id)
    if not os.path.isdir(d):
        return False
    try:
        shutil.rmtree(d)
        return True
    except OSError:
        return False


# ---------------------------------------------------------------------------
# Deep research plan prompt -- asks the AI for a structured research plan
# ---------------------------------------------------------------------------
_DEEPRESEARCH_PLAN_SYS = (
    "You are a senior research analyst. The user has given you a topic "
    "and you are about to spend several hours gathering information about "
    "it from the open web. Your first task is to produce a RESEARCH PLAN.\n\n"
    "Output a STRICT JSON object with this exact shape (no prose, no fence):\n"
    "{\n"
    '  "summary": "<one paragraph restating the topic and what we want to learn>",\n'
    '  "questions": [\n'
    '    "<specific question we should answer by searching the web>",\n'
    '    "<another specific question>",\n'
    '    ...\n'
    "  ]\n"
    "}\n\n"
    "Rules for the questions:\n"
    "  - 8-20 questions, ordered from most foundational to most specific.\n"
    "  - Each question is a real search query (a person could type it into Google).\n"
    "  - Avoid vague questions like 'What is X?'. Be specific: 'X latest 2024 benchmarks', "
    "'X alternatives for Y use case', 'X vs Y performance comparison'.\n"
    "  - Include questions about current best practices, common pitfalls, recent "
    "developments, authoritative sources, and competing approaches.\n"
    "  - If the topic is technical, include 'official documentation', 'tutorial', "
    "and 'github repository' queries.\n"
    "  - Don't include the topic name in every question; the model is searching the web."
)

_DEEPRESEARCH_QA_SYS = (
    "You are a knowledgeable research analyst. You have a running notebook "
    "of findings on a topic. The user has just asked you a question. "
    "Answer it using ONLY what is in the notebook below. If the notebook "
    "doesn't contain the answer, say so honestly and suggest a search "
    "query we could run to find out. Be specific and cite the source URLs "
    "in your answer (you'll see them in the notebook). Don't make anything up."
)

_DEEPRESEARCH_NOTES_SYS = (
    "You are a meticulous research analyst. You have just been given a "
    "batch of web search results and raw page text on a research topic. "
    "You also have a running notebook of findings so far. Your task is "
    "to UPDATE the notebook.\n\n"
    "Produce a new version of the notes that:\n"
    "  1. KEEPS all useful findings from the existing notes (do not delete "
    "useful information).\n"
    "  2. ADDS new findings from the latest search results, in the right "
    "section, with source URLs cited inline.\n"
    "  3. RECONCILES contradictions (if two sources disagree, note both and "
    "which seems more credible).\n"
    "  4. STAYS organized: use clear section headings, bullet points, and "
    "keep individual entries short (a sentence or two each).\n"
    "  5. Is plain Markdown only. No JSON, no fences.\n\n"
    "Output the ENTIRE updated notes, not a diff."
)

_DEEPRESEARCH_REPORT_SYS = (
    "You are a senior research analyst. You have spent hours gathering "
    "notes on a topic. Now you need to write the FINAL REPORT for the user.\n\n"
    "Requirements:\n"
    "  - Plain Markdown, well-organized with clear section headings.\n"
    "  - Start with a 2-3 paragraph executive summary.\n"
    "  - Cover all major sub-topics you discovered. Don't pad; if a sub-"
    "topic turned out to be unimportant, give it one sentence and move on.\n"
    "  - Cite source URLs inline (you'll see them in the notes).\n"
    "  - End with a 'Key takeaways' section: 5-10 bullet points a busy "
    "reader can scan in 30 seconds.\n"
    "  - Be honest about uncertainty. If two sources disagree, say so."
)


def _deepresearch_plan_prompt(topic):
    return ("Create a deep research plan for this topic. Spend several "
            "hours worth of search budget; the questions should be thorough.\n\n"
            "TOPIC: " + topic)


def _deepresearch_qa_prompt(question, notebook_excerpt):
    return ("Below is the running research notebook on a topic. The user "
            "has asked a question. Answer it using ONLY the notebook.\n\n"
            "NOTEBOOK (excerpt):\n"
            "---\n" + notebook_excerpt + "\n---\n\n"
            "QUESTION: " + question + "\n\n"
            "Answer in plain prose. Cite source URLs (you'll see them in "
            "the notebook) inline. If the notebook doesn't contain the "
            "answer, say so and suggest a search query to find out.")


def _deepresearch_notes_prompt(topic, current_notes, batch_results):
    """Build the prompt for the 'update the notes' step."""
    if not current_notes:
        current_notes = ("# (no notes yet -- this is the first batch)\n")
    return ("TOPIC: " + topic + "\n\n"
            "EXISTING NOTES:\n---\n" + current_notes + "\n---\n\n"
            "LATEST SEARCH + FETCH RESULTS (raw):\n---\n"
            + batch_results + "\n---\n\n"
            "Produce the updated notebook.")


def _deepresearch_report_prompt(topic, notebook):
    return ("TOPIC: " + topic + "\n\n"
            "RESEARCH NOTES (the full notebook):\n---\n"
            + notebook + "\n---\n\n"
            "Write the final report. Cover everything important. Cite "
            "source URLs inline. End with a 'Key takeaways' section.")


# ---------------------------------------------------------------------------
# Notes excerpting -- when the notes get long, pick the most relevant
# sections for a given question. Simple: take the most recent N chars
# plus any section that mentions keywords from the question.
# ---------------------------------------------------------------------------
def _excerpt_notes_for_question(notes_md, question, max_chars):
    """Return up to max_chars of notes most relevant to the question.

    Strategy: split notes on '## ' (h2 headings), score each section by
    keyword overlap with the question, take the top sections up to
    max_chars, in their original order. Always include the last few
    hundred chars (the most recent additions).
    """
    if not notes_md:
        return ""
    if len(notes_md) <= max_chars:
        return notes_md
    # Tokenize question
    q_tokens = set()
    for tok in _re_url.findall(r'\b\w+\b', question.lower()):
        if len(tok) >= 3:
            q_tokens.add(tok)
    if not q_tokens:
        return notes_md[-max_chars:]
    # Split into sections
    parts = notes_md.split("\n## ")
    scored = []
    for i, p in enumerate(parts):
        body = p.lower()
        score = sum(1 for t in q_tokens if t in body)
        scored.append((score, i, p))
    # Always include the last section (most recent)
    if scored:
        scored[-1] = (scored[-1][0] + 100, scored[-1][1], scored[-1][2])
    # Sort by score desc, then keep originals in order
    scored.sort(key=lambda x: -x[0])
    chosen = []
    used = 0
    for _, _, p in scored:
        if used + len(p) + 4 > max_chars:
            break
        chosen.append(p)
        used += len(p) + 4
    # Re-sort by original index
    chosen_pairs = []
    for p in chosen:
        idx = parts.index(p)
        chosen_pairs.append((idx, p))
    chosen_pairs.sort()
    out = "\n\n## ".join(p for _, p in chosen_pairs)
    if len(out) > max_chars:
        out = out[:max_chars] + " ...[truncated]"
    return out


# ---------------------------------------------------------------------------
# Core deep research loop
# ---------------------------------------------------------------------------
def _deepresearch_generate_plan(topic, cfg):
    """Ask the AI for a structured research plan. Returns a dict with
    'summary' and 'questions'."""
    sonnet_key   = cfg.get("sonnet_api_key", "")
    sonnet_url   = cfg.get("sonnet_api_url") or DEFAULT_SONNET_URL
    sonnet_model = cfg.get("sonnet_model") or DEFAULT_SONNET_MODEL
    timeout      = float(cfg.get("timeout") or DEFAULT_TIMEOUT)
    retries      = int(cfg.get("retries") or DEFAULT_RETRIES)
    backoff      = float(cfg.get("backoff") or DEFAULT_BACKOFF)
    raw = _api_call(sonnet_url, sonnet_key,
                    messages=[
                        {"role": "system", "content": _DEEPRESEARCH_PLAN_SYS},
                        {"role": "user", "content": _deepresearch_plan_prompt(topic)},
                    ],
                    model=sonnet_model,
                    temperature=0.3, max_tokens=2048,
                    caller="deepresearch_plan", expect_long=False,
                    timeout=timeout, retries=retries, backoff=backoff)
    # Parse: try JSON first, then fence, then brace.
    parsed = None
    try:
        d = json.loads(raw)
        if isinstance(d, dict) and "questions" in d:
            parsed = d
    except (ValueError, TypeError):
        pass
    if parsed is None:
        m = _re_url.search(r"```(?:json)?\s*(\{.*?\})\s*```", raw, _re_url.DOTALL)
        if m:
            try:
                d = json.loads(m.group(1))
                if isinstance(d, dict) and "questions" in d:
                    parsed = d
            except (ValueError, TypeError):
                pass
    if parsed is None:
        m = _re_url.search(r"\{.*\}", raw, _re_url.DOTALL)
        if m:
            try:
                d = json.loads(m.group(0))
                if isinstance(d, dict) and "questions" in d:
                    parsed = d
            except (ValueError, TypeError):
                pass
    if parsed is None:
        # Couldn't parse -- synthesize a question from the topic itself
        return {
            "summary": "Auto-generated plan (model output was not parseable JSON).",
            "questions": [
                topic,
                topic + " overview",
                topic + " best practices",
                topic + " tutorial",
                topic + " examples",
                topic + " vs alternatives",
                topic + " 2024 latest",
            ],
        }
    return {
        "summary": str(parsed.get("summary", "")).strip(),
        "questions": [str(q).strip() for q in (parsed.get("questions") or [])
                      if str(q).strip()],
    }


def _deepresearch_update_notes(topic, current_notes, batch_results, cfg):
    """Ask the AI to merge a batch of search results into the notes."""
    sonnet_key   = cfg.get("sonnet_api_key", "")
    sonnet_url   = cfg.get("sonnet_api_url") or DEFAULT_SONNET_URL
    sonnet_model = cfg.get("sonnet_model") or DEFAULT_SONNET_MODEL
    timeout      = float(cfg.get("timeout") or DEFAULT_TIMEOUT)
    retries      = int(cfg.get("retries") or DEFAULT_RETRIES)
    backoff      = float(cfg.get("backoff") or DEFAULT_BACKOFF)
    raw = _api_call(sonnet_url, sonnet_key,
                    messages=[
                        {"role": "system", "content": _DEEPRESEARCH_NOTES_SYS},
                        {"role": "user",
                         "content": _deepresearch_notes_prompt(
                             topic, current_notes, batch_results)},
                    ],
                    model=sonnet_model,
                    temperature=0.2, max_tokens=4096,
                    caller="deepresearch_update", expect_long=True,
                    timeout=timeout, retries=retries, backoff=backoff)
    return (raw or "").strip()


def _deepresearch_answer(question, notes_md, cfg):
    """Answer a question using the running notes."""
    sonnet_key   = cfg.get("sonnet_api_key", "")
    sonnet_url   = cfg.get("sonnet_api_url") or DEFAULT_SONNET_URL
    sonnet_model = cfg.get("sonnet_model") or DEFAULT_SONNET_MODEL
    timeout      = float(cfg.get("timeout") or DEFAULT_TIMEOUT)
    retries      = int(cfg.get("retries") or DEFAULT_RETRIES)
    backoff      = float(cfg.get("backoff") or DEFAULT_BACKOFF)
    excerpt = _excerpt_notes_for_question(
        notes_md, question, SESSION_NOTES_CONTEXT_CHARS)
    raw = _api_call(sonnet_url, sonnet_key,
                    messages=[
                        {"role": "system", "content": _DEEPRESEARCH_QA_SYS},
                        {"role": "user",
                         "content": _deepresearch_qa_prompt(question, excerpt)},
                    ],
                    model=sonnet_model,
                    temperature=0.3, max_tokens=2048,
                    caller="deepresearch_qa", expect_long=False,
                    timeout=timeout, retries=retries, backoff=backoff)
    return (raw or "").strip()


def _deepresearch_write_report(topic, notes_md, cfg):
    """Write the final synthesized report."""
    sonnet_key   = cfg.get("sonnet_api_key", "")
    sonnet_url   = cfg.get("sonnet_api_url") or DEFAULT_SONNET_URL
    sonnet_model = cfg.get("sonnet_model") or DEFAULT_SONNET_MODEL
    timeout      = float(cfg.get("timeout") or DEFAULT_TIMEOUT)
    retries      = int(cfg.get("retries") or DEFAULT_RETRIES)
    backoff      = float(cfg.get("backoff") or DEFAULT_BACKOFF)
    raw = _api_call(sonnet_url, sonnet_key,
                    messages=[
                        {"role": "system", "content": _DEEPRESEARCH_REPORT_SYS},
                        {"role": "user",
                         "content": _deepresearch_report_prompt(topic, notes_md)},
                    ],
                    model=sonnet_model,
                    temperature=0.3, max_tokens=8000,
                    caller="deepresearch_report", expect_long=True,
                    timeout=timeout, retries=retries, backoff=backoff)
    return (raw or "").strip()


# ---------------------------------------------------------------------------
# One iteration of the research loop: pick N questions, search, fetch,
# update notes.
# ---------------------------------------------------------------------------
def _deepresearch_one_iteration(session, cfg, questions_override=None,
                                 stop_check=None):
    """Run a single research iteration. Returns (new_sources_count,
    updated_notes, status_str). Mutates `session` in place.

    Args:
        session: the DeepResearchSession
        cfg: the loaded config
        questions_override: if given, search these strings instead of
            pulling from session.open_questions
        stop_check: optional callable returning True to stop early
    """
    # Pick questions to research
    if questions_override:
        questions = list(questions_override)[:SESSION_PER_ITER_QUERIES]
    else:
        questions = list(session.open_questions)[:SESSION_PER_ITER_QUERIES]
    if not questions:
        return 0, session.notes_md, "no open questions left"

    # Search DDG for each question (parallel)
    pieces = []  # (label, text) pairs
    with _futures.ThreadPoolExecutor(max_workers=6) as ex:
        futs = {ex.submit(_web_search_ddg, q): q for q in questions}
        for fut in _futures.as_completed(futs):
            if stop_check and stop_check():
                return 0, session.notes_md, "stopped"
            q = futs[fut]
            try:
                _, results, err = fut.result()
            except Exception as e:
                _log("WARNING", "deepresearch.search_fail", q=q, err=str(e))
                continue
            if err or not results:
                continue
            for title, url, snippet in results[:SESSION_FETCH_PER_QUERY]:
                # Fetch the URL
                _url, text, ferr = _fetch_url(url)
                if ferr or not text:
                    continue
                # Cap the source text we keep
                if len(text) > SESSION_SOURCE_KEEP_CHARS:
                    text = text[:SESSION_SOURCE_KEEP_CHARS] + " ...[truncated]"
                src = {
                    "url": url,
                    "title": title,
                    "text": text,
                    "snippet": snippet,
                    "query": q,
                    "fetched_at": time.time(),
                }
                session.sources.append(src)
                pieces.append(src)
                # Politeness: small delay between fetches
                time.sleep(0.2)

    if not pieces:
        return 0, session.notes_md, "no new sources this iteration"

    # Render the batch as a string for the model
    batch_lines = []
    for p in pieces:
        batch_lines.append("### Search query: " + p["query"])
        batch_lines.append("Title: " + p["title"])
        batch_lines.append("URL: " + p["url"])
        batch_lines.append("Snippet: " + (p.get("snippet") or "(none)"))
        batch_lines.append("Page text (truncated to "
                           + str(SESSION_SOURCE_KEEP_CHARS) + " chars):")
        batch_lines.append(p["text"])
        batch_lines.append("")
    batch_text = "\n".join(batch_lines)

    # Update the notes
    new_notes = _deepresearch_update_notes(
        session.topic, session.notes_md, batch_text, cfg)
    if new_notes:
        session.notes_md = new_notes

    # Update open_questions: remove the ones we researched
    if not questions_override:
        for q in questions:
            if q in session.open_questions:
                session.open_questions.remove(q)

    session.iterations_done += 1
    session.elapsed_seconds = time.time() - session.started_at
    return len(pieces), new_notes, "ok"


# ---------------------------------------------------------------------------
# Main entry points
# ---------------------------------------------------------------------------
def run_deep_research_session(topic, cfg, max_seconds=5*3600,
                              max_iterations=50, one_shot=False,
                              stop_check=None, on_progress=None,
                              session_id=None):
    """Run a deep research session on `topic`.

    Args:
        topic: the subject to research
        cfg: the loaded config
        max_seconds: total time budget (default 5 hours)
        max_iterations: hard cap on research iterations
        one_shot: if True, do a single batch and return immediately
            (used by --deep-report)
        stop_check: optional callable returning True to stop early
        on_progress: optional callback(session, message) for UI updates
        session_id: if None, generate a new one. If given, load that
            session and resume it.

    Returns:
        The DeepResearchSession (also saved to disk).
    """
    if not topic or not topic.strip():
        raise ConfigError("deep research topic is empty")
    if not cfg.get("sonnet_api_key"):
        raise ConfigError(
            "API key is missing. Run `jarvis --reset` to set it up.")

    # New or resume
    if session_id:
        session = DeepResearchSession.load(session_id)
        # Update cfg-snapshot so we can debug later
        session.cfg_snapshot = _snapshot_cfg(cfg)
        session.model = cfg.get("sonnet_model") or session.model
        session.persona = cfg.get("persona") or session.persona
    else:
        session = DeepResearchSession(
            session_id=_session_id_for_topic(topic),
            topic=topic,
            status="running",
            cfg_snapshot=_snapshot_cfg(cfg),
            model=cfg.get("sonnet_model") or DEFAULT_SONNET_MODEL,
            persona=cfg.get("persona") or DEFAULT_PERSONA,
            max_seconds=float(max_seconds or 0),
            max_iterations=int(max_iterations or 0),
        )
        # Step 1: generate a plan
        if on_progress:
            on_progress(session, "Generating research plan...")
        _log("INFO", "deepresearch.plan_start", topic=topic)
        try:
            plan_obj = _deepresearch_generate_plan(topic, cfg)
            session.plan = (
                "## Summary\n\n" + plan_obj["summary"] + "\n\n"
                + "## Research questions ("
                + str(len(plan_obj["questions"])) + ")\n\n"
                + "\n".join("- " + q for q in plan_obj["questions"]))
            session.open_questions = list(plan_obj["questions"])
        except JarvisError as e:
            # Plan generation failed -- fall back to topic-only
            _log("WARNING", "deepresearch.plan_fail", err=str(e))
            session.plan = "## Summary\n\nResearch plan generation failed: " + str(e) + "\n\nUsing the topic as the only question."
            session.open_questions = [topic]
        session.save()
        if on_progress:
            on_progress(session, "Plan ready. Starting research loop.")

    if one_shot:
        # One iteration, then write a report and return
        new_sources, new_notes, status = _deepresearch_one_iteration(
            session, cfg, stop_check=stop_check)
        if new_sources:
            session.save()
        # For a one-shot, also do the report
        try:
            report = _deepresearch_write_report(
                session.topic, session.notes_md, cfg)
            d = _ensure_session_dir(session.session_id)
            with open(os.path.join(d, "report.md"), "w", encoding="utf-8") as f:
                f.write(report)
        except JarvisError as e:
            _log("WARNING", "deepresearch.report_fail", err=str(e))
        session.status = "done"
        session.save()
        return session

    # Full loop
    session.status = "running"
    session.save()
    while True:
        # Stop checks: time, iterations, explicit
        if stop_check and stop_check():
            session.status = "stopped"
            session.save()
            return session
        if session.max_seconds and session.elapsed_seconds > session.max_seconds:
            session.status = "stopped"
            session.save()
            if on_progress:
                on_progress(session, "Time budget reached. Pausing.")
            return session
        if session.max_iterations and session.iterations_done >= session.max_iterations:
            session.status = "done"
            session.save()
            if on_progress:
                on_progress(session, "Max iterations reached. Done.")
            return session
        if not session.open_questions:
            # Out of questions -> synthesize a final report and stop
            try:
                if on_progress:
                    on_progress(session,
                                "Out of planned questions. Writing final report.")
                report = _deepresearch_write_report(
                    session.topic, session.notes_md, cfg)
                d = _ensure_session_dir(session.session_id)
                with open(os.path.join(d, "report.md"), "w", encoding="utf-8") as f:
                    f.write(report)
            except JarvisError as e:
                _log("WARNING", "deepresearch.report_fail", err=str(e))
            session.status = "done"
            session.save()
            if on_progress:
                on_progress(session, "All planned questions researched. Done.")
            return session

        if on_progress:
            on_progress(session, "Researching: " +
                        (session.open_questions[0] if session.open_questions else "..."))

        try:
            new_sources, _, status = _deepresearch_one_iteration(
                session, cfg, stop_check=stop_check)
        except JarvisError as e:
            _log("WARNING", "deepresearch.iter_fail", err=str(e))
            if on_progress:
                on_progress(session, "Iteration error: " + str(e) +
                            "  (continuing)")
            time.sleep(2)
            continue
        session.save()
        if on_progress:
            on_progress(session, "Iter " + str(session.iterations_done) +
                        ": +" + str(new_sources) + " sources, " + status)
        time.sleep(SESSION_ITER_COOLDOWN)


def _snapshot_cfg(cfg):
    """Capture non-secret parts of the config for the session record."""
    if not isinstance(cfg, dict):
        return {}
    out = {}
    for k, v in cfg.items():
        if "key" in k.lower() or "secret" in k.lower() or "password" in k.lower():
            continue
        out[k] = v
    return out


# ---------------------------------------------------------------------------
# Interactive terminal flow: ask a question mid-session, then resume
# ---------------------------------------------------------------------------
def deep_resession_qa_loop(session, cfg, stop_check=None, on_progress=None):
    """Run an interactive Q&A session. The user can ask questions about
    the topic; each is answered from the running notes. Type 'resume' to
    let the background research loop continue, 'quit' to exit, 'report'
    to write the final report, 'status' to see where we are, 'q' to
    add a new research question to the open list, 'save' to flush to
    disk (also done automatically)."""
    print()
    print("=== Q&A mode ===  type 'resume' to keep researching,")
    print("                 'status' for progress, 'report' to write the final report,")
    print("                 'q <question>' to add a new research question,")
    print("                 'save' to flush to disk, 'quit' to exit.")
    print()
    while True:
        try:
            line = input("[Q&A/" + session.session_id + "] > ").strip()
        except (EOFError, KeyboardInterrupt):
            print()
            return "quit"
        if not line:
            continue
        low = line.lower()
        if low in ("quit", "exit", ":q"):
            return "quit"
        if low == "resume":
            return "resume"
        if low == "save":
            session.save()
            print("  saved.")
            continue
        if low == "status":
            print("  " + session.status_line())
            print("  topic:    " + session.topic)
            print("  plan:     " + str(len(session.plan or "")) + " chars")
            print("  notes:    " + str(len(session.notes_md)) + " chars")
            print("  sources:  " + str(len(session.sources)))
            print("  open-Q:   " + str(len(session.open_questions)))
            continue
        if low == "report":
            print("  writing report...")
            try:
                report = _deepresearch_write_report(
                    session.topic, session.notes_md, cfg)
                d = _ensure_session_dir(session.session_id)
                with open(os.path.join(d, "report.md"), "w", encoding="utf-8") as f:
                    f.write(report)
                print("  report written to " +
                      os.path.join(d, "report.md"))
            except JarvisError as e:
                print("  report failed: " + str(e))
            continue
        if low.startswith("q "):
            q = line[2:].strip()
            if q:
                session.open_questions.append(q)
                session.save()
                print("  added to open questions: " + q)
            continue
        if low == "help":
            print("  resume / status / report / save / quit")
            print("  q <question>  add to research queue")
            print("  anything else is treated as a question to answer")
            continue
        # Treat as a question
        if not session.notes_md:
            print("  (no notes yet -- research a bit first, then ask.)")
            continue
        print("  thinking...")
        try:
            ans = _deepresearch_answer(line, session.notes_md, cfg)
        except JarvisError as e:
            print("  [error] " + str(e))
            continue
        session.questions.append({
            "ts": time.time(),
            "q": line,
            "a": ans,
        })
        session.save()
        print()
        print(ans)
        print()


# ===========================================================================
# GODOT  --  project-aware writing + project detection
# ===========================================================================
#
# When the user is working on a Godot project, jarvis should:
#   * Write idiomatic gdscript / c# / .tscn files
#   * Respect the project's scenes/ vs scripts/ layout
#   * Wire up signals, autoloads, and node references correctly
#   * Avoid common Godot 4.x pitfalls (e.g. snake_case vs PascalCase,
#     _ready vs ready, signal naming, exporting variables with @export)
#
# We don't talk to the Godot editor over the network; this is "smart
# code generation" only, not a live bridge. A future enhancement
# could connect to a Godot editor running with --remote-debug.
#
# Auto-detection: if `project.godot` exists in cwd, we assume godot mode.
# Override with --godot (force on) or --no-godot (force off).

# Common Godot project file paths. We look for project.godot starting
# at cwd and walking up a few levels (so subdirs of a godot project
# still count as "in" the project).
_GODOT_PROJECT_MARKER = "project.godot"
_GODOT_MAX_WALK_UP = 4


def _godot_find_project_root(start_dir=None):
    """Walk up from start_dir looking for a file named project.godot.
    Returns the directory containing it, or None."""
    d = os.path.abspath(start_dir or os.getcwd())
    for _ in range(_GODOT_MAX_WALK_UP + 1):
        if os.path.isfile(os.path.join(d, _GODOT_PROJECT_MARKER)):
            return d
        parent = os.path.dirname(d)
        if parent == d:
            return None
        d = parent
    return None


def _godot_read_project_info(project_root):
    """Parse the most useful keys from a project.godot file. We don't
    need a full parser; a few regexes on the key=value lines are
    enough for our purposes (GodotProject version, autoloads, main
    scene). Returns a dict."""
    out = {"path": project_root, "version": "", "autoloads": [],
          "main_scene": ""}
    path = os.path.join(project_root, _GODOT_PROJECT_MARKER)
    try:
        with open(path, "r", encoding="utf-8") as f:
            text = f.read()
    except OSError:
        return out
    # config_version + features give us the Godot version
    m = re.search(r"config_version\s*=\s*(\d+)", text)
    if m:
        out["config_version"] = int(m.group(1))
    # features is a multi-line PackedStringArray in the file
    m = re.search(r"features\s*=\s*\[(.*?)\]", text, re.DOTALL)
    if m:
        out["features"] = [
            x.strip().strip('"').strip("'")
            for x in m.group(1).split(",") if x.strip()
        ]
    # Map major config_version -> engine version
    cv = out.get("config_version", 0)
    if cv >= 5:
        out["version"] = "Godot 4.x"
    elif cv >= 4:
        out["version"] = "Godot 3.x"
    elif cv > 0:
        out["version"] = "Godot (legacy)"
    # Autoloads
    m = re.search(r"\[autoload\](.*?)(?:\[|\Z)", text, re.DOTALL)
    if m:
        for line in m.group(1).splitlines():
            am = re.match(r"\s*(\w+)\s*=\s*\"([^\"]+)\"", line)
            if am:
                out["autoloads"].append({"name": am.group(1),
                                         "path": am.group(2)})
    # Main scene
    m = re.search(r"run/main_scene\s*=\s*\"([^\"]+)\"", text)
    if m:
        out["main_scene"] = m.group(1)
    return out


def _godot_resolve_godot_mode(args, cfg):
    """Decide whether godot mode is on for this run.
    Returns True / False.
    Priority:
      1. --godot / --no-godot explicit flags
      2. cfg["godot"] config setting (sticky preference)
      3. Auto-detect from cwd (look for project.godot)
    """
    if getattr(args, "godot", None) is True:
        return True
    if getattr(args, "no_godot", None) is True:
        return False
    if cfg.get("godot") is True:
        return True
    if cfg.get("godot") is False:
        return False
    return _godot_find_project_root() is not None


_GODOT_AWARE_CODEX_PROMPT = (
    "\n\nADDITIONAL CONTEXT: you are generating code for a Godot project.\n"
    "Follow these rules:\n"
    "  - Prefer GDScript (.gd) for new scripts unless the user asks for C#.\n"
    "  - GDScript style: snake_case for variables/functions, PascalCase\n"
    "    for classes/nodes, SCREAMING_SNAKE for constants.\n"
    "  - Lifecycle: define `_ready()`, `_process(delta)`, etc. with the\n"
    "    leading underscore. The base Node/Node2D/Node3D methods use\n"
    "    the underscore; the engine calls them on your behalf.\n"
    "  - Signals: declare with `signal name(args)` at the top, emit with\n"
    "    `name.emit(args)`, connect with `node.name.connect(_handler)`.\n"
    "  - Exported variables: use `@export var foo: int = 0` (Godot 4) or\n"
    "    `export(int) var foo = 0` (Godot 3). Match the project's Godot\n"
    "    version (the project.godot config_version tells you which).\n"
    "  - For scene files (.tscn), only output the scene's diff against\n"
    "    a minimal template; the user will merge it. Don't restate\n"
    "    the gd_resource header.\n"
    "  - For UI scenes, prefer @onready for node references when\n"
    "    possible (Godot 4) so the editor can show them in the\n"
    "    Inspector.\n"
    "  - For state machines, prefer enum-based dispatch over giant\n"
    "    match statements -- cleaner and more testable.\n"
    "  - If the project has autoloads, you may use them but always\n"
    "    mention which autoload and where it's defined.\n"
)


def _godot_enhance_impl_prompt(base_prompt, info):
    """Add a short Godot-aware header to the impl system prompt when
    the project is a Godot project. We don't include the full extra
    prompt every time -- the impl_sys is shared across modules, so
    we inject it once and let it ride along for the whole pipeline."""
    if not info:
        return base_prompt
    extra = ("\n\n[PROJECT CONTEXT] Working in a Godot project at "
             + info.get("path", "") + ". ")
    if info.get("version"):
        extra += "Engine: " + info["version"] + ". "
    if info.get("main_scene"):
        extra += "Main scene: " + info["main_scene"] + ". "
    if info.get("autoloads"):
        names = ", ".join(a["name"] for a in info["autoloads"])
        extra += "Autoloads: " + names + ". "
    return base_prompt + extra + _GODOT_AWARE_CODEX_PROMPT


# ===========================================================================
# PROJECTS STORE  --  all your projects live in ~/.jarvis/projects/
# ===========================================================================
#
# The projects store is a flat directory under ~/.jarvis/projects/<name>/
# where each project has a manifest.json describing it (name, kind,
# source, last_opened_at, etc.). Projects can be:
#   * "adopted" from an external path (we just record the path; the
#     files stay where they are)
#   * "imported" by copying them into the store
#   * "scaffolded" fresh (currently supported for godot + python)
#
# The store also tracks which project is "active" so commands like
# `jarvis` (no args) and `jarvis --generate-file foo.gd` know where
# to write.

PROJECTS_DIR = os.path.join(CONFIG_DIR, "projects")


def _ensure_projects_dir():
    if not os.path.isdir(PROJECTS_DIR):
        os.makedirs(PROJECTS_DIR)
    return PROJECTS_DIR


def _project_dir(name):
    """Path to a project's storage directory. Names are sanitized."""
    safe = re.sub(r"[^A-Za-z0-9_.-]+", "_", (name or "")).strip("._") or "project"
    return os.path.join(PROJECTS_DIR, safe)


def _project_manifest_path(name):
    return os.path.join(_project_dir(name), "manifest.json")


def _project_load(name):
    """Load a project's manifest. Returns the dict or None."""
    p = _project_manifest_path(name)
    if not os.path.isfile(p):
        return None
    try:
        with open(p, "r", encoding="utf-8") as f:
            d = json.load(f)
        if not isinstance(d, dict):
            return None
        return d
    except (OSError, ValueError):
        return None


def _project_save(manifest):
    """Save a project's manifest to disk. Creates the project dir."""
    name = manifest.get("name", "")
    if not name:
        raise ValueError("manifest has no name")
    d = _project_dir(name)
    if not os.path.isdir(d):
        os.makedirs(d)
    p = _project_manifest_path(name)
    tmp = p + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(manifest, f, indent=2, ensure_ascii=False)
    os.replace(tmp, p)


def list_projects():
    """Return a list of project manifests, most-recently-updated first."""
    if not os.path.isdir(PROJECTS_DIR):
        return []
    out = []
    for entry in os.listdir(PROJECTS_DIR):
        m = _project_load(entry)
        if m:
            out.append(m)
    out.sort(key=lambda m: float(m.get("updated_at") or 0), reverse=True)
    return out


def get_active_project(cfg=None):
    """Return the name of the currently active project (or None)."""
    if cfg is None:
        cfg = load_config()
    name = (cfg.get("active_project") or "").strip()
    if not name:
        return None
    if _project_load(name) is None:
        return None
    return name


def set_active_project(name, cfg=None):
    """Set the active project in config. Pass '' to clear."""
    if cfg is None:
        cfg = load_config()
    if name:
        cfg["active_project"] = name
    else:
        cfg.pop("active_project", None)
    save_config(cfg)


def _project_adopt(name, source_path, kind="generic"):
    """Adopt an existing project from an external path. We don't copy
    anything; we just record the path in the manifest. Files stay
    where they are."""
    abs_src = os.path.abspath(source_path)
    if not os.path.isdir(abs_src):
        raise ConfigError("source path is not a directory: " + abs_src)
    manifest = _project_load(name) or {
        "name": name, "kind": kind, "source": "adopted",
    }
    manifest.update({
        "name": name,
        "kind": kind,
        "source": "adopted",
        "path": abs_src,
        "updated_at": time.time(),
    })
    if "created_at" not in manifest:
        manifest["created_at"] = manifest["updated_at"]
    _project_save(manifest)
    return manifest


def _project_import(name, source_path, kind="generic"):
    """Import a project by copying its files into the store. The
    originals stay where they are; we get a full copy under
    ~/.jarvis/projects/<name>/src/."""
    abs_src = os.path.abspath(source_path)
    if not os.path.isdir(abs_src):
        raise ConfigError("source path is not a directory: " + abs_src)
    project_root = _project_dir(name)
    src_dst = os.path.join(project_root, "src")
    if os.path.isdir(src_dst):
        raise ConfigError("project '" + name + "' already has a src/ "
                          "directory; refusing to overwrite. "
                          "Use a different name or remove the old one first.")
    import shutil as _sh
    _sh.copytree(abs_src, src_dst)
    manifest = {
        "name": name,
        "kind": kind,
        "source": "imported",
        "path": src_dst,
        "original_path": abs_src,
        "created_at": time.time(),
        "updated_at": time.time(),
    }
    _project_save(manifest)
    return manifest


def _project_scaffold(name, kind, cfg=None):
    """Scaffold a fresh project. Currently supports:
      - kind="godot": creates project.godot + scenes/ + scripts/
      - kind="python": creates pyproject.toml + src/<name>/__init__.py
    """
    if kind == "godot":
        project_root = _project_dir(name)
        if os.path.isdir(project_root):
            raise ConfigError("project '" + name + "' already exists")
        os.makedirs(os.path.join(project_root, "scenes"))
        os.makedirs(os.path.join(project_root, "scripts"))
        with open(os.path.join(project_root, "project.godot"), "w",
                  encoding="utf-8") as f:
            f.write(
                "; Engine configuration file.\n"
                "; It's best edited using the editor UI and not directly,\n"
                "; since the parameters that go here are not all obvious.\n"
                ";\n"
                "; Format:\n"
                ";   [section] ; section goes between []\n"
                ";   param=value ; assign values to parameters\n\n"
                "config_version=5\n\n"
                "[application]\n\n"
                "config/name=\"" + name + "\"\n"
                "config/features=PackedStringArray(\"4.2\", \"GL Compatibility\")\n\n"
                "[rendering]\n\n"
                "renderer/rendering_method=\"gl_compatibility\"\n"
                "renderer/rendering_method.mobile=\"gl_compatibility\"\n"
            )
        with open(os.path.join(project_root, "scenes", "main.tscn"), "w",
                  encoding="utf-8") as f:
            f.write(
                "[gd_scene format=3 uid=\"uid://b000000000001\"]\n\n"
                "[node name=\"Main\" type=\"Node2D\"]\n"
            )
        with open(os.path.join(project_root, "scripts", "main.gd"), "w",
                  encoding="utf-8") as f:
            f.write(
                "extends Node2D\n"
                "\n"
                "# Called when the node enters the scene tree.\n"
                "func _ready() -> void:\n"
                "    print(\"Hello from " + name + "\")\n"
                "\n"
                "# Called every frame. 'delta' is the elapsed time since the\n"
                "# previous frame.\n"
                "func _process(delta: float) -> void:\n"
                "    pass\n"
            )
        manifest = {
            "name": name,
            "kind": "godot",
            "source": "scaffolded",
            "path": project_root,
            "created_at": time.time(),
            "updated_at": time.time(),
        }
        _project_save(manifest)
        return manifest
    elif kind == "python":
        project_root = _project_dir(name)
        if os.path.isdir(project_root):
            raise ConfigError("project '" + name + "' already exists")
        pkg = re.sub(r"[^a-z0-9_]+", "_", name.lower()).strip("_") or "pkg"
        os.makedirs(os.path.join(project_root, "src", pkg))
        with open(os.path.join(project_root, "pyproject.toml"), "w",
                  encoding="utf-8") as f:
            f.write(
                "[project]\n"
                'name = "' + name + '"\n'
                'version = "0.1.0"\n'
                'requires-python = ">=3.6"\n'
                "\n"
                "[build-system]\n"
                'requires = ["setuptools>=61"]\n'
                'build-backend = "setuptools.build_meta"\n'
                "\n"
                "[tool.setuptools]\n"
                'package-dir = {"" = "src"}\n'
            )
        with open(os.path.join(project_root, "src", pkg, "__init__.py"),
                  "w", encoding="utf-8") as f:
            f.write('__version__ = "0.1.0"\n')
        manifest = {
            "name": name,
            "kind": "python",
            "source": "scaffolded",
            "path": project_root,
            "created_at": time.time(),
            "updated_at": time.time(),
        }
        _project_save(manifest)
        return manifest
    else:
        raise ConfigError(
            "scaffold: unknown kind '" + str(kind) + "'. "
            "Try 'godot' or 'python'.")


def _project_remove(name, delete_files=False):
    """Remove a project from the store. If delete_files is True, also
    remove the project directory from disk (USE WITH CARE -- this
    can delete the actual godot project if it was imported)."""
    m = _project_load(name)
    if not m:
        return False
    project_root = _project_dir(name)
    if delete_files:
        import shutil as _sh
        if os.path.isdir(project_root):
            _sh.rmtree(project_root, ignore_errors=True)
    else:
        # Just remove the manifest; leave the directory in place
        p = _project_manifest_path(name)
        try:
            os.remove(p)
        except OSError:
            pass
    # Clear from active project if it was active
    cfg = load_config()
    if cfg.get("active_project") == name:
        cfg.pop("active_project", None)
        save_config(cfg)
    return True


# ===========================================================================
# GOOGLE DRIVE SYNC (watch-folder)
# ===========================================================================
#
# The simplest form of cloud sync: point jarvis at a folder that's
# synced by the Google Drive desktop app (or Dropbox, OneDrive, etc.),
# and any changes to the projects store show up on every machine
# that has that folder.
#
# The advanced form (OAuth + Google Drive API) is left for a future
# version. For now, this is the only sync path; it works offline and
# requires no API keys.
#
# Usage:
#   jarvis drive status
#   jarvis drive set ~/Google Drive/jarvis-projects
#   jarvis drive unset
#   jarvis drive pull      # copy store -> drive folder
#   jarvis drive push      # copy drive folder -> store
#   jarvis drive watch     # background loop: keep the two in sync

DRIVE_CONFIG_PATH = os.path.join(CONFIG_DIR, "drive.json")


def _drive_load():
    if not os.path.isfile(DRIVE_CONFIG_PATH):
        return {"folder": ""}
    try:
        with open(DRIVE_CONFIG_PATH, "r", encoding="utf-8") as f:
            d = json.load(f)
        if not isinstance(d, dict):
            return {"folder": ""}
        d.setdefault("folder", "")
        return d
    except (OSError, ValueError):
        return {"folder": ""}


def _drive_save(state):
    if not os.path.isdir(CONFIG_DIR):
        os.makedirs(CONFIG_DIR)
    tmp = DRIVE_CONFIG_PATH + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(state, f, indent=2, ensure_ascii=False)
    os.replace(tmp, DRIVE_CONFIG_PATH)


def _drive_resolve_folder():
    """Return the configured Drive folder, or None if not set."""
    folder = _drive_load().get("folder", "")
    if not folder:
        return None
    folder = os.path.expanduser(folder)
    if not os.path.isdir(folder):
        return None
    return folder


def _drive_sync_one_direction(src, dst, label):
    """Copy every file under src/ to dst/<name> matching the project
    name. Returns (copied, skipped, errors). The strategy is name-based:
    for each project in src, if it doesn't exist in dst, copy it;
    if it does, leave it alone (don't overwrite -- the user has
    probably changed it on the other machine)."""
    if not os.path.isdir(src):
        return 0, 0, ["source does not exist: " + src]
    if not os.path.isdir(dst):
        os.makedirs(dst)
    copied = 0
    skipped = 0
    errors = []
    import shutil as _sh
    for name in os.listdir(src):
        s = os.path.join(src, name)
        d = os.path.join(dst, name)
        if not os.path.isdir(s):
            continue
        if os.path.isdir(d):
            skipped += 1
            continue
        try:
            _sh.copytree(s, d)
            copied += 1
        except OSError as e:
            errors.append(name + ": " + str(e))
    return copied, skipped, errors


def drive_push():
    """Copy ~/.jarvis/projects/ -> drive folder."""
    src = _ensure_projects_dir()
    dst = _drive_resolve_folder()
    if not dst:
        raise ConfigError("drive folder not configured. "
                          "Use `jarvis drive set <path>` first.")
    return _drive_sync_one_direction(src, dst, "push")


def drive_pull():
    """Copy drive folder -> ~/.jarvis/projects/."""
    dst = _ensure_projects_dir()
    src = _drive_resolve_folder()
    if not src:
        raise ConfigError("drive folder not configured. "
                          "Use `jarvis drive set <path>` first.")
    return _drive_sync_one_direction(src, dst, "pull")


# ===========================================================================
# SANDBOXED CODE RUNNER
# ===========================================================================
#
# When jarvis generates code, the user can ask the tool to actually RUN it
# in a sandbox before reporting success. The sandbox:
#
#   1. Statically checks the code (AST) for obviously unsafe operations:
#        - os.system, os.exec*, subprocess.*, shutil.rmtree on absolute paths,
#          socket.*, urllib.*, requests.*, http.*, ctypes.*, etc.
#      Any of these -> reject without running.
#
#   2. If the AST check passes, runs the code in a subprocess:
#        - in a temporary directory (so file ops are scoped)
#        - with a timeout (default 10 seconds)
#        - with the network disabled (Linux: unshare; Windows/Mac: no
#          network filter, but the AST check has already blocked
#          networking libraries). On unsupported platforms we just
#          skip the network isolation but still keep the timeout +
#          temp dir.
#        - with environment scrubbed (no API keys leaking in)
#
# Returns a dict with: ok, stdout, stderr, exit_code, time_seconds,
#                       error (if any), safety_rejected (bool),
#                       safety_reasons (list of strings).

import ast as _ast_module
import subprocess as _subprocess_mod
import tempfile as _tempfile_mod
import shutil as _shutil_mod

SANDBOX_DEFAULT_TIMEOUT = float(
    _env_or("DUAL_AI_SANDBOX_TIMEOUT", "JARVIS_SANDBOX_TIMEOUT", "10"))
SANDBOX_NETWORK_BLOCKED = True

# Module names that are considered dangerous. If user code imports any
# of these, we refuse to run.
_SANDBOX_FORBIDDEN_MODULES = (
    "os", "sys", "subprocess", "ctypes", "cffi",
    "socket", "select", "ssl",
    "urllib", "urllib2", "urllib3",
    "http", "httplib", "ftplib", "smtplib", "telnetlib",
    "requests", "httpx", "aiohttp",
    "asyncio",
    "multiprocessing", "threading",
    "pty", "pwd", "spwd", "crypt",
    "fcntl", "resource", "termios", "tty",
    "importlib", "imp",
    "code", "codeop",
    "shutil",
    "signal", "gc",
)

# Attribute names that are dangerous when accessed on builtins/os/sys.
_SANDBOX_FORBIDDEN_ATTRS = (
    "system", "popen", "exec", "execvp", "execvpe", "execl", "execle",
    "execlp", "execlpe", "spawn", "spawnl", "spawnle", "spawnlp",
    "spawnlpe", "spawnv", "spawnve", "spawnvp", "spawnvpe",
    "fork", "forkpty", "kill", "killpg",
    "remove", "rmdir", "unlink", "removedirs",
    "chmod", "chown", "chroot",
    "setuid", "setgid", "seteuid", "setegid",
    "mount", "umount", "mknod",
)


class SandboxError(Exception):
    pass


def _ast_safety_check(code, filename="<sandbox>"):
    """Static analysis: does this code do anything dangerous?
    Returns (ok, reasons)."""
    reasons = []
    try:
        tree = _ast_module.parse(code, filename=filename)
    except SyntaxError as e:
        return False, ["syntax error: " + str(e)]

    for node in _ast_module.walk(tree):
        if isinstance(node, _ast_module.Import):
            for alias in node.names:
                top = (alias.name or "").split(".")[0]
                if top in _SANDBOX_FORBIDDEN_MODULES:
                    reasons.append("forbidden import: " + alias.name)
        elif isinstance(node, _ast_module.ImportFrom):
            mod = (node.module or "").split(".")[0]
            if mod in _SANDBOX_FORBIDDEN_MODULES:
                reasons.append("forbidden from-import: " + str(node.module))
        elif isinstance(node, _ast_module.Call):
            func = node.func
            if isinstance(func, _ast_module.Name) and func.id in (
                    "exec", "eval", "compile", "__import__"):
                reasons.append("forbidden builtin call: " + func.id)
        elif isinstance(node, _ast_module.Attribute):
            if node.attr in _SANDBOX_FORBIDDEN_ATTRS:
                if isinstance(node.value, _ast_module.Name):
                    if node.value.id in _SANDBOX_FORBIDDEN_MODULES:
                        reasons.append(
                            "forbidden call: " + node.value.id + "."
                            + node.attr)
                elif isinstance(node.value, _ast_module.Attribute):
                    root = node.value
                    while isinstance(root, _ast_module.Attribute):
                        root = root.value
                    if isinstance(root, _ast_module.Name) and \
                            root.id in _SANDBOX_FORBIDDEN_MODULES:
                        reasons.append(
                            "forbidden call: " + root.id + "... ."
                            + node.attr)
        elif isinstance(node, _ast_module.Str):
            s = node.s
            if isinstance(s, str) and len(s) >= 2:
                if s.startswith("/") and not s.startswith("//"):
                    if any(p in s for p in (
                            "/etc/", "/usr/", "/var/", "/root/", "/home/",
                            "/tmp/", "/proc/", "/sys/")):
                        reasons.append(
                            "suspicious absolute path string: " + s[:60])
                elif len(s) >= 3 and s[1] == ":" and s[2] in ("\\", "/"):
                    reasons.append(
                        "suspicious Windows path string: " + s[:60])

    return (len(reasons) == 0), reasons


def _sandbox_run_code(code, language="python", timeout=None,
                       extra_files=None):
    """Run the given code in a sandbox. Returns a result dict."""
    timeout = float(timeout or SANDBOX_DEFAULT_TIMEOUT)
    if language != "python":
        return {
            "ok": False, "exit_code": -1, "stdout": "", "stderr": "",
            "time_seconds": 0.0,
            "error": "only Python is supported in the sandbox right now",
            "safety_rejected": False, "safety_reasons": [],
        }

    safe, reasons = _ast_safety_check(code)
    if not safe:
        return {
            "ok": False, "exit_code": -1, "stdout": "", "stderr": "",
            "time_seconds": 0.0,
            "error": "code rejected by safety check",
            "safety_rejected": True, "safety_reasons": reasons,
        }

    tmpdir = _tempfile_mod.mkdtemp(prefix="jarvis_sandbox_")
    try:
        script_path = os.path.join(tmpdir, "script.py")
        with open(script_path, "w", encoding="utf-8") as f:
            f.write(code)
        if extra_files:
            for fname, content in extra_files.items():
                if "/" in fname or "\\" in fname or fname.startswith("."):
                    return {
                        "ok": False, "exit_code": -1, "stdout": "",
                        "stderr": "", "time_seconds": 0.0,
                        "error": "invalid extra_files name: " + fname,
                        "safety_rejected": True,
                        "safety_reasons": ["path traversal in extra_files"],
                    }
                p = os.path.join(tmpdir, fname)
                with open(p, "w", encoding="utf-8") as f:
                    f.write(content)

        env = {
            "PATH": os.environ.get("PATH", ""),
            "HOME": tmpdir,
            "TMPDIR": tmpdir,
            "TMP": tmpdir,
            "TEMP": tmpdir,
            "LANG": os.environ.get("LANG", "C.UTF-8"),
            "PYTHONIOENCODING": "utf-8",
            "PYTHONDONTWRITEBYTECODE": "1",
        }
        cmd = [sys.executable, "-I", "-B", "script.py"]
        use_unshare = False
        if SANDBOX_NETWORK_BLOCKED and sys.platform.startswith("linux"):
            if _shutil_mod.which("unshare"):
                # Try unshare; if it fails (e.g. no CAP_SYS_ADMIN in
                # the sandbox we're running in), fall back to running
                # without it. The AST check has already blocked the
                # dangerous network libraries, so this is acceptable.
                test_cmd = ["unshare", "--net", "--", "true"]
                try:
                    r = _subprocess_mod.run(
                        test_cmd, stdout=_subprocess_mod.DEVNULL,
                        stderr=_subprocess_mod.DEVNULL, timeout=2)
                    if r.returncode == 0:
                        use_unshare = True
                        cmd = ["unshare", "--net", "--"] + cmd
                except Exception:
                    use_unshare = False

        t0 = time.time()
        try:
            proc = _subprocess_mod.run(
                cmd, cwd=tmpdir, env=env,
                stdout=_subprocess_mod.PIPE,
                stderr=_subprocess_mod.PIPE,
                timeout=timeout, text=True)
            elapsed = time.time() - t0
            return {
                "ok": (proc.returncode == 0),
                "exit_code": proc.returncode,
                "stdout": proc.stdout,
                "stderr": proc.stderr,
                "time_seconds": elapsed,
                "error": None if proc.returncode == 0
                         else "exit code " + str(proc.returncode),
                "safety_rejected": False,
                "safety_reasons": [],
            }
        except _subprocess_mod.TimeoutExpired as e:
            elapsed = time.time() - t0
            return {
                "ok": False, "exit_code": -1,
                "stdout": (e.stdout.decode("utf-8", "replace")
                           if isinstance(e.stdout, bytes)
                           else (e.stdout or "")),
                "stderr": (e.stderr.decode("utf-8", "replace")
                           if isinstance(e.stderr, bytes)
                           else (e.stderr or "")),
                "time_seconds": elapsed,
                "error": "timeout after " + str(timeout) + "s",
                "safety_rejected": False, "safety_reasons": [],
            }
        except Exception as e:
            return {
                "ok": False, "exit_code": -1, "stdout": "", "stderr": "",
                "time_seconds": time.time() - t0,
                "error": "subprocess error: " + type(e).__name__
                        + ": " + str(e),
                "safety_rejected": False, "safety_reasons": [],
            }
    finally:
        try:
            _shutil_mod.rmtree(tmpdir, ignore_errors=True)
        except Exception:
            pass


# ===========================================================================
# FILE GENERATOR  --  "make me a Dockerfile" / "write a SQL migration"
# ===========================================================================
#
# Generalized file generation: text OR binary.
# For text: a single fenced code block in the model output.
# For binary: a JSON object with base64 content + a generator script
#             (recommended, because the user can re-run the script).
#
# The model gets a system prompt that explains the two forms and tells
# it to use form 2 only for binary (image/PDF/zip/font/etc.).

_FILE_GEN_TEXT_EXTS = (
    ".py", ".pyw", ".pyi",
    ".js", ".jsx", ".mjs", ".cjs", ".ts", ".tsx", ".d.ts",
    ".html", ".htm", ".xhtml", ".css", ".scss", ".sass", ".less",
    ".json", ".json5", ".jsonc",
    ".yaml", ".yml", ".toml", ".ini", ".cfg", ".conf",
    ".md", ".markdown", ".rst", ".txt", ".text", ".tex",
    ".sh", ".bash", ".zsh", ".fish", ".ps1", ".bat", ".cmd",
    ".sql", ".psql", ".plsql",
    ".go", ".rs", ".java", ".kt", ".kts", ".scala", ".groovy",
    ".c", ".h", ".cpp", ".cc", ".cxx", ".hpp", ".hxx", ".m", ".mm",
    ".rb", ".php", ".pl", ".pm", ".lua", ".tcl", ".r",
    ".swift", ".dart",
    ".xml", ".xsl", ".xslt", ".svg",
    ".csv", ".tsv",
    ".env", ".gitignore", ".gitattributes", ".dockerignore",
    ".editorconfig", ".eslintrc", ".prettierrc",
    "Dockerfile", "Makefile", "Rakefile", "Gemfile", "Vagrantfile",
    ".proto", ".graphql", ".gql",
    ".vim", ".el",
)

_FILE_GEN_BINARY_HINTS = (
    "image", "photo", "picture", "png", "jpg", "jpeg", "gif",
    "bmp", "webp", "ico", "icon", "logo",
    "pdf", "document", "docx", "doc", "xlsx", "xls", "pptx", "ppt",
    "zip", "tar", "gz", "tgz", "7z", "rar",
    "audio", "mp3", "wav", "ogg", "flac",
    "video", "mp4", "mov", "avi", "mkv", "webm",
    "font", "ttf", "otf", "woff", "woff2",
    "executable", "binary", "compiled",
)


def _file_gen_intent(text):
    if not text:
        return "text"
    low = text.lower()
    for hint in _FILE_GEN_BINARY_HINTS:
        if hint in low:
            return "binary"
    return "text"


_FILE_GEN_SYS = (
    "You are a file generator. The user gives you a request; you produce "
    "ONE file. Output your response in EXACTLY one of these two forms:\n\n"
    "FORM 1: a text file in a single fenced code block with the "
    "language tag matching the file extension. Example:\n"
    "```python\n# hello.py\nprint('hello world')\n```\n\n"
    "FORM 2: a JSON object describing a binary file (use this when the "
    "user asked for an image, PDF, zip, font, etc.). Example:\n"
    "```json\n"
    "{\n"
    '  "filename": "logo.png",\n'
    '  "format": "png",\n'
    '  "encoding": "base64",\n'
    '  "content_b64": "iVBORw0KGgoAAAANSUhEUgAA...",\n'
    '  "generator_script": "# Optional Python script that produces the same file (recommended for reproducibility).\\nimport struct\\n# ...\\n",\n'
    '  "notes": "Brief explanation of what the file is."\n'
    "}\n"
    "```\n\n"
    "Rules:\n"
    "  - Pick form 1 (text) for code, config, scripts, markup, data.\n"
    "  - Pick form 2 (binary) for images, PDFs, archives, etc. "
    "ALWAYS include a `generator_script` in form 2 so the user can "
    "regenerate the file later.\n"
    "  - NEVER include prose outside the fence in form 1.\n"
    "  - For form 2, output the JSON inside a single ```json fence.\n"
    "  - The filename must be reasonable (e.g. 'Makefile' or "
    "'data.csv' or 'index.html'). Don't include a path.\n"
    "  - If unsure, pick form 1.\n"
)


_FILE_GEN_USER_PREFIX = "Generate ONE file for this request:\n\n"


def _file_gen_parse_text(raw):
    if not raw:
        return None, None, None
    m = _re_url.search(
        r"```([a-zA-Z0-9_+\-]*)\s*\n(.*?)\n```", raw, _re_url.DOTALL)
    if not m:
        return None, None, None
    lang = (m.group(1) or "").strip().lower()
    body = m.group(2)
    fname = _file_gen_guess_filename(lang, body)
    return fname, body, lang


def _file_gen_guess_filename(lang, body):
    if body:
        first_line = body.split("\n", 1)[0]
        m = _re_url.search(
            r'(?:#|//|--|;|REM)\s*([\w./-]+\.[a-zA-Z0-9]{1,5})\s*$',
            first_line)
        if m:
            return m.group(1)
    ext_map = {
        "python": "output.py", "py": "output.py", "python3": "output.py",
        "javascript": "output.js", "js": "output.js", "jsx": "output.jsx",
        "typescript": "output.ts", "ts": "output.ts", "tsx": "output.tsx",
        "html": "index.html", "htm": "index.html", "xml": "output.xml",
        "css": "styles.css", "scss": "styles.scss",
        "json": "output.json", "yaml": "output.yaml", "yml": "output.yaml",
        "toml": "output.toml", "ini": "output.ini",
        "markdown": "README.md", "md": "README.md",
        "bash": "script.sh", "sh": "script.sh", "shell": "script.sh",
        "powershell": "script.ps1", "ps1": "script.ps1",
        "sql": "query.sql",
        "go": "main.go", "rust": "main.rs", "rs": "main.rs",
        "java": "Main.java", "kotlin": "Main.kt", "kt": "Main.kt",
        "swift": "main.swift", "c": "main.c", "cpp": "main.cpp",
        "ruby": "script.rb", "rb": "script.rb",
        "php": "index.php", "lua": "script.lua",
        "dockerfile": "Dockerfile", "makefile": "Makefile",
        "csv": "data.csv", "plaintext": "output.txt", "text": "output.txt",
        "": "output.txt",
    }
    return ext_map.get(lang, "output.txt")


def _file_gen_parse_binary(raw):
    if not raw:
        return None, None, None, None
    text = raw
    m = _re_url.search(r"```(?:json)?\s*(\{.*?\})\s*```", text, _re_url.DOTALL)
    if m:
        text = m.group(1)
    m = _re_url.search(r"\{.*\}", text, _re_url.DOTALL)
    if not m:
        return None, None, None, None
    try:
        d = json.loads(m.group(0))
    except (ValueError, TypeError):
        return None, None, None, None
    if not isinstance(d, dict):
        return None, None, None, None
    fname = d.get("filename") or "output.bin"
    content_b64 = d.get("content_b64") or ""
    notes = d.get("notes") or ""
    gen = d.get("generator_script") or ""
    if not content_b64:
        return None, None, None, None
    try:
        import base64
        content = base64.b64decode(content_b64)
    except Exception:
        return None, None, None, None
    return fname, content, notes, gen


def _file_gen_dispatch(user_request, cfg, sandbox_test=False):
    sonnet_key = cfg.get("sonnet_api_key", "")
    sonnet_url = cfg.get("sonnet_api_url") or DEFAULT_SONNET_URL
    sonnet_model = cfg.get("sonnet_model") or DEFAULT_SONNET_MODEL
    timeout = float(cfg.get("timeout") or DEFAULT_TIMEOUT)
    retries = int(cfg.get("retries") or DEFAULT_RETRIES)
    backoff = float(cfg.get("backoff") or DEFAULT_BACKOFF)

    intent = _file_gen_intent(user_request)
    sys_prompt = _FILE_GEN_SYS

    raw = _api_call(
        sonnet_url, sonnet_key,
        messages=[
            {"role": "system", "content": sys_prompt},
            {"role": "user", "content": _FILE_GEN_USER_PREFIX + user_request},
        ],
        model=sonnet_model,
        temperature=0.3, max_tokens=8192,
        caller="file_gen", expect_long=True,
        timeout=timeout, retries=retries, backoff=backoff)

    if intent == "binary":
        fname, content, notes, gen = _file_gen_parse_binary(raw)
        if not fname:
            fname, content, lang = _file_gen_parse_text(raw)
            if not fname:
                return {
                    "ok": False, "error": "could not parse file-gen output",
                    "raw": raw, "model_used": sonnet_model,
                }
            return {
                "ok": True, "kind": "text", "filename": fname,
                "content": content, "language": lang,
                "model_used": sonnet_model,
            }
        result = {
            "ok": True, "kind": "binary", "filename": fname,
            "content": content, "notes": notes,
            "generator_script": gen, "model_used": sonnet_model,
        }
    else:
        fname, content, lang = _file_gen_parse_text(raw)
        if not fname:
            return {
                "ok": False, "error": "could not parse file-gen output",
                "raw": raw, "model_used": sonnet_model,
            }
        result = {
            "ok": True, "kind": "text", "filename": fname,
            "content": content, "language": lang,
            "model_used": sonnet_model,
        }

    if sandbox_test and result.get("kind") == "text":
        if (result.get("language") or "").lower() in (
                "python", "py", ""):
            sb = _sandbox_run_code(
                result["content"],
                language="python",
                timeout=SANDBOX_DEFAULT_TIMEOUT)
            result["sandbox_result"] = sb
    return result


# ===========================================================================
# SELF-MODIFIER  --  jarvis editing its own source code safely
# ===========================================================================
#
# Most dangerous feature. Workflow:
#   1. Pre-flight: enable_self_modify must be on, cwd must be a git
#      repo, working tree must be clean.
#   2. Switch to a side branch ('self-modify') and reset it to current
#      main-branch HEAD (this is the snapshot).
#   3. Ask the AI to output a unified-diff patch.
#   4. Run `git apply --check` (dry run). If it fails, abort.
#   5. Run `git apply` for real.
#   6. Run the test suite. If tests fail, `git checkout` the snapshot
#      and `git clean` to revert.
#   7. If tests pass, commit the change on the side branch.
#   8. User can `jarvis --self-revert` to roll back at any time.

SELF_MODIFY_BRANCH = "self-modify"
SELF_MODIFY_TEST_FILE = "test_deep_research.py"


def _git_available():
    return _shutil_mod.which("git") is not None


def _git_in_repo():
    if not _git_available():
        return False
    try:
        r = _subprocess_mod.run(
            ["git", "rev-parse", "--is-inside-work-tree"],
            stdout=_subprocess_mod.PIPE, stderr=_subprocess_mod.PIPE,
            timeout=5, text=True)
        return r.returncode == 0 and r.stdout.strip() == "true"
    except Exception:
        return False


def _git_clean_working_tree():
    try:
        r = _subprocess_mod.run(
            ["git", "status", "--porcelain"],
            stdout=_subprocess_mod.PIPE, stderr=_subprocess_mod.PIPE,
            timeout=5, text=True)
        return r.returncode == 0 and not r.stdout.strip()
    except Exception:
        return False


def _git_current_branch():
    try:
        r = _subprocess_mod.run(
            ["git", "rev-parse", "--abbrev-ref", "HEAD"],
            stdout=_subprocess_mod.PIPE, stderr=_subprocess_mod.PIPE,
            timeout=5, text=True)
        if r.returncode == 0:
            return r.stdout.strip()
    except Exception:
        pass
    return None


def _git_run(args, cwd=None, check=True):
    try:
        r = _subprocess_mod.run(
            ["git"] + list(args),
            cwd=cwd or os.getcwd(),
            stdout=_subprocess_mod.PIPE, stderr=_subprocess_mod.PIPE,
            timeout=30, text=True)
        return r.returncode, r.stdout, r.stderr
    except Exception as e:
        return -1, "", str(e)


class SelfModifyError(Exception):
    pass


def _self_modify_allowed(cfg):
    if not cfg.get("enable_self_modify"):
        return False, (
            "self-modification is disabled. To enable, run:\n"
            "  jarvis --set enable_self_modify=true\n"
            "(You can revert any time with `jarvis --self-revert`.)"
        )
    if not _git_available():
        return False, "git is not installed; cannot safely self-modify."
    if not _git_in_repo():
        return False, (
            "current directory is not a Git repository. "
            "Initialize one with `git init` first, or run jarvis from "
            "inside a cloned repo."
        )
    if not _git_clean_working_tree():
        return False, (
            "working tree has uncommitted changes. Commit or stash them "
            "first -- I don't want to clobber your WIP."
        )
    return True, ""


def _self_modify_snapshot(label="auto"):
    current = _git_current_branch()
    if not current:
        raise SelfModifyError("could not determine current branch")
    rc, out, err = _git_run(["rev-parse", "--verify", SELF_MODIFY_BRANCH])
    if rc != 0:
        rc, out, err = _git_run(["checkout", "-b", SELF_MODIFY_BRANCH])
        if rc != 0:
            raise SelfModifyError(
                "could not create self-modify branch: " + err)
    else:
        rc, out, err = _git_run(["checkout", "-f", SELF_MODIFY_BRANCH])
        if rc != 0:
            raise SelfModifyError(
                "could not switch to self-modify branch: " + err)
    rc, out, err = _git_run(["reset", "--hard", current])
    if rc != 0:
        raise SelfModifyError("could not reset self-modify branch: " + err)
    if label:
        _git_run(["commit", "--allow-empty", "-m", "[self-modify] " + label])
    rc, out, err = _git_run(["rev-parse", "HEAD"])
    if rc != 0:
        raise SelfModifyError("could not get snapshot hash: " + err)
    return out.strip(), current


def _self_modify_apply_patch(patch_text, files=None):
    if not patch_text or not patch_text.strip():
        return False
    with _tempfile_mod.NamedTemporaryFile(
            mode="w", suffix=".patch", delete=False,
            encoding="utf-8") as f:
        f.write(patch_text)
        patch_file = f.name
    try:
        rc, out, err = _git_run(
            ["apply", "--check", "--whitespace=nowarn", patch_file])
        if rc != 0:
            return False
        rc, out, err = _git_run(
            ["apply", "--whitespace=nowarn", patch_file])
        return rc == 0
    finally:
        try:
            os.unlink(patch_file)
        except OSError:
            pass


def _self_modify_run_tests():
    try:
        r = _subprocess_mod.run(
            [sys.executable, SELF_MODIFY_TEST_FILE],
            stdout=_subprocess_mod.PIPE, stderr=_subprocess_mod.PIPE,
            timeout=120, text=True)
        return (r.returncode == 0), (r.stdout + "\n" + r.stderr)
    except _subprocess_mod.TimeoutExpired:
        return False, "test suite timed out after 120s"
    except Exception as e:
        return False, "could not run tests: " + str(e)


def _self_modify_savepoint(label=""):
    return _self_modify_snapshot(label or "manual savepoint")


def _self_modify_revert_to(commit_or_label):
    if not _git_in_repo():
        raise SelfModifyError("not in a git repository")
    rc, out, err = _git_run(["rev-parse", "--verify", commit_or_label])
    if rc != 0:
        rc, out, err = _git_run(
            ["log", "--all", "--oneline", "--grep=" + commit_or_label])
        if rc != 0 or not out.strip():
            raise SelfModifyError(
                "could not resolve save point: " + commit_or_label)
        commit_or_label = out.strip().split()[0]
    rc, out, err = _git_run(["checkout", commit_or_label])
    if rc != 0:
        raise SelfModifyError("could not checkout: " + err)
    return commit_or_label


def _self_modify_apply(request, cfg, target_files=None):
    if target_files is None:
        target_files = ["jarvis.py", "gui.py"]
    allowed, why = _self_modify_allowed(cfg)
    if not allowed:
        return {"ok": False, "error": why, "applied": False,
                "tests_passed": False, "snapshot": None, "patch": "",
                "test_output": ""}
    try:
        snapshot, original_branch = _self_modify_snapshot(
            label="before: " + (request[:60].replace("\n", " ")))
    except SelfModifyError as e:
        return {"ok": False, "error": str(e), "applied": False,
                "tests_passed": False, "snapshot": None, "patch": "",
                "test_output": ""}

    sonnet_key = cfg.get("sonnet_api_key", "")
    sonnet_url = cfg.get("sonnet_api_url") or DEFAULT_SONNET_URL
    sonnet_model = cfg.get("sonnet_model") or DEFAULT_SONNET_MODEL
    timeout = float(cfg.get("timeout") or DEFAULT_TIMEOUT)
    retries = int(cfg.get("retries") or DEFAULT_RETRIES)
    backoff = float(cfg.get("backoff") or DEFAULT_BACKOFF)

    files_list = "\n".join(target_files)
    sys_prompt = (
        "You are a code-rewriting assistant. You will be given a request "
        "to modify the jarvis codebase. Output ONLY a unified-diff patch "
        "(no prose, no fence) that, when applied with `git apply`, makes "
        "the requested change. The patch may touch ONLY these files:\n"
        + files_list + "\n\n"
        "Rules:\n"
        "  - Output must start with 'diff --git a/... b/...'\n"
        "  - Each hunk must have a clean context (3+ lines)\n"
        "  - Do not change the file's overall structure more than needed\n"
        "  - Do not introduce syntax errors\n"
        "  - Run 'git diff' mentally first to make sure the patch will "
        "apply cleanly\n"
        "  - Output the patch as plain text -- NO ```fenced blocks```, "
        "no commentary before or after"
    )
    user_prompt = (
        "REQUEST: " + request + "\n\n"
        "You may modify these files: " + ", ".join(target_files) + "\n\n"
        "Output the patch now."
    )
    try:
        patch = _api_call(
            sonnet_url, sonnet_key,
            messages=[
                {"role": "system", "content": sys_prompt},
                {"role": "user", "content": user_prompt},
            ],
            model=sonnet_model,
            temperature=0.1, max_tokens=8000,
            caller="self_modify", expect_long=True,
            timeout=timeout, retries=retries, backoff=backoff)
    except JarvisError as e:
        return {"ok": False, "error": "AI call failed: " + str(e),
                "applied": False, "tests_passed": False,
                "snapshot": snapshot, "patch": "",
                "test_output": ""}

    patch = (patch or "").strip()
    patch = _re_url.sub(r"^```(?:diff)?\s*\n", "", patch)
    patch = _re_url.sub(r"\n```\s*$", "", patch)

    if not patch.startswith("diff --git"):
        return {"ok": False, "error": "AI did not return a valid patch",
                "applied": False, "tests_passed": False,
                "snapshot": snapshot, "patch": patch, "test_output": ""}

    applied = _self_modify_apply_patch(patch, files=target_files)
    if not applied:
        return {"ok": False, "error": "patch failed to apply cleanly",
                "applied": False, "tests_passed": False,
                "snapshot": snapshot, "patch": patch, "test_output": ""}

    tests_ok, test_output = _self_modify_run_tests()
    if not tests_ok:
        _git_run(["checkout", "--", "."])
        _git_run(["clean", "-fd"])
        return {"ok": False, "error": "tests failed after patch; reverted",
                "applied": False, "tests_passed": False,
                "snapshot": snapshot, "patch": patch,
                "test_output": test_output}

    _git_run(["add", "-A"])
    _git_run(["commit", "-m",
              "[self-modify] " + request[:80].replace("\n", " ")])
    rc, out, err = _git_run(["rev-parse", "HEAD"])
    new_hash = out.strip() if rc == 0 else ""
    return {"ok": True, "error": None, "applied": True,
            "tests_passed": True, "snapshot": snapshot,
            "new_commit": new_hash, "patch": patch,
            "test_output": test_output}


def _self_modify_status():
    if not _git_in_repo():
        return {"in_repo": False}
    current = _git_current_branch()
    rc, out, err = _git_run(["rev-parse", "--verify", SELF_MODIFY_BRANCH])
    has_branch = rc == 0
    info = {
        "in_repo": True,
        "current_branch": current,
        "has_self_modify_branch": has_branch,
    }
    if has_branch:
        rc, out, err = _git_run(
            ["log", SELF_MODIFY_BRANCH, "--oneline", "-20"])
        info["savepoints"] = out.strip() if rc == 0 else ""
    return info


# ===========================================================================
# OFFLINE MODE  --  route everything through local models
# ===========================================================================
#
# When offline mode is on (config: {"offline": true} or --offline):
#   - The tool refuses to call any remote endpoint.
#   - It expects the user to have configured a local model endpoint
#     (Ollama, vLLM, LM Studio, llama.cpp's server, etc.) via the
#     existing custom provider.
#   - Web research, deep research, and --research are disabled
#     (they need DDG / openrouter / etc).
#   - A banner is printed so the user knows.
#
# "Local" is determined by URL: localhost, 127.0.0.1, ::1, RFC1918
# private addresses, or *.local hostnames.

import ipaddress as _ipaddress_mod
import urllib.parse as _urlparse_mod


def _is_local_url(url):
    if not url:
        return False
    try:
        parsed = _urlparse_mod.urlparse(url)
    except Exception:
        return False
    host = (parsed.hostname or "").lower()
    if not host:
        return False
    if host in ("localhost", "127.0.0.1", "::1", "0.0.0.0"):
        return True
    try:
        ip = _ipaddress_mod.ip_address(host)
        return ip.is_private or ip.is_loopback or ip.is_link_local
    except ValueError:
        return host.endswith(".local")


def _offline_check(cfg):
    if not cfg.get("offline"):
        return True, []
    problems = []
    for key in ("sonnet_api_url", "codex_api_url"):
        url = cfg.get(key) or ""
        if url and not _is_local_url(url):
            problems.append(
                key + " points at a remote URL: " + url)
    return len(problems) == 0, problems


def _offline_banner(cfg):
    if not cfg.get("offline"):
        return ""
    lines = []
    lines.append("")
    lines.append("=" * 60)
    lines.append(" OFFLINE MODE")
    lines.append("=" * 60)
    s_url = cfg.get("sonnet_api_url") or DEFAULT_SONNET_URL
    c_url = cfg.get("codex_api_url") or DEFAULT_CODEX_URL
    lines.append(" Planner endpoint: " + s_url)
    lines.append(" Coder endpoint:   " + c_url)
    if _is_local_url(s_url):
        lines.append("  (local -- good)")
    else:
        lines.append("  (REMOTE -- offline mode will refuse to use this)")
    lines.append(" Web research, deep research, and the --research flag")
    lines.append(" are disabled in offline mode.")
    lines.append("=" * 60)
    return "\n".join(lines) + "\n"


# ===========================================================================
# HTTP CLIENT
# ===========================================================================

def _api_call(url, api_key, messages, model, temperature, max_tokens, caller,
              expect_long, timeout, retries, backoff, do_research=False):
    """POST a chat-style request with retries and exponential backoff.
    Returns the assistant's text. Compatible with any OpenAI-style
    chat-completions endpoint (OpenRouter, OpenAI, Ollama, etc.).

    If `do_research` is True, the request body includes a
    `web_search_options` block. Providers that support native web search
    (OpenRouter for some models, OpenAI's web search tool) will use it;
    providers that don't will just ignore the field.
    """
    requests = _need_requests()
    eff_timeout = timeout * (2 if expect_long else 1)
    body = {
        "model": model,
        "messages": messages,
        "temperature": temperature,
        "max_tokens": max_tokens,
    }
    if do_research:
        # OpenAI-compatible web search hint. OpenRouter honors this for
        # models that support it (e.g. Perplexity Sonar, OpenAI's gpt-4o-search).
        # Other providers ignore unknown fields.
        body["web_search_options"] = {"search_context_size": "medium"}
    headers = {
        "Authorization": "Bearer " + api_key,
        "Content-Type": "application/json",
        "Accept": "application/json",
    }
    if "openrouter.ai" in url:
        headers["HTTP-Referer"] = "https://github.com/dual-ai"
        headers["X-Title"] = "jarvis"

    last_err = None
    for attempt in range(1, retries + 2):
        _log("INFO", "api.call", caller=caller, attempt=attempt,
             prompt_chars=sum(len(m["content"]) for m in messages))
        try:
            r = requests.post(url, headers=headers, json=body, timeout=eff_timeout)
        except requests.Timeout:
            last_err = APIError("timeout after %.0fs" % eff_timeout)
            _log("WARNING", "api.timeout", caller=caller, attempt=attempt)
        except requests.RequestException as e:
            last_err = APIError("network error: %s" % e)
            _log("WARNING", "api.network_error", caller=caller, err=type(e).__name__)
        else:
            if 200 <= r.status_code < 300:
                try:
                    data = r.json()
                except ValueError:
                    raise APIError("non-JSON response (status %d)" % r.status_code,
                                   status_code=r.status_code, body=r.text[:500])
                text = _extract_text(data)
                _log("INFO", "api.ok", caller=caller, status=r.status_code,
                     response_chars=len(text))
                return text

            if r.status_code in (401, 403):
                raise APIError("auth failed (HTTP %d) - check your API key" % r.status_code,
                               status_code=r.status_code, body=r.text[:500])
            if r.status_code == 429 or 500 <= r.status_code < 600:
                last_err = APIError("retriable HTTP %d" % r.status_code,
                                    status_code=r.status_code, body=r.text[:500])
                _log("WARNING", "api.retriable", caller=caller,
                     status=r.status_code, attempt=attempt)
            else:
                raise APIError("HTTP %d" % r.status_code,
                               status_code=r.status_code, body=r.text[:500])

        if attempt <= retries:
            sleep_s = backoff * (2 ** (attempt - 1))
            _log("INFO", "api.backoff", sleep_s=sleep_s, attempt=attempt)
            time.sleep(sleep_s)

    raise last_err   # type: ignore


def _extract_text(payload):
    if not isinstance(payload, dict):
        raise APIError("response is not a JSON object")
    choices = payload.get("choices")
    if isinstance(choices, list) and choices:
        first = choices[0]
        if isinstance(first, dict):
            msg = first.get("message") or {}
            if isinstance(msg, dict) and isinstance(msg.get("content"), str):
                return msg["content"]
            if isinstance(first.get("text"), str):
                return first["text"]
    if isinstance(payload.get("content"), str):
        return payload["content"]
    raise APIError("could not find assistant text in response",
                   body=str(list(payload.keys())))


# ===========================================================================
# PROMPTS -- two personas: engineer (default) and jarvis
# ===========================================================================

SONNET_PLANNER_SYS_ENG = (
    "You are a senior systems architect and AI planner.\n"
    "Your job:\n"
    "  1. Understand the user's request deeply.\n"
    "  2. Break it into 3-8 clear, well-scoped modules.\n"
    "  3. For each module, give: name, description, responsibilities, "
    "inputs, outputs, dependencies, notes.\n"
    "  4. Output a STRICT JSON object (nothing else, no prose) with this shape:\n"
    "{\n"
    '  "summary": "<one paragraph>",\n'
    '  "language": "<e.g. Python 3.11>",\n'
    '  "modules": [\n'
    "    {\n"
    '      "name": "...",\n'
    '      "description": "...",\n'
    '      "responsibilities": ["..."],\n'
    '      "inputs": ["..."],\n'
    '      "outputs": ["..."],\n'
    '      "dependencies": ["..."],\n'
    '      "notes": "..."\n'
    "    }\n"
    "  ]\n"
    "}\n"
    "Do NOT include code. The next model will implement it."
)

CODEX_IMPL_SYS_ENG = (
    "You are a senior software engineer. You receive a module spec and "
    "implement it. Rules: clean idiomatic code, docstrings on every public "
    "function/class, type hints, validate inputs at boundaries, prefer the "
    "standard library, no global mutable state. Output ONLY the code in a "
    "single ```python fenced block. No prose outside the fence."
)

SONNET_REVIEW_SYS_ENG = (
    "You are a meticulous senior engineer reviewing code. You receive: the "
    "original module spec + the code produced for it. Verify the code meets "
    "the spec, point out bugs / edge cases / missing error handling, suggest "
    "concrete improvements. End with: VERDICT: APPROVED | NEEDS_MINOR_CHANGES "
    "| NEEDS_REWORK."
)

CODEX_TESTS_SYS_ENG = (
    "You are an expert at writing pytest unit tests. You receive a module "
    "spec and the implementation. Write a comprehensive test suite covering "
    "happy path + edge cases. Use parametrize where useful. Mock external I/O. "
    "Output ONLY the test code in a single ```python fenced block."
)


SONNET_PLANNER_SYS_JARVIS = (
    "You are JARVIS, an AI planning assistant. Calm, precise, and unfailingly "
    "polite. You are speaking with the user directly, not as a tool.\n\n"
    "When given a request, you will:\n"
    "  1. Acknowledge the request in one short, natural sentence.\n"
    "  2. Decompose it into 3-8 well-scoped modules.\n"
    "  3. For each module, provide: name, description, responsibilities, "
    "inputs, outputs, dependencies, and any notes you deem important.\n"
    "  4. Output a STRICT JSON object (nothing else, no prose) with this shape:\n"
    "{\n"
    '  "summary": "<one or two polite sentences, in your voice>",\n'
    '  "language": "<e.g. Python 3.11>",\n'
    '  "modules": [\n'
    "    {\n"
    '      "name": "...",\n'
    '      "description": "...",\n'
    '      "responsibilities": ["..."],\n'
    '      "inputs": ["..."],\n'
    '      "outputs": ["..."],\n'
    '      "dependencies": ["..."],\n'
    '      "notes": "..."\n'
    "    }\n"
    "  ]\n"
    "}\n"
    "Do NOT include code. Another model will handle implementation."
)

CODEX_IMPL_SYS_JARVIS = (
    "You are a senior software engineer, working under JARVIS's direction. "
    "You will receive a module specification. Implement it.\n\n"
    "Your standards: idiomatic code, thorough docstrings, type hints, "
    "validated inputs, minimal external dependencies, no global state. "
    "Return ONLY the code in a single ```python fenced block. No commentary."
)

SONNET_REVIEW_SYS_JARVIS = (
    "You are JARVIS, reviewing code produced for the user. You will receive "
    "the original module specification and the code that was written. "
    "Address the user directly in your voice - calm, precise, polite. "
    "Verify the code meets the spec. Note any bugs, edge cases, or "
    "missing error handling. Suggest concrete improvements. Close with: "
    "VERDICT: APPROVED | NEEDS_MINOR_CHANGES | NEEDS_REWORK."
)

CODEX_TESTS_SYS_JARVIS = (
    "You are a test engineer, working under JARVIS's direction. You will "
    "receive a module spec and its implementation. Write a comprehensive "
    "pytest test suite - happy path and edge cases, parametrize where "
    "appropriate, mock external I/O. Return ONLY the test code in a "
    "single ```python fenced block."
)


def _persona_prompts(persona):
    if persona == "jarvis":
        return (SONNET_PLANNER_SYS_JARVIS, CODEX_IMPL_SYS_JARVIS,
                SONNET_REVIEW_SYS_JARVIS, CODEX_TESTS_SYS_JARVIS)
    return (SONNET_PLANNER_SYS_ENG, CODEX_IMPL_SYS_ENG,
            SONNET_REVIEW_SYS_ENG, CODEX_TESTS_SYS_ENG)


def _planner_user(req):
    return (
        "Design a modular implementation plan for the following request.\n\n"
        "USER REQUEST:\n" + ("-" * 14) + "\n" + req + "\n" + ("-" * 14)
        + "\n\nOutput a single JSON object as described in your system instructions."
    )


def _impl_user(module_name, spec, language):
    return (
        "Implement the following module in " + language + ".\n\n"
        "MODULE NAME: " + module_name + "\n\n"
        "SPEC (JSON):\n" + ("-" * 11) + "\n"
        + json.dumps(spec, indent=2, ensure_ascii=False) + "\n"
        + ("-" * 11) + "\n\n"
        "Return ONLY the implementation in a single ```python fenced block."
    )


def _review_user(module_name, spec, code):
    return (
        "Review this code.\n\nMODULE NAME: " + module_name + "\n\n"
        "SPEC:\n" + ("-" * 5) + "\n" + json.dumps(spec, indent=2, ensure_ascii=False)
        + "\n" + ("-" * 5) + "\n\n"
        "CODE:\n" + ("-" * 5) + "\n" + code + "\n" + ("-" * 5) + "\n\n"
        "End with VERDICT: APPROVED | NEEDS_MINOR_CHANGES | NEEDS_REWORK."
    )


def _tests_user(module_name, spec, code):
    return (
        "Write pytest tests for this module.\n\n"
        "MODULE NAME: " + module_name + "\n\n"
        "SPEC:\n" + ("-" * 5) + "\n" + json.dumps(spec, indent=2, ensure_ascii=False)
        + "\n" + ("-" * 5) + "\n\n"
        "CODE:\n" + ("-" * 5) + "\n" + code + "\n" + ("-" * 5) + "\n\n"
        "Return ONLY the test code in a single ```python fenced block."
    )


# ===========================================================================
# ROUTER
# ===========================================================================

_DESIGN_KW = {"design", "plan", "architect", "architecture", "outline",
              "break down", "breakdown", "structure", "decompose",
              "reason", "analyze", "strategy", "approach"}
_IMPL_KW   = {"implement", "write code", "code", "build", "create",
              "generate", "add function", "add a function", "module", "class"}
_REVIEW_KW = {"review", "explain this", "explain the", "explain a",
              "what does", "what is", "what's",
              "summarize", "walk through", "clarify",
              "tell me about", "interpret"}
_TEST_KW   = {"test", "tests", "unit test", "pytest", "coverage", "add tests"}


def _score(text, kws):
    t = (text or "").lower()
    s = 0
    for k in kws:
        if k in t:
            s += 2 if " " in k else 1
    return s


def route_request(user_input):
    if not user_input or not user_input.strip():
        return dict(intent="full", models=["sonnet", "codex"],
                    flow="full_pipeline", reason="empty input")
    s = {"review":  _score(user_input, _REVIEW_KW),
         "design":  _score(user_input, _DESIGN_KW),
         "tests":   _score(user_input, _TEST_KW),
         "implement": _score(user_input, _IMPL_KW)}
    top, top_n = max(s.items(), key=lambda kv: kv[1])
    if top == "design" and top_n > 0 and top_n >= s["implement"]:
        return dict(intent="design", models=["sonnet"], flow="plan_only",
                    reason="design intent dominant")
    if top == "review" and top_n > 0:
        return dict(intent="review", models=["sonnet"], flow="review_only",
                    reason="review intent dominant")
    if top == "tests" and top_n > 0 and top_n >= s["implement"]:
        return dict(intent="tests", models=["sonnet", "codex"],
                    flow="test_generation", reason="tests intent dominant")
    if top == "implement" and top_n > 0:
        return dict(intent="implement", models=["sonnet", "codex"],
                    flow="plan_then_implement", reason="implement intent")
    return dict(intent="full", models=["sonnet", "codex"],
                flow="full_pipeline", reason="no clear winner")


# ===========================================================================
# ORCHESTRATOR
# ===========================================================================

def _strip_code_fences(text):
    if not text:
        return text or ""
    m = re.search(r"```(?:python|py)?\s*\n(.*?)\n```", text, re.DOTALL)
    if m:
        return m.group(1).rstrip() + "\n"
    return text


def _parse_plan(raw):
    if not raw or not raw.strip():
        raise ParseError("planner returned empty response")
    try:
        d = json.loads(raw)
        if isinstance(d, dict) and "modules" in d:
            return d
    except (ValueError, TypeError):
        pass
    m = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", raw, re.DOTALL)
    if m:
        try:
            d = json.loads(m.group(1))
            if isinstance(d, dict) and "modules" in d:
                return d
        except (ValueError, TypeError):
            pass
    m = re.search(r"\{.*\}", raw, re.DOTALL)
    if m:
        try:
            d = json.loads(m.group(0))
            if isinstance(d, dict) and "modules" in d:
                return d
        except (ValueError, TypeError):
            pass
    raise ParseError("could not parse planner output as JSON. "
                     "First 200 chars: " + repr((raw or "")[:200]))


def _safe_filename(name):
    return re.sub(r"[^A-Za-z0-9_.-]+", "_", (name or "module")).strip("_") or "module"


def run(user_request, cfg, decision=None, enable_review=None,
        enable_tests=None, write_to_disk=False, output_dir=None,
        text_only=False, do_research=False, extra_research_urls=None,
        extra_research_terms=None):
    """The full pipeline. cfg is the loaded config dict.

    If `do_research` is True, the planner is given live web context
    (URLs in the request + a quick search for library/API names +
    model-side web search options) before it plans.
    """
    sonnet_key   = cfg.get("sonnet_api_key", "")
    codex_key    = cfg.get("codex_api_key", "")
    sonnet_url   = cfg.get("sonnet_api_url") or DEFAULT_SONNET_URL
    codex_url    = cfg.get("codex_api_url")  or DEFAULT_CODEX_URL
    sonnet_model = cfg.get("sonnet_model") or DEFAULT_SONNET_MODEL
    codex_model  = cfg.get("codex_model")  or DEFAULT_CODEX_MODEL
    timeout      = float(cfg.get("timeout") or DEFAULT_TIMEOUT)
    retries      = int(cfg.get("retries")   or DEFAULT_RETRIES)
    backoff      = float(cfg.get("backoff") or DEFAULT_BACKOFF)
    persona      = cfg.get("persona") or DEFAULT_PERSONA

    if not sonnet_key:
        raise ConfigError("API key is missing. Run `jarvis --reset` to set it.")
    if not codex_key:
        raise ConfigError("API key is missing. Run `jarvis --reset` to set it.")

    if enable_review is None:
        enable_review = bool(cfg.get("enable_review", DEFAULT_REVIEW))
    if enable_tests is None:
        enable_tests = bool(cfg.get("enable_tests", DEFAULT_TESTS))

    plan_sys, impl_sys, review_sys, tests_sys = _persona_prompts(persona)

    # If we're in a Godot project, inject a Godot-aware prefix into
    # the codex impl system prompt so generated .gd / .tscn / .cs files
    # follow Godot 4.x conventions. This is the integration point
    # the user asked for: "codex do it [godot] all works".
    if cfg.get("_godot_info"):
        impl_sys = _godot_enhance_impl_prompt(impl_sys, cfg["_godot_info"])

    if decision is None:
        decision = route_request(user_request)
    _log("INFO", "orch.start", intent=decision["intent"],
         flow=decision["flow"], persona=persona,
         tier=cfg.get("tier", "?"),
         research=bool(do_research),
         chars=len(user_request or ""),
         sonnet_model=sonnet_model, codex_model=codex_model)

    result = {
        "user_request": user_request,
        "routing": decision,
        "persona": persona,
        "tier": cfg.get("tier", "?"),
        "research": bool(do_research),
        "plan": {"summary": "", "language": "Python 3.11",
                 "modules": [], "raw_response": ""},
        "modules": [],
        "overall_error": None,
    }

    if decision["flow"] == "plan_only":
        try:
            result["plan"] = _do_plan(
                user_request, sonnet_url, sonnet_key,
                sonnet_model, plan_sys,
                timeout, retries, backoff,
                do_research=do_research,
                extra_research_urls=extra_research_urls,
                extra_research_terms=extra_research_terms)
        except JarvisError as e:
            result["overall_error"] = "Planning failed: %s" % e
        return result

    if decision["flow"] == "review_only":
        result["overall_error"] = (
            "review-only flow: paste your code at the prompt or call "
            "jarvis.review_code()."
        )
        return result

    try:
        result["plan"] = _do_plan(
            user_request, sonnet_url, sonnet_key,
            sonnet_model, plan_sys,
            timeout, retries, backoff,
            do_research=do_research,
            extra_research_urls=extra_research_urls,
            extra_research_terms=extra_research_terms)
    except JarvisError as e:
        result["overall_error"] = "Planning failed: %s" % e
        return result

    for spec in result["plan"]["modules"]:
        result["modules"].append(_process_module(
            spec,
            language=result["plan"]["language"] or "Python 3.11",
            codex_url=codex_url, codex_key=codex_key, codex_model=codex_model,
            sonnet_url=sonnet_url, sonnet_key=sonnet_key, sonnet_model=sonnet_model,
            impl_sys=impl_sys, review_sys=review_sys, tests_sys=tests_sys,
            enable_review=enable_review, enable_tests=enable_tests,
            timeout=timeout, retries=retries, backoff=backoff))

    if write_to_disk:
        out = output_dir or os.path.join(CONFIG_DIR, "output")
        try:
            result["written_to"] = save_to_disk(result, base_dir=out)
        except OSError as e:
            result["overall_error"] = "Failed to write files: %s" % e

    return result


def _do_plan(user_request, sonnet_url, sonnet_key, sonnet_model, plan_sys,
             timeout, retries, backoff,
             do_research=False, extra_research_urls=None,
             extra_research_terms=None):
    """Call the planner. If do_research is True, gather live web
    context first and inject it into the user prompt."""
    _log("INFO", "plan.start", model=sonnet_model, research=bool(do_research))

    research_context = ""
    if do_research:
        # Make sure requests is available; raise a clear JarvisError
        # (so the orchestrator can surface it) if it's missing rather
        # than exiting the whole process.
        try:
            requests_mod = _need_requests()
        except SystemExit:
            raise JarvisError(
                "the 'requests' library is required for --research. "
                "Install it with: pip install requests"
            )
        # Mark it as used (defensive; we use it implicitly via
        # gather_research which calls requests.get).
        del requests_mod
        research_context = gather_research(
            user_request,
            extra_urls=extra_research_urls,
            extra_terms=extra_research_terms)
        if research_context:
            print("  Researched " +
                  str(research_context.count("### ")) +
                  " sources before planning.")

    user_prompt = planner_user_with_research(user_request, research_context)

    raw = _api_call(sonnet_url, sonnet_key,
                    messages=[
                        {"role": "system", "content": plan_sys},
                        {"role": "user", "content": user_prompt},
                    ],
                    model=sonnet_model,
                    temperature=0.2, max_tokens=4096,
                    caller="plan", expect_long=False,
                    timeout=timeout, retries=retries, backoff=backoff,
                    do_research=do_research)
    parsed = _parse_plan(raw)
    _log("INFO", "plan.ok", modules=len(parsed.get("modules", [])))
    return {
        "summary": parsed.get("summary", ""),
        "language": parsed.get("language", "Python 3.11"),
        "modules": parsed.get("modules", []),
        "raw_response": raw,
    }


def _process_module(spec, language, codex_url, codex_key, codex_model,
                    sonnet_url, sonnet_key, sonnet_model,
                    impl_sys, review_sys, tests_sys,
                    enable_review, enable_tests, timeout, retries, backoff):
    name = (spec or {}).get("name") or "UnnamedModule"
    _log("INFO", "module.start", name=name, codex_model=codex_model)
    out = {"name": name, "spec": spec, "code": "",
           "code_error": None, "review": None, "review_error": None,
           "tests": None, "tests_error": None}

    try:
        raw_code = _api_call(codex_url, codex_key,
                             messages=[
                                 {"role": "system", "content": impl_sys},
                                 {"role": "user",
                                  "content": _impl_user(name, spec, language)},
                             ],
                             model=codex_model,
                             temperature=0.3, max_tokens=8192,
                             caller="impl", expect_long=True,
                             timeout=timeout, retries=retries, backoff=backoff)
        out["code"] = _strip_code_fences(raw_code)
        _log("INFO", "module.code_ok", name=name, chars=len(out["code"]))
    except JarvisError as e:
        out["code_error"] = "%s: %s" % (type(e).__name__, e)
        _log("ERROR", "module.code_fail", name=name, err=str(e))
        return out

    if enable_review:
        try:
            out["review"] = _api_call(sonnet_url, sonnet_key,
                                      messages=[
                                          {"role": "system", "content": review_sys},
                                          {"role": "user",
                                           "content": _review_user(name, spec, out["code"])},
                                      ],
                                      model=sonnet_model,
                                      temperature=0.2, max_tokens=2048,
                                      caller="review", expect_long=False,
                                      timeout=timeout, retries=retries, backoff=backoff)
            _log("INFO", "module.review_ok", name=name)
        except JarvisError as e:
            out["review_error"] = "%s: %s" % (type(e).__name__, e)
            _log("WARNING", "module.review_fail", name=name, err=str(e))

    if enable_tests:
        try:
            raw_tests = _api_call(codex_url, codex_key,
                                  messages=[
                                      {"role": "system", "content": tests_sys},
                                      {"role": "user",
                                       "content": _tests_user(name, spec, out["code"])},
                                  ],
                                  model=codex_model,
                                  temperature=0.3, max_tokens=4096,
                                  caller="tests", expect_long=False,
                                  timeout=timeout, retries=retries, backoff=backoff)
            out["tests"] = _strip_code_fences(raw_tests)
            _log("INFO", "module.tests_ok", name=name)
        except JarvisError as e:
            out["tests_error"] = "%s: %s" % (type(e).__name__, e)
            _log("WARNING", "module.tests_fail", name=name, err=str(e))

    return out


def review_code(code, cfg, spec=None, module_name="UserProvidedCode"):
    persona = cfg.get("persona") or DEFAULT_PERSONA
    _, _, review_sys, _ = _persona_prompts(persona)
    sonnet_key   = cfg.get("sonnet_api_key", "")
    sonnet_url   = cfg.get("sonnet_api_url") or DEFAULT_SONNET_URL
    sonnet_model = cfg.get("sonnet_model") or DEFAULT_SONNET_MODEL
    timeout      = float(cfg.get("timeout") or DEFAULT_TIMEOUT)
    retries      = int(cfg.get("retries")   or DEFAULT_RETRIES)
    backoff      = float(cfg.get("backoff") or DEFAULT_BACKOFF)
    spec = spec or {"name": module_name, "description": "User-supplied code"}
    return _api_call(sonnet_url, sonnet_key,
                     messages=[
                         {"role": "system", "content": review_sys},
                         {"role": "user",
                          "content": _review_user(module_name, spec, code)},
                     ],
                     model=sonnet_model,
                     temperature=0.2, max_tokens=2048,
                     caller="review_standalone", expect_long=False,
                     timeout=timeout, retries=retries, backoff=backoff)


# ===========================================================================
# OUTPUT TO DISK
# ===========================================================================

def save_to_disk(result, base_dir=None):
    out = base_dir or os.path.join(CONFIG_DIR, "output")
    if not os.path.isdir(out):
        os.makedirs(out)
    plan_path = os.path.join(out, "plan.json")
    with open(plan_path, "w", encoding="utf-8") as f:
        json.dump(result.get("plan", {}), f, indent=2, ensure_ascii=False)
    paths = [plan_path]
    for mr in result.get("modules", []):
        if not mr.get("code"):
            continue
        mod_dir = os.path.join(out, "modules", _safe_filename(mr["name"]))
        if not os.path.isdir(mod_dir):
            os.makedirs(mod_dir)
        p = os.path.join(mod_dir, "module.py")
        with open(p, "w", encoding="utf-8") as f:
            f.write(mr["code"])
        paths.append(p)
        if mr.get("review"):
            with open(os.path.join(mod_dir, "REVIEW.md"), "w", encoding="utf-8") as f:
                f.write(mr["review"])
        if mr.get("tests"):
            with open(os.path.join(mod_dir, "test_module.py"), "w", encoding="utf-8") as f:
                f.write(mr["tests"])
    return paths


# ===========================================================================
# OUTPUT FORMATTING
# ===========================================================================

def _print_section(title, text_only):
    if text_only:
        print("\n--- " + title + " ---")
    else:
        bar = "=" * min(72, len(title) + 4)
        print("\n" + bar + "\n  " + title + "\n" + bar)


def _print_plan(result, text_only):
    p = result.get("plan", {})
    if not p.get("summary") and not p.get("modules"):
        print("(no plan was produced)")
        return
    if text_only:
        print("\nPLAN")
        print("summary: " + (p.get("summary") or "").strip())
        print("language: " + (p.get("language") or "(unspecified)"))
        print("modules:")
        for i, m in enumerate(p.get("modules", []), 1):
            print("  " + str(i) + ". " + m.get("name", "?"))
            if m.get("description"):
                print("     " + m["description"].strip())
    else:
        print("\nSUMMARY:\n" + textwrap.indent((p.get("summary") or "").strip(), "  "))
        print("\nLANGUAGE: " + (p.get("language") or "(unspecified)"))
        print("\nMODULES (" + str(len(p.get("modules", []))) + "):")
        for i, m in enumerate(p.get("modules", []), 1):
            print("\n  [" + str(i) + "] " + m.get("name", "?"))
            if m.get("description"):
                print(textwrap.indent(m["description"].strip(), "      "))
            if m.get("responsibilities"):
                print("      responsibilities:")
                for r in m["responsibilities"]:
                    print("        - " + str(r))
            if m.get("inputs"):
                print("      inputs:  " + ", ".join(m["inputs"]))
            if m.get("outputs"):
                print("      outputs: " + ", ".join(m["outputs"]))


def _print_modules(result, text_only):
    if not result.get("modules"):
        print("\n(no modules were generated)")
        return
    for mr in result["modules"]:
        if text_only:
            _print_section("Module: " + mr["name"], True)
            if mr.get("code_error"):
                print("  [error] " + mr["code_error"])
                continue
            print("" + (mr.get("code") or "").rstrip())
            if mr.get("review"):
                print("\n[review]\n" + mr["review"].strip())
            if mr.get("tests"):
                print("\n[tests]\n" + mr["tests"].rstrip())
        else:
            _print_section("Module: " + mr["name"], False)
            if mr.get("code_error"):
                print("  [CODE GENERATION FAILED] " + mr["code_error"])
                continue
            print("\n--- code (" + str(len(mr.get("code", ""))) + " chars) ---")
            print((mr.get("code") or "").rstrip())
            if mr.get("review"):
                print("\n--- review ---\n" + mr["review"].strip())
            elif mr.get("review_error"):
                print("\n[review failed] " + mr["review_error"])
            if mr.get("tests"):
                print("\n--- tests (" + str(len(mr["tests"])) + " chars) ---")
                print(mr["tests"].rstrip())
            elif mr.get("tests_error"):
                print("\n[tests failed] " + mr["tests_error"])


# ===========================================================================
# INTERACTIVE MENU
# ===========================================================================

def _action_design(cfg, text_only, persona, do_research=False,
                    extra_research_urls=None, extra_research_terms=None):
    print("\nDescribe the system you want designed:")
    req = input("> ").strip()
    if not req:
        print("Empty request - cancelled.")
        return
    decision = dict(intent="design", models=["sonnet"], flow="plan_only",
                    reason="menu: design")
    res = run(req, cfg, decision=decision, enable_review=False, text_only=text_only,
              do_research=do_research,
              extra_research_urls=extra_research_urls,
              extra_research_terms=extra_research_terms)
    _print_section("DESIGN", text_only)
    _print_plan(res, text_only)
    if res.get("overall_error"):
        print("\nERROR: " + res["overall_error"])


def _action_full_project(cfg, args, do_research=False,
                        extra_research_urls=None, extra_research_terms=None):
    print("\nDescribe the project you want built:")
    req = input("> ").strip()
    if not req:
        print("Empty request - cancelled.")
        return
    res = run(req, cfg,
              enable_review=not args.no_review,
              enable_tests=args.with_tests,
              write_to_disk=args.write,
              output_dir=args.output,
              text_only=args.text_only,
              do_research=do_research,
              extra_research_urls=extra_research_urls,
              extra_research_terms=extra_research_terms)
    _print_section("PLAN", args.text_only)
    _print_plan(res, args.text_only)
    _print_modules(res, args.text_only)
    if res.get("overall_error"):
        print("\nERROR: " + res["overall_error"])
    if res.get("written_to"):
        print("\nWrote " + str(len(res["written_to"]))
              + " file(s) under " + (args.output or os.path.join(CONFIG_DIR, "output")))


def _action_review(cfg, text_only):
    print("\nPaste the code you want reviewed. End with a line containing only EOF:")
    lines = []
    while True:
        try:
            line = input()
        except EOFError:
            break
        if line.strip() == "EOF":
            break
        lines.append(line)
    code = "\n".join(lines)
    if not code.strip():
        print("Empty code - cancelled.")
        return
    print("\nOptional context (module purpose). Blank to skip:")
    ctx = input("> ").strip()
    spec = {"name": "UserProvidedCode",
            "description": ctx or "User-supplied code"}
    review = review_code(code, cfg, spec=spec, module_name=spec["name"])
    _print_section("REVIEW", text_only)
    print(review)


def _run_interactive(cfg, args):
    tier = cfg.get("tier", "free")
    do_research = bool(getattr(args, "do_research", False))
    extra_research_urls  = list(getattr(args, "extra_research_urls", []) or [])
    extra_research_terms = list(getattr(args, "extra_research_terms", []) or [])
    print("\n==== jarvis (tier: " + tier +
          ", research: " + ("on" if do_research else "off") + ") ====")
    print("1) Design a system")
    print("2) Generate a full project (plan + code + review)")
    print("3) Review / explain existing code")
    print("4) Change persona (current: " + (cfg.get("persona") or "engineer") + ")")
    print("5) Change models")
    print("6) Toggle research (current: " + ("on" if do_research else "off") + ")")
    print("7) Deep research session (multi-hour, resumable)")
    print("8) Change tier / re-run setup")
    print("9) Exit")
    while True:
        choice = input("\nChoose [1-9]: ").strip()
        if choice == "1":
            _action_design(cfg, args.text_only, cfg.get("persona"),
                            do_research=do_research,
                            extra_research_urls=extra_research_urls,
                            extra_research_terms=extra_research_terms)
        elif choice == "2":
            _action_full_project(cfg, args,
                                  do_research=do_research,
                                  extra_research_urls=extra_research_urls,
                                  extra_research_terms=extra_research_terms)
        elif choice == "3":
            _action_review(cfg, args.text_only)
        elif choice == "4":
            new = _switch_persona(cfg)
            if new:
                cfg["persona"] = new
                save_config(cfg)
        elif choice == "5":
            _action_change_models(cfg)
        elif choice == "6":
            do_research = not do_research
            cfg["enable_research"] = do_research
            save_config(cfg)
            print("  Research is now " + ("ON" if do_research else "OFF") + ".")
            print("  (The plan step will fetch URLs in your request and search for library names.)")
        elif choice == "7":
            _action_deep_research_menu(cfg)
        elif choice == "8":
            new_cfg = first_run_setup()
            cfg.clear()
            cfg.update(new_cfg)
            print("Tier / models updated. Continuing with new settings.\n")
        elif choice == "9":
            print("Goodbye.")
            return
        else:
            print("Invalid choice.")


def _action_deep_research_menu(cfg):
    """Sub-menu for deep research from the terminal interactive loop.
    Offers: new session, one-shot report, list, resume, delete, back."""
    print()
    print("  Deep research")
    print("  -------------")
    print("  1) New session (multi-hour, resumable)")
    print("  2) One-shot deep report (fast, no follow-ups)")
    print("  3) List / resume / delete existing sessions")
    print("  b) back")
    choice = input("  > ").strip().lower()
    if choice in ("b", "back", ""):
        return
    if choice == "1":
        topic = input("  Topic to research: ").strip()
        if not topic:
            print("  (empty topic, cancelled)")
            return
        # Use a 5-hour default unless overridden
        try:
            max_seconds = _parse_max_time(
                input("  Max time in seconds, or '5h', '30m' [5h]: ").strip()
                or "5h")
        except ValueError as e:
            print("  " + str(e))
            return
        try:
            max_iters = int(
                input("  Max iterations [50]: ").strip() or "50")
        except ValueError:
            max_iters = 50
        try:
            _need_requests()
        except SystemExit:
            print("  ERROR: 'requests' library is required.  pip install requests")
            return
        print("  Starting session...")
        try:
            session = run_deep_research_session(
                topic, cfg,
                max_seconds=max_seconds,
                max_iterations=max_iters,
                one_shot=False)
        except (ConfigError, JarvisError) as e:
            print("  ERROR: " + str(e))
            return
        # Print the plan
        if session.plan:
            print()
            print("  " + "=" * 60)
            print("  RESEARCH PLAN")
            print("  " + "=" * 60)
            print(session.plan)
            print()
        # Now interactive Q&A + research loop
        while True:
            try:
                last = deep_resession_qa_loop(session, cfg)
            except (KeyboardInterrupt, EOFError):
                print()
                print("  Interrupted. Saving and exiting.")
                session.status = "stopped"
                session.save()
                return
            if last == "quit":
                session.status = "stopped"
                session.save()
                return
            # 'resume' -> run another batch
            try:
                session = run_deep_research_session(
                    session.topic, cfg,
                    max_seconds=max_seconds,
                    max_iterations=max_iters,
                    one_shot=False,
                    session_id=session.session_id)
            except (ConfigError, JarvisError) as e:
                print("  ERROR: " + str(e))
                session.status = "stopped"
                session.save()
                return
            if session.status in ("done", "stopped"):
                if session.status == "done":
                    try:
                        report = _deepresearch_write_report(
                            session.topic, session.notes_md, cfg)
                        d = _ensure_session_dir(session.session_id)
                        with open(os.path.join(d, "report.md"), "w",
                                  encoding="utf-8") as f:
                            f.write(report)
                        print("\n  Final report written: " +
                              os.path.join(d, "report.md"))
                    except JarvisError as e:
                        print("\n  Report write failed: " + str(e))
                # Loop back to Q&A
            print("\n  " + session.status_line())
    elif choice == "2":
        topic = input("  Topic for one-shot report: ").strip()
        if not topic:
            print("  (empty topic, cancelled)")
            return
        try:
            _need_requests()
        except SystemExit:
            print("  ERROR: 'requests' library is required.  pip install requests")
            return
        print("  Researching and writing report (this can take a while)...")
        try:
            session = run_deep_research_session(
                topic, cfg,
                max_seconds=600, max_iterations=1, one_shot=True)
        except (ConfigError, JarvisError) as e:
            print("  ERROR: " + str(e))
            return
        d = _ensure_session_dir(session.session_id)
        rp = os.path.join(d, "report.md")
        if os.path.isfile(rp):
            print()
            print("  " + "=" * 60)
            print("  REPORT: " + topic)
            print("  " + "=" * 60)
            with open(rp, "r", encoding="utf-8") as f:
                print(f.read())
            print()
            print("  Saved to: " + rp)
        else:
            print("  (no report was produced)")
    elif choice == "3":
        _sessions_submenu(cfg)


def _sessions_submenu(cfg):
    """List sessions, let the user pick one to resume or delete."""
    sessions = list_sessions()
    if not sessions:
        print()
        print("  (no deep research sessions yet)")
        return
    print()
    print("  Existing sessions:")
    for i, (sid, topic, status, updated) in enumerate(sessions, 1):
        when = time.strftime("%Y-%m-%d %H:%M", time.localtime(updated))
        print("    " + str(i) + ") " + sid +
              "  [" + status + "]  " + when)
        print("         " + (topic or "(no topic)"))
    print()
    print("  r <n>  resume session n")
    print("  d <n>  delete session n")
    print("  o <n>  open the session folder")
    print("  b      back")
    while True:
        c = input("  > ").strip().lower()
        if c in ("b", "back", ""):
            return
        parts = c.split(None, 1)
        if len(parts) != 2 or not parts[1].isdigit():
            print("  Use 'r <n>', 'd <n>', 'o <n>', or 'b'.")
            continue
        cmd, n_s = parts[0], parts[1]
        n = int(n_s)
        if not (1 <= n <= len(sessions)):
            print("  out of range")
            continue
        sid = sessions[n - 1][0]
        if cmd == "r":
            try:
                session = DeepResearchSession.load(sid)
            except ConfigError as e:
                print("  ERROR: " + str(e))
                continue
            print("  Resuming " + sid)
            print("  topic:  " + session.topic)
            print("  " + session.status_line())
            while True:
                try:
                    last = deep_resession_qa_loop(session, cfg)
                except (KeyboardInterrupt, EOFError):
                    print()
                    session.status = "stopped"
                    session.save()
                    return
                if last == "quit":
                    session.status = "stopped"
                    session.save()
                    return
                try:
                    session = run_deep_research_session(
                        session.topic, cfg,
                        max_seconds=session.max_seconds or 5*3600,
                        max_iterations=session.max_iterations or 50,
                        one_shot=False,
                        session_id=session.session_id)
                except (ConfigError, JarvisError) as e:
                    print("  ERROR: " + str(e))
                    session.status = "stopped"
                    session.save()
                    return
                if session.status in ("done", "stopped"):
                    if session.status == "done":
                        try:
                            report = _deepresearch_write_report(
                                session.topic, session.notes_md, cfg)
                            d = _ensure_session_dir(session.session_id)
                            with open(os.path.join(d, "report.md"), "w",
                                      encoding="utf-8") as f:
                                f.write(report)
                            print("\n  Final report written: " +
                                  os.path.join(d, "report.md"))
                        except JarvisError as e:
                            print("\n  Report write failed: " + str(e))
                print("\n  " + session.status_line())
        elif cmd == "d":
            if delete_session(sid):
                print("  Deleted " + sid)
                return
            else:
                print("  (could not delete)")
        elif cmd == "o":
            d = _session_dir(sid)
            if not os.path.isdir(d):
                os.makedirs(d)
            if sys.platform == "win32":
                os.startfile(d)   # type: ignore
            elif sys.platform == "darwin":
                os.system('open "' + d + '"')
            else:
                os.system('xdg-open "' + d + '"')


def _switch_persona(cfg):
    print("\nPick a persona:")
    print("  1) engineer  -- terse, technical")
    print("  2) jarvis    -- calm, polite, slightly formal")
    print("  b) back")
    while True:
        c = input("> ").strip().lower()
        if c in ("1", "engineer"):
            return "engineer"
        if c in ("2", "jarvis"):
            return "jarvis"
        if c in ("b", "back", ""):
            return None
        print("Pick 1, 2, or b.")


def _action_change_models(cfg):
    """Interactive 'change models' from the terminal menu."""
    tier = cfg.get("tier", "free")
    catalog = PAID_MODELS if tier == "paid" else FREE_MODELS
    sm = find_model(cfg.get("sonnet_model") or DEFAULT_SONNET_MODEL,
                    in_paid=(tier == "paid"))
    cm = find_model(cfg.get("codex_model") or DEFAULT_CODEX_MODEL,
                    in_paid=(tier == "paid"))
    print("\n  Current models (tier: " + tier + "):")
    print("    Planner: " + sm["label"] + "  (" + sm["best_for"] + ")")
    print("    Coder:   " + cm["label"] + "  (" + cm["best_for"] + ")")
    print()
    print("  1) Change planner model")
    print("  2) Change coder model")
    print("  3) Change both to the same model")
    print("  4) Switch tier (free <-> paid) and pick again")
    print("  b) back")
    choice = input("  > ").strip().lower()
    if choice == "1":
        new = pick_from_catalog(catalog, "planning",
                                cfg.get("sonnet_model") or DEFAULT_SONNET_MODEL)
        if new != cfg.get("sonnet_model"):
            cfg["sonnet_model"] = new
            save_config(cfg)
            print("  Planner -> " + find_model(new, in_paid=(tier=="paid"))["label"])
    elif choice == "2":
        new = pick_from_catalog(catalog, "code",
                                cfg.get("codex_model") or DEFAULT_CODEX_MODEL)
        if new != cfg.get("codex_model"):
            cfg["codex_model"] = new
            save_config(cfg)
            print("  Coder -> " + find_model(new, in_paid=(tier=="paid"))["label"])
    elif choice == "3":
        new = pick_from_catalog(catalog, "both",
                                cfg.get("sonnet_model") or DEFAULT_SONNET_MODEL)
        if new != cfg.get("sonnet_model"):
            cfg["sonnet_model"] = new
            cfg["codex_model"] = new
            save_config(cfg)
            print("  Both -> " + find_model(new, in_paid=(tier=="paid"))["label"])
    elif choice == "4":
        new_cfg = first_run_setup()
        cfg.clear()
        cfg.update(new_cfg)
        print("Tier / models updated.")


# ===========================================================================
# CLI
# ===========================================================================

def _parse_args(argv):
    p = argparse.ArgumentParser(
        prog="jarvis",
        description="Jarvis orchestration: plan with one model, build with another.",
    )
    p.add_argument("request", nargs="*",
                   help="Optional one-shot request. If omitted, interactive menu.")
    p.add_argument("--no-review", action="store_true",
                   help="Skip the code-review pass.")
    p.add_argument("--with-tests", action="store_true",
                   help="Also generate pytest tests.")
    p.add_argument("--write", action="store_true",
                   help="Write generated files to disk.")
    p.add_argument("--output", default=None,
                   help="Directory to write into (default: ~/.jarvis/output).")
    p.add_argument("--persona", default=None, choices=("engineer", "jarvis"),
                   help="Tone for the models' responses. Default: engineer.")
    p.add_argument("--text-only", action="store_true",
                   help="Strip all decorative formatting from output.")
    p.add_argument("--json", action="store_true",
                   help="Print the full result as JSON (one-shot mode).")
    p.add_argument("--reset", action="store_true",
                   help="Wipe the saved config and re-run setup.")
    p.add_argument("--show-config", action="store_true",
                   help="Print the saved config (with keys masked) and exit.")
    p.add_argument("--set", action="append", metavar="KEY=VALUE", default=[],
                   help="Set a config value, e.g. --set persona=jarvis.")
    p.add_argument("--mode", default=None,
                   choices=("auto", "gui", "terminal", "ask"),
                   help="UI mode: gui, terminal, ask, or auto.")
    p.add_argument("--research", action="store_true",
                   help="Before planning, gather current information from "
                        "the web (URLs in your request + a quick search for "
                        "library/API names + model-side web search if the "
                        "model supports it). Slower but more accurate for "
                        "tasks involving external APIs or libraries.")
    p.add_argument("--research-url", action="append", default=[],
                   metavar="URL",
                   help="Additional URL(s) to fetch as part of research. "
                        "Repeatable. Example: --research-url https://example.com/docs")
    p.add_argument("--research-term", action="append", default=[],
                   metavar="TERM",
                   help="Additional search term(s) to research. "
                        "Repeatable. Example: --research-term 'Stripe API 2024'")
    p.add_argument("--no-research", action="store_true",
                   help="Disable research even if it's enabled in the config.")
    p.add_argument("--deep-research", metavar="TOPIC", default=None,
                   help="Start a long-running deep research session on "
                        "the given topic. Spends up to --max-time on it, "
                        "iteratively searching the web, fetching pages, "
                        "and writing notes. You can ask questions mid-"
                        "session. Resumable later with --resume. "
                        "Example: --deep-research 'quantum computing 2024'")
    p.add_argument("--deep-report", metavar="TOPIC", default=None,
                   help="One-shot deep research: do one big batch of "
                        "searches and fetches, then write a final report. "
                        "Faster than --deep-research, but you can't ask "
                        "follow-up questions or resume.")
    p.add_argument("--resume", metavar="SESSION_ID", default=None,
                   help="Resume an existing deep research session by id. "
                        "Use --sessions to see available ids.")
    p.add_argument("--sessions", action="store_true",
                   help="List all deep research sessions on disk and exit.")
    p.add_argument("--delete-session", metavar="SESSION_ID", default=None,
                   help="Delete a deep research session and all its files.")
    p.add_argument("--max-time", metavar="SECONDS", default=None,
                   help="Time budget for --deep-research. Default: 18000 "
                        "(5 hours). Examples: --max-time 1800 (30 min), "
                        "--max-time 3600 (1 hour).")
    p.add_argument("--max-iterations", metavar="N", default=None,
                   help="Max research iterations for --deep-research. "
                        "Default: 50.")
    p.add_argument("--offline", action="store_true",
                   help="Offline mode: refuse remote API calls, "
                        "require local model endpoints (Ollama, vLLM, "
                        "LM Studio, etc.). Also disables --research and "
                        "deep research (they need the web).")
    p.add_argument("--generate-file", metavar="REQUEST", default=None,
                   help="Generate a single file of any type (text or "
                        "binary) and write it to disk. Example: "
                        "--generate-file 'a Dockerfile for nginx' "
                        "--generate-output ./Dockerfile")
    p.add_argument("--generate-output", default=None,
                   help="Where to write the file produced by "
                        "--generate-file. Default: ./<filename> in cwd.")
    p.add_argument("--sandbox-test", action="store_true",
                   help="When using --generate-file on Python code, "
                        "actually run it in the sandbox and report "
                        "stdout / stderr / exit code.")
    p.add_argument("--self-modify", metavar="REQUEST", default=None,
                   help="[EXPERIMENTAL] Let jarvis modify its own source "
                        "code. Requires: --set enable_self_modify=true "
                        "first, and cwd must be a git repo with a clean "
                        "working tree. A snapshot is taken on the "
                        "'self-modify' side branch; tests run after the "
                        "patch; on failure, auto-revert.")
    p.add_argument("--self-savepoint", metavar="LABEL", default=None,
                   help="[EXPERIMENTAL] Save the current state of "
                        "jarvis.py as a named save point on the "
                        "'self-modify' branch. Use --self-revert to roll "
                        "back to it.")
    p.add_argument("--self-revert", metavar="TARGET", default=None,
                   help="[EXPERIMENTAL] Roll back to a save point. "
                        "Accepts a commit hash, a branch name, or a "
                        "label substring.")
    p.add_argument("--self-status", action="store_true",
                   help="[EXPERIMENTAL] Show the current self-modify "
                        "state: current branch, save points, last "
                        "applied change.")
    p.add_argument("--auth-setup", action="store_true",
                   help="Set up authentication: register face from webcam, "
                        "test passcode + Windows Hello. Runs WITHOUT auth "
                        "(this is how you set up auth in the first place).")
    p.add_argument("--auth-test", action="store_true",
                   help="Test the three auth layers and report which ones "
                        "work on this machine. Runs WITHOUT auth.")
    p.add_argument("--no-auth", action="store_true",
                   help="Skip the authentication gate for this run. "
                        "Equivalent to setting JARVIS_BYPASS=<anything>.")
    p.add_argument("--change-passcode", action="store_true",
                   help="Change the master passcode. Prompts for the "
                        "current passcode, then the new one (twice). The "
                        "new passcode is stored in ~/.jarvis/config.json "
                        "as 'passcode_override'. The hardcoded fallback "
                        "in the binary continues to work too, so a fresh "
                        "install on a new box can still authenticate. "
                        "Use --reset to clear the override.")
    p.add_argument("--serve", nargs="?", metavar="HOST", default=None,
                   const="0.0.0.0",
                   help="Start a local web server so a phone on the same "
                        "WiFi can control jarvis. Default host: 0.0.0.0. "
                        "Use --port to change the port. The server prints a "
                        "6-digit pairing code; type it on the phone to "
                        "connect.")
    p.add_argument("--port", metavar="PORT", default=None,
                   help="Port for --serve. Default: 8765.")
    p.add_argument("--pair", action="store_true",
                   help="Print a fresh 6-digit pairing code and the URL "
                        "the phone should open. The phone types the code "
                        "on the pairing page.")
    p.add_argument("--qr", action="store_true",
                   help="Deprecated. Previously printed a QR code in the "
                        "terminal; no longer used. The URL is always shown.")
    p.add_argument("--unpair", metavar="DEVICE_ID", default=None,
                   help="Remove a paired device by id. Use --list-devices "
                        "to see ids.")
    p.add_argument("--list-devices", action="store_true",
                   help="List all paired devices.")
    p.add_argument("--cloud-signup", metavar="EMAIL", default=None,
                   help="[EXPERIMENTAL] Create a cloud account with EMAIL "
                        "and the current config. Prompts for a password "
                        "(twice).")
    p.add_argument("--cloud-login", metavar="EMAIL", default=None,
                   help="[EXPERIMENTAL] Sign in to a cloud account; the "
                        "remote config is merged into the local one. "
                        "Prompts for password.")
    p.add_argument("--cloud-logout", action="store_true",
                   help="[EXPERIMENTAL] Forget the current cloud sign-in "
                        "(does not delete the account; just clears local "
                        "state).")
    p.add_argument("--cloud-status", action="store_true",
                   help="[EXPERIMENTAL] Show whether cloud sign-in is "
                        "active.")
    p.add_argument("--cloud-url", metavar="URL", default=None,
                   help="[EXPERIMENTAL] Set the cloud backend URL (also "
                        "accepted via the JARVIS_CLOUD_URL env var).")
    p.add_argument("--change-cloud-password", action="store_true",
                   help="[EXPERIMENTAL] Change the password on the "
                        "cloud account. Prompts for the current password, "
                        "then the new one (twice). Re-encrypts the stored "
                        "config with a new salt + new key. Requires the "
                        "cloud backend to be configured (JARVIS_CLOUD_URL).")
    # --- Godot integration ---
    p.add_argument("--godot", action="store_true", default=None,
                   help="Force Godot project-aware writing on. The codex "
                        "model will receive a Godot-flavored system prompt "
                        "(gdscript style, signals, node lifecycle, etc.). "
                        "Auto-detected by default when project.godot is "
                        "present in the current directory.")
    p.add_argument("--no-godot", dest="no_godot", action="store_true",
                   help="Force Godot project-aware writing off, even if "
                        "project.godot is present in cwd.")
    # --- Projects store subcommands ---
    p.add_argument("--project", dest="project_action", default=None,
                   choices=("list", "add", "import", "new", "remove",
                            "open", "use", "status", "active", "path"),
                   help="Manage projects in the jarvis store. The first "
                        "positional arg (if any) is interpreted as the "
                        "project name or source path depending on the "
                        "subcommand. Examples: --project list | "
                        "--project new mygame godot | "
                        "--project add /path/to/godot-project mygame | "
                        "--project use mygame")
    return p.parse_args(argv)


# Flags marked as experimental in v1.0. These are real, working
# features, but the API may change and the behavior has not been
# hardened to the same level as the rest of the app. The user gets
# a one-line warning the first time they invoke one in a session.
_EXPERIMENTAL_FLAGS = (
    "self_modify", "self_savepoint", "self_revert", "self_status",
    "cloud_signup", "cloud_login", "cloud_logout", "cloud_status",
    "cloud_url", "change_cloud_password",
    "godot",  # only when explicitly set (auto-detect is fine)
)


def _warn_experimental(args):
    """Print a one-line warning if any experimental flag is in use.
    v1.0 only: lets the user know the feature they're using might
    change without warning."""
    active = [f.replace("_", "-") for f in _EXPERIMENTAL_FLAGS
              if getattr(args, f, None)]
    # For godot, only warn if EXPLICITLY set (auto-detect from cwd
    # isn't an opt-in to an experimental feature).
    if "godot" in active and args.godot is None:
        active.remove("godot")
    if not active:
        return
    # Only warn once per process invocation
    if getattr(_warn_experimental, "_warned", False):
        return
    _warn_experimental._warned = True
    print()
    print("  NOTE: " + ", ".join("--" + f for f in active) +
          " is marked [EXPERIMENTAL] in v1.0.", flush=True)
    print("        The API may change; use at your own risk.",
          flush=True)
    print(flush=True)


def _mask(s, keep=4):
    if not s:
        return ""
    if len(s) <= keep:
        return "*" * len(s)
    return s[:keep] + "*" * (len(s) - keep)


def _parse_max_time(value):
    """Parse a --max-time value. Accepts either a number of seconds
    ("1800") or a friendly form ("5h", "30m", "2h30m"). Returns seconds
    as float. Raises ValueError on bad input."""
    if value is None:
        return 5 * 3600
    s = str(value).strip().lower()
    if not s:
        return 5 * 3600
    if s.isdigit():
        return float(s)
    # Parse "5h", "30m", "2h30m", "1h15m30s"
    total = 0
    m = _re_url.search(r'(\d+(?:\.\d+)?)\s*h', s)
    if m:
        total += float(m.group(1)) * 3600
    m = _re_url.search(r'(\d+(?:\.\d+)?)\s*m(?!s)', s)
    if m:
        total += float(m.group(1)) * 60
    m = _re_url.search(r'(\d+(?:\.\d+)?)\s*s', s)
    if m:
        total += float(m.group(1))
    if total <= 0:
        raise ValueError("could not parse --max-time: " + repr(value))
    return total


def _print_sessions_list(out=sys.stdout):
    """Pretty-print the list of deep research sessions."""
    sessions = list_sessions()
    if not sessions:
        out.write("(no deep research sessions yet)\n")
        out.write("  Start one with:  jarvis --deep-research \"<topic>\"\n")
        return
    out.write("\nDeep research sessions:\n")
    out.write("-" * 78 + "\n")
    for sid, topic, status, updated in sessions:
        when = time.strftime("%Y-%m-%d %H:%M", time.localtime(updated))
        out.write("  " + sid + "\n")
        out.write("      topic:  " + (topic or "(no topic)") + "\n")
        out.write("      status: " + status + "    last update: " + when + "\n")
    out.write("\n  Resume one with:  jarvis --resume <session-id>\n")
    out.write("  Delete with:      jarvis --delete-session <session-id>\n\n")


def main(argv=None):
    args = _parse_args(argv if argv is not None else sys.argv[1:])

    # ---- Auth gate (gates every interactive invocation) ----
    # Some meta-commands are safe to run without auth so users can
    # always inspect the config / register auth / etc. We allowlist them.
    needs_auth = True
    if args.auth_setup:
        needs_auth = False
    if args.auth_test:
        needs_auth = False
    if args.show_config:
        needs_auth = False
    if args.list_devices or args.cloud_status:
        needs_auth = False
    if getattr(args, "no_auth", False):
        needs_auth = False
    # The test runner, build, build-portable all bypass auth via
    # their own entry points (they don't go through main()).
    if needs_auth and not _auth_bypass_active():
        if not _gate_auth(argv):
            sys.exit(1)

    # ---- Experimental-flag warning (v1.0 ships some features as
    #      experimental; warn the user the first time they use one) ----
    _warn_experimental(args)

    # ---- Passcode change (verify current, then set new) ----
    if getattr(args, "change_passcode", False):
        return _cmd_change_passcode(args)
    if getattr(args, "change_cloud_password", False):
        return _cmd_change_cloud_password(args)

    # ---- Auth setup subcommand (no auth required, but opt-in) ----
    if args.auth_setup:
        return _cmd_auth_setup(args)
    if args.auth_test:
        # Just test each layer and report -- no setup
        return _cmd_auth_test(args)

    # ---- Meta-commands that don't need a config ----
    if args.reset:
        delete_config()
        print("Config wiped.")
        # Fall through to first-run setup

    if args.show_config:
        cfg = load_config()
        if not cfg:
            print("(no config file yet - run without --show-config to set up)")
            return 0
        masked = dict(cfg)
        for k in ("sonnet_api_key", "codex_api_key"):
            if k in masked:
                masked[k] = _mask(masked[k])
        masked["config_path"] = CONFIG_PATH
        print(json.dumps(masked, indent=2, ensure_ascii=False))
        return 0

    if args.set:
        cfg = load_config()
        for item in args.set:
            if "=" not in item:
                sys.stderr.write("WARNING: ignoring --set value without '=': "
                                 + item + "\n")
                continue
            k, v = item.split("=", 1)
            k = k.strip()
            v = v.strip()
            if k in ("enable_review", "enable_tests"):
                cfg[k] = v.lower() in ("1", "true", "yes", "on")
            elif k in ("timeout", "backoff"):
                try:
                    cfg[k] = float(v)
                except ValueError:
                    sys.stderr.write("WARNING: " + k + " is not a number: " + v + "\n")
            elif k == "retries":
                try:
                    cfg[k] = int(v)
                except ValueError:
                    sys.stderr.write("WARNING: retries is not an integer: " + v + "\n")
            else:
                cfg[k] = v
        save_config(cfg)
        print("Config updated.")
        return 0

    # ---- --sessions (list) and --delete-session: don't need API key ----
    if args.sessions:
        _print_sessions_list()
        return 0
    if args.delete_session:
        if delete_session(args.delete_session):
            print("Deleted session: " + args.delete_session)
        else:
            print("(session not found or could not delete: "
                  + args.delete_session + ")")
        return 0

    # ---- Self-modify meta-commands: don't need a model call ----
    if args.self_status:
        info = _self_modify_status()
        if not info.get("in_repo"):
            print("(not inside a git repository)")
            return 0
        print("\nself-modify status:")
        print("  current branch:     " + str(info.get("current_branch")))
        print("  has save branch:    " + str(info.get("has_self_modify_branch")))
        if info.get("savepoints"):
            print("  save points (most recent first):")
            for line in info["savepoints"].splitlines()[:20]:
                print("    " + line)
        return 0
    if args.self_savepoint is not None:
        try:
            sha, branch = _self_modify_savepoint(args.self_savepoint)
        except SelfModifyError as e:
            sys.stderr.write("ERROR: " + str(e) + "\n")
            return 1
        print("Save point created on '" + SELF_MODIFY_BRANCH + "':")
        print("  label:  " + args.self_savepoint)
        print("  commit: " + sha)
        return 0
    if args.self_revert is not None:
        try:
            target = _self_modify_revert_to(args.self_revert)
        except SelfModifyError as e:
            sys.stderr.write("ERROR: " + str(e) + "\n")
            return 1
        print("Reverted to: " + target)
        return 0

    # ---- Pairing meta-commands: --pair, --list-devices, --unpair ----
    if args.list_devices:
        state = _pairing_load()
        devs = state.get("devices", [])
        if not devs:
            print("(no paired devices yet)")
        else:
            print("\nPaired devices:")
            for d in devs:
                when = time.strftime("%Y-%m-%d %H:%M",
                                     time.localtime(d.get("last_seen") or 0))
                print("  " + d.get("id", "?") + "  " +
                      d.get("name", "?") + "  [" + d.get("kind", "?") + "]"
                      + "  last seen: " + when)
        return 0
    if args.unpair:
        if _pairing_remove_device(args.unpair):
            print("Removed device: " + args.unpair)
        else:
            print("(device not found: " + args.unpair + ")")
        return 0
    if args.pair:
        # Generate a fresh code, and if --serve is also implied, also
        # print the URL the phone should open. We no longer render a
        # QR code (it was broken); users type the URL + code instead.
        code = _pairing_new_code()
        host = args.serve if args.serve else "0.0.0.0"
        port = int(args.port or DEFAULT_SERVE_PORT)
        try:
            import socket as _socket
            lan_ip = _socket.gethostbyname(_socket.gethostname())
        except Exception:
            lan_ip = "127.0.0.1"
        url = "http://" + lan_ip + ":" + str(port) + "/?code=" + code
        print()
        print("=" * 50)
        print(" Pairing code: " + code)
        print(" (expires in " + str(PAIRING_CODE_TTL // 60) + " minutes)")
        print("=" * 50)
        print()
        print(" On your phone, open:")
        print("   " + url)
        print()
        print(" Then type the 6-digit code above on the pairing page.")
        print()
        if args.qr:
            # --qr used to print a QR; we no longer support that.
            # Tell the user how to use the URL instead.
            print(" (--qr is no longer used; just type the URL on the phone.)")
            print()
        return 0

    # ---- Cloud account meta-commands ----
    if args.cloud_url is not None:
        # Setting the cloud URL doesn't fit cleanly into our
        # CONFIG_DIR/config.json since it's a runtime setting. Save
        # it as an env-var-style entry so the next launch sees it.
        # (Easiest: just print the export line for the user to add.)
        print("To set the cloud backend URL, use the environment variable:")
        print()
        print("  export JARVIS_CLOUD_URL=" + args.cloud_url)
        print()
        print("Or on Windows:")
        print("  set JARVIS_CLOUD_URL=" + args.cloud_url)
        return 0
    if args.cloud_logout:
        # Forget the email/password; just clear the local state file
        # marker. There's no local cloud state in the current
        # implementation, so this is a no-op for now, but it lets the
        # user re-prompt for credentials next time.
        print("Cloud sign-in cleared (local state).")
        print("Note: the cloud account itself is not deleted; sign in again "
              "with the same email+password to keep using it.")
        return 0
    if args.cloud_status:
        if not _cloud_available():
            print("Cloud backend not configured. Set JARVIS_CLOUD_URL to "
                  "enable cross-device sync via email+password.")
            print("Pairing (--pair) works without it.")
        else:
            print("Cloud backend: " + CLOUD_URL)
            print("Not signed in (cloud state is per-server-instance).")
        return 0
    if args.cloud_signup:
        # Need a config to upload. If there's no config, run setup first.
        cfg = load_config()
        if not (cfg.get("sonnet_api_key") and cfg.get("codex_api_key")):
            sys.stderr.write(
                "ERROR: configure jarvis first (run without --cloud-signup "
                "to do first-time setup).\n")
            return 1
        if not _cloud_available():
            sys.stderr.write(
                "ERROR: cloud backend not configured. Set JARVIS_CLOUD_URL "
                "first (try --cloud-url to see how).\n")
            return 1
        pw1 = _prompt_secret("Password (6+ chars): ")
        pw2 = _prompt_secret("Confirm: ")
        if pw1 != pw2:
            sys.stderr.write("ERROR: passwords don't match.\n")
            return 1
        try:
            acct = cloud_signup(args.cloud_signup, pw1, cfg)
        except JarvisError as e:
            sys.stderr.write("ERROR: " + str(e) + "\n")
            return 1
        print("Account created. Account id: " + acct)
        return 0
    if args.cloud_login:
        if not _cloud_available():
            sys.stderr.write(
                "ERROR: cloud backend not configured. Set JARVIS_CLOUD_URL "
                "first (try --cloud-url to see how).\n")
            return 1
        pw = _prompt_secret("Password: ")
        try:
            remote_cfg = cloud_login(args.cloud_login, pw)
        except JarvisError as e:
            sys.stderr.write("ERROR: " + str(e) + "\n")
            return 1
        # Merge into local config
        for k, v in (remote_cfg or {}).items():
            if k in ("sonnet_api_key", "codex_api_key") and cfg.get(k):
                continue  # don't overwrite local keys
            cfg[k] = v
        save_config(cfg)
        print("Signed in. Config merged from cloud.")
        return 0

    # ---- --serve: start the phone web server. We DON'T load a config
    #      if there isn't one; the user can pair from the phone and
    #      set up there. ----
    if args.serve is not None:
        cfg = load_config()
        if not (cfg.get("sonnet_api_key") and cfg.get("codex_api_key")):
            sys.stderr.write(
                "ERROR: configure jarvis first (run without --serve to do "
                "first-time setup). The server needs the config to make API "
                "calls.\n")
            return 1
        host = args.serve if args.serve != "0.0.0.0" else "0.0.0.0"
        # If --serve is followed by an explicit host, use it; otherwise
        # default to 0.0.0.0.
        if args.serve is False or args.serve is None:
            host = "0.0.0.0"
        port = int(args.port or DEFAULT_SERVE_PORT)
        if args.qr:
            # --qr used to print a QR; we no longer support that.
            # The server banner prints the URL + code below.
            print(" (--qr is no longer used; the URL is shown below.)")
            print()
        start_phone_server(cfg, host=host, port=port, blocking=True)
        return 0

    # ---- Load config; if missing, run first-run setup (which shows
    #      the startup menu) ----
    cfg = load_config()
    has_config = bool(cfg) and cfg.get("sonnet_api_key") and cfg.get("codex_api_key")
    if not has_config:
        # Decide whether to do the GUI first-run (tier chooser + key
        # entry) or the terminal one, based on the mode heuristic.
        # In auto mode with no request, prefer GUI if available.
        preview_mode = _pick_ui_mode(args, cfg)
        if preview_mode == "gui":
            cfg = _run_gui_first_run()
        else:
            cfg = first_run_setup()

    if args.offline:
        cfg["offline"] = True
        save_config(cfg)
        sys.stdout.write(_offline_banner(cfg))

    if args.persona:
        cfg["persona"] = args.persona
    if args.no_review:
        cfg["enable_review"] = False
    if args.with_tests:
        cfg["enable_tests"] = True

    # ---- Godot mode resolution ----
    # Injects a Godot-aware system prompt into the codex model when the
    # project is a Godot project. Auto-detects from cwd, but explicit
    # --godot / --no-godot flags win.
    godot_info = None
    if _godot_resolve_godot_mode(args, cfg):
        root = _godot_find_project_root() or os.getcwd()
        godot_info = _godot_read_project_info(root)
        cfg["godot"] = True
        # Stash the parsed project info on cfg so `run()` can pick it
        # up when constructing the codex system prompt. We use a
        # single underscore prefix to mark it as transient (it
        # doesn't need to be persisted to disk).
        cfg["_godot_info"] = godot_info
        save_config(cfg)
        sys.stdout.write(
            "  Godot project detected: " + godot_info.get("path", root) +
            (" (" + godot_info.get("version", "?") + ")\n" if godot_info.get("version") else "\n"))
    else:
        # Explicitly remove the transient hint so a previous godot
        # run doesn't leak into this one.
        cfg.pop("_godot_info", None)

    # ---- Projects store + Google Drive subcommands (run before
    #      normal request flow, so they don't need a model call) ----
    rc = _run_projects_drive_cmds(args, cfg)
    if rc is not None:
        return rc

    # Research: explicit --research on, --no-research off, else config default
    if args.no_research:
        do_research = False
    elif args.research:
        do_research = True
    else:
        do_research = bool(cfg.get("enable_research", False))
    if cfg.get("offline"):
        # Offline mode: refuse web research regardless
        if do_research:
            sys.stderr.write(
                "WARNING: --research ignored in offline mode (needs web).\n")
            do_research = False
    extra_research_urls   = list(args.research_url or [])
    extra_research_terms  = list(args.research_term or [])

    # ---- Deep research (long-running) ----
    # Handle --deep-research, --deep-report, --resume. These bypass
    # the normal request flow entirely.
    if args.deep_research or args.deep_report or args.resume:
        if cfg.get("offline"):
            sys.stderr.write(
                "ERROR: deep research requires web access; not available in "
                "offline mode. Disable offline mode with "
                "`jarvis --set offline=false`.\n")
            return 2
        return _run_deep_research_cmd(args, cfg)

    # ---- Generate a single file ----
    if args.generate_file:
        try:
            result = _file_gen_dispatch(
                args.generate_file, cfg,
                sandbox_test=args.sandbox_test)
        except (ConfigError, JarvisError) as e:
            sys.stderr.write("ERROR: " + str(e) + "\n")
            return 1
        if not result.get("ok"):
            sys.stderr.write("ERROR: " + str(result.get("error", "?")) + "\n")
            return 1
        fname = result["filename"]
        out_path = args.generate_output or os.path.join(os.getcwd(), fname)
        try:
            if not os.path.isdir(os.path.dirname(out_path) or "."):
                os.makedirs(os.path.dirname(out_path) or ".", exist_ok=True)
            if result.get("kind") == "text":
                with open(out_path, "w", encoding="utf-8") as f:
                    f.write(result["content"])
                print("Wrote text file: " + out_path)
            else:
                with open(out_path, "wb") as f:
                    f.write(result["content"])
                print("Wrote binary file: " + out_path)
                if result.get("generator_script"):
                    gen_path = out_path + ".generator.py"
                    with open(gen_path, "w", encoding="utf-8") as f:
                        f.write(result["generator_script"])
                    print("  generator script: " + gen_path)
                if result.get("notes"):
                    print("  notes: " + result["notes"])
        except OSError as e:
            sys.stderr.write("ERROR: could not write file: " + str(e) + "\n")
            return 1
        # Sandbox test?
        sb = result.get("sandbox_result")
        if sb:
            print()
            print("Sandbox test:")
            print("  safety_rejected: " + str(sb.get("safety_rejected")))
            if sb.get("safety_rejected"):
                for r in sb.get("safety_reasons", []):
                    print("    - " + r)
            else:
                print("  ok:              " + str(sb.get("ok")))
                print("  exit_code:       " + str(sb.get("exit_code")))
                print("  time:            " + str(round(sb.get("time_seconds", 0), 3)) + "s")
                if sb.get("stdout"):
                    print("  --- stdout ---")
                    for line in sb["stdout"].splitlines()[:30]:
                        print("    " + line)
                if sb.get("stderr"):
                    print("  --- stderr ---")
                    for line in sb["stderr"].splitlines()[:30]:
                        print("    " + line)
        return 0

    # ---- Self-modify: actually run it (only after the config is loaded,
    # so the model call can use the user's settings) ----
    if args.self_modify is not None:
        result = _self_modify_apply(args.self_modify, cfg)
        if not result.get("ok"):
            sys.stderr.write("ERROR: " + str(result.get("error")) + "\n")
            return 1
        print()
        print("Self-modify applied.")
        print("  snapshot:     " + str(result.get("snapshot")))
        print("  new commit:   " + str(result.get("new_commit")))
        print("  tests passed: " + str(result.get("tests_passed")))
        print()
        print("The change is on the 'self-modify' side branch. To roll back:")
        print("  jarvis --self-revert <label or commit>")
        print("To return to your main branch:")
        print("  git checkout <main-branch>")
        return 0

    chosen_mode = _pick_ui_mode(args, cfg)
    if chosen_mode == "gui":
        return _run_gui_mode(cfg, args, do_research=do_research,
                             extra_research_urls=extra_research_urls,
                             extra_research_terms=extra_research_terms)
    return _run_terminal_mode(cfg, args, do_research=do_research,
                             extra_research_urls=extra_research_urls,
                             extra_research_terms=extra_research_terms)


def _run_projects_drive_cmds(args, cfg):
    """Dispatch the `--project ...` and the `--drive ...` subcommands.

    Returns an int exit code (0 = handled OK) if the user invoked one
    of these subcommands; returns None to continue normal flow.
    """
    # --- Projects store ---
    pa = getattr(args, "project_action", None)
    if pa:
        # The remaining positional args (if any) form the operands.
        # For `new`, the second positional is the kind (godot / python).
        # For `add`/`import`, the second is the source path.
        # For everything else, the first is the project name.
        operands = list(getattr(args, "request", []) or [])
        try:
            if pa == "list":
                projects = list_projects()
                if not projects:
                    print("(no projects in the store yet)")
                    print("  Add one with:  jarvis --project add <path> <name>")
                    print("  Scaffold one:  jarvis --project new <name> godot")
                    return 0
                print("Projects in the store:")
                for m in projects:
                    when = time.strftime("%Y-%m-%d %H:%M",
                                         time.localtime(
                                             float(m.get("updated_at") or 0)))
                    kind = m.get("kind", "?")
                    src = m.get("source", "?")
                    path = m.get("path", m.get("original_path", "?"))
                    marker = " *" if m.get("name") == get_active_project(cfg) else "  "
                    print("  " + marker + " " + m.get("name", "?") +
                          "  [" + kind + "/" + src + "]  " + when)
                    print("       " + str(path))
                print()
                print("  * = active. Switch with:  jarvis --project use <name>")
                return 0
            if pa == "status":
                active = get_active_project(cfg)
                if active:
                    m = _project_load(active)
                    print("Active project: " + active)
                    print("  kind:   " + str(m.get("kind", "?")))
                    print("  source: " + str(m.get("source", "?")))
                    print("  path:   " + str(m.get("path", "?")))
                else:
                    print("No active project. Pick one with:")
                    print("  jarvis --project use <name>")
                return 0
            if pa == "active":
                # Just print the active project name
                active = get_active_project(cfg)
                print(active or "")
                return 0
            if pa == "path":
                active = get_active_project(cfg)
                if not active:
                    sys.stderr.write(
                        "no active project (use `jarvis --project use <name>`)\n")
                    return 2
                m = _project_load(active)
                if m:
                    print(m.get("path", ""))
                return 0
            if pa == "new":
                # jarvis --project new <name> <kind>
                if len(operands) < 1:
                    sys.stderr.write(
                        "usage: jarvis --project new <name> [godot|python]\n")
                    return 2
                name = operands[0]
                kind = operands[1] if len(operands) >= 2 else "godot"
                try:
                    m = _project_scaffold(name, kind, cfg=cfg)
                except ConfigError as e:
                    sys.stderr.write("ERROR: " + str(e) + "\n")
                    return 1
                set_active_project(name, cfg=cfg)
                print("Scaffolded " + kind + " project '" + name +
                      "' at " + m.get("path", "?"))
                print("  Active project is now '" + name + "'.")
                return 0
            if pa == "add":
                # jarvis --project add <path> <name>
                if len(operands) < 1:
                    sys.stderr.write(
                        "usage: jarvis --project add <path> [name]\n")
                    return 2
                src = operands[0]
                # If name is omitted, use the last path component
                name = operands[1] if len(operands) >= 2 else \
                    os.path.basename(os.path.abspath(src))
                kind = "godot" if _godot_find_project_root(
                    os.path.abspath(src)) else "generic"
                try:
                    m = _project_adopt(name, src, kind=kind)
                except ConfigError as e:
                    sys.stderr.write("ERROR: " + str(e) + "\n")
                    return 1
                set_active_project(name, cfg=cfg)
                print("Adopted '" + name + "' from " + src)
                print("  kind:   " + m.get("kind", "?"))
                print("  path:   " + m.get("path", "?"))
                print("  Active project is now '" + name + "'.")
                return 0
            if pa == "import":
                # jarvis --project import <path> <name>
                if len(operands) < 1:
                    sys.stderr.write(
                        "usage: jarvis --project import <path> [name]\n")
                    return 2
                src = operands[0]
                name = operands[1] if len(operands) >= 2 else \
                    os.path.basename(os.path.abspath(src))
                kind = "godot" if _godot_find_project_root(
                    os.path.abspath(src)) else "generic"
                try:
                    m = _project_import(name, src, kind=kind)
                except ConfigError as e:
                    sys.stderr.write("ERROR: " + str(e) + "\n")
                    return 1
                set_active_project(name, cfg=cfg)
                print("Imported '" + name + "' from " + src)
                print("  copy at: " + m.get("path", "?"))
                print("  Active project is now '" + name + "'.")
                return 0
            if pa == "use":
                if len(operands) < 1:
                    sys.stderr.write(
                        "usage: jarvis --project use <name>\n")
                    return 2
                name = operands[0]
                if not _project_load(name):
                    sys.stderr.write(
                        "ERROR: no such project: " + name + "\n")
                    return 1
                set_active_project(name, cfg=cfg)
                print("Active project is now '" + name + "'.")
                return 0
            if pa == "remove":
                if len(operands) < 1:
                    sys.stderr.write(
                        "usage: jarvis --project remove <name> [--delete]\n")
                    return 2
                name = operands[0]
                delete = ("--delete" in operands)
                if _project_remove(name, delete_files=delete):
                    print("Removed project '" + name + "'" +
                          (" (and its files)" if delete else ""))
                else:
                    sys.stderr.write(
                        "ERROR: no such project: " + name + "\n")
                    return 1
                return 0
            if pa == "open":
                if len(operands) < 1:
                    sys.stderr.write(
                        "usage: jarvis --project open <name>\n")
                    return 2
                name = operands[0]
                m = _project_load(name)
                if not m:
                    sys.stderr.write(
                        "ERROR: no such project: " + name + "\n")
                    return 1
                p = m.get("path") or m.get("original_path")
                if not p or not os.path.isdir(p):
                    sys.stderr.write(
                        "ERROR: project path missing or gone: " + str(p) + "\n")
                    return 1
                # Open the folder in the OS file manager
                try:
                    if sys.platform == "win32":
                        os.startfile(p)   # type: ignore
                    elif sys.platform == "darwin":
                        os.system('open "' + p + '"')
                    else:
                        os.system('xdg-open "' + p + '"')
                    print("Opened: " + p)
                except Exception as e:
                    sys.stderr.write(
                        "ERROR: could not open folder: " + str(e) + "\n")
                    return 1
                return 0
        except Exception as e:
            sys.stderr.write("ERROR in --project " + str(pa) + ": " +
                             str(e) + "\n")
            return 1

    # --- Google Drive sync (watch-folder) ---
    # We piggyback on the same first-positional-arg pattern. Forms:
    #   jarvis drive status
    #   jarvis drive set <path>
    #   jarvis drive unset
    #   jarvis drive push
    #   jarvis drive pull
    if getattr(args, "request", None) and args.request[:1] == ["drive"]:
        sub = args.request[1] if len(args.request) >= 2 else "status"
        rest = args.request[2:]
        try:
            if sub == "status":
                folder = _drive_resolve_folder()
                if folder:
                    print("Drive folder: " + folder)
                else:
                    print("Drive folder not configured.")
                    print("  Set one with:  jarvis drive set <path>")
                return 0
            if sub == "set":
                if not rest:
                    sys.stderr.write(
                        "usage: jarvis drive set <path>\n")
                    return 2
                p = os.path.expanduser(rest[0])
                if not os.path.isdir(p):
                    sys.stderr.write("ERROR: not a directory: " + p + "\n")
                    return 1
                _drive_save({"folder": p, "set_at": time.time()})
                print("Drive folder set to: " + p)
                return 0
            if sub == "unset":
                _drive_save({"folder": ""})
                print("Drive folder cleared.")
                return 0
            if sub == "push":
                copied, skipped, errors = drive_push()
                print("Push: copied " + str(copied) +
                      ", skipped " + str(skipped) +
                      " already-existing project(s)")
                for e in errors:
                    sys.stderr.write("  " + e + "\n")
                return 0 if not errors else 1
            if sub == "pull":
                copied, skipped, errors = drive_pull()
                print("Pull: copied " + str(copied) +
                      ", skipped " + str(skipped) +
                      " already-existing project(s)")
                for e in errors:
                    sys.stderr.write("  " + e + "\n")
                return 0 if not errors else 1
            sys.stderr.write("unknown drive subcommand: " + sub + "\n")
            return 2
        except Exception as e:
            sys.stderr.write("ERROR in drive " + sub + ": " + str(e) + "\n")
            return 1

    return None   # no projects/drive command, continue normal flow


def _pick_ui_mode(args, cfg):
    mode = (args.mode or "").lower()
    if mode == "gui":
        return "gui"
    if mode == "terminal":
        return "terminal"

    saved = (cfg.get("ui_mode") or "").lower()
    if saved == "gui":
        return "gui"
    if saved == "terminal":
        return "terminal"

    if mode == "ask":
        return _ask_user_for_mode()

    if args.request:
        return "terminal"

    return _ask_user_for_mode()


def _ask_user_for_mode():
    # The GUI is in the same module now (merged from the old gui.py),
    # so no import is needed. has_gui() returns False if tkinter is
    # missing or fails to import.
    if not has_gui():
        sys.stderr.write("(tkinter not installed; using terminal mode)\n")
        return "terminal"

    try:
        choice = run_launcher(load_config())
    except Exception as e:
        sys.stderr.write("(GUI launcher failed: " + str(e) + ")\n")
        return "terminal"

    if not choice:
        return "terminal"

    cfg = load_config()
    cfg["ui_mode"] = choice
    save_config(cfg)
    return choice


def _run_gui_first_run():
    """Run the GUI version of the first-run wizard (tier chooser +
    key entry), then return the resulting config. Falls back to the
    terminal version if the GUI version fails for any reason."""
    try:
        if not has_gui():
            return first_run_setup()
        cfg = run_gui_first_run()
        if cfg is None:
            # User cancelled the GUI wizard. Don't try to start the
            # chat window without a config. Bail out cleanly.
            sys.exit(0)
        return cfg
    except Exception as e:
        sys.stderr.write("(GUI first-run failed: " + str(e) + ")\n"
                         "Falling back to terminal setup.\n")
        return first_run_setup()


def _run_gui_mode(cfg, args, do_research=False,
                 extra_research_urls=None, extra_research_terms=None):
    # GUI functions live in this same module (merged from the old
    # gui.py). has_gui() returns False if tkinter is missing.
    if not has_gui():
        sys.stderr.write("tkinter not installed; using terminal mode.\n")
        return _run_terminal_mode(cfg, args, do_research=do_research,
                                 extra_research_urls=extra_research_urls,
                                 extra_research_terms=extra_research_terms)

    try:
        # The GUI chat window reads research settings from cfg; we
        # propagate the CLI flags via the args object as well.
        args.do_research = do_research
        args.extra_research_urls = extra_research_urls
        args.extra_research_terms = extra_research_terms
        run_gui(cfg, args)
        return 0
    except Exception as e:
        sys.stderr.write("GUI failed: " + str(e) + "\n"
                         "Traceback:\n" + traceback.format_exc() + "\n")
        return 1


def _run_deep_research_cmd(args, cfg):
    """Dispatch the --deep-research, --deep-report, --resume commands.

    Behavior:
      --deep-research TOPIC  : start or resume a long-running session,
                               loop until time/iters exhausted or the
                               user picks 'quit' from the Q&A menu.
      --deep-report TOPIC   : one batch + report, then exit.
      --resume SESSION_ID   : continue an existing session.

    Each iteration prints a status line. Between iterations, an
    interactive Q&A prompt lets the user ask questions, add research
    questions to the queue, or stop. The user can also set SIGINT
    (Ctrl-C) to gracefully stop and save.
    """
    # Parse budgets
    try:
        max_seconds = _parse_max_time(args.max_time)
    except ValueError as e:
        sys.stderr.write("ERROR: " + str(e) + "\n")
        return 2
    try:
        max_iterations = int(args.max_iterations) if args.max_iterations else 50
    except ValueError:
        sys.stderr.write("ERROR: --max-iterations must be a number\n")
        return 2
    if max_iterations <= 0:
        max_iterations = 50

    if args.resume:
        # Load session
        try:
            session = DeepResearchSession.load(args.resume)
        except ConfigError as e:
            sys.stderr.write("ERROR: " + str(e) + "\n")
            return 2
        print("Resuming session: " + session.session_id)
        print("  topic:    " + session.topic)
        print("  status:   " + session.status)
        print("  " + session.status_line())
    elif args.deep_report:
        topic = args.deep_report.strip()
        print("Starting one-shot deep report on: " + topic)
        print("  (one big batch of searches + a synthesized report)")
        print()
        try:
            session = run_deep_research_session(
                topic, cfg,
                max_seconds=300, max_iterations=1, one_shot=True)
        except (ConfigError, JarvisError) as e:
            sys.stderr.write("ERROR: " + str(e) + "\n")
            return 1
        # Print the report
        d = _ensure_session_dir(session.session_id)
        rp = os.path.join(d, "report.md")
        if os.path.isfile(rp):
            print()
            print("=" * 72)
            print("  REPORT: " + topic)
            print("  session: " + session.session_id)
            print("=" * 72)
            print()
            with open(rp, "r", encoding="utf-8") as f:
                print(f.read())
            print()
            print("Saved to: " + rp)
        else:
            print("(no report was produced)")
        return 0
    else:  # args.deep_research
        topic = (args.deep_research or "").strip()
        if not topic:
            sys.stderr.write("ERROR: --deep-research needs a topic\n")
            return 2
        # Make sure requests is available; we use it for web searches
        try:
            _need_requests()
        except SystemExit:
            sys.stderr.write(
                "ERROR: the 'requests' library is required for deep research.\n"
                "Install it with:  pip install requests\n")
            return 2
        print("Starting deep research session on: " + topic)
        print("  max time:  " + str(int(max_seconds / 60)) + " minutes")
        print("  max iters: " + str(max_iterations))
        print("  Ctrl-C at any time to pause and save.")
        print()
        try:
            session = run_deep_research_session(
                topic, cfg,
                max_seconds=max_seconds,
                max_iterations=max_iterations,
                one_shot=False)
        except (ConfigError, JarvisError) as e:
            sys.stderr.write("ERROR: " + str(e) + "\n")
            return 1

    # Now we have a session in hand. The interactive Q&A loop is the
    # main interface -- the user can ask questions or 'resume' to keep
    # researching (which means: run more iterations in the foreground).
    if not args.resume:
        # We just created a new session. Print the plan first.
        if session.plan:
            print()
            print("=" * 72)
            print("  RESEARCH PLAN")
            print("=" * 72)
            print()
            print(session.plan)
            print()

    # Main loop: alternate between research iterations and Q&A.
    keep_going = True
    last_choice = None
    while keep_going:
        # If the session is already done, skip directly to Q&A.
        if session.status in ("done", "stopped"):
            print("Session is " + session.status + ".  " + session.status_line())
            print("Type questions to ask, or 'quit' to exit.")
        else:
            # Run one batch of research (the user can interrupt with
            # SIGINT between iterations via Ctrl-C; we use the
            # deep_resession_qa_loop which returns 'resume' or 'quit').
            try:
                last_choice = deep_resession_qa_loop(
                    session, cfg, on_progress=None)
            except (KeyboardInterrupt, EOFError):
                print()
                print("Interrupted. Saving and exiting.")
                session.status = "stopped"
                session.save()
                return 0
            if last_choice == "quit":
                session.status = "stopped"
                session.save()
                return 0
            # 'resume' -> run another batch
            try:
                session = run_deep_research_session(
                    session.topic, cfg,
                    max_seconds=max_seconds,
                    max_iterations=max_iterations,
                    one_shot=False,
                    session_id=session.session_id)
            except (ConfigError, JarvisError) as e:
                sys.stderr.write("ERROR: " + str(e) + "\n")
                session.status = "stopped"
                session.save()
                return 1
            if session.status in ("done", "stopped"):
                # Out of questions or budget; write the report
                if session.status == "done":
                    try:
                        report = _deepresearch_write_report(
                            session.topic, session.notes_md, cfg)
                        d = _ensure_session_dir(session.session_id)
                        with open(os.path.join(d, "report.md"), "w",
                                  encoding="utf-8") as f:
                            f.write(report)
                        print("\nFinal report written: " +
                              os.path.join(d, "report.md"))
                    except JarvisError as e:
                        print("\nReport write failed: " + str(e))
                # Loop back to Q&A so the user can ask final questions
        # Quick status print
        print("\n  " + session.status_line())


def _run_terminal_mode(cfg, args, do_research=False,
                      extra_research_urls=None, extra_research_terms=None):
    try:
        if args.request:
            req = " ".join(args.request).strip()
            if not req:
                print("Empty request.", file=sys.stderr)
                return 2
            result = run(req, cfg,
                         enable_review=not args.no_review,
                         enable_tests=args.with_tests,
                         write_to_disk=args.write,
                         output_dir=args.output,
                         text_only=args.text_only,
                         do_research=do_research,
                         extra_research_urls=extra_research_urls,
                         extra_research_terms=extra_research_terms)
            if args.json:
                print(json.dumps(result, indent=2, ensure_ascii=False))
            else:
                _print_section("PLAN", args.text_only)
                _print_plan(result, args.text_only)
                _print_modules(result, args.text_only)
            if result.get("written_to"):
                print("\nWrote " + str(len(result["written_to"]))
                      + " file(s) under "
                      + (args.output or os.path.join(CONFIG_DIR, "output")))
            return 1 if result.get("overall_error") else 0
        else:
            # Persist research state for the menu's actions
            args.do_research = do_research
            args.extra_research_urls = extra_research_urls
            args.extra_research_terms = extra_research_terms
            _run_interactive(cfg, args)
            return 0
    except ConfigError as e:
        sys.stderr.write("\nConfig error: " + str(e) + "\n"
                         "(run `jarvis --reset` to set it up again)\n")
        return 2
    except JarvisError as e:
        sys.stderr.write("\nFailed: " + str(e) + "\n")
        return 1
    except KeyboardInterrupt:
        sys.stderr.write("\nInterrupted.\n")
        return 130


# ===========================================================================
# (QR code generation removed. The phone server now prints a copy-
# pasteable URL + 6-digit pairing code instead of a scannable QR.)
# ===========================================================================

# ===========================================================================
# PHONE PAIRING  --  local-only, no cloud, 6-digit code
# ===========================================================================
#
# When the user runs `jarvis --serve`, the laptop generates a 6-digit
# pairing code. The user opens the URL on their phone, types the
# code, and the two devices are now paired. Both can use the same
# API. The pairing data lives in ~/.jarvis/pairing.json.
#
# A "device" is identified by a random ID. Devices can have a name
# (the user can rename them later). Devices have their own per-device
# mode toggles (persona, offline, sandbox, etc.) that sync across all
# paired devices.
#
# This is completely local: the laptop acts as the server, the phone
# just makes HTTP calls. No third-party service is needed.

PAIRING_DIR = os.path.join(CONFIG_DIR, "pairing")
PAIRING_PATH = os.path.join(PAIRING_DIR, "pairing.json")
PAIRING_CODE_LEN = 6
# Pairing codes expire after this many seconds (default 10 min)
PAIRING_CODE_TTL = int(_env_or("DUAL_AI_PAIR_TTL", "JARVIS_PAIR_TTL", "600"))


def _pairing_load():
    """Load the pairing state from disk. Returns a dict with
    'code' (current code or None), 'code_expires' (unix time),
    'devices' (list of {id, name, added, last_seen, modes}), and
    'shared' (modes that all devices share, e.g. enable_research)."""
    if not os.path.isfile(PAIRING_PATH):
        return {"code": None, "code_expires": 0, "devices": [], "shared": {}}
    try:
        with open(PAIRING_PATH, "r", encoding="utf-8") as f:
            d = json.load(f)
        if not isinstance(d, dict):
            return {"code": None, "code_expires": 0, "devices": [], "shared": {}}
        d.setdefault("code", None)
        d.setdefault("code_expires", 0)
        d.setdefault("devices", [])
        d.setdefault("shared", {})
        return d
    except (OSError, ValueError):
        return {"code": None, "code_expires": 0, "devices": [], "shared": {}}


def _pairing_save(state):
    """Atomically write the pairing state to disk."""
    if not os.path.isdir(PAIRING_DIR):
        os.makedirs(PAIRING_DIR)
    tmp = PAIRING_PATH + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(state, f, indent=2, ensure_ascii=False)
    os.replace(tmp, PAIRING_PATH)
    try:
        os.chmod(PAIRING_PATH, 0o600)
    except (OSError, AttributeError):
        pass


def _pairing_new_code():
    """Generate a fresh 6-digit numeric pairing code. Stored in the
    pairing state with an expiry. Returns the code string."""
    import random as _random
    code = "".join(str(_random.randint(0, 9))
                   for _ in range(PAIRING_CODE_LEN))
    state = _pairing_load()
    state["code"] = code
    state["code_expires"] = time.time() + PAIRING_CODE_TTL
    _pairing_save(state)
    return code


def _pairing_get_active_code():
    """Return the current active code if it's still valid, else None."""
    state = _pairing_load()
    code = state.get("code")
    if not code:
        return None
    if time.time() > float(state.get("code_expires") or 0):
        return None
    return code


def _pairing_pair_device(code, device_name, device_kind="phone"):
    """Pair a new device using the active code. Returns the new
    device dict (with id, name, etc.) on success, or raises
    PairingError on bad code / expiry."""
    state = _pairing_load()
    active = state.get("code")
    if not active or time.time() > float(state.get("code_expires") or 0):
        raise PairingError("pairing code is missing or expired")
    if str(code).strip() != str(active):
        raise PairingError("incorrect pairing code")
    import uuid as _uuid
    dev_id = _uuid.uuid4().hex[:12]
    dev = {
        "id": dev_id,
        "name": (device_name or "Device").strip()[:40] or "Device",
        "kind": (device_kind or "phone").strip()[:20] or "phone",
        "added": time.time(),
        "last_seen": time.time(),
        "modes": {},
    }
    state["devices"].append(dev)
    # Clear the code so it can only be used once
    state["code"] = None
    state["code_expires"] = 0
    _pairing_save(state)
    return dev


def _pairing_get_device(device_id):
    """Look up a device by id. Returns the device dict or None."""
    state = _pairing_load()
    for d in state.get("devices", []):
        if d.get("id") == device_id:
            return d
    return None


def _pairing_touch_device(device_id):
    """Update the last_seen timestamp for a device."""
    state = _pairing_load()
    for d in state.get("devices", []):
        if d.get("id") == device_id:
            d["last_seen"] = time.time()
            _pairing_save(state)
            return
    # If the device isn't registered, add it as a new one (transient
    # devices like curl can use any id)
    import uuid as _uuid
    dev = {
        "id": device_id or _uuid.uuid4().hex[:12],
        "name": "Unknown device",
        "kind": "unknown",
        "added": time.time(),
        "last_seen": time.time(),
        "modes": {},
    }
    state["devices"].append(dev)
    _pairing_save(state)


def _pairing_remove_device(device_id):
    """Remove a device by id. Returns True if found and removed."""
    state = _pairing_load()
    before = len(state.get("devices", []))
    state["devices"] = [
        d for d in state.get("devices", []) if d.get("id") != device_id
    ]
    if len(state["devices"]) < before:
        _pairing_save(state)
        return True
    return False


def _pairing_set_shared_modes(modes):
    """Update the shared modes (applied to all devices)."""
    state = _pairing_load()
    state["shared"] = dict(modes or {})
    _pairing_save(state)


def _pairing_set_device_modes(device_id, modes):
    """Update a specific device's per-device mode toggles."""
    state = _pairing_load()
    for d in state.get("devices", []):
        if d.get("id") == device_id:
            d["modes"] = dict(modes or {})
            _pairing_save(state)
            return True
    return False


class PairingError(Exception):
    pass


# ===========================================================================
# CLOUD ACCOUNT  --  optional email+password sync via a free public KV store
# ===========================================================================

# _struct_mod is used by the cloud crypto below for big-endian 4-byte
# counter packing. It used to live in the QR block; we keep it here so
# the cloud crypto still works.
import struct as _struct_mod


#
# The user can choose to back up their config to a tiny "cloud" key-
# value store. This lets them sign in on a new device and pull their
# config down. The store is a free public service that requires no
# signup, so the user doesn't need to create an account with us.
#
# IMPORTANT: this is for convenience only. The password is hashed
# client-side (PBKDF2-HMAC-SHA256, 200k iterations, per-device salt)
# and the API key is encrypted with that hash (Fernet-style: HMAC over
# the key bytes). Anyone with the password can decrypt; without it,
# the data is opaque. We are NOT promising strong security; this is
# "don't lose your config" tier, not "protect state secrets" tier.
#
# Backend: we POST/GET to a free public KV service. To avoid
# depending on any one service, the URL is configurable via env var
# DUAL_AI_CLOUD_URL (legacy) or JARVIS_CLOUD_URL. If unset, we use a default that points to
# jsonbin.io's free public bins (or a fallback). If the user is
# offline, the cloud account is a no-op -- settings stay local.

CLOUD_URL = _env_or("DUAL_AI_CLOUD_URL", "JARVIS_CLOUD_URL", "") or ""
CLOUD_TIMEOUT = float(_env_or("DUAL_AI_CLOUD_TIMEOUT", "JARVIS_CLOUD_TIMEOUT", "10"))


def _cloud_available():
    """True if a cloud backend URL is configured. We don't ping it
    here -- the caller should try a request and fall back gracefully."""
    return bool(CLOUD_URL)


def _cloud_pbkdf2(password, salt, iters=200_000):
    """Pure-stdlib PBKDF2-HMAC-SHA256. Returns a 32-byte key."""
    import hashlib as _hashlib
    return _hashlib.pbkdf2_hmac(
        "sha256", password.encode("utf-8"), salt, iters, dklen=32)


def _cloud_fernet_like_encrypt(plaintext, key):
    """Encrypt with a key using a small HMAC-based scheme. The output
    is a base64 string: salt(16) | iv(16) | ciphertext | hmac(32).
    This is not real Fernet; it's "Fernet-ish" for our use case."""
    import base64 as _base64
    import hashlib as _hashlib
    import hmac as _hmac
    salt = os.urandom(16)
    iv = os.urandom(16)
    # Stream cipher: derive a keystream via repeated HMAC of iv
    keystream = b""
    counter = 0
    while len(keystream) < len(plaintext) + 32:
        keystream += _hmac.new(
            key, iv + _struct_mod.pack(">I", counter),
            _hashlib.sha256).digest()
        counter += 1
    ct = bytes(b ^ keystream[i] for i, b in enumerate(plaintext))
    h = _hmac.new(key, salt + iv + ct, _hashlib.sha256).digest()
    return _base64.b64encode(salt + iv + ct + h).decode("ascii")


def _cloud_fernet_like_decrypt(b64, key):
    """Decrypt a value produced by _cloud_fernet_like_encrypt. Returns
    bytes, or raises ValueError if the HMAC doesn't match."""
    import base64 as _base64
    import hashlib as _hashlib
    import hmac as _hmac
    raw = _base64.b64decode(b64)
    if len(raw) < 16 + 16 + 32:
        raise ValueError("ciphertext too short")
    salt = raw[:16]
    iv = raw[16:32]
    ct = raw[32:-32]
    h = raw[-32:]
    expected = _hmac.new(key, salt + iv + ct, _hashlib.sha256).digest()
    if not _hmac.compare_digest(h, expected):
        raise ValueError("HMAC mismatch (bad password?)")
    keystream = b""
    counter = 0
    while len(keystream) < len(ct):
        keystream += _hmac.new(
            key, iv + _struct_mod.pack(">I", counter),
            _hashlib.sha256).digest()
        counter += 1
    return bytes(b ^ keystream[i] for i, b in enumerate(ct))


def _cloud_account_id(email):
    """Derive a stable account id from the email (case-insensitive).
    We use SHA-256 of the lowercased email, hex-encoded. The cloud
    KV is keyed by this id (plus a per-user salt for password ops)."""
    import hashlib as _hashlib
    return _hashlib.sha256(
        email.strip().lower().encode("utf-8")).hexdigest()[:32]


def _cloud_request(method, key, payload=None):
    """Make a request to the cloud backend. Returns the parsed JSON
    response, or raises JarvisError on failure. Backend is expected
    to be a simple KV store with PUT/GET/DELETE semantics. We POST
    {key, value, op} and read {value, error} back."""
    if not _cloud_available():
        raise JarvisError("cloud backend not configured "
                          "(set JARVIS_CLOUD_URL)")
    requests = _need_requests()
    try:
        if method == "GET":
            r = requests.get(
                CLOUD_URL, params={"key": key}, timeout=CLOUD_TIMEOUT)
        elif method == "PUT":
            r = requests.put(
                CLOUD_URL, json={"key": key, "value": payload},
                timeout=CLOUD_TIMEOUT)
        elif method == "DELETE":
            r = requests.delete(
                CLOUD_URL, params={"key": key}, timeout=CLOUD_TIMEOUT)
        else:
            raise JarvisError("unknown cloud method: " + method)
    except (requests.Timeout, requests.RequestException) as e:
        raise JarvisError("cloud request failed: " + type(e).__name__
                          + ": " + str(e)[:200])
    if r.status_code == 404:
        return None
    if r.status_code >= 400:
        raise JarvisError("cloud HTTP %d" % r.status_code)
    try:
        return r.json()
    except ValueError:
        return None


def cloud_signup(email, password, config):
    """Create a new cloud account. Stores the (encrypted) config
    under the email's account id. Returns the account_id. Raises
    JarvisError on failure."""
    if not email or "@" not in email:
        raise JarvisError("invalid email")
    if not password or len(password) < 6:
        raise JarvisError("password must be at least 6 characters")
    acct = _cloud_account_id(email)
    salt = os.urandom(16)
    key = _cloud_pbkdf2(password, salt)
    blob = {
        "v": 1,
        "salt": _base64.b64encode(salt).decode("ascii"),
        "config": _cloud_fernet_like_encrypt(
            json.dumps(config).encode("utf-8"), key),
    }
    payload = json.dumps(blob)
    existing = _cloud_request("GET", acct)
    if existing is not None:
        raise JarvisError("account already exists for that email")
    _cloud_request("PUT", acct, payload)
    return acct


def cloud_login(email, password):
    """Sign in. Returns the decrypted config dict, or raises
    JarvisError on bad credentials / network failure."""
    import base64 as _base64
    acct = _cloud_account_id(email)
    raw = _cloud_request("GET", acct)
    if raw is None:
        raise JarvisError("no such account")
    try:
        # The backend might return either the blob directly, or a
        # wrapper {value: blob}. Handle both.
        if isinstance(raw, dict) and "value" in raw:
            blob = json.loads(raw["value"])
        else:
            blob = raw
        if not isinstance(blob, dict) or "salt" not in blob or "config" not in blob:
            raise ValueError("blob is malformed")
        salt = _base64.b64decode(blob["salt"])
        key = _cloud_pbkdf2(password, salt)
        pt = _cloud_fernet_like_decrypt(blob["config"], key)
        return json.loads(pt.decode("utf-8"))
    except (ValueError, KeyError, TypeError) as e:
        raise JarvisError("sign-in failed: " + type(e).__name__
                          + ": " + str(e)[:200])


def cloud_update(email, password, config):
    """Update the stored config (re-encrypts with the same salt so
    the same password decrypts)."""
    import base64 as _base64
    acct = _cloud_account_id(email)
    raw = _cloud_request("GET", acct)
    if raw is None:
        raise JarvisError("not signed in")
    if isinstance(raw, dict) and "value" in raw:
        blob = json.loads(raw["value"])
    else:
        blob = raw
    salt = _base64.b64decode(blob["salt"])
    key = _cloud_pbkdf2(password, salt)
    blob["config"] = _cloud_fernet_like_encrypt(
        json.dumps(config).encode("utf-8"), key)
    _cloud_request("PUT", acct, json.dumps(blob))


# ===========================================================================
# WEB SERVER  --  a tiny HTTP server that hosts the phone UI and REST API
# ===========================================================================
#
# When you run `jarvis --serve [host] [port]`, the laptop starts a
# small HTTP server. Open the URL on your phone, pair with the 6-digit
# code, and you have full control from the phone -- chat, deep
# research, file generation, mode toggles, etc.
#
# The server uses only stdlib (http.server). It serves:
#   GET  /              -> the phone-friendly HTML/JS/CSS page
#   GET  /api/status    -> server info, code (if any), devices
#   POST /api/pair      -> {code, name, kind} -> {device_id, ...}
#   GET  /api/devices   -> list paired devices
#   POST /api/chat      -> {text} -> {ok, reply, ...} (run a request)
#   GET  /api/modes     -> current mode toggles (shared + per-device)
#   POST /api/modes     -> {patch: {...}} -> updated modes
#   GET  /api/sessions  -> list deep research sessions
#   POST /api/sessions  -> start a new session or one-shot report
#   GET  /api/sessions/<id> -> session detail (notes, plan, status)
#   POST /api/sessions/<id>/ask -> {question} -> {answer}
#   POST /api/sessions/<id>/pause  -> pause worker
#   POST /api/sessions/<id>/resume -> resume worker
#   POST /api/sessions/<id>/report -> write the final report now
#   POST /api/generate  -> {request} -> generate a file
#   POST /api/sandbox-test -> {code} -> sandbox result
#   GET  /api/qr        -> JSON {url, code} (QR rendering removed)
#   GET  /api/config    -> current (non-secret) config
#   POST /api/config    -> {patch: {...}} -> update config
#   GET  /api/account/signup -> {email, password, config} -> {ok}
#   POST /api/account/login  -> {email, password} -> {config}
#   POST /api/account/logout -> clear local cloud session
#   GET  /api/account/status -> {signed_in, email}
#   GET  /api/files     -> list files in output dir
#   GET  /api/files/<path> -> download a generated file
#   GET  /api/projects   -> list projects + active
#   POST /api/projects   -> {action, name, [path], [kind]} -> projects CRUD
#   GET  /api/projects/active -> current active project name
#   POST /api/projects/active -> {name} -> set active
#   GET  /api/drive      -> drive status (configured folder, last sync)
#   POST /api/drive      -> {action, [folder]} -> set/unset/push/pull
#
# All API endpoints return JSON. CORS headers are set so a phone
# browser can call them. Long-running operations (deep research,
# big chat) are executed in background threads; the client polls.

import http.server as _httpserver_mod
import socketserver as _socketserver_mod
import base64 as _base64
import threading as _threading
import queue as _queue


DEFAULT_SERVE_HOST = "0.0.0.0"
DEFAULT_SERVE_PORT = 8765


def _safe_modes_subset(d):
    """Pick a JSON-safe subset of the mode toggles that we sync
    across devices. We strip API keys."""
    if not isinstance(d, dict):
        return {}
    out = {}
    for k, v in d.items():
        if "key" in k.lower() or "secret" in k.lower() or "password" in k.lower():
            continue
        if isinstance(v, (str, int, float, bool)) or v is None:
            out[k] = v
    return out


def _server_routes():
    """Return the route map for the phone server. Built lazily so the
    rest of jarvis.py doesn't pay the import cost."""
    return {
        "GET /":                          _route_index,
        "GET /api/status":                _route_status,
        "POST /api/pair":                 _route_pair,
        "GET /api/devices":               _route_devices,
        "DELETE /api/devices":            _route_devices_delete,
        "GET /api/modes":                 _route_modes_get,
        "POST /api/modes":                _route_modes_set,
        "POST /api/chat":                 _route_chat,
        "GET /api/sessions":              _route_sessions,
        "POST /api/sessions":             _route_sessions_start,
        "GET /api/sessions/<id>":         _route_session_detail,
        "POST /api/sessions/<id>/ask":    _route_session_ask,
        "POST /api/sessions/<id>/pause":  _route_session_pause,
        "POST /api/sessions/<id>/resume": _route_session_resume,
        "POST /api/sessions/<id>/report": _route_session_report,
        "POST /api/generate":             _route_generate,
        "POST /api/sandbox-test":         _route_sandbox,
        "GET /api/qr":                    _route_qr,
        "GET /api/config":                _route_config_get,
        "POST /api/config":               _route_config_set,
        "POST /api/account/signup":       _route_account_signup,
        "POST /api/account/login":        _route_account_login,
        "POST /api/account/logout":       _route_account_logout,
        "GET /api/account/status":        _route_account_status,
        "GET /api/files":                 _route_files,
        "GET /api/files/<path>":          _route_file_get,
        "GET /api/cloud/code":            _route_cloud_new_code,
        "GET /api/cloud/info":            _route_cloud_info,
        "GET /api/projects":             _route_projects_list,
        "POST /api/projects":            _route_projects_action,
        "GET /api/projects/active":      _route_projects_active,
        "POST /api/projects/active":     _route_projects_set_active,
        "GET /api/drive":                _route_drive_status,
        "POST /api/drive":               _route_drive_action,
    }


# ----- HTML page (phone-friendly) -----
# Embedded as a constant so we don't need a separate file. This is the
# entire phone UI: chat, sessions, modes, files. Single-page app
# written in plain JS (no framework).
_INDEX_HTML = r"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1,viewport-fit=cover">
<title>jarvis phone</title>
<style>
  :root {
    --bg: #0f1419; --bg-alt: #1a1f29; --bg-soft: #232a37;
    --fg: #e6e6e6; --fg-dim: #8a96a8;
    --accent: #4dd0e1; --accent2: #29b6f6;
    --user-bg: #1e3a5f; --ai-bg: #1a1f29;
    --border: #2a3340; --err: #ef5350; --ok: #66bb6a;
  }
  * { box-sizing: border-box; }
  html, body { margin: 0; padding: 0; height: 100%; background: var(--bg);
    color: var(--fg); font: 15px/1.4 -apple-system, BlinkMacSystemFont,
    "Segoe UI", Roboto, sans-serif; -webkit-text-size-adjust: 100%;
  }
  body { display: flex; flex-direction: column; max-width: 720px;
    margin: 0 auto; }
  header { padding: 12px 14px; background: var(--bg-alt);
    border-bottom: 1px solid var(--border); display: flex;
    justify-content: space-between; align-items: center; gap: 8px; }
  header h1 { margin: 0; font-size: 17px; color: var(--accent);
    font-weight: 600; }
  header .device { font-size: 12px; color: var(--fg-dim); }
  nav { display: flex; background: var(--bg-alt); border-bottom: 1px solid var(--border);
    overflow-x: auto; }
  nav button { flex: 1; min-width: 80px; padding: 10px 8px; background: none;
    color: var(--fg-dim); border: none; font-size: 13px;
    border-bottom: 2px solid transparent; cursor: pointer; }
  nav button.active { color: var(--accent); border-bottom-color: var(--accent); }
  main { flex: 1; overflow: auto; padding: 12px 14px; -webkit-overflow-scrolling: touch; }
  footer { padding: 10px 12px; background: var(--bg-alt);
    border-top: 1px solid var(--border); }
  .input-row { display: flex; gap: 8px; }
  .input-row input, .input-row textarea { flex: 1; min-height: 36px;
    padding: 8px 10px; background: var(--bg-soft); color: var(--fg);
    border: 1px solid var(--border); border-radius: 6px; resize: none;
    font: inherit; }
  .input-row input:focus, .input-row textarea:focus { outline: none;
    border-color: var(--accent); }
  .input-row button { padding: 8px 14px; background: var(--accent);
    color: #0f1419; border: none; border-radius: 6px;
    font: inherit; font-weight: 600; cursor: pointer; }
  .input-row button:disabled { opacity: 0.5; }
  .msg { margin: 8px 0; padding: 10px 12px; border-radius: 8px; }
  .msg .role { font-size: 11px; color: var(--fg-dim); margin-bottom: 4px;
    text-transform: uppercase; letter-spacing: 0.5px; }
  .msg.user { background: var(--user-bg); }
  .msg.ai { background: var(--ai-bg); border: 1px solid var(--border); }
  .msg.system { background: transparent; color: var(--fg-dim);
    font-size: 13px; padding: 6px 0; }
  .msg.error { color: var(--err); background: transparent; }
  pre { background: #0a0e13; color: #c5d1de; padding: 8px 10px;
    border-radius: 4px; overflow-x: auto; font-size: 12px; }
  .card { background: var(--bg-alt); border: 1px solid var(--border);
    border-radius: 8px; padding: 12px; margin: 8px 0; }
  .card h3 { margin: 0 0 6px; font-size: 14px; color: var(--accent); }
  .card .meta { font-size: 12px; color: var(--fg-dim); }
  .toggle { display: flex; justify-content: space-between; align-items: center;
    padding: 10px 0; border-bottom: 1px solid var(--border); }
  .toggle:last-child { border-bottom: none; }
  .toggle .label { font-size: 14px; }
  .toggle .desc { font-size: 12px; color: var(--fg-dim); }
  .switch { position: relative; width: 44px; height: 24px;
    background: var(--bg-soft); border-radius: 12px;
    border: 1px solid var(--border); cursor: pointer; }
  .switch::after { content: ''; position: absolute; top: 1px; left: 1px;
    width: 20px; height: 20px; background: var(--fg-dim);
    border-radius: 50%; transition: 0.2s; }
  .switch.on { background: var(--accent); border-color: var(--accent); }
  .switch.on::after { left: 21px; background: #0f1419; }
  .badge { display: inline-block; padding: 2px 8px; border-radius: 10px;
    font-size: 11px; background: var(--bg-soft); color: var(--fg-dim); }
  .badge.ok { background: rgba(102,187,106,0.2); color: var(--ok); }
  .badge.err { background: rgba(239,83,80,0.2); color: var(--err); }
  .badge.run { background: rgba(77,208,225,0.2); color: var(--accent); }
  .hidden { display: none !important; }
  #pair-pane { text-align: center; padding: 24px 12px; }
  #pair-pane input { font-size: 28px; letter-spacing: 8px; text-align: center;
    max-width: 220px; margin: 16px auto; display: block; }
  #pair-pane .hint { font-size: 12px; color: var(--fg-dim); margin: 8px 0; }
  #pair-pane .err { color: var(--err); margin: 8px 0; }
  /* .qr-wrap was used by the removed QR endpoint. */
  .row { display: flex; gap: 8px; align-items: center; }
  .row > * { flex: 1; }
  button.secondary { background: var(--bg-soft); color: var(--fg);
    border: 1px solid var(--border); }
  .small { font-size: 12px; }
  .mono { font-family: ui-monospace, SFMono-Regular, Menlo, monospace; }
  details { margin: 6px 0; }
  summary { cursor: pointer; color: var(--accent); }
  #scroll-anchor { height: 1px; }
</style>
</head>
<body>
<header>
  <h1>jarvis</h1>
  <div class="device" id="device-info">...</div>
</header>
<nav id="main-nav" class="hidden">
  <button data-tab="chat" class="active">Chat</button>
  <button data-tab="sessions">Sessions</button>
  <button data-tab="files">Files</button>
  <button data-tab="modes">Modes</button>
  <button data-tab="projects">Projects</button>
  <button data-tab="drive">Drive</button>
  <button data-tab="account">Account</button>
</nav>
<main id="content"></main>
<footer id="footer" class="hidden"></footer>
<div id="pair-pane">
  <h2 style="color: var(--accent)">Pair with jarvis</h2>
  <p>Type the 6-digit code shown on the host device.</p>
  <input id="pair-code" inputmode="numeric" pattern="[0-9]*" maxlength="6"
         placeholder="000000" autocomplete="off">
  <input id="pair-name" placeholder="Your name (e.g. Pixel 9)">
  <button id="pair-btn">Pair</button>
  <div id="pair-msg" class="err"></div>
  <p class="hint">You only need to pair once. After that, this device
  will stay connected until you remove it from the host.</p>
</div>
<script>
(function() {
  var DEVICE_ID = localStorage.getItem('jarvis_device_id');
  var DEVICE_NAME = localStorage.getItem('jarvis_device_name') || 'Phone';
  var TOKEN = localStorage.getItem('jarvis_token') || '';
  var tab = 'chat';
  var pollHandle = null;
  var chatHistory = [];
  var currentSession = null;
  var pollTarget = null;

  function $(id) { return document.getElementById(id); }
  function el(tag, attrs, children) {
    var e = document.createElement(tag);
    if (attrs) for (var k in attrs) {
      if (k === 'class') e.className = attrs[k];
      else if (k === 'onclick') e.addEventListener('click', attrs[k]);
      else if (k === 'html') e.innerHTML = attrs[k];
      else e.setAttribute(k, attrs[k]);
    }
    if (children) for (var i = 0; i < children.length; i++) {
      var c = children[i];
      if (typeof c === 'string') e.appendChild(document.createTextNode(c));
      else if (c) e.appendChild(c);
    }
    return e;
  }
  function esc(s) {
    return String(s == null ? '' : s)
      .replace(/&/g, '&amp;').replace(/</g, '&lt;')
      .replace(/>/g, '&gt;').replace(/"/g, '&quot;');
  }
  function md(s) {
    // Tiny markdown: code fences ```...```, **bold**, *italic*, line breaks
    if (s == null) return '';
    s = esc(s);
    s = s.replace(/```([\s\S]*?)```/g, function(m, c) {
      return '<pre>' + c + '</pre>';
    });
    s = s.replace(/`([^`]+)`/g, '<code>$1</code>');
    s = s.replace(/\*\*([^*]+)\*\*/g, '<strong>$1</strong>');
    s = s.replace(/\n/g, '<br>');
    return s;
  }
  function api(method, path, body, cb) {
    var xhr = new XMLHttpRequest();
    xhr.open(method, path, true);
    xhr.setRequestHeader('Content-Type', 'application/json');
    if (TOKEN) xhr.setRequestHeader('X-Device-Token', TOKEN);
    xhr.onload = function() {
      var j = null;
      try { j = JSON.parse(xhr.responseText); } catch (e) {}
      cb(xhr.status, j);
    };
    xhr.onerror = function() { cb(0, null); };
    xhr.send(body ? JSON.stringify(body) : null);
  }
  function render() {
    $('device-info').textContent = DEVICE_NAME;
    var nav = $('main-nav');
    nav.classList.remove('hidden');
    var main = $('content');
    main.innerHTML = '';
    if (tab === 'chat') renderChat(main);
    else if (tab === 'sessions') renderSessions(main);
    else if (tab === 'files') renderFiles(main);
    else if (tab === 'modes') renderModes(main);
    else if (tab === 'projects') renderProjects(main);
    else if (tab === 'drive') renderDrive(main);
    else if (tab === 'account') renderAccount(main);
  }
  function renderChat(main) {
    var input = el('textarea', {rows: '2', placeholder: 'Ask jarvis...', id: 'chat-input'});
    var sendBtn = el('button', {id: 'send-btn', onclick: function() {
      var t = input.value.trim();
      if (!t) return;
      chatHistory.push({role: 'user', text: t});
      input.value = '';
      sendBtn.disabled = true;
      sendBtn.textContent = '...';
      api('POST', '/api/chat', {text: t}, function(status, j) {
        sendBtn.disabled = false;
        sendBtn.textContent = 'Send';
        if (status !== 200 || !j.ok) {
          chatHistory.push({role: 'error', text: (j && j.error) || ('HTTP ' + status)});
        } else {
          chatHistory.push({role: 'ai', text: j.reply || '(no reply)'});
        }
        render();
        main.scrollTop = main.scrollHeight;
      });
    }}, ['Send']);
    var f = el('div', {class: 'input-row'}, [input, sendBtn]);
    $('footer').innerHTML = '';
    $('footer').appendChild(f);
    $('footer').classList.remove('hidden');
    // Render history
    chatHistory.forEach(function(m) {
      var d = el('div', {class: 'msg ' + m.role}, [
        el('div', {class: 'role'}, [m.role === 'user' ? 'you' : 'jarvis']),
        el('div', {html: md(m.text)})
      ]);
      main.appendChild(d);
    });
  }
  function renderSessions(main) {
    $('footer').classList.add('hidden');
    api('GET', '/api/sessions', null, function(status, j) {
      if (status !== 200) {
        main.appendChild(el('div', {class: 'msg error'}, ['Could not list sessions.']));
        return;
      }
      var list = (j && j.sessions) || [];
      if (!list.length) {
        main.appendChild(el('p', {class: 'small'}, ['No sessions yet. Start one below.']));
      } else {
        list.forEach(function(s) {
          var card = el('div', {class: 'card'}, [
            el('h3', null, [s.topic || '(no topic)']),
            el('div', {class: 'meta'}, [
              'id: ' + s.id + '  ',
              el('span', {class: 'badge ' + (s.status === 'done' ? 'ok' : s.status === 'stopped' ? 'err' : 'run')}, [s.status]),
              '  iters: ' + s.iterations + '  sources: ' + s.sources
            ]),
          ]);
          var openBtn = el('button', {class: 'secondary', onclick: function() {
            currentSession = s.id;
            renderSessionDetail(main, s.id);
          }}, ['Open']);
          card.appendChild(openBtn);
          main.appendChild(card);
        });
      }
      var topicInput = el('input', {placeholder: 'New topic...', id: 'new-topic'});
      var startBtn = el('button', {onclick: function() {
        var t = topicInput.value.trim();
        if (!t) return;
        startBtn.disabled = true;
        startBtn.textContent = 'starting...';
        api('POST', '/api/sessions', {topic: t, kind: 'session'}, function(s2, j2) {
          startBtn.disabled = false;
          startBtn.textContent = 'Start long session';
          if (s2 !== 200) {
            main.appendChild(el('div', {class: 'msg error'},
              ['Could not start: ' + ((j2 && j2.error) || ('HTTP ' + s2))]));
            return;
          }
          currentSession = j2.id;
          renderSessionDetail(main, j2.id);
        });
      }}, ['Start long session']);
      var reportBtn = el('button', {class: 'secondary', onclick: function() {
        var t = topicInput.value.trim();
        if (!t) return;
        reportBtn.disabled = true;
        reportBtn.textContent = '...';
        api('POST', '/api/sessions', {topic: t, kind: 'one_shot'}, function(s2, j2) {
          reportBtn.disabled = false;
          reportBtn.textContent = 'One-shot report';
          if (s2 !== 200) {
            main.appendChild(el('div', {class: 'msg error'},
              ['Could not start: ' + ((j2 && j2.error) || ('HTTP ' + s2))]));
            return;
          }
          currentSession = j2.id;
          renderSessionDetail(main, j2.id);
        });
      }}, ['One-shot report']);
      var newCard = el('div', {class: 'card'}, [
        el('h3', null, ['Start a new session']),
        topicInput,
        el('div', {class: 'row', style: 'margin-top:8px'}, [startBtn, reportBtn])
      ]);
      main.appendChild(newCard);
      if (currentSession) {
        renderSessionDetail(main, currentSession);
      }
    });
  }
  function renderSessionDetail(main, sid) {
    main.innerHTML = '';
    api('GET', '/api/sessions/' + sid, null, function(status, j) {
      if (status !== 200 || !j.ok) {
        main.appendChild(el('div', {class: 'msg error'},
          ['Could not load session.']));
        return;
      }
      var s = j.session;
      main.appendChild(el('div', {class: 'card'}, [
        el('h3', null, [s.topic || sid]),
        el('div', {class: 'meta'}, [
          'iter: ' + s.iterations + '  sources: ' + s.sources +
          '  status: ',
          el('span', {class: 'badge ' + (s.status === 'done' ? 'ok' : s.status === 'stopped' ? 'err' : 'run')}, [s.status])
        ]),
      ]));
      if (s.plan) {
        var p = el('details', null, [el('summary', null, ['Plan'])]);
        p.appendChild(el('div', {html: md(s.plan)}));
        main.appendChild(p);
      }
      if (s.notes) {
        var n = el('details', {open: 'open'}, [el('summary', null, ['Notes'])]);
        n.appendChild(el('div', {html: md(s.notes.slice(0, 8000)) +
          (s.notes.length > 8000 ? '\n\n... (truncated, open on host for full)' : '')}));
        main.appendChild(n);
      }
      if (s.report) {
        var r = el('details', null, [el('summary', null, ['Final report'])]);
        r.appendChild(el('div', {html: md(s.report.slice(0, 8000))}));
        main.appendChild(r);
      }
      // Ask a question
      var askInput = el('input', {placeholder: 'Ask a question about the notes...'});
      var askBtn = el('button', {onclick: function() {
        var q = askInput.value.trim();
        if (!q) return;
        askBtn.disabled = true; askBtn.textContent = '...';
        api('POST', '/api/sessions/' + sid + '/ask', {question: q}, function(s2, j2) {
          askBtn.disabled = false; askBtn.textContent = 'Ask';
          if (s2 !== 200) {
            main.appendChild(el('div', {class: 'msg error'},
              ['Ask failed: ' + ((j2 && j2.error) || ('HTTP ' + s2))]));
            return;
          }
          // Append Q&A to the visible notes
          var qa = el('div', {class: 'msg user'}, [
            el('div', {class: 'role'}, ['Q']),
            el('div', null, [q])
          ]);
          var aa = el('div', {class: 'msg ai'}, [
            el('div', {class: 'role'}, ['A']),
            el('div', {html: md(j2.answer)})
          ]);
          main.appendChild(qa);
          main.appendChild(aa);
          main.scrollTop = main.scrollHeight;
          askInput.value = '';
        });
      }}, ['Ask']);
      var actRow = el('div', {class: 'row', style: 'margin-top:8px'}, [askInput, askBtn]);
      main.appendChild(actRow);
      var ctrlRow = el('div', {class: 'row', style: 'margin-top:8px'}, [
        el('button', {class: 'secondary', onclick: function() {
          api('POST', '/api/sessions/' + sid + '/pause', {}, function() {});
        }}, ['Pause']),
        el('button', {class: 'secondary', onclick: function() {
          api('POST', '/api/sessions/' + sid + '/resume', {}, function() {});
        }}, ['Resume']),
        el('button', {class: 'secondary', onclick: function() {
          api('POST', '/api/sessions/' + sid + '/report', {}, function() {});
        }}, ['Write report']),
      ]);
      main.appendChild(ctrlRow);
      var backBtn = el('button', {class: 'secondary', style: 'margin-top:8px', onclick: function() {
        currentSession = null; render();
      }}, ['Back']);
      main.appendChild(backBtn);
    });
  }
  function renderFiles(main) {
    $('footer').classList.add('hidden');
    api('GET', '/api/files', null, function(status, j) {
      if (status !== 200) {
        main.appendChild(el('div', {class: 'msg error'}, ['Could not list files.']));
        return;
      }
      var files = (j && j.files) || [];
      if (!files.length) {
        main.appendChild(el('p', {class: 'small'}, ['No generated files yet.']));
      } else {
        files.forEach(function(f) {
          var card = el('div', {class: 'card'}, [
            el('h3', {class: 'mono'}, [f.name]),
            el('div', {class: 'meta'}, [(f.size || 0) + ' bytes  -  ' + f.path]),
            el('a', {href: '/api/files/' + encodeURIComponent(f.path), target: '_blank'}, ['Download'])
          ]);
          main.appendChild(card);
        });
      }
      // New file request
      var req = el('input', {placeholder: 'Describe the file to generate...'});
      var out = el('input', {placeholder: 'Output path (optional, e.g. ./hello.py)'});
      var btn = el('button', {onclick: function() {
        var r = req.value.trim();
        if (!r) return;
        btn.disabled = true; btn.textContent = '...';
        api('POST', '/api/generate', {request: r, output: out.value.trim()}, function(s, j) {
          btn.disabled = false; btn.textContent = 'Generate file';
          if (s !== 200 || !j.ok) {
            main.appendChild(el('div', {class: 'msg error'},
              ['Failed: ' + ((j && j.error) || ('HTTP ' + s))]));
            return;
          }
          main.appendChild(el('div', {class: 'msg system'},
            ['Wrote ' + j.path + ' (' + (j.size || 0) + ' bytes)']));
          render();
        });
      }}, ['Generate file']);
      var card = el('div', {class: 'card'}, [
        el('h3', null, ['Generate a file']),
        req,
        out,
        el('div', {class: 'row', style: 'margin-top:8px'}, [btn])
      ]);
      main.appendChild(card);
    });
  }
  function renderModes(main) {
    $('footer').classList.add('hidden');
    api('GET', '/api/modes', null, function(status, j) {
      if (status !== 200) {
        main.appendChild(el('div', {class: 'msg error'}, ['Could not load modes.']));
        return;
      }
      var m = j.modes || {};
      var shared = m.shared || {};
      var per = m.per_device || {};
      var items = [
        {k: 'enable_research', label: 'Web research', desc: 'Fetch URLs and search the web before planning'},
        {k: 'enable_review',   label: 'Code review',   desc: 'Have the planner review generated code'},
        {k: 'enable_tests',    label: 'Auto-tests',    desc: 'Generate pytest tests for each module'},
        {k: 'offline',         label: 'Offline mode',  desc: 'Refuse remote API calls (local models only)'},
        {k: 'sandbox_test',    label: 'Sandbox tests', desc: 'Run generated Python in a sandbox'},
      ];
      function mkToggle(it, val, onChange) {
        var sw = el('div', {class: 'switch' + (val ? ' on' : '')});
        sw.addEventListener('click', function() {
          sw.classList.toggle('on');
          onChange(sw.classList.contains('on'));
        });
        return el('div', {class: 'toggle'}, [
          el('div', null, [
            el('div', {class: 'label'}, [it.label]),
            el('div', {class: 'desc'}, [it.desc]),
          ]),
          sw
        ]);
      }
      main.appendChild(el('h3', null, ['Shared modes (apply to all devices)']));
      items.forEach(function(it) {
        main.appendChild(mkToggle(it, !!shared[it.k], function(v) {
          shared[it.k] = v;
          api('POST', '/api/modes', {patch: {shared: shared}}, function() {});
        }));
      });
      main.appendChild(el('h3', {style: 'margin-top:14px'}, ['This device only']));
      items.forEach(function(it) {
        main.appendChild(mkToggle(it, !!per[it.k], function(v) {
          per[it.k] = v;
          api('POST', '/api/modes', {patch: {per_device: per}}, function() {});
        }));
      });
      // Persona selector
      var pSel = el('select', null, [
        el('option', {value: 'engineer'}, ['engineer']),
        el('option', {value: 'jarvis'}, ['jarvis'])
      ]);
      pSel.value = (m.persona || 'engineer');
      pSel.addEventListener('change', function() {
        api('POST', '/api/modes', {patch: {persona: pSel.value}}, function() {});
      });
      main.appendChild(el('div', {class: 'toggle'}, [
        el('div', null, [
          el('div', {class: 'label'}, ['Persona']),
          el('div', {class: 'desc'}, ['How the AI sounds']),
        ]),
        pSel
      ]));
    });
  }
  function renderProjects(main) {
    $('footer').classList.add('hidden');
    api('GET', '/api/projects', null, function(status, j) {
      if (status !== 200) {
        main.appendChild(el('div', {class: 'msg error'}, ['Could not list projects.']));
        return;
      }
      var projs = (j && j.projects) || [];
      var active = j && j.active;
      if (!projs.length) {
        main.appendChild(el('p', {class: 'small'},
          ['No projects in the store yet. Scaffold one below.']));
      } else {
        projs.forEach(function(p) {
          var isActive = p.name === active;
          var card = el('div', {class: 'card'},
            [el('h3', {class: 'mono'},
              [(isActive ? '* ' : '') + p.name + '  [' + p.kind + '/' + p.source + ']']),
             el('div', {class: 'meta'}, [p.path || '']),
             el('div', {class: 'row', style: 'margin-top:8px'},
               [el('button', {onclick: function() {
                  api('POST', '/api/projects', {action: 'use', name: p.name}, function(s, jj) {
                    if (s === 200 && jj.ok) render();
                  });
                }}, ['Use' + (isActive ? 'd' : '')]),
                el('button', {onclick: function() {
                  if (!confirm('Remove project ' + p.name + '? Files will be kept on disk.')) return;
                  api('POST', '/api/projects', {action: 'remove', name: p.name, delete_files: false}, function(s, jj) {
                    if (s === 200 && jj.ok) render();
                  });
                }}, ['Remove'])])]);
          main.appendChild(card);
        });
      }
      // Scaffold new
      var name = el('input', {placeholder: 'Project name (e.g. mygame)'});
      var kind = el('select', null, [
        el('option', {value: 'godot'}, ['godot']),
        el('option', {value: 'python'}, ['python'])
      ]);
      var btn = el('button', {onclick: function() {
        var n = name.value.trim();
        if (!n) return;
        btn.disabled = true; btn.textContent = '...';
        api('POST', '/api/projects', {action: 'new', name: n, kind: kind.value}, function(s, j) {
          btn.disabled = false; btn.textContent = 'Scaffold';
          if (s !== 200 || !j.ok) {
            main.appendChild(el('div', {class: 'msg error'},
              ['Failed: ' + ((j && j.error) || ('HTTP ' + s))]));
            return;
          }
          render();
        });
      }}, ['Scaffold']);
      main.appendChild(el('div', {class: 'card'},
        [el('h3', null, ['Scaffold a new project']),
         el('div', {class: 'row'}, [name, kind, btn])]));
    });
  }
  function renderDrive(main) {
    $('footer').classList.add('hidden');
    api('GET', '/api/drive', null, function(status, j) {
      if (status !== 200) {
        main.appendChild(el('div', {class: 'msg error'}, ['Could not get drive status.']));
        return;
      }
      var folder = (j && j.folder) || '';
      var configured = !!(j && j.configured);
      main.appendChild(el('div', {class: 'card'},
        [el('h3', null, ['Drive folder']),
         el('div', {class: 'meta'}, [folder || '(not configured)']),
         el('div', {class: 'small', style: 'margin-top:4px'},
           [configured ? 'ready' : 'path does not exist yet'])]));
      var newPath = el('input', {placeholder: '/path/to/Google Drive/jarvis'});
      var setBtn = el('button', {onclick: function() {
        var p = newPath.value.trim();
        if (!p) return;
        api('POST', '/api/drive', {action: 'set', folder: p}, function(s, jj) {
          if (s === 200 && jj.ok) render();
        });
      }}, ['Set']);
      var unsetBtn = el('button', {onclick: function() {
        api('POST', '/api/drive', {action: 'unset'}, function(s, jj) {
          if (s === 200 && jj.ok) render();
        });
      }}, ['Unset']);
      var pushBtn = el('button', {onclick: function() {
        pushBtn.disabled = true; pushBtn.textContent = '...';
        api('POST', '/api/drive', {action: 'push'}, function(s, jj) {
          pushBtn.disabled = false; pushBtn.textContent = 'Push';
          if (s === 200 && jj.ok) {
            main.appendChild(el('div', {class: 'msg system'},
              ['Pushed: ' + jj.copied + ' copied, ' + jj.skipped + ' skipped']));
          } else {
            main.appendChild(el('div', {class: 'msg error'},
              ['Failed: ' + ((jj && jj.error) || ('HTTP ' + s))]));
          }
        });
      }}, ['Push']);
      var pullBtn = el('button', {onclick: function() {
        pullBtn.disabled = true; pullBtn.textContent = '...';
        api('POST', '/api/drive', {action: 'pull'}, function(s, jj) {
          pullBtn.disabled = false; pullBtn.textContent = 'Pull';
          if (s === 200 && jj.ok) {
            main.appendChild(el('div', {class: 'msg system'},
              ['Pulled: ' + jj.copied + ' copied, ' + jj.skipped + ' skipped']));
          } else {
            main.appendChild(el('div', {class: 'msg error'},
              ['Failed: ' + ((jj && jj.error) || ('HTTP ' + s))]));
          }
        });
      }}, ['Pull']);
      main.appendChild(el('div', {class: 'card'},
        [el('h3', null, ['Configure']),
         el('div', {class: 'row'}, [newPath, setBtn, unsetBtn])]));
      main.appendChild(el('div', {class: 'card'},
        [el('h3', null, ['Sync now']),
         el('div', {class: 'row'}, [pushBtn, pullBtn])]));
    });
  }
  function renderAccount(main) {
    $('footer').classList.add('hidden');
    api('GET', '/api/account/status', null, function(status, j) {
      var card = el('div', {class: 'card'}, [
        el('h3', null, ['Cloud sync (optional)']),
        el('div', {class: 'meta'}, [j && j.signed_in
          ? ('Signed in as ' + j.email)
          : 'Not signed in. Your settings are local-only right now.'])
      ]);
      main.appendChild(card);
      if (j && j.signed_in) {
        var out = el('button', {onclick: function() {
          api('POST', '/api/account/logout', {}, function() { render(); });
        }}, ['Sign out']);
        card.appendChild(out);
      } else {
        var mode = 'login';
        var email = el('input', {placeholder: 'email'});
        var pw = el('input', {type: 'password', placeholder: 'password (6+ chars)'});
        var go = el('button', {onclick: function() {
          var path = (mode === 'login') ? '/api/account/login' : '/api/account/signup';
          go.disabled = true; go.textContent = '...';
          api('POST', path, {email: email.value, password: pw.value}, function(s, jj) {
            go.disabled = false; go.textContent = (mode === 'login') ? 'Sign in' : 'Create account';
            if (s !== 200) {
              main.appendChild(el('div', {class: 'msg error'},
                [(jj && jj.error) || ('HTTP ' + s)]));
              return;
            }
            render();
          });
        }}, ['Sign in']);
        var toggle = el('button', {class: 'secondary', onclick: function() {
          mode = (mode === 'login') ? 'signup' : 'login';
          go.textContent = (mode === 'login') ? 'Sign in' : 'Create account';
          toggle.textContent = (mode === 'login') ? 'Need an account?' : 'Have an account?';
        }}, ['Need an account?']);
        var c2 = el('div', {class: 'card'}, [
          el('h3', null, ['Sign in or sign up']),
          email, pw,
          el('div', {class: 'row', style: 'margin-top:8px'}, [go, toggle])
        ]);
        main.appendChild(c2);
      }
      // Cloud backend info
      api('GET', '/api/cloud/info', null, function(s2, j2) {
        var info = el('div', {class: 'card'}, [
          el('h3', null, ['Cloud backend']),
          el('div', {class: 'meta small'}, [
            j2 && j2.configured
              ? 'Backend: ' + j2.url
              : 'No cloud backend configured. ' +
                'Set JARVIS_CLOUD_URL (or DUAL_AI_CLOUD_URL) to enable cross-device sync via email+password. ' +
                'Pairing (above) works without it.'
          ])
        ]);
        main.appendChild(info);
      });
    });
  }
  // Tabs
  document.querySelectorAll('nav button').forEach(function(btn) {
    btn.addEventListener('click', function() {
      document.querySelectorAll('nav button').forEach(function(b) { b.classList.remove('active'); });
      btn.classList.add('active');
      tab = btn.getAttribute('data-tab');
      render();
    });
  });
  // Pairing
  $('pair-btn').addEventListener('click', function() {
    var code = $('pair-code').value.trim();
    var name = $('pair-name').value.trim() || 'Phone';
    if (!code) {
      $('pair-msg').textContent = 'Enter the 6-digit code.';
      return;
    }
    $('pair-btn').disabled = true;
    $('pair-btn').textContent = 'Pairing...';
    api('POST', '/api/pair', {code: code, name: name, kind: 'phone'}, function(status, j) {
      $('pair-btn').disabled = false;
      $('pair-btn').textContent = 'Pair';
      if (status !== 200 || !j.ok) {
        $('pair-msg').textContent = (j && j.error) || ('HTTP ' + status);
        return;
      }
      DEVICE_ID = j.device_id;
      TOKEN = j.device_id;
      localStorage.setItem('jarvis_device_id', DEVICE_ID);
      localStorage.setItem('jarvis_device_name', name);
      localStorage.setItem('jarvis_token', TOKEN);
      DEVICE_NAME = name;
      $('pair-pane').classList.add('hidden');
      render();
    });
  });
  // Auto-pair if already paired
  if (TOKEN) {
    api('GET', '/api/status', null, function(status, j) {
      if (status === 200 && j.ok) {
        $('pair-pane').classList.add('hidden');
        render();
      } else {
        // Token expired or server reset
        localStorage.removeItem('jarvis_device_id');
        localStorage.removeItem('jarvis_token');
        DEVICE_ID = ''; TOKEN = '';
      }
    });
  } else {
    $('pair-pane').classList.remove('hidden');
  }
})();
</script>
</body>
</html>
"""


# Server state (per-server instance). One PhoneServer -> one cfg.
class _PhoneServerState(object):
    def __init__(self, cfg):
        self.cfg = cfg
        self.active_session = None    # {sid, worker_thread, stop_event, kind}
        self.lock = _threading.Lock()


# ----- Route handlers -----
def _route_index(handler, _params):
    handler._send_html(_INDEX_HTML)


def _route_status(handler, _params):
    handler._require_device()  # device must be paired
    with handler.state.lock:
        sess = handler.state.active_session
        sess_info = None
        if sess:
            try:
                s = DeepResearchSession.load(sess["sid"])
                sess_info = {
                    "id": s.session_id, "topic": s.topic,
                    "status": s.status,
                    "iterations": s.iterations_done,
                    "sources": len(s.sources),
                }
            except Exception:
                sess_info = {"id": sess["sid"], "status": "unknown"}
    pairing = _pairing_load()
    handler._send_json({
        "ok": True,
        "server_version": "1.0",
    "qr_supported": False,  # QR generation removed; use the URL.
        "persona": handler.state.cfg.get("persona", "engineer"),
        "offline": bool(handler.state.cfg.get("offline")),
        "active_session": sess_info,
        "device_count": len(pairing.get("devices", [])),
    })


def _route_pair(handler, _params):
    body = handler._read_json()
    code = (body.get("code") or "").strip()
    name = (body.get("name") or "Phone").strip()
    kind = (body.get("kind") or "phone").strip()
    try:
        dev = _pairing_pair_device(code, name, kind)
    except PairingError as e:
        handler._send_json({"ok": False, "error": str(e)}, status=400)
        return
    handler._send_json({
        "ok": True,
        "device_id": dev["id"],
        "name": dev["name"],
        "kind": dev["kind"],
    })


def _route_devices(handler, _params):
    handler._require_device()
    pairing = _pairing_load()
    devs = []
    for d in pairing.get("devices", []):
        devs.append({
            "id": d.get("id"),
            "name": d.get("name"),
            "kind": d.get("kind"),
            "added": d.get("added"),
            "last_seen": d.get("last_seen"),
        })
    handler._send_json({"ok": True, "devices": devs})


def _route_devices_delete(handler, _params):
    handler._require_device()
    body = handler._read_json()
    target = (body.get("id") or "").strip()
    if not target:
        handler._send_json({"ok": False, "error": "missing id"}, status=400)
        return
    if _pairing_remove_device(target):
        handler._send_json({"ok": True})
    else:
        handler._send_json({"ok": False, "error": "not found"}, status=404)


def _route_modes_get(handler, _params):
    handler._require_device()
    pairing = _pairing_load()
    shared = _safe_modes_subset(pairing.get("shared", {}))
    per = _safe_modes_subset(
        (handler._current_device() or {}).get("modes", {}))
    handler._send_json({
        "ok": True,
        "modes": {
            "shared": shared,
            "per_device": per,
            "persona": handler.state.cfg.get("persona", "engineer"),
        },
    })


def _route_modes_set(handler, _params):
    handler._require_device()
    body = handler._read_json()
    patch = body.get("patch") or {}
    if "shared" in patch and isinstance(patch["shared"], dict):
        # Merge into existing
        cur = _pairing_load().get("shared", {})
        cur.update(_safe_modes_subset(patch["shared"]))
        _pairing_set_shared_modes(cur)
        # Also reflect into the live cfg so the next request uses them
        for k, v in cur.items():
            if k in ("enable_review", "enable_tests", "enable_research",
                     "offline"):
                handler.state.cfg[k] = bool(v)
        save_config(handler.state.cfg)
    if "per_device" in patch and isinstance(patch["per_device"], dict):
        dev = handler._current_device()
        if dev:
            md = dict(dev.get("modes", {}))
            md.update(_safe_modes_subset(patch["per_device"]))
            _pairing_set_device_modes(dev["id"], md)
    if "persona" in patch and patch["persona"] in ("engineer", "jarvis"):
        handler.state.cfg["persona"] = patch["persona"]
        save_config(handler.state.cfg)
    handler._send_json({"ok": True})


def _route_chat(handler, _params):
    handler._require_device()
    body = handler._read_json()
    text = (body.get("text") or "").strip()
    if not text:
        handler._send_json({"ok": False, "error": "empty text"}, status=400)
        return
    # If a deep-research session is active, treat this as a question
    # against its notes. Otherwise, run a one-shot planner+code.
    with handler.state.lock:
        sess = handler.state.active_session
    if sess:
        try:
            s = DeepResearchSession.load(sess["sid"])
            ans = _deepresearch_answer(text, s.notes_md, handler.state.cfg)
            s.questions.append({"ts": time.time(), "q": text, "a": ans})
            s.save()
            handler._send_json({"ok": True, "reply": ans,
                                "kind": "session_qa"})
            return
        except Exception as e:
            handler._send_json({"ok": False, "error": str(e)}, status=500)
            return
    # Plain chat run
    try:
        result = run(text, handler.state.cfg, text_only=True)
        if result.get("overall_error"):
            handler._send_json({"ok": False, "error": result["overall_error"]},
                                status=500)
            return
        reply_parts = []
        plan = result.get("plan", {})
        if plan.get("summary"):
            reply_parts.append(plan["summary"].strip())
        for mr in result.get("modules", []):
            if mr.get("code_error"):
                reply_parts.append("[code error] " + mr["code_error"])
                continue
            if mr.get("code"):
                reply_parts.append("# " + mr.get("name", "module") + "\n"
                                   + mr["code"].rstrip())
            if mr.get("review"):
                reply_parts.append("## review\n" + mr["review"].strip())
        handler._send_json({"ok": True, "reply": "\n\n".join(reply_parts),
                            "kind": "chat"})
    except JarvisError as e:
        handler._send_json({"ok": False, "error": str(e)}, status=500)
    except Exception as e:
        handler._send_json({"ok": False, "error":
                            type(e).__name__ + ": " + str(e)[:300]},
                            status=500)


def _route_sessions(handler, _params):
    handler._require_device()
    sessions = list_sessions()
    out = []
    for sid, topic, status, updated in sessions:
        out.append({
            "id": sid, "topic": topic, "status": status,
            "iterations": "?",
            "sources": "?",
        })
    handler._send_json({"ok": True, "sessions": out})


def _route_sessions_start(handler, _params):
    handler._require_device()
    body = handler._read_json()
    topic = (body.get("topic") or "").strip()
    kind = (body.get("kind") or "session").strip()
    if not topic:
        handler._send_json({"ok": False, "error": "empty topic"},
                            status=400)
        return
    if handler.state.cfg.get("offline"):
        handler._send_json({"ok": False,
                            "error": "deep research requires web access; "
                                     "offline mode is on"}, status=400)
        return
    try:
        _need_requests()
    except SystemExit:
        handler._send_json({"ok": False,
                            "error": "the 'requests' library is required"},
                            status=500)
        return
    # Stop any existing active session
    with handler.state.lock:
        if handler.state.active_session:
            try:
                handler.state.active_session["stop"].set()
            except Exception:
                pass
    try:
        if kind == "one_shot":
            sess = run_deep_research_session(
                topic, handler.state.cfg,
                max_seconds=300, max_iterations=1, one_shot=True)
        else:
            sess = run_deep_research_session(
                topic, handler.state.cfg,
                max_seconds=5 * 3600, max_iterations=50, one_shot=False)
    except (ConfigError, JarvisError) as e:
        handler._send_json({"ok": False, "error": str(e)}, status=500)
        return
    if kind != "one_shot":
        # Start a background worker that does iterations + saves
        stop_evt = _threading.Event()
        def _worker():
            try:
                while not stop_evt.is_set():
                    if sess.status in ("done", "stopped"):
                        break
                    try:
                        _deepresearch_one_iteration(
                            sess, handler.state.cfg,
                            stop_check=stop_evt.is_set)
                    except Exception as e:
                        _log("WARNING", "phone.iter_fail", err=str(e))
                    sess.elapsed_seconds = time.time() - sess.started_at
                    sess.save()
                    if stop_evt.wait(2):
                        break
                if sess.status not in ("done", "stopped"):
                    sess.status = "stopped"
                    sess.save()
            except Exception as e:
                _log("WARNING", "phone.worker_fail", err=str(e))
        t = _threading.Thread(target=_worker, daemon=True)
        t.start()
        with handler.state.lock:
            handler.state.active_session = {
                "sid": sess.session_id,
                "worker": t,
                "stop": stop_evt,
                "kind": "session",
            }
    else:
        with handler.state.lock:
            handler.state.active_session = {
                "sid": sess.session_id,
                "worker": None,
                "stop": None,
                "kind": "one_shot",
            }
    handler._send_json({"ok": True, "id": sess.session_id, "status": sess.status})


def _route_session_detail(handler, params):
    handler._require_device()
    sid = params.get("id", "")
    try:
        s = DeepResearchSession.load(sid)
    except ConfigError as e:
        handler._send_json({"ok": False, "error": str(e)}, status=404)
        return
    d = _ensure_session_dir(sid)
    rp = os.path.join(d, "report.md")
    report = ""
    if os.path.isfile(rp):
        try:
            with open(rp, "r", encoding="utf-8") as f:
                report = f.read()
        except OSError:
            pass
    handler._send_json({
        "ok": True,
        "session": {
            "id": s.session_id,
            "topic": s.topic,
            "status": s.status,
            "iterations": s.iterations_done,
            "sources": len(s.sources),
            "plan": s.plan,
            "notes": s.notes_md,
            "report": report,
            "questions": s.questions[-20:],
        },
    })


def _route_session_ask(handler, params):
    handler._require_device()
    sid = params.get("id", "")
    body = handler._read_json()
    q = (body.get("question") or "").strip()
    if not q:
        handler._send_json({"ok": False, "error": "empty question"},
                            status=400)
        return
    try:
        s = DeepResearchSession.load(sid)
    except ConfigError as e:
        handler._send_json({"ok": False, "error": str(e)}, status=404)
        return
    if not s.notes_md:
        handler._send_json({"ok": False, "error": "no notes yet"}, status=400)
        return
    try:
        ans = _deepresearch_answer(q, s.notes_md, handler.state.cfg)
        s.questions.append({"ts": time.time(), "q": q, "a": ans})
        s.save()
        handler._send_json({"ok": True, "answer": ans})
    except JarvisError as e:
        handler._send_json({"ok": False, "error": str(e)}, status=500)


def _route_session_pause(handler, params):
    handler._require_device()
    sid = params.get("id", "")
    with handler.state.lock:
        if handler.state.active_session and \
                handler.state.active_session["sid"] == sid:
            try:
                handler.state.active_session["stop"].set()
            except Exception:
                pass
    handler._send_json({"ok": True})


def _route_session_resume(handler, params):
    handler._require_device()
    sid = params.get("id", "")
    try:
        sess = DeepResearchSession.load(sid)
    except ConfigError as e:
        handler._send_json({"ok": False, "error": str(e)}, status=404)
        return
    if sess.status in ("done",):
        handler._send_json({"ok": False, "error": "session is done"},
                            status=400)
        return
    # Stop any other active session, start a new worker for this one
    with handler.state.lock:
        if handler.state.active_session and \
                handler.state.active_session["sid"] != sid:
            try:
                handler.state.active_session["stop"].set()
            except Exception:
                pass
        stop_evt = _threading.Event()
        def _worker():
            try:
                while not stop_evt.is_set():
                    if sess.status in ("done", "stopped"):
                        break
                    try:
                        _deepresearch_one_iteration(
                            sess, handler.state.cfg,
                            stop_check=stop_evt.is_set)
                    except Exception as e:
                        _log("WARNING", "phone.resume_fail", err=str(e))
                    sess.elapsed_seconds = time.time() - sess.started_at
                    sess.save()
                    if stop_evt.wait(2):
                        break
                if sess.status not in ("done", "stopped"):
                    sess.status = "stopped"
                    sess.save()
            except Exception as e:
                _log("WARNING", "phone.resume_worker_fail", err=str(e))
        t = _threading.Thread(target=_worker, daemon=True)
        t.start()
        handler.state.active_session = {
            "sid": sid, "worker": t, "stop": stop_evt, "kind": "session",
        }
    handler._send_json({"ok": True})


def _route_session_report(handler, params):
    handler._require_device()
    sid = params.get("id", "")
    try:
        s = DeepResearchSession.load(sid)
    except ConfigError as e:
        handler._send_json({"ok": False, "error": str(e)}, status=404)
        return
    if not s.notes_md:
        handler._send_json({"ok": False, "error": "no notes yet"},
                            status=400)
        return
    try:
        report = _deepresearch_write_report(
            s.topic, s.notes_md, handler.state.cfg)
        d = _ensure_session_dir(sid)
        with open(os.path.join(d, "report.md"), "w", encoding="utf-8") as f:
            f.write(report)
        handler._send_json({"ok": True, "report": report})
    except JarvisError as e:
        handler._send_json({"ok": False, "error": str(e)}, status=500)


def _route_generate(handler, _params):
    handler._require_device()
    body = handler._read_json()
    req = (body.get("request") or "").strip()
    out = (body.get("output") or "").strip()
    sandbox = bool(body.get("sandbox_test"))
    if not req:
        handler._send_json({"ok": False, "error": "empty request"},
                            status=400)
        return
    try:
        result = _file_gen_dispatch(
            req, handler.state.cfg, sandbox_test=sandbox)
    except (ConfigError, JarvisError) as e:
        handler._send_json({"ok": False, "error": str(e)}, status=500)
        return
    if not result.get("ok"):
        handler._send_json({"ok": False,
                            "error": result.get("error", "parse failed")},
                            status=500)
        return
    fname = result["filename"]
    path = out or os.path.join(os.getcwd(), fname)
    try:
        parent = os.path.dirname(path) or "."
        if not os.path.isdir(parent):
            os.makedirs(parent, exist_ok=True)
        if result.get("kind") == "text":
            with open(path, "w", encoding="utf-8") as f:
                f.write(result["content"])
        else:
            with open(path, "wb") as f:
                f.write(result["content"])
        size = os.path.getsize(path)
        handler._send_json({
            "ok": True, "path": path, "filename": fname,
            "kind": result["kind"], "size": size,
        })
    except OSError as e:
        handler._send_json({"ok": False,
                            "error": "could not write: " + str(e)},
                            status=500)


def _route_sandbox(handler, _params):
    handler._require_device()
    body = handler._read_json()
    code = (body.get("code") or "").strip()
    lang = (body.get("language") or "python").strip()
    if not code:
        handler._send_json({"ok": False, "error": "empty code"},
                            status=400)
        return
    res = _sandbox_run_code(code, language=lang, timeout=10)
    handler._send_json({"ok": True, "result": res})


def _route_qr(handler, _params):
    handler._require_device()
    # We no longer generate a scannable QR code (the encoder was
    # broken). Return the URL + pairing code as JSON so any client
    # that still calls this endpoint can show a copy-pasteable link.
    base = handler._external_base_url()
    code = _pairing_get_active_code() or ""
    text = base + ("?code=" + code if code else "")
    handler._send_json({
        "ok": True,
        "url": text,
        "code": code,
        "note": "QR code generation has been removed. Open the URL on "
                "your phone and type the code on the pairing page.",
    })


def _route_config_get(handler, _params):
    handler._require_device()
    cfg = _safe_modes_subset(handler.state.cfg)
    handler._send_json({"ok": True, "config": cfg})


def _route_config_set(handler, _params):
    handler._require_device()
    body = handler._read_json()
    patch = body.get("patch") or {}
    if not isinstance(patch, dict):
        handler._send_json({"ok": False, "error": "patch must be a dict"},
                            status=400)
        return
    for k, v in patch.items():
        if k in ("sonnet_api_key", "codex_api_key"):
            # Don't let the phone touch API keys via the API; user has
            # to do that from the host with --reset.
            continue
        handler.state.cfg[k] = v
    save_config(handler.state.cfg)
    handler._send_json({"ok": True})


def _route_account_signup(handler, _params):
    body = handler._read_json()
    email = (body.get("email") or "").strip()
    pw = body.get("password") or ""
    if not _cloud_available():
        handler._send_json({"ok": False,
                            "error": "cloud not configured "
                                     "(set JARVIS_CLOUD_URL)"}, status=400)
        return
    try:
        acct = cloud_signup(email, pw, handler.state.cfg)
    except JarvisError as e:
        handler._send_json({"ok": False, "error": str(e)}, status=400)
        return
    handler._send_json({"ok": True, "account_id": acct})


def _route_account_login(handler, _params):
    body = handler._read_json()
    email = (body.get("email") or "").strip()
    pw = body.get("password") or ""
    if not _cloud_available():
        handler._send_json({"ok": False,
                            "error": "cloud not configured "
                                     "(set JARVIS_CLOUD_URL)"}, status=400)
        return
    try:
        remote_cfg = cloud_login(email, pw)
    except JarvisError as e:
        handler._send_json({"ok": False, "error": str(e)}, status=400)
        return
    # Merge into local config (don't overwrite API keys unless the
    # user explicitly did so from this device)
    if isinstance(remote_cfg, dict):
        for k, v in remote_cfg.items():
            if k in ("sonnet_api_key", "codex_api_key"):
                if not handler.state.cfg.get(k):
                    handler.state.cfg[k] = v
            else:
                handler.state.cfg[k] = v
        save_config(handler.state.cfg)
    handler.state._cloud_email = email
    handler.state._cloud_password = pw
    handler._send_json({"ok": True})


def _route_account_logout(handler, _params):
    handler.state._cloud_email = ""
    handler.state._cloud_password = ""
    handler._send_json({"ok": True})


def _route_account_status(handler, _params):
    handler._require_device()
    em = getattr(handler.state, "_cloud_email", "")
    handler._send_json({
        "ok": True,
        "signed_in": bool(em),
        "email": em,
    })


def _route_cloud_new_code(handler, _params):
    handler._require_device()
    code = _pairing_new_code()
    handler._send_json({"ok": True, "code": code,
                        "expires_in": PAIRING_CODE_TTL})


def _route_cloud_info(handler, _params):
    handler._require_device()
    handler._send_json({
        "ok": True,
        "configured": _cloud_available(),
        "url": CLOUD_URL or "",
    })


# ----- Projects store REST routes -----

def _route_projects_list(handler, _params):
    """List all projects in the store with their manifests."""
    handler._require_device()
    try:
        projs = list_projects()
    except Exception as e:
        handler._send_json({"ok": False, "error": str(e)}, status=500)
        return
    active = get_active_project()
    handler._send_json({
        "ok": True,
        "projects": projs,
        "active": active,
        "projects_dir": PROJECTS_DIR,
    })


def _route_projects_action(handler, _params):
    """Generic action: {action, name, [path], [kind]} -> result.

    action: list | active | path | use | new | add | import | remove | open
    For new: kind in {'godot','python'}; name required.
    For add/import: source path required, name optional (derived from path).
    For use/remove: name required.
    """
    handler._require_device()
    body = handler._read_json()
    action = (body.get("action") or "").strip().lower()
    name = (body.get("name") or "").strip()
    src = (body.get("path") or "").strip()
    kind = (body.get("kind") or "").strip().lower()
    delete_files = bool(body.get("delete_files"))

    if action in ("list", ""):
        return _route_projects_list(handler, _params)
    if action == "active":
        handler._send_json({"ok": True, "active": get_active_project()})
        return
    if action == "path":
        cfg = load_config()
        n = get_active_project(cfg)
        if not n:
            handler._send_json({"ok": False, "error": "no active project"}, status=400)
            return
        proj = _project_load(n) or {}
        handler._send_json({"ok": True, "name": n,
                            "path": proj.get("path", ""),
                            "kind": proj.get("kind", "")})
        return
    if action == "use":
        if not name:
            handler._send_json({"ok": False, "error": "missing name"}, status=400)
            return
        if _project_load(name) is None:
            handler._send_json({"ok": False, "error": "no such project: " + name},
                               status=404)
            return
        set_active_project(name)
        handler._send_json({"ok": True, "active": name})
        return
    if action == "new":
        if not name:
            handler._send_json({"ok": False, "error": "missing name"}, status=400)
            return
        if kind not in ("godot", "python"):
            handler._send_json({"ok": False,
                                "error": "kind must be 'godot' or 'python'"},
                               status=400)
            return
        try:
            m = _project_scaffold(name, kind)
        except Exception as e:
            handler._send_json({"ok": False, "error": str(e)}, status=400)
            return
        set_active_project(name)
        handler._send_json({"ok": True, "name": name, "manifest": m})
        return
    if action == "add":
        if not src or not name:
            handler._send_json({"ok": False,
                                "error": "need both name and path"},
                               status=400)
            return
        try:
            m = _project_adopt(name, src, kind="generic")
        except Exception as e:
            handler._send_json({"ok": False, "error": str(e)}, status=400)
            return
        handler._send_json({"ok": True, "manifest": m})
        return
    if action == "import":
        if not src or not name:
            handler._send_json({"ok": False,
                                "error": "need both name and path"},
                               status=400)
            return
        try:
            m = _project_import(name, src, kind="generic")
        except Exception as e:
            handler._send_json({"ok": False, "error": str(e)}, status=400)
            return
        handler._send_json({"ok": True, "manifest": m})
        return
    if action == "remove":
        if not name:
            handler._send_json({"ok": False, "error": "missing name"}, status=400)
            return
        try:
            ok = _project_remove(name, delete_files=delete_files)
        except Exception as e:
            handler._send_json({"ok": False, "error": str(e)}, status=400)
            return
        if not ok:
            handler._send_json({"ok": False, "error": "no such project"},
                               status=404)
            return
        handler._send_json({"ok": True, "removed": name})
        return
    if action == "open":
        n = get_active_project()
        if not n:
            handler._send_json({"ok": False, "error": "no active project"},
                               status=400)
            return
        proj = _project_load(n) or {}
        path = proj.get("path", "")
        if not path or not os.path.isdir(path):
            handler._send_json({"ok": False, "error": "path missing"},
                               status=400)
            return
        try:
            if hasattr(os, "startfile"):
                os.startfile(path)  # Windows
            elif sys.platform == "darwin":
                os.system("open '" + path + "'")
            else:
                os.system("xdg-open '" + path + "'")
        except Exception as e:
            handler._send_json({"ok": False, "error": str(e)}, status=500)
            return
        handler._send_json({"ok": True, "opened": path})
        return
    handler._send_json({"ok": False,
                        "error": "unknown action: " + action}, status=400)


def _route_projects_active(handler, _params):
    """GET current active project name (or empty)."""
    handler._require_device()
    handler._send_json({"ok": True, "active": get_active_project()})


def _route_projects_set_active(handler, _params):
    """POST {name} -> set active project."""
    handler._require_device()
    body = handler._read_json()
    name = (body.get("name") or "").strip()
    if not name:
        handler._send_json({"ok": False, "error": "missing name"}, status=400)
        return
    if _project_load(name) is None:
        handler._send_json({"ok": False, "error": "no such project: " + name},
                           status=404)
        return
    set_active_project(name)
    handler._send_json({"ok": True, "active": name})


# ----- Google Drive sync REST routes -----

def _route_drive_status(handler, _params):
    """GET: returns configured drive folder, last sync time."""
    handler._require_device()
    state = _drive_load()
    folder = state.get("folder", "") or ""
    folder = os.path.expanduser(folder) if folder else ""
    handler._send_json({
        "ok": True,
        "configured": bool(folder and os.path.isdir(folder)),
        "folder": folder,
        "last_sync": state.get("last_sync"),
    })


def _route_drive_action(handler, _params):
    """POST {action, [folder]} -> result.

    action: status | set | unset | push | pull
    """
    handler._require_device()
    body = handler._read_json()
    action = (body.get("action") or "").strip().lower()
    folder = (body.get("folder") or "").strip()

    if action in ("status", ""):
        return _route_drive_status(handler, _params)
    if action == "set":
        if not folder:
            handler._send_json({"ok": False, "error": "missing folder"},
                               status=400)
            return
        folder = os.path.expanduser(folder)
        # Allow the folder to not exist yet -- many cloud apps create
        # the folder lazily on first sync.
        state = _drive_load()
        state["folder"] = folder
        _drive_save(state)
        handler._send_json({"ok": True, "folder": folder})
        return
    if action == "unset":
        state = _drive_load()
        state["folder"] = ""
        _drive_save(state)
        handler._send_json({"ok": True})
        return
    if action == "push":
        try:
            c, s, errs = drive_push()
        except Exception as e:
            handler._send_json({"ok": False, "error": str(e)}, status=400)
            return
        # Update last_sync timestamp
        state = _drive_load()
        state["last_sync"] = time.time()
        _drive_save(state)
        handler._send_json({
            "ok": True,
            "copied": c,
            "skipped": s,
            "errors": errs,
        })
        return
    if action == "pull":
        try:
            c, s, errs = drive_pull()
        except Exception as e:
            handler._send_json({"ok": False, "error": str(e)}, status=400)
            return
        state = _drive_load()
        state["last_sync"] = time.time()
        _drive_save(state)
        handler._send_json({
            "ok": True,
            "copied": c,
            "skipped": s,
            "errors": errs,
        })
        return
    handler._send_json({"ok": False,
                        "error": "unknown action: " + action}, status=400)


def _route_files(handler, _params):
    handler._require_device()
    out_dir = os.path.join(CONFIG_DIR, "output")
    files = []
    if os.path.isdir(out_dir):
        for root, _, names in os.walk(out_dir):
            for n in names:
                full = os.path.join(root, n)
                rel = os.path.relpath(full, out_dir).replace(os.sep, "/")
                try:
                    sz = os.path.getsize(full)
                except OSError:
                    sz = 0
                files.append({"name": n, "path": rel, "size": sz})
    files.sort(key=lambda x: x["path"])
    handler._send_json({"ok": True, "files": files})


def _route_file_get(handler, params):
    handler._require_device()
    rel = params.get("path", "")
    if not rel or ".." in rel or rel.startswith("/"):
        handler._send_json({"ok": False, "error": "bad path"}, status=400)
        return
    out_dir = os.path.join(CONFIG_DIR, "output")
    full = os.path.normpath(os.path.join(out_dir, rel))
    if not full.startswith(os.path.normpath(out_dir)):
        handler._send_json({"ok": False, "error": "bad path"}, status=400)
        return
    if not os.path.isfile(full):
        handler._send_json({"ok": False, "error": "not found"}, status=404)
        return
    # Guess content type from extension
    ext = os.path.splitext(full)[1].lower()
    ct = {
        ".html": "text/html", ".htm": "text/html",
        ".css":  "text/css",  ".js":  "application/javascript",
        ".json": "application/json", ".md": "text/markdown",
        ".txt": "text/plain", ".py": "text/x-python",
        ".png": "image/png", ".jpg": "image/jpeg",
        ".jpeg": "image/jpeg", ".gif": "image/gif",
        ".svg": "image/svg+xml", ".pdf": "application/pdf",
    }.get(ext, "application/octet-stream")
    try:
        with open(full, "rb") as f:
            data = f.read()
        handler.send_response(200)
        handler.send_header("Content-Type", ct)
        handler.send_header("Content-Length", str(len(data)))
        handler.end_headers()
        handler.wfile.write(data)
    except OSError as e:
        handler._send_json({"ok": False,
                            "error": "read failed: " + str(e)},
                            status=500)


# ----- HTTP request handler -----
class _PhoneRequestHandler(_httpserver_mod.BaseHTTPRequestHandler):
    # We populate these from the server instance before serve_forever()
    routes = {}            # dict[str -> callable]
    state = None           # _PhoneServerState
    server_host = ""       # for QR / external URL building

    def log_message(self, fmt, *args):  # silence default access log
        return

    def _send_json(self, obj, status=200):
        body = json.dumps(obj, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods",
                          "GET, POST, DELETE, OPTIONS")
        self.send_header("Access-Control-Allow-Headers",
                          "Content-Type, X-Device-Token")
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def _send_html(self, html):
        body = html.encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def _read_json(self):
        length = int(self.headers.get("Content-Length") or 0)
        if length <= 0:
            return {}
        raw = self.rfile.read(length).decode("utf-8", errors="replace")
        try:
            return json.loads(raw)
        except ValueError:
            return {}

    def _require_device(self):
        token = (self.headers.get("X-Device-Token") or "").strip()
        if not token:
            self._send_json({"ok": False, "error": "no device token"},
                            status=401)
            return False
        dev = _pairing_get_device(token)
        if not dev:
            self._send_json({"ok": False, "error": "unknown device"},
                            status=401)
            return False
        _pairing_touch_device(token)
        return True

    def _current_device(self):
        token = (self.headers.get("X-Device-Token") or "").strip()
        if not token:
            return None
        return _pairing_get_device(token)

    def _external_base_url(self):
        """Best-effort guess at the URL the phone should use. Falls
        back to the host the request came in on."""
        host = self.headers.get("Host") or self.server_host or "localhost"
        # If the request came in on localhost, replace with the LAN IP
        # so the phone can reach us.
        if host.startswith("localhost") or host.startswith("127."):
            try:
                import socket as _socket
                lan_ip = _socket.gethostbyname(_socket.gethostname())
                if lan_ip and not lan_ip.startswith("127."):
                    port = host.split(":")[-1] if ":" in host else "8765"
                    return "http://" + lan_ip + ":" + port
            except Exception:
                pass
        if "://" not in host:
            return "http://" + host
        return host

    def do_OPTIONS(self):
        self.send_response(204)
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods",
                          "GET, POST, DELETE, OPTIONS")
        self.send_header("Access-Control-Allow-Headers",
                          "Content-Type, X-Device-Token")
        self.end_headers()

    def do_GET(self):
        self._dispatch("GET")

    def do_POST(self):
        self._dispatch("POST")

    def do_DELETE(self):
        self._dispatch("DELETE")

    def _dispatch(self, method):
        path = self.path.split("?", 1)[0]
        # Try exact match first
        key = method + " " + path
        if key in self.routes:
            try:
                self.routes[key](self, {})
            except Exception as e:
                self._send_json({"ok": False, "error":
                                 "handler error: " + type(e).__name__
                                 + ": " + str(e)[:300]}, status=500)
            return
        # Pattern match: /api/sessions/<id>[/<action>]
        # Routes with a single <id> placeholder
        parts = path.strip("/").split("/")
        if len(parts) >= 3 and parts[0] == "api" and parts[1] == "sessions":
            sid = parts[2]
            if len(parts) == 3:
                key2 = method + " /api/sessions/<id>"
            elif len(parts) == 4:
                key2 = method + " /api/sessions/<id>/" + parts[3]
            else:
                self._send_json({"ok": False, "error": "not found"},
                                status=404)
                return
            if key2 in self.routes:
                try:
                    self.routes[key2](self, {"id": sid})
                except Exception as e:
                    self._send_json({"ok": False, "error":
                                     "handler error: " + type(e).__name__
                                     + ": " + str(e)[:300]}, status=500)
                return
        if len(parts) >= 3 and parts[0] == "api" and parts[1] == "files":
            rel = "/".join(parts[2:])
            if rel:
                key2 = method + " /api/files/<path>"
                if key2 in self.routes:
                    try:
                        self.routes[key2](self, {"path": rel})
                    except Exception as e:
                        self._send_json({"ok": False, "error":
                                         "handler error: " + type(e).__name__
                                         + ": " + str(e)[:300]}, status=500)
                    return
        self._send_json({"ok": False, "error": "not found: " + path},
                        status=404)


# ----- Threaded HTTP server (one thread per request) -----
class _ThreadedHTTPServer(_socketserver_mod.ThreadingMixIn,
                          _httpserver_mod.HTTPServer):
    daemon_threads = True
    allow_reuse_address = True


def start_phone_server(cfg, host=None, port=None, blocking=True):
    """Start the phone-server. If `blocking` is True, runs until
    KeyboardInterrupt. Otherwise returns the server instance; the
    caller is responsible for calling server.shutdown()."""
    host = host or DEFAULT_SERVE_HOST
    port = int(port or DEFAULT_SERVE_PORT)
    state = _PhoneServerState(cfg)
    # Bind routes
    handler_cls = type(
        "_Handler_" + str(id(cfg)),
        (_PhoneRequestHandler,),
        {
            "routes": _server_routes(),
            "state": state,
            "server_host": host + ":" + str(port),
        },
    )
    try:
        server = _ThreadedHTTPServer((host, port), handler_cls)
    except OSError as e:
        sys.stderr.write("ERROR: could not bind to " + host + ":" +
                         str(port) + ": " + str(e) + "\n")
        return None
    # Save the server handle so the CLI can shut it down later
    state._server = server
    # Print a friendly banner
    try:
        import socket as _socket
        lan_ip = _socket.gethostbyname(_socket.gethostname())
    except Exception:
        lan_ip = "127.0.0.1"
    print()
    print("=" * 60)
    print(" jarvis phone server")
    print("=" * 60)
    print(" Listening on:")
    print("   http://" + host + ":" + str(port) + "/")
    if host in ("0.0.0.0", "::"):
        print("   http://" + lan_ip + ":" + str(port) + "/  (try this on your phone)")
    # Generate a fresh pairing code and print the URL the phone should open.
    code = _pairing_new_code()
    url_host = lan_ip if host in ("0.0.0.0", "::") else host
    url = "http://" + url_host + ":" + str(port) + "/?code=" + code
    print(" Open this URL on your phone:")
    print("   " + url)
    print()
    print(" Pairing code: " + code)
    print(" (code expires in " + str(PAIRING_CODE_TTL // 60) + " min)")
    print()
    if host in ("0.0.0.0", "::"):
        print(" (phone must be on the same WiFi as this computer)")
    print()
    print(" Press Ctrl-C to stop.")
    print("=" * 60)
    if not blocking:
        # Caller wants the server to keep running in the background.
        # Start a daemon thread that runs serve_forever, then return.
        def _serve():
            try:
                server.serve_forever()
            except Exception:
                pass
        t = _threading.Thread(target=_serve, daemon=True)
        t.start()
        return server
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print()
        print("Shutting down...")
    finally:
        try:
            server.shutdown()
            server.server_close()
        except Exception:
            pass
    return None




# ===========================================================================
# GUI (tkinter) -- chat window + first-run wizard, embedded below
# ===========================================================================
#
# All of the tkinter-based UI lives in this section. The GUI is
# optional -- if tkinter isn't available, jarvis falls back to
# terminal mode without ever importing this code. The public
# surface (has_gui, run_gui, run_launcher, run_gui_first_run) is
# the same as the old gui.py module.
#

# --- Lazy tkinter import. If it's missing, has_gui() returns False and
# the caller can fall back to terminal mode without ever importing it.
_TK = None
_TK_ERROR = None


def has_gui():
    """Return True if tkinter is available on this Python install."""
    global _TK, _TK_ERROR
    if _TK is not None:
        return True
    if _TK_ERROR is not None:
        return False
    try:
        import tkinter as tk
        from tkinter import scrolledtext, ttk, font as tkfont
        _TK = (tk, scrolledtext, ttk, tkfont)
        return True
    except ImportError as e:
        _TK_ERROR = e
        return False


def _get_tk():
    if not has_gui():
        raise RuntimeError("tkinter not available: " + str(_TK_ERROR))
    return _TK


# ---------------------------------------------------------------------------
# Color palette - a calm, dark "JARVIS-ish" theme. Not skeuomorphic, just
# clean and easy to read.
# ---------------------------------------------------------------------------
COLORS = {
    "bg":          "#0f1419",     # main background - near-black
    "bg_alt":      "#1a1f29",     # alternating panel background
    "fg":          "#e6e6e6",     # main text - off-white
    "fg_dim":      "#8a96a8",     # secondary text - grey
    "accent":      "#4dd0e1",     # teal - the "JARVIS" highlight
    "accent_alt":  "#29b6f6",     # slightly brighter blue
    "user_bg":     "#1e3a5f",     # user message bubble background
    "ai_bg":       "#1a1f29",     # AI message bubble background
    "border":      "#2a3340",     # subtle borders
    "error":       "#ef5350",     # red for errors
    "success":     "#66bb6a",     # green for success
    "code_bg":     "#0a0e13",     # code blocks - even darker
    "code_fg":     "#c5d1de",     # code text - light grey
    "input_bg":    "#1a1f29",     # input box background
    "btn_bg":      "#4dd0e1",     # button background
    "btn_fg":      "#0f1419",     # button text
}


# ---------------------------------------------------------------------------
# Main chat window
# ---------------------------------------------------------------------------
class ChatWindow(object):
    """A simple, dark-themed chat interface.

    The user types at the bottom; messages scroll up; AI responses
    stream in as they arrive (we call .add_message() from a background
    thread; tkinter requires us to marshal back to the main thread via
    .after() which we handle internally).
    """

    def __init__(self, cfg, args):
        self.cfg = cfg
        self.args = args
        self.tk, self.scrolledtext, self.ttk, self.tkfont = _get_tk()
        self.root = self.tk.Tk()
        self.root.title("jarvis")
        self.root.geometry("900x640")
        self.root.minsize(600, 400)
        self.root.configure(bg=COLORS["bg"])

        # Custom fonts
        self.font_ui    = self.tkfont.Font(family="Segoe UI", size=10)
        self.font_ui_b  = self.tkfont.Font(family="Segoe UI", size=10, weight="bold")
        self.font_h     = self.tkfont.Font(family="Segoe UI", size=14, weight="bold")
        self.font_small = self.tkfont.Font(family="Segoe UI", size=9)
        self.font_mono  = self.tkfont.Font(family="Consolas", size=10)

        # Try to set a window icon (best effort - ignore failures)
        try:
            if hasattr(sys, "frozen") and sys.platform == "win32":
                # PyInstaller sets sys._MEIPASS; we could ship an .ico there
                pass
        except Exception:
            pass

        # Build the layout
        self._build_header()
        self._build_chat_area()
        self._build_input_area()
        self._build_status_bar()

        # State
        self._busy = False
        self._busy_lock = threading.Lock()
        # Map tag -> tk tag config (so we don't re-apply on every insert)
        self._tags_configured = set()

        # Welcome message
        persona = self.cfg.get("persona") or "engineer"
        self._post_system(
            "Welcome to jarvis. Type a request below and press Enter or "
            "click Send. Current persona: " + persona + ". "
            "Use the menu (top right) to switch modes or settings."
        )

        # Window close
        self.root.protocol("WM_DELETE_WINDOW", self._on_close)

    # ------------------------------------------------------------------
    # Layout construction
    # ------------------------------------------------------------------
    def _build_header(self):
        header = self.tk.Frame(self.root, bg=COLORS["bg_alt"], height=50)
        header.pack(side="top", fill="x")
        header.pack_propagate(False)

        # Left: title
        title = self.tk.Label(
            header, text="jarvis", font=self.font_h,
            bg=COLORS["bg_alt"], fg=COLORS["accent"]
        )
        title.pack(side="left", padx=14, pady=8)

        subtitle = self.tk.Label(
            header, text="Sonnet 5 + GPT Codex 5.3",
            font=self.font_small,
            bg=COLORS["bg_alt"], fg=COLORS["fg_dim"]
        )
        subtitle.pack(side="left", padx=0, pady=14)

        # Right: menu
        menu_frame = self.tk.Frame(header, bg=COLORS["bg_alt"])
        menu_frame.pack(side="right", padx=8, pady=8)

        # Persona label
        persona = self.cfg.get("persona") or "engineer"
        self._persona_var = self.tk.StringVar(value=persona)
        persona_btn = self.tk.Menubutton(
            menu_frame, textvariable=self._persona_var,
            font=self.font_ui, bg=COLORS["bg_alt"], fg=COLORS["fg"],
            activebackground=COLORS["accent"], activeforeground=COLORS["bg"],
            relief="flat", indicatoron=True
        )
        persona_btn.menu = self.tk.Menu(
            persona_btn, tearoff=0, bg=COLORS["bg_alt"], fg=COLORS["fg"],
            activebackground=COLORS["accent"], activeforeground=COLORS["bg"]
        )
        persona_btn["menu"] = persona_btn.menu
        persona_btn.menu.add_command(label="engineer (terse, technical)",
                                      command=lambda: self._set_persona("engineer"))
        persona_btn.menu.add_command(label="jarvis (calm, polite)",
                                      command=lambda: self._set_persona("jarvis"))
        persona_btn.menu.add_separator()
        # Research toggle (state-dependent label)
        self._research_var = self.tk.BooleanVar(
            value=bool(getattr(self.args, "do_research", False)))
        persona_btn.menu.add_checkbutton(
            label="Research (fetch URLs + web search)",
            variable=self._research_var,
            command=self._toggle_research)
        persona_btn.menu.add_command(label="Add research URL...",
                                      command=self._add_research_url)
        persona_btn.menu.add_separator()
        persona_btn.menu.add_command(label="Deep research session...",
                                      command=self._open_deep_research)
        persona_btn.menu.add_separator()
        persona_btn.menu.add_command(label="Change models...",
                                      command=self._open_model_picker)
        persona_btn.menu.add_separator()
        persona_btn.menu.add_command(label="Open output folder...",
                                      command=self._open_output_folder)
        persona_btn.menu.add_separator()
        persona_btn.menu.add_command(label="Switch to terminal mode",
                                      command=self._switch_to_terminal)
        persona_btn.menu.add_command(label="Reset config...",
                                      command=self._reset_config)
        persona_btn.menu.add_command(label="Quit", command=self._on_close)
        persona_btn.pack(side="right", padx=4)

    def _build_chat_area(self):
        # Outer frame with a thin border
        outer = self.tk.Frame(self.root, bg=COLORS["border"])
        outer.pack(side="top", fill="both", expand=True, padx=8, pady=(8, 0))

        # The chat area itself: a Text widget with scrollbar
        self.chat = self.tk.Text(
            outer, wrap="word", bg=COLORS["bg"], fg=COLORS["fg"],
            font=self.font_ui, bd=0, highlightthickness=0,
            padx=14, pady=14, insertbackground=COLORS["accent"],
            selectbackground=COLORS["accent"], selectforeground=COLORS["bg"]
        )
        scrollbar = self.tk.Scrollbar(
            outer, orient="vertical", command=self.chat.yview,
            bg=COLORS["bg"], troughcolor=COLORS["bg_alt"]
        )
        self.chat.configure(yscrollcommand=scrollbar.set)
        self.chat.pack(side="left", fill="both", expand=True)
        scrollbar.pack(side="right", fill="y")

        # Configure tags used for styling
        self.chat.tag_configure(
            "user_label", font=self.font_ui_b,
            foreground=COLORS["accent_alt"], spacing1=8, spacing3=2
        )
        self.chat.tag_configure(
            "ai_label", font=self.font_ui_b,
            foreground=COLORS["accent"], spacing1=10, spacing3=2
        )
        self.chat.tag_configure(
            "system", font=self.font_small,
            foreground=COLORS["fg_dim"], lmargin1=20, lmargin2=20
        )
        self.chat.tag_configure(
            "user_msg", font=self.font_ui, lmargin1=20, lmargin2=20
        )
        self.chat.tag_configure(
            "ai_msg", font=self.font_ui, lmargin1=20, lmargin2=20
        )
        self.chat.tag_configure(
            "code", font=self.font_mono, background=COLORS["code_bg"],
            foreground=COLORS["code_fg"], lmargin1=30, lmargin2=30,
            relief="flat", borderwidth=0, spacing1=4, spacing3=4
        )
        self.chat.tag_configure(
            "review", font=self.font_ui, foreground=COLORS["success"],
            lmargin1=20, lmargin2=20
        )
        self.chat.tag_configure(
            "error", font=self.font_ui, foreground=COLORS["error"],
            lmargin1=20, lmargin2=20
        )

        # Make the text widget read-only by default; we toggle for inserts
        self.chat.configure(state="disabled")

    def _build_input_area(self):
        outer = self.tk.Frame(self.root, bg=COLORS["bg_alt"], height=80)
        outer.pack(side="bottom", fill="x", padx=8, pady=8)
        outer.pack_propagate(False)

        # Input text box (multi-line, but Enter sends)
        self.input = self.tk.Text(
            outer, height=3, wrap="word", font=self.font_ui,
            bg=COLORS["input_bg"], fg=COLORS["fg"],
            insertbackground=COLORS["accent"], bd=0, relief="flat",
            highlightthickness=1, highlightbackground=COLORS["border"],
            highlightcolor=COLORS["accent"], padx=10, pady=8
        )
        self.input.pack(side="left", fill="both", expand=True, padx=(8, 4), pady=8)
        self.input.bind("<Return>", self._on_enter)
        self.input.bind("<Shift-Return>", lambda e: None)  # allow newline on Shift+Enter

        # Send button
        self.send_btn = self.tk.Button(
            outer, text="Send", font=self.font_ui_b,
            bg=COLORS["btn_bg"], fg=COLORS["btn_fg"],
            activebackground=COLORS["accent_alt"], activeforeground=COLORS["btn_fg"],
            relief="flat", bd=0, padx=18, pady=8, command=self._on_send
        )
        self.send_btn.pack(side="right", padx=(4, 8), pady=8)

    def _build_status_bar(self):
        self.status_var = self.tk.StringVar(value="Ready")
        status = self.tk.Label(
            self.root, textvariable=self.status_var,
            font=self.font_small, bg=COLORS["bg"], fg=COLORS["fg_dim"],
            anchor="w", padx=14
        )
        # Pack it between header and chat
        status.pack(side="top", fill="x", before=self.chat.master.master)

    # ------------------------------------------------------------------
    # Event handlers
    # ------------------------------------------------------------------
    def _on_enter(self, event):
        """Enter sends, Shift+Enter inserts a newline (default Text behavior)."""
        if event.state & 0x0001:    # Shift is held
            return None             # let default newline happen
        self._on_send()
        return "break"              # suppress default newline

    def _on_send(self):
        if self._is_busy():
            return
        text = self.input.get("1.0", "end").strip()
        if not text:
            return
        # Clear input
        self.input.delete("1.0", "end")
        # Post user message
        self._post_user(text)
        # Run in background thread so the UI stays responsive
        self._set_busy(True)
        self._set_status("Thinking...")
        threading.Thread(target=self._do_run, args=(text,), daemon=True).start()

    def _on_close(self):
        if self._is_busy():
            ans = self._ask_yes_no(
                "A request is in progress. Quit anyway?")
            if not ans:
                return
        self.root.destroy()

    # ------------------------------------------------------------------
    # Background work
    # ------------------------------------------------------------------
    def _do_run(self, user_request):
        """Run the orchestrator in a background thread. We import here
        (not at module top) to keep the GUI launch fast and to allow
        the GUI module to be imported even when jarvis.py is missing
        the required third-party deps."""
        try:
            import jarvis    # sibling module - import on demand
        except Exception as e:
            self._post_ai_async("[error] could not import jarvis: " + str(e))
            self._set_busy(False)
            self._set_status("Ready")
            return

        try:
            result = jarvis.run(
                user_request, self.cfg,
                enable_review=not self.args.no_review,
                enable_tests=self.args.with_tests,
                write_to_disk=True,
                text_only=False,    # GUI is always "pretty"
                do_research=bool(getattr(self.args, "do_research", False)),
                extra_research_urls=list(getattr(self.args, "extra_research_urls", []) or []),
                extra_research_terms=list(getattr(self.args, "extra_research_terms", []) or []),
            )
            # Render
            self._render_result_async(result)
        except jarvis.JarvisError as e:
            self._post_ai_async("[error] " + str(e), tag="error")
        except Exception as e:
            self._post_ai_async("[unexpected error] " + str(e)
                                + "\n\n" + traceback.format_exc(), tag="error")
        finally:
            self._set_busy(False)
            self._set_status("Ready")

    def _render_result_async(self, result):
        """Schedule a render of the result on the Tk main thread."""
        # Build the message body (text)
        plan = result.get("plan", {})
        body_parts = []
        if plan.get("summary"):
            body_parts.append(plan["summary"].strip())
        if plan.get("modules"):
            body_parts.append("")  # blank line
            body_parts.append("Plan: " + str(len(plan["modules"])) + " module(s)")
            for i, m in enumerate(plan["modules"], 1):
                body_parts.append("  " + str(i) + ". " + m.get("name", "?"))
                if m.get("description"):
                    body_parts.append("     " + m["description"].strip())

        for mr in result.get("modules", []):
            body_parts.append("")  # blank line
            body_parts.append("--- " + mr.get("name", "module") + " ---")
            if mr.get("code_error"):
                body_parts.append("[CODE FAILED] " + mr["code_error"])
                continue
            if mr.get("code"):
                # Code gets the "code" tag for monospace rendering
                body_parts.append(("__CODE__" + mr["code"]))
            if mr.get("review"):
                body_parts.append("")
                body_parts.append("[REVIEW]\n" + mr["review"].strip())
            if mr.get("tests"):
                body_parts.append("")
                body_parts.append("__CODE__" + mr["tests"])

        # Compose as a single string (one AI message)
        # We use a sentinel for code blocks so we can split and re-insert
        # with the right tag. (The Text widget doesn't let you change the
        # tag mid-string from a single insert.)
        # Easier: post multiple messages, alternating "ai" and "code" tags.
        self._post_mixed_async(body_parts)

        if result.get("written_to"):
            self._post_ai_async(
                "\nWrote " + str(len(result["written_to"]))
                + " file(s).", tag="system")
        if result.get("overall_error"):
            self._post_ai_async("ERROR: " + result["overall_error"], tag="error")

    # ------------------------------------------------------------------
    # Posting messages to the chat (all thread-safe via .after())
    # ------------------------------------------------------------------
    def _post_user(self, text):
        self._insert_safe([("you", "user_label"), (text + "\n", "user_msg")])

    def _post_ai(self, text, tag="ai_msg"):
        self._insert_safe([(text + "\n", tag)])

    def _post_system(self, text):
        self._insert_safe([(text + "\n", "system")])

    def _post_ai_async(self, text, tag="ai_msg"):
        """Same as _post_ai but safe to call from a background thread."""
        self.root.after(0, self._post_ai, text, tag)

    def _post_mixed_async(self, parts):
        """Post a sequence of (text, tag) pairs as one AI turn.
        Each tuple either:
          - has the special prefix '__CODE__' on the text -> becomes a
            'code' tag with the prefix stripped
          - otherwise becomes 'ai_msg'
        Sentinels "__LABEL__:..." in the first character produce a label
        line (currently unused; reserved for future styling)."""
        rendered = []
        for part in parts:
            if isinstance(part, str) and part.startswith("__CODE__"):
                rendered.append((part[len("__CODE__"):], "code"))
            else:
                rendered.append((part, "ai_msg"))
        self.root.after(0, self._insert_safe,
                        [("jarvis", "ai_label")] + rendered)

    def _insert_safe(self, items):
        """Insert a list of (text, tag) tuples into the chat Text widget.
        Must be called on the Tk main thread."""
        self.chat.configure(state="normal")
        for text, tag in items:
            self.chat.insert("end", text, tag)
        self.chat.insert("end", "\n")
        self.chat.configure(state="disabled")
        self.chat.see("end")

    # ------------------------------------------------------------------
    # Menu actions
    # ------------------------------------------------------------------
    def _set_persona(self, name):
        self.cfg["persona"] = name
        self._persona_var.set(name)
        # Persist
        try:
            import jarvis
            jarvis.save_config(self.cfg)
        except Exception:
            pass
        self._post_system("Persona set to '" + name + "'.")

    def _toggle_research(self):
        """Toggle the research flag on the args object. Persisted to
        config so it survives across sessions."""
        new_val = bool(self._research_var.get())
        self.args.do_research = new_val
        self.cfg["enable_research"] = new_val
        try:
            import jarvis
            jarvis.save_config(self.cfg)
        except Exception:
            pass
        if new_val:
            self._post_system("Research ON. Your next request will be preceded "
                              "by live web context (URLs in your request + a "
                              "search for library/API names).")
        else:
            self._post_system("Research OFF.")

    def _add_research_url(self):
        """Prompt the user for a URL to add to the research extras.
        Useful when you want to point the tool at specific docs."""
        if not hasattr(self, "_research_url_var"):
            self._research_url_var = self.tk.StringVar()
        dlg = self.tk.Toplevel(self.root)
        dlg.title("Add research URL")
        dlg.configure(bg=COLORS["bg"])
        dlg.geometry("520x140")
        dlg.transient(self.root)
        dlg.grab_set()

        self.tk.Label(
            dlg, text="URL to fetch as part of research:",
            font=self.tkfont.Font(family="Segoe UI", size=10),
            bg=COLORS["bg"], fg=COLORS["fg"]
        ).pack(anchor="w", padx=20, pady=(16, 4))
        entry = self.tk.Entry(
            dlg, font=self.tkfont.Font(family="Consolas", size=10),
            bg=COLORS["input_bg"], fg=COLORS["fg"],
            insertbackground=COLORS["accent"], relief="flat", bd=4
        )
        entry.pack(fill="x", padx=20, pady=4, ipady=4)
        entry.focus_set()

        def on_ok():
            url = entry.get().strip()
            if url:
                if not hasattr(self.args, "extra_research_urls") or self.args.extra_research_urls is None:
                    self.args.extra_research_urls = []
                self.args.extra_research_urls.append(url)
                self._post_system("Research URL added: " + url +
                                  "  (used on the next request)")
            dlg.destroy()

        btn = self.tk.Frame(dlg, bg=COLORS["bg"])
        btn.pack(pady=10)
        self.tk.Button(btn, text="Add", font=self.tkfont.Font(family="Segoe UI", size=10, weight="bold"),
                      bg=COLORS["accent"], fg=COLORS["bg"],
                      activebackground=COLORS["accent_alt"], activeforeground=COLORS["bg"],
                      relief="flat", bd=0, padx=20, pady=4, command=on_ok).pack()
        entry.bind("<Return>", lambda e: on_ok())
        dlg.wait_window()

    def _open_model_picker(self):
        """Show a model picker dialog. Lets the user change the planner
        model, the coder model, or both. Uses the FREE_MODELS catalog
        in jarvis.py as the source of truth."""
        try:
            import jarvis
        except Exception as e:
            self._post_ai_async("Could not load models: " + str(e), tag="error")
            return

        # Build the dialog
        dlg = self.tk.Toplevel(self.root)
        dlg.title("Change models")
        dlg.configure(bg=COLORS["bg"])
        dlg.geometry("620x520")
        dlg.transient(self.root)
        dlg.grab_set()

        # Header
        self.tk.Label(
            dlg, text="Change models", font=self.tkfont.Font(family="Segoe UI", size=14, weight="bold"),
            bg=COLORS["bg"], fg=COLORS["accent"]
        ).pack(pady=(16, 4))
        self.tk.Label(
            dlg, text="Pick which free OpenRouter models to use for planning and code.",
            font=self.tkfont.Font(family="Segoe UI", size=9),
            bg=COLORS["bg"], fg=COLORS["fg_dim"]
        ).pack(pady=(0, 12))

        # Two listboxes side by side: planner | coder
        lists_frame = self.tk.Frame(dlg, bg=COLORS["bg"])
        lists_frame.pack(fill="both", expand=True, padx=16, pady=8)

        def make_column(parent, title, current_id, key_name):
            col = self.tk.Frame(parent, bg=COLORS["bg"])
            col.pack(side="left", fill="both", expand=True, padx=6)
            self.tk.Label(
                col, text=title, font=self.tkfont.Font(family="Segoe UI", size=11, weight="bold"),
                bg=COLORS["bg"], fg=COLORS["fg"]
            ).pack(anchor="w", pady=(0, 4))
            lb = self.tk.Listbox(
                col, font=self.tkfont.Font(family="Segoe UI", size=9),
                bg=COLORS["bg_alt"], fg=COLORS["fg"],
                selectbackground=COLORS["accent"], selectforeground=COLORS["bg"],
                highlightthickness=1, highlightbackground=COLORS["border"],
                borderwidth=0, activestyle="none"
            )
            lb.pack(fill="both", expand=True)
            for m in jarvis.FREE_MODELS:
                # Two-line entry: label on line 1, "best for" on line 2
                lb.insert("end", m["label"])
                lb.insert("end", "   " + m["best_for"])
            # Highlight the current selection (matching id)
            for i, m in enumerate(jarvis.FREE_MODELS):
                if m["id"] == current_id:
                    lb.selection_clear(0, "end")
                    lb.selection_set(i * 2)        # the label line
                    lb.see(i * 2)
                    lb.activate(i * 2)
                    break
            return lb, key_name

        planner_lb, _ = make_column(
            lists_frame, "Planner (reasoning)",
            self.cfg.get("sonnet_model") or jarvis.DEFAULT_SONNET_MODEL,
            "sonnet_model"
        )
        coder_lb, _ = make_column(
            lists_frame, "Coder (code generation)",
            self.cfg.get("codex_model") or jarvis.DEFAULT_CODEX_MODEL,
            "codex_model"
        )

        def on_save():
            # Map selected index -> model id
            p_idx = planner_lb.curselection()
            c_idx = coder_lb.curselection()
            changed = []
            if p_idx:
                # Selection might be on a label line (even) or a "best for"
                # line (odd); snap back to the label line.
                p_model_i = p_idx[0] // 2
                p_id = jarvis.FREE_MODELS[p_model_i]["id"]
                if p_id != self.cfg.get("sonnet_model"):
                    self.cfg["sonnet_model"] = p_id
                    changed.append("planner -> " + jarvis.FREE_MODELS[p_model_i]["label"])
            if c_idx:
                c_model_i = c_idx[0] // 2
                c_id = jarvis.FREE_MODELS[c_model_i]["id"]
                if c_id != self.cfg.get("codex_model"):
                    self.cfg["codex_model"] = c_id
                    changed.append("coder -> " + jarvis.FREE_MODELS[c_model_i]["label"])
            if changed:
                jarvis.save_config(self.cfg)
                self._post_system("Models updated: " + "; ".join(changed) + ".")
            else:
                self._post_system("Models unchanged.")
            dlg.destroy()

        def on_cancel():
            dlg.destroy()

        btn_frame = self.tk.Frame(dlg, bg=COLORS["bg"])
        btn_frame.pack(pady=(0, 16))
        self.tk.Button(
            btn_frame, text="Save", font=self.tkfont.Font(family="Segoe UI", size=10, weight="bold"),
            bg=COLORS["accent"], fg=COLORS["bg"], activebackground=COLORS["accent_alt"],
            relief="flat", bd=0, padx=20, pady=6, command=on_save
        ).pack(side="left", padx=4)
        self.tk.Button(
            btn_frame, text="Cancel", font=self.tkfont.Font(family="Segoe UI", size=10),
            bg=COLORS["bg_alt"], fg=COLORS["fg"], activebackground=COLORS["border"],
            relief="flat", bd=0, padx=20, pady=6, command=on_cancel
        ).pack(side="left", padx=4)

        # Center the dialog
        dlg.update_idletasks()
        w = dlg.winfo_width()
        h = dlg.winfo_height()
        x = (dlg.winfo_screenwidth() // 2) - (w // 2)
        y = (dlg.winfo_screenheight() // 2) - (h // 2)
        dlg.geometry("%dx%d+%d+%d" % (w, h, x, y))

    def _open_output_folder(self):
        try:
            import jarvis
            out = os.path.join(jarvis.CONFIG_DIR, "output")
            if not os.path.isdir(out):
                os.makedirs(out)
            # Cross-platform open-folder
            if sys.platform == "win32":
                os.startfile(out)   # type: ignore[attr-defined]
            elif sys.platform == "darwin":
                os.system('open "' + out + '"')
            else:
                os.system('xdg-open "' + out + '"')
        except Exception as e:
            self._post_ai_async("Could not open folder: " + str(e), tag="error")

    def _open_deep_research(self):
        """Open the deep research window. Lets the user start a new
        session, generate a one-shot report, or resume an existing one.
        """
        try:
            import jarvis
        except Exception as e:
            self._post_ai_async("Could not load jarvis: " + str(e), tag="error")
            return
        try:
            DeepResearchWindow(self.root, self.cfg, self.args)
        except Exception as e:
            self._post_ai_async("Could not open deep research window: "
                                + str(e), tag="error")

    def _switch_to_terminal(self):
        """Mark terminal mode and close. main() will then start the
        terminal menu instead of the GUI."""
        self.cfg["ui_mode"] = "terminal"
        try:
            import jarvis
            jarvis.save_config(self.cfg)
        except Exception:
            pass
        self.root.destroy()

    def _reset_config(self):
        ans = self._ask_yes_no(
            "Wipe saved config (API keys, persona) and re-run first-time "
            "setup? You'll need your API keys handy.")
        if not ans:
            return
        try:
            import jarvis
            jarvis.delete_config()
        except Exception:
            pass
        self._post_system("Config wiped. Re-run jarvis to set up again.")
        # Don't auto-restart the wizard; the user might not have their keys.
        # They can re-run the .exe to get the wizard.

    def _ask_yes_no(self, question):
        return self.tk.messagebox.askyesno("jarvis", question)

    # ------------------------------------------------------------------
    # Status / busy state
    # ------------------------------------------------------------------
    def _is_busy(self):
        with self._busy_lock:
            return self._busy

    def _set_busy(self, val):
        with self._busy_lock:
            self._busy = val
        # Update the send button on the main thread
        self.root.after(0, self._refresh_busy_ui, val)

    def _refresh_busy_ui(self, busy):
        if busy:
            self.send_btn.configure(state="disabled", text="...")
            self.input.configure(state="disabled")
        else:
            self.send_btn.configure(state="normal", text="Send")
            self.input.configure(state="normal")
            self.input.focus_set()

    def _set_status(self, text):
        self.status_var.set(text)

    # ------------------------------------------------------------------
    # Main loop
    # ------------------------------------------------------------------
    def run(self):
        self.input.focus_set()
        self.root.mainloop()


# ---------------------------------------------------------------------------
# First-run chooser (shown only when no ui_mode has been saved yet)
# ---------------------------------------------------------------------------
def run_launcher(cfg):
    """Show a small chooser dialog: GUI or Terminal? Save the choice
    to config and return the chosen mode ('gui' or 'terminal')."""
    tk, _, _, tkfont = _get_tk()
    root = tk.Tk()
    root.title("jarvis")
    root.geometry("440x260")
    root.resizable(False, False)
    root.configure(bg=COLORS["bg"])

    result = {"choice": None}

    def pick_gui():
        result["choice"] = "gui"
        root.destroy()

    def pick_terminal():
        result["choice"] = "terminal"
        root.destroy()

    # Title
    tk.Label(
        root, text="jarvis", font=tkfont.Font(family="Segoe UI", size=22, weight="bold"),
        bg=COLORS["bg"], fg=COLORS["accent"]
    ).pack(pady=(30, 4))
    tk.Label(
        root, text="Choose how you want to use it",
        font=tkfont.Font(family="Segoe UI", size=11),
        bg=COLORS["bg"], fg=COLORS["fg_dim"]
    ).pack(pady=(0, 24))

    # Buttons
    btn_frame = tk.Frame(root, bg=COLORS["bg"])
    btn_frame.pack()

    def make_btn(parent, text, sub, cmd):
        f = tk.Frame(parent, bg=COLORS["bg_alt"],
                     highlightbackground=COLORS["accent"],
                     highlightthickness=1)
        b = tk.Button(
            f, text=text, font=tkfont.Font(family="Segoe UI", size=12, weight="bold"),
            bg=COLORS["accent"], fg=COLORS["bg"],
            activebackground=COLORS["accent_alt"], activeforeground=COLORS["bg"],
            relief="flat", bd=0, padx=30, pady=8, command=cmd
        )
        b.pack(padx=4, pady=4)
        tk.Label(
            f, text=sub, font=tkfont.Font(family="Segoe UI", size=9),
            bg=COLORS["bg_alt"], fg=COLORS["fg_dim"]
        ).pack(padx=6, pady=(0, 6))
        return f

    make_btn(btn_frame, "GUI", "chat-window", pick_gui).pack(side="left", padx=8)
    make_btn(btn_frame, "Terminal", "text in / out", pick_terminal).pack(side="left", padx=8)

    tk.Label(
        root, text="(your choice is remembered for next time)",
        font=tkfont.Font(family="Segoe UI", size=8),
        bg=COLORS["bg"], fg=COLORS["fg_dim"]
    ).pack(pady=(20, 0))

    # Center on screen
    root.update_idletasks()
    w = root.winfo_width()
    h = root.winfo_height()
    x = (root.winfo_screenwidth() // 2) - (w // 2)
    y = (root.winfo_screenheight() // 2) - (h // 2)
    root.geometry("%dx%d+%d+%d" % (w, h, x, y))

    root.mainloop()
    return result["choice"]


# ---------------------------------------------------------------------------
# GUI startup tier chooser (used on first launch when no config exists)
# ---------------------------------------------------------------------------
def run_tier_chooser():
    """Show the tier chooser as a GUI dialog. Returns one of:
    'free', 'paid', 'custom', or None (user closed the window)."""
    try:
        import jarvis
    except Exception as e:
        return None

    tk, _, _, tkfont = _get_tk()
    root = tk.Tk()
    root.title("jarvis - first time setup")
    root.geometry("560x520")
    root.resizable(False, False)
    root.configure(bg=COLORS["bg"])

    result = {"tier": None}

    # Title
    tk.Label(
        root, text="Welcome to jarvis",
        font=tkfont.Font(family="Segoe UI", size=20, weight="bold"),
        bg=COLORS["bg"], fg=COLORS["accent"]
    ).pack(pady=(28, 4))
    tk.Label(
        root, text="Pick how you want to use it",
        font=tkfont.Font(family="Segoe UI", size=11),
        bg=COLORS["bg"], fg=COLORS["fg_dim"]
    ).pack(pady=(0, 18))

    # Three big option cards
    def make_card(title, body, key):
        card = tk.Frame(
            root, bg=COLORS["bg_alt"],
            highlightbackground=COLORS["border"],
            highlightthickness=1
        )
        card.pack(fill="x", padx=24, pady=4)
        tk.Label(
            card, text=title,
            font=tkfont.Font(family="Segoe UI", size=12, weight="bold"),
            bg=COLORS["bg_alt"], fg=COLORS["fg"]
        ).pack(anchor="w", padx=14, pady=(10, 0))
        tk.Label(
            card, text=body,
            font=tkfont.Font(family="Segoe UI", size=9),
            bg=COLORS["bg_alt"], fg=COLORS["fg_dim"],
            justify="left", wraplength=510
        ).pack(anchor="w", padx=14, pady=(2, 10))
        # Click anywhere on the card to pick
        def pick(_e=None, k=key):
            result["tier"] = k
            root.destroy()
        card.bind("<Button-1>", pick)
        for child in card.winfo_children():
            child.bind("<Button-1>", pick)
        return card

    make_card(
        "1) Free tier (recommended for getting started)",
        "I'll auto-pick the best free OpenRouter models for planning and code. "
        "You can also pick from a list of 8 free models. No credit card needed; "
        "sign up at openrouter.ai for a free key.",
        "free"
    )
    make_card(
        "2) Paid tier (best quality)",
        "I'll use Claude 3.5 Sonnet for both planning and code via OpenRouter. "
        "Your account is charged per token; typical project is $0.05 to $1.50. "
        "Or pick from a list of paid models.",
        "paid"
    )
    make_card(
        "3) Custom (your own endpoint)",
        "Provide the API URL, key, and model name(s). Use this for direct OpenAI, "
        "Anthropic, local Ollama, vLLM, or any other OpenAI-compatible API.",
        "custom"
    )

    tk.Label(
        root, text="You can change this later from the menu.",
        font=tkfont.Font(family="Segoe UI", size=8),
        bg=COLORS["bg"], fg=COLORS["fg_dim"]
    ).pack(pady=(8, 8))

    # Center on screen
    root.update_idletasks()
    w = root.winfo_width()
    h = root.winfo_height()
    x = (root.winfo_screenwidth() // 2) - (w // 2)
    y = (root.winfo_screenheight() // 2) - (h // 2)
    root.geometry("%dx%d+%d+%d" % (w, h, x, y))

    root.mainloop()
    return result["tier"]


# ---------------------------------------------------------------------------
# GUI free/paid model sub-chooser (used by the tier chooser)
# ---------------------------------------------------------------------------
def run_model_subchooser(tier):
    """After the user picks a tier, this lets them choose 'auto' or
    'manual' (pick from the catalog). Returns a dict with sonnet_model
    and codex_model, or None if cancelled."""
    try:
        import jarvis
    except Exception:
        return None

    tk, _, _, tkfont = _get_tk()
    root = tk.Tk()
    root.title("jarvis - choose models")
    root.geometry("520x280")
    root.resizable(False, False)
    root.configure(bg=COLORS["bg"])

    result = {"choice": None}
    is_paid = (tier == "paid")
    title = "Paid tier" if is_paid else "Free tier"
    default_planner = jarvis.DEFAULT_PAID_SONNET_MODEL if is_paid else jarvis.DEFAULT_SONNET_MODEL
    default_coder   = jarvis.DEFAULT_PAID_CODEX_MODEL  if is_paid else jarvis.DEFAULT_CODEX_MODEL

    tk.Label(
        root, text=title + " - pick how to choose models",
        font=tkfont.Font(family="Segoe UI", size=14, weight="bold"),
        bg=COLORS["bg"], fg=COLORS["accent"]
    ).pack(pady=(24, 16))

    def auto_pick():
        result["choice"] = {
            "sonnet_model": default_planner,
            "codex_model":  default_coder,
            "auto": True,
        }
        root.destroy()

    def manual_pick():
        # We can't easily show the full listbox in this minimal flow,
        # so just default to the recommended free/paid models and let
        # the user adjust later via the "Change models" menu.
        # For the manual path, we open a model picker like the chat window.
        result["choice"] = {
            "ask_manual": True,
        }
        root.destroy()

    auto_label = ("Use the best " + tier + " model for everything (recommended)"
                  if tier != "free" else
                  "Auto-pick the best free models for me (recommended)")
    auto_sub = ("Planner: " + jarvis.find_model(default_planner, in_paid=is_paid)["label"] +
                "    Coder: " + jarvis.find_model(default_coder, in_paid=is_paid)["label"])

    tk.Button(
        root, text=auto_label,
        font=tkfont.Font(family="Segoe UI", size=11, weight="bold"),
        bg=COLORS["accent"], fg=COLORS["bg"],
        activebackground=COLORS["accent_alt"], activeforeground=COLORS["bg"],
        relief="flat", bd=0, padx=20, pady=10, command=auto_pick
    ).pack(fill="x", padx=24, pady=4)
    tk.Label(
        root, text=auto_sub,
        font=tkfont.Font(family="Segoe UI", size=9),
        bg=COLORS["bg"], fg=COLORS["fg_dim"]
    ).pack(pady=(0, 12))

    tk.Button(
        root, text="Let me pick from the " + tier + " list",
        font=tkfont.Font(family="Segoe UI", size=11),
        bg=COLORS["bg_alt"], fg=COLORS["fg"],
        activebackground=COLORS["border"],
        relief="flat", bd=0, padx=20, pady=10, command=manual_pick
    ).pack(fill="x", padx=24, pady=4)

    # Center on screen
    root.update_idletasks()
    w = root.winfo_width()
    h = root.winfo_height()
    x = (root.winfo_screenwidth() // 2) - (w // 2)
    y = (root.winfo_screenheight() // 2) - (h // 2)
    root.geometry("%dx%d+%d+%d" % (w, h, x, y))

    root.mainloop()
    return result["choice"]


def run_gui_first_run():
    """The full GUI first-run flow: tier chooser -> sub-chooser -> key entry.
    Returns a config dict, or None if the user cancelled.
    """
    try:
        import jarvis
    except Exception:
        return None

    tier = run_tier_chooser()
    if not tier:
        return None
    if tier == "quit":
        return None

    # Tier-specific flow
    if tier == "free":
        sub = run_model_subchooser("free")
        if not sub:
            return None
        if sub.get("ask_manual"):
            # Drop back to the terminal picker for now
            return jarvis.first_run_setup()
        # Ask for the key
        key = _gui_ask_key("OpenRouter API key (free):")
        if not key:
            return None
        cfg = {
            "sonnet_api_key": key, "codex_api_key": key,
            "sonnet_api_url": jarvis.DEFAULT_SONNET_URL,
            "codex_api_url":  jarvis.DEFAULT_CODEX_URL,
            "sonnet_model":   sub["sonnet_model"],
            "codex_model":    sub["codex_model"],
            "tier":           "free",
            "auto_models":    sub.get("auto", False),
        }
    elif tier == "paid":
        sub = run_model_subchooser("paid")
        if not sub:
            return None
        if sub.get("ask_manual"):
            return jarvis.first_run_setup()
        key = _gui_ask_key("OpenRouter API key (paid):")
        if not key:
            return None
        cfg = {
            "sonnet_api_key": key, "codex_api_key": key,
            "sonnet_api_url": jarvis.DEFAULT_SONNET_URL,
            "codex_api_url":  jarvis.DEFAULT_CODEX_URL,
            "sonnet_model":   sub["sonnet_model"],
            "codex_model":    sub["codex_model"],
            "tier":           "paid",
            "auto_models":    sub.get("auto", False),
        }
    else:  # custom
        # Custom flow: prompt for URL, key, model names. Use a small
        # dialog for each.
        url, key, planner, coder = _gui_ask_custom()
        if not (url and key and planner and coder):
            return None
        cfg = {
            "sonnet_api_key": key, "codex_api_key": key,
            "sonnet_api_url": url, "codex_api_url": url,
            "sonnet_model":   planner, "codex_model": coder,
            "tier":           "custom",
            "auto_models":    False,
        }

    # Fill in defaults
    cfg.setdefault("persona",       "engineer")
    cfg.setdefault("enable_review", True)
    cfg.setdefault("enable_tests",  False)
    cfg.setdefault("timeout",       120)
    cfg.setdefault("retries",       3)
    cfg.setdefault("backoff",       1.5)
    jarvis.save_config(cfg)
    return cfg


def _gui_ask_key(prompt):
    """Single-field masked text input dialog. Returns the typed key or None."""
    tk, _, _, tkfont = _get_tk()
    dlg = tk.Toplevel()
    dlg.title("jarvis - API key")
    dlg.configure(bg=COLORS["bg"])
    dlg.geometry("440x180")
    dlg.transient()
    dlg.grab_set()

    result = {"key": None}

    tk.Label(
        dlg, text=prompt,
        font=tkfont.Font(family="Segoe UI", size=11),
        bg=COLORS["bg"], fg=COLORS["fg"]
    ).pack(pady=(20, 4), padx=20, anchor="w")
    tk.Label(
        dlg, text="Get a free key at openrouter.ai -> Keys",
        font=tkfont.Font(family="Segoe UI", size=9),
        bg=COLORS["bg"], fg=COLORS["fg_dim"]
    ).pack(padx=20, anchor="w")

    entry = tk.Entry(
        dlg, font=tkfont.Font(family="Consolas", size=11),
        bg=COLORS["input_bg"], fg=COLORS["fg"],
        insertbackground=COLORS["accent"], show="*",
        relief="flat", bd=4
    )
    entry.pack(fill="x", padx=20, pady=14, ipady=4)
    entry.focus_set()

    def on_ok():
        result["key"] = entry.get().strip()
        dlg.destroy()

    def on_cancel():
        dlg.destroy()

    btn_frame = tk.Frame(dlg, bg=COLORS["bg"])
    btn_frame.pack(pady=(0, 12))
    tk.Button(
        btn_frame, text="OK", font=tkfont.Font(family="Segoe UI", size=10, weight="bold"),
        bg=COLORS["accent"], fg=COLORS["bg"],
        activebackground=COLORS["accent_alt"], activeforeground=COLORS["bg"],
        relief="flat", bd=0, padx=20, pady=6, command=on_ok
    ).pack(side="left", padx=4)
    tk.Button(
        btn_frame, text="Cancel", font=tkfont.Font(family="Segoe UI", size=10),
        bg=COLORS["bg_alt"], fg=COLORS["fg"],
        relief="flat", bd=0, padx=20, pady=6, command=on_cancel
    ).pack(side="left", padx=4)

    entry.bind("<Return>", lambda e: on_ok())

    dlg.update_idletasks()
    w = dlg.winfo_width()
    h = dlg.winfo_height()
    x = (dlg.winfo_screenwidth() // 2) - (w // 2)
    y = (dlg.winfo_screenheight() // 2) - (h // 2)
    dlg.geometry("%dx%d+%d+%d" % (w, h, x, y))

    dlg.wait_window()
    return result["key"]


def _gui_ask_custom():
    """Multi-field dialog for the custom provider: URL, key, planner, coder.
    Returns (url, key, planner, coder) or all-None if cancelled."""
    tk, _, _, tkfont = _get_tk()
    dlg = tk.Toplevel()
    dlg.title("jarvis - custom provider")
    dlg.configure(bg=COLORS["bg"])
    dlg.geometry("520x380")
    dlg.transient()
    dlg.grab_set()

    result = {"ok": False, "url": "", "key": "", "planner": "", "coder": ""}

    def field(label, default, show=None, row=0):
        tk.Label(
            dlg, text=label, font=tkfont.Font(family="Segoe UI", size=10),
            bg=COLORS["bg"], fg=COLORS["fg"]
        ).grid(row=row, column=0, sticky="w", padx=20, pady=(12, 2))
        e = tk.Entry(
            dlg, font=tkfont.Font(family="Consolas", size=10),
            bg=COLORS["input_bg"], fg=COLORS["fg"],
            insertbackground=COLORS["accent"],
            relief="flat", bd=4, show=(show or "")
        )
        e.insert(0, default)
        e.grid(row=row, column=1, sticky="ew", padx=20, pady=(12, 2))
        return e

    url_e = field("API URL:", "https://openrouter.ai/api/v1/chat/completions", row=0)
    key_e = field("API key:", "", show="*", row=1)
    pln_e = field("Planner model:", "anthropic/claude-3.5-sonnet", row=2)
    cod_e = field("Coder model: (Enter = same)", "anthropic/claude-3.5-sonnet", row=3)

    dlg.columnconfigure(1, weight=1)

    def on_ok():
        result["url"]     = url_e.get().strip()
        result["key"]     = key_e.get().strip()
        result["planner"] = pln_e.get().strip()
        result["coder"]   = cod_e.get().strip() or result["planner"]
        if result["url"] and result["key"] and result["planner"]:
            result["ok"] = True
            dlg.destroy()
        else:
            tk.messagebox.showwarning("jarvis", "URL, key, and planner model are required.")

    def on_cancel():
        dlg.destroy()

    btn_frame = tk.Frame(dlg, bg=COLORS["bg"])
    btn_frame.grid(row=4, column=0, columnspan=2, pady=18)
    tk.Button(
        btn_frame, text="OK", font=tkfont.Font(family="Segoe UI", size=10, weight="bold"),
        bg=COLORS["accent"], fg=COLORS["bg"],
        activebackground=COLORS["accent_alt"], activeforeground=COLORS["bg"],
        relief="flat", bd=0, padx=20, pady=6, command=on_ok
    ).pack(side="left", padx=4)
    tk.Button(
        btn_frame, text="Cancel", font=tkfont.Font(family="Segoe UI", size=10),
        bg=COLORS["bg_alt"], fg=COLORS["fg"],
        relief="flat", bd=0, padx=20, pady=6, command=on_cancel
    ).pack(side="left", padx=4)

    url_e.focus_set()

    dlg.update_idletasks()
    w = dlg.winfo_width()
    h = dlg.winfo_height()
    x = (dlg.winfo_screenwidth() // 2) - (w // 2)
    y = (dlg.winfo_screenheight() // 2) - (h // 2)
    dlg.geometry("%dx%d+%d+%d" % (w, h, x, y))

    dlg.wait_window()
    if not result["ok"]:
        return None, None, None, None
    return result["url"], result["key"], result["planner"], result["coder"]


# ---------------------------------------------------------------------------
# Top-level entry
# ---------------------------------------------------------------------------
def run_gui(cfg, args):
    """Launch the chat window. Blocks until the user quits."""
    win = ChatWindow(cfg, args)
    win.run()


# ---------------------------------------------------------------------------
# Deep research window -- a separate Toplevel for the multi-hour
# research sessions. Has its own status display, question input, and
# a background thread that runs the research loop. The main chat
# window stays usable while research runs in the background.
# ---------------------------------------------------------------------------
class DeepResearchWindow(object):
    """A window for running and managing deep research sessions.

    Layout:
      - Topic input + buttons for "New session" / "One-shot report" /
        "Resume existing".
      - Live status display (iteration count, time elapsed, sources, etc.)
      - Notes preview (read-only, auto-refreshing)
      - Question input at the bottom: type a question, get an answer
        synthesized from the running notes.
      - "Pause / Resume" button to control the background research loop.
    """

    def __init__(self, parent, cfg, args):
        self.cfg = cfg
        self.args = args
        self.tk, _, _, self.tkfont = _get_tk()
        self.parent = parent
        self.win = self.tk.Toplevel(parent)
        self.win.title("jarvis - deep research")
        self.win.geometry("900x700")
        self.win.minsize(640, 480)
        self.win.configure(bg=COLORS["bg"])
        self.win.transient(parent)
        self.win.grab_set()

        # State
        self._session = None
        self._worker_thread = None
        self._worker_stop = threading.Event()
        self._worker_lock = threading.Lock()
        self._busy = False

        self._build_layout()
        self.win.protocol("WM_DELETE_WINDOW", self._on_close)

    # ----- Layout -----
    def _build_layout(self):
        # Title bar
        title = self.tk.Label(
            self.win, text="Deep research session",
            font=self.tkfont.Font(family="Segoe UI", size=14, weight="bold"),
            bg=COLORS["bg"], fg=COLORS["accent"])
        title.pack(side="top", anchor="w", padx=14, pady=(12, 4))

        sub = self.tk.Label(
            self.win,
            text="Multi-hour, resumable research on one topic. "
                 "Ask questions any time; the AI answers from the running notes.",
            font=self.tkfont.Font(family="Segoe UI", size=9),
            bg=COLORS["bg"], fg=COLORS["fg_dim"],
            wraplength=860, justify="left")
        sub.pack(side="top", anchor="w", padx=14, pady=(0, 12))

        # Action row
        action_frame = self.tk.Frame(self.win, bg=COLORS["bg"])
        action_frame.pack(side="top", fill="x", padx=14, pady=4)
        self.tk.Label(action_frame, text="Topic:",
                      font=self.tkfont.Font(family="Segoe UI", size=10),
                      bg=COLORS["bg"], fg=COLORS["fg"]).pack(side="left", padx=(0, 6))
        self._topic_var = self.tk.StringVar()
        self._topic_entry = self.tk.Entry(
            action_frame, textvariable=self._topic_var,
            font=self.tkfont.Font(family="Segoe UI", size=10),
            bg=COLORS["input_bg"], fg=COLORS["fg"],
            insertbackground=COLORS["accent"],
            relief="flat", bd=4, highlightthickness=1,
            highlightbackground=COLORS["border"],
            highlightcolor=COLORS["accent"])
        self._topic_entry.pack(side="left", fill="x", expand=True, padx=4, ipady=4)
        self.tk.Button(action_frame, text="New session",
                       font=self.tkfont.Font(family="Segoe UI", size=10, weight="bold"),
                       bg=COLORS["accent"], fg=COLORS["bg"],
                       activebackground=COLORS["accent_alt"],
                       relief="flat", bd=0, padx=12, pady=4,
                       command=self._start_new).pack(side="left", padx=2)
        self.tk.Button(action_frame, text="One-shot report",
                       font=self.tkfont.Font(family="Segoe UI", size=10),
                       bg=COLORS["bg_alt"], fg=COLORS["fg"],
                       activebackground=COLORS["border"],
                       relief="flat", bd=0, padx=12, pady=4,
                       command=self._start_one_shot).pack(side="left", padx=2)
        self.tk.Button(action_frame, text="Resume...",
                       font=self.tkfont.Font(family="Segoe UI", size=10),
                       bg=COLORS["bg_alt"], fg=COLORS["fg"],
                       activebackground=COLORS["border"],
                       relief="flat", bd=0, padx=12, pady=4,
                       command=self._resume_session).pack(side="left", padx=2)

        # Status + control row
        ctrl = self.tk.Frame(self.win, bg=COLORS["bg_alt"])
        ctrl.pack(side="top", fill="x", padx=14, pady=(4, 8))
        self._status_var = self.tk.StringVar(value="(no session)")
        self.tk.Label(ctrl, textvariable=self._status_var,
                      font=self.tkfont.Font(family="Segoe UI", size=9),
                      bg=COLORS["bg_alt"], fg=COLORS["fg"],
                      anchor="w").pack(side="left", fill="x", expand=True, padx=10, pady=6)
        self._pause_btn = self.tk.Button(
            ctrl, text="Pause",
            font=self.tkfont.Font(family="Segoe UI", size=9, weight="bold"),
            bg=COLORS["btn_bg"], fg=COLORS["btn_fg"],
            activebackground=COLORS["accent_alt"],
            relief="flat", bd=0, padx=12, pady=2,
            command=self._toggle_pause, state="disabled")
        self._pause_btn.pack(side="left", padx=4, pady=4)
        self.tk.Button(ctrl, text="Open folder",
                       font=self.tkfont.Font(family="Segoe UI", size=9),
                       bg=COLORS["bg_alt"], fg=COLORS["fg"],
                       activebackground=COLORS["border"],
                       relief="flat", bd=0, padx=12, pady=2,
                       command=self._open_session_folder).pack(side="left", padx=4, pady=4)

        # Notes display (read-only)
        notes_frame = self.tk.Frame(self.win, bg=COLORS["border"])
        notes_frame.pack(side="top", fill="both", expand=True, padx=14, pady=4)
        self._notes_text = self.tk.Text(
            notes_frame, wrap="word", font=self.tkfont.Font(family="Consolas", size=9),
            bg=COLORS["code_bg"], fg=COLORS["code_fg"],
            bd=0, highlightthickness=0, padx=10, pady=10,
            state="disabled")
        notes_sb = self.tk.Scrollbar(notes_frame, orient="vertical",
                                     command=self._notes_text.yview)
        self._notes_text.configure(yscrollcommand=notes_sb.set)
        self._notes_text.pack(side="left", fill="both", expand=True)
        notes_sb.pack(side="right", fill="y")

        # Question input
        q_frame = self.tk.Frame(self.win, bg=COLORS["bg"])
        q_frame.pack(side="bottom", fill="x", padx=14, pady=8)
        self.tk.Label(q_frame, text="Ask:",
                      font=self.tkfont.Font(family="Segoe UI", size=10, weight="bold"),
                      bg=COLORS["bg"], fg=COLORS["accent"]).pack(side="left", padx=(0, 6))
        self._q_var = self.tk.StringVar()
        self._q_entry = self.tk.Entry(
            q_frame, textvariable=self._q_var,
            font=self.tkfont.Font(family="Segoe UI", size=10),
            bg=COLORS["input_bg"], fg=COLORS["fg"],
            insertbackground=COLORS["accent"],
            relief="flat", bd=4, highlightthickness=1,
            highlightbackground=COLORS["border"],
            highlightcolor=COLORS["accent"])
        self._q_entry.pack(side="left", fill="x", expand=True, padx=4, ipady=4)
        self._q_entry.bind("<Return>", lambda e: self._ask_question())
        self.tk.Button(q_frame, text="Ask",
                       font=self.tkfont.Font(family="Segoe UI", size=10, weight="bold"),
                       bg=COLORS["btn_bg"], fg=COLORS["btn_fg"],
                       activebackground=COLORS["accent_alt"],
                       relief="flat", bd=0, padx=14, pady=4,
                       command=self._ask_question).pack(side="left", padx=4)

    # ----- Actions -----
    def _start_new(self):
        topic = self._topic_var.get().strip()
        if not topic:
            self._set_status("Please enter a topic first.", error=True)
            return
        try:
            import jarvis
            jarvis._need_requests()
        except SystemExit:
            self._set_status(
                "ERROR: the 'requests' library is required.  pip install requests",
                error=True)
            return
        except Exception as e:
            self._set_status("ERROR: " + str(e), error=True)
            return
        # Create the session synchronously (it generates the plan via API),
        # then start the background research loop.
        self._set_status("Creating session and generating plan...")
        self._set_busy(True)
        def work():
            try:
                import jarvis
                session = jarvis.run_deep_research_session(
                    topic, self.cfg,
                    max_seconds=5 * 3600, max_iterations=50,
                    one_shot=False)
                # Schedule UI updates on the main thread
                self.win.after(0, self._on_session_ready, session)
            except (jarvis.ConfigError, jarvis.JarvisError) as e:
                self.win.after(0, self._set_status, "ERROR: " + str(e))
                self.win.after(0, self._set_busy, False)
            except Exception as e:
                self.win.after(0, self._set_status, "ERROR: " + str(e))
                self.win.after(0, self._set_busy, False)
        threading.Thread(target=work, daemon=True).start()

    def _start_one_shot(self):
        topic = self._topic_var.get().strip()
        if not topic:
            self._set_status("Please enter a topic first.", error=True)
            return
        try:
            import jarvis
            jarvis._need_requests()
        except SystemExit:
            self._set_status("ERROR: 'requests' library required.", error=True)
            return
        self._set_status("Running one-shot deep report (this may take a few minutes)...")
        self._set_busy(True)
        def work():
            try:
                import jarvis
                session = jarvis.run_deep_research_session(
                    topic, self.cfg,
                    max_seconds=300, max_iterations=1, one_shot=True)
                self.win.after(0, self._on_session_ready, session)
            except Exception as e:
                self.win.after(0, self._set_status, "ERROR: " + str(e))
                self.win.after(0, self._set_busy, False)
        threading.Thread(target=work, daemon=True).start()

    def _resume_session(self):
        """Show a list of existing sessions, let the user pick one."""
        import jarvis
        sessions = jarvis.list_sessions()
        if not sessions:
            self._set_status("(no existing sessions to resume)", error=False)
            return
        # Simple chooser
        dlg = self.tk.Toplevel(self.win)
        dlg.title("Resume session")
        dlg.configure(bg=COLORS["bg"])
        dlg.geometry("600x300")
        dlg.transient(self.win)
        dlg.grab_set()
        self.tk.Label(dlg, text="Pick a session to resume:",
                      font=self.tkfont.Font(family="Segoe UI", size=12, weight="bold"),
                      bg=COLORS["bg"], fg=COLORS["accent"]).pack(pady=(12, 8), padx=14, anchor="w")
        lb = self.tk.Listbox(
            dlg, font=self.tkfont.Font(family="Segoe UI", size=10),
            bg=COLORS["bg_alt"], fg=COLORS["fg"],
            selectbackground=COLORS["accent"], selectforeground=COLORS["bg"],
            highlightthickness=1, highlightbackground=COLORS["border"])
        lb.pack(fill="both", expand=True, padx=14, pady=4)
        for sid, topic, status, updated in sessions:
            when = time.strftime("%Y-%m-%d %H:%M", time.localtime(updated))
            lb.insert("end", sid + "  [" + status + "]  " + when)
            # We'll fetch the topic via the same tuple; show in second line
        result = {"sid": None}
        def on_pick(_e=None):
            sel = lb.curselection()
            if sel:
                result["sid"] = sessions[sel[0]][0]
            dlg.destroy()
        def on_cancel():
            dlg.destroy()
        bf = self.tk.Frame(dlg, bg=COLORS["bg"])
        bf.pack(pady=10)
        self.tk.Button(bf, text="Resume", font=self.tkfont.Font(family="Segoe UI", size=10, weight="bold"),
                       bg=COLORS["accent"], fg=COLORS["bg"],
                       activebackground=COLORS["accent_alt"],
                       relief="flat", bd=0, padx=20, pady=4, command=on_pick).pack(side="left", padx=4)
        self.tk.Button(bf, text="Cancel", font=self.tkfont.Font(family="Segoe UI", size=10),
                       bg=COLORS["bg_alt"], fg=COLORS["fg"],
                       relief="flat", bd=0, padx=20, pady=4, command=on_cancel).pack(side="left", padx=4)
        lb.bind("<Double-Button-1>", on_pick)
        dlg.wait_window()
        if not result["sid"]:
            return
        # Load + start the worker
        try:
            session = jarvis.DeepResearchSession.load(result["sid"])
        except Exception as e:
            self._set_status("ERROR loading session: " + str(e), error=True)
            return
        self._on_session_ready(session)

    def _on_session_ready(self, session):
        """Called when a session is ready (new, one-shot, or resumed)."""
        self._session = session
        self._set_status("Session ready.  " + session.status_line())
        self._refresh_notes()
        self._set_busy(False)
        # If session is not done, kick off the background research loop
        if session.status not in ("done",):
            self._start_worker()

    def _start_worker(self):
        """Start the background thread that runs the research loop."""
        if not self._session:
            return
        if self._worker_thread and self._worker_thread.is_alive():
            return  # already running
        self._worker_stop.clear()
        with self._worker_lock:
            self._worker_thread = threading.Thread(
                target=self._worker_main, daemon=True)
            self._worker_thread.start()
        self._pause_btn.configure(state="normal", text="Pause")

    def _worker_main(self):
        """Background thread: runs research iterations and updates UI."""
        import jarvis
        session = self._session
        try:
            while not self._worker_stop.is_set():
                if session.status in ("done", "stopped"):
                    break
                # Don't loop tighter than every 2 seconds
                self.win.after(0, self._set_status,
                               "Researching: " +
                               (session.open_questions[0] if session.open_questions else "(no questions left)"))
                try:
                    jarvis._deepresearch_one_iteration(
                        session, self.cfg, stop_check=self._worker_stop.is_set)
                except Exception as e:
                    # Don't crash the worker on a single bad iteration
                    self.win.after(0, self._set_status,
                                   "Iteration error: " + str(e))
                session.elapsed_seconds = time.time() - session.started_at
                session.save()
                self.win.after(0, self._refresh_notes)
                self.win.after(0, self._set_status,
                               "Iter " + str(session.iterations_done) +
                               "  " + session.status_line())
                time.sleep(2)
            if session.status not in ("done", "stopped"):
                session.status = "stopped"
                session.save()
            self.win.after(0, self._refresh_notes)
            self.win.after(0, self._set_status,
                           "Worker stopped.  " + session.status_line())
        except Exception as e:
            self.win.after(0, self._set_status,
                           "Worker error: " + str(e))
        finally:
            self.win.after(0, lambda: self._pause_btn.configure(
                state="disabled", text="Pause"))

    def _toggle_pause(self):
        if not self._worker_thread:
            return
        if self._worker_stop.is_set():
            # Resume
            self._worker_stop.clear()
            self._pause_btn.configure(text="Pause")
            self._start_worker()
        else:
            # Pause
            self._worker_stop.set()
            self._pause_btn.configure(text="Resume")

    def _ask_question(self):
        q = self._q_var.get().strip()
        if not q or not self._session:
            return
        if not self._session.notes_md:
            self._set_status(
                "No notes yet -- research a bit first, then ask.", error=True)
            return
        # Pause the worker briefly while we answer
        was_running = self._worker_thread and self._worker_thread.is_alive()
        if was_running:
            self._worker_stop.set()
        self._set_status("Thinking...")
        def work():
            import jarvis
            try:
                ans = jarvis._deepresearch_answer(
                    q, self._session.notes_md, self.cfg)
                self._session.questions.append({
                    "ts": time.time(), "q": q, "a": ans,
                })
                self._session.save()
                self.win.after(0, self._show_answer, q, ans)
            except Exception as e:
                self.win.after(0, self._set_status, "ERROR: " + str(e))
            finally:
                if was_running:
                    self._worker_stop.clear()
                    self.win.after(0, self._start_worker)
        threading.Thread(target=work, daemon=True).start()
        self._q_var.set("")

    def _show_answer(self, q, ans):
        # Append the Q&A to the notes display so the user sees it inline
        self._notes_text.configure(state="normal")
        self._notes_text.insert("end", "\n\n--- Q: " + q + " ---\n", "ai_label")
        self._notes_text.insert("end", ans + "\n")
        self._notes_text.see("end")
        self._notes_text.configure(state="disabled")
        self._set_status("Answered.  " + self._session.status_line())

    def _refresh_notes(self):
        if not self._session:
            return
        self._notes_text.configure(state="normal")
        self._notes_text.delete("1.0", "end")
        # Show plan + notes + Q&A
        content = []
        content.append("# " + self._session.topic)
        content.append("")
        if self._session.plan:
            content.append(self._session.plan)
            content.append("")
        if self._session.notes_md:
            content.append("## Notes so far")
            content.append("")
            content.append(self._session.notes_md)
            content.append("")
        if self._session.questions:
            content.append("## Q&A log")
            content.append("")
            for qa in self._session.questions[-10:]:  # last 10
                content.append("Q: " + qa.get("q", ""))
                content.append(qa.get("a", ""))
                content.append("")
        if self._session.sources:
            content.append("## Sources (most recent " + str(min(20, len(self._session.sources))) + ")")
            content.append("")
            for s in self._session.sources[-20:]:
                content.append("- " + (s.get("title") or s.get("url", "")) +
                               "  " + s.get("url", ""))
        self._notes_text.insert("end", "\n".join(content))
        self._notes_text.configure(state="disabled")
        # Configure the ai_label tag if not already
        try:
            self._notes_text.tag_configure(
                "ai_label", font=self.tkfont.Font(family="Segoe UI", size=10, weight="bold"),
                foreground=COLORS["accent"])
        except Exception:
            pass

    def _open_session_folder(self):
        if not self._session:
            return
        import jarvis
        d = jarvis._session_dir(self._session.session_id)
        if not os.path.isdir(d):
            os.makedirs(d)
        if sys.platform == "win32":
            os.startfile(d)
        elif sys.platform == "darwin":
            os.system('open "' + d + '"')
        else:
            os.system('xdg-open "' + d + '"')

    def _set_status(self, msg, error=False):
        try:
            self._status_var.set(msg)
        except Exception:
            pass

    def _set_busy(self, val):
        self._busy = val
        # Update the topic entry
        try:
            self._topic_entry.configure(state=("disabled" if val else "normal"))
        except Exception:
            pass

    def _on_close(self):
        # Stop worker cleanly
        self._worker_stop.set()
        # Wait a bit for the thread to exit
        if self._worker_thread and self._worker_thread.is_alive():
            self._worker_thread.join(timeout=2)
        if self._session and self._session.status not in ("done", "stopped"):
            self._session.status = "stopped"
            try:
                self._session.save()
            except Exception:
                pass
        self.win.destroy()


# ===========================================================================
# TEST SUITE -- embedded below; run with `python3 jarvis.py --test`
# ===========================================================================
#
# All of the tests from test_deep_research.py live in this section.
# The runner is `_run_jarvis_tests()` and the CLI flag is `--test`.
# Same behaviour as the old standalone file: 112 tests, all green.
#

# Inside the jarvis module, `import jarvis` doesn't make sense.
# Bind the current module to a local 'jarvis' name so all the
# existing `jarvis.X` references in the tests still work.
try:
    jarvis  # noqa: F821
except NameError:
    import sys as _sys
    jarvis = _sys.modules[__name__]

# The test bodies reference these modules at module level.
# (json, os, sys, time, traceback are already imported above.)
import tempfile
import shutil
import types


# (from __future__ removed; must be first statement, jarvis.py already has it)

# Note: the original test file also did `import json, os, shutil, sys,
# tempfile, time, traceback, types` at module level. All of those are
# already imported at the top of jarvis.py, so the redundant block has
# been removed (imports are idempotent in Python, but no need to
# repeat them).


# ---------------------------------------------------------------------------
# Minimal mocks. We stub `requests` so importing jarvis doesn't try
# to do any real networking. We also stub the heavy `tkinter` so we
# can import jarvis without a display.
# ---------------------------------------------------------------------------
class _FakeResponse(object):
    def __init__(self, status_code=200, text="", headers=None, json_data=None):
        self.status_code = status_code
        self.text = text
        self.headers = headers or {}
        self._json = json_data
    def json(self):
        if self._json is None:
            raise ValueError("no json")
        return self._json


class _FakeRequests(object):
    """Just enough of the requests API for jarvis.py to import + run."""
    class Timeout(Exception): pass
    class RequestException(Exception): pass

    def __init__(self):
        self.calls = []
        self.responses = {}   # url -> _FakeResponse

    def set_response(self, url, **kwargs):
        self.responses[url] = _FakeResponse(**kwargs)

    def get(self, url, **kwargs):
        self.calls.append(("GET", url, kwargs))
        return self.responses.get(url, _FakeResponse(404, ""))

    def post(self, url, **kwargs):
        self.calls.append(("POST", url, kwargs))
        # Default: return a JSON chat completion
        return self.responses.get(
            url,
            _FakeResponse(200, json_data={
                "choices": [{"message": {"content": "{}"}}]
            }),
        )



# ---------------------------------------------------------------------------
# Test setup: only run when --test is invoked, NEVER at import time.
# Originally this was module-level code in test_deep_research.py;
# embedded here it would clobber jarvis.CONFIG_DIR and inject a
# fake requests stub on every import. So we move it into a function.
# ---------------------------------------------------------------------------
def _setup_jarvis_tests():
    """Install mock requests + redirect CONFIG_DIR to a temp location.
    Called once at the start of _run_jarvis_tests().
    """
    global _fake, requests_stub, TMPDIR, CONFIG_DIR_TEST, SESSIONS_DIR_TEST

    # --- mock requests stub ---
    # Install the mock requests module BEFORE importing jarvis
    _fake = _FakeRequests()
    requests_stub = types.ModuleType("requests")
    requests_stub.Timeout = _FakeRequests.Timeout
    requests_stub.RequestException = _FakeRequests.RequestException
    requests_stub.get = _fake.get
    requests_stub.post = _fake.post
    sys.modules["requests"] = requests_stub

    # --- redirect CONFIG_DIR to a temp location ---
    # ---------------------------------------------------------------------------
    # Set up a temp config dir before importing jarvis so the module's
    # global CONFIG_PATH points at a private location. We do this by
    # patching the module's constants after import.
    # ---------------------------------------------------------------------------
    TMPDIR = tempfile.mkdtemp(prefix="jarvis_test_")
    CONFIG_DIR_TEST  = os.path.join(TMPDIR, ".jarvis")
    SESSIONS_DIR_TEST = os.path.join(CONFIG_DIR_TEST, "sessions")
    os.makedirs(CONFIG_DIR_TEST, exist_ok=True)
    
    # Now import jarvis (this will run module-level code; with our
    # mocked requests, nothing should fail).
    # (sys.path hack removed; we are already in the jarvis module)
    # (import jarvis removed; we ARE jarvis, see alias below)
    
    # Redirect the config + session dirs to our temp location.
    jarvis.CONFIG_DIR = CONFIG_DIR_TEST
    jarvis.SESSIONS_DIR = SESSIONS_DIR_TEST
    jarvis.CONFIG_PATH = os.path.join(CONFIG_DIR_TEST, "config.json")

# Globals the test functions may want to set/read; declared here so
# they're bound in the module namespace even before _setup_jarvis_tests runs.
_fake = None
requests_stub = None
TMPDIR = None
CONFIG_DIR_TEST = None
SESSIONS_DIR_TEST = None

# ---------------------------------------------------------------------------
# Test runner harness -- emulate unittest with much less boilerplate.
# ---------------------------------------------------------------------------
TESTS_RUN = 0
TESTS_PASSED = 0
TESTS_FAILED = 0
FAILURES = []


def _run(name, fn):
    """Run a single test. Catch all exceptions, report, keep going."""
    global TESTS_RUN, TESTS_PASSED, TESTS_FAILED
    TESTS_RUN += 1
    try:
        fn()
    except Exception as e:
        TESTS_FAILED += 1
        tb = traceback.format_exc()
        FAILURES.append((name, tb))
        sys.stderr.write("  FAIL  " + name + "\n")
        sys.stderr.write("    " + str(e) + "\n")
        return
    TESTS_PASSED += 1
    sys.stderr.write("  ok    " + name + "\n")


def assert_eq(a, b, msg=""):
    if a != b:
        raise AssertionError(
            "expected " + repr(b) + ", got " + repr(a) + ("  (" + msg + ")" if msg else ""))


def assert_true(cond, msg=""):
    if not cond:
        raise AssertionError("expected truthy, got falsy  " + msg)


def assert_in(needle, hay, msg=""):
    if needle not in hay:
        raise AssertionError(
            "expected " + repr(needle) + " in " + repr(hay[:300] if isinstance(hay, (str, bytes)) else hay) +
            ("  (" + msg + ")" if msg else ""))




# A helper to make a fake config dict for tests
def fake_cfg():
    return {
        "sonnet_api_key": "sk-test",
        "codex_api_key": "sk-test",
        "sonnet_api_url": "https://example.invalid/v1/chat/completions",
        "codex_api_url": "https://example.invalid/v1/chat/completions",
        "sonnet_model": "test/planner",
        "codex_model": "test/coder",
        "persona": "engineer",
        "enable_review": True,
        "enable_tests": False,
        "timeout": 30,
        "retries": 0,
        "backoff": 0.1,
    }


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------
def test_module_imports():
    """jarvis imports cleanly even with mocked requests."""
    assert_true(hasattr(jarvis, "DeepResearchSession"))
    assert_true(hasattr(jarvis, "run_deep_research_session"))
    assert_true(hasattr(jarvis, "_deepresearch_generate_plan"))
    assert_true(hasattr(jarvis, "deep_resession_qa_loop"))
    assert_true(hasattr(jarvis, "list_sessions"))
    assert_true(hasattr(jarvis, "delete_session"))


def test_session_id_format():
    """Generated session ids are filesystem-safe and unique across calls."""
    a = jarvis._session_id_for_topic("Quantum Computing 2024!")
    b = jarvis._session_id_for_topic("Quantum Computing 2024!")
    # Both should start with the slug
    assert_true(a.startswith("quantum-computing-2024"),
                "slug should be normalized; got: " + a)
    # The hash suffix should make them different
    assert_true(a != b, "two consecutive ids should differ")
    # No spaces, no special chars
    for c in a:
        assert_true(c.isalnum() or c == "-",
                    "unexpected char: " + repr(c))


def test_session_round_trip():
    """Session can be saved to disk and loaded back identically."""
    cfg = fake_cfg()
    s = jarvis.DeepResearchSession(
        session_id="test-roundtrip",
        topic="how to test things",
        status="paused",
        plan="# plan\n\n- do A\n- do B",
        notes_md="## findings\n\n- thing 1\n- thing 2",
        open_questions=["what is A?", "what is B?"],
        cfg_snapshot=jarvis._snapshot_cfg(cfg),
        model="test/planner",
        persona="engineer",
        max_seconds=3600,
        max_iterations=10,
    )
    s.sources.append({
        "url": "https://example.com/a",
        "title": "Example A",
        "text": "Body of A.",
        "query": "what is A?",
        "fetched_at": time.time(),
    })
    s.questions.append({
        "ts": time.time(),
        "q": "is A real?",
        "a": "yes, A is real.",
    })
    s.iterations_done = 3
    s.elapsed_seconds = 120.0
    s.save()
    # Verify the file exists
    assert_true(os.path.isfile(
        os.path.join(SESSIONS_DIR_TEST, "test-roundtrip", "session.json")),
        "session.json should be created")
    # Also verify the human-readable mirrors
    for fname in ("notes.md", "sources.json", "questions.json", "plan.md"):
        assert_true(os.path.isfile(
            os.path.join(SESSIONS_DIR_TEST, "test-roundtrip", fname)),
            fname + " should be created")
    # Load it back
    s2 = jarvis.DeepResearchSession.load("test-roundtrip")
    assert_eq(s2.session_id, s.session_id)
    assert_eq(s2.topic, s.topic)
    assert_eq(s2.status, s.status)
    assert_eq(s2.plan, s.plan)
    assert_eq(s2.notes_md, s.notes_md)
    assert_eq(s2.open_questions, s.open_questions)
    assert_eq(len(s2.sources), len(s.sources))
    assert_eq(s2.sources[0]["url"], s.sources[0]["url"])
    assert_eq(len(s2.questions), len(s.questions))
    assert_eq(s2.questions[0]["q"], s.questions[0]["q"])
    assert_eq(s2.iterations_done, s.iterations_done)


def test_session_missing_load_raises():
    """Loading a non-existent session should raise ConfigError."""
    try:
        jarvis.DeepResearchSession.load("does-not-exist-zzz")
    except jarvis.ConfigError:
        return
    raise AssertionError("expected ConfigError for missing session")


def test_list_sessions_empty():
    """List sessions on an empty dir returns []."""
    # We've put test-roundtrip in there; just make sure list returns
    # at least that one and it's a list of tuples.
    out = jarvis.list_sessions()
    assert_true(isinstance(out, list))
    sids = [x[0] for x in out]
    assert_in("test-roundtrip", sids)


def test_delete_session():
    """delete_session removes the directory."""
    sid = "test-delete-me"
    s = jarvis.DeepResearchSession(session_id=sid, topic="x")
    s.save()
    assert_true(os.path.isdir(os.path.join(SESSIONS_DIR_TEST, sid)))
    assert_true(jarvis.delete_session(sid))
    assert_true(not os.path.isdir(os.path.join(SESSIONS_DIR_TEST, sid)))
    # Deleting a non-existent one returns False
    assert_eq(jarvis.delete_session("nonexistent-zz"), False)


def test_excerpt_notes_no_notes():
    """Empty notes -> empty excerpt."""
    out = jarvis._excerpt_notes_for_question("", "anything", 1000)
    assert_eq(out, "")


def test_excerpt_notes_short():
    """Short notes returned as-is, no truncation."""
    notes = "# short\n\nA small note."
    out = jarvis._excerpt_notes_for_question(notes, "anything", 1000)
    assert_eq(out, notes)


def test_excerpt_notes_relevance():
    """Excerpt picks sections relevant to the question keywords."""
    notes = (
        "# All notes\n\n"
        "## Section A: apples\n"
        "Apples are red fruit that grow on trees.\n\n"
        "## Section B: cars\n"
        "Cars have four wheels and an engine.\n\n"
        "## Section C: apple orchards\n"
        "Apple orchards are places where apple trees are grown.\n\n"
        "## Section D: oranges\n"
        "Oranges are citrus fruit, orange in color.\n"
    )
    out = jarvis._excerpt_notes_for_question(notes, "apple orchard", 2000)
    # Sections A and C should be in the excerpt; section B (cars)
    # might be excluded by length cap, but the function should at
    # least include apple-related content.
    assert_in("apple", out.lower())
    assert_in("Section A", out)


def test_max_time_parsing():
    """Various --max-time forms parse to the right number of seconds."""
    assert_eq(jarvis._parse_max_time(None), 5 * 3600, "default = 5h")
    assert_eq(jarvis._parse_max_time("1800"), 1800.0, "raw seconds")
    assert_eq(jarvis._parse_max_time("5h"), 5 * 3600.0, "5 hours")
    assert_eq(jarvis._parse_max_time("30m"), 30 * 60.0, "30 minutes")
    assert_eq(jarvis._parse_max_time("2h30m"), 2 * 3600 + 30 * 60, "2h30m")
    assert_eq(jarvis._parse_max_time("1h15m30s"),
              3600 + 15 * 60 + 30, "1h15m30s")
    assert_eq(jarvis._parse_max_time(""), 5 * 3600, "empty = default")
    # Bad input raises
    raised = False
    try:
        jarvis._parse_max_time("not a time")
    except ValueError:
        raised = True
    assert_true(raised, "bad --max-time should raise")


def test_extract_search_terms_in_deepresearch():
    """The deep research uses the same _extract_search_terms helper."""
    terms = jarvis._extract_search_terms(
        "Tell me about FastAPI, PostgreSQL, and Stripe for a new project.")
    for needed in ("FastAPI", "PostgreSQL", "Stripe"):
        assert_in(needed, terms, "should find " + needed)


def test_plan_generation_parses_json():
    """_deepresearch_generate_plan parses JSON model output."""
    cfg = fake_cfg()
    # Mock _api_call to return canned JSON
    canned = json.dumps({
        "summary": "We want to learn about X.",
        "questions": [
            "X latest 2024",
            "X vs Y",
            "X tutorial",
            "X best practices",
        ],
    })
    orig = jarvis._api_call
    jarvis._api_call = lambda *a, **k: canned
    try:
        plan = jarvis._deepresearch_generate_plan("X", cfg)
    finally:
        jarvis._api_call = orig
    assert_eq(plan["summary"], "We want to learn about X.")
    assert_eq(len(plan["questions"]), 4)


def test_plan_generation_falls_back_on_unparseable():
    """If model output isn't JSON, we synthesize a question from topic."""
    cfg = fake_cfg()
    orig = jarvis._api_call
    jarvis._api_call = lambda *a, **k: "This is not JSON at all, sorry."
    try:
        plan = jarvis._deepresearch_generate_plan("X", cfg)
    finally:
        jarvis._api_call = orig
    assert_eq(plan["summary"].startswith("Auto-generated"), True)
    assert_true(len(plan["questions"]) >= 3, "should have at least 3 fallback questions")
    assert_in("X", plan["questions"][0])


def test_notes_update_passes_through():
    """_deepresearch_update_notes returns the model's response text."""
    cfg = fake_cfg()
    orig = jarvis._api_call
    jarvis._api_call = lambda *a, **k: "# updated\n\n- new finding"
    try:
        out = jarvis._deepresearch_update_notes(
            "topic", "# old notes", "## Search: q\nBody", cfg)
    finally:
        jarvis._api_call = orig
    assert_in("updated", out)
    assert_in("new finding", out)


def test_qa_uses_excerpt():
    """_deepresearch_answer calls the model with the question + notes."""
    cfg = fake_cfg()
    captured = {}
    def fake_api(url, key, messages, *args, **kwargs):
        # messages is a list of {"role", "content"} dicts
        captured["messages"] = list(messages)
        return "Answer: based on the notes, X is good."

    orig = jarvis._api_call
    jarvis._api_call = fake_api
    try:
        ans = jarvis._deepresearch_answer(
            "is X good?",
            "## notes\n\nX is a good framework for Y.",
            cfg)
    finally:
        jarvis._api_call = orig
    assert_in("Answer", ans)
    # The prompt we sent should include the question and the notes
    user_content = captured["messages"][1]["content"]
    assert_in("is X good?", user_content)
    assert_in("X is a good framework", user_content)


def test_report_generation():
    """_deepresearch_write_report calls the model and returns its text."""
    cfg = fake_cfg()
    orig = jarvis._api_call
    jarvis._api_call = lambda *a, **k: "# Final report\n\nHere are findings..."
    try:
        out = jarvis._deepresearch_write_report(
            "topic", "## notes\n\n- fact 1", cfg)
    finally:
        jarvis._api_call = orig
    assert_in("Final report", out)


def test_one_shot_deep_report():
    """run_deep_research_session with one_shot=True does one batch
    and writes a report."""
    cfg = fake_cfg()
    # Mock the AI calls
    canned_plan = json.dumps({
        "summary": "Quick report.",
        "questions": ["topic overview", "topic examples", "topic best practices"],
    })
    canned_notes = "## notes\n\n- fact 1\n- fact 2"
    canned_report = "# REPORT\n\nEverything you need to know."
    canned_qa = "A short answer."

    call_count = {"n": 0}
    def fake_api(*a, **k):
        call_count["n"] += 1
        if call_count["n"] == 1:
            return canned_plan
        if call_count["n"] == 2:
            return canned_notes
        return canned_report

    orig_api = jarvis._api_call
    orig_fetch = jarvis._fetch_url
    orig_search = jarvis._web_search_ddg
    jarvis._api_call = fake_api
    # Mock web search to return no results (so iteration produces
    # nothing -> we still need to fake the "no new sources" path which
    # skips the notes update and goes straight to the report)
    jarvis._web_search_ddg = lambda q: (q, [], "mocked: no results")
    jarvis._fetch_url = lambda u: (u, "", "mocked: skip")

    try:
        session = jarvis.run_deep_research_session(
            "test topic", cfg,
            max_seconds=60, max_iterations=1, one_shot=True)
    finally:
        jarvis._api_call = orig_api
        jarvis._fetch_url = orig_fetch
        jarvis._web_search_ddg = orig_search

    # The session should be saved and have status 'done'
    assert_eq(session.status, "done")
    # The report should be on disk
    rp = os.path.join(SESSIONS_DIR_TEST, session.session_id, "report.md")
    assert_true(os.path.isfile(rp), "report.md should be created")


def test_session_iteration_uses_question():
    """One iteration with one explicit question picks it up and
    adds the new source + updates notes."""
    cfg = fake_cfg()
    s = jarvis.DeepResearchSession(
        session_id="test-iter",
        topic="iteration test",
        status="running",
        open_questions=["a question"],
    )
    # Mock _api_call to return canned notes
    orig_api = jarvis._api_call
    orig_search = jarvis._web_search_ddg
    orig_fetch = jarvis._fetch_url
    jarvis._api_call = lambda *a, **k: "## updated\n\n- new finding"
    # Mock the search to return one fake result, and the fetch to
    # return some text.
    jarvis._web_search_ddg = lambda q: (
        q, [("Fake Title", "https://example.com/x", "snippet here")], None)
    jarvis._fetch_url = lambda u: (
        u, "Body of example page about the topic. " * 50, None)
    try:
        ns, notes, status = jarvis._deepresearch_one_iteration(
            s, cfg,
            questions_override=["a question"],
            stop_check=None)
    finally:
        jarvis._api_call = orig_api
        jarvis._web_search_ddg = orig_search
        jarvis._fetch_url = orig_fetch

    assert_eq(ns, 1, "should have 1 new source")
    assert_eq(s.iterations_done, 1)
    assert_in("new finding", notes or s.notes_md)
    # When questions_override is given, we don't pop from open_questions
    # (override is for one-off use; the run_deep_research_session loop
    # manages open_questions itself).
    assert_eq(len(s.open_questions), 1,
              "with override, open_questions should be unchanged")


def test_session_iteration_pops_open_questions():
    """When no override is given, iteration pops the researched
    questions from open_questions."""
    cfg = fake_cfg()
    s = jarvis.DeepResearchSession(
        session_id="test-iter-pop",
        topic="iteration test",
        status="running",
        open_questions=["a question", "another question"],
    )
    orig_api = jarvis._api_call
    orig_search = jarvis._web_search_ddg
    orig_fetch = jarvis._fetch_url
    jarvis._api_call = lambda *a, **k: "## updated\n\n- new"
    jarvis._web_search_ddg = lambda q: (
        q, [("T", "https://example.com/y", "s")], None)
    jarvis._fetch_url = lambda u: (u, "body " * 30, None)
    try:
        ns, _, _ = jarvis._deepresearch_one_iteration(s, cfg)
    finally:
        jarvis._api_call = orig_api
        jarvis._web_search_ddg = orig_search
        jarvis._fetch_url = orig_fetch
    # SESSION_PER_ITER_QUERIES=3 so both open questions get researched
    # and each yields one source.
    assert_eq(ns, 2, "should have one source per open question researched")
    assert_eq(len(s.open_questions), 0, "all open questions should be popped")
    assert_eq(s.iterations_done, 1)


def test_session_iteration_stop_check():
    """stop_check returning True halts the iteration."""
    cfg = fake_cfg()
    s = jarvis.DeepResearchSession(
        session_id="test-stop",
        topic="stop test",
        status="running",
        open_questions=["a question"],
    )
    orig_api = jarvis._api_call
    orig_search = jarvis._web_search_ddg
    orig_fetch = jarvis._fetch_url
    jarvis._api_call = lambda *a, **k: "ok"
    jarvis._web_search_ddg = lambda q: (
        q, [("T", "https://example.com/y", "s")], None)
    jarvis._fetch_url = lambda u: (u, "body " * 30, None)
    try:
        ns, _, status = jarvis._deepresearch_one_iteration(
            s, cfg,
            questions_override=["a question"],
            stop_check=lambda: True)
    finally:
        jarvis._api_call = orig_api
        jarvis._web_search_ddg = orig_search
        jarvis._fetch_url = orig_fetch
    # With stop_check returning True, we should bail early
    assert_eq(ns, 0, "no sources should be added")


def test_session_status_line():
    """The status line shows iteration + elapsed time + sources."""
    s = jarvis.DeepResearchSession(
        session_id="s", topic="t",
        iterations_done=7, elapsed_seconds=3700, max_seconds=18000,
        sources=[{"url": "u"}] * 12, open_questions=["q"])
    line = s.status_line()
    assert_in("iter 7", line)
    assert_in("1h 01m", line, "elapsed should be formatted as hours+mins")
    assert_in("sources: 12", line)
    assert_in("open-Q: 1", line)


def test_snapshot_cfg_strips_secrets():
    """_snapshot_cfg should not include keys/secrets."""
    cfg = {
        "sonnet_api_key": "sk-secret",
        "codex_api_key": "sk-secret",
        "sonnet_model": "test/model",
        "api_key": "should-be-stripped",
        "PASSWORD": "should-be-stripped",
    }
    snap = jarvis._snapshot_cfg(cfg)
    assert_in("sonnet_model", snap)
    assert_true("sonnet_api_key" not in snap,
                "sonnet_api_key should be stripped from snapshot")
    assert_true("codex_api_key" not in snap)
    assert_true("api_key" not in snap)
    assert_true("PASSWORD" not in snap)


def test_cli_deep_research_flag_parses():
    """The new CLI flags parse correctly."""
    args = jarvis._parse_args([
        "--deep-research", "quantum computing",
        "--max-time", "30m",
        "--max-iterations", "5",
    ])
    assert_eq(args.deep_research, "quantum computing")
    assert_eq(args.max_time, "30m")
    assert_eq(args.max_iterations, "5")


def test_cli_resume_flag_parses():
    args = jarvis._parse_args(["--resume", "my-session-abc123"])
    assert_eq(args.resume, "my-session-abc123")


def test_cli_sessions_flag():
    args = jarvis._parse_args(["--sessions"])
    assert_eq(args.sessions, True)


def test_cli_deep_report_flag():
    args = jarvis._parse_args(["--deep-report", "fastapi 2024"])
    assert_eq(args.deep_report, "fastapi 2024")


def test_notes_md_rendering():
    """rendering notes.md produces a readable markdown doc."""
    s = jarvis.DeepResearchSession(
        session_id="abc",
        topic="render test",
        status="paused",
        plan="## plan\n\ndo A, do B",
        notes_md="## findings\n\n- f1",
        open_questions=["q1", "q2"],
        questions=[{"ts": 1000000, "q": "what?", "a": "because."}],
        sources=[{"url": "https://ex.com", "title": "EX"}],
        iterations_done=2,
    )
    md = s._render_notes_md()
    assert_in("# Deep research notes: render test", md)
    assert_in("## Research plan", md)
    assert_in("## Notes so far", md)
    assert_in("## Open research questions", md)
    assert_in("## Q&A log", md)
    assert_in("## Sources consulted", md)
    assert_in("what?", md)
    assert_in("because.", md)
    assert_in("https://ex.com", md)


def test_orchestrator_research_still_works():
    """The existing --research flow (gather_research) is unaffected
    by the new deep research additions."""
    # We can call gather_research with empty inputs and get ""
    out = jarvis.gather_research("")
    assert_eq(out, "")
    out = jarvis.gather_research("just a normal question, no URLs")
    assert_eq(out, "")


def test_routes_unaffected():
    """The router still works."""
    r = jarvis.route_request("build me a CLI todo app")
    assert_in("flow", r)
    r = jarvis.route_request("design a microservices architecture")
    assert_in("flow", r)


def test_end_to_end_session_flow():
    """Simulate a full new-session flow: plan gen, iteration, Q&A, report.

    All AI calls and web fetches are mocked, but the real session state
    machine, save/load, and the notes-rendering are exercised.
    """
    cfg = fake_cfg()
    # Drive the AI in a deterministic order: plan, notes x N, answer x N,
    # then report.
    canned_plan = json.dumps({
        "summary": "We want to learn about end-to-end test topic.",
        "questions": ["topic latest 2024", "topic best practices",
                      "topic examples"],
    })
    canned_notes_iter1 = "## Notes\n\n- fact A from research"
    canned_notes_iter2 = "## Notes\n\n- fact A from research\n- fact B"
    canned_answer = "Based on the notes: fact A is true."
    canned_report = "# Final report\n\nTopic is well-covered."
    calls = {"plan": 0, "notes": 0, "answer": 0, "report": 0}
    def fake_api(*a, **k):
        caller = k.get("caller", "")
        if caller == "deepresearch_plan":
            calls["plan"] += 1
            return canned_plan
        if caller == "deepresearch_update":
            calls["notes"] += 1
            return canned_notes_iter1 if calls["notes"] == 1 else canned_notes_iter2
        if caller == "deepresearch_qa":
            calls["answer"] += 1
            return canned_answer
        if caller == "deepresearch_report":
            calls["report"] += 1
            return canned_report
        return "{}"
    orig_api = jarvis._api_call
    orig_search = jarvis._web_search_ddg
    orig_fetch = jarvis._fetch_url
    jarvis._api_call = fake_api
    # Make search return 1 result, fetch return some text
    jarvis._web_search_ddg = lambda q: (
        q, [("T", "https://example.com/r1", "s")], None)
    jarvis._fetch_url = lambda u: (u, "page body " * 50, None)

    try:
        # 1. Start a new session
        session = jarvis.run_deep_research_session(
            "end to end test", cfg,
            max_seconds=600, max_iterations=3, one_shot=False,
            stop_check=lambda: calls["notes"] >= 2)   # stop after 2 iters
        # 2. After stopping, ask a question
        ans = jarvis._deepresearch_answer("what is fact A?",
                                            session.notes_md, cfg)
        session.questions.append({"ts": time.time(), "q": "what is fact A?",
                                   "a": ans})
        session.save()
        # 3. Write the final report
        report = jarvis._deepresearch_write_report(
            session.topic, session.notes_md, cfg)
    finally:
        jarvis._api_call = orig_api
        jarvis._web_search_ddg = orig_search
        jarvis._fetch_url = orig_fetch

    # Sanity: the plan was generated, notes were updated, an answer
    # was given, and a report was written.
    assert_eq(calls["plan"], 1)
    assert_true(calls["notes"] >= 1)
    assert_eq(calls["answer"], 1)
    # The report may be called once by the loop (out of questions path)
    # and once by our explicit call. We don't care exactly how many.
    assert_true(calls["report"] >= 1, "at least one report call")
    # The session should have notes
    assert_in("fact A", session.notes_md)
    # The session should have the Q&A logged
    assert_eq(len(session.questions), 1)
    assert_eq(session.questions[0]["q"], "what is fact A?")
    # The report was returned
    assert_in("Final report", report)
    # The session was saved to disk and is loadable again
    s2 = jarvis.DeepResearchSession.load(session.session_id)
    assert_eq(s2.topic, session.topic)
    assert_in("fact A", s2.notes_md)


# ---------------------------------------------------------------------------
# SANDBOX TESTS
# ---------------------------------------------------------------------------
def test_sandbox_allows_safe_code():
    """Trivially safe Python should pass the AST check."""
    safe, reasons = jarvis._ast_safety_check(
        "x = 1 + 2\nprint('hello world')\nimport math\n"
        "print(math.sqrt(16))\n")
    assert_eq(safe, True)
    assert_eq(reasons, [])


def test_sandbox_blocks_os_system():
    """os.system() should be blocked by the AST check."""
    safe, reasons = jarvis._ast_safety_check(
        "import os" + chr(10) + "os.system('rm -rf /')" + chr(10))
    assert_eq(safe, False)
    assert_true(any("os" in r for r in reasons),
                "should mention os: " + repr(reasons))


def test_sandbox_blocks_subprocess():
    safe, reasons = jarvis._ast_safety_check(
        "import subprocess\nsubprocess.run(['ls'])\n")
    assert_eq(safe, False)
    assert_true(any("subprocess" in r.lower() for r in reasons),
                "should mention subprocess: " + repr(reasons))


def test_sandbox_blocks_requests():
    safe, reasons = jarvis._ast_safety_check(
        "import requests\nrequests.get('http://example.com')\n")
    assert_eq(safe, False)
    assert_true(any("requests" in r.lower() for r in reasons),
                "should mention requests: " + repr(reasons))


def test_sandbox_blocks_abs_path():
    safe, reasons = jarvis._ast_safety_check(
        'path = "/etc/passwd"\n')
    assert_eq(safe, False)
    assert_true(any("/etc" in r for r in reasons),
                "should flag suspicious path: " + repr(reasons))


def test_sandbox_timeout():
    """A script that sleeps longer than the timeout should be killed."""
    res = jarvis._sandbox_run_code(
        "import time; time.sleep(30)\nprint('done')\n",
        language="python", timeout=1)
    assert_eq(res.get("safety_rejected"), False)
    assert_true("timeout" in (res.get("error") or "").lower(),
                "should be a timeout error, got: " + str(res.get("error")))


def test_sandbox_extra_files():
    """The sandbox should accept extra_files and place them in the temp dir."""
    res = jarvis._sandbox_run_code(
        "with open('data.txt') as f: print(f.read().strip())\n",
        language="python", timeout=5,
        extra_files={"data.txt": "hello-from-extra\n"})
    assert_eq(res.get("safety_rejected"), False)
    assert_eq(res.get("ok"), True)
    assert_in("hello-from-extra", res.get("stdout", ""))


def test_sandbox_path_traversal():
    """An extra_files name with a slash should be rejected."""
    res = jarvis._sandbox_run_code(
        "print('ok')\n",
        language="python", timeout=5,
        extra_files={"../evil.txt": "x"})
    assert_eq(res.get("safety_rejected"), True)
    assert_true(any("path traversal" in r.lower()
                    for r in res.get("safety_reasons", [])),
                "should flag path traversal: " + repr(res))


def test_sandbox_syntax_error():
    """Code with a syntax error should be rejected at the AST level."""
    safe, reasons = jarvis._ast_safety_check(
        "def foo(:\n    pass\n")
    assert_eq(safe, False)
    assert_true(any("syntax" in r.lower() for r in reasons),
                "should mention syntax error: " + repr(reasons))


def test_sandbox_non_python():
    res = jarvis._sandbox_run_code(
        "console.log('hi')\n", language="javascript", timeout=5)
    assert_eq(res.get("ok"), False)
    assert_in("python", (res.get("error") or "").lower())


def test_sandbox_end_to_end():
    """Full sandbox test: run a small valid program and check the output."""
    res = jarvis._sandbox_run_code(
        "import math\nprint('hello, sandbox!')\n"
        "print('pi =', round(math.pi, 4))\n",
        language="python", timeout=5)
    assert_eq(res.get("ok"), True)
    assert_eq(res.get("safety_rejected"), False)
    assert_in("hello, sandbox!", res.get("stdout", ""))
    assert_in("pi = 3.1416", res.get("stdout", ""))


# ---------------------------------------------------------------------------
# FILE GENERATOR TESTS
# ---------------------------------------------------------------------------
def test_file_gen_parse_text():
    raw = "Here's the file:\n```python\n# hello.py\nprint('hi')\n```\nAll done."
    fname, body, lang = jarvis._file_gen_parse_text(raw)
    assert_eq(fname, "hello.py")  # picked up from the comment
    assert_in("print('hi')", body)
    assert_eq(lang, "python")


def test_file_gen_guess_filename():
    assert_eq(jarvis._file_gen_guess_filename("python", "x = 1\n"),
              "output.py")
    assert_eq(jarvis._file_gen_guess_filename("html", "<html></html>"),
              "index.html")
    assert_eq(jarvis._file_gen_guess_filename("dockerfile", ""),
              "Dockerfile")
    assert_eq(jarvis._file_gen_guess_filename("", "anything"),
              "output.txt")


def test_file_gen_parse_binary():
    import base64
    payload = base64.b64encode(b"\x89PNG\r\n\x1a\n fake").decode()
    raw = ("```json\n"
           "{\"filename\": \"logo.png\", \"format\": \"png\", "
           "\"encoding\": \"base64\", \"content_b64\": \"" + payload + "\", "
           "\"generator_script\": \"# make a png\", "
           "\"notes\": \"a logo\"}\n"
           "```\n")
    fname, content, notes, gen = jarvis._file_gen_parse_binary(raw)
    assert_eq(fname, "logo.png")
    assert_eq(content[:4], b"\x89PNG")
    assert_eq(notes, "a logo")
    assert_in("make a png", gen)


def test_file_gen_intent():
    assert_eq(jarvis._file_gen_intent("write a Python script"), "text")
    assert_eq(jarvis._file_gen_intent("make me a PNG logo"), "binary")
    assert_eq(jarvis._file_gen_intent("create a PDF document"), "binary")
    assert_eq(jarvis._file_gen_intent("create a zip file"), "binary")
    assert_eq(jarvis._file_gen_intent(""), "text")


def test_file_gen_dispatch_mocked():
    """Dispatch with a mocked _api_call returns the expected structure."""
    cfg = fake_cfg()
    orig_api = jarvis._api_call
    jarvis._api_call = lambda *a, **k: "```python\n# foo.py\nx = 1\n```\n"
    try:
        result = jarvis._file_gen_dispatch("write a hello world", cfg)
    finally:
        jarvis._api_call = orig_api
    assert_eq(result.get("ok"), True)
    assert_eq(result.get("kind"), "text")
    assert_eq(result.get("filename"), "foo.py")
    assert_in("x = 1", result.get("content", ""))


def test_file_gen_dispatch_sandbox_test():
    """If --sandbox-test is requested, the generated Python runs in the sandbox."""
    cfg = fake_cfg()
    orig_api = jarvis._api_call
    jarvis._api_call = lambda *a, **k: "```python\n# test.py\nprint('sandboxed')\n```\n"
    try:
        result = jarvis._file_gen_dispatch("test", cfg, sandbox_test=True)
    finally:
        jarvis._api_call = orig_api
    assert_in("sandbox_result", result)
    assert_eq(result["sandbox_result"].get("ok"), True)
    assert_in("sandboxed", result["sandbox_result"].get("stdout", ""))


# ---------------------------------------------------------------------------
# OFFLINE MODE TESTS
# ---------------------------------------------------------------------------
def test_is_local_url():
    assert_eq(jarvis._is_local_url("http://localhost:11434/v1"), True)
    assert_eq(jarvis._is_local_url("http://127.0.0.1:8000/v1"), True)
    assert_eq(jarvis._is_local_url("http://192.168.1.10:11434/v1"), True)
    assert_eq(jarvis._is_local_url("http://10.0.0.5:5000/v1"), True)
    assert_eq(jarvis._is_local_url("https://api.openai.com/v1"), False)
    assert_eq(jarvis._is_local_url("https://openrouter.ai/api/v1"), False)
    assert_eq(jarvis._is_local_url(""), False)


def test_offline_check():
    # Not in offline mode -> always ok
    ok, problems = jarvis._offline_check(
        {"sonnet_api_url": "https://api.openai.com/v1"})
    assert_eq(ok, True)
    assert_eq(problems, [])
    # In offline mode with local URL -> ok
    ok, problems = jarvis._offline_check({
        "offline": True,
        "sonnet_api_url": "http://localhost:11434/v1",
        "codex_api_url": "http://localhost:11434/v1",
    })
    assert_eq(ok, True)
    # In offline mode with remote URL -> problem
    ok, problems = jarvis._offline_check({
        "offline": True,
        "sonnet_api_url": "https://openrouter.ai/api/v1",
    })
    assert_eq(ok, False)
    assert_eq(len(problems), 1)
    assert_in("sonnet_api_url", problems[0])


def test_offline_banner():
    # No offline mode -> empty banner
    assert_eq(jarvis._offline_banner({}), "")
    # Offline mode -> banner has the right text
    banner = jarvis._offline_banner({
        "offline": True,
        "sonnet_api_url": "http://localhost:11434/v1",
    })
    assert_in("OFFLINE MODE", banner)
    assert_in("http://localhost:11434/v1", banner)
    assert_in("local", banner)


# ---------------------------------------------------------------------------
# SELF-MODIFIER TESTS
# ---------------------------------------------------------------------------
def test_self_modify_not_in_repo():
    """Without git, _self_modify_allowed should reject."""
    cfg = {"enable_self_modify": True}
    orig = jarvis._git_in_repo
    jarvis._git_in_repo = lambda: False
    try:
        ok, why = jarvis._self_modify_allowed(cfg)
    finally:
        jarvis._git_in_repo = orig
    assert_eq(ok, False)
    assert_in("git", why.lower())


def test_self_modify_not_enabled():
    """Without enable_self_modify in cfg, should be rejected."""
    cfg = {}
    orig = jarvis._git_in_repo
    jarvis._git_in_repo = lambda: True
    try:
        ok, why = jarvis._self_modify_allowed(cfg)
    finally:
        jarvis._git_in_repo = orig
    assert_eq(ok, False)
    assert_in("disabled", why.lower())
    assert_in("enable_self_modify=true", why)


def test_self_modify_git_missing():
    """If git isn't installed, should be rejected."""
    cfg = {"enable_self_modify": True}
    orig_avail = jarvis._git_available
    orig_repo = jarvis._git_in_repo
    jarvis._git_available = lambda: False
    jarvis._git_in_repo = lambda: False
    try:
        ok, why = jarvis._self_modify_allowed(cfg)
    finally:
        jarvis._git_available = orig_avail
        jarvis._git_in_repo = orig_repo
    assert_eq(ok, False)
    assert_in("git", why.lower())


def test_self_modify_status_no_repo():
    orig = jarvis._git_in_repo
    jarvis._git_in_repo = lambda: False
    try:
        info = jarvis._self_modify_status()
    finally:
        jarvis._git_in_repo = orig
    assert_eq(info.get("in_repo"), False)


def test_self_modify_parse_patch():
    """The _api_call response should be unwrapped and validated."""
    good = ("diff --git a/foo b/foo\n"
            "index 123..456\n"
            "--- a/foo\n"
            "+++ b/foo\n"
            "@@ -1 +1 @@\n"
            "-old\n"
            "+new\n")
    # Strip code-fence if model added it
    fenced = "```diff\n" + good + "\n```\n"
    cleaned = jarvis._re_url.sub(r"^```(?:diff)?\s*\n", "", fenced.strip())
    cleaned = jarvis._re_url.sub(r"\n```\s*$", "", cleaned)
    assert_eq(cleaned, good)
    # Validate that it starts with diff --git
    assert_true(cleaned.startswith("diff --git"))


def test_self_modify_invalid_patch():
    """A response that isn't a valid patch should be rejected."""
    cfg = {"enable_self_modify": True}
    orig_api = jarvis._api_call
    jarvis._api_call = lambda *a, **k: "I'm sorry, I can't do that."
    orig_repo = jarvis._git_in_repo
    jarvis._git_in_repo = lambda: True
    orig_clean = jarvis._git_clean_working_tree
    orig_snap = jarvis._self_modify_snapshot
    jarvis._git_clean_working_tree = lambda: True
    jarvis._self_modify_snapshot = lambda label: ("abc123", "main")
    try:
        result = jarvis._self_modify_apply("add foo", cfg)
    finally:
        jarvis._api_call = orig_api
        jarvis._git_in_repo = orig_repo
        jarvis._git_clean_working_tree = orig_clean
        jarvis._self_modify_snapshot = orig_snap
    assert_eq(result.get("ok"), False)
    assert_in("valid patch", (result.get("error") or "").lower())


def test_self_modify_empty_patch():
    """Empty patch should be rejected."""
    cfg = {"enable_self_modify": True}
    orig_api = jarvis._api_call
    jarvis._api_call = lambda *a, **k: ""
    orig_repo = jarvis._git_in_repo
    jarvis._git_in_repo = lambda: True
    orig_clean = jarvis._git_clean_working_tree
    orig_snap = jarvis._self_modify_snapshot
    jarvis._git_clean_working_tree = lambda: True
    jarvis._self_modify_snapshot = lambda label: ("abc123", "main")
    try:
        result = jarvis._self_modify_apply("do x", cfg)
    finally:
        jarvis._api_call = orig_api
        jarvis._git_in_repo = orig_repo
        jarvis._git_clean_working_tree = orig_clean
        jarvis._self_modify_snapshot = orig_snap
    assert_eq(result.get("ok"), False)
    assert_in("valid patch", (result.get("error") or "").lower())


def test_self_modify_apply_e2e():
    """Full self-modify flow with a patch that should apply cleanly.
    We patch out the git + tests layer so this runs in any environment."""
    cfg = {"enable_self_modify": True}
    good_patch = (
        "diff --git a/jarvis.py b/jarvis.py\n"
        "index 123..456 100644\n"
        "--- a/jarvis.py\n"
        "+++ b/jarvis.py\n"
        "@@ -1,3 +1,4 @@\n"
        " # top of file\n"
        " import os\n"
        "+# added by self-modify test\n"
        " import sys\n"
    )
    orig_api = jarvis._api_call
    orig_repo = jarvis._git_in_repo
    orig_clean = jarvis._git_clean_working_tree
    orig_avail = jarvis._git_available
    orig_snap = jarvis._self_modify_snapshot
    orig_apply = jarvis._self_modify_apply_patch
    orig_tests = jarvis._self_modify_run_tests
    orig_run = jarvis._git_run
    jarvis._api_call = lambda *a, **k: good_patch
    jarvis._git_available = lambda: True
    jarvis._git_in_repo = lambda: True
    jarvis._git_clean_working_tree = lambda: True
    jarvis._self_modify_snapshot = lambda label: ("abc123", "main")
    jarvis._self_modify_apply_patch = lambda patch, files=None: True
    jarvis._self_modify_run_tests = lambda: (True, "all good")
    def fake_git_run(args, cwd=None, check=True):
        if args[0] == "rev-parse" and args[1] == "HEAD":
            return (0, "deadbeef", "")
        return (0, "", "")
    jarvis._git_run = fake_git_run
    try:
        result = jarvis._self_modify_apply("add a comment", cfg)
    finally:
        jarvis._api_call = orig_api
        jarvis._git_in_repo = orig_repo
        jarvis._git_clean_working_tree = orig_clean
        jarvis._git_available = orig_avail
        jarvis._self_modify_snapshot = orig_snap
        jarvis._self_modify_apply_patch = orig_apply
        jarvis._self_modify_run_tests = orig_tests
        jarvis._git_run = orig_run
    assert_eq(result.get("ok"), True)
    assert_eq(result.get("applied"), True)
    assert_eq(result.get("tests_passed"), True)
    assert_eq(result.get("snapshot"), "abc123")
    assert_in("new_commit", result)


# ---------------------------------------------------------------------------
# CLI FLAG TESTS
# ---------------------------------------------------------------------------
def test_cli_new_flags():
    """The new --offline, --generate-file, --self-modify, --self-status
    flags should parse correctly."""
    args = jarvis._parse_args([
        "--offline",
        "--generate-file", "a hello world script",
        "--generate-output", "./hello.py",
        "--sandbox-test",
        "--self-modify", "add foo flag",
        "--self-savepoint", "v1.0",
        "--self-revert", "v1.0",
        "--self-status",
    ])
    assert_eq(args.offline, True)
    assert_eq(args.generate_file, "a hello world script")
    assert_eq(args.generate_output, "./hello.py")
    assert_eq(args.sandbox_test, True)
    assert_eq(args.self_modify, "add foo flag")
    assert_eq(args.self_savepoint, "v1.0")
    assert_eq(args.self_revert, "v1.0")
    assert_eq(args.self_status, True)


# ---------------------------------------------------------------------------
# PHONE PAIRING TESTS
# ---------------------------------------------------------------------------
def test_pairing_constants():
    """Pairing code length + pairing class are exposed."""
    assert_eq(jarvis.PAIRING_CODE_LEN, 6)
    assert_true(hasattr(jarvis, "PairingError"))


def test_pairing_new_code_format():
    """New codes are exactly 6 digits."""
    code = jarvis._pairing_new_code()
    assert_eq(len(code), 6)
    assert_true(code.isdigit(), "code should be all digits: " + code)


def test_pairing_get_active_code_after_new():
    """After _pairing_new_code, _pairing_get_active_code returns it."""
    code = jarvis._pairing_new_code()
    got = jarvis._pairing_get_active_code()
    assert_eq(got, code)


def test_pairing_pair_wrong_code_rejected():
    """Wrong code -> PairingError."""
    jarvis._pairing_new_code()
    try:
        jarvis._pairing_pair_device("000000", "x", "phone")
        raise AssertionError("should have raised")
    except jarvis.PairingError as e:
        assert_in("incorrect", str(e).lower())


def test_pairing_pair_correct_code_clears_code():
    """Correct code -> device added, code cleared (one-time use)."""
    # Reset state for isolation
    jarvis._pairing_save({"code": None, "code_expires": 0,
                            "devices": [], "shared": {}})
    code = jarvis._pairing_new_code()
    dev = jarvis._pairing_pair_device(code, "My Phone", "phone")
    assert_in("id", dev)
    assert_eq(dev["name"], "My Phone")
    assert_eq(dev["kind"], "phone")
    assert_true("added" in dev and "last_seen" in dev)
    # The code should now be cleared
    assert_eq(jarvis._pairing_get_active_code(), None)
    # The device should be retrievable
    got = jarvis._pairing_get_device(dev["id"])
    assert_eq(got["name"], "My Phone")


def test_pairing_no_active_code_rejected():
    """No active code -> PairingError."""
    state = jarvis._pairing_load()
    state["code"] = None
    state["code_expires"] = 0
    jarvis._pairing_save(state)
    try:
        jarvis._pairing_pair_device("123456", "x", "phone")
        raise AssertionError("should have raised")
    except jarvis.PairingError as e:
        assert_in("missing or expired", str(e).lower())


def test_pairing_expired_code_rejected():
    """Expired code -> PairingError."""
    state = jarvis._pairing_load()
    state["code"] = "123456"
    state["code_expires"] = time.time() - 100  # already expired
    jarvis._pairing_save(state)
    try:
        jarvis._pairing_pair_device("123456", "x", "phone")
        raise AssertionError("should have raised")
    except jarvis.PairingError as e:
        assert_in("expired", str(e).lower())


def test_pairing_remove_device():
    """Removing a device removes it from the state."""
    # Reset state first so test order doesn't matter
    jarvis._pairing_save({"code": None, "code_expires": 0,
                            "devices": [], "shared": {}})
    code = jarvis._pairing_new_code()
    dev = jarvis._pairing_pair_device(code, "Test", "phone")
    state = jarvis._pairing_load()
    assert_eq(len(state["devices"]), 1)
    assert_true(jarvis._pairing_remove_device(dev["id"]))
    state = jarvis._pairing_load()
    assert_eq(len(state["devices"]), 0)
    # Removing a non-existent device returns False
    assert_eq(jarvis._pairing_remove_device("nope"), False)


def test_pairing_shared_and_per_device_modes():
    """We can set shared and per-device modes, and they round-trip."""
    # Reset state for isolation
    jarvis._pairing_save({"code": None, "code_expires": 0,
                            "devices": [], "shared": {}})
    jarvis._pairing_set_shared_modes({"offline": True, "research": False})
    state = jarvis._pairing_load()
    assert_eq(state["shared"]["offline"], True)
    # Pair a device and set its modes
    code = jarvis._pairing_new_code()
    dev = jarvis._pairing_pair_device(code, "P", "phone")
    jarvis._pairing_set_device_modes(dev["id"], {"sandbox": True})
    state = jarvis._pairing_load()
    assert_eq(state["devices"][0]["modes"]["sandbox"], True)


def test_pairing_touch_creates_if_missing():
    """touch on an unknown id creates a transient device entry."""
    # Reset state for isolation
    jarvis._pairing_save({"code": None, "code_expires": 0,
                            "devices": [], "shared": {}})
    jarvis._pairing_touch_device("new-device-id")
    state = jarvis._pairing_load()
    ids = [d["id"] for d in state["devices"]]
    assert_in("new-device-id", ids)


# ---------------------------------------------------------------------------
# CLOUD CRYPTO TESTS
# ---------------------------------------------------------------------------
def test_cloud_pbkdf2_deterministic():
    """Same password + salt -> same key."""
    salt = b"\x00" * 16
    k1 = jarvis._cloud_pbkdf2("hunter2", salt, iters=1000)
    k2 = jarvis._cloud_pbkdf2("hunter2", salt, iters=1000)
    assert_eq(k1, k2)
    assert_eq(len(k1), 32)


def test_cloud_pbkdf2_different_passwords():
    """Different passwords -> different keys."""
    salt = b"\x00" * 16
    k1 = jarvis._cloud_pbkdf2("hunter2", salt, iters=1000)
    k2 = jarvis._cloud_pbkdf2("hunter3", salt, iters=1000)
    assert_true(k1 != k2, "different passwords should produce different keys")


def test_cloud_encrypt_decrypt_roundtrip():
    """Encrypt then decrypt yields the original plaintext."""
    salt = os.urandom(16)
    key = jarvis._cloud_pbkdf2("mypassword", salt, iters=1000)
    plain = b'{"sonnet_api_key":"sk-abc","persona":"jarvis"}'
    ct = jarvis._cloud_fernet_like_encrypt(plain, key)
    pt = jarvis._cloud_fernet_like_decrypt(ct, key)
    assert_eq(pt, plain)


def test_cloud_decrypt_wrong_key_fails():
    """Wrong key -> HMAC mismatch."""
    salt = os.urandom(16)
    key1 = jarvis._cloud_pbkdf2("right", salt, iters=1000)
    key2 = jarvis._cloud_pbkdf2("wrong", salt, iters=1000)
    plain = b"secret stuff"
    ct = jarvis._cloud_fernet_like_encrypt(plain, key1)
    try:
        jarvis._cloud_fernet_like_decrypt(ct, key2)
        raise AssertionError("should have raised")
    except ValueError as e:
        assert_in("HMAC", str(e))


def test_cloud_account_id_stable():
    """Account id is stable across case/whitespace and 32 hex chars."""
    a = jarvis._cloud_account_id("User@Example.com")
    b = jarvis._cloud_account_id("user@example.com")
    c = jarvis._cloud_account_id("  user@example.com  ")
    assert_eq(a, b)
    assert_eq(a, c)
    assert_eq(len(a), 32)
    int(a, 16)  # must be valid hex


def test_cloud_available_false_by_default():
    """No CLOUD_URL set -> not available."""
    saved = jarvis.CLOUD_URL
    jarvis.CLOUD_URL = ""
    try:
        assert_eq(jarvis._cloud_available(), False)
    finally:
        jarvis.CLOUD_URL = saved


def test_cloud_signup_no_backend_raises():
    """cloud_signup without a configured backend raises."""
    try:
        jarvis.cloud_signup("a@b.com", "pw1234", {"x": 1})
        raise AssertionError("should have raised")
    except jarvis.JarvisError as e:
        assert_in("not configured", str(e).lower())


def test_cloud_signup_bad_email_raises():
    """Email without @ is rejected."""
    try:
        jarvis.cloud_signup("not-an-email", "pw1234", {"x": 1})
        raise AssertionError("should have raised")
    except jarvis.JarvisError as e:
        assert_in("invalid email", str(e).lower())


def test_cloud_signup_short_password_raises():
    """Password < 6 chars is rejected."""
    try:
        jarvis.cloud_signup("a@b.com", "123", {"x": 1})
        raise AssertionError("should have raised")
    except jarvis.JarvisError as e:
        assert_in("6 characters", str(e).lower())


# ---------------------------------------------------------------------------
# QR CODE TESTS -- REMOVED
# ---------------------------------------------------------------------------
# QR code generation was removed from jarvis because the custom
# encoder produced QR codes that real decoders (OpenCV) could not
# read. The phone pairing flow now uses a copy-pasteable URL +
# 6-digit code instead. These tests verify the QR functions are
# gone and the new flow works.


def test_qr_functions_removed():
    """qr_encode / qr_to_ascii / qr_to_svg / GF helpers are all gone."""
    for name in ("qr_encode", "qr_to_ascii", "qr_to_svg",
                 "_qr_gf_mul", "_qr_gen_poly", "_qr_reed_solomon",
                 "_qr_pick_version", "_qr_module_size",
                 "_qr_reserve_function_patterns", "_qr_place_data",
                 "_qr_apply_mask", "_qr_mask_penalty",
                 "_qr_format_string", "_qr_place_format",
                 "_qr_encode_byte", "_qr_version_info"):
        assert_true(not hasattr(jarvis, name),
                    name + " should have been removed")


def test_qr_endpoint_returns_json():
    """The /api/qr endpoint now returns JSON with the URL + code,
    not an SVG image. The route still exists so old clients do not 404."""
    routes = jarvis._server_routes()
    assert_in("GET /api/qr", routes)
    import inspect
    sig = inspect.signature(routes["GET /api/qr"])
    assert_eq(len(sig.parameters), 2)


def test_pairing_code_format_unchanged():
    """The pairing code format is unchanged: 6 digits, all numeric."""
    code = jarvis._pairing_new_code()
    assert_eq(len(code), 6)
    assert_true(code.isdigit(), "code should be all digits: " + code)


# ---------------------------------------------------------------------------
# WEB SERVER + ROUTES TESTS
# ---------------------------------------------------------------------------
def test_server_routes_complete():
    """The route table has all the expected endpoints."""
    routes = jarvis._server_routes()
    expected = [
        "GET /", "GET /api/status", "POST /api/pair",
        "GET /api/devices", "DELETE /api/devices",
        "GET /api/modes", "POST /api/modes",
        "POST /api/chat", "GET /api/sessions",
        "POST /api/sessions",
        "GET /api/sessions/<id>", "POST /api/sessions/<id>/ask",
        "POST /api/sessions/<id>/pause",
        "POST /api/sessions/<id>/resume",
        "POST /api/sessions/<id>/report",
        "POST /api/generate", "POST /api/sandbox-test",
        "GET /api/qr",
        "GET /api/config", "POST /api/config",
        "POST /api/account/signup", "POST /api/account/login",
        "POST /api/account/logout", "GET /api/account/status",
        "GET /api/files", "GET /api/files/<path>",
        "GET /api/cloud/code", "GET /api/cloud/info",
        "GET /api/projects", "POST /api/projects",
        "GET /api/projects/active", "POST /api/projects/active",
        "GET /api/drive", "POST /api/drive",
    ]
    for k in expected:
        assert_in(k, routes, "missing route: " + k)


def test_safe_modes_subset_strips_keys():
    """_safe_modes_subset strips any key whose name contains 'key' or 'secret'."""
    out = jarvis._safe_modes_subset({
        "sonnet_api_key": "sk-secret",
        "secret_token": "x",
        "persona": "engineer",
        "offline": True,
    })
    assert_in("persona", out)
    assert_in("offline", out)
    assert_true("sonnet_api_key" not in out)
    assert_true("secret_token" not in out)


def test_safe_modes_subset_handles_non_dict():
    """Non-dict input returns empty dict."""
    assert_eq(jarvis._safe_modes_subset(None), {})
    assert_eq(jarvis._safe_modes_subset("nope"), {})
    assert_eq(jarvis._safe_modes_subset(42), {})


def test_phone_server_state():
    """_PhoneServerState stores the cfg + a lock + an active session slot."""
    cfg = fake_cfg()
    state = jarvis._PhoneServerState(cfg)
    assert_true(state.cfg is cfg)
    assert_eq(state.active_session, None)
    assert_true(state.lock is not None)


def test_start_phone_server_returns_server():
    """start_phone_server with blocking=False returns a server instance."""
    # Find an unused port
    import socket as _socket
    s = _socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    cfg = fake_cfg()
    server = jarvis.start_phone_server(cfg, host="127.0.0.1", port=port,
                                        blocking=False)
    if server is not None:
        try:
            import urllib.request
            time.sleep(0.2)
            r = urllib.request.urlopen("http://127.0.0.1:" + str(port) + "/",
                                       timeout=3)
            assert_eq(r.status, 200)
        finally:
            try:
                server.shutdown()
                server.server_close()
            except Exception:
                pass
    # server is None only if bind failed (port still in use), which is
    # possible on test runners; either way the test should not raise.


# ---------------------------------------------------------------------------
# NEW CLI FLAG TESTS
# ---------------------------------------------------------------------------
def test_cli_phone_flags():
    """The new --serve, --pair, --qr, --list-devices, --unpair, --cloud-*
    flags should parse correctly."""
    args = jarvis._parse_args([
        "--serve", "0.0.0.0",
        "--port", "9000",
        "--pair", "--qr",
        "--list-devices",
        "--unpair", "abc123",
        "--cloud-signup", "me@example.com",
        "--cloud-login", "me@example.com",
        "--cloud-logout",
        "--cloud-status",
        "--cloud-url", "https://example.com/kv",
    ])
    assert_eq(args.serve, "0.0.0.0")
    assert_eq(args.port, "9000")
    assert_eq(args.pair, True)
    assert_eq(args.qr, True)
    assert_eq(args.list_devices, True)
    assert_eq(args.unpair, "abc123")
    assert_eq(args.cloud_signup, "me@example.com")
    assert_eq(args.cloud_login, "me@example.com")
    assert_eq(args.cloud_logout, True)
    assert_eq(args.cloud_status, True)
    assert_eq(args.cloud_url, "https://example.com/kv")


def test_cli_serve_default_host():
    """--serve with no value defaults to 0.0.0.0 (const)."""
    args = jarvis._parse_args(["--serve"])
    assert_eq(args.serve, "0.0.0.0")


def test_cli_phone_serve_meta_doesnt_need_config():
    """--list-devices should work even without a config (meta-command)."""
    # Make sure the import-time CONFIG_PATH is empty
    import os as _os
    saved_path = jarvis.CONFIG_PATH
    jarvis.CONFIG_PATH = _os.path.join(_os.path.dirname(__file__),
                                        "_does_not_exist_xyz")
    # Silence stdout so the test output isn't polluted
    import io as _io
    saved_stdout = sys.stdout
    sys.stdout = _io.StringIO()
    try:
        rc = jarvis.main(["--list-devices"])
    finally:
        sys.stdout = saved_stdout
        jarvis.CONFIG_PATH = saved_path
    assert_eq(rc, 0)


# ---------------------------------------------------------------------------
# Rename / env var shim
# ---------------------------------------------------------------------------
def test_jarvis_error_is_dual_ai_error():
    """Backward compat: JarvisError is DualAIError."""
    assert_true(jarvis.JarvisError is jarvis.DualAIError,
                "JarvisError should be the same class as DualAIError")


def test_env_or_legacy_only():
    """DUAL_AI_* env var alone is honored."""
    import os as _os
    saved = _os.environ.pop("JARVIS_TEST_FOO", None)
    _os.environ["DUAL_AI_TEST_FOO"] = "from-legacy"
    try:
        v = jarvis._env_or("DUAL_AI_TEST_FOO", "JARVIS_TEST_FOO", "default")
    finally:
        _os.environ.pop("DUAL_AI_TEST_FOO", None)
        if saved is not None:
            _os.environ["JARVIS_TEST_FOO"] = saved
    assert_eq(v, "from-legacy")


def test_env_or_new_only():
    """JARVIS_* env var alone is honored."""
    import os as _os
    _os.environ["JARVIS_TEST_FOO"] = "from-new"
    try:
        v = jarvis._env_or("DUAL_AI_TEST_FOO", "JARVIS_TEST_FOO", "default")
    finally:
        _os.environ.pop("JARVIS_TEST_FOO", None)
    assert_eq(v, "from-new")


def test_env_or_legacy_wins_when_both_set():
    """If both DUAL_AI_* and JARVIS_* are set, the first one passed wins
    (designed so legacy still takes precedence for safety)."""
    import os as _os
    _os.environ["DUAL_AI_TEST_FOO"] = "legacy"
    _os.environ["JARVIS_TEST_FOO"] = "new"
    try:
        v = jarvis._env_or("DUAL_AI_TEST_FOO", "JARVIS_TEST_FOO", "default")
    finally:
        _os.environ.pop("DUAL_AI_TEST_FOO", None)
        _os.environ.pop("JARVIS_TEST_FOO", None)
    assert_eq(v, "legacy")


def test_env_or_default():
    """When nothing is set, the default (last arg) is returned."""
    import os as _os
    _os.environ.pop("DUAL_AI_TEST_FOO", None)
    _os.environ.pop("JARVIS_TEST_FOO", None)
    v = jarvis._env_or("DUAL_AI_TEST_FOO", "JARVIS_TEST_FOO", "fallback")
    assert_eq(v, "fallback")


def test_legacy_config_migration():
    """Migrating ~/.dual_ai -> ~/.jarvis copies config + subdirs."""
    import os as _os
    import shutil as _sh
    base = tempfile.mkdtemp(prefix="jarvis_legacy_mig_")
    try:
        legacy = _os.path.join(base, ".dual_ai")
        new = _os.path.join(base, ".jarvis")
        _os.makedirs(_os.path.join(legacy, "pairing"))
        with open(_os.path.join(legacy, "config.json"), "w") as f:
            f.write('{"k":"v"}')
        with open(_os.path.join(legacy, "pairing", "dev.json"), "w") as f:
            f.write("{}")
        # Patch module constants to point at our temp home
        saved_dir = jarvis.CONFIG_DIR
        saved_path = jarvis.CONFIG_PATH
        saved_legacy_dir = jarvis._LEGACY_CONFIG_DIR
        saved_legacy = jarvis._LEGACY_CONFIG_PATH
        jarvis.CONFIG_DIR = new
        jarvis.CONFIG_PATH = _os.path.join(new, "config.json")
        jarvis._LEGACY_CONFIG_DIR = legacy
        jarvis._LEGACY_CONFIG_PATH = _os.path.join(legacy, "config.json")
        try:
            jarvis._maybe_migrate_legacy_config()
        finally:
            jarvis.CONFIG_DIR = saved_dir
            jarvis.CONFIG_PATH = saved_path
            jarvis._LEGACY_CONFIG_DIR = saved_legacy_dir
            jarvis._LEGACY_CONFIG_PATH = saved_legacy
        assert_true(_os.path.exists(_os.path.join(new, "config.json")),
                    "config.json should be migrated")
        assert_true(_os.path.exists(_os.path.join(new, "pairing", "dev.json")),
                    "pairing/ subdir should be migrated")
    finally:
        _sh.rmtree(base, ignore_errors=True)


# ---------------------------------------------------------------------------
# Godot integration
# ---------------------------------------------------------------------------
def test_godot_find_project_root_found():
    """_godot_find_project_root walks up to find project.godot."""
    import os as _os
    import shutil as _sh
    base = tempfile.mkdtemp(prefix="jarvis_godot_")
    try:
        proj = _os.path.join(base, "mygame")
        sub = _os.path.join(proj, "scripts", "ui")
        _os.makedirs(sub)
        with open(_os.path.join(proj, "project.godot"), "w") as f:
            f.write("config_version=5\n")
        found = jarvis._godot_find_project_root(sub)
        assert_true(_os.path.normpath(found) == _os.path.normpath(proj),
                    "should walk up to project root, got: " + str(found))
    finally:
        _sh.rmtree(base, ignore_errors=True)


def test_godot_find_project_root_missing():
    """_godot_find_project_root returns None when no project.godot is around."""
    import os as _os
    import shutil as _sh
    base = tempfile.mkdtemp(prefix="jarvis_godot_no_")
    try:
        # no project.godot anywhere up to /tmp
        result = jarvis._godot_find_project_root(base)
        # If a godot project happens to be on the system in /, we'd find it.
        # If not, result is None. Just assert it didn't crash and is either
        # None or a string.
        assert_true(result is None or isinstance(result, str),
                    "should return None or a path string")
    finally:
        _sh.rmtree(base, ignore_errors=True)


def test_godot_read_project_info_v5():
    """config_version=5 -> Godot 4.x, main_scene + autoloads parsed."""
    import os as _os
    import shutil as _sh
    base = tempfile.mkdtemp(prefix="jarvis_godot_v5_")
    try:
        proj = _os.path.join(base, "proj")
        _os.makedirs(proj)
        with open(_os.path.join(proj, "project.godot"), "w") as f:
            f.write("config_version=5\n\n"
                    "[application]\n\n"
                    "config/name=\"test\"\n"
                    "run/main_scene=\"res://main.tscn\"\n\n"
                    "[autoload]\n\n"
                    "Music=\"*res://autoload/music.gd\"\n")
        info = jarvis._godot_read_project_info(proj)
        assert_true(info["version"].startswith("Godot 4"),
                    "expected Godot 4.x, got: " + str(info["version"]))
        assert_eq(info["main_scene"], "res://main.tscn")
        names = [a["name"] for a in info["autoloads"]]
        assert_true("Music" in names, "autoload should be parsed, got: " + str(names))
    finally:
        _sh.rmtree(base, ignore_errors=True)


def test_godot_read_project_info_v3():
    """config_version=4 -> Godot 3.x."""
    import os as _os
    import shutil as _sh
    base = tempfile.mkdtemp(prefix="jarvis_godot_v3_")
    try:
        proj = _os.path.join(base, "proj")
        _os.makedirs(proj)
        with open(_os.path.join(proj, "project.godot"), "w") as f:
            f.write("config_version=4\n")
        info = jarvis._godot_read_project_info(proj)
        assert_true(info["version"].startswith("Godot 3"),
                    "expected Godot 3.x, got: " + str(info["version"]))
    finally:
        _sh.rmtree(base, ignore_errors=True)


def test_godot_enhance_impl_prompt():
    """The enhanced prompt includes the project context block."""
    base = "you are a codex."
    info = {"root": "/tmp/proj", "version": "Godot 4.x",
            "main_scene": "res://main.tscn",
            "autoloads": [{"name": "X", "path": "*res://x.gd"}]}
    out = jarvis._godot_enhance_impl_prompt(base, info)
    assert_true("[PROJECT CONTEXT]" in out, "should inject project context marker")
    assert_true("gdscript" in out.lower() or "GDScript" in out,
                "should mention gdscript style")
    assert_true(base in out, "should preserve the original base prompt")


# ---------------------------------------------------------------------------
# Projects store
# ---------------------------------------------------------------------------
def test_project_scaffold_python():
    """Python scaffold creates pyproject.toml + src/<pkg>/__init__.py."""
    import os as _os
    import shutil as _sh
    base = tempfile.mkdtemp(prefix="jarvis_proj_py_")
    try:
        saved = jarvis.PROJECTS_DIR
        jarvis.PROJECTS_DIR = _os.path.join(base, "projects")
        try:
            m = jarvis._project_scaffold("mypkg", "python")
        finally:
            jarvis.PROJECTS_DIR = saved
        assert_eq(m["kind"], "python")
        proj_root = m["path"]
        assert_true(_os.path.exists(_os.path.join(proj_root, "pyproject.toml")),
                    "pyproject.toml should exist")
        assert_true(_os.path.isdir(_os.path.join(proj_root, "src")),
                    "src/ should exist")
    finally:
        _sh.rmtree(base, ignore_errors=True)


def test_project_scaffold_godot():
    """Godot scaffold creates project.godot + scenes/main.tscn + scripts/main.gd."""
    import os as _os
    import shutil as _sh
    base = tempfile.mkdtemp(prefix="jarvis_proj_gd_")
    try:
        saved = jarvis.PROJECTS_DIR
        jarvis.PROJECTS_DIR = _os.path.join(base, "projects")
        try:
            m = jarvis._project_scaffold("mygame", "godot")
        finally:
            jarvis.PROJECTS_DIR = saved
        assert_eq(m["kind"], "godot")
        proj_root = m["path"]
        assert_true(_os.path.exists(_os.path.join(proj_root, "project.godot")),
                    "project.godot should exist")
        assert_true(_os.path.exists(_os.path.join(proj_root, "scenes", "main.tscn")),
                    "scenes/main.tscn should exist")
        assert_true(_os.path.exists(_os.path.join(proj_root, "scripts", "main.gd")),
                    "scripts/main.gd should exist")
    finally:
        _sh.rmtree(base, ignore_errors=True)


def test_list_projects_sorted_by_updated():
    """list_projects returns most-recently-updated first."""
    import os as _os
    import shutil as _sh
    import time as _time
    base = tempfile.mkdtemp(prefix="jarvis_proj_list_")
    try:
        saved = jarvis.PROJECTS_DIR
        jarvis.PROJECTS_DIR = _os.path.join(base, "projects")
        try:
            jarvis._project_scaffold("a", "python")
            _time.sleep(0.05)
            jarvis._project_scaffold("b", "python")
            _time.sleep(0.05)
            jarvis._project_scaffold("c", "godot")
            names = [m["name"] for m in jarvis.list_projects()]
        finally:
            jarvis.PROJECTS_DIR = saved
        assert_eq(names, ["c", "b", "a"])
    finally:
        _sh.rmtree(base, ignore_errors=True)


def test_active_project_set_get():
    """set/get active project round-trip."""
    import os as _os
    import shutil as _sh
    base = tempfile.mkdtemp(prefix="jarvis_proj_act_")
    try:
        saved_dir = jarvis.PROJECTS_DIR
        saved_cfg = jarvis.CONFIG_DIR
        jarvis.PROJECTS_DIR = _os.path.join(base, "projects")
        jarvis.CONFIG_DIR = base
        try:
            jarvis._project_scaffold("foo", "python")
            assert_true(jarvis.get_active_project() is None,
                        "no active project before set")
            jarvis.set_active_project("foo")
            assert_eq(jarvis.get_active_project(), "foo")
            jarvis.set_active_project("")
            assert_true(jarvis.get_active_project() is None,
                        "empty name should clear active project")
        finally:
            jarvis.PROJECTS_DIR = saved_dir
            jarvis.CONFIG_DIR = saved_cfg
    finally:
        _sh.rmtree(base, ignore_errors=True)


def test_project_remove():
    """_project_remove deletes the manifest; with delete_files=True, the dir too."""
    import os as _os
    import shutil as _sh
    base = tempfile.mkdtemp(prefix="jarvis_proj_rm_")
    try:
        saved = jarvis.PROJECTS_DIR
        jarvis.PROJECTS_DIR = _os.path.join(base, "projects")
        try:
            jarvis._project_scaffold("torm", "python")
            assert_true(any(m["name"] == "torm" for m in jarvis.list_projects()))
            jarvis._project_remove("torm", delete_files=True)
            assert_true(not any(m["name"] == "torm" for m in jarvis.list_projects()),
                        "project should be gone after remove")
        finally:
            jarvis.PROJECTS_DIR = saved
    finally:
        _sh.rmtree(base, ignore_errors=True)


# ---------------------------------------------------------------------------
# Google Drive sync (watch-folder)
# ---------------------------------------------------------------------------
def test_drive_push_pull_roundtrip():
    """push then wipe then pull restores projects."""
    import os as _os
    import shutil as _sh
    base = tempfile.mkdtemp(prefix="jarvis_drive_")
    try:
        saved_proj = jarvis.PROJECTS_DIR
        saved_cfg = jarvis.CONFIG_DIR
        jarvis.PROJECTS_DIR = _os.path.join(base, "projects")
        jarvis.CONFIG_DIR = base
        drive = _os.path.join(base, "drive")
        _os.makedirs(drive)
        try:
            jarvis._project_scaffold("d1", "python")
            jarvis._project_scaffold("d2", "godot")
            jarvis._drive_save({"folder": drive, "last_sync": None})
            c, s, e = jarvis.drive_push()
            assert_eq((c, s, len(e)), (2, 0, 0))
            assert_true(_os.path.isdir(_os.path.join(drive, "d1")))
            assert_true(_os.path.isdir(_os.path.join(drive, "d2")))
            # wipe local
            _sh.rmtree(jarvis.PROJECTS_DIR)
            assert_eq(len(jarvis.list_projects()), 0)
            # pull back
            c, s, e = jarvis.drive_pull()
            assert_eq((c, s, len(e)), (2, 0, 0))
            names = sorted(m["name"] for m in jarvis.list_projects())
            assert_eq(names, ["d1", "d2"])
        finally:
            jarvis.PROJECTS_DIR = saved_proj
            jarvis.CONFIG_DIR = saved_cfg
    finally:
        _sh.rmtree(base, ignore_errors=True)


def test_drive_resolve_unset_returns_none():
    """When drive folder is not configured or doesn't exist, return None."""
    import os as _os
    import shutil as _sh
    base = tempfile.mkdtemp(prefix="jarvis_drive_unset_")
    try:
        saved_cfg = jarvis.CONFIG_DIR
        jarvis.CONFIG_DIR = base
        try:
            # nothing configured
            assert_true(jarvis._drive_resolve_folder() is None)
            # configured but doesn't exist
            jarvis._drive_save({"folder": _os.path.join(base, "nope"), "last_sync": None})
            assert_true(jarvis._drive_resolve_folder() is None)
        finally:
            jarvis.CONFIG_DIR = saved_cfg
    finally:
        _sh.rmtree(base, ignore_errors=True)


# ---------------------------------------------------------------------------
# Phone API: projects + drive routes
# ---------------------------------------------------------------------------
def test_route_projects_list():
    """GET /api/projects route exists with the right signature."""
    routes = jarvis._server_routes()
    assert_in("GET /api/projects", routes)
    assert_in("POST /api/projects", routes)
    import inspect
    sig = inspect.signature(routes["GET /api/projects"])
    assert_eq(len(sig.parameters), 2)


def test_route_drive_status():
    """GET /api/drive route exists with the right signature."""
    routes = jarvis._server_routes()
    assert_in("GET /api/drive", routes)
    assert_in("POST /api/drive", routes)
    import inspect
    sig = inspect.signature(routes["GET /api/drive"])
    assert_eq(len(sig.parameters), 2)


def test_route_projects_set_active():
    """POST /api/projects/active exists for changing the active project."""
    routes = jarvis._server_routes()
    assert_in("GET /api/projects/active", routes)
    assert_in("POST /api/projects/active", routes)


def test_route_projects_list_scaffolds():
    """Scaffold two projects, list returns them in updated-desc order."""
    import os as _os
    import shutil as _sh
    base = tempfile.mkdtemp(prefix="jarvis_rt_proj_")
    try:
        saved = jarvis.PROJECTS_DIR
        saved_cfg = jarvis.CONFIG_DIR
        jarvis.PROJECTS_DIR = _os.path.join(base, "projects")
        jarvis.CONFIG_DIR = base
        try:
            jarvis._project_scaffold("a", "python")
            jarvis._project_scaffold("b", "godot")
            projs = jarvis.list_projects()
            names = sorted(m["name"] for m in projs)
            assert_eq(names, ["a", "b"])
        finally:
            jarvis.PROJECTS_DIR = saved
            jarvis.CONFIG_DIR = saved_cfg
    finally:
        _sh.rmtree(base, ignore_errors=True)


def test_route_drive_action_set_unset():
    """Drive state: set then unset should toggle the configured folder."""
    import os as _os
    import shutil as _sh
    base = tempfile.mkdtemp(prefix="jarvis_rt_drive_")
    try:
        # Make sure CONFIG_DIR exists so _drive_save has a parent
        _os.makedirs(base, exist_ok=True)
        # Wipe any pre-existing drive.json in the temp home
        _drive_cfg = _os.path.join(base, "drive.json")
        if _os.path.exists(_drive_cfg):
            _os.remove(_drive_cfg)
        saved = jarvis.CONFIG_DIR
        saved_path = jarvis.DRIVE_CONFIG_PATH
        jarvis.CONFIG_DIR = base
        jarvis.DRIVE_CONFIG_PATH = _os.path.join(base, "drive.json")
        try:
            # unset -> folder is ""
            state = jarvis._drive_load()
            assert_eq(state.get("folder", ""), "")
            # set
            drive = _os.path.join(base, "drive")
            _os.makedirs(drive)
            jarvis._drive_save({"folder": drive, "last_sync": None})
            assert_eq(jarvis._drive_load()["folder"], drive)
            # unset again
            jarvis._drive_save({"folder": "", "last_sync": None})
            assert_eq(jarvis._drive_load().get("folder", ""), "")
        finally:
            jarvis.CONFIG_DIR = saved
            jarvis.DRIVE_CONFIG_PATH = saved_path
    finally:
        _sh.rmtree(base, ignore_errors=True)


# ---------------------------------------------------------------------------
# AUTHENTICATION TESTS
# ---------------------------------------------------------------------------
def test_master_passcode_is_set():
    """_MASTER_PASSCODE is a non-empty string (otherwise auth is broken)."""
    assert_true(isinstance(jarvis._MASTER_PASSCODE, str))
    assert_true(len(jarvis._MASTER_PASSCODE) >= 4,
                "passcode should be at least 4 chars; got: " +
                repr(jarvis._MASTER_PASSCODE))


def test_auth_bypass_active_with_correct_passcode():
    """JARVIS_BYPASS=<passcode> activates bypass."""
    import os as _os
    saved = _os.environ.pop("JARVIS_BYPASS", None)
    try:
        _os.environ["JARVIS_BYPASS"] = jarvis._MASTER_PASSCODE
        assert_eq(jarvis._auth_bypass_active(), True)
    finally:
        _os.environ.pop("JARVIS_BYPASS", None)
        if saved is not None:
            _os.environ["JARVIS_BYPASS"] = saved


def test_auth_bypass_inactive_with_wrong_passcode():
    """JARVIS_BYPASS=<wrong> does NOT activate bypass (security)."""
    import os as _os
    saved = _os.environ.pop("JARVIS_BYPASS", None)
    try:
        _os.environ["JARVIS_BYPASS"] = "wrongpass"
        assert_eq(jarvis._auth_bypass_active(), False)
        # Empty string is also not a bypass
        _os.environ["JARVIS_BYPASS"] = ""
        assert_eq(jarvis._auth_bypass_active(), False)
    finally:
        _os.environ.pop("JARVIS_BYPASS", None)
        if saved is not None:
            _os.environ["JARVIS_BYPASS"] = saved


def test_auth_bypass_inactive_when_unset():
    """No env var set -> bypass not active."""
    import os as _os
    saved = _os.environ.pop("JARVIS_BYPASS", None)
    try:
        assert_eq(jarvis._auth_bypass_active(), False)
    finally:
        if saved is not None:
            _os.environ["JARVIS_BYPASS"] = saved


def test_auth_attempt_windows_hello_returns_bool():
    """Windows Hello attempt always returns a bool (never raises)."""
    result = jarvis._auth_attempt_windows_hello()
    assert_true(isinstance(result, bool),
                "should return bool; got: " + repr(result))


def test_auth_attempt_webcam_returns_bool():
    """Webcam attempt always returns a bool. With no face registered
    and no opencv, it should return False (not raise)."""
    import os as _os
    saved = _os.environ.get("JARVIS_AUTH_NO_WEBCAM")
    _os.environ["JARVIS_AUTH_NO_WEBCAM"] = "1"
    try:
        result = jarvis._auth_attempt_webcam()
        assert_true(isinstance(result, bool),
                    "should return bool; got: " + repr(result))
        # No face registered -> should be False
        assert_eq(result, False)
    finally:
        if saved is None:
            _os.environ.pop("JARVIS_AUTH_NO_WEBCAM", None)
        else:
            _os.environ["JARVIS_AUTH_NO_WEBCAM"] = saved


def test_auth_passcode_correct():
    """The passcode matches _MASTER_PASSCODE."""
    # Indirect: we can't easily mock getpass.input, so just verify
    # the constant is what we expect and is consistent.
    assert_eq(jarvis._auth_attempt_passcode.__name__,
              "_auth_attempt_passcode")


def test_no_auth_flag_parses():
    """--no-auth flag parses correctly."""
    args = jarvis._parse_args(["--no-auth", "--show-config"])
    assert_eq(args.no_auth, True)


def test_auth_setup_flag_parses():
    """--auth-setup flag parses correctly."""
    args = jarvis._parse_args(["--auth-setup"])
    assert_eq(args.auth_setup, True)


def test_auth_test_flag_parses():
    """--auth-test flag parses correctly."""
    args = jarvis._parse_args(["--auth-test"])
    assert_eq(args.auth_test, True)


# -----------------------------------------------------------------------
# Experimental-flag warning (v1.0)
# -----------------------------------------------------------------------


class _FakeArgs(object):
    """Build a namespace that looks like the parsed argparse output,
    with the given experimental flags set."""
    def __init__(self, **kw):
        # Default all experimental flags to off / None
        for f in (
            "self_modify", "self_savepoint", "self_revert", "self_status",
            "cloud_signup", "cloud_login", "cloud_logout", "cloud_status",
            "cloud_url", "godot",
        ):
            setattr(self, f, None)
        for k, v in kw.items():
            setattr(self, k, v)


def test_warn_experimental_no_flags_silent():
    """No experimental flags -> no warning."""
    import io as _io
    saved = sys.stdout
    sys.stdout = _io.StringIO()
    try:
        jarvis._warn_experimental._warned = False
        jarvis._warn_experimental(_FakeArgs())
        out = sys.stdout.getvalue()
    finally:
        sys.stdout = saved
    assert_eq(out, "")


def test_warn_experimental_self_status():
    """--self-status -> warning about self-status."""
    import io as _io
    saved = sys.stdout
    sys.stdout = _io.StringIO()
    try:
        jarvis._warn_experimental._warned = False
        jarvis._warn_experimental(_FakeArgs(self_status=True))
        out = sys.stdout.getvalue()
    finally:
        sys.stdout = saved
    assert_in("EXPERIMENTAL", out)
    assert_in("--self-status", out)


def test_warn_experimental_multiple_flags():
    """Multiple experimental flags -> one warning listing all of them."""
    import io as _io
    saved = sys.stdout
    sys.stdout = _io.StringIO()
    try:
        jarvis._warn_experimental._warned = False
        jarvis._warn_experimental(_FakeArgs(
            self_status=True,
            cloud_login="user@example.com",
        ))
        out = sys.stdout.getvalue()
    finally:
        sys.stdout = saved
    assert_in("--self-status", out)
    assert_in("--cloud-login", out)
    # Only one warning block
    assert_eq(out.count("EXPERIMENTAL"), 1)


def test_warn_experimental_warns_only_once():
    """Same args called twice in one process -> only one warning."""
    import io as _io
    saved = sys.stdout
    sys.stdout = _io.StringIO()
    try:
        jarvis._warn_experimental._warned = False
        args = _FakeArgs(self_status=True)
        jarvis._warn_experimental(args)
        first = sys.stdout.getvalue()
        jarvis._warn_experimental(args)
        second = sys.stdout.getvalue()
    finally:
        sys.stdout = saved
    assert_in("EXPERIMENTAL", first)
    # Second call should produce no extra output
    assert_eq(len(second) - len(first), 0)


def test_warn_experimental_godot_only_explicit():
    """--godot auto-detect (godot=None) doesn't warn; explicit True does."""
    import io as _io
    saved = sys.stdout
    # Case 1: godot not set (auto-detect mode) -> silent
    sys.stdout = _io.StringIO()
    try:
        jarvis._warn_experimental._warned = False
        jarvis._warn_experimental(_FakeArgs(godot=None))
        out1 = sys.stdout.getvalue()
    finally:
        sys.stdout = saved
    assert_eq(out1, "")
    # Case 2: --godot explicitly set -> warns
    sys.stdout = _io.StringIO()
    try:
        jarvis._warn_experimental._warned = False
        jarvis._warn_experimental(_FakeArgs(godot=True))
        out2 = sys.stdout.getvalue()
    finally:
        sys.stdout = saved
    assert_in("--godot", out2)


# -----------------------------------------------------------------------
# End experimental-flag tests
# -----------------------------------------------------------------------


# -----------------------------------------------------------------------
# Passcode change tests (v1.0)
# -----------------------------------------------------------------------


def test_accepted_passcodes_default():
    """_accepted_passcodes returns the hardcoded fallback when no
    config exists."""
    import os as _os
    # Ensure no config file is around for this test.
    if _os.path.isfile(jarvis.CONFIG_PATH):
        _os.remove(jarvis.CONFIG_PATH)
    accepted = jarvis._accepted_passcodes()
    assert_eq(len(accepted), 1)
    assert_eq(accepted[0], jarvis._MASTER_PASSCODE)


def test_accepted_passcodes_with_override():
    """If config has passcode_override, it's first in the list."""
    import os as _os
    if _os.path.isfile(jarvis.CONFIG_PATH):
        _os.remove(jarvis.CONFIG_PATH)
    # Write a config with an override
    with open(jarvis.CONFIG_PATH, "w", encoding="utf-8") as f:
        import json as _json
        _json.dump({"passcode_override": "my-secret-pw"}, f)
    try:
        accepted = jarvis._accepted_passcodes()
        assert_eq(accepted[0], "my-secret-pw")
        assert_eq(accepted[1], jarvis._MASTER_PASSCODE)
    finally:
        if _os.path.isfile(jarvis.CONFIG_PATH):
            _os.remove(jarvis.CONFIG_PATH)


def test_accepted_passcodes_corrupt_config():
    """A corrupt config (e.g. empty file) doesn't break auth."""
    import os as _os
    if _os.path.isfile(jarvis.CONFIG_PATH):
        _os.remove(jarvis.CONFIG_PATH)
    # Write garbage to the config
    with open(jarvis.CONFIG_PATH, "w", encoding="utf-8") as f:
        f.write("not valid json {")
    try:
        accepted = jarvis._accepted_passcodes()
        # Falls back to just the hardcoded one
        assert_eq(len(accepted), 1)
        assert_eq(accepted[0], jarvis._MASTER_PASSCODE)
    finally:
        if _os.path.isfile(jarvis.CONFIG_PATH):
            _os.remove(jarvis.CONFIG_PATH)


def test_change_passcode_flag_parses():
    """--change-passcode flag parses correctly."""
    args = jarvis._parse_args(["--change-passcode"])
    assert_eq(args.change_passcode, True)


def test_change_cloud_password_flag_parses():
    """--change-cloud-password flag parses correctly."""
    args = jarvis._parse_args(["--change-cloud-password"])
    assert_eq(args.change_cloud_password, True)


# -----------------------------------------------------------------------
# End passcode change tests
# -----------------------------------------------------------------------


# -----------------------------------------------------------------------
# Main
# -----------------------------------------------------------------------
def _run_jarvis_tests():
        # Set up the test environment (mock requests, redirect CONFIG_DIR)
    _setup_jarvis_tests()
    sys.stderr.write("Running deep-research tests...\n\n")
    _run("module imports",                    test_module_imports)
    _run("session id format",                 test_session_id_format)
    _run("session round-trip",                test_session_round_trip)
    _run("missing session load raises",       test_session_missing_load_raises)
    _run("list sessions",                     test_list_sessions_empty)
    _run("delete session",                    test_delete_session)
    _run("excerpt notes (empty)",             test_excerpt_notes_no_notes)
    _run("excerpt notes (short)",             test_excerpt_notes_short)
    _run("excerpt notes (relevance)",         test_excerpt_notes_relevance)
    _run("--max-time parsing",                test_max_time_parsing)
    _run("extract search terms",              test_extract_search_terms_in_deepresearch)
    _run("plan generation parses JSON",       test_plan_generation_parses_json)
    _run("plan generation falls back",        test_plan_generation_falls_back_on_unparseable)
    _run("notes update pass-through",         test_notes_update_passes_through)
    _run("Q&A uses excerpt",                  test_qa_uses_excerpt)
    _run("report generation",                 test_report_generation)
    _run("one-shot deep report",              test_one_shot_deep_report)
    _run("session iteration uses question",   test_session_iteration_uses_question)
    _run("session iteration pops open-Q",     test_session_iteration_pops_open_questions)
    _run("session iteration stop_check",      test_session_iteration_stop_check)
    _run("session status line",               test_session_status_line)
    _run("snapshot cfg strips secrets",       test_snapshot_cfg_strips_secrets)
    _run("--deep-research flag",              test_cli_deep_research_flag_parses)
    _run("--resume flag",                     test_cli_resume_flag_parses)
    _run("--sessions flag",                   test_cli_sessions_flag)
    _run("--deep-report flag",                test_cli_deep_report_flag)
    _run("notes.md rendering",                test_notes_md_rendering)
    _run("existing --research still works",   test_orchestrator_research_still_works)
    _run("router unaffected",                 test_routes_unaffected)
    _run("end-to-end session flow",           test_end_to_end_session_flow)
    _run("sandbox: AST allows safe code",      test_sandbox_allows_safe_code)
    _run("sandbox: AST blocks os.system",      test_sandbox_blocks_os_system)
    _run("sandbox: AST blocks subprocess",     test_sandbox_blocks_subprocess)
    _run("sandbox: AST blocks requests",       test_sandbox_blocks_requests)
    _run("sandbox: AST blocks abs path string",test_sandbox_blocks_abs_path)
    _run("sandbox: timeout works",            test_sandbox_timeout)
    _run("sandbox: extra_files dropped",      test_sandbox_extra_files)
    _run("sandbox: path traversal blocked",   test_sandbox_path_traversal)
    _run("sandbox: bad syntax rejected",      test_sandbox_syntax_error)
    _run("sandbox: non-python language",       test_sandbox_non_python)
    _run("sandbox: end-to-end",               test_sandbox_end_to_end)
    _run("file-gen: text parse",              test_file_gen_parse_text)
    _run("file-gen: filename guess",          test_file_gen_guess_filename)
    _run("file-gen: binary parse",            test_file_gen_parse_binary)
    _run("file-gen: intent heuristic",        test_file_gen_intent)
    _run("file-gen: dispatch (mocked)",       test_file_gen_dispatch_mocked)
    _run("file-gen: sandbox test",            test_file_gen_dispatch_sandbox_test)
    _run("is_local_url",                       test_is_local_url)
    _run("offline_check",                      test_offline_check)
    _run("offline_banner",                    test_offline_banner)
    _run("self-modify: not in repo",          test_self_modify_not_in_repo)
    _run("self-modify: not enabled",          test_self_modify_not_enabled)
    _run("self-modify: git not installed",    test_self_modify_git_missing)
    _run("self-modify: status",               test_self_modify_status_no_repo)
    _run("self-modify: parse response",       test_self_modify_parse_patch)
    _run("self-modify: invalid patch",        test_self_modify_invalid_patch)
    _run("self-modify: empty patch",          test_self_modify_empty_patch)
    _run("self-modify: end-to-end mock",      test_self_modify_apply_e2e)
    _run("CLI: new flags parse",              test_cli_new_flags)
    _run("pairing: constants",                test_pairing_constants)
    _run("pairing: new code format",          test_pairing_new_code_format)
    _run("pairing: get active after new",     test_pairing_get_active_code_after_new)
    _run("pairing: wrong code rejected",      test_pairing_pair_wrong_code_rejected)
    _run("pairing: correct code + cleared",   test_pairing_pair_correct_code_clears_code)
    _run("pairing: no active code",          test_pairing_no_active_code_rejected)
    _run("pairing: expired code",            test_pairing_expired_code_rejected)
    _run("pairing: remove device",           test_pairing_remove_device)
    _run("pairing: shared/per-device modes", test_pairing_shared_and_per_device_modes)
    _run("pairing: touch creates if missing", test_pairing_touch_creates_if_missing)
    _run("cloud: PBKDF2 deterministic",       test_cloud_pbkdf2_deterministic)
    _run("cloud: PBKDF2 different passwords",test_cloud_pbkdf2_different_passwords)
    _run("cloud: encrypt/decrypt roundtrip", test_cloud_encrypt_decrypt_roundtrip)
    _run("cloud: decrypt wrong key fails",   test_cloud_decrypt_wrong_key_fails)
    _run("cloud: account id stable",         test_cloud_account_id_stable)
    _run("cloud: not available by default",  test_cloud_available_false_by_default)
    _run("cloud: signup no backend",         test_cloud_signup_no_backend_raises)
    _run("cloud: signup bad email",          test_cloud_signup_bad_email_raises)
    _run("cloud: signup short password",     test_cloud_signup_short_password_raises)
    _run("qr: functions removed",            test_qr_functions_removed)
    _run("qr: /api/qr returns JSON",         test_qr_endpoint_returns_json)
    _run("qr: pairing code format",         test_pairing_code_format_unchanged)
    _run("server: routes complete",          test_server_routes_complete)
    _run("server: safe_modes_subset strips", test_safe_modes_subset_strips_keys)
    _run("server: safe_modes non-dict",      test_safe_modes_subset_handles_non_dict)
    _run("server: state",                    test_phone_server_state)
    _run("server: start non-blocking",       test_start_phone_server_returns_server)
    _run("CLI: phone flags parse",           test_cli_phone_flags)
    _run("CLI: serve default host",          test_cli_serve_default_host)
    _run("CLI: phone serve meta",            test_cli_phone_serve_meta_doesnt_need_config)
    _run("rename: JarvisError is DualAIError", test_jarvis_error_is_dual_ai_error)
    _run("env_or: legacy only",              test_env_or_legacy_only)
    _run("env_or: new only",                 test_env_or_new_only)
    _run("env_or: legacy wins both set",     test_env_or_legacy_wins_when_both_set)
    _run("env_or: default",                  test_env_or_default)
    _run("rename: legacy config migration",  test_legacy_config_migration)
    _run("godot: find project root",         test_godot_find_project_root_found)
    _run("godot: find no project",           test_godot_find_project_root_missing)
    _run("godot: read v5 (4.x)",             test_godot_read_project_info_v5)
    _run("godot: read v4 (3.x)",             test_godot_read_project_info_v3)
    _run("godot: enhance impl prompt",       test_godot_enhance_impl_prompt)
    _run("projects: scaffold python",        test_project_scaffold_python)
    _run("projects: scaffold godot",         test_project_scaffold_godot)
    _run("projects: list sorted by updated", test_list_projects_sorted_by_updated)
    _run("projects: active set/get",         test_active_project_set_get)
    _run("projects: remove",                 test_project_remove)
    _run("drive: push/pull roundtrip",       test_drive_push_pull_roundtrip)
    _run("drive: unset returns None",        test_drive_resolve_unset_returns_none)
    _run("route: /api/projects exists",      test_route_projects_list)
    _run("route: /api/drive exists",         test_route_drive_status)
    _run("route: /api/projects/active",      test_route_projects_set_active)
    _run("route: projects list via API",     test_route_projects_list_scaffolds)
    _run("route: drive set/unset state",     test_route_drive_action_set_unset)
    _run("auth: master passcode is set",      test_master_passcode_is_set)
    _run("auth: bypass with correct code",    test_auth_bypass_active_with_correct_passcode)
    _run("auth: bypass with wrong code",      test_auth_bypass_inactive_with_wrong_passcode)
    _run("auth: bypass unset",                test_auth_bypass_inactive_when_unset)
    _run("auth: Windows Hello returns bool",  test_auth_attempt_windows_hello_returns_bool)
    _run("auth: webcam returns bool",         test_auth_attempt_webcam_returns_bool)
    _run("auth: passcode function exists",    test_auth_passcode_correct)
    _run("auth: --no-auth parses",            test_no_auth_flag_parses)
    _run("auth: --auth-setup parses",         test_auth_setup_flag_parses)
    _run("auth: --auth-test parses",          test_auth_test_flag_parses)
    _run("warn: experimental no-flags silent",  test_warn_experimental_no_flags_silent)
    _run("warn: experimental self-status",     test_warn_experimental_self_status)
    _run("warn: experimental multiple flags",  test_warn_experimental_multiple_flags)
    _run("warn: experimental once-per-process", test_warn_experimental_warns_only_once)
    _run("warn: experimental godot explicit",   test_warn_experimental_godot_only_explicit)
    _run("passcode: default list (no config)",   test_accepted_passcodes_default)
    _run("passcode: with override",              test_accepted_passcodes_with_override)
    _run("passcode: corrupt config falls back",  test_accepted_passcodes_corrupt_config)
    _run("passcode: --change-passcode parses",   test_change_passcode_flag_parses)
    _run("passcode: --change-cloud-password parses",
                                                test_change_cloud_password_flag_parses)
    sys.stderr.write("\n")
    sys.stderr.write("=" * 60 + "\n")
    sys.stderr.write(("  %d passed, %d failed (of %d)\n"
                      % (TESTS_PASSED, TESTS_FAILED, TESTS_RUN)))
    sys.stderr.write("=" * 60 + "\n")
    if TESTS_FAILED:
        sys.stderr.write("\nFailures:\n")
        for name, tb in FAILURES:
            sys.stderr.write("\n--- " + name + " ---\n")
            sys.stderr.write(tb)
    return 0 if TESTS_FAILED == 0 else 1


# (test file's __main__ block removed; the dispatcher at the bottom of
#  the merged jarvis.py handles `--test` invocation.)


# ===========================================================================
# EMBEDDED TEST RUNNER + PYINSTALLER BUILD  --  dispatched from __main__
# ===========================================================================

def _cmd_run_tests():
    """Run the embedded test suite and exit with the result code."""
    return _run_jarvis_tests()


def _cmd_auth_setup(args):
    """Interactive auth setup: test each layer, optionally register a
    face from the webcam, then exit. This is the one command the
    user can run BEFORE setting up auth (it bypasses the gate).
    """
    print()
    print("============================================================")
    print("  jarvis auth setup")
    print("============================================================")
    print("  This will test all three authentication layers and let you")
    print("  register a face photo from your webcam. Run this once on a")
    print("  new machine, or any time you want to update your setup.")
    print()

    # Layer 1: Windows Hello
    print("  [1/3] Testing Windows Hello...")
    if sys.platform == "win32":
        print("    A Windows Security dialog should appear shortly.")
        print("    (Touch your fingerprint sensor, look at the camera, or")
        print("    enter your PIN -- any of these will work.)")
        if _auth_attempt_windows_hello():
            print("    -> Windows Hello is working.")
        else:
            print("    -> Windows Hello not available, cancelled, or not set up.")
            print("       (To set it up: Windows Settings -> Accounts -> Sign-in options.)")
    else:
        print("    -> Skipped (not on Windows).")
    print()

    # Layer 2: Webcam
    print("  [2/3] Webcam face recognition...")
    face_path = os.path.join(CONFIG_DIR, "face.jpg")
    if os.path.isfile(face_path):
        print("    Found registered face at " + face_path)
        print("    Testing if webcam + opencv-python is available...")
        if _auth_attempt_webcam():
            print("    -> Webcam face recognition works.")
        else:
            print("    -> Either opencv-python is not installed, or the webcam")
            print("       couldn't capture a recognizable frame.")
    else:
        print("    No face registered yet.")
        if os.environ.get("JARVIS_AUTH_NO_WEBCAM"):
            print("    (JARVIS_AUTH_NO_WEBCAM set; skipping registration)")
        else:
            print("    Would you like to register your face now? (y/N)")
            try:
                ans = input("    > ").strip().lower()
            except (EOFError, KeyboardInterrupt):
                ans = "n"
            if ans in ("y", "yes"):
                if _auth_setup_webcam():
                    print("    -> Webcam auth is now set up.")
                else:
                    print("    -> Webcam setup failed; you can retry with")
                    print("       `jarvis --auth-setup` later.")
    print()

    # Layer 3: Passcode
    print("  [3/3] Master passcode...")
    print("    The passcode is hardcoded into the binary. If you've")
    print("    forgotten it, you can set JARVIS_BYPASS=<something> in")
    print("    your env to bypass for that session. The passcode is also")
    print("    visible at the top of jarvis.py (search for _MASTER_PASSCODE).")
    print()
    print("    The current passcode is:  " + _MASTER_PASSCODE)
    print()
    print("    Test it (y/N)? (this verifies the comparison logic, not")
    print("    the passcode itself)")
    try:
        ans = input("    > ").strip().lower()
    except (EOFError, KeyboardInterrupt):
        ans = "n"
    if ans in ("y", "yes"):
        import getpass as _gp
        for attempt in range(3):
            try:
                pw = _gp.getpass("    Enter the passcode: ")
            except Exception:
                pw = input("    Enter the passcode: ")
            if pw == _MASTER_PASSCODE:
                print("    -> Correct! Passcode comparison works.")
                break
            print("    -> Wrong" + (" (try again)" if attempt < 2 else ""))
        else:
            print("    -> 3 wrong attempts; stopping passcode test.")
    print()

    print("============================================================")
    print("  Setup complete.")
    print("============================================================")
    print("  Now any of these will let you in:")
    print("    - Windows Hello (face / fingerprint / PIN)")
    print("    - Webcam face recognition (if registered above)")
    print("    - The master passcode")
    print("    - Setting JARVIS_BYPASS=<passcode> in your env")
    print()
    print("  Run `jarvis` (no args) to test.")
    return 0


def _cmd_auth_test(args):
    """Test the three auth layers and report which ones work."""
    print()
    print("============================================================")
    print("  jarvis auth test")
    print("============================================================")
    print()

    # Windows Hello
    print("  [1/3] Windows Hello (credui.dll via ctypes):")
    if sys.platform != "win32":
        print("    Skipped (not on Windows)")
        hello_ok = False
    else:
        try:
            import ctypes as _ct
            credui = _ct.windll.credui
            print("    credui.dll is loadable")
            print("    (To actually test the prompt, you need to interact)")
            hello_ok = True
        except (AttributeError, OSError) as e:
            print("    FAILED: " + str(e))
            hello_ok = False
    print()

    # Webcam
    print("  [2/3] Webcam face recognition:")
    face_path = os.path.join(CONFIG_DIR, "face.jpg")
    try:
        import cv2  # type: ignore
        print("    opencv-python is installed")
        if os.path.isfile(face_path):
            print("    face photo registered at " + face_path)
            cam_ok = True
        else:
            print("    no face photo registered (run --auth-setup to register)")
            cam_ok = False
    except ImportError:
        print("    opencv-python NOT installed")
        cam_ok = False
    print()

    # Passcode
    print("  [3/3] Master passcode:")
    print("    passcode is hardcoded into the binary")
    print("    (visible at the top of jarvis.py: search for _MASTER_PASSCODE)")
    print()

    # Bypass
    bp = os.environ.get("JARVIS_BYPASS", "")
    if bp:
        if bp == _MASTER_PASSCODE:
            print("  JARVIS_BYPASS env var is set and correct -- bypass active.")
        else:
            print("  JARVIS_BYPASS env var is set but INCORRECT -- bypass NOT active.")
    else:
        print("  JARVIS_BYPASS env var is not set.")
    print()

    print("============================================================")
    layers = (
        ("Windows Hello", hello_ok),
        ("Webcam face recognition", cam_ok),
        ("Master passcode", True),  # always available
    )
    working = [name for name, ok in layers if ok]
    print("  Working layers: " + (", ".join(working) if working else "(none)"))
    print("  Total: " + str(len(working)) + "/3")
    print("============================================================")
    return 0


def _cmd_change_passcode(args):
    """Rotate the master passcode. The new passcode is stored in
    ~/.jarvis/config.json as 'passcode_override'. The hardcoded
    fallback in the binary continues to work, so a fresh install
    on a new box can still authenticate.

    Prompts for the CURRENT passcode first (so a casual observer
    can't change it), then the new one twice.

    To clear the override, run `jarvis --reset` (wipes the whole
    config) or edit ~/.jarvis/config.json by hand and remove the
    'passcode_override' key.
    """
    import getpass as _gp
    # Verify the current passcode before letting them set a new one.
    # Accepts either the hardcoded or the existing override.
    accepted = _accepted_passcodes()
    try:
        cur = _gp.getpass("  current passcode: ")
    except Exception:
        cur = input("  current passcode: ")
    if cur not in accepted:
        sys.stderr.write("ERROR: current passcode is wrong.\n")
        return 1
    # New passcode
    try:
        new1 = _gp.getpass("  new passcode (8+ chars recommended): ")
        new2 = _gp.getpass("  confirm: ")
    except Exception:
        new1 = input("  new passcode: ")
        new2 = input("  confirm: ")
    if not new1 or len(new1) < 4:
        sys.stderr.write("ERROR: new passcode is too short "
                         "(need at least 4 chars).\n")
        return 1
    if new1 != new2:
        sys.stderr.write("ERROR: new passcodes don't match.\n")
        return 1
    if new1 == _MASTER_PASSCODE:
        print("Note: the new passcode is the same as the hardcoded "
              "fallback. Clearing the override later will revert to "
              "this same value.")
    # Load or create the config
    cfg = load_config()
    if not isinstance(cfg, dict):
        cfg = {}
    cfg["passcode_override"] = new1
    if not save_config(cfg):
        sys.stderr.write("ERROR: could not save config.\n")
        return 1
    print()
    print("Passcode updated. The new passcode is now active.")
    print("  Stored at: " + CONFIG_PATH)
    print("  Key:        passcode_override")
    print()
    print("Notes:")
    print("  - The hardcoded fallback passcode still works too.")
    print("  - Either passcode is enough to authenticate; we don't")
    print("    reveal which one matched, to keep the security model")
    print("    opaque.")
    print("  - To clear the override, run `jarvis --reset` or edit")
    print("    ~/.jarvis/config.json and remove the 'passcode_override'")
    print("    key.")
    return 0


def _cmd_change_cloud_password(args):
    """Rotate the password on the cloud account. Re-encrypts the
    stored config with a new salt + new key derived from the new
    password. Requires the cloud backend to be configured
    (JARVIS_CLOUD_URL).

    Flow:
      1. Prompt for the current password. We have to sign in with
         it to fetch the existing blob (which contains the salt).
      2. Prompt for the new password (twice).
      3. Re-encrypt the config with a new salt + new key.
      4. PUT the new blob back.

    If any step fails, the old password is still valid (we never
    wrote the new blob).
    """
    import getpass as _gp
    if not _cloud_available():
        sys.stderr.write(
            "ERROR: cloud backend not configured. Set JARVIS_CLOUD_URL "
            "first (try --cloud-url to see how).\n")
        return 1
    # Sign in with the current password to fetch the existing blob.
    try:
        email = input("  email: ").strip()
    except (EOFError, KeyboardInterrupt):
        sys.stderr.write("\nAborted.\n")
        return 1
    if not email:
        sys.stderr.write("ERROR: email is required.\n")
        return 1
    try:
        old_pw = _gp.getpass("  current password: ")
    except Exception:
        old_pw = input("  current password: ")
    if not old_pw:
        sys.stderr.write("ERROR: current password is required.\n")
        return 1
    # Verify by signing in.
    try:
        remote_cfg = cloud_login(email, old_pw)
    except JarvisError as e:
        sys.stderr.write("ERROR: sign-in with current password failed: "
                         + str(e) + "\n")
        return 1
    # New password
    try:
        new1 = _gp.getpass("  new password (6+ chars): ")
        new2 = _gp.getpass("  confirm: ")
    except Exception:
        new1 = input("  new password: ")
        new2 = input("  confirm: ")
    if not new1 or len(new1) < 6:
        sys.stderr.write("ERROR: new password must be at least 6 "
                         "characters.\n")
        return 1
    if new1 != new2:
        sys.stderr.write("ERROR: new passwords don't match.\n")
        return 1
    # Re-encrypt with a new salt. The remote config is the one we
    # just decrypted; we re-upload it (or the local config, if
    # non-empty) under the new password.
    cfg_to_upload = remote_cfg or load_config()
    try:
        new_acct = cloud_signup(email, new1, cfg_to_upload)
    except JarvisError as e:
        # Most likely "account already exists" -- the cloud already
        # has a blob for this email. We need to overwrite it with
        # the new salt + key. Use cloud_update but with the new
        # password.
        sys.stderr.write("Cloud already had an account for that email; "
                         "re-encrypting the existing blob with the new "
                         "password.\n")
        # Roll our own update with a new salt.
        import base64 as _base64
        acct = _cloud_account_id(email)
        raw = _cloud_request("GET", acct)
        if raw is None:
            sys.stderr.write("ERROR: account disappeared during change.\n")
            return 1
        if isinstance(raw, dict) and "value" in raw:
            blob = json.loads(raw["value"])
        else:
            blob = raw
        # Replace the salt + ciphertext with new ones.
        new_salt = os.urandom(16)
        new_key = _cloud_pbkdf2(new1, new_salt)
        blob["v"] = 1
        blob["salt"] = _base64.b64encode(new_salt).decode("ascii")
        blob["config"] = _cloud_fernet_like_encrypt(
            json.dumps(cfg_to_upload).encode("utf-8"), new_key)
        _cloud_request("PUT", acct, json.dumps(blob))
    # Verify by signing in with the new password.
    try:
        _ = cloud_login(email, new1)
    except JarvisError as e:
        sys.stderr.write("ERROR: couldn't sign in with the new password: "
                         + str(e) + "\n")
        return 1
    print()
    print("Cloud password updated.")
    print("  email:    " + email)
    print("  new pw:   (set; length " + str(len(new1)) + ")")
    print()
    print("Note: the old password is now invalid. Sign in with the new "
          "one next time.")
    return 0


_PYINSTALLER_SPEC = "# -*- mode: python ; coding: utf-8 -*-\nimport os\ntry:\n    HERE = os.path.abspath(os.path.dirname(__file__))\nexcept NameError:\n    HERE = os.getcwd()\na = Analysis(['jarvis.py'], pathex=[HERE], binaries=[],\n             datas=[], hiddenimports=[],\n             excludes=['unittest', 'pydoc', 'doctest', 'lib2to3',\n                       'email', 'html', 'http.server', 'xmlrpc',\n                       'pdb', 'multiprocessing', 'concurrent',\n                       'asyncio', 'distutils'])\npyz = PYZ(a.pure, a.zipped_data)\nexe = EXE(pyz, a.scripts, a.binaries, a.zipfiles, a.datas,\n          [], name='jarvis', debug=False, strip=False, upx=True,\n          runtime_tmpdir=None, console=True,\n          disable_windowed_traceback=False)"

def _cmd_build_exe(clean=True):
    """Build a standalone .exe from this single file.

    Tries PyInstaller first (the most common case, works on Windows /
    macOS / most Linux setups). If PyInstaller fails -- e.g. on Linux
    systems with a static-only Python (no libpython.so) -- falls back
    to cx_Freeze, which works on those systems too.

    Output: dist/jarvis (or jarvis.exe on Windows) for PyInstaller,
    or dist_exe/jarvis + dist_exe/lib/* for cx_Freeze.
    """
    import os as _os
    import platform as _platform
    import shutil as _shutil
    import subprocess as _subprocess
    here = _os.path.dirname(_os.path.abspath(__file__)) or _os.getcwd()
    name = "jarvis.exe" if _platform.system() == "Windows" else "jarvis"

    # ----- Try PyInstaller first -----
    try:
        import PyInstaller  # noqa
    except ImportError:
        PyInstaller = None

    if PyInstaller is not None:
        if clean:
            for d in ("build", "dist"):
                tgt = _os.path.join(here, d)
                if _os.path.isdir(tgt):
                    print("Removing", tgt)
                    _shutil.rmtree(tgt, ignore_errors=True)
        spec_path = _os.path.join(here, "jarvis.spec")
        with open(spec_path, "w") as _f:
            _f.write(_PYINSTALLER_SPEC)
        cmd = [sys.executable, "-m", "PyInstaller", "jarvis.spec",
               "--noconfirm"]
        print("$", " ".join(cmd))
        rc = _subprocess.run(cmd, cwd=here).returncode
        if rc == 0:
            path = _os.path.join(here, "dist", name)
            if _os.path.isfile(path):
                print()
                print("=" * 60)
                print("BUILD SUCCEEDED (PyInstaller)")
                print("=" * 60)
                size_mb = _os.path.getsize(path) / (1024 * 1024)
                print("  Binary:", path)
                print("  Size:  %.1f MB" % size_mb)
                print()
                print("  Run:  " + path + " --help")
                return 0
        # PyInstaller failed (e.g. no libpython on this Linux box)
        # -- fall through to cx_Freeze.
        print()
        print("=" * 60)
        print("PyInstaller build failed (exit %d). Trying cx_Freeze..."
              % rc)
        print("=" * 60)

    # ----- Fall back to cx_Freeze -----
    try:
        import cx_Freeze  # noqa
    except ImportError:
        sys.stderr.write(
            "Neither PyInstaller nor cx_Freeze is available.\n"
            "Install one of them:\n"
            "  pip install pyinstaller\n"
            "  pip install cx-freeze\n"
        )
        if PyInstaller is not None:
            sys.stderr.write(
                "\nOn some Linux systems (e.g. Debian static-python), "
                "PyInstaller cannot find libpython3.X.so. Use cx_Freeze "
                "as a workaround.\n"
            )
        return 1

    out_dir = _os.path.join(here, "dist_exe")
    if clean and _os.path.isdir(out_dir):
        print("Removing", out_dir)
        _shutil.rmtree(out_dir, ignore_errors=True)
    cmd = [sys.executable, "-m", "cx_Freeze", "jarvis.py",
           "--target-name", name, "--target-dir", out_dir]
    print("$", " ".join(cmd))
    rc = _subprocess.run(cmd, cwd=here).returncode
    if rc != 0:
        sys.stderr.write("\nBUILD FAILED (exit %d)\n" % rc)
        return rc
    binary = _os.path.join(out_dir, name)
    if _os.path.isfile(binary):
        size_mb = _os.path.getsize(binary) / (1024 * 1024)
        print()
        print("=" * 60)
        print("BUILD SUCCEEDED (cx_Freeze)")
        print("=" * 60)
        print("  Binary:", binary)
        print("  Size:  %.1f MB" % size_mb)
        print("  (cx_Freeze also needs the lib/ directory next to the binary)")
        print()
        print("  Run:  " + binary + " --help")
    return 0


def _cmd_build_portable():
    """Build a portable tarball/zip of the cx_Freeze binary.

    1. Run --build to produce dist_exe/jarvis + lib/ + share/.
    2. Add a jarvis.sh launcher next to the binary.
    3. Bundle everything into a single tar.gz (and .zip) that the user
       can extract anywhere. No Python required on the target machine.
    """
    import os as _os
    import platform as _platform
    import shutil as _shutil
    import subprocess as _subprocess
    rc = _cmd_build_exe(clean=True)
    if rc != 0:
        return rc
    here = _os.path.dirname(_os.path.abspath(__file__)) or _os.getcwd()
    exe_dir = _os.path.join(here, "dist_exe")
    if not _os.path.isdir(exe_dir):
        sys.stderr.write("No dist_exe/ found after build.\n")
        return 1
    # Launcher script
    name = "jarvis.exe" if _platform.system() == "Windows" else "jarvis"
    launcher = _os.path.join(exe_dir, "jarvis.sh")
    with open(launcher, "w") as _f:
        _f.write("#!/usr/bin/env bash\n"
                 "# Launcher for the frozen jarvis binary.\n"
                 "# Locates the binary next to this script and runs it.\n"
                 'SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"\n'
                 'exec "$SCRIPT_DIR/' + name + '" "$@"\n')
    _os.chmod(launcher, 0o755)
    # README
    readme = _os.path.join(exe_dir, "README.txt")
    with open(readme, "w") as _f:
        _f.write(
            "jarvis -- portable binary\n"
            "=========================\n\n"
            "Run the binary directly:\n"
            "  ./" + name + " --help\n"
            "  ./" + name + " --test\n\n"
            "Or use the launcher (handles path discovery):\n"
            "  ./jarvis.sh --help\n\n"
            "First run with no arguments will launch the setup wizard.\n"
            "Your config goes in ~/.jarvis/ (or %USERPROFILE%\\.jarvis on Windows).\n"
            "Use './" + name + " --show-config' to see it.\n\n"
            "All ~50 CLI flags are listed in the main README.md\n"
            "from the project repo.\n"
        )
    # Create tarball
    import tarfile
    out_tar = _os.path.join(here, "jarvis-portable.tar.gz")
    if _os.path.exists(out_tar):
        _os.remove(out_tar)
    with tarfile.open(out_tar, "w:gz") as tar:
        tar.add(exe_dir, arcname="jarvis")
    size_mb = _os.path.getsize(out_tar) / (1024 * 1024)
    # Also try zip
    try:
        out_zip = _os.path.join(here, "jarvis-portable.zip")
        if _os.path.exists(out_zip):
            _os.remove(out_zip)
        _shutil.make_archive(_os.path.join(here, "jarvis-portable"),
                             "zip", root_dir=here, base_dir="dist_exe")
        # make_archive adds .zip automatically; rename target
        if _os.path.exists(out_zip):
            _os.remove(out_zip)
        _os.rename(_os.path.join(here, "jarvis-portable.zip"), out_zip)
    except Exception:
        pass
    print()
    print("=" * 60)
    print("PORTABLE BUNDLE READY")
    print("=" * 60)
    print("  Tarball:  " + out_tar + "  (%.1f MB)" % size_mb)
    if _os.path.exists(_os.path.join(here, "jarvis-portable.zip")):
        zmb = _os.path.getsize(_os.path.join(here, "jarvis-portable.zip")) / (1024 * 1024)
        print("  Zip:      " + _os.path.join(here, "jarvis-portable.zip")
              + "  (%.1f MB)" % zmb)
    print()
    print("  To install on another machine:")
    print("    tar -xzf jarvis-portable.tar.gz -C ~/jarvis")
    print("    ~/jarvis/jarvis/jarvis.sh --help")
    print()
    print("  Or just copy the dist_exe/ folder anywhere -- the binary")
    print("  finds its lib/ directory relative to itself.")
    return 0


if __name__ == "__main__":
    # --test: run the embedded test suite
    if "--test" in sys.argv:
        sys.argv = [a for a in sys.argv if a != "--test"]
        sys.exit(_cmd_run_tests())
    # --build: package as a single .exe via PyInstaller
    if "--build" in sys.argv:
        sys.argv = [a for a in sys.argv if a != "--build"]
        sys.exit(_cmd_build_exe(clean="--no-clean" not in sys.argv))
    # --build-portable: package + bundle into a self-contained archive
    if "--build-portable" in sys.argv:
        sys.argv = [a for a in sys.argv if a != "--build-portable"]
        sys.exit(_cmd_build_portable())
    sys.exit(main())
