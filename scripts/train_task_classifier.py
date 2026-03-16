#!/usr/bin/env python3
"""
训练任务分类器：已缓存的 BERT 模型均值池化 + LogisticRegression 分类头。

使用已在全局 HF 缓存中完整下载的模型，无需任何额外下载。
模型优先级：hfl/chinese-roberta-wwm-ext (392MB, 已缓存)

工件输出到 models/task_classifier/：
  clf.pkl            — 训练好的 sklearn 分类器
  label_encoder.pkl  — 标签编码器
  config.json        — 元数据（模型名、类别列表、训练准确率等）

用法：
  # 先提取数据（如尚未执行）：
  python scripts/extract_classifier_data.py

  # 训练：
  python scripts/train_task_classifier.py
"""

import json
import os
import pickle
import sys
import time
import warnings
from collections import Counter

# Suppress transformers background safetensors-conversion network errors
os.environ.setdefault("TRANSFORMERS_SAFETENSORS_DISABLE_AUTO_CONVERSION", "1")

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
MODEL_DIR = os.path.join(REPO_ROOT, "models", "task_classifier")
DATA_PATH = os.path.join(MODEL_DIR, "training_data.json")

# 已缓存模型优先级列表
CANDIDATE_MODELS = [
    "hfl/chinese-roberta-wwm-ext",           # 392MB 已缓存，中文最佳
    "sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2",  # 多语言，如已缓存
]


def _mean_pool(token_embeddings, attention_mask):
    """Mean pooling over non-padding tokens."""
    import torch
    mask_expanded = attention_mask.unsqueeze(-1).expand(token_embeddings.size()).float()
    sum_emb = torch.sum(token_embeddings * mask_expanded, 1)
    sum_mask = torch.clamp(mask_expanded.sum(1), min=1e-9)
    return sum_emb / sum_mask


def _encode_texts(tokenizer, model, texts, batch_size=32, device=None):
    """Encode texts using mean-pooled BERT embeddings."""
    import torch
    import torch.nn.functional as F
    import numpy as np

    if device is None:
        device = "cuda" if torch.cuda.is_available() else "cpu"
    model = model.to(device)
    model.eval()

    all_embeddings = []
    for i in range(0, len(texts), batch_size):
        batch = texts[i:i + batch_size]
        encoded = tokenizer(
            batch,
            padding=True,
            truncation=True,
            max_length=128,
            return_tensors="pt",
        )
        encoded = {k: v.to(device) for k, v in encoded.items()}
        with torch.no_grad():
            out = model(**encoded)
        emb = _mean_pool(out.last_hidden_state, encoded["attention_mask"])
        emb = F.normalize(emb, p=2, dim=1)
        all_embeddings.append(emb.cpu().numpy())
        print(f"  已处理 {min(i + batch_size, len(texts))}/{len(texts)}", end="\r")
    print()
    return np.vstack(all_embeddings)


def _find_best_model():
    """Return (model_name, tokenizer, model) for the best available cached model."""
    from huggingface_hub import constants
    hf_cache = constants.HF_HUB_CACHE

    for model_name in CANDIDATE_MODELS:
        cache_key = "models--" + model_name.replace("/", "--")
        cache_path = os.path.join(hf_cache, cache_key)
        if not os.path.exists(cache_path):
            print(f"[Train] 跳过 {model_name}（未缓存）")
            continue
        # 检查权重文件大小确认完整下载
        total_mb = sum(
            os.path.getsize(os.path.join(r, f)) / 1024 / 1024
            for r, dirs, files in os.walk(cache_path)
            for f in files
            if f.endswith((".safetensors", ".bin"))
        )
        if total_mb < 50:
            print(f"[Train] 跳过 {model_name}（权重不完整: {total_mb:.0f}MB）")
            continue

        print(f"[Train] 使用已缓存模型: {model_name} ({total_mb:.0f}MB)")
        try:
            from transformers import AutoTokenizer, AutoModel
            tokenizer = AutoTokenizer.from_pretrained(model_name)
            model = AutoModel.from_pretrained(model_name)
            return model_name, tokenizer, model
        except Exception as e:
            print(f"[Train] 加载 {model_name} 失败: {e}")
            continue

    return None, None, None


def _ensure_sklearn():
    try:
        import sklearn  # noqa: F401
    except ImportError:
        print("[Train] 安装 scikit-learn")
        os.system(f"{sys.executable} -m pip install scikit-learn")


