# -*- coding: utf-8 -*-
"""
TrainingDataBuilder — 从 Koto 现有功能数据生成本地模型训练集
============================================================

职责
----
1. 扫描 chats/*.json   →  结构化对话样本（带 task_type 标签）
2. 扫描 workspace/shadow_traces/*.jsonl  →  高质量认可样本
3. 生成 Ollama 兼容的 GGUF/Modelfile 微调格式（JSONL）
4. 生成 Qwen3 Chat 格式数据集（供 lora_pipeline.py 使用）
5. 提供 Flask API 路由（/api/training/*）

产出文件
--------
workspace/training_data/
  koto_routing_{timestamp}.jsonl     ← 任务路由微调数据（最关键）
  koto_chat_{timestamp}.jsonl        ← 通用对话数据
  koto_full_{timestamp}.jsonl        ← 合并总集
  stats_{timestamp}.json             ← 统计摘要

用法
----
  python -m app.core.learning.training_data_builder  # CLI 生成
  from app.core.learning.training_data_builder import TrainingDataBuilder
  result = TrainingDataBuilder.build_all()
"""

from __future__ import annotations

import json
import logging
import os
import re
import time
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)

# ── 路径 ─────────────────────────────────────────────────────────────────────
import sys as _sys
def _get_base_dir() -> Path:
    if getattr(_sys, 'frozen', False):
        return Path(_sys.executable).parent
    return Path(__file__).resolve().parents[3]
_BASE_DIR = _get_base_dir()
_CHATS_DIR = _BASE_DIR / "chats"
_SHADOW_DIR = _BASE_DIR / "workspace" / "shadow_traces"
_OUT_DIR = _BASE_DIR / "workspace" / "training_data"

# ── 已知任务类型 ──────────────────────────────────────────────────────────────
VALID_TASK_TYPES = {
    "CHAT", "CODER", "PAINTER", "FILE_GEN", "DOC_ANNOTATE",
    "RESEARCH", "WEB_SEARCH", "FILE_SEARCH", "SYSTEM", "AGENT",
    "FILE_OP", "FILE_EDIT", "MULTI_STEP", "VISION", "FILE_CLASSIFY"
}

# ── Koto 系统提示（路由器用）────────────────────────────────────────────────
_ROUTER_SYSTEM = """你是 Koto AI 的任务路由分类器。
根据用户输入判断任务类型，严格只输出 JSON: {"task":"TYPE","confidence":0.9}
可用类型: CHAT CODER PAINTER FILE_GEN DOC_ANNOTATE RESEARCH WEB_SEARCH FILE_SEARCH SYSTEM AGENT"""

_CHAT_SYSTEM = """你是 Koto，一个基于 Gemini 的本地 AI 助手。
你擅长中英文对话、代码编写、文档处理和系统操作。
请直接、简洁地回答用户问题。
**重要**：除非用户明确询问系统状态（CPU、内存、磁盘、进程等），否则不要主动提及系统信息。普通问答、学习解释、写作、代码指导等场景均无需报告系统状态。"""


@dataclass
class TrainingSample:
    """单条训练样本"""
    system: str
    user: str
    assistant: str
    task_type: str = "CHAT"
    source: str = "chat_history"   # chat_history / shadow_trace / synthetic
    quality: float = 0.7           # 0.0-1.0 质量评分
    metadata: Dict[str, Any] = field(default_factory=dict)

    def to_ollama_jsonl(self) -> str:
        """Ollama Modelfile 微调格式"""
        return json.dumps({
            "messages": [
                {"role": "system",    "content": self.system},
                {"role": "user",      "content": self.user},
                {"role": "assistant", "content": self.assistant},
            ]
        }, ensure_ascii=False)

    def to_qwen3_jsonl(self) -> str:
        """Qwen3 SFT 格式（HuggingFace datasets 兼容）"""
        return json.dumps({
            "system":     self.system,
            "input":      self.user,
            "output":     self.assistant,
            "task_type":  self.task_type,
            "source":     self.source,
            "quality":    self.quality,
        }, ensure_ascii=False)


