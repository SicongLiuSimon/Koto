"""
WebToolsBridgePlugin — 将 web/tool_registry.py 中的 25 个工具桥接进 UnifiedAgent。

原先 web 层（Flask 路由直调）与应用层（UnifiedAgent）各自有独立注册表，
此插件消除碎片化：所有 web 工具统一通过 AgentPlugin 协议注入 ToolRegistry。
"""

import logging
from typing import Any, Dict, List

from app.core.agent.base import AgentPlugin

logger = logging.getLogger(__name__)


class WebToolsBridgePlugin(AgentPlugin):
    """桥接 web/tool_registry.ToolRegistry 的全部工具"""

    @property
    def name(self) -> str:
        return "WebToolsBridge"

    @property
    def description(self) -> str:
        return (
            "Bridges web-layer tools: WeChat, calendar, reminders, web search, "
            "browser automation, clipboard, file I/O, Excel analysis, document gen, etc."
        )

    def get_tools(self) -> List[Dict[str, Any]]:
        try:
            from web.tool_registry import ToolRegistry as WebRegistry
            web_reg = WebRegistry()
        except Exception as exc:
            logger.warning(f"[WebToolsBridgePlugin] 无法加载 web/tool_registry: {exc}")
            return []

        tools = []
        for tool_name, tool_info in web_reg._tools.items():
            raw_params = tool_info.get("parameters", {})
            # 转换 JSON Schema (lowercase types) → Gemini 格式 (uppercase TYPE)
            converted_params = _convert_schema(raw_params)

            tools.append({
                "name": tool_name,
                "func": _make_wrapper(web_reg, tool_name),
                "description": tool_info.get("description", ""),
                "parameters": converted_params,
            })

        logger.info(f"[WebToolsBridgePlugin] 桥接了 {len(tools)} 个 web 层工具")
        return tools


# ─── 辅助函数 ─────────────────────────────────────────────────────────────────

_TYPE_MAP = {
    "string":  "STRING",
    "integer": "INTEGER",
    "number":  "NUMBER",
    "boolean": "BOOLEAN",
    "array":   "ARRAY",
    "object":  "OBJECT",
}


def _convert_schema(schema: Dict) -> Dict:
    """递归将 JSON Schema 的小写 type 转换为 Gemini API 要求的大写形式"""
    if not isinstance(schema, dict):
        return schema

    result = {}
    for k, v in schema.items():
        if k == "type" and isinstance(v, str):
            result[k] = _TYPE_MAP.get(v.lower(), v.upper())
        elif isinstance(v, dict):
            result[k] = _convert_schema(v)
        elif isinstance(v, list):
            result[k] = [_convert_schema(i) if isinstance(i, dict) else i for i in v]
        else:
            result[k] = v
    return result


def _make_wrapper(registry, tool_name: str):
    """
    创建一个闭包，将调用转发到 web ToolRegistry.execute()，
    并将返回的 dict 规范化为字符串（agent loop 要求 str 结果）。
    """
    def _wrapper(**kwargs):
        result = registry.execute(tool_name, kwargs)
        if isinstance(result, dict):
            import json
            return json.dumps(result, ensure_ascii=False, default=str)
        return str(result)

    _wrapper.__name__ = tool_name
    _wrapper.__doc__ = f"Wrapper for web tool: {tool_name}"
    return _wrapper
