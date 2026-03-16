"""
模型降级执行器 (Model Fallback Executor)
==========================================
当首选模型不可用（404 / model-not-found / quota 耗尽），
按任务类型自动切换到下一个最合适的模型重试。

每次切换都写入短期不可用缓存（TTL 5 分钟），避免重复触发。

用法::

    from app.core.llm.model_fallback import get_fallback_executor

    executor = get_fallback_executor()

    # 带自动降级的生成调用
    result = executor.generate_with_fallback(
        provider=gemini_provider,
        prompt="你好",
        preferred_model="gemini-3-flash-preview",
        task_type="CHAT",
    )

    # 查询当前可用的最佳模型
    model_id = executor.get_best_available(task_type="CODER")

    # 手动标记某模型短暂不可用
    executor.mark_unavailable("gemini-3-pro-preview")
"""

from __future__ import annotations

import re
import time
import logging
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

# ── 判定"模型本身不可用"的错误特征 ────────────────────────────────────────────
_MODEL_NOT_FOUND_PATTERNS = [
    r"\b404\b",
    r"not\s+found",
    r"model.*not.*exist",
    r"model.*unavailable",
    r"does not exist",
    r"not supported",
    r"INVALID_ARGUMENT.*model",
    r"unknown model",
    r"model_not_found",
    r"Project.*does not have access",
    r"permission.*denied.*model",
    r"is not available",
    # Interactions-API-only 模型通过 generate_content 调用时返回的错误
    r"Interactions\s+API",
    r"interactions\.create",
    r"requires.*Interactions",
    r"only.*Interactions",
    r"use.*client\.interactions",
]

# ── 通用降级链（无任务信息时使用）──────────────────────────────────────────────
_DEFAULT_FALLBACK_CHAIN: List[str] = [
    "gemini-3-flash-preview",      # 首选：最新 Gemini 3 Flash（generate_content）
    "gemini-2.5-flash",            # 次选：快速路径
    "gemini-3.1-pro-preview",      # 高质量
    "gemini-3-pro-preview",        # Gemini 3 Pro（generate_content）
    "gemini-2.5-pro-preview",
    "gemini-2.0-flash",
    "gemini-1.5-flash",
]

# ── 按任务类型的专属降级链 ──────────────────────────────────────────────────────
# 注意：gemini-3-flash-preview / gemini-3-pro-preview 是普通 generate_content 模型。
# 只有 deep-research-pro-preview-* 才是 Interactions API agent（使用 agent= 字段）。
_TASK_FALLBACK_CHAINS: Dict[str, List[str]] = {
    "CHAT": [
        "gemini-3-flash-preview",     # 首选：最新 Gemini 3 Flash
        "gemini-2.5-flash",           # 次选
        "gemini-2.0-flash",
        "gemini-1.5-flash",
    ],
    "CODER": [
        "gemini-3.1-pro-preview",     # 最强代码能力
        "gemini-3-pro-preview",       # Gemini 3 Pro
        "gemini-2.5-pro-preview",
        "gemini-2.5-flash",
        "gemini-2.0-flash",
    ],
    "RESEARCH": [
        "gemini-3.1-pro-preview",     # 高质量 generate_content
        "gemini-3-pro-preview",       # Gemini 3 Pro
        "gemini-2.5-pro-preview",
        "deep-research-pro-preview-12-2025",  # Interactions API agent（专用深研，慢）
        "gemini-2.5-flash",
    ],
    "PAINTER": [
        "gemini-3.1-flash-image-preview",
        "gemini-2.0-flash-exp",
    ],
    "WEB_SEARCH": [
        "gemini-2.5-flash",           # grounding 需要 generate_content
        "gemini-2.0-flash",
        "gemini-1.5-flash",
    ],
    "FILE_GEN": [
        "gemini-3-flash-preview",     # 首选
        "gemini-2.5-flash",           # 次选
        "gemini-2.0-flash",
        "gemini-1.5-flash",
    ],
    "AGENT": [
        "gemini-3-flash-preview",     # 首选
        "gemini-2.5-flash",
        "gemini-2.0-flash",
    ],
    "DOC_ANNOTATE": [
        "gemini-3-flash-preview",     # 首选
        "gemini-2.5-flash",
        "gemini-2.0-flash",
    ],
    "FILE_SEARCH": [
        "gemini-3-flash-preview",     # 首选
        "gemini-2.5-flash",
        "gemini-2.0-flash",
    ],
    "VISION": [
        "gemini-2.5-flash",           # 需要 generate_content 处理图像字节
        "gemini-2.0-flash",
        "gemini-1.5-pro",
    ],
    "MULTI_STEP": [
        "gemini-3.1-pro-preview",     # 最强
        "gemini-3-pro-preview",       # Gemini 3 Pro
        "gemini-2.5-pro-preview",
        "gemini-2.5-flash",
        "gemini-2.0-flash",
    ],
    "COMPLEX": [
        "gemini-3.1-pro-preview",     # 最强
        "gemini-3-pro-preview",       # Gemini 3 Pro
        "gemini-2.5-pro-preview",
        "gemini-2.5-flash",
    ],
}


def _is_model_unavailable_error(exc: Exception) -> bool:
    """
    判断异常是否表示"模型本身不可用"（而非 prompt 错误、网络抖动等）。
    只有模型不可用错误才需要切换模型重试。
    """
    msg = str(exc)
    for pattern in _MODEL_NOT_FOUND_PATTERNS:
        if re.search(pattern, msg, re.IGNORECASE):
            return True
    return False


