"""
动态模型管理器 (Dynamic Model Manager)
========================================
自动发现 API 可用模型，根据任务类型智能匹配最佳模型。
支持 Gemini 及未来其他 Provider 扩展。

核心能力：
- 调用 client.models.list() 自动发现 API 可用模型列表
- 基于能力矩阵为每个任务类型评分，选择最优模型
- TTL 缓存（默认 6 小时），避免频繁 API 调用
- 新模型加入 API 后自动感知并路由，无需手动维护
- 优雅降级：API 不可用时使用静态默认值
"""

from __future__ import annotations

import logging
import re
import time
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Set, Tuple

logger = logging.getLogger(__name__)

# ─── 任务能力需求权重表 ────────────────────────────────────────────────────
# 每个任务所看重的能力维度及权重，权重之和不必等于 1。
# 必须能力 (required=True) 的维度：模型不满足则直接排除。
TASK_REQUIREMENTS: Dict[str, Dict[str, Any]] = {
    "CHAT": {
        "speed": 8,
        "quality": 6,
        "context": 4,
        "reasoning": 4,
        "multimodal": False,  # 不强制要求
        "image_gen": False,
        "grounding": False,
        "function_calling": False,
    },
    "CODER": {
        "speed": 4,
        "quality": 9,
        "reasoning": 9,
        "context": 8,
        "function_calling": True,  # 必须支持
        "multimodal": False,
        "image_gen": False,
        "grounding": False,
    },
    "WEB_SEARCH": {
        "speed": 7,
        "quality": 6,
        "grounding": True,  # 必须支持
        "multimodal": False,
        "image_gen": False,
        "function_calling": False,
    },
    "VISION": {
        "speed": 7,
        "quality": 7,
        "multimodal": True,  # 必须支持
        "image_gen": False,
        "grounding": False,
        "function_calling": False,
    },
    "RESEARCH": {
        "speed": 1,
        "quality": 10,
        "reasoning": 10,
        "context": 10,
        "grounding": True,
        "function_calling": False,
        "image_gen": False,
        "multimodal": False,
    },
    "FILE_GEN": {
        "speed": 5,
        "quality": 8,
        "context": 8,
        "function_calling": True,
        "multimodal": False,
        "image_gen": False,
        "grounding": False,
    },
    "PAINTER": {
        "image_gen": True,  # 必须支持
        "quality": 8,
        "speed": 5,
        "multimodal": False,
        "grounding": False,
        "function_calling": False,
    },
    "AGENT": {
        "speed": 7,
        "function_calling": True,  # 必须支持
        "reasoning": 8,
        "multimodal": True,
        "context": 6,
        "image_gen": False,
        "grounding": False,
    },
}

# ─── 本地执行任务（无需 API 模型）────────────────────────────────────────────
LOCAL_EXECUTOR_TASKS = {"SYSTEM", "FILE_OP"}

