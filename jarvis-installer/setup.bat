@echo off
chcp 65001 >nul 2>&1
REM ===========================================================================
REM Self-heal: if this file was saved as UTF-16 (PowerShell's
REM 'irm -OutFile' default), labels like :DONE can't be found and
REM the script dies with "label not found". Detect that here and
REM re-save the file as ASCII before doing anything else.
REM ===========================================================================
if exist "%~f0" (
    set "FIRST2="
    for /f "usebackq" %%B in (`powershell -NoProfile -ExecutionPolicy Bypass -Command "$b=[IO.File]::ReadAllBytes('%~f0'); '{0:x} {1:x}' -f $b[0],$b[1]"`) do set "FIRST2=%%B"
    if "!FIRST2!"=="ff fe" (
        echo ==^> Detected UTF-16 BOM in this script; converting to ASCII ...
        powershell -NoProfile -ExecutionPolicy Bypass -Command "$content = [IO.File]::ReadAllText('%~f0'); [IO.File]::WriteAllText('%~f0', $content, [System.Text.Encoding]::ASCII)"
        echo    Re-running setup.bat as ASCII ...
        call "%~f0" %*
        exit /b
    )
)
REM ===========================================================================
REM Smart-screen bypass: strip the "mark of the web" (Zone.Identifier)
REM from every file in this folder so windows doesn't fire smartscreen
REM warnings. The mark is an NTFS alternate data stream added by
REM browsers / explorer when files are downloaded. Removing it makes
REM the files look "local" to smartscreen.
REM
REM We do this in BOTH powershell (Unblock-File) and certutil (more
REM aggressive -- works on some windows builds where Unblock-File
REM silently does nothing on .bat files). If both fail, the user can
REM right-click each file -> Properties -> Unblock manually.
REM ===========================================================================
echo ==^> Stripping mark-of-the-web (so smartscreen stays silent) ...
powershell -NoProfile -ExecutionPolicy Bypass -Command "Get-ChildItem -Path '%~dp0' -Recurse -File -Force | ForEach-Object { try { Unblock-File -Path $_.FullName -ErrorAction SilentlyContinue } catch {} }" 2>nul
for /f "delims=" %%F in ('dir /b /s /a "%~dp0*" 2^>nul') do (
    if exist "%%F" (
        certutil -delstore "Mark of the Web" "%%F" >nul 2>&1
    )
)
echo    done.
echo.
REM ===========================================================================
REM setup.bat  --  one-click setup for jarvis on Windows.
REM
REM This is the SIMPLE installer. Just double-click it. It will:
REM   1. Find Python (or install it for you, no admin needed)
REM   2. pip install the two third-party deps (requests, opencv-python)
REM   3. Run the first-run auth setup wizard
REM   4. Open a chat window with jarvis
REM
REM You don't need a compiler, you don't need git, you don't need
REM to build anything. jarvis.py is the program; Python is the
REM runtime. We just make sure Python + the two pip packages are
REM present, then run jarvis.py directly.
REM
REM Folder layout (everything is in this one folder):
REM   setup.bat       this file (double-click me)
REM   jarvis.py       the jarvis program (~530KB, single file)
REM   docs\           user guides (README, CAPABILITIES, etc.) - read anytime
REM
REM Usage: download jarvis-installer.zip, extract it anywhere,
REM double-click setup.bat. That's the whole thing.
REM ===========================================================================

setlocal EnableExtensions EnableDelayedExpansion

set "SCRIPT_DIR=%~dp0"
set "JARVIS_PY=%SCRIPT_DIR%jarvis.py"

