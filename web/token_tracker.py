"""
token_tracker.py — Koto Token 使用监测模块
============================================
完全本地工作，无需额外连接 Google。
Token 数据直接从 Gemini API 响应的 usage_metadata 字段读取。

数据持久化到: config/token_usage.json
"""

from __future__ import annotations

import json
import os
import sys
import threading
from datetime import date, datetime
from typing import Any, Dict

# ── 配置 ─────────────────────────────────────────────────────────────────────

# 打包模式：config/ 紧邻 Koto.exe；开发模式：config/ 在 web/ 的父级
if getattr(sys, "frozen", False):
    _BASE_DIR = os.path.dirname(sys.executable)
else:
    _BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_DATA_FILE = os.path.join(_BASE_DIR, "config", "token_usage.json")

# Gemini 定价表（USD / 100 万 tokens）
# 参考 Google AI 官方定价（2026 年）
# 注意：dict 按插入顺序遍历，更具体的前缀必须排在更通用的前缀之前
_PRICING: Dict[str, Dict[str, float]] = {
    # ── Gemini 3.x ────────────────────────────────────────────────
    # 注意：更具体的前缀必须在更短前缀之前
    "gemini-3.1-pro": {"input": 1.25, "output": 10.00},  # 3.1 Pro
    "gemini-3.1-flash": {"input": 0.075, "output": 0.30},  # 3.1 Flash
    "gemini-3-pro": {"input": 1.25, "output": 10.00},  # 3.0 Pro
    "gemini-3-flash": {"input": 0.075, "output": 0.30},  # 3.0 Flash
    "gemini-3": {"input": 0.075, "output": 0.30},  # 其他 3.x fallback
    # ── Gemini 2.5 ────────────────────────────────────────────────
    "gemini-2.5-pro": {"input": 1.25, "output": 10.00},
    "gemini-2.5-flash": {"input": 0.075, "output": 0.30},
    # ── Gemini 2.0 ────────────────────────────────────────────────
    "gemini-2.0-flash-lite": {
        "input": 0.075,
        "output": 0.30,
    },  # Lite 版更便宜（须在 flash 前）
    "gemini-2.0-flash": {"input": 0.10, "output": 0.40},  # 标准 Flash / Exp / Preview
    "gemini-2.0-pro": {"input": 1.25, "output": 5.00},
    # ── Gemini 1.5 ────────────────────────────────────────────────
    "gemini-1.5-pro": {"input": 1.25, "output": 5.00},
    "gemini-1.5-flash": {"input": 0.075, "output": 0.30},
    # ── 深度研究 ────────────────────────────────────────────
    "deep-research": {"input": 2.00, "output": 8.00},  # 按 Pro 估算（官方未公布）
    # ── Embedding ────────────────────────────────────────────
    "text-embedding-004": {"input": 0.025, "output": 0.0},  # $0.025/M tokens
    "text-embedding": {"input": 0.025, "output": 0.0},  # embedding fallback
    # ── 图像生成 ────────────────────────────────────────────
    "nano-banana": {"input": 0.075, "output": 0.30},  # 按 flash 计
    # Imagen 按张计费，这里用 1000 合成 tokens/张换算，与 _TrackedModels.generate_images 配套
    # imagen-4.0 = $0.04/张 → 1000 tokens * $40/M = $0.04
    # imagen-4.0-fast = $0.02/张 → 1000 tokens * $20/M = $0.02
    "imagen-4.0-fast": {"input": 20.0, "output": 0.0},  # Imagen 4.0 Fast
    "imagen-4.0": {"input": 40.0, "output": 0.0},  # Imagen 4.0 标准
    "imagen-3": {"input": 20.0, "output": 0.0},  # Imagen 3
    # ── 默认（兜底） ──────────────────────────────────────────────
    "default": {"input": 0.075, "output": 0.30},
}

USD_TO_CNY = 7.25  # 近似汇率

# ── 内部状态 ──────────────────────────────────────────────────────────────────

_lock = threading.Lock()
_data: Dict[str, Any] = {}  # 内存缓存，结构见 _empty_data()
_dirty = False  # 标记是否有未保存的变动


def _empty_data() -> Dict[str, Any]:
    return {
        "version": 1,
        "daily": {},  # { "YYYY-MM-DD": { model: { input, output, calls } } }
        "monthly": {},  # { "YYYY-MM": { model: { input, output, calls } } }
        "skills": {},  # { skill_id: { date: { model: { input, output, calls } } } }
        "sessions": {},  # { session_id: { "total_tokens": int, "cost_cny": float } }
    }


