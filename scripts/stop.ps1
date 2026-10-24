param([int]$Port = 8765)
$ErrorActionPreference = 'Stop'
$taskRoot = Split-Path -Parent $PSScriptRoot
$taskPython = Join-Path $taskRoot '.venv\Scripts\python.exe'
if (-not (Test-Path -LiteralPath $taskPython)) { throw '找不到项目虚拟环境，请按部署手册修复。' }
& $taskPython (Join-Path $PSScriptRoot 'service.py') stop --port $Port
if ($LASTEXITCODE -ne 0) { throw '停止未确认成功；身份不符的进程不会被关闭。' }
Write-Output '数策服务已停止；DSH共享模型网关继续保留。'
