#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
generate_skill_match_training_data.py
======================================
为 SkillAutoMatcher 生成训练数据。

训练目标
--------
给定「任务类型 + 用户消息 + 可用技能目录」，输出最适合本轮的 Skill ID JSON 数组。
这与 generate_skill_training_data.py 中的「如何执行 Skill」训练不同，
这里训练的是：「看到用户消息 → 决定用哪个 Skill」的判断能力。

训练数据格式（JSONL，与 TRL SFTTrainer chat 格式兼容）
------------------------------------------------------
{
  "messages": [
    {"role": "system",  "content": "你是 Koto Skill 匹配引擎..."},
    {"role": "user",    "content": "任务类型: CHAT\\n用户消息: ...\\n\\n候选技能列表:\\n..."},
    {"role": "assistant","content": "[\"step_by_step\"]"}
  ]
}

用法
----
# 生成全部 32 个 Skill 的匹配训练数据（各 10 条正样本 + 5 条负样本）
python scripts/generate_skill_match_training_data.py

# 仅为新增 Skill 生成数据（例如刚注册了 my_new_skill）
python scripts/generate_skill_match_training_data.py --skills my_new_skill

# 追加到现有文件
python scripts/generate_skill_match_training_data.py --append

Options
-------
--output PATH    输出文件路径（默认 config/training_data/skill_match_samples.jsonl）
--pairs N        每个 Skill 生成几条正样本（默认 10）
--neg N          每个 Skill 生成几条负样本（默认 3）
--skills ID...   仅为指定 Skill 生成
--append         追加到现有输出文件（默认覆盖）
--resume         跳过已在输出文件中出现的 Skill
"""
import argparse
import json
import os
import re
import sys
import time
from pathlib import Path

# ── 项目根路径 ────────────────────────────────────────────────────────────────
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

# ── 技能元数据（来自 SkillManager） ──────────────────────────────────────────
def _load_skill_catalog() -> list[dict]:
    """从 SkillManager 加载完整技能目录（包含自定义 Skill）。"""
    try:
        from app.core.skills.skill_manager import SkillManager
        SkillManager._ensure_init()
        catalog = []
        for sid, s in SkillManager._registry.items():
            desc = s.get("intent_description") or s.get("description", "")
            catalog.append({
                "id": sid,
                "name": s.get("name", sid),
                "description": desc,
                "task_types": s.get("task_types", []),
                "category": s.get("category", ""),
            })
        return catalog
    except Exception as e:
        print(f"[警告] SkillManager 加载失败，使用内置列表: {e}")
        # 内置备用列表（子集）
        return [
            {"id": "step_by_step",       "name": "步骤化输出",  "description": "教程/流程类问题", "task_types": [], "category": "behavior"},
            {"id": "concise_mode",        "name": "精简模式",    "description": "快速查询/简短回答", "task_types": ["CHAT"], "category": "behavior"},
            {"id": "teaching_mode",       "name": "教学模式",    "description": "通俗讲解复杂概念", "task_types": ["CHAT", "RESEARCH"], "category": "behavior"},
            {"id": "code_best_practices", "name": "代码最佳实践","description": "写高质量代码", "task_types": ["CODER", "CHAT"], "category": "domain"},
            {"id": "security_aware",      "name": "安全意识",    "description": "安全/漏洞/加密类", "task_types": ["CODER", "SYSTEM", "CHAT"], "category": "domain"},
            {"id": "professional_tone",   "name": "专业语气",    "description": "报告/邮件/正式文档", "task_types": ["CHAT", "FILE_GEN", "RESEARCH"], "category": "style"},
            {"id": "research_depth",      "name": "深度研究",    "description": "系统性深入分析", "task_types": ["CHAT", "RESEARCH"], "category": "domain"},
            {"id": "task_planner",        "name": "任务规划",    "description": "计划/路线图/任务拆解", "task_types": [], "category": "domain"},
        ]


# ── Gemini API 调用 ────────────────────────────────────────────────────────────
def _load_gemini_key() -> str:
    env_file = ROOT / "config" / "gemini_config.env"
    if env_file.exists():
        for line in env_file.read_text(encoding="utf-8").splitlines():
            if line.strip().startswith("GEMINI_API_KEY"):
                return line.split("=", 1)[-1].strip().strip('"').strip("'")
    return os.environ.get("GEMINI_API_KEY", "")


def _fix_json_newlines(s: str) -> str:
    """修复 JSON 字符串内部的裸换行。"""
    result = []
    in_string = False
    escaped = False
    for ch in s:
        if escaped:
            result.append(ch)
            escaped = False
        elif ch == "\\":
            result.append(ch)
            escaped = True
        elif ch == '"' and not escaped:
            in_string = not in_string
            result.append(ch)
        elif in_string and ch == "\n":
            result.append("\\n")
        elif in_string and ch == "\r":
            result.append("\\r")
        else:
            result.append(ch)
    return "".join(result)


def _call_gemini(api_key: str, prompt: str, model: str = "gemini-2.5-flash") -> str:
    import urllib.request, urllib.error
    url = (
        f"https://generativelanguage.googleapis.com/v1beta/models/"
        f"{model}:generateContent?key={api_key}"
    )
    body = json.dumps({
        "contents": [{"role": "user", "parts": [{"text": prompt}]}],
        "generationConfig": {"temperature": 0.9, "maxOutputTokens": 8192},
    }).encode("utf-8")
    req = urllib.request.Request(url, data=body,
                                  headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=60) as resp:
        data = json.loads(resp.read().decode("utf-8"))
    return data["candidates"][0]["content"]["parts"][0]["text"]


# ── 提示词构建 ─────────────────────────────────────────────────────────────────
_CATALOG_SYSTEM = (
    "你是 Koto Skill 匹配引擎。根据任务类型和用户消息，从候选技能列表中选出 "
    "0-3 个最合适的技能 ID。严格只输出 JSON 数组，如 [\"step_by_step\"] 或 []，"
    "禁止任何额外文字。"
)


def _build_catalog_text(catalog: list[dict], task_type: str) -> str:
    tt = task_type.upper()
    lines = []
    for s in catalog:
        applicable = s.get("task_types", [])
        if applicable and tt and tt not in applicable:
            continue
        lines.append(f"  • {s['id']} ({s['name']}): {s['description']}")
    return "\n".join(lines)


def _generate_match_samples(
    api_key: str,
    skill: dict,
    catalog: list[dict],
    num_positive: int = 10,
    num_negative: int = 3,
) -> list[dict]:
    """
    为一个 Skill 生成：
    - num_positive 条正样本（消息应匹配该 Skill）
    - num_negative 条负样本（消息不应匹配该 Skill）

    返回 list of chat-format dict
    """
    skill_id = skill["id"]
    skill_name = skill["name"]
    skill_desc = skill["description"]
    # 确定适用的任务类型
    applicable = skill.get("task_types", []) or ["CHAT", "CODER", "RESEARCH"]
    # 如果没有限制则默认 CHAT
    sample_task_types = applicable[:3] if applicable else ["CHAT"]

    total = num_positive + num_negative

    catalog_for_prompt = json.dumps(
        [{"id": s["id"], "name": s["name"], "description": s["description"]}
         for s in catalog],
        ensure_ascii=False, indent=2
    )

    prompt = f"""你是训练数据生成器，为 Koto Skill 匹配模型生成训练样本。

