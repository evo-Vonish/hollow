# 一键本地三起：SearXNG(8888) + hollow gateway(8080) + React 前端(3000)。
# 前置：.venv-searx、.venv-api 与 frontend/node_modules 已按 README 安装。
$root = Split-Path -Parent $PSScriptRoot
$logs = Join-Path $root 'logs'
New-Item -ItemType Directory -Force -Path $logs | Out-Null

foreach ($relative in '.venv-searx\Scripts\python.exe', '.venv-api\Scripts\python.exe', 'frontend\node_modules\vite\bin\vite.js') {
    if (-not (Test-Path (Join-Path $root $relative))) {
        throw "missing $relative; follow README.md local setup first"
    }
}

$env:SEARXNG_SETTINGS_PATH = Join-Path $root 'searxng\settings.yml'
$env:PYTHONPATH = (Join-Path $root 'compat\win') + ';' + (Join-Path $root 'vendor\searxng')
$env:PYTHONIOENCODING = 'utf-8'
$env:PYTHONUNBUFFERED = '1'

Write-Host "[1/3] starting SearXNG on http://127.0.0.1:8888  (logs: $logs\searxng-*.log)"
$searx = Start-Process -FilePath (Join-Path $root '.venv-searx\Scripts\python.exe') `
    -ArgumentList '-m', 'searx.webapp' `
    -WorkingDirectory $root -PassThru -WindowStyle Hidden `
    -RedirectStandardOutput (Join-Path $logs 'searxng-out.log') `
    -RedirectStandardError (Join-Path $logs 'searxng-err.log')

Remove-Item Env:SEARXNG_SETTINGS_PATH, Env:PYTHONPATH

Write-Host "[2/3] starting hollow gateway on http://127.0.0.1:8080  (logs: $logs\gateway-*.log)"
$gateway = Start-Process -FilePath (Join-Path $root '.venv-api\Scripts\python.exe') `
    -ArgumentList '-m', 'uvicorn', 'api.main:app', '--host', '127.0.0.1', '--port', '8080' `
    -WorkingDirectory $root -PassThru -WindowStyle Hidden `
    -RedirectStandardOutput (Join-Path $logs 'gateway-out.log') `
    -RedirectStandardError (Join-Path $logs 'gateway-err.log')

Write-Host "[3/3] starting search UI on http://127.0.0.1:3000/search/  (logs: $logs\frontend-*.log)"
$frontend = Start-Process -FilePath 'node.exe' `
    -ArgumentList (Join-Path $root 'frontend\node_modules\vite\bin\vite.js'), '--host', '127.0.0.1' `
    -WorkingDirectory (Join-Path $root 'frontend') -PassThru -WindowStyle Hidden `
    -RedirectStandardOutput (Join-Path $logs 'frontend-out.log') `
    -RedirectStandardError (Join-Path $logs 'frontend-err.log')

Write-Host "SearXNG PID=$($searx.Id)  gateway PID=$($gateway.Id)  frontend PID=$($frontend.Id)"
Write-Host 'open:    http://127.0.0.1:3000/search/'
Write-Host 'probe:   Invoke-RestMethod http://127.0.0.1:8080/healthz'
Write-Host 'health+: Invoke-RestMethod ''http://127.0.0.1:8080/healthz?deep=1'''
Write-Host "logs:    Get-Content $logs\gateway-err.log -Wait -Tail 20"
Write-Host "stop:    Stop-Process $($searx.Id),$($gateway.Id),$($frontend.Id)"
