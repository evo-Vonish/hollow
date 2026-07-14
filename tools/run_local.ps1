# 一键本地双起:SearXNG(8888) + hollow gateway(8080)。Windows PowerShell 5.1+。
# 前置:.venv-searx 与 .venv-api 已按 README/requirements 建好。
# 2026-07-15(生产就绪批 #1):输出不再 -WindowStyle Hidden 吞掉,重定向到 logs\ 下可见日志文件。
$root = Split-Path -Parent $PSScriptRoot
$logs = Join-Path $root 'logs'
New-Item -ItemType Directory -Force -Path $logs | Out-Null

# 子进程继承当前进程环境(PS 5.1 的 Start-Process 没有 -Environment 参数)
$env:SEARXNG_SETTINGS_PATH = Join-Path $root 'searxng\settings.yml'
# compat\win 前置:Unix-only `pwd` 模块的 Windows 桩(vendor 零改动)
$env:PYTHONPATH = (Join-Path $root 'compat\win') + ';' + (Join-Path $root 'vendor\searxng')
# 日志:UTF-8 不乱码 + 不缓冲,及时落盘
$env:PYTHONIOENCODING = 'utf-8'
$env:PYTHONUNBUFFERED = '1'

Write-Host "[1/2] starting SearXNG on http://127.0.0.1:8888  (logs: $logs\searxng-*.log)"
$searx = Start-Process -FilePath (Join-Path $root '.venv-searx\Scripts\python.exe') `
    -ArgumentList '-m', 'searx.webapp' `
    -WorkingDirectory $root -PassThru -WindowStyle Hidden `
    -RedirectStandardOutput (Join-Path $logs 'searxng-out.log') `
    -RedirectStandardError  (Join-Path $logs 'searxng-err.log')

# gateway 不需要 SearXNG 的两个变量,清掉避免污染
Remove-Item Env:SEARXNG_SETTINGS_PATH, Env:PYTHONPATH

Write-Host "[2/2] starting hollow gateway on http://127.0.0.1:8080  (logs: $logs\gateway-*.log)"
$gateway = Start-Process -FilePath (Join-Path $root '.venv-api\Scripts\python.exe') `
    -ArgumentList '-m', 'uvicorn', 'api.main:app', '--host', '127.0.0.1', '--port', '8080' `
    -WorkingDirectory $root -PassThru -WindowStyle Hidden `
    -RedirectStandardOutput (Join-Path $logs 'gateway-out.log') `
    -RedirectStandardError  (Join-Path $logs 'gateway-err.log')

Write-Host "SearXNG PID=$($searx.Id)  gateway PID=$($gateway.Id)"
Write-Host 'probe:   Invoke-RestMethod http://127.0.0.1:8080/healthz'
Write-Host 'health+: Invoke-RestMethod ''http://127.0.0.1:8080/healthz?deep=1'''
Write-Host "logs:    Get-Content $logs\gateway-err.log -Wait -Tail 20"
Write-Host "stop:    Stop-Process $($searx.Id),$($gateway.Id)"
