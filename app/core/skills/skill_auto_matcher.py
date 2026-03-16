# -*- coding: utf-8 -*-
"""
Koto SkillAutoMatcher — 自动技能匹配引擎
==========================================

当用户没有手动启用任何 Skill 时，根据任务类型和用户输入，自动推荐
最适合本轮对话的 1-3 个 Skill，并以「临时注入」方式作用于本次请求，
不修改 SkillManager 中技能的持久启用状态。

关键特性
--------
1. **本地模型优先**：调用 Ollama (Qwen3 + /no_think) 进行语义匹配；
   响应快、隐私安全、支持中英文。
2. **新 Skill 零重训**：每次从 SkillManager 动态读取技能目录，
   包含 description / intent_description 字段；新增 Skill 后无需
   重新训练，模型可立即从上下文中学到新技能用途，完成匹配。
3. **规则兜底**：Ollama 不可用时自动降级到正则模式匹配，
   保证基础功能不中断。
4. **保守策略**：用户已手动启用了 Skill（当前 task_type 下有任何
   启用的 Skill）时，AutoMatcher 静默退出，尊重用户选择。

用法
----
    from app.core.skills.skill_auto_matcher import SkillAutoMatcher

    # 在 UnifiedAgent/agent_routes 的 inject_into_prompt 之前调用：
    temp_ids = SkillAutoMatcher.match(user_input, task_type="CHAT")
    prompt = SkillManager.inject_into_prompt(
        base_instruction, task_type="CHAT",
        user_input=user_input,
        temp_skill_ids=temp_ids,
    )
"""

from __future__ import annotations

import json
import logging
import re
import time
from typing import List, Optional

logger = logging.getLogger(__name__)

# ──────────────────────────────────────────────────────────────────────────────
# Ollama 调用超时（秒）——匹配推理比任务分类稍微宽松一点
# ──────────────────────────────────────────────────────────────────────────────
_MATCH_TIMEOUT = 8.0
_MAX_AUTO_SKILLS = 3  # 单次最多自动注入的 Skill 数量


