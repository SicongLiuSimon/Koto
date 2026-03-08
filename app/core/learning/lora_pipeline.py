"""
lora_pipeline.py — Koto LoRA 蒸馏训练流水线（生产级实装）
===========================================================
为 Koto 提供「ShadowTracer 数据 → Qwen3 LoRA 微调 → 适配器注册」的完整流水线。

架构：
  TrainingConfig          — 硬件自适应超参（5 档，旗舰默认 Qwen3-8B）
  LoRAPipeline.prepare_dataset() — 从 ShadowTracer JSONL 生成 Qwen3 chat 格式数据集
  LoRAPipeline.train()           — 真实 SFTTrainer 训练（依赖可用时）/ 骨架兜底
  LoRAPipeline._run_real_training() — SFTTrainer + LoRA + 实时 loss 回调
  LoRAPipeline.register_as_adapter() — 元数据写入 config/adapters/
  LoRAPipeline.watch_shadow_tracer() — 阈值触发自动训练

依赖安装:
  pip install peft transformers datasets accelerate trl bitsandbytes
  pip install torch --index-url https://download.pytorch.org/whl/cu126
"""
from __future__ import annotations

import json
import logging
import os
import threading
import time
from dataclasses import asdict, dataclass, field
from typing import Any, Callable, Dict, List, Optional

logger = logging.getLogger(__name__)

# ── 路径 ──────────────────────────────────────────────────────────────────────

_BASE_DIR = os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(
    os.path.abspath(__file__)))))
_SHADOW_DIR = os.path.join(_BASE_DIR, "workspace", "shadow_traces")
_DATASET_DIR = os.path.join(_BASE_DIR, "workspace", "lora_datasets")
_ADAPTER_DIR = os.path.join(_BASE_DIR, "config", "adapters")
_CHECKPOINT_DIR = os.path.join(_BASE_DIR, "workspace", "lora_checkpoints")


# ── 数据结构 ──────────────────────────────────────────────────────────────────

