import json
import logging
import socket
import time
from dataclasses import dataclass, field
from typing import List, Optional

import requests

logger = logging.getLogger(__name__)

# ══════════════════════════════════════════════════════════════════
# RouterDecision — 结构化路由决策（v2）
# ══════════════════════════════════════════════════════════════════


@dataclass
class RouterDecision:
    """
    本地 Router 的结构化决策结果。

    替代旧版 (task_type: str, confidence: str, source: str) 三元组，
    承载更丰富的路由信息，供 UnifiedAgent 和 Skill 系统使用。

    字段说明
    ────────
    task_type       : 任务分类（CHAT / CODER / PAINTER / ...）
    skill_id        : 若本地模型识别出对应已注册 Skill，填入 Skill ID；否则 None
    forward_to_cloud: 是否需要转发给云端（True=云端，False=本地直接处理）
    confidence      : 分类置信度 (0.0-1.0)
    hint            : 对云端执行模型的格式提示（可选）
    source          : 决策来源 ("Local" / "Cache" / "Fallback")
    latency_ms      : 本地路由耗时（毫秒）
    params          : 额外路由参数（如目标 Skill 的变量预填值）
    """

    task_type: str = "CHAT"
    skill_id: Optional[str] = None
    forward_to_cloud: bool = True
    confidence: float = 0.0
    hint: Optional[str] = None
    source: str = "Local"
    latency_ms: int = 0
    params: dict = field(default_factory=dict)

    @property
    def confidence_str(self) -> str:
        """向后兼容旧版 (task, conf_str, source) 三元组中的 conf_str 字段"""
        return f"{self.source} {self.confidence:.2f} ({self.latency_ms}ms)"

    def to_legacy_tuple(self):
        """向后兼容：转换为旧版 (task_type, confidence_str, source) 三元组"""
        return self.task_type, self.confidence_str, self.source


