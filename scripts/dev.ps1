# Launch the Job Signal desktop app with the Rust toolchain on PATH.
# Usage: .\scripts\dev.ps1
$cargoBin = Join-Path $env:USERPROFILE ".cargo\bin"
if (Test-Path $cargoBin) {
    $env:PATH = "$cargoBin;$env:PATH"
}
Set-Location (Join-Path $PSScriptRoot "..\frontend")
npm run tauri dev
