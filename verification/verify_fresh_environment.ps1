$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest

$student2Root = [System.IO.Path]::GetFullPath((Join-Path $PSScriptRoot '..'))
$student2Verifier = Join-Path $PSScriptRoot 'verify_installed.py'
if (-not (Test-Path -LiteralPath (Join-Path $student2Root 'pyproject.toml') -PathType Leaf)) {
    throw "Repository pyproject.toml not found: $student2Root"
}
if (-not (Test-Path -LiteralPath $student2Verifier -PathType Leaf)) {
    throw "Installed-package verifier not found: $student2Verifier"
}

$student2Temp = [System.IO.Path]::GetTempPath()
$student2Venv = Join-Path $student2Temp ('agent-exchange-fresh-env-' + [guid]::NewGuid().ToString('N'))
$student2TestTemp = Join-Path $student2Temp ('agent-exchange-tests-' + [guid]::NewGuid().ToString('N'))
$student2BuildRoot = Join-Path $student2Temp ('agent-exchange-wheel-source-' + [guid]::NewGuid().ToString('N'))
$student2Wheelhouse = Join-Path $student2Temp ('student2-wheelhouse-' + [guid]::NewGuid().ToString('N'))
$student2OldPythonPath = [Environment]::GetEnvironmentVariable('PYTHONPATH', 'Process')
$student2OldNoBytecode = [Environment]::GetEnvironmentVariable('PYTHONDONTWRITEBYTECODE', 'Process')
$student2OldPipRetries = [Environment]::GetEnvironmentVariable('PIP_RETRIES', 'Process')
$student2OldPipTimeout = [Environment]::GetEnvironmentVariable('PIP_TIMEOUT', 'Process')

Push-Location -LiteralPath $student2Root
try {
    $env:PYTHONPATH = ''
    $env:PYTHONDONTWRITEBYTECODE = '1'
    $env:PIP_RETRIES = '0'
    $env:PIP_TIMEOUT = '15'
    Write-Output "Fresh environment: $student2Venv"

    python -m venv $student2Venv
    if ($LASTEXITCODE -ne 0) { throw "Environment creation failed (exit $LASTEXITCODE)" }

    $student2Python = Join-Path $student2Venv 'Scripts/python.exe'
    # Build from an isolated copy so setuptools does not alter checkout build or egg-info files.
    New-Item -ItemType Directory -Path $student2BuildRoot, $student2Wheelhouse | Out-Null
    foreach ($item in @('pyproject.toml', 'README.md', 'agents', 'market_data', 'features', 'model_clients', 'exchange', 'risk', 'execution', 'experiments', 'compat', 'verification', 'dashboard', 'licenses')) {
        Copy-Item -LiteralPath (Join-Path $student2Root $item) -Destination $student2BuildRoot -Recurse
    }
    & $student2Python -m pip wheel --retries 0 --timeout 15 --no-deps --wheel-dir $student2Wheelhouse $student2BuildRoot
    if ($LASTEXITCODE -ne 0) { throw "Wheel build failed (exit $LASTEXITCODE)" }
    $student2Wheels = @(Get-ChildItem -LiteralPath $student2Wheelhouse -Filter '*.whl' -File)
    if ($student2Wheels.Count -ne 1) { throw 'Expected exactly one built project wheel.' }
    $student2WheelSpec = $student2Wheels[0].FullName + '[test,specialists,live]'
    & $student2Python -m pip install --retries 0 --timeout 15 $student2WheelSpec
    if ($LASTEXITCODE -ne 0) { throw "Wheel installation failed (exit $LASTEXITCODE)" }

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
        tests/test_student2_yahoo_compat.py `
        tests/test_betting_evidence_forecast.py `
        tests/test_binance_json_transport.py `
        tests/test_binance_raw_transport.py `
        tests/test_limitless_live_reader.py `
        tests/test_limitless_powershell_collector.py `
        tests/test_limitless_saved_listing.py `
        tests/test_limitless_targeted_discovery.py `
        tests/test_market_evidence_specialist.py `
        tests/test_market_price_context.py
    if ($LASTEXITCODE -ne 0) { throw "Offline tests failed (exit $LASTEXITCODE)" }

    Write-Output "Fresh-environment verification passed: $student2Venv"
}
finally {
    [Environment]::SetEnvironmentVariable('PYTHONPATH', $student2OldPythonPath, 'Process')
    [Environment]::SetEnvironmentVariable('PYTHONDONTWRITEBYTECODE', $student2OldNoBytecode, 'Process')
    [Environment]::SetEnvironmentVariable('PIP_RETRIES', $student2OldPipRetries, 'Process')
    [Environment]::SetEnvironmentVariable('PIP_TIMEOUT', $student2OldPipTimeout, 'Process')
    Pop-Location
}
