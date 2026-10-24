param([string]$Wheelhouse = '')
$ErrorActionPreference = 'Stop'
$taskRoot = Split-Path -Parent $PSScriptRoot
$taskPython = Join-Path $taskRoot '.venv\Scripts\python.exe'
if (-not (Test-Path -LiteralPath $taskPython)) {
    & python -m venv (Join-Path $taskRoot '.venv')
    if ($LASTEXITCODE -ne 0) { throw 'Python虚拟环境创建失败；请安装Python 3.12并加入PATH。' }
}
$taskPip = @('-m', 'pip', 'install', '-r', (Join-Path $taskRoot 'requirements.lock.txt'))
if ($Wheelhouse) {
    if (-not (Test-Path -LiteralPath $Wheelhouse -PathType Container)) { throw '离线依赖目录不存在。' }
    $taskPip += @('--no-index', '--find-links', $Wheelhouse)
}
& $taskPython @taskPip
if ($LASTEXITCODE -ne 0) { throw '依赖安装失败；未宣告安装完成。' }
& $taskPython -m pip check
if ($LASTEXITCODE -ne 0) { throw '依赖一致性检查失败。' }
& $taskPython (Join-Path $PSScriptRoot 'seed_examples.py')
if ($LASTEXITCODE -ne 0) { throw '演示CSV准备失败。' }
# Installation prepares files only. Importing examples and DB creation occur on start.
Write-Output '安装完成。运行离线演示.cmd，或启动数策.cmd。首次安装依赖需要网络或完整wheelhouse。'
