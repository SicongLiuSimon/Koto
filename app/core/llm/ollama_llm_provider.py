# -*- coding: utf-8 -*-
"""
OllamaLLMProvider
=================
A LLMProvider implementation for Ollama that plugs directly into UnifiedAgent.

Because UnifiedAgent calls `SkillManager.inject_into_prompt()` on every turn
*before* it calls `llm.generate_content()`, all active Skills are automatically
included in the system_instruction passed here — no extra wiring needed.

Tool calling
─────────────
Passed through using Ollama's OpenAI-compatible tool format.  Works out of
the box for models that support it (qwen3, qwen2.5, llama3.1, mistral-nemo…).
If the model or Ollama version doesn't support tools, the payload is safely
ignored and the agent falls back to plain-text parsing of tool requests.

Usage
─────
    from app.core.llm.ollama_llm_provider import OllamaLLMProvider
    from app.core.agent.unified_agent import UnifiedAgent

    agent = UnifiedAgent(
        llm_provider=OllamaLLMProvider(model="qwen3:8b"),
        ...
    )
"""

from __future__ import annotations

import json
import logging
import os
import time
import urllib.request
import urllib.error
import urllib.request
from typing import Any, Dict, Generator, List, Optional, Union

from app.core.llm.base import LLMProvider

logger = logging.getLogger(__name__)

_OLLAMA_BASE_URL = os.environ.get("OLLAMA_BASE_URL", "http://localhost:11434")

# ── Koto Skill 元认知前言 ────────────────────────────────────────────────────
# 注入在 system prompt 最前面，让本地模型理解 Koto Skills 框架。
# 只在确实有激活 Skills 时插入（通过检测 Skills 分隔符来判断）。
_KOTO_SKILL_PREAMBLE = """\
你正在 Koto AI 助手平台上运行。
本系统消息中，以「## 🎯 当前激活的 Skills」开头的部分是用户为本次对话激活的功能模块（Skills）。
每个 Skill 以「## [emoji] ...」标题开头，并附有具体的格式与行为要求。
你必须严格遵循所有 Skill 的全部规则，它们的优先级高于你的默认行为风格。
如果多个 Skill 同时存在，请同时满足所有要求，不要忽略其中任何一个。
---
"""
_SKILL_BLOCK_MARKER = "## 🎯 当前激活的 Skills"


# ─────────────────────────────────────────────────────────────────────────────
# Format helpers
# ─────────────────────────────────────────────────────────────────────────────


def _to_ollama_messages(
    prompt: Union[str, List[Dict[str, Any]]],
    system_instruction: Optional[str],
) -> List[Dict[str, str]]:
    """Convert UnifiedAgent prompt + system_instruction → Ollama messages list."""
    messages: List[Dict[str, str]] = []

    if system_instruction:
        messages.append({"role": "system", "content": system_instruction})

    if isinstance(prompt, str):
        messages.append({"role": "user", "content": prompt})
        return messages

    for msg in prompt or []:
        role = msg.get("role", "user")
        if role == "model":
            role = "assistant"
        # Skip roles Ollama doesn't understand (system already added above)
        if role not in ("user", "assistant"):
            continue
        content = msg.get("content", "")
        # Handle Gemini-style parts
        if not content and isinstance(msg.get("parts"), list):
            content = " ".join(str(p) for p in msg["parts"] if p)
        if content:
            messages.append({"role": role, "content": str(content)})

    return messages


def _to_ollama_tools(tools: Optional[List[Dict]]) -> Optional[List[Dict]]:
    """Convert UnifiedAgent tool defs → Ollama OpenAI-compatible tool format."""
    if not tools:
        return None
    result = []
    for t in tools:
        if not isinstance(t, dict) or not t.get("name"):
            continue
        result.append(
            {
                "type": "function",
                "function": {
                    "name": t["name"],
                    "description": t.get("description", ""),
                    "parameters": t.get("parameters")
                    or {
                        "type": "object",
                        "properties": {},
                        "required": [],
                    },
                },
            }
        )
    return result or None


def _parse_ollama_response(resp_json: Dict) -> Dict[str, Any]:
    """Convert Ollama /api/chat response → UnifiedAgent {content, tool_calls, usage}."""
    msg = resp_json.get("message") or {}
    content = msg.get("content") or ""

    tool_calls: List[Dict] = []
    for tc in msg.get("tool_calls") or []:
        fn = tc.get("function") or {}
        name = fn.get("name")
        args = fn.get("arguments") or {}
        if isinstance(args, str):
            try:
                args = json.loads(args)
            except Exception:
                args = {}
        if name:
            tool_calls.append({"name": name, "args": args})

    usage = {
        "prompt_tokens": resp_json.get("prompt_eval_count", 0),
        "completion_tokens": resp_json.get("eval_count", 0),
    }
    return {"content": content, "tool_calls": tool_calls, "usage": usage}


def _raw_post(
    url: str,
    payload: Dict,
    stream: bool = False,
) -> Any:
    """Single HTTP POST.  Non-stream → dict.  Stream → generator of delta str."""
    data = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(
        url,
        data=data,
        method="POST",
        headers={"Content-Type": "application/json"},
    )

    if stream:
        return _stream_deltas(req)

    try:
        with urllib.request.urlopen(req, timeout=180) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        body = e.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"Ollama HTTP {e.code}: {body}") from e
    except Exception as e:
        raise RuntimeError(f"Ollama request failed: {e}") from e


