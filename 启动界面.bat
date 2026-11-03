@echo off
chcp 65001 >nul
cd /d "%~dp0"

rem 优先用项目自带虚拟环境；没有就用系统 python
set "PY=python"
if exist ".venv\Scripts\python.exe" set "PY=.venv\Scripts\python.exe"

"%PY%" -c "import streamlit" >nul 2>&1
if errorlevel 1 (
  echo [错误] 还没安装依赖。
  echo 请在当前目录运行：pip install -r requirements.txt
  echo 详细步骤见 README.md 的「快速开始」。
  pause
  exit /b 1
)

echo ============================================================
echo   文献 Agent 网页界面
echo   启动后浏览器会自动打开 http://localhost:8501
echo   关闭这个黑窗口 = 关闭服务
echo ============================================================
echo.

"%PY%" -m streamlit run app.py --server.port 8501 --server.headless false

pause
