"""按白名单把项目打包成「可直接上传 GitHub / 发给朋友」的文件夹 + zip。

用法：.venv\\Scripts\\python.exe .tools/build_package.py
产出：发布包/local-literature-agent/ 和 发布包/local-literature-agent.zip
"""

import shutil
import sys
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "发布包"
TARGET = OUT / "local-literature-agent"
ZIP_PATH = OUT / "local-literature-agent.zip"

# 仓库根目录要带的文件
ROOT_FILES = [
    "README.md",
    "ATTRIBUTION.md",
    "requirements.txt",
    "pyproject.toml",
    "LICENSE",
    ".gitignore",
    ".env.example",
    "使用指南.md",
    "使用场景与工作流.md",
    "给朋友的一页说明.md",
    "index.html",
    "app.py",
    "run_lit_review.py",
    "run_paper_read.py",
    "run_paper_chat.py",
    "run_library_chat.py",
    "一键安装.bat",
    "一键安装.command",
    "启动界面.bat",
    "运行-文献综述.bat",
    "运行-精读论文.bat",
    "运行-论文对话.bat",
    "环境自检.bat",
]

# 目录按白名单复制，并排除这些
DIRS = ["src", "docs", "portfolio", "eval", "user_test", ".tools", "papers"]
EXCLUDE_DIRS = {"__pycache__", ".cache", ".tmp", "results", "screenshots_old"}
EXCLUDE_NAMES = {".env", ".DS_Store", "Thumbs.db"}
EXCLUDE_SUFFIX = {".pyc", ".pyo", ".pdf", ".caj", ".docx"}


def ignored(path: Path) -> bool:
    if path.name in EXCLUDE_NAMES or path.name.startswith("~$"):
        return True
    if path.suffix.lower() in EXCLUDE_SUFFIX:
        return True
    return any(part in EXCLUDE_DIRS for part in path.parts)


def main() -> int:
    if OUT.exists():
        shutil.rmtree(OUT, ignore_errors=True)
    TARGET.mkdir(parents=True)

    copied = 0
    for name in ROOT_FILES:
        source = ROOT / name
        if not source.exists():
            print(f"  ! 缺少文件，跳过：{name}")
            continue
        shutil.copy2(source, TARGET / name)
        copied += 1

    for directory in DIRS:
        source_dir = ROOT / directory
        if not source_dir.is_dir():
            continue
        for path in source_dir.rglob("*"):
            if not path.is_file() or ignored(path):
                continue
            relative = path.relative_to(ROOT)
            target = TARGET / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(path, target)
            copied += 1

    # papers 目录只保留说明文件
    for leftover in (TARGET / "papers").glob("*.pdf"):
        leftover.unlink()

    with zipfile.ZipFile(ZIP_PATH, "w", zipfile.ZIP_DEFLATED) as archive:
        for path in sorted(TARGET.rglob("*")):
            if path.is_file():
                archive.write(path, path.relative_to(OUT))

    size = sum(p.stat().st_size for p in TARGET.rglob("*") if p.is_file())
    print(f"打包完成：{copied} 个文件，{size / 1024 / 1024:.2f} MB")
    print(f"  文件夹：{TARGET}")
    print(f"  压缩包：{ZIP_PATH}（{ZIP_PATH.stat().st_size / 1024 / 1024:.2f} MB）")

    # 自检：确认敏感文件没被打进去
    bad = []
    for path in TARGET.rglob("*"):
        if not path.is_file():
            continue
        name = path.name.lower()
        if name == ".env" or name.endswith((".docx", ".pdf", ".caj", ".pyc")):
            bad.append(path)
        if "输出" in path.parts or "个人文件" in path.parts:
            bad.append(path)
    if bad:
        print("\n⚠️ 发现不该打包的文件：")
        for path in bad:
            print(f"  {path.relative_to(TARGET)}")
        return 1
    print("敏感文件检查：通过（无 .env / 简历 / PDF / 缓存）")
    return 0


if __name__ == "__main__":
    sys.exit(main())