class LocalModelRouter:
    """
    使用本地 Ollama 模型进行任务分类（可选功能）

    - 如果安装了 Ollama + Qwen，使用本地模型（更快、更准）
    - 如果没有安装，自动降级到 SmartDispatcher（纯规则+语料匹配）
    - 对用户透明，不影响正常使用
    """

    _initialized = False
    _model_name = None
    _available = None  # 缓存可用性状态
    _check_time = 0  # 上次检查时间

    # 推荐的快速模型（按优先级排序）
    OLLAMA_MODELS = [
        "koto-router",  # ★★ Koto 专用路由器（基于 qwen3:8b 微调，针对 Koto 任务分类）
        "qwen3:8b",  # ★ 最佳中英文能力，RTX 4090 流畅运行
        "qwen3:4b",  # 快速备选
        "qwen3:1.7b",  # 轻量备选
        "qwen2.5:7b",  # 旧版但质量好
        "qwen2.5:3b",  # 旧版快速
        "qwen2.5:1.5b",  # 旧版轻量
        "llama3.2:3b",  # 英文为主
    ]
    
    # 分类 Prompt（固定 JSON 格式，确保输出一致）
    # Qwen3 支持 /no_think 模式，跳过思考直接输出，加速分类
    CLASSIFY_PROMPT = """/no_think
你是任务分类器。严格只输出 JSON，不输出任何其他内容。

━━━ 任务类型定义 ━━━
- PAINTER    : 生成/绘制/创作图片、封面、壁纸、头像、示意图（侧重"创作视觉内容"）
- FILE_GEN   : 生成完整可保存文件（Word/PDF/Excel/PPT/报告/合同/日报模板等）
- DOC_ANNOTATE: 对用户提供的已有文本/代码进行修改、润色、标注、批注、校对、优化
- RESEARCH   : 对某主题做系统性深入研究，输出长篇结构化内容（须含"深入/全面/系统"等信号词）
- CODER      : 编写/调试/重构代码、脚本、算法（侧重"产出可运行代码"），或制作数据图表/可视化（折线图/柱状图/饼图/散点图/图表/plot/chart/matplotlib等）
- SYSTEM     : 命令执行操作系统级操作（打开/关闭应用、文件管理、系统设置、截图、关机）
- AGENT      : 命令执行跨应用工具操作（发送消息/邮件、设提醒/闹钟、日历、浏览器自动化）
- WEB_SEARCH : 需要实时/当前信息（今日天气、即时股价/汇率、最新新闻、当前价格/排名）
- CHAT       : 知识问答、概念解释、日常对话、建议咨询、历史信息、通用翻译、短文本创作

━━━ 分类优先级（多条规则同时符合时，序号小的优先）━━━
1. PAINTER      ← 含"画/绘/生成图/创作图/图片/壁纸/头像"等视觉创作词（注意："图表/折线图/柱状图/可视化"不在此列）
2. FILE_GEN     ← 明确要生成+保存为文件格式（Word/PPT/Excel/PDF）
3. DOC_ANNOTATE ← 有"这段/这篇/以下/下面的内容/代码"等**已有内容**的改写/润色请求；或 [FILE_ATTACHED] + 标注/批注/修改动作
4. CODER        ← 要求输出可运行代码（写代码/写脚本/实现功能/帮我写一个函数），或调试/检查代码bug（这段代码有bug/帮我debug），或制作数据图表/可视化（作图/图表/折线图/柱状图/饼图/散点图）
5. SYSTEM       ← 命令语气 + 操作本机系统/进程/环境（打开应用、关机、调音量、截图、修改设置、运行程序）
6. AGENT        ← 命令语气 + 跨应用工具/服务（消息/提醒/日历/浏览器），或要求执行自动化工作流/多步骤任务（帮我自动完成X/按流程执行X/多步骤任务）
7. WEB_SEARCH   ← 明确需要实时变化的数据（今天/现在/最新/当前）
8. RESEARCH     ← 带"深入/系统/全面+具体主题"的研究请求
9. CHAT         ← 默认分类（问问题、学知识、闲聊、短文本）

━━━ 关键区分规则 ━━━

【CHAT vs CODER】
- "怎么写/如何实现/什么是/能解释一下" → CHAT（求知识，不要求产出代码）
- "帮我写/给我写/写一个函数/实现X功能/生成一段代码" → CODER（要求产出代码）
- "如何写一个排序算法" → CHAT  |  "写一个排序算法" → CODER

【DOC_ANNOTATE vs CODER】
- 用户提供了已有文本/文档，要求润色/修改/批注/校对/改写 → DOC_ANNOTATE
- 用户要求从零新写/实现代码/脚本 → CODER
- 调试/检查/debug 代码（这段代码有什么bug/帮我debug/代码报错了/找出错误）→ CODER
- "帮我优化这段文字的措辞" → DOC_ANNOTATE  |  "帮我写一个功能" → CODER
- "这段代码有什么bug？" → CODER（调试=CODER，不是DOC_ANNOTATE）
- "[FILE_ATTACHED:.docx] 润色这篇论文" → DOC_ANNOTATE

【SYSTEM vs AGENT】
- SYSTEM: 操作本机系统（打开微信、关机、调音量、截图、打开文件夹、运行程序）
- AGENT: 控制应用发送内容/调用外部服务（给X发消息、发邮件、设提醒、操作浏览器）
- AGENT: 执行自动化工作流/多步骤任务（帮我自动完成这个工作流/按流程执行/帮我完成多步骤任务）
- "打开微信" → SYSTEM  |  "给微信好友发消息" → AGENT
- "帮我自动完成这个工作流" → AGENT  |  "帮我规划并执行这个多步骤任务" → AGENT

【FILE_SEARCH vs SYSTEM vs CODER】
- FILE_SEARCH: 搜索/查找/定位文件（在磁盘/目录/文件夹中找文件，不管文件类型）
- SYSTEM: 操作系统执行（运行、打开、安装、删除、配置）
- FILE_SEARCH 信号：找/搜索/查找/定位 + 文件名/目录/路径/有没有/存不存在
- "帮我找一下桌面上的文件" → FILE_SEARCH（搜索桌面文件，不是操作系统）
- "在我的文档里搜索合同" → FILE_SEARCH（文档目录下找文件）
- "项目目录里有没有config.yaml" → FILE_SEARCH（查询文件是否存在）
- "找到所有包含TODO的代码文件" → FILE_SEARCH（搜索文件内容，不是写代码）
- "列出当前运行的进程" → SYSTEM（操作系统命令）

【CODER vs AGENT】
- "用Python写一个脚本来爬取数据" → CODER（要求代码输出）
- "帮我自动登录某网站/自动发帖/自动填表" → AGENT（要求执行操作）

【PAINTER vs CODER（图表/可视化）】
- "帮我画一张艺术图/生成一张封面/AI绘画": → PAINTER（视觉创作）
- "帮我作图/做一个折线图/柱状图/饼图/散点图/数据可视化/chart/plot/matplotlib" → CODER（编码产出图表）

【RESEARCH vs CHAT】
- RESEARCH 触发条件（满足其一即可）：
  ① 含"深入/全面/系统/详尽/完整"等强度副词 + 具体主题
  ② 含分析动词"分析/梳理/总结/评估/综述" + 具体主题（如竞争格局/技术方法/优缺点/发展历程）
- 仅"了解一下/告诉我/研究一下/介绍下/是什么/什么是" → CHAT（日常口语，不是深度研究）
- "帮我深入研究X的技术原理" → RESEARCH
- "分析特斯拉和比亚迪的竞争格局" → RESEARCH
- "梳理大模型的主流训练方法" → RESEARCH
- "总结联邦学习的优缺点" → RESEARCH
- "介绍一下量子计算" → CHAT  |  "研究一下X" → CHAT（口语"研究一下"≠深度分析）

【WEB_SEARCH vs CHAT】
- 历史/固定知识（定义、原理、历史事件） → CHAT
- 实时变化或时效敏感的信息 → WEB_SEARCH
- 时效性信号词（任一出现即强烈提示 WEB_SEARCH）：
  今天/今日/现在/当前/目前/近期/最近/近况/近来/如今/当下/眼下/此刻/
  最新/进展/动态/局势/战况/现状/行情/走势 等
- "二战是哪年开始的" → CHAT  |  "目前伊朗战事如何" → WEB_SEARCH
- 关键规则：话题词前/后带时效词（目前X如何 / X近况 / X最新进展 / X局势动态）→ WEB_SEARCH
- 例外：若句子同时含分析动词（分析/梳理/总结/评估/研究）+ 行业/技术主题词 → 优先 RESEARCH
  - "目前伊朗局势" → WEB_SEARCH（无分析动词，纯时事询问）
  - "目前主流Agent企业的技术路径是什么" → RESEARCH（技术路径分析，非时事）
  - "近期AI技术的发展" → WEB_SEARCH  |  "分析目前AI芯片竞争格局" → RESEARCH

【金融/商品价格 → 强制 WEB_SEARCH】
价格本质上是实时数据，以下类别名词 + "价格/价/多少/怎样/走势/行情/现价/报价/涨跌" 无论是否带时效词 → WEB_SEARCH：
- 大宗商品：原油（布伦特/WTI）、天然气、黄金、白银、铜、铁矿石、大豆、小麦、棉花
- 股票/指数：A股、港股、纳斯达克、道琼斯、标普500、某股票/ETF
- 加密货币：比特币、以太坊、BTC、ETH
- 汇率：美元/人民币、欧元/美元、外汇
- 其他：期货、基金净值、债券收益率
注意："X是什么/X的历史/X的生产原理" → CHAT；带价格/现价/行情/涨跌/走势 → WEB_SEARCH
- "布伦特原油价格" → WEB_SEARCH（价格=实时数据，即使无时效词）
- "黄金现在多少钱" → WEB_SEARCH
- "比特币今天涨了吗" → WEB_SEARCH
- "原油是什么" → CHAT（概念问答，无价格词）
- "布伦特原油和WTI的区别" → CHAT（知识对比，无价格词）

【FILE_GEN vs CHAT】
- "写一段/写一篇/帮我写个/给我写个" = 短文本输出 → CHAT
- "生成一份.../做一个PPT/写一份Word文档" = 需要文件 → FILE_GEN
- "写一段自我介绍" → CHAT  |  "帮我制作一份Word版简历" → FILE_GEN
- 关键信号：含 word/docx/pdf/ppt/excel/报告 等文件格式词 + "做一个/做一份/写一份/生成" 等动作词 → FILE_GEN
- "介绍一下X" / "帮我讲讲X" → CHAT（无文件格式词）  
- "做一个关于X的word介绍" / "写一份X的PDF报告" → FILE_GEN（有格式词+动作词）
- 注意：含"表格/数据汇总"的生成请求 → FILE_GEN（不是CODER也不是PAINTER）
  - "生成一张表格汇总这些数据" → FILE_GEN  |  "帮我把数据整理成表格" → FILE_GEN

【附件分析 vs DOC_ANNOTATE vs FILE_GEN（输入含 [FILE_ATTACHED:xxx] 标记 = 用户上传了文件）】
- [FILE_ATTACHED] + 提问/分析/告诉我/帮我看/检查/评估/是否/有没有/怎么/什么意思 → CHAT（读文件作答）
- [FILE_ATTACHED:.docx/.doc] + 标注/批注/润色/修改/改善/校对/标记/标出/改正/纠正/优化翻译/语序 → DOC_ANNOTATE（在上传文件上直接标注修改）
- [FILE_ATTACHED] + 明确含"深入/全面/系统/详尽"等词 + 具体研究主题 → RESEARCH
- [FILE_ATTACHED] + 明确要生成新文件（含格式词 word/ppt/pdf/excel + 生成动作词） → FILE_GEN
- 关键原则：用户传文件是为了让你"读"它，不是让你"生成"它——默认走 CHAT，除非明确说要生成新文件或编辑原文
- "[FILE_ATTACHED:.pdf] 告诉我这份文件想做什么，是否有投资价值" → CHAT
- "[FILE_ATTACHED:.pdf] 分析一下这份合同有哪些风险" → CHAT
- "[FILE_ATTACHED:.docx] 把所有不合适的翻译标注改善" → DOC_ANNOTATE
- "[FILE_ATTACHED:.docx] 润色这篇论文" → DOC_ANNOTATE
- "[FILE_ATTACHED:.pdf] 帮我把内容整理成一份Word汇报" → FILE_GEN
- "[FILE_ATTACHED:.docx] 深入研究这家公司的财务状况" → RESEARCH

━━━ 正例 ━━━
输入: 打开微信
输出: {{"task":"SYSTEM","confidence":0.95}}
输入: 画一只猫
输出: {{"task":"PAINTER","confidence":0.92}}
输入: 写一个快速排序函数
输出: {{"task":"CODER","confidence":0.93}}
输入: 查下明天北京天气
输出: {{"task":"WEB_SEARCH","confidence":0.90}}
输入: 帮我做一个PPT
输出: {{"task":"FILE_GEN","confidence":0.88}}
输入: 标注这篇文档的不当之处
输出: {{"task":"DOC_ANNOTATE","confidence":0.88}}
输入: 给张三发微信说明天开会
输出: {{"task":"AGENT","confidence":0.90}}
输入: 帮我深入研究MicroLED的技术原理和发展历程
输出: {{"task":"RESEARCH","confidence":0.90}}
输入: 设置明天早上8点提醒我开会
输出: {{"task":"AGENT","confidence":0.92}}
输入: 用Python实现一个文件批量重命名工具
输出: {{"task":"CODER","confidence":0.93}}
输入: 帮我把这段代码优化一下
输出: {{"task":"DOC_ANNOTATE","confidence":0.87}}
输入: 帮我写一份工作总结Word文档
输出: {{"task":"FILE_GEN","confidence":0.91}}
输入: 做一个关于1月新番导视的word介绍
输出: {{"task":"FILE_GEN","confidence":0.90}}
（含"word"文件格式词+"做一个"生成动作→FILE_GEN，不是普通聊天介绍）
输入: 帮我做一份竞品分析报告
输出: {{"task":"FILE_GEN","confidence":0.89}}
（"报告"是文件格式词，"做一份"是生成动作→FILE_GEN）
输入: 写一个关于春节习俗的PDF介绍
输出: {{"task":"FILE_GEN","confidence":0.90}}
（含"PDF"文件格式词+"写一个"生成动作→FILE_GEN）
输入: 今天A股涨了吗
输出: {{"task":"WEB_SEARCH","confidence":0.92}}
输入: 布伦特原油价格
输出: {{"task":"WEB_SEARCH","confidence":0.95}}
（价格=实时数据，即使无时效词也是 WEB_SEARCH）
输入: 黄金现在多少钱
输出: {{"task":"WEB_SEARCH","confidence":0.95}}
输入: 比特币今天涨了吗
输出: {{"task":"WEB_SEARCH","confidence":0.94}}
输入: 美元汇率
输出: {{"task":"WEB_SEARCH","confidence":0.93}}
输入: [FILE_ATTACHED:.pdf] 告诉我这份文件想做什么，是否有投资价值
输出: {{"task":"CHAT","confidence":0.92}}
（文件已附上，用户要内容解读，不是生成新文件 → CHAT）
输入: [FILE_ATTACHED:.pdf] 这份商业计划书值得投资吗
输出: {{"task":"CHAT","confidence":0.91}}
（问"值不值得"=内容分析问答 → CHAT）
输入: [FILE_ATTACHED:.pdf] 帮我把这份材料做成一份PPT
输出: {{"task":"FILE_GEN","confidence":0.90}}
（用户明确要生成 PPT 格式文件 → FILE_GEN）
输入: [FILE_ATTACHED:.docx] 深入分析这家公司的财务状况和经营风险
输出: {{"task":"RESEARCH","confidence":0.87}}
（有"深入分析"信号词 + 具体研究主题 → RESEARCH）
输入: [FILE_ATTACHED:.pdf] 帮我看看这里有没有法律风险
输出: {{"task":"CHAT","confidence":0.90}}
（"帮我看看/有没有"=内容问答 → CHAT，不是生成文件）
输入: [FILE_ATTACHED:.docx] 把所有不合适的翻译 不符合中文语序逻辑 生硬的地方标注改善
输出: {{"task":"DOC_ANNOTATE","confidence":0.93}}
（上传.docx + "标注改善"=在原文上标记修改 → DOC_ANNOTATE）
输入: [FILE_ATTACHED:.docx] 润色一下这篇论文
输出: {{"task":"DOC_ANNOTATE","confidence":0.91}}
（上传.docx + "润色"=对已有文档修改标注 → DOC_ANNOTATE）

━━━ 反例（容易误判，务必注意）━━━
输入: 在Windows环境里快速启动bash虚拟环境，一般用什么办法
输出: {{"task":"CHAT","confidence":0.92}}
（虽含"启动"但这是知识提问，不是命令执行）
输入: python怎么安装第三方库
输出: {{"task":"CHAT","confidence":0.88}}
（问"怎么"=求知识，不是让你写代码）
输入: 什么是docker
输出: {{"task":"CHAT","confidence":0.90}}
输入: 研究一下这个问题
输出: {{"task":"CHAT","confidence":0.80}}
（日常"研究一下"≠深度研究，无信号词）
输入: 写一段自我介绍
输出: {{"task":"CHAT","confidence":0.85}}
（短文本输出，不是生成文件，不含代码）
输入: 介绍一下量子计算
输出: {{"task":"CHAT","confidence":0.88}}
（纯问答，无文件格式词，不是生成文件）
输入: 搜索怎么用git
输出: {{"task":"CHAT","confidence":0.82}}
（要知识/教程，不是实时信息）
输入: 如何写一个排序算法
输出: {{"task":"CHAT","confidence":0.88}}
（问"如何写"=求知识，不是要你输出代码）
输入: 二战是哪年开始的
输出: {{"task":"CHAT","confidence":0.95}}
（固定历史知识，不需要实时数据）
输入: 调一下这段代码的bug
输出: {{"task":"DOC_ANNOTATE","confidence":0.87}}
（对已有代码修改，不是新建代码）
输入: 原油是什么
输出: {{"task":"CHAT","confidence":0.92}}
（概念问答，无价格/行情词 → CHAT，不是 WEB_SEARCH）
输入: 布伦特原油和WTI原油有什么区别
输出: {{"task":"CHAT","confidence":0.90}}
（知识对比，不涉及实时价格 → CHAT）
输入: 帮我自动在淘宝上搜索商品并截图
输出: {{"task":"AGENT","confidence":0.88}}
（执行浏览器自动化操作，不是写代码）
输入: [FILE_ATTACHED:.pdf] 告诉我这份文件说的是什么
输出: {{"task":"CHAT","confidence":0.91}}
（用户传文件是为了让你读它回答问题，不要误判成 FILE_GEN 生成新文档）
输入: [FILE_ATTACHED:.pdf] 分析这份合同
输出: {{"task":"CHAT","confidence":0.89,"complexity":"normal"}}
（"分析+文件"=读内容作答 → CHAT，没有生成新文件意图）

━━━ complexity 复杂度评估 ━━━
❗ 默认填 "normal"，只有明确满足以下条件之一才填 "complex"：
  ① 需要产出 超大体量 内容（≥10个章节/≥20页PPT/完整大型项目架构）
  ② 需要 跨领域交叉综合分析 且用户明确要求深度（如：全面对比X与Y并给出专业投资策略）
  ③ 代码任务涉及 系统级架构设计（微服务/高并发/多模块框架，不含单函数/小脚本）
  ④ 需要多轮迭代推理才能完成的专项研究报告（含竞争格局分析/学术综述/技术白皮书）

✅ 以下情况 必须填 "normal"（覆盖 90%+ 的日常请求）：
  - 任何产品使用方法/操作说明/功能介绍（无论品牌/型号）
  - 知识问答/概念解释/翻译/摘要/日常建议
  - 文件搜索/整理/归纳/列举（无论文件数量或路径复杂度）
  - 标准代码实现（单函数/小脚本/算法题/调试）
  - 常规文档生成（简历/日报/周报/标准PPT模板/普通报告）
  - 网页搜索/实时信息查询
  - 系统命令/文件打开/应用操作
  - 所有 CHAT 分类（CHAT 强制 = normal，无例外）
  - DOC_ANNOTATE 标准润色/标注（非超大文档的深度重写）

正例：
输入: 韶音游泳耳机怎么使用          → complexity: "normal"（产品说明，CHAT）
输入: 归纳桌面上所有word文件        → complexity: "normal"（文件整理，FILE_SEARCH）
输入: 写一个冒泡排序                → complexity: "normal"（基础代码）
输入: 查一下今天天气                → complexity: "normal"（实时查询）
输入: 帮我做一份PPT                 → complexity: "normal"（标准PPT，FILE_GEN）
输入: 深入分析特斯拉和比亚迪技术路线并给出投资建议 → complexity: "complex"（跨维度+策略建议）
输入: 设计一个支持千万并发的微服务电商系统架构     → complexity: "complex"（系统级架构）
输入: 写一份50页的量子计算行业白皮书               → complexity: "complex"（超大体量）

只输出 JSON：
{{"task":"...","confidence":0.0-1.0,"complexity":"normal"|"complex"}}
"""

    @classmethod
    def is_ollama_available(cls) -> bool:
        """检查 Ollama 是否可用（带缓存，避免频繁检测）"""
        import os

        # 云端模式下禁用 Ollama（云服务器没有本地 GPU）
        if os.environ.get("KOTO_DEPLOY_MODE") == "cloud":
            cls._available = False
            return False

        # 缓存 30 秒
        if cls._available is not None and (time.time() - cls._check_time) < 30:
            return cls._available

        try:
            sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            sock.settimeout(0.1)
            result = sock.connect_ex(("127.0.0.1", 11434))
            sock.close()
            cls._available = result == 0
            cls._check_time = time.time()
            return cls._available
        except:
            cls._available = False
            cls._check_time = time.time()
            return False

    @classmethod
    def init_model(cls, model_name: str = None) -> bool:
        """初始化本地模型（静默失败，不影响使用）"""
        if cls._initialized and cls._model_name:
            return True
        
        if not cls.is_ollama_available():
            # 静默返回，不打印错误（避免刷屏）
            return False
        
        # 获取已安装的模型
        try:
            resp = requests.get("http://localhost:11434/api/tags", timeout=2)
            if resp.status_code != 200:
                return False
            installed = [m['name'].split(':')[0] + ':' + m['name'].split(':')[1] if ':' in m['name'] else m['name'] 
                        for m in resp.json().get('models', [])]
        except:
            return False
        
        if not installed:
            return False
        
        # 选择可用的最快模型
        target_model = model_name
        if not target_model:
            for m in cls.OLLAMA_MODELS:
                base_name = m.split(':')[0]
                if any(base_name in im for im in installed):
                    for im in installed:
                        if base_name in im:
                            target_model = im
                            break
                    break
        
        if not target_model:
            return False
        
        cls._model_name = target_model
        cls._initialized = True
        print(f"[LocalModelRouter] ✅ 使用本地模型: {target_model}")
        return True

    # ══════════════════════════════════════════════════════════════════
    # 共享 Ollama 调用工具 — 消除 intent_analyzer / local_planner 中的重复 HTTP 代码
    # ══════════════════════════════════════════════════════════════════

    @classmethod
    def call_ollama_chat(
        cls,
        messages: list,
        model_name: str = None,
        fmt: str = None,
        options: dict = None,
        timeout: float = 10.0,
        strip_think: bool = True,
    ) -> tuple:
        """
        向 Ollama API 发送 chat 请求并返回文本内容。

        Args:
            messages:   Ollama 格式的消息列表 [{"role": ..., "content": ...}]。
            model_name: 模型名称；None 时使用已初始化的 ``_model_name``。
            fmt:        Ollama ``format`` 字段（如 ``"json"``）；None 表示不限制。
            options:    Ollama ``options`` 字典（temperature / num_predict 等）。
            timeout:    HTTP 超时秒数。
            strip_think: 是否剥离 Qwen3 ``<think>…</think>`` 思考块。

        Returns:
            ``(content: str, error: str | None)``：
              - 成功时 error 为 None；
              - 失败时 content 为空字符串，error 包含描述。
        """
        _model = model_name or cls._model_name
        if not _model:
            return "", "❌ 无可用模型（请先调用 init_model()）"

        payload = {
            "model": _model,
            "messages": messages,
            "stream": False,
        }
        if fmt:
            payload["format"] = fmt
            payload["think"] = False  # Qwen3: json 模式下关闭思考
        if options:
            payload["options"] = options

        try:
            resp = requests.post(
                "http://localhost:11434/api/chat",
                json=payload,
                timeout=timeout,
            )
            if resp.status_code != 200:
                return "", f"❌ HTTP {resp.status_code}: {resp.text[:120]}"

            data = resp.json()
            content = (data.get("message", {}) or {}).get("content", "")
            if not content:
                content = data.get("response", "")
            content = (content or "").strip()

            if strip_think and content:
                import re as _re

                content = _re.sub(r"<think>[\s\S]*?</think>", "", content).strip()

            return content, None

        except requests.exceptions.Timeout:
            return "", f"⏱️ 超时 ({timeout}s)"
        except Exception as exc:
            return "", f"❌ 请求失败: {exc}"

    @classmethod
    def classify(cls, user_input: str, timeout: float = 4.0) -> tuple:
        """
        使用本地 Ollama 模型分类任务

        返回: (task_type, confidence_str, source) 或 (None, reason, source)
        """
        start = time.time()

        # 确保模型可用
        if not cls._initialized:
            if not cls.init_model():
                return None, "❌ ModelNotReady", "Local"

        prompt = cls.CLASSIFY_PROMPT

        try:
            resp = requests.post(
                "http://localhost:11434/api/chat",
                json={
                    "model": cls._model_name,
                    "messages": [
                        {"role": "system", "content": prompt},
                        {"role": "user", "content": user_input[:500]},
                    ],
                    "stream": False,
                    "format": "json",
                    "think": False,  # Qwen3: 禁用思考模式，加速分类
                    "options": {
                        "temperature": 0.0,
                        "num_predict": 80,
                    },
                },
                timeout=timeout,
            )

            latency = (time.time() - start) * 1000

            if resp.status_code != 200:
                return None, f"❌ API Error {resp.status_code}", "Local"

            data_json = resp.json()
            raw = (data_json.get("message", {}) or {}).get("content", "")
            if not raw:
                raw = data_json.get("response", "")
            raw = (raw or "").strip()
            # 剥离 Qwen3 / koto-router 的 <think>...</think> 思考块
            import re as _re_think

            raw = _re_think.sub(r"<think>[\s\S]*?</think>", "", raw).strip()
            valid_tasks = [
                "PAINTER",
                "FILE_GEN",
                "DOC_ANNOTATE",
                "RESEARCH",
                "CODER",
                "SYSTEM",
                "AGENT",
                "WEB_SEARCH",
                "CHAT",
                "FILE_SEARCH",
            ]

            # 解析 JSON 输出
            task_type = None
            confidence = 0.0
            # 任务别名映射（兼容模型可能输出的英文别名）
            _TASK_ALIASES = {
                "DRAW": "PAINTER",
                "IMAGE": "PAINTER",
                "ART": "PAINTER",
                "GENERATE_IMAGE": "PAINTER",
                "SEARCH": "WEB_SEARCH",
                "WEB": "WEB_SEARCH",
                "CODE": "CODER",
                "CODING": "CODER",
                "PROGRAM": "CODER",
                "FILE": "FILE_GEN",
                "GENERATE": "FILE_GEN",
                "ANNOTATE": "DOC_ANNOTATE",
                "EDIT_DOC": "DOC_ANNOTATE",
                "FIND_FILE": "FILE_SEARCH",
                "FIND": "FILE_SEARCH",
                "EXECUTE": "SYSTEM",
                "OS": "SYSTEM",
                "AUTO": "AGENT",
                "AUTOMATION": "AGENT",
                "DEEP_RESEARCH": "RESEARCH",
                "ANALYZE": "RESEARCH",
            }
            try:
                import json as _json

                data = _json.loads(raw)
                task_type = str(data.get("task", "")).strip().upper()
                confidence = float(data.get("confidence", 0.0))
                # 应用别名映射
                task_type = _TASK_ALIASES.get(task_type, task_type)
            except Exception:
                # 回退：尝试从纯文本中提取
                raw_upper = raw.upper()
                for t in valid_tasks:
                    if t in raw_upper:
                        task_type = t
                        confidence = 0.5
                        break
                if not task_type:
                    # 尝试别名匹配
                    for alias, canonical in _TASK_ALIASES.items():
                        if alias in raw_upper:
                            task_type = canonical
                            confidence = 0.5
                            break

            if (
                task_type in valid_tasks
                and "|" not in task_type
                and 0.0 <= confidence <= 1.0
                and confidence >= 0.45
            ):
                conf_str = f"🤖 Local {confidence:.2f} ({latency:.0f}ms)"
                logger.info(f"[LocalModelRouter] {task_type} {conf_str}")
                # ── 自动记录到训练数据库（后台，不阻塞）──────────────────────
                try:
                    from app.core.learning.training_db import auto_record_interaction

                    auto_record_interaction(user_input, task_type, confidence)
                except Exception:
                    pass
                return task_type, conf_str, "Local"
            else:
                logger.info(f"[LocalModelRouter] 无法解析结果: {raw[:80]}")
                return None, f"⚠️ ParseError", "Local"

        except requests.exceptions.Timeout:
            return None, f"⏱️ Timeout ({timeout}s)", "Local"
        except Exception as e:
            logger.error(f"[LocalModelRouter] 错误: {e}")
            return None, f"❌ Error", "Local"

    @classmethod
    def classify_with_hint(cls, user_input: str, timeout: float = 5.0) -> tuple:
        """
        分类任务类型，同时为执行模型生成 skill_prompt（响应格式提示）。
        用于实现「本地模型理解意图 → 生成执行指令 → 云端模型按指令执行」的流程。

        返回: (task_type, confidence_str, source, hint_or_None)
          - hint: 对云端模型的执行提示，如 "用表格显示班次：车次|出发|到达|历时|价格"
          - hint=None 表示使用各任务默认的 system_instruction
        """
        start = time.time()

        if not cls._initialized:
            if not cls.init_model():
                return (
                    None,
                    "❌ ModelNotReady",
                    "Local",
                    None,
                    "normal",
                )  # 5-tuple，匹配 SmartDispatcher 解包

        # 在原有分类 prompt 基础上追加 hint 生成指令
        hint_addon = """\n━━━ hint 字段━━━
用≤30字中文描述用户期望的响应格式（全任务类型均可填写（无特殊要求则填 null））。

示例：
  WEB_SEARCH 查火车/高鐵 → "用表格显示班次：车次|出发站|到达站|发车时间|到达|历时|票价"
  WEB_SEARCH 查天气         → "输出气温、天气状况、未来3天预报、出行建议"
  WEB_SEARCH 查股价/金价  → "输出当前价格、今日涨跌幅、简短走势"
  WEB_SEARCH 查新闻/信息  → "列出3-5条关键要点"
  WEB_SEARCH 查航班          → "用表格显示航班：航班号|出发|到达|起飞|落地|历时"
  RESEARCH 技术分析       → "从技术原理、优缺点、应用场景三个维度分析"
  CODER 写生成器         → "需要模块化设计和完整错误处理"
  CODER 写算法           → "包含复杂度分析和示例测试用例"
  CHAT 写文案            → "输出多个风格备选方案"
  CHAT 解释概念          → "先下定义再举例，200字内"
  CHAT 分析文件内容     → "逐点回应用户提问，引用文件关键原文支撑结论"
  RESEARCH 深度分析文件 → "分"核心信息/风险点/结论建议"三层展开"
  FILE_GEN 生成广告文案    → "输出标题+正文+口号三段结构"

只输出 JSON（含 hint、complexity 字段）：
{{"task":"...","confidence":0.0-1.0,"hint":"..."或null,"complexity":"normal"|"complex"}}"""

        extended_prompt = cls.CLASSIFY_PROMPT.replace(
            '只输出 JSON：\n{{"task":"...","confidence":0.0-1.0}}', hint_addon
        )
        # 安全检查：若替换未生效（格式差异），则直接追加
        if extended_prompt == cls.CLASSIFY_PROMPT:
            extended_prompt = cls.CLASSIFY_PROMPT.rstrip() + "\n" + hint_addon

        try:
            resp = requests.post(
                "http://localhost:11434/api/chat",
                json={
                    "model": cls._model_name,
                    "messages": [
                        {"role": "system", "content": extended_prompt},
                        {"role": "user", "content": user_input[:500]},
                    ],
                    "stream": False,
                    "format": "json",
                    "think": False,
                    "options": {
                        "temperature": 0.0,
                        "num_predict": 200,  # 比 classify() 多，允许输出 hint
                    },
                },
                timeout=timeout,
            )

            latency = (time.time() - start) * 1000

            if resp.status_code != 200:
                # 降级到无 hint 的分类
                task, conf, src = cls.classify(user_input, timeout=timeout)
                return task, conf, src, None, "normal"

            data_json = resp.json()
            raw = (data_json.get("message", {}) or {}).get("content", "")
            if not raw:
                raw = data_json.get("response", "")
            raw = (raw or "").strip()
            # 剥离 Qwen3 / koto-router 的 <think>...</think> 思考块
            import re as _re_think2

            raw = _re_think2.sub(r"<think>[\s\S]*?</think>", "", raw).strip()

            valid_tasks = [
                "PAINTER",
                "FILE_GEN",
                "DOC_ANNOTATE",
                "RESEARCH",
                "CODER",
                "SYSTEM",
                "AGENT",
                "WEB_SEARCH",
                "CHAT",
                "FILE_SEARCH",
            ]
            _TASK_ALIASES2 = {
                "DRAW": "PAINTER",
                "IMAGE": "PAINTER",
                "ART": "PAINTER",
                "SEARCH": "WEB_SEARCH",
                "WEB": "WEB_SEARCH",
                "CODE": "CODER",
                "CODING": "CODER",
                "PROGRAM": "CODER",
                "FILE": "FILE_GEN",
                "GENERATE": "FILE_GEN",
                "ANNOTATE": "DOC_ANNOTATE",
                "EDIT_DOC": "DOC_ANNOTATE",
                "FIND_FILE": "FILE_SEARCH",
                "FIND": "FILE_SEARCH",
                "EXECUTE": "SYSTEM",
                "OS": "SYSTEM",
                "AUTO": "AGENT",
                "AUTOMATION": "AGENT",
                "DEEP_RESEARCH": "RESEARCH",
                "ANALYZE": "RESEARCH",
            }

            task_type = None
            confidence = 0.0
            hint = None
            complexity = "normal"
            try:
                import json as _json

                data = _json.loads(raw)
                task_type = str(data.get("task", "")).strip().upper()
                task_type = _TASK_ALIASES2.get(task_type, task_type)
                confidence = float(data.get("confidence", 0.0))
                raw_hint = data.get("hint") or data.get("instruction") or None
                if raw_hint and isinstance(raw_hint, str) and len(raw_hint.strip()) > 3:
                    hint = raw_hint.strip()
                raw_complexity = str(data.get("complexity", "normal")).strip().lower()
                if raw_complexity == "complex":
                    complexity = "complex"
            except Exception:
                raw_upper = raw.upper()
                for t in valid_tasks:
                    if t in raw_upper:
                        task_type = t
                        confidence = 0.5
                        break
                if not task_type:
                    for alias, canonical in _TASK_ALIASES2.items():
                        if alias in raw_upper:
                            task_type = canonical
                            confidence = 0.5
                            break

            if (
                task_type in valid_tasks
                and 0.0 <= confidence <= 1.0
                and confidence >= 0.45
            ):
                conf_str = f"🤖 Local+Hint {confidence:.2f} ({latency:.0f}ms)"
                logger.info(
                    f"[LocalModelRouter] classify_with_hint → {task_type} complexity={complexity} | hint={'yes' if hint else 'none'} {conf_str}"
                )
                return task_type, conf_str, "Local", hint, complexity
            else:
                # 解析失败，降级
                task, conf, src = cls.classify(user_input, timeout=timeout)
                return task, conf, src, None, "normal"

        except requests.exceptions.Timeout:
            task, conf, src = cls.classify(user_input, timeout=timeout)
            return task, conf, src, None, "normal"
        except Exception as e:
            logger.error(f"[LocalModelRouter] classify_with_hint 错误: {e}")
            task, conf, src = cls.classify(user_input, timeout=timeout)
            return task, conf, src, None, "normal"

    # ── 本地模型响应生成（简单问题快速通道） ──

    # 用于响应生成的模型（按偏好排序，比分类模型可以更大）
    OLLAMA_RESPONSE_MODELS = [
        "qwen3:8b",  # ★ 最佳，中英文流畅
        "qwen3:4b",  # 快速备选
        "qwen2.5:7b",  # 旧版质量好
        "qwen2.5:3b",  # 旧版快速
        "llama3.2:3b",
    ]

    _response_model = None  # 用于生成的模型（可能比分类模型大）
    _response_model_inited = False

    @classmethod
    def _init_response_model(cls) -> bool:
        """初始化用于响应生成的本地模型"""
        if cls._response_model_inited and cls._response_model:
            return True
        if not cls.is_ollama_available():
            return False

        try:
            resp = requests.get("http://localhost:11434/api/tags", timeout=2)
            if resp.status_code != 200:
                return False
            installed = [m['name'] for m in resp.json().get('models', [])]
        except Exception:
            return False

        if not installed:
            return False

        # 优先选择更大的生成模型
        for want in cls.OLLAMA_RESPONSE_MODELS:
            base = want.split(':')[0]
            for im in installed:
                if base in im:
                    cls._response_model = im
                    cls._response_model_inited = True
                    logger.info(f"[LocalModelRouter] ✅ 响应生成模型: {im}")
                    return True

        # 回退到分类模型
        if cls._model_name:
            cls._response_model = cls._model_name
            cls._response_model_inited = True
            return True
        return False

    @classmethod
    def pick_best_chat_model(cls) -> Optional[str]:
        """
        返回最适合对话/生成的已安装本地模型名称。
        按 OLLAMA_RESPONSE_MODELS 优先级顺序检测，找到即返回；
        若都未安装则回退到分类模型 (_model_name)；
        全部不可用时返回 None（调用方自行处理）。

        等价于 _init_response_model() 但作为只读查询，不修改内部状态。
        """
        if cls._response_model:
            return cls._response_model
        # 尝试初始化（内部会设置 _response_model）
        if cls._init_response_model():
            return cls._response_model
        # 最后降级到分类模型
        return cls._model_name

    # ── 技术性话题信号词：命中即路由到云端 ──────────────────────────────
    # 原则：本地小模型可以回答"是什么/简介"类，但技术深度问题需要云端质量
    _TECHNICAL_CHAT_SIGNALS = [
        # 编程语言核心概念
        "装饰器",
        "闭包",
        "协程",
        "异步",
        "并发",
        "同步锁",
        "线程安全",
        "虚拟机",
        "编译器",
        "解释器",
        "lambda",
        "生成器",
        "迭代器",
        "元类",
        "依赖注入",
        "设计模式",
        "单例",
        "工厂模式",
        "代理模式",
        "观察者",
        "多态",
        "继承",
        "封装",
        "抽象类",
        "接口",
        "泛型",
        "反射",
        # Git/版本控制
        "rebase",
        "cherry-pick",
        "git bisect",
        "git stash",
        "squash",
        # DevOps/基础设施
        "dockerfile",
        "kubernetes",
        "k8s",
        "nginx",
        "kafka",
        "rabbitmq",
        "redis",
        "elasticsearch",
        "pipeline",
        "ci/cd",
        "devops",
        # CS基础
        "算法复杂度",
        "时间复杂度",
        "空间复杂度",
        "二叉树",
        "平衡树",
        "哈希冲突",
        "动态规划",
        "贪心算法",
        "回溯算法",
        "bfs",
        "dfs",
        "图论",
        "最短路径",
        "拓扑排序",
        # 网络/协议
        "tcp握手",
        "tcp/ip",
        "http2",
        "websocket",
        "oauth",
        "jwt",
        "ssl/tls",
        "dns解析",
        "cdn原理",
        "负载均衡",
        # 系统原理
        "内存管理",
        "垃圾回收",
        "gc算法",
        "进程调度",
        "死锁",
        "race condition",
        "缓存一致性",
        "分布式事务",
        "cap定理",
        # 机器学习/AI
        "梯度下降",
        "反向传播",
        "注意力机制",
        "transformer",
        "embedding",
        "卷积神经网络",
        "过拟合",
        "正则化",
        "batch normalization",
    ]

    @classmethod
    def is_simple_query(
        cls, user_input: str, task_type: str, history: list = None
    ) -> bool:
        """
        判断是否可以直接用本地模型回答（跳过云端）。

        适合本地模型的条件（全部满足）：
        - 任务类型是 CHAT
        - 输入长度 ≤80 字符（过长通常意味着细节要求，云端更优）
        - 不含实时数据关键词
        - 不含技术性深度信号词（见 _TECHNICAL_CHAT_SIGNALS）
        - 不含深度/专业写作需求（长报告/论文等）
        - 历史对话不超过 4 轮（8 条）
        """
        if task_type != "CHAT":
            return False

        if not cls.is_ollama_available():
            return False

        text = user_input.strip()
        tl = text.lower()

        # 长输入 → 通常含具体细节要求，云端质量更好
        if len(text) > 80:
            return False

        # 多轮对话上下文太多 → 云模型语境理解更好
        if history and len(history) > 8:  # 4 轮 = 8 条 (user+model)
            return False

        # 需要实时数据的关键词 → 必须联网
        realtime_kw = [
            "今天",
            "现在",
            "最新",
            "实时",
            "天气",
            "股价",
            "汇率",
            "新闻",
            "热点",
            "价格",
            "多少钱",
            "涨",
            "跌",
            "比赛",
            "成绩",
            "排名",
            "选举",
            "疫情",
            "航班",
            "火车票",
            "高铁",
        ]
        if any(kw in text for kw in realtime_kw):
            return False

        # 技术性/研究性话题 → 云端模型理解更准确，回答更可靠
        if any(sig in tl for sig in cls._TECHNICAL_CHAT_SIGNALS):
            return False

        # 需要长篇深度输出的关键词 → 云模型质量更好
        deep_kw = [
            "深入",
            "深度分析",
            "系统性",
            "全面分析",
            "写一篇",
            "写一份",
            "完整报告",
            "论文",
        ]
        if any(kw in text for kw in deep_kw):
            return False

        # 涉及代码生成意图 → 云模型更可靠；纯概念解释可以本地
        code_gen_kw = ["帮我写", "写一个", "帮我实现", "debug", "修改这段代码"]
        has_code_ctx = any(
            k in tl
            for k in ["代码", "函数", "脚本", "python", "java", "javascript", "bug"]
        )
        if has_code_ctx and any(kw in text for kw in code_gen_kw):
            return False

        return True

    @classmethod
    def generate_plan(
        cls, user_input: str, task_type: str, timeout: float = 3.0
    ) -> list:
        """
        并联模式：在云端模型首包响应前，用本地模型生成任务执行计划步骤。
        设计为与云端模型 **并发执行**，填充首包等待的"死区"，
        让用户看到 Koto 正在做什么，而不是空白等待。

        返回: List[str]，每个元素为一个执行步骤描述（中文，≤15字）
        失败时返回空列表（静默降级，不影响主流程）
        """
        if not cls._initialized:
            if not cls.init_model():
                return []

        PLAN_PROMPT = """/no_think
你是任务规划师。根据用户输入和任务类型，生成3-5个执行步骤。

要求：
1. 每步最多15字，动词开头，简洁具体
2. 步骤按执行顺序排列
3. 严格只输出 JSON，不输出任何其他内容

示例：
输入: "帮我写一个快速排序算法" 类型: CODER
输出: {"steps":["分析算法需求与约束","设计递归/迭代结构","编写核心排序逻辑","添加边界条件处理","提供调用示例"]}

输入: "分析一下这个商业计划书" 类型: RESEARCH
输出: {"steps":["解读文档整体结构","分析市场与竞争定位","评估财务与运营可行性","归纳核心优劣势","提出改进建议"]}

输入: "今天北京天气怎么样" 类型: WEB_SEARCH
输出: {"steps":["解析地点与时间要求","调用搜索获取实时数据","整理温度与天气状况","输出出行建议"]}

输入: "写一首关于秋天的诗" 类型: CHAT
输出: {"steps":["理解用户的创作意图","构思诗歌意象与情感","选择合适的格律风格","生成诗歌内容"]}

只输出 JSON：{"steps":["...","...",...]}
"""
        try:
            resp = requests.post(
                "http://localhost:11434/api/chat",
                json={
                    "model": cls._model_name,
                    "messages": [
                        {"role": "system", "content": PLAN_PROMPT},
                        {
                            "role": "user",
                            "content": f"输入: {repr(user_input[:200])} 类型: {task_type}",
                        },
                    ],
                    "stream": False,
                    "format": "json",
                    "think": False,
                    "options": {
                        "temperature": 0.3,
                        "num_predict": 150,
                    },
                },
                timeout=timeout,
            )
            if resp.status_code != 200:
                return []
            raw = (resp.json().get("message", {}) or {}).get("content", "").strip()
            if not raw:
                return []
            data = json.loads(raw)
            steps = data.get("steps", [])
            if isinstance(steps, list):
                return [str(s).strip() for s in steps if str(s).strip()][:5]
            return []
        except Exception as e:
            logger.error(
                f"[LocalModelRouter] generate_plan 静默失败: {type(e).__name__}"
            )
            return []

    @classmethod
    def generate_stream(
        cls,
        user_input: str,
        history: list = None,
        system_instruction: str = None,
        timeout: float = 30.0,
    ):
        """
        使用本地 Ollama 模型流式生成响应。
        当前激活的 Skills 会自动注入到系统指令中（若调用方未提供 system_instruction）。

        Returns: generator of text chunks, or None if unavailable
        """
        if not cls._init_response_model():
            return None

        # 构建 messages
        messages = []

        # ── Skills 注入：若调用方未指定 system_instruction，则自动注入激活 Skills ──
        if system_instruction:
            sys_prompt = system_instruction
        else:
            _base = (
                "你是 Koto，一个友善、专业的 AI 助手。"
                "用中文回答用户问题，如果用户用英文则用英文回答。"
                "回答要简洁明了、准确可靠。"
                "如果不确定答案，请诚实说明。"
            )
            try:
                from app.core.skills.skill_manager import SkillManager

                # AutoMatcher + BindingManager 双路径合并
                _temp_ids: list = []
                try:
                    from app.core.skills.skill_auto_matcher import SkillAutoMatcher

                    _temp_ids = SkillAutoMatcher.match(
                        user_input=user_input or "", task_type="CHAT"
                    )
                except Exception:
                    pass
                try:
                    from app.core.skills.skill_trigger_binding import (
                        get_skill_binding_manager,
                    )

                    _binding_ids = get_skill_binding_manager().match_intent(
                        user_input or ""
                    )
                    if _binding_ids:
                        _temp_ids = list(dict.fromkeys(_temp_ids + _binding_ids))
                except Exception:
                    pass
                sys_prompt = SkillManager.inject_into_prompt(
                    _base,
                    task_type="CHAT",
                    user_input=user_input,
                    temp_skill_ids=_temp_ids,
                )
            except Exception:
                sys_prompt = _base

        messages.append({"role": "system", "content": sys_prompt})

        # ── 注入记忆快照（PersonalityMatrix → 个人背景提示）────────────────
        try:
            import sys as _sys
            _app = _sys.modules.get("web.app") or _sys.modules.get("app")
            _get_mgr = getattr(_app, "get_memory_manager", None) if _app else None
            if _get_mgr:
                _mgr = _get_mgr()
                if _mgr and hasattr(_mgr, "get_compact_memory_snapshot"):
                    _mem_snap = _mgr.get_compact_memory_snapshot(max_chars=150) or ""
                    if _mem_snap:
                        messages[0]["content"] = (
                            f"[用户背景：{_mem_snap}]\n\n" + messages[0]["content"]
                        )
        except Exception:
            pass

        # 加入历史对话（最多最近 4 轮）
        if history:
            recent = history[-8:]  # 最多 4 轮
            for turn in recent:
                role = (
                    "assistant"
                    if turn.get("role") == "model"
                    else turn.get("role", "user")
                )
                parts_text = " ".join(turn.get("parts", []))
                if parts_text.strip():
                    messages.append({"role": role, "content": parts_text[:500]})

        messages.append({"role": "user", "content": user_input})

        def _stream():
            try:
                resp = requests.post(
                    "http://localhost:11434/api/chat",
                    json={
                        "model": cls._response_model,
                        "messages": messages,
                        "stream": True,
                        "options": {
                            "temperature": 0.7,
                            "num_predict": 2048,
                        },
                    },
                    stream=True,
                    timeout=timeout,
                )
                if resp.status_code != 200:
                    return

                import re as _re

                _in_think = False
                _think_buf = ""
                for line in resp.iter_lines():
                    if not line:
                        continue
                    try:
                        data = json.loads(line)
                        content = data.get("message", {}).get("content", "")
                        if content:
                            # 过滤 Qwen3 的 <think>...</think> 思考标签
                            _think_buf += content
                            while True:
                                if _in_think:
                                    end_idx = _think_buf.find("</think>")
                                    if end_idx >= 0:
                                        _think_buf = _think_buf[end_idx + 8 :]
                                        _in_think = False
                                    else:
                                        _think_buf = ""  # 仍在思考中，丢弃
                                        break
                                else:
                                    start_idx = _think_buf.find("<think>")
                                    if start_idx >= 0:
                                        before = _think_buf[:start_idx]
                                        if before:
                                            yield before
                                        _think_buf = _think_buf[start_idx + 7 :]
                                        _in_think = True
                                    else:
                                        # 没有 think 标签，直接输出
                                        # 保留最后几个字符以防标签被截断
                                        if len(_think_buf) > 10:
                                            yield _think_buf[:-10]
                                            _think_buf = _think_buf[-10:]
                                        break
                        if data.get("done"):
                            # 输出剩余缓冲
                            if _think_buf and not _in_think:
                                yield _think_buf
                            break
                    except Exception:
                        continue
            except Exception as e:
                logger.error(f"[LocalModelRouter] 流式生成错误: {e}")

        return _stream()

    @classmethod
    def classify_v2(
        cls,
        user_input: str,
        timeout: float = 4.0,
        include_skill_routing: bool = True,
    ) -> RouterDecision:
        """
        结构化路由决策 v2 — 返回 RouterDecision 对象。

        在旧版 classify() / classify_with_hint() 基础上增加：
        - skill_id    : 尝试将任务类型映射到已注册的 Skill ID
        - forward_to_cloud: 基于 is_simple_query() 决策是否本地处理
        - params      : 预填充的 Skill 变量（如 task_type 对应的系统参数）

        向后兼容：decision.to_legacy_tuple() 可还原为旧版三元组。

        Args:
            user_input           : 用户输入文本
            timeout              : 超时秒数
            include_skill_routing: 是否尝试 Skill 映射（需要 SkillManager 可用）

        Returns:
            RouterDecision 对象
        """
        start = time.time()

        # 调用带 hint 的分类（同时获得 task_type + confidence + hint + complexity）
        task_type, conf_str, source, hint, complexity = cls.classify_with_hint(
            user_input, timeout=timeout
        )

        latency_ms = int((time.time() - start) * 1000)

        # 解析置信度数值（conf_str 格式: "🤖 Local 0.92 (123ms)"）
        confidence = 0.0
        try:
            for token in (conf_str or "").split():
                try:
                    v = float(token)
                    if 0.0 <= v <= 1.0:
                        confidence = v
                        break
                except ValueError:
                    pass
        except Exception:
            pass

        if task_type is None:
            return RouterDecision(
                task_type="CHAT",
                forward_to_cloud=True,
                confidence=0.0,
                hint=hint,
                source=source or "Fallback",
                latency_ms=latency_ms,
            )

        # ── 是否本地直接处理 ─────────────────────────────────────────
        local_ok = cls.is_simple_query(user_input, task_type)
        forward_to_cloud = not local_ok

        # ── Skill 映射（将 task_type 映射到具体 Skill ID）───────────
        skill_id = None
        if include_skill_routing:
            try:
                from app.core.skills.skill_manager import SkillManager

                # 查找活跃 Skill 中 task_types 包含当前分类的第一个
                for sid, sdef in SkillManager._def_registry.items():
                    legacy = SkillManager._registry.get(sid, {})
                    if not legacy.get("enabled", False):
                        continue
                    if task_type in (sdef.task_types or []):
                        # 优先选有 intent_description 的 Skill（更精准）
                        if (
                            sdef.intent_description
                            and sdef.intent_description in user_input
                        ):
                            skill_id = sid
                            break
                        elif skill_id is None:
                            skill_id = sid  # 候选，继续看是否有更好的
            except Exception:
                pass

        return RouterDecision(
            task_type=task_type,
            skill_id=skill_id,
            forward_to_cloud=forward_to_cloud,
            confidence=confidence,
            hint=hint,
            source=source or "Local",
            latency_ms=latency_ms,
            params={"task_type": task_type, "complexity": complexity},
        )
