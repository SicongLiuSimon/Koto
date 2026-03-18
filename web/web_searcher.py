#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
WebSearcher 桥接模块 — 避免 tool_registry → app.py 循环导入
从 app.py 中的 WebSearcher 类代理 search_with_grounding 方法
"""

import logging
import time

logger = logging.getLogger(__name__)


def _detect_query_type(query: str) -> str:
    """检测查询意图类型: travel / weather / finance / general"""
    q = query.lower()
    travel_kw = [
        "火车票",
        "高铁票",
        "动车票",
        "机票",
        "余票",
        "班次",
        "车次",
        "时刻表",
        "列车时刻",
        "列车",
        "高铁",
        "动车",
        "航班",
        "航班动态",
        "几点到",
        "几点出发",
        "几点抵达",
        "要多久",
        "多久到",
    ]
    if any(kw in q for kw in travel_kw):
        return "travel"
    weather_kw = [
        "天气",
        "气温",
        "下雨",
        "下雪",
        "温度",
        "weather",
        "forecast",
        "天气预报",
    ]
    if any(kw in q for kw in weather_kw):
        return "weather"
    finance_kw = [
        "股价",
        "股票",
        "汇率",
        "比特币",
        "黄金",
        "金价",
        "行情",
        "基金",
        "石油",
        "原油",
    ]
    if any(kw in q for kw in finance_kw):
        return "finance"
    return "general"


def _build_system_instruction(query_type: str) -> str:
    """根据查询类型返回专用 system instruction"""
    if query_type == "travel":
        return (
            "你是 Koto，一个智能出行助手。用户在查询交通出行信息（高铁/火车/动车/机票等）。\n"
            "请基于搜索结果，按以下格式输出（用 Markdown）：\n\n"
            "1. 先用一句话说明查询的出发日期和路线（如有）。\n"
            "2. 用 **Markdown 表格** 列出主要班次，列标题为：\n"
            "   | 班次 | 出发站 | 到达站 | 出发时间 | 到达时间 | 历时 | 二等座 | 一等座 |\n"
            "   只列出搜索结果中明确出现的班次，不要自行补全或推测。\n"
            "3. 表格后，提醒用户前往 12306 或铁路官方渠道查看实时余票并购票。\n"
            "4. **严禁** 在搜索结果班次信息不足时自行编造、补全或推测班次数据。若搜索结果不足，明确告知用户『当前搜索结果班次信息有限』，并直接引导用户前往 12306 官网或 App 查询。\n"
            "用中文输出，格式整洁，突出关键数据。"
        )
    elif query_type == "weather":
        return (
            "你是 Koto，一个智能助手。请根据搜索结果提供准确的天气信息。\n"
            "格式要求：当前气温和天气状况、今日最高/最低气温、未来3天天气（如有）、出行或着装建议。\n"
            "用中文输出，简洁清晰。"
        )
    elif query_type == "finance":
        return (
            "你是 Koto，一个智能助手。请根据搜索结果提供准确的金融行情信息。\n"
            "格式要求：当前价格/价值、今日涨跌幅（如有）、近期走势简析（1-2句）。\n"
            "用中文输出，简洁专业。"
        )
    else:
        return (
            "你是 Koto，一个智能助手。使用搜索结果提供准确、实时的信息。"
            "用中文回答，格式清晰，关键数据用 Markdown 列表或加粗呈现。"
        )


def search_with_grounding(query: str, skill_prompt: str = None) -> dict:
    """
    使用 Gemini Google Search Grounding 进行实时搜索（意图感知版本）

    skill_prompt: 来自本地/AI路由器生成的执行指令（描述期望的响应格式）。
      若提供，优先使用；否则回退到关键词检测分支。

    返回格式: {"success": bool, "response": str, "message": str}
    """
    try:
        # 延迟导入，避免模块加载时的循环依赖
        from google.genai import types

        from app import get_client
    except ImportError:
        from google.genai import types

        from web.app import get_client

    # 1. 优先使用模型生成的 skill_prompt
    if skill_prompt and len(skill_prompt.strip()) > 5:
        system_instruction = (
            "你是 Koto，一个智能助手。请使用搜索结果提供准确、实时的信息。\n"
            f"{skill_prompt}\n"
            "用中文回答，格式整洁清晰。"
        )
        logger.info(f"[web_searcher] 使用 skill_prompt: {skill_prompt[:60]}")
    else:
        # 2. 回退：关键词检测 + 分类 system_instruction
        query_type = _detect_query_type(query)
        system_instruction = _build_system_instruction(query_type)
        logger.info(f"[web_searcher] 关键词检测备用: {query_type}")

    try:
        client = get_client()
        max_retries = 3
        for attempt in range(max_retries):
            try:
                response = client.models.generate_content(
                    model="gemini-2.5-flash",
                    contents=query,
                    config=types.GenerateContentConfig(
                        tools=[types.Tool(google_search=types.GoogleSearch())],
                        system_instruction=system_instruction,
                    ),
                )
                break
            except Exception as e:
                if attempt < max_retries - 1:
                    delay = 2**attempt
                    logger.warning(
                        "Web search attempt %d failed: %s, retrying in %ds",
                        attempt + 1,
                        e,
                        delay,
                    )
                    time.sleep(delay)
                else:
                    raise

        if response.text:
            return {
                "success": True,
                "message": response.text,
                "response": response.text,  # 向后兼容
            }
        else:
            return {
                "success": False,
                "error": "搜索未返回结果",
                "message": "搜索未返回结果",
            }
    except Exception as e:
        return {
            "success": False,
            "error": f"搜索失败: {str(e)}",
            "message": f"搜索失败: {str(e)}",
        }
