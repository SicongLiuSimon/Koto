# -*- coding: utf-8 -*-
"""
Koto Skill 行为训练数据生成器
==============================
使用 Gemini Flash 为 Koto 的每个 Skill 批量生成「提问→理想回答」训练对，
输出到 config/training_data/skill_behavior_samples.jsonl。

用法：
    python scripts/generate_skill_training_data.py
    python scripts/generate_skill_training_data.py --skills step_by_step concise_mode
    python scripts/generate_skill_training_data.py --pairs 15 --output config/training_data/my_data.jsonl

参数：
    --skills    只为指定 skill_id 生成（空=全部）
    --pairs     每个 Skill 生成的训练对数量（默认 12）
    --output    输出文件路径（默认 config/training_data/skill_behavior_samples.jsonl）
    --neg       同时生成负例（不启用 Skill 时的"普通"回答，用于对比学习）
    --resume    跳过已成功生成的 Skill（追加模式）
"""
from __future__ import annotations

import argparse
import json
import logging
import os
import sys
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

# ── 路径引导：让脚本从项目根目录运行时能 import app ──────────────────────────
_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_ROOT))

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger("koto.data_gen")

# ── 加载 Gemini API Key ───────────────────────────────────────────────────────
def _load_api_key() -> str:
    env_file = _ROOT / "config" / "gemini_config.env"
    if env_file.exists():
        for line in env_file.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if line.startswith("GEMINI_API_KEY="):
                return line.split("=", 1)[1].strip()
    key = os.environ.get("GEMINI_API_KEY", "")
    if not key:
        raise RuntimeError("找不到 GEMINI_API_KEY，请检查 config/gemini_config.env")
    return key


# ── 加载所有 Skills ───────────────────────────────────────────────────────────
def load_all_skills() -> List[Dict]:
    """返回内置 + 自定义 Skills 的完整列表（只加载有 prompt 的 skill）"""
    from app.core.skills.skill_manager import SkillManager, BUILTIN_SKILLS

    SkillManager._ensure_init()

    skills = []
    seen = set()

    # 内置
    for s in BUILTIN_SKILLS:
        if s["id"] in seen:
            continue
        seen.add(s["id"])
        prompt = s.get("prompt", "").strip()
        if not prompt or s["id"] == "long_term_memory":  # 长期记忆 skill 跳过
            continue
        skills.append({
            "id": s["id"],
            "name": s["name"],
            "icon": s.get("icon", "🔧"),
            "category": s.get("category", "behavior"),
            "description": s.get("description", ""),
            "prompt": prompt,
            "task_types": s.get("task_types", []),
        })

    # 自定义 JSON
    skills_dir = _ROOT / "config" / "skills"
    if skills_dir.exists():
        for jf in sorted(skills_dir.glob("*.json")):
            try:
                data = json.loads(jf.read_text(encoding="utf-8"))
                sid = data.get("id", jf.stem)
                if sid in seen:
                    continue
                seen.add(sid)
                prompt = data.get("prompt", "").strip()
                if not prompt:
                    continue
                skills.append({
                    "id": sid,
                    "name": data.get("name", sid),
                    "icon": data.get("icon", "🔧"),
                    "category": data.get("category", "custom"),
                    "description": data.get("description", ""),
                    "prompt": prompt,
                    "task_types": data.get("task_types", []),
                })
            except Exception as e:
                logger.warning(f"跳过 {jf.name}: {e}")

    logger.info(f"共加载 {len(skills)} 个可训练 Skills")
    return skills


# ── Gemini 调用 ───────────────────────────────────────────────────────────────
def call_gemini(api_key: str, messages: List[Dict], model: str = "gemini-2.5-flash") -> str:
    """简单的 Gemini HTTP 调用，返回文本响应"""
    try:
        from google import genai
        client = genai.Client(api_key=api_key)
        # 转为 Gemini SDK 格式
        contents = []
        sys_instruction = None
        for m in messages:
            if m["role"] == "system":
                sys_instruction = m["content"]
            elif m["role"] == "user":
                contents.append(m["content"])

        config_kwargs: Dict[str, Any] = {"max_output_tokens": 8192}
        if sys_instruction:
            config_kwargs["system_instruction"] = sys_instruction

        from google.genai import types
        response = client.models.generate_content(
            model=model,
            contents=contents,
            config=types.GenerateContentConfig(**config_kwargs),
        )
        return response.text or ""
    except Exception as e:
        logger.error(f"Gemini 调用失败: {e}")
        raise


