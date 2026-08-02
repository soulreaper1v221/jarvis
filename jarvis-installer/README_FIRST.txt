jarvis v1.0.3 -- windows installer
====================================

this is the only folder you need.

  1. double-click run.cmd         (the friendly one, recommended)
     or if that gives you trouble:
     double-click install.ps1     (the powershell wrapper, same thing)
     or as a last resort:
     double-click setup.bat       (the bare installer, may show smartscreen)

  2. wait ~1 minute. setup will:
       - find your existing python (or download embeddable python,
         no admin needed)
       - pip install requests (required) and opencv-python (optional)
       - run the auth wizard so you can test windows hello,
         register your face, and see the master passcode
       - launch jarvis in a new window

  3. done. jarvis is running.

folder contents:
  run.cmd            THE one to double-click (recommended)
  install.ps1        powershell wrapper that strips the "downloaded
                     from the internet" marker so smartscreen
                     doesn't fire
  setup.bat          the bare installer (run this directly if the
                     above two don't work; may show smartscreen)
  jarvis.py          the program (~530KB, single file, no build)
  docs\              user guides -- open any .md in notepad to read

why the three-file setup?  smartscreen flags .bat files downloaded
from the internet with a scary "windows protected your pc" prompt.
the .ps1 wrapper gets around that by stripping the "mark of the web"
(mark of the web = the NTFS alternate data stream that windows
adds to files on download). once stripped, smartscreen stays quiet
and run.cmd just runs.

if you EVER see the smartscreen warning:
  - click "more info" (small text below the warning)
  - click "run anyway"
  - it will not appear again on this machine for this file

after setup runs once, you can launch jarvis anytime by:
  - double-clicking run.cmd (or setup.bat) again
  - or opening a cmd window and running:  python jarvis.py
  - or if you put this folder on your PATH: just `jarvis`

for the full feature list see docs\CAPABILITIES.md
for what every flag does see docs\README.md
for what's new in v1.0 see docs\CHANGELOG.md
for the master passcode location, see docs\PASSWORDS.md
for what each command does behind the scenes, see docs\SIDE_EFFECTS.md
