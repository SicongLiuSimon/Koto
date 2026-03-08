"""组装可分发的 Koto Windows 便携包。"""

from __future__ import annotations

import argparse
import os
import stat
import shutil
import time
from pathlib import Path


ROOT = Path(__file__).resolve().parent
APP_BUILD_DIR = ROOT / "dist" / "Koto"
OUTPUT_DIR = ROOT / "dist" / "Koto_Portable"

LOCAL_INSTALLER_CANDIDATES = [
    ROOT / "dist" / "LocalModelInstaller.exe",
    ROOT / "dist" / "LocalModelInstaller" / "LocalModelInstaller.exe",
    ROOT / "dist" / "local_model_installer" / "LocalModelInstaller.exe",
]


PORTABLE_README = """Koto Windows 便携版
====================

使用步骤：
1. 双击 Install_Local_Model.bat，按提示安装 Ollama 和推荐本地模型。
2. 本地模型安装完成后，双击 Start_Koto.bat 或 Koto.exe。
3. 首次启动时按向导填写 Gemini API Key。
4. 后续直接双击 Start_Koto.bat 即可使用。

目录说明：
- Koto.exe: 主程序
- Start_Koto.bat: 推荐启动入口
- Stop_Koto.bat: 关闭 Koto
- Install_Local_Model.bat: 本地模型安装入口
- LocalModelInstaller.exe: 独立本地模型安装器

分发建议：
1. 将整个 Koto_Portable 文件夹压缩为 zip 后发送。
2. 收件人解压到任意本地目录即可使用。
3. 不建议直接在压缩包内运行。
"""


def find_local_installer() -> Path | None:
    for candidate in LOCAL_INSTALLER_CANDIDATES:
        if candidate.exists():
            return candidate
    return None


def _handle_remove_error(func, path, exc_info) -> None:
    try:
        os.chmod(path, stat.S_IWRITE)
    except OSError:
        pass
    func(path)


def ensure_clean_output(output_dir: Path) -> None:
    if output_dir.exists():
        last_error = None
        for _ in range(3):
            try:
                shutil.rmtree(output_dir, onerror=_handle_remove_error)
                break
            except OSError as exc:
                last_error = exc
                time.sleep(0.5)
        if output_dir.exists():
            raise last_error or OSError(f"无法清理输出目录: {output_dir}")
    output_dir.mkdir(parents=True, exist_ok=True)


def write_text(path: Path, content: str) -> None:
    path.write_text(content, encoding="utf-8")


def create_launchers(output_dir: Path, include_installer: bool) -> None:
    write_text(
        output_dir / "Start_Koto.bat",
        "@echo off\n"
        "setlocal\n"
        "cd /d \"%~dp0\"\n"
        "start \"\" \"%~dp0Koto.exe\"\n"
        "endlocal\n",
    )

    write_text(
        output_dir / "Stop_Koto.bat",
        "@echo off\n"
        "setlocal EnableDelayedExpansion\n"
        "cd /d \"%~dp0\"\n"
        "set \"LOCK_FILE=%~dp0.koto.lock\"\n"
        "if exist \"%LOCK_FILE%\" (\n"
        "  set /p LOCKED_PID=<\"%LOCK_FILE%\"\n"
        "  if defined LOCKED_PID if not \"!LOCKED_PID!\"==\"starting\" taskkill /F /PID !LOCKED_PID! >nul 2>&1\n"
        "  del /F \"%LOCK_FILE%\" >nul 2>&1\n"
        ")\n"
        "taskkill /F /IM Koto.exe >nul 2>&1\n"
        "endlocal\n",
    )

    if include_installer:
        install_content = (
            "@echo off\n"
            "setlocal\n"
            "cd /d \"%~dp0\"\n"
            "start \"\" \"%~dp0LocalModelInstaller.exe\"\n"
            "endlocal\n"
        )
    else:
        install_content = (
            "@echo off\n"
            "echo [ERROR] 当前便携包未包含 LocalModelInstaller.exe\n"
            "echo 请先在开发机执行 pyinstaller local_model_installer.spec --clean -y\n"
            "pause\n"
        )
    write_text(output_dir / "Install_Local_Model.bat", install_content)


def build_portable_bundle(output_dir: Path, strict_installer: bool) -> None:
    if not APP_BUILD_DIR.exists():
        raise FileNotFoundError(f"未找到主程序构建目录: {APP_BUILD_DIR}")

    installer_path = find_local_installer()
    if strict_installer and installer_path is None:
        raise FileNotFoundError("未找到 LocalModelInstaller.exe，请先构建本地模型安装器")

    ensure_clean_output(output_dir)
    shutil.copytree(APP_BUILD_DIR, output_dir, dirs_exist_ok=True)

    if installer_path is not None:
        shutil.copy2(installer_path, output_dir / "LocalModelInstaller.exe")

    create_launchers(output_dir, installer_path is not None)
    write_text(output_dir / "README_便携版.txt", PORTABLE_README)

    docs_src = ROOT / "docs" / "PORTABLE_RELEASE_GUIDE.md"
    if docs_src.exists():
        shutil.copy2(docs_src, output_dir / "PORTABLE_RELEASE_GUIDE.md")


def main() -> None:
    parser = argparse.ArgumentParser(description="组装 Koto Windows 便携分发目录")
    parser.add_argument(
        "--output",
        default=str(OUTPUT_DIR),
        help="输出目录，默认 dist/Koto_Portable",
    )
    parser.add_argument(
        "--allow-missing-installer",
        action="store_true",
        help="允许在缺少 LocalModelInstaller.exe 时继续组装",
    )
    args = parser.parse_args()

    output_dir = Path(args.output).resolve()
    build_portable_bundle(output_dir, strict_installer=not args.allow_missing_installer)

    print(f"✅ 便携包已生成: {output_dir}")
    print("建议将该目录压缩为 zip 后发送给 Windows 用户。")


if __name__ == "__main__":
    main()
