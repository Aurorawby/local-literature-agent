@echo off
chcp 65001 >nul
cd /d "%~dp0"

rem 优先用项目自带虚拟环境；没有就用系统 python
set "PY=python"
if exist ".venv\Scripts\python.exe" set "PY=.venv\Scripts\python.exe"

echo ============================================================
echo   文献综述 Agent
echo   输入一个研究主题，自动检索期刊/预印本并生成中文综述
echo ============================================================
echo.

"%PY%" run_lit_review.py %*

echo.
echo ============================================================
echo   运行结束。报告在 output 文件夹里。
echo ============================================================
pause
