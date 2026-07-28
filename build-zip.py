#!/usr/bin/env python3
"""
build-zip.py -- construct jarvis-windows.zip from the canonical
sources in this repo. Run this whenever you change jarvis.py or any
of the build/setup scripts and want to update the zip.

Layout produced (when extracted, the user gets this folder tree):
    README.md
    setup.bat                       <-- top-level click-to-setup
    jarvis/
        jarvis.py
        jarvis.sh
        README.md
        setup.bat                   <-- same content as root
        install.bat                 <-- from-source installer
        build.bat                   <-- build only
        build.sh
        install.sh
        quick-build.bat
        clean-reinstall.bat

Why two copies of setup.bat (root + jarvis/): so the user sees it
no matter where they look first. The path-discovery logic in
setup.bat works from either location.

No pre-built jarvis.exe is included. The setup.bat is smart: it
checks for a pre-built binary first, and if none exists, calls
install.bat to download Python + build from source. So the zip
is small (158 KB) and works on any Windows machine with internet.

Usage:
    python3 build-zip.py
"""

import os
import sys
import zipfile

HERE = os.path.dirname(os.path.abspath(__file__)) or os.getcwd()
OUT_ZIP = os.path.join(HERE, "jarvis-windows.zip")

# Top-level files
ROOT_FILES = [
    "README.md",
    "setup.bat",
]
# Files inside jarvis/ (built from the canonical root versions)
JARVIS_FILES = [
    # Source (lives in jarvis/ already)
    ("jarvis/jarvis.py", "jarvis/jarvis.py"),
    ("jarvis/jarvis.sh", "jarvis/jarvis.sh"),
    ("jarvis/README.md", "jarvis/README.md"),
    # setup.bat -- copied from root (so it works whether extracted
    # at top level or inside jarvis/)
    ("setup.bat", "jarvis/setup.bat"),
    # Build scripts -- copied from root
    ("install.bat", "jarvis/install.bat"),
    ("build.bat", "jarvis/build.bat"),
    ("build.sh", "jarvis/build.sh"),
    ("install.sh", "jarvis/install.sh"),
    ("quick-build.bat", "jarvis/quick-build.bat"),
    ("clean-reinstall.bat", "jarvis/clean-reinstall.bat"),
]


def add_file(zf, fs_path, arcname):
    if not os.path.isfile(fs_path):
        sys.stderr.write("ERROR: missing file %s\n" % fs_path)
        sys.exit(1)
    with open(fs_path, "rb") as f:
        data = f.read()
    zi = zipfile.ZipInfo(arcname)
    zi.compress_type = zipfile.ZIP_DEFLATED
    zi.external_attr = 0o644 << 16
    zf.writestr(zi, data)
    print("  + %-30s  %8d bytes  <-  %s" % (arcname, len(data), fs_path))


def main():
    if os.path.exists(OUT_ZIP):
        os.remove(OUT_ZIP)
    print("Building jarvis-windows.zip from %s ..." % HERE)
    print()
    with zipfile.ZipFile(OUT_ZIP, "w", compresslevel=6) as zf:
        print("Top-level files:")
        for fn in ROOT_FILES:
            add_file(zf, os.path.join(HERE, fn), fn)
        print()
        print("jarvis/ files (built from canonical sources):")
        for src_rel, arcname in JARVIS_FILES:
            add_file(zf, os.path.join(HERE, src_rel), arcname)
    size = os.path.getsize(OUT_ZIP)
    print()
    print("Built: %s  (%d bytes / %.1f KB)" % (
        OUT_ZIP, size, size / 1024.0))
    # Sanity verify
    with zipfile.ZipFile(OUT_ZIP) as zf:
        names = set(zf.namelist())
        expected = set(ROOT_FILES) | {arc for _, arc in JARVIS_FILES}
        missing = expected - names
        if missing:
            sys.stderr.write("MISSING: %s\n" % missing)
            sys.exit(1)
        bad = [n for n in names if n.startswith("jarvis/dist_exe/")
               or n.endswith(".so") or n.endswith(".so.0.0.0")]
        if bad:
            sys.stderr.write("LEFTOVER LINUX ARTIFACTS: %s\n" % bad)
            sys.exit(1)
    print("Verification: OK (all %d expected files, no linux artifacts)"
          % len(expected))


if __name__ == "__main__":
    main()
