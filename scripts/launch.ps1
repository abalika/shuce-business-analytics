param([switch]$NoBrowser, [switch]$Offline, [int]$Port = 8765, [string]$DataDir = '')
$ErrorActionPreference = 'Stop'
$taskRoot = Split-Path -Parent $PSScriptRoot
$taskPython = Join-Path $taskRoot '.venv\Scripts\python.exe'
if (-not (Test-Path -LiteralPath $taskPython)) { throw '请先运行 scripts\setup.ps1 安装项目依赖。' }
$taskArguments = @((Join-Path $PSScriptRoot 'service.py'), 'start', '--port', "$Port")
if ($Offline) { $taskArguments += '--offline' }
if ($DataDir) { $taskArguments += @('--data', $DataDir) }
& $taskPython @taskArguments
if ($LASTEXITCODE -ne 0) { throw '启动未确认成功，请根据上方提示检查；不要删除原数据。' }
if (-not $NoBrowser) { Start-Process "http://127.0.0.1:$Port" }
Write-Output "数策已启动：http://127.0.0.1:$Port；停止数策.cmd 可关闭默认端口。共享模型网关未被改动。"
