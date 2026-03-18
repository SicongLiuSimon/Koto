"""
跨文件问答模块（本地 Ollama）
流程：
  1. 关键词拆解 → 用 FileIndexer FTS5 查找相关文件
  2. 若 FileIndexer 无结果，直接扫磁盘路径（fallback）
  3. 用 FileAnalyzer._extract_content() 读取文件内容
  4. 将内容片段 + 问题送 Ollama → 返回自然语言答案

用法：
    from web.file_qa import answer_file_question
    result = answer_file_question(
        question="这几份合同里哪个最早到期？",
        search_dirs=["C:/Users/xxx/Desktop"],
        top_k=5,
    )
"""

from __future__ import annotations

import json
import re
import socket
from pathlib import Path
from typing import Any, Dict, List, Optional

# ── Ollama ───────────────────────────────────────────────────────────────────
_OLLAMA_HOST = "127.0.0.1"
_OLLAMA_PORT = 11434
_OLLAMA_URL = f"http://{_OLLAMA_HOST}:{_OLLAMA_PORT}"
_AI_MODEL = "qwen3:8b"

_QA_SYSTEM = """\
你是文件管家助手，已获得若干文件的摘要内容。
根据这些内容准确回答用户的问题。
若多个文件都有相关信息，请逐一引用文件名说明。
若内容不足以回答，明确说明哪方面信息缺失。
"""


def _ollama_available() -> bool:
    try:
        s = socket.create_connection((_OLLAMA_HOST, _OLLAMA_PORT), timeout=1)
        s.close()
        return True
    except OSError:
        return False


def _extract_content_local(file_path: str) -> str:
    """复用 FileAnalyzer 的内容提取逻辑（避免实例化开销，直接调用模块函数）。"""
    try:
        try:
            from web.file_analyzer import FileAnalyzer
        except ImportError:
            from file_analyzer import FileAnalyzer
        # 用单例或临时实例均可，内容提取无状态
        _fa = FileAnalyzer()
        return _fa._extract_content(file_path) or ""
    except Exception:
        return ""


def _search_files_in_dirs(
    keywords: List[str],
    search_dirs: List[str],
    ext_filters: Optional[List[str]] = None,
    top_k: int = 5,
) -> List[str]:
    """在给定目录中按关键词找文件（先 FTS 索引，无结果则 glob 扫目录）。"""
    found: List[str] = []

    # 1. FileIndexer FTS
    try:
        try:
            from web.file_indexer import FileIndexer
        except ImportError:
            from file_indexer import FileIndexer
        _idx = FileIndexer()
        for kw in keywords:
            hits = _idx.search(kw, limit=top_k)
            for h in hits:
                fp = h.get("file_path", "")
                if fp and fp not in found:
                    found.append(fp)
        if found:
            return found[:top_k]
    except Exception:
        pass

    # 2. Fallback: glob scan
    _SUPPORTED = {
        ".doc",
        ".docx",
        ".pdf",
        ".txt",
        ".xlsx",
        ".xls",
        ".pptx",
        ".ppt",
        ".csv",
        ".md",
    }
    _exts = set(ext_filters) if ext_filters else _SUPPORTED
    seen: set = set()
    for d in search_dirs:
        dp = Path(d)
        if not dp.is_dir():
            continue
        for p in dp.iterdir():
            if not p.is_file():
                continue
            if p.suffix.lower() not in _exts:
                continue
            if p.name.startswith("~$"):
                continue
            # simple keyword match on filename
            name_lower = p.name.lower()
            if any(kw.lower() in name_lower for kw in keywords):
                if str(p) not in seen:
                    seen.add(str(p))
                    found.append(str(p))
    if not found:
        # last fallback: return everything in dirs if no keyword match
        for d in search_dirs:
            dp = Path(d)
            if not dp.is_dir():
                continue
            for p in dp.iterdir():
                if (
                    p.is_file()
                    and p.suffix.lower() in _exts
                    and not p.name.startswith("~$")
                ):
                    if str(p) not in seen:
                        seen.add(str(p))
                        found.append(str(p))
    return found[:top_k]