class SkillAutoMatcher:
    """自动 Skill 匹配引擎（v1）。"""

    # ── Qwen3 /no_think 格式的匹配 Prompt ─────────────────────────────────────
    MATCH_PROMPT = """/no_think
你是 Koto Skill 匹配引擎。根据任务类型和用户消息，从候选技能列表中选出 \
0-{max_n} 个最合适的技能 ID。
规则：
- 只在技能能明显改善本次回答时才选；不要「凑数」
- 严格只输出 JSON 数组，例如 ["step_by_step"] 或 [] ；禁止任何额外文字
- 不要重复选择同一 ID

任务类型: {task_type}
用户消息: {user_input}

候选技能列表:
{skill_catalog}

输出 JSON 数组:"""

    # ── 规则兜底：意图关键词 → Skill ID ──────────────────────────────────────
    _PATTERN_MAP: List[dict] = [
        {"skill_id": "concise_mode",
         "patterns": ["简短", "简洁", "一句话", "快速说", "长话短说", "总结一下", "brief", "concise", "short"]},
        {"skill_id": "step_by_step",
         "patterns": ["一步一步", "分步骤", "操作流程", "详细步骤", "怎么做", "如何做", "请教我", "step by step", "step-by-step"]},
        {"skill_id": "teaching_mode",
         "patterns": ["教我", "讲解", "通俗解释", "像老师一样", "我没学过", "能解释一下", "浅显", "explain", "teach me"]},
        {"skill_id": "professional_tone",
         "patterns": ["正式一点", "专业一点", "商务语气", "写邮件", "汇报", "报告", "formal", "professional"]},
        {"skill_id": "writing_assistant",
         "patterns": ["润色", "改写", "优化表达", "重写", "整理成文", "polish", "rewrite"]},
        {"skill_id": "code_best_practices",
         "patterns": ["写代码", "重构", "代码优化", "最佳实践", "写个函数", "实现一下", "best practice"]},
        {"skill_id": "security_aware",
         "patterns": ["安全", "风险", "漏洞", "加密", "权限", "注入", "security", "vulnerability"]},
        {"skill_id": "research_depth",
         "patterns": ["深入分析", "深度研究", "详细分析", "全面比较", "in-depth", "comprehensive"]},
        {"skill_id": "task_planner",
         "patterns": ["计划", "安排", "待办", "路线图", "拆解任务", "里程碑", "plan", "roadmap", "todo"]},
        {"skill_id": "data_analysis",
         "patterns": ["数据分析", "统计", "图表", "可视化", "趋势", "data analysis", "visualization"]},
        # ── 专项调试技能（必须在 debug_python 前，避免被截胡）──
        {
            "skill_id": "debug_api",
            "patterns": [
                "api报错",
                "api错误",
                "接口报错",
                "接口错误",
                "接口调试",
                "http状态码",
                "请求失败",
                "调用接口",
                "rest报错",
                "restful",
                "401",
                "403",
                "404",
                "500",
                "接口返回",
                "api调用失败",
                "axios报错",
                "fetch报错",
                "requests报错",
                "response错误",
                "api debug",
                "debug api",
                "postman",
            ],
        },
        {
            "skill_id": "debug_web_frontend",
            "patterns": [
                "前端报错",
                "前端调试",
                "react报错",
                "vue报错",
                "js报错",
                "javascript报错",
                "typescript报错",
                "css问题",
                "html问题",
                "dom报错",
                "webpack报错",
                "控制台报错",
                "console.error",
                "前端bug",
                "浏览器报错",
                "前端错误",
                "react error",
                "vue error",
                "next.js报错",
                "vite报告",
                "前端页面",
                "页面报错",
                "前端问题",
                "前端一直报",
            ],
        },
        {
            "skill_id": "debug_performance",
            "patterns": [
                "性能问题",
                "性能慢",
                "响应慢",
                "内存泄漏",
                "cpu占用高",
                "内存占用高",
                "卡顿",
                "超时",
                "timeout",
                "memory leak",
                "性能优化调试",
                "慢查询",
                "程序卡",
                "资源占用",
                "性能分析",
                "profiling",
                "瓶颈",
                "性能很慢",
                "系统很慢",
                "加载慢",
                "接口很慢",
                "响应很慢",
                "匚慢了",
                "很慢了",
                "加载很慢",
                "cpu太高",
                "内存太高",
            ],
        },
        {
            "skill_id": "debug_python",
            "patterns": [
                "python报错",
                "python错误",
                "python调试",
                "python异常",
                "python bug",
                "traceback",
                "python debug",
                "调试python",
                "python崩溃",
                "调试",
                "报错",
                "bug",
                "错误",
                "异常",
                "debug",
                "error",
                "exception",
            ],
        },
        {
            "skill_id": "creative_writing",
            "patterns": [
                "创意",
                "故事",
                "文案",
                "诗",
                "小说",
                "creative",
                "story",
                "poem",
            ],
        },
        {
            "skill_id": "bilingual",
            "patterns": [
                "双语",
                "英文",
                "翻译",
                "术语",
                "bilingual",
                "translate",
                "english",
            ],
        },
        # ── 文件读取与解析 ──
        {
            "skill_id": "pdf_reader",
            "patterns": [
                "读取pdf",
                "解析pdf",
                "打开pdf",
                "pdf内容",
                "读pdf",
                "提取pdf",
                "read pdf",
                "pdf文件",
                "查看pdf",
                "分析pdf",
            ],
        },
        {
            "skill_id": "multi_format_reader",
            "patterns": [
                "读取文件",
                "打开文件",
                "查看文件",
                "读文件",
                "解析文件",
                "文件内容",
                "read file",
                "打开docx",
                "读取docx",
                "读取xlsx",
                "读取csv",
            ],
        },
        {
            "skill_id": "long_doc_parser",
            "patterns": [
                "长文档",
                "很长的文档",
                "长报告",
                "大文件",
                "分段读",
                "逐段分析",
                "长文章",
                "文章很长",
                "超长文件",
                "分块解析",
                "文档太长",
                "太长了",
                "太长",
                "分段分析",
                "分段",
                "内容太多",
                "文章太长",
            ],
        },
        {
            "skill_id": "spreadsheet_analyst",
            "patterns": [
                "表格分析",
                "excel分析",
                "数据表",
                "分析xlsx",
                "分析csv",
                "表格数据",
                "统计表",
                "数据报表",
                "spreadsheet",
            ],
        },
        {
            "skill_id": "multi_doc_synthesis",
            "patterns": [
                "对比文件",
                "比较文档",
                "多个文件",
                "几份文件",
                "综合分析",
                "文件对比",
                "compare docs",
                "多份文档",
                "整合资料",
                "汇总文档",
            ],
        },
        {
            "skill_id": "table_extractor",
            "patterns": [
                "提取表格",
                "读取表格",
                "表格提取",
                "获取表格",
                "table extraction",
                "表格内容",
                "docx表格",
                "pdf表格",
                "里的表格",
                "里面的表格",
                "文档里表格",
                "文件里表格",
                "文档中的表格",
                "文件中表格",
                "提取excel表格",
                "读取excel表格",
            ],
        },
        # ── 文件生成 ──
        {
            "skill_id": "ppt_generator_pro",
            "patterns": [
                "生成ppt",
                "做ppt",
                "制作ppt",
                "做幻灯片",
                "create ppt",
                "make ppt",
                "演示文稿",
                "ppt文件",
                "幻灯片报告",
                "演示报告",
                "一份ppt",
                "个ppt",
                "份ppt",
                "做个ppt",
                "ppt演示",
                "生成幻灯",
                "ppt报告",
                "整理成ppt",
            ],
        },
        {
            "skill_id": "excel_generator_pro",
            "patterns": [
                "生成excel",
                "做excel",
                "制作excel",
                "create excel",
                "make excel",
                "生成xlsx",
                "做xlsx",
                "制作表格文件",
                "做一个excel",
                "做个excel",
                "新建excel",
                "创建excel",
                "生成excel表格",
                "excel表格生成",
                "制作excel表格",
            ],
        },
        {
            "skill_id": "docx_generator_pro",
            "patterns": [
                "生成word",
                "做word",
                "制作word",
                "创建docx",
                "create word",
                "生成文档",
                "做文档",
                "word文档",
                "生成docx",
            ],
        },
        {
            "skill_id": "docx_translator",
            "patterns": [
                "翻译word",
                "翻译文档",
                "word翻译",
                "docx翻译",
                "translate word",
                "translate docx",
                "翻译成英文",
                "翻译成日文",
                "翻译成中文",
                "翻译成法文",
                "翻译成韩文",
                "翻译成德文",
                "文档翻译",
                "把word翻译",
                "word文件翻译",
                "文档翻译成",
                "翻译这个文档",
                "翻译这个word",
                "帮我翻译文档",
                "word转译",
                "译成英文",
                "译成日文",
                "翻译docx",
                "翻译这个docx",
                "把docx翻译",
            ],
        },
        {
            "skill_id": "pdf_generator_pro",
            "patterns": [
                "生成pdf",
                "做pdf",
                "制作pdf",
                "create pdf",
                "导出pdf",
                "生成报告pdf",
                "pdf报告",
                "pdf文档",
            ],
        },
        # ── 批注类 ──
        {
            "skill_id": "annotate_academic",
            "patterns": [
                "学术论文",
                "期刊",
                "投稿",
                "论文润色",
                "研究报告",
                "英文润色",
                "审稿",
                "批注论文",
                "修改论文",
                "学术写作",
                "论文修改",
            ],
        },
        {
            "skill_id": "annotate_business",
            "patterns": [
                "商务报告批注",
                "合同修改",
                "公文批注",
                "商业文档批注",
                "对外报告批注",
                "商务文案批注",
                "合同审查",
                "批注商务",
                "商务文件批注",
            ],
        },
        {
            "skill_id": "annotate_code_review",
            "patterns": [
                "code review",
                "代码审查",
                "代码批注",
                "帮我看看代码",
                "检查代码",
                "找bug",
                "找出bug",
                "review代码",
                "看看这段代码",
                "代码有没有问题",
            ],
        },
        {
            "skill_id": "annotate_translation",
            "patterns": [
                "翻译批注",
                "检查翻译",
                "校对翻译",
                "对照原文",
                "翻译审查",
                "双语批注",
                "翻译有没有问题",
                "校正翻译",
                "润色译文",
                "译文批注",
            ],
        },
        # ── 工作区感知 ──
        {
            "skill_id": "workspace_context",
            "patterns": [
                "当前目录",
                "项目结构",
                "我的项目",
                "工程目录",
                "工作目录",
                "工作区",
                "这个项目",
                "项目文件",
                "目录结构",
                "文件夹结构",
                "扫描目录",
                "workspace",
                "project structure",
                "current directory",
                "项目里有哪些",
            ],
        },
        # ── 文档摘要 ──
        {
            "skill_id": "doc_summarizer",
            "patterns": [
                "总结文档",
                "文档摘要",
                "帮我看文档",
                "帮我看看这个文档",
                "这个文档说了什么",
                "文件摘要",
                "总结一下这个文件",
                "概括文档",
                "文档主要内容",
                "summarize document",
                "document summary",
                "帮我读一下",
                "帮我了解这个文档",
                "文档内容是什么",
                "这份文件讲了什么",
            ],
        },
        # ── 文件管理工具 ──
        {
            "skill_id": "archive_assistant",
            "patterns": [
                "整理文件",
                "归档文件",
                "文件归档",
                "清理下载",
                "文件夹整理",
                "整理文件夹",
                "整理桌面",
                "清理桌面",
                "自动分类文件",
                "文件自动整理",
                "批量整理",
                "归纳文件",
                "organize files",
                "file organization",
                "整理资料",
            ],
        },
        {
            "skill_id": "file_duplicate_hunter",
            "patterns": [
                "重复文件",
                "找重复",
                "清理磁盘",
                "磁盘空间",
                "释放空间",
                "删重复",
                "duplicate files",
                "找出重复",
                "相同文件",
                "占用空间太大",
                "磁盘清理",
            ],
        },
        {
            "skill_id": "file_smart_rename",
            "patterns": [
                "批量重命名",
                "重命名文件",
                "文件重命名",
                "整理文件名",
                "规范命名",
                "rename files",
                "文件名整理",
                "批量改名",
                "照片重命名",
                "图片重命名",
            ],
        },
        {
            "skill_id": "file_daily_report",
            "patterns": [
                "工作日报",
                "今天做了什么",
                "工作进展",
                "工作总结",
                "文件变动",
                "每日汇报",
                "工作日志",
                "今日工作",
                "daily report",
                "文件活动记录",
            ],
        },
        # ── 高价值 Workflow Skills（商业场景）──
        {"skill_id": "email_writer",
         "patterns": ["写邮件", "帮我写邮件", "邮件正文", "回复邮件", "发邮件", "邮件模板",
                       "起草邮件", "邮件草稿", "write email", "draft email", "compose email",
                       "客户邮件", "商务邮件正文", "邮件内容",
                       "一封邮件", "封邮件", "封邮", "邮件怎么写", "邮件范文",
                       "商务邮件", "邮件内容", "写封邮", "邮件撰写"]},
        {"skill_id": "meeting_notes",
         "patterns": ["会议纪要", "整理会议", "会议记录", "会议总结", "帮我整理会议",
                       "meeting minutes", "会议内容整理", "讨论要点", "会议梳理", "开会记录"]},
        {"skill_id": "work_report_generator",
         "patterns": ["写报告", "帮我写报告", "生成报告", "撰写报告", "分析报告",
                       "工作报告", "项目报告", "write report", "报告模板", "报告正文"]},
        {"skill_id": "negotiation_assist",
         "patterns": ["谈判", "砍价", "商务谈判", "谈条件", "价格谈判", "谈合同",
                       "negotiation", "谈判策略", "谈判话术", "如何谈判", "谈判技巧",
                       "应对客户压价", "合同谈判"]},
        {"skill_id": "root_cause",
         "patterns": ["根因分析", "根本原因", "问题溯源", "问题根因", "rca", "root cause",
                       "为什么会发生", "找原因", "故障分析", "复盘原因", "追溯问题"]},
        {"skill_id": "brainstorm",
         "patterns": ["头脑风暴", "想法发散", "创意方案", "帮我想想", "有什么方案",
                       "brainstorm", "idea generation", "ideation", "想点子", "创意发想",
                       "有哪些思路", "集思广益", "发散思维"]},
        {"skill_id": "pros_cons",
         "patterns": ["优缺点", "利弊", "对比分析", "pros and cons", "pros cons",
                       "利弊分析", "正反两面", "做决策", "帮我比较", "方案对比",
                       "权衡利弊", "好处和坏处", "分析利弊"]},
        {"skill_id": "okr_builder",
         "patterns": ["okr", "目标设定", "kpi制定", "关键结果", "key results",
                       "帮我写okr", "制定目标", "季度目标", "年度目标", "okr拆解"]},
        {"skill_id": "sprint_planner",
         "patterns": ["sprint", "迭代计划", "sprint计划", "冲刺计划", "敏捷开发",
                       "sprint planning", "迭代安排", "排期", "里程碑计划", "发版计划"]},
        {"skill_id": "contract_reviewer",
         "patterns": ["审合同", "合同审查", "看合同", "合同条款", "合同风险",
                       "contract review", "审核合同", "合同有没有问题", "帮我看合同",
                       "协议审查", "甲乙方条款",
                       "合同", "协议条款", "关于合同", "合同内容"]},
        {"skill_id": "sop_writer",
         "patterns": ["sop", "标准操作", "操作规范", "流程文档", "写流程",
                       "standard operating procedure", "制作sop", "操作手册", "流程规范",
                       "作业指导书", "操作说明书"]},
        {"skill_id": "interview_prep",
         "patterns": ["面试准备", "面试题", "帮我准备面试", "interview prep", "interview questions",
                       "模拟面试", "面试技巧", "hr面试", "技术面试", "面试常见问题",
                       "面试自我介绍", "面试问答"]},
        {"skill_id": "learning_guide",
         "patterns": ["学习路线", "学习路径", "学习计划", "learning roadmap", "learning path",
                       "如何学习", "从哪里开始学", "入门到精通", "学习大纲", "技能树",
                       "怎么系统学习", "自学方案"]},
        {"skill_id": "survey_designer",
         "patterns": ["问卷设计", "设计问卷", "调查问卷", "问卷模板", "survey design",
                       "做问卷", "设计调研", "用户调研问卷", "满意度调查", "问卷怎么写"]},
        {"skill_id": "kpi_designer",
         "patterns": ["kpi设计", "设计kpi", "绩效指标", "kpi指标", "考核指标",
                       "kpi framework", "绩效考核设计", "设计绩效", "评估指标", "kpi体系"]},
        {"skill_id": "social_copy",
         "patterns": ["朋友圈文案", "小红书文案", "社媒文案", "微博文案", "社交媒体文案",
                       "social media copy", "营销文案", "推广文案", "写文案", "广告文案",
                       "抖音文案", "种草文案", "公众号文案"]},
        {"skill_id": "feedback_polisher",
         "patterns": ["优化反馈", "改写反馈", "润色反馈", "更委婉", "表达得更好",
                       "feedback polish", "说得好听一点", "更有建设性", "温和地表达",
                       "批评怎么说", "如何给反馈", "建设性意见"]},
        {"skill_id": "prompt_refiner",
         "patterns": ["写prompt", "优化prompt", "prompt工程", "提示词", "prompt engineering",
                       "写提示词", "提示词优化", "system prompt", "如何写prompt",
                       "prompt设计", "指令优化", "ai提示词"]},
    ]

    @classmethod
    def _build_skill_catalog(cls, task_type: str) -> tuple[List[dict], str]:
        """
        从 SkillManager 动态构建候选技能目录（过滤不适用于当前 task_type 的技能）。
        Returns: (candidate_list, catalog_text_for_prompt)
        """
        try:
            from app.core.skills.skill_manager import SkillManager

            SkillManager._ensure_init()
        except Exception as e:
            logger.debug(f"[AutoMatcher] SkillManager 加载失败: {e}")
            return [], ""

        candidates: List[dict] = []
        lines: List[str] = []
        tt = task_type.upper() if task_type else ""

        for skill_id, s in SkillManager._registry.items():
            applicable = s.get("task_types", [])
            # 保留所有技能作为候选——task_type 已包含在 Prompt 里，
            # 由 Ollama 语义决定是否适合本轮，而非在目录构建时硬过滤。
            # （原先的过滤会导致 DOC_ANNOTATE 技能在 CHAT 场景下不可见）
            desc = s.get("intent_description") or s.get("description", "")
            name = s.get("name", skill_id)
            candidates.append(
                {
                    "id": skill_id,
                    "name": name,
                    "desc": desc,
                    "task_types": applicable,
                }
            )
            lines.append(f"  • {skill_id} ({name}): {desc}")

        return candidates, "\n".join(lines)

    @classmethod
    def _has_active_skills_for_task(cls, task_type: str) -> bool:
        """判断当前 task_type 下是否已有用户手动启用的非系统 Skill。

        系统级 Skill（skill_nature='system'，如 long_term_memory）即使启用也不
        阻断 AutoMatcher，因为它们是后台能力，不代表用户对本轮任务的主动干预。
        """
        try:
            from app.core.skills.skill_manager import SkillManager

            SkillManager._ensure_init()
            tt = task_type.upper() if task_type else ""
            for sid, s in SkillManager._registry.items():
                if not s.get("enabled", False):
                    continue
                # 跳过系统级 Skill（不代表用户的任务导向选择）
                if s.get("skill_nature", "") == "system":
                    continue
                applicable = s.get("task_types", [])
                if not applicable or tt in applicable:
                    return True
        except Exception:
            pass
        return False

    @classmethod
    def _match_with_intent_ngram(
        cls,
        user_input: str,
        candidates: List[dict],
        n: int = 2,
        threshold: float = 0.07,
    ) -> List[str]:
        """
        字符 n-gram 相似度中间层：计算用户输入与每个技能 intent_description / description
        的字符二元组 Jaccard 相似度，作为 Ollama（本地模型）与规则兜底之间的语义补充层。
        无外部依赖，不增加启动开销。
        """
        if not user_input or not candidates:
            return []

        def _ngrams(text: str) -> set:
            t = text.lower().replace(" ", "")
            return {t[i:i + n] for i in range(max(0, len(t) - n + 1))}

        input_ng = _ngrams(user_input[:300])
        if not input_ng:
            return []

        scored: list = []
        for c in candidates:
            desc = c.get("desc", "")
            if not desc:
                continue
            desc_ng = _ngrams(desc)
            if not desc_ng:
                continue
            union = len(input_ng | desc_ng)
            if union == 0:
                continue
            jaccard = len(input_ng & desc_ng) / union
            if jaccard >= threshold:
                scored.append((jaccard, c["id"]))

        scored.sort(key=lambda x: x[0], reverse=True)
        return [sid for _, sid in scored[:_MAX_AUTO_SKILLS]]

    @classmethod
    def _match_with_patterns(cls, user_input: str, candidates: List[dict]) -> List[str]:
        """规则兜底：关键词匹配，返回匹配到的 skill_id 列表。

        匹配来源（优先级依次降低）：
        1. 类级 _PATTERN_MAP（内置硬编码规则，启动时固定）
        2. SkillDefinition.trigger_keywords（自定义 Skill 运行时注册，重启后仍有效）
        """
        candidate_ids = {c["id"] for c in candidates}
        matched: List[str] = []
        matched_set: set = set()
        lowered = user_input.lower()

        # 1. 内置 _PATTERN_MAP
        for entry in cls._PATTERN_MAP:
            sid = entry["skill_id"]
            if sid not in candidate_ids or sid in matched_set:
                continue
            if any(p.lower() in lowered for p in entry["patterns"]):
                matched.append(sid)
                matched_set.add(sid)
                if len(matched) >= _MAX_AUTO_SKILLS:
                    return matched

        # 2. SkillDefinition.trigger_keywords（持久化在 JSON 的自定义 Skill）
        try:
            from app.core.skills.skill_manager import SkillManager
            SkillManager._ensure_init()
            for sid, skill_def in SkillManager._def_registry.items():
                if sid not in candidate_ids or sid in matched_set:
                    continue
                kws = getattr(skill_def, "trigger_keywords", None) or []
                if kws and any(kw.lower() in lowered for kw in kws):
                    matched.append(sid)
                    matched_set.add(sid)
                    if len(matched) >= _MAX_AUTO_SKILLS:
                        return matched
        except Exception as _e:
            logger.debug("[AutoMatcher] trigger_keywords 扫描失败: %s", _e)

        return matched

    @classmethod
    def _match_with_local_model(
        cls,
        user_input: str,
        task_type: str,
        catalog_text: str,
        candidate_ids: set,
    ) -> Optional[List[str]]:
        """
        使用本地 Ollama 模型进行语义匹配。
        Returns None 如果 Ollama 不可用或调用失败。
        """
        try:
            from app.core.routing.local_model_router import LocalModelRouter

            if not LocalModelRouter.is_ollama_available():
                return None
            if not LocalModelRouter._initialized:
                LocalModelRouter.init_model()
            if not LocalModelRouter._initialized or not LocalModelRouter._model_name:
                return None

            prompt = cls.MATCH_PROMPT.format(
                max_n=_MAX_AUTO_SKILLS,
                task_type=task_type,
                user_input=user_input[:500],  # 截断超长输入
                skill_catalog=catalog_text,
            )

            start = time.time()
            result, err = LocalModelRouter.call_ollama_chat(
                messages=[
                    {
                        "role": "system",
                        "content": (
                            "你是 Koto Skill 匹配引擎。"
                            '只输出 JSON 数组，格式如 ["skill_id"] 或 []，'
                            "禁止任何其他内容。"
                        ),
                    },
                    {"role": "user", "content": prompt},
                ],
                options={"temperature": 0.0, "num_predict": 80},
                timeout=_MATCH_TIMEOUT,
            )
            latency = time.time() - start

            if err:
                logger.debug(f"[AutoMatcher] Ollama 调用失败: {err}")
                return None

            # 解析 JSON 数组
            result = result.strip()
            # 移除 markdown 代码块
            result = re.sub(r"^```[a-z]*\n?", "", result)
            result = re.sub(r"\n?```$", "", result)
            result = result.strip()

            if not result.startswith("["):
                # 尝试提取 [...] 片段
                m = re.search(r"\[.*?\]", result, re.DOTALL)
                result = m.group() if m else "[]"

            try:
                parsed = json.loads(result)
            except json.JSONDecodeError:
                logger.debug(f"[AutoMatcher] JSON 解析失败: {result!r}")
                return None

            if not isinstance(parsed, list):
                return None

            # 过滤出已知 skill_id，最多 _MAX_AUTO_SKILLS 个
            valid = [
                sid for sid in parsed if isinstance(sid, str) and sid in candidate_ids
            ][:_MAX_AUTO_SKILLS]

            logger.info(
                f"[AutoMatcher] 🎯 本地模型匹配 ({latency:.2f}s): "
                f"{task_type} → {valid}"
            )
            return valid

        except Exception as e:
            logger.debug(f"[AutoMatcher] 本地模型调用异常: {e}")
            return None

    @classmethod
    def match(
        cls,
        user_input: str,
        task_type: str = "CHAT",
        force: bool = False,
    ) -> List[str]:
        """
        自动匹配本轮对话最适合的 Skill ID 列表（临时注入，不修改持久状态）。

        参数
        ----
        user_input : 用户原始输入文本
        task_type  : 任务分类（SmartDispatcher 输出）
        force      : True = 即使用户已手动启用 Skill 也执行匹配（补充模式）

        返回
        ----
        List[str]  : 推荐临时激活的 skill_id 列表；空列表表示不需要额外注入
        """
        # ── 构建候选 Skill 目录 ─────────────────────────────────────────────
        candidates, catalog_text = cls._build_skill_catalog(task_type)
        if not candidates:
            return []

        candidate_ids = {c["id"] for c in candidates}

        # ── 优先尝试本地模型匹配 ────────────────────────────────────────────
        model_result = cls._match_with_local_model(
            user_input, task_type, catalog_text, candidate_ids
        )
        if model_result is not None:
            return model_result

        # ── n-gram 语义相似度中间层（Ollama 不可用时，比纯关键词更泛化）─────
        ngram_result = cls._match_with_intent_ngram(user_input, candidates)
        if ngram_result:
            logger.info(
                f"[AutoMatcher] 🔤 n-gram 匹配: {task_type} → {ngram_result}"
            )
            return ngram_result

        # ── 最终兜底：精确关键词规则 ────────────────────────────────────────
        pattern_result = cls._match_with_patterns(user_input, candidates)
        if pattern_result:
            logger.info(
                f"[AutoMatcher] 📋 规则匹配: {task_type} → {pattern_result}"
            )
            return pattern_result

        # ── 如果已有活跃领域 Skill，跳过 LLM 调用（只节省 LLM 开销） ───────────
        if not force and cls._has_active_skills_for_task(task_type):
            logger.debug(f"[AutoMatcher] 用户已启用域 Skill，跳过 LLM 自动匹配")
            return []

        # ── 规则未命中，尝试本地模型语义匹配 ──────────────────────────────────
        model_result = cls._match_with_local_model(
            user_input, task_type, catalog_text, candidate_ids
        )
        if model_result:
            return model_result

        return []

    @classmethod
    def describe_matched(cls, skill_ids: List[str]) -> str:
        """返回匹配到的 Skill 的中文名称列表，用于日志/调试输出。"""
        if not skill_ids:
            return "（无）"
        try:
            from app.core.skills.skill_manager import SkillManager

            SkillManager._ensure_init()
            names = [
                SkillManager._registry.get(sid, {}).get("name", sid)
                for sid in skill_ids
            ]
            return "、".join(names)
        except Exception:
            return ", ".join(skill_ids)