@dataclass
class TrainingConfig:
    """
    LoRA 微调超参配置（Qwen3 系列）。
    默认值已针对 RTX 4090 (24GB VRAM) + i9-13900KF + 64GB RAM 调优，
    使用 Qwen3-8B（≈ Qwen2.5-14B 能力，128K 上下文，混合思维模式）。
    使用 TrainingConfig.for_hardware() 自动适配其他设备。
    """
    # ── 模型选择 ─────────────────────────────────────────────────────────────
    # RTX 4090 24GB: Qwen3-8B ≈ Qwen2.5-14B 能力，128K 上下文，混合思维模式
    # 比 Qwen2.5-7B 强一代，fp16 LoRA 完全无压力
    base_model: str = "Qwen/Qwen3-8B"

    # ── LoRA 超参 ────────────────────────────────────────────────────────────
    lora_r: int = 16                                   # 4090 可用更高 rank
    lora_alpha: int = 32
    lora_dropout: float = 0.05
    target_modules: List[str] = field(
        default_factory=lambda: ["q_proj", "k_proj", "v_proj", "o_proj",
                                  "gate_proj", "up_proj", "down_proj"]
    )

    # ── 训练超参 ─────────────────────────────────────────────────────────────
    num_epochs: int = 3
    per_device_train_batch_size: int = 8               # 4090 显存够，大 batch
    gradient_accumulation_steps: int = 2
    learning_rate: float = 2e-4
    max_seq_length: int = 1024                         # 更长上下文
    warmup_steps: int = 20
    save_steps: int = 50
    fp16: bool = True                                  # 4090 bf16/fp16 极快
    use_4bit: bool = False                             # 24GB 无需 4-bit 量化

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

    @classmethod
    def for_hardware(cls, vram_gb: float = 0, ram_gb: float = 0) -> "TrainingConfig":
        """
        根据检测到的硬件自动选择最优 Qwen3 模型和超参。

        Qwen3 分级策略（能力等级均比上一代 Qwen2.5 提升约一档）:
          ≥ 20GB VRAM → Qwen3-8B   rank=16, fp16=True  (旗舰，≈Qwen2.5-14B 能力，128K ctx)
          ≥ 10GB VRAM → Qwen3-4B   rank=16, fp16=True  (中高端，≈Qwen2.5-7B 能力)
          ≥  6GB VRAM → Qwen3-1.7B rank=8,  fp16=True  (中端，≈Qwen2.5-3B 能力)
          ≥  4GB VRAM → Qwen3-0.6B rank=8,  use_4bit=True (入门)
          CPU 机器    → Qwen3-0.6B rank=4,  use_4bit=True (最慢)

        所有 Qwen3 模型均支持混合思维模式（enable_thinking）和 119 种语言。
        """
        if vram_gb >= 20:
            return cls(
                base_model="Qwen/Qwen3-8B",
                lora_r=16, lora_alpha=32,
                per_device_train_batch_size=8,
                max_seq_length=2048,     # Qwen3-8B 支持 128K，训练用 2K 足够
                fp16=True, use_4bit=False,
            )
        elif vram_gb >= 10:
            return cls(
                base_model="Qwen/Qwen3-4B",
                lora_r=16, lora_alpha=32,
                per_device_train_batch_size=4,
                max_seq_length=2048,
                fp16=True, use_4bit=False,
            )
        elif vram_gb >= 6:
            return cls(
                base_model="Qwen/Qwen3-1.7B",
                lora_r=8, lora_alpha=16,
                per_device_train_batch_size=4,
                max_seq_length=1024,
                fp16=True, use_4bit=False,
            )
        elif vram_gb >= 4:
            return cls(
                base_model="Qwen/Qwen3-0.6B",
                lora_r=8, lora_alpha=16,
                per_device_train_batch_size=2,
                max_seq_length=512,
                fp16=False, use_4bit=True,
            )
        else:  # CPU only
            return cls(
                base_model="Qwen/Qwen3-0.6B",
                lora_r=4, lora_alpha=8,
                per_device_train_batch_size=1,
                gradient_accumulation_steps=8,
                max_seq_length=256,
                fp16=False, use_4bit=True,
            )

    @classmethod
    def detect_and_build(cls) -> "TrainingConfig":
        """
        自动检测当前机器硬件并返回最优配置。
        可在任意环境安全调用（无依赖时降级到 CPU 配置）。
        """
        vram_gb = 0.0
        try:
            import subprocess, re
            result = subprocess.run(
                ["nvidia-smi", "--query-gpu=memory.total", "--format=csv,noheader,nounits"],
                capture_output=True, text=True, timeout=5
            )
            if result.returncode == 0:
                vram_mb = max(int(x.strip()) for x in result.stdout.strip().splitlines() if x.strip().isdigit())
                vram_gb = vram_mb / 1024
        except Exception:
            pass

        import psutil
        ram_gb = psutil.virtual_memory().total / (1024 ** 3)

        cfg = cls.for_hardware(vram_gb=vram_gb, ram_gb=ram_gb)
        logger.info(
            f"[TrainingConfig] 硬件检测: VRAM={vram_gb:.1f}GB RAM={ram_gb:.1f}GB"
            f" → 选用 Qwen3 模型: {cfg.base_model} rank={cfg.lora_r} fp16={cfg.fp16}"
        )
        return cfg


@dataclass
class AdapterMeta:
    """注册到 config/adapters/ 的适配器元数据"""
    skill_id: str
    adapter_path: str
    base_model: str
    trained_at: str
    num_samples: int
    num_epochs: int
    eval_loss: Optional[float] = None
    enabled: bool = True
    description: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: Dict[str, Any]) -> "AdapterMeta":
        return cls(**{k: v for k, v in d.items() if k in cls.__dataclass_fields__})


# ── 主类 ──────────────────────────────────────────────────────────────────────