class ModelFallbackExecutor:
    """
    任务模型降级执行器。

    - 维护短期不可用缓存（model_id → expires_at），TTL 默认 5 分钟。
    - ``generate_with_fallback()``: 顺序尝试 preferred + fallback_chain，
      遇到"模型不可用"错误自动切换，非模型错误直接透传。
    - ``get_best_available()``: 返回当前链中第一个不在黑名单的模型。
    - ``mark_unavailable()``: 手动将某模型标记为短期不可用。
    - ``update_model_map()``: 接收 ModelManager 的最新路由表用于 get_best_available。
    """

    _UNAVAILABLE_TTL: int = 300  # 5 分钟内不重试失败模型

    def __init__(self) -> None:
        self._unavailable: Dict[str, float] = {}   # model_id → 过期 unix 时间戳
        self._model_map: Dict[str, str] = {}        # 由 ModelManager 注入

    # ── 公开 API ─────────────────────────────────────────────────────────────

    def update_model_map(self, model_map: Dict[str, str]) -> None:
        """由 ModelManager 初始化后注入最新路由表，用于 get_best_available() 参考。"""
        self._model_map = dict(model_map)

    def mark_unavailable(self, model_id: str, ttl: int = None) -> None:
        """手动将模型标记为短期不可用（例如在上层捕获到 API 错误后调用）。"""
        if not model_id:
            return
        seconds = ttl if ttl is not None else self._UNAVAILABLE_TTL
        self._unavailable[model_id] = time.time() + seconds
        logger.warning(
            f"[ModelFallback] ⚠️  标记不可用 {seconds}s: {model_id}"
        )

    def is_available(self, model_id: str) -> bool:
        """检查模型当前是否可用（未在黑名单，或黑名单已过期）。"""
        exp = self._unavailable.get(model_id)
        if exp is None:
            return True
        if time.time() >= exp:
            del self._unavailable[model_id]
            return True
        return False

    def get_best_available(
        self, task_type: str = "CHAT", preferred: str = None
    ) -> Optional[str]:
        """
        返回指定任务当前最佳可用模型：
          1. preferred（若提供且可用）
          2. MODEL_MAP[task_type]（若可用）
          3. 任务专属降级链中第一个可用模型
          4. 通用降级链中第一个可用模型
        """
        candidates = self._build_candidate_list(preferred or "", task_type)
        for model in candidates:
            if self.is_available(model):
                return model
        return None  # 全部不可用（极端情况）

    def generate_with_fallback(
        self,
        provider,
        prompt,
        preferred_model: str,
        task_type: str = "CHAT",
        *,
        system_instruction: str = None,
        tools: list = None,
        stream: bool = False,
        **kwargs,
    ):
        """
        尝试 preferred_model 生成，遇到"模型不可用"错误自动切换到备选模型。

        Args:
            provider:         LLMProvider 实例（GeminiProvider 等）。
            prompt:           用户 prompt（str 或 list）。
            preferred_model:  首选模型 ID。
            task_type:        任务类型，用于选择降级链（默认 "CHAT"）。
            system_instruction, tools, stream, **kwargs: 透传给 provider.generate_content()。

        Returns:
            与 provider.generate_content() 相同类型的返回值。

        Raises:
            最终异常————所有候选模型均失败时抛出最后一次异常。
        """
        tried: set = set()
        last_exc: Exception = None

        candidates = self._build_candidate_list(preferred_model, task_type)

        for model_id in candidates:
            if model_id in tried or not self.is_available(model_id):
                continue
            tried.add(model_id)
            try:
                result = provider.generate_content(
                    prompt=prompt,
                    model=model_id,
                    system_instruction=system_instruction,
                    tools=tools,
                    stream=stream,
                    **kwargs,
                )
                if model_id != preferred_model:
                    logger.info(
                        f"[ModelFallback] ✅ 降级成功: {preferred_model} → {model_id} "
                        f"(task={task_type})"
                    )
                return result

            except Exception as exc:
                last_exc = exc
                if _is_model_unavailable_error(exc):
                    self.mark_unavailable(model_id)
                    logger.warning(
                        f"[ModelFallback] 模型不可用，切换: {model_id} — {exc}"
                    )
                    # 继续尝试下一个候选
                else:
                    # 非"模型不存在"错误（如 prompt 格式错误、鉴权失败等）直接上抛，不降级
                    raise

        # 所有候选均失败
        if last_exc:
            raise last_exc
        raise RuntimeError(
            f"[ModelFallback] 所有候选模型均不可用 (task={task_type}, preferred={preferred_model})"
        )

    # ── 私有方法 ─────────────────────────────────────────────────────────────

    def _build_candidate_list(self, preferred: str, task_type: str) -> List[str]:
        """构建有序、去重的模型候选列表。"""
        seen: set = set()
        result: List[str] = []

        def _add(m: str) -> None:
            if m and m not in seen:
                seen.add(m)
                result.append(m)

        _add(preferred)
        _add(self._model_map.get(task_type))
        for m in _TASK_FALLBACK_CHAINS.get(task_type, []):
            _add(m)
        for m in _DEFAULT_FALLBACK_CHAIN:
            _add(m)
        return result


# ── 全局单例 ──────────────────────────────────────────────────────────────────
_executor: Optional[ModelFallbackExecutor] = None


def get_fallback_executor() -> ModelFallbackExecutor:
    """返回全局 ModelFallbackExecutor 单例（懒加载，线程安全地延迟初始化）。"""
    global _executor
    if _executor is None:
        _executor = ModelFallbackExecutor()
    return _executor
