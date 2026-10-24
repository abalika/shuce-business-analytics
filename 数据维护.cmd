@echo off
chcp 65001 >nul
cd /d "%~dp0"
if "%~1"=="" (
  echo 用法：数据维护.cmd check / backup / verify / restore / seal
  echo check 核查当前数据；backup 创建带校验的本地备份。
  echo verify --archive "备份.zip" 校验备份。
  echo restore --archive "备份.zip" --to "全新目录" 恢复副本，绝不覆盖现有数据。
  echo seal 仅用于旧版升级，须先停止数策；不修改经营计算或旧快照。
  pause
  exit /b 0
)
".venv\Scripts\python.exe" scripts\maintain.py %*
exit /b %errorlevel%