class TrainingDataBuilder:
    """
    从 Koto 现有运行数据构建本地模型训练集。

    主入口: TrainingDataBuilder.build_all()
    """

    # 最短有效响应长度（太短的回复不作为训练样本）
    MIN_RESPONSE_LEN = 20
    # 最长响应（超长可能含噪音）
    MAX_RESPONSE_LEN = 4000
    # 最短用户输入
    MIN_INPUT_LEN = 3

    @classmethod
    def build_all(
        cls,
        include_routing: bool = True,
        include_chat:    bool = True,
        include_shadow:  bool = True,
        include_synthetic: bool = True,
        include_memory:  bool = True,
        min_quality: float = 0.5,
        output_dir: Optional[Path] = None,
        verbose: bool = True,
    ) -> Dict[str, Any]:
        """
        全量构建训练数据集。

        Returns:
            {
              "routing_file": str,
              "chat_file": str,
              "full_file": str,
              "stats": {...},
            }
        """
        _out = output_dir or _OUT_DIR
        _out.mkdir(parents=True, exist_ok=True)
        ts = datetime.now().strftime("%Y%m%d_%H%M%S")

        samples: List[TrainingSample] = []

        # ─── 1. 聊天历史 ───────────────────────────────────────────────────
        if include_chat or include_routing:
            chat_samples = cls._load_chat_history()
            samples.extend(chat_samples)
            if verbose:
                print(f"[TrainingBuilder] 📁 聊天历史: {len(chat_samples)} 条")

        # ─── 2. Shadow Traces（用户认可的高质量样本）─────────────────────
        if include_shadow:
            shadow_samples = cls._load_shadow_traces()
            samples.extend(shadow_samples)
            if verbose:
                print(f"[TrainingBuilder] 👤 Shadow Traces: {len(shadow_samples)} 条")

        # ─── 3. 合成数据（规则生成任务路由示例）─────────────────────────
        if include_synthetic:
            synth_samples = cls._generate_synthetic_routing_samples()
            samples.extend(synth_samples)
            if verbose:
                print(f"[TrainingBuilder] 🔧 合成路由样本: {len(synth_samples)} 条")

            # 合成行为示例：教模型在通用对话中不主动提及系统信息
            behavior_samples = cls._generate_synthetic_chat_samples()
            samples.extend(behavior_samples)
            if verbose:
                print(f"[TrainingBuilder] 🎯 合成行为样本（减少系统信息提及）: {len(behavior_samples)} 条")

        # ─── 记忆桥接样本（个性化探针 + 高质量评分对话 + 风格样本）────
        if include_memory:
            try:
                from app.core.learning.memory_to_training import MemoryToTraining
                mem_dicts = MemoryToTraining.build_samples(verbose=verbose)
                for d in mem_dicts:
                    samples.append(TrainingSample(
                        system=d["system"],
                        user=d["user"],
                        assistant=d["assistant"],
                        task_type=d.get("task_type", "CHAT"),
                        source=d.get("source", "memory"),
                        quality=float(d.get("quality", 0.75)),
                        metadata=d.get("metadata", {}),
                    ))
            except Exception as _me:
                if verbose:
                    print(f"[TrainingBuilder] ⚠️ 记忆桥接跳过: {_me}")

        # ─── 5. 文件分类样本（catalog 归纳时生成）────────────────────────
        import json as _json_td
        _classify_file = _BASE_DIR / "config" / "training_data" / "file_classify_samples.jsonl"
        if _classify_file.exists() and _classify_file.stat().st_size > 0:
            try:
                _classify_count = 0
                with open(_classify_file, encoding="utf-8") as _cf:
                    for _line in _cf:
                        _line = _line.strip()
                        if not _line:
                            continue
                        try:
                            _d = _json_td.loads(_line)
                            samples.append(TrainingSample(
                                system=_d.get("system", ""),
                                user=_d.get("user", ""),
                                assistant=_d.get("assistant", ""),
                                task_type=_d.get("task_type", "FILE_CLASSIFY"),
                                source=_d.get("source", "catalog_run"),
                                quality=float(_d.get("quality", 0.7)),
                                metadata={"timestamp": _d.get("timestamp", "")},
                            ))
                            _classify_count += 1
                        except Exception:
                            pass
                if verbose:
                    print(f"[TrainingBuilder] 📄 文件分类样本: {_classify_count} 条")
            except Exception as _ce:
                if verbose:
                    print(f"[TrainingBuilder] ⚠️ 文件分类样本加载失败: {_ce}")

        # ─── 过滤 ──────────────────────────────────────────────────────────
        samples = [s for s in samples if s.quality >= min_quality]
        samples = cls._deduplicate(samples)
        if verbose:
            print(f"[TrainingBuilder] ✅ 去重后总计: {len(samples)} 条")

        # ─── 分组输出 ─────────────────────────────────────────────────────
        routing_samples = [s for s in samples if s.system == _ROUTER_SYSTEM]
        chat_samples_out = [s for s in samples if s.system != _ROUTER_SYSTEM]

        routing_file = _out / f"koto_routing_{ts}.jsonl"
        chat_file    = _out / f"koto_chat_{ts}.jsonl"
        full_file    = _out / f"koto_full_{ts}.jsonl"

        cls._write_jsonl(routing_file, routing_samples)
        cls._write_jsonl(chat_file,    chat_samples_out)
        cls._write_jsonl(full_file,    samples)

        # ─── 统计 ─────────────────────────────────────────────────────────
        task_counts: Dict[str, int] = {}
        src_counts:  Dict[str, int] = {}
        for s in samples:
            task_counts[s.task_type] = task_counts.get(s.task_type, 0) + 1
            src_counts[s.source]     = src_counts.get(s.source, 0) + 1

        stats = {
            "total":              len(samples),
            "routing_samples":    len(routing_samples),
            "chat_samples":       len(chat_samples_out),
            "by_task_type":       task_counts,
            "by_source":          src_counts,
            "timestamp":          ts,
            "routing_file":       str(routing_file),
            "chat_file":          str(chat_file),
            "full_file":          str(full_file),
        }
        stats_file = _out / f"stats_{ts}.json"
        stats_file.write_text(json.dumps(stats, ensure_ascii=False, indent=2), encoding="utf-8")

        if verbose:
            print(f"[TrainingBuilder] 📊 统计: {stats}")
            print(f"[TrainingBuilder] 💾 输出目录: {_out}")

        # ─── 自动推送到 Ollama（如可用）───────────────────────────────────
        cls._push_to_ollama_if_available(routing_file, full_file, verbose=verbose)

        return {
            "routing_file": str(routing_file),
            "chat_file":    str(chat_file),
            "full_file":    str(full_file),
            "stats":        stats,
        }

    # ══════════════════════════════════════════════════════════════════
    # 数据加载
    # ══════════════════════════════════════════════════════════════════

    @classmethod
    def _load_chat_history(cls) -> List[TrainingSample]:
        """从 chats/*.json 加载对话历史"""
        samples: List[TrainingSample] = []
        if not _CHATS_DIR.exists():
            return samples

        for chat_file in _CHATS_DIR.glob("*.json"):
            try:
                turns = json.loads(chat_file.read_text(encoding="utf-8"))
                samples.extend(cls._parse_chat_turns(turns, source=f"chat:{chat_file.stem}"))
            except Exception as e:
                logger.warning(f"[TrainingBuilder] 解析 {chat_file.name} 失败: {e}")

        return samples

    @classmethod
    def _parse_chat_turns(cls, turns: List[Dict], source: str) -> List[TrainingSample]:
        """将对话轮次解析为 TrainingSample 列表"""
        samples: List[TrainingSample] = []
        i = 0
        while i < len(turns) - 1:
            user_turn  = turns[i]
            model_turn = turns[i + 1]
            i += 2

            if user_turn.get("role") != "user":
                continue
            if model_turn.get("role") != "model":
                continue

            user_text  = cls._extract_text(user_turn)
            model_text = cls._extract_text(model_turn)
            task_type  = model_turn.get("task", "CHAT")

            if not cls._is_valid_sample(user_text, model_text):
                continue
            if task_type not in VALID_TASK_TYPES:
                task_type = "CHAT"

            quality = cls._estimate_quality(user_text, model_text, task_type)

            # 路由样本（每个历史对话都带任务分类标签，可用于路由训练）
            router_answer = json.dumps({"task": task_type, "confidence": 0.88}, ensure_ascii=False)
            samples.append(TrainingSample(
                system=_ROUTER_SYSTEM,
                user=user_text,
                assistant=router_answer,
                task_type=task_type,
                source=source,
                quality=quality,
                metadata={"model": model_turn.get("model_name", "")},
            ))

            # 对话样本
            samples.append(TrainingSample(
                system=_CHAT_SYSTEM,
                user=user_text,
                assistant=model_text,
                task_type=task_type,
                source=source,
                quality=quality,
                metadata={"model": model_turn.get("model_name", "")},
            ))

        return samples

    @classmethod
    def _load_shadow_traces(cls) -> List[TrainingSample]:
        """从 workspace/shadow_traces/*.jsonl 加载高质量记录"""
        samples: List[TrainingSample] = []
        if not _SHADOW_DIR.exists():
            return samples

        for trace_file in _SHADOW_DIR.glob("*.jsonl"):
            try:
                for line in trace_file.read_text(encoding="utf-8").splitlines():
                    if not line.strip():
                        continue
                    try:
                        rec = json.loads(line)
                    except json.JSONDecodeError:
                        continue

                    user_text  = rec.get("user_input", "")
                    ai_text    = rec.get("ai_response", "")
                    task_type  = rec.get("task_type") or "CHAT"
                    feedback   = rec.get("feedback", "")

                    if not cls._is_valid_sample(user_text, ai_text):
                        continue
                    if task_type not in VALID_TASK_TYPES:
                        task_type = "CHAT"

                    # 用户认可的样本质量更高
                    quality = 0.90 if feedback in ("thumbs_up", "workflow_complete") else 0.75

                    router_answer = json.dumps({"task": task_type, "confidence": 0.92}, ensure_ascii=False)
                    samples.append(TrainingSample(
                        system=_ROUTER_SYSTEM,
                        user=user_text,
                        assistant=router_answer,
                        task_type=task_type,
                        source="shadow_trace",
                        quality=quality,
                        metadata={"feedback": feedback, "model": rec.get("model_used", "")},
                    ))

                    samples.append(TrainingSample(
                        system=_CHAT_SYSTEM,
                        user=user_text,
                        assistant=ai_text,
                        task_type=task_type,
                        source="shadow_trace",
                        quality=quality,
                    ))
            except Exception as e:
                logger.warning(f"[TrainingBuilder] 解析 {trace_file.name} 失败: {e}")

        return samples

    @classmethod
    def _generate_synthetic_routing_samples(cls) -> List[TrainingSample]:
        """
        调用 SyntheticDataGenerator 获取大规模黄金标准标注数据（1000+ 条），
        覆盖所有任务类型、中英文变体、边界歧义场景，提升本地模型任务识别能力。
        同时保留少量内联样本作为兜底。
        """
        # ── 主数据源：SyntheticDataGenerator（大规模黄金标准）──────────────
        try:
            from app.core.learning.synthetic_data_generator import SyntheticDataGenerator
            gold_samples = SyntheticDataGenerator.generate_all(shuffle=True)
            samples: List[TrainingSample] = []
            for user_text, task_type, confidence in gold_samples:
                if task_type not in VALID_TASK_TYPES:
                    task_type = "CHAT"
                answer = json.dumps({"task": task_type, "confidence": confidence}, ensure_ascii=False)
                samples.append(TrainingSample(
                    system=_ROUTER_SYSTEM,
                    user=user_text,
                    assistant=answer,
                    task_type=task_type,
                    source="synthetic_gold",
                    quality=0.96,
                ))
            logger.info(f"[TrainingBuilder] SyntheticDataGenerator 加载 {len(samples)} 条黄金标准样本")
            return samples
        except ImportError:
            logger.warning("[TrainingBuilder] SyntheticDataGenerator 未找到，使用内联样本兜底")

        # ── 兜底内联样本 ─────────────────────────────────────────────────────
        SYNTHETIC_EXAMPLES = [
            # (user_input, task_type, confidence)
            # ── SYSTEM ──────────────────────────────────────────────────────
            ("打开微信",               "SYSTEM",      0.97),
            ("帮我截图",               "SYSTEM",      0.95),
            ("打开 Chrome 浏览器",      "SYSTEM",      0.95),
            ("关闭所有窗口",            "SYSTEM",      0.93),
            ("打开任务管理器",          "SYSTEM",      0.95),
            ("帮我关机",               "SYSTEM",      0.96),
            ("打开系统设置",            "SYSTEM",      0.94),
            # ── AGENT ───────────────────────────────────────────────────────
            ("给张三发微信说明天开会",   "AGENT",       0.95),
            ("设置明天早上8点提醒我开会","AGENT",       0.93),
            ("帮我自动登录网站",         "AGENT",       0.90),
            ("向李四发邮件说项目完成了", "AGENT",       0.94),
            # ── WEB_SEARCH ──────────────────────────────────────────────────
            ("查下明天北京天气",         "WEB_SEARCH",  0.96),
            ("今天A股涨了吗",            "WEB_SEARCH",  0.95),
            ("现在美元汇率多少",         "WEB_SEARCH",  0.94),
            ("最新的iPhone多少钱",       "WEB_SEARCH",  0.92),
            ("查一下去上海的高铁票",     "WEB_SEARCH",  0.93),
            # ── FILE_GEN ────────────────────────────────────────────────────
            ("帮我做一个PPT",            "FILE_GEN",    0.92),
            ("帮我写一份Word文档",       "FILE_GEN",    0.91),
            ("做一个关于AI的介绍PDF",    "FILE_GEN",    0.90),
            ("生成一份竞品分析报告",     "FILE_GEN",    0.89),
            ("做一个关于春节习俗的Excel","FILE_GEN",    0.88),
            # ── DOC_ANNOTATE ────────────────────────────────────────────────
            ("[FILE_ATTACHED:.docx] 把所有不合适的翻译标注改善", "DOC_ANNOTATE", 0.95),
            ("[FILE_ATTACHED:.docx] 润色这篇论文",              "DOC_ANNOTATE", 0.94),
            ("[FILE_ATTACHED:.docx] 帮我修改语序不通的地方",    "DOC_ANNOTATE", 0.93),
            ("帮我优化这段代码的写法",   "DOC_ANNOTATE", 0.87),
            ("[FILE_ATTACHED:.py] 帮我找出这段代码里的bug",     "DOC_ANNOTATE", 0.90),
            ("[FILE_ATTACHED:.txt] 这篇文章语言太生硬，帮我润色", "DOC_ANNOTATE", 0.91),
            # ── CODER ───────────────────────────────────────────────────────
            ("写一个快速排序函数",               "CODER", 0.95),
            ("用Python实现文件批量重命名",        "CODER", 0.94),
            ("帮我写一个爬虫脚本",               "CODER", 0.93),
            ("实现一个二叉树的遍历",             "CODER", 0.92),
            ("给我写一段Python代码",             "CODER", 0.95),
            ("帮我写一个冒泡排序",               "CODER", 0.95),
            ("实现一个登录功能的后端接口",        "CODER", 0.93),
            ("写一个爬取京东商品价格的脚本",      "CODER", 0.94),
            ("帮我用JavaScript实现一个轮播图",   "CODER", 0.93),
            ("写一个把CSV转Excel的Python脚本",   "CODER", 0.94),
            ("帮我实现一个二分查找算法",          "CODER", 0.93),
            ("写一段读取JSON文件的代码",          "CODER", 0.93),
            ("给我一个Flask的Hello World示例代码","CODER", 0.92),
            ("帮我写一个自动发邮件的Python脚本",  "CODER", 0.93),
            ("实现一个简单的计算器程序",          "CODER", 0.93),
            # ── PAINTER ─────────────────────────────────────────────────────
            ("画一只猫",                "PAINTER",     0.95),
            ("帮我生成一张封面图片",     "PAINTER",     0.93),
            ("生成一个科技感背景图",     "PAINTER",     0.91),
            ("帮我画一张宣传海报",       "PAINTER",     0.93),
            ("生成一张二次元风格的头像", "PAINTER",     0.92),
            ("画一幅中国山水画",         "PAINTER",     0.90),
            ("生成一张产品展示图",       "PAINTER",     0.91),
            # ── RESEARCH ────────────────────────────────────────────────────
            ("帮我深入研究MicroLED技术原理",       "RESEARCH", 0.92),
            ("全面分析GPT-4和Claude的差异",        "RESEARCH", 0.91),
            ("系统研究量子计算的发展历程",          "RESEARCH", 0.90),
            ("帮我深入研究量子计算",               "RESEARCH", 0.91),
            ("全面分析特斯拉的竞争优势和风险",      "RESEARCH", 0.91),
            ("系统介绍大模型微调的各种方法",        "RESEARCH", 0.90),
            ("详尽研究中美贸易战的历史和影响",      "RESEARCH", 0.90),
            ("深入分析比特币的技术实现原理",        "RESEARCH", 0.91),
            ("全面评估新能源汽车行业的投资价值",    "RESEARCH", 0.90),
            ("帮我系统梳理机器学习各算法的优缺点",  "RESEARCH", 0.91),
            # ── FILE_SEARCH ─────────────────────────────────────────────────
            ("帮我找一下简历文件",               "FILE_SEARCH", 0.93),
            ("全盘扫描我的电脑",                 "FILE_SEARCH", 0.95),
            ("找一下2025年的报告文件",           "FILE_SEARCH", 0.92),
            ("在我的电脑上找一个叫项目计划的文件", "FILE_SEARCH", 0.93),
            ("搜索我桌面上的PDF文件",             "FILE_SEARCH", 0.92),
            ("找一下我上个月下载的合同",          "FILE_SEARCH", 0.93),
            ("帮我找到去年的财务报告",            "FILE_SEARCH", 0.92),
            ("在D盘找所有Excel文件",              "FILE_SEARCH", 0.94),
            ("搜索文件名包含'报价单'的文档",       "FILE_SEARCH", 0.93),
            ("找找我的工资条在哪里",              "FILE_SEARCH", 0.92),
            # ── CHAT ────────────────────────────────────────────────────────
            ("你好，介绍一下你自己",     "CHAT",        0.97),
            ("什么是机器学习",           "CHAT",        0.95),
            ("如何学好Python",           "CHAT",        0.94),
            ("帮我讲讲区块链",           "CHAT",        0.93),
            ("写一段自我介绍",           "CHAT",        0.90),
            ("今天工作压力好大",         "CHAT",        0.92),
            ("git怎么用",               "CHAT",        0.91),
            ("如何写一个排序算法",        "CHAT",        0.93),  # 求知识 → CHAT
            ("什么是快速排序",           "CHAT",        0.95),  # 概念问答 → CHAT
            ("Python怎么安装第三方库",   "CHAT",        0.93),  # 知识问题 → CHAT
            ("docker是什么",             "CHAT",        0.94),  # 概念解释 → CHAT
            ("如何实现一个登录功能",      "CHAT",        0.91),  # 求知识，非产出代码 → CHAT
            ("研究一下Python",           "CHAT",        0.90),  # 口语"研究一下" → CHAT
            ("给我解释一下什么是递归",   "CHAT",        0.93),
            ("帮我分析一下这个问题的原因","CHAT",        0.91),
            # ── 文件附件判断（关键边界用例）────────────────────────────────
            ("[FILE_ATTACHED:.pdf] 告诉我这份文件的核心观点",   "CHAT",        0.93),
            ("[FILE_ATTACHED:.pdf] 这份商业计划书值得投资吗",   "CHAT",        0.92),
            ("[FILE_ATTACHED:.pdf] 帮我把这份材料做成一份PPT", "FILE_GEN",    0.91),
            ("[FILE_ATTACHED:.pdf] 深入研究这家公司的财务状况","RESEARCH",    0.90),
            # ── 容易混淆的边界用例 ──────────────────────────────────────────
            ("深入研究Python的GIL机制原理",   "RESEARCH",   0.91),   # 明确信号词 → RESEARCH
            ("帮我制作一份Word版简历",        "FILE_GEN",   0.92),   # 明确格式词 → FILE_GEN
            ("写一段简历的自我介绍",          "CHAT",       0.91),   # 短文本，无格式词 → CHAT
            ("写一个排序算法",               "CODER",      0.93),   # 要代码 → CODER
            ("打开微信然后给我妈发消息",      "AGENT",      0.90),   # 复合动作 → AGENT
            ("帮我写一个函数实现字符串翻转",  "CODER",      0.94),   # 写函数 → CODER
            ("在哪里找到系统的hosts文件",     "CHAT",       0.91),   # 知识问答 → CHAT
            ("帮我找一个叫main.py的文件",     "FILE_SEARCH",0.93),   # 文件搜索 → FILE_SEARCH
        ]

        samples: List[TrainingSample] = []
        for user_text, task_type, confidence in SYNTHETIC_EXAMPLES:
            answer = json.dumps({"task": task_type, "confidence": confidence}, ensure_ascii=False)
            samples.append(TrainingSample(
                system=_ROUTER_SYSTEM,
                user=user_text,
                assistant=answer,
                task_type=task_type,
                source="synthetic",
                quality=0.95,
            ))
        return samples

    @classmethod
    def _generate_synthetic_chat_samples(cls) -> List[TrainingSample]:
        """
        合成对话样本：示范在非系统相关查询中不主动提及系统信息。
        这些样本用于降低本地模型在通用对话中报告 CPU/内存/磁盘的频率。
        """
        # (user_input, assistant_response) —— 回复中均不含多余系统信息
        CHAT_BEHAVIOR_EXAMPLES = [
            (
                "什么是机器学习？",
                "机器学习是让计算机从数据中自动学习规律并做出预测的技术，分为监督学习、无监督学习和强化学习三大类。"
            ),
            (
                "Python 里的列表和元组有什么区别？",
                "主要区别：列表（list）可变，可以增删改元素；元组（tuple）不可变，创建后不能修改。元组通常用于不需要修改的数据集合，且访问速度略快。"
            ),
            (
                "帮我解释一下递归是什么",
                "递归是函数调用自身的编程技术。需要两个条件：基准情况（终止条件）和递归情况（向基准靠近的调用）。经典例子是阶乘：`factorial(n) = n * factorial(n-1)`，直到 `n=1` 时返回 1。"
            ),
            (
                "git rebase 和 git merge 怎么选？",
                "简单规则：\n- **merge**：保留完整历史，适合公共分支合并\n- **rebase**：线性整洁历史，适合个人功能分支整理\n\n公共分支上**避免 rebase**，因为会改写提交历史影响他人。"
            ),
            (
                "帮我写一段 Python 快速排序",
                "```python\ndef quicksort(arr):\n    if len(arr) <= 1:\n        return arr\n    pivot = arr[len(arr) // 2]\n    left = [x for x in arr if x < pivot]\n    mid  = [x for x in arr if x == pivot]\n    right = [x for x in arr if x > pivot]\n    return quicksort(left) + mid + quicksort(right)\n```"
            ),
            (
                "最近压力好大，有什么放松的建议吗？",
                "几个有效的放松方式：\n1. **短暂休息** - 每工作 45~60 分钟起身活动 5 分钟\n2. **深呼吸 / 冥想** - 4-7-8 呼吸法能快速缓解焦虑\n3. **运动** - 哪怕 15 分钟散步也能明显改善情绪\n4. **断开通知** - 专注工作时关闭不必要提醒，减少打断感"
            ),
            (
                "如何学好英语口语？",
                "核心建议：\n1. **大量输入** - 每天听真实英语内容（播客、视频）至少 30 分钟\n2. **开口练习** - 用 AI 对话工具或找语伴每天说英语，哪怕 10 分钟\n3. **模仿跟读** - 选喜欢的视频跟读，注意语调和停顿\n4. **不要怕犯错** - 流利比正确更重要，犯错是进步的必经过程"
            ),
            (
                "解释一下 Docker 是什么",
                "Docker 是一个容器化平台，让你把应用及其依赖打包进一个轻量的「容器」里运行。容器之间相互隔离，但共享宿主机的操作系统内核，比虚拟机更轻量快速。核心好处：**一次构建，到处运行**，解决「在我电脑上没问题」的环境问题。"
            ),
            (
                "怎么提高写作能力？",
                "几个实用方法：\n1. **多读** - 读优质文章，留意结构和用词\n2. **每天写** - 哪怕 200 字日记，保持输出习惯\n3. **先完成再完善** - 初稿不追求完美，写完再修改\n4. **找人反馈** - 让别人指出不清楚的地方\n5. **模仿好文** - 仿写你欣赏的文章风格"
            ),
            (
                "正则表达式怎么匹配邮箱？",
                "常用邮箱匹配正则：\n```\n^[\\w.+-]+@[\\w-]+\\.[\\w.-]+$\n```\nPython 示例：\n```python\nimport re\npattern = r'^[\\w.+-]+@[\\w-]+\\.[\\w.-]+$'\nprint(bool(re.match(pattern, 'user@example.com')))  # True\n```\n注意：邮箱格式复杂，这个正则覆盖绝大多数情况但非 100% 严格。"
            ),
        ]

        samples: List[TrainingSample] = []
        for user_text, assistant_text in CHAT_BEHAVIOR_EXAMPLES:
            samples.append(TrainingSample(
                system=_CHAT_SYSTEM,
                user=user_text,
                assistant=assistant_text,
                task_type="CHAT",
                source="synthetic_behavior",
                quality=0.92,
            ))
        return samples


    # 推送到 Ollama
    # ══════════════════════════════════════════════════════════════════

    @classmethod
    def _push_to_ollama_if_available(
        cls,
        routing_file: Path,
        full_file: Path,
        verbose: bool = True,
    ):
        """
        如果 Ollama 正在运行，生成 Modelfile 并提示用户运行微调命令。
        实际 LoRA 训练通过 lora_pipeline.py 执行；这里只生成 Modelfile。
        """
        try:
            import requests as _req
            resp = _req.get("http://localhost:11434/api/tags", timeout=2)
            if resp.status_code != 200:
                return
            models = [m["name"] for m in resp.json().get("models", [])]
        except Exception:
            if verbose:
                print("[TrainingBuilder] ℹ️ Ollama 未运行，跳过 Modelfile 生成")
            return

        # 找到可用的基底模型（动态评分选择）
        base_model = None
        try:
            from app.core.routing.local_model_router import LocalModelRouter
            base_model = LocalModelRouter.pick_best_chat_model(models)
        except Exception:
            pass
        if not base_model and models:
            base_model = models[0]
        if not base_model:
            if verbose:
                print("[TrainingBuilder] ⚠️ Ollama 无可用模型，跳过")
            return

        # 生成 Modelfile
        modelfile_content = f"""FROM {base_model}

SYSTEM \"\"\"
你是 Koto，一个基于本地大模型的 AI 助手。
你既可以作为任务路由分类器（输出 JSON），也可以直接回答用户问题。
路由格式: {{"task":"TYPE","confidence":0.9}}
可用类型: CHAT CODER PAINTER FILE_GEN DOC_ANNOTATE RESEARCH WEB_SEARCH FILE_SEARCH SYSTEM AGENT
\"\"\"

PARAMETER temperature 0.1
PARAMETER num_predict 50
PARAMETER num_ctx 4096
"""

        out_dir = full_file.parent
        modelfile_path = out_dir / "Koto_Router.Modelfile"
        modelfile_path.write_text(modelfile_content, encoding="utf-8")

        # 生成训练脚本
        train_script = out_dir / "run_ollama_train.bat"
        train_script.write_text(
            f"@echo off\n"
            f"echo [Koto] 正在创建 Koto-Router 模型...\n"
            f"ollama create koto-router -f \"{modelfile_path}\"\n"
            f"echo [Koto] koto-router 模型已创建！\n"
            f"echo [Koto] 训练数据位于: {routing_file}\n"
            f"pause\n",
            encoding="gbk"
        )

        if verbose:
            print(f"[TrainingBuilder] 🤖 Ollama 已运行，检测到模型: {base_model}")
            print(f"[TrainingBuilder] 📄 Modelfile 已生成: {modelfile_path}")
            print(f"[TrainingBuilder] ▶️ 运行 {train_script} 创建 koto-router 模型")
            print(f"[TrainingBuilder] 📊 路由训练数据: {routing_file}")

    # ══════════════════════════════════════════════════════════════════
    # 工具方法
    # ══════════════════════════════════════════════════════════════════

    @classmethod
    def _extract_text(cls, turn: Dict) -> str:
        parts = turn.get("parts", [])
        if not parts:
            return ""
        return str(parts[0]).strip()

    @classmethod
    def _is_valid_sample(cls, user_text: str, model_text: str) -> bool:
        if len(user_text) < cls.MIN_INPUT_LEN:
            return False
        if len(model_text) < cls.MIN_RESPONSE_LEN:
            return False
        if len(model_text) > cls.MAX_RESPONSE_LEN:
            model_text = model_text[:cls.MAX_RESPONSE_LEN]  # 截断不丢弃
        # 过滤错误消息
        if model_text.strip().startswith("❌ 发生错误") or "地区限制" in model_text:
            return False
        return True

    @classmethod
    def _estimate_quality(cls, user_text: str, model_text: str, task_type: str) -> float:
        """根据简单启发式规则估算样本质量"""
        score = 0.7
        # 较长且结构化的回复质量更高
        if len(model_text) > 200:
            score += 0.05
        if len(model_text) > 500:
            score += 0.05
        # Markdown 格式更好
        if "###" in model_text or "**" in model_text or "- " in model_text:
            score += 0.05
        # 回复中有代码块
        if "```" in model_text and task_type == "CODER":
            score += 0.08
        # 用户输入过短（可能是试探性输入）
        if len(user_text) < 10:
            score -= 0.1
        return min(max(score, 0.0), 1.0)

    @classmethod
    def _deduplicate(cls, samples: List[TrainingSample]) -> List[TrainingSample]:
        """基于 (system, user) 去重，保留质量最高的"""
        seen: Dict[Tuple[str, str], TrainingSample] = {}
        for s in samples:
            key = (s.system[:50], s.user[:100])
            if key not in seen or s.quality > seen[key].quality:
                seen[key] = s
        return list(seen.values())

    @classmethod
    def _write_jsonl(cls, path: Path, samples: List[TrainingSample], fmt: str = "ollama"):
        """写入 JSONL 文件"""
        if not samples:
            path.write_text("", encoding="utf-8")
            return
        lines = []
        for s in samples:
            if fmt == "ollama":
                lines.append(s.to_ollama_jsonl())
            else:
                lines.append(s.to_qwen3_jsonl())
        path.write_text("\n".join(lines) + "\n", encoding="utf-8")
        logger.info(f"[TrainingBuilder] 写入 {len(lines)} 条 → {path.name}")

    @classmethod
    def get_stats(cls) -> Dict[str, Any]:
        """获取已生成的训练数据统计"""
        _OUT_DIR.mkdir(parents=True, exist_ok=True)
        stats_files = sorted(_OUT_DIR.glob("stats_*.json"), reverse=True)
        if not stats_files:
            return {"total": 0, "message": "尚未生成训练数据，请运行 build_all()"}
        latest = json.loads(stats_files[0].read_text(encoding="utf-8"))
        latest["history_count"] = len(stats_files)
        return latest


