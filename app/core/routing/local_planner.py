import json
import logging
import re
from app.core.routing.local_model_router import LocalModelRouter

logger = logging.getLogger(__name__)

class LocalPlanner:
    """Local planner/controller using Ollama for multi-step task planning."""

    # ── 允许的任务类型 ─────────────────────────────────────────────────────────
    ALLOWED_TASKS = {
        "WEB_SEARCH", "RESEARCH", "FILE_GEN", "PAINTER",
        "CODER", "SYSTEM", "AGENT",
    }

    # ── 重规划提示（用于 replan()）─────────────────────────────────────────────
    REPLAN_PROMPT = '''你是一个多步任务重规划器，只输出 JSON。
任务执行中断，需要重新规划剩余步骤。

允许的任务类型：WEB_SEARCH / RESEARCH / FILE_GEN / PAINTER / CODER / SYSTEM / AGENT

【原始目标】
{goal}

【已完成步骤】
{completed_summary}

【失败步骤】
{failed_desc}
失败原因: {error}

【原剩余步骤】（可参考调整）
{remaining_desc}

请分析失败原因，给出从当前状态出发完成目标的修订步骤。规则：
- 如果可以绕过失败步骤，提供替代方案
- 如果目标真的无法继续完成，输出 {{"use_planner": false, "steps": []}}
- 步骤 id 从 {next_id} 开始递增，depends_on 可引用已完成步骤的 id

只输出 JSON:
{{"use_planner":true|false,"steps":[{{"id":{next_id},"task":"...","input":"...","description":"...","output_key":"...","depends_on":[],"context_keys":[]}}]}}
'''

    PLAN_PROMPT = '''你是一个多步任务规划器，只输出 JSON。

允许的任务类型（只从这里选）：
- WEB_SEARCH : 联网搜索获取最新信息
- RESEARCH   : 深度分析/整理材料
- FILE_GEN   : 生成文档/PPT/Word/PDF/Excel
- PAINTER    : 生成配图（AI绘画）
- CODER      : 编写/运行代码、数据可视化图表
- SYSTEM     : 系统操作（打开应用、截图等）
- AGENT      : 复杂自动化（微信发消息、浏览器操作等）

字段说明:
- id          : 步骤序号（从1开始）
- task        : 任务类型（从上面选）
- input       : 本步骤的执行描述（不含前步结果）
- description : 对用户展示的步骤名称（≤20字）
- output_key  : 本步骤输出的存储键名（英文，如 search_result、chart_code）
- depends_on  : 依赖的步骤 id 列表（如 [1]，无依赖则为 []）
- context_keys: 执行时需要注入的前步输出键名列表（如 ["search_result"]）

规则:
- 如果不需要多步，输出 use_planner=false，steps=[]
- 如果需要多步，输出 use_planner=true，steps 中每步都要有完整字段
- 只输出 JSON，不要有其他文本

示例1（查询+生成文档）:
输入: 查询黄金价格历史走势，然后生成一个 Excel 价格波动表格
输出: {{"use_planner":true,"steps":[
  {{"id":1,"task":"WEB_SEARCH","input":"黄金价格历史走势","description":"搜索黄金价格数据","output_key":"gold_data","depends_on":[],"context_keys":[]}},
  {{"id":2,"task":"FILE_GEN","input":"基于金价数据生成Excel表格","description":"生成价格波动Excel","output_key":"excel_file","depends_on":[1],"context_keys":["gold_data"]}}
]}}

示例2（研究+报告）:
输入: 收集最新大模型对比资料，整理成 Word 报告
输出: {{"use_planner":true,"steps":[
  {{"id":1,"task":"RESEARCH","input":"大模型最新对比评测","description":"深度研究大模型","output_key":"research_result","depends_on":[],"context_keys":[]}},
  {{"id":2,"task":"FILE_GEN","input":"基于研究内容生成Word报告","description":"生成Word报告","output_key":"word_file","depends_on":[1],"context_keys":["research_result"]}}
]}}

示例3（不需要多步）:
输入: 帮我画一张猫
输出: {{"use_planner":false,"steps":[]}}

用户输入: {input}

只输出 JSON:
{{"use_planner":true|false,"steps":[{{"id":1,"task":"...","input":"...","description":"...","output_key":"...","depends_on":[],"context_keys":[]}}]}}
'''

    CHECK_PROMPT = '''你是任务进度检查器，只输出 JSON。

输入包含:
- 用户需求
- 计划步骤及执行结果

请判断是否完成，并给出简短结论。

只输出 JSON:
{{"status":"complete|partial|failed","summary":"...","next_actions":["..."]}}
'''

    @classmethod
    def can_plan(cls, user_input: str) -> bool:
        """是否值得尝试多步规划（宽松版，用于兜底触发）"""
        text = user_input.lower()
        multi_markers = ["并", "然后", "再", "同时", "先", "之后", "并且", "接着", "完成后", "最后"]
        search_markers = ["收集", "查询", "搜索", "查", "搜", "最新", "资料", "信息", "数据", "研究"]
        output_markers = ["ppt", "报告", "文档", "word", "pdf", "表格", "excel", "总结", "代码", "图表"]
        if any(m in text for m in multi_markers) and (any(s in text for s in search_markers) or any(o in text for o in output_markers)):
            return True
        if any(s in text for s in search_markers) and any(o in text for o in output_markers):
            return True
        return False

    @classmethod
    def should_preempt(cls, user_input: str) -> bool:
        """
        更严格的判断：是否应在模型分类之前就抢先进入规划流程。
        要求同时满足：1) 明确的多步连接词 2) 既有信息获取又有输出格式
        """
        text = user_input.lower()
        if len(text) < 15:
            return False
        # 明确的先后连接词
        explicit_seq = ["先.*再", "先.*然后", "首先.*然后", "收集.*生成", "查询.*生成",
                        "查.*做成", "搜.*整理", "研究.*写", "分析.*制作"]
        import re
        for pattern in explicit_seq:
            if re.search(pattern, text):
                return True
        # 查询词 + 输出格式词（更严格）
        strong_search = ["搜索", "查询", "收集最新", "研究最新", "调研"]
        strong_output = ["ppt", "word", "excel", "pdf", "报告", "表格文档", "分析报告"]
        if any(s in text for s in strong_search) and any(o in text for o in strong_output):
            return True
        return False

    @classmethod
    def plan(cls, user_input: str, timeout: float = 4.0) -> dict:
        """返回规划结果: {use_planner: bool, steps: list} 或 None"""
        try:
            if not LocalModelRouter.is_ollama_available():
                # Ollama 不可用 → 立即尝试 Cloud fallback
                return cls._plan_with_cloud(user_input)

            if not LocalModelRouter.init_model():
                return cls._plan_with_cloud(user_input)

            prompt = cls.PLAN_PROMPT.format(input=user_input[:600])
            raw, err = LocalModelRouter.call_ollama_chat(
                messages=[
                    {"role": "system", "content": prompt},
                    {"role": "user", "content": user_input[:600]},
                ],
                fmt="json",
                options={"temperature": 0.0, "num_predict": 500},
                timeout=timeout,
            )
            if err:
                # Ollama 调用失败 → Cloud fallback
                return cls._plan_with_cloud(user_input)

            result = cls._parse_plan_json(raw)
            if result is not None:
                return result
            # 解析失败 → Cloud fallback
            return cls._plan_with_cloud(user_input)

        except Exception:
            return cls._plan_with_cloud(user_input)

    @classmethod
    def _parse_plan_json(cls, raw: str):
        """解析规划 JSON，成功返回 dict，失败返回 None"""
        try:
            plan = json.loads(raw)
        except Exception:
            return None

        if not isinstance(plan, dict):
            return None

        use_planner = bool(plan.get("use_planner", False))
        steps = plan.get("steps", []) if isinstance(plan.get("steps", []), list) else []

        cleaned = []
        for i, s in enumerate(steps):
            task = str(s.get("task", "")).strip().upper()
            if task not in cls.ALLOWED_TASKS:
                continue
            step_id = s.get("id", i + 1)
            depends_on = s.get("depends_on", [])
            if not isinstance(depends_on, list):
                depends_on = []
            context_keys = s.get("context_keys", [])
            if not isinstance(context_keys, list):
                context_keys = []
            output_key = s.get("output_key") or f"step_{step_id}_output"
            cleaned.append({
                "id": step_id,
                "task_type": task,
                "description": s.get("description", ""),
                "input": s.get("input", ""),
                "output_key": output_key,
                "depends_on": depends_on,
                "context_keys": context_keys,
            })

        return {"use_planner": use_planner and len(cleaned) > 0, "steps": cleaned}

    @classmethod
    def _plan_with_cloud(cls, user_input: str) -> dict:
        """
        Cloud fallback 规划器：当 Ollama 不可用时，用 Gemini 2.5 Flash 生成计划。
        同步调用，适合在普通线程中运行。
        """
        try:
            # 延迟导入，避免循环依赖
            import importlib
            _genai = importlib.import_module("google.genai")
            _types = importlib.import_module("google.genai.types")

            # 从 app.py 获取共享 client（通过 sys.modules 避免循环 import）
            import sys
            _app_module = sys.modules.get("web.app") or sys.modules.get("app")
            _client = getattr(_app_module, "client", None) if _app_module else None
            if _client is None:
                return None

            cloud_prompt = (
                cls.PLAN_PROMPT.format(input=user_input[:600]) +
                "\n\n注意：如果任务不需要多步，请直接输出 {\"use_planner\": false, \"steps\": []}"
            )

            resp = _client.models.generate_content(
                model="gemini-2.5-flash",
                contents=user_input[:600],
                config=_types.GenerateContentConfig(
                    system_instruction=cloud_prompt,
                    response_mime_type="application/json",
                    temperature=0.1,
                    max_output_tokens=600,
                )
            )
            raw = resp.text or ""
            result = cls._parse_plan_json(raw)
            if result:
                print(f"[LocalPlanner] ✅ Cloud fallback 规划成功: {len(result.get('steps', []))} 步")
            return result
        except Exception as e:
            print(f"[LocalPlanner] ⚠️ Cloud fallback 规划失败: {e}")
            return None

    @classmethod
    def self_check(cls, user_input: str, steps: list, results: list, timeout: float = 4.0) -> dict:
        """对执行结果进行自检"""
        try:
            if not LocalModelRouter.is_ollama_available():
                return {"status": "complete", "summary": "(本地模型不可用，跳过自检)", "next_actions": []}

            if not LocalModelRouter.init_model():
                return {"status": "complete", "summary": "(本地模型不可用，跳过自检)", "next_actions": []}

            summary_lines = []
            for i, (s, r) in enumerate(zip(steps, results), start=1):
                ok = r.get("success") if isinstance(r, dict) else False
                out = (r.get("output") or r.get("error") or "")[:120] if isinstance(r, dict) else ""
                summary_lines.append(f"步骤{i}: {s.get('task_type')} - {'OK' if ok else 'FAIL'} - {out}")

            prompt = cls.CHECK_PROMPT + "\n用户需求:\n" + user_input + "\n\n执行摘要:\n" + "\n".join(summary_lines)

            raw, err = LocalModelRouter.call_ollama_chat(
                messages=[
                    {"role": "system", "content": cls.CHECK_PROMPT},
                    {"role": "user", "content": prompt},
                ],
                fmt="json",
                options={"temperature": 0.0, "num_predict": 120},
                timeout=timeout,
            )
            if err:
                return {"status": "partial", "summary": "(自检失败)", "next_actions": []}

            try:
                check = json.loads(raw)
                if isinstance(check, dict):
                    return {
                        "status": check.get("status", "partial"),
                        "summary": check.get("summary", ""),
                        "next_actions": check.get("next_actions", []) if isinstance(check.get("next_actions", []), list) else []
                    }
            except Exception:
                pass

            return {"status": "partial", "summary": raw[:200], "next_actions": []}

        except Exception:
            return {"status": "partial", "summary": "(自检异常)", "next_actions": []}

    # ── 动态重规划（Reflexion 风格）─────────────────────────────────────────────

    @classmethod
    def replan(
        cls,
        user_input: str,
        completed_steps: list,
        completed_outputs: list,
        failed_step: dict,
        error: str,
        remaining_steps: list,
        next_id: int = 1,
        timeout: float = 4.0,
    ) -> dict:
        """
        动态重规划：某步骤失败后，根据失败原因生成修订的后续步骤。

        Args:
            user_input:        原始用户目标
            completed_steps:   已完成步骤列表 (dict with task_type/description/id)
            completed_outputs: 对应的输出摘要文本列表
            failed_step:       失败步骤 dict
            error:             失败错误信息
            remaining_steps:   原计划中未执行的步骤（可参考调整）
            next_id:           新生成步骤的起始 id
            timeout:           Ollama 调用超时秒数

        Returns:
            {use_planner: bool, steps: list} — 新的剩余步骤，或 {use_planner: False} 表示放弃
        """
        # 构建摘要上下文
        completed_summary = "\n".join(
            f"  步骤{s.get('id', i + 1)} ({s.get('task_type', '?')}): "
            f"{s.get('description', '')} → {out[:80]}"
            for i, (s, out) in enumerate(zip(completed_steps, completed_outputs))
        ) or "  (无已完成步骤)"

        failed_desc = (
            f"步骤{failed_step.get('id', '?')} "
            f"({failed_step.get('task_type', '?')}): "
            f"{failed_step.get('description', '')}"
        )

        remaining_desc = "\n".join(
            f"  步骤{s.get('id', '?')} ({s.get('task_type', '?')}): {s.get('description', '')}"
            for s in remaining_steps
        ) or "  (无)"

        prompt = cls.REPLAN_PROMPT.format(
            goal=user_input[:400],
            completed_summary=completed_summary[:800],
            failed_desc=failed_desc,
            error=str(error)[:300],
            remaining_desc=remaining_desc[:400],
            next_id=next_id,
        )

        # 尝试 Ollama 本地模型
        try:
            if LocalModelRouter.is_ollama_available() and LocalModelRouter.init_model():
                raw, err = LocalModelRouter.call_ollama_chat(
                    messages=[
                        {"role": "system", "content": prompt},
                        {"role": "user", "content": user_input[:400]},
                    ],
                    fmt="json",
                    options={"temperature": 0.0, "num_predict": 500},
                    timeout=timeout,
                )
                if not err:
                    result = cls._parse_plan_json(raw)
                    if result:
                        logger.info(
                            f"[LocalPlanner] ✅ Replan (Ollama): "
                            f"{len(result.get('steps', []))} 新步骤"
                        )
                        return result
        except Exception as _e:
            logger.debug(f"[LocalPlanner] Replan Ollama 失败: {_e}")

        # Cloud fallback
        return cls._replan_with_cloud(prompt, user_input)

    @classmethod
    def _replan_with_cloud(cls, prompt: str, user_input: str) -> dict:
        """Cloud fallback for replan()."""
        try:
            import sys
            import importlib
            _types = importlib.import_module("google.genai.types")
            _app_module = sys.modules.get("web.app") or sys.modules.get("app")
            _client = getattr(_app_module, "client", None) if _app_module else None
            if not _client:
                return {"use_planner": False, "steps": []}

            resp = _client.models.generate_content(
                model="gemini-2.5-flash",
                contents=user_input[:400],
                config=_types.GenerateContentConfig(
                    system_instruction=prompt,
                    response_mime_type="application/json",
                    temperature=0.1,
                    max_output_tokens=600,
                ),
            )
            raw = resp.text or ""
            result = cls._parse_plan_json(raw)
            if result:
                logger.info(
                    f"[LocalPlanner] ✅ Replan (Cloud): "
                    f"{len(result.get('steps', []))} 新步骤"
                )
                return result
        except Exception as e:
            logger.warning(f"[LocalPlanner] Replan cloud fallback 失败: {e}")
        return {"use_planner": False, "steps": []}

