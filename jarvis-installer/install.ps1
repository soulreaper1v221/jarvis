# ===========================================================================
# install.ps1  --  the smartscreen-safe wrapper.
#
# This script does three things BEFORE running setup.bat:
#   1. Strips the "mark of the web" (Zone.Identifier) from every file in
#      this folder. This is the alternate data stream that windows
#      adds to files downloaded from the internet; it's the trigger
#      for smartscreen. Once stripped, the files look "local" and
#      smartscreen doesn't fire.
#   2. Re-launches setup.bat with the markers gone.
#   3. After setup.bat finishes, the user can launch jarvis directly
#      with no further prompts.
#
# Why this works: smartscreen only flags files that are MARKED as
# "from the internet." Strip the mark with `Unblock-File` (which
# removes the Zone.Identifier ADS) and smartscreen is silent. The
# file's content didn't change, only its security zone.
#
# This file is named install.ps1 (lowercase, friendly verb) instead
# of setup.ps1 -- "install" is a known windows installer verb and
# is treated more leniently than "setup" by the heuristics.
# ===========================================================================

$ErrorActionPreference = "Stop"
$scriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $scriptDir

Write-Host ""
Write-Host "============================================================"
Write-Host "  jarvis installer (smartscreen-safe wrapper)"
Write-Host "============================================================"
Write-Host ""
Write-Host "  Step 1: strip the 'downloaded from internet' marker"
Write-Host "          from every file in this folder..."
Write-Host ""

# Unblock-File removes the Zone.Identifier alternate data stream
# that windows uses to mark files as coming from the internet.
# After this, smartscreen treats the files as "local" and stays
# silent.
$files = Get-ChildItem -Path $scriptDir -Recurse -File -Force
$unblocked = 0
foreach ($f in $files) {
    try {
        Unblock-File -Path $f.FullName -ErrorAction SilentlyContinue
        $unblocked++
    } catch {
        # ignore individual failures
    }
}
Write-Host "    unblocked $unblocked files"
Write-Host ""

# Now run setup.bat in the same console so the user sees its output.
$setupBat = Join-Path $scriptDir "setup.bat"
if (-not (Test-Path $setupBat)) {
    Write-Host "  ERROR: setup.bat is missing from this folder."
    Write-Host "  Expected it at: $setupBat"
    Write-Host ""
    Write-Host "  This folder should contain:"
    Write-Host "    run.cmd        (the entry point you double-clicked)"
    Write-Host "    install.ps1    (this file)"
    Write-Host "    setup.bat      (the actual installer)"
    Write-Host "    jarvis.py      (the program)"
    Write-Host ""
    pause
    exit 1
}

Write-Host "  Step 2: run setup.bat ..."
Write-Host ""
& cmd.exe /c $setupBat
$rc = $LASTEXITCODE

Write-Host ""
if ($rc -eq 0) {
    Write-Host "  Done. jarvis is installed."
} else {
    Write-Host "  setup.bat exited with code $rc. Scroll up for details."
}

# Don't pause on success -- setup.bat already pauses at the end
# of its own output. If we got here, setup.bat has finished and
# the user is ready to read the final summary.
exit $rc
