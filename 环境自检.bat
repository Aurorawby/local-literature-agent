@echo off
chcp 65001 >nul
cd /d "%~dp0"

rem 优先用项目自带虚拟环境；没有就用系统 python
set "PY=python"
if exist ".venv\Scripts\python.exe" set "PY=.venv\Scripts\python.exe"

echo ============================================================
echo   环境自检：检查 Python / 依赖 / 模型 / 检索源是否正常
echo ============================================================
echo.

"%PY%" .tools\selftest_lit.py

echo.
pause
