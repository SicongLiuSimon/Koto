# -*- coding: utf-8 -*-
"""
Koto RAG Service (Retrieval-Augmented Generation)
==================================================
为 Koto 添加向量检索能力 —— 让 Agent 在回答问题前，先从本地知识库中
检索相关片段作为上下文，大幅提升回答质量和事实准确性。

架构选型决策：
┌─────────────────────────────────────────────────────────────────────┐
│  向量数据库：FAISS (本地文件，无服务器，毫秒级检索)                   │
│  嵌入模型  ：Google text-embedding-004（复用已有 API Key）             │
│             降级方案：langchain sentence-transformers（全本地）         │
│  分块策略  ：RecursiveCharacterTextSplitter + 中文分词兼容              │
│  检索策略  ：Cosine Similarity Top-K                                    │
│  持久化    ：config/rag_index/ 目录（FAISS 二进制文件 + JSON 元数据）   │
└─────────────────────────────────────────────────────────────────────┘

支持的文档类型（通过 langchain-community loaders）：
  - TXT, Markdown
  - PDF (PyPDF2 / pdfplumber 已安装)
  - DOCX (python-docx 已安装)
  - CSV, JSON
  - 纯文本注入

用法：
    from app.core.services.rag_service import RAGService

    rag = RAGService()

    # 索引文档
    rag.index_file("/path/to/doc.pdf")
    rag.index_text("Koto 是一款 AI 桌面助手...", source="about_koto")

    # 检索
    chunks = rag.retrieve("Koto 有什么功能？", k=5)

    # 一站式 RAG 问答
    answer = rag.rag_answer("Koto 支持哪些文件格式？")
    print(answer["answer"], answer["sources"])
"""

from __future__ import annotations

import json
import logging
import os
import time
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)

# ── 默认路径 ─────────────────────────────────────────────────────────────────
_DEFAULT_INDEX_DIR = str(
    Path(os.environ.get("KOTO_DB_DIR", Path(__file__).parent.parent.parent.parent / "config"))
    / "rag_index"
)

# ── 单例缓存 ──────────────────────────────────────────────────────────────────
_rag_instance: Optional["RAGService"] = None


def get_rag_service(index_dir: Optional[str] = None) -> "RAGService":
    """获取全局 RAGService 单例。"""
    global _rag_instance
    if _rag_instance is None:
        _rag_instance = RAGService(index_dir=index_dir)
    return _rag_instance


# ─────────────────────────────────────────────────────────────────────────────
# Embedding 工厂
# ─────────────────────────────────────────────────────────────────────────────

def _get_embeddings(prefer_local: bool = False):
    """
    获取嵌入模型。

    优先级：
      1. Google text-embedding-004（需要 GEMINI_API_KEY，效果最佳）
      2. sentence-transformers all-MiniLM-L6-v2（本地，~90MB，无需 API）

    参数:
        prefer_local: True = 跳过 Google，直接使用本地模型
    """
    if not prefer_local:
        api_key = (
            os.environ.get("GEMINI_API_KEY")
            or os.environ.get("API_KEY")
            or os.environ.get("GOOGLE_API_KEY")
        )
        if api_key:
            try:
                from langchain_google_genai import GoogleGenerativeAIEmbeddings
                emb = GoogleGenerativeAIEmbeddings(
                    model="models/text-embedding-004",
                    google_api_key=api_key,
                )
                logger.info("[RAGService] 嵌入模型: Google text-embedding-004")
                return emb
            except Exception as exc:
                logger.warning(f"[RAGService] Google Embeddings 初始化失败: {exc}，尝试本地模型")

    # 本地 fallback
    try:
        from langchain_community.embeddings import HuggingFaceEmbeddings
        emb = HuggingFaceEmbeddings(
            model_name="all-MiniLM-L6-v2",
            model_kwargs={"device": "cpu"},
            encode_kwargs={"normalize_embeddings": True},
        )
        logger.info("[RAGService] 嵌入模型: HuggingFace all-MiniLM-L6-v2 (本地)")
        return emb
    except Exception as exc:
        raise RuntimeError(
            f"[RAGService] 无法加载嵌入模型。\n"
            f"请安装：pip install langchain-google-genai（云端）\n"
            f"或：pip install sentence-transformers（本地）\n"
            f"错误: {exc}"
        ) from exc


