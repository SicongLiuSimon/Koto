"""
SystemToolsPlugin — Python 代码执行 & 包管理

从 web/adaptive_agent.py 的 python_exec / package_mgmt 工具迁移而来,
适配 UnifiedAgent 插件体系。
"""

import os
import sys
import subprocess
import importlib
import traceback
from typing import Any, Dict, List

from app.core.agent.base import AgentPlugin


class SystemToolsPlugin(AgentPlugin):
    """提供 Python 代码执行和包管理能力。"""

    @property
    def name(self) -> str:
        return "SystemTools"

    @property
    def description(self) -> str:
        return "Execute Python code snippets and manage pip packages."

    def get_tools(self) -> List[Dict[str, Any]]:
        return [
            {
                "name": "python_exec",
                "func": self.python_exec,
                "description": "Execute a Python code snippet and return the printed output. "
                               "Use `print()` to expose results.",
                "parameters": {
                    "type": "OBJECT",
                    "properties": {
                        "code": {
                            "type": "STRING",
                            "description": "Python source code to execute."
                        }
                    },
                    "required": ["code"]
                }
            },
            {
                "name": "pip_install",
                "func": self.pip_install,
                "description": "Install one or more Python packages via pip.",
                "parameters": {
                    "type": "OBJECT",
                    "properties": {
                        "packages": {
                            "type": "STRING",
                            "description": "Comma-separated package names, e.g. 'pandas,numpy'."
                        }
                    },
                    "required": ["packages"]
                }
            },
            {
                "name": "pip_check",
                "func": self.pip_check,
                "description": "Check if Python packages are importable. "
                               "Returns a list of missing packages.",
                "parameters": {
                    "type": "OBJECT",
                    "properties": {
                        "packages": {
                            "type": "STRING",
                            "description": "Comma-separated package names to check."
                        }
                    },
                    "required": ["packages"]
                }
            },
        ]

    # ------------------------------------------------------------------
    # Tool implementations
    # ------------------------------------------------------------------

    @staticmethod
    def python_exec(code: str) -> str:
        """Execute Python code in a restricted namespace and capture stdout."""
        import io
        import contextlib

        stdout_capture = io.StringIO()
        exec_globals: Dict[str, Any] = {"__builtins__": __builtins__}

        try:
            with contextlib.redirect_stdout(stdout_capture):
                exec(code, exec_globals)
            output = stdout_capture.getvalue()
            return output if output else "(code executed successfully, no output)"
        except Exception as exc:
            tb = traceback.format_exc()
            return f"Execution error:\n{tb}"

    @staticmethod
    def pip_install(packages: str) -> str:
        """Install packages using pip."""
        pkg_list = [p.strip() for p in packages.split(",") if p.strip()]
        if not pkg_list:
            return "Error: no packages specified."

        results: List[str] = []
        for pkg in pkg_list:
            if getattr(sys, 'frozen', False):
                results.append(f"❌ {pkg}: 打包版不支持安装新包，请联系开发者。")
                continue
            try:
                proc = subprocess.run(
                    [sys.executable, "-m", "pip", "install", pkg],
                    capture_output=True,
                    text=True,
                    timeout=300,
                )
                if proc.returncode == 0:
                    results.append(f"✅ {pkg} installed successfully.")
                else:
                    results.append(f"❌ {pkg} failed: {proc.stderr.strip()[:300]}")
            except subprocess.TimeoutExpired:
                results.append(f"❌ {pkg}: installation timed out.")
            except Exception as exc:
                results.append(f"❌ {pkg}: {exc}")

        return "\n".join(results)

    @staticmethod
    def pip_check(packages: str) -> str:
        """Check whether packages are importable."""
        pkg_list = [p.strip() for p in packages.split(",") if p.strip()]
        missing: List[str] = []
        available: List[str] = []

        for pkg in pkg_list:
            try:
                importlib.import_module(pkg)
                available.append(pkg)
            except ImportError:
                missing.append(pkg)

        parts = []
        if available:
            parts.append(f"Available: {', '.join(available)}")
        if missing:
            parts.append(f"Missing: {', '.join(missing)}")
        return " | ".join(parts) if parts else "No packages checked."
