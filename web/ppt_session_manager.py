#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
PPT 编辑数据管理 - 支持保存和恢复 PPT 生成数据
用于 P1 阶段的"生成后编辑"功能
"""

import hashlib
import json
import logging
import os
import uuid
from datetime import datetime
from typing import Dict, List, Optional

logger = logging.getLogger(__name__)


class PPTSessionManager:
    """PPT 生成会话管理（存储生成历史用于编辑）"""

    def __init__(self, storage_dir: str = None):
        """
        初始化 PPT 会话管理器

        Args:
            storage_dir: 存储目录（默认为项目根 workspace/ppt_sessions）
        """
        if storage_dir is None:
            storage_dir = os.path.join(
                os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                "workspace",
                "ppt_sessions",
            )

        self.storage_dir = storage_dir
        os.makedirs(storage_dir, exist_ok=True)

    def create_session(
        self, title: str, user_input: str, theme: str = "business", user_id: str = None
    ) -> str:
        """
        创建新的 PPT 生成会话

        Args:
            title: PPT 标题
            user_input: 用户原始输入
            theme: 主题（business/tech/creative/minimal）
            user_id: 用户 ID（可选，用于权限验证）

        Returns:
            session_id: 唯一的会话 ID
        """
        session_id = str(uuid.uuid4())

        session_data = {
            "session_id": session_id,
            "title": title,
            "user_input": user_input,
            "theme": theme,
            "user_id": user_id,
            "created_at": datetime.now().isoformat(),
            "updated_at": datetime.now().isoformat(),
            "ppt_data": None,  # 生成后填充
            "search_context": "",
            "research_context": "",
            "uploaded_file_context": "",
            "ppt_file_path": None,
            "status": "pending",  # pending / generating / completed / failed
        }

        session_file = os.path.join(self.storage_dir, f"{session_id}.json")
        with open(session_file, "w", encoding="utf-8") as f:
            json.dump(session_data, f, ensure_ascii=False, indent=2)

        return session_id

    def save_generation_data(
        self,
        session_id: str,
        ppt_data: Dict,
        ppt_file_path: str,
        search_context: str = "",
        research_context: str = "",
        uploaded_file_context: str = "",
    ) -> bool:
        """
        保存 PPT 生成数据和上下文

        Args:
            session_id: 会话 ID
            ppt_data: PPT 大纲数据结构
            ppt_file_path: 生成的 PPTX 文件路径
            search_context: 搜索上下文
            research_context: 深度研究上下文
            uploaded_file_context: 上传文件内容

        Returns:
            是否保存成功
        """
        session_file = os.path.join(self.storage_dir, f"{session_id}.json")

        if not os.path.exists(session_file):
            return False

        try:
            with open(session_file, "r", encoding="utf-8") as f:
                session_data = json.load(f)

            session_data.update(
                {
                    "ppt_data": ppt_data,
                    "ppt_file_path": ppt_file_path,
                    "search_context": search_context,
                    "research_context": research_context,
                    "uploaded_file_context": uploaded_file_context,
                    "status": "completed" if ppt_file_path else "preparing",
                    "updated_at": datetime.now().isoformat(),
                }
            )

            with open(session_file, "w", encoding="utf-8") as f:
                json.dump(session_data, f, ensure_ascii=False, indent=2)

            return True
        except Exception as e:
            logger.info(f"[PPTSessionManager] 保存失败: {e}")
            return False

    def load_session(self, session_id: str) -> Optional[Dict]:
        """加载会话数据"""
        session_file = os.path.join(self.storage_dir, f"{session_id}.json")

        if not os.path.exists(session_file):
            return None

        try:
            with open(session_file, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception as e:
            logger.info(f"[PPTSessionManager] 加载失败: {e}")
            return None

    def update_slide(self, session_id: str, slide_index: int, slide_data: Dict) -> bool:
        """
        更新单个幻灯片数据

        Args:
            session_id: 会话 ID
            slide_index: 幻灯片索引（0-based）
            slide_data: 新的幻灯片数据

        Returns:
            是否更新成功
        """
        session = self.load_session(session_id)
        if not session or "ppt_data" not in session:
            return False

        ppt_data = session.get("ppt_data", {})
        slides = ppt_data.get("slides", [])

        if 0 <= slide_index < len(slides):
            slides[slide_index].update(slide_data)
            session["ppt_data"]["slides"] = slides
            session["updated_at"] = datetime.now().isoformat()

            session_file = os.path.join(self.storage_dir, f"{session_id}.json")
            try:
                with open(session_file, "w", encoding="utf-8") as f:
                    json.dump(session, f, ensure_ascii=False, indent=2)
                return True
            except Exception as e:
                logger.info(f"[PPTSessionManager] 更新幻灯片失败: {e}")
                return False

        return False

    def reorder_slides(self, session_id: str, new_order: List[int]) -> bool:
        """
        重排幻灯片顺序

        Args:
            session_id: 会话 ID
            new_order: 新的索引顺序 [0, 2, 1, 3, ...]

        Returns:
            是否重排成功
        """
        session = self.load_session(session_id)
        if not session or "ppt_data" not in session:
            return False

        ppt_data = session.get("ppt_data", {})
        slides = ppt_data.get("slides", [])

        if len(new_order) != len(slides):
            return False

        try:
            reordered = [slides[i] for i in new_order if 0 <= i < len(slides)]
            if len(reordered) != len(slides):
                return False

            ppt_data["slides"] = reordered
            session["ppt_data"] = ppt_data
            session["updated_at"] = datetime.now().isoformat()

            session_file = os.path.join(self.storage_dir, f"{session_id}.json")
            with open(session_file, "w", encoding="utf-8") as f:
                json.dump(session, f, ensure_ascii=False, indent=2)

            return True
        except Exception as e:
            logger.info(f"[PPTSessionManager] 重排失败: {e}")
            return False

    def delete_slide(self, session_id: str, slide_index: int) -> bool:
        """
        删除单个幻灯片

        Args:
            session_id: 会话 ID
            slide_index: 幻灯片索引

        Returns:
            是否删除成功
        """
        session = self.load_session(session_id)
        if not session or "ppt_data" not in session:
            return False

        ppt_data = session.get("ppt_data", {})
        slides = ppt_data.get("slides", [])

        if 0 <= slide_index < len(slides):
            slides.pop(slide_index)
            ppt_data["slides"] = slides
            session["ppt_data"] = ppt_data
            session["updated_at"] = datetime.now().isoformat()

            session_file = os.path.join(self.storage_dir, f"{session_id}.json")
            try:
                with open(session_file, "w", encoding="utf-8") as f:
                    json.dump(session, f, ensure_ascii=False, indent=2)
                return True
            except Exception as e:
                logger.info(f"[PPTSessionManager] 删除幻灯片失败: {e}")
                return False

        return False

    def insert_slide(self, session_id: str, slide_index: int, slide_data: Dict) -> bool:
        """
        在指定位置插入幻灯片

        Args:
            session_id: 会话 ID
            slide_index: 插入位置（0-based）
            slide_data: 幻灯片数据

        Returns:
            是否插入成功
        """
        session = self.load_session(session_id)
        if not session or "ppt_data" not in session:
            return False

        ppt_data = session.get("ppt_data", {})
        slides = ppt_data.get("slides", [])

        if 0 <= slide_index <= len(slides):
            slides.insert(slide_index, slide_data)
            ppt_data["slides"] = slides
            session["ppt_data"] = ppt_data
            session["updated_at"] = datetime.now().isoformat()

            session_file = os.path.join(self.storage_dir, f"{session_id}.json")
            try:
                with open(session_file, "w", encoding="utf-8") as f:
                    json.dump(session, f, ensure_ascii=False, indent=2)
                return True
            except Exception as e:
                logger.info(f"[PPTSessionManager] 插入幻灯片失败: {e}")
                return False

        return False

    def list_sessions(self, limit: int = 20) -> List[Dict]:
        """
        列出最近的会话（用于"打开最近的 PPT"功能）

        Args:
            limit: 最多返回多少个会话

        Returns:
            会话列表（按创建时间倒序）
        """
        sessions = []
        try:
            for filename in os.listdir(self.storage_dir):
                if filename.endswith(".json"):
                    filepath = os.path.join(self.storage_dir, filename)
                    with open(filepath, "r", encoding="utf-8") as f:
                        session = json.load(f)
                        sessions.append(
                            {
                                "session_id": session.get("session_id"),
                                "title": session.get("title"),
                                "created_at": session.get("created_at"),
                                "status": session.get("status"),
                            }
                        )

            # 按创建时间倒序
            sessions.sort(key=lambda x: x.get("created_at", ""), reverse=True)
            return sessions[:limit]
        except Exception as e:
            logger.info(f"[PPTSessionManager] 列表获取失败: {e}")
            return []


# 全局会话管理器实例
_ppt_session_manager = None


def get_ppt_session_manager():
    """获取全局 PPT 会话管理器（单例）"""
    global _ppt_session_manager
    if _ppt_session_manager is None:
        _ppt_session_manager = PPTSessionManager()
    return _ppt_session_manager