# ══════════════════════════════════════════════════════════════════
# Flask API 注册（供 web/app.py 调用）
# ══════════════════════════════════════════════════════════════════

def register_training_routes(app):
    """将训练数据 API 注册到 Flask app"""
    from flask import jsonify, request as flask_request

    @app.route("/api/training/build", methods=["POST"])
    def training_build():
        """触发训练数据构建"""
        import threading
        opts = flask_request.json or {}
        result_holder = {}

        def _run():
            try:
                result_holder["result"] = TrainingDataBuilder.build_all(
                    include_routing=opts.get("include_routing", True),
                    include_chat=opts.get("include_chat", True),
                    include_shadow=opts.get("include_shadow", True),
                    include_synthetic=opts.get("include_synthetic", True),
                    include_memory=opts.get("include_memory", True),
                    min_quality=opts.get("min_quality", 0.5),
                    verbose=True,
                )
                result_holder["status"] = "ok"
            except Exception as e:
                result_holder["status"] = "error"
                result_holder["error"] = str(e)

        t = threading.Thread(target=_run, daemon=True)
        t.start()
        t.join(timeout=60)  # 最多等 60s

        if result_holder.get("status") == "ok":
            return jsonify({"success": True, "data": result_holder["result"]})
        else:
            return jsonify({"success": False, "error": result_holder.get("error", "timeout")}), 500

    @app.route("/api/training/stats", methods=["GET"])
    def training_stats():
        """获取训练数据统计"""
        return jsonify(TrainingDataBuilder.get_stats())

    # ── 评分 API ─────────────────────────────────────────────────────────────

    @app.route("/api/response/rate", methods=["POST"])
    def response_rate():
        """
        用户为一条 AI 回复打分（1-5星）。
        Body: {msg_id, stars, comment?, session_name?, user_input?, ai_response?, task_type?}
        也接受不带 msg_id 的请求（此时自动计算 msg_id）。
        """
        try:
            from app.core.learning.rating_store import get_rating_store, RatingStore
            data = flask_request.json or {}
            stars = int(data.get("stars", 0))
            if not 1 <= stars <= 5:
                return jsonify({"success": False, "error": "stars 必须在 1~5 之间"}), 400

            session_name = data.get("session_name", "")
            user_input   = data.get("user_input", "")
            ai_response  = data.get("ai_response", "")
            task_type    = data.get("task_type", "CHAT")
            comment      = data.get("comment", "")
            msg_id       = data.get("msg_id") or RatingStore.make_msg_id(session_name, user_input)

            rs = get_rating_store()
            rs.save_user_rating(
                msg_id=msg_id, stars=stars, comment=comment,
                session_name=session_name, user_input=user_input,
                ai_response=ai_response, task_type=task_type,
            )

            # 高分 → 自动触发 ShadowTracer
            if stars >= 4 and user_input and ai_response:
                try:
                    from app.core.learning.shadow_tracer import ShadowTracer
                    ShadowTracer.record_approved(
                        session_id=session_name,
                        user_input=user_input,
                        ai_response=ai_response,
                        task_type=task_type,
                    )
                except Exception:
                    pass

            combined = rs.combined_score(msg_id)
            return jsonify({"success": True, "msg_id": msg_id,
                            "combined_score": combined})
        except Exception as e:
            return jsonify({"success": False, "error": str(e)}), 500

    @app.route("/api/ratings/stats", methods=["GET"])
    def ratings_stats():
        """双轨评分统计（用户 + 模型自评）。"""
        try:
            from app.core.learning.rating_store import get_rating_store
            return jsonify(get_rating_store().get_stats())
        except Exception as e:
            return jsonify({"error": str(e)}), 500

    @app.route("/api/ratings/sample/<msg_id>", methods=["GET"])
    def rating_sample(msg_id: str):
        """获取单条消息的双轨评分详情。"""
        try:
            from app.core.learning.rating_store import get_rating_store
            rs = get_rating_store()
            return jsonify({
                "user_rating":  rs.user_rating_for(msg_id),
                "model_eval":   rs.model_eval_for(msg_id),
                "combined":     rs.combined_score(msg_id),
            })
        except Exception as e:
            return jsonify({"error": str(e)}), 500

    @app.route("/api/training/push-ollama", methods=["POST"])
    def training_push_ollama():
        """手动触发 Ollama 模型创建"""
        _OUT_DIR.mkdir(parents=True, exist_ok=True)
        routing_files = sorted(_OUT_DIR.glob("koto_routing_*.jsonl"), reverse=True)
        full_files    = sorted(_OUT_DIR.glob("koto_full_*.jsonl"),    reverse=True)

        if not routing_files or not full_files:
            return jsonify({"success": False, "error": "请先运行 /api/training/build 生成数据"}), 400

        TrainingDataBuilder._push_to_ollama_if_available(
            routing_files[0], full_files[0], verbose=True
        )
        return jsonify({"success": True, "message": "Modelfile 已生成，查看 workspace/training_data/"})

    print("[TrainingAPI] ✅ 训练数据 API 已注册: /api/training/*")


# ══════════════════════════════════════════════════════════════════
# CLI 入口
# ══════════════════════════════════════════════════════════════════

if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description="Koto 本地模型训练数据生成器")
    parser.add_argument("--min-quality", type=float, default=0.5, help="最低质量阈值")
    parser.add_argument("--no-synthetic", action="store_true", help="不生成合成数据")
    parser.add_argument("--no-shadow",    action="store_true", help="不加载 shadow_traces")
    args = parser.parse_args()

    result = TrainingDataBuilder.build_all(
        include_synthetic=not args.no_synthetic,
        include_shadow=not args.no_shadow,
        min_quality=args.min_quality,
        verbose=True,
    )
    print(f"\n✅ 完成！训练数据已保存到: {result['full_file']}")
    print(f"   路由样本: {result['stats']['routing_samples']} 条")
    print(f"   对话样本: {result['stats']['chat_samples']} 条")
    print(f"   合计:     {result['stats']['total']} 条")