# ─────────────────────────────────────────────────────────────────────────────
# RAGService
# ─────────────────────────────────────────────────────────────────────────────

class RAGService:
    """
    Koto 向量检索服务。

    核心方法：
        index_file(path)         : 索引文件（自动识别类型）
        index_text(text, source) : 索引原始文本
        retrieve(query, k)       : 检索 top-k 相关片段
        rag_answer(question)     : 检索 + LLM 生成答案
        save() / load()          : 持久化 / 加载 FAISS 索引
        clear()                  : 清空索引
        stats()                  : 索引统计信息
    """

    CHUNK_SIZE = 800        # 每块字符数（中文约 400 tokens）
    CHUNK_OVERLAP = 100     # 块间重叠（保留上下文连续性）
    DEFAULT_K = 5           # 默认检索 top-k

    def __init__(
        self,
        index_dir: Optional[str] = None,
        prefer_local_embeddings: bool = False,
        auto_load: bool = True,
    ):
        self.index_dir = Path(index_dir or _DEFAULT_INDEX_DIR)
        self.index_dir.mkdir(parents=True, exist_ok=True)

        self._embeddings = None  # 懒加载
        self._vectorstore = None
        self._doc_count = 0
        self._prefer_local = prefer_local_embeddings
        self._metadata_path = self.index_dir / "metadata.json"
        self._index_path = str(self.index_dir / "faiss_index")
        self._bm25_cache: Optional[Tuple] = None  # (BM25Okapi, docs_list)，文档变动时失效

        if auto_load:
            self.load()

    # ── 嵌入模型（懒加载）──────────────────────────────────────────────────────

    @property
    def embeddings(self):
        if self._embeddings is None:
            self._embeddings = _get_embeddings(prefer_local=self._prefer_local)
        return self._embeddings

    # ── 分块工具 ──────────────────────────────────────────────────────────────

    def _split_text(self, text: str, source: str = "text") -> List[Any]:
        """将文本分块，返回 LangChain Document 对象列表。"""
        from langchain_text_splitters import RecursiveCharacterTextSplitter
        from langchain_core.documents import Document

        splitter = RecursiveCharacterTextSplitter(
            chunk_size=self.CHUNK_SIZE,
            chunk_overlap=self.CHUNK_OVERLAP,
            # 中文兼容分隔符（优先段落 → 换行 → 句号 → 逗号 → 字符）
            separators=["\n\n", "\n", "。", "！", "？", "；", "，", " ", ""],
            length_function=len,
        )
        chunks = splitter.split_text(text)
        return [
            Document(
                page_content=chunk,
                metadata={"source": source, "chunk_index": i, "indexed_at": int(time.time())},
            )
            for i, chunk in enumerate(chunks)
        ]

    # ── 文档加载器 ────────────────────────────────────────────────────────────

    def _load_file_docs(self, file_path: str) -> List[Any]:
        """根据文件类型自动选择 LangChain loader。"""
        from pathlib import Path
        ext = Path(file_path).suffix.lower()

        try:
            if ext == ".pdf":
                from langchain_community.document_loaders import PyPDFLoader
                loader = PyPDFLoader(file_path)
            elif ext in (".docx", ".doc"):
                from langchain_community.document_loaders import Docx2txtLoader
                loader = Docx2txtLoader(file_path)
            elif ext in (".txt", ".md", ".markdown"):
                from langchain_community.document_loaders import TextLoader
                loader = TextLoader(file_path, encoding="utf-8")
            elif ext == ".csv":
                from langchain_community.document_loaders import CSVLoader
                loader = CSVLoader(file_path)
            elif ext == ".json":
                from langchain_community.document_loaders import JSONLoader
                loader = JSONLoader(file_path, jq_schema=".", text_content=False)
            else:
                # 通用文本加载
                from langchain_community.document_loaders import TextLoader
                loader = TextLoader(file_path, encoding="utf-8", autodetect_encoding=True)

            return loader.load()
        except Exception as exc:
            logger.warning(f"[RAGService] 文件加载失败 ({file_path}): {exc}，尝试纯文本读取")
            try:
                text = Path(file_path).read_text(encoding="utf-8", errors="ignore")
                from langchain_core.documents import Document
                return [Document(page_content=text, metadata={"source": file_path})]
            except Exception as exc2:
                raise RuntimeError(f"无法加载文件 {file_path}: {exc2}") from exc2

    # ── 索引操作 ──────────────────────────────────────────────────────────────

    def index_file(self, file_path: str) -> int:
        """
        索引文件并存入向量库。

        参数:
            file_path: 文件路径

        返回: 新增 chunk 数量
        """
        logger.info(f"[RAGService] 索引文件: {file_path}")
        docs = self._load_file_docs(file_path)
        all_chunks = []
        for doc in docs:
            chunks = self._split_text(doc.page_content, source=file_path)
            all_chunks.extend(chunks)

        return self._add_documents(all_chunks)

    def index_text(self, text: str, source: str = "user_input") -> int:
        """
        索引原始文本。

        参数:
            text   : 待索引文本
            source : 来源标签

        返回: 新增 chunk 数量
        """
        chunks = self._split_text(text, source=source)
        return self._add_documents(chunks)

    def index_directory(self, dir_path: str, extensions: Optional[List[str]] = None) -> Dict[str, int]:
        """
        递归索引目录下的所有文件。

        参数:
            dir_path   : 目录路径
            extensions : 限制文件扩展名（如 [".pdf", ".txt"]），None = 所有支持类型

        返回: {file_path: chunk_count}
        """
        allowed = set(extensions or [".txt", ".md", ".pdf", ".docx", ".csv"])
        results = {}
        for p in Path(dir_path).rglob("*"):
            if p.is_file() and p.suffix.lower() in allowed:
                try:
                    count = self.index_file(str(p))
                    results[str(p)] = count
                    logger.info(f"[RAGService] 已索引 {p.name}: {count} chunks")
                except Exception as exc:
                    logger.warning(f"[RAGService] 跳过 {p.name}: {exc}")
                    results[str(p)] = -1
        return results

    def _add_documents(self, docs: List[Any]) -> int:
        """将 Document 列表添加到向量库。"""
        if not docs:
            return 0
        try:
            from langchain_community.vectorstores import FAISS
            if self._vectorstore is None:
                self._vectorstore = FAISS.from_documents(docs, self.embeddings)
            else:
                self._vectorstore.add_documents(docs)
            self._doc_count += len(docs)
            self._bm25_cache = None  # 文档变更 → 失效 BM25 缓存
            self.save()  # 自动持久化
            return len(docs)
        except Exception as exc:
            logger.error(f"[RAGService] _add_documents 失败: {exc}", exc_info=True)
            raise

    # ── BM25 支持（懒加载，从 FAISS docstore 重建）─────────────────────────────

    def _build_bm25(self) -> Tuple[Any, List[Any]]:
        """
        从 FAISS docstore 懒加载 BM25 索引。
        返回 (BM25Okapi | None, docs_list)。
        结果缓存在 self._bm25_cache，add/clear/load 时失效。
        """
        if self._bm25_cache is not None:
            return self._bm25_cache

        if self._vectorstore is None:
            return None, []

        try:
            from rank_bm25 import BM25Okapi  # type: ignore
        except ImportError:
            logger.debug("[RAGService] rank_bm25 未安装，BM25 不可用（pip install rank-bm25）")
            self._bm25_cache = (None, [])
            return None, []

        try:
            id_map: Dict = getattr(self._vectorstore, "index_to_docstore_id", {})
            docstore = getattr(self._vectorstore, "docstore", None)
            if not id_map or docstore is None:
                self._bm25_cache = (None, [])
                return None, []

            docs = [
                docstore._dict[id_map[i]]
                for i in range(len(id_map))
                if id_map.get(i) in (docstore._dict or {})
            ]
            if not docs:
                self._bm25_cache = (None, [])
                return None, []

            def _tok(text: str) -> List[str]:
                """空格分割 + 字符 bigram，兼容中英文混合文本。"""
                words = text.split()
                bigrams = [text[j:j + 2] for j in range(len(text) - 1)]
                return words + bigrams

            tokenized = [_tok(d.page_content) for d in docs]
            bm25 = BM25Okapi(tokenized)
            self._bm25_cache = (bm25, docs)
            logger.info(f"[RAGService] BM25 索引构建完成 ({len(docs)} docs)")
            return bm25, docs
        except Exception as exc:
            logger.warning(f"[RAGService] BM25 构建失败: {exc}")
            self._bm25_cache = (None, [])
            return None, []

    def hybrid_retrieve(
        self,
        query: str,
        k: int = DEFAULT_K,
        score_threshold: float = 0.3,
        bm25_weight: float = 0.3,
    ) -> List[Dict[str, Any]]:
        """
        混合检索：向量搜索 + BM25 关键词搜索，通过 RRF（Reciprocal Rank Fusion）融合。
        可选 Cross-Encoder 二次精排（需 pip install sentence-transformers）。

        参数:
            query           : 查询文本
            k               : 最终返回数量
            score_threshold : 向量搜索初筛阈值（RRF 融合后不再过滤，确保 BM25 命中也能留存）
            bm25_weight     : BM25 在 RRF 中的权重（0–1，默认 0.3；向量权重 = 1 - bm25_weight）

        返回:
            同 retrieve()：[{"content", "source", "score", "chunk_index"}, ...]
        """
        if self._vectorstore is None:
            return []

        fetch_k = min(k * 4, max(k + 10, 20))
        RRF_K = 60
        v_weight = 1.0 - bm25_weight

        # ── Step 1: 向量召回 ────────────────────────────────────────────────────
        vector_hits = self.retrieve(query, k=fetch_k, score_threshold=0.0)

        # ── Step 2: BM25 关键词召回 ─────────────────────────────────────────────
        bm25_hits: List[Dict[str, Any]] = []
        bm25_idx, all_docs = self._build_bm25()
        if bm25_idx is not None and all_docs:
            def _tok(t: str) -> List[str]:
                return t.split() + [t[j:j + 2] for j in range(len(t) - 1)]
            try:
                scores = bm25_idx.get_scores(_tok(query))
                top_indices = sorted(range(len(scores)), key=lambda i: -scores[i])[:fetch_k]
                for rank, idx in enumerate(top_indices):
                    if scores[idx] > 0:
                        doc = all_docs[idx]
                        bm25_hits.append({
                            "content": doc.page_content,
                            "source": doc.metadata.get("source", "unknown"),
                            "score": float(scores[idx]),
                            "chunk_index": doc.metadata.get("chunk_index", 0),
                        })
            except Exception as bm25_err:
                logger.debug(f"[RAGService] BM25 检索异常: {bm25_err}")

        # ── Step 3: RRF 融合 ─────────────────────────────────────────────────────
        rrf: Dict[str, Dict[str, Any]] = {}
        for rank, doc in enumerate(vector_hits):
            key = doc["content"][:120]
            if key not in rrf:
                rrf[key] = {"doc": dict(doc), "rrf_score": 0.0}
            rrf[key]["rrf_score"] += v_weight / (rank + RRF_K)

        for rank, doc in enumerate(bm25_hits):
            key = doc["content"][:120]
            if key not in rrf:
                rrf[key] = {"doc": dict(doc), "rrf_score": 0.0}
            rrf[key]["rrf_score"] += bm25_weight / (rank + RRF_K)

        candidates = sorted(rrf.values(), key=lambda x: x["rrf_score"], reverse=True)
        candidates = [c["doc"] for c in candidates]

        # ── Step 4: Cross-Encoder 精排（可选，需 sentence-transformers）────────────
        try:
            from sentence_transformers import CrossEncoder  # type: ignore
            if len(candidates) > 1:
                _ce = CrossEncoder("cross-encoder/ms-marco-MiniLM-L-6-v2")
                pairs = [(query, c["content"]) for c in candidates]
                ce_scores = _ce.predict(pairs)
                for doc, ce_s in zip(candidates, ce_scores):
                    doc["_ce"] = float(ce_s)
                candidates.sort(key=lambda d: d.get("_ce", 0.0), reverse=True)
                for doc in candidates:
                    doc.pop("_ce", None)
        except Exception:
            pass  # Cross-Encoder 不可用：维持 RRF 顺序

        result = candidates[:k]
        logger.info(
            f"[RAGService] hybrid_retrieve → vector={len(vector_hits)} "
            f"bm25={len(bm25_hits)} fused={len(rrf)} final={len(result)}"
        )
        return result

    # ── 检索 ──────────────────────────────────────────────────────────────────

    def retrieve(
        self,
        query: str,
        k: int = DEFAULT_K,
        score_threshold: float = 0.0,
    ) -> List[Dict[str, Any]]:
        """
        检索最相关的 top-k 文本片段。

        参数:
            query            : 查询文本
            k                : 返回数量
            score_threshold  : 最低相似度分数（0.0 = 不过滤）

        返回:
            [{"content": str, "source": str, "score": float, "chunk_index": int}, ...]
        """
        if self._vectorstore is None:
            logger.info("[RAGService] 索引为空，返回空结果")
            return []

        try:
            results_with_scores = self._vectorstore.similarity_search_with_relevance_scores(
                query, k=k
            )
            chunks = []
            for doc, score in results_with_scores:
                if score >= score_threshold:
                    chunks.append({
                        "content": doc.page_content,
                        "source": doc.metadata.get("source", "unknown"),
                        "score": round(float(score), 4),
                        "chunk_index": doc.metadata.get("chunk_index", 0),
                    })
            return chunks
        except Exception as exc:
            logger.error(f"[RAGService] 检索失败: {exc}")
            return []

    def rag_answer(
        self,
        question: str,
        k: int = DEFAULT_K,
        model_id: str = "gemini-2.5-flash-preview-05-20",
        score_threshold: float = 0.2,
    ) -> Dict[str, Any]:
        """
        一站式 RAG 问答：检索 → 注入上下文 → LLM 生成答案。

        返回:
            {
                "answer": str,           # LLM 生成的答案
                "sources": List[str],    # 参考的文件来源
                "chunks": List[dict],    # 原始检索块（调试用）
                "context_used": bool,    # 是否找到相关上下文
            }
        """
        chunks = self.retrieve(question, k=k, score_threshold=score_threshold)

        if not chunks:
            # 索引为空或无匹配 → 直接提示 LLM（无 RAG 上下文）
            return {
                "answer": None,  # 调用方可选择直接用 LLM 回答
                "sources": [],
                "chunks": [],
                "context_used": False,
            }

        # 拼接上下文
        context_parts = []
        for i, c in enumerate(chunks):
            context_parts.append(
                f"[片段 {i+1}（来源：{Path(c['source']).name}，相似度：{c['score']}）]\n"
                f"{c['content']}"
            )
        context = "\n\n".join(context_parts)

        # 调用 LLM
        from app.core.llm.langchain_adapter import KotoLangChainLLM
        from langchain_core.messages import SystemMessage, HumanMessage

        llm = KotoLangChainLLM(model_id=model_id)
        system = (
            "你是 Koto AI 助手。根据以下从知识库中检索到的上下文片段，"
            "准确回答用户问题。\n"
            "回答要求：\n"
            "- 基于所提供的上下文进行回答\n"
            "- 如果上下文不足以回答，明确说明并补充你的知识\n"
            "- 在回答末尾注明引用的来源文件名\n"
        )
        user_msg = f"【知识库上下文】\n{context}\n\n【用户问题】\n{question}"

        try:
            resp = llm.invoke([SystemMessage(content=system), HumanMessage(content=user_msg)])
            answer = resp.content if hasattr(resp, "content") else str(resp)
        except Exception as exc:
            answer = f"[RAG 答案生成失败] {exc}"

        sources = list({c["source"] for c in chunks})

        return {
            "answer": answer,
            "sources": sources,
            "chunks": chunks,
            "context_used": True,
        }

    # ── 持久化 ────────────────────────────────────────────────────────────────

    def save(self) -> bool:
        """将当前 FAISS 索引保存到磁盘。"""
        if self._vectorstore is None:
            return True
        try:
            self._vectorstore.save_local(self._index_path)
            # 保存元数据
            meta = {
                "doc_count": self._doc_count,
                "saved_at": int(time.time()),
                "index_path": self._index_path,
            }
            self._metadata_path.write_text(json.dumps(meta, ensure_ascii=False, indent=2))
            logger.debug(f"[RAGService] 索引已保存 → {self._index_path} ({self._doc_count} chunks)")
            return True
        except Exception as exc:
            logger.error(f"[RAGService] 保存失败: {exc}")
            return False

    def load(self) -> bool:
        """从磁盘加载 FAISS 索引（若存在）。"""
        if not Path(self._index_path + ".faiss").exists():
            return False
        try:
            from langchain_community.vectorstores import FAISS
            self._vectorstore = FAISS.load_local(
                self._index_path,
                self.embeddings,
                allow_dangerous_deserialization=True,
            )
            if self._metadata_path.exists():
                meta = json.loads(self._metadata_path.read_text())
                self._doc_count = meta.get("doc_count", 0)
            self._bm25_cache = None  # 新索引加载 → 失效旧缓存
            logger.info(f"[RAGService] ✅ 索引已加载 ({self._doc_count} chunks)")
            return True
        except Exception as exc:
            logger.warning(f"[RAGService] 加载索引失败（将从空库开始）: {exc}")
            self._vectorstore = None
            return False

    def clear(self) -> bool:
        """清空向量索引（删除磁盘文件 + 内存）。"""
        try:
            self._vectorstore = None
            self._doc_count = 0
            self._bm25_cache = None
            for f in self.index_dir.glob("faiss_index*"):
                f.unlink(missing_ok=True)
            self._metadata_path.unlink(missing_ok=True)
            logger.info("[RAGService] 索引已清空")
            return True
        except Exception as exc:
            logger.error(f"[RAGService] clear 失败: {exc}")
            return False

    # ── 统计 ──────────────────────────────────────────────────────────────────

    def stats(self) -> Dict[str, Any]:
        """返回索引统计信息。"""
        index_size_mb = 0.0
        try:
            faiss_file = Path(self._index_path + ".faiss")
            if faiss_file.exists():
                index_size_mb = round(faiss_file.stat().st_size / 1024 / 1024, 2)
        except Exception:
            pass

        return {
            "initialized": self._vectorstore is not None,
            "doc_count": self._doc_count,
            "index_dir": str(self.index_dir),
            "index_size_mb": index_size_mb,
            "embedding_model": (
                type(self._embeddings).__name__ if self._embeddings else "not_loaded"
            ),
        }
