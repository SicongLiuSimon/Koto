#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
记忆系统集成模块
负责在对话过程中自动学习和应用用户记忆
"""

import json
from typing import Dict, List, Optional
import logging


logger = logging.getLogger(__name__)

class MemoryIntegration:
    """记忆系统集成"""
    
    @staticmethod
    def create_extraction_prompt(user_msg: str, ai_msg: str, history: Optional[List] = None) -> str:
        """
        创建用于LLM提取记忆的prompt（增强版）
        """
        prompt = f"""分析以下对话，提取用户的特征、偏好和值得长期记住的信息。

**对话内容：**
用户：{user_msg[:600]}
AI：{ai_msg[:400]}

**提取任务：**
请以JSON格式返回以下信息（只返回JSON，不要其他文字）：

{{
  "programming_languages": [],   // 提及的编程语言
  "tools": [],                   // 提及的工具、框架、库
  "domains": [],                 // 涉及的领域
  "likes": [],                   // 用户明确喜欢的东西
  "dislikes": [],                // 用户明确不喜欢的东西
  "communication_style": {{}},   // 沟通风格偏好（preferred_detail_level: brief/moderate/detailed）
  "memories_to_save": [          // 值得永久记住的信息
    {{"content": "...", "category": "user_preference"}}
  ]
}}

**category 可选候选：**
- user_preference: 用户的习惯、偏好、工作方式
- project_info: 用户正在做的项目、任务、目标
- fact: 用户的个人信息、背景
- correction: 用户明确纠正了AI的误差和误解

**提取规则：**
1. 只提取明确、重要、可复用的信息（能跨会话起作用）
2. 忽略：临时性问题、闲聊、单次性指令
3. memories_to_save 每条 content 必须是完整短句，可独立理解
4. 如无内容可记，所有列表返回空
5. 只返回JSON，不要解释文字
"""
        return prompt
    
    @staticmethod
    async def extract_and_apply(memory_manager, user_msg: str, ai_msg: str, 
                               llm_client, history: Optional[List] = None) -> Dict:
        """
        使用LLM提取信息并应用到记忆系统
        
        Args:
            memory_manager: EnhancedMemoryManager实例
            user_msg: 用户消息
            ai_msg: AI回复
            llm_client: LLM客户端（用于调用AI提取）
            history: 对话历史
            
        Returns:
            提取结果字典
        """
        try:
            # 创建提取prompt
            extraction_prompt = MemoryIntegration.create_extraction_prompt(
                user_msg, ai_msg, history
            )
            
            # 调用LLM提取
            # 注意：这里需要使用简单快速的模型
            response = await llm_client.generate(
                prompt=extraction_prompt,
                temperature=0.1,  # 低温度，更确定性
                max_tokens=500
            )
            
            # 解析JSON
            content = response.strip()
            
            # 清理可能的markdown标记
            if content.startswith("```json"):
                content = content.split("```json")[1].split("```")[0]
            elif content.startswith("```"):
                content = content.split("```")[1].split("```")[0]
            
            extracted = json.loads(content)
            
            # 应用到用户画像
            profile_updates = {}
            
            if extracted.get("programming_languages"):
                profile_updates["programming_languages"] = extracted["programming_languages"]
            
            if extracted.get("tools"):
                profile_updates["tools"] = extracted["tools"]
            
            if extracted.get("domains"):
                profile_updates["domains"] = extracted["domains"]
            
            if extracted.get("likes"):
                profile_updates["likes"] = extracted["likes"]
            
            if extracted.get("dislikes"):
                profile_updates["dislikes"] = extracted["dislikes"]
            
            if extracted.get("communication_style"):
                profile_updates["communication_style"] = extracted["communication_style"]
            
            # 更新用户画像
            if profile_updates:
                memory_manager.user_profile.update_from_extraction(profile_updates)
                logger.info(f"[MemoryIntegration] 🧠 学习到新特征：{list(profile_updates.keys())}")
            
            # 保存显式记忆
            if extracted.get("memories_to_save"):
                for mem in extracted["memories_to_save"]:
                    memory_manager.add_memory(
                        content=mem.get("content", ""),
                        category=mem.get("category", "general"),
                        source="extraction"
                    )
                logger.info(f"[MemoryIntegration] 💾 保存了 {len(extracted['memories_to_save'])} 条记忆")
            
            return {
                "success": True,
                "extracted": extracted,
                "applied": profile_updates
            }
            
        except json.JSONDecodeError as e:
            logger.warning(f"[MemoryIntegration] ⚠️  JSON解析失败: {e}")
            logger.info(f"[MemoryIntegration] 原始响应: {response[:200]}...")
            return {"success": False, "error": "JSON解析失败"}
            
        except Exception as e:
            logger.error(f"[MemoryIntegration] ❌ 提取失败: {e}")
            return {"success": False, "error": str(e)}
    
    @staticmethod
    def should_extract(user_msg: str, ai_msg: str) -> bool:
        """
        判断是否需要进行记忆提取
        避免对每条消息都提取，节省API调用
        """
        # 消息太短，跳过
        if len(user_msg) < 8:
            return False

        # 纯闲聊 / 打招呼，跳过
        greetings = ["你好", "hi", "hello", "嗨", "在吗", "hey", "ok", "okay", "好的", "好"]
        if user_msg.strip().lower() in greetings:
            return False

        # 包含偏好 / 纠正信号词，必要提取
        strong_signals = [
            "喜欢", "不喜欢", "prefer", "倾向", "避免", "不要",
            "更好", "优先", "希望", "想要", "编程风格",
            "记得", "记住", "下次", "以后", "不要再"
        ]
        if any(signal in user_msg for signal in strong_signals):
            return True

        # 包含技术内容，需要提取
        tech_keywords = [
            "python", "javascript", "java", "代码", "项目",
            "开发", "编程", "算法", "数据", "AI",
            "react", "vue", "flask", "django", "fastapi", "docker",
            "框架", "库", "工具", "数据库"
        ]
        if any(kw in user_msg.lower() for kw in tech_keywords):
            return True

        # 较长的对话（可能包含有价值信息），降低阈值
        if len(user_msg) > 40:
            return True

        return False
    
    @staticmethod
    def enhance_system_instruction(base_instruction: str, memory_context: str,
                                   profile_summary: str) -> str:
        """
        增强系统指令，注入记忆和用户画像
        """
        enhanced = base_instruction
        
        # 添加用户画像
        if profile_summary:
            enhanced += f"\n\n{profile_summary}"
        
        # 添加记忆上下文
        if memory_context:
            enhanced += f"\n\n{memory_context}"
        
        # 添加个性化指导
        enhanced += """

