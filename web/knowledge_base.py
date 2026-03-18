#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
本地知识库系统 - 向量化语义搜索 + 全文检索
支持：PDF、Word、Markdown、TXT 等格式
使用 Gemini text-embedding-004 实现语义搜索
"""

import hashlib
import json
import logging
import os
import re
import time
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

import numpy as np

logger = logging.getLogger(__name__)

try:
    import google.genai as genai  # New SDK
except ImportError:
    try:
        import google.generativeai as genai  # Fallback to deprecated SDK
    except ImportError:
        genai = None


class KnowledgeBase:
    """向量化知识库管理器 - 使用 Gemini 嵌入和余弦相似度搜索"""

    CHUNK_SIZE = 500  # 文本块大小
    CHUNK_OVERLAP = 50  # 块重叠
    BATCH_SIZE = 20  # 批量嵌入的大小

    def __init__(self, workspace_dir: str = None, api_key: str = None):
        if workspace_dir is None:
            workspace_dir = os.path.join(
                os.path.dirname(os.path.dirname(__file__)), "workspace"
            )

        self.workspace_dir = workspace_dir
        self.kb_dir = os.path.join(workspace_dir, "knowledge_base")
        self.index_file = os.path.join(self.kb_dir, "index.json")
        self.chunks_file = os.path.join(self.kb_dir, "chunks.json")
        os.makedirs(self.kb_dir, exist_ok=True)

        # Initialize Gemini API
        api_key = api_key or os.getenv("GEMINI_API_KEY")
        self.client = None
        self.embedding_model = "text-embedding-004"

        if api_key and genai:
            try:
                if hasattr(genai, "Client"):
                    # New SDK
                    self.client = genai.Client(api_key=api_key)
                else:
                    # Old SDK - wrap it to mimic new client structure?
                    # Or just set it and handle differences in methods
                    genai.configure(api_key=api_key)

                    # Create a shim for old SDK to look like new SDK client.models.embed_content
                    class OldSDKShim:
                        def __init__(self, old_genai):
                            self._genai = old_genai
                            self.models = self

                        def embed_content(self, model, content):
                            # Adapt new SDK call to old SDK
                            # old: result = genai.embed_content(model=..., content=...)
                            # result['embedding'] is the vector
                            res = self._genai.embed_content(
                                model=model, content=content
                            )

                            class ShimResponse:
                                pass

                            r = ShimResponse()
                            r.embedding = res["embedding"]
                            return r

                        def batch_embed_contents(self, model, requests):
                            # Adapt batch
                            texts = [r["content"] for r in requests]
                            res = self._genai.embed_content(model=model, content=texts)

                            # res['embedding'] is list of vectors if content is list
                            class ShimBatchResponse:
                                pass

                            r = ShimBatchResponse()
                            r.embeddings = [
                                type("obj", (), {"values": v}) for v in res["embedding"]
                            ]
                            return r

                    self.client = OldSDKShim(genai)

            except Exception as e:
                logger.info(f"[KnowledgeBase] API initialization error: {e}")

        # Load data
        self.index = self._load_index()
        self.chunks = self._load_chunks()
        self._vector_cache = None  # Lazy load vector cache

    def _load_index(self) -> Dict:
        """加载文档索引"""
        if os.path.exists(self.index_file):
            try:
                with open(self.index_file, "r", encoding="utf-8") as f:
                    return json.load(f)
            except Exception as e:
                logger.info(f"[KnowledgeBase] 索引加载失败: {e}")
        return {"documents": {}, "last_updated": None}

    def _save_index(self):
        """保存文档索引"""
        self.index["last_updated"] = datetime.now().isoformat()
        with open(self.index_file, "w", encoding="utf-8") as f:
            json.dump(self.index, f, ensure_ascii=False, indent=2)

    def _load_chunks(self) -> Dict:
        """加载文本块和向量"""
        if os.path.exists(self.chunks_file):
            try:
                with open(self.chunks_file, "r", encoding="utf-8") as f:
                    return json.load(f)
            except Exception as e:
                logger.info(f"[KnowledgeBase] 块文件加载失败: {e}")
        return {"chunks": {}, "last_updated": None}

    def _save_chunks(self):
        """保存文本块和向量"""
        self.chunks["last_updated"] = datetime.now().isoformat()
        with open(self.chunks_file, "w", encoding="utf-8") as f:
            json.dump(self.chunks, f, ensure_ascii=False, indent=2)

    def _chunk_text(self, text: str) -> List[str]:
        """将文本分块，带有重叠"""
        if not text or len(text) <= self.CHUNK_SIZE:
            return [text] if text else []

        chunks = []
        for i in range(0, len(text), self.CHUNK_SIZE - self.CHUNK_OVERLAP):
            if i == 0:
                end = self.CHUNK_SIZE
            else:
                end = i + self.CHUNK_SIZE

            chunk = text[i:end]
            if len(chunk.strip()) > 50:  # 跳过太短的块
                chunks.append(chunk)

        return chunks

    def _get_embeddings(self, texts: List[str]) -> List[List[float]]:
        """批量调用 Gemini API 获取嵌入向量"""
        if not texts or not self.client:
            # Return zero vectors if no API key
            return [[0.0] * 768 for _ in texts]

        embeddings = []
        for i in range(0, len(texts), self.BATCH_SIZE):
            batch = texts[i : i + self.BATCH_SIZE]
            try:
                # Try new SDK first, fall back to old
                try:
                    resp = self.client.models.batch_embed_contents(
                        model=self.embedding_model,
                        requests=[
                            {"model": self.embedding_model, "content": text}
                            for text in batch
                        ],
                    )
                    for emb in resp.embeddings:
                        embeddings.append(emb.values)
                except (AttributeError, TypeError):
                    # Fallback for old SDK or API errors
                    for text in batch:
                        embeddings.append([0.0] * 768)
            except Exception as e:
                logger.info(f"[KnowledgeBase] Embedding error: {e}")
                # Return zero vectors on failure
                embeddings.extend([[0.0] * 768 for _ in batch])

        return embeddings

    def _update_vector_cache(self):
        """从 chunks.json 重建向量缓存 (并归一化)"""
        vectors = []
        self._chunk_ids_cache = []  # Parallel list of IDs

        for chunk_id, chunk_data in self.chunks.get("chunks", {}).items():
            if "embedding" in chunk_data and chunk_data["embedding"]:
                vectors.append(chunk_data["embedding"])
                self._chunk_ids_cache.append(chunk_id)

        if vectors:
            matrix = np.array(vectors)
            # L2 Normalize for Cosine Similarity
            norms = np.linalg.norm(matrix, axis=1, keepdims=True)
            # Avoid division by zero
            norms[norms == 0] = 1e-9
            self._vector_cache = matrix / norms
        else:
            self._vector_cache = np.zeros((0, 768))  # 默认维度

    def search(self, query: str, top_k: int = 5) -> List[Dict[str, Any]]:
        """语义搜索：查询嵌入 → 余弦相似度 → 返回top-k"""
        if not self.chunks.get("chunks"):
            return []

        if self._vector_cache is None:
            self._update_vector_cache()

        if self._vector_cache.shape[0] == 0:
            return []

        try:
            # Generate query embedding
            if not self.client:
                return []

            # Compatible with different SDK versions
            embedding_model = self.embedding_model or "models/text-embedding-004"
            if "models/" not in embedding_model:
                embedding_model = f"models/{embedding_model}"

            # New SDK usually returns an object with `embeddings` list or `embedding`
            try:
                resp = self.client.models.embed_content(
                    model=embedding_model, contents=query
                )
                # Check response structure
                if hasattr(resp, "embeddings") and resp.embeddings:
                    q_vec = resp.embeddings[0].values
                elif hasattr(resp, "embedding"):
                    q_vec = resp.embedding
                else:
                    logger.info("[KB] Unexpected embedding response format")
                    return []
            except Exception as embed_err:
                # Fallback for old SDK structure?
                logger.info(f"[KB] Embedding error: {embed_err}")
                return []

            query_vec = np.array(q_vec)
            # Normalize query vector
            q_norm = np.linalg.norm(query_vec)
            if q_norm > 0:
                query_vec = query_vec / q_norm

            # Compute Cosine Similarity (Dot product of normalized vectors)
            similarities = np.dot(self._vector_cache, query_vec)

            # Get Top-K
            # Ensure top_k doesn't exceed available chunks
            k = min(top_k, len(similarities))
            if k == 0:
                return []

            top_indices = np.argsort(similarities)[-k:][::-1]

            results = []
            for idx in top_indices:
                score = float(similarities[idx])
                if score < 0.45:
                    continue  # Minimum threshold

                chunk_id = self._chunk_ids_cache[idx]
                chunk_data = self.chunks["chunks"][chunk_id]
                doc_id = chunk_data["doc_id"]
                doc_info = self.index.get("documents", {}).get(doc_id, {})

                # Fetch metadata
                meta = chunk_data.get("metadata", {})

                results.append(
                    {
                        "chunk_id": chunk_id,
                        "doc_id": doc_id,
                        "file_name": doc_info.get(
                            "file_name", meta.get("file_name", "Unknown")
                        ),
                        "text": chunk_data["text"],
                        "similarity": score,
                        "metadata": meta,
                    }
                )

            return results

        except Exception as e:
            logger.info(f"[KnowledgeBase] Search failed: {e}")
            import traceback

            traceback.print_exc()
            return []

    def _extract_text(self, file_path: str) -> str:
        """提取文件文本内容"""
        ext = os.path.splitext(file_path)[1].lower()

        try:
            if ext == ".txt" or ext == ".md":
                with open(file_path, "r", encoding="utf-8") as f:
                    return f.read()

            elif ext == ".docx":
                try:
                    from docx import Document

                    doc = Document(file_path)
                    paragraphs = [p.text for p in doc.paragraphs if p.text.strip()]
                    return "\n".join(paragraphs)
                except ImportError:
                    return "[需要安装 python-docx]"

            elif ext == ".pdf":
                try:
                    import PyPDF2

                    with open(file_path, "rb") as f:
                        reader = PyPDF2.PdfReader(f)
                        text = []
                        for page in reader.pages:
                            text.append(page.extract_text())
                        return "\n".join(text)
                except ImportError:
                    return "[需要安装 PyPDF2]"

            else:
                return "[不支持的格式]"

        except Exception as e:
            return f"[提取失败: {str(e)}]"

    def _calculate_hash(self, file_path: str) -> str:
        """计算文件哈希"""
        hasher = hashlib.md5()
        with open(file_path, "rb") as f:
            for chunk in iter(lambda: f.read(4096), b""):
                hasher.update(chunk)
        return hasher.hexdigest()

    def add_content(self, text: str, metadata: Dict[str, Any]) -> Dict[str, Any]:
        """直接添加文本内容到知识库 (用于处理已提取文本的文件)"""
        if not text:
            return {"success": False, "error": "文本内容为空"}

        file_path = metadata.get("file_path", "unknown")
        file_name = metadata.get("file_name", "unknown")

        # 使用内容哈希作为ID, 避免重复内容
        content_hash = hashlib.md5(text.encode("utf-8")).hexdigest()

        # 检查是否已存在
        if content_hash in self.index.get("documents", {}):
            return {"success": True, "message": "内容已存在", "doc_id": content_hash}

        # 分块
        chunks = self._chunk_text(text)
        if not chunks:
            return {"success": False, "error": "无法分块"}

        # 批量嵌入
        embeddings = self._get_embeddings(chunks)

        # 保存块和向量
        chunk_ids = []
        for i, (chunk, emb) in enumerate(zip(chunks, embeddings)):
            chunk_id = f"{content_hash}_{i}"
            # 兼容旧结构，如果没有 self.chunks["chunks"] 则初始化
            if "chunks" not in self.chunks:
                self.chunks["chunks"] = {}

            self.chunks["chunks"][chunk_id] = {
                "doc_id": content_hash,
                "chunk_index": i,
                "text": chunk,
                "embedding": emb,
                "created_at": datetime.now().isoformat(),
                "metadata": metadata,
            }
            chunk_ids.append(chunk_id)

        # 保存文档记录
        doc_record = {
            "file_path": file_path,
            "file_name": file_name,
            "file_type": metadata.get("file_type", ".txt"),
            "file_hash": content_hash,
            "mtime": metadata.get("mtime", time.time()),
            "size": len(text),  # 近似大小
            "text_length": len(text),
            "chunk_count": len(chunks),
            "indexed_at": datetime.now().isoformat(),
            "chunk_ids": chunk_ids,
        }

        if "documents" not in self.index:
            self.index["documents"] = {}
        self.index["documents"][content_hash] = doc_record

        self._save_index()
        self._save_chunks()
        self._vector_cache = None  # 清除缓存

        return {
            "success": True,
            "message": "内容已添加到知识库",
            "doc_id": content_hash,
            "chunks": len(chunks),
        }

    def add_document(self, file_path: str) -> Dict[str, Any]:
        """添加文档：文本提取 → 分块 → 嵌入 → 存储"""
        if not os.path.exists(file_path):
            return {"success": False, "error": "文件不存在"}

        file_hash = self._calculate_hash(file_path)

        # 检查是否已存在
        if file_hash in self.index["documents"]:
            doc = self.index["documents"][file_hash]
            if doc["file_path"] == file_path and os.path.getmtime(file_path) == doc.get(
                "mtime"
            ):
                return {
                    "success": True,
                    "message": "文档已存在且未修改",
                    "doc_id": file_hash,
                }

        # 提取文本
        text = self._extract_text(file_path)
        if text.startswith("["):
            return {"success": False, "error": text}

        # 分块
        chunks = self._chunk_text(text)
        if not chunks:
            return {"success": False, "error": "无法分块"}

        # 批量嵌入
        embeddings = self._get_embeddings(chunks)

        # 保存块和向量
        chunk_ids = []
        for i, (chunk, emb) in enumerate(zip(chunks, embeddings)):
            chunk_id = f"{file_hash}_{i}"
            self.chunks["chunks"][chunk_id] = {
                "doc_id": file_hash,
                "chunk_index": i,
                "text": chunk,
                "embedding": emb,
                "created_at": datetime.now().isoformat(),
            }
            chunk_ids.append(chunk_id)

        # 保存文档记录
        doc_record = {
            "file_path": file_path,
            "file_name": os.path.basename(file_path),
            "file_type": os.path.splitext(file_path)[1],
            "file_hash": file_hash,
            "mtime": os.path.getmtime(file_path),
            "size": os.path.getsize(file_path),
            "text_length": len(text),
            "chunk_count": len(chunks),
            "indexed_at": datetime.now().isoformat(),
            "chunk_ids": chunk_ids,
        }

        self.index["documents"][file_hash] = doc_record
        self._save_index()
        self._save_chunks()
        self._vector_cache = None  # 清除缓存，下次搜索重建

        return {
            "success": True,
            "message": "文档已添加",
            "doc_id": file_hash,
            "chunks": len(chunks),
        }

    def scan_directory(self, directory: str = None) -> Dict[str, Any]:
        """扫描目录，批量添加文档"""
        if directory is None:
            directory = os.path.join(self.workspace_dir, "documents")

        if not os.path.exists(directory):
            return {"success": False, "error": "目录不存在"}

        supported_exts = [".txt", ".md", ".docx", ".pdf"]
        added = []
        skipped = []
        errors = []

        for root, dirs, files in os.walk(directory):
            for file in files:
                ext = os.path.splitext(file)[1].lower()
                if ext in supported_exts:
                    file_path = os.path.join(root, file)
                    result = self.add_document(file_path)

                    if result["success"]:
                        if "已存在" in result["message"]:
                            skipped.append(file)
                        else:
                            added.append(file)
                    else:
                        errors.append(f"{file}: {result.get('error')}")

        return {
            "success": True,
            "added": len(added),
            "skipped": len(skipped),
            "errors": len(errors),
            "details": {"added": added, "skipped": skipped, "errors": errors},
        }

    def get_stats(self) -> Dict[str, Any]:
        """获取知识库统计信息"""
        total_size = sum(doc.get("size", 0) for doc in self.index["documents"].values())
        file_types = {}
        for doc in self.index["documents"].values():
            ft = doc.get("file_type", "unknown")
            file_types[ft] = file_types.get(ft, 0) + 1

        return {
            "total_documents": len(self.index["documents"]),
            "total_chunks": len(self.chunks["chunks"]),
            "total_size_mb": round(total_size / 1024 / 1024, 2),
            "file_types": file_types,
            "last_updated": self.index.get("last_updated"),
        }

    def remove_document(self, doc_id: str) -> Dict[str, Any]:
        """删除文档和关联的块"""
        if doc_id not in self.index["documents"]:
            return {"success": False, "error": "文档不存在"}

        doc = self.index["documents"][doc_id]
        chunk_ids = doc.get("chunk_ids", [])

        # 删除块
        for chunk_id in chunk_ids:
            self.chunks["chunks"].pop(chunk_id, None)

        # 删除文档
        del self.index["documents"][doc_id]

        self._save_index()
        self._save_chunks()
        self._vector_cache = None

        return {"success": True, "message": f"已删除文档及其 {len(chunk_ids)} 个块"}


# ==================== 测试示例 ====================
if __name__ == "__main__":
    kb = KnowledgeBase()

    logger.info("=" * 50)
    logger.info("Koto 向量化知识库测试")
    logger.info("=" * 50)

    # 1. 扫描目录
    logger.info("\n1. 扫描文档目录...")
    result = kb.scan_directory()
    logger.info(f"   添加: {result['added']} 个")
    logger.info(f"   跳过: {result['skipped']} 个")
    logger.error(f"   错误: {result['errors']} 个")

    # 2. 统计信息
    logger.info("\n2. 知识库统计...")
    stats = kb.get_stats()
    logger.info(f"   文档总数: {stats['total_documents']}")
    logger.info(f"   块总数: {stats['total_chunks']}")
    logger.info(f"   总大小: {stats['total_size_mb']} MB")
    logger.info(f"   文件类型: {stats['file_types']}")

    # 3. 语义搜索测试
    logger.info("\n3. 语义搜索测试 (查询: 'Koto')...")
    results = kb.search("Koto", top_k=3)
    logger.info(f"   找到 {len(results)} 个相关块")
    for r in results:
        logger.info(f"   - {r['file_name']} (相似度: {r['similarity']:.3f})")
        logger.info(f"     {r['text'][:80]}...")
