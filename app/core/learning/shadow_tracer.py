# -*- coding: utf-8 -*-
"""
╔══════════════════════════════════════════════════════════════════╗
║   Koto  ─  Shadow Tracing 影子记录机制（自进化飞轮入口）           ║
╚══════════════════════════════════════════════════════════════════╝

职责
────
静默记录被用户认可的高质量对话和任务流，作为后续 LoRA 微调的训练数据。

设计原则
────────
1. 零感知   — 用户无感，在后台异步追加写入，不影响主对话响应速度
2. 隐私优先 — 写入前自动调用 PIIFilter 脱敏，不存储原始 PII
3. 用户控制 — recording_enabled 开关，用户可随时在设置中关闭
4. 幂等安全 — 使用 trace_id（UUID）避免重复记录
5. 阈值触发 — 当某 Skill 积累到 shadow_threshold 条记录时，
               触发 TrainingReadyEvent 通知，外部可监听并启动 LoRA 训练

触发方式
────────
    # 方式 A：用户点赞
    ShadowTracer.record_approved(session_id, user_input, ai_response, skill_id="summarize_doc")

    # 方式 B：用户采纳（复制/下载/继续对话）
    ShadowTracer.record_adopted(session_id, user_input, ai_response)

    # 方式 C：显式提交整个对话流
    ShadowTracer.record_workflow(session_id, steps, skill_id="code_review")

存储结构
────────
    workspace/shadow_traces/
    ├── {skill_id}.jsonl          ← 每个 Skill 一个文件
    ├── _general.jsonl            ← 无明确 Skill 的高质量对话
    └── _manifest.json            ← 各 Skill 记录数统计
"""

from __future__ import annotations

import json
import logging
import re
import threading
import uuid
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

from app.core.security.pii_filter import PIIFilter, PIIConfig

logger = logging.getLogger(__name__)


# ══════════════════════════════════════════════════════════════════
# 枚举 / 事件
# ══════════════════════════════════════════════════════════════════

class TraceEvent:
    """由外部监听的事件常量"""
    TRAINING_READY = "training_ready"   # Skill 积累数量达到阈值


# ══════════════════════════════════════════════════════════════════
# 数据结构
# ══════════════════════════════════════════════════════════════════

@dataclass
class TraceRecord:
    """
    一条影子记录。写入 JSONL 时使用 JSON 序列化。

    Attributes:
        trace_id     : UUID，用于去重
        session_id   : 对话 session 标识
        skill_id     : 触发此对话的 Skill ID（如有）
        task_type    : 路由分类结果（CHAT/CODER 等）
        user_input   : 脱敏后的用户输入
        ai_response  : 脱敏后的 AI 响应（云端最终返回）
        feedback     : "thumbs_up" | "adopted" | "workflow_complete"
        steps        : 多步任务流步骤（Workflow 场景使用）
        model_used   : 使用的模型名称（e.g. gemini-2.5-pro）
        latency_ms   : 响应延迟（毫秒）
        timestamp    : ISO8601 UTC 时间戳
        metadata     : 额外元数据（工具调用记录等）
    """
    trace_id: str = field(default_factory=lambda: str(uuid.uuid4()))
    session_id: str = ""
    skill_id: Optional[str] = None
    task_type: Optional[str] = None
    user_input: str = ""
    ai_response: str = ""
    feedback: str = "thumbs_up"
    steps: List[Dict[str, Any]] = field(default_factory=list)
    model_used: str = ""
    latency_ms: Optional[int] = None
    timestamp: str = field(
        default_factory=lambda: datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    )
    metadata: Dict[str, Any] = field(default_factory=dict)

    def to_jsonl_line(self) -> str:
        return json.dumps(asdict(self), ensure_ascii=False)


# ══════════════════════════════════════════════════════════════════
# 核心：ShadowTracer
# ══════════════════════════════════════════════════════════════════

