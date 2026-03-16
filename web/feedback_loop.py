#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
AI反馈循环 - 根据质量评估结果，自动改进生成的内容
集成 Gemini 模型进行迭代改进
"""

import json
from typing import Dict, List, Any, Optional, Callable
import time
import logging


logger = logging.getLogger(__name__)

class FeedbackLoopManager:
    """管理 AI 反馈循环，进行迭代改进"""
    
    def __init__(self, get_client_func: Callable):
        """
        Args:
            get_client_func: 返回 Gemini 客户端的可调用对象
        """
        self.get_client = get_client_func
        self.improvement_iterations = 0
        self.max_iterations = 2  # 最多改进2次
    
    def improve_document_content(
        self,
        original_content: str,
        evaluation_result: Dict[str, Any],
        document_title: str,
        progress_callback: Optional[Callable] = None,
        model_id: str = "gemini-3.1-pro-preview"
    ) -> Dict[str, Any]:
        """根据评分结果改进文档内容
        
        Args:
            original_content: 原始文档内容
            evaluation_result: 来自 quality_evaluator 的评估结果
            document_title: 文档标题
            progress_callback: 进度回调函数 (stage, message)
            model_id: Gemini 模型 ID
        
        Returns:
            改进后的内容和改进过程记录
        """
        self.improvement_iterations = 0
        improvement_history = []
        current_content = original_content
        
        # 如果不需要改进，直接返回
        if not evaluation_result.get("needs_improvement", False):
            if progress_callback:
                progress_callback("completed", f"文档质量良好 (评分: {evaluation_result.get('overall_score', 0):.1f}/100)，无需改进")
            return {
                "improved_content": current_content,
                "improvement_history": improvement_history,
                "final_score": evaluation_result.get("overall_score", 0),
                "iterations": 0,
                "message": "文档已达到质量标准"
            }
        
        # 开始改进循环
        while self.improvement_iterations < self.max_iterations:
            self.improvement_iterations += 1
            iteration_num = self.improvement_iterations
            
            if progress_callback:
                progress_callback(
                    "improving",
                    f"正在进行第 {iteration_num}/{self.max_iterations} 次改进..."
                )
            
            # 构建改进提示
            prompt = self._build_improvement_prompt(
                current_content,
                evaluation_result,
                document_title,
                iteration_num
            )
            
            # 调用 Gemini 进行改进
            try:
                improved_content = self._call_gemini_for_improvement(prompt, model_id)
                
                if improved_content:
                    current_content = improved_content
                    improvement_history.append({
                        "iteration": iteration_num,
                        "actions": evaluation_result.get("improvement_priority", []),
                        "success": True
                    })
                    
                    if progress_callback:
                        progress_callback(
                            "improving",
                            f"第 {iteration_num} 次改进完成，正在重新评估..."
                        )
                    
                    # 重新评估改进后的内容
                    from quality_evaluator import DocumentEvaluator
                    evaluator = DocumentEvaluator()
                    new_evaluation = evaluator.evaluate_document(current_content)
                    new_score = new_evaluation.overall_score
                    
                    # 如果评分没有改善，停止循环
                    old_score = evaluation_result.get("overall_score", 0)
                    if new_score <= old_score + 5:  # 改进不足5分则停止
                        improvement_history[-1]["final_score"] = new_score
                        if progress_callback:
                            progress_callback(
                                "completed",
                                f"改进效果有限 (评分: {old_score:.1f} → {new_score:.1f}，仅提升 {new_score - old_score:.1f} 分)，停止迭代"
                            )
                        break
                    
                    improvement_history[-1]["final_score"] = new_score
                    evaluation_result = new_evaluation.__dict__ if hasattr(new_evaluation, '__dict__') else {
                        "overall_score": new_score,
                        "issues": new_evaluation.issues,
                        "suggestions": new_evaluation.suggestions,
                        "needs_improvement": new_evaluation.needs_improvement,
                        "improvement_priority": new_evaluation.improvement_priority
                    }
                    
                    # 如果达到良好质量，停止循环
                    if new_score >= 80:
                        if progress_callback:
                            progress_callback(
                                "completed",
                                f"文档质量已优化至 {new_score:.1f}/100，改进完成"
                            )
                        break
                else:
                    improvement_history.append({
                        "iteration": iteration_num,
                        "actions": evaluation_result.get("improvement_priority", []),
                        "success": False,
                        "error": "模型未返回改进内容"
                    })
                    break
            
            except Exception as e:
                improvement_history.append({
                    "iteration": iteration_num,
                    "actions": evaluation_result.get("improvement_priority", []),
                    "success": False,
                    "error": str(e)
                })
                if progress_callback:
                    progress_callback("error", f"改进失败: {str(e)}")
                break
        
        return {
            "improved_content": current_content,
            "improvement_history": improvement_history,
            "final_score": evaluation_result.get("overall_score", 0),
            "iterations": self.improvement_iterations,
            "message": f"已完成 {self.improvement_iterations} 次改进"
        }
    
    def improve_ppt_outline(
        self,
        original_outline: List[Dict[str, Any]],
        evaluation_result: Dict[str, Any],
        title: str,
        progress_callback: Optional[Callable] = None,
        model_id: str = "gemini-3.1-pro-preview"
    ) -> Dict[str, Any]:
        """根据 PPT 评分结果改进 PPT 大纲
        
        Args:
            original_outline: 原始 PPT 大纲（字典列表）
            evaluation_result: PPT 评估结果
            title: PPT 标题
            progress_callback: 进度回调
            model_id: 模型 ID
        
        Returns:
            改进后的大纲
        """
        self.improvement_iterations = 0
        improvement_history = []
        
        if not evaluation_result.get("needs_improvement", False):
            if progress_callback:
                progress_callback("completed", f"PPT质量良好 (评分: {evaluation_result.get('overall_score', 0):.1f}/100)，无需改进")
            return {
                "improved_outline": original_outline,
                "improvement_history": improvement_history,
                "final_score": evaluation_result.get("overall_score", 0),
                "iterations": 0
            }
        
        current_outline = original_outline
        
        while self.improvement_iterations < self.max_iterations:
            self.improvement_iterations += 1
            
            if progress_callback:
                progress_callback(
                    "improving",
                    f"正在进行 PPT 第 {self.improvement_iterations}/{self.max_iterations} 次改进..."
                )
            
            prompt = self._build_ppt_improvement_prompt(
                current_outline,
                evaluation_result,
                title,
                self.improvement_iterations
            )
            
            try:
                improved_outline_json = self._call_gemini_for_improvement(prompt, model_id)
                
                if improved_outline_json:
                    # 解析返回的大纲
                    try:
                        improved_outline = json.loads(improved_outline_json)
                        current_outline = improved_outline
                        
                        improvement_history.append({
                            "iteration": self.improvement_iterations,
                            "success": True,
                            "actions": evaluation_result.get("improvement_priority", [])
                        })
                    except json.JSONDecodeError:
                        improvement_history.append({
                            "iteration": self.improvement_iterations,
                            "success": False,
                            "error": "返回内容格式错误"
                        })
                        break
                    
                    if progress_callback:
                        progress_callback("improving", f"PPT 第 {self.improvement_iterations} 次改进完成")
                else:
                    break
            
            except Exception as e:
                improvement_history.append({
                    "iteration": self.improvement_iterations,
                    "success": False,
                    "error": str(e)
                })
                break
        
        return {
            "improved_outline": current_outline,
            "improvement_history": improvement_history,
            "final_score": evaluation_result.get("overall_score", 0),
            "iterations": self.improvement_iterations
        }
    
    def _build_improvement_prompt(
        self,
        content: str,
        evaluation: Dict[str, Any],
        title: str,
        iteration: int
    ) -> str:
        """构建改进提示"""
        issues = "\n".join([f"- {issue}" for issue in evaluation.get("issues", [])])
        suggestions = "\n".join([f"- {suggestion}" for suggestion in evaluation.get("suggestions", [])])
        
        prompt = f"""你是一个专业的文档编辑，需要根据质量评估反馈改进一份文档。

