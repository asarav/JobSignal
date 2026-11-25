$ErrorActionPreference = "Stop"
Set-Location -LiteralPath $PSScriptRoot

if (Get-Command py -ErrorAction SilentlyContinue) {
    & py -3 scripts/run.py @args
} else {
    & python scripts/run.py @args
}

exit $LASTEXITCODE
