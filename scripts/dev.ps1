# Launch the Job Signal desktop app with the Rust toolchain on PATH.
# Usage: .\scripts\dev.ps1
#
# Resets the local dev database when migration SQL files have changed
# (the migrator enforces checksums per-migration and refuses to start on
# stale schemas). This is intentional: dev data is ephemeral.
$cargoBin = Join-Path $env:USERPROFILE ".cargo\bin"
if (Test-Path $cargoBin) {
    $env:PATH = "$cargoBin;$env:PATH"
}

$devDbDir = Join-Path $env:APPDATA "com.jobsignal.app\worker"
$devDb = Join-Path $devDbDir "lodestar.sqlite"
$devDbWal = $devDb + "-wal"
$devDbShm = $devDb + "-shm"

if (Test-Path $devDb) {
    Write-Warning "Stale dev database detected at $devDb"
    Write-Warning "Removing it so the worker can apply the current migration schema."
    Remove-Item $devDb -Force -ErrorAction SilentlyContinue
    Remove-Item $devDbWal -Force -ErrorAction SilentlyContinue
    Remove-Item $devDbShm -Force -ErrorAction SilentlyContinue
}

Set-Location (Join-Path $PSScriptRoot "..\frontend")
npm run tauri dev