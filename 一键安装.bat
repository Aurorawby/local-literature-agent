@echo off
chcp 65001 >nul
cd /d "%~dp0"
title 文献 Agent - 一键安装

echo ============================================================
echo   文献 Agent 一键安装
echo   会依次：检查 Python -> 建虚拟环境 -> 装依赖 -> 生成配置 -> 自检
echo ============================================================
echo.

where python >nul 2>&1
if errorlevel 1 (
  echo [错误] 没找到 Python。
  echo 请先安装 Python 3.10+：https://www.python.org/downloads/
  echo 安装时务必勾选 "Add Python to PATH"，装完重新双击本文件。
  pause
  exit /b 1
)

python -c "import sys; sys.exit(0 if sys.version_info >= (3,10) else 1)" >nul 2>&1
if errorlevel 1 (
  echo [错误] Python 版本太低（需要 3.10+）。
  python --version
  pause
  exit /b 1
)

if not exist ".venv\Scripts\python.exe" (
  echo [1/4] 创建虚拟环境 .venv ...
  python -m venv .venv
  if errorlevel 1 (
    echo [错误] 创建虚拟环境失败。
    pause
    exit /b 1
  )
) else (
  echo [1/4] 已存在 .venv，跳过创建
)

echo.
echo [2/4] 安装依赖（首次约 5-15 分钟，取决于网速）...
".venv\Scripts\python.exe" -m pip --version >nul 2>&1
if errorlevel 1 (
  echo [提示] 虚拟环境里没有 pip，尝试修复 ...
  ".venv\Scripts\python.exe" -m ensurepip --upgrade >nul 2>&1
)
".venv\Scripts\python.exe" -m pip --version >nul 2>&1
if errorlevel 1 (
  echo [错误] 虚拟环境损坏（缺少 pip）。请删除 .venv 文件夹后重新双击本文件。
  pause
  exit /b 1
)
".venv\Scripts\python.exe" -m pip install --upgrade pip >nul 2>&1
".venv\Scripts\python.exe" -m pip install -r requirements.txt
if errorlevel 1 (
  echo.
  echo [提示] 安装失败，通常是网络问题。可以复制下面这行重试（国内镜像）：
  echo   .venv\Scripts\python.exe -m pip install -r requirements.txt -i https://pypi.tuna.tsinghua.edu.cn/simple
  pause
  exit /b 1
)

echo.
if not exist ".env" (
  echo [3/4] 生成配置文件 .env（来自 .env.example）
  copy .env.example .env >nul
) else (
  echo [3/4] 已存在 .env，保留你的配置
)

echo.
echo [4/4] 运行环境自检 ...
".venv\Scripts\python.exe" .tools\selftest_lit.py

echo.
echo ============================================================
echo   如果上面全是 ✓，安装完成！
echo   下一步：双击 启动界面.bat 打开网页界面；
echo   或者看 使用场景与工作流.md / 给朋友的一页说明.md
echo.
echo   如果提示 Ollama 连不上：去 https://ollama.com/download 安装 Ollama，
echo   然后执行：ollama pull qwen2.5:3b
echo ============================================================
pause
