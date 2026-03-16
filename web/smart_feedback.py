#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
Smart Feedback System — 智能反馈系统
替代过去的硬编码/公式化进度消息，基于任务上下文动态生成自然、有信息量的反馈。
- 根据用户的真实要求，反馈实际进展
- 避免千篇一律的固定说辞
- 提供动态心跳（长时间任务不沉默）
- 任务感知：PPT/文档/搜索/图像等不同任务有不同风格
"""

import time
import re
import threading
from typing import Optional, Callable, Dict, Any, List
import logging


logger = logging.getLogger(__name__)

class SmartFeedback:
    """
    基于任务上下文的智能反馈生成器。
    每次任务应创建一个新的 SmartFeedback 实例。

    用法:
        fb = SmartFeedback(
            user_request="帮我做一个关于人工智能的PPT",
            task_type="PPT",
            emit=lambda msg, detail: send_sse(msg, detail),
            total_steps=5
        )
        fb.start("分析你的主题需求")
        fb.step("正在搜索人工智能最新动态和数据")
        fb.step("大纲已规划，共 10 页幻灯片", important=True)
        fb.substep("充实第 3 页: 机器学习原理")
        fb.done("PPT 生成完成")
    """

    # 任务类型到中文名的映射
    TASK_LABELS = {
        'PPT': '演示文稿',
        'WORD': 'Word 文档',
        'PDF': 'PDF 文档',
        'EXCEL': 'Excel 表格',
        'DOC': '文档',
        'FILE_GEN': '文件',
        'WEB_SEARCH': '搜索',
        'RESEARCH': '深度研究',
        'PAINTER': '图像',
        'MULTI_STEP': '多步任务',
        'AGENT': '智能助手',
        'CHAT': '对话',
    }

    def __init__(
        self,
        user_request: str,
        task_type: str = "FILE_GEN",
        emit: Optional[Callable[[str, str], None]] = None,
        total_steps: Optional[int] = None,
    ):
        """
        Args:
            user_request: 用户原始请求文本
            task_type: 任务类型 (PPT, WORD, EXCEL, RESEARCH, etc.)
            emit: 回调函数 (message, detail) 用于发送进度
            total_steps: 预估总步骤数（可选，用于百分比计算）
        """
        self.user_request = user_request
        self.task_type = task_type.upper()
        self.emit = emit or (lambda msg, detail: None)
        self.total_steps = total_steps
        self.current_step = 0
        self.start_time = time.time()
        self._topic = self._extract_topic(user_request)
        self._task_label = self.TASK_LABELS.get(self.task_type, '任务')
        self._history: List[str] = []
        self._heartbeat_thread: Optional[threading.Thread] = None
        self._heartbeat_active = False
        self._last_message = ""
        self._last_emit_time = 0

    def _extract_topic(self, text: str) -> str:
        """从用户请求中提取主题关键词"""
        # 移除动作词，提取主题
        cleaned = re.sub(
            r'(帮我|请|给我|做一个|生成|制作|创建|写一个|关于|的PPT|的演示|的文档|的报告|ppt|word|excel|pdf)',
            '', text, flags=re.IGNORECASE
        ).strip()
        # 取前30字作为主题摘要
        if len(cleaned) > 30:
            cleaned = cleaned[:30] + '...'
        return cleaned or text[:30]

    def _elapsed(self) -> str:
        """获取已用时间的可读字符串"""
        elapsed = time.time() - self.start_time
        if elapsed < 60:
            return f"{elapsed:.0f}s"
        return f"{elapsed / 60:.1f}min"

    def _send(self, message: str, detail: str = ""):
        """发送一条进度消息，同时返回 (message, detail) 元组"""
        self._last_message = message
        self._last_emit_time = time.time()
        self._history.append(message)
        try:
            self.emit(message, detail)
        except Exception as e:
            logger.info(f"[SmartFeedback] emit error: {e}")
        return (message, detail)

    def start(self, context_hint: str = ""):
        """任务开始 — 发送第一条智能消息。返回 (msg, detail)。"""
        self.start_time = time.time()

        if context_hint:
            msg = f"开始处理: {context_hint}"
        else:
            msg = f"开始为你{'制作' if self.task_type in ('PPT', 'WORD', 'PDF', 'EXCEL') else '处理'}「{self._topic}」"

        return self._send(msg, f"任务类型: {self._task_label}")

    def step(self, message: str, detail: str = "", important: bool = False):
        """推进一个主要步骤。返回 (msg, detail)。"""
        self.current_step += 1

        # 添加步骤计数（如果有总步骤数）
        if self.total_steps and self.total_steps > 1:
            prefix = f"[{self.current_step}/{self.total_steps}] "
        else:
            prefix = ""

        full_msg = f"{prefix}{message}"
        return self._send(full_msg, detail)

    def substep(self, message: str, detail: str = ""):
        """推进一个子步骤（不增加主步骤计数）。返回 (msg, detail)。"""
        return self._send(f"  → {message}", detail)

    def info(self, message: str, detail: str = ""):
        """发送信息性消息（不算步骤）。返回 (msg, detail)。"""
        return self._send(message, detail)

    def warn(self, message: str, detail: str = ""):
        """发送警告消息。返回 (msg, detail)。"""
        return self._send(f"⚠️ {message}", detail)

    def quality_report(self, score: int, issues: List[str] = None, fixes: List[str] = None):
        """发送质量检查报告"""
        if score >= 80:
            emoji = "✅"
            verdict = "质量优秀"
        elif score >= 60:
            emoji = "👍"
            verdict = "质量良好"
        else:
            emoji = "⚠️"
            verdict = "质量待改进"

        msg = f"{emoji} 质量检查: {score}/100 — {verdict}"
        details = []
        if issues:
            details.append("问题: " + "; ".join(issues[:3]))
        if fixes:
            details.append(f"已自动修复 {len(fixes)} 处")
        return self._send(msg, " | ".join(details) if details else "")

    def done(self, message: str = "", detail: str = ""):
        """任务完成。返回 (msg, detail)。"""
        self.stop_heartbeat()
        elapsed = self._elapsed()
        if message:
            final = f"✅ {message} (耗时 {elapsed})"
        else:
            final = f"✅ {self._task_label}处理完成 (耗时 {elapsed})"
        return self._send(final, detail)

    def error(self, message: str, detail: str = ""):
        """任务出错。返回 (msg, detail)。"""
        self.stop_heartbeat()
        return self._send(f"❌ {message}", detail)

    # ─── 心跳系统 ───

    def start_heartbeat(self, interval: float = 8.0, custom_messages: List[str] = None):
        """
        启动后台心跳线程，在长时间无更新时自动发送进度。
        通过上下文生成有意义的心跳信息而非固定 "处理中..."。
        """
        if self._heartbeat_active:
            return

        self._heartbeat_active = True
        default_messages = [
            f"正在处理你关于「{self._topic}」的{self._task_label}，请稍候",
            f"AI 正在深度分析内容，确保高质量输出",
            f"正在优化内容结构和排版",
            f"仍在处理中，内容越详细越需要时间",
            f"正在进行最后的质量检查",
        ]
        messages = custom_messages or default_messages
        msg_idx = [0]

        def _heartbeat_loop():
            while self._heartbeat_active:
                time.sleep(interval)
                if not self._heartbeat_active:
                    break
                # 只有在超过 interval 未发送消息时才发心跳
                since_last = time.time() - self._last_emit_time
                if since_last >= interval * 0.8:
                    elapsed = self._elapsed()
                    hb_msg = messages[msg_idx[0] % len(messages)]
                    self._send(hb_msg, f"已用时 {elapsed}")
                    msg_idx[0] += 1

        self._heartbeat_thread = threading.Thread(target=_heartbeat_loop, daemon=True)
        self._heartbeat_thread.start()

    def stop_heartbeat(self):
        """停止心跳"""
        self._heartbeat_active = False

    # ─── PPT 专用方法 ───

    def ppt_planning(self, context: str = ""):
        """PPT: 开始规划。返回 (msg, detail)。"""
        if context:
            return self.step(f"正在规划「{self._topic}」的内容结构", context)
        else:
            return self.step(f"正在分析主题并规划演示文稿结构")

    def ppt_outline_ready(self, slide_count: int, title: str = "", type_summary: str = ""):
        """PPT: 大纲就绪。返回 (msg, detail)。"""
        detail_parts = []
        if title:
            detail_parts.append(f"标题: {title}")
        if type_summary:
            detail_parts.append(type_summary)
        return self.step(
            f"内容规划完成，共 {slide_count} 页幻灯片",
            " | ".join(detail_parts) if detail_parts else "",
            important=True
        )

    def ppt_enriching(self, count: int):
        """PPT: 开始充实内容。返回 (msg, detail)。"""
        return self.step(f"正在充实 {count} 页的详细内容", f"确保每页信息量充足")

    def ppt_enriched(self, count: int):
        """PPT: 充实完成。返回 (msg, detail)。"""
        return self.substep(f"已充实 {count} 页内容")

    def ppt_images(self, count: int):
        """PPT: 生成配图。返回 (msg, detail)。"""
        return self.step(f"正在为 {count} 个页面生成配图", "使用 AI 绘图引擎")

    def ppt_images_done(self, count: int):
        """PPT: 配图完成。返回 (msg, detail)。"""
        if count > 0:
            return self.substep(f"已生成 {count} 张配图")
        else:
            return self.substep("配图跳过，使用纯文本布局")

    def ppt_rendering(self, slide_count: int = 0):
        """PPT: 开始渲染。返回 (msg, detail)。"""
        if slide_count:
            return self.step(f"正在渲染 {slide_count} 页 PPT 文件", "应用主题样式和排版")
        else:
            return self.step("正在渲染 PPT 文件")

    def ppt_slide_progress(self, current: int, total: int, title: str, stype: str):
        """PPT: 逐页渲染进度。返回 (msg, detail)。"""
        return self.substep(f"渲染 {current}/{total}: {title}", stype)

    def ppt_quality_check(self, score: int, issues: List[str] = None, fixes: List[str] = None):
        """PPT 质量检查结果。返回 (msg, detail)。"""
        return self.quality_report(score, issues, fixes)

    # ─── 文档专用方法 ───

    def doc_generating(self, doc_type: str = "Word", model: str = ""):
        """文档: 开始生成。返回 (msg, detail)。"""
        detail = f"使用 {model} 模型" if model else ""
        return self.step(f"正在撰写「{self._topic}」{doc_type} 文档", detail)

    def doc_writing_progress(self, chars: int):
        """文档: 写作进度。返回 (msg, detail)。"""
        elapsed = self._elapsed()
        return self.substep(f"已生成 {chars} 字符 (耗时 {elapsed})")

    def doc_saving(self, doc_type: str = "Word"):
        """文档: 保存中。返回 (msg, detail)。"""
        return self.substep(f"正在保存 {doc_type} 文件")

    # ─── 搜索专用方法 ───

    def search_start(self, query: str = ""):
        """搜索: 开始。返回 (msg, detail)。"""
        if query:
            return self.step(f"正在搜索「{query[:30]}」相关信息")
        else:
            return self.step(f"正在搜索「{self._topic}」的最新信息")

    def search_done(self, result_count: int = 0, char_count: int = 0):
        """搜索: 完成。返回 (msg, detail)。"""
        detail = ""
        if char_count:
            detail = f"获取 {char_count} 字符参考资料"
        return self.substep(f"搜索完成" + (f"，获取 {result_count} 条结果" if result_count else ""), detail)

    # ─── 研究专用方法 ───

    def research_start(self):
        """深度研究: 开始。返回 (msg, detail)。"""
        return self.step(f"启动深度研究「{self._topic}」", "使用专业模型进行深入分析")

    def research_done(self, char_count: int = 0):
        """深度研究: 完成。返回 (msg, detail)。"""
        detail = f"获取 {char_count} 字研究报告" if char_count else ""
        return self.substep("深度研究完成", detail)

    # ─── 通用工厂方法 ───

    @classmethod
    def for_ppt(cls, user_request: str, emit: Callable, total_steps: int = 7) -> 'SmartFeedback':
        """创建 PPT 任务的反馈器"""
        return cls(user_request=user_request, task_type="PPT", emit=emit, total_steps=total_steps)

    @classmethod
    def for_document(cls, user_request: str, emit: Callable, doc_type: str = "WORD") -> 'SmartFeedback':
        """创建文档任务的反馈器"""
        return cls(user_request=user_request, task_type=doc_type, emit=emit, total_steps=4)

    @classmethod
    def for_multi_step(cls, user_request: str, emit: Callable, total_steps: int = 3) -> 'SmartFeedback':
        """创建多步任务的反馈器"""
        return cls(user_request=user_request, task_type="MULTI_STEP", emit=emit, total_steps=total_steps)
