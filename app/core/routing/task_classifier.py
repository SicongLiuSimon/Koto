"""
TaskClassifier — 内置任务分类器

基于 paraphrase-multilingual-MiniLM-L12-v2 + LogisticRegression，
不依赖用户安装的 Ollama 模型，可跨模型复用。

使用方式::

    from app.core.routing.task_classifier import TaskClassifier

    task_type, confidence = TaskClassifier.classify("帮我写个Python排序函数")
    # → ("CODER", 0.93)

    if TaskClassifier.is_available():
        ...

训练方式::

    python scripts/extract_classifier_data.py
    python scripts/train_task_classifier.py
"""

import logging
import os
import time

# Suppress transformers' background safetensors-conversion network chatter
os.environ.setdefault("TRANSFORMERS_SAFETENSORS_DISABLE_AUTO_CONVERSION", "1")

logger = logging.getLogger(__name__)

# 定位工件目录（相对仓库根目录）
_HERE = os.path.abspath(__file__)  # …/app/core/routing/task_classifier.py
_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(_HERE))))
_MODEL_DIR = os.path.join(_REPO_ROOT, "models", "task_classifier")


class TaskClassifier:
    """
    轻量任务分类器。所有状态均为类变量，首次 classify() 时懒加载，
    后续调用直接复用已加载的模型（<10ms/次）。

    不可用时（工件未训练）gracefully 返回 ("CHAT", 0.0)，不影响上层逻辑。
    """

    _st_model = None
    _clf = None
    _le = None
    _config: dict = {}
    _available: bool | None = None  # None = 尚未检测
    _load_error: str = ""

    # ── 加载 ─────────────────────────────────────────────────────────────────

    @classmethod
    def _load(cls) -> bool:
        """懒加载所有模型工件，返回是否成功。"""
        if cls._available is not None:
            return cls._available

        clf_path = os.path.join(_MODEL_DIR, "clf.pkl")
        le_path = os.path.join(_MODEL_DIR, "label_encoder.pkl")
        config_path = os.path.join(_MODEL_DIR, "config.json")

        # 工件存在性检查
        if not (os.path.exists(clf_path) and os.path.exists(le_path)):
            cls._available = False
            cls._load_error = "工件文件不存在，请先运行 train_task_classifier.py"
            logger.info(f"[TaskClassifier] {cls._load_error}")
            return False

        # 加载 sklearn 工件
        try:
            import pickle

            with open(clf_path, "rb") as f:
                cls._clf = pickle.load(f)
            with open(le_path, "rb") as f:
                cls._le = pickle.load(f)
            if os.path.exists(config_path):
                import json as _json

                with open(config_path, encoding="utf-8") as f:
                    cls._config = _json.load(f)
        except Exception as e:
            cls._available = False
            cls._load_error = f"pickle 加载失败: {e}"
            logger.warning(f"[TaskClassifier] {cls._load_error}")
            return False

        # 加载编码器（支持两种后端）
        backend = cls._config.get("backend", "sentence_transformers")
        model_name = cls._config.get(
            "model_name", "paraphrase-multilingual-MiniLM-L12-v2"
        )

        try:
            t0 = time.time()
            if backend == "transformers_mean_pool":
                # 新版：使用 transformers 直接加载，均值池化，无需 sentence-transformers
                from transformers import AutoModel, AutoTokenizer

                cls._st_model = {
                    "tokenizer": AutoTokenizer.from_pretrained(model_name),
                    "model": AutoModel.from_pretrained(model_name),
                    "backend": "transformers_mean_pool",
                }
            else:
                # 旧版：sentence-transformers 后端
                from sentence_transformers import SentenceTransformer

                st_cache_dir = cls._config.get("st_cache_dir", "st_model")
                if st_cache_dir:
                    st_cache = os.path.join(_MODEL_DIR, st_cache_dir)
                    cls._st_model = SentenceTransformer(
                        model_name, cache_folder=st_cache
                    )
                else:
                    cls._st_model = SentenceTransformer(model_name)
            elapsed = time.time() - t0

            classes_str = ", ".join(cls._config.get("classes", []))
            logger.info(
                f"[TaskClassifier] 已加载 (backend={backend})，耗时 {elapsed:.2f}s  "
                f"类别: [{classes_str}]  "
                f"训练准确率: {cls._config.get('train_accuracy', '?')}"
            )
        except ImportError as e:
            cls._available = False
            cls._load_error = f"缺少依赖: {e}"
            logger.warning(f"[TaskClassifier] {cls._load_error}")
            return False
        except Exception as e:
            cls._available = False
            cls._load_error = f"模型加载失败: {e}"
            logger.warning(f"[TaskClassifier] {cls._load_error}")
            return False

        cls._available = True
        return True

    # ── 公开接口 ──────────────────────────────────────────────────────────────

    @classmethod
    def is_available(cls) -> bool:
        """返回分类器是否可用（工件已训练且依赖已安装）。"""
        return cls._load()

    @classmethod
    def classify(cls, text: str) -> tuple[str, float]:
        """
        将输入文本分类为任务类型。

        Returns:
            (task_type, confidence)
            例: ("CODER", 0.93)
            不可用时返回 ("CHAT", 0.0)。
        """
        if not cls._load():
            return "CHAT", 0.0

        try:
            import numpy as np

            t0 = time.time()
            if (
                isinstance(cls._st_model, dict)
                and cls._st_model.get("backend") == "transformers_mean_pool"
            ):
                import torch
                import torch.nn.functional as F

                tokenizer = cls._st_model["tokenizer"]
                model = cls._st_model["model"]
                model.eval()
                encoded = tokenizer(
                    [text],
                    padding=True,
                    truncation=True,
                    max_length=128,
                    return_tensors="pt",
                )
                with torch.no_grad():
                    out = model(**encoded)
                mask_expanded = (
                    encoded["attention_mask"]
                    .unsqueeze(-1)
                    .expand(out.last_hidden_state.size())
                    .float()
                )
                sum_emb = torch.sum(out.last_hidden_state * mask_expanded, 1)
                sum_mask = torch.clamp(mask_expanded.sum(1), min=1e-9)
                emb = sum_emb / sum_mask
                emb = F.normalize(emb, p=2, dim=1)
                embedding = emb.cpu().numpy()
            else:
                embedding = cls._st_model.encode([text], normalize_embeddings=True)
            probs = cls._clf.predict_proba(embedding)[0]
            best_idx = int(np.argmax(probs))
            task_type = str(cls._le.inverse_transform([best_idx])[0])
            confidence = float(probs[best_idx])
            elapsed_ms = int((time.time() - t0) * 1000)

            logger.debug(
                f"[TaskClassifier] '{text[:40]}' → {task_type} "
                f"({confidence:.2f}, {elapsed_ms}ms)"
            )
            return task_type, confidence

        except Exception as e:
            logger.warning(f"[TaskClassifier] classify() 异常: {e}")
            return "CHAT", 0.0

    @classmethod
    def classify_with_scores(cls, text: str) -> tuple[str, float, dict]:
        """
        返回最优类别 + 置信度 + 全类别概率字典（用于调试/可视化）。

        Returns:
            (task_type, confidence, {label: prob, ...})
        """
        if not cls._load():
            return "CHAT", 0.0, {}

        try:
            import numpy as np

            if (
                isinstance(cls._st_model, dict)
                and cls._st_model.get("backend") == "transformers_mean_pool"
            ):
                import torch
                import torch.nn.functional as F

                tokenizer = cls._st_model["tokenizer"]
                model = cls._st_model["model"]
                model.eval()
                encoded = tokenizer(
                    [text],
                    padding=True,
                    truncation=True,
                    max_length=128,
                    return_tensors="pt",
                )
                with torch.no_grad():
                    out = model(**encoded)
                mask_expanded = (
                    encoded["attention_mask"]
                    .unsqueeze(-1)
                    .expand(out.last_hidden_state.size())
                    .float()
                )
                sum_emb = torch.sum(out.last_hidden_state * mask_expanded, 1)
                sum_mask = torch.clamp(mask_expanded.sum(1), min=1e-9)
                emb = sum_emb / sum_mask
                embedding = F.normalize(emb, p=2, dim=1).cpu().numpy()
            else:
                embedding = cls._st_model.encode([text], normalize_embeddings=True)
            probs = cls._clf.predict_proba(embedding)[0]
            best_idx = int(np.argmax(probs))
            task_type = str(cls._le.inverse_transform([best_idx])[0])
            confidence = float(probs[best_idx])

            all_scores = {
                str(cls._le.inverse_transform([i])[0]): round(float(p), 4)
                for i, p in enumerate(probs)
            }
            return task_type, confidence, all_scores

        except Exception as e:
            logger.warning(f"[TaskClassifier] classify_with_scores() 异常: {e}")
            return "CHAT", 0.0, {}

    @classmethod
    def reload(cls) -> bool:
        """强制重新加载模型（训练新版本后调用）。"""
        cls._st_model = None
        cls._clf = None
        cls._le = None
        cls._config = {}
        cls._available = None
        cls._load_error = ""
        return cls._load()
