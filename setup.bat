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
REM setup.bat -- one-click setup for jarvis on Windows.
REM
REM What it does, in order:
REM   1. Locates the jarvis source folder (jarvis.py must be there
REM      or in a jarvis\ subfolder of this script's dir).
REM   2. Looks for a PRE-BUILT jarvis.exe in either dist\jarvis.exe
REM      (PyInstaller) or dist_exe\jarvis.exe (cx_Freeze). If found,
REM      uses it directly -- no Python install required.
REM   3. If no pre-built binary, calls install.bat to download
REM      Python (if needed) and build the binary from source.
REM   4. Copies the binary + its lib/ tree to %USERPROFILE%\jarvis-exe\
REM   5. Adds %USERPROFILE%\jarvis-exe to the user PATH (idempotent).
REM   6. Runs `jarvis --auth-setup` so the user can configure
REM      Windows Hello, the webcam face, and test the master passcode.
REM   7. Pauses with a "what to do next" checklist.
REM
REM Usage: just double-click this file from inside the extracted
REM jarvis-windows.zip folder. No admin rights needed.
REM
REM This is the "click to set it up" entry point. For a from-source
REM build with all the bells and whistles (portable git + python
REM download, fallback to cloning from GitHub, etc), use install.bat
REM instead. setup.bat is simpler: it just uses whatever's in the
REM current folder.
REM ===========================================================================

setlocal EnableExtensions EnableDelayedExpansion

set "EXE_DIR=%USERPROFILE%\jarvis-exe"
set "JARVIS_EXE_NAME=jarvis.exe"

echo.
echo ============================================================
echo   jarvis setup
echo ============================================================
echo   1. Find jarvis source + check for a pre-built binary
echo   2. If no pre-built binary, build from source
echo   3. Copy the binary to %EXE_DIR%
echo   4. Add %EXE_DIR% to your user PATH
echo   5. Walk you through auth (Windows Hello + webcam + passcode)
echo ============================================================
echo.
echo   If Windows shows a SmartScreen warning ("Windows protected
echo   your PC" or "Unrecognized app") when you double-click this
echo   file, click "More info" then "Run anyway". This is normal
echo   for unsigned .bat files downloaded from the internet.
echo.

REM ===========================================================================
REM Step 1: locate the jarvis source folder. jarvis.py must be in the
REM current dir, in a jarvis\ subfolder, or in a parent dir.
REM ===========================================================================
echo ==^> Locating jarvis source folder ...

set "JARVIS_ROOT="
set "SCRIPT_DIR=%~dp0"

REM Case 1: jarvis.py is in the script's dir
if exist "%SCRIPT_DIR%jarvis.py" (
    set "JARVIS_ROOT=%SCRIPT_DIR%."
    goto :SOURCE_FOUND
)
REM Case 2: jarvis\jarvis.py is in the script's dir
if exist "%SCRIPT_DIR%jarvis\jarvis.py" (
    set "JARVIS_ROOT=%SCRIPT_DIR%jarvis"
    goto :SOURCE_FOUND
)
REM Case 3: walk up
set "CANDIDATE=%SCRIPT_DIR%"
set "LEVELS=0"
:WALK_UP_SOURCE
if exist "%CANDIDATE%jarvis.py" (
    set "JARVIS_ROOT=%CANDIDATE%."
    goto :SOURCE_FOUND
)
if exist "%CANDIDATE%jarvis\jarvis.py" (
    set "JARVIS_ROOT=%CANDIDATE%jarvis"
    goto :SOURCE_FOUND
)
set /a LEVELS+=1
if !LEVELS! geq 5 goto :WALK_UP_SOURCE_DONE
for %%P in ("%CANDIDATE%") do set "CANDIDATE=%%~dpP"
if "%CANDIDATE:~-1%"=="\" set "CANDIDATE=%CANDIDATE:~0,-1%"
goto :WALK_UP_SOURCE
:WALK_UP_SOURCE_DONE

echo.
echo   ERROR: could not find jarvis.py.
echo.
echo   This script needs the jarvis source next to it. The zip
echo   should contain:
echo     jarvis\setup.bat            (this file)
echo     jarvis\jarvis.py            (the source)
echo     jarvis\jarvis.sh            (bash launcher, optional on Windows)
echo     jarvis\README.md            (feature reference)
echo.
echo   Did you extract the full zip? Try:
echo     1. Re-download jarvis-windows.zip
echo     2. Right-click -^> Extract All... (don't just double-click)
echo     3. Open the extracted jarvis\ folder
echo     4. Double-click setup.bat
echo.
pause
exit /b 1

:SOURCE_FOUND
echo    Found source: %JARVIS_ROOT%
echo.

REM ===========================================================================
REM Step 2: look for a pre-built binary. Two possible locations:
REM   - dist\jarvis.exe     (PyInstaller single-file or one-dir)
REM   - dist_exe\jarvis.exe (cx_Freeze, the default in jarvis.py)
REM If we find one, use it. If not, run install.bat to build.
REM ===========================================================================
echo ==^> Looking for a pre-built jarvis.exe ...

set "PREBUILT="
set "BUILT_DIR="

REM PyInstaller default: dist\jarvis.exe (or just dist\jarvis on non-Windows)
if exist "%JARVIS_ROOT%\dist\jarvis.exe" (
    set "BUILT_DIR=%JARVIS_ROOT%\dist"
    set "PREBUILT=%JARVIS_ROOT%\dist\jarvis.exe"
    goto :PREBUILT_FOUND
)

REM cx_Freeze default: dist_exe\jarvis.exe
if exist "%JARVIS_ROOT%\dist_exe\jarvis.exe" (
    set "BUILT_DIR=%JARVIS_ROOT%\dist_exe"
    set "PREBUILT=%JARVIS_ROOT%\dist_exe\jarvis.exe"
    goto :PREBUILT_FOUND
)

REM No prebuilt. Delegate to install.bat which handles the full
REM build flow (downloads Python if needed, installs deps, runs
REM build.bat). After install.bat finishes, dist_exe\jarvis.exe
REM should exist.
echo    No pre-built binary found.
echo.
echo ==^> Delegating to install.bat to build from source ...
echo    (this downloads Python if you don't have it, then builds)
echo.

REM Find install.bat
set "INSTALL_BAT="
if exist "%JARVIS_ROOT%\install.bat" set "INSTALL_BAT=%JARVIS_ROOT%\install.bat"
if exist "%JARVIS_ROOT%\..\install.bat" set "INSTALL_BAT=%JARVIS_ROOT%\..\install.bat"
if "%INSTALL_BAT%"=="" (
    REM Try a few more spots
    if exist "%~dp0install.bat" set "INSTALL_BAT=%~dp0install.bat"
    if exist "%~dp0..\install.bat" set "INSTALL_BAT=%~dp0..\install.bat"
)
if "%INSTALL_BAT%"=="" (
    echo   ERROR: install.bat not found in the zip. Expected it at:
    echo     %JARVIS_ROOT%\install.bat
    echo   or
    echo     %JARVIS_ROOT%\..\install.bat
    echo.
    echo   Your zip may be incomplete. Re-download jarvis-windows.zip.
    pause
    exit /b 1
)

echo    Found install.bat: %INSTALL_BAT%
call "%INSTALL_BAT%"
if errorlevel 1 (
    echo.
    echo   install.bat failed. See output above.
    echo   If it complained about Python download issues, check your
    echo   network connection and try again.
    pause
    exit /b 1
)

REM After install.bat, the binary should be at dist_exe\jarvis.exe
if exist "%JARVIS_ROOT%\dist_exe\jarvis.exe" (
    set "BUILT_DIR=%JARVIS_ROOT%\dist_exe"
    set "PREBUILT=%JARVIS_ROOT%\dist_exe\jarvis.exe"
    goto :PREBUILT_FOUND
)
if exist "%JARVIS_ROOT%\dist\jarvis.exe" (
    set "BUILT_DIR=%JARVIS_ROOT%\dist"
    set "PREBUILT=%JARVIS_ROOT%\dist\jarvis.exe"
    goto :PREBUILT_FOUND
)

echo.
echo   ERROR: install.bat said it succeeded but I can't find the binary.
echo   Expected at one of:
echo     %JARVIS_ROOT%\dist\jarvis.exe
echo     %JARVIS_ROOT%\dist_exe\jarvis.exe
echo.
echo   Try running build.bat manually for more verbose output.
pause
exit /b 1

:PREBUILT_FOUND
echo    Found: %PREBUILT%
echo.

REM ===========================================================================
REM Step 3: copy the binary + its siblings (lib/, share/) to %EXE_DIR%.
REM The cx_Freeze binary needs lib/ and share/ next to it. We use xcopy
REM to preserve the directory structure.
REM ===========================================================================
echo ==^> Installing to %EXE_DIR% ...
if exist "%EXE_DIR%" rmdir /s /q "%EXE_DIR%" 2>nul
mkdir "%EXE_DIR%" 2>nul
if errorlevel 1 (
    echo   ERROR: could not create %EXE_DIR%. Check permissions.
    pause
    exit /b 1
)

REM Copy the whole built dir (preserves lib/, share/, jarvis.sh, etc.)
xcopy /E /I /Y /Q "%BUILT_DIR%\*" "%EXE_DIR%\" >nul
if errorlevel 1 (
    echo   ERROR: could not copy jarvis files to %EXE_DIR%.
    pause
    exit /b 1
)
echo    Installed %JARVIS_EXE_NAME% + lib + share to %EXE_DIR%

REM ===========================================================================
REM Step 4: add %EXE_DIR% to the user PATH (HKCU\Environment). Idempotent
REM -- we check the current PATH first and only add if not already there.
REM ===========================================================================
echo.
echo ==^> Adding %EXE_DIR% to your user PATH ...
set "ALREADY_ON_PATH=0"
for /f "tokens=2*" %%A in ('reg query "HKCU\Environment" /v PATH 2^>nul') do (
    set "USER_PATH=%%B"
    echo !USER_PATH! | findstr /I /C:"%EXE_DIR%" >nul
    if not errorlevel 1 set "ALREADY_ON_PATH=1"
)
if "%ALREADY_ON_PATH%"=="1" (
    echo    Already on PATH -- nothing to change.
) else (
    set "NEW_PATH=%EXE_DIR%"
    if defined USER_PATH set "NEW_PATH=%EXE_DIR%;%USER_PATH%"
    reg add "HKCU\Environment" /v PATH /t REG_EXPAND_SZ /d "!NEW_PATH!" /f >nul
    if errorlevel 1 (
        echo   WARNING: could not update PATH automatically. You may
        echo   need to add %EXE_DIR% to your PATH manually.
    ) else (
        echo    Added. Open a NEW cmd window to pick up the change.
    )
)

REM Make jarvis available in THIS window too (so the auth-setup call below works)
set "PATH=%EXE_DIR%;%PATH%"

REM ===========================================================================
REM Step 5: run --auth-setup. This bypasses the auth gate (--auth-setup
REM is in the bypass list) and walks the user through all three layers.
REM ===========================================================================
echo.
echo ============================================================
echo   Running `jarvis --auth-setup`
echo ============================================================
echo   This is the FIRST-RUN auth setup. It will:
echo     1. Test Windows Hello (face / fingerprint / PIN popup)
echo     2. Let you register a face photo from your webcam
echo        (skipped if opencv-python isn't installed)
echo     3. Show the master passcode and test it
echo.
echo   After this, every `jarvis` invocation will require auth.
echo ============================================================
echo.

REM Make sure the binary actually runs before we trust it
"%EXE_DIR%\%JARVIS_EXE_NAME%" --version >nul 2>&1
if errorlevel 1 (
    "%EXE_DIR%\%JARVIS_EXE_NAME%" --help >nul 2>&1
    if errorlevel 1 (
        echo.
        echo   WARNING: jarvis.exe didn't respond to --version or --help.
        echo   The file may be corrupt or built for a different platform.
        echo   Try rebuilding:  cd %JARVIS_ROOT%  ^&^&  build.bat
        echo.
    )
)

"%EXE_DIR%\%JARVIS_EXE_NAME%" --auth-setup
set "AUTH_RC=%errorlevel%"

echo.
echo ============================================================
if "%AUTH_RC%"=="0" (
    echo   SETUP COMPLETE
) else (
    echo   SETUP RAN (exit %AUTH_RC% -- usually harmless)
)
echo ============================================================
echo.
echo   What to do next:
echo.
echo     1. Open a NEW cmd or PowerShell window
echo        (so the updated PATH takes effect)
echo.
echo     2. Type `jarvis` from any folder
echo        You'll be prompted for Windows Hello, then the
echo        webcam face, then the passcode -- any one works.
echo.
echo     3. If Windows Hello doesn't appear, or you cancel it,
echo        jarvis will fall through to the next layer
echo        automatically. No panic.
echo.
echo   Quick reference:
echo     jarvis --help           list all flags
echo     jarvis --auth-test      see which auth layers work
echo     jarvis --no-auth        skip auth for this one run
echo     set JARVIS_BYPASS=...   env var to bypass (must match
echo                             the master passcode; the auth
echo                             wizard above printed it)
echo.
echo   If you ever forget the master passcode, look at the
echo   top of jarvis.py: search for _MASTER_PASSCODE.
echo.
pause
exit /b 0
