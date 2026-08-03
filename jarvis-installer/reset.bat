@echo off
chcp 65001 >nul 2>&1
REM ===========================================================================
REM Self-heal: UTF-16 BOM detection (same as setup.bat).
REM ===========================================================================
if exist "%~f0" (
    set "FIRST2="
    for /f "usebackq" %%B in (`powershell -NoProfile -ExecutionPolicy Bypass -Command "$b=[IO.File]::ReadAllBytes('%~f0'); '{0:x} {1:x}' -f $b[0],$b[1]"`) do set "FIRST2=%%B"
    if "!FIRST2!"=="ff fe" (
        echo ==^> Detected UTF-16 BOM; converting to ASCII ...
        powershell -NoProfile -ExecutionPolicy Bypass -Command "$content = [IO.File]::ReadAllText('%~f0'); [IO.File]::WriteAllText('%~f0', $content, [System.Text.Encoding]::ASCII)"
        call "%~f0" %*
        exit /b
    )
)

REM ===========================================================================
REM reset.bat  --  nuke everything jarvis-related from this machine
REM                  and reinstall the latest version from github.
REM
REM WHAT THIS DOES, IN ORDER:
REM   1. Kills any running jarvis processes (python, the chat window,
REM      the auth wizard, anything that grabbed jarvis.py).
REM   2. Deletes:
REM        - the jarvis-installer folder at %USERPROFILE%\Downloads\
REM        - the user data folder at %USERPROFILE%\.jarvis\
REM          (this wipes your api key, passcode override, projects,
REM          deep-research sessions, paired phones, everything)
REM        - the embeddable python at %USERPROFILE%\jarvis-python\
REM        - the old exe install at %USERPROFILE%\jarvis-exe\ (if any)
REM   3. Removes %USERPROFILE%\jarvis-exe\ from the user PATH
REM      (left over from old v1.0 installs; the new version doesn't
REM      need it).
REM   4. Downloads the latest jarvis-installer.zip from github.
REM   5. Extracts it to %USERPROFILE%\Downloads\jarvis-installer\.
REM   6. Runs run.cmd (the friendly entry point) to do the install.
REM
REM WHAT THIS DOES NOT DO:
REM   - Does NOT touch a system-wide python install (C:\Python*).
REM   - Does NOT touch anything outside %USERPROFILE%.
REM   - Does NOT need admin rights.
REM
REM DOUBLE-CLICK THIS. IT WILL ASK ONE YES/NO QUESTION AND PROCEED.
REM
REM The whole thing takes about 2 minutes:
REM   ~10s to delete old files
REM   ~5s  to download the zip
REM   ~5s  to extract
REM   ~90s for the install (python, pip, auth setup, launch)
REM ===========================================================================

setlocal EnableExtensions EnableDelayedExpansion
cd /d "%~dp0"

set "GITHUB_ZIP_URL=https://github.com/soulreaper1v221/jarvis/raw/main/jarvis-installer.zip"
set "INSTALL_DIR=%USERPROFILE%\Downloads\jarvis-installer"
set "USER_DATA=%USERPROFILE%\.jarvis"
set "EMBED_PYTHON=%USERPROFILE%\jarvis-python"
set "OLD_EXE_DIR=%USERPROFILE%\jarvis-exe"
set "TMP_ZIP=%TEMP%\jarvis-installer.zip"

echo.
echo ============================================================
echo   jarvis reset.bat
echo ============================================================
echo.
echo   This will DELETE everything jarvis-related on this machine
echo   and reinstall the latest version from github.
echo.
echo   Specifically, it will:
echo     1. Kill any running jarvis processes
echo     2. Delete:   %INSTALL_DIR%
echo     3. Delete:   %USER_DATA%     (your api key, projects, sessions)
echo     4. Delete:   %EMBED_PYTHON%  (the embeddable python)
echo     5. Delete:   %OLD_EXE_DIR%   (old exe install, if any)
echo     6. Remove %OLD_EXE_DIR% from your user PATH
echo     7. Download fresh jarvis-installer.zip from github
echo     8. Extract to %INSTALL_DIR%
echo     9. Run run.cmd (install + auth + launch)
echo.
echo   Nothing outside %USERPROFILE% will be touched. System python
echo   (if you have one at C:\Python*) is left alone. Your other
echo   files, programs, and downloads are not affected.
echo.

set "CONFIRM="
set /p "CONFIRM=Type YES to continue (anything else cancels): "
if /i not "%CONFIRM%"=="YES" (
    echo.
    echo   Cancelled. Nothing was changed.
    pause
    exit /b 0
)
echo.

REM ===========================================================================
REM Step 1: kill any running jarvis processes
REM ===========================================================================
echo ==^> Step 1: killing any running jarvis processes ...

REM Find and kill python processes whose command line contains jarvis.py
REM We use wmic so we can see the full command line (taskkill /im only
REM does exe name, which would kill ALL python -- not what we want).
for /f "usebackq tokens=*" %%P in (`powershell -NoProfile -Command "Get-CimInstance Win32_Process -Filter \"Name = 'python.exe'\" | Where-Object { $_.CommandLine -like '*jarvis.py*' } | ForEach-Object { $_.ProcessId }"`) do (
    echo    killing python pid %%P (had jarvis.py in command line)
    taskkill /F /PID %%P >nul 2>&1
)

REM Also kill anything matching "jarvis" in its window title, just in
REM case it's not a python process (e.g. a leftover cmd window).
taskkill /F /FI "WINDOWTITLE eq jarvis*" >nul 2>&1

echo    done.
echo.

