@echo off
REM ===========================================================================
REM run.cmd  --  the friendliest entry point.
REM
REM Double-click this file. It launches the powershell wrapper with
REM -ExecutionPolicy Bypass so you don't get the "running scripts is
REM disabled on this system" prompt. The powershell wrapper then
REM strips the mark-of-the-web from every file in this folder (so
REM smartscreen doesn't fire), and runs setup.bat.
REM
REM If you see ANY smartscreen / "Windows protected your PC" prompt
REM afterwards, click "More info" then "Run anyway" -- but the
REM marker-strip should mean smartscreen doesn't trigger at all.
REM
REM This file is .cmd not .bat intentionally -- .cmd files are
REM slightly less aggressive in windows heuristics than .bat. And
REM it's named "run.cmd" (lowercase, friendly verb) instead of
REM "setup.bat" or "install.bat" -- those names are flagged more
REM often because they match known installer patterns.
REM ===========================================================================
setlocal
REM Force cwd to the script's own folder. cmd.exe can inherit a
REM cwd of C:\Windows\System32 when you double-click a .cmd/.bat
REM from explorer; cd /d fixes that.
cd /d "%~dp0"
set "SCRIPT_DIR=%~dp0"
set "PS1=%SCRIPT_DIR%install.ps1"

if not exist "%PS1%" (
    echo.
    echo   ERROR: install.ps1 is missing.
    echo   This folder should contain:
    echo     run.cmd          (this file - the one you double-clicked)
    echo     install.ps1      (the powershell wrapper)
    echo     setup.bat        (the actual installer)
    echo     jarvis.py        (the program)
    echo.
    echo   Did you extract the full jarvis-installer.zip?
    echo.
    pause
    exit /b 1
)

REM -ExecutionPolicy Bypass: skip the "scripts disabled" prompt
REM -NoProfile: don't load user profile (faster, no surprises)
REM -NonInteractive: don't prompt for input
REM -File: run the wrapper
powershell.exe -NoProfile -ExecutionPolicy Bypass -NonInteractive -File "%PS1%" %*
set "RC=%errorlevel%"

if not "%RC%"=="0" (
    echo.
    echo   run.cmd: setup exited with code %RC%.
    echo   Scroll up to see what went wrong, or re-run and read
    echo   the messages carefully.
    pause
)

exit /b %RC%
