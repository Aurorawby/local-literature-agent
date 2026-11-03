#!/bin/bash
# 文献 Agent 一键安装（macOS / Linux）
# 用法：双击前先在终端执行一次：chmod +x 一键安装.command
cd "$(dirname "$0")" || exit 1

echo "============================================================"
echo "  文献 Agent 一键安装（macOS / Linux）"
echo "============================================================"

if ! command -v python3 >/dev/null 2>&1; then
  echo "[错误] 没找到 python3，请先安装 Python 3.10+：https://www.python.org/downloads/"
  read -r -p "按回车退出..."
  exit 1
fi

echo "[1/4] 创建虚拟环境 .venv ..."
[ -d ".venv" ] || python3 -m venv .venv

echo "[2/4] 安装依赖 ..."
if ! ./.venv/bin/python -m pip --version >/dev/null 2>&1; then
  ./.venv/bin/python -m ensurepip --upgrade >/dev/null 2>&1
fi
./.venv/bin/python -m pip install --upgrade pip >/dev/null 2>&1
if ! ./.venv/bin/python -m pip install -r requirements.txt; then
  echo "[提示] 安装失败，可换国内镜像重试："
  echo "  ./.venv/bin/python -m pip install -r requirements.txt -i https://pypi.tuna.tsinghua.edu.cn/simple"
  read -r -p "按回车退出..."
  exit 1
fi

echo "[3/4] 生成配置文件 ..."
[ -f ".env" ] || cp .env.example .env

echo "[4/4] 运行环境自检 ..."
./.venv/bin/python .tools/selftest_lit.py

echo
echo "完成。运行网页界面："
echo "  ./.venv/bin/python -m streamlit run app.py"
echo
read -r -p "按回车退出..."