# ─── 已知模型能力注册表 ───────────────────────────────────────────────────────
# 预填已知模型的能力；未知模型通过名称规则自动推断。
# provider: "gemini" | "openai" | "anthropic" | ...（预留扩展）
# tier: 综合能力等级（1-10），同任务需求下优先选高 tier
# interactions_only: True 表示必须走 Interactions API（而非 generate_content）
KNOWN_MODEL_REGISTRY: Dict[str, Dict[str, Any]] = {
    # ── Gemini 3.x ──────────────────────────────────────────────
    "gemini-3-pro-preview": {
        "provider": "gemini",
        "tier": 9,
        "speed": 4,
        "quality": 10,
        "reasoning": 10,
        "context": 10,
        "multimodal": True,
        "function_calling": True,
        "grounding": True,
        "image_gen": False,
        "interactions_only": True,
        "display": "Gemini 3.0 Pro 🚀",
        "strengths": ["推理", "代码", "分析", "复杂任务"],
    },
    "gemini-3-flash-preview": {
        "provider": "gemini",
        "tier": 7,
        "speed": 9,
        "quality": 7,
        "reasoning": 7,
        "context": 7,
        "multimodal": True,
        "function_calling": True,
        "grounding": True,
        "image_gen": False,
        "interactions_only": True,
        "display": "Gemini 3.0 Flash ⚡",
        "strengths": ["快速", "对话", "多模态"],
    },
    "gemini-3.1-pro-preview": {
        "provider": "gemini", "tier": 9,
        "speed": 4,  "quality": 10, "reasoning": 10,
        "context": 10, "multimodal": True, "function_calling": True,
        "grounding": True, "image_gen": False,
        "interactions_only": False,
        "display": "Gemini 3.1 Pro 🎯",
        "strengths": ["推理", "代码", "分析", "复杂任务"],
    },
    "gemini-3.1-flash-image-preview": {
        "provider": "gemini",
        "tier": 7,
        "speed": 7,
        "quality": 8,
        "reasoning": 5,
        "context": 5,
        "multimodal": True,
        "function_calling": False,
        "grounding": False,
        "image_gen": True,
        "interactions_only": False,
        "display": "Gemini 3.1 Flash Image 🎨",
        "strengths": ["图像生成", "多模态"],
    },
    # ── Gemini 2.5 ──────────────────────────────────────────────
    "gemini-2.5-pro-preview": {
        "provider": "gemini",
        "tier": 8,
        "speed": 4,
        "quality": 9,
        "reasoning": 9,
        "context": 9,
        "multimodal": True,
        "function_calling": True,
        "grounding": True,
        "image_gen": False,
        "interactions_only": False,
        "display": "Gemini 2.5 Pro 🎯",
        "strengths": ["推理", "代码", "分析"],
    },
    "gemini-2.5-flash-preview": {
        "provider": "gemini",
        "tier": 6,
        "speed": 8,
        "quality": 7,
        "reasoning": 6,
        "context": 7,
        "multimodal": True,
        "function_calling": True,
        "grounding": True,
        "image_gen": False,
        "interactions_only": False,
        "display": "Gemini 2.5 Flash Preview 🌐",
        "strengths": ["联网搜索", "grounding", "快速"],
    },
    "gemini-2.5-flash": {
        "provider": "gemini",
        "tier": 6,
        "speed": 8,
        "quality": 7,
        "reasoning": 6,
        "context": 7,
        "multimodal": True,
        "function_calling": True,
        "grounding": True,
        "image_gen": False,
        "interactions_only": False,
        "display": "Gemini 2.5 Flash 🌐",
        "strengths": ["联网搜索", "grounding", "快速"],
    },
    # ── Deep Research ────────────────────────────────────────────
    "deep-research-pro-preview-12-2025": {
        "provider": "gemini", "tier": 10,
        "speed": 1,  "quality": 10, "reasoning": 10,
        "context": 10, "multimodal": False, "function_calling": False,
        "grounding": True, "image_gen": False,
        "interactions_only": True,   # 必须走 Interactions API，不支持 generate_content
        "display": "Deep Research Pro 🔬",
        "strengths": ["深度研究", "学术分析", "综合报告"],
    },
    # ── Gemini 2.0 ──────────────────────────────────────────────
    "gemini-2.0-flash-exp": {
        "provider": "gemini",
        "tier": 5,
        "speed": 9,
        "quality": 6,
        "reasoning": 6,
        "context": 6,
        "multimodal": True,
        "function_calling": True,
        "grounding": False,
        "image_gen": True,
        "interactions_only": False,
        "display": "Gemini 2.0 Flash Exp 🧪",
        "strengths": ["实验功能", "图像生成"],
    },
    "gemini-2.0-flash": {
        "provider": "gemini",
        "tier": 5,
        "speed": 9,
        "quality": 6,
        "reasoning": 6,
        "context": 6,
        "multimodal": True,
        "function_calling": True,
        "grounding": True,
        "image_gen": False,
        "interactions_only": False,
        "display": "Gemini 2.0 Flash ⚡",
        "strengths": ["快速", "多模态"],
    },
    # ── Gemini 1.5 ──────────────────────────────────────────────
    "gemini-1.5-pro": {
        "provider": "gemini",
        "tier": 6,
        "speed": 4,
        "quality": 8,
        "reasoning": 8,
        "context": 10,
        "multimodal": True,
        "function_calling": True,
        "grounding": True,
        "image_gen": False,
        "interactions_only": False,
        "display": "Gemini 1.5 Pro 📚",
        "strengths": ["长上下文", "推理", "多模态"],
    },
    "gemini-1.5-flash": {
        "provider": "gemini",
        "tier": 4,
        "speed": 9,
        "quality": 5,
        "reasoning": 5,
        "context": 8,
        "multimodal": True,
        "function_calling": True,
        "grounding": True,
        "image_gen": False,
        "interactions_only": False,
        "display": "Gemini 1.5 Flash ⚡",
        "strengths": ["快速", "经济"],
    },
}

