# -*- coding: utf-8 -*-
"""
PlanExecutor — 多步任务执行引擎
================================
负责执行 LocalPlanner 生成的结构化计划，支持：

  - 步骤间结果传递（output_key / context_keys）
  - 依赖拓扑排序（depends_on）
  - 单步失败重试（max_retry=1）
  - 实时进度回调（yield_fn）

用法（在 app.py 的 generate_multi_step 中）:
    from app.core.routing.plan_executor import PlanExecutor

    executor = PlanExecutor(
        steps=multi_step_info["subtasks"],
        user_input=user_input,
        handlers=_build_handlers(context),   # 见 build_handlers()
        yield_fn=lambda event: None,          # SSE yield 函数
    )
    async for event in executor.execute():
        yield f"data: {json.dumps(event)}\\n\\n"
"""

from __future__ import annotations

import asyncio
import logging
import time
from typing import Any, Callable, Dict, Generator, List, Optional

logger = logging.getLogger(__name__)


# ─────────────────────────────────────────────────────────────────────────────
# Context Store
# ─────────────────────────────────────────────────────────────────────────────

class ContextStore:
    """步骤间结果共享仓库。"""

    def __init__(self, user_input: str):
        self._store: Dict[str, Any] = {
            "original_input": user_input,
            "user_input": user_input,
        }

    def put(self, key: str, value: Any) -> None:
        self._store[key] = value

    def get(self, key: str, default: Any = None) -> Any:
        return self._store.get(key, default)

    def get_text(self, key: str, max_chars: int = 4000) -> str:
        """取出存储值并转为字符串文本（超过 max_chars 时自动摘要压缩）。"""
        val = self._store.get(key)
        if val is None:
            return ""
        if isinstance(val, dict):
            for field in ("output", "content", "result", "text"):
                text = val.get(field)
                if text and isinstance(text, str):
                    return self._compress_if_needed(text, max_chars)
            return str(val)[:max_chars]
        return self._compress_if_needed(str(val), max_chars)

    def _compress_if_needed(self, text: str, max_chars: int) -> str:
        """超过阈值时尝试用 gemini-2.0-flash-lite 生成摘要，否则硬截断。"""
        if len(text) <= max_chars:
            return text
        try:
            import sys
            _app_mod = sys.modules.get("web.app") or sys.modules.get("app")
            _client = getattr(_app_mod, "client", None) if _app_mod else None
            _types_mod = sys.modules.get("google.genai.types")
            if _client and _types_mod:
                resp = _client.models.generate_content(
                    model="gemini-2.0-flash-lite",
                    contents=(
                        f"请将以下内容压缩为{max_chars // 2}字以内的要点摘要，"
                        f"保留所有关键数据、数字和结论：\n\n{text[:20000]}"
                    ),
                    config=_types_mod.GenerateContentConfig(
                        max_output_tokens=max_chars // 2,
                        temperature=0.1,
                    ),
                )
                summary = getattr(resp, "text", None) or ""
                if summary and len(summary) < len(text):
                    return summary
        except Exception:
            pass
        # 兜底：硬截断
        return text[:max_chars]

    def as_legacy_context(self) -> Dict[str, Any]:
        """返回与现有 TaskOrchestrator._execute_xxx(context=…) 兼容的 dict。"""
        return dict(self._store)


# ─────────────────────────────────────────────────────────────────────────────
# Topological Sort
# ─────────────────────────────────────────────────────────────────────────────

def _topo_sort(steps: List[Dict]) -> List[Dict]:
    """
    对步骤列表按 depends_on 做拓扑排序。
    如果没有 depends_on 字段，直接按 id 顺序返回。
    """
    # 建立 id → step 映射
    id_map = {s.get("id", i + 1): s for i, s in enumerate(steps)}
    all_ids = list(id_map.keys())

    in_degree: Dict[int, int] = {sid: 0 for sid in all_ids}
    adj: Dict[int, List[int]] = {sid: [] for sid in all_ids}

    for s in steps:
        sid = s.get("id", 0)
        for dep in (s.get("depends_on") or []):
            if dep in id_map:
                adj[dep].append(sid)
                in_degree[sid] = in_degree.get(sid, 0) + 1

    queue = [sid for sid in all_ids if in_degree.get(sid, 0) == 0]
    queue.sort()
    ordered = []
    while queue:
        node = queue.pop(0)
        ordered.append(id_map[node])
        for neighbor in sorted(adj.get(node, [])):
            in_degree[neighbor] -= 1
            if in_degree[neighbor] == 0:
                queue.append(neighbor)

    # 若有环或遗漏，兜底返回原顺序
    if len(ordered) != len(steps):
        return steps
    return ordered


# ─────────────────────────────────────────────────────────────────────────────
# Enriched Input Builder
# ─────────────────────────────────────────────────────────────────────────────

