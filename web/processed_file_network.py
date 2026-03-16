#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
🌐 处理文件网络索引系统 (Processed File Network)

功能：
- 追踪文件处理历史（标注、编辑、转换等）
- 文本反向索引（找出包含特定文本的所有文件）
- 文件关系网络（源文件→处理后文件、相关文档）
- 快速打开文件
- 多维查询（时间、类型、标签、关键词）

使用场景：
  用户: "打开昨天处理过的关于'黄金价格'的所有word文档"
  → 1. 时间过滤 → 2. 文本搜索 → 3. 类型过滤 → 4. 返回文件列表 + 快速打开
"""

import os
import json
import hashlib
import sqlite3
import re
from pathlib import Path
from datetime import datetime, timedelta
from typing import Dict, List, Optional, Any, Tuple
from dataclasses import dataclass, asdict
from collections import defaultdict
import subprocess
import platform
import logging


logger = logging.getLogger(__name__)

@dataclass
class FileRecord:
    """文件记录"""
    file_id: str
    path: str
    name: str
    file_type: str
    size: int
    created_at: str
    modified_at: str
    indexed_at: str
    content_hash: str
    tags: List[str] = None
    
    def __post_init__(self):
        if self.tags is None:
            self.tags = []


@dataclass
class ProcessingRecord:
    """处理记录"""
    record_id: str
    file_id: str
    operation: str  # annotate, edit, convert, cleanup
    timestamp: str
    details: Dict[str, Any]
    input_file: str
    output_file: Optional[str]
    changes_count: int
    duration_seconds: float
    status: str  # success, failed, partial
    error: Optional[str] = None


@dataclass
class FileRelation:
    """文件关系"""
    relation_id: str
    source_file_id: str
    target_file_id: str
    relation_type: str  # derived_from, related_to, reference, version
    created_at: str
    metadata: Dict[str, Any] = None
    
    def __post_init__(self):
        if self.metadata is None:
            self.metadata = {}


class ProcessedFileNetwork:
    """处理文件网络索引系统"""
    
    def __init__(self, db_path: str = "workspace/.koto_file_network.db", workspace_dir: str = "workspace"):
        self.db_path = db_path
        self.workspace_dir = workspace_dir
        self._init_database()
    
    def _init_database(self):
        """初始化数据库"""
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        
        # 文件记录表
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS file_records (
                file_id TEXT PRIMARY KEY,
                path TEXT UNIQUE NOT NULL,
                name TEXT NOT NULL,
                file_type TEXT,
                size INTEGER,
                created_at TIMESTAMP,
                modified_at TIMESTAMP,
                indexed_at TIMESTAMP,
                content_hash TEXT,
                tags TEXT
            )
        """)
        
        # 处理历史表
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS processing_history (
                record_id TEXT PRIMARY KEY,
                file_id TEXT NOT NULL,
                operation TEXT NOT NULL,
                timestamp TIMESTAMP,
                details TEXT,
                input_file TEXT,
                output_file TEXT,
                changes_count INTEGER DEFAULT 0,
                duration_seconds REAL DEFAULT 0,
                status TEXT DEFAULT 'success',
                error TEXT,
                FOREIGN KEY(file_id) REFERENCES file_records(file_id)
            )
        """)
        
        # 文件关系表
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS file_relations (
                relation_id TEXT PRIMARY KEY,
                source_file_id TEXT NOT NULL,
                target_file_id TEXT NOT NULL,
                relation_type TEXT NOT NULL,
                created_at TIMESTAMP,
                metadata TEXT,
                FOREIGN KEY(source_file_id) REFERENCES file_records(file_id),
                FOREIGN KEY(target_file_id) REFERENCES file_records(file_id)
            )
        """)
        
        # 文本片段索引表（用于快速文本搜索）
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS text_snippets (
                snippet_id TEXT PRIMARY KEY,
                file_id TEXT NOT NULL,
                snippet_text TEXT NOT NULL,
                snippet_type TEXT,
                position INTEGER,
                created_at TIMESTAMP,
                FOREIGN KEY(file_id) REFERENCES file_records(file_id)
            )
        """)
        
        # 全文搜索虚表（FTS5）
        cursor.execute("""
            CREATE VIRTUAL TABLE IF NOT EXISTS text_search USING fts5(
                file_id,
                file_name,
                snippet_text,
                tokenize = 'porter unicode61'
            )
        """)
        
        # 创建索引
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_file_type ON file_records(file_type)")
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_indexed_at ON file_records(indexed_at)")
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_operation ON processing_history(operation)")
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_timestamp ON processing_history(timestamp)")
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_relation_type ON file_relations(relation_type)")
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_source_file ON file_relations(source_file_id)")
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_target_file ON file_relations(target_file_id)")
        
        conn.commit()
        conn.close()
    
    def register_file(
        self,
        file_path: str,
        tags: List[str] = None,
        extract_snippets: bool = True
    ) -> Dict[str, Any]:
        """
        注册文件到网络
        
        Args:
            file_path: 文件路径
            tags: 标签列表
            extract_snippets: 是否提取文本片段
            
        Returns:
            {"file_id": "...", "success": True}
        """
        try:
            file_path = Path(file_path).resolve()
            if not file_path.exists():
                return {"success": False, "error": "文件不存在"}
            
            # 生成文件ID
            file_id = self._generate_file_id(file_path)
            
            # 计算文件哈希
            content_hash = self._calculate_hash(file_path)
            
            # 创建文件记录
            file_record = FileRecord(
                file_id=file_id,
                path=str(file_path),
                name=file_path.name,
                file_type=file_path.suffix[1:].lower(),
                size=file_path.stat().st_size,
                created_at=datetime.fromtimestamp(file_path.stat().st_ctime).isoformat(),
                modified_at=datetime.fromtimestamp(file_path.stat().st_mtime).isoformat(),
                indexed_at=datetime.now().isoformat(),
                content_hash=content_hash,
                tags=tags or []
            )
            
            # 保存到数据库
            self._save_file_record(file_record)
            
            # 提取文本片段（用于搜索）
            if extract_snippets:
                self._extract_and_index_snippets(file_id, file_path)
            
            return {
                "success": True,
                "file_id": file_id,
                "path": str(file_path),
                "name": file_path.name
            }
            
        except Exception as e:
            return {"success": False, "error": str(e)}
    
    def record_processing(
        self,
        file_path: str,
        operation: str,
        changes_count: int = 0,
        output_file: Optional[str] = None,
        duration_seconds: float = 0,
        status: str = "success",
        details: Dict[str, Any] = None,
        error: Optional[str] = None
    ) -> Dict[str, Any]:
        """
        记录文件处理操作
        
        Args:
            file_path: 输入文件路径
            operation: 操作类型（annotate, edit, convert, cleanup）
            changes_count: 修改数量
            output_file: 输出文件路径
            duration_seconds: 处理耗时
            status: 状态（success, failed, partial）
            details: 详细信息
            error: 错误信息
        """
        try:
            # 确保文件已注册
            result = self.register_file(file_path)
            if not result["success"]:
                return result
            
            file_id = result["file_id"]
            
            # 创建处理记录
            record_id = self._generate_record_id()
            processing_record = ProcessingRecord(
                record_id=record_id,
                file_id=file_id,
                operation=operation,
                timestamp=datetime.now().isoformat(),
                details=details or {},
                input_file=str(file_path),
                output_file=str(output_file) if output_file else None,
                changes_count=changes_count,
                duration_seconds=duration_seconds,
                status=status,
                error=error
            )
            
            # 保存处理记录
            self._save_processing_record(processing_record)
            
            # 如果有输出文件，注册并建立关系
            if output_file and os.path.exists(output_file):
                output_result = self.register_file(output_file)
                if output_result["success"]:
                    self.create_relation(
                        source_file_id=file_id,
                        target_file_id=output_result["file_id"],
                        relation_type="derived_from",
                        metadata={
                            "operation": operation,
                            "timestamp": datetime.now().isoformat()
                        }
                    )
            
            return {
                "success": True,
                "record_id": record_id,
                "file_id": file_id
            }
            
        except Exception as e:
            return {"success": False, "error": str(e)}
    
    def create_relation(
        self,
        source_file_id: str,
        target_file_id: str,
        relation_type: str,
        metadata: Dict[str, Any] = None
    ) -> Dict[str, Any]:
        """
        创建文件关系
        
        Args:
            source_file_id: 源文件ID
            target_file_id: 目标文件ID
            relation_type: 关系类型（derived_from, related_to, reference, version）
            metadata: 元数据
        """
        try:
            relation_id = self._generate_relation_id()
            relation = FileRelation(
                relation_id=relation_id,
                source_file_id=source_file_id,
                target_file_id=target_file_id,
                relation_type=relation_type,
                created_at=datetime.now().isoformat(),
                metadata=metadata or {}
            )
            
            self._save_relation(relation)
            
            return {
                "success": True,
                "relation_id": relation_id
            }
            
        except Exception as e:
            return {"success": False, "error": str(e)}
    
    def search_files(
        self,
        query: Optional[str] = None,
        file_type: Optional[str] = None,
        tags: Optional[List[str]] = None,
        operation: Optional[str] = None,
        date_from: Optional[str] = None,
        date_to: Optional[str] = None,
        limit: int = 50
    ) -> Dict[str, Any]:
        """
        多维查询文件
        
        Args:
            query: 文本搜索查询
            file_type: 文件类型过滤（docx, pdf等）
            tags: 标签过滤
            operation: 处理操作过滤（annotate, edit等）
            date_from: 开始日期（ISO格式）
            date_to: 结束日期（ISO格式）
            limit: 返回数量限制
        """
        try:
            conn = sqlite3.connect(self.db_path)
            conn.row_factory = sqlite3.Row
            cursor = conn.cursor()
            
            # 构建查询
            conditions = []
            params = []
            
            # 基础查询
            sql = """
                SELECT DISTINCT f.*,
                       GROUP_CONCAT(DISTINCT p.operation) as operations,
                       COUNT(DISTINCT p.record_id) as processing_count
                FROM file_records f
                LEFT JOIN processing_history p ON f.file_id = p.file_id
            """
            
            # 文本搜索
            if query:
                sql = """
                    SELECT DISTINCT f.*,
                           GROUP_CONCAT(DISTINCT p.operation) as operations,
                           COUNT(DISTINCT p.record_id) as processing_count,
                           ts.snippet_text
                    FROM file_records f
                    LEFT JOIN processing_history p ON f.file_id = p.file_id
                    JOIN text_search ts ON f.file_id = ts.file_id
                    WHERE ts.snippet_text MATCH ?
                """
                params.append(query)
            else:
                sql += " WHERE 1=1"
            
            # 文件类型过滤
            if file_type:
                conditions.append("f.file_type = ?")
                params.append(file_type.lower())
            
            # 标签过滤
            if tags:
                for tag in tags:
                    conditions.append("f.tags LIKE ?")
                    params.append(f"%{tag}%")
            
            # 操作类型过滤
            if operation:
                conditions.append("p.operation = ?")
                params.append(operation)
            
            # 日期范围过滤
            if date_from:
                conditions.append("f.indexed_at >= ?")
                params.append(date_from)
            
            if date_to:
                conditions.append("f.indexed_at <= ?")
                params.append(date_to)
            
            # 添加条件
            if conditions:
                if query:
                    sql += " AND " + " AND ".join(conditions)
                else:
                    sql += " AND " + " AND ".join(conditions)
            
            # 分组和排序
            sql += " GROUP BY f.file_id ORDER BY f.indexed_at DESC LIMIT ?"
            params.append(limit)
            
            cursor.execute(sql, params)
            rows = cursor.fetchall()
            
            results = []
            for row in rows:
                file_info = dict(row)
                
                # 获取处理历史
                cursor.execute("""
                    SELECT * FROM processing_history 
                    WHERE file_id = ? 
                    ORDER BY timestamp DESC
                """, (file_info["file_id"],))
                history = [dict(h) for h in cursor.fetchall()]
                
                # 获取关系
                cursor.execute("""
                    SELECT * FROM file_relations 
                    WHERE source_file_id = ? OR target_file_id = ?
                """, (file_info["file_id"], file_info["file_id"]))
                relations = [dict(r) for r in cursor.fetchall()]
                
                file_info["processing_history"] = history
                file_info["relations"] = relations
                results.append(file_info)
            
            conn.close()
            
            return {
                "success": True,
                "results": results,
                "total_count": len(results)
            }
            
        except Exception as e:
            return {"success": False, "error": str(e)}
    
    def get_file_network(self, file_id: str, depth: int = 2) -> Dict[str, Any]:
        """
        获取文件关系网络
        
        Args:
            file_id: 文件ID
            depth: 关系深度（1=直接关系，2=二级关系）
        """
        try:
            conn = sqlite3.connect(self.db_path)
            conn.row_factory = sqlite3.Row
            cursor = conn.cursor()
            
            # 获取文件信息
            cursor.execute("SELECT * FROM file_records WHERE file_id = ?", (file_id,))
            file_info = cursor.fetchone()
            if not file_info:
                return {"success": False, "error": "文件不存在"}
            
            # 构建网络图
            network = {
                "nodes": [dict(file_info)],
                "edges": []
            }
            
            visited = {file_id}
            to_visit = [file_id]
            
            for _ in range(depth):
                if not to_visit:
                    break
                
                current_batch = to_visit[:]
                to_visit = []
                
                for current_id in current_batch:
                    # 获取所有关系
                    cursor.execute("""
                        SELECT * FROM file_relations 
                        WHERE source_file_id = ? OR target_file_id = ?
                    """, (current_id, current_id))
                    
                    relations = cursor.fetchall()
                    for rel in relations:
                        rel_dict = dict(rel)
                        network["edges"].append(rel_dict)
                        
                        # 找到相关文件
                        other_id = rel_dict["target_file_id"] if rel_dict["source_file_id"] == current_id else rel_dict["source_file_id"]
                        
                        if other_id not in visited:
                            visited.add(other_id)
                            to_visit.append(other_id)
                            
                            # 获取文件信息
                            cursor.execute("SELECT * FROM file_records WHERE file_id = ?", (other_id,))
                            other_file = cursor.fetchone()
                            if other_file:
                                network["nodes"].append(dict(other_file))
            
            conn.close()
            
            return {
                "success": True,
                "network": network,
                "node_count": len(network["nodes"]),
                "edge_count": len(network["edges"])
            }
            
        except Exception as e:
            return {"success": False, "error": str(e)}
    
    def open_file(self, file_id: str) -> Dict[str, Any]:
        """
        快速打开文件
        
        Args:
            file_id: 文件ID
        """
        try:
            conn = sqlite3.connect(self.db_path)
            cursor = conn.cursor()
            
            cursor.execute("SELECT path FROM file_records WHERE file_id = ?", (file_id,))
            row = cursor.fetchone()
            conn.close()
            
            if not row:
                return {"success": False, "error": "文件不存在"}
            
            file_path = row[0]
            if not os.path.exists(file_path):
                return {"success": False, "error": "文件路径无效"}
            
            # 根据操作系统打开文件
            system = platform.system()
            if system == "Windows":
                os.startfile(file_path)
            elif system == "Darwin":  # macOS
                subprocess.run(["open", file_path])
            else:  # Linux
                subprocess.run(["xdg-open", file_path])
            
            return {
                "success": True,
                "path": file_path,
                "message": "文件已打开"
            }
            
        except Exception as e:
            return {"success": False, "error": str(e)}
    
    def get_statistics(self) -> Dict[str, Any]:
        """获取统计信息"""
        try:
            conn = sqlite3.connect(self.db_path)
            cursor = conn.cursor()
            
            # 总文件数
            cursor.execute("SELECT COUNT(*) FROM file_records")
            total_files = cursor.fetchone()[0]
            
            # 按类型统计
            cursor.execute("""
                SELECT file_type, COUNT(*) as count 
                FROM file_records 
                GROUP BY file_type
            """)
            by_type = {row[0]: row[1] for row in cursor.fetchall()}
            
            # 总处理次数
            cursor.execute("SELECT COUNT(*) FROM processing_history")
            total_operations = cursor.fetchone()[0]
            
            # 按操作统计
            cursor.execute("""
                SELECT operation, COUNT(*) as count 
                FROM processing_history 
                GROUP BY operation
            """)
            by_operation = {row[0]: row[1] for row in cursor.fetchall()}
            
            # 总关系数
            cursor.execute("SELECT COUNT(*) FROM file_relations")
            total_relations = cursor.fetchone()[0]
            
            # 最近处理的文件
            cursor.execute("""
                SELECT f.name, p.operation, p.timestamp 
                FROM processing_history p
                JOIN file_records f ON p.file_id = f.file_id
                ORDER BY p.timestamp DESC
                LIMIT 10
            """)
            recent = [{"name": row[0], "operation": row[1], "timestamp": row[2]} for row in cursor.fetchall()]
            
            conn.close()
            
            return {
                "success": True,
                "statistics": {
                    "total_files": total_files,
                    "total_operations": total_operations,
                    "total_relations": total_relations,
                    "files_by_type": by_type,
                    "operations_by_type": by_operation,
                    "recent_activity": recent
                }
            }
            
        except Exception as e:
            return {"success": False, "error": str(e)}
    
    # ==================== 私有辅助方法 ====================
    
    def _generate_file_id(self, file_path: Path) -> str:
        """生成文件ID"""
        return hashlib.md5(str(file_path).encode()).hexdigest()[:16]
    
    def _generate_record_id(self) -> str:
        """生成记录ID"""
        return hashlib.md5(f"{datetime.now().isoformat()}{os.urandom(8)}".encode()).hexdigest()[:16]
    
    def _generate_relation_id(self) -> str:
        """生成关系ID"""
        return hashlib.md5(f"rel_{datetime.now().isoformat()}{os.urandom(8)}".encode()).hexdigest()[:16]
    
    def _calculate_hash(self, file_path: Path) -> str:
        """计算文件哈希"""
        hasher = hashlib.md5()
        with open(file_path, 'rb') as f:
            for chunk in iter(lambda: f.read(4096), b''):
                hasher.update(chunk)
        return hasher.hexdigest()
    
    def _save_file_record(self, record: FileRecord):
        """保存文件记录"""
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        
        cursor.execute("""
            INSERT OR REPLACE INTO file_records 
            (file_id, path, name, file_type, size, created_at, modified_at, indexed_at, content_hash, tags)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            record.file_id, record.path, record.name, record.file_type, record.size,
            record.created_at, record.modified_at, record.indexed_at, record.content_hash,
            json.dumps(record.tags)
        ))
        
        conn.commit()
        conn.close()
    
    def _save_processing_record(self, record: ProcessingRecord):
        """保存处理记录"""
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        
        cursor.execute("""
            INSERT INTO processing_history 
            (record_id, file_id, operation, timestamp, details, input_file, output_file, 
             changes_count, duration_seconds, status, error)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            record.record_id, record.file_id, record.operation, record.timestamp,
            json.dumps(record.details), record.input_file, record.output_file,
            record.changes_count, record.duration_seconds, record.status, record.error
        ))
        
        conn.commit()
        conn.close()
    
    def _save_relation(self, relation: FileRelation):
        """保存文件关系"""
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        
        cursor.execute("""
            INSERT OR REPLACE INTO file_relations 
            (relation_id, source_file_id, target_file_id, relation_type, created_at, metadata)
            VALUES (?, ?, ?, ?, ?, ?)
        """, (
            relation.relation_id, relation.source_file_id, relation.target_file_id,
            relation.relation_type, relation.created_at, json.dumps(relation.metadata)
        ))
        
        conn.commit()
        conn.close()
    
    def _extract_and_index_snippets(self, file_id: str, file_path: Path):
        """提取并索引文本片段"""
        try:
            content = self._extract_text(file_path)
            if not content:
                return
            
            # 分割成片段（每500字符一个片段）
            snippets = []
            chunk_size = 500
            for i in range(0, len(content), chunk_size):
                snippet = content[i:i+chunk_size].strip()
                if len(snippet) > 50:  # 过滤太短的片段
                    snippets.append((i, snippet))
            
            conn = sqlite3.connect(self.db_path)
            cursor = conn.cursor()
            
            # 清除旧片段
            cursor.execute("DELETE FROM text_snippets WHERE file_id = ?", (file_id,))
            cursor.execute("DELETE FROM text_search WHERE file_id = ?", (file_id,))
            
            # 插入新片段
            for position, snippet in snippets:
                snippet_id = f"{file_id}_{position}"
                
                cursor.execute("""
                    INSERT INTO text_snippets 
                    (snippet_id, file_id, snippet_text, snippet_type, position, created_at)
                    VALUES (?, ?, ?, ?, ?, ?)
                """, (snippet_id, file_id, snippet, "content", position, datetime.now().isoformat()))
                
                cursor.execute("""
                    INSERT INTO text_search (file_id, file_name, snippet_text)
                    VALUES (?, ?, ?)
                """, (file_id, file_path.name, snippet))
            
            conn.commit()
            conn.close()
            
        except Exception as e:
            logger.info(f"[ProcessedFileNetwork] 片段提取失败: {e}")
    
    def _extract_text(self, file_path: Path) -> str:
        """提取文件文本"""
        ext = file_path.suffix.lower()
        
        try:
            if ext in ['.txt', '.md', '.markdown']:
                with open(file_path, 'r', encoding='utf-8', errors='ignore') as f:
                    return f.read()
            
            elif ext == '.docx':
                try:
                    from docx import Document
                    doc = Document(file_path)
                    return '\n'.join([p.text for p in doc.paragraphs])
                except ImportError:
                    return ""
            
            elif ext == '.pdf':
                try:
                    import PyPDF2
                    text = []
                    with open(file_path, 'rb') as f:
                        reader = PyPDF2.PdfReader(f)
                        for page in reader.pages:
                            text.append(page.extract_text())
                    return '\n'.join(text)
                except ImportError:
                    return ""
            
            return ""
            
        except Exception as e:
            logger.info(f"[ProcessedFileNetwork] 文本提取失败 {file_path}: {e}")
            return ""


# ==================== 全局实例 ====================

_file_network_instance = None

def get_file_network(db_path: str = None, workspace_dir: str = None) -> ProcessedFileNetwork:
    """获取文件网络单例"""
    global _file_network_instance
    
    if _file_network_instance is None:
        db_path = db_path or os.path.join(os.path.dirname(__file__), "..", "workspace", ".koto_file_network.db")
        workspace_dir = workspace_dir or os.path.join(os.path.dirname(__file__), "..", "workspace")
        _file_network_instance = ProcessedFileNetwork(db_path, workspace_dir)
    
    return _file_network_instance
