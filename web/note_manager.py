#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
快速笔记管理器
支持快速记录、搜索、分类
"""
import os
import json
from datetime import datetime
from typing import List, Dict
import logging


logger = logging.getLogger(__name__)

class QuickNoteManager:
    """快速笔记管理器"""
    
    def __init__(self, notes_dir: str = None):
        if notes_dir is None:
            # 默认保存到 workspace/notes
            script_dir = os.path.dirname(os.path.abspath(__file__))
            project_root = os.path.dirname(script_dir)
            notes_dir = os.path.join(project_root, 'workspace', 'notes')
        
        self.notes_dir = notes_dir
        os.makedirs(self.notes_dir, exist_ok=True)
        
        self.index_file = os.path.join(self.notes_dir, '_index.json')
        self.notes = self._load_index()
    
    def _load_index(self) -> List[Dict]:
        """加载笔记索引"""
        if os.path.exists(self.index_file):
            try:
                with open(self.index_file, 'r', encoding='utf-8') as f:
                    return json.load(f)
            except Exception as e:
                logger.info(f"[笔记] 索引加载失败: {e}")
                return []
        return []
    
    def _save_index(self):
        """保存笔记索引"""
        try:
            with open(self.index_file, 'w', encoding='utf-8') as f:
                json.dump(self.notes, f, ensure_ascii=False, indent=2)
        except Exception as e:
            logger.info(f"[笔记] 索引保存失败: {e}")
    
    def add_note(self, content: str, tags: List[str] = None, category: str = "general") -> Dict:
        """
        添加新笔记
        
        Args:
            content: 笔记内容
            tags: 标签列表
            category: 分类
            
        Returns:
            笔记对象
        """
        note_id = datetime.now().strftime('%Y%m%d_%H%M%S_%f')
        timestamp = datetime.now().isoformat()
        
        note = {
            'id': note_id,
            'content': content,
            'tags': tags or [],
            'category': category,
            'created_at': timestamp,
            'updated_at': timestamp
        }
        
        # 保存笔记文件
        note_file = os.path.join(self.notes_dir, f"{note_id}.json")
        try:
            with open(note_file, 'w', encoding='utf-8') as f:
                json.dump(note, f, ensure_ascii=False, indent=2)
        except Exception as e:
            logger.info(f"[笔记] 保存失败: {e}")
            return None
        
        # 更新索引
        self.notes.insert(0, {
            'id': note_id,
            'preview': content[:100],
            'tags': tags or [],
            'category': category,
            'created_at': timestamp
        })
        self._save_index()
        
        logger.info(f"[笔记] 已添加: {note_id}")
        return note
    
    def get_note(self, note_id: str) -> Dict:
        """获取笔记详情"""
        note_file = os.path.join(self.notes_dir, f"{note_id}.json")
        if not os.path.exists(note_file):
            return None
        
        try:
            with open(note_file, 'r', encoding='utf-8') as f:
                return json.load(f)
        except Exception as e:
            logger.info(f"[笔记] 读取失败 {note_id}: {e}")
            return None
    
    def search_notes(self, query: str = None, tags: List[str] = None, category: str = None) -> List[Dict]:
        """
        搜索笔记
        
        Args:
            query: 关键词搜索
            tags: 标签过滤
            category: 分类过滤
            
        Returns:
            匹配的笔记列表
        """
        results = []
        
        for note_info in self.notes:
            # 分类过滤
            if category and note_info.get('category') != category:
                continue
            
            # 标签过滤
            if tags:
                note_tags = set(note_info.get('tags', []))
                if not note_tags.intersection(tags):
                    continue
            
            # 关键词搜索
            if query:
                note = self.get_note(note_info['id'])
                if note and query.lower() in note['content'].lower():
                    results.append(note_info)
            else:
                results.append(note_info)
        
        return results
    
    def delete_note(self, note_id: str) -> bool:
        """删除笔记"""
        note_file = os.path.join(self.notes_dir, f"{note_id}.json")
        
        try:
            if os.path.exists(note_file):
                os.remove(note_file)
            
            # 从索引中删除
            self.notes = [n for n in self.notes if n['id'] != note_id]
            self._save_index()
            
            logger.info(f"[笔记] 已删除: {note_id}")
            return True
        except Exception as e:
            logger.info(f"[笔记] 删除失败 {note_id}: {e}")
            return False
    
    def get_recent_notes(self, limit: int = 10) -> List[Dict]:
        """获取最近的笔记"""
        return self.notes[:limit]
    
    def get_categories(self) -> List[str]:
        """获取所有分类"""
        categories = set()
        for note in self.notes:
            categories.add(note.get('category', 'general'))
        return sorted(list(categories))
    
    def get_all_tags(self) -> List[str]:
        """获取所有标签"""
        tags = set()
        for note in self.notes:
            tags.update(note.get('tags', []))
        return sorted(list(tags))


# 全局实例
_note_manager = None


def get_note_manager() -> QuickNoteManager:
    """获取全局笔记管理器单例"""
    global _note_manager
    if _note_manager is None:
        _note_manager = QuickNoteManager()
    return _note_manager
