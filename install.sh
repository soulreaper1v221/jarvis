#!/usr/bin/env bash
# install.sh -- one-shot installer: clone, build, install the jarvis
# portable exe into a known location.
#
# What it does:
#   1. Clones the jarvis repo into $JARVIS_INSTALL_DIR (default: ~/jarvis-app)
#   2. Runs ./build.sh inside the clone (installs deps, builds the
#      portable tarball + binary)
#   3. Extracts the tarball into $JARVIS_EXE_DIR (default: ~/jarvis-exe)
#   4. Optionally adds $JARVIS_EXE_DIR to your PATH in ~/.bashrc /
#      ~/.zshrc / ~/.profile (so you can run `jarvis` from anywhere)
#   5. Prints the path to the final jarvis binary
#
# Usage:
#   ./install.sh                    # default: ~/jarvis-app + ~/jarvis-exe
#   JARVIS_INSTALL_DIR=/opt/src ./install.sh
#   JARVIS_EXE_DIR=/opt/jarvis ./install.sh
#   NO_PATH_UPDATE=1 ./install.sh   # don't touch shell rc files
#
# After this finishes, `jarvis --help` works from any directory.

set -e

# ---- Configuration ----
REPO_URL="${JARVIS_REPO_URL:-https://github.com/soulreaper1v221/jarvis.git}"
REPO_BRANCH="${JARVIS_REPO_BRANCH:-main}"
INSTALL_DIR="${JARVIS_INSTALL_DIR:-$HOME/jarvis-app}"
EXE_DIR="${JARVIS_EXE_DIR:-$HOME/jarvis-exe}"

echo "============================================================"
echo " jarvis one-shot installer"
echo "============================================================"
echo "  Repo:      $REPO_URL @ $REPO_BRANCH"
echo "  Clone to:  $INSTALL_DIR"
echo "  Install:   $EXE_DIR"
echo ""

# ---- Step 1: clone or update the repo ----
if [ -d "$INSTALL_DIR/.git" ]; then
    echo "==> Repo already exists at $INSTALL_DIR; pulling latest..."
    cd "$INSTALL_DIR"
    git fetch origin
    git reset --hard "origin/$REPO_BRANCH"
else
    echo "==> Cloning $REPO_URL..."
    git clone --depth 1 --branch "$REPO_BRANCH" "$REPO_URL" "$INSTALL_DIR"
fi

# ---- Step 2: build ----
echo ""
echo "==> Building..."
cd "$INSTALL_DIR"
./build.sh

# ---- Step 3: extract portable tarball to EXE_DIR ----
TARBALL="$INSTALL_DIR/jarvis/jarvis-portable.tar.gz"
if [ ! -f "$TARBALL" ]; then
    echo "ERROR: expected tarball at $TARBALL but it wasn't produced."
    echo "Check the build output above for errors."
    exit 1
fi

echo ""
echo "==> Installing to $EXE_DIR..."
rm -rf "$EXE_DIR"
mkdir -p "$EXE_DIR"
tar -xzf "$TARBALL" -C "$EXE_DIR"
chmod +x "$EXE_DIR/jarvis/jarvis" "$EXE_DIR/jarvis/jarvis.sh"

# ---- Step 4: add to PATH ----
if [ "${NO_PATH_UPDATE:-0}" != "1" ]; then
    PATH_LINE="export PATH=\"$EXE_DIR/jarvis:\$PATH\""
    for rc in "$HOME/.bashrc" "$HOME/.zshrc" "$HOME/.profile"; do
        if [ -f "$rc" ] || [ "$rc" = "$HOME/.profile" ]; then
            if ! grep -qF "$EXE_DIR/jarvis" "$rc" 2>/dev/null; then
                echo "" >> "$rc"
                echo "# jarvis (added by install.sh)" >> "$rc"
                echo "$PATH_LINE" >> "$rc"
                echo "  -> added to $rc"
            fi
        fi
    done
fi

# ---- Step 5: verify ----
BINARY="$EXE_DIR/jarvis/jarvis"
echo ""
echo "============================================================"
echo " INSTALL COMPLETE"
echo "============================================================"
echo "  Binary:  $BINARY"
echo "  Launcher: $EXE_DIR/jarvis/jarvis.sh"
echo "  Size:    $(du -sh "$BINARY" | cut -f1)"
echo ""
echo "  Test it:  $BINARY --help"
echo ""
if [ "${NO_PATH_UPDATE:-0}" != "1" ]; then
    echo "  PATH updated. Run one of these to refresh your shell:"
    echo "    source ~/.bashrc   (bash)"
    echo "    source ~/.zshrc    (zsh)"
    echo "    exec \$SHELL        (any)"
    echo ""
    echo "  Then:    jarvis --help"
else
    echo "  Add $EXE_DIR/jarvis to your PATH manually to run 'jarvis' from anywhere."
fi
