import json
import logging
import os
import time

from flask import Blueprint, Response, jsonify, request, stream_with_context

from app.core.agent.factory import create_agent
from app.core.agent.types import AgentStepType
from app.core.config_defaults import DEFAULT_MODEL

logger = logging.getLogger(__name__)

agent_bp = Blueprint("agent", __name__)


# ── v2 护栏模块（懒加载）──────────────────────────────────────────────────────
def _lazy_pii():
    from app.core.security.pii_filter import PIIFilter

    return PIIFilter


def _lazy_validator():
    from app.core.security.output_validator import OutputValidator

    return OutputValidator


def _lazy_tracer():
    from app.core.learning.shadow_tracer import ShadowTracer

    return ShadowTracer


def _make_eval_llm_fn():
    """后台自评用 LLM 函数（复用 Agent 的 Gemini provider，不另起连接）。"""
    try:
        _a = get_agent()

        def _fn(prompt: str) -> str:
            try:
                r = _a.llm_provider.generate_content(
                    prompt,
                    model=DEFAULT_MODEL,
                    max_tokens=512,
                    temperature=0.1,
                )
                return r.get("content", "") if isinstance(r, dict) else str(r)
            except Exception:
                return ""

        return _fn
    except Exception:
        return lambda _: ""


# ── 503 / 连接故障 → 本地模型兜底 ────────────────────────────────────────────


def _is_service_unavailable_error(text: str) -> bool:
    """检测是否为 503 / 网络连接故障，用于判断是否启用本地模型兜底。"""
    t = (text or "").lower()
    return any(
        sig in t
        for sig in (
            "503",
            "service unavailable",
            "unavailable",
            "overloaded",
            "connection error",
            "timed out",
            "timeout",
            "resource_exhausted",
            "high demand",
            "serviceunavailable",
        )
    )


def _build_skill_system_instruction(
    user_input: str = "", task_type: str = "CHAT"
) -> str:
    """
    构建注入了当前激活 Skills 的系统指令。
    供本地模型兜底路径使用，确保本地模型也能理解并遵循用户启用的 Skill。
    """
    _base = (
        "你是 Koto，一个友善、专业的 AI 助手。"
        "请用中文回答，内容准确简洁。"
        "如果不确定答案，请诚实说明不确定。"
    )
    try:
        from app.core.skills.skill_manager import SkillManager

        # 自动匹配补充：当用户没有手动启用 Skill 时推荐合适的临时 Skill
        _auto_ids: list = []
        try:
            from app.core.skills.skill_auto_matcher import SkillAutoMatcher

            _auto_ids = SkillAutoMatcher.match(
                user_input=user_input, task_type=task_type
            )
        except Exception:
            pass
        return SkillManager.inject_into_prompt(
            _base,
            task_type=task_type,
            user_input=user_input,
            temp_skill_ids=_auto_ids,
        )
    except Exception as _e:
        logger.debug(f"[local_fallback] Skill 注入跳过: {_e}")
        return _base


def _local_model_fallback(user_message: str, history: list = None) -> tuple:
    """
    尝试调用本地 Ollama 模型回答用户问题。
    返回 (answer_text, model_name)，或 (None, None) 当本地模型不可用时。
    当前激活的 Skills 会注入到系统指令中，本地模型与云端模型行为保持一致。
    """
    try:
        import requests as _req

        from app.core.routing.local_model_router import LocalModelRouter

        if not LocalModelRouter.is_ollama_available():
            logger.info("[fallback] 本地 Ollama 不可用，跳过兜底")
            return None, None

        if not LocalModelRouter.init_model():
            logger.info("[fallback] 本地模型初始化失败，跳过兜底")
            return None, None

        model_name = getattr(LocalModelRouter, "_model_name", None)
        if not model_name:
            return None, None

        # ── 注入 Skills 到系统指令 ──────────────────────────────────────────
        system_instruction = _build_skill_system_instruction(
            user_input=user_message, task_type="CHAT"
        )
        active_skill_names = []
        try:
            from app.core.skills.skill_manager import SkillManager

            active_skill_names = SkillManager.get_active_skill_names()
        except Exception:
            pass
        if active_skill_names:
            logger.info(f"[fallback] 本地模型携带 Skills: {active_skill_names}")

        # ── 构建对话历史（过滤掉系统快照等噪音） ────────────────────────────
        messages = [{"role": "system", "content": system_instruction}]
        for msg in history or []:
            role = msg.get("role", "user")
            content = msg.get("content", "")
            if role == "model":
                role = "assistant"
            if (
                role in ("user", "assistant")
                and content
                and not content.startswith("Session context:")
            ):
                messages.append({"role": role, "content": content})
        messages.append({"role": "user", "content": user_message})

        resp = _req.post(
            "http://localhost:11434/api/chat",
            json={
                "model": model_name,
                "messages": messages,
                "stream": False,
                "options": {
                    "temperature": 0.7,
                    "num_predict": 2048,
                },
            },
            timeout=60,
        )

        if resp.status_code != 200:
            logger.warning(f"[fallback] Ollama 返回 HTTP {resp.status_code}")
            return None, None

        content = (resp.json().get("message", {}) or {}).get("content", "")
        return (content.strip() if content else None), model_name

    except Exception as exc:
        logger.warning(f"[fallback] 本地模型兜底调用失败: {exc}")
        return None, None


# ------------------------------------------------------------------
# Session history helpers — reuse chats/ directory for persistence
# ------------------------------------------------------------------
_CHATS_DIR = None

# In-memory LRU cache for chat history: avoids disk read on every turn.
# Keyed by session_id; stores the full raw history list (pre-truncation).
# Max 50 sessions keeps memory bounded (~few MB even with large histories).
_HISTORY_CACHE: "OrderedDict[str, list]" = None
_HISTORY_CACHE_MAX = 50
_HISTORY_CACHE_LOCK = None


def _get_history_cache():
    global _HISTORY_CACHE, _HISTORY_CACHE_LOCK
    if _HISTORY_CACHE is None:
        import threading as _threading
        from collections import OrderedDict

        _HISTORY_CACHE = OrderedDict()
        _HISTORY_CACHE_LOCK = _threading.Lock()
    return _HISTORY_CACHE, _HISTORY_CACHE_LOCK


def _get_chats_dir() -> str:
    """Lazily resolve chats/ directory (same as web/app.py uses)."""
    global _CHATS_DIR
    if _CHATS_DIR is None:
        project_root = os.path.dirname(
            os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        )
        _CHATS_DIR = os.path.join(project_root, "chats")
        os.makedirs(_CHATS_DIR, exist_ok=True)
    return _CHATS_DIR


