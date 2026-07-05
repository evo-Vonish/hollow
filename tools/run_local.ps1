# 一键本地双起:SearXNG(8888) + hollow gateway(8080)。Windows PowerShell 5.1+。
# 前置:.venv-searx 与 .venv-api 已按 README/requirements 建好。
$root = Split-Path -Parent $PSScriptRoot

# 子进程继承当前进程环境(PS 5.1 的 Start-Process 没有 -Environment 参数)
$env:SEARXNG_SETTINGS_PATH = Join-Path $root 'searxng\settings.yml'
# compat\win 前置:Unix-only `pwd` 模块的 Windows 桩(vendor 零改动)
$env:PYTHONPATH = (Join-Path $root 'compat\win') + ';' + (Join-Path $root 'vendor\searxng')

Write-Host '[1/2] starting SearXNG on http://127.0.0.1:8888 ...'
$searx = Start-Process -FilePath (Join-Path $root '.venv-searx\Scripts\python.exe') `
    -ArgumentList '-m', 'searx.webapp' `
    -WorkingDirectory $root -PassThru -WindowStyle Hidden

# gateway 不需要这两个变量,清掉避免污染
Remove-Item Env:SEARXNG_SETTINGS_PATH, Env:PYTHONPATH

Write-Host '[2/2] starting hollow gateway on http://127.0.0.1:8080 ...'
$gateway = Start-Process -FilePath (Join-Path $root '.venv-api\Scripts\python.exe') `
    -ArgumentList '-m', 'uvicorn', 'api.main:app', '--host', '127.0.0.1', '--port', '8080' `
    -WorkingDirectory $root -PassThru -WindowStyle Hidden

Write-Host "SearXNG PID=$($searx.Id)  gateway PID=$($gateway.Id)"
Write-Host 'probe:   Invoke-RestMethod http://127.0.0.1:8080/healthz'
Write-Host "stop:    Stop-Process $($searx.Id),$($gateway.Id)"
