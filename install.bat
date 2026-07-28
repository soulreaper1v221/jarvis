@echo off
chcp 65001 >nul 2>&1
REM ===========================================================================
REM Self-heal: if this file was saved as UTF-16 (PowerShell's
REM 'irm -OutFile' default), labels like :GIT_OK can't be found and
REM the script dies with "label not found". Detect that here and
REM re-save the file as ASCII before doing anything else.
REM ===========================================================================
if exist "%~f0" (
    set "FIRST2="
    for /f "usebackq" %%B in (`powershell -NoProfile -ExecutionPolicy Bypass -Command "$b=[IO.File]::ReadAllBytes('%~f0'); '{0:x} {1:x}' -f $b[0],$b[1]"`) do set "FIRST2=%%B"
    if "!FIRST2!"=="ff fe" (
        echo ==^> Detected UTF-16 BOM in this script; converting to ASCII ...
        powershell -NoProfile -ExecutionPolicy Bypass -Command "$content = [IO.File]::ReadAllText('%~f0'); [IO.File]::WriteAllText('%~f0', $content, [System.Text.Encoding]::ASCII)"
        echo    Re-running install.bat as ASCII ...
        call "%~f0" %*
        exit /b
    )
)
REM ===========================================================================
REM install.bat -- fully self-contained jarvis installer for Windows.
REM
REM What it does, in order:
REM   1. Checks for git on PATH; if missing, downloads a portable git.
REM   2. Checks for python on PATH; if missing, downloads the official
REM      python.org embeddable distribution.
REM   3. Locates the jarvis source: checks CWD, walks up parent dirs,
REM      checks %USERPROFILE%\jarvis; if not found, clones from GitHub.
REM   4. If the repo is missing build.bat / install.bat (e.g. someone
REM      ran this script as a standalone download), fetches them.
REM   5. Runs build.bat (installs requests + pyinstaller, builds jarvis.exe).
REM   6. Copies jarvis.exe to %USERPROFILE%\jarvis-exe\jarvis.exe
REM      and adds that to user PATH.
REM
REM One-liner (paste into PowerShell):
REM   [Net.ServicePointManager]::SecurityProtocol=[Net.SecurityProtocolType]::Tls12; (New-Object Net.WebClient).DownloadString('https://raw.githubusercontent.com/soulreaper1v221/jarvis/main/install.bat') ^| Out-File -Encoding ascii install.bat; .\install.bat
REM
REM (Note: do NOT use 'irm' to download install.bat -- it writes
REM UTF-16 LE with BOM, which breaks batch label parsing. Use
REM 'Net.WebClient' with '-Encoding ascii' instead. But the self-
REM heal block above will convert the file even if you do.)
REM ===========================================================================

setlocal EnableExtensions EnableDelayedExpansion

