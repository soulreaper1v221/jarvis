#!/usr/bin/env bash
# Launcher for the frozen jarvis binary.
#
# Use this after running `python3 jarvis.py --build`. It locates the
# binary next to itself and runs it, so the user doesn't need to know
# about the lib/ folder.
#
# This file is shipped in the source tree so you can `cp jarvis.sh
# next-to-the-binary/` after building. The --build-portable command
# also drops a copy of this script into dist_exe/ automatically.
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
exec "$SCRIPT_DIR/jarvis" "$@"
