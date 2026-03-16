import threading
import hashlib
# google.genai.types 延迟到 classify() 内部加载，避免启动时加载 (~4.7s)


class AIRouter:
    """
    基于轻量级 AI 模型的智能任务路由器
    使用 gemini-2.0-flash-lite 进行任务分类
    """
    
    # 路由器专用系统指令
    ROUTER_INSTRUCTION = """你是任务分类器。根据用户输入判断任务类型。只输出一个类型名称。

类型列表:
- PAINTER: 用户要你生成/绘制艺术图片、上色画（AI画图）
- FILE_GEN: 用户要你生成Word/PDF/Excel/PPT文件
- DOC_ANNOTATE: 用户要你标注/批注/润色/校对已有文档
- RESEARCH: 用户需要深度系统性研究分析（长篇报告）
- CODER: 用户要你写代码/编程/调试，或要制作数据图表/可视化图（折线图/柱状图/饼图/散点图/数据图等）
- FILE_SEARCH: 用户要找某个文件/帮我找xx文件/打开某个文件/全盘扫描/扫描电脑
- SYSTEM: 用户命令你打开/关闭某个具体应用程序
- AGENT: 用户要你执行工具操作（发微信/设提醒/浏览器控制/帮我买票订票）
- WEB_SEARCH: 用户询问需要实时数据的问题（天气/股价/新闻/比赛/票务查询/出行时刻/商品现价/原油价格/黄金价格/期货价格/汇率/加密货币价格）
- CHAT: 闲聊、知识问答、概念解释、教程咨询

关键区分:
- 问知识/教程/方法 → CHAT（即使提到"启动""打开"等词）
- "帮我找xx文件"/"在哪里"/"全盘扫描" → FILE_SEARCH
- "打开微信/Chrome/某应用" → SYSTEM（打开某个应用程序）
- "帮我打开/找到某个文件" → FILE_SEARCH（找文件而非启动应用）
- "查火车票/查机票/查余票/查班次/时刻表/几点到/要多久去/怎么去" → WEB_SEARCH
- "帮我买票/订票/帮我订高铁/12306购票" → AGENT
- 命令执行操作 → 对应类型
- "了解/研究一下" → CHAT（日常"看看"之意）
- "深入研究/系统分析/技术原理" → RESEARCH
- "你会做X么/能否做X/你能X吗/会不会X" → CHAT（询问能力，未明确下达任务指令）
- "做一个关于X的word介绍/帮我做一份XX报告/写一个XX的PDF" → FILE_GEN（含文件格式词+生成动作）
- "介绍一下X/帮我讲讲X" → CHAT（无文件格式词，纯问答）
- "作图"/"画图表"/"做一个图表"/"折线图"/"柱状图"/"饼图"/"散点图"/"数据可视化"/"plot"/"chart" → CODER（不是 PAINTER）
- "画一张艺术图"/"生成AI图片"/"渲染一张"/"上色画" → PAINTER

只输出类型名称，如: CHAT"""

    # 缓存最近的分类结果（避免重复调用）
    _cache = {}
    _cache_max_size = 100
    
    @classmethod
    def _cache_set(cls, key, value):
        """Set a cache entry, evicting oldest half when full."""
        if len(cls._cache) >= cls._CACHE_MAX_SIZE:
            keys = list(cls._cache.keys())
            for k in keys[:len(keys) // 2]:
                del cls._cache[k]
        cls._cache[key] = value

    @classmethod
    def classify(cls, client, user_input: str, timeout: float = 2.0) -> tuple:
        """
        使用 AI 模型分类任务

        Args:
            client: Google GenAI Client instance
            user_input: User prompt
            timeout: Timeout in seconds

        返回: (task_type, confidence, source)
        - task_type: 任务类型
        - confidence: 置信度描述
        - source: "AI" 或 "Cache"
        """
        
        # 动态构建技能路由提示（根据当前启用的 skill）
        _dynamic_instruction = cls.ROUTER_INSTRUCTION
        _skill_hint_hash = ""
        try:
            from app.core.skills.skill_manager import SkillManager
            SkillManager._ensure_init()
            _hints = []
            for s in SkillManager._registry.values():
                if not s.get("enabled"):
                    continue
                _intent = s.get("intent_description", "")
                _tts = s.get("task_types", [])
                if _intent and _tts:
                    _hints.append(
                        f"- 若用户意图是「{_intent}」→ 优先路由到 {_tts[0]}"
                    )
            if _hints:
                _skill_hint_hash = hashlib.md5(
                    "\n".join(_hints).encode()
                ).hexdigest()[:8]
                _dynamic_instruction = (
                    cls.ROUTER_INSTRUCTION
                    + "\n\n当前用户启用的 Skill 路由提示（优先参考）:\n"
                    + "\n".join(_hints)
                )
        except Exception:
            pass

        # 检查缓存（加入 skill 状态哈希，技能启用变化时自动失效）
        cache_key = hashlib.md5(
            (user_input + _skill_hint_hash).encode()
        ).hexdigest()[:16]
        if cache_key in cls._cache:
            cached = cls._cache[cache_key]
            print(f"[AIRouter] Cache hit: {cached}")
            return cached[0], cached[1], "Cache"
        
        try:
            result_holder = {"task": None, "error": None}
            valid_tasks = ["PAINTER", "FILE_GEN", "DOC_ANNOTATE", "RESEARCH",
                           "CODER", "FILE_SEARCH", "SYSTEM", "AGENT", "WEB_SEARCH", "CHAT"]

            def call_model():
                from google.genai import types
                # 构建尝试顺序：当前模型优先，再按降级链补全
                models_to_try = [cls._router_model]
                for m in cls._ROUTER_MODEL_CHAIN:
                    if m not in models_to_try:
                        models_to_try.append(m)
                for model_id in models_to_try:
                    try:
                        response = client.models.generate_content(
                            model=model_id,
                            contents=user_input,
                            config=types.GenerateContentConfig(
                                system_instruction=_dynamic_instruction,
                                max_output_tokens=20,  # 只需要一个词
                                temperature=0.1,  # 低温度，更确定性
                            )
                        )
                    )
                    if response.candidates and response.candidates[0].content.parts:
                        text = response.candidates[0].content.parts[0].text.strip().upper()
                        # 清理输出
                        valid_tasks = ["PAINTER", "FILE_GEN", "DOC_ANNOTATE", "RESEARCH", "CODER", "FILE_SEARCH", "SYSTEM", "AGENT", "WEB_SEARCH", "CHAT"]
                        for task in valid_tasks:
                            if task in text:
                                result_holder['task'] = task
                                return
                        result_holder['task'] = "CHAT"  # 默认
                except Exception as e:
                    result_holder['error'] = str(e)
            
            # 带超时的调用
            thread = threading.Thread(target=call_model, daemon=True)
            thread.start()
            thread.join(timeout=timeout)

            if thread.is_alive():
                print(f"[AIRouter] Timeout after {timeout}s, falling back to CHAT")
                return "CHAT", "Timeout-fallback", "AI"

            if result_holder["error"]:
                print(f"[AIRouter] Error: {result_holder['error']}")
                return None, "Error", "AI"

            task = result_holder["task"]
            if task:
                # 缓存结果
                if len(cls._cache) >= cls._cache_max_size:
                    # 清除一半缓存
                    keys = list(cls._cache.keys())[:cls._cache_max_size // 2]
                    for k in keys:
                        del cls._cache[k]
                cls._cache[cache_key] = (task, "🤖 AI")
                
                print(f"[AIRouter] Classified as: {task}")
                return task, "🤖 AI", "AI"

            return None, "NoResult", "AI"

        except Exception as e:
            print(f"[AIRouter] Exception: {e}")
            return None, "Exception", "AI"

    # ── 带执行提示的分类（classify + skill_prompt hint） ──────────────────

    ROUTER_WITH_HINT_INSTRUCTION = """你是任务分类器。根据用户输入判断任务类型，并生成执行提示 hint。

类型列表（同上）:
PAINTER / FILE_GEN / DOC_ANNOTATE / RESEARCH / CODER / FILE_SEARCH / SYSTEM / AGENT / WEB_SEARCH / CHAT

图表/可视化关键区分:
- "作图"/"画图表"/"折线图"/"柱状图"/"饼图"/"散点图"/"数据可视化"/"plot"/"chart"/"matplotlib" → CODER
- "画一张艺术图"/"生成AI图片"/"渲染一张"/"上色画" → PAINTER

输出 JSON（仅两个字段）:
{"task": "TASK_NAME", "hint": "执行提示或null"}

hint 规则（所有任务均可填写，无特殊要求则填 null）:
- WEB_SEARCH: ≤30字，描述用户期望的响应格式
  例: "用表格显示班次：车次|出发站|到达站|发车|到达|历时|票价"
      "输出气温、天气状况、未来3天预报、出行建议"
      "输出当前价格、今日涨跌幅、近期走势"
      "列出3-5条关键新闻要点"
- RESEARCH: ≤30字，描述分析角度
  例: "从技术原理、优缺点、应用场景三个维度分析"
- CODER: ≤30字，描述代码要求重点
  例: "需要模块化和错误处理" / "包含复杂度分析和测试用例"
  图表例: "用matplotlib创建，标注坐标轴和图例" / "用plotly创建交互式图表"
- CHAT: ≤30字，描述期望输出结构
  例: "输出多个备选方案" / "先下定义再举例，200字内"

只输出 JSON，不输出其他内容。"""

    @classmethod
    def classify_with_hint(cls, client, user_input: str, timeout: float = 3.0) -> tuple:
        """
        分类任务类型并生成执行提示 (skill_prompt hint)。

        返回: (task_type, confidence, source, hint_or_None)
        """
        cache_key = "h:" + hashlib.md5(user_input.encode()).hexdigest()[:16]
        if cache_key in cls._cache:
            cached = cls._cache[cache_key]
            return cached[0], cached[1], "Cache", cached[2] if len(cached) > 2 else None

        try:
            result_holder = {"task": None, "hint": None, "error": None}

            def call_model():
                try:
                    from google.genai import types
                    response = client.models.generate_content(
                        model="gemini-2.0-flash-lite",
                        contents=user_input,
                        config=types.GenerateContentConfig(
                            system_instruction=cls.ROUTER_WITH_HINT_INSTRUCTION,
                            max_output_tokens=150,
                            temperature=0.1,
                            response_mime_type="application/json",
                        )
                    )
                    if response.candidates and response.candidates[0].content.parts:
                        raw = response.candidates[0].content.parts[0].text.strip()
                        import json as _json
                        try:
                            data = _json.loads(raw)
                            task = str(data.get("task", "")).strip().upper()
                            hint_raw = data.get("hint") or None
                            valid_tasks = ["PAINTER", "FILE_GEN", "DOC_ANNOTATE", "RESEARCH",
                                           "CODER", "FILE_SEARCH", "SYSTEM", "AGENT", "WEB_SEARCH", "CHAT"]
                            if task in valid_tasks:
                                result_holder['task'] = task
                                if hint_raw and isinstance(hint_raw, str) and len(hint_raw.strip()) > 3:
                                    result_holder['hint'] = hint_raw.strip()
                                return
                        except Exception:
                            pass
                        # Fallback: plain text extraction
                        valid_tasks = ["PAINTER", "FILE_GEN", "DOC_ANNOTATE", "RESEARCH",
                                       "CODER", "FILE_SEARCH", "SYSTEM", "AGENT", "WEB_SEARCH", "CHAT"]
                        for task in valid_tasks:
                            if task in raw.upper():
                                result_holder['task'] = task
                                return
                        result_holder['task'] = "CHAT"
                except Exception as e:
                    result_holder['error'] = str(e)

            thread = threading.Thread(target=call_model, daemon=True)
            thread.start()
            thread.join(timeout=timeout)

            if thread.is_alive():
                # Timeout: fall back to classify()
                task, conf, src = cls.classify(client, user_input, timeout=timeout)
                return task, conf, src, None

            if result_holder["error"]:
                task, conf, src = cls.classify(client, user_input, timeout=timeout)
                return task, conf, src, None

            task = result_holder["task"]
            hint = result_holder["hint"]
            if task:
                # Cache: store task + hint
                if len(cls._cache) >= cls._cache_max_size:
                    keys = list(cls._cache.keys())[:cls._cache_max_size // 2]
                    for k in keys:
                        del cls._cache[k]
                cls._cache[cache_key] = (task, "🤖 AI+Hint", hint)
                print(f"[AIRouter] classify_with_hint → {task} | hint={'yes' if hint else 'none'}")
                return task, "🤖 AI+Hint", "AI", hint

            task, conf, src = cls.classify(client, user_input, timeout=timeout)
            return task, conf, src, None

        except Exception as e:
            print(f"[AIRouter] classify_with_hint exception: {e}")
            task, conf, src = cls.classify(client, user_input, timeout=timeout)
            return task, conf, src, None