def _load() -> None:
    """从磁盘加载数据（首次调用时执行）"""
    global _data
    if _data:
        return
    try:
        if os.path.exists(_DATA_FILE):
            with open(_DATA_FILE, "r", encoding="utf-8") as f:
                loaded = json.load(f)
            # 简单版本迁移保障
            if loaded.get("version") == 1:
                _data = loaded
                return
    except Exception:
        pass
    _data = _empty_data()


def _save_if_dirty() -> None:
    """将内存数据写回磁盘（只在有变动时写）"""
    global _dirty
    if not _dirty:
        return
    try:
        os.makedirs(os.path.dirname(_DATA_FILE), exist_ok=True)
        tmp = _DATA_FILE + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(_data, f, ensure_ascii=False, indent=2)
        os.replace(tmp, _DATA_FILE)
        _dirty = False
    except Exception as e:
        pass  # 静默失败，不影响主流程


# ── 公开 API ──────────────────────────────────────────────────────────────────


def record_usage(model: str, prompt_tokens: int, completion_tokens: int) -> None:
    """
    记录一次 API 调用的 token 用量。
    在每次 generate_content 调用完成后调用此函数。
    """
    if not prompt_tokens and not completion_tokens:
        return

    global _dirty
    today = date.today().isoformat()  # "YYYY-MM-DD"
    month = today[:7]  # "YYYY-MM"
    model_key = _normalize_model(model)

    with _lock:
        _load()

        # 更新 daily
        daily = _data.setdefault("daily", {})
        day_data = daily.setdefault(today, {})
        m_day = day_data.setdefault(model_key, {"input": 0, "output": 0, "calls": 0})
        m_day["input"] += prompt_tokens
        m_day["output"] += completion_tokens
        m_day["calls"] += 1

        # 更新 monthly
        monthly = _data.setdefault("monthly", {})
        mo_data = monthly.setdefault(month, {})
        m_mo = mo_data.setdefault(model_key, {"input": 0, "output": 0, "calls": 0})
        m_mo["input"] += prompt_tokens
        m_mo["output"] += completion_tokens
        m_mo["calls"] += 1

        _dirty = True
        _save_if_dirty()


def record_usage_with_skill(
    model: str,
    prompt_tokens: int,
    completion_tokens: int,
    skill_id: str | None = None,
    session_id: str | None = None,
) -> None:
    """
    扩展版 record_usage：除记录全局日/月统计外，
    同时记录 per-skill 和 per-session 成本，
    与 UnifiedAgent / agent_routes.py 的新调用路径配套使用。
    """
    # 先调用全局记录
    record_usage(model, prompt_tokens, completion_tokens)

    if not skill_id and not session_id:
        return
    if not prompt_tokens and not completion_tokens:
        return

    global _dirty
    today = date.today().isoformat()
    model_key = _normalize_model(model)

    with _lock:
        _load()

        # ── per-skill ──────────────────────────────────────────────
        if skill_id:
            skills = _data.setdefault("skills", {})
            sk = skills.setdefault(skill_id, {})
            sk_day = sk.setdefault(today, {})
            sk_m = sk_day.setdefault(model_key, {"input": 0, "output": 0, "calls": 0})
            sk_m["input"] += prompt_tokens
            sk_m["output"] += completion_tokens
            sk_m["calls"] += 1

        # ── per-session ────────────────────────────────────────────
        if session_id:
            sessions = _data.setdefault("sessions", {})
            sess = sessions.setdefault(
                session_id, {"total_tokens": 0, "cost_cny": 0.0, "calls": 0}
            )
            total = prompt_tokens + completion_tokens
            price = _PRICING.get(model_key, _PRICING["default"])
            cost_usd = (
                prompt_tokens / 1_000_000 * price["input"]
                + completion_tokens / 1_000_000 * price["output"]
            )
            sess["total_tokens"] += total
            sess["cost_cny"] = round(sess["cost_cny"] + cost_usd * USD_TO_CNY, 6)
            sess["calls"] += 1

        _dirty = True
        _save_if_dirty()


def get_skill_stats(skill_id: str | None = None) -> Dict[str, Any]:
    """
    返回 per-skill 的 token / 成本统计。
    不传 skill_id 时返回所有 skill 的汇总。

    响应格式 (单个 skill):
    {
      skill_id: {
        "total_calls": int,
        "total_tokens": int,
        "cost_cny": float,
        "by_date": { date: { model: {...} } }
      }
    }
    """
    with _lock:
        _load()
        skills_raw = _data.get("skills", {})
        target = (
            {skill_id: skills_raw[skill_id]}
            if skill_id and skill_id in skills_raw
            else skills_raw
        )

        result = {}
        for sid, date_map in target.items():
            total_calls = 0
            total_tokens = 0
            cost_usd = 0.0
            for _date, model_map in date_map.items():
                for m_key, counts in model_map.items():
                    price = _PRICING.get(m_key, _PRICING["default"])
                    total_calls += counts.get("calls", 0)
                    inp = counts.get("input", 0)
                    out = counts.get("output", 0)
                    total_tokens += inp + out
                    cost_usd += (
                        inp / 1_000_000 * price["input"]
                        + out / 1_000_000 * price["output"]
                    )
            result[sid] = {
                "total_calls": total_calls,
                "total_tokens": total_tokens,
                "cost_usd": round(cost_usd, 6),
                "cost_cny": round(cost_usd * USD_TO_CNY, 4),
                "by_date": date_map,
            }
        return result