目标 Skill:
  id: {skill_id}
  name: {skill_name}
  description: {skill_desc}
  适用任务类型: {sample_task_types}

可用技能目录（JSON）:
{catalog_for_prompt}

任务：
1. 生成 {num_positive} 条「正样本」：用户消息明显需要此技能（预期输出数组中含 {skill_id}）
2. 生成 {num_negative} 条「负样本」：用户消息不需要此技能（预期输出数组为 [] 或不含 {skill_id}）
3. 每条样本随机选一个任务类型：{sample_task_types}
4. 用户消息要自然多样（不同长度、中英文、正式/口语）

严格输出 JSON 数组，每项格式：
{{
  "task_type": "CHAT",
  "user_input": "...用户消息...",
  "expected": ["skill_id_1"]  // 空=[] 也可以
}}

总计输出 {total} 条，直接输出 JSON 数组，不要任何前缀或后缀文字:"""

    raw = _call_gemini(api_key, prompt)
    # 移除 markdown fence
    raw = re.sub(r"^```[a-z]*\n?", "", raw.strip())
    raw = re.sub(r"\n?```$", "", raw.strip())
    raw = _fix_json_newlines(raw.strip())

    items = json.loads(raw)
    if not isinstance(items, list):
        return []

    samples = []
    for item in items:
        if not isinstance(item, dict):
            continue
        task_type = item.get("task_type", "CHAT")
        user_input = item.get("user_input", "").strip()
        expected = item.get("expected", [])
        if not user_input:
            continue
        if not isinstance(expected, list):
            expected = [expected] if expected else []

        # 构建 catalog text for this task_type
        catalog_text = _build_catalog_text(catalog, task_type)

        samples.append({
            "messages": [
                {"role": "system", "content": _CATALOG_SYSTEM},
                {
                    "role": "user",
                    "content": (
                        f"任务类型: {task_type}\n"
                        f"用户消息: {user_input}\n\n"
                        f"候选技能列表:\n{catalog_text}"
                    ),
                },
                {
                    "role": "assistant",
                    "content": json.dumps(expected, ensure_ascii=False),
                },
            ],
            # 元数据（不进入训练，仅供检查）
            "_meta": {
                "skill_id": skill_id,
                "task_type": task_type,
                "is_positive": skill_id in expected,
            },
        })

    return samples


# ── 主程序 ────────────────────────────────────────────────────────────────────
def main():
    parser = argparse.ArgumentParser(description="生成 Skill 匹配训练数据")
    parser.add_argument("--output", default="config/training_data/skill_match_samples.jsonl")
    parser.add_argument("--pairs", type=int, default=10, help="每个 Skill 生成几条正样本")
    parser.add_argument("--neg",   type=int, default=3,  help="每个 Skill 生成几条负样本")
    parser.add_argument("--skills", nargs="+", help="仅为指定 Skill ID 生成")
    parser.add_argument("--append", action="store_true", help="追加到现有文件")
    parser.add_argument("--resume", action="store_true", help="跳过已生成的 Skill")
    args = parser.parse_args()

    api_key = _load_gemini_key()
    if not api_key:
        print("❌ 未找到 GEMINI_API_KEY，请检查 config/gemini_config.env")
        sys.exit(1)

    output_path = ROOT / args.output
    output_path.parent.mkdir(parents=True, exist_ok=True)

    # 已生成的 skill_id（用于 --resume）
    already_done: set[str] = set()
    if args.resume and output_path.exists():
        with open(output_path, encoding="utf-8") as f:
            for line in f:
                try:
                    obj = json.loads(line)
                    meta = obj.get("_meta", {})
                    if meta.get("skill_id"):
                        already_done.add(meta["skill_id"])
                except Exception:
                    pass
        print(f"[Resume] 已跳过 {len(already_done)} 个 Skill: {sorted(already_done)}")

    # 加载技能目录
    catalog = _load_skill_catalog()
    print(f"📚 技能目录已加载: {len(catalog)} 个 Skill")

    # 过滤目标 Skill
    if args.skills:
        target_skills = [s for s in catalog if s["id"] in args.skills]
        not_found = set(args.skills) - {s["id"] for s in target_skills}
        if not_found:
            print(f"⚠️  未找到以下 Skill（已忽略）: {not_found}")
    else:
        target_skills = catalog

    if args.resume:
        target_skills = [s for s in target_skills if s["id"] not in already_done]

    print(f"🎯 待生成: {len(target_skills)} 个 Skill × {args.pairs} 正样本 + {args.neg} 负样本")

    mode = "a" if args.append or args.resume else "w"
    total_written = 0
    failed = []

    with open(output_path, mode, encoding="utf-8") as f:
        for i, skill in enumerate(target_skills, 1):
            skill_id = skill["id"]
            print(f"\n[{i}/{len(target_skills)}] 生成 {skill_id} ({skill['name']})...", end="", flush=True)
            try:
                samples = _generate_match_samples(
                    api_key, skill, catalog,
                    num_positive=args.pairs,
                    num_negative=args.neg,
                )
                for sample in samples:
                    f.write(json.dumps(sample, ensure_ascii=False) + "\n")
                total_written += len(samples)
                print(f" ✅ {len(samples)} 条")
            except Exception as e:
                print(f" ❌ 失败: {e}")
                failed.append(skill_id)
            time.sleep(0.5)  # 避免触发 rate limit

    print(f"\n{'='*50}")
    print(f"✅ 完成！总计 {total_written} 条样本 → {output_path}")
    if failed:
        print(f"❌ 失败的 Skill: {failed}")
        print(f"   可以用 --resume --skills {' '.join(failed)} 重新生成")


if __name__ == "__main__":
    main()