class ShadowTracer:
    """
    影子记录器。所有写入操作均异步（后台线程），不阻塞主线程。

    类变量（全进程共享）
    ────────────────────
    recording_enabled  : 全局开关，False 时所有 record_* 方法立即返回
    shadow_threshold   : 达到此数量时触发 TrainingReadyEvent（默认 5，每 5 条触发一次）
    _listeners         : 事件监听回调列表
    """

    recording_enabled: bool = True
    shadow_threshold: int = 5   # 每积累 5 条即触发一次训练（可按需调整）

    # PII 过滤配置（关闭 IP 过滤，保留语义；其他全开）
    _pii_config = PIIConfig(mask_ip=False, log_stats=False)

    _listeners: List[Callable[[str, str, int], None]] = []  # (event, skill_id, count)
    _write_lock = threading.Lock()

    # ── 工作目录 ───────────────────────────────────────────────────
    @classmethod
    def _traces_dir(cls) -> Path:
        """shadow_traces 目录（workspace/shadow_traces/）"""
        import sys
        if getattr(sys, 'frozen', False):
            project_root = Path(sys.executable).parent
        else:
            here = Path(__file__).resolve()
            project_root = here.parents[3]
        d = project_root / "workspace" / "shadow_traces"
        d.mkdir(parents=True, exist_ok=True)
        return d

    @classmethod
    def _trace_file(cls, skill_id: Optional[str]) -> Path:
        name = skill_id if skill_id else "_general"
        # 安全化文件名
        safe_name = re.sub(r"[^a-zA-Z0-9_\-]", "_", name) if name else "_general"
        return cls._traces_dir() / f"{safe_name}.jsonl"

    @classmethod
    def _manifest_file(cls) -> Path:
        return cls._traces_dir() / "_manifest.json"

    # ── 公开 API ────────────────────────────────────────────────────

    @classmethod
    def record_approved(
        cls,
        session_id: str,
        user_input: str,
        ai_response: str,
        skill_id: Optional[str] = None,
        task_type: Optional[str] = None,
        model_used: str = "",
        latency_ms: Optional[int] = None,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> Optional[str]:
        """
        用户点赞时调用。异步记录，返回 trace_id（或 None 若已禁用）。
        """
        return cls._enqueue(
            session_id=session_id,
            user_input=user_input,
            ai_response=ai_response,
            skill_id=skill_id,
            task_type=task_type,
            feedback="thumbs_up",
            model_used=model_used,
            latency_ms=latency_ms,
            metadata=metadata or {},
        )

    @classmethod
    def record_adopted(
        cls,
        session_id: str,
        user_input: str,
        ai_response: str,
        skill_id: Optional[str] = None,
        task_type: Optional[str] = None,
        model_used: str = "",
        latency_ms: Optional[int] = None,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> Optional[str]:
        """
        用户采纳响应时调用（如复制内容、下载文件、继续基于此回复对话）。
        比 thumbs_up 更隐式的积极信号。
        """
        return cls._enqueue(
            session_id=session_id,
            user_input=user_input,
            ai_response=ai_response,
            skill_id=skill_id,
            task_type=task_type,
            feedback="adopted",
            model_used=model_used,
            latency_ms=latency_ms,
            metadata=metadata or {},
        )

    @classmethod
    def record_workflow(
        cls,
        session_id: str,
        steps: List[Dict[str, Any]],
        skill_id: Optional[str] = None,
        task_type: Optional[str] = None,
        model_used: str = "",
        summary_input: str = "",
        summary_output: str = "",
        metadata: Optional[Dict[str, Any]] = None,
    ) -> Optional[str]:
        """
        完整工作流完成时调用。steps 包含各步的 input/output/tool_calls。
        """
        return cls._enqueue(
            session_id=session_id,
            user_input=summary_input,
            ai_response=summary_output,
            skill_id=skill_id,
            task_type=task_type,
            feedback="workflow_complete",
            model_used=model_used,
            steps=steps,
            metadata=metadata or {},
        )

    @classmethod
    def get_counts(cls) -> Dict[str, int]:
        """
        返回各 Skill 的记录条数 {skill_id: count}。
        读取 manifest 文件，不遍历 JSONL（高效）。
        """
        try:
            mf = cls._manifest_file()
            if mf.exists():
                with open(mf, "r", encoding="utf-8") as f:
                    return json.load(f)
        except Exception as e:
            logger.warning(f"[ShadowTracer] 读取 manifest 失败: {e}")
        return {}

    @classmethod
    def get_traces(
        cls,
        skill_id: Optional[str] = None,
        limit: int = 100,
    ) -> List[Dict[str, Any]]:
        """
        读取指定 Skill 的影子记录（用于微调 pipeline 消费）。

        Args:
            skill_id: None 表示读取 _general.jsonl
            limit   : 最多返回条数（从最新开始）

        Returns:
            记录列表（dict 格式）
        """
        trace_file = cls._trace_file(skill_id)
        if not trace_file.exists():
            return []
        try:
            lines = trace_file.read_text(encoding="utf-8").strip().split("\n")
            records = []
            for line in reversed(lines):  # 最新的在前
                if line.strip():
                    try:
                        records.append(json.loads(line))
                    except json.JSONDecodeError:
                        pass
                if len(records) >= limit:
                    break
            return records
        except Exception as e:
            logger.error(f"[ShadowTracer] 读取影子记录失败: {e}")
            return []

    @classmethod
    def add_listener(cls, callback: Callable[[str, str, int], None]):
        """
        注册事件监听器。
        callback(event: str, skill_id: str, count: int)
        当某 Skill 记录数达到 shadow_threshold 时触发 TraceEvent.TRAINING_READY
        """
        cls._listeners.append(callback)

    @classmethod
    def clear_traces(cls, skill_id: Optional[str] = None):
        """清除指定 Skill 的影子记录（测试用）"""
        tf = cls._trace_file(skill_id)
        if tf.exists():
            tf.unlink()
        cls._update_manifest(skill_id or "_general", 0)

    # ── 内部实现 ────────────────────────────────────────────────────

    @classmethod
    def _enqueue(cls, **kwargs) -> Optional[str]:
        """异步提交记录任务"""
        if not cls.recording_enabled:
            return None

        # 分配 trace_id
        trace_id = str(uuid.uuid4())

        # 后台线程执行（不阻塞调用方）
        t = threading.Thread(
            target=cls._write_record,
            kwargs={"trace_id": trace_id, **kwargs},
            daemon=True,
            name=f"shadow-tracer-{trace_id[:8]}",
        )
        t.start()
        return trace_id

    @classmethod
    def _write_record(
        cls,
        trace_id: str,
        session_id: str,
        user_input: str,
        ai_response: str,
        skill_id: Optional[str],
        task_type: Optional[str],
        feedback: str,
        model_used: str,
        latency_ms: Optional[int] = None,
        steps: Optional[List[Dict]] = None,
        metadata: Optional[Dict] = None,
    ):
        """在后台线程中脱敏 + 写入 JSONL"""
        try:
            # PII 脱敏（不存储原始敏感信息）
            masked_input = PIIFilter.mask(user_input, cls._pii_config).masked_text
            masked_response = PIIFilter.mask(ai_response, cls._pii_config).masked_text

            record = TraceRecord(
                trace_id=trace_id,
                session_id=session_id,
                skill_id=skill_id,
                task_type=task_type,
                user_input=masked_input,
                ai_response=masked_response,
                feedback=feedback,
                steps=steps or [],
                model_used=model_used,
                latency_ms=latency_ms,
                metadata=metadata or {},
            )

            trace_file = cls._trace_file(skill_id)
            with cls._write_lock:
                with open(trace_file, "a", encoding="utf-8") as f:
                    f.write(record.to_jsonl_line() + "\n")

            # 更新 manifest 统计
            count = cls._increment_manifest(skill_id or "_general")

            logger.debug(
                f"[ShadowTracer] 📝 记录 [{feedback}] skill={skill_id or '_general'} "
                f"count={count} trace_id={trace_id[:8]}"
            )

            # 检查是否达到训练阈值
            if count >= cls.shadow_threshold and count % cls.shadow_threshold == 0:
                cls._fire_training_ready(skill_id or "_general", count)

        except Exception as e:
            logger.error(f"[ShadowTracer] 写入记录失败: {e}", exc_info=True)

    @classmethod
    def _increment_manifest(cls, key: str) -> int:
        """线程安全地递增 manifest 中的计数，返回新值"""
        try:
            mf = cls._manifest_file()
            with cls._write_lock:
                data = {}
                if mf.exists():
                    try:
                        data = json.loads(mf.read_text(encoding="utf-8"))
                    except Exception:
                        data = {}
                data[key] = data.get(key, 0) + 1
                mf.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
                return data[key]
        except Exception as e:
            logger.warning(f"[ShadowTracer] manifest 更新失败: {e}")
            return 0

    @classmethod
    def _update_manifest(cls, key: str, value: int):
        """直接设置 manifest 中某 key 的值"""
        try:
            mf = cls._manifest_file()
            data = {}
            if mf.exists():
                data = json.loads(mf.read_text(encoding="utf-8"))
            data[key] = value
            mf.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
        except Exception as e:
            logger.warning(f"[ShadowTracer] manifest 设置失败: {e}")

    @classmethod
    def _fire_training_ready(cls, skill_id: str, count: int):
        """触发 TRAINING_READY 事件，通知所有监听器"""
        logger.info(
            f"[ShadowTracer] 🚀 技能 [{skill_id}] 已积累 {count} 条高质量记录，"
            f"达到微调阈值 ({cls.shadow_threshold})，可以启动 LoRA 训练！"
        )
        for listener in cls._listeners:
            try:
                listener(TraceEvent.TRAINING_READY, skill_id, count)
            except Exception as e:
                logger.warning(f"[ShadowTracer] 事件监听器异常: {e}")