# ─── 名称推断规则 (越靠前优先级越高) ──────────────────────────────────────────
# 用于从未见过的新模型名称中推断能力
_INFER_RULES: List[Tuple[str, Dict[str, Any]]] = [
    # 图像生成类
    (
        r"imagen",
        {
            "image_gen": True,
            "multimodal": True,
            "grounding": False,
            "function_calling": False,
        },
    ),
    (r"image.*gen|gen.*image", {"image_gen": True}),
    (r"image.*preview|flash.*image", {"image_gen": True, "multimodal": True}),
    # 深度研究
    (
        r"deep.?research",
        {
            "grounding": True,
            "reasoning": 10,
            "context": 10,
            "speed": 1,
            "tier_bonus": 3,
        },
    ),
    # Pro 系列能力更强
    (
        r"\bpro\b",
        {"quality": 9, "reasoning": 9, "context": 9, "speed": 4, "tier_bonus": 2},
    ),
    # Flash 系列速度优先
    (r"\bflash\b", {"speed": 9, "quality": 6, "tier_bonus": 0}),
    # Ultra 最高能力
    (
        r"\bultra\b",
        {"quality": 10, "reasoning": 10, "context": 10, "speed": 3, "tier_bonus": 4},
    ),
    # Nano/Micro 轻量
    (
        r"\bnano\b|\bmicro\b",
        {"speed": 10, "quality": 4, "reasoning": 3, "tier_bonus": -2},
    ),
    # 多模态
    (r"vision|multimodal", {"multimodal": True}),
    # 联网
    (r"grounding|search", {"grounding": True}),
    # 实验版本
    (r"\bexp\b|\bexperimental\b", {"tier_bonus": -1}),
    # Preview
    (r"\bpreview\b", {"tier_bonus": 1}),
]


# 版本号 → tier 基底分
def _version_to_tier_base(model_name: str) -> int:
    """从模型名中提取大版本号，用于基础 tier 分计算。"""
    m = re.search(r"gemini[- ]?(\d+)(?:\.(\d+))?", model_name)
    if m:
        major = int(m.group(1))
        minor = float(m.group(2) or 0) / 10
        return min(9, 2 + major + round(minor))
    return 4  # 未知模型的保守默认值


def infer_capabilities(model_id: str) -> Dict[str, Any]:
    """
    对于注册表中未记录的新模型，从名称推断能力。
    返回与 KNOWN_MODEL_REGISTRY 格式兼容的 dict。
    """
    name = model_id.lower()
    caps: Dict[str, Any] = {
        "provider": "gemini" if "gemini" in name or "palm" in name else "unknown",
        "tier": _version_to_tier_base(name),
        "speed": 7,
        "quality": 7,
        "reasoning": 7,
        "context": 7,
        "multimodal": False,
        "function_calling": True,
        "grounding": False,
        "image_gen": False,
        "interactions_only": False,
        "display": model_id,
        "strengths": [],
        "_inferred": True,  # 标记为自动推断
    }
    tier_bonus = 0
    for pattern, updates in _INFER_RULES:
        if re.search(pattern, name):
            bonus = updates.pop("tier_bonus", 0) if "tier_bonus" in updates else 0
            tier_bonus += bonus
            caps.update({k: v for k, v in updates.items() if k != "tier_bonus"})
    caps["tier"] = max(1, min(10, caps["tier"] + tier_bonus))
    return caps