echo.
echo ============================================================
echo   jarvis setup
echo ============================================================
echo   1. Find or install Python
echo   2. Install two pip packages (requests, opencv-python)
echo   3. Walk you through auth (Windows Hello + webcam + passcode)
echo   4. Open jarvis
echo ============================================================
echo.
echo   If Windows shows a SmartScreen warning ("Windows protected
echo   your PC" or "Unrecognized app") when you double-click this
echo   file, click "More info" then "Run anyway". This is normal
echo   for unsigned .bat files downloaded from the internet.
echo.

REM ===========================================================================
REM Step 0: make sure jarvis.py is actually next to us.
REM ===========================================================================
if not exist "%JARVIS_PY%" (
    echo.
    echo   ERROR: jarvis.py is not next to setup.bat.
    echo.
    echo   This folder should contain:
    echo     setup.bat      (this file)
    echo     jarvis.py      (the program)
    echo     docs\          (optional reading)
    echo.
    echo   Did you extract the full zip? Re-download jarvis-installer.zip
    echo   and use "Extract All..." (right-click the zip).
    echo.
    pause
    exit /b 1
)

REM ===========================================================================
REM Step 1: find Python. Try py launcher first (works on all modern
REM Windows installs), then python, then python3. If none of them work,
REM download the official embeddable python and unpack it to
REM %USERPROFILE%\jarvis-python\ -- no admin, no PATH pollution.
REM ===========================================================================
echo ==^> Step 1: looking for Python ...
set "PYTHON="

REM Try the py launcher (preferred on Windows)
where py >nul 2>&1
if not errorlevel 1 (
    for /f "usebackq" %%V in (`py -3 -c "import sys; print(sys.executable)"`) do set "PYTHON=%%V"
)

REM Try plain python / python3
if "!PYTHON!"=="" (
    where python >nul 2>&1
    if not errorlevel 1 (
        for /f "usebackq" %%V in (`python -c "import sys; print(sys.executable)"`) do set "PYTHON=%%V"
    )
)
if "!PYTHON!"=="" (
    where python3 >nul 2>&1
    if not errorlevel 1 (
        for /f "usebackq" %%V in (`python3 -c "import sys; print(sys.executable)"`) do set "PYTHON=%%V"
    )
)

REM Found one
if not "!PYTHON!"=="" (
    echo    Found: !PYTHON!
    "!PYTHON!" --version
    echo.
    goto :PYTHON_OK
)

REM No python. Try to bootstrap with the official installer.
echo    No system Python found. Downloading the official embeddable
echo    Python to %USERPROFILE%\jarvis-python\ (no admin required) ...

set "PYTHON_DIR=%USERPROFILE%\jarvis-python"
set "PYTHON_ZIP=%TEMP%\jarvis-python-embed.zip"
set "PYTHON_URL=https://www.python.org/ftp/python/3.12.7/python-3.12.7-embed-amd64.zip"
set "GET_PIP_URL=https://bootstrap.pypa.io/get-pip.py"

REM Download the embeddable python (small, ~10MB)
powershell -NoProfile -ExecutionPolicy Bypass -Command "try { [Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12; Invoke-WebRequest -Uri '%PYTHON_URL%' -OutFile '%PYTHON_ZIP%' -UseBasicParsing -ErrorAction Stop } catch { exit 1 }"
if errorlevel 1 (
    echo.
    echo   ERROR: could not download Python from python.org.
    echo   Check your network connection and try again.
    echo.
    echo   Alternative: install Python 3.8+ from https://python.org/downloads/
    echo   then re-run setup.bat. (Make sure to check "Add Python to PATH".)
    echo.
    pause
    exit /b 1
)

REM Unpack it
if exist "%PYTHON_DIR%" rmdir /s /q "%PYTHON_DIR%" 2>nul
mkdir "%PYTHON_DIR%" 2>nul || (
    echo   ERROR: could not create %PYTHON_DIR%.
    pause
    exit /b 1
)
powershell -NoProfile -ExecutionPolicy Bypass -Command "Expand-Archive -Path '%PYTHON_ZIP%' -DestinationPath '%PYTHON_DIR%' -Force"
if errorlevel 1 (
    echo   ERROR: could not unpack Python.
    pause
    exit /b 1
)
del /q "%PYTHON_ZIP%" 2>nul

set "PYTHON=%PYTHON_DIR%\python.exe"
if not exist "%PYTHON%" (
    echo   ERROR: python.exe missing after unpack. Something went wrong.
    pause
    exit /b 1
)

REM The embeddable python needs python312._pth tweaked to enable site-packages.
REM We back up the original first.
set "PYTH_FILE=%PYTHON_DIR%\python312._pth"
if exist "%PYTH_FILE%" (
    findstr /C:"#import site" "%PYTH_FILE%" >nul 2>&1
    if not errorlevel 1 (
        REM Already commented out (uncommented); leave it.
        echo.
    ) else (
        REM Make a backup, then uncomment "import site" so pip can install.
        copy /Y "%PYTH_FILE%" "%PYTH_FILE%.bak" >nul
        powershell -NoProfile -ExecutionPolicy Bypass -Command "(Get-Content '%PYTH_FILE%') -replace '^#import site', 'import site' | Set-Content '%PYTH_FILE%'"
    )
)

REM Download get-pip.py and use it to bootstrap pip
echo    Bootstrapping pip ...
set "GET_PIP=%TEMP%\jarvis-get-pip.py"
powershell -NoProfile -ExecutionPolicy Bypass -Command "try { [Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12; Invoke-WebRequest -Uri '%GET_PIP_URL%' -OutFile '%GET_PIP%' -UseBasicParsing -ErrorAction Stop } catch { exit 1 }"
if errorlevel 1 (
    echo   ERROR: could not download get-pip.py.
    pause
    exit /b 1
)
"%PYTHON%" "%GET_PIP%" --no-warn-script-location
if errorlevel 1 (
    echo   ERROR: pip bootstrap failed.
    pause
    exit /b 1
)
del /q "%GET_PIP%" 2>nul

echo    Python installed at: %PYTHON%
"%PYTHON%" --version
echo.

:PYTHON_OK
REM ===========================================================================
REM Step 2: pip install the two third-party deps. requests is required.
REM opencv-python is optional (only needed for webcam face auth).
REM ===========================================================================
echo ==^> Step 2: installing pip packages ...
echo    (this is fast on a normal connection, ~30s for requests,
echo     ~60s for opencv-python)
echo.

"%PYTHON%" -m pip install --upgrade pip --quiet --disable-pip-version-check
if errorlevel 1 (
    echo   WARNING: pip self-upgrade failed (not fatal, continuing).
)

"%PYTHON%" -m pip install requests --quiet --disable-pip-version-check
if errorlevel 1 (
    echo.
    echo   ERROR: pip install requests failed.
    echo   Check your network connection and try again.
    echo.
    pause
    exit /b 1
)
echo    + requests installed

REM opencv-python is big (~50MB). Try it but don't fail if it doesn't install --
REM the user can still use Windows Hello + passcode auth without it.
"%PYTHON%" -m pip install opencv-python --quiet --disable-pip-version-check
if errorlevel 1 (
    echo    - opencv-python install failed or was skipped.
    echo      (webcam face auth won't work, but the rest is fine)
) else (
    echo    + opencv-python installed
)
echo.

REM ===========================================================================
REM Step 3: run the auth-setup wizard. This is a meta-command that
REM bypasses the auth gate (so you can run it on first install) and
REM walks you through all three layers.
REM ===========================================================================
echo ==^> Step 3: running jarvis --auth-setup ...
echo.
echo   You'll see a menu with three steps:
echo     [1/3] Windows Hello  --  face / fingerprint / PIN popup
echo     [2/3] Webcam face    --  registers a photo from your webcam
echo                              (skipped if opencv-python isn't installed)
echo     [3/3] Passcode       --  shows the hardcoded master passcode
echo.
echo   After this, every `jarvis` invocation will require auth.
echo   You can re-run this anytime with:   jarvis --auth-setup
echo.

"%PYTHON%" "%JARVIS_PY%" --auth-setup
set "AUTH_RC=%errorlevel%"

REM ===========================================================================
REM Done. Print the "what next" cheat sheet.
REM ===========================================================================
echo.
echo ============================================================
if "%AUTH_RC%"=="0" (
    echo   SETUP COMPLETE
) else (
    echo   SETUP RAN ^(exit %AUTH_RC% -- usually harmless^)
)
echo ============================================================
echo.
echo   What to do next:
echo.
echo     1. From any folder, type:
echo.
echo          "%PYTHON%" "%JARVIS_PY%"
echo.
echo        Or add this folder to your PATH so you can just type `jarvis`:
echo          set PATH=%SCRIPT_DIR%;%PATH%
echo.
echo     2. You can also double-click setup.bat again anytime to
echo        re-run the auth wizard.
echo.
echo   Quick reference:
echo     jarvis --help           list all flags
echo     jarvis --auth-test      see which auth layers work
echo     jarvis --no-auth        skip auth for this one run
echo     set JARVIS_BYPASS=...   env var to bypass auth
echo                             (must match the master passcode; the
echo                             auth wizard above printed it)
echo.
echo   User guides in the docs\ folder:
dir /b "%SCRIPT_DIR%docs" 2>nul
echo.
echo   If you ever forget the master passcode, it's at the top of
echo   jarvis.py: search for _MASTER_PASSCODE.
echo.
echo   Launching jarvis now ...
echo.

REM ===========================================================================
REM Step 4: launch jarvis in a new window so it stays open after setup closes.
REM ===========================================================================
start "jarvis" "%PYTHON%" "%JARVIS_PY%"
timeout /t 3 /nobreak >nul
exit /b 0
