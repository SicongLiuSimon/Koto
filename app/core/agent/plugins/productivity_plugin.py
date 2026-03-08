"""
ProductivityPlugin — 补全 Koto 缺失的高优先级本地能力

新增工具：
  • list_directory      — 浏览文件夹内容
  • open_file_or_folder — 用系统默认程序打开文件/文件夹
  • shell_command        — 执行 shell 命令（有白名单保护）
  • get_clipboard_text   — 读取当前剪贴板文本
  • set_clipboard_text   — 写入文本到剪贴板
  • take_screenshot       — 截取全屏并保存
  • send_email            — 通过 SMTP 发送邮件
  • move_file            — 移动/重命名文件
  • delete_file          — 删除文件（移入回收站）
  • zip_files            — 压缩文件/文件夹
  • unzip_file           — 解压 zip 包
"""

import logging
import os
import subprocess
import shutil
from pathlib import Path
from typing import Any, Dict, List

from app.core.agent.base import AgentPlugin

logger = logging.getLogger(__name__)

# 工作区根目录（可通过环境变量覆盖）
_WORKSPACE = os.environ.get("KOTO_WORKSPACE", os.path.join(os.path.dirname(__file__), "../../../../workspace"))
_WORKSPACE = os.path.abspath(_WORKSPACE)

# shell 命令白名单（可安全执行的前缀）
_ALLOWED_COMMANDS = {
    "dir", "ls", "echo", "ping", "ipconfig", "ifconfig",
    "python", "pip", "git", "curl", "wget", "cat",
    "type", "find", "grep", "cd", "pwd", "hostname",
    "tasklist", "ps", "netstat", "nslookup",
}