# ── 为单个 Skill 生成训练对 ───────────────────────────────────────────────────
_GENERATOR_SYSTEM = """\
你是一个专业的 AI 训练数据生成专家，熟悉 Koto AI 平台的 Skills 系统。
你的任务是：给定一个 Koto Skill 的定义，生成高质量的「问题→理想回答」训练对。

要求：
1. 每对训练数据包含 user 问题和 assistant 回答
2. assistant 的回答必须严格遵循 Skill 的所有行为规则
3. 问题要多样化，覆盖该 Skill 最典型的使用场景
4. 回答要真实、有用，不要造假信息
5. 用中文生成（专业术语可保留英文）
6. 输出严格的 JSON 数组格式，不要有多余说明文字

输出格式（JSON 数组）：
[
  {
    "user": "用户问题",
    "assistant": "严格遵循 Skill 规则的理想回答"
  },
  ...
]
"""

def generate_pairs_for_skill(
    api_key: str,
    skill: Dict,
    num_pairs: int = 12,
    _batch_size: int = 2,
) -> List[Dict]:
    """调用 Gemini 为一个 Skill 生成训练对（自动分批避免 token 截断）"""

    skill_desc = f"""
Skill ID: {skill['id']}
Skill 名称: {skill['name']} {skill['icon']}
描述: {skill['description']}
分类: {skill['category']}
适用场景: {', '.join(skill['task_types']) if skill['task_types'] else '所有场景'}

注入到 system prompt 的 Skill 规则：
{skill['prompt']}
"""

    def _fix_json_newlines(s: str) -> str:
        """将 JSON 字符串字面量内部的裸换行 / 制表符替换为转义序列"""
        result = []
        in_string = False
        escape_next = False
        for ch in s:
            if escape_next:
                result.append(ch)
                escape_next = False
            elif ch == '\\' and in_string:
                result.append(ch)
                escape_next = True
            elif ch == '"':
                in_string = not in_string
                result.append(ch)
            elif in_string and ch == '\n':
                result.append('\\n')
            elif in_string and ch == '\r':
                result.append('\\r')
            elif in_string and ch == '\t':
                result.append('\\t')
            else:
                result.append(ch)
        return ''.join(result)

    def _call_batch(n: int) -> List[Dict]:
        user_msg = f"""\
请为以下 Koto Skill 生成 {n} 条训练数据对。

{skill_desc}

生成要求：
- 问题要多样：覆盖直接问答、任务请求、技术问题等不同类型
- 回答必须体现 Skill 的每一条规则（不遗漏）
- 回答长度要恰当，不要过短或过长
- 用中文生成

输出：纯 JSON 数组，无其他文字。
"""
        messages = [
            {"role": "system", "content": _GENERATOR_SYSTEM},
            {"role": "user", "content": user_msg},
        ]
        raw = call_gemini(api_key, messages)
        raw = raw.strip()
        if raw.startswith("```"):
            lines = raw.split("\n")
            raw = "\n".join(lines[1:-1] if lines[-1].strip() == "```" else lines[1:])
        raw_fixed = _fix_json_newlines(raw)
        try:
            result = json.loads(raw_fixed)
            if not isinstance(result, list):
                raise ValueError("期望 JSON 数组")
            return result
        except Exception as e:
            logger.error(f"解析训练对失败 ({skill['id']}): {e}\n原始响应: {raw[:500]}")
            return []

    all_pairs: List[Dict] = []
    remaining = num_pairs
    while remaining > 0:
        batch_n = min(_batch_size, remaining)
        batch = _call_batch(batch_n)
        all_pairs.extend(batch)
        if not batch:
            # 当前批次失败则跳出，避免无限循环
            break
        remaining -= len(batch)
    return all_pairs