REM ===========================================================================
REM Step 2-5: delete old stuff
REM ===========================================================================
echo ==^> Step 2: deleting old jarvis-installer folder ...
if exist "%INSTALL_DIR%" (
    rmdir /s /q "%INSTALL_DIR%" 2>nul
    if exist "%INSTALL_DIR%" (
        echo    WARNING: could not delete %INSTALL_DIR%.
        echo    (probably a file is in use; close any open jarvis
        echo    windows and try again)
        pause
        exit /b 1
    )
)
echo    done.
echo.

echo ==^> Step 3: deleting user data folder %USER_DATA% ...
if exist "%USER_DATA%" (
    rmdir /s /q "%USER_DATA%" 2>nul
    if exist "%USER_DATA%" (
        echo    WARNING: could not delete %USER_DATA%.
        echo    close any open jarvis windows and try again.
        pause
        exit /b 1
    )
)
echo    done.
echo.

echo ==^> Step 4: deleting embeddable python at %EMBED_PYTHON% ...
if exist "%EMBED_PYTHON%" (
    rmdir /s /q "%EMBED_PYTHON%" 2>nul
    if exist "%EMBED_PYTHON%" (
        echo    WARNING: could not delete %EMBED_PYTHON%.
        echo    close any open jarvis windows and try again.
        pause
        exit /b 1
    )
)
echo    done.
echo.

echo ==^> Step 5: deleting old exe install at %OLD_EXE_DIR% ...
if exist "%OLD_EXE_DIR%" (
    rmdir /s /q "%OLD_EXE_DIR%" 2>nul
    echo    deleted.
) else (
    echo    (not present, skipping)
)
echo.

REM ===========================================================================
REM Step 6: remove %OLD_EXE_DIR% from PATH (if it was ever added)
REM ===========================================================================
echo ==^> Step 6: cleaning up user PATH ...
for /f "tokens=2*" %%A in ('reg query "HKCU\Environment" /v PATH 2^>nul') do (
    set "USER_PATH=%%B"
    set "NEW_PATH=!USER_PATH:;%OLD_EXE_DIR%=!"
    set "NEW_PATH=!NEW_PATH:%OLD_EXE_DIR%;=!"
    set "NEW_PATH=!NEW_PATH:%OLD_EXE_DIR%=!"
    if not "!NEW_PATH!"=="!USER_PATH!" (
        reg add "HKCU\Environment" /v PATH /t REG_EXPAND_SZ /d "!NEW_PATH!" /f >nul
        echo    removed %OLD_EXE_DIR% from PATH.
    ) else (
        echo    (PATH was clean, no change)
    )
)
echo.

REM ===========================================================================
REM Step 7: download the latest jarvis-installer.zip from github
REM ===========================================================================
echo ==^> Step 7: downloading fresh jarvis-installer.zip from github ...
echo    URL: %GITHUB_ZIP_URL%
echo    (this is fast, ~5s on a normal connection)
echo.

if exist "%TMP_ZIP%" del /q "%TMP_ZIP%" 2>nul
powershell -NoProfile -ExecutionPolicy Bypass -Command "try { [Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12; Invoke-WebRequest -Uri '%GITHUB_ZIP_URL%' -OutFile '%TMP_ZIP%' -UseBasicParsing -ErrorAction Stop } catch { Write-Host 'DOWNLOAD_FAILED:'; Write-Host $_.Exception.Message; exit 1 }"
if errorlevel 1 (
    echo.
    echo   ERROR: download failed. Check your network connection.
    echo   Then either:
    echo     - re-run reset.bat, or
    echo     - manually download from:
    echo       %GITHUB_ZIP_URL%
    echo       and extract to %INSTALL_DIR%
    echo.
    pause
    exit /b 1
)
echo    downloaded.
echo.

REM ===========================================================================
REM Step 8: extract the zip
REM ===========================================================================
echo ==^> Step 8: extracting to %INSTALL_DIR% ...
powershell -NoProfile -ExecutionPolicy Bypass -Command "try { if (Test-Path '%INSTALL_DIR%') { Remove-Item -Recurse -Force '%INSTALL_DIR%' -ErrorAction Stop }; Expand-Archive -Path '%TMP_ZIP%' -DestinationPath '%USERPROFILE%\Downloads' -Force } catch { Write-Host 'EXTRACT_FAILED:'; Write-Host $_.Exception.Message; exit 1 }"
if errorlevel 1 (
    echo.
    echo   ERROR: could not extract the zip. The downloaded file is at:
    echo     %TMP_ZIP%
    echo   Try extracting it by hand (right-click -^> Extract All...)
    echo   to: %INSTALL_DIR%
    echo.
    pause
    exit /b 1
)
del /q "%TMP_ZIP%" 2>nul
echo    extracted.
echo.

REM ===========================================================================
REM Step 9: run run.cmd (which does the actual install)
REM ===========================================================================
echo ==^> Step 9: launching the installer (run.cmd) ...
echo.
echo   run.cmd will now:
echo     - find or download python
echo     - pip install requests + opencv-python
echo     - run the auth wizard (windows hello + webcam + passcode)
echo     - launch jarvis
echo.
echo   The console will stay open so you can see progress.
echo.

cd /d "%INSTALL_DIR%"
if exist "jarvis-installer\run.cmd" (
    REM The zip extracted as a nested jarvis-installer\ folder.
    REM cd into it so run.cmd finds its siblings.
    cd jarvis-installer
)
call "run.cmd"
set "RC=%errorlevel%"

echo.
echo ============================================================
if "%RC%"=="0" (
    echo   RESET + REINSTALL COMPLETE
) else (
    echo   RESET DONE, INSTALLER EXITED %RC%
    echo   The old jarvis files are gone and the new ones are
    echo   in place; you can re-run run.cmd manually to retry:
    echo     %INSTALL_DIR%\jarvis-installer\run.cmd
)
echo ============================================================
echo.
pause
exit /b %RC%
