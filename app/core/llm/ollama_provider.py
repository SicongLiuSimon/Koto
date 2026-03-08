#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Koto Ollama Provider
====================
为 web/app.py 提供与 Gemini 客户端完全兼容的 Ollama 代理对象。
当 user_settings.json 中 model_mode == "local" 时，Koto 将自动使用此模块。

设计原则：
  - OllamaClientProxy.models 与 genai.Client().models 接口一致
  - 支持 generate_content() 和 generate_content_stream()
  - 将 Gemini contents/config 格式自动转换为 Ollama /api/chat 格式
  - 返回带有 .text / .candidates 的 Gemini 兼容响应对象
  - 自动启动 Ollama 服务（若未运行）
"""

from __future__ import annotations

import json
import logging
import os
import platform
import shutil
import socket
import subprocess
import time
import urllib.request
import urllib.error
from pathlib import Path
from typing import Any, Dict, Generator, Iterator, List, Optional

logger = logging.getLogger(__name__)

_OLLAMA_BASE_URL = os.environ.get("OLLAMA_BASE_URL", "http://localhost:11434")
_STARTUP_TIMEOUT = 30  # 等待 Ollama 启动的最长秒数


# ════════════════════════════════════════════════════════════════════════
# Gemini 响应兼容结构（让 web/app.py 的代码无需修改）
# ════════════════════════════════════════════════════════════════════════

class _OllamaPart:
    """模拟 Gemini types.Part"""
    def __init__(self, text: str = ""):
        self.text = text
        self.function_call = None
        self.function_response = None
        self.inline_data = None


class _OllamaContent:
    """模拟 Gemini types.Content"""
    def __init__(self, role: str, text: str):
        self.role = role
        self.parts = [_OllamaPart(text=text)]


class _OllamaCandidate:
    """模拟 Gemini types.Candidate"""
    def __init__(self, text: str):
        self.content = _OllamaContent("model", text)
        self.finish_reason = "STOP"
        self.index = 0
        self.grounding_metadata = None


class _OllamaUsageMetadata:
    """模拟 Gemini types.UsageMetadata"""
    def __init__(self, prompt_tokens: int = 0, completion_tokens: int = 0):
        self.prompt_token_count = prompt_tokens
        self.candidates_token_count = completion_tokens
        self.total_token_count = prompt_tokens + completion_tokens


class OllamaResponse:
    """
    模拟 google.genai GenerateContentResponse。
    web/app.py 通常访问:
      response.text
      response.candidates[0].content.parts[0].text
      response.usage_metadata
    """
    def __init__(self, text: str, prompt_tokens: int = 0, completion_tokens: int = 0):
        self._text = text
        self.candidates = [_OllamaCandidate(text)]
        self.usage_metadata = _OllamaUsageMetadata(prompt_tokens, completion_tokens)
        self.prompt_feedback = None

    @property
    def text(self) -> str:
        return self._text

    def __str__(self):
        return self._text


class OllamaStreamChunk:
    """
    模拟流式 Gemini 响应块。
    web/app.py 访问:
      chunk.text
      chunk.candidates[0].content.parts[0].text
    """
    def __init__(self, delta: str):
        self._delta = delta
        self.candidates = [_OllamaCandidate(delta)]
        self.usage_metadata = None

    @property
    def text(self) -> str:
        return self._delta


# ════════════════════════════════════════════════════════════════════════
# Contents / Config 格式转换
# ════════════════════════════════════════════════════════════════════════

def _extract_text_from_part(part: Any) -> str:
    """从 Gemini Part 对象/字典中提取纯文本"""
    if part is None:
        return ""
    if isinstance(part, str):
        return part
    # 对象属性
    if hasattr(part, "text") and part.text:
        return part.text
    # 字典
    if isinstance(part, dict):
        return part.get("text") or part.get("content") or ""
    return str(part)


def _gemini_contents_to_ollama_messages(
    contents: Any,
    system_instruction: Optional[str] = None,
) -> List[Dict[str, str]]:
    """
    将 Gemini contents 格式转换为 Ollama /api/chat messages 格式。

    Gemini contents 可以是：
      - str
      - [{"role": "user", "parts": [{"text": "..."}]}, ...]
      - [types.Content(role=..., parts=[types.Part(text=...)]), ...]
      - [{"role": "user", "content": "..."}]  (简化格式)
    """
    messages: List[Dict[str, str]] = []

    # 加入 system prompt
    if system_instruction:
        messages.append({"role": "system", "content": system_instruction})

    if contents is None:
        return messages

    if isinstance(contents, str):
        messages.append({"role": "user", "content": contents})
        return messages

    if not isinstance(contents, (list, tuple)):
        # 单个 Content 对象
        contents = [contents]

    for item in contents:
        if item is None:
            continue

        # 字典格式
        if isinstance(item, dict):
            role = item.get("role", "user")
            # 直接 content 字段（简化格式）
            if "content" in item and isinstance(item["content"], str):
                messages.append({"role": _map_role(role), "content": item["content"]})
                continue
            # parts 格式
            parts = item.get("parts", [])
            if isinstance(parts, list):
                text = " ".join(_extract_text_from_part(p) for p in parts if p)
            else:
                text = _extract_text_from_part(parts)
            messages.append({"role": _map_role(role), "content": text})
            continue

        # Gemini types.Content 对象
        role = getattr(item, "role", "user")
        parts = getattr(item, "parts", [])
        if isinstance(parts, (list, tuple)):
            text = " ".join(_extract_text_from_part(p) for p in parts if p)
        else:
            text = _extract_text_from_part(parts)
        messages.append({"role": _map_role(role), "content": text})

    return messages


def _map_role(role: str) -> str:
    """映射 Gemini 角色名到 Ollama 角色名"""
    mapping = {
        "model": "assistant",
        "assistant": "assistant",
        "user": "user",
        "system": "system",
        "tool": "tool",
        "function": "tool",
    }
    return mapping.get((role or "user").lower(), "user")


def _extract_config_params(config: Any) -> Dict[str, Any]:
    """从 Gemini GenerateContentConfig 提取参数"""
    params: Dict[str, Any] = {}
    if config is None:
        return params

    # system_instruction
    si = getattr(config, "system_instruction", None)
    if si:
        if hasattr(si, "parts"):
            params["system_instruction"] = " ".join(
                _extract_text_from_part(p) for p in si.parts if p
            )
        elif isinstance(si, str):
            params["system_instruction"] = si
        else:
            params["system_instruction"] = str(si)

    # 生成参数
    for attr, ollama_key in [
        ("temperature", "temperature"),
        ("top_p", "top_p"),
        ("top_k", "top_k"),
        ("max_output_tokens", "num_predict"),
    ]:
        val = getattr(config, attr, None)
        if val is not None:
            params[ollama_key] = val

    return params


# ════════════════════════════════════════════════════════════════════════
# Ollama 服务管理
# ════════════════════════════════════════════════════════════════════════

def _is_ollama_running(base_url: str = _OLLAMA_BASE_URL) -> bool:
    """检测 Ollama 是否正在运行"""
    try:
        host, port_str = base_url.split("://", 1)[1].rsplit(":", 1)
        sock = socket.create_connection((host, int(port_str)), timeout=2)
        sock.close()
        return True
    except Exception:
        return False


def _start_ollama_if_needed(base_url: str = _OLLAMA_BASE_URL) -> bool:
    """尝试启动 Ollama 服务（如果未运行）"""
    if _is_ollama_running(base_url):
        return True

    ollama_path = shutil.which("ollama")
    if not ollama_path:
        logger.warning("[OllamaProvider] ollama 命令不在 PATH 中，无法自动启动")
        return False

    logger.info("[OllamaProvider] 正在启动 Ollama 服务...")
    try:
        creationflags = (
            subprocess.CREATE_NO_WINDOW
            if platform.system() == "Windows"
            else 0
        )
        subprocess.Popen(
            ["ollama", "serve"],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            creationflags=creationflags,
        )
        for _ in range(_STARTUP_TIMEOUT):
            time.sleep(1)
            if _is_ollama_running(base_url):
                logger.info("[OllamaProvider] ✅ Ollama 服务已启动")
                return True
    except Exception as e:
        logger.error(f"[OllamaProvider] 启动 Ollama 失败: {e}")

    logger.warning("[OllamaProvider] 等待超时，Ollama 服务未能启动")
    return False


# ════════════════════════════════════════════════════════════════════════
# HTTP 调用（不依赖 requests 库，使用 urllib 以保证打包兼容性）
# ════════════════════════════════════════════════════════════════════════

def _ollama_chat(
    model: str,
    messages: List[Dict[str, str]],
    options: Optional[Dict] = None,
    base_url: str = _OLLAMA_BASE_URL,
    stream: bool = False,
) -> Any:
    """
    调用 Ollama /api/chat 端点。
    非流式：返回完整的 JSON 响应字典。
    流式：返回迭代 JSON 行的生成器。
    """
    payload = {
        "model": model,
        "messages": messages,
        "stream": stream,
    }
    if options:
        payload["options"] = options

    data = json.dumps(payload).encode("utf-8")
    url = f"{base_url.rstrip('/')}/api/chat"

    req = urllib.request.Request(
        url,
        data=data,
        method="POST",
        headers={"Content-Type": "application/json"},
    )

    if stream:
        return _stream_ollama_response(req)
    else:
        try:
            with urllib.request.urlopen(req, timeout=180) as resp:
                return json.loads(resp.read().decode("utf-8"))
        except urllib.error.HTTPError as e:
            body = e.read().decode("utf-8", errors="replace")
            raise RuntimeError(f"Ollama HTTP {e.code}: {body}") from e
        except Exception as e:
            raise RuntimeError(f"Ollama 请求失败: {e}") from e


def _stream_ollama_response(req: urllib.request.Request) -> Generator[str, None, None]:
    """流式读取 Ollama 响应，逐行 yield delta 文本"""
    try:
        with urllib.request.urlopen(req, timeout=180) as resp:
            for raw_line in resp:
                line = raw_line.decode("utf-8", errors="replace").strip()
                if not line:
                    continue
                try:
                    chunk = json.loads(line)
                    delta = chunk.get("message", {}).get("content", "")
                    if delta:
                        yield delta
                    if chunk.get("done", False):
                        break
                except json.JSONDecodeError:
                    continue
    except urllib.error.HTTPError as e:
        body = e.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"Ollama 流式请求失败 HTTP {e.code}: {body}") from e
    except Exception as e:
        raise RuntimeError(f"Ollama 流式请求失败: {e}") from e


# ════════════════════════════════════════════════════════════════════════
# Gemini 兼容代理
# ════════════════════════════════════════════════════════════════════════

class OllamaModelsProxy:
    """
    模拟 genai.Client().models 接口。
    提供 generate_content() 和 generate_content_stream()。
    """

    def __init__(self, model_tag: str, base_url: str = _OLLAMA_BASE_URL):
        self._model_tag = model_tag
        self._base_url = base_url

    def _ensure_running(self):
        """确保 Ollama 服务运行，失败时抛出异常"""
        if not _start_ollama_if_needed(self._base_url):
            raise RuntimeError(
                "Ollama 服务未运行。请先安装并启动 Ollama（https://ollama.com）。"
            )

    def generate_content(
        self,
        model: Optional[str] = None,
        contents: Any = None,
        config: Any = None,
        **kwargs,
    ) -> OllamaResponse:
        """非流式生成（返回 OllamaResponse，接口兼容 Gemini 响应）"""
        self._ensure_running()

        # 始终使用本地安装的模型 tag，忽略 Gemini 模型名称（如 gemini-3-flash-preview）
        target_model = self._model_tag
        config_params = _extract_config_params(config)
        system_instruction = config_params.pop("system_instruction", None)

        messages = _gemini_contents_to_ollama_messages(
            contents, system_instruction=system_instruction
        )

        # Ollama options（生成参数）
        options = {k: v for k, v in config_params.items() if v is not None} or None

        try:
            resp_json = _ollama_chat(
                model=target_model,
                messages=messages,
                options=options,
                base_url=self._base_url,
                stream=False,
            )
        except RuntimeError as e:
            logger.error(f"[OllamaProvider] generate_content 失败: {e}")
            raise

        text = resp_json.get("message", {}).get("content", "")
        prompt_tokens = resp_json.get("prompt_eval_count", 0)
        completion_tokens = resp_json.get("eval_count", 0)

        return OllamaResponse(
            text=text,
            prompt_tokens=prompt_tokens,
            completion_tokens=completion_tokens,
        )

    def generate_content_stream(
        self,
        model: Optional[str] = None,
        contents: Any = None,
        config: Any = None,
        **kwargs,
    ) -> Iterator[OllamaStreamChunk]:
        """
        流式生成（返回 OllamaStreamChunk 迭代器）。
        web/app.py 的 SSE 流处理会访问 chunk.text。
        """
        self._ensure_running()

        # 始终使用本地安装的模型 tag
        target_model = self._model_tag
        config_params = _extract_config_params(config)
        system_instruction = config_params.pop("system_instruction", None)

        messages = _gemini_contents_to_ollama_messages(
            contents, system_instruction=system_instruction
        )
        options = {k: v for k, v in config_params.items() if v is not None} or None

        url = f"{self._base_url.rstrip('/')}/api/chat"
        payload = {
            "model": target_model,
            "messages": messages,
            "stream": True,
        }
        if options:
            payload["options"] = options

        import urllib.request as _ureq
        data = json.dumps(payload).encode("utf-8")
        req = _ureq.Request(
            url,
            data=data,
            method="POST",
            headers={"Content-Type": "application/json"},
        )

        try:
            with _ureq.urlopen(req, timeout=180) as resp:
                for raw_line in resp:
                    line = raw_line.decode("utf-8", errors="replace").strip()
                    if not line:
                        continue
                    try:
                        chunk = json.loads(line)
                        delta = chunk.get("message", {}).get("content", "")
                        if delta:
                            yield OllamaStreamChunk(delta=delta)
                        if chunk.get("done", False):
                            break
                    except json.JSONDecodeError:
                        continue
        except Exception as e:
            logger.error(f"[OllamaProvider] 流式生成失败: {e}")
            raise RuntimeError(f"Ollama 流式生成失败: {e}") from e

    def __getattr__(self, name: str):
        """对 count_tokens 等未实现方法返回假实现，避免崩溃"""
        def _stub(*args, **kwargs):
            logger.debug(f"[OllamaProvider] models.{name}() 未实现，返回空响应")
            return None
        return _stub


class OllamaClientProxy:
    """
    顶层代理对象，完全模拟 google.genai.Client。
    web/app.py 访问: client.models.generate_content(...)
    """

    def __init__(self, model_tag: str, base_url: str = _OLLAMA_BASE_URL):
        self.models = OllamaModelsProxy(model_tag=model_tag, base_url=base_url)
        self._model_tag = model_tag
        self._base_url = base_url
        logger.info(f"[OllamaProvider] 初始化完成 — 模型: {model_tag}")

    def __getattr__(self, name: str):
        """对未实现属性返回 None 或空对象，保持兼容性"""
        return None


# ════════════════════════════════════════════════════════════════════════
# 工厂函数
# ════════════════════════════════════════════════════════════════════════

def create_ollama_client(
    model_tag: Optional[str] = None,
    base_url: str = _OLLAMA_BASE_URL,
) -> OllamaClientProxy:
    """
    创建 OllamaClientProxy。
    model_tag 为 None 时，尝试从 config/user_settings.json 自动读取。
    """
    if model_tag is None:
        model_tag = _resolve_model_from_settings()

    if not model_tag:
        raise ValueError("未指定 Ollama 模型，且 user_settings.json 中未配置 local_model")

    return OllamaClientProxy(model_tag=model_tag, base_url=base_url)


def _resolve_model_from_settings() -> Optional[str]:
    """从 user_settings.json 读取 local_model 字段"""
    try:
        # 兼容打包/开发两种路径
        import sys
        if getattr(sys, "frozen", False):
            root = Path(sys.executable).parent
        else:
            root = Path(__file__).parent.parent.parent.parent  # Koto root

        settings_path = root / "config" / "user_settings.json"
        if settings_path.exists():
            with open(settings_path, "r", encoding="utf-8") as f:
                data = json.load(f)
            return data.get("local_model") or data.get("ai", {}).get("local_model")
    except Exception as e:
        logger.warning(f"[OllamaProvider] 读取 user_settings.json 失败: {e}")
    return None


def get_local_model_info() -> Dict[str, Any]:
    """
    返回当前本地模型配置信息。
    供 web/app.py 的状态接口使用。
    """
    try:
        import sys
        if getattr(sys, "frozen", False):
            root = Path(sys.executable).parent
        else:
            root = Path(__file__).parent.parent.parent.parent

        settings_path = root / "config" / "user_settings.json"
        if not settings_path.exists():
            return {"mode": "cloud", "model": None, "running": False}

        with open(settings_path, "r", encoding="utf-8") as f:
            data = json.load(f)

        model_mode = data.get("model_mode", "cloud")
        local_model = data.get("local_model")
        running = _is_ollama_running() if model_mode == "local" else False

        return {
            "mode": model_mode,
            "model": local_model,
            "running": running,
            "ollama_installed": shutil.which("ollama") is not None,
        }
    except Exception:
        return {"mode": "cloud", "model": None, "running": False}
