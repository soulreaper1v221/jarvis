@echo off
REM ===========================================================================
REM clean-reinstall.bat -- one-shot: nuke + fresh-clone + build + install.
REM
REM Use this when you've made a mess of your local install and want to
REM start over from scratch. Equivalent to:
REM   rmdir /s /q "%USERPROFILE%\jarvis"
REM   rmdir /s /q "%USERPROFILE%\jarvis-app"
REM   rmdir /s /q "%USERPROFILE%\jarvis-exe"
REM   git clone https://github.com/soulreaper1v221/jarvis.git "%USERPROFILE%\jarvis"
REM   cd "%USERPROFILE%\jarvis"
REM   build.bat
REM   install.bat
REM
REM Just paste the ONE LINE below into a fresh cmd window:
REM   curl -fsSL https://raw.githubusercontent.com/soulreaper1v221/jarvis/main/clean-reinstall.bat -o clean.bat && clean.bat
REM
REM Or if you already have the repo:
REM   cd path\to\jarvis
REM   clean-reinstall.bat
REM ===========================================================================

setlocal EnableExtensions EnableDelayedExpansion

echo.
echo ============================================================
echo   jarvis clean reinstall
echo ============================================================
echo   This will:
echo     1. Delete your existing %USERPROFILE%\jarvis*
echo     2. Do a fresh git clone from GitHub
echo     3. Build the .exe
echo     4. Install it to your PATH
echo.
echo   Press Ctrl-C in the next 5 seconds to cancel.
echo ============================================================
echo.
timeout /t 5 /nobreak >nul

REM Make sure we have git
where git >nul 2>nul
if errorlevel 1 (
    echo ERROR: git not on PATH. Install Git for Windows first:
    echo   https://git-scm.com/download/win
    pause
    exit /b 1
)

REM Make sure we have python
where python >nul 2>nul
if errorlevel 1 (
    echo ERROR: python not on PATH. Install Python 3.6+ from python.org:
    echo   https://www.python.org/downloads/windows/
    echo   Tick "Add Python to PATH" during install.
    pause
    exit /b 1
)

REM Nuke the existing mess
echo ==^> Removing old jarvis directories...
if exist "%USERPROFILE%\jarvis-app" rmdir /s /q "%USERPROFILE%\jarvis-app"
if exist "%USERPROFILE%\jarvis-exe" rmdir /s /q "%USERPROFILE%\jarvis-exe"
if exist "%USERPROFILE%\jarvis" (
    REM Only nuke it if it's a git clone (don't blow away unrelated jarvis dirs)
    if exist "%USERPROFILE%\jarvis\.git" rmdir /s /q "%USERPROFILE%\jarvis"
)

REM Fresh clone
set "CLONE_DEST=%USERPROFILE%\jarvis"
echo ==^> Cloning to %CLONE_DEST%...
git clone --depth 1 --branch main https://github.com/soulreaper1v221/jarvis.git "%CLONE_DEST%"
if errorlevel 1 (
    echo.
    echo   ERROR: git clone failed. Check your network connection.
    pause
    exit /b 1
)

REM Now run the build
echo.
echo ==^> Building...
cd /d "%CLONE_DEST%"
call build.bat
if errorlevel 1 (
    echo.
    echo   ERROR: build failed. Read the output above.
    pause
    exit /b 1
)

REM And install
echo.
echo ==^> Installing to your PATH...
call install.bat
if errorlevel 1 (
    echo.
    echo   ERROR: install failed. Read the output above.
    pause
    exit /b 1
)

echo.
echo ============================================================
echo   ALL DONE
echo ============================================================
echo   jarvis.exe is at:  %USERPROFILE%\jarvis-exe\jarvis.exe
echo.
echo   1. Open a NEW cmd window (so PATH takes effect)
echo   2. Type:  jarvis --help
echo   3. First run will launch the setup wizard
echo.
echo   Press any key to close this window.
pause >nul
