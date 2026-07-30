jarvis v1.0.1 -- windows installer
====================================

this is the only folder you need.

  1. make sure jarvis.py is next to setup.bat (it should be)
  2. double-click setup.bat
  3. wait ~1 minute (downloads python if you don't have it,
     installs two pip packages, then runs the auth wizard)
  4. done. jarvis will open.

folder contents:
  setup.bat       the one file you double-click
  jarvis.py       the program (~530KB, single file, no build needed)
  docs\           user guides -- open any .md in notepad to read
  README_FIRST.txt  this file

what setup.bat does, in order:
  1. finds your existing python (or downloads the official embeddable
     one to %userprofile%\jarvis-python\, no admin needed)
  2. pip installs two packages: requests (required) and opencv-python
     (optional, for webcam face auth)
  3. runs `python jarvis.py --auth-setup` so you can test windows
     hello, register your face, and see the master passcode
  4. launches jarvis in a new window

after setup runs once, you can launch jarvis anytime by either:
  - double-clicking setup.bat again
  - or opening a cmd window and running:  python jarvis.py
  - or if you put this folder on your PATH: just `jarvis`

for the full feature list see docs\CAPABILITIES.md
for what every flag does see docs\README.md
for what's new in v1.0 see docs\CHANGELOG.md
for the master passcode location, see docs\PASSWORDS.md
for what each command does behind the scenes, see docs\SIDE_EFFECTS.md

smart-screen warning: if windows asks "windows protected your pc"
or "unrecognized app" when you double-click setup.bat, click
"more info" then "run anyway". this is normal for unsigned .bat
files downloaded from the internet.
