@echo off
REM build.bat -- one-shot build script for the jarvis portable exe (Windows).
REM
REM What it does:
REM   1. Verifies python + pip are available.
REM   2. Installs requests + pyinstaller.
REM   3. Runs `python jarvis.py --build` from the jarvis source folder.
REM   4. Prints the final dist\jarvis.exe path.
REM
REM Usage (from any of these locations):
REM   - C:\path\to\repo\build.bat            (repo root)
REM   - C:\path\to\repo\jarvis\build.bat     (app subfolder)
REM   - C:\path\to\repo\jarvis\jarvis.py     (inside app subfolder)
REM
REM Output: dist\jarvis.exe (or dist\jarvis\jarvis on Linux/macOS)

setlocal enabledelayedexpansion

REM Resolve the jarvis source folder. The script works from any of:
REM   - repo root (where build.bat + jarvis\ subdir live)
REM   - jarvis\ subfolder (where jarvis.py + jarvis.sh live)
REM   - anywhere else (walks up looking for jarvis.py)
set "SCRIPT_DIR=%~dp0"
set "JARVIS_DIR="

REM Case 1: jarvis.py is right here
if exist "%SCRIPT_DIR%jarvis.py" (
    set "JARVIS_DIR=%SCRIPT_DIR%."
    goto :FOUND_JARVIS
)
REM Case 2: jarvis.py is in jarvis\ subfolder of script's location
if exist "%SCRIPT_DIR%jarvis\jarvis.py" (
    set "JARVIS_DIR=%SCRIPT_DIR%jarvis"
    goto :FOUND_JARVIS
)
REM Case 3: walk up looking for jarvis.py (handles being run from a sub-subfolder)
set "CANDIDATE=%SCRIPT_DIR%"
:WALK_UP
if exist "%CANDIDATE%jarvis.py" (
    set "JARVIS_DIR=%CANDIDATE%."
    goto :FOUND_JARVIS
)
if exist "%CANDIDATE%jarvis\jarvis.py" (
    set "JARVIS_DIR=%CANDIDATE%jarvis"
    goto :FOUND_JARVIS
)
if "%CANDIDATE%"=="%CANDIDATE:~0,3%" goto :NOT_FOUND
for %%P in ("%CANDIDATE%") do set "PARENT=%%~dpP"
set "PARENT=%PARENT:~0,-1%"
if "%PARENT%"=="%CANDIDATE%" goto :NOT_FOUND
set "CANDIDATE=%PARENT%"
goto :WALK_UP

:NOT_FOUND
echo ERROR: jarvis.py not found.
echo This script must be in the jarvis repo, in the jarvis\ subfolder,
echo or in any nested subfolder.
exit /b 1

:FOUND_JARVIS

REM Find python
where python >nul 2>nul
if errorlevel 1 (
    where python3 >nul 2>nul
    if errorlevel 1 (
        echo ERROR: python is not on PATH. Install Python 3.6+ first.
        exit /b 1
    ) else (
        set "PY=python3"
    )
) else (
    set "PY=python"
)

echo ==^> Installing build dependencies...
%PY% -m pip install --quiet --upgrade pip
%PY% -m pip install --quiet requests
%PY% -m pip install --quiet pyinstaller

echo ==^> Building portable jarvis...
cd /d "%JARVIS_DIR%"
%PY% jarvis.py --build %*

if exist "%JARVIS_DIR%\dist\jarvis.exe" (
    echo.
    echo ============================================================
    echo  BUILD COMPLETE
    echo ============================================================
    echo   Binary:  %JARVIS_DIR%\dist\jarvis.exe
    echo.
    echo   Run:  "%JARVIS_DIR%\dist\jarvis.exe" --help
    echo   Or copy dist\jarvis.exe anywhere and double-click it.
) else (
    echo.
    echo WARNING: Build finished but dist\jarvis.exe not found.
    echo Check the output above for errors.
    exit /b 1
)
