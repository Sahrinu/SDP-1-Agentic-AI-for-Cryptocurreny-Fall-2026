$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest

$student2Root = [System.IO.Path]::GetFullPath((Join-Path $PSScriptRoot '../..'))
$student2Verifier = Join-Path $PSScriptRoot 'verify_installed.py'
if (-not (Test-Path -LiteralPath (Join-Path $student2Root 'pyproject.toml') -PathType Leaf)) {
    throw "Repository pyproject.toml not found: $student2Root"
}
if (-not (Test-Path -LiteralPath $student2Verifier -PathType Leaf)) {
    throw "Installed-package verifier not found: $student2Verifier"
}

$student2Temp = [System.IO.Path]::GetTempPath()
$student2Venv = Join-Path $student2Temp ('student2-fresh-env-' + [guid]::NewGuid().ToString('N'))
$student2TestTemp = Join-Path $student2Temp ('student2-tests-' + [guid]::NewGuid().ToString('N'))
$student2OldPythonPath = [Environment]::GetEnvironmentVariable('PYTHONPATH', 'Process')
$student2OldNoBytecode = [Environment]::GetEnvironmentVariable('PYTHONDONTWRITEBYTECODE', 'Process')

Push-Location -LiteralPath $student2Root
try {
    $env:PYTHONPATH = ''
    $env:PYTHONDONTWRITEBYTECODE = '1'
    Write-Output "Fresh environment: $student2Venv"

    python -m venv $student2Venv
    if ($LASTEXITCODE -ne 0) { throw "Environment creation failed (exit $LASTEXITCODE)" }

    $student2Python = Join-Path $student2Venv 'Scripts/python.exe'
    & $student2Python -m pip install '.[student2,student2-test]'
    if ($LASTEXITCODE -ne 0) { throw "Package installation failed (exit $LASTEXITCODE)" }

    & $student2Python -m pip check
    if ($LASTEXITCODE -ne 0) { throw "Dependency check failed (exit $LASTEXITCODE)" }

    & $student2Python -I -B $student2Verifier
    if ($LASTEXITCODE -ne 0) { throw "Installed import or package-data verification failed (exit $LASTEXITCODE)" }

    & $student2Python -m pytest -p no:cacheprovider --basetemp $student2TestTemp `
        tests/test_betting_odds_specialist.py `
        tests/test_news_specialist.py `
        tests/test_mood_specialist.py `
        tests/test_news_specialist_fake_headline.py `
        tests/test_student2_adapter.py `
        tests/test_runner_abstentions.py `
        tests/test_student2_import_provenance.py `
        tests/test_student2_yahoo_compat.py
    if ($LASTEXITCODE -ne 0) { throw "Student 2 offline tests failed (exit $LASTEXITCODE)" }

    Write-Output "Fresh-environment verification passed: $student2Venv"
}
finally {
    [Environment]::SetEnvironmentVariable('PYTHONPATH', $student2OldPythonPath, 'Process')
    [Environment]::SetEnvironmentVariable('PYTHONDONTWRITEBYTECODE', $student2OldNoBytecode, 'Process')
    Pop-Location
}
