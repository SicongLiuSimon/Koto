import json
import os
import time
from typing import List, Dict, Optional
import logging

logger = logging.getLogger(__name__)

class MemoryManager:
    """
    Manages long-term memory for Koto across sessions.
    Stores memories in a JSON file.
    """
    
    def __init__(self, memory_path: str = "config/memory.json"):
        self.memory_path = memory_path
        self.memories: List[Dict] = []
        self._load()

    def _load(self):
        """Load memories from disk."""
        if os.path.exists(self.memory_path):
            try:
                with open(self.memory_path, 'r', encoding='utf-8') as f:
                    self.memories = json.load(f)
            except Exception as e:
                logger.info(f"[MemoryManager] Failed to load memory: {e}")
                self.memories = []
        else:
            self.memories = []

    def _save(self):
        """Save memories to disk."""
        os.makedirs(os.path.dirname(self.memory_path), exist_ok=True)
        try:
            with open(self.memory_path, 'w', encoding='utf-8') as f:
                json.dump(self.memories, f, ensure_ascii=False, indent=2)
        except Exception as e:
            logger.info(f"[MemoryManager] Failed to save memory: {e}")

    def add_memory(self, content: str, category: str = "user_preference", source: str = "user"):
        """
        Add a new memory item.
        
        Args:
            content: The text content of the memory.
            category: 'user_preference', 'fact', 'project_info', 'correction'
            source: 'user' (explicit), 'extraction' (auto-extracted)
        """
        item = {
            "id": int(time.time() * 1000),
            "content": content.strip(),
            "category": category,
            "source": source,
            "created_at": time.strftime("%Y-%m-%d %H:%M:%S"),
            "use_count": 0
        }
        self.memories.append(item)
        self._save()
        logger.info(f"[MemoryManager] Added memory: {content[:50]}...")
        return item

    def delete_memory(self, memory_id: int) -> bool:
        """Delete a memory by ID."""
        initial_len = len(self.memories)
        self.memories = [m for m in self.memories if m["id"] != memory_id]
        if len(self.memories) < initial_len:
            self._save()
            return True
        return False

    def get_all_memories(self) -> List[Dict]:
        """Return all memories, sorted by newest first."""
        return sorted(self.memories, key=lambda x: x["created_at"], reverse=True)
    
    def list_memories(self) -> List[Dict]:
        """Alias for get_all_memories() - list all stored memories."""
        return self.get_all_memories()

    def search_memories(self, query: str, limit: int = 5) -> List[Dict]:
        """
        Simple keyword-based search for relevant memories.
        TODO: Upgrade to vector/embedding search in Phase 3.
        """
        if not query:
            return []
            
        query_lower = query.lower()
        scored = []
        
        # Simple scoring: +1 for each keyword match
        keywords = [k for k in query_lower.split() if len(k) > 1]
        
        for m in self.memories:
            content_lower = m["content"].lower()
            score = 0
            
            # Category boost
            if m["category"] == "user_preference":
                score += 2  # Preferences are always important
            
            # Keyword matching
            if query_lower in content_lower:
                score += 5 # Exact phrase match
            
            for kw in keywords:
                if kw in content_lower:
                    score += 1
            
            if score > 0:
                scored.append((score, m))
        
        # Sort by score desc, then recency
        scored.sort(key=lambda x: (x[0], x[1]["created_at"]), reverse=True)
        
        # Return top N
        result = [item[1] for item in scored[:limit]]
        
        # Increment use count (simple simulation of relevance reinforcement)
        for m in result:
            m["use_count"] = m.get("use_count", 0) + 1
        
        return result

    def get_context_string(self, user_input: str) -> str:
        """
        Get a formatted string of relevant memories to inject into LLM context.
        """
        relevant = self.search_memories(user_input)
        if not relevant:
            return ""
            
        lines = ["\n[User Memory / Preferences]:"]
        for m in relevant:
            lines.append(f"- {m['content']}")
        
        return "\n".join(lines) + "\n"
