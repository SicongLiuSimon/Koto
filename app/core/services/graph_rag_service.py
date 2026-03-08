"""
GraphRAGService — Entity-graph augmented retrieval for Koto.

Architecture (Phase 3):
  1. Entity extraction from query (regex + stopword) — no heavy NLP dep.
  2. For each entity: direct triple lookup + 1-hop BFS neighbor expansion
  3. Format triples as readable context block

Usage (web/app.py):
    from app.core.services.graph_rag_service import GraphRAGService
    graph_ctx = GraphRAGService.retrieve(query, k=10)
    if graph_ctx:
        _rag_context_block += "\\n\\n" + graph_ctx
"""

from __future__ import annotations

import logging
import re
from typing import Dict, List, Optional, Set

logger = logging.getLogger(__name__)

# ── Minimal Chinese / English stopwords ────────────────────────────────────
_STOPWORDS: Set[str] = {
    "的", "了", "是", "在", "和", "与", "我", "你", "他", "她", "它",
    "我们", "你们", "他们", "这", "那", "有", "被", "把", "对", "将",
    "a", "an", "the", "is", "are", "was", "were", "be", "been",
    "i", "you", "he", "she", "it", "we", "they", "this", "that",
    "for", "in", "on", "at", "to", "of", "and", "or", "but",
    "what", "who", "how", "when", "where", "which", "why",
    "帮我", "帮", "请", "请问", "什么", "哪些", "怎么", "怎样", "如何",
}

# Minimum entity length to consider
_MIN_ENTITY_LEN = 2


def _extract_entities(text: str) -> List[str]:
    """
    Lightweight entity extraction — no NLP dependency.

    Strategy:
    1. Split on punctuation + whitespace.
    2. Keep tokens that are:
       - ≥ 2 chars long
       - Not pure digits
       - Not in stopword list
       - Contain at least one CJK or Latin letter (not numbers-only)
    3. Returns up to 8 unique candidates.
    """
    # Split on common delimiters
    tokens = re.split(r'[\s，。！？,.!?;；:："""《》【】\(\)\[\]]+', text)
    seen: List[str] = []
    visited: Set[str] = set()
    for tok in tokens:
        tok = tok.strip()
        if (
            len(tok) >= _MIN_ENTITY_LEN
            and not tok.isdigit()
            and tok.lower() not in _STOPWORDS
            and re.search(r'[\u4e00-\u9fffA-Za-z]', tok)
            and tok not in visited
        ):
            seen.append(tok)
            visited.add(tok)
        if len(seen) >= 8:
            break
    return seen


def _get_kg():
    """Lazily load KnowledgeGraph — avoids circular imports."""
    try:
        import sys
        # Try web context first
        if "knowledge_graph" in sys.modules:
            mod = sys.modules["knowledge_graph"]
        else:
            try:
                import importlib
                mod = importlib.import_module("web.knowledge_graph")
            except ImportError:
                mod = importlib.import_module("knowledge_graph")
        return mod.KnowledgeGraph()
    except Exception as e:
        logger.debug(f"[GraphRAG] _get_kg failed: {e}")
        return None


def _format_triples(triples: List[Dict], max_triples: int = 15) -> str:
    """Format a list of triple dicts as a compact human-readable block."""
    if not triples:
        return ""
    seen: Set[str] = set()
    lines: List[str] = []
    for t in triples[:max_triples]:
        line = f"{t['subject']} → {t['relation']} → {t['object']}"
        if line not in seen:
            seen.add(line)
            lines.append(line)
    return "\n".join(lines)


class GraphRAGService:
    """Graph-based retrieval augmentation."""

    @staticmethod
    def retrieve(query: str, k: int = 10) -> str:
        """
        Retrieve relevant triples for `query`.

        Returns a formatted string block, or "" if nothing found.
        """
        if not query or len(query.strip()) < 2:
            return ""

        kg = _get_kg()
        if kg is None:
            return ""

        try:
            entities = _extract_entities(query)
            if not entities:
                # Fallback: fuzzy whole-query search
                triples = kg.search_triples_fuzzy(query, limit=k)
                return _format_triples(triples)

            all_triples: List[Dict] = []
            seen_keys: Set[str] = set()

            for entity in entities:
                # Direct triples
                direct = kg.search_triples(entity, limit=k)
                for t in direct:
                    key = f"{t['subject']}|{t['relation']}|{t['object']}"
                    if key not in seen_keys:
                        seen_keys.add(key)
                        all_triples.append(t)

                # 1-hop neighbor expansion (depth=1, small BFS)
                neighbors = kg.get_entity_neighbors(entity, depth=1)
                for neighbor in list(neighbors)[:3]:  # limit expansion
                    hop_triples = kg.search_triples(neighbor, limit=5)
                    for t in hop_triples:
                        key = f"{t['subject']}|{t['relation']}|{t['object']}"
                        if key not in seen_keys:
                            seen_keys.add(key)
                            all_triples.append(t)

            if not all_triples:
                # Fallback to fuzzy
                for entity in entities[:3]:
                    fuzzy = kg.search_triples_fuzzy(entity, limit=5)
                    for t in fuzzy:
                        key = f"{t['subject']}|{t['relation']}|{t['object']}"
                        if key not in seen_keys:
                            seen_keys.add(key)
                            all_triples.append(t)

            if not all_triples:
                return ""

            # Sort by confidence
            all_triples.sort(key=lambda x: x.get("confidence", 0), reverse=True)

            block = _format_triples(all_triples, max_triples=k)
            if block:
                return f"## 🕸️ 知识图谱关联事实\n{block}"
            return ""

        except Exception as e:
            logger.debug(f"[GraphRAG] retrieve error: {e}")
            return ""

    @staticmethod
    def add_triples_from_llm(
        triples_json: list,
        source_text: str = "",
        origin: str = "reflector",
    ) -> int:
        """
        Bulk-insert triples extracted by LLM.

        Each item in `triples_json` should be:
          {"subject": "...", "relation": "...", "object": "..."}
        Optional: "confidence" (float 0-1)

        Returns count of successfully inserted triples.
        """
        kg = _get_kg()
        if kg is None:
            return 0

        count = 0
        for t in triples_json:
            if not isinstance(t, dict):
                continue
            subj = str(t.get("subject", "")).strip()
            rel  = str(t.get("relation", "")).strip()
            obj  = str(t.get("object",  "")).strip()
            conf = float(t.get("confidence", 0.8))
            if subj and rel and obj:
                ok = kg.add_triple(
                    subject=subj,
                    relation=rel,
                    obj=obj,
                    source_text=source_text[:400],
                    confidence=conf,
                    origin=origin,
                )
                if ok:
                    count += 1
        return count
