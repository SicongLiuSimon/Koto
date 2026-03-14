# -*- coding: utf-8 -*-
"""
Koto Tree of Thought (ToT) 并行多路推理引擎
==============================================

工作流程：
  1. **Expand**  : 并行生成 N 条思考分支（不同温度 + 视角指令）
  2. **Evaluate** : Critic Agent 对每条分支打分（1–10）并给出简要评语
  3. **Select**   : 选取最高分分支作为最终答案返回

适用场景：
  - RESEARCH  (深度研究报告)
  - FILE_GEN  (高质量文档生成)

用法 (直接 Python)::

    from app.core.agent.tree_of_thought import TreeOfThought

    tot = TreeOfThought(n_branches=3)
    winner = tot.run("请分析大语言模型的发展趋势", task_type="RESEARCH")
    print(winner.content)

用法 (流式)::

    for event in tot.stream("写一篇关于量子计算的技术报告"):
        print(event)
        # {"stage": "expand", "branch_id": 1, "status": "generating"}
        # {"stage": "expand", "branch_id": 1, "status": "done", "preview": "..."}
        # {"stage": "evaluate", "status": "scoring"}
        # {"stage": "select", "winner_id": 2, "score": 9.1, "content": "..."}
"""

from __future__ import annotations

import logging
import re
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field
from typing import Any, Dict, Generator, List, Optional

logger = logging.getLogger(__name__)

# ── 每条分支使用不同的"探索视角"，增加多样性 ──────────────────────────────────
_BRANCH_PERSPECTIVES: List[Dict[str, Any]] = [
    {
        "id": 1,
        "label": "分析视角",
        "directive": (
            "从**分析与逻辑**角度深入思考，关注数据、因果关系和系统性解析。"
            "确保覆盖核心论点，支持论据充分，层次分明。"
        ),
        "temperature": 0.5,
    },
    {
        "id": 2,
        "label": "创意视角",
        "directive": (
            "从**创造性与前瞻性**角度思考，尝试提出新颖见解、类比和非传统观点。"
            "在准确性前提下，追求内容的深度与独到性。"
        ),
        "temperature": 0.85,
    },
    {
        "id": 3,
        "label": "批判视角",
        "directive": (
            "从**批判性与综合**角度思考，主动识别反例、潜在盲点与不确定性。"
            "在提出质疑的同时，输出结构严谨、论述完整的回答。"
        ),
        "temperature": 0.65,
    },
]

_CRITIC_SYSTEM = """你是一位内容质量评审专家（ToT Critic）。
你将收到同一问题的多条候选回答，请对每条回答综合评估：

评分维度（各 10 分，取平均）：
1. 准确性与深度   —— 内容正确、论据充分、有深度
2. 结构与可读性   —— 逻辑清晰、层次分明、易于理解
3. 覆盖度与完整性 —— 全面回应问题、无明显遗漏
4. 语言质量       —— 专业、流畅、无冗余

**严格按照以下 JSON 格式输出，不要有任何额外文字**：

{
  "evaluations": [
    {"branch_id": 1, "score": 8.5, "critique": "一句话简评"},
    {"branch_id": 2, "score": 9.2, "critique": "一句话简评"},
    {"branch_id": 3, "score": 7.8, "critique": "一句话简评"}
  ],
  "winner_id": 2,
  "reason": "选择理由（1-2 句话）"
}
"""


# ─────────────────────────────────────────────────────────────────────────────
# 数据类
# ─────────────────────────────────────────────────────────────────────────────


@dataclass
class ThoughtBranch:
    """单条思考分支的产出。"""

    branch_id: int
    label: str
    content: str
    score: float = 0.0
    critique: str = ""
    temperature: float = 0.7
    elapsed_sec: float = 0.0
    error: Optional[str] = None


@dataclass
class ToTResult:
    """TreeOfThought.run() 的最终结果。"""

    winner: ThoughtBranch
    all_branches: List[ThoughtBranch] = field(default_factory=list)
    total_elapsed_sec: float = 0.0
    critic_reason: str = ""


# ─────────────────────────────────────────────────────────────────────────────
# 核心引擎
# ─────────────────────────────────────────────────────────────────────────────