def _stream_deltas(req: urllib.request.Request) -> Generator[str, None, None]:
    """Yield text delta strings from a streaming Ollama response."""
    try:
        with urllib.request.urlopen(req, timeout=180) as resp:
            for raw in resp:
                line = raw.decode("utf-8", errors="replace").strip()
                if not line:
                    continue
                try:
                    chunk = json.loads(line)
                    delta = (chunk.get("message") or {}).get("content", "")
                    if delta:
                        yield delta
                    if chunk.get("done", False):
                        break
                except json.JSONDecodeError:
                    continue
    except Exception as e:
        raise RuntimeError(f"Ollama streaming failed: {e}") from e


# ─────────────────────────────────────────────────────────────────────────────
# Provider
# ─────────────────────────────────────────────────────────────────────────────


class OllamaLLMProvider(LLMProvider):
    """
    LLMProvider backed by a local Ollama instance.

    UnifiedAgent calls ``generate_content(prompt, system_instruction=…, tools=…)``
    on every ReAct step.  The system_instruction already includes all active
    Skills (injected upstream by ``SkillManager.inject_into_prompt``), so this
    class just faithfully forwards everything to Ollama.

    Parameters
    ----------
    model : Ollama model tag (e.g. "qwen3:8b", "gemma3:4b").
            ``None`` (default) = 自动从已安装模型中选出最佳选项，每 60 秒重新检测一次。
    base_url : Ollama server base URL (default: http://localhost:11434)
    temperature : Sampling temperature
    num_predict : Max tokens to generate per call
    """

    # 类级自动选模型缓存（所有 model=None 实例共享，60 秒 TTL）
    _auto_model: str = ""
    _auto_model_ts: float = 0.0
    _AUTO_MODEL_TTL: float = 60.0

    def __init__(
        self,
        model: Optional[str] = None,
        base_url: str = _OLLAMA_BASE_URL,
        temperature: float = 0.7,
        num_predict: int = 4096,
    ):
        self.model = model  # None 表示运行时自动选择
        self.base_url = base_url.rstrip("/")
        self._options: Dict[str, Any] = {
            "temperature": temperature,
            "num_predict": num_predict,
        }

    # ── LLMProvider interface ──────────────────────────────────────────────
    def _resolve_model(self) -> str:
        """
        返回实际使用的模型 TAG。
        - 初始化时指定了 model 则直接返回。
        - model=None 时对已安装模型进行评分，60 秒内缓存结果。
        """
        if self.model:
            return self.model
        now = time.time()
        if (
            OllamaLLMProvider._auto_model
            and (now - OllamaLLMProvider._auto_model_ts)
            < OllamaLLMProvider._AUTO_MODEL_TTL
        ):
            return OllamaLLMProvider._auto_model
        try:
            from app.core.routing.local_model_router import LocalModelRouter

            best = LocalModelRouter.pick_best_chat_model()
            if best:
                OllamaLLMProvider._auto_model = best
                OllamaLLMProvider._auto_model_ts = now
                logger.info(f"[OllamaLLMProvider] 自动选择模型: {best}")
                return best
        except Exception:
            pass
        return "qwen3:8b"  # 绝对保底，实际运行时 Ollama 应已安装

    def generate_content(
        self,
        prompt: Union[str, List[Dict[str, Any]]],
        model: str = None,
        system_instruction: Optional[str] = None,
        tools: Optional[List[Any]] = None,
        stream: bool = False,
        **kwargs,
    ) -> Union[Dict[str, Any], Generator[Dict[str, Any], None, None]]:
        target = model or self._resolve_model()
        # 若 system_instruction 含有激活的 Skills 块，在最前面插入元认知前言
        effective_sys = system_instruction
        if effective_sys and _SKILL_BLOCK_MARKER in effective_sys:
            effective_sys = _KOTO_SKILL_PREAMBLE + effective_sys
        messages = _to_ollama_messages(prompt, effective_sys)
        ollama_tools = _to_ollama_tools(tools)

        # Merge per-call kwargs into options
        options = {**self._options}
        for key in ("temperature", "num_predict", "top_p", "top_k"):
            if kwargs.get(key) is not None:
                options[key] = kwargs[key]

        payload: Dict[str, Any] = {
            "model": target,
            "messages": messages,
            "stream": stream,
            "options": options,
        }
        if ollama_tools:
            payload["tools"] = ollama_tools

        url = f"{self.base_url}/api/chat"

        if stream:
            return self._stream_chunks(url, payload)

        resp_json = _raw_post(url, payload, stream=False)
        return _parse_ollama_response(resp_json)

    def get_token_count(
        self,
        prompt: Union[str, List[Dict[str, Any]]],
        model: str,
    ) -> int:
        # Ollama has no dedicated count-tokens endpoint; return 0 as safe fallback
        return 0

    # ── Internal ─────────────────────────────────────────────────────────

    def _stream_chunks(
        self,
        url: str,
        payload: Dict,
    ) -> Generator[Dict[str, Any], None, None]:
        """Yield UnifiedAgent-format chunks from a streaming Ollama call."""
        payload = {**payload, "stream": True}
        data = json.dumps(payload).encode("utf-8")
        req = urllib.request.Request(
            url,
            data=data,
            method="POST",
            headers={"Content-Type": "application/json"},
        )
        for delta in _stream_deltas(req):
            yield {"content": delta, "tool_calls": [], "usage": {}}