原始文档标题: {title}
当前评分: {evaluation.get('overall_score', 0):.1f}/100

【识别的问题】
{issues if issues else "无"}

【改进建议】
{suggestions if suggestions else "无"}

【当前文档内容】
{content}

请根据上述问题和建议改进文档。改进要求：
1. 保留原文档的核心结构和主要观点
2. 针对识别的问题进行修复
3. 按照建议增强内容质量
4. 确保改进后的文档长度至少400字（不包括标题）
5. 保持 Markdown 格式（如有）

请直接返回改进后的完整文档内容，不要添加额外说明。"""
        
        return prompt
    
    def _build_ppt_improvement_prompt(
        self,
        outline: List[Dict[str, Any]],
        evaluation: Dict[str, Any],
        title: str,
        iteration: int
    ) -> str:
        """构建 PPT 改进提示"""
        issues = "\n".join([f"- {issue}" for issue in evaluation.get("issues", [])])
        suggestions = "\n".join([f"- {suggestion}" for suggestion in evaluation.get("suggestions", [])])
        
        outline_text = json.dumps(outline, ensure_ascii=False, indent=2)
        
        prompt = f"""你是一个专业的PPT设计顾问，需要根据质量评估反馈改进一份演示文稿大纲。

演示文稿标题: {title}
当前评分: {evaluation.get('overall_score', 0):.1f}/100

【识别的问题】
{issues if issues else "无"}

【改进建议】
{suggestions if suggestions else "无"}

【当前PPT大纲】
{outline_text}

请根据上述问题和建议改进 PPT 大纲。改进要求：
1. 调整内容分布，避免单页过多/过少信息
2. 增加图片描述和可视化需求
3. 保持总幻灯片数在 5-20 页之间
4. 增强每页的逻辑清晰度
5. 为需要图片的幻灯片添加详细的配图需求

请返回改进后的 JSON 格式大纲（与输入格式相同），不要添加 markdown 代码块。"""
        
        return prompt
    
    def _call_gemini_for_improvement(self, prompt: str, model_id: str = "gemini-3.1-pro-preview") -> Optional[str]:
        """调用 Gemini 模型进行改进"""
        try:
            client = self.get_client()
            
            response = client.models.generate_content(
                model=model_id,
                contents=prompt,
                config={
                    "temperature": 0.7,
                    "top_p": 0.9,
                    "max_output_tokens": 8000
                }
            )
            
            if response and response.text:
                return response.text.strip()
            return None
        
        except Exception as e:
            logger.info(f"[AI反馈循环] Gemini 调用失败: {e}")
            return None


def create_feedback_manager(get_client_func: Callable) -> FeedbackLoopManager:
    """工厂函数创建反馈管理器"""
    return FeedbackLoopManager(get_client_func)