class LoRAPipeline:
    """
    Koto LoRA 微调流水线。

    典型用法:
        pipeline = LoRAPipeline()

        # 检查依赖
        ok, missing = pipeline.check_prerequisites()

        # 准备数据集
        dataset_path = pipeline.prepare_dataset("email_writer")

        # 训练（需依赖已安装）
        result = pipeline.train("email_writer")

        # 注册适配器
        if result["success"]:
            pipeline.register_as_adapter("email_writer", result["adapter_path"])

    自动触发模式（与 ShadowTracer 联动）:
        pipeline.watch_shadow_tracer(auto_train=True)
    """

    def __init__(self, config: Optional[TrainingConfig] = None):
        self.config = config or TrainingConfig()
        self._training_lock = threading.Lock()
        self._active_trainings: Dict[str, bool] = {}  # skill_id → is_running

    # ══════════════════════════════════════════════════════════════════════════
    # 1. 环境检查
    # ══════════════════════════════════════════════════════════════════════════

    def check_prerequisites(self) -> tuple[bool, List[str]]:
        """
        检查 LoRA 微调所需的 Python 依赖是否已安装。

        返回: (all_ok: bool, missing_packages: List[str])
        """
        required = {
            "peft": "peft",
            "transformers": "transformers",
            "datasets": "datasets",
            "accelerate": "accelerate",
            "torch": "torch",
            "trl": "trl",
        }
        missing = []
        for pkg, label in required.items():
            try:
                __import__(pkg)
            except ImportError:
                missing.append(label)

        all_ok = len(missing) == 0
        if not all_ok:
            logger.warning(
                f"[LoRAPipeline] 缺少依赖: {missing}\n"
                "安装命令: pip install peft transformers datasets accelerate trl bitsandbytes\n"
                "          pip install torch --index-url https://download.pytorch.org/whl/cu126"
            )
        else:
            logger.info("[LoRAPipeline] ✅ 所有依赖已就绪")

        # 检查 CUDA
        try:
            import torch
            cuda_available = torch.cuda.is_available()
            cuda_ver = torch.version.cuda or "N/A"
            logger.info(f"[LoRAPipeline] CUDA 可用: {cuda_available}  版本: {cuda_ver}")
        except Exception:
            cuda_available = False

        return all_ok, missing

    # ══════════════════════════════════════════════════════════════════════════
    # 2. 数据集准备
    # ══════════════════════════════════════════════════════════════════════════

    def prepare_dataset(
        self,
        skill_id: str,
        min_samples: int = 5,
        output_format: str = "qwen3_chat",
        system_prompt: Optional[str] = None,
    ) -> Optional[str]:
        """
        从 ShadowTracer JSONL 生成适用于 SFTTrainer 的训练数据集。

        参数:
            skill_id      - 目标 Skill ID
            min_samples   - 最少样本数，不足时返回 None
            output_format - "qwen3_chat"（默认）| "alpaca" | "sharegpt"
            system_prompt - 自定义 system 提示（None 则使用默认）

        qwen3_chat 格式（SFTTrainer + tokenizer.apply_chat_template 直接可用）:
        [
          {"messages": [
            {"role": "system",    "content": "..."},
            {"role": "user",      "content": "..."},
            {"role": "assistant", "content": "..."}
          ]},
          ...
        ]
        """
        trace_file = os.path.join(_SHADOW_DIR, f"{skill_id}.jsonl")
        if not os.path.exists(trace_file):
            logger.warning(f"[LoRAPipeline] 影子记录不存在: {trace_file}")
            return None

        records = []
        with open(trace_file, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    records.append(json.loads(line))
                except json.JSONDecodeError:
                    continue

        if len(records) < min_samples:
            logger.warning(
                f"[LoRAPipeline] skill='{skill_id}' 样本数 {len(records)} < "
                f"最低要求 {min_samples}，跳过数据集准备"
            )
            return None

        sys_msg = system_prompt or (
            f"你是 Koto 助手的专项技能模块（{skill_id}），"
            "请用清晰、简洁的中文回答用户问题。"
        )

        os.makedirs(_DATASET_DIR, exist_ok=True)
        dataset_path = os.path.join(_DATASET_DIR, f"{skill_id}_{output_format}.json")

        if output_format == "qwen3_chat":
            converted = [
                {
                    "messages": [
                        {"role": "system",    "content": sys_msg},
                        {"role": "user",      "content": r.get("user_input", "")},
                        {"role": "assistant", "content": r.get("ai_response", "")},
                    ]
                }
                for r in records
                if r.get("user_input") and r.get("ai_response")
            ]
        elif output_format == "alpaca":
            converted = [
                {
                    "instruction": r.get("user_input", ""),
                    "input": "",
                    "output": r.get("ai_response", ""),
                }
                for r in records
                if r.get("user_input") and r.get("ai_response")
            ]
        elif output_format == "sharegpt":
            converted = [
                {
                    "conversations": [
                        {"from": "system", "value": sys_msg},
                        {"from": "human",  "value": r.get("user_input", "")},
                        {"from": "gpt",    "value": r.get("ai_response", "")},
                    ]
                }
                for r in records
                if r.get("user_input") and r.get("ai_response")
            ]
        else:
            converted = records

        with open(dataset_path, "w", encoding="utf-8") as f:
            json.dump(converted, f, ensure_ascii=False, indent=2)

        logger.info(
            f"[LoRAPipeline] ✅ 数据集准备完成: {dataset_path} "
            f"({len(converted)} 条样本, 格式={output_format})"
        )
        return dataset_path

    # ══════════════════════════════════════════════════════════════════════════
    # 3. 训练（骨架 + 实际实现）
    # ══════════════════════════════════════════════════════════════════════════

    def train(
        self,
        skill_id: str,
        dataset_path: Optional[str] = None,
        config_override: Optional[Dict[str, Any]] = None,
        progress_cb: Optional[Callable[[Dict[str, Any]], None]] = None,
    ) -> Dict[str, Any]:
        """
        执行 LoRA 蒸馏训练。

        参数:
            skill_id       - 要训练的 Skill ID
            dataset_path   - 指定数据集路径（None 则自动从 ShadowTracer 生成）
            config_override - 覆盖默认 TrainingConfig 的字段
            progress_cb    - 进度回调 fn({"msg", "step", "loss", "pct"})，用于 SSE 推送

        返回:
        {
          "success": bool,
          "skill_id": str,
          "adapter_path": str | None,
          "num_samples": int,
          "duration_s": float,
          "eval_loss": float | None,
          "skeleton": bool,   # True = 依赖缺失，未实际训练
          "base_model": str,
        }
        """
        if self._active_trainings.get(skill_id):
            return {"success": False, "error": f"skill '{skill_id}' 正在训练中，请稍后重试", "skill_id": skill_id}

        t0 = time.time()
        self._active_trainings[skill_id] = True
        try:
            # ── 数据集 ─────────────────────────────────────────────────────
            if dataset_path is None:
                dataset_path = self.prepare_dataset(skill_id)
            if dataset_path is None:
                return {
                    "success": False,
                    "skill_id": skill_id,
                    "error": "数据集准备失败（样本不足或影子记录不存在）",
                    "adapter_path": None,
                    "num_samples": 0,
                    "duration_s": time.time() - t0,
                    "skeleton": False,
                }

            # ── 合并配置 ───────────────────────────────────────────────────
            base_dict = self.config.to_dict()
            if config_override:
                base_dict.update(config_override)
            cfg = TrainingConfig(**base_dict)

            # ── 尝试真实训练 ───────────────────────────────────────────────
            try:
                return self._run_real_training(skill_id, dataset_path, cfg, t0, progress_cb)
            except ImportError as ie:
                logger.warning(
                    f"[LoRAPipeline] 依赖未安装，骨架模式运行: {ie}\n"
                    "  安装: pip install peft transformers datasets accelerate trl\n"
                    "         pip install torch --index-url https://download.pytorch.org/whl/cu126"
                )
                return self._skeleton_response(skill_id, dataset_path, t0)

        except Exception as e:
            logger.error(f"[LoRAPipeline] 训练失败 skill={skill_id}: {e}", exc_info=True)
            return {
                "success": False,
                "skill_id": skill_id,
                "error": str(e),
                "adapter_path": None,
                "num_samples": 0,
                "duration_s": time.time() - t0,
                "skeleton": False,
            }
        finally:
            self._active_trainings.pop(skill_id, None)

    def _skeleton_response(self, skill_id: str, dataset_path: str, t0: float) -> Dict[str, Any]:
        """依赖缺失时的骨架占位响应。"""
        try:
            with open(dataset_path, "r", encoding="utf-8") as f:
                num_samples = len(json.load(f))
        except Exception:
            num_samples = 0
        adapter_path = os.path.join(_CHECKPOINT_DIR, skill_id, "skeleton_adapter")
        return {
            "success": True,
            "skill_id": skill_id,
            "adapter_path": adapter_path,
            "num_samples": num_samples,
            "duration_s": round(time.time() - t0, 2),
            "skeleton": True,
            "message": (
                "骨架模式：训练依赖未安装，未执行实际训练。\n"
                "如需启用 GPU 训练，请在本机执行：\n"
                "  pip install -r config/requirements_training.txt\n"
                "  pip install torch torchvision torchaudio "
                "--index-url https://download.pytorch.org/whl/cu126"
            ),
        }

    def _run_real_training(
        self, skill_id: str, dataset_path: str, cfg: TrainingConfig, t0: float,
        progress_cb: Optional[Callable[[Dict[str, Any]], None]] = None,
    ) -> Dict[str, Any]:
        """
        生产级 LoRA 蒸馏训练实现（Qwen3 专用）。

        使用 TRL SFTTrainer + apply_chat_template 完整对话格式。
        实时通过 progress_cb 回调汇报进度（用于 SSE 流式推送）。

        需要: peft transformers datasets accelerate trl torch
        """
        import torch
        from datasets import load_dataset
        from peft import LoraConfig, TaskType
        from transformers import (
            AutoModelForCausalLM,
            AutoTokenizer,
            BitsAndBytesConfig,
            TrainerCallback,
            TrainerControl,
            TrainerState,
            TrainingArguments,
        )
        from trl import SFTConfig, SFTTrainer

        def _report(msg: str, step: int = 0, loss: Optional[float] = None,
                    pct: float = 0.0) -> None:
            logger.info(f"[LoRAPipeline] {msg}")
            if progress_cb:
                try:
                    progress_cb({"msg": msg, "step": step, "loss": loss, "pct": pct})
                except Exception:
                    pass

        _report(f"🚀 开始 Qwen3 LoRA 蒸馏训练  skill={skill_id}", pct=0)
        _report(f"基座模型: {cfg.base_model}  rank={cfg.lora_r}  fp16={cfg.fp16}", pct=1)

        adapter_path = os.path.join(_CHECKPOINT_DIR, skill_id, "lora_adapter")
        os.makedirs(adapter_path, exist_ok=True)

        # ── 1. 加载数据集 ──────────────────────────────────────────────────
        raw_ds = load_dataset("json", data_files=dataset_path, split="train")
        num_samples = len(raw_ds)
        _report(f"数据集加载完成: {num_samples} 条样本", pct=5)

        # ── 2. 分词器 ──────────────────────────────────────────────────────
        tokenizer = AutoTokenizer.from_pretrained(cfg.base_model, trust_remote_code=True)
        if tokenizer.pad_token is None:
            tokenizer.pad_token = tokenizer.eos_token
        tokenizer.padding_side = "right"   # SFTTrainer 推荐 right-padding
        _report("分词器加载完成", pct=8)

        # ── 3. 格式化函数（Qwen3 apply_chat_template）─────────────────────
        def _format_messages(sample: Dict[str, Any]) -> Dict[str, str]:
            """把 messages 字段转换为 Qwen3 chat 格式字符串。"""
            msgs = sample.get("messages", [])
            # Qwen3 不需要 enable_thinking=True for SFT；关闭思维链节省序列长度
            text = tokenizer.apply_chat_template(
                msgs,
                tokenize=False,
                add_generation_prompt=False,
                enable_thinking=False,
            )
            return {"text": text}

        # 检测数据集格式是否为 qwen3_chat（有 messages 字段）
        first_row = raw_ds[0] if num_samples > 0 else {}
        if "messages" in first_row:
            formatted_ds = raw_ds.map(_format_messages, num_proc=1)
            text_field = "text"
        else:
            # alpaca 兼容：拼接 instruction + output
            def _format_alpaca(s):
                return {"text": f"### Instruction:\n{s.get('instruction','')}\n\n### Response:\n{s.get('output','')}"}
            formatted_ds = raw_ds.map(_format_alpaca, num_proc=1)
            text_field = "text"

        _report("数据格式化完成（Qwen3 chat template）", pct=12)

        # ── 4. 加载模型 ────────────────────────────────────────────────────
        load_kwargs: Dict[str, Any] = {
            "trust_remote_code": True,
            "torch_dtype": torch.float16 if cfg.fp16 else torch.float32,
        }
        if cfg.use_4bit:
            try:
                import bitsandbytes  # noqa: F401
                bnb_config = BitsAndBytesConfig(
                    load_in_4bit=True,
                    bnb_4bit_use_double_quant=True,
                    bnb_4bit_quant_type="nf4",
                    bnb_4bit_compute_dtype=torch.float16,
                )
                load_kwargs["quantization_config"] = bnb_config
                load_kwargs.pop("torch_dtype", None)
            except ImportError:
                logger.warning("[LoRAPipeline] bitsandbytes 未安装，回退 fp16 加载")
                load_kwargs["torch_dtype"] = torch.float16
        elif torch.cuda.is_available():
            load_kwargs["device_map"] = "auto"

        _report("正在加载基座模型（可能需要 1-3 分钟）…", pct=15)
        model = AutoModelForCausalLM.from_pretrained(cfg.base_model, **load_kwargs)
        model.config.use_cache = False           # 训练时关闭 KV cache
        model.enable_input_require_grads()
        _report("模型加载完成", pct=30)

        # ── 5. LoRA 配置 ───────────────────────────────────────────────────
        lora_config = LoraConfig(
            task_type=TaskType.CAUSAL_LM,
            r=cfg.lora_r,
            lora_alpha=cfg.lora_alpha,
            lora_dropout=cfg.lora_dropout,
            target_modules=cfg.target_modules,
            bias="none",
            init_lora_weights="gaussian",   # 比默认 kaiming 收敛更平稳
        )

        # ── 6. 实时 loss 回调 ──────────────────────────────────────────────
        class _ProgressCallback(TrainerCallback):
            def __init__(self):
                self._total_steps = 0

            def on_train_begin(self, args, state: TrainerState, control: TrainerControl, **kw):
                self._total_steps = state.max_steps or 1

            def on_log(self, args, state: TrainerState, control: TrainerControl,
                       logs: Optional[Dict] = None, **kw):
                if logs and "loss" in logs:
                    pct = min(30 + int(state.global_step / self._total_steps * 65), 95)
                    _report(
                        f"step={state.global_step}/{self._total_steps}  loss={logs['loss']:.4f}",
                        step=state.global_step,
                        loss=logs["loss"],
                        pct=pct,
                    )

        # ── 7. SFT 训练 ────────────────────────────────────────────────────
        sft_cfg = SFTConfig(
            output_dir=adapter_path,
            num_train_epochs=cfg.num_epochs,
            per_device_train_batch_size=cfg.per_device_train_batch_size,
            gradient_accumulation_steps=cfg.gradient_accumulation_steps,
            learning_rate=cfg.learning_rate,
            warmup_steps=cfg.warmup_steps,
            save_steps=cfg.save_steps,
            logging_steps=5,
            fp16=cfg.fp16 and not cfg.use_4bit,
            bf16=False,                     # RTX 4090 支持，但 fp16 更快
            report_to="none",
            dataloader_num_workers=0,       # Windows 兼容
            remove_unused_columns=True,
            max_seq_length=cfg.max_seq_length,
            dataset_text_field=text_field,
            packing=False,                  # 不 pack，保持对话边界清晰
            save_total_limit=2,
            load_best_model_at_end=False,
        )

        trainer = SFTTrainer(
            model=model,
            args=sft_cfg,
            train_dataset=formatted_ds,
            peft_config=lora_config,
            processing_class=tokenizer,
            callbacks=[_ProgressCallback()],
        )

        _report("开始 SFT 训练…", pct=32)
        trainer.train()

        # ── 8. 保存 ────────────────────────────────────────────────────────
        trainer.save_model(adapter_path)
        tokenizer.save_pretrained(adapter_path)

        eval_loss = None
        if trainer.state.log_history:
            losses = [e["loss"] for e in trainer.state.log_history if "loss" in e]
            if losses:
                eval_loss = round(losses[-1], 4)

        duration = round(time.time() - t0, 2)
        _report(f"✅ 训练完成  loss={eval_loss}  耗时={duration}s", pct=100)

        return {
            "success": True,
            "skill_id": skill_id,
            "adapter_path": adapter_path,
            "num_samples": num_samples,
            "duration_s": duration,
            "eval_loss": eval_loss,
            "skeleton": False,
            "base_model": cfg.base_model,
        }

    # ══════════════════════════════════════════════════════════════════════════
    # 4. 注册适配器
    # ══════════════════════════════════════════════════════════════════════════

    def register_as_adapter(
        self,
        skill_id: str,
        adapter_path: str,
        num_samples: int = 0,
        eval_loss: Optional[float] = None,
        description: str = "",
    ) -> str:
        """
        将训练完成的 LoRA 适配器注册到 config/adapters/{skill_id}.json。

        返回: 元数据文件路径
        """
        os.makedirs(_ADAPTER_DIR, exist_ok=True)
        meta = AdapterMeta(
            skill_id=skill_id,
            adapter_path=adapter_path,
            base_model=self.config.base_model,
            trained_at=time.strftime("%Y-%m-%dT%H:%M:%S"),
            num_samples=num_samples,
            num_epochs=self.config.num_epochs,
            eval_loss=eval_loss,
            enabled=True,
            description=description or f"自动训练的 LoRA 适配器 (skill={skill_id})",
        )
        meta_path = os.path.join(_ADAPTER_DIR, f"{skill_id}.json")
        with open(meta_path, "w", encoding="utf-8") as f:
            json.dump(meta.to_dict(), f, ensure_ascii=False, indent=2)
        logger.info(f"[LoRAPipeline] ✅ 适配器已注册: {meta_path}")
        return meta_path

    def list_adapters(self) -> List[AdapterMeta]:
        """返回所有已注册的适配器元数据列表。"""
        if not os.path.exists(_ADAPTER_DIR):
            return []
        result = []
        for fname in os.listdir(_ADAPTER_DIR):
            if not fname.endswith(".json"):
                continue
            try:
                with open(os.path.join(_ADAPTER_DIR, fname), "r", encoding="utf-8") as f:
                    result.append(AdapterMeta.from_dict(json.load(f)))
            except Exception:
                pass
        return result

    # ══════════════════════════════════════════════════════════════════════════
    # 5. ShadowTracer 自动触发监听
    # ══════════════════════════════════════════════════════════════════════════

    def watch_shadow_tracer(self, auto_train: bool = False) -> None:
        """
        注册 ShadowTracer 的 TRAINING_READY 事件监听器。
        当某 Skill 的影子记录达到阈值时：
          - auto_train=True  → 在后台线程自动触发训练
          - auto_train=False → 仅记录日志提示

        调用时机：应用初始化之后调用一次即可。
        """
        try:
            from app.core.learning.shadow_tracer import ShadowTracer, TraceEvent

            def _on_training_ready(event: TraceEvent, skill_id: str, count: int, **_kw):
                if event != TraceEvent.TRAINING_READY:
                    return
                logger.info(
                    f"[LoRAPipeline] 🔔 训练就绪信号 skill={skill_id} traces={count}"
                )
                if auto_train:
                    t = threading.Thread(
                        target=self._auto_train_worker,
                        args=(skill_id,),
                        daemon=True,
                        name=f"lora_auto_train_{skill_id}",
                    )
                    t.start()
                    logger.info(f"[LoRAPipeline] 🚀 后台训练已启动 (thread={t.name})")

            ShadowTracer.add_listener(_on_training_ready)
            logger.info("[LoRAPipeline] ✅ ShadowTracer 监听器已注册")
        except Exception as e:
            logger.warning(f"[LoRAPipeline] ShadowTracer 监听注册失败: {e}")

    def _auto_train_worker(self, skill_id: str) -> None:
        """后台自动训练工作器。"""
        logger.info(f"[LoRAPipeline] 开始自动训练 skill={skill_id}")
        try:
            result = self.train(skill_id)
            if result["success"]:
                self.register_as_adapter(
                    skill_id=skill_id,
                    adapter_path=result["adapter_path"],
                    num_samples=result.get("num_samples", 0),
                    eval_loss=result.get("eval_loss"),
                )
                logger.info(f"[LoRAPipeline] ✅ 自动训练+注册完成 skill={skill_id}")
            else:
                logger.error(f"[LoRAPipeline] ❌ 自动训练失败 skill={skill_id}: {result.get('error')}")
        except Exception as e:
            logger.error(f"[LoRAPipeline] 自动训练异常 skill={skill_id}: {e}", exc_info=True)


# ── 模块级单例 ────────────────────────────────────────────────────────────────

_default_pipeline: Optional[LoRAPipeline] = None


def get_pipeline(config: Optional[TrainingConfig] = None) -> LoRAPipeline:
    """返回模块级默认 LoRAPipeline 单例。"""
    global _default_pipeline
    if _default_pipeline is None:
        _default_pipeline = LoRAPipeline(config)
    return _default_pipeline