def _load_history(session_id: str, max_turns: int = 30, token_budget: int = 4096):
    """Load recent history from chats/<session_id>.json, compatible with
    SessionManager format {role, parts}. Converts to agent-compatible
    {role, content} dicts."""
    if not session_id:
        return []
    fname = session_id if session_id.endswith(".json") else f"{session_id}.json"
    path = os.path.join(_get_chats_dir(), fname)
    if not os.path.exists(path):
        return []
    try:
        with open(path, "r", encoding="utf-8", errors="ignore") as f:
            raw = json.load(f)
        # Convert {role, parts} → {role, content} for the last max_turns messages
        converted = []
        for msg in raw[-max_turns:]:
            role = msg.get("role", "user")
            parts = msg.get("parts", [])
            content = parts[0] if parts else msg.get("content", "")
            converted.append({"role": role, "content": content})
        # Apply token budget: iterate newest-first, stop when budget overflows
        budget_used = 0
        selected = []
        for msg in reversed(converted):
            est = max(1, len(msg.get("content", "")) // 4)
            if budget_used + est > token_budget and selected:
                break
            selected.insert(0, msg)
            budget_used += est
        logger.debug(
            f"[_load_history] {len(selected)}/{len(converted)} msgs kept, ~{budget_used} est. tokens"
        )
        return selected
    except Exception as exc:
        logger.warning(f"Failed to load history for {session_id}: {exc}")
        return []


def _get_tracker_path(session_id: str) -> str:
    """Return path to the per-session ConversationTracker JSON file."""
    safe_id = (session_id or "").replace(".json", "").strip()
    return os.path.join(_get_chats_dir(), f"{safe_id}.tracker.json")


def _save_history(session_id: str, user_msg: str, model_msg: str):
    """Append a turn (user + model) to chats/<session_id>.json in
    SessionManager-compatible format. Also updates the in-memory cache."""
    if not session_id:
        return
    fname = session_id if session_id.endswith(".json") else f"{session_id}.json"
    path = os.path.join(_get_chats_dir(), fname)
    try:
        cache, lock = _get_history_cache()
        with lock:
            cached = cache.get(session_id)
        if cached is not None:
            history = list(cached)
        elif os.path.exists(path):
            with open(path, "r", encoding="utf-8", errors="ignore") as f:
                history = json.load(f)
        else:
            history = []
        history.append({"role": "user", "parts": [user_msg]})
        history.append({"role": "model", "parts": [model_msg]})
        with open(path, "w", encoding="utf-8") as f:
            json.dump(history, f, indent=2, ensure_ascii=False)
        # Update cache with new history
        with lock:
            cache[session_id] = history
            cache.move_to_end(session_id)
            if len(cache) > _HISTORY_CACHE_MAX:
                cache.popitem(last=False)
    except Exception as exc:
        logger.warning(f"Failed to save history for {session_id}: {exc}")


# ------------------------------------------------------------------
# Phase3: Session state snapshots for cross-turn system context reuse
# ------------------------------------------------------------------
_SYSTEM_TOOL_TO_STATE_KEY = {
    "query_cpu_status": "cpu",
    "query_memory_status": "memory",
    "query_disk_usage": "disk",
    "query_network_status": "network",
    "query_python_env": "python_env",
    "list_running_apps": "processes",
    "get_system_warnings": "warnings",
}


def _get_state_path(session_id: str) -> str:
    """Get path for session state snapshot file."""
    safe_id = (session_id or "").replace(".json", "").strip()
    return os.path.join(_get_chats_dir(), f"{safe_id}.state.json")


def _load_session_state(session_id: str) -> dict:
    """Load session state snapshot containing system info summary."""
    if not session_id:
        return {"system_snapshot": {}, "updated_at": None}
    path = _get_state_path(session_id)
    if not os.path.exists(path):
        return {"system_snapshot": {}, "updated_at": None}
    try:
        with open(path, "r", encoding="utf-8", errors="ignore") as f:
            data = json.load(f)
        if not isinstance(data, dict):
            return {"system_snapshot": {}, "updated_at": None}
        data.setdefault("system_snapshot", {})
        data.setdefault("updated_at", None)
        return data
    except Exception as exc:
        logger.warning(f"Failed to load session state for {session_id}: {exc}")
        return {"system_snapshot": {}, "updated_at": None}


def _save_session_state(session_id: str, state: dict):
    """Save session state snapshot."""
    if not session_id:
        return
    path = _get_state_path(session_id)
    try:
        with open(path, "w", encoding="utf-8") as f:
            json.dump(state, f, indent=2, ensure_ascii=False)
    except Exception as exc:
        logger.warning(f"Failed to save session state for {session_id}: {exc}")


def _parse_observation_json(text: str):
    """Try to parse JSON from observation text."""
    if not isinstance(text, str):
        return None
    content = text.strip()
    if not content or content[0] not in ("{", "["):
        return None
    try:
        return json.loads(content)
    except Exception:
        return None


def _merge_system_snapshot_from_steps(session_state: dict, steps_payload: list):
    """Extract system tool results from steps and merge into state snapshot."""
    if not isinstance(session_state, dict):
        session_state = {"system_snapshot": {}, "updated_at": None}
    snapshot = session_state.get("system_snapshot") or {}

    last_tool_name = None
    for step in steps_payload or []:
        step_type = str(step.get("step_type", "")).lower()
        if step_type == "action":
            action = step.get("action") or {}
            last_tool_name = action.get("tool_name")
            continue

        if step_type != "observation" or not last_tool_name:
            continue

        state_key = _SYSTEM_TOOL_TO_STATE_KEY.get(last_tool_name)
        if not state_key:
            continue

        obs_text = step.get("observation") or step.get("content") or ""
        obs_data = _parse_observation_json(obs_text)
        snapshot[state_key] = {
            "tool": last_tool_name,
            "captured_at": int(time.time()),
            "data": obs_data if obs_data is not None else {"raw": str(obs_text)[:1200]},
        }

    session_state["system_snapshot"] = snapshot
    session_state["updated_at"] = int(time.time())
    return session_state


def _build_snapshot_context_text(session_state: dict) -> str:
    """Build human-readable context string from system snapshot."""
    snapshot = (session_state or {}).get("system_snapshot") or {}
    if not snapshot:
        return ""

    lines = [
        "Session context: latest local system snapshot (may be stale, use tools if needed):"
    ]
    for key in [
        "cpu",
        "memory",
        "disk",
        "network",
        "python_env",
        "processes",
        "warnings",
    ]:
        item = snapshot.get(key)
        if not item:
            continue
        data = item.get("data")
        if isinstance(data, dict):
            compact = json.dumps(data, ensure_ascii=False)[:280]
        elif isinstance(data, list):
            compact = json.dumps(data, ensure_ascii=False)[:280]
        else:
            compact = str(data)[:280]
        lines.append(f"- {key}: {compact}")

    return "\n".join(lines)


# ------------------------------------------------------------------
# Agent instance management
# ------------------------------------------------------------------
_agent_instance = None


def get_agent():
    global _agent_instance
    if _agent_instance is None:
        _agent_instance = create_agent()
    return _agent_instance


def _resolve_runtime_skill(
    user_input: str,
    explicit_skill_id: str = None,
    task_type: str = None,
):
    """Resolve a per-request skill from explicit input first, then intent bindings."""
    if explicit_skill_id:
        return explicit_skill_id, [explicit_skill_id]

    try:
        from app.core.skills.skill_manager import SkillManager
        from app.core.skills.skill_trigger_binding import get_skill_binding_manager

        matched_ids = get_skill_binding_manager().match_intent(user_input or "")
        if not matched_ids:
            return None, []

        SkillManager._ensure_init()
        candidates = []
        for skill_id in matched_ids:
            skill_def = SkillManager.get_definition(skill_id)
            if not skill_def:
                continue
            # Intent bindings are user-triggered signals; skip task_type gate so that
            # domain skills (e.g. annotate_* with task_types=["DOC_ANNOTATE"]) can
            # still be injected when the route task_type is "CHAT".
            candidates.append(skill_def)

        if not candidates:
            return None, []

        candidates.sort(key=lambda skill: getattr(skill, "priority", 50), reverse=True)
        return candidates[0].id, [skill.id for skill in candidates]
    except Exception as exc:
        logger.debug(f"[agent_routes] 运行时技能解析跳过: {exc}")
        return explicit_skill_id, [explicit_skill_id] if explicit_skill_id else []


def _run_agent_collect(
    agent,
    message,
    history=None,
    session_id: str = None,
    skill_id: str = None,
    task_type: str = None,
):
    """Run agent once and collect steps/final answer for sync APIs."""
    steps_payload = []
    final_answer = ""

    for step in agent.run(
        input_text=message,
        history=history or [],
        session_id=session_id,
        skill_id=skill_id,
        task_type=task_type,
    ):
        step_data = step.to_dict()
        steps_payload.append(step_data)
        if step.step_type == AgentStepType.ANSWER:
            final_answer = step.content or ""

    if not final_answer and steps_payload:
        final_answer = steps_payload[-1].get("content", "")

    return {
        "id": f"task_{int(time.time() * 1000)}",
        "status": "success",
        "result": final_answer,
        "steps": steps_payload,
    }


@agent_bp.route("/chat", methods=["POST"])
def chat():
    data = request.json
    message = data.get('message')
    session_id = data.get('session_id') or data.get('session', '')
    history = data.get('history') or _load_history(session_id)
    model_id = data.get('model', 'gemini-3-flash-preview')
    skill_id = data.get('skill_id')          # v2: 关联的 Skill ID
    task_type = data.get('task_type')         # v2: 任务分类

    if not message:
        return jsonify({"error": "Message is required"}), 400

    # ── v4: 载入对话跟踪器 ─────────────────────────────────────────
    _tracker = None
    _tracker_path = ""
    try:
        from app.core.memory.conversation_tracker import ConversationTracker
        _tracker_path = _get_tracker_path(session_id)
        _tracker = ConversationTracker.load(_tracker_path)
    except Exception as _te:
        logger.debug(f"[chat] ConversationTracker 加载跳过: {_te}")

    # ── v4: 意图分析与重写 (IntentAnalyzer) ────────────────────
    _rewritten_message = message
    try:
        from app.core.routing.intent_analyzer import IntentAnalyzer
        if IntentAnalyzer.should_analyze(message):
            _rw = IntentAnalyzer.rewrite_intent(message, history, _tracker)
            if _rw and _rw != message:
                logger.info(f"[chat] 意图重写: '{message[:40]}' -> '{_rw[:60]}'")
                _rewritten_message = _rw
    except Exception as _ia_err:
        logger.debug(f"[chat] IntentAnalyzer 跳过: {_ia_err}")

    # ── v4: ContextWindowManager (MemGPT 历史压缩)──────────────────
    _cw_paged_context = ""
    try:
        from app.core.memory.context_window_manager import ContextWindowManager
        _cw_out = ContextWindowManager.manage(
            history=history,
            query=_rewritten_message,
            session_name=(session_id or "").replace(".json", ""),
            get_memory_fn=lambda: None,
        )
        history = _cw_out["history"]
        _cw_paged_context = _cw_out.get("paged_in_context", "")
    except Exception as _cw_err:
        logger.debug(f"[chat] ContextWindowManager 跳过: {_cw_err}")

    # ── 构建 system_context 注入块 ──────────────────────────────────────────
    _system_ctx_parts = []
    if _tracker is not None:
        _ctx_inj = _tracker.get_context_injection()
        if _ctx_inj:
            _system_ctx_parts.append(_ctx_inj)
    if _cw_paged_context:
        _system_ctx_parts.append(_cw_paged_context)
    _system_context = "\n\n".join(_system_ctx_parts) if _system_ctx_parts else None

    # Phase3: load system state snapshot and inject into history
    session_state = _load_session_state(session_id)
    snapshot_ctx = _build_snapshot_context_text(session_state)
    if snapshot_ctx:
        history = (history or []) + [{"role": "model", "content": snapshot_ctx}]
    

    skill_id, auto_skill_ids = _resolve_runtime_skill(_rewritten_message, skill_id, task_type)

    agent = get_agent()
    if agent.model_id != model_id:
        agent.model_id = model_id

    # ── v2: PII 脱敏 ─────────────────────────────────────────────────────────
    mask_result = None
    safe_message = _rewritten_message
    try:
        PIIFilter = _lazy_pii()
        mask_result = PIIFilter.mask(_rewritten_message)
        if mask_result.has_pii:
            safe_message = mask_result.masked_text
            logger.info(f"[chat] 🔒 PII 脱敏 {mask_result.stats}")
    except Exception as _e:
        logger.warning(f"[chat] PII 过滤异常（跳过）: {_e}")

    def generate():
        collected_steps = []
        final_answer = ""
        used_local_fallback = False
        local_fallback_model = None
        _t_start = time.time()
        try:
            for step in agent.run(
                input_text=safe_message,
                history=history,
                session_id=session_id,
                skill_id=skill_id,
                task_type=task_type,
                system_context=_system_context,
            ):
                step_data = step.to_dict()
                collected_steps.append(step_data)
                if step.step_type == AgentStepType.ANSWER:
                    final_answer = step.content or ""
                yield f"data: {json.dumps({'type': 'agent_step', 'data': step_data}, ensure_ascii=False)}\n\n"

            if not final_answer and collected_steps:
                final_answer = collected_steps[-1].get("content", "")

            # ── 503 / 连接故障：本地模型兜底 ────────────────────────────────
            _error_steps = [s for s in collected_steps if s.get("step_type") == "error"]
            if _error_steps and _is_service_unavailable_error(
                _error_steps[-1].get("content", "")
            ):
                logger.warning("[chat] 检测到云端连接故障（503），尝试本地模型兜底")
                _notice = {
                    "step_type": "thought",
                    "content": "⚠️ 云端服务暂时不可用，正在切换到本地模型处理您的请求...",
                    "metadata": {"source": "local_fallback"},
                }
                yield f"data: {json.dumps({'type': 'agent_step', 'data': _notice}, ensure_ascii=False)}\n\n"
                _local_ans, _local_mod = _local_model_fallback(safe_message, history)
                if _local_ans:
                    final_answer = _local_ans
                    used_local_fallback = True
                    local_fallback_model = _local_mod
                    logger.info(
                        f"[chat] 本地模型兜底成功（{_local_mod}），响应长度: {len(_local_ans)}"
                    )
                else:
                    final_answer = (
                        "⚠️ 云端服务暂时不可用（503），本地模型也无法访问，请稍后重试。"
                    )

            # ── v2: 输出质量验收 ──────────────────────────────────────────────
            validated_answer = final_answer
            validation_action = "PASS"
            try:
                if final_answer and not used_local_fallback:
                    OutputValidator = _lazy_validator()
                    val = OutputValidator.validate(
                        text=final_answer,
                        skill_id=skill_id,
                        original_prompt=message,
                    )
                    validation_action = val.action
                    if val.is_blocked:
                        validated_answer = val.text
                        logger.warning(f"[chat] 🚫 输出被拦截: {val.reasons}")
                    else:
                        validated_answer = val.text
            except Exception as _ve:
                logger.warning(f"[chat] 输出验收异常（跳过）: {_ve}")

            # ── v2: PII 还原 ──────────────────────────────────────────────────
            display_answer = validated_answer
            if mask_result and mask_result.has_pii:
                try:
                    display_answer = mask_result.restore(validated_answer)
                except Exception:
                    pass

            # ── 本地模型兜底提示前缀 ─────────────────────────────────────────
            if used_local_fallback:
                _lm = local_fallback_model or "本地模型"
                display_answer = (
                    f"🔄 **[本地模型回复]** 云端服务暂时不可用，"
                    f"以下回答由本地 AI（`{_lm}`）提供，能力可能弱于云端：\n\n"
                    f"{display_answer}"
                )

            # ── Skill 推荐提示 ────────────────────────────────────────────────
            # 在回答末尾追加相关但未启用的 Skill 推荐，
            # 帮助用户发现可以增强本类任务体验的专项技能。
            if display_answer and not used_local_fallback:
                try:
                    from app.core.skills.skill_suggester import SkillSuggester
                    _suggestions = SkillSuggester.suggest(
                        user_input=message or "",
                        task_type=task_type or "CHAT",
                        already_active_ids=auto_skill_ids or [],
                        answer_text=display_answer,
                    )
                    if _suggestions:
                        display_answer += SkillSuggester.format_hint(_suggestions)
                    # ── chains_to：基于本轮激活 Skill 推荐下一步 ──────────────
                    _all_active = list(set((auto_skill_ids or []) + ([skill_id] if skill_id else [])))
                    _already_ids = [s["id"] for s in _suggestions] if _suggestions else []
                    _chains = SkillSuggester.suggest_chains(
                        active_skill_ids=_all_active,
                        already_suggested_ids=_already_ids,
                    )
                    if _chains:
                        display_answer += SkillSuggester.format_chain_hint(_chains)
                except Exception as _se:
                    logger.debug(f"[chat] Skill 推荐注入跳过: {_se}")

            latency_ms = int((time.time() - _t_start) * 1000)
            task_payload = {
                "id": f"task_{int(time.time() * 1000)}",
                "status": "success",
                "result": display_answer,
                "steps": collected_steps,
                # v2 元数据
                "meta": {
                    "session_id": session_id,
                    "skill_id": skill_id,
                    "auto_skill_ids": auto_skill_ids,
                    "task_type": task_type,
                    "validation_action": validation_action,
                    "pii_masked": mask_result.has_pii if mask_result else False,
                    "latency_ms": latency_ms,
                    "model": local_fallback_model if used_local_fallback else model_id,
                    "local_fallback": used_local_fallback,
                },
            }
            yield f"data: {json.dumps({'type': 'task_final', 'data': task_payload}, ensure_ascii=False)}\n\n"

            # Persist turn to disk + phase3 state snapshot
            _save_history(
                session_id, message, display_answer or "[Agent task completed]"
            )
            merged_state = _merge_system_snapshot_from_steps(
                session_state, collected_steps
            )
            _save_session_state(session_id, merged_state)

            # ── v4: 更新对话跟踪器（异步，非阻塞）─────────────────────────
            if _tracker is not None and _tracker_path and display_answer:
                _tracker.update_async(message, display_answer, _tracker_path)

            # ── 后台自评分（数据飞轮: model_eval 通道）────────────────────────
            if display_answer and not used_local_fallback:
                try:
                    from app.core.learning.rating_store import RatingStore
                    from app.core.learning.response_evaluator import ResponseEvaluator

                    ResponseEvaluator.evaluate_async(
                        msg_id=RatingStore.make_msg_id(session_id or "", message or ""),
                        user_input=message,
                        ai_response=display_answer,
                        task_type=task_type or "CHAT",
                        session_name=session_id or "",
                        llm_fn=_make_eval_llm_fn(),
                    )
                except Exception as _ee:
                    logger.debug(f"[chat] 自评分启动失败: {_ee}")

        except Exception as e:
            logger.exception("/chat stream failed")
            _err_str = str(e)
            # ── 流异常中检测到 503：仍然尝试本地兜底 ──────────────────────
            if _is_service_unavailable_error(_err_str):
                logger.warning("[chat] 流异常中检测到 503，尝试本地模型兜底")
                _notice = {
                    "step_type": "thought",
                    "content": "⚠️ 云端服务暂时不可用，正在切换到本地模型处理您的请求...",
                    "metadata": {"source": "local_fallback"},
                }
                yield f"data: {json.dumps({'type': 'agent_step', 'data': _notice}, ensure_ascii=False)}\n\n"
                _local_ans, _local_mod = _local_model_fallback(safe_message, history)
                if _local_ans:
                    _lm = _local_mod or "本地模型"
                    _display = (
                        f"🔄 **[本地模型回复]** 云端服务不可用，"
                        f"以下由本地 AI（`{_lm}`）提供：\n\n{_local_ans}"
                    )
                    _lf_payload = {
                        "id": f"task_{int(time.time() * 1000)}",
                        "status": "success",
                        "result": _display,
                        "steps": collected_steps,
                        "meta": {
                            "session_id": session_id,
                            "skill_id": skill_id,
                            "task_type": task_type,
                            "model": _lm,
                            "local_fallback": True,
                        },
                    }
                    yield f"data: {json.dumps({'type': 'task_final', 'data': _lf_payload}, ensure_ascii=False)}\n\n"
                    _save_history(session_id, message, _local_ans)
                    return
            yield f"data: {json.dumps({'type': 'error', 'data': {'error': _err_str}}, ensure_ascii=False)}\n\n"

    return Response(stream_with_context(generate()), mimetype="text/event-stream")


@agent_bp.route("/tools", methods=["GET"])
def list_tools():
    """List available tools for the agent."""
    agent = get_agent()
    definitions = agent.registry.get_definitions()
    return jsonify(definitions)


@agent_bp.route("/process", methods=["POST"])
def process_compat():
    """Phase2 compatibility endpoint for legacy AdaptiveAgent clients."""
    data = request.json or {}
    user_request = data.get("request", "")
    session_id = data.get("session_id") or data.get("session", "")
    skill_id = data.get("skill_id")
    task_type = data.get("task_type")
    context = data.get("context", {})
    history = context.get("history", []) if isinstance(context, dict) else []

    # Phase3: load and inject system state snapshot
    session_state = _load_session_state(session_id)
    snapshot_ctx = _build_snapshot_context_text(session_state)
    if snapshot_ctx:
        history = (history or []) + [{"role": "model", "content": snapshot_ctx}]

    if not user_request:
        return jsonify({"success": False, "error": "缺少请求内容"}), 400

    skill_id, auto_skill_ids = _resolve_runtime_skill(user_request, skill_id, task_type)

    try:
        agent = get_agent()
        task = _run_agent_collect(
            agent,
            user_request,
            history=history,
            session_id=session_id,
            skill_id=skill_id,
            task_type=task_type,
        )
        task["skill_id"] = skill_id
        task["auto_skill_ids"] = auto_skill_ids
        task["task_type"] = task_type
        merged_state = _merge_system_snapshot_from_steps(
            session_state, task.get("steps", [])
        )
        _save_session_state(session_id, merged_state)
        return jsonify({"success": True, "task": task})
    except Exception as exc:
        logger.exception("/process failed")
        return jsonify({"success": False, "error": str(exc)}), 500


@agent_bp.route("/process-stream", methods=["POST"])
def process_stream_compat():
    """Phase2 compatibility SSE endpoint for legacy AdaptiveAgent clients. (v2 PII + validation)"""
    data = request.json or {}
    user_request = data.get("request", "")
    session_id = data.get("session_id") or data.get("session", "")
    skill_id = data.get("skill_id")
    task_type = data.get("task_type")
    context = data.get("context", {})
    # Prefer explicit history from request, fall back to disk
    history = (
        context.get("history", []) if isinstance(context, dict) else []
    ) or _load_history(session_id)

    # Phase3: load and inject system state snapshot
    session_state = _load_session_state(session_id)
    snapshot_ctx = _build_snapshot_context_text(session_state)
    if snapshot_ctx:
        history = (history or []) + [{"role": "model", "content": snapshot_ctx}]

    if not user_request:
        return jsonify({"success": False, "error": "缺少请求内容"}), 400

    skill_id, auto_skill_ids = _resolve_runtime_skill(user_request, skill_id, task_type)

    # ── PII 屏蔽（在发送给 Agent 前执行）──────────────────────────
    mask_result = None
    safe_request = user_request
    try:
        PIIFilter = _lazy_pii()
        mask_result = PIIFilter.mask(user_request)
        if mask_result.has_pii:
            safe_request = mask_result.masked_text
            logger.info(
                f"[process-stream] PII 屏蔽 session={session_id} "
                f"types={[e.entity_type for e in mask_result.entities]}"
            )
    except Exception as _pe:
        logger.warning(f"[process-stream] PII 过滤器初始化失败，跳过屏蔽: {_pe}")

    agent = get_agent()

    def generate():
        collected_steps = []
        raw_final = ""
        used_local_fallback = False
        local_fallback_model = None
        t0 = time.time()
        try:
            for step in agent.run(
                input_text=safe_request,
                history=history,
                session_id=session_id,
                skill_id=skill_id,
                task_type=task_type,
            ):
                step_data = step.to_dict()
                collected_steps.append(step_data)
                if step.step_type == AgentStepType.ANSWER:
                    raw_final = step.content or ""

                yield f"data: {json.dumps({'type': 'agent_step', 'data': step_data}, ensure_ascii=False)}\n\n"

            if not raw_final and collected_steps:
                raw_final = collected_steps[-1].get("content", "")

            # ── 503 / 连接故障：本地模型兜底 ────────────────────────────────
            _error_steps = [s for s in collected_steps if s.get("step_type") == "error"]
            if _error_steps and _is_service_unavailable_error(
                _error_steps[-1].get("content", "")
            ):
                logger.warning(
                    "[process-stream] 检测到云端连接故障（503），尝试本地模型兜底"
                )
                _notice = {
                    "step_type": "thought",
                    "content": "⚠️ 云端服务暂时不可用，正在切换到本地模型处理您的请求...",
                    "metadata": {"source": "local_fallback"},
                }
                yield f"data: {json.dumps({'type': 'agent_step', 'data': _notice}, ensure_ascii=False)}\n\n"
                _local_ans, _local_mod = _local_model_fallback(safe_request, history)
                if _local_ans:
                    raw_final = _local_ans
                    used_local_fallback = True
                    local_fallback_model = _local_mod
                    logger.info(f"[process-stream] 本地模型兜底成功（{_local_mod}）")
                else:
                    raw_final = (
                        "⚠️ 云端服务暂时不可用（503），本地模型也无法访问，请稍后重试。"
                    )

            # ── 输出校验 ─────────────────────────────────────────
            latency_ms = int((time.time() - t0) * 1000)
            validation_action = "PASS"
            try:
                if not used_local_fallback:
                    OutputValidator = _lazy_validator()
                    val_result = OutputValidator.validate(
                        raw_final,
                        skill_id=skill_id,
                        original_prompt=user_request,
                    )
                    validation_action = val_result.action
                    if validation_action == "BLOCK":
                        raw_final = "[内容被安全策略拦截，请调整您的请求]"
                    elif validation_action in ("WARN", "REFORMAT"):
                        raw_final = val_result.text or raw_final
            except Exception as _ve:
                logger.warning(f"[process-stream] 输出校验失败: {_ve}")

            # ── PII 还原 ──────────────────────────────────────────
            final_answer = raw_final
            if mask_result and mask_result.has_pii:
                try:
                    final_answer = mask_result.restore(raw_final)
                except Exception:
                    pass

            # ── 本地模型兜底提示前缀 ─────────────────────────────────────────
            if used_local_fallback:
                _lm = local_fallback_model or "本地模型"
                final_answer = (
                    f"🔄 **[本地模型回复]** 云端服务暂时不可用，"
                    f"以下回答由本地 AI（`{_lm}`）提供，能力可能弱于云端：\n\n"
                    f"{final_answer}"
                )

            task_payload = {
                "id": f"task_{int(time.time() * 1000)}",
                "status": "success",
                "result": final_answer,
                "steps": collected_steps,
                "meta": {
                    "session_id": session_id,
                    "skill_id": skill_id,
                    "auto_skill_ids": auto_skill_ids,
                    "task_type": task_type,
                    "validation_action": validation_action,
                    "pii_masked": mask_result.has_pii if mask_result else False,
                    "latency_ms": latency_ms,
                    "model": local_fallback_model if used_local_fallback else None,
                    "local_fallback": used_local_fallback,
                },
            }
            yield f"data: {json.dumps({'type': 'task_final', 'data': task_payload}, ensure_ascii=False)}\n\n"

            # Persist turn to disk + phase3 state snapshot
            _save_history(
                session_id, user_request, final_answer or "[Agent task completed]"
            )
            merged_state = _merge_system_snapshot_from_steps(
                session_state, collected_steps
            )
            _save_session_state(session_id, merged_state)

            # ── 后台自评分（数据飞轮: model_eval 通道）────────────────────────
            if final_answer and not used_local_fallback:
                try:
                    from app.core.learning.rating_store import RatingStore
                    from app.core.learning.response_evaluator import ResponseEvaluator

                    ResponseEvaluator.evaluate_async(
                        msg_id=RatingStore.make_msg_id(
                            session_id or "", user_request or ""
                        ),
                        user_input=user_request,
                        ai_response=final_answer,
                        task_type=task_type or "CHAT",
                        session_name=session_id or "",
                        llm_fn=_make_eval_llm_fn(),
                    )
                except Exception as _ee:
                    logger.debug(f"[process-stream] 自评分启动失败: {_ee}")
        except Exception as exc:
            logger.exception("/process-stream failed")
            _err_str = str(exc)
            # ── 流异常中检测到 503：仍然尝试本地兜底 ──────────────────────
            if _is_service_unavailable_error(_err_str):
                logger.warning("[process-stream] 流异常中检测到 503，尝试本地模型兜底")
                _notice = {
                    "step_type": "thought",
                    "content": "⚠️ 云端服务暂时不可用，正在切换到本地模型处理您的请求...",
                    "metadata": {"source": "local_fallback"},
                }
                yield f"data: {json.dumps({'type': 'agent_step', 'data': _notice}, ensure_ascii=False)}\n\n"
                _local_ans, _local_mod = _local_model_fallback(safe_request, history)
                if _local_ans:
                    _lm = _local_mod or "本地模型"
                    _display = (
                        f"🔄 **[本地模型回复]** 云端服务不可用，"
                        f"以下由本地 AI（`{_lm}`）提供：\n\n{_local_ans}"
                    )
                    _lf_payload = {
                        "id": f"task_{int(time.time() * 1000)}",
                        "status": "success",
                        "result": _display,
                        "steps": collected_steps,
                        "meta": {
                            "session_id": session_id,
                            "skill_id": skill_id,
                            "model": _lm,
                            "local_fallback": True,
                        },
                    }
                    yield f"data: {json.dumps({'type': 'task_final', 'data': _lf_payload}, ensure_ascii=False)}\n\n"
                    _save_history(session_id, user_request, _local_ans)
                    return
            yield f"data: {json.dumps({'type': 'error', 'data': {'error': _err_str}}, ensure_ascii=False)}\n\n"

    return Response(stream_with_context(generate()), mimetype="text/event-stream")


# ======================================================================
# Legacy compatibility routes — proxy confirm / choice to old
# agent_loop module when available, otherwise return a stub response.
# These were originally defined directly in web/app.py.
# ======================================================================


def _get_legacy_agent():
    """Try to import the old agent_loop singleton."""
    try:
        from agent_loop import get_agent_loop

        return get_agent_loop()
    except Exception:
        return None


@agent_bp.route("/confirm", methods=["POST"])
def agent_confirm():
    """User confirmation callback (legacy compat)."""
    data = request.json or {}
    session = data.get("session", "")
    confirmed = data.get("confirmed", False)

    agent = _get_legacy_agent()
    if agent is None:
        return jsonify({"success": False, "error": "Agent 尚未初始化"}), 400

    try:
        agent.submit_confirmation(session, confirmed)
        return jsonify({"success": True, "confirmed": confirmed})
    except Exception as exc:
        return jsonify({"success": False, "error": str(exc)}), 500


@agent_bp.route("/choice", methods=["POST"])
def agent_choice():
    """User choice callback (legacy compat)."""
    data = request.json or {}
    session = data.get("session", "")
    selected = data.get("selected", "")

    agent = _get_legacy_agent()
    if agent is None:
        return jsonify({"success": False, "error": "Agent 尚未初始化"}), 400

    try:
        agent.submit_choice(session, selected)
        return jsonify({"success": True, "selected": selected})
    except Exception as exc:
        return jsonify({"success": False, "error": str(exc)}), 500


@agent_bp.route("/plan", methods=["POST"])
def agent_plan():
    """Multi-step planning endpoint — uses UnifiedAgent ReAct loop with an
    explicit planning system instruction."""
    data = request.json or {}
    user_request = data.get("request", "")
    session_name = data.get("session", "")
    context = data.get("context", {})
    history = context.get("history", []) if isinstance(context, dict) else []

    if not user_request:
        return jsonify({"success": False, "error": "缺少请求内容"}), 400

    agent = get_agent()
    # Override system instruction for planning mode
    original_instruction = agent.base_system_instruction
    agent.base_system_instruction = (
        "You are Koto, an intelligent AI assistant in planning mode. "
        "Break the user's request into logical steps. For each step, think carefully, "
        "choose the right tool, execute it, and verify the result before moving on. "
        "When all steps are complete, provide a comprehensive final answer summarizing "
        "what was done and any produced results."
    )

    def generate():
        collected_steps = []
        final_answer = ""
        try:
            for step in agent.run(
                input_text=user_request,
                history=history,
                session_id=session_name,
                task_type="PLAN",
            ):
                step_data = step.to_dict()
                collected_steps.append(step_data)
                if step.step_type == AgentStepType.ANSWER:
                    final_answer = step.content or ""
                yield f"data: {json.dumps({'type': 'agent_step', 'data': step_data}, ensure_ascii=False)}\n\n"

            if not final_answer and collected_steps:
                final_answer = collected_steps[-1].get("content", "")

            task_payload = {
                "id": f"task_{int(time.time() * 1000)}",
                "status": "success",
                "result": final_answer,
                "steps": collected_steps,
            }
            yield f"data: {json.dumps({'type': 'task_final', 'data': task_payload}, ensure_ascii=False)}\n\n"
        except Exception as exc:
            logger.exception("/plan failed")
            yield f"data: {json.dumps({'type': 'error', 'data': {'error': str(exc)}}, ensure_ascii=False)}\n\n"
        finally:
            # Restore original instruction
            agent.base_system_instruction = original_instruction

    return Response(stream_with_context(generate()), mimetype="text/event-stream")


@agent_bp.route("/optimize", methods=["POST"])
def agent_optimize():
    """Phase4: System performance optimization advisor.

    Analyzes current system metrics and provides actionable optimization
    recommendations in a single turn.
    """
    data = request.json or {}
    user_request = data.get("request") or "Analyze my system and suggest optimizations"
    session_id = data.get("session_id") or data.get("session", "")
    context = data.get("context", {})
    history = context.get("history", []) if isinstance(context, dict) else []

    # Phase3: load and inject system state snapshot
    session_state = _load_session_state(session_id)
    snapshot_ctx = _build_snapshot_context_text(session_state)
    if snapshot_ctx:
        history = (history or []) + [{"role": "model", "content": snapshot_ctx}]

    agent = get_agent()
    # Override system instruction for optimization mode
    original_instruction = agent.base_system_instruction
    agent.base_system_instruction = (
        "You are a system performance optimization advisor. "
        "Analyze the current system metrics and provide specific, actionable recommendations. "
        "Use the analyze_system_performance and suggest_optimizations tools to gather data. "
        "Focus on: (1) Identifying bottlenecks, (2) Prioritizing issues by severity, "
        "(3) Providing step-by-step solutions. Be concise but thorough."
    )

    def generate():
        collected_steps = []
        final_answer = ""
        try:
            for step in agent.run(
                input_text=user_request,
                history=history,
                session_id=session_id,
                task_type="SYSTEM",
            ):
                step_data = step.to_dict()
                collected_steps.append(step_data)
                if step.step_type == AgentStepType.ANSWER:
                    final_answer = step.content or ""
                yield f"data: {json.dumps({'type': 'agent_step', 'data': step_data}, ensure_ascii=False)}\n\n"

            if not final_answer and collected_steps:
                final_answer = collected_steps[-1].get("content", "")

            task_payload = {
                "id": f"task_{int(time.time() * 1000)}",
                "status": "success",
                "result": final_answer,
                "steps": collected_steps,
            }
            yield f"data: {json.dumps({'type': 'task_final', 'data': task_payload}, ensure_ascii=False)}\n\n"

            # Persist turn to disk + phase3 state snapshot
            _save_history(
                session_id,
                user_request,
                final_answer or "[Optimization analysis completed]",
            )
            merged_state = _merge_system_snapshot_from_steps(
                session_state, collected_steps
            )
            _save_session_state(session_id, merged_state)
        except Exception as exc:
            logger.exception("/optimize failed")
            yield f"data: {json.dumps({'type': 'error', 'data': {'error': str(exc)}}, ensure_ascii=False)}\n\n"
        finally:
            # Restore original instruction
            agent.base_system_instruction = original_instruction

    return Response(stream_with_context(generate()), mimetype="text/event-stream")


# ------------------------------------------------------------------
# Phase 4b: Monitoring Control Endpoints
# ------------------------------------------------------------------


@agent_bp.route("/monitor/start", methods=["POST"])
def start_monitoring():
    """Start background system monitoring."""
    try:
        from app.core.monitoring.system_event_monitor import get_system_event_monitor

        data = request.get_json() or {}
        check_interval = data.get("check_interval", 30)

        monitor = get_system_event_monitor(check_interval=check_interval)

        if monitor.is_running():
            return jsonify(
                {
                    "status": "already_running",
                    "message": "System monitoring is already active",
                    "check_interval": monitor.check_interval,
                }
            )

        monitor.start()

        return jsonify(
            {
                "status": "success",
                "message": "System monitoring started",
                "check_interval": monitor.check_interval,
            }
        )
    except Exception as e:
        logger.error(f"Error starting monitoring: {e}", exc_info=True)
        return (
            jsonify(
                {"status": "error", "message": f"Failed to start monitoring: {str(e)}"}
            ),
            500,
        )


@agent_bp.route("/monitor/stop", methods=["POST"])
def stop_monitoring():
    """Stop background system monitoring."""
    try:
        from app.core.monitoring.system_event_monitor import get_system_event_monitor

        monitor = get_system_event_monitor()

        if not monitor.is_running():
            return jsonify(
                {
                    "status": "not_running",
                    "message": "System monitoring is not currently active",
                }
            )

        monitor.stop()

        return jsonify({"status": "success", "message": "System monitoring stopped"})
    except Exception as e:
        logger.error(f"Error stopping monitoring: {e}", exc_info=True)
        return (
            jsonify(
                {"status": "error", "message": f"Failed to stop monitoring: {str(e)}"}
            ),
            500,
        )


@agent_bp.route("/monitor/status", methods=["GET"])
def monitoring_status():
    """Get current monitoring status and event summary."""
    try:
        from app.core.monitoring.system_event_monitor import get_system_event_monitor

        monitor = get_system_event_monitor()

        return jsonify(
            {
                "status": "success",
                "monitoring_active": monitor.is_running(),
                "check_interval": (
                    monitor.check_interval if monitor.is_running() else None
                ),
                "health": monitor.get_summary(),
                "recent_events": monitor.get_events(limit=5),
            }
        )
    except Exception as e:
        logger.error(f"Error getting monitoring status: {e}", exc_info=True)
        return (
            jsonify({"status": "error", "message": f"Failed to get status: {str(e)}"}),
            500,
        )


@agent_bp.route("/monitor/events", methods=["GET"])
def get_monitoring_events():
    """Get detected anomalies from monitoring."""
    try:
        from app.core.monitoring.system_event_monitor import get_system_event_monitor

        limit = request.args.get("limit", 20, type=int)
        event_type = request.args.get("event_type", None, type=str)

        monitor = get_system_event_monitor()
        events = monitor.get_events(limit=limit, event_type=event_type)

        return jsonify(
            {
                "status": "success",
                "anomaly_count": len(events),
                "anomalies": events,
                "monitoring_active": monitor.is_running(),
            }
        )
    except Exception as e:
        logger.error(f"Error getting events: {e}", exc_info=True)
        return (
            jsonify({"status": "error", "message": f"Failed to get events: {str(e)}"}),
            500,
        )


@agent_bp.route("/monitor/clear", methods=["POST"])
def clear_monitoring_events():
    """Clear recorded anomalies from monitoring log."""
    try:
        from app.core.monitoring.system_event_monitor import get_system_event_monitor

        monitor = get_system_event_monitor()
        count = monitor.clear_events()

        return jsonify(
            {
                "status": "success",
                "message": f"Cleared {count} events from monitoring log",
            }
        )
    except Exception as e:
        logger.error(f"Error clearing events: {e}", exc_info=True)
        return (
            jsonify(
                {"status": "error", "message": f"Failed to clear events: {str(e)}"}
            ),
            500,
        )


# ------------------------------------------------------------------
# Phase 4c: Script Generation Endpoints
# ------------------------------------------------------------------


@agent_bp.route("/generate-script", methods=["POST"])
def generate_fix_script():
    """Generate an executable script to fix a detected system issue."""
    try:
        from app.core.agent.plugins.script_generation_plugin import (
            ScriptGenerationPlugin,
        )

        data = request.get_json() or {}
        issue_type = data.get("issue_type")

        if not issue_type:
            return (
                jsonify(
                    {
                        "status": "error",
                        "message": "Missing required parameter: issue_type",
                    }
                ),
                400,
            )

        plugin = ScriptGenerationPlugin()
        result = plugin.generate_fix_script(
            issue_type=issue_type,
            process_name=data.get("process_name"),
            service_name=data.get("service_name"),
            min_gb=data.get("min_gb", 5),
        )

        return jsonify(result)
    except Exception as e:
        logger.error(f"Error generating script: {e}", exc_info=True)
        return (
            jsonify(
                {"status": "error", "message": f"Failed to generate script: {str(e)}"}
            ),
            500,
        )


@agent_bp.route("/generate-script/list", methods=["GET"])
def list_available_scripts():
    """List available fix script templates."""
    try:
        from app.core.agent.plugins.script_generation_plugin import (
            ScriptGenerationPlugin,
        )

        plugin = ScriptGenerationPlugin()
        result = plugin.list_available_scripts()

        return jsonify(result)
    except Exception as e:
        logger.error(f"Error listing scripts: {e}", exc_info=True)
        return (
            jsonify(
                {"status": "error", "message": f"Failed to list scripts: {str(e)}"}
            ),
            500,
        )


@agent_bp.route("/generate-script/save", methods=["POST"])
def save_generated_script():
    """Save a generated script to workspace."""
    try:
        from app.core.agent.plugins.script_generation_plugin import (
            ScriptGenerationPlugin,
        )

        data = request.get_json() or {}
        script_content = data.get("script_content")
        filename = data.get("filename")

        if not script_content or not filename:
            return (
                jsonify(
                    {
                        "status": "error",
                        "message": "Missing required parameters: script_content, filename",
                    }
                ),
                400,
            )

        plugin = ScriptGenerationPlugin()
        result = plugin.save_script_to_file(
            script_content=script_content, filename=filename
        )

        return jsonify(result)
    except Exception as e:
        logger.error(f"Error saving script: {e}", exc_info=True)
        return (
            jsonify({"status": "error", "message": f"Failed to save script: {str(e)}"}),
            500,
        )


# ══════════════════════════════════════════════════════════════════
# v2 新增：用户反馈 / 影子记录 / 成本面板 API
# ══════════════════════════════════════════════════════════════════


@agent_bp.route("/feedback", methods=["POST"])
def submit_feedback():
    """
    用户反馈端点 — 触发 ShadowTracer 影子记录。

    请求体:
    {
      "feedback_type": "thumbs_up" | "thumbs_down" | "adopted",
      "session_id": str,
      "message_id": str (可选，用于定位对话轮次),
      "user_input": str,
      "ai_response": str,
      "skill_id": str | null,
      "task_type": str | null,
      "model_used": str | null,
      "latency_ms": int | null
    }

    响应:
    { "success": true, "trace_id": str | null, "recorded": bool }
    """
    data = request.json or {}
    feedback_type = data.get("feedback_type", "thumbs_up")
    session_id = data.get("session_id", "")
    user_input = data.get("user_input", "")
    ai_response = data.get("ai_response", "")
    skill_id = data.get("skill_id")
    task_type = data.get("task_type")
    model_used = data.get("model_used", "")
    latency_ms = data.get("latency_ms")

    if not user_input and not ai_response:
        return (
            jsonify(
                {"success": False, "error": "user_input 或 ai_response 不能同时为空"}
            ),
            400,
        )

    trace_id = None
    try:
        ShadowTracer = _lazy_tracer()
        if feedback_type in ("thumbs_up", "approved"):
            trace_id = ShadowTracer.record_approved(
                session_id=session_id,
                user_input=user_input,
                ai_response=ai_response,
                skill_id=skill_id,
                task_type=task_type,
                model_used=model_used or "",
                latency_ms=latency_ms,
            )
        elif feedback_type == "adopted":
            trace_id = ShadowTracer.record_adopted(
                session_id=session_id,
                user_input=user_input,
                ai_response=ai_response,
                skill_id=skill_id,
                task_type=task_type,
                model_used=model_used or "",
                latency_ms=latency_ms,
            )
        elif feedback_type == "thumbs_down":
            # 负面反馈不计入影子记录，仅日志
            logger.info(f"[feedback] 👎 负面反馈 session={session_id} skill={skill_id}")
        else:
            return (
                jsonify({"success": False, "error": f"未知反馈类型: {feedback_type}"}),
                400,
            )

    except Exception as e:
        logger.error(f"[feedback] 记录失败: {e}")
        return jsonify({"success": False, "error": str(e), "recorded": False}), 500

    return jsonify(
        {
            "success": True,
            "trace_id": trace_id,
            "recorded": trace_id is not None,
            "feedback_type": feedback_type,
        }
    )


@agent_bp.route("/feedback/stats", methods=["GET"])
def feedback_stats():
    """
    返回各 Skill 的影子记录统计。

    响应:
    {
      "counts": { skill_id: count, ... },
      "threshold": int,
      "skills_ready_for_training": [skill_id, ...]
    }
    """
    try:
        ShadowTracer = _lazy_tracer()
        counts = ShadowTracer.get_counts()
        threshold = ShadowTracer.shadow_threshold
        ready = [k for k, v in counts.items() if v >= threshold]
        return jsonify(
            {
                "counts": counts,
                "threshold": threshold,
                "skills_ready_for_training": ready,
                "recording_enabled": ShadowTracer.recording_enabled,
            }
        )
    except Exception as e:
        logger.error(f"[feedback/stats] 错误: {e}")
        return jsonify({"error": str(e)}), 500


@agent_bp.route("/feedback/settings", methods=["POST"])
def feedback_settings():
    """
    更新影子记录设置。

    请求体: { "recording_enabled": bool, "threshold": int (可选) }
    """
    data = request.json or {}
    try:
        ShadowTracer = _lazy_tracer()
        if "recording_enabled" in data:
            ShadowTracer.recording_enabled = bool(data["recording_enabled"])
        if "threshold" in data:
            t = int(data["threshold"])
            if 10 <= t <= 10000:
                ShadowTracer.shadow_threshold = t
        return jsonify(
            {
                "success": True,
                "recording_enabled": ShadowTracer.recording_enabled,
                "threshold": ShadowTracer.shadow_threshold,
            }
        )
    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500


# ──────────────────────────────────────────────────────────────────
# 成本 & 性能透明面板 API
# ──────────────────────────────────────────────────────────────────


@agent_bp.route("/stats/cost", methods=["GET"])
def cost_stats():
    """
    返回成本与性能统计面板数据。

    查询参数:
      period: "today" (default) | "week" | "month"
      skill_id: 指定 Skill 查看该 Skill 的使用成本（可选）

    响应包含:
    - 云端 Token 费用（USD / CNY）
    - 本地 CPU 算力消耗（如果有记录）
    - 各 Skill 的调用次数和费用
    - 每日 / 每月趋势
    """
    period = request.args.get("period", "today")
    skill_id_filter = request.args.get("skill_id")

    try:
        # 导入 token_tracker
        try:
            import os as _os
            import sys

            _wb = _os.path.join(
                _os.path.dirname(
                    _os.path.dirname(_os.path.dirname(_os.path.abspath(__file__)))
                ),
                "web",
            )
            if _wb not in sys.path:
                sys.path.insert(0, _wb)
            import token_tracker

            token_stats = token_tracker.get_stats()
        except Exception as _te:
            logger.warning(f"[cost_stats] token_tracker 不可用: {_te}")
            token_stats = {}

        # 影子记录统计（间接反映 Skill 使用量）
        try:
            ShadowTracer = _lazy_tracer()
            trace_counts = ShadowTracer.get_counts()
        except Exception as _e:
            logger.debug("[stats] trace_counts fetch failed: %s", _e)
            trace_counts = {}

        # 本地算力估算（简单 psutil）
        local_compute = {}
        try:
            import psutil

            local_compute = {
                "cpu_percent": psutil.cpu_percent(interval=0.1),
                "memory_used_mb": round(psutil.virtual_memory().used / 1024 / 1024),
                "memory_total_mb": round(psutil.virtual_memory().total / 1024 / 1024),
            }
        except Exception:
            pass

        # 组装面板数据
        panel = {
            "period": period,
            "cloud": {
                "today": token_stats.get("today", {}),
                "this_month": token_stats.get("this_month", {}),
                "last_7_days": token_stats.get("last_7_days", []),
            },
            "local_compute": local_compute,
            "skill_usage": {
                "trace_counts": trace_counts,
                "total_approved_responses": sum(trace_counts.values()),
            },
            "summary": {
                "cost_cny_today": token_stats.get("today", {}).get("cost_cny", 0),
                "cost_cny_month": token_stats.get("this_month", {}).get("cost_cny", 0),
                "calls_today": token_stats.get("today", {}).get("calls", 0),
            },
        }

        if skill_id_filter:
            panel["skill_filter"] = skill_id_filter
            panel["skill_trace_count"] = trace_counts.get(skill_id_filter, 0)

        return jsonify({"success": True, "data": panel})
    except Exception as e:
        logger.error(f"[cost_stats] 错误: {e}")
        return jsonify({"success": False, "error": str(e)}), 500


# ──────────────────────────────────────────────────────────────────
# 硬件检测 & 本地模型推荐 API
# ──────────────────────────────────────────────────────────────────


@agent_bp.route("/hardware", methods=["GET"])
def hardware_info():
    """
    检测当前设备硬件配置并返回本地模型训练/推理推荐。

    响应:
    {
      "gpu": { "name": str, "vram_gb": float, "available": bool },
      "cpu": { "cores": int },
      "ram_gb": float,
      "recommended": {
        "training_model": str,       # 推荐训练模型
        "inference_model": str,      # 推荐推理模型（Ollama）
        "gguf_size_estimate": str,   # 量化后体积估算
        "tier": str,                 # flagship / high / mid / entry / cpu_only
        "training_config": dict,     # TrainingConfig 参数
        "can_train": bool,
        "notes": str
      }
    }
    """
    import subprocess

    # ── GPU 检测 ─────────────────────────────────────────────────
    gpu_info = {"name": "Unknown", "vram_gb": 0.0, "available": False}
    try:
        result = subprocess.run(
            [
                "nvidia-smi",
                "--query-gpu=name,memory.total",
                "--format=csv,noheader,nounits",
            ],
            capture_output=True,
            text=True,
            timeout=5,
        )
        if result.returncode == 0:
            lines = [l.strip() for l in result.stdout.strip().splitlines() if l.strip()]
            if lines:
                parts = lines[0].split(",")
                gpu_info["name"] = parts[0].strip()
                gpu_info["vram_gb"] = round(int(parts[1].strip()) / 1024, 1)
                gpu_info["available"] = True
    except Exception:
        pass

    # ── CPU & RAM ────────────────────────────────────────────────
    cpu_cores = 0
    ram_gb = 0.0
    try:
        import psutil

        cpu_cores = psutil.cpu_count(logical=False) or 0
        ram_gb = round(psutil.virtual_memory().total / (1024**3), 1)
    except Exception:
        pass

    # ── 推荐逻辑 ─────────────────────────────────────────────────
    vram = gpu_info["vram_gb"]

    if vram >= 20:
        tier = "flagship"
        train_model = "Qwen/Qwen3-8B"
        infer_model = "qwen3:8b"
        gguf_est = "~5.2GB (Q4_K_M)"
        notes = (
            "RTX 4090 / A100 级别。Qwen3-8B ≈ Qwen2.5-14B 能力，128K 上下文，"
            "混合思维模式 (enable_thinking)，LoRA fp16 无压力，"
            "量化后 GGUF 约 5.2GB，可直接打包分发。"
        )
    elif vram >= 10:
        tier = "high"
        train_model = "Qwen/Qwen3-4B"
        infer_model = "qwen3:4b"
        gguf_est = "~2.6GB (Q4_K_M)"
        notes = (
            "RTX 3080/4070 级别。Qwen3-4B ≈ Qwen2.5-7B 能力，"
            "LoRA 非常流畅，量化后约 2.6GB，分发友好。"
        )
    elif vram >= 6:
        tier = "mid"
        train_model = "Qwen/Qwen3-1.7B"
        infer_model = "qwen3:1.7b"
        gguf_est = "~1.1GB (Q4_K_M)"
        notes = (
            "RTX 3060/4060 级别。Qwen3-1.7B ≈ Qwen2.5-3B 能力，"
            "QLoRA 训练稳定，速度快，适合快速迭代。"
        )
    elif vram >= 4:
        tier = "entry"
        train_model = "Qwen/Qwen3-0.6B"
        infer_model = "qwen3:0.6b"
        gguf_est = "~450MB (Q4_K_M)"
        notes = "入门独显。Qwen3-0.6B QLoRA 可跑，训练慢，适合轻量 Skill。"
    else:
        tier = "cpu_only"
        train_model = "Qwen/Qwen3-0.6B"
        infer_model = "qwen3:0.6b"
        gguf_est = "~450MB (Q4_K_M)"
        notes = "无独显环境。本地训练极慢（不推荐），建议仅做推理，训练任务交给云端。"

    # 获取对应 TrainingConfig
    training_cfg = {}
    try:
        from app.core.learning.lora_pipeline import TrainingConfig

        cfg = TrainingConfig.for_hardware(vram_gb=vram, ram_gb=ram_gb)
        training_cfg = cfg.to_dict()
    except Exception as e:
        logger.warning(f"[hardware] TrainingConfig 加载失败: {e}")

    return jsonify(
        {
            "gpu": gpu_info,
            "cpu": {"cores": cpu_cores},
            "ram_gb": ram_gb,
            "recommended": {
                "training_model": train_model,
                "inference_model": infer_model,
                "gguf_size_estimate": gguf_est,
                "tier": tier,
                "training_config": training_cfg,
                "can_train": tier != "cpu_only",
                "notes": notes,
            },
        }
    )


# ══════════════════════════════════════════════════════════════════════════════
# 蒸馏训练管理 API
# ══════════════════════════════════════════════════════════════════════════════


def _lazy_distill():
    from app.core.learning.distill_manager import DistillManager

    return DistillManager.instance()


@agent_bp.route("/distill/train", methods=["POST"])
def distill_train():
    """
    POST /api/agent/distill/train
    提交蒸馏训练任务（立即返回 job_id，后台异步训练）。

    Body (JSON):
      skill_id        - 要训练的 Skill ID（必填）
      config_override - 可选，覆盖 TrainingConfig 字段，如 {"num_epochs": 5}
      dataset_path    - 可选，指定数据集路径

    Returns:
      {"job_id": "...", "skill_id": "...", "status": "queued"}
    """
    data = request.get_json(silent=True) or {}
    skill_id = data.get("skill_id", "").strip()
    if not skill_id:
        return jsonify({"error": "skill_id 必填"}), 400

    config_override = data.get("config_override") or {}
    dataset_path = data.get("dataset_path")

    mgr = _lazy_distill()
    job_id = mgr.submit(
        skill_id=skill_id,
        config_override=config_override,
        dataset_path=dataset_path,
    )
    job = mgr.get_job(job_id)
    return jsonify(
        {
            "job_id": job_id,
            "skill_id": skill_id,
            "status": job.status if job else "queued",
            "message": "训练任务已提交，使用 GET /api/agent/distill/jobs/{job_id} 查询进度",
            "stream_url": f"/api/agent/distill/jobs/{job_id}/stream",
        }
    )


@agent_bp.route("/distill/jobs", methods=["GET"])
def distill_list_jobs():
    """
    GET /api/agent/distill/jobs[?skill_id=xxx]
    列出所有训练任务（可按 skill_id 过滤）。
    """
    skill_id = request.args.get("skill_id")
    mgr = _lazy_distill()
    jobs = mgr.list_jobs(skill_id=skill_id)
    return jsonify({"jobs": jobs, "count": len(jobs)})


@agent_bp.route("/distill/jobs/<job_id>", methods=["GET"])
def distill_job_status(job_id: str):
    """
    GET /api/agent/distill/jobs/<job_id>
    查询某个训练任务的详细状态。
    """
    mgr = _lazy_distill()
    job = mgr.get_job(job_id)
    if not job:
        return jsonify({"error": f"job_id={job_id} 不存在"}), 404
    return jsonify(job.to_dict())


@agent_bp.route("/distill/jobs/<job_id>/stream", methods=["GET"])
def distill_job_stream(job_id: str):
    """
    GET /api/agent/distill/jobs/<job_id>/stream
    SSE 实时进度流。前端用 EventSource 订阅。

    事件格式: data: {"event":"progress","pct":45,"loss":0.32,"msg":"..."}
    结束事件: data: {"event":"done","pct":100,"eval_loss":0.18,"adapter_path":"..."}
    """
    from flask import Response, stream_with_context

    mgr = _lazy_distill()
    job = mgr.get_job(job_id)
    if not job:
        return jsonify({"error": f"job_id={job_id} 不存在"}), 404

    return Response(
        stream_with_context(mgr.stream_progress(job_id)),
        mimetype="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
        },
    )