def get_stats() -> Dict[str, Any]:
    """
    返回统计摘要，供 /api/token-stats 接口使用。
    结构:
    {
      "today": { "input": int, "output": int, "total": int, "calls": int,
                 "cost_usd": float, "cost_cny": float,
                 "by_model": { model: {...} } },
      "this_month": { ... },
      "last_7_days": [ { "date": str, "total": int, "cost_usd": float, "cost_cny": float } ],
    }
    """
    with _lock:
        _load()

        today_str = date.today().isoformat()
        month_str = today_str[:7]

        return {
            "today": _aggregate_period(_data.get("daily", {}).get(today_str, {})),
            "this_month": _aggregate_period(
                _data.get("monthly", {}).get(month_str, {})
            ),
            "last_7_days": _last_n_days(7),
            "data_file": _DATA_FILE,
        }


def reset_stats(period: str = "all") -> Dict[str, Any]:
    """
    重置统计数据。period: 'today' | 'month' | 'all'
    """
    global _dirty
    with _lock:
        _load()
        today_str = date.today().isoformat()
        month_str = today_str[:7]

        if period == "today":
            _data.get("daily", {}).pop(today_str, None)
        elif period == "month":
            _data.get("monthly", {}).pop(month_str, None)
            # 同时清除本月内的 daily 记录
            for k in list(_data.get("daily", {}).keys()):
                if k.startswith(month_str):
                    _data["daily"].pop(k, None)
        else:
            _data.clear()
            _data.update(_empty_data())

        _dirty = True
        _save_if_dirty()
    return {"success": True, "reset": period}


# ── 内部工具 ──────────────────────────────────────────────────────────────────


def _normalize_model(model: str) -> str:
    """把完整的模型名归一化为可读短名，并保留原始前缀用于查价。"""
    m = model.lower().strip()
    # 去掉 models/ 前缀（如果有）
    if m.startswith("models/"):
        m = m[7:]
    return m


def _get_price(model_key: str) -> Dict[str, float]:
    """根据模型名返回定价（USD / 1M tokens）"""
    for prefix, price in _PRICING.items():
        if model_key.startswith(prefix):
            return price
    return _PRICING["default"]


def _calc_cost(model_key: str, input_tokens: int, output_tokens: int) -> float:
    """计算 USD 费用"""
    price = _get_price(model_key)
    return (input_tokens * price["input"] + output_tokens * price["output"]) / 1_000_000


def _aggregate_period(model_map: Dict[str, Dict]) -> Dict[str, Any]:
    """把某个时间段的 { model: {input,output,calls} } 聚合成摘要"""
    total_input = total_output = total_calls = 0
    cost_usd = 0.0
    by_model = {}

    for model_key, v in model_map.items():
        inp = v.get("input", 0)
        out = v.get("output", 0)
        calls = v.get("calls", 0)
        c_usd = _calc_cost(model_key, inp, out)

        total_input += inp
        total_output += out
        total_calls += calls
        cost_usd += c_usd

        by_model[model_key] = {
            "input": inp,
            "output": out,
            "total": inp + out,
            "calls": calls,
            "cost_usd": round(c_usd, 6),
            "cost_cny": round(c_usd * USD_TO_CNY, 4),
        }

    cost_cny = cost_usd * USD_TO_CNY
    return {
        "input": total_input,
        "output": total_output,
        "total": total_input + total_output,
        "calls": total_calls,
        "cost_usd": round(cost_usd, 6),
        "cost_cny": round(cost_cny, 4),
        "by_model": by_model,
    }


def _last_n_days(n: int) -> list:
    """返回最近 n 天的每日汇总（含今天），按日期升序"""
    from datetime import timedelta

    today = date.today()
    daily = _data.get("daily", {})
    result = []
    for i in range(n - 1, -1, -1):
        d = (today - timedelta(days=i)).isoformat()
        day_models = daily.get(d, {})
        agg = _aggregate_period(day_models)
        result.append(
            {
                "date": d,
                "total": agg["total"],
                "calls": agg["calls"],
                "cost_usd": agg["cost_usd"],
                "cost_cny": agg["cost_cny"],
            }
        )
    return result