def main():
    # 0. 依赖检查
    _ensure_sklearn()

    from sklearn.linear_model import LogisticRegression
    from sklearn.preprocessing import LabelEncoder
    from sklearn.model_selection import StratifiedKFold, cross_val_score

    # 1. 加载数据
    if not os.path.exists(DATA_PATH):
        print(f"[Train] 找不到训练数据: {DATA_PATH}")
        print("[Train] 请先运行：python scripts/extract_classifier_data.py")
        sys.exit(1)

    with open(DATA_PATH, encoding="utf-8") as f:
        samples = json.load(f)

    texts  = [s["text"]  for s in samples]
    labels = [s["label"] for s in samples]

    print(f"[Train] 加载了 {len(texts)} 条样本")
    counts = Counter(labels)
    for label, cnt in sorted(counts.items()):
        print(f"  {label:<15}: {cnt}")

    # 2. 找到可用的已缓存模型
    model_name, tokenizer, bert_model = _find_best_model()
    if model_name is None:
        print("[Train] 找不到已缓存的可用模型")
        print("[Train] 请下载模型后重试:")
        print("  python -c \"from transformers import AutoTokenizer,AutoModel; "
              "AutoTokenizer.from_pretrained('hfl/chinese-roberta-wwm-ext'); "
              "AutoModel.from_pretrained('hfl/chinese-roberta-wwm-ext')\"")
        sys.exit(1)

    # 3. 文本向量化
    print("[Train] 向量化文本...")
    t0 = time.time()
    X = _encode_texts(tokenizer, bert_model, texts)
    print(f"[Train] 向量化完成，耗时 {time.time()-t0:.1f}s，shape: {X.shape}")

    # 4. 标签编码
    le = LabelEncoder()
    y  = le.fit_transform(labels)

    # 5. 训练 LogisticRegression
    print("\n[Train] 训练 LogisticRegression...")
    clf = LogisticRegression(
        C=4.0,
        max_iter=1000,
        solver="lbfgs",
        random_state=42,
    )

    min_class_count = min(counts.values())
    n_splits = min(5, min_class_count)
    if n_splits >= 2:
        skf = StratifiedKFold(n_splits=n_splits, shuffle=True, random_state=42)
        cv_scores = cross_val_score(clf, X, y, cv=skf, scoring="accuracy")
        print(f"[Train] {n_splits}-折交叉验证准确率: "
              f"{cv_scores.mean():.3f} ± {cv_scores.std():.3f}  "
              f"(每折: {', '.join(f'{s:.3f}' for s in cv_scores)})")
    else:
        print("[Train] 样本量不足，跳过交叉验证")

    t0 = time.time()
    clf.fit(X, y)
    train_acc = clf.score(X, y)
    print(f"[Train] 训练集准确率: {train_acc:.3f}  耗时 {time.time()-t0:.1f}s")

    # 6. 保存工件
    os.makedirs(MODEL_DIR, exist_ok=True)
    clf_path    = os.path.join(MODEL_DIR, "clf.pkl")
    le_path     = os.path.join(MODEL_DIR, "label_encoder.pkl")
    config_path = os.path.join(MODEL_DIR, "config.json")

    with open(clf_path, "wb") as f:
        pickle.dump(clf, f)
    with open(le_path, "wb") as f:
        pickle.dump(le, f)

    config = {
        "model_name":     model_name,
        "backend":        "transformers_mean_pool",
        "st_cache_dir":   "",
        "n_samples":      len(texts),
        "classes":        list(le.classes_),
        "train_accuracy": round(float(train_acc), 4),
        "version":        "1.1",
    }
    with open(config_path, "w", encoding="utf-8") as f:
        json.dump(config, f, ensure_ascii=False, indent=2)

    print(f"\n[Train] 工件已保存到 {MODEL_DIR}/")
    print(f"  文件: clf.pkl  label_encoder.pkl  config.json")
    print(f"  类别: {list(le.classes_)}")

    # 7. 快速健壮性测试
    print("\n[Train] 健壮性抽查:")
    test_cases = [
        ("打开微信",                         "SYSTEM"),
        ("启动vscode",                       "SYSTEM"),
        ("今天北京天气怎么样",                 "WEB_SEARCH"),
        ("比特币现在价格",                     "WEB_SEARCH"),
        ("帮我写一个Python排序函数",           "CODER"),
        ("用matplotlib画折线图",             "CODER"),
        ("你好",                              "CHAT"),
        ("什么是量子计算",                     "CHAT"),
        ("生成一份项目报告Word文档",            "FILE_GEN"),
        ("做一个产品介绍PPT",                 "FILE_GEN"),
        ("画一张宇宙飞船图片",                 "PAINTER"),
        ("提醒我下午3点开会",                  "AGENT"),
        ("给张三发微信",                       "AGENT"),
        ("深入分析新能源汽车市场",              "RESEARCH"),
        ("[FILE_ATTACHED:.docx] 润色这篇报告", "DOC_ANNOTATE"),
        ("如何写一个排序算法",                 "CHAT"),
        ("帮我画一个柱状图",                   "CODER"),
    ]

    test_texts  = [t for t, _ in test_cases]
    test_embeds = _encode_texts(tokenizer, bert_model, test_texts)
    probs       = clf.predict_proba(test_embeds)

    ok = total = 0
    for (text, expected), prob in zip(test_cases, probs):
        total += 1
        pred_idx   = int(prob.argmax())
        pred_label = le.inverse_transform([pred_idx])[0]
        conf       = float(prob[pred_idx])
        status     = "OK" if pred_label == expected else "FAIL"
        if pred_label == expected:
            ok += 1
        print(f"  [{status}] {text[:35]:<35} -> {pred_label:<12} {conf:.2f}  (expected {expected})")

    print(f"\n[Train] 抽查通过率: {ok}/{total} ({ok/total*100:.0f}%)")
    if ok / total < 0.75:
        print("[Train] 通过率偏低，建议补充更多训练样本后重新训练")


if __name__ == "__main__":
    main()