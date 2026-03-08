# -*- coding: utf-8 -*-
"""
Koto Skill LoRA 微调训练脚本
==============================
基于 QLoRA（4-bit 量化 + LoRA）对本地 Qwen3/LLaMA3 模型进行微调，
让本地模型学会正确遵循 Koto Skills 的行为规则。

依赖（GPU 模式）：
    pip install -r config/requirements_training.txt

用法：
    # 基础训练（自动检测 GPU/CPU）
    python scripts/train_skill_lora.py

    # 指定模型和数据
    python scripts/train_skill_lora.py \\
        --model Qwen/Qwen3-8B \\
        --data config/training_data/skill_behavior_samples.jsonl \\
        --output models/koto_skill_lora

    # 低显存模式（4-bit 量化）
    python scripts/train_skill_lora.py --qlora

显存需求：
    Qwen3-8B  全精度 LoRA  → ~16 GB VRAM
    Qwen3-8B  QLoRA(4bit) → ~8  GB VRAM
    Qwen3-4B  QLoRA(4bit) → ~5  GB VRAM
    Qwen3-1.7B QLoRA(4bit)→ ~3  GB VRAM
"""
from __future__ import annotations

import argparse
import json
import logging
import os
import sys
import time
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

# ── 路径引导 ──────────────────────────────────────────────────────────────────
_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_ROOT))

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
    handlers=[
        logging.StreamHandler(),
        logging.FileHandler(_ROOT / "logs" / "train_lora.log", encoding="utf-8"),
    ],
)
logger = logging.getLogger("koto.train")

# ── 可选依赖检测 ───────────────────────────────────────────────────────────────
def _check_deps() -> Dict[str, bool]:
    deps = {}
    for pkg in ["torch", "transformers", "peft", "trl", "datasets", "accelerate"]:
        try:
            __import__(pkg)
            deps[pkg] = True
        except ImportError:
            deps[pkg] = False
    return deps


# ── 数据加载 ──────────────────────────────────────────────────────────────────
def load_training_data(path: Path) -> List[Dict]:
    """加载 JSONL 训练数据，过滤空样本"""
    if not path.exists():
        raise FileNotFoundError(f"训练数据不存在: {path}\n请先运行: python scripts/generate_skill_training_data.py")

    samples = []
    with open(path, "r", encoding="utf-8") as f:
        for lineno, line in enumerate(f, 1):
            line = line.strip()
            if not line:
                continue
            try:
                rec = json.loads(line)
                if rec.get("user") and rec.get("assistant"):
                    samples.append(rec)
            except Exception as e:
                logger.warning(f"第 {lineno} 行解析失败: {e}")

    logger.info(f"加载训练数据: {len(samples)} 条样本 ← {path}")
    return samples


def format_as_chat(sample: Dict, tokenizer: Any) -> str:
    """将样本格式化为模型的 chat template 格式"""
    messages = []
    if sample.get("system"):
        messages.append({"role": "system", "content": sample["system"]})
    messages.append({"role": "user", "content": sample["user"]})
    messages.append({"role": "assistant", "content": sample["assistant"]})
    return tokenizer.apply_chat_template(messages, tokenize=False, add_generation_prompt=False)