def answer_file_question(
    question: str,
    search_dirs: Optional[List[str]] = None,
    file_paths: Optional[List[str]] = None,
    top_k: int = 5,
) -> Dict[str, Any]:
    """
    回答关于文件内容的问题。

    Args:
        question:    用户的自然语言问题
        search_dirs: 在这些目录里搜索相关文件（可选）
        file_paths:  直接指定文件列表（可选，与 search_dirs 可同时使用）
        top_k:       最多纳入上下文的文件数

    Returns:
        {
            "answer": str,
            "sources": [{"file_name": str, "snippet": str}],
            "success": bool,
            "error": str or None,
        }
    """
    if not _ollama_available():
        return {
            "success": False,
            "error": "Ollama 未运行（localhost:11434 不可达），请先启动 Ollama",
            "answer": "",
            "sources": [],
        }

    # ── 确定文件列表 ──────────────────────────────────────────────────────
    all_paths: List[str] = list(file_paths or [])
    if search_dirs:
        # 从问题中提取关键词用于搜索
        _kws = [w for w in re.split(r"[\s，。？！、,?!]+", question) if len(w) >= 2][:6]
        found = _search_files_in_dirs(_kws, search_dirs, top_k=top_k)
        for fp in found:
            if fp not in all_paths:
                all_paths.append(fp)
    all_paths = all_paths[:top_k]

    if not all_paths:
        return {
            "success": False,
            "error": "未找到相关文件，请指定文件路径或检查搜索目录",
            "answer": "",
            "sources": [],
        }

    # ── 提取各文件内容 ────────────────────────────────────────────────────
    contexts: List[str] = []
    sources: List[Dict] = []
    for fp in all_paths:
        p = Path(fp)
        if not p.exists():
            continue
        content = _extract_content_local(fp)
        snippet = content[:400].strip() if content else "(无法提取内容)"
        contexts.append(f"【{p.name}】\n{snippet}")
        sources.append({"file_name": p.name, "snippet": snippet[:200]})

    if not contexts:
        return {
            "success": False,
            "error": "所有文件均无法提取内容",
            "answer": "",
            "sources": [],
        }

    # ── 调用 Ollama 作答 ──────────────────────────────────────────────────
    full_prompt = (
        _QA_SYSTEM
        + "\n\n以下是文件摘要：\n\n"
        + "\n\n".join(contexts)
        + f"\n\n用户的问题：{question}"
    )
    try:
        import requests as _req

        resp = _req.post(
            f"{_OLLAMA_URL}/api/generate",
            json={
                "model": _AI_MODEL,
                "prompt": full_prompt,
                "stream": False,
                "options": {"temperature": 0.2, "num_predict": 1024},
            },
            timeout=60,
        )
        answer = (
            resp.json().get("response", "").strip()
            if resp.status_code == 200
            else "（模型无响应）"
        )
    except Exception as e:
        return {"success": False, "error": str(e), "answer": "", "sources": sources}

    return {
        "success": True,
        "error": None,
        "answer": answer,
        "sources": sources,
    }


# ── 批量内容过滤 ──────────────────────────────────────────────────────────────

_FILTER_PROMPT = """\
以下是一批文件的名称和内容摘要（编号 1 开始）。

请判断其中哪些文件符合「{criterion}」的描述。

只输出 JSON，格式：
{{"matches": [{{"index": 1, "file_name": "xxx.doc", "reason": "简要说明（一句话）"}}]}}

若没有任何文件符合，输出：{{"matches": []}}

文件列表：
{file_list}
"""


def filter_files_by_criterion(
    criterion: str,
    directory: str,
    ext_filters: Optional[List[str]] = None,
    batch_size: int = 12,
) -> Dict[str, Any]:
    """
    扫描目录中所有文件，用 Ollama 过滤出符合 criterion 描述的文件。

    Args:
        criterion:    过滤条件，如 "企业访谈报告"、"合同"、"简历"
        directory:    要扫描的目录路径（仅当前层，不递归）
        ext_filters:  可选扩展名过滤，如 [".docx", ".pdf"]
        batch_size:   每批发给 Ollama 的文件数

    Returns:
        {
            "success": bool,
            "matches": [{"file_name": str, "file_path": str, "reason": str}],
            "total_scanned": int,
            "error": str or None,
        }
    """
    if not _ollama_available():
        return {
            "success": False,
            "error": "Ollama 未运行（localhost:11434 不可达）",
            "matches": [],
            "total_scanned": 0,
        }

    _SUPPORTED = {
        ".doc",
        ".docx",
        ".pdf",
        ".txt",
        ".xlsx",
        ".xls",
        ".pptx",
        ".ppt",
        ".csv",
        ".md",
        ".rtf",
    }
    _exts = set(ext_filters) if ext_filters else _SUPPORTED

    dp = Path(directory)
    if not dp.is_dir():
        return {
            "success": False,
            "error": f"目录不存在: {directory}",
            "matches": [],
            "total_scanned": 0,
        }

    all_files = [
        p
        for p in dp.iterdir()
        if p.is_file() and p.suffix.lower() in _exts and not p.name.startswith("~$")
    ]

    if not all_files:
        return {"success": True, "matches": [], "total_scanned": 0, "error": None}

    import requests as _req

    all_matches: List[Dict] = []

    # Process in batches to keep prompt size reasonable
    for batch_start in range(0, len(all_files), batch_size):
        batch = all_files[batch_start : batch_start + batch_size]
        file_entries = []
        for local_idx, fp in enumerate(batch, 1):
            content = _extract_content_local(str(fp))
            snippet = content[:300].strip() if content else "(无可读内容)"
            file_entries.append(
                f"{local_idx}. 文件名: {fp.name}\n   内容摘要: {snippet}"
            )

        prompt = _FILTER_PROMPT.format(
            criterion=criterion,
            file_list="\n\n".join(file_entries),
        )
        try:
            resp = _req.post(
                f"{_OLLAMA_URL}/api/generate",
                json={
                    "model": _AI_MODEL,
                    "prompt": prompt,
                    "stream": False,
                    "options": {"temperature": 0.1, "num_predict": 512},
                },
                timeout=60,
            )
            if resp.status_code != 200:
                continue
            raw = resp.json().get("response", "")
            m = re.search(r"\{[\s\S]*?\}", raw)
            if not m:
                continue
            data = json.loads(m.group(0))
            for hit in data.get("matches", []):
                idx = hit.get("index", 1) - 1  # convert to 0-based
                if 0 <= idx < len(batch):
                    all_matches.append(
                        {
                            "file_name": batch[idx].name,
                            "file_path": str(batch[idx]),
                            "reason": hit.get("reason", ""),
                        }
                    )
        except Exception:
            continue

    return {
        "success": True,
        "matches": all_matches,
        "total_scanned": len(all_files),
        "error": None,
    }
