#!/usr/bin/env python3
"""
build-installer.py -- rebuild the one-click installer zip from
canonical sources at the repo root.

Run from anywhere:
    python3 build-installer.py

Outputs:
    ../jarvis-installer.zip     (relative to this script)

The zip contains:
    setup.bat            the one-click installer
    jarvis.py            the program
    README_FIRST.txt     quick orientation
    docs/                user guides (CAPABILITIES, README, etc.)
"""
import os
import sys
import zipfile

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)            # the repo root
OUT  = os.path.join(ROOT, "jarvis-installer.zip")

# (arcname, source-on-disk) pairs
FILES = [
    ("run.cmd",              os.path.join(HERE, "run.cmd")),
    ("install.ps1",          os.path.join(HERE, "install.ps1")),
    ("setup.bat",            os.path.join(HERE, "setup.bat")),
    ("reset.bat",            os.path.join(HERE, "reset.bat")),
    ("jarvis.py",            os.path.join(ROOT, "jarvis", "jarvis.py")),
    ("README_FIRST.txt",     os.path.join(HERE, "README_FIRST.txt")),
    ("build-installer.py",   os.path.join(HERE, "build-installer.py")),
]
DOCS_SRC = os.path.join(ROOT, "DOCS_SRC")   # the docs we bundle
DOCS = [
    ("README.md",            os.path.join(ROOT, "README.md")),
    ("CAPABILITIES.md",      os.path.join(ROOT, "CAPABILITIES.md")),
    ("CHANGELOG.md",         os.path.join(ROOT, "CHANGELOG.md")),
    ("PASSWORDS.md",         os.path.join(ROOT, "PASSWORDS.md")),
    ("RELEASE.md",           os.path.join(ROOT, "RELEASE.md")),
    ("SIDE_EFFECTS.md",      os.path.join(ROOT, "SIDE_EFFECTS.md")),
]


def main():
    if os.path.isfile(OUT):
        os.remove(OUT)
    missing = [src for _, src in FILES + DOCS if not os.path.isfile(src)]
    if missing:
        print("ERROR: missing source files:")
        for m in missing:
            print("  " + m)
        sys.exit(1)
    n = 0
    with zipfile.ZipFile(OUT, "w", zipfile.ZIP_DEFLATED) as zf:
        for arc, src in FILES:
            zf.write(src, arcname=os.path.join("jarvis-installer", arc))
            n += 1
        for arc, src in DOCS:
            zf.write(src, arcname=os.path.join("jarvis-installer", "docs", arc))
            n += 1
    size_kb = os.path.getsize(OUT) / 1024.0
    print("Wrote " + OUT + "  (%.1f KB, %d files)" % (size_kb, n))


if __name__ == "__main__":
    main()