# ── 主训练流程 ─────────────────────────────────────────────────────────────────
def train(args: argparse.Namespace):
    deps = _check_deps()
    missing = [k for k, v in deps.items() if not v]
    if missing:
        logger.error(
            f"缺少训练依赖：{missing}\n"
            "请按以下步骤安装：\n"
            "  1. 安装 CUDA 版 PyTorch（见 config/requirements_training.txt 顶部注释）\n"
            "  2. pip install -r config/requirements_training.txt"
        )
        sys.exit(1)

    import torch
    from transformers import AutoModelForCausalLM, AutoTokenizer, TrainingArguments
    from peft import LoraConfig, get_peft_model, TaskType
    from trl import SFTTrainer, SFTConfig
    from datasets import Dataset

    # ── 设备检测 ──────────────────────────────────────────────────────────────
    if torch.cuda.is_available():
        device = "cuda"
        gpu_name = torch.cuda.get_device_name(0)
        vram_gb = torch.cuda.get_device_properties(0).total_memory / (1024**3)
        logger.info(f"GPU 检测: {gpu_name} ({vram_gb:.1f} GB VRAM)")
    elif hasattr(torch.backends, "mps") and torch.backends.mps.is_available():
        device = "mps"
        logger.info("使用 Apple Silicon MPS 加速")
    else:
        device = "cpu"
        logger.warning("未检测到 GPU，使用 CPU 训练（速度很慢，建议仅用于验证）")

    # ── 加载训练数据 ──────────────────────────────────────────────────────────
    data_path = Path(args.data)
    samples = load_training_data(data_path)

    if len(samples) < 10:
        logger.error(f"训练数据太少（{len(samples)} 条），至少需要 10 条。请先运行数据生成器。")
        sys.exit(1)

    # 数据分割 (90% 训练 / 10% 验证)
    split = int(len(samples) * 0.9)
    train_data = samples[:split]
    eval_data = samples[split:]
    logger.info(f"数据分割: 训练={len(train_data)}, 验证={len(eval_data)}")

    # ── 加载 Tokenizer ────────────────────────────────────────────────────────
    model_name = args.model
    logger.info(f"加载 Tokenizer: {model_name}")
    tokenizer = AutoTokenizer.from_pretrained(
        model_name,
        trust_remote_code=True,
        padding_side="right",
    )
    if tokenizer.pad_token is None:
        tokenizer.pad_token = tokenizer.eos_token

    # ── 格式化为对话格式 ───────────────────────────────────────────────────────
    def format_sample(sample: Dict) -> Dict:
        return {"text": format_as_chat(sample, tokenizer)}

    train_dataset = Dataset.from_list([format_sample(s) for s in train_data])
    eval_dataset = Dataset.from_list([format_sample(s) for s in eval_data])

    # ── 加载模型 ──────────────────────────────────────────────────────────────
    load_kwargs: Dict[str, Any] = {
        "trust_remote_code": True,
        "device_map": "auto" if device in ("cuda", "mps") else None,
    }

    if args.qlora and device == "cuda":
        try:
            from transformers import BitsAndBytesConfig
            import bitsandbytes  # noqa: F401
            bnb_config = BitsAndBytesConfig(
                load_in_4bit=True,
                bnb_4bit_use_double_quant=True,
                bnb_4bit_quant_type="nf4",
                bnb_4bit_compute_dtype=torch.bfloat16,
            )
            load_kwargs["quantization_config"] = bnb_config
            logger.info("QLoRA 4-bit 量化模式启用")
        except ImportError:
            logger.warning("bitsandbytes 未安装，降级为 fp16 模式")
            load_kwargs["torch_dtype"] = torch.float16
    elif device == "cuda":
        load_kwargs["torch_dtype"] = torch.bfloat16
    elif device == "cpu":
        load_kwargs["torch_dtype"] = torch.float32

    logger.info(f"加载模型: {model_name}...")
    model = AutoModelForCausalLM.from_pretrained(model_name, **load_kwargs)

    # ── LoRA 配置 ─────────────────────────────────────────────────────────────
    # target_modules 针对 Qwen3 / LLaMA3 架构
    lora_config = LoraConfig(
        task_type=TaskType.CAUSAL_LM,
        r=args.lora_rank,
        lora_alpha=args.lora_rank * 2,
        lora_dropout=0.05,
        target_modules=[
            "q_proj", "k_proj", "v_proj", "o_proj",
            "gate_proj", "up_proj", "down_proj",
        ],
        bias="none",
        inference_mode=False,
    )
    model = get_peft_model(model, lora_config)
    model.print_trainable_parameters()

    # ── 训练参数 ───────────────────────────────────────────────────────────────
    output_dir = Path(args.output)
    output_dir.mkdir(parents=True, exist_ok=True)

    training_args = SFTConfig(
        output_dir=str(output_dir),
        num_train_epochs=args.epochs,
        per_device_train_batch_size=args.batch_size,
        per_device_eval_batch_size=args.batch_size,
        gradient_accumulation_steps=4,
        learning_rate=args.lr,
        lr_scheduler_type="cosine",
        warmup_ratio=0.05,
        fp16=(device == "cuda" and not args.qlora),
        bf16=(device == "cuda" and args.qlora),
        logging_steps=10,
        eval_strategy="epoch",
        save_strategy="epoch",
        save_total_limit=2,
        load_best_model_at_end=True,
        metric_for_best_model="eval_loss",
        greater_is_better=False,
        report_to="none",          # 不上传 wandb/tensorboard
        dataloader_num_workers=0,  # Windows 兼容
        max_seq_length=args.max_seq_len,
        dataset_text_field="text",
        packing=False,
    )

    # ── 训练器 ────────────────────────────────────────────────────────────────
    trainer = SFTTrainer(
        model=model,
        args=training_args,
        train_dataset=train_dataset,
        eval_dataset=eval_dataset,
        tokenizer=tokenizer,
    )

    logger.info("=" * 60)
    logger.info("🚀 开始训练 Koto Skill LoRA...")
    logger.info(f"   模型:     {model_name}")
    logger.info(f"   训练样本: {len(train_data)}")
    logger.info(f"   验证样本: {len(eval_data)}")
    logger.info(f"   轮次:     {args.epochs}")
    logger.info(f"   LoRA rank:{args.lora_rank}")
    logger.info(f"   输出目录: {output_dir}")
    logger.info("=" * 60)

    start_time = time.time()
    trainer.train()
    elapsed = time.time() - start_time

    # ── 保存最终适配器 ─────────────────────────────────────────────────────────
    final_dir = output_dir / "final"
    final_dir.mkdir(exist_ok=True)
    model.save_pretrained(str(final_dir))
    tokenizer.save_pretrained(str(final_dir))

    # 保存训练元信息
    meta = {
        "base_model": model_name,
        "lora_rank": args.lora_rank,
        "epochs": args.epochs,
        "train_samples": len(train_data),
        "eval_samples": len(eval_data),
        "training_seconds": round(elapsed),
        "output_dir": str(final_dir),
        "timestamp": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "koto_skills_version": "1.0",
    }
    (output_dir / "training_meta.json").write_text(
        json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8"
    )

    logger.info("=" * 60)
    logger.info(f"✅ 训练完成！耗时 {elapsed/60:.1f} 分钟")
    logger.info(f"   LoRA 适配器已保存到: {final_dir}")
    logger.info("")
    logger.info("后续步骤 — 将适配器合并进 Ollama：")
    logger.info("  1. 用 transformers 合并权重：")
    logger.info("     from peft import PeftModel")
    logger.info(f"     model = PeftModel.from_pretrained(base, '{final_dir}')")
    logger.info("     model.merge_and_unload().save_pretrained('models/koto-merged')")
    logger.info("  2. 转为 GGUF：llama.cpp convert_hf_to_gguf.py models/koto-merged")
    logger.info("  3. ollama create koto-qwen3-finetuned -f models/Modelfile.koto-qwen3")
    logger.info("=" * 60)


