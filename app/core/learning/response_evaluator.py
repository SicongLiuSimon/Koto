# -*- coding: utf-8 -*-
"""
response_evaluator.py — 模型自我评分器
=======================================

每轮对话结束后，在后台用轻量 LLM 对 AI 自身回复进行多维度打分，
结果写入 RatingStore.model_evals。

评分维度（各 0.0~1.0）：
  accuracy        — 事实准确性 / 无错误
  helpfulness     — 是否真正解决了用户问题
  personalization — 是否用到了用户偏好/历史上下文
  task_completion — 任务是否完整完成（代码可运行、文件已生成等）

加权公式（同 RatingStore）：
  overall = accuracy*0.35 + helpfulness*0.30 + personalization*0.20 + task_completion*0.15

使用方式（web/app.py _start_memory_extraction 内）：
    from app.core.learning.response_evaluator import ResponseEvaluator
    ResponseEvaluator.evaluate_async(
        msg_id, user_input, ai_response, task_type,
        session_name, llm_fn
    )
"""

from __future__ import annotations

import json
import logging
import re
import threading
from typing import Callable, Dict, Optional

logger = logging.getLogger(__name__)

_EVAL_PROMPT = """\
你是 Koto AI 的质量审查员。请对以下 AI 回复进行客观评分。

用户输入：
{user_input}

AI 回复：
{ai_response}

任务类型：{task_type}

请对以下4个维度各打 0.0~1.0 的分数，严格按 JSON 输出：
{{
  "accuracy":        <float>,   // 事实准确性，有无明显错误
  "helpfulness":     <float>,   // 是否真正解决用户问题，有无废话
  "personalization": <float>,   // 是否体现出对该用户偏好/上下文的理解
  "task_completion": <float>,   // 任务完整性（代码可运行/文件真实生成/答案完整）
  "reasoning":       "<一句话评语>"
}}

打分原则：
- 0.9+ 只给真正出色的回复
- 0.7-0.9 正常合格
- 0.5-0.7 基本可以但有明显改进空间
- <0.5 存在错误或严重缺陷
- 只输出 JSON，不要有其他文字
"""


_MIN_RESPONSE_LEN = 10           # 过短的回复不值得评分
_EVAL_MODEL       = "gemini-2.0-flash-lite"  # 快速、便宜
_MAX_INPUT_CHARS  = 1200         # 截断避免长度超限


class ResponseEvaluator:
    """后台异步自评分器（daemon 线程，不阻塞主流程）。"""

    @staticmethod
    def should_evaluate(user_input: str, ai_response: str, task_type: str) -> bool:
        """快速过滤：太短或系统消息不值得评分。"""
        if len(ai_response.strip()) < _MIN_RESPONSE_LEN:
            return False
        if task_type in {"SYSTEM"}:
            return False
        if len((user_input + ai_response).strip()) < 30:
            return False
        return True

    @staticmethod
    def _do_eval(
        msg_id: str,
        user_input: str,
        ai_response: str,
        task_type: str,
        session_name: str,
        llm_fn: Callable[[str], str],
    ) -> Optional[Dict[str, float]]:
        """同步评分（运行在 daemon 线程内）。"""
        prompt = _EVAL_PROMPT.format(
            user_input=user_input[:_MAX_INPUT_CHARS],
            ai_response=ai_response[:_MAX_INPUT_CHARS],
            task_type=task_type,
        )
        try:
            raw = llm_fn(prompt)
        except Exception as e:
            logger.debug(f"[Evaluator] LLM call failed: {e}")
            return None

        raw = raw.strip()
        raw = re.sub(r"^```(?:json)?\s*", "", raw)
        raw = re.sub(r"\s*```$", "", raw)

        try:
            data = json.loads(raw)
        except json.JSONDecodeError:
            m = re.search(r"\{.*\}", raw, re.DOTALL)
            if m:
                try:
                    data = json.loads(m.group())
                except Exception:
                    logger.debug(f"[Evaluator] JSON parse failed: {raw[:200]}")
                    return None
            else:
                return None

        scores = {
            "accuracy":        float(data.get("accuracy",        0.5)),
            "helpfulness":     float(data.get("helpfulness",     0.5)),
            "personalization": float(data.get("personalization", 0.5)),
            "task_completion": float(data.get("task_completion", 0.5)),
        }
        # 钳位到 [0, 1]
        for k in scores:
            scores[k] = max(0.0, min(1.0, scores[k]))

        reasoning = str(data.get("reasoning", "")).strip()[:500]

        try:
            from app.core.learning.rating_store import get_rating_store
            rs = get_rating_store()
            rs.save_model_eval(
                msg_id=msg_id,
                scores=scores,
                session_name=session_name,
                task_type=task_type,
                reasoning=reasoning,
            )
            overall = sum(
                scores[d] * w
                for d, w in {"accuracy": 0.35, "helpfulness": 0.30,
                             "personalization": 0.20, "task_completion": 0.15}.items()
            )
            logger.info(
                f"[Evaluator] ✅ {msg_id[:8]}… overall={overall:.2f}"
                f" (acc={scores['accuracy']:.2f} help={scores['helpfulness']:.2f}"
                f" pers={scores['personalization']:.2f} task={scores['task_completion']:.2f})"
            )
            return scores
        except Exception as e:
            logger.debug(f"[Evaluator] save failed: {e}")
            return scores

    @classmethod
    def evaluate_async(
        cls,
        msg_id: str,
        user_input: str,
        ai_response: str,
        task_type: str,
        session_name: str,
        llm_fn: Callable[[str], str],
    ) -> None:
        """
        启动后台评分线程（不阻塞，不抛出异常）。

        Args:
            msg_id:       RatingStore.make_msg_id(session_name, user_input)
            user_input:   本轮用户输入
            ai_response:  本轮 AI 完整回复
            task_type:    任务类型（CHAT/CODER/RESEARCH…）
            session_name: 会话名称
            llm_fn:       同步 LLM 调用函数 (prompt: str) -> str
        """
        if not cls.should_evaluate(user_input, ai_response, task_type):
            return

        def _run():
            try:
                cls._do_eval(msg_id, user_input, ai_response,
                             task_type, session_name, llm_fn)
            except Exception as e:
                logger.debug(f"[Evaluator] thread error: {e}")

        threading.Thread(target=_run, daemon=True).start()
