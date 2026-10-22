param(
    [string]$OutputDirectory = ".\backups"
)

$ErrorActionPreference = "Stop"
if (-not $env:DATABASE_URL) {
    throw "DATABASE_URL is required."
}
if (-not (Get-Command pg_dump -ErrorAction SilentlyContinue)) {
    throw "pg_dump is required. Install PostgreSQL client tools first."
}

New-Item -ItemType Directory -Force -Path $OutputDirectory | Out-Null
$stamp = Get-Date -Format "yyyyMMdd-HHmmss"
$target = Join-Path (Resolve-Path $OutputDirectory) "tripmate-$stamp.dump"
pg_dump --format=custom --no-owner --no-privileges --file $target $env:DATABASE_URL
Write-Output "Backup written to $target"