# ── 入口 ──────────────────────────────────────────────────────────────────────
def main():
    parser = argparse.ArgumentParser(description="Koto Skill LoRA 微调训练")
    parser.add_argument(
        "--model", default="Qwen/Qwen3-1.7B",
        help="HuggingFace 模型名称（默认 Qwen/Qwen3-1.7B，显存不足时用小模型）"
    )
    parser.add_argument(
        "--data",
        default=str(_ROOT / "config" / "training_data" / "skill_behavior_samples.jsonl"),
        help="训练数据路径"
    )
    parser.add_argument(
        "--output", default=str(_ROOT / "models" / "koto_skill_lora"),
        help="LoRA 适配器输出目录"
    )
    parser.add_argument("--epochs", type=int, default=3, help="训练轮次")
    parser.add_argument("--batch-size", type=int, default=2, help="每批样本数")
    parser.add_argument("--lr", type=float, default=2e-4, help="学习率")
    parser.add_argument("--lora-rank", type=int, default=16, help="LoRA rank（越大越强但越慢）")
    parser.add_argument("--max-seq-len", type=int, default=2048, help="最大序列长度")
    parser.add_argument("--qlora", action="store_true", help="启用 QLoRA 4-bit 量化（需 bitsandbytes）")
    args = parser.parse_args()

    # 确保日志目录存在
    (_ROOT / "logs").mkdir(exist_ok=True)

    train(args)


if __name__ == "__main__":
    main()