# ─── 核心评分函数 ─────────────────────────────────────────────────────────────
def score_model_for_task(caps: Dict[str, Any], task: str) -> float:
    """
    给模型对特定任务进行打分。
    - 布尔型必要能力不满足 → 返回 -1（排除）
    - 否则加权求和，加上 tier 奖励
    """
    reqs = TASK_REQUIREMENTS.get(task, {})
    if not reqs:
        return 0.0

    score = 0.0
    for dim, requirement in reqs.items():
        model_val = caps.get(dim)
        if isinstance(requirement, bool):
            if requirement and not model_val:
                return -1.0  # 硬性排除
            # 不需要该能力 → 不加分也不扣分
        elif isinstance(requirement, (int, float)):
            # 数值型：越接近需求，评分越高；超出上限不额外加分
            val = float(model_val or 0)
            score += requirement * (val / 10.0)

    # tier 贡献（最高加 2 分，避免完全覆盖任务匹配度）
    score += caps.get("tier", 5) * 0.2
    return round(score, 4)


class ModelManager:
    """
    动态模型管理器。

    用法：
        manager = ModelManager(client)
        model_map = manager.get_model_map()     # 得到任务 → 模型ID 的映射
        model_id  = manager.get_model_for_task("CODER")
        manager.refresh()                       # 手动刷新

    get_model_map() 会在第一次调用（或缓存过期）时查询 API，
    后续调用直接返回缓存，直到 TTL 到期。
    """

    DEFAULT_CACHE_TTL = 6 * 3600  # 6 小时
    FAST_RETRY_AFTER = 300  # API 失败后 5 分钟内不重试

    def __init__(self, client, cache_ttl: int = DEFAULT_CACHE_TTL):
        self._client = client
        self._cache_ttl = cache_ttl
        self._cached_map: Optional[Dict[str, str]] = None
        self._cached_caps: Dict[str, Dict[str, Any]] = {}  # model_id → caps
        self._available_ids: List[str] = []
        self._last_refresh = 0.0
        self._last_fail_ts = 0.0

    # ── 公共接口 ─────────────────────────────────────────────────────────────

    def get_model_for_task(self, task: str) -> Optional[str]:
        """返回指定任务的最佳模型 ID；本地任务返回 'local-executor'。"""
        if task in LOCAL_EXECUTOR_TASKS:
            return "local-executor"
        return self.get_model_map().get(task)

    def get_model_map(self) -> Dict[str, str]:
        """
        返回完整任务 → 模型ID 映射。
        优先读缓存，过期后自动刷新；API 失败则保留上次缓存或使用静态默认值。
        """
        now = time.time()
        if (
            self._cached_map is not None
            and (now - self._last_refresh) < self._cache_ttl
        ):
            return self._cached_map
        # 失败冷却期内不重试
        if self._last_fail_ts and (now - self._last_fail_ts) < self.FAST_RETRY_AFTER:
            return self._cached_map or self._static_default_map()
        self._rebuild()
        return self._cached_map or self._static_default_map()

    def get_available_models(self) -> List[Dict[str, Any]]:
        """
        返回当前可用模型列表，每个元素包含 id / display / tier / caps 等信息。
        供前端展示或调试用。
        """
        self.get_model_map()  # 触发一次刷新（如有必要）
        result = []
        for mid in self._available_ids:
            caps = self._cached_caps.get(mid, {})
            result.append(
                {
                    "id": mid,
                    "display": caps.get("display", mid),
                    "tier": caps.get("tier", 5),
                    "provider": caps.get("provider", "gemini"),
                    "strengths": caps.get("strengths", []),
                    "capabilities": {
                        "multimodal": caps.get("multimodal", False),
                        "image_gen": caps.get("image_gen", False),
                        "grounding": caps.get("grounding", False),
                        "function_calling": caps.get("function_calling", False),
                        "interactions_only": caps.get("interactions_only", False),
                        "_inferred": caps.get("_inferred", False),
                    },
                }
            )
        return sorted(result, key=lambda x: x["tier"], reverse=True)

    def get_model_map_with_scores(self) -> Dict[str, Dict[str, Any]]:
        """
        返回带有评分明细的路由结果，供调试/API 展示用。
        """
        model_map = self.get_model_map()
        out = {}
        for task, model_id in model_map.items():
            caps = self._cached_caps.get(model_id, {})
            out[task] = {
                "model_id": model_id,
                "display": caps.get("display", model_id),
                "tier": caps.get("tier", 0),
                "score": score_model_for_task(caps, task) if caps else 0,
                "provider": caps.get(
                    "provider", "local" if model_id == "local-executor" else "gemini"
                ),
                "_inferred": caps.get("_inferred", False),
            }
        return out

    def get_interactions_only_models(self) -> Set[str]:
        """返回必须走 Interactions API 的模型集合。"""
        self.get_model_map()
        return {
            mid
            for mid, caps in self._cached_caps.items()
            if caps.get("interactions_only", False)
        }

    def get_fallback_model(self) -> str:
        """返回最适合做通用降级的模型（支持 generate_content，速度快、稳定可用）。"""
        self.get_model_map()
        # 优先选用已知稳定的 Flash 模型，避免 pro-preview 等访问受限的模型
        _PREFERRED_FALLBACKS = [
            "gemini-2.5-flash",
            "gemini-2.5-flash-preview",
            "gemini-1.5-flash",
            "gemini-1.5-pro",
        ]
        for mid in _PREFERRED_FALLBACKS:
            caps = self._cached_caps.get(mid)
            if (
                caps
                and not caps.get("interactions_only", False)
                and not caps.get("image_gen", False)
            ):
                return mid
        # 兜底：按 tier+speed 排序，但排除 preview/exp 等访问受限模型
        candidates = [
            (mid, caps)
            for mid, caps in self._cached_caps.items()
            if not caps.get("interactions_only", False)
            and not caps.get("image_gen", False)
            and mid != "local-executor"
            and "preview" not in mid
            and "-exp" not in mid
        ]
        if not candidates:
            candidates = [
                (mid, caps)
                for mid, caps in self._cached_caps.items()
                if not caps.get("interactions_only", False)
                and not caps.get("image_gen", False)
                and mid != "local-executor"
            ]
        if not candidates:
            return "gemini-2.5-flash"
        best = max(
            candidates, key=lambda x: x[1].get("tier", 0) + x[1].get("speed", 0) * 0.3
        )
        return best[0]

    def refresh(self) -> Dict[str, str]:
        """强制刷新模型列表，返回新的 model_map。"""
        self._last_refresh = 0.0
        self._last_fail_ts = 0.0
        return self.get_model_map()

    # ── 私有方法 ─────────────────────────────────────────────────────────────

    def _rebuild(self):
        """从 API 获取可用模型列表，重新构建 model_map 和 capabilities 缓存。"""
        try:
            discovered = self._fetch_available_model_ids()
        except Exception as exc:
            logger.warning(f"[ModelManager] 模型列表获取失败: {exc}")
            self._last_fail_ts = time.time()
            # 如无缓存，使用静态默认
            if self._cached_map is None:
                self._cached_map = self._static_default_map()
                self._preload_static_caps()
            return

        # 合并已发现模型（API）与注册表中的已知模型
        all_model_ids = self._merge_with_registry(discovered)
        self._available_ids = all_model_ids

        # 构建能力缓存
        for mid in all_model_ids:
            if mid not in self._cached_caps:
                if mid in KNOWN_MODEL_REGISTRY:
                    self._cached_caps[mid] = KNOWN_MODEL_REGISTRY[mid].copy()
                else:
                    self._cached_caps[mid] = infer_capabilities(mid)

        # 为每个任务类型选择最优模型
        new_map: Dict[str, str] = {}
        for task in TASK_REQUIREMENTS:
            best = self._select_best(task, all_model_ids)
            if best:
                new_map[task] = best
        for task in LOCAL_EXECUTOR_TASKS:
            new_map[task] = "local-executor"

        self._cached_map = new_map
        self._last_refresh = time.time()
        self._last_fail_ts = 0.0

        logger.info(
            f"[ModelManager] 刷新完成 — 发现 {len(discovered)} 个可用模型，"
            f"路由 {len(new_map)} 个任务"
        )
        self._log_routing_summary(new_map)

    def _fetch_available_model_ids(self) -> List[str]:
        """
        调用 Gemini API 列出可用模型，返回 model ID 列表（去掉 'models/' 前缀）。
        过滤掉 embedding、音频等纯特殊用途模型。
        """
        exclude_keywords = {"embedding", "aqa", "tts", "speech", "whisper"}
        include_actions = {
            "generateContent",
            "generate_content",
            "streamGenerateContent",
            "generateImages",
        }

        model_ids: List[str] = []
        try:
            # google-genai SDK: client.models.list() 返回 Model 对象的迭代器
            page = self._client.models.list(config={"page_size": 200})
            for model in page:
                raw_name = getattr(model, "name", "") or ""
                # 标准化 ID：去掉 "models/" 前缀
                mid = (
                    raw_name.removeprefix("models/")
                    if raw_name.startswith("models/")
                    else raw_name
                )
                if not mid:
                    continue
                # 过滤纯嵌入等不可用于生成的模型
                if any(kw in mid.lower() for kw in exclude_keywords):
                    continue
                # 检查 supported_actions 过滤
                supported = getattr(model, "supported_actions", None) or []
                if supported and not any(a in include_actions for a in supported):
                    continue
                model_ids.append(mid)
        except TypeError:
            # 部分 SDK 版本 list() 不接受 config 参数
            for model in self._client.models.list():
                raw_name = getattr(model, "name", "") or ""
                mid = (
                    raw_name.removeprefix("models/")
                    if raw_name.startswith("models/")
                    else raw_name
                )
                if not mid:
                    continue
                if any(kw in mid.lower() for kw in exclude_keywords):
                    continue
                model_ids.append(mid)

        logger.info(f"[ModelManager] API 返回 {len(model_ids)} 个可用模型")
        return model_ids

    def _merge_with_registry(self, discovered: List[str]) -> List[str]:
        """
        将 API 发现的模型与注册表条目合并。
        注册表中的模型即使 API 没返回也保留（用于本地测试/预填）。
        但 interactions_only 模型不参与无 Interactions API 路由。
        """
        merged = list(dict.fromkeys(discovered))  # 去重保序
        for known_id in KNOWN_MODEL_REGISTRY:
            if known_id not in merged:
                merged.append(known_id)
        return merged

    def _select_best(self, task: str, model_ids: List[str]) -> Optional[str]:
        """从提供的模型列表中，为指定任务选出得分最高的模型。"""
        best_id = None
        best_score = -1.0
        for mid in model_ids:
            caps = self._cached_caps.get(mid)
            if not caps:
                continue
            sc = score_model_for_task(caps, task)
            if sc > best_score:
                best_score = sc
                best_id = mid
        return best_id

    def _preload_static_caps(self):
        """将注册表的能力描述预加载到缓存，供 API 失败时使用。"""
        for mid, caps in KNOWN_MODEL_REGISTRY.items():
            if mid not in self._cached_caps:
                self._cached_caps[mid] = caps.copy()

    @staticmethod
    def _static_default_map() -> Dict[str, str]:
        """API 不可用时的静态兜底映射（与原 MODEL_MAP 保持一致）。"""
        defaults = {
            "CHAT":       "gemini-3-flash-preview",
            "CODER":      "gemini-3.1-pro-preview",
            "WEB_SEARCH": "gemini-2.5-flash",
            "VISION":     "gemini-3-flash-preview",
            "RESEARCH":   "deep-research-pro-preview-12-2025",
            "FILE_GEN":   "gemini-3-flash-preview",
            "PAINTER":    "gemini-3.1-flash-image-preview",
            "AGENT":      "gemini-3-flash-preview",
            "SYSTEM":     "local-executor",
            "FILE_OP":    "local-executor",
            "COMPLEX":    "gemini-3.1-pro-preview",
        }
        return defaults

    @staticmethod
    def _log_routing_summary(model_map: Dict[str, str]):
        lines = ["[ModelManager] 最新路由表:"]
        for task, mid in sorted(model_map.items()):
            lines.append(f"  {task:<12} → {mid}")
        logger.info("\n".join(lines))