def build_sample(
    skill: Dict,
    pair: Dict,
    negative: bool = False,
) -> Dict:
    """将一个训练对封装成标准 JSONL 样本"""
    if negative:
        # 负例：system 中不含 Skill，用于对比学习
        system_prompt = (
            "你是 Koto，一个本地 AI 助手，请尽力回答用户问题。"
        )
    else:
        # 正例：system 中包含完整 Skill 前言 + Skill prompt
        preamble = (
            "你正在 Koto AI 助手平台上运行。\n"
            "本系统消息中，以「## 🎯 当前激活的 Skills」开头的部分是用户激活的功能模块。\n"
            "你必须严格遵循所有 Skill 的全部规则，它们优先级高于默认行为。\n"
            "---\n"
            "\n## 🎯 当前激活的 Skills\n"
        )
        system_prompt = preamble + skill["prompt"]

    return {
        "system": system_prompt,
        "user": pair.get("user", ""),
        "assistant": pair.get("assistant", ""),
        "task_type": "SKILL_BEHAVIOR",
        "skill_id": skill["id"],
        "skill_name": skill["name"],
        "negative": negative,
        "source": "gemini_generated",
        "quality": 0.85,
        "timestamp": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
    }


# ── 主流程 ────────────────────────────────────────────────────────────────────
def main():
    parser = argparse.ArgumentParser(description="Koto Skill 训练数据生成器")
    parser.add_argument("--skills", nargs="*", default=[], help="只为指定 skill_id 生成")
    parser.add_argument("--pairs", type=int, default=12, help="每个 Skill 的训练对数量")
    parser.add_argument(
        "--output",
        default=str(_ROOT / "config" / "training_data" / "skill_behavior_samples.jsonl"),
        help="输出文件路径",
    )
    parser.add_argument("--neg", action="store_true", default=False, help="同时生成负例")
    parser.add_argument("--resume", action="store_true", default=False, help="追加模式（跳过已有）")
    args = parser.parse_args()

    api_key = _load_api_key()
    all_skills = load_all_skills()

    # 过滤指定 skills
    if args.skills:
        all_skills = [s for s in all_skills if s["id"] in args.skills]
        if not all_skills:
            logger.error(f"未找到指定的 Skills: {args.skills}")
            sys.exit(1)

    output_path = Path(args.output)
    output_path.parent.mkdir(parents=True, exist_ok=True)

    # 读取已有记录（resume 模式）
    done_skills: set = set()
    if args.resume and output_path.exists():
        with open(output_path, "r", encoding="utf-8") as f:
            for line in f:
                try:
                    rec = json.loads(line)
                    done_skills.add(rec.get("skill_id", ""))
                except Exception:
                    pass
        logger.info(f"Resume 模式：已跳过 {len(done_skills)} 个已完成 Skill")

    # 打开输出文件（追加模式）
    mode = "a" if args.resume else "w"
    total_written = 0

    with open(output_path, mode, encoding="utf-8") as out_f:
        for i, skill in enumerate(all_skills):
            sid = skill["id"]
            if sid in done_skills:
                logger.info(f"[{i+1}/{len(all_skills)}] 跳过（已完成）: {skill['name']}")
                continue

            logger.info(f"[{i+1}/{len(all_skills)}] 生成: {skill['name']} ({sid})")

            try:
                pairs = generate_pairs_for_skill(api_key, skill, num_pairs=args.pairs)
                if not pairs:
                    logger.warning(f"  ⚠️ {sid}: 未生成任何训练对")
                    continue

                for pair in pairs:
                    if not pair.get("user") or not pair.get("assistant"):
                        continue
                    # 正例
                    sample = build_sample(skill, pair, negative=False)
                    out_f.write(json.dumps(sample, ensure_ascii=False) + "\n")
                    total_written += 1
                    # 负例（可选）
                    if args.neg:
                        neg_sample = build_sample(skill, pair, negative=True)
                        out_f.write(json.dumps(neg_sample, ensure_ascii=False) + "\n")
                        total_written += 1

                out_f.flush()
                logger.info(f"  ✅ {sid}: 写入 {len(pairs)} 条正例" + (f" + {len(pairs)} 条负例" if args.neg else ""))

                # 轻微延迟，避免触发 API 限流
                if i < len(all_skills) - 1:
                    time.sleep(1.5)

            except Exception as e:
                logger.error(f"  ❌ {sid}: 失败 → {e}")
                continue

    logger.info(f"\n✅ 数据生成完成！共写入 {total_written} 条样本 → {output_path}")


if __name__ == "__main__":
    main()