@agent_bp.route("/distill/jobs/<job_id>/cancel", methods=["POST"])
def distill_cancel_job(job_id: str):
    """
    POST /api/agent/distill/jobs/<job_id>/cancel
    取消排队中的训练任务（运行中无法取消）。
    """
    mgr = _lazy_distill()
    ok = mgr.cancel(job_id)
    if ok:
        return jsonify({"job_id": job_id, "cancelled": True})
    return jsonify({"error": "任务不存在或正在运行中，无法取消", "job_id": job_id}), 400


@agent_bp.route("/distill/prerequisites", methods=["GET"])
def distill_prerequisites():
    """
    GET /api/agent/distill/prerequisites
    检查蒸馏训练所需依赖是否已安装。
    """
    try:
        from app.core.learning.lora_pipeline import LoRAPipeline

        pipeline = LoRAPipeline()
        all_ok, missing = pipeline.check_prerequisites()
        return jsonify(
            {
                "ready": all_ok,
                "missing": missing,
                "install_cmd": (
                    (
                        "pip install peft transformers datasets accelerate trl bitsandbytes\n"
                        "pip install torch --index-url https://download.pytorch.org/whl/cu126"
                    )
                    if not all_ok
                    else None
                ),
            }
        )
    except Exception as e:
        return jsonify({"ready": False, "error": str(e)}), 500