**回复调整建议：**
- 根据用户的经验水平调整解释深度
- 遵循用户的代码风格偏好
- 尊重用户明确表达的喜好和厌恶
- 保持用户习惯的沟通风格
"""
        
        return enhanced


# 便捷函数

def get_enhanced_memory_manager():
    """获取增强的记忆管理器实例"""
    try:
        from enhanced_memory_manager import EnhancedMemoryManager
        return EnhancedMemoryManager()
    except ImportError:
        try:
            from web.enhanced_memory_manager import EnhancedMemoryManager
            return EnhancedMemoryManager()
        except ImportError:
            # 降级到基础版本
            logger.warning("[MemoryIntegration] ⚠️  降级到基础记忆管理器")
            from memory_manager import MemoryManager
            return MemoryManager()


if __name__ == "__main__":
    # 测试
    logger.info("=" * 60)
    logger.info("  记忆集成模块测试")
    logger.info("=" * 60)
    
    # 测试提取判断
    test_cases = [
        ("你好", False),
        ("我喜欢简洁的代码", True),
        ("帮我写一个Python爬虫", True),
        ("好的", False),
        ("我在开发一个AI项目，需要用到TensorFlow和PyTorch", True)
    ]
    
    logger.info("\n提取判断测试：")
    for msg, expected in test_cases:
        result = MemoryIntegration.should_extract(msg, "")
        status = "✅" if result == expected else "❌"
        logger.info(f"{status} '{msg}' -> {result} (期望: {expected})")
    
    # 测试prompt生成
    logger.info("\n提取Prompt测试：")
    prompt = MemoryIntegration.create_extraction_prompt(
        "我喜欢简洁的Python代码",
        "好的，我会为你生成简洁的代码"
    )
    logger.info(prompt[:200] + "...")
    
    logger.info("\n✅ 测试完成")
