$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest

$projectRoot = [System.IO.Path]::GetFullPath((Join-Path $PSScriptRoot '..'))
$projectVerifier = Join-Path $PSScriptRoot 'verify_installed.py'
if (-not (Test-Path -LiteralPath (Join-Path $projectRoot 'pyproject.toml') -PathType Leaf)) {
    throw "Repository pyproject.toml not found: $projectRoot"
}
if (-not (Test-Path -LiteralPath $projectVerifier -PathType Leaf)) {
    throw "Installed-package verifier not found: $projectVerifier"
}

$projectTemp = [System.IO.Path]::GetTempPath()
$projectVenv = Join-Path $projectTemp ('agent-exchange-fresh-env-' + [guid]::NewGuid().ToString('N'))
$projectTestTemp = Join-Path $projectTemp ('agent-exchange-tests-' + [guid]::NewGuid().ToString('N'))
$projectBuildRoot = Join-Path $projectTemp ('agent-exchange-wheel-source-' + [guid]::NewGuid().ToString('N'))
$projectWheelhouse = Join-Path $projectTemp ('agent-exchange-wheelhouse-' + [guid]::NewGuid().ToString('N'))
$projectOldPythonPath = [Environment]::GetEnvironmentVariable('PYTHONPATH', 'Process')
$projectOldNoBytecode = [Environment]::GetEnvironmentVariable('PYTHONDONTWRITEBYTECODE', 'Process')
$projectOldPipRetries = [Environment]::GetEnvironmentVariable('PIP_RETRIES', 'Process')
$projectOldPipTimeout = [Environment]::GetEnvironmentVariable('PIP_TIMEOUT', 'Process')

Push-Location -LiteralPath $projectRoot
try {
    $env:PYTHONPATH = ''
    $env:PYTHONDONTWRITEBYTECODE = '1'
    $env:PIP_RETRIES = '0'
    $env:PIP_TIMEOUT = '15'
    Write-Output "Fresh environment: $projectVenv"

    python -m venv $projectVenv
    if ($LASTEXITCODE -ne 0) { throw "Environment creation failed (exit $LASTEXITCODE)" }

    $projectPython = Join-Path $projectVenv 'Scripts/python.exe'
    # Build from an isolated copy so setuptools does not alter checkout build or egg-info files.
    New-Item -ItemType Directory -Path $projectBuildRoot, $projectWheelhouse | Out-Null
    foreach ($item in @('pyproject.toml', 'README.md', 'LICENSE', 'agents', 'market_data', 'features', 'model_clients', 'exchange', 'risk', 'execution', 'experiments', 'verification', 'dashboard', 'licenses')) {
        Copy-Item -LiteralPath (Join-Path $projectRoot $item) -Destination $projectBuildRoot -Recurse
    }
    & $projectPython -m pip wheel --retries 0 --timeout 15 --no-deps --wheel-dir $projectWheelhouse $projectBuildRoot
    if ($LASTEXITCODE -ne 0) { throw "Wheel build failed (exit $LASTEXITCODE)" }
    $projectWheels = @(Get-ChildItem -LiteralPath $projectWheelhouse -Filter '*.whl' -File)
    if ($projectWheels.Count -ne 1) { throw 'Expected exactly one built project wheel.' }
    $projectWheelSpec = $projectWheels[0].FullName + '[test,specialists,live]'
    & $projectPython -m pip install --retries 0 --timeout 15 $projectWheelSpec
    if ($LASTEXITCODE -ne 0) { throw "Wheel installation failed (exit $LASTEXITCODE)" }

    & $projectPython -m pip check
    if ($LASTEXITCODE -ne 0) { throw "Dependency check failed (exit $LASTEXITCODE)" }

    & $projectPython -I -B $projectVerifier
    if ($LASTEXITCODE -ne 0) { throw "Installed import or package-data verification failed (exit $LASTEXITCODE)" }

    & $projectPython -m pytest -p no:cacheprovider --basetemp $projectTestTemp `
        tests/test_betting_odds_specialist.py `
        tests/test_news_specialist.py `
        tests/test_mood_specialist.py `
        tests/test_news_specialist_fake_headline.py `
        tests/test_specialist_adapter.py `
        tests/test_runner_abstentions.py `
        tests/test_import_provenance.py `
        tests/test_yahoo_news.py `
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

    Write-Output "Fresh-environment verification passed: $projectVenv"
}
finally {
    [Environment]::SetEnvironmentVariable('PYTHONPATH', $projectOldPythonPath, 'Process')
    [Environment]::SetEnvironmentVariable('PYTHONDONTWRITEBYTECODE', $projectOldNoBytecode, 'Process')
    [Environment]::SetEnvironmentVariable('PIP_RETRIES', $projectOldPipRetries, 'Process')
    [Environment]::SetEnvironmentVariable('PIP_TIMEOUT', $projectOldPipTimeout, 'Process')
    Pop-Location
}
