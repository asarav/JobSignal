# Launch the Job Signal desktop app with the Rust toolchain on PATH.
# Usage: .\scripts\dev.ps1
#
# The migrator enforces a checksum per migration file. When a migration file
# changes (for example after pulling a new milestone), the stored checksum no
# longer matches and the worker refuses to start.
#
# To avoid a crash loop, this script compares a hash of the migration files
# with the hash stored next to the dev database. Only when they differ is the
# (ephemeral) dev database removed so the worker can rebuild it.
$cargoBin = Join-Path $env:USERPROFILE ".cargo\bin"
if (Test-Path $cargoBin) {
    $env:PATH = "$cargoBin;$env:PATH"
}

$devDbDir = Join-Path $env:APPDATA "com.jobsignal.app\worker"
$devDb = Join-Path $devDbDir "lodestar.sqlite"
$devDbHash = "$devDb.schema-hash"
$migrationsDir = Join-Path $PSScriptRoot "..\app\infrastructure\persistence\sqlite\migrations"

$currentHash = ""
if (Test-Path $migrationsDir) {
    $currentHash = (
        Get-ChildItem $migrationsDir -Filter *.sql |
            Sort-Object Name |
            ForEach-Object { (Get-FileHash $_.FullName -Algorithm SHA256).Hash }
    ) -join ""
}
$storedHash = ""
if (Test-Path $devDbHash) {
    $storedHash = (Get-Content $devDbHash -Raw).Trim()
}

if ((Test-Path $devDb) -and $currentHash -ne $storedHash) {
    Write-Warning "Migration files changed; resetting the dev database at $devDb"
    Remove-Item $devDb -Force -ErrorAction SilentlyContinue
    Remove-Item "$devDb-wal" -Force -ErrorAction SilentlyContinue
    Remove-Item "$devDb-shm" -Force -ErrorAction SilentlyContinue
}

if ($currentHash -ne "") {
    Set-Content -Path $devDbHash -Value $currentHash
}

Set-Location (Join-Path $PSScriptRoot "..\frontend")
npm run tauri dev
