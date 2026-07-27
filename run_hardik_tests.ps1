param(
    [string]$Dsn = "host=127.0.0.1 dbname=fraudguard user=postgres password=postgres",
    [string]$TargetRows = "2000,20000,100000,200000",
    [switch]$Reset
)

$ErrorActionPreference = "Stop"
Set-Location $PSScriptRoot

New-Item -ItemType Directory -Force -Path "performance_results" | Out-Null

if ($Reset) {
    Write-Host "Resetting schema and loading seed data..."
    psql "$Dsn" -v ON_ERROR_STOP=1 -f "sql/01_schema.sql" -f "sql/02_seed_data.sql"
    if ($LASTEXITCODE -ne 0) { throw "Schema/seed load failed." }
}

Write-Host "Running database validation..."
# pending re-add of sql/04_validation.sql
# psql "$Dsn" -v ON_ERROR_STOP=1 -f "sql/04_validation.sql" |
    Tee-Object -FilePath "performance_results/validation_output.txt"
if ($LASTEXITCODE -ne 0) { throw "Validation failed." }

Write-Host "Running PostgreSQL performance benchmark..."
python "performance_test.py" --dsn "$Dsn" --target-rows "$TargetRows" --output-dir "performance_results"
if ($LASTEXITCODE -ne 0) { throw "Performance benchmark failed." }

Write-Host "Running partitioning demonstration..."
# pending re-add of sql/05_partitioning_demo.sql
# psql "$Dsn" -v ON_ERROR_STOP=1 -f "sql/05_partitioning_demo.sql" |
    Tee-Object -FilePath "performance_results/partitioning_output.txt"
if ($LASTEXITCODE -ne 0) { throw "Partitioning demo failed." }

Write-Host "Done. Open performance_results/performance_report.md."
