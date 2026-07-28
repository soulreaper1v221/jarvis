#!/usr/bin/env bash
# build.sh -- one-shot build script for the jarvis portable exe.
#
# What it does:
#   1. Makes sure pip + requests are installed.
#   2. Tries to install pyinstaller (Windows / macOS / most Linux).
#   3. Tries to install cx-freeze (fallback for Linux systems where
#      PyInstaller can't find libpython3.X.so).
#   4. Runs `python3 jarvis.py --build-portable` from the jarvis/ subdir.
#   5. Prints the final tarball path so you can copy/download it.
#
# Usage:
#   ./build.sh             # full build, cleans previous output
#   ./build.sh --no-clean  # incremental build
#
# Output:
#   jarvis/jarvis-portable.tar.gz   (~22 MB) -- self-extracting archive
#   jarvis/dist_exe/                (folder, also usable directly)

set -e

# Resolve the directory of this script (following symlinks)
SCRIPT_PATH="${BASH_SOURCE[0]}"
while [ -L "$SCRIPT_PATH" ]; do
    SCRIPT_DIR=$(dirname "$SCRIPT_PATH")
    SCRIPT_PATH=$(readlink "$SCRIPT_PATH")
    [[ "$SCRIPT_PATH" != /* ]] && SCRIPT_PATH="$SCRIPT_DIR/$SCRIPT_PATH"
done
SCRIPT_DIR=$(cd "$(dirname "$SCRIPT_PATH")" && pwd)
JARVIS_DIR="$SCRIPT_DIR/jarvis"

# Sanity check
if [ ! -f "$JARVIS_DIR/jarvis.py" ]; then
    echo "ERROR: jarvis.py not found at $JARVIS_DIR/jarvis.py"
    echo "Make sure this build.sh is in the repo root, next to the jarvis/ folder."
    exit 1
fi

# Make sure pip is available
if ! command -v pip3 >/dev/null 2>&1; then
    if ! command -v pip >/dev/null 2>&1; then
        echo "ERROR: pip is not installed. Install it with:"
        echo "  python3 -m ensurepip --upgrade"
        echo "or:"
        echo "  sudo apt install python3-pip     (Debian/Ubuntu)"
        echo "  brew install python3             (macOS)"
        exit 1
    fi
    PIP=pip
else
    PIP=pip3
fi

# Detect --break-system-packages flag (PEP 668 on Debian 12+ / Ubuntu 23+)
PIP_FLAGS=""
# pip only supports --break-system-packages on 23.0.1+
if $PIP install --help 2>&1 | grep -q "break-system-packages"; then
    # Only add the flag if we're on a system-managed Python (e.g.
    # apt-installed, where the prefix is /usr and pip lives in
    # /usr/lib/python3/dist-packages).
    PIP_Prefix=$(python3 -c "import sys; print(sys.prefix)" 2>/dev/null || echo "")
    if [ "$PIP_Prefix" = "/usr" ] || [ "$PIP_Prefix" = "/usr/local" ]; then
        PIP_FLAGS="--break-system-packages"
    fi
fi

# Use python3 -m pip if direct pip is missing on some systems
run_pip() {
    if command -v $PIP >/dev/null 2>&1; then
        $PIP install $PIP_FLAGS "$@"
    else
        python3 -m pip install $PIP_FLAGS "$@"
    fi
}

# Install build deps
echo "==> Installing build dependencies..."
run_pip --quiet --upgrade pip
run_pip --quiet requests
run_pip --quiet pyinstaller || true
run_pip --quiet cx-freeze || true

# Make sure pip-installed scripts are on PATH
export PATH="$HOME/.local/bin:$PATH"

# Run the build
echo "==> Building portable jarvis..."
cd "$JARVIS_DIR"
python3 jarvis.py --build-portable "$@"

# Find the tarball
TARBALL="$JARVIS_DIR/jarvis-portable.tar.gz"
if [ -f "$TARBALL" ]; then
    SIZE_MB=$(du -m "$TARBALL" | cut -f1)
    echo ""
    echo "============================================================"
    echo " BUILD COMPLETE"
    echo "============================================================"
    echo "  Tarball:  $TARBALL"
    echo "  Size:     ${SIZE_MB} MB"
    echo ""
    echo "  Install on another machine:"
    echo "    tar -xzf \"$TARBALL\" -C ~/jarvis"
    echo "    ~/jarvis/jarvis/jarvis.sh --help"
    echo ""
    echo "  Or just copy the dist_exe/ folder directly:"
    echo "    $JARVIS_DIR/dist_exe/"
    echo "    (run ./jarvis.sh from inside)"
else
    echo ""
    echo "WARNING: Build finished but jarvis-portable.tar.gz not found."
    echo "Check the output above for errors."
    exit 1
fi