REM ===========================================================================
REM Helper: download a URL to a file. Tries 4 methods in order:
REM   1. curl (Windows 10 1803+ / Windows 11 ships with it)
REM   2. PowerShell WebClient (more permissive than irm)
REM   3. PowerShell BITS (works through more proxies)
REM   4. PowerShell Invoke-WebRequest with TLS 1.2 forced
REM Each method has its own quirks; we try them all until one works.
REM
REM Call:  call :DOWNLOAD_FILE <url> <dest_path>
REM After: DOWNLOAD_OK is 1 (success) or 0 (all methods failed)
REM ===========================================================================
goto :AFTER_HELPERS
:DOWNLOAD_FILE
set "DOWNLOAD_OK=0"
REM Method 1: curl
where curl >nul 2>nul
if not errorlevel 1 (
    curl -fsSL --retry 3 --connect-timeout 15 -o "%~2" "%~1" >nul 2>&1
    if not errorlevel 1 (
        set "DOWNLOAD_OK=1"
        goto :eof
    )
)
REM Method 2: WebClient (most permissive; doesn't trigger "internet" false positive)
powershell -NoProfile -ExecutionPolicy Bypass -Command "try { [Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12 -bor [Net.SecurityProtocolType]::Tls11; (New-Object System.Net.WebClient).DownloadFile('%~1','%~2') } catch { exit 1 }" >nul 2>&1
if not errorlevel 1 if exist "%~2" (
    set "DOWNLOAD_OK=1"
    goto :eof
)
REM Method 3: BITS
powershell -NoProfile -ExecutionPolicy Bypass -Command "try { Import-Module BitsTransfer -ErrorAction SilentlyContinue; Start-BitsTransfer -Source '%~1' -Destination '%~2' -ErrorAction SilentlyContinue } catch { }" >nul 2>&1
if exist "%~2" (
    set "DOWNLOAD_OK=1"
    goto :eof
)
REM Method 4: irm fallback
powershell -NoProfile -ExecutionPolicy Bypass -Command "try { [Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12; Invoke-WebRequest -Uri '%~1' -OutFile '%~2' -UseBasicParsing } catch { exit 1 }" >nul 2>&1
if not errorlevel 1 set "DOWNLOAD_OK=1"
goto :eof
:AFTER_HELPERS

REM ===========================================================================
REM Configuration
REM ===========================================================================
set "REPO_URL=https://github.com/soulreaper1v221/jarvis.git"
set "REPO_BRANCH=main"
set "REPO_RAW=https://raw.githubusercontent.com/soulreaper1v221/jarvis/%REPO_BRANCH%"
set "EXE_DIR=%USERPROFILE%\jarvis-exe"
set "PYTHON_MIN_VERSION=3.6"

echo.
echo ============================================================
echo   jarvis installer for Windows
echo ============================================================
echo   1. Make sure git + python are available (downloads if not)
echo   2. Find or download the jarvis source code
echo   3. Build a portable jarvis.exe
echo   4. Install it to %EXE_DIR% and add to your PATH
echo ============================================================
echo.

REM ===========================================================================
REM Step 1: Check for git. Download a portable copy if missing.
REM ===========================================================================
where git >nul 2>nul
if not errorlevel 1 goto :GIT_OK

echo ==^> git not found on PATH. Downloading portable git...
set "GIT_DIR=%USERPROFILE%\jarvis-tools\git"
if not exist "%GIT_DIR%\cmd\git.exe" (
    set "GIT_ZIP=%TEMP%\git-portable.exe"
    set "GIT_URL=https://github.com/git-for-windows/git/releases/download/v2.43.0.windows.1/PortableGit-2.43.0-64-bit.7z.exe"
    echo    Downloading from %GIT_URL% ...
    call :DOWNLOAD_FILE "%GIT_URL%" "%GIT_ZIP%"
    if not "!DOWNLOAD_OK!"=="1" (
        echo.
        echo   ERROR: Could not download git.
        echo   Please install Git for Windows manually:
        echo     https://git-scm.com/download/win
        echo   Then re-run this script.
        exit /b 1
    )
    echo    Extracting to %GIT_DIR% ...
    if not exist "%GIT_DIR%" mkdir "%GIT_DIR%"
    "%GIT_ZIP%" -y -o"%GIT_DIR%" >nul
)
set "PATH=%GIT_DIR%\cmd;%GIT_DIR%\usr\bin;%PATH%"
where git >nul 2>nul
if errorlevel 1 (
    echo   ERROR: portable git not detectable. Install Git from
    echo   https://git-scm.com/download/win and re-run.
    exit /b 1
)
:GIT_OK
for /f "tokens=*" %%G in ('git --version') do echo   Found: %%G

REM ===========================================================================
REM Step 2: Check for python. Download embeddable if missing.
REM ===========================================================================
where python >nul 2>nul
if not errorlevel 1 goto :PY_OK
where python3 >nul 2>nul
if not errorlevel 1 (
    set "PYTHON_BIN=python3"
    goto :PY_OK
)

echo.
echo ==^> python not found on PATH. Downloading embeddable python...
set "PY_DIR=%USERPROFILE%\jarvis-tools\python"
if not exist "%PY_DIR%\python.exe" (
    set "PY_ZIP=%TEMP%\python-embed.zip"
    set "PY_URL=https://www.python.org/ftp/python/3.11.9/python-3.11.9-embed-amd64.zip"
    echo    Downloading from %PY_URL% ...
    call :DOWNLOAD_FILE "%PY_URL%" "%PY_ZIP%"
    if not "!DOWNLOAD_OK!"=="1" (
        echo.
        echo   ERROR: Could not download python.
        echo   Please install Python 3.6+ from python.org:
        echo     https://www.python.org/downloads/windows/
        echo   Tick "Add Python to PATH" during install. Then re-run.
        exit /b 1
    )
    echo    Extracting to %PY_DIR% ...
    if not exist "%PY_DIR%" mkdir "%PY_DIR%"
    powershell -NoProfile -ExecutionPolicy Bypass -Command "Expand-Archive -Path '%PY_ZIP%' -DestinationPath '%PY_DIR%' -Force" >nul 2>&1
    if not exist "%PY_DIR%\python.exe" (
        echo   ERROR: extraction failed.
        exit /b 1
    )
    REM Patch python311._pth to enable site-packages
    if exist "%PY_DIR%\python311._pth" (
        powershell -NoProfile -ExecutionPolicy Bypass -Command "(Get-Content '%PY_DIR%\python311._pth') -replace '^\#import site$', 'import site' | Set-Content '%PY_DIR%\python311._pth'" >nul 2>&1
    )
    REM Bootstrap pip
    echo    Bootstrapping pip ...
    call :DOWNLOAD_FILE "https://bootstrap.pypa.io/get-pip.py" "%PY_DIR%\get-pip.py"
    if "!DOWNLOAD_OK!"=="1" "%PY_DIR%\python.exe" "%PY_DIR%\get-pip.py" --no-warn-script-location >nul 2>&1
)
set "PATH=%PY_DIR%;%PATH%"
set "PYTHON_BIN=%PY_DIR%\python.exe"
where python >nul 2>nul
if errorlevel 1 (
    echo   ERROR: python not detectable at %PYTHON_BIN%.
    exit /b 1
)
:PY_OK
for /f "tokens=*" %%P in ('python --version 2^>^&1') do echo   Found: %%P
if defined PYTHON_BIN set "PATH=%PYTHON_BIN%;%PATH%"

REM ===========================================================================
REM Step 3: Find the jarvis source folder.
REM
REM Looks in 4 places, in order:
REM   1. CWD (if you ran install.bat from the jarvis source folder)
REM   2. Script's own directory (the install.bat location)
REM   3. Walking up parent directories (handles being run from a sub-subfolder)
REM   4. %USERPROFILE%\jarvis (if you moved the install.bat there)
REM If still not found, clones the repo from GitHub.
REM ===========================================================================
echo.
echo ==^> Locating jarvis source folder...

set "JARVIS_ROOT="

REM Method 1: jarvis.py is in CWD
if exist "%CD%\jarvis.py" if exist "%CD%\jarvis.sh" (
    set "JARVIS_ROOT=%CD%"
    goto :FOUND
)

REM Method 2: jarvis.py is in the same dir as install.bat
if exist "%~dp0jarvis.py" if exist "%~dp0jarvis.sh" (
    set "JARVIS_ROOT=%~dp0"
    goto :FOUND
)

REM Method 3: jarvis.py is in a sub-folder called jarvis\
if exist "%CD%\jarvis\jarvis.py" if exist "%CD%\jarvis\jarvis.sh" (
    set "JARVIS_ROOT=%CD%\jarvis"
    goto :FOUND
)

REM Method 4: walk up parent directories
set "CANDIDATE=%CD%"
:SEARCH_LOOP
if exist "%CANDIDATE%\jarvis.py" if exist "%CANDIDATE%\jarvis.sh" (
    set "JARVIS_ROOT=%CANDIDATE%"
    goto :FOUND
)
if exist "%CANDIDATE%\.git" (
    if exist "%CANDIDATE%\jarvis\jarvis.py" (
        set "JARVIS_ROOT=%CANDIDATE%\jarvis"
        goto :FOUND
    )
)
if "%CANDIDATE%"=="%CANDIDATE:~0,3%" goto :SEARCH_DONE
for %%P in ("%CANDIDATE%") do set "PARENT=%%~dpP"
set "PARENT=%PARENT:~0,-1%"
if "%PARENT%"=="%CANDIDATE%" goto :SEARCH_DONE
set "CANDIDATE=%PARENT%"
goto :SEARCH_LOOP
:SEARCH_DONE

REM Method 5: check %USERPROFILE%\jarvis
if not defined JARVIS_ROOT if exist "%USERPROFILE%\jarvis\jarvis.py" set "JARVIS_ROOT=%USERPROFILE%\jarvis"

if not defined JARVIS_ROOT (
    echo    jarvis source not found. Cloning from GitHub...
    REM Pick a destination that exists and is empty (or doesn't exist yet).
    REM If %USERPROFILE%\jarvis-app exists and is non-empty, use a unique name.
    set "CLONE_BASE=%USERPROFILE%\jarvis-app"
    set "CLONE_DEST=%CLONE_BASE%"
    set "CLONE_TRY=0"
    :PICK_CLONE_DEST
    if exist "%CLONE_DEST%" (
        REM Check if it's already a valid clone
        if exist "%CLONE_DEST%\.git" (
            if exist "%CLONE_DEST%\jarvis\jarvis.py" (
                set "JARVIS_ROOT=%CLONE_DEST%\jarvis"
                goto :FOUND
            )
            REM Has .git but not the full jarvis folder -- partial / failed clone.
            REM Nuke it and re-clone in place.
            echo    Removing partial clone at %CLONE_DEST% ...
            rmdir /s /q "%CLONE_DEST%" 2>nul
        ) else (
            REM Not a git dir, has files we don't want to overwrite.
            REM Try a numbered variant.
            set /a CLONE_TRY+=1
            set "CLONE_DEST=%CLONE_BASE%-%CLONE_TRY%"
            if !CLONE_TRY! lss 50 goto :PICK_CLONE_DEST
            echo   ERROR: too many existing jarvis-app* directories. Clean some up.
            exit /b 1
        )
    )
    if not exist "%CLONE_DEST%" mkdir "%CLONE_DEST%" >nul 2>&1
    echo    Cloning into %CLONE_DEST% ...
    git clone --depth 1 --branch %REPO_BRANCH% %REPO_URL% "%CLONE_DEST%"
    if errorlevel 1 (
        echo   ERROR: git clone failed. Check your network connection.
        echo   If the issue is 'destination already exists', delete
        echo   %CLONE_DEST% manually and re-run.
        exit /b 1
    )
    set "JARVIS_ROOT=%CLONE_DEST%\jarvis"
)
:FOUND
echo    Found: %JARVIS_ROOT%

REM ===========================================================================
REM Step 4: Make sure build.bat and install.bat are there.
REM (If this script was downloaded standalone, those files are missing.)
REM ===========================================================================
if not exist "%JARVIS_ROOT%\build.bat" (
    echo ==^> Downloading build.bat ...
    call :DOWNLOAD_FILE "%REPO_RAW%/build.bat" "%JARVIS_ROOT%\build.bat"
)
if not exist "%JARVIS_ROOT%\install.bat" (
    echo ==^> Downloading install.bat (self-update) ...
    call :DOWNLOAD_FILE "%REPO_RAW%/install.bat" "%JARVIS_ROOT%\install.bat"
)

REM ===========================================================================
REM Step 5: Build.
REM ===========================================================================
echo.
echo ==^> Building jarvis...
cd /d "%JARVIS_ROOT%"
call build.bat
if errorlevel 1 (
    echo.
    echo   ERROR: build failed. Read the output above.
    echo   Common issues:
    echo     - Python not on PATH (re-open a new cmd after installing)
    echo     - Antivirus blocking pyinstaller
    echo     - No network (pip can't download requests / pyinstaller)
    exit /b 1
)

REM ===========================================================================
REM Step 6: Install to EXE_DIR and add to user PATH.
REM ===========================================================================
set "BUILT_EXE=%JARVIS_ROOT%\dist\jarvis.exe"
if not exist "%BUILT_EXE%" (
    echo   ERROR: expected jarvis.exe at %BUILT_EXE% but it wasn't produced.
    exit /b 1
)

echo.
echo ==^> Installing to %EXE_DIR%...
if exist "%EXE_DIR%" rmdir /s /q "%EXE_DIR%"
mkdir "%EXE_DIR%"
copy /Y "%BUILT_EXE%" "%EXE_DIR%\jarvis.exe" >nul

if not "%NO_PATH_UPDATE%"=="1" (
    echo ==^> Adding %EXE_DIR% to your user PATH ...
    for /f "tokens=2*" %%A in ('reg query "HKCU\Environment" /v PATH 2^>nul') do set "USER_PATH=%%B"
    set "NEW_PATH=%EXE_DIR%"
    if defined USER_PATH set "NEW_PATH=%EXE_DIR%;%USER_PATH%"
    reg add "HKCU\Environment" /v PATH /t REG_EXPAND_SZ /d "!NEW_PATH!" /f >nul
)

echo.
echo ============================================================
echo   INSTALL COMPLETE
echo ============================================================
echo   Binary:  %EXE_DIR%\jarvis.exe
echo   Source:  %JARVIS_ROOT%
echo.
echo   1. Open a NEW cmd / PowerShell window for the PATH change.
echo   2. Type:  jarvis --help
echo   3. First run will launch the setup wizard.
echo ============================================================
echo.