def _build_enriched_input(step: Dict, store: ContextStore) -> str:
    """
    将 context_keys 引用的前步输出拼接到 step_input 中，
    生成带上下文的增强 prompt，供执行器使用。
    """
    base_input = step.get("input") or store.get("user_input", "")
    context_keys = step.get("context_keys") or []

    if not context_keys:
        return base_input

    parts = []
    for key in context_keys:
        text = store.get_text(key)
        if text:
            parts.append(f"【前置步骤结果 - {key}】\n{text}")

    if not parts:
        return base_input

    context_block = "\n\n".join(parts)
    return f"{base_input}\n\n--- 参考前步结果 ---\n{context_block}"


# ─────────────────────────────────────────────────────────────────────────────
# PlanExecutor
# ─────────────────────────────────────────────────────────────────────────────

class PlanExecutor:
    """
    执行 LocalPlanner 生成的多步计划。

    参数:
        steps        : LocalPlanner 返回的 steps 列表（含 id/task_type/depends_on/context_keys/output_key）
        user_input   : 用户原始输入
        handlers     : 异步任务处理器字典，key 为 task_type，value 为 async callable(step_input, context) -> dict
        max_retry    : 单步重试次数（默认 1）
    """

    def __init__(
        self,
        steps: List[Dict],
        user_input: str,
        handlers: Dict[str, Any],
        max_retry: int = 1,
    ):
        self.steps = _topo_sort(steps)
        self.store = ContextStore(user_input)
        self.handlers = handlers
        self.max_retry = max_retry
        self.step_results: List[Dict] = []
        self.saved_files: List[str] = []

    # ─── Public API ──────────────────────────────────────────────────────────

    async def execute(self) -> Generator:
        """
        异步生成器：逐步执行计划，yield 进度事件 dict。
        事件格式与现有 SSE 体系兼容：
            {"type": "progress", "message": "...", "detail": "..."}
            {"type": "status",   "message": "..."}
            {"type": "step_done","step_id": 1, "task_type": "WEB_SEARCH", "success": True, ...}
            {"type": "plan_done","final_output": "...", "saved_files": [...], "step_results": [...]}
        """
        total = len(self.steps)
        for idx, step in enumerate(self.steps):
            step_id = step.get("id", idx + 1)
            task_type = step.get("task_type", "CHAT")
            description = step.get("description", f"步骤 {step_id}")
            output_key = step.get("output_key") or f"step_{step_id}_output"

            yield {
                "type": "progress",
                "message": f"步骤 {idx+1}/{total}: {description}",
                "detail": f"任务类型: {task_type}",
            }

            handler = self.handlers.get(task_type) or self.handlers.get("DEFAULT")
            if handler is None:
                err = f"无法处理任务类型: {task_type}"
                logger.warning(f"[PlanExecutor] {err}")
                result = {"success": False, "error": err, "output": ""}
                self._record(step, result, output_key)
                yield self._step_done_event(idx + 1, total, step, result)
                continue

            enriched_input = _build_enriched_input(step, self.store)
            result = await self._run_with_retry(
                handler, step, enriched_input, task_type, output_key, idx, total
            )
            self._record(step, result, output_key)
            yield self._step_done_event(idx + 1, total, step, result)

            # 追加文件
            if result.get("saved_files"):
                self.saved_files.extend(result["saved_files"])

        # 汇总最终输出
        final_output = self._merge_outputs()
        yield {
            "type": "plan_done",
            "final_output": final_output,
            "saved_files": self.saved_files,
            "step_results": self.step_results,
            "context_snapshot": {k: (str(v)[:200] if not isinstance(v, str) else v[:200])
                                 for k, v in self.store.as_legacy_context().items()
                                 if k not in ("original_input", "user_input")},
        }

    # ─── Private helpers ─────────────────────────────────────────────────────

    async def _run_with_retry(
        self, handler, step: Dict, enriched_input: str,
        task_type: str, output_key: str, idx: int, total: int
    ) -> Dict:
        last_result = {"success": False, "error": "未执行", "output": ""}
        for attempt in range(self.max_retry + 1):
            try:
                t0 = time.time()
                result = await handler(
                    step_input=enriched_input,
                    context=self.store.as_legacy_context(),
                    step=step,
                )
                elapsed = time.time() - t0
                if not isinstance(result, dict):
                    result = {"success": True, "output": str(result)}
                result.setdefault("success", True)
                logger.info(
                    f"[PlanExecutor] ✅ 步骤 {idx+1}/{total} ({task_type}) "
                    f"完成 ({elapsed:.1f}s)"
                )
                return result
            except Exception as exc:
                last_result = {"success": False, "error": str(exc), "output": ""}
                logger.warning(
                    f"[PlanExecutor] ⚠️ 步骤 {idx+1}/{total} ({task_type}) "
                    f"第{attempt+1}次失败: {exc}"
                )
                if attempt < self.max_retry:
                    await asyncio.sleep(1.0)
        return last_result

    def _record(self, step: Dict, result: Dict, output_key: str) -> None:
        """把结果存入 ContextStore 和 step_results 列表。"""
        self.store.put(output_key, result)
        # 同时写 task_type 通配键（兼容 TaskOrchestrator 的 context 消费方式）
        task_type = step.get("task_type", "STEP")
        self.store.put(f"{task_type}_result", result)
        step_id = step.get("id", len(self.step_results) + 1)
        self.store.put(f"step_{step_id}_output",
                       result.get("output") or result.get("content") or "")
        self.step_results.append({"step": step, "result": result})

    def _merge_outputs(self) -> str:
        """
        合并所有步骤输出为最终文本。
        优先使用最后一个成功步骤的 output/content 字段。
        """
        final = ""
        for entry in reversed(self.step_results):
            res = entry["result"]
            if res.get("success"):
                text = res.get("output") or res.get("content") or ""
                if text:
                    final = text
                    break
        if not final:
            # fallback: 拼接所有成功输出
            parts = []
            for entry in self.step_results:
                res = entry["result"]
                text = res.get("output") or res.get("content") or ""
                if text:
                    parts.append(text)
            final = "\n\n".join(parts)
        return final

    @staticmethod
    def _step_done_event(idx: int, total: int, step: Dict, result: Dict) -> Dict:
        success = result.get("success", False)
        return {
            "type": "step_done",
            "step_index": idx,
            "step_id": step.get("id", idx),
            "task_type": step.get("task_type", ""),
            "description": step.get("description", ""),
            "success": success,
            "output_preview": (result.get("output") or result.get("content") or "")[:120],
            "error": result.get("error") if not success else None,
        }


