#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
文件索引与搜索 - 快速定位 Koto 处理过的文件
支持：全文搜索、语义搜索、内容预览
"""

import os
import re
import json
import sqlite3
from typing import Dict, List, Any, Optional
from pathlib import Path
from datetime import datetime
import hashlib
import logging


logger = logging.getLogger(__name__)

class FileIndexer:
    """文件索引与搜索引擎"""
    
    def __init__(self, workspace_dir: str = None, db_path: str = None):
        """
        Args:
            workspace_dir: 工作目录
            db_path: 索引数据库路径
        """
        if workspace_dir is None:
            workspace_dir = os.path.join(os.path.dirname(os.path.dirname(__file__)), "workspace")
        self.workspace_dir = Path(workspace_dir)
        
        if db_path is None:
            db_path = self.workspace_dir / "_index" / "file_index.db"
        
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        
        self._init_database()
    
    def _init_database(self):
        """初始化数据库表"""
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        
        # 文件索引表
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS file_index (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                file_path TEXT UNIQUE NOT NULL,
                file_name TEXT NOT NULL,
                file_ext TEXT,
                content TEXT,
                content_hash TEXT,
                file_size INTEGER,
                indexed_at DATETIME DEFAULT CURRENT_TIMESTAMP,
                modified_at DATETIME,
                tags TEXT,
                metadata TEXT
            )
        """)
        
        # 全文搜索索引（FTS5）
        cursor.execute("""
            CREATE VIRTUAL TABLE IF NOT EXISTS file_content_fts 
            USING fts5(file_path, file_name, content, tokenize='unicode61')
        """)
        
        # 创建索引
        cursor.execute("""
            CREATE INDEX IF NOT EXISTS idx_file_name ON file_index(file_name)
        """)
        cursor.execute("""
            CREATE INDEX IF NOT EXISTS idx_file_ext ON file_index(file_ext)
        """)
        
        conn.commit()
        conn.close()
    
    def _compute_hash(self, content: str) -> str:
        """计算内容哈希"""
        return hashlib.md5(content.encode('utf-8')).hexdigest()
    
    def index_file(self, file_path: str, tags: List[str] = None, metadata: Dict = None) -> Dict[str, Any]:
        """
        索引单个文件
        
        Args:
            file_path: 文件路径
            tags: 标签列表
            metadata: 元数据字典
        
        Returns:
            {"success": bool, "indexed": bool, "error": str}
        """
        try:
            path = Path(file_path)
            if not path.exists() or not path.is_file():
                return {"success": False, "error": "文件不存在"}
            
            # 只索引文本文件
            text_extensions = {'.txt', '.md', '.py', '.js', '.json', '.xml', '.html', '.css', 
                             '.csv', '.log', '.yaml', '.yml', '.ini', '.conf', '.sh', '.bat',
                             '.c', '.cpp', '.h', '.java', '.go', '.rs', '.swift', '.kt'}
            
            if path.suffix.lower() not in text_extensions:
                return {"success": False, "error": "不支持的文件类型（仅索引文本文件）"}
            
            # 读取内容
            try:
                content = path.read_text(encoding='utf-8', errors='ignore')
            except:
                try:
                    content = path.read_text(encoding='gbk', errors='ignore')
                except:
                    return {"success": False, "error": "无法读取文件内容"}
            
            # 计算哈希
            content_hash = self._compute_hash(content)
            
            # 检查是否已索引且内容未变
            conn = sqlite3.connect(self.db_path)
            cursor = conn.cursor()
            
            cursor.execute("SELECT content_hash FROM file_index WHERE file_path = ?", (str(path.resolve()),))
            existing = cursor.fetchone()
            
            if existing and existing[0] == content_hash:
                conn.close()
                return {"success": True, "indexed": False, "message": "文件已索引且未修改"}
            
            # 插入或更新索引
            file_stat = path.stat()
            modified_at = datetime.fromtimestamp(file_stat.st_mtime).isoformat()
            
            cursor.execute("""
                INSERT OR REPLACE INTO file_index 
                (file_path, file_name, file_ext, content, content_hash, file_size, modified_at, tags, metadata)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                str(path.resolve()),
                path.name,
                path.suffix.lower(),
                content[:100000],  # 限制内容长度（前100KB）
                content_hash,
                file_stat.st_size,
                modified_at,
                json.dumps(tags or [], ensure_ascii=False),
                json.dumps(metadata or {}, ensure_ascii=False)
            ))
            
            # 更新全文搜索索引
            cursor.execute("""
                INSERT OR REPLACE INTO file_content_fts (file_path, file_name, content)
                VALUES (?, ?, ?)
            """, (str(path.resolve()), path.name, content[:100000]))
            
            conn.commit()
            conn.close()
            
            return {"success": True, "indexed": True, "message": f"已索引: {path.name}"}
            
        except Exception as e:
            return {"success": False, "error": f"索引失败: {str(e)}"}
    
    def index_directory(self, directory: str, recursive: bool = True, 
                       extensions: List[str] = None) -> Dict[str, Any]:
        """
        批量索引目录
        
        Args:
            directory: 目录路径
            recursive: 是否递归子目录
            extensions: 文件扩展名过滤（如 ['.py', '.txt']）
        """
        try:
            dir_path = Path(directory)
            if not dir_path.exists() or not dir_path.is_dir():
                return {"success": False, "error": "目录不存在"}
            
            indexed_count = 0
            skipped_count = 0
            error_count = 0
            
            # 收集文件
            if recursive:
                files = [f for f in dir_path.rglob('*') if f.is_file()]
            else:
                files = [f for f in dir_path.glob('*') if f.is_file()]
            
            # 扩展名过滤
            if extensions:
                files = [f for f in files if f.suffix.lower() in extensions]
            
            # 索引每个文件
            for file_path in files:
                result = self.index_file(str(file_path))
                if result["success"]:
                    if result.get("indexed"):
                        indexed_count += 1
                    else:
                        skipped_count += 1
                else:
                    error_count += 1
            
            return {
                "success": True,
                "total": len(files),
                "indexed": indexed_count,
                "skipped": skipped_count,
                "errors": error_count
            }
            
        except Exception as e:
            return {"success": False, "error": f"目录索引失败: {str(e)}"}
    
    def search(self, query: str, limit: int = 20, file_types: List[str] = None) -> List[Dict[str, Any]]:
        """
        全文搜索
        
        Args:
            query: 搜索关键词
            limit: 最大结果数
            file_types: 文件类型过滤（如 ['.py', '.md']）
        
        Returns:
            List of {
                "file_path": str,
                "file_name": str,
                "match_snippet": str,
                "score": float
            }
        """
        try:
            conn = sqlite3.connect(self.db_path)
            cursor = conn.cursor()
            
            # 使用 FTS5 全文搜索
            sql = """
                SELECT 
                    f.file_path, 
                    f.file_name, 
                    f.file_ext,
                    f.content,
                    fts.rank
                FROM file_content_fts fts
                JOIN file_index f ON fts.file_path = f.file_path
                WHERE file_content_fts MATCH ?
            """
            
            params = [query]
            
            if file_types:
                placeholders = ','.join('?' * len(file_types))
                sql += f" AND f.file_ext IN ({placeholders})"
                params.extend(file_types)
            
            sql += " ORDER BY fts.rank LIMIT ?"
            params.append(limit)
            
            cursor.execute(sql, params)
            rows = cursor.fetchall()
            conn.close()
            
            # 生成结果
            results = []
            for row in rows:
                file_path, file_name, file_ext, content, rank = row
                
                # 生成匹配摘要（显示关键词上下文）
                snippet = self._generate_snippet(content, query)
                
                results.append({
                    "file_path": file_path,
                    "file_name": file_name,
                    "file_ext": file_ext,
                    "match_snippet": snippet,
                    "score": abs(rank)  # FTS5 rank 是负数
                })
            
            return results
            
        except Exception as e:
            logger.info(f"[FileIndexer] 搜索失败: {e}")
            return []
    
    def _generate_snippet(self, content: str, query: str, context_chars: int = 100) -> str:
        """生成匹配摘要（显示关键词上下文）"""
        if not content:
            return ""
        
        # 找到第一个匹配位置
        query_lower = query.lower()
        content_lower = content.lower()
        pos = content_lower.find(query_lower)
        
        if pos == -1:
            # 未找到，返回开头
            return content[:200] + "..."
        
        # 提取上下文
        start = max(0, pos - context_chars)
        end = min(len(content), pos + len(query) + context_chars)
        
        snippet = content[start:end]
        
        # 高亮关键词
        snippet = re.sub(f"({re.escape(query)})", r"**\1**", snippet, flags=re.IGNORECASE)
        
        if start > 0:
            snippet = "..." + snippet
        if end < len(content):
            snippet = snippet + "..."
        
        return snippet
    
    def find_by_content(self, content_sample: str, min_similarity: float = 0.5) -> List[Dict[str, Any]]:
        """
        根据内容片段查找相似文件
        
        Args:
            content_sample: 内容样本（一段文字）
            min_similarity: 最小相似度（0-1）
        """
        try:
            conn = sqlite3.connect(self.db_path)
            cursor = conn.cursor()
            
            # 简单实现：使用关键词提取 + 全文搜索
            # 提取重要词汇（去除停用词）
            words = re.findall(r'\w+', content_sample.lower())
            stop_words = {'the', 'a', 'an', 'and', 'or', 'but', 'in', 'on', 'at', 'to', 'for',
                         '的', '了', '是', '在', '我', '有', '和', '就', '不', '人'}
            keywords = [w for w in words if w not in stop_words and len(w) > 2]
            
            if not keywords:
                return []
            
            # 构建搜索查询（取前5个关键词）
            search_query = ' OR '.join(keywords[:5])
            
            results = self.search(search_query, limit=10)
            
            # 计算相似度（简单的词汇重叠比例）
            filtered_results = []
            sample_words_set = set(keywords)
            
            for result in results:
                content = result.get("match_snippet", "")
                result_words = set(re.findall(r'\w+', content.lower()))
                
                if len(sample_words_set) == 0:
                    continue
                
                overlap = len(sample_words_set & result_words)
                similarity = overlap / len(sample_words_set)
                
                if similarity >= min_similarity:
                    result["similarity"] = similarity
                    filtered_results.append(result)
            
            # 按相似度排序
            filtered_results.sort(key=lambda x: x.get("similarity", 0), reverse=True)
            
            return filtered_results
            
        except Exception as e:
            logger.info(f"[FileIndexer] 内容查找失败: {e}")
            return []
    
    def get_file_info(self, file_path: str) -> Optional[Dict[str, Any]]:
        """获取文件索引信息"""
        try:
            conn = sqlite3.connect(self.db_path)
            cursor = conn.cursor()
            
            cursor.execute("""
                SELECT file_path, file_name, file_ext, file_size, indexed_at, modified_at, tags, metadata
                FROM file_index
                WHERE file_path = ?
            """, (file_path,))
            
            row = cursor.fetchone()
            conn.close()
            
            if not row:
                return None
            
            return {
                "file_path": row[0],
                "file_name": row[1],
                "file_ext": row[2],
                "file_size": row[3],
                "indexed_at": row[4],
                "modified_at": row[5],
                "tags": json.loads(row[6]) if row[6] else [],
                "metadata": json.loads(row[7]) if row[7] else {}
            }
            
        except Exception as e:
            logger.info(f"[FileIndexer] 获取文件信息失败: {e}")
            return None
    
    def list_indexed_files(self, limit: int = 100, offset: int = 0) -> List[Dict[str, Any]]:
        """列出所有已索引的文件"""
        try:
            conn = sqlite3.connect(self.db_path)
            cursor = conn.cursor()
            
            cursor.execute("""
                SELECT file_path, file_name, file_ext, file_size, indexed_at
                FROM file_index
                ORDER BY indexed_at DESC
                LIMIT ? OFFSET ?
            """, (limit, offset))
            
            rows = cursor.fetchall()
            conn.close()
            
            return [
                {
                    "file_path": row[0],
                    "file_name": row[1],
                    "file_ext": row[2],
                    "file_size": row[3],
                    "indexed_at": row[4]
                }
                for row in rows
            ]
            
        except Exception as e:
            logger.info(f"[FileIndexer] 列出文件失败: {e}")
            return []
    
    def remove_file(self, file_path: str) -> Dict[str, Any]:
        """从索引中移除文件"""
        try:
            conn = sqlite3.connect(self.db_path)
            cursor = conn.cursor()
            
            cursor.execute("DELETE FROM file_index WHERE file_path = ?", (file_path,))
            cursor.execute("DELETE FROM file_content_fts WHERE file_path = ?", (file_path,))
            
            conn.commit()
            conn.close()
            
            return {"success": True, "message": "已移除索引"}
            
        except Exception as e:
            return {"success": False, "error": f"移除失败: {str(e)}"}
    
    def rebuild_index(self) -> Dict[str, Any]:
        """重建全部索引"""
        try:
            # 清空数据库
            conn = sqlite3.connect(self.db_path)
            cursor = conn.cursor()
            cursor.execute("DELETE FROM file_index")
            cursor.execute("DELETE FROM file_content_fts")
            conn.commit()
            conn.close()
            
            # 重新索引 workspace 目录
            result = self.index_directory(str(self.workspace_dir), recursive=True)
            
            return {
                "success": True,
                "message": "索引重建完成",
                "stats": result
            }
            
        except Exception as e:
            return {"success": False, "error": f"重建失败: {str(e)}"}


def test_file_indexer():
    """测试文件索引器"""
    indexer = FileIndexer()
    
    logger.info("=== 测试文件索引器 ===\n")
    
    # 测试 1: 索引目录
    logger.info("1. 索引 workspace 目录...")
    result = indexer.index_directory(str(indexer.workspace_dir), recursive=True)
    logger.info(f"   结果: 总数={result.get('total')}, 已索引={result.get('indexed')}, 跳过={result.get('skipped')}\n")
    
    # 测试 2: 搜索
    logger.info("2. 搜索关键词 'python'...")
    results = indexer.search("python", limit=5)
    logger.info(f"   找到 {len(results)} 个结果:")
    for i, r in enumerate(results, 1):
        logger.info(f"   {i}. {r['file_name']} - {r['match_snippet'][:50]}...\n")
    
    # 测试 3: 内容查找
    logger.info("3. 根据内容查找文件...")
    sample = "import os\nimport sys"
    results = indexer.find_by_content(sample)
    logger.info(f"   找到 {len(results)} 个相似文件:")
    for i, r in enumerate(results[:3], 1):
        logger.info(f"   {i}. {r['file_name']} (相似度: {r.get('similarity', 0):.2f})\n")
    
    # 测试 4: 列出所有文件
    logger.info("4. 列出前 10 个索引文件...")
    files = indexer.list_indexed_files(limit=10)
    for i, f in enumerate(files, 1):
        logger.info(f"   {i}. {f['file_name']} ({f['file_size']} bytes)")


if __name__ == "__main__":
    test_file_indexer()
