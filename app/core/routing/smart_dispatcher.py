from typing import Dict, Any, Tuple, List, Optional
import time
import re

# 延迟导入 - 这些模块仅在运行时方法调用时加载，避免启动时加载 google.genai (~4.7s) 和 requests (~0.5s)
# from app.core.routing.local_model_router import LocalModelRouter
# from app.core.routing.ai_router import AIRouter
# from app.core.routing.task_decomposer import TaskDecomposer
# from app.core.routing.local_planner import LocalPlanner

def _get_local_model_router():
    from app.core.routing.local_model_router import LocalModelRouter
    return LocalModelRouter

def _get_ai_router():
    from app.core.routing.ai_router import AIRouter
    return AIRouter

def _get_task_decomposer():
    from app.core.routing.task_decomposer import TaskDecomposer
    return TaskDecomposer

def _get_local_planner():
    from app.core.routing.local_planner import LocalPlanner
    return LocalPlanner

class SmartDispatcher:
    """
    混合智能路由算法
    1. 首先尝试 AI 路由器（快速、智能）
    2. 如果 AI 超时或失败，回退到本地算法
    """
    
    # 是否启用 AI 路由
    USE_AI_ROUTER = True
    
    # 依赖注入容器
    _dependencies = {
        "LocalExecutor": None,
        "ContextAnalyzer": None,
        "WebSearcher": None,
        "MODEL_MAP": {},
        "client": None
    }
    
    @classmethod
    def configure(cls, local_executor, context_analyzer, web_searcher, model_map, client):
        """配置外部依赖"""
        cls._dependencies["LocalExecutor"] = local_executor
        cls._dependencies["ContextAnalyzer"] = context_analyzer
        cls._dependencies["WebSearcher"] = web_searcher
        cls._dependencies["MODEL_MAP"] = model_map
        cls._dependencies["client"] = client

    # 任务语料库 - 每个任务的锚定表达（精简版，作为余量兜底；主分类由 AI 模型完成）
    TASK_CORPUS = {
        "PAINTER":     ["画一张图", "帮我画", "生成图片", "draw me", "generate image"],
        "CODER":       ["写代码", "帮我写个函数", "python实现", "write code", "implement function",
                    "帮我作图", "作一个折线图", "画柱状图", "画饼图", "生成图表", "数据可视化",
                    "用matplotlib画", "画散点图", "plot数据", "chart数据", "统计图"],
        "FILE_GEN":    ["生成word文档", "做ppt", "做一个word", "帮我做一份", "创建pdf", "写一个文档",
                    "export excel", "生成报告模板", "做一个介绍文档", "制作幻灯片"],
        "RESEARCH":    ["深入分析", "全面调研", "technical principle", "in-depth study", "对比分析"],
        "WEB_SEARCH":  ["今天天气", "股价多少", "最新新闻", "current price", "比赛结果",
                    "目前价格", "现在价格", "价格多少", "原油价格", "黄金价格",
                    "布伦特原油", "WTI原油", "白银价格", "铜价", "期货价格",
                    "汇率", "今日价", "实时价格", "加密货币", "比特币价格",
                    "以太坊价格", "黄金行情", "原油行情", "外汇行情",
                    "股市行情", "基金净值", "债券收益率"],
        "FILE_OP":     ["读取文件", "文件列表", "批量重命名", "list files", "整理文件夹"],
        "FILE_EDIT":   ["修改文件", "替换内容", "删除第几行", "edit file", "replace in file"],
        "FILE_SEARCH": ["找文件", "哪个文件", "文件在哪", "find file", "search for"],
        "CHAT":        ["你好", "是什么", "介绍一下", "tell me about", "help me understand"],
        "SYSTEM":      ["打开微信", "启动chrome", "关闭qq", "截图", "系统时间", "shutdown", "关机",
                    "打开steam", "打开edge", "启动vscode", "打开计算器", "关掉任务管理器",
                    "打开加速器", "启动游戏", "打开软件", "运行程序"],
        "AGENT":       ["发微信", "给他发消息", "设提醒", "设闹钟", "帮我买票", "订票",
                    "提醒我", "日历安排", "浏览器打开", "自动发邮件"],
    }

    # ── analyze() 高频常量（提升为类级别，避免每次调用重新分配对象）──────────────
    # Force-Plan 触发词
    _FORCE_PLAN_TRIGGERS: tuple = (
        "请制定计划", "拆解任务", "帮我计划", "分步骤", "一步步",
        "分步完成", "制定方案", "拆分任务", "步骤规划",
        "step by step", "step-by-step", "plan and execute",
    )
    # 文档编辑意图词
    _DOC_EDIT_KEYWORDS: tuple = (
        "修改", "更改", "标注", "批注", "润色", "改写", "校对", "审校", "修订",
        "纠错", "改善", "优化", "调整", "精炼", "通畅", "通顺", "流畅", "精简",
        "凝练", "简洁", "整理", "梳理", "提炼", "整体修改", "修一下", "帮我改",
        "改一改", "改得", "写得", "polish", "refine", "revise", "edit", "improve",
    )
    # 指定路径列举关键词
    _PATH_LIST_KWS: tuple = (
        "归纳", "列出", "列举", "有哪些", "所有", "全部",
        "找", "查", "看看", "显示", "汇总", "整理",
        "监控", "监视", "停止监控", "自动归类",
        "提取", "关键信息", "合同信息", "解读", "分析",
        "哪个", "哪些", "里面", "这几份", "对比",
        "哪几", "几个", "几份", "路径下", "下有", "下面有",
        "属于", "什么文件", "文件类型", "下的文件",
    )
    # 系统命令动词（快速通道 + 兜底共享）
    _SYS_ACTION_STARTERS: tuple = ("打开", "启动", "运行", "开启", "关闭", "退出", "关掉", "杀掉")
    # 快速通道系统命令排除词（宽）
    _SYS_FAST_EXCLUDE: tuple = (
        "怎么", "如何", "什么", "为什么", "能不能", "可以吗", "怎样", "咋",
        "文件", "网页", "网址", "url", "网站", "链接", "附件",
        "思路", "方式", "方法", "问题", "功能",
    )
    # 兜底系统命令排除词（窄）
    _SYS_FALLBACK_EXCLUDE: tuple = (
        "怎么", "如何", "什么", "文件", "网页", "网站", "思路", "方法", "功能",
    )
    # 提醒/消息快速通道正则
    _AGENT_NOTIFY_PATTERNS: tuple = (
        r'(设置?|帮我设?)(提醒|闹钟|定时).{0,20}',
        r'提醒我.{0,25}(点|时|分|号|日)',
        r'(给|向).{1,8}(发|回)(微信|消息|邮件)',
        r'(发|回)(微信|消息).{0,15}给.{1,8}',
    )
    # AI 绘画快速通道正则
    _PAINTER_FAST_PATTERNS: tuple = (
        r'(画|做|生成|创作|绘制|帮我画|帮我做|帮我生成).{0,20}(图片|照片|壁纸|头像|封面)',
        r'(画|做|生成|创作|绘制|帮我画|帮我做|帮我生成).{0,3}(一张|一幅|一个|张|幅).{0,30}图',
        r'(一张|一幅).{1,20}(图|图片|照片)',
        r'(ai|AI).{0,5}(画|绘|生成|创作)',
    )
    # 图表/可视化排除词（区分 PAINTER vs CODER）
    _PAINTER_CHART_EXCLUDE: tuple = (
        "图表", "折线图", "柱状图", "饼图", "散点图", "直方图", "条形图",
        "可视化", "统计图", "数据图", "chart", "plot", "matplotlib",
    )
    # 天气快速通道关键词
    _WEATHER_KWS: tuple = (
        "天气", "气温", "下雨吗", "下雨", "下雪吗", "下雪", "天气怎么样", "天气怎样",
        "天气预报", "weather", "温度多少", "穿什么衣服",
    )
    # 代码编写快速通道
    _CODE_WRITE_VERBS: tuple = (
        "帮我写", "给我写", "写一个", "写个", "实现", "编写", "开发", "编程",
    )
    _CODE_CONCEPTS: tuple = (
        "函数", "算法", "类", "接口", "脚本", "程序", "代码",
        "排序", "查找", "递归", "遍历", "爬虫", "api", "模块",
    )
    _CODE_LANGS: tuple = (
        "python", "javascript", "java", "c++", "golang", "rust",
        "typescript", "kotlin", "swift", "php", "ruby", "sql",
    )
    # 时效性快速通道
    _REALTIME_SIGNALS: tuple = (
        "目前", "现在", "当前", "最新", "近期", "今日", "近况",
    )
    _REALTIME_TOPIC_KWS: tuple = (
        "新闻", "消息", "进展", "动态", "局势", "战况", "现状", "情况",
        "比分", "结果", "成绩", "排名", "股价", "金价", "油价",
    )
    _REALTIME_EXCLUDE_KWS: tuple = (
        "历史", "是什么", "什么是", "定义", "原理", "原因", "介绍", "解释",
    )
    # 数据图表/可视化快速通道
    _CHART_KWS: tuple = (
        "图表", "折线图", "柱状图", "饼图", "散点图", "直方图", "条形图", "热力图",
        "作图", "可视化", "统计图", "数据图", "chart", "plot", "graph",
        "matplotlib", "seaborn", "plotly", "echarts",
    )
    # 出行查询快速通道正则
    _TRAVEL_SEARCH_PATTERNS: tuple = (
        r'(查|查询|查一下|看|有没有|有无|还有).{0,8}(火车票|高铁票|动车票|机票|余票|班次)',
        r'(下周|明天|后天|今天|大后天|[0-9]+[号日]).{0,14}(去|到|从).{0,14}(高铁|动车|火车|航班|机票)',
        r'(去|从).{1,14}(去|到).{1,20}(火车|高铁|动车|机)',
        r'(几点|什么时候).{0,8}(从|到|出发|到达).{0,12}(车|班|次|机)',
        r'(余票|时刻表|列车时刻|航班动态|航班查询)',
    )
    # 购票意图词（出行快速通道 + 本地模型 override 共享）
    _TICKET_BUY_KWS: tuple = ("订票", "买票", "购票", "帮我买", "帮我订", "12306")
    # 出行查询关键词（本地模型 override 用）
    _TICKET_QUERY_KWS: tuple = ("12306", "火车票", "高铁票", "动车票")
    # 金融资产/价格快速通道
    _PRICE_ASSETS: tuple = (
        "原油", "布伦特", "wti", "天然气", "黄金", "白银", "铜", "铁矿石",
        "大豆", "小麦", "棉花", "黄铜", "铝", "锌", "铅", "镍",
        "比特币", "以太坊", "btc", "eth", "加密货币", "数字货币",
        "美元", "欧元", "日元", "英镑", "港币", "外汇", "汇率",
        "a股", "港股", "道琼斯", "纳斯达克", "标普", "上证", "深证",
        "期货", "基金", "债券", "股票",
        "金价", "油价", "银价", "铜价",
    )
    _PRICE_SIGNALS: tuple = (
        "价格", "现价", "报价", "行情", "走势", "涨跌", "多少钱",
        "今日价", "实时", "最新价", "开盘", "收盘", "涨了", "跌了",
    )
    # AGENT override 正则（本地模型 override 段）
    _AGENT_OVERRIDE_PATTERNS: tuple = (
        r"发微信", r"回微信", r"微信发", r"微信回",
        r"给.{1,6}发消息", r"给.{1,6}发微信",
        r"浏览器打开", r"点击.{1,6}按钮",
    )
    # FILE_GEN 意图词（本地模型 override 段）
    _FILE_GEN_INTENT_KWS: tuple = (
        "生成", "创建", "导出", "制作", "做", "写", "帮我做", "帮我写",
        "ppt", "word", "docx", "pdf", "excel", "报告", "文档", "幻灯片",
        "输出", "保存", "做成", "转成", "介绍",
    )
    # FILE_GEN 意图词（AI 路由 override 段，语义更宽）
    _AI_FILE_GEN_OUTPUT_KWS: tuple = (
        "生成", "创建", "导出", "制作", "输出", "保存为", "做成", "转成",
        "写份", "写一份", "写一个", "做一个", "做一份", "做个",
        "word", "docx", ".doc", "pdf", "excel", "ppt", "幻灯片",
        "帮我做", "帮我写", "帮我生成", "报告", "文档", "介绍文档",
    )
    # PPT/文档生成兜底关键词
    _PPT_KEYWORDS: tuple = (
        "ppt", "幻灯片", "演示文稿", "presentation", "slide", "slides", ".pptx",
    )
    _PPT_ACTION_KWS: tuple = (
        "做", "生成", "创建", "制作", "做一个", "做个", "帮我做", "帮我生成",
    )
    _DOC_GEN_OUTPUT_KWS: tuple = (
        "word", "docx", ".doc", "pdf", "excel", ".xlsx", "报告", "文档", "介绍文档", "word版",
    )
    _DOC_GEN_ACTION_KWS: tuple = (
        "做一个", "做一份", "做个", "写一份", "写一个", "帮我做", "帮我写",
        "生成一个", "生成一份", "生成", "创建一个", "创建一份", "制作",
    )
    # 文件搜索兜底正则
    _FILE_SEARCH_PATTERNS: tuple = (
        r"帮我找.{0,20}文件", r"找一下.{1,30}", r"找找.{1,30}",
        r"找到.{1,20}文件", r"定位.{1,20}文件", r"搜索文件",
        r"在哪(里|儿|个目录)", r"哪个文件.{0,10}",
        r"扫描(我的)?(电脑|磁盘|硬盘|文件)", r"全盘扫描",
        r"帮我打开.{1,30}(文件|\.)",
    )
    # RAG 搜索跟进动词
    _SEARCH_VERBS: tuple = (
        "查", "搜", "搜索", "查询", "找", "再找", "再查", "再搜", "再看看",
    )
    # ML 兜底疑问词
    _QUESTION_WORDS: tuple = (
        "怎么", "如何", "什么", "为什么", "能不能", "可以吗",
        "怎样", "咋", "啥", "how", "what", "why", "which",
    )

    # 预计算特征 (字符级 n-gram)
    _features = None
    _task_vectors = None
    
    @classmethod
    def _init_features(cls):
        """初始化特征向量 (懒加载)"""
        if cls._features is not None:
            return
        
        all_ngrams = set()
        for corpus in cls.TASK_CORPUS.values():
            for text in corpus:
                ngrams = cls._extract_ngrams(text)
                all_ngrams.update(ngrams)
        
        cls._features = list(all_ngrams)
        
        cls._task_vectors = {}
        for task, corpus in cls.TASK_CORPUS.items():
            vectors = [cls._text_to_vector(text) for text in corpus]
            avg_vector = [sum(v[i] for v in vectors) / len(vectors) for i in range(len(cls._features))]
            cls._task_vectors[task] = avg_vector

    @classmethod
    def _compute_similarity_scores(cls, user_input: str) -> dict:
        """计算各任务的相似度分数"""
        if cls._features is None or cls._task_vectors is None:
            cls._init_features()
        user_vector = cls._text_to_vector(user_input)
        return {
            task: cls._cosine_similarity(user_vector, task_vector)
            for task, task_vector in cls._task_vectors.items()
        }

    @classmethod
    def _build_routing_list(cls, scores: dict, boosts: dict = None, reasons: dict = None, top_k: int = 6) -> list:
        """构建路由分配列表（用于可视化展示）"""
        boosts = boosts or {}
        reasons = reasons or {}
        routing = []
        for task, score in scores.items():
            final_score = max(score, boosts.get(task, 0))
            reason_list = reasons.get(task, [])
            if not reason_list:
                reason_list = ["similarity"]
            routing.append({
                "task": task,
                "score": float(final_score),
                "reason": " + ".join(reason_list)
            })
        routing.sort(key=lambda x: x["score"], reverse=True)
        return routing[:top_k]
    
    # ──────────────────────────────────────────────────────────────
    # 极简快速通道：无需任何 AI 分类器即可确认的简单输入
    # ──────────────────────────────────────────────────────────────
    _TRIVIAL_GREETINGS = {
        "你好", "你好呀", "你好啊", "hi", "hello", "哈喽", "嗨", "hey",
        "早上好", "早安", "中午好", "下午好", "晚上好", "晚安",
        "谢谢", "谢谢你", "谢了", "感谢", "多谢", "thanks", "thank you",
        "再见", "拜拜", "bye", "goodbye", "下次见",
        "好的", "好", "嗯", "嗯嗯", "明白了", "知道了", "收到", "ok", "okay",
    }
    _TRIVIAL_IDENTITY: tuple = (
        "你是谁", "你叫什么", "你叫啥", "你是什么", "介绍一下你自己", "你是koto", "koto是什么",
    )
    # 若存在这些词，再短也不能走极简通道
    _TRIVIAL_EXCLUDE: tuple = (
        "画", "图片", "照片", "图", "代码", "程序", "脚本", "文件", "文档", "报告",
        "pdf", "word", "excel", "ppt", "天气", "股价", "新闻", "汇率",
        "打开", "关闭", "截图", "启动", "运行", "搜索",
        "微信", "发送", "发消息", "发邮件", "购票",
        "研究", "分析", "深入", "全面",
        # 图表/数据可视化 — 防止「帮我作图」被误判为极简 CHAT
        "作图", "图表", "折线图", "柱状图", "饼图", "散点图", "直方图", "可视化",
        "统计图", "数据图", "chart", "plot", "matplotlib", "seaborn", "plotly",
        # 金融/商品资产词 — 防止「布伦特原油价格」被短句极简通道误判为 CHAT
        "原油", "布伦特", "黄金", "白银", "铜价", "期货", "汇率", "比特币",
        "以太坊", "价格", "行情", "走势", "现价", "涨跌",
        # 金价/油价等简写形式
        "金价", "油价", "银价", "气价",
        # 天气相关变体
        "下雨", "下雪", "气温", "天气",
        # 编程/代码关键词 — 防止「帮我写个Python排序函数」被极简通道误判为 CHAT
        "python", "javascript", "java", "golang", "rust", "c++", "sql",
        "函数", "算法", "脚本", "接口", "api",
        # 时效性信号词 — 防止「目前金价」「近期AI动态」被极简通道漏判
        "目前", "近期", "局势", "战况", "动态", "进展", "现状", "近况",
    )

    @classmethod
    def _is_trivial_input(cls, user_input: str) -> bool:
        """
        判断是否为极简输入，可不经任何 AI 分类器、直接路由到 CHAT + 本地模型。
        条件：
          1. 是已知问候/致谢/确认词，或
          2. 是简短身份询问（≤20字），或
          3. 长度 ≤15 字且不含复杂任务关键词
        """
        text = user_input.strip()
        tl = text.lower()

        if tl in cls._TRIVIAL_GREETINGS:
            return True

        if len(text) <= 20 and any(kw in tl for kw in cls._TRIVIAL_IDENTITY):
            return True

        if len(text) <= 15 and not any(k in tl for k in cls._TRIVIAL_EXCLUDE):
            return True

        return False

    @staticmethod
    def _extract_ngrams(text, n=2):
        """提取字符级 n-gram"""
        text = text.lower().strip()
        ngrams = set()
        for char in text:
            if char.strip():
                ngrams.add(char)
        for i in range(len(text) - 1):
            if text[i:i+2].strip():
                ngrams.add(text[i:i+2])
        return ngrams
    
    @classmethod
    def _quick_task_hint(cls, user_input: str) -> str:
        text_lower = user_input.lower()
        # 数据图表/可视化 — 必须在通配"图"之前检查，否则折线图/柱状图/作图会被误送 PAINTER
        if any(k in text_lower for k in [
            "图表", "折线图", "柱状图", "饼图", "散点图", "直方图", "作图",
            "可视化", "统计图", "数据图", "chart", "plot", "matplotlib",
            "seaborn", "plotly", "echarts",
        ]):
            return "CODER"
        # AI 绘画/图片生成（通配"图"放在图表检查之后）
        if any(k in text_lower for k in ["画", "图片", "照片", "生成图", "绘制", "绘图", "ai画", "图"]):
            return "PAINTER"
        if any(k in text_lower for k in ["代码", "编程", "python", "javascript", "函数"]):
            return "CODER"
        if any(k in text_lower for k in ["查", "搜索", "价格", "天气", "新闻"]):
            return "WEB_SEARCH"
        # 系统操作：命令动词开头 + 短输入
        _sys_starters = ("打开", "启动", "运行", "开启", "关闭", "退出", "关掉", "杀掉")
        _sys_exclude = ("怎么", "如何", "什么", "文件", "网页", "网站", "思路", "方法")
        stripped = user_input.strip()
        if (
            len(stripped) <= 18
            and any(stripped.startswith(s) for s in _sys_starters)
            and not any(k in text_lower for k in _sys_exclude)
        ):
            return "SYSTEM"
        # 提醒/消息 → AGENT
        if any(k in text_lower for k in ["提醒我", "提醒一下", "设闹钟", "设提醒", "发微信"]):
            return "AGENT"
        # 当输入附带文件前缀 [FILE_ATTACHED:ext] 时，优先判断是编辑已有文件还是生成新文件
        # 避免 "[FILE_ATTACHED:.docx]" 中的 "docx" 直接触发 FILE_GEN 误路由
        if "[file_attached:" in text_lower:
            _file_edit_hints = [
                "修改", "更改", "标注", "批注", "润色", "改写", "校对", "审校", "修订",
                "纠错", "改善", "优化", "调整", "精炼", "通畅", "整体修改", "通顺",
                "流畅", "精简", "凝练", "简洁", "整理", "梳理", "提炼", "修一下",
                "帮我改", "改一改", "改得", "写得", "改写", "polish", "refine", "revise",
            ]
            if any(k in text_lower for k in _file_edit_hints):
                return "DOC_ANNOTATE"
        if any(k in text_lower for k in ["word", "pdf", "docx", "表格", "文档", "报告", "生成", "做成", "标注", "批注", "润色", "改写", "校对", "审校", "修订", "纠错"]):
            return "FILE_GEN"
        if any(k in text_lower for k in ["研究", "分析", "深入", "介绍"]):
            return "RESEARCH"
        return "CHAT"
    
    @classmethod
    def _text_to_vector(cls, text):
        if cls._features is None:
            cls._init_features()
        ngrams = cls._extract_ngrams(text)
        vector = [1 if f in ngrams else 0 for f in cls._features]
        return vector
    
    @staticmethod
    def _cosine_similarity(v1, v2):
        dot_product = sum(a * b for a, b in zip(v1, v2))
        norm1 = sum(a * a for a in v1) ** 0.5
        norm2 = sum(b * b for b in v2) ** 0.5
        if norm1 == 0 or norm2 == 0:
            return 0
        return dot_product / (norm1 * norm2)
    
    @classmethod
    def _get_dep(cls, name):
        """Helper to get dependency safely"""
        return cls._dependencies.get(name)

    @staticmethod
    def _should_use_annotation_system(user_input, has_file=False):
        """Simplistic check if annotation system should be used"""
        # This logic was previously inline or imported, implementing basic check here
        keywords = ["标注", "批注", "润色", "改写", "校对", "审校", "修订", "纠错", "改善", "优化", "修改"]
        quality_words = ["不合适", "生硬", "翻译腔", "语序", "用词", "逻辑", "问题"]
        target_words = ["翻译", "文章", "文档", "内容", "文本", "段落", "句子", "字词"]
        
        if not has_file:
            return False
            
        has_kw = any(k in user_input for k in keywords)
        has_qw = any(q in user_input for q in quality_words)
        has_target = any(t in user_input for t in target_words)
        
        return has_kw or (has_qw and has_target)

    @classmethod
    def analyze(cls, user_input: str, history=None, file_context=None):
        """
        智能分析用户输入，返回最匹配的任务类型
        优先级：规则检测 > 本地快速模型 > RAG > 远程AI > 本地语料
        
        返回: (task_type, confidence_info, context_info)
        """
        start_time = time.time()
        
        # Get dependencies
        LocalExecutor = cls._get_dep("LocalExecutor")
        ContextAnalyzer = cls._get_dep("ContextAnalyzer")
        WebSearcher = cls._get_dep("WebSearcher")
        client = cls._get_dep("client")
        
        # 初始化特征 (首次调用)
        cls._init_features()
        
        user_lower = user_input.lower().strip()
        context_info = None
        similarity_scores = cls._compute_similarity_scores(user_input)
        base_routing_list = cls._build_routing_list(similarity_scores)

        # 剥离 [FILE_ATTACHED:ext] 前缀用于长度判断和极简通道检测
        # （该前缀由 app.py 注入以辅助本地模型，但不应影响 trivial/short 判断）
        _input_for_trivial = re.sub(r'^\[FILE_ATTACHED:[^\]]+\]\s*', '', user_input).strip()
        
        # === 0. Force Plan Mode (New Feature) ===
        if user_input.strip().startswith("/plan ") or any(t in user_input for t in cls._FORCE_PLAN_TRIGGERS):
            context_info = {"complexity": "complex", "is_multi_step_task": True}
            context_info["multi_step_info"] = {
                "pattern": "forced_plan",
                "description": "User forced planning mode"
            }
            context_info["routing_list"] = cls._build_routing_list(
                similarity_scores, 
                boosts={"MULTI_STEP": 1.0},
                reasons={"MULTI_STEP": ["user_forced"]}
            )
            return "MULTI_STEP", "🛠️ Forced-Plan", context_info
        
        # === 优先：文件附件处理 logic ===
        if file_context and file_context.get("has_file"):
            file_ext = file_context.get("file_type", "")
            has_edit_intent = any(kw in user_lower for kw in cls._DOC_EDIT_KEYWORDS)
            
            if has_edit_intent and file_ext in [".docx", ".doc"]:
                context_info = {"complexity": "complex"}
                context_info["routing_list"] = cls._build_routing_list(
                    similarity_scores, 
                    boosts={"DOC_ANNOTATE": 1.0},
                    reasons={"DOC_ANNOTATE": ["rule:doc_annotate"]}
                )
                print(f"[SmartDispatcher] 📄 检测到 Word 文档标注请求: {file_ext}")
                return "DOC_ANNOTATE", "📄 Doc-Annotate", context_info
            elif has_edit_intent and file_ext in [".md", ".txt"]:
                context_info = {"complexity": "complex", "is_multi_step_task": True}
                context_info["multi_step_info"] = {
                    "pattern": "document_workflow",
                    "description": "文档智能编辑工作流"
                }
                context_info["routing_list"] = cls._build_routing_list(
                    similarity_scores, 
                    boosts={"MULTI_STEP": 1.0},
                    reasons={"MULTI_STEP": ["rule:doc_workflow"]}
                )
                print(f"[SmartDispatcher] 📄 检测到文件编辑请求: {file_ext}")
                return "MULTI_STEP", "📄 Doc-Workflow", context_info

        # === 快速通道: 超短输入（用去前缀的原始文本判断）===
        if len(_input_for_trivial) <= 3:
            if LocalExecutor and LocalExecutor.is_system_command(_input_for_trivial):
                context_info = context_info or {}
                context_info["routing_list"] = cls._build_routing_list(
                    similarity_scores,
                    boosts={"SYSTEM": 1.0},
                    reasons={"SYSTEM": ["rule:standalone_command"]}
                )
                return "SYSTEM", "🖥️ Rule-Detected", context_info
            return "CHAT", "⚡ Quick", None

        # === 指定路径文件列举快速通道（最高优先级，防止被误路由到 FILE_GEN/CHAT）===
        # 匹配：输入含 Windows 路径（如 C:\xxx）且含列举/归纳/查找意图关键词
        if re.search(r'[A-Za-z]:[\\]', user_input):
            if any(k in user_input for k in cls._PATH_LIST_KWS):
                context_info = context_info or {}
                context_info["routing_list"] = cls._build_routing_list(
                    similarity_scores,
                    boosts={"FILE_SEARCH": 1.0},
                    reasons={"FILE_SEARCH": ["rule:path_listing"]}
                )
                print(f"[SmartDispatcher] 📁 指定路径列举快速通道: '{user_input[:40]}' → FILE_SEARCH")
                return "FILE_SEARCH", "📁 Path-Listing", context_info

        # === 系统操作快速通道（打开/启动/关闭 + 应用名，不依赖 APP_ALIASES）===
        # 命令语气、短输入、不含问句/文件/网页关键词
        _stripped = user_input.strip()
        if (
            len(_stripped) <= 18
            and any(_stripped.startswith(s) for s in cls._SYS_ACTION_STARTERS)
            and not any(k in user_lower for k in cls._SYS_FAST_EXCLUDE)
        ):
            context_info = context_info or {}
            context_info["routing_list"] = cls._build_routing_list(
                similarity_scores,
                boosts={"SYSTEM": 1.0},
                reasons={"SYSTEM": ["rule:action_verb_direct"]}
            )
            print(f"[SmartDispatcher] 🖥️ 系统操作快速通道: '{_stripped}' → SYSTEM")
            return "SYSTEM", "🖥️ Action-Direct", context_info

        # === 提醒/日程/消息发送快速通道 → AGENT ===
        if any(re.search(p, user_input) for p in cls._AGENT_NOTIFY_PATTERNS):
            context_info = context_info or {}
            context_info["routing_list"] = cls._build_routing_list(
                similarity_scores,
                boosts={"AGENT": 1.0},
                reasons={"AGENT": ["rule:agent_notify_direct"]}
            )
            print(f"[SmartDispatcher] 🤖 提醒/消息快速通道: '{user_input[:30]}' → AGENT")
            return "AGENT", "🤖 Notify-Direct", context_info

        # === AI绘画/图片生成快速通道（在极简通道之前，防止短句被误分到 CHAT）===
        # 匹配：画/做/生成 + 一张/个/幅 + 任意内容 + 图/图片/照片；或含明确图片生成词
        if any(re.search(p, user_input) for p in cls._PAINTER_FAST_PATTERNS):
            # 排除图表/可视化词（那些走 CODER）
            _not_chart = not any(k in user_lower for k in cls._PAINTER_CHART_EXCLUDE)
            if _not_chart:
                context_info = context_info or {}
                context_info["routing_list"] = cls._build_routing_list(
                    similarity_scores,
                    boosts={"PAINTER": 1.0},
                    reasons={"PAINTER": ["rule:image_gen"]}
                )
                print(f"[SmartDispatcher] 🎨 图片生成快速通道: '{user_input[:30]}' → PAINTER")
                return "PAINTER", "🎨 Image-Direct", context_info

        # === 极简通道: 明显的闲聊/问候/简短问答，跳过所有分类器 ===
        # 使用去掉 [FILE_ATTACHED:ext] 前缀的原始文本，避免前缀膨胀导致误分类
        if cls._is_trivial_input(_input_for_trivial):
            context_info = context_info or {}
            context_info["routing_list"] = cls._build_routing_list(
                similarity_scores,
                boosts={"CHAT": 1.0},
                reasons={"CHAT": ["rule:trivial"]}
            )
            print(f"[SmartDispatcher] ⚡ 极简通道: '{_input_for_trivial[:20]}' → CHAT (跳过分类器)")
            return "CHAT", "⚡ Trivial", context_info

        # === 天气 / 实时信息快速通道（在 Trivial 之后、模型之前，防止冷启动漏判）===
        if any(k in user_lower for k in cls._WEATHER_KWS):
            context_info = context_info or {}
            context_info["routing_list"] = cls._build_routing_list(
                similarity_scores,
                boosts={"WEB_SEARCH": 1.0},
                reasons={"WEB_SEARCH": ["rule:weather_direct"]}
            )
            print(f"[SmartDispatcher] 🌤️ 天气实时快速通道: '{user_input[:30]}' → WEB_SEARCH")
            return "WEB_SEARCH", "🌤️ Weather-Direct", context_info

        # === 代码编写快速通道（在本地模型之前，避免 koto-router 误判明确写代码请求）===
        # 条件：含写作动词 + 编程语言/代码概念，但不是"帮我写一段自我介绍"这类纯文本
        _has_code_verb = any(v in user_lower for v in cls._CODE_WRITE_VERBS)
        _has_code_concept = any(c in user_lower for c in cls._CODE_CONCEPTS)
        _has_code_lang = any(l in user_lower for l in cls._CODE_LANGS)
        if _has_code_verb and (_has_code_concept or _has_code_lang):
            context_info = context_info or {}
            context_info["routing_list"] = cls._build_routing_list(
                similarity_scores,
                boosts={"CODER": 1.0},
                reasons={"CODER": ["rule:code_write_direct"]}
            )
            print(f"[SmartDispatcher] 💻 代码编写快速通道: '{user_input[:30]}' → CODER")
            return "CODER", "💻 Code-Write-Direct", context_info

        # === 时效性关键词快速通道（目前/近期/最新 + 时事主题）===
        if (
            any(s in user_lower for s in cls._REALTIME_SIGNALS)
            and any(t in user_lower for t in cls._REALTIME_TOPIC_KWS)
            and not any(e in user_lower for e in cls._REALTIME_EXCLUDE_KWS)
        ):
            context_info = context_info or {}
            context_info["routing_list"] = cls._build_routing_list(
                similarity_scores,
                boosts={"WEB_SEARCH": 1.0},
                reasons={"WEB_SEARCH": ["rule:realtime_signal"]}
            )
            print(f"[SmartDispatcher] ⏰ 时效信息快速通道: '{user_input[:30]}' → WEB_SEARCH")
            return "WEB_SEARCH", "⏰ Realtime-Direct", context_info

        # === 数据图表/可视化快速通道（在所有模型之前，防止被误路由到 PAINTER/CHAT）===
        if any(k in user_lower for k in cls._CHART_KWS):
            # 包含图表类型词就直接走 CODER（数据可视化），不必配合动作词
            context_info = context_info or {}
            context_info["routing_list"] = cls._build_routing_list(
                similarity_scores,
                boosts={"CODER": 1.0},
                reasons={"CODER": ["rule:chart_viz"]}
            )
            print(f"[SmartDispatcher] 📊 图表可视化快速通道: '{user_input[:30]}' → CODER")
            return "CODER", "📊 Chart-Direct", context_info

        # === 实时出行查询快速通道（在所有模型之前，确保不被误判为CHAT/AGENT）===
        if any(re.search(p, user_input) for p in cls._TRAVEL_SEARCH_PATTERNS):
            if any(k in user_lower for k in cls._TICKET_BUY_KWS):
                context_info = context_info or {}
                context_info["routing_list"] = cls._build_routing_list(
                    similarity_scores, boosts={"AGENT": 1.0},
                    reasons={"AGENT": ["rule:ticket_buy"]}
                )
                return "AGENT", "🤖 Ticket-Buy", context_info
            context_info = context_info or {}
            context_info["routing_list"] = cls._build_routing_list(
                similarity_scores, boosts={"WEB_SEARCH": 1.0},
                reasons={"WEB_SEARCH": ["rule:travel_query"]}
            )
            print(f"[SmartDispatcher] 🌐 出行查询快速通道: '{user_input[:30]}' → WEB_SEARCH")
            return "WEB_SEARCH", "🌐 Travel-Query", context_info

        # === 金融/商品价格快速通道（价格 = 实时数据，强制 WEB_SEARCH）===
        _has_asset = any(k in user_lower for k in cls._PRICE_ASSETS)
        _has_price_signal = any(k in user_lower for k in cls._PRICE_SIGNALS)
        # 资产名称 + 价格信号词 → 强制 WEB_SEARCH（无需时效词）
        # 纯资产名称（无价格词）也路由 WEB_SEARCH，因询问资产名称本身通常是为了了解价格
        if _has_asset and (_has_price_signal or len(user_input.strip()) <= 12):
            context_info = context_info or {}
            context_info["routing_list"] = cls._build_routing_list(
                similarity_scores,
                boosts={"WEB_SEARCH": 1.0},
                reasons={"WEB_SEARCH": ["rule:financial_price"]}
            )
            print(f"[SmartDispatcher] 💹 金融价格快速通道: '{user_input[:30]}' → WEB_SEARCH")
            return "WEB_SEARCH", "💹 Price-Direct", context_info

        # === 多步任务抢先规划（Pre-empt）===
        # 对于强信号多步输入（如"查询X然后生成Y"），在模型分类前直接触发 LocalPlanner
        # 这样可避免单任务路由器把复合任务误拆为单步
        try:
            _LocalPlannerCls = _get_local_planner()
            if _LocalPlannerCls.should_preempt(user_input):
                _early_plan = _LocalPlannerCls.plan(user_input, timeout=5.0)
                if _early_plan and _early_plan.get("use_planner") and len(_early_plan.get("steps", [])) >= 2:
                    context_info = context_info or {}
                    context_info["is_multi_step_task"] = True
                    context_info["multi_step_info"] = {
                        "pattern": "local_plan",
                        "subtasks": _early_plan["steps"],
                    }
                    context_info["routing_list"] = cls._build_routing_list(
                        similarity_scores,
                        boosts={"MULTI_STEP": 1.0},
                        reasons={"MULTI_STEP": ["preempt:local_planner"]}
                    )
                    print(f"[SmartDispatcher] 🧭 多步抢先规划成功: {len(_early_plan['steps'])} 步 → MULTI_STEP")
                    return "MULTI_STEP", "🧭 Preempt-Plan", context_info
        except Exception as _pe:
            print(f"[SmartDispatcher] ⚠️ 多步抢先规划异常（跳过）: {_pe}")

        # === 本地 Ollama 路由（优先信号，低置信再回退规则） ===
        # classify_with_hint() 同时返回任务分类 + skill_prompt + complexity，实现「本地理解意图 → 生成执行指令 → 云端模型执行」
        local_task, local_confidence, local_source, local_hint, local_complexity = _get_local_model_router().classify_with_hint(user_input, timeout=4.5)
        local_conf_value = 0.0
        if isinstance(local_confidence, str):
            m = re.search(r"(\d+\.\d+)", local_confidence)
            if m:
                try:
                    local_conf_value = float(m.group(1))
                except Exception:
                    local_conf_value = 0.0
        elif isinstance(local_confidence, (int, float)):
            local_conf_value = float(local_confidence)

        local_confident = local_conf_value >= 0.70
        if local_task:
            if local_task == "CHAT" and WebSearcher and WebSearcher.needs_web_search(user_input):
                context_info = context_info or {}
                context_info["routing_list"] = cls._build_routing_list(
                    similarity_scores,
                    boosts={"WEB_SEARCH": 0.95},
                    reasons={"WEB_SEARCH": ["override:chat_to_web_search"]}
                )
                return "WEB_SEARCH", "🌐 Override-Detected", context_info

            if LocalExecutor and LocalExecutor.is_system_command(user_input) and local_task != "SYSTEM":
                context_info = context_info or {}
                context_info["routing_list"] = cls._build_routing_list(
                    similarity_scores,
                    boosts={"SYSTEM": 0.95},
                    reasons={"SYSTEM": ["local_override:system"]}
                )
                return "SYSTEM", "🖥️ Local-Override", context_info

            if any(re.search(p, user_lower) for p in cls._AGENT_OVERRIDE_PATTERNS):
                context_info = context_info or {}
                context_info["routing_list"] = cls._build_routing_list(
                    similarity_scores,
                    boosts={"AGENT": 0.95},
                    reasons={"AGENT": ["local_override:agent"]}
                )
                return "AGENT", "🤖 Local-Override", context_info

            if any(k in user_lower for k in cls._TICKET_QUERY_KWS):
                # 区分「查票」→ WEB_SEARCH 和「买/订票」→ AGENT
                context_info = context_info or {}
                if any(k in user_lower for k in cls._TICKET_BUY_KWS):
                    context_info["routing_list"] = cls._build_routing_list(
                        similarity_scores, boosts={"AGENT": 0.95},
                        reasons={"AGENT": ["local_override:ticket_buy"]}
                    )
                    return "AGENT", "🤖 Local-Override", context_info
                else:
                    context_info["routing_list"] = cls._build_routing_list(
                        similarity_scores, boosts={"WEB_SEARCH": 0.95},
                        reasons={"WEB_SEARCH": ["local_override:ticket_query"]}
                    )
                    print(f"[SmartDispatcher] 🌐 票务查询 → WEB_SEARCH")
                    return "WEB_SEARCH", "🌐 Ticket-Query", context_info

            # DOC_ANNOTATE 需要文件附件（编辑已有文档）；FILE_GEN 是生成新文件，无需附件
            if local_task == "DOC_ANNOTATE":
                has_file = file_context and file_context.get("has_file")
                if not has_file:
                    print(f"[SmartDispatcher] ⚠️ 本地模型返回 DOC_ANNOTATE 但无文件上下文，跳过")
                    pass
                else:
                    if not local_confident:
                        print(f"[SmartDispatcher] ⚠️ 本地模型低置信度({local_conf_value:.2f})，回退规则判断")
                        pass
                    else:
                        context_info = context_info or {}
                        context_info["routing_list"] = cls._build_routing_list(
                            similarity_scores,
                            boosts={"DOC_ANNOTATE": 0.9},
                            reasons={"DOC_ANNOTATE": ["local_model_with_file"]}
                        )
                        if local_hint:
                            context_info["skill_prompt"] = local_hint
                        if local_complexity == "complex":
                            context_info["complexity"] = "complex"
                        return "DOC_ANNOTATE", f"{local_confidence}", context_info
            elif local_task == "FILE_GEN":
                # FILE_GEN 不需要已有文件——检测生成意图关键词即可放行
                _has_file_gen_intent = any(w in user_lower for w in cls._FILE_GEN_INTENT_KWS)
                if _has_file_gen_intent and local_confident:
                    context_info = context_info or {}
                    context_info["routing_list"] = cls._build_routing_list(
                        similarity_scores,
                        boosts={"FILE_GEN": 0.9},
                        reasons={"FILE_GEN": ["local_model_file_gen"]}
                    )
                    if local_hint:
                        context_info["skill_prompt"] = local_hint
                    if local_complexity == "complex":
                        context_info["complexity"] = "complex"
                    print(f"[SmartDispatcher] 📄 本地模型 FILE_GEN 意图确认: '{user_input[:30]}'")
                    return "FILE_GEN", f"{local_confidence}", context_info
                else:
                    print(f"[SmartDispatcher] ⚠️ 本地模型返回 FILE_GEN 但{'无生成意图' if not _has_file_gen_intent else '低置信度'}，继续评估")
            else:
                if not local_confident:
                    print(f"[SmartDispatcher] ⚠️ 本地模型低置信度({local_conf_value:.2f})，回退规则判断")
                else:
                    context_info = context_info or {}
                    context_info["routing_list"] = cls._build_routing_list(
                        similarity_scores,
                        boosts={local_task: 0.9},
                        reasons={local_task: ["local_model"]}
                    )
                    if local_hint:
                        context_info["skill_prompt"] = local_hint
                    # 只有本地模型明确判定为 complex 时才升级；CHAT 任务不受复杂度影响（始终走 Flash）
                    if local_complexity == "complex" and local_task != "CHAT":
                        context_info["complexity"] = "complex"
                    return local_task, f"{local_confidence}", context_info

        # === 在线 AI 路由（本地模型不可用或低置信时的第二优先级） ===
        # 放在关键词规则之前，用语义理解而非关键词匹配来判断任务
        if not local_task or not local_confident:
            if client:
                print(f"[SmartDispatcher] 🌐 本地模型{'不可用' if not local_task else f'低置信({local_conf_value:.2f})'}，尝试在线 AI 路由...")
                ai_task, ai_confidence, ai_source, ai_hint = _get_ai_router().classify_with_hint(client, user_input, timeout=3.0)
                if ai_task:
                    latency = (time.time() - start_time) * 1000
                    
                    # 对 FILE_GEN/DOC_ANNOTATE 额外检查：
                    # DOC_ANNOTATE 需要文件；FILE_GEN 只需有生成意图（创建新文件）
                    if ai_task == "DOC_ANNOTATE":
                        has_file = file_context and file_context.get("has_file")
                        if not has_file:
                            print(f"[SmartDispatcher] ⚠️ AI路由返回 DOC_ANNOTATE，但无文件附件，降级 CHAT")
                            ai_task = "CHAT"
                    elif ai_task == "FILE_GEN":
                        has_file = file_context and file_context.get("has_file")
                        _ai_has_output_intent = any(w in user_lower for w in cls._AI_FILE_GEN_OUTPUT_KWS)
                        if not has_file and not _ai_has_output_intent:
                            print(f"[SmartDispatcher] ⚠️ AI路由返回 FILE_GEN，但无文件且无生成意图，降级 CHAT")
                            ai_task = "CHAT"
                    
                    context_info = context_info or {}
                    context_info["routing_list"] = cls._build_routing_list(
                        similarity_scores,
                        boosts={ai_task: 0.85},
                        reasons={ai_task: ["ai_router"]}
                    )
                    if ai_hint:
                        context_info["skill_prompt"] = ai_hint
                    print(f"[SmartDispatcher] ✅ AI路由决策: {ai_task} ({ai_confidence}) hint={'yes' if ai_hint else 'no'}")
                    return ai_task, f"{ai_confidence} ({latency:.0f}ms)", context_info

        # === 关键词兜底规则（仅在本地模型+在线模型都无法判断时触发） ===
        print(f"[SmartDispatcher] ⚠️ 模型路由均未成功，回退关键词兜底规则...")

        # -- 附件文档标注 (需要 file_context 支撑，不纯靠关键词) --
        if file_context and file_context.get("has_file"):
            _fc_ext = file_context.get("file_type", "")
            if _fc_ext in [".doc", ".docx"]:
                try:
                    if cls._should_use_annotation_system(user_input, has_file=True):
                        context_info = {"complexity": "complex"}
                        context_info["routing_list"] = cls._build_routing_list(
                            similarity_scores,
                            boosts={"DOC_ANNOTATE": 1.0},
                            reasons={"DOC_ANNOTATE": ["fallback:annotation_with_file"]}
                        )
                        return "DOC_ANNOTATE", "📄 Fallback-Annotation", context_info
                except Exception:
                    pass

        # -- PPT 直通 (需要同时有 PPT 关键词 + 动作词) --
        if any(k in user_lower for k in cls._PPT_KEYWORDS) and any(a in user_lower for a in cls._PPT_ACTION_KWS):
            context_info = {"complexity": "complex"}
            context_info["routing_list"] = cls._build_routing_list(
                similarity_scores,
                boosts={"FILE_GEN": 1.0},
                reasons={"FILE_GEN": ["fallback:ppt_direct"]}
            )
            print(f"[SmartDispatcher] 🎯 PPT 请求直通 FILE_GEN")
            return "FILE_GEN", "📄 PPT-Direct", context_info

        # -- 文档/报告生成直通 (Word/PDF/Excel 等明确输出格式 + 动作意图，不含PPT已有规则) --
        if any(k in user_lower for k in cls._DOC_GEN_OUTPUT_KWS) and any(a in user_lower for a in cls._DOC_GEN_ACTION_KWS):
            context_info = context_info or {"complexity": "complex"}
            context_info["complexity"] = "complex"
            context_info["routing_list"] = cls._build_routing_list(
                similarity_scores,
                boosts={"FILE_GEN": 1.0},
                reasons={"FILE_GEN": ["fallback:doc_gen_direct"]}
            )
            print(f"[SmartDispatcher] 📄 文档生成请求直通 FILE_GEN: '{user_input[:30]}'")
            return "FILE_GEN", "📄 DocGen-Direct", context_info

        # -- 全盘文件搜索/打开（优先于系统命令，避免"打开xxx文件"被误判为SYSTEM）--
        if any(re.search(p, user_input) for p in cls._FILE_SEARCH_PATTERNS):
            context_info = context_info or {}
            context_info["routing_list"] = cls._build_routing_list(
                similarity_scores,
                boosts={"FILE_SEARCH": 1.0},
                reasons={"FILE_SEARCH": ["rule:disk_file_search"]}
            )
            print(f"[SmartDispatcher] 🔍 文件搜索/全盘扫描直通 FILE_SEARCH")
            return "FILE_SEARCH", "🔍 FileSearch-Direct", context_info

        # -- 系统命令 --
        if LocalExecutor and LocalExecutor.is_system_command(user_input):
            context_info = context_info or {}
            context_info["routing_list"] = cls._build_routing_list(
                similarity_scores,
                boosts={"SYSTEM": 0.9},
                reasons={"SYSTEM": ["fallback:system"]}
            )
            return "SYSTEM", "🖥️ Fallback-System", context_info

        # -- 系统命令兜底：命令动词 + 短输入（不依赖 APP_ALIASES）--
        _stripped_fb = user_input.strip()
        if (
            len(_stripped_fb) <= 18
            and any(_stripped_fb.startswith(s) for s in cls._SYS_ACTION_STARTERS)
            and not any(k in user_lower for k in cls._SYS_FALLBACK_EXCLUDE)
        ):
            context_info = context_info or {}
            context_info["routing_list"] = cls._build_routing_list(
                similarity_scores,
                boosts={"SYSTEM": 0.9},
                reasons={"SYSTEM": ["fallback:action_verb"]}
            )
            print(f"[SmartDispatcher] 🖥️ 系统命令兜底: '{_stripped_fb}' → SYSTEM")
            return "SYSTEM", "🖥️ Fallback-ActionVerb", context_info

        # -- 多步任务规划 --
        _LocalPlanner = _get_local_planner()
        if _LocalPlanner.can_plan(user_input):
            plan = _LocalPlanner.plan(user_input)
            if plan and plan.get("use_planner") and plan.get("steps"):
                context_info = context_info or {}
                context_info["is_multi_step_task"] = True
                context_info["multi_step_info"] = {
                    "pattern": "local_plan",
                    "subtasks": plan.get("steps", [])
                }
                context_info["routing_list"] = cls._build_routing_list(
                    similarity_scores,
                    boosts={"MULTI_STEP": 0.95},
                    reasons={"MULTI_STEP": ["fallback:local_planner"]}
                )
                return "MULTI_STEP", "🧭 Fallback-Plan", context_info

        initial_task_hint = cls._quick_task_hint(user_input)
        compound_info = _get_task_decomposer().detect_compound_task(user_input, initial_task_hint)
        
        if compound_info["is_compound"]:
            context_info = {
                "is_multi_step_task": True,
                "multi_step_info": compound_info
            }
            context_info["routing_list"] = base_routing_list
            return "MULTI_STEP", "🔄 Fallback-MultiStep", context_info

        # -- RAG 上下文延续 --
        # -- RAG 上下文延续（单次 analyze_context，跨两个匹配检查复用）--
        _rag_ctx = None
        if history and len(history) >= 2 and ContextAnalyzer:
            _rag_ctx = ContextAnalyzer.analyze_context(user_input, history)
            if _rag_ctx.get("is_continuation"):
                # 1) WEB_SEARCH 跟进动作词（如「再搜一下」）
                if _rag_ctx.get("related_task") == "WEB_SEARCH" and any(v in user_lower for v in cls._SEARCH_VERBS):
                    _rag_ctx["routing_list"] = cls._build_routing_list(
                        similarity_scores,
                        boosts={"WEB_SEARCH": 0.9},
                        reasons={"WEB_SEARCH": ["fallback:search_followup"]}
                    )
                    return "WEB_SEARCH", "🌐 Fallback-SearchFollowup", _rag_ctx

        # -- 网页搜索检测 --
        if WebSearcher and WebSearcher.needs_web_search(user_input):
            context_info = context_info or {}
            context_info["routing_list"] = cls._build_routing_list(
                similarity_scores,
                boosts={"WEB_SEARCH": 0.9},
                reasons={"WEB_SEARCH": ["fallback:web_search"]}
            )
            return "WEB_SEARCH", "🌐 Fallback-WebSearch", context_info

        # -- RAG 历史延续（复用已计算的 _rag_ctx，避免重复调用 analyze_context）--
        if _rag_ctx and _rag_ctx.get("is_continuation") and _rag_ctx.get("confidence", 0) > 0.7:
            related_task = _rag_ctx.get("related_task")
            continuation_type = _rag_ctx.get("continuation_type", "unknown")
            if related_task:
                _rag_ctx["routing_list"] = cls._build_routing_list(
                    similarity_scores,
                    boosts={related_task: 0.88},
                    reasons={related_task: [f"fallback:rag_{continuation_type}"]}
                )
                return related_task, f"🔗 Fallback-RAG-{continuation_type}", _rag_ctx
        
        # === 最终兜底：ML 相似度 → 默认 CHAT ===
        scores = similarity_scores
        best_task = max(scores, key=scores.get)
        best_score = scores[best_task]
        latency = (time.time() - start_time) * 1000
        
        if best_score > 0.45:
            is_q = any(qw in user_lower for qw in cls._QUESTION_WORDS)
            if is_q and best_score < 0.6 and best_task != "CHAT":
                pass 
            else:
                confidence = f"🧠 ML ({best_score:.0%}, {latency:.1f}ms)"
                context_info = context_info or {}
                context_info["routing_list"] = cls._build_routing_list(
                    similarity_scores,
                    boosts={best_task: best_score},
                    reasons={best_task: ["similarity_best"]}
                )
                return best_task, confidence, context_info
        
        context_info = context_info or {}
        context_info["routing_list"] = base_routing_list
        return "CHAT", f"💬 Default ({latency:.1f}ms)", context_info
    
    @classmethod
    def get_model_for_task(cls, task_type, has_image=False, complexity="normal"):
        """根据任务类型获取最优模型。
        
        优先级（由高到低）：
        1. MODEL_MAP 中的 "<TASK>_COMPLEX" 复杂度变体键（如 FILE_GEN_COMPLEX）
        2. MODEL_MAP 中的标准任务键（如 FILE_GEN）
        3. MODEL_MAP["COMPLEX"] — 通用复杂度升级兜底
        4. MODEL_MAP["CHAT"] — 最终默认
        
        全部走 MODEL_MAP，不再内嵌硬编码模型名，方便后台动态覆盖。
        """
        MODEL_MAP = cls._get_dep("MODEL_MAP")
        if not MODEL_MAP:
            MODEL_MAP = {"CHAT": "gemini-3-flash-preview"}

        # 本地任务直通，不走模型
        if task_type in ("SYSTEM", "FILE_OP"):
            return MODEL_MAP.get(task_type, "local-executor")

        # 图片附件但任务非 PAINTER → 视觉模型
        if has_image and task_type not in ("PAINTER", "VISION"):
            return MODEL_MAP.get("VISION", MODEL_MAP.get("CHAT", "gemini-3-flash-preview"))

        # 优先检查调用方手动注入的复杂度变体键（如 FILE_GEN_COMPLEX）
        if complexity == "complex":
            complex_key = f"{task_type}_COMPLEX"
            if complex_key in MODEL_MAP:
                return MODEL_MAP[complex_key]
            # 有标准键时，非 CHAT/本地任务统一升级到 COMPLEX 兜底
            if task_type not in ("CHAT",) and "COMPLEX" in MODEL_MAP:
                return MODEL_MAP["COMPLEX"]

        # 标准任务键查询
        if task_type in MODEL_MAP:
            return MODEL_MAP[task_type]

        # 最终兜底
        return MODEL_MAP.get("CHAT", "gemini-3-flash-preview")

    # ── LangGraph 工作流集成 ────────────────────────────────────────────────
    @classmethod
    def resolve_workflow(cls, task_type: str, user_input: str) -> str:
        """
        根据 dispatch() 返回的 task_type 决定是否使用 LangGraph 多步工作流。

        返回值:
            "langgraph_react"          → 使用 LangGraphAgent（单 Agent ReAct）
            "langgraph_research_doc"   → 使用 WorkflowEngine: research_and_document
            "langgraph_multi_agent_ppt"→ 使用 WorkflowEngine: multi_agent_ppt
            "legacy"                   → 保持原有 UnifiedAgent 处理路径

        集成方式（在 web/app.py 或对应处理函数中）:
            task_type, conf, ctx = SmartDispatcher.dispatch(user_input, ...)
            wf = SmartDispatcher.resolve_workflow(task_type, user_input)
            if wf.startswith("langgraph_"):
                # 使用 LangGraph 路径
                ...
        """
        try:
            from app.core.workflow.langgraph_workflow import WorkflowEngine
            detected = WorkflowEngine.detect_workflow(task_type, user_input)
            if detected == "multi_agent_ppt":
                return "langgraph_multi_agent_ppt"
            elif detected == "research_and_document":
                return "langgraph_research_doc"
            elif task_type == "MULTI_STEP":
                # 通用多步任务 → LangGraphAgent ReAct
                return "langgraph_react"
            else:
                return "legacy"
        except ImportError:
            # langgraph 未安装 → 回退到原有路径
            return "legacy"