class TreeOfThought:
    """
    Tree of Thought 并行多路推理引擎。

    参数
    ----
    n_branches       : 并行思考分支数（默认 3，最多取 _BRANCH_PERSPECTIVES 长度）
    model_id         : 用于生成各分支的 Gemini 模型 ID
    evaluator_model  : Critic 使用的模型（默认与 model_id 相同）
    max_tokens       : 每个分支最大 token 数
    max_workers      : 并行线程数（默认 = n_branches）
    timeout_sec      : 单分支生成超时（秒）
    """

    DEFAULT_MODEL = "gemini-2.5-flash"

    def __init__(
        self,
        n_branches: int = 3,
        model_id: Optional[str] = None,
        evaluator_model: Optional[str] = None,
        max_tokens: int = 6000,
        max_workers: int = 3,
        timeout_sec: int = 90,
    ):
        self.n_branches = min(n_branches, len(_BRANCH_PERSPECTIVES))
        self.perspectives = _BRANCH_PERSPECTIVES[: self.n_branches]
        self.model_id = model_id or self.DEFAULT_MODEL
        self.evaluator_model = evaluator_model or self.model_id
        self.max_tokens = max_tokens
        self.max_workers = max_workers
        self.timeout_sec = timeout_sec

    # ── 私有辅助 ─────────────────────────────────────────────────────────────

    def _llm_call(self, system: str, user: str, temperature: float = 0.7) -> str:
        """同步 LLM 调用，返回文本。"""
        try:
            from langchain_core.messages import HumanMessage  # type: ignore
            from langchain_core.messages import SystemMessage

            from app.core.llm.langchain_adapter import KotoLangChainLLM

            llm = KotoLangChainLLM(model_id=self.model_id, temperature=temperature)
            msgs = [SystemMessage(content=system), HumanMessage(content=user)]
            resp = llm.invoke(msgs)
            return resp.content if hasattr(resp, "content") else str(resp)
        except Exception as exc:
            logger.error(f"[ToT] LLM call failed ({type(exc).__name__}): {exc}")
            raise

    def _generate_branch(
        self,
        user_input: str,
        perspective: Dict[str, Any],
        base_system: str,
    ) -> ThoughtBranch:
        """生成单条思考分支（在线程中执行）。"""
        _t0 = time.time()
        _bid = perspective["id"]
        _label = perspective["label"]
        _temp = perspective["temperature"]
        _directive = perspective["directive"]

        system = f"{base_system}\n\n【本次思考模式 — {_label}】\n{_directive}"

        try:
            content = self._llm_call(system=system, user=user_input, temperature=_temp)
            return ThoughtBranch(
                branch_id=_bid,
                label=_label,
                content=content,
                temperature=_temp,
                elapsed_sec=time.time() - _t0,
            )
        except Exception as exc:
            return ThoughtBranch(
                branch_id=_bid,
                label=_label,
                content="",
                temperature=_temp,
                elapsed_sec=time.time() - _t0,
                error=str(exc),
            )

    def _evaluate_branches(
        self,
        user_input: str,
        branches: List[ThoughtBranch],
    ) -> List[ThoughtBranch]:
        """
        Critic Agent 对所有分支评分，返回带 score/critique 的 branches 列表。
        winner_id 最高分。
        """
        # 构造 Critic 输入
        branches_text = ""
        for b in branches:
            if not b.error and b.content:
                branches_text += (
                    f"\n\n---\n【分支 {b.branch_id}——{b.label}】\n{b.content[:3000]}"
                )

        if not branches_text.strip():
            logger.warning("[ToT] 所有分支均失败，无法评估")
            return branches

        user_msg = (
            f"用户问题：\n{user_input}\n\n"
            f"以下是 {len(branches)} 条候选回答：\n{branches_text}"
        )

        try:
            # 使用评估模型（可能与生成模型不同）
            from langchain_core.messages import HumanMessage  # type: ignore
            from langchain_core.messages import SystemMessage

            from app.core.llm.langchain_adapter import KotoLangChainLLM

            llm = KotoLangChainLLM(model_id=self.evaluator_model, temperature=0.2)
            msgs = [
                SystemMessage(content=_CRITIC_SYSTEM),
                HumanMessage(content=user_msg),
            ]
            resp = llm.invoke(msgs)
            raw = resp.content if hasattr(resp, "content") else str(resp)

            # 解析 JSON
            import json

            # 提取第一个完整 JSON 对象
            match = re.search(r"\{.*\}", raw, re.DOTALL)
            if not match:
                raise ValueError("Critic 未返回有效 JSON")
            critic_data = json.loads(match.group(0))

            # 写回 score / critique
            score_map: Dict[int, Dict] = {
                e["branch_id"]: e for e in critic_data.get("evaluations", [])
            }
            for b in branches:
                ev = score_map.get(b.branch_id)
                if ev:
                    b.score = float(ev.get("score", 0))
                    b.critique = ev.get("critique", "")

            return branches

        except Exception as exc:
            logger.warning(f"[ToT] Critic 评估失败，降级为长度启发式: {exc}")
            # Fallback：用内容长度作为粗略分数
            for b in branches:
                b.score = len(b.content) / 200.0  # 每 200 字 ≈ 1 分（粗略）
                b.critique = "（自动长度评分）"
            return branches

    def _pick_winner(self, branches: List[ThoughtBranch]) -> ThoughtBranch:
        """从所有分支中选出得分最高且无错误的分支。"""
        valid = [b for b in branches if not b.error and b.content]
        if not valid:
            raise RuntimeError("[ToT] 所有分支均失败，无法选出赢家")
        return max(valid, key=lambda b: b.score)

    # ── 公开接口 ──────────────────────────────────────────────────────────────

    def run(
        self,
        user_input: str,
        task_type: str = "RESEARCH",
        system_instruction: Optional[str] = None,
    ) -> ToTResult:
        """
        同步执行 Tree of Thought，返回 ToTResult（包含最优分支）。

        参数
        ----
        user_input         : 用户原始问题
        task_type          : "RESEARCH" | "FILE_GEN"（影响基础系统提示）
        system_instruction : 可选的自定义系统提示（覆盖默认）
        """
        _t0 = time.time()
        base_system = system_instruction or _get_base_system(task_type)

        logger.info(
            f"[ToT] 启动 | task={task_type} | branches={self.n_branches} | model={self.model_id}"
        )

        # ── Step 1: 并行展开分支 ────────────────────────────────────────────
        branches: List[ThoughtBranch] = []
        with ThreadPoolExecutor(max_workers=self.max_workers) as executor:
            futures = {
                executor.submit(self._generate_branch, user_input, p, base_system): p
                for p in self.perspectives
            }
            for fut in as_completed(futures, timeout=self.timeout_sec):
                branch = fut.result()
                branches.append(branch)
                status = "✓" if not branch.error else "✗"
                logger.info(
                    f"[ToT] 分支 {branch.branch_id}({branch.label}) {status} "
                    f"({branch.elapsed_sec:.1f}s, {len(branch.content)} chars)"
                )

        # 按 id 排序，保证稳定性
        branches.sort(key=lambda b: b.branch_id)

        # ── Step 2: Critic 评估 ──────────────────────────────────────────────
        logger.info("[ToT] 开始 Critic 评估...")
        branches = self._evaluate_branches(user_input, branches)
        for b in branches:
            logger.info(f"[ToT] 分支 {b.branch_id} 得分={b.score:.1f} | {b.critique}")

        # ── Step 3: 选出赢家 ─────────────────────────────────────────────────
        winner = self._pick_winner(branches)
        elapsed = time.time() - _t0
        logger.info(
            f"[ToT] 完成 | 赢家=分支 {winner.branch_id}({winner.label}) "
            f"| 得分={winner.score:.1f} | 总耗时={elapsed:.1f}s"
        )

        return ToTResult(
            winner=winner,
            all_branches=branches,
            total_elapsed_sec=elapsed,
        )

    def stream(
        self,
        user_input: str,
        task_type: str = "RESEARCH",
        system_instruction: Optional[str] = None,
    ) -> Generator[Dict[str, Any], None, None]:
        """
        流式执行 Tree of Thought，yield 进度事件字典。

        事件类型
        --------
        {"stage": "start",    "n_branches": N, "model": "..."}
        {"stage": "expand",   "branch_id": N, "label": "...", "status": "generating"}
        {"stage": "expand",   "branch_id": N, "label": "...", "status": "done",
         "preview": "...", "elapsed": 1.2}
        {"stage": "expand",   "branch_id": N, "label": "...", "status": "error", "error": "..."}
        {"stage": "evaluate", "status": "scoring"}
        {"stage": "evaluate", "branch_id": N, "score": 8.5, "critique": "..."}
        {"stage": "select",   "winner_id": N, "winner_label": "...", "score": 9.1,
         "reason": "...", "content": "...", "elapsed": 12.3}
        {"stage": "error",    "message": "..."}
        """
        _t0 = time.time()
        base_system = system_instruction or _get_base_system(task_type)

        yield {"stage": "start", "n_branches": self.n_branches, "model": self.model_id}

        # ── Step 1: 并行展开 ─────────────────────────────────────────────────
        for p in self.perspectives:
            yield {
                "stage": "expand",
                "branch_id": p["id"],
                "label": p["label"],
                "status": "generating",
            }

        branches: List[ThoughtBranch] = []
        try:
            with ThreadPoolExecutor(max_workers=self.max_workers) as executor:
                futures = {
                    executor.submit(
                        self._generate_branch, user_input, p, base_system
                    ): p
                    for p in self.perspectives
                }
                for fut in as_completed(futures, timeout=self.timeout_sec):
                    branch = fut.result()
                    branches.append(branch)
                    if branch.error:
                        yield {
                            "stage": "expand",
                            "branch_id": branch.branch_id,
                            "label": branch.label,
                            "status": "error",
                            "error": branch.error,
                        }
                    else:
                        yield {
                            "stage": "expand",
                            "branch_id": branch.branch_id,
                            "label": branch.label,
                            "status": "done",
                            "preview": branch.content[:120] + "…",
                            "elapsed": round(branch.elapsed_sec, 1),
                        }
        except Exception as exc:
            yield {"stage": "error", "message": f"分支生成失败: {exc}"}
            return

        branches.sort(key=lambda b: b.branch_id)

        # ── Step 2: Critic 评估 ──────────────────────────────────────────────
        yield {"stage": "evaluate", "status": "scoring"}
        try:
            branches = self._evaluate_branches(user_input, branches)
        except Exception as exc:
            yield {"stage": "error", "message": f"Critic 评估失败: {exc}"}
            return

        for b in branches:
            yield {
                "stage": "evaluate",
                "branch_id": b.branch_id,
                "label": b.label,
                "score": b.score,
                "critique": b.critique,
            }

        # ── Step 3: 选出赢家 ─────────────────────────────────────────────────
        try:
            winner = self._pick_winner(branches)
        except RuntimeError as exc:
            yield {"stage": "error", "message": str(exc)}
            return

        total_elapsed = time.time() - _t0
        yield {
            "stage": "select",
            "winner_id": winner.branch_id,
            "winner_label": winner.label,
            "score": winner.score,
            "reason": winner.critique,
            "content": winner.content,
            "elapsed": round(total_elapsed, 1),
        }


