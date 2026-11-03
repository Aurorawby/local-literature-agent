@echo off
chcp 65001 >nul
cd /d "%~dp0"

rem 优先用项目自带虚拟环境；没有就用系统 python
set "PY=python"
if exist ".venv\Scripts\python.exe" set "PY=.venv\Scripts\python.exe"

if not "%~1"=="" (
  set "TARGET=%~1"
) else (
  echo ============================================================
  echo   论文对话 Agent
  echo   支持：本地 PDF / 知网下载的 PDF / arXiv 链接 / DOI
  echo ============================================================
  echo.
  set /p "TARGET=请把 PDF 文件拖到这里，或粘贴路径/arXiv 链接/DOI，然后回车："
)

rem 去掉粘贴时带上的引号（否则外层再包一层引号，路径会被空格拆成多个参数）
set "TARGET=%TARGET:"=%"

echo.
"%PY%" run_paper_chat.py "%TARGET%"

echo.
pause
