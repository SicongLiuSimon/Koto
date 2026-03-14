#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
gemini_decision_layer_demo.py
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
演示 Gemini 3 Pro / Flash 作为纯"决策与路由层"（The Brain）：

  用户自然语言意图
        │
        ▼
  Gemini 3 Flash/Pro   ← 仅做意图解析，NEVER 输出对话文字
        │  function_call (强制 Tool Call，ANY_TOOL mode)
        ▼
  本地 Python 函数调度
        │
        ▼
  结果返回用户

关键机制：
  - google-genai >= 1.0.0  (latest SDK，使用 from google import genai)
  - tools 参数传入 Python 函数列表，SDK 自动从 Type Hints + Docstring 生成 JSON Schema
  - tool_config = ANY  → 强制模型必须输出 function_call，禁止纯文本回答
  - 返回 Content.parts[].function_call 解析 + 本地分发执行
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
"""

import os
import json
import logging
from typing import Any

from google import genai
from google.genai import types

# ─────────────────────────────────────────────────────────────────────────────
#  配置
# ─────────────────────────────────────────────────────────────────────────────

logging.basicConfig(level=logging.INFO, format="%(levelname)s | %(message)s")
logger = logging.getLogger(__name__)

# 优先取 .env 文件中的 GEMINI_API_KEY，其次取环境变量
API_KEY: str = (
    os.getenv("GEMINI_API_KEY")
    or os.getenv("GOOGLE_API_KEY")
    or ""
)
assert API_KEY, "请设置 GEMINI_API_KEY 环境变量"

# 使用 Interactions-API 模型；如已开放 generate_content，替换为
# "gemini-2.5-flash" 或 "gemini-2.5-pro-preview"
# 目前 gemini-3 系列仅 interactions API 可用，本 demo 用 gemini-2.5-flash 演示完整流程
DECISION_MODEL: str = os.getenv("KOTO_DECISION_MODEL", "gemini-2.5-flash")


# ─────────────────────────────────────────────────────────────────────────────
#  本地工具函数（Mock）
#  SDK 通过 Type Hints + Docstring 自动生成 JSON Schema，无需手写 schema
# ─────────────────────────────────────────────────────────────────────────────

def extract_local_word_data(file_path: str, fields: list[str] | None = None) -> dict:
    """
    从本地 Word/.docx 文件中提取结构化字段。

    Args:
        file_path: Word 文件的绝对路径，例如 "C:/contracts/合同A.docx"。
        fields:    要提取的字段名称列表，例如 ["金额", "甲方", "签署日期"]。
                   若为空则提取所有已知字段。

    Returns:
        包含提取结果的字典，键为字段名，值为提取到的字符串。
    """
    # ── 真实实现中此处调用 python-docx / OCR 等 ──
    logger.info("[LocalTool] extract_local_word_data called | path=%s fields=%s", file_path, fields)
    mock_result = {
        "file_path": file_path,
        "金额": "人民币壹佰万元整（¥1,000,000.00）",
        "甲方": "北京示例科技有限公司",
        "乙方": "上海演示贸易有限公司",
        "签署日期": "2026-03-01",
        "有效期": "12个月",
        "_extracted_fields": fields or ["金额", "甲方", "乙方", "签署日期", "有效期"],
    }
    return mock_result


def route_to_local_8b_model(task_desc: str, priority: str = "normal") -> str:
    """
    将任务描述转发给本地部署的 8B 轻量模型（如 Ollama/Llama3）执行。

    根据 priority 决定调度策略：
    - "high"   → 抢占式调度，立即推理
    - "normal" → 入队等待
    - "low"    → 后台批处理

    Args:
        task_desc: 任务的自然语言描述，将作为 Prompt 传入本地模型。
        priority:  调度优先级，可选 "high" / "normal" / "low"，默认 "normal"。

    Returns:
        本地模型的推理结果字符串。
    """
    # ── 真实实现中此处调用 ollama.chat() 或 requests.post("http://localhost:11434/...") ──
    logger.info("[LocalTool] route_to_local_8b_model called | priority=%s task=%s", priority, task_desc)
    return f"[8B-Local @ priority={priority}] 已完成任务：{task_desc[:80]}"


# ─────────────────────────────────────────────────────────────────────────────
#  工具注册表：函数名 → Python 可调用对象的映射
#  是唯一需要手动维护的地方；添加新工具时只需在这里注册
# ─────────────────────────────────────────────────────────────────────────────

LOCAL_TOOL_REGISTRY: dict[str, Any] = {
    "extract_local_word_data": extract_local_word_data,
    "route_to_local_8b_model": route_to_local_8b_model,
}


# ─────────────────────────────────────────────────────────────────────────────
#  决策层核心类
# ─────────────────────────────────────────────────────────────────────────────

class GeminiDecisionLayer:
    """
    Gemini 纯决策层。
    职责：解析用户意图 → 输出 function_call → 本地函数执行。
    绝不直接回复用户自然语言。
    """

    # 系统指令：明确告诉模型它的职责边界
    SYSTEM_INSTRUCTION = (
        "你是 Koto 的决策路由核心。你的唯一职责是分析用户意图并调用正确的工具函数。\n"
        "规则：\n"
        "1. 你必须且只能调用工具，绝对不允许直接用文字回答用户。\n"
        "2. 从用户输入中精确推断每个参数的值，不得随意假设或省略。\n"
        "3. 若无法确定某参数，使用最合理的默认值并在参数中体现。"
    )

    def __init__(self, model: str = DECISION_MODEL):
        self.client = genai.Client(api_key=API_KEY)
        self.model = model
        # SDK 直接接受 Python 函数列表，自动提取 schema
        self.tools = [extract_local_word_data, route_to_local_8b_model]

    # ── 公开接口 ───────────────────────────────────────────────────────────────

    def decide_and_execute(self, user_message: str) -> list[dict]:
        """
        主入口：接受用户自然语言，返回所有本地工具的执行结果列表。

        Args:
            user_message: 用户输入的原始文本。

        Returns:
            每条结果为 {"tool": str, "args": dict, "result": Any}
        """
        logger.info("[DecisionLayer] 收到请求: %s", user_message)

        response = self._call_gemini(user_message)
        function_calls = self._extract_function_calls(response)

        if not function_calls:
            # 理论上 ANY 模式下不会走到这里；作为防御性兜底
            logger.warning("[DecisionLayer] 模型未返回 function_call，使用本地 8B 兜底")
            fallback_result = route_to_local_8b_model(task_desc=user_message, priority="normal")
            return [{"tool": "route_to_local_8b_model", "args": {"task_desc": user_message}, "result": fallback_result}]

        results = []
        for fc in function_calls:
            result = self._dispatch(fc.name, dict(fc.args))
            results.append({"tool": fc.name, "args": dict(fc.args), "result": result})

        return results

    # ── 内部方法 ───────────────────────────────────────────────────────────────

    def _call_gemini(self, user_message: str) -> types.GenerateContentResponse:
        """
        调用 Gemini API。
        tool_config 使用 ANY 模式，强制模型必须选择一个工具调用，
        不允许输出纯文字（等效于 OpenAI 的 tool_choice="required"）。
        """
        config = types.GenerateContentConfig(
            system_instruction=self.SYSTEM_INSTRUCTION,
            tools=self.tools,
            tool_config=types.ToolConfig(
                function_calling_config=types.FunctionCallingConfig(
                    mode="ANY",  # ANY = 必须调用工具; AUTO = 模型自行决定; NONE = 禁止调用
                    # allowed_function_names=["extract_local_word_data"],  # 可进一步限定候选集
                )
            ),
            # 关键：禁用 SDK 内置的 Automatic Function Calling (AFC)
            # AFC 默认开启，会自动执行函数并无限循环回调最多 10 次。
            # 决策层需要自己拦截 function_call 并手动 dispatch，必须禁用。
            automatic_function_calling=types.AutomaticFunctionCallingConfig(
                disable=True
            ),
            temperature=0.0,     # 路由层需要确定性输出，温度设为 0
            max_output_tokens=512,
        )

        response = self.client.models.generate_content(
            model=self.model,
            contents=user_message,
            config=config,
        )
        logger.debug("[DecisionLayer] raw response: %s", response)
        return response

    @staticmethod
    def _extract_function_calls(response: types.GenerateContentResponse) -> list:
        """
        从响应中安全提取所有 function_call 部分。
        兼容单 Part 和多 Part（并行工具调用）响应。
        content 为 None 时（安全过滤 / finish_reason=OTHER）返回空列表，
        由调用方触发兜底逻辑。
        """
        calls = []
        if not response.candidates:
            return calls
        candidate = response.candidates[0]
        # finish_reason 非 STOP 时 content 可能为 None（SAFETY / MAX_TOKENS / OTHER）
        if candidate.content is None:
            reason = getattr(candidate, "finish_reason", "unknown")
            logger.warning("[DecisionLayer] content=None，finish_reason=%s，将走兜底", reason)
            return calls
        for part in candidate.content.parts:
            if part.function_call and part.function_call.name:
                calls.append(part.function_call)
        return calls

    @staticmethod
    def _dispatch(tool_name: str, args: dict) -> Any:
        """
        根据工具名在注册表中找到对应 Python 函数并执行。
        args 由 Gemini 负责从用户输入中推断并填充。
        """
        fn = LOCAL_TOOL_REGISTRY.get(tool_name)
        if fn is None:
            raise ValueError(f"[DecisionLayer] 未知工具: {tool_name!r}，请检查注册表")
        logger.info("[DecisionLayer] 分发 → %s(%s)", tool_name, json.dumps(args, ensure_ascii=False))
        return fn(**args)


# ─────────────────────────────────────────────────────────────────────────────
#  演示入口
# ─────────────────────────────────────────────────────────────────────────────

def _print_results(results: list[dict]) -> None:
    print("\n" + "═" * 60)
    print("  执行结果")
    print("═" * 60)
    for i, r in enumerate(results, 1):
        print(f"\n[{i}] 工具: {r['tool']}")
        print(f"    参数: {json.dumps(r['args'], ensure_ascii=False, indent=6)}")
        result_str = json.dumps(r["result"], ensure_ascii=False, indent=6) if isinstance(r["result"], dict) else r["result"]
        print(f"    结果: {result_str}")
    print("\n" + "═" * 60)


if __name__ == "__main__":
    layer = GeminiDecisionLayer()

    test_cases = [
        # 应触发 extract_local_word_data
        "帮我读取 C:/Documents/合同2026.docx 并提取金额、甲方和签署日期",
        # 应触发 route_to_local_8b_model
        "用本地模型高优先级帮我总结一下今天的会议记录内容",
        # 歧义输入：Gemini 应推断最合适的工具
        "帮我读取 C盘的合同并提取金额",
    ]

    for prompt in test_cases:
        print(f"\n▶ 用户: {prompt}")
        results = layer.decide_and_execute(prompt)
        _print_results(results)
