"""
SystemToolsPlugin — Python 代码执行 & 包管理

从 web/adaptive_agent.py 的 python_exec / package_mgmt 工具迁移而来,
适配 UnifiedAgent 插件体系。
"""

import ast
import importlib
import logging
import os
import subprocess
import sys
import traceback
from typing import Any, Dict, List

from app.core.agent.base import AgentPlugin

logger = logging.getLogger(__name__)

_BLOCKED_AST_NODES = (ast.Import, ast.ImportFrom)
_BLOCKED_NAMES = frozenset(
    {
        "os",
        "sys",
        "subprocess",
        "shutil",
        "pathlib",
        "socket",
        "ctypes",
        "signal",
        "multiprocessing",
        "threading",
        "__import__",
        "breakpoint",
    }
)
_BLOCKED_ATTRS = frozenset(
    {
        "__builtins__",
        "__import__",
        "__subclasses__",
        "__globals__",
        "__code__",
        "__closure__",
        "__class__",
        "__bases__",
        "__mro__",
    }
)
_BLOCKED_BUILTINS = frozenset(
    {
        "exec",
        "eval",
        "compile",
        "open",
        "input",
        "__import__",
        "getattr",
        "setattr",
        "delattr",
        "globals",
        "locals",
        "vars",
        "exit",
        "quit",
        "breakpoint",
        "memoryview",
    }
)
_SAFE_BUILTINS = {
    k: v
    for k, v in (
        __builtins__.items()
        if isinstance(__builtins__, dict)
        else ((a, getattr(__builtins__, a)) for a in dir(__builtins__))
    )
    if k not in _BLOCKED_BUILTINS and not k.startswith("_")
}


def _validate_code_ast(code: str):
    """Return error message if code is dangerous, else None."""
    try:
        tree = ast.parse(code)
    except SyntaxError as e:
        return f"Syntax error: {e}"
    for node in ast.walk(tree):
        if isinstance(node, _BLOCKED_AST_NODES):
            return "Import statements are not allowed in sandboxed execution."
        if isinstance(node, ast.Name) and node.id in _BLOCKED_NAMES:
            return f"Access to '{node.id}' is not allowed."
        if isinstance(node, ast.Attribute) and node.attr in _BLOCKED_ATTRS:
            return f"Access to '{node.attr}' is not allowed."
    return None


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
                            "description": "Python source code to execute.",
                        }
                    },
                    "required": ["code"],
                },
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
                            "description": "Comma-separated package names, e.g. 'pandas,numpy'.",
                        }
                    },
                    "required": ["packages"],
                },
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
                            "description": "Comma-separated package names to check.",
                        }
                    },
                    "required": ["packages"],
                },
            },
        ]

    # ------------------------------------------------------------------
    # Tool implementations
    # ------------------------------------------------------------------

    @staticmethod
    def python_exec(code: str) -> str:
        """Execute Python code in a restricted namespace and capture stdout."""
        import contextlib
        import io

        # AST validation before execution
        error = _validate_code_ast(code)
        if error:
            return f"Blocked: {error}"

        stdout_capture = io.StringIO()
        exec_globals: Dict[str, Any] = {"__builtins__": _SAFE_BUILTINS}

        try:
            with contextlib.redirect_stdout(stdout_capture):
                exec(code, exec_globals)  # nosec B102
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
            if getattr(sys, "frozen", False):
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