class ProductivityPlugin(AgentPlugin):

    @property
    def name(self) -> str:
        return "Productivity"

    @property
    def description(self) -> str:
        return "Local productivity tools: file browser, clipboard, screenshot, email, shell commands."

    def get_tools(self) -> List[Dict[str, Any]]:
        return [
            {
                "name": "list_directory",
                "func": self.list_directory,
                "description": "列出指定目录的文件和子文件夹（不填则列出工作区根目录）",
                "parameters": {
                    "type": "OBJECT",
                    "properties": {
                        "path": {
                            "type": "STRING",
                            "description": "目录路径（相对于工作区或绝对路径，默认工作区根目录）"
                        },
                        "show_hidden": {
                            "type": "BOOLEAN",
                            "description": "是否显示隐藏文件（默认 false）"
                        }
                    }
                }
            },
            {
                "name": "open_file_or_folder",
                "func": self.open_file_or_folder,
                "description": "用系统默认程序打开文件或文件夹（如用 Excel 打开 .xlsx，用资源管理器打开目录）",
                "parameters": {
                    "type": "OBJECT",
                    "properties": {
                        "path": {
                            "type": "STRING",
                            "description": "文件或文件夹路径"
                        }
                    },
                    "required": ["path"]
                }
            },
            {
                "name": "shell_command",
                "func": self.shell_command,
                "description": "在本机执行 shell 命令并返回输出（支持 dir/ls/ping/git/python 等安全命令）",
                "parameters": {
                    "type": "OBJECT",
                    "properties": {
                        "command": {
                            "type": "STRING",
                            "description": "要执行的 shell 命令（如 'git status'、'pip list'）"
                        },
                        "cwd": {
                            "type": "STRING",
                            "description": "执行命令的工作目录（可选，默认工作区根目录）"
                        },
                        "timeout": {
                            "type": "INTEGER",
                            "description": "超时秒数（默认 15，最大 60）"
                        }
                    },
                    "required": ["command"]
                }
            },
            {
                "name": "get_clipboard_text",
                "func": self.get_clipboard_text,
                "description": "读取当前剪贴板中的文本内容",
                "parameters": {
                    "type": "OBJECT",
                    "properties": {}
                }
            },
            {
                "name": "set_clipboard_text",
                "func": self.set_clipboard_text,
                "description": "将文本写入剪贴板，方便用户粘贴使用",
                "parameters": {
                    "type": "OBJECT",
                    "properties": {
                        "text": {
                            "type": "STRING",
                            "description": "要写入剪贴板的文本"
                        }
                    },
                    "required": ["text"]
                }
            },
            {
                "name": "take_screenshot",
                "func": self.take_screenshot,
                "description": "截取全屏或指定窗口截图，保存到工作区并返回文件路径",
                "parameters": {
                    "type": "OBJECT",
                    "properties": {
                        "filename": {
                            "type": "STRING",
                            "description": "保存的文件名（不含扩展名，默认按时间戳命名）"
                        }
                    }
                }
            },
            {
                "name": "send_email",
                "func": self.send_email,
                "description": "通过 SMTP 发送邮件（需预先在 config/user_settings.json 中配置 email_smtp_*）",
                "parameters": {
                    "type": "OBJECT",
                    "properties": {
                        "to": {
                            "type": "STRING",
                            "description": "收件人地址（多个用逗号分隔）"
                        },
                        "subject": {
                            "type": "STRING",
                            "description": "邮件主题"
                        },
                        "body": {
                            "type": "STRING",
                            "description": "邮件正文（支持纯文本或 HTML）"
                        },
                        "is_html": {
                            "type": "BOOLEAN",
                            "description": "正文是否为 HTML 格式（默认 false）"
                        },
                        "cc": {
                            "type": "STRING",
                            "description": "抄送地址（可选）"
                        }
                    },
                    "required": ["to", "subject", "body"]
                }
            },
            {
                "name": "move_file",
                "func": self.move_file,
                "description": "移动或重命名文件/文件夹",
                "parameters": {
                    "type": "OBJECT",
                    "properties": {
                        "source": {
                            "type": "STRING",
                            "description": "源文件路径"
                        },
                        "destination": {
                            "type": "STRING",
                            "description": "目标路径（路径或新名称）"
                        }
                    },
                    "required": ["source", "destination"]
                }
            },
            {
                "name": "delete_file",
                "func": self.delete_file,
                "description": "删除文件或空文件夹（移入回收站，如不支持则直接删除）",
                "parameters": {
                    "type": "OBJECT",
                    "properties": {
                        "path": {
                            "type": "STRING",
                            "description": "要删除的文件/文件夹路径"
                        },
                        "confirm": {
                            "type": "BOOLEAN",
                            "description": "必须为 true 才会执行删除（安全确认）"
                        }
                    },
                    "required": ["path", "confirm"]
                }
            },
            {
                "name": "zip_files",
                "func": self.zip_files,
                "description": "将一个或多个文件/文件夹压缩为 zip",
                "parameters": {
                    "type": "OBJECT",
                    "properties": {
                        "paths": {
                            "type": "ARRAY",
                            "description": "要压缩的文件/文件夹路径列表",
                            "items": {"type": "STRING"}
                        },
                        "output_name": {
                            "type": "STRING",
                            "description": "输出 zip 文件名（不含扩展名，默认 'archive'）"
                        }
                    },
                    "required": ["paths"]
                }
            },
            {
                "name": "unzip_file",
                "func": self.unzip_file,
                "description": "解压 zip 文件到指定目录",
                "parameters": {
                    "type": "OBJECT",
                    "properties": {
                        "zip_path": {
                            "type": "STRING",
                            "description": "zip 文件路径"
                        },
                        "extract_to": {
                            "type": "STRING",
                            "description": "解压目标目录（可选，默认在 zip 同级目录）"
                        }
                    },
                    "required": ["zip_path"]
                }
            },
        ]

    # ─────────────────────────── 实现 ────────────────────────────────────────

    def _resolve_path(self, path: str) -> Path:
        p = Path(path)
        if not p.is_absolute():
            p = Path(_WORKSPACE) / p
        return p.resolve()

    # ── list_directory ────────────────────────────────────────────────────────
    def list_directory(self, path: str = "", show_hidden: bool = False) -> str:
        target = self._resolve_path(path) if path else Path(_WORKSPACE)
        if not target.exists():
            return f"错误：路径不存在 → {target}"
        if not target.is_dir():
            return f"错误：{target} 不是目录"

        items = []
        for entry in sorted(target.iterdir()):
            if not show_hidden and entry.name.startswith("."):
                continue
            kind = "/" if entry.is_dir() else ""
            try:
                size = "" if entry.is_dir() else f"  {entry.stat().st_size:,} bytes"
            except Exception:
                size = ""
            items.append(f"{'[DIR] ' if entry.is_dir() else '[FILE]'} {entry.name}{kind}{size}")

        if not items:
            return f"目录为空：{target}"
        return f"目录内容（{target}）：\n" + "\n".join(items)

    # ── open_file_or_folder ───────────────────────────────────────────────────
    def open_file_or_folder(self, path: str) -> str:
        target = self._resolve_path(path)
        if not target.exists():
            return f"错误：路径不存在 → {target}"
        try:
            os.startfile(str(target))  # Windows
            return f"已用系统默认程序打开：{target}"
        except AttributeError:
            import subprocess
            subprocess.Popen(["xdg-open", str(target)])
            return f"已打开：{target}"
        except Exception as exc:
            return f"打开失败：{exc}"

    # ── shell_command ─────────────────────────────────────────────────────────
    def shell_command(self, command: str, cwd: str = "", timeout: int = 15) -> str:
        cmd_lower = command.strip().lower()
        first_word = cmd_lower.split()[0] if cmd_lower.split() else ""

        if first_word not in _ALLOWED_COMMANDS:
            return (
                f"安全限制：命令 '{first_word}' 不在白名单中。\n"
                f"允许的命令前缀：{', '.join(sorted(_ALLOWED_COMMANDS))}"
            )

        timeout = min(int(timeout), 60)
        work_dir = str(self._resolve_path(cwd)) if cwd else _WORKSPACE

        try:
            proc = subprocess.run(
                command,
                shell=True,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                cwd=work_dir,
                timeout=timeout,
            )
            output = proc.stdout or ""
            err = proc.stderr or ""
            result_parts = []
            if output.strip():
                result_parts.append(output.strip())
            if err.strip():
                result_parts.append(f"[stderr]\n{err.strip()}")
            if proc.returncode != 0:
                result_parts.append(f"[exit code: {proc.returncode}]")
            return "\n".join(result_parts) if result_parts else "(命令执行成功，无输出)"
        except subprocess.TimeoutExpired:
            return f"命令超时（>{timeout}s）"
        except Exception as exc:
            return f"执行失败：{exc}"

    # ── clipboard ─────────────────────────────────────────────────────────────
    def get_clipboard_text(self) -> str:
        try:
            import pyperclip
            text = pyperclip.paste()
            return text if text else "(剪贴板为空)"
        except ImportError:
            return "错误：需要安装 pyperclip（pip install pyperclip）"
        except Exception as exc:
            return f"读取剪贴板失败：{exc}"

    def set_clipboard_text(self, text: str) -> str:
        try:
            import pyperclip
            pyperclip.copy(text)
            preview = text[:80] + ("..." if len(text) > 80 else "")
            return f"已写入剪贴板（{len(text)} 个字符）：{preview}"
        except ImportError:
            return "错误：需要安装 pyperclip（pip install pyperclip）"
        except Exception as exc:
            return f"写入剪贴板失败：{exc}"

    # ── screenshot ────────────────────────────────────────────────────────────
    def take_screenshot(self, filename: str = "") -> str:
        try:
            from PIL import ImageGrab
        except ImportError:
            return "错误：需要安装 Pillow（pip install Pillow）"

        from datetime import datetime as _dt
        fname = filename or f"screenshot_{_dt.now().strftime('%Y%m%d_%H%M%S')}"
        if not fname.endswith(".png"):
            fname += ".png"

        save_dir = os.path.join(_WORKSPACE, "screenshots")
        os.makedirs(save_dir, exist_ok=True)
        save_path = os.path.join(save_dir, fname)

        try:
            img = ImageGrab.grab()
            img.save(save_path)
            return f"截图已保存：{save_path}（{img.width}×{img.height}）"
        except Exception as exc:
            return f"截图失败：{exc}"

    # ── send_email ────────────────────────────────────────────────────────────
    def send_email(
        self,
        to: str,
        subject: str,
        body: str,
        is_html: bool = False,
        cc: str = "",
    ) -> str:
        import smtplib
        import json as _json
        from email.mime.multipart import MIMEMultipart
        from email.mime.text import MIMEText

        # 读取 SMTP 配置
        settings_path = os.path.join(
            os.path.dirname(__file__), "../../../../config/user_settings.json"
        )
        settings_path = os.path.abspath(settings_path)
        try:
            with open(settings_path, "r", encoding="utf-8") as f:
                settings = _json.load(f)
        except Exception:
            settings = {}

        smtp_host = settings.get("email_smtp_host") or os.environ.get("EMAIL_SMTP_HOST", "")
        smtp_port = int(settings.get("email_smtp_port") or os.environ.get("EMAIL_SMTP_PORT", 465))
        smtp_user = settings.get("email_smtp_user") or os.environ.get("EMAIL_SMTP_USER", "")
        smtp_pass = settings.get("email_smtp_pass") or os.environ.get("EMAIL_SMTP_PASS", "")
        from_addr = settings.get("email_from") or smtp_user

        if not smtp_host or not smtp_user or not smtp_pass:
            return (
                "邮件配置不完整。请在 config/user_settings.json 中设置：\n"
                "  email_smtp_host, email_smtp_port, email_smtp_user, email_smtp_pass"
            )

        msg = MIMEMultipart("alternative")
        msg["From"] = from_addr
        msg["To"] = to
        msg["Subject"] = subject
        if cc:
            msg["Cc"] = cc

        mime_type = "html" if is_html else "plain"
        msg.attach(MIMEText(body, mime_type, "utf-8"))

        recipients = [a.strip() for a in to.split(",")]
        if cc:
            recipients += [a.strip() for a in cc.split(",")]

        try:
            if smtp_port == 465:
                with smtplib.SMTP_SSL(smtp_host, smtp_port, timeout=15) as server:
                    server.login(smtp_user, smtp_pass)
                    server.sendmail(from_addr, recipients, msg.as_string())
            else:
                with smtplib.SMTP(smtp_host, smtp_port, timeout=15) as server:
                    server.starttls()
                    server.login(smtp_user, smtp_pass)
                    server.sendmail(from_addr, recipients, msg.as_string())
            return f"邮件已发送至 {to}，主题：{subject}"
        except Exception as exc:
            return f"发送邮件失败：{exc}"

    # ── move_file ─────────────────────────────────────────────────────────────
    def move_file(self, source: str, destination: str) -> str:
        src = self._resolve_path(source)
        dst = self._resolve_path(destination)
        if not src.exists():
            return f"错误：源路径不存在 → {src}"
        try:
            shutil.move(str(src), str(dst))
            return f"已移动/重命名：{src} → {dst}"
        except Exception as exc:
            return f"操作失败：{exc}"

    # ── delete_file ───────────────────────────────────────────────────────────
    def delete_file(self, path: str, confirm: bool = False) -> str:
        if not confirm:
            return "操作取消：confirm 必须为 true 才执行删除。"
        target = self._resolve_path(path)
        if not target.exists():
            return f"错误：路径不存在 → {target}"
        try:
            try:
                import send2trash
                send2trash.send2trash(str(target))
                return f"已移入回收站：{target}"
            except ImportError:
                if target.is_dir():
                    shutil.rmtree(str(target))
                else:
                    target.unlink()
                return f"已删除：{target}"
        except Exception as exc:
            return f"删除失败：{exc}"

    # ── zip_files ─────────────────────────────────────────────────────────────
    def zip_files(self, paths: List[str], output_name: str = "archive") -> str:
        import zipfile
        from datetime import datetime as _dt

        if not output_name:
            output_name = f"archive_{_dt.now().strftime('%Y%m%d_%H%M%S')}"

        save_dir = _WORKSPACE
        zip_path = os.path.join(save_dir, f"{output_name}.zip")

        try:
            with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as zf:
                for p in paths:
                    target = self._resolve_path(p)
                    if not target.exists():
                        continue
                    if target.is_file():
                        zf.write(str(target), target.name)
                    else:
                        for f in target.rglob("*"):
                            zf.write(str(f), str(f.relative_to(target.parent)))
            return f"已压缩到：{zip_path}"
        except Exception as exc:
            return f"压缩失败：{exc}"

    # ── unzip_file ────────────────────────────────────────────────────────────
    def unzip_file(self, zip_path: str, extract_to: str = "") -> str:
        import zipfile

        src = self._resolve_path(zip_path)
        if not src.exists():
            return f"错误：zip 文件不存在 → {src}"

        dest = self._resolve_path(extract_to) if extract_to else src.parent / src.stem
        dest.mkdir(parents=True, exist_ok=True)

        try:
            with zipfile.ZipFile(str(src), "r") as zf:
                zf.extractall(str(dest))
            return f"已解压到：{dest}"
        except Exception as exc:
            return f"解压失败：{exc}"