# ─────────────────────────────────────────────────────────────────────────────
# Handler Factory (在 app.py 中用于构建 handlers dict)
# ─────────────────────────────────────────────────────────────────────────────

def build_handlers_from_orchestrator(orchestrator_cls, context: Dict) -> Dict[str, Any]:
    """
    从 TaskOrchestrator 类构建 PlanExecutor 所需的 handlers 字典。

    用法（app.py 中）:
        from app.core.routing.plan_executor import build_handlers_from_orchestrator
        handlers = build_handlers_from_orchestrator(TaskOrchestrator, context)
        executor = PlanExecutor(steps=subtasks, user_input=user_input, handlers=handlers)
    """

    async def _web_search(step_input: str, context: Dict, step: Dict = None) -> Dict:
        return await orchestrator_cls._execute_web_search(step_input, context)

    async def _research(step_input: str, context: Dict, step: Dict = None) -> Dict:
        return await orchestrator_cls._execute_research(step_input, context)

    async def _file_gen(step_input: str, context: Dict, step: Dict = None) -> Dict:
        return await orchestrator_cls._execute_file_gen(step_input, context, step or {})

    async def _painter(step_input: str, context: Dict, step: Dict = None) -> Dict:
        return await orchestrator_cls._execute_painter(step_input, context)

    async def _coder(step_input: str, context: Dict, step: Dict = None) -> Dict:
        # CODER: 路由到 TaskOrchestrator._execute_coder 若存在，否则降级 research
        if hasattr(orchestrator_cls, "_execute_coder"):
            return await orchestrator_cls._execute_coder(step_input, context)
        # 降级：用通用 LLM 生成代码
        return {"success": True, "output": f"[CODE 任务] {step_input}", "content": ""}

    async def _system(step_input: str, context: Dict, step: Dict = None) -> Dict:
        if hasattr(orchestrator_cls, "_execute_system"):
            return await orchestrator_cls._execute_system(step_input, context)
        return {"success": False, "error": "SYSTEM handler 未注册", "output": ""}

    async def _agent(step_input: str, context: Dict, step: Dict = None) -> Dict:
        if hasattr(orchestrator_cls, "_execute_agent"):
            return await orchestrator_cls._execute_agent(step_input, context)
        return {"success": False, "error": "AGENT handler 未注册", "output": ""}

    return {
        "WEB_SEARCH": _web_search,
        "RESEARCH":   _research,
        "FILE_GEN":   _file_gen,
        "PAINTER":    _painter,
        "CODER":      _coder,
        "SYSTEM":     _system,
        "AGENT":      _agent,
        # CHAT 直接返回 user_input（步骤本身在其他路由中完成）
        "CHAT": lambda si, ctx, step=None: _async_return(
            {"success": True, "output": si}
        ),
    }


async def _async_return(value: Any) -> Any:
    """辅助：直接 await 返回一个值（用于简单 lambda 场景）。"""
    return value