# ─────────────────────────────────────────────────────────────────────────────
# 任务类型 → 基础系统提示（可被调用方覆盖）
# ─────────────────────────────────────────────────────────────────────────────

_BASE_SYSTEMS: Dict[str, str] = {
    "RESEARCH": (
        "你是专业研究助手，擅长深度分析复杂话题。\n"
        "请提供结构清晰、内容全面、有深度的研究报告，使用 Markdown 格式。\n"
        "要求：准确、客观、数据支撑、层次分明。"
    ),
    "FILE_GEN": (
        "你是专业文档撰写专家，擅长生成高质量 Markdown/Word/PDF 文档。\n"
        "请按照用户需求，输出结构完整、内容充实、格式规范的文档正文。\n"
        "使用清晰的标题层级和适当的列表/表格。"
    ),
}


def _get_base_system(task_type: str) -> str:
    return _BASE_SYSTEMS.get(task_type, _BASE_SYSTEMS["RESEARCH"])


# ─────────────────────────────────────────────────────────────────────────────
# 便捷工厂函数
# ─────────────────────────────────────────────────────────────────────────────


def create_tot(
    task_type: str = "RESEARCH",
    n_branches: int = 3,
    model_id: Optional[str] = None,
) -> TreeOfThought:
    """
    根据任务类型创建预配置的 TreeOfThought 实例。

    FILE_GEN 使用更保守的 2 分支（减少等待时间）。
    RESEARCH 使用 3 分支（最大质量提升）。
    """
    if task_type == "FILE_GEN":
        n_branches = min(n_branches, 2)
    return TreeOfThought(
        n_branches=n_branches,
        model_id=model_id,
        max_workers=n_branches,
    )
