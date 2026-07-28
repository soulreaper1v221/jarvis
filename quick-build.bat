@echo off
REM ===========================================================================
REM quick-build.bat -- the simplest possible build + install for users
REM who already have git + python + the jarvis repo cloned.
REM
REM Usage:  double-click this file, or run from cmd:
REM   cd C:\path\to\jarvis\jarvis
REM   quick-build.bat
REM
REM This is a thin wrapper around build.bat + install.bat. Use it when
REM you don't need the self-downloading fallback logic in install.bat.
REM ===========================================================================

echo.
echo ============================================================
echo   jarvis quick build + install
echo ============================================================
echo.

REM Make sure we're in the jarvis\ source folder
if not exist "jarvis.py" (
    if exist "..\jarvis\jarvis.py" (
        cd ..
    ) else (
        echo ERROR: jarvis.py not found in this directory or its parent.
        echo Run this from C:\path\to\jarvis\jarvis or its parent folder.
        pause
        exit /b 1
    )
)

echo ==^> Building...
call build.bat
if errorlevel 1 (
    echo.
    echo Build failed. See output above.
    pause
    exit /b 1
)

echo.
echo ==^> Installing to your PATH...
call install.bat
if errorlevel 1 (
    echo.
    echo Install failed. See output above.
    pause
    exit /b 1
)

echo.
echo ============================================================
echo   Done! Open a NEW cmd window and type:  jarvis --help
echo ============================================================
pause
