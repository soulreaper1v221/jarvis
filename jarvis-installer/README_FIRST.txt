jarvis v1.0.5 -- windows installer
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

to uninstall + reinstall (nuke and pave):
  double-click reset.bat
    - it asks "type YES to continue"
    - it kills any running jarvis, deletes the installer folder,
      your user data (~/.jarvis/), the embeddable python, and the
      old exe install (if any)
    - then it re-downloads the latest jarvis-installer.zip from
      github and runs the installer
    - WARNING: this wipes your api key, passcode override, projects,
      deep-research sessions, and paired phones. full clean slate.
      if you want to keep anything, copy ~/.jarvis/ somewhere first.

folder contents:
  run.cmd            THE one to double-click (recommended)
  install.ps1        powershell wrapper that strips the "downloaded
                     from the internet" marker so smartscreen
                     doesn't fire
  setup.bat          the bare installer (run this directly if the
                     above two don't work; may show smartscreen)
  reset.bat          uninstall + reinstall (use this if jarvis is
                     broken and you want a clean slate)
  jarvis.py          the program (~530KB, single file, no build)
  docs\              user guides -- open any .md in notepad to read

why the multi-file setup?  smartscreen flags .bat files downloaded
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
